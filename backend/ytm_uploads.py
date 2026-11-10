"""Your uploads: uploaded songs, albums and artists, their pages, and
uploading and deleting files (S10).

Reads (signed in, CACHEABLE, on the pages lane):

- ``uploadSongs({order?, cursor?})``: FEmusic_library_privately_owned_tracks,
  about 50 songs a call (at most 3 requests, counting the session check),
  then ``cursor`` pages (``c.<token>``, never cached).
- ``uploadAlbums({order?})`` and ``uploadArtists({order?})``:
  get_library_upload_albums / _artists, read whole (at most LIST_MAX).
- ``uploadAlbum({browseId})``: get_library_upload_album, as an album
  collection; ``uploadArtist({browseId})``: get_library_upload_artist, as a
  collection of that artist's uploaded songs.

Songs are tracks of kind ``upload`` with the ``entityId`` that deletes them;
they stream with the account. Albums and artists keep the uploads ids
(FEmusic_library_privately_owned_...), which tracksFor reads through
``album_tracks`` / ``artist_tracks`` (ytm_play.route).

Orders: the uploads pages serve no sort menu. Their frameworkUpdates hold
one musicFormBooleanChoice per order with an opaque token
(``SortSpec_Type_ADDED_TIMESTAMP_SortSpec_Order_DESCENDING`` and the like),
which says which ones the page has and which is selected; ``order`` is one
of ytm_library.ORDERS, sent as ytmusicapi's order params. YouTube keeps the
order on the account (ytm_pages.saves_order), as on the other library pages.

Edits (MUTATES; the shell sends them with retry false):

- ``uploadSong({path})`` on the ``upload`` lane (a synchronous POST of the
  whole file, no timeout of its own): refused under a brand account
  (ytm_account.require_upload_identity), then the path is checked: ``~``
  expanded, absolute, a regular file of an accepted type (any case) under
  300 MB. ytmusicapi checks the suffix case-sensitively, so ``SONG.MP3`` is
  sent through a ``SONG.mp3`` symlink in <cache>/upload-tmp, removed after.
  ``{ok, processing: true, name}``: YouTube lists the song once it has
  processed it, minutes later.
- ``deleteUpload({entityId})``: delete_upload_entity (a song's ``t_po_...``
  or an album's ``entityId``); anything but STATUS_SUCCEEDED is UPSTREAM.

Both drop every cached uploads read. Area module (see AREA_MODULES in
ytm.py). Nothing is recorded: these pages are private, so the tests use
hand-built responses.
"""

import contextlib
import os
import shutil
import tempfile
from pathlib import Path

from ytmusicapi.mixins._utils import prepare_order_params
from ytmusicapi.navigation import FRAMEWORK_MUTATIONS, MRLIR, nav
from ytmusicapi.parsers.uploads import parse_uploaded_items

import ytm_account as identities
import ytm_cache as caches
import ytm_library as library
import ytm_normalize as norm
import ytm_pages as pages
import ytm_raw as raw
from ytm_proto import Fixed, WorkerError, opt_cursor, opt_order, req_str

SONGS_ID = "FEmusic_library_privately_owned_tracks"
ALBUMS_ID = "FEmusic_library_privately_owned_releases"
ARTISTS_ID = "FEmusic_library_privately_owned_artists"
ALBUM_PREFIX = norm.UPLOAD_ALBUM_PREFIX
ARTIST_PREFIX = norm.UPLOAD_ARTIST_PREFIX

PAGE_ITEMS = 50
# Albums and artists are read whole; a library this big is cut here.
LIST_MAX = 1000
MAX_TRACKS = 5000
ORDERS = library.ORDERS
ORDER_TITLES = {"recently_added": "Recently added", "a_to_z": "A to Z", "z_to_a": "Z to A"}
# (sort type, direction) inside a served choice's opaque token, per order.
SORT_TOKENS = {"recently_added": ("ADDED_TIMESTAMP", "DESCENDING"), "a_to_z": ("ENTITY_NAME", "ASCENDING"),
               "z_to_a": ("ENTITY_NAME", "DESCENDING")}

# What YouTube Music takes (ytmusicapi's list) and its size limit: a file of
# this many bytes or more is refused.
EXTENSIONS = ("mp3", "m4a", "wma", "flac", "ogg")
MAX_BYTES = 300 * 1024 * 1024
TMP_DIR = "upload-tmp"


# ---- orders ------------------------------------------------------------------------------


def token_order(token):
    """The order a served choice's opaque token names, or None."""
    text = token if isinstance(token, str) else ""
    for order, (kind, direction) in SORT_TOKENS.items():
        if f"SortSpec_Type_{kind}_" in text and text.endswith(f"SortSpec_Order_{direction}"):
            return order
    return None


def served_orders(response):
    """``[{index, title, selected}]`` of the orders the page names, in served order."""
    out, seen = [], set()
    mutations = nav(response, FRAMEWORK_MUTATIONS, True) if isinstance(response, dict) else None
    for mutation in mutations or []:
        choice = nav(mutation, ["payload", "musicFormBooleanChoice"], True)
        order = token_order(choice.get("opaqueToken")) if isinstance(choice, dict) else None
        if order and order not in seen:
            seen.add(order)
            out.append({"index": order, "title": ORDER_TITLES[order], "selected": bool(choice.get("selected"))})
    return out


def orders_of(response, order):
    """``(orders, current)``: the served orders (all of ORDERS when the page
    names none), the one the reply is in marked selected: the one asked for,
    else the one the page says."""
    orders = served_orders(response) or [{"index": o, "title": ORDER_TITLES[o], "selected": False} for o in ORDERS]
    current = order if order is not None else next((o["index"] for o in orders if o["selected"]), None)
    return [dict(o, selected=o["index"] == current) for o in orders], current


def _body(browse_id, order):
    body = {"browseId": browse_id}
    if order is not None:
        body["params"] = prepare_order_params(order)
    return body


def _forget_order(worker, browse_id, order):
    """An ordered read saves the order on the account: what it made stale
    goes (ytm_pages.forget_order, as ytm_library does): the cached
    browsePage copies of that page and the list's own cached reads."""
    if order is not None and pages.saves_order(browse_id):
        pages.forget_order(worker, browse_id)


# ---- reading ------------------------------------------------------------------------------


def _tab_rows(response):
    """The rows of the selected tab first (the uploads pages serve Library and
    Downloads tabs before it, with no rows), then the other tabs'."""
    tabs = nav(response, ["contents", "singleColumnBrowseResultsRenderer", "tabs"], True) if isinstance(response, dict) else None
    tabs = [t for t in tabs or [] if isinstance(t, dict)]
    tabs.sort(key=lambda t: nav(t, ["tabRenderer", "selected"], True) is not True)
    out = []
    for tab in tabs:
        out.extend(nav(tab, ["tabRenderer", "content", "sectionListRenderer", "contents"], True) or [])
    return out


def _renderers(row):
    """``(key, data)`` of a row and of each entry of an item section."""
    if not isinstance(row, dict) or not row:
        return []
    key, data = next(iter(row.items()))
    if key == "itemSectionRenderer":
        return [next(iter(c.items())) for c in (data or {}).get("contents") or [] if isinstance(c, dict) and c]
    return [(key, data)]


def list_container(response, renderer="musicShelfRenderer"):
    """The music shelf (or grid) of an uploads list page, or None."""
    for row in _tab_rows(response):
        for key, data in _renderers(row):
            if key == renderer and isinstance(data, dict):
                return data
    return None


def empty_message(response):
    """The text of an empty uploads page's message, or None."""
    for row in _tab_rows(response):
        for key, data in _renderers(row):
            if key == "messageRenderer" and isinstance(data, dict):
                subtext = data.get("subtext") if isinstance(data.get("subtext"), dict) else {}
                return raw.text(data.get("text")) or raw.text(nav(subtext, ["messageSubtextRenderer", "text"], True)) \
                    or None
    return None


def songs_of(contents):
    """Uploaded songs of shelf rows; the leading Shuffle row (no menu),
    continuation entries and malformed rows are skipped."""
    out = []
    for content in contents or []:
        if not isinstance(content, dict) or MRLIR not in content:
            continue
        try:
            parsed = parse_uploaded_items([content])
        except (KeyError, TypeError, IndexError, AttributeError, ValueError) as exc:
            norm.log(f"skipped malformed upload: {type(exc).__name__}")
            continue
        out.extend(norm.normalize_list(parsed, norm.upload_track, "upload"))
    return out


def _page(method, page_id, title, items, cursor=None, orders=None, order=None, message=None, layout="shelf"):
    sections = [norm.section(None, items, layout=layout)] if items or not message else []
    return norm.page(method, page_id, title, sections=sections, cursor=cursor, orders=orders, order=order,
                     message=message)


def _upload_id(params, prefix, what):
    browse_id = req_str(params, "browseId")
    if not browse_id.startswith(prefix) or len(browse_id) == len(prefix):
        raise WorkerError("BAD_REQUEST", f"'browseId' is not an uploaded {what}")
    return browse_id


# ---- methods: reads ---------------------------------------------------------------------------


def m_upload_songs(worker, params):
    """``{order?, cursor?}``: a page of your uploaded songs (one untitled section)."""
    worker.require_auth()
    order = opt_order(params, ORDERS)
    cursor = opt_cursor(params)
    body = _body(SONGS_ID, order)
    if cursor:
        items, cursor = pages.fetch(worker.yt, "browse", body, "ctoken", None, songs_of, cursor=cursor,
                                    min_items=PAGE_ITEMS, max_http=library._budget(0))
        return _page("uploadSongs", SONGS_ID, "Uploaded songs", items, cursor, order=order)
    try:
        first = pages.first_page(worker.yt, "browse", body)
    finally:
        _forget_order(worker, SONGS_ID, order)
    response = first[0]
    orders, current = orders_of(response, order)
    container = list_container(response)
    if container is None:
        return _page("uploadSongs", SONGS_ID, "Uploaded songs", [], None, orders, current,
                     message=empty_message(response) or "No uploaded songs yet")
    items, cursor = pages.fetch(worker.yt, "browse", body, "ctoken", lambda _: container, songs_of,
                                min_items=PAGE_ITEMS, max_http=library._budget(first[1]), first=first)
    return _page("uploadSongs", SONGS_ID, "Uploaded songs", items, cursor, orders, current)


def _whole_list(worker, params, browse_id, read, normalize, method, title, layout, what):
    worker.require_auth()
    order = opt_order(params, ORDERS)
    try:
        with raw.capture(("browse",)) as cap:
            values = read(limit=LIST_MAX, order=order)
    finally:
        _forget_order(worker, browse_id, order)
    orders, current = orders_of(cap.first("browse"), order)
    items = norm.normalize_list(values, normalize, what)
    return _page(method, browse_id, title, items, None, orders, current,
                 message=None if items else f"No uploaded {what}s yet", layout=layout)


def m_upload_albums(worker, params):
    """``{order?}``: every album of your uploads (one grid section)."""
    return _whole_list(worker, params, ALBUMS_ID, worker.yt.get_library_upload_albums, norm.upload_album,
                       "uploadAlbums", "Uploaded albums", "grid", "album")


def m_upload_artists(worker, params):
    """``{order?}``: every artist of your uploads (one shelf section)."""
    return _whole_list(worker, params, ARTISTS_ID, worker.yt.get_library_upload_artists, norm.upload_artist,
                       "uploadArtists", "Uploaded artists", "shelf", "artist")


def album_page(data, browse_id):
    """An uploaded album as an album collection: upload tracks, the album's
    artists from its songs when the header names none, its ``entityId``
    (Delete), and no playlist (its MLPR id plays nothing elsewhere)."""
    data = dict(data or {})
    tracks = data.get("tracks") or []
    if not data.get("artists"):
        data["artists"] = next((t.get("artists") for t in tracks if isinstance(t, dict) and t.get("artists")), None) or []
    page = norm.collection("album", dict(data, tracks=[]), browse_id)
    ref = {"name": page["title"], "id": browse_id}
    covers = (page["thumb"], page["thumbLarge"]) if page["thumb"] else None
    page["tracks"] = norm.normalize_list(tracks, lambda t: norm.upload_track(t, album=ref, album_thumbs=covers),
                                         "upload album track")
    if page["trackCount"] == 0:
        page["trackCount"] = len(page["tracks"])
    page["unavailable"] = max(0, page["trackCount"] - len(page["tracks"]))
    page["playlistId"] = None
    page["header"]["audioPlaylistId"] = None
    page["upload"] = True
    page["entityId"] = norm.upload_entity(browse_id)
    return page


def artist_page(songs, browse_id, name):
    """An uploaded artist's songs as a collection (kind ``uploadArtist``)."""
    tracks = norm.normalize_list(songs, norm.upload_track, "upload artist song")
    if not name:
        name = next((a["name"] for t in tracks for a in t["artists"] if a.get("id") == browse_id), "")
    page = norm.collection("uploadArtist", {"title": name, "tracks": [], "trackCount": len(tracks)}, browse_id)
    page.update({"tracks": tracks, "playlistId": None, "unavailable": 0, "upload": True,
                 "subtitle": "1 song" if len(tracks) == 1 else f"{len(tracks)} songs"})
    return page


def m_upload_album(worker, params):
    """``{browseId}`` (FEmusic_library_privately_owned_release_detail...)."""
    browse_id = _upload_id(params, ALBUM_PREFIX, "album")
    worker.require_auth()
    return album_page(worker.yt.get_library_upload_album(browse_id), browse_id)


def _artist_songs(worker, browse_id, limit=MAX_TRACKS):
    """``(songs, name)``: ytmusicapi's songs of an uploaded artist and the
    header's name. Its limit is a minimum: it reads whole 25-song pages
    until it holds that many, so it may answer more."""
    with raw.capture(("browse",)) as cap:
        songs = worker.yt.get_library_upload_artist(browse_id, limit=limit)
    name = raw.text(nav(cap.first("browse"), ["header", "musicHeaderRenderer", "title"], True)) or ""
    return songs or [], name


def m_upload_artist(worker, params):
    """``{browseId}`` (FEmusic_library_privately_owned_artist_detail...)."""
    browse_id = _upload_id(params, ARTIST_PREFIX, "artist")
    worker.require_auth()
    songs, name = _artist_songs(worker, browse_id)
    return artist_page(songs, browse_id, name)


# ---- tracksFor (ytm_play.route) ------------------------------------------------------------------


def _tracks_reply(page, limit):
    items = page["tracks"][:limit]
    return {"playlistId": None, "items": items, "complete": len(items) >= len(page["tracks"]),
            "total": page["trackCount"]}


def album_tracks(worker, browse_id, limit=MAX_TRACKS):
    worker.require_auth()
    return _tracks_reply(album_page(worker.yt.get_library_upload_album(browse_id), browse_id), limit)


def artist_tracks(worker, browse_id, limit=MAX_TRACKS):
    """One song more than ``limit`` is asked for: ytmusicapi stops once it
    holds the limit, so only a read past it says the artist has more. Cut
    short, the reply is not complete and its total unknown (None)."""
    worker.require_auth()
    songs, name = _artist_songs(worker, browse_id, limit + 1)
    page = artist_page(songs, browse_id, name)
    complete = len(songs) <= limit
    return {"playlistId": None, "items": page["tracks"][:limit], "complete": complete,
            "total": page["trackCount"] if complete else None}


# ---- methods: edits --------------------------------------------------------------------------------


def upload_path(params):
    """The file ``params.path`` names, checked as YouTube Music would take it."""
    text = req_str(params, "path")
    if len(text) > 4096 or "\0" in text:
        raise WorkerError("BAD_REQUEST", "'path' is not a file path")
    path = Path(os.path.expanduser(text))
    if not path.is_absolute():
        raise WorkerError("BAD_REQUEST", "Give the file's full path (it may start with ~)")
    if not path.exists():
        raise WorkerError("BAD_REQUEST", f"No such file: {path.name}")
    if path.is_dir():
        raise WorkerError("BAD_REQUEST", f"{path.name} is a folder; give a music file")
    if not path.is_file():
        raise WorkerError("BAD_REQUEST", f"{path.name} is not a regular file")
    if path.suffix[1:].lower() not in EXTENSIONS:
        raise WorkerError("BAD_REQUEST", "YouTube Music takes " + ", ".join(EXTENSIONS[:-1]) + f" or {EXTENSIONS[-1]} files")
    if path.stat().st_size >= MAX_BYTES:
        raise WorkerError("BAD_REQUEST", f"{path.name} is 300 MB or more; YouTube Music takes smaller files")
    return path


def tmp_root():
    return caches.cache_root() / TMP_DIR


@contextlib.contextmanager
def sendable(path):
    """``path`` as ytmusicapi accepts it: itself, or (an upper-case suffix) a
    symlink named with the suffix in lower case, in a folder of its own under
    tmp_root(), removed afterwards. YouTube gets that name as the file's."""
    if path.suffix == path.suffix.lower():
        yield path
        return
    caches.private_dir(tmp_root())
    folder = Path(tempfile.mkdtemp(dir=tmp_root()))
    link = folder / (path.stem + path.suffix.lower())
    try:
        link.symlink_to(path.resolve())
        yield link
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _succeeded(result):
    return result == "STATUS_SUCCEEDED" or getattr(result, "value", None) == "STATUS_SUCCEEDED"


def m_upload_song(worker, params):
    """``{path, identityKey?}``: uploads the file; ``{ok, processing: true,
    name}``. ``identityKey``: the identity the upload was asked under; an
    upload that waited on the upload lane while the account changed is
    refused, unsent (ytm_account.require_identity_key)."""
    identities.require_upload_identity(worker)  # S16: never as a brand account
    identities.require_identity_key(worker, params, "this upload")
    path = upload_path(params)
    worker.require_auth()
    with sendable(path) as sent:
        result = worker.yt.upload_song(str(sent))
    if not _succeeded(result):
        status = getattr(result, "status_code", None)
        raise WorkerError("UPSTREAM", "YouTube Music did not take the file"
                          + (f" (HTTP {status})" if isinstance(status, int) else ""))
    return {"ok": True, "processing": True, "name": path.name}


def m_delete_upload(worker, params):
    """``{entityId}``: deletes an uploaded song or album."""
    entity_id = req_str(params, "entityId")
    if len(entity_id) > 512 or any(c.isspace() for c in entity_id):
        raise WorkerError("BAD_REQUEST", "'entityId' is not an upload's id")
    worker.require_auth()
    if not _succeeded(worker.yt.delete_upload_entity(entity_id)):
        raise WorkerError("UPSTREAM", "YouTube Music did not delete the upload")
    return {"ok": True, "entityId": entity_id}


# ---- registry (merged by ytm.py) ------------------------------------------------------------

METHODS = {
    "uploadSongs": m_upload_songs,
    "uploadAlbums": m_upload_albums,
    "uploadArtists": m_upload_artists,
    "uploadAlbum": m_upload_album,
    "uploadArtist": m_upload_artist,
    "uploadSong": m_upload_song,
    "deleteUpload": m_delete_upload,
}
READS = ("uploadSongs", "uploadAlbums", "uploadArtists", "uploadAlbum", "uploadArtist")
# First pages only for uploadSongs (its key holds the order); cursor pages are never cached.
CACHEABLE = set(READS)
# deleteUpload is one quick request, answered in order like the other edits
# (not behind an upload of several minutes).
LANES = dict({name: "pages" for name in READS}, uploadSong="upload")
# Every uploads read, the browse pages of the three lists, and the library
# lists an upload may show in (its songs, albums and artists, Recent activity).
_UPLOADS = [(name, None, None) for name in READS] + [
    ("browsePage", Fixed(SONGS_ID, ALBUMS_ID, ARTISTS_ID), "browseId"),
    ("librarySongs", None, None), ("libraryAlbums", None, None), ("libraryArtists", None, None),
    ("libraryLanding", None, None)]
INVALIDATES = {"uploadSong": _UPLOADS, "deleteUpload": _UPLOADS}
# Private pages: no recorders (fixtures would hold the uploads).
RECORDERS = {}
MUTATES = {"uploadSong", "deleteUpload"}
