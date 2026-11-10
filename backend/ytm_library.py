"""Your library: playlists, songs, albums, artists, subscriptions and the
landing page's recent activity, with the sort orders YouTube serves (S2).

Every method needs the account and reads on the pages lane. Long lists come
a page at a time (about 50 items, at most 3 requests per call): the first
page is cached (its key holds the order), later pages follow the opaque
``cursor`` and are never cached. Orders:

- ``a_to_z``, ``z_to_a`` and ``recently_added`` go into the browse body as
  ytmusicapi's order params (one request, as get_library_*(order=...) sends).
- A served option that is none of those (the landing's Recent activity and
  Recently played, the artists' Most songs) is ``menu:<index>`` and applied
  with its reload token (two requests). Without a served menu it is not
  offered, and asking for it is BAD_REQUEST.

A served option is matched to its order by the params inside its reload
token (the token is the browse request it reloads), never by its title, so
``orders`` carry the titles exactly as served.

YouTube saves a re-sort of these pages on the account (ytm_pages.saves_order),
so an ordered first page also drops the cached browsePage copies of it.

Area module (see AREA_MODULES in ytm.py). Like ytm_pages.py it uses
ytmusicapi internals, pinned to 1.12.2. Nothing here is recorded: these
pages are private, so the tests use hand-built responses.
"""

import base64
import binascii
import re
import urllib.parse

from ytmusicapi.continuations import get_continuation_string
from ytmusicapi.mixins._utils import prepare_order_params
from ytmusicapi.navigation import MRLIR, MTRIR, nav
from ytmusicapi.parsers._utils import get_flex_column_item
from ytmusicapi.parsers.browsing import parse_playlist
from ytmusicapi.parsers.library import parse_albums, parse_artists
from ytmusicapi.parsers.watch import parse_watch_playlist as parse_watch_tracks

import ytm_normalize as norm
import ytm_pages as pages
import ytm_raw as raw
from ytm_proto import WorkerError, opt_cursor

ORDERS = ("a_to_z", "z_to_a", "recently_added")
# Titles of the three for a page that serves no sort menu (every library
# page serves one today; the titles are otherwise always YouTube's).
ORDER_TITLES = {"recently_added": "Recently saved", "a_to_z": "A to Z", "z_to_a": "Z to A"}
MENU_ORDER = re.compile(r"^menu:(\d{1,2})$")
PAGE_ITEMS = 50
MAX_HTTP = 3
# libraryPlaylists reads the whole list (one request for most libraries).
PLAYLISTS_MAX_HTTP = 20
LIST_RENDERERS = ("gridRenderer", "musicShelfRenderer")
# Auto playlists the Library tab shows its own way (Liked Music as an entry
# row; Episodes for later on Podcasts, S8).
SPECIAL = {"LM": "liked", "SE": "episodes"}

BROWSE_IDS = {
    "libraryPlaylists": "FEmusic_liked_playlists",
    "librarySongs": "FEmusic_liked_videos",
    "libraryAlbums": "FEmusic_liked_albums",
    "libraryArtists": "FEmusic_library_corpus_track_artists",
    "librarySubscriptions": "FEmusic_library_corpus_artists",
    "libraryLanding": "FEmusic_library_landing",
}

# Shuffle all: YouTube's server shuffle of the whole library. The songs
# page's leading row plays the watch playlist SHUFFLE_ID with these params
# (the ones get_watch_playlist(shuffle=True) sends). get_watch_playlist
# cannot read on past its first page there (the panel continues like a
# playlist, not a radio), so shuffle_tracks reads it itself.
SHUFFLE_ID = "MLCT"
SHUFFLE_PARAMS = "wAEB8gECKAE%3D"
# Each request takes 1 to 2 s (a whole library is shuffled), so the first
# 50 songs (two requests) are read and autoplay carries on from there.
SHUFFLE_TRACKS = 50
SHUFFLE_MAX_HTTP = 2
_PANEL = ["contents", "singleColumnMusicWatchNextResultsRenderer", "tabbedRenderer", "watchNextTabbedResultsRenderer",
          "tabs", 0, "tabRenderer", "content", "musicQueueRenderer", "content", "playlistPanelRenderer"]


# ---- orders ---------------------------------------------------------------------------


def order_param(params):
    """``params.order``: one of ORDERS, ``menu:<n>``, or None."""
    value = params.get("order")
    if value is None:
        return None
    if isinstance(value, str) and (value in ORDERS or MENU_ORDER.match(value)):
        return value
    raise WorkerError("BAD_REQUEST", "'order' must be a_to_z, z_to_a, recently_added or a served menu:<n>")


def _menu_index(order):
    match = MENU_ORDER.match(order) if isinstance(order, str) else None
    return int(match.group(1)) if match else None


def _body(browse_id, order):
    body = {"browseId": browse_id}
    if order in ORDERS:
        body["params"] = prepare_order_params(order)
    return body


def token_order(token):
    """The order whose params a served option's reload token carries, or None.

    The token is the browse request it reloads (browseId and params, as
    base64); an option with params none of ORDERS has (Most songs) is None.
    """
    if not isinstance(token, str) or not token:
        return None
    text = urllib.parse.unquote(token)
    try:
        data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return None
    for order in ORDERS:
        if prepare_order_params(order).encode() in data:
            return order
    return None


def served_orders(response):
    """``[{index, title, selected}]`` of a page's sort menu with ``index`` the
    worker's order id: one of ORDERS, else ``menu:<position>``."""
    out = []
    for option in pages.sort_options(response):
        try:
            token = pages.reload_token(response, option["index"])
        except WorkerError:
            token = None
        order = token_order(token)
        out.append({"index": order or f"menu:{option['index']}", "title": option["title"],
                    "selected": option["selected"]})
    return out


def _orders(response, order, offered=ORDERS):
    """``(orders, current)``: the served options (``offered`` with fallback
    titles when the page serves none) and the order the reply is in, marked
    selected: the one asked for, else the one YouTube says it served."""
    served = served_orders(response) if response else []
    orders = served or [{"index": o, "title": ORDER_TITLES[o], "selected": False} for o in offered]
    position = _menu_index(order)
    if order is None:
        current = next((o["index"] for o in orders if o["selected"]), None)
    elif position is not None and position < len(served):
        current = served[position]["index"]  # menu:<n> of an option that is one of ORDERS is that order
    else:
        current = order
    return [dict(o, selected=o["index"] == current) for o in orders], current


# ---- reading ----------------------------------------------------------------------------


def _container(response):
    """The grid or music shelf holding a library list."""
    for row in raw.rows(response):
        key, data = raw.unwrap(row)
        candidates = [(key, data)]
        if key == "itemSectionRenderer":
            candidates = [next(iter(c.items())) for c in (data or {}).get("contents") or []
                          if isinstance(c, dict) and c]
        for name, value in candidates:
            if name in LIST_RENDERERS and isinstance(value, dict):
                return value
    return None


def _first_page(worker, browse_id, body, order):
    """The first page in ``order`` (a ytm_pages.first_page result). An ordered
    request is also a saved setting on these pages: the cached browsePage
    copies of the page go, whether or not it worked (it may have applied)."""
    try:
        return pages.first_page(worker.yt, "browse", body, _menu_index(order))
    finally:
        if order is not None and pages.saves_order(browse_id):
            worker.invalidate("browsePage", {"browseId": browse_id})


def _budget(counted):
    """fetch's max_http so the whole call stays within MAX_HTTP: the session
    check before a worker's first library call is a request too. ``counted``
    is what fetch already counts (the first page's requests)."""
    spent = max(0, raw.http_count() - counted)
    return max(counted, 1, MAX_HTTP - spent)


def _page(method, browse_id, items, cursor, orders=None, order=None, header=None, message=None, layout=None):
    sections = [norm.section(None, items, layout=layout)] if items or not message else []
    return norm.page(method, browse_id, None, sections=sections, cursor=cursor, header=header, orders=orders,
                     order=order, message=message)


def _read_list(worker, params, method, parse, layout, offered=ORDERS, header_of=None, browse_id=None):
    """A page of one library list: ``{type: "page", kind: method, sections: [one
    section], cursor, orders, order, header, message}``. ``browse_id``: a list
    of another module (S8's podcasts and channels); else BROWSE_IDS[method]."""
    worker.require_auth()
    browse_id = browse_id or BROWSE_IDS[method]
    order = order_param(params)
    cursor = opt_cursor(params)
    body = _body(browse_id, order)
    if cursor:
        items, cursor = pages.fetch(worker.yt, "browse", body, "ctoken", None, parse, cursor=cursor,
                                    min_items=PAGE_ITEMS, max_http=_budget(0))
        return _page(method, browse_id, items, cursor, order=order, layout=layout)
    first = _first_page(worker, browse_id, body, order)
    response, _, _, base = first
    orders, current = _orders(base, order, offered)
    container = _container(response)
    if container is None:
        kind, message = pages.detect(response)
        return _page(method, browse_id, [], None, orders, current, message=message if kind == "message" else None,
                     layout=layout)
    header = header_of(container) if header_of else None
    items, cursor = pages.fetch(worker.yt, "browse", body, "ctoken", lambda _: container, parse,
                                min_items=PAGE_ITEMS, max_http=_budget(first[1]), first=first)
    return _page(method, browse_id, items, cursor, orders, current, header=header, layout=layout)


def _each(contents, renderer, build, what):
    """``build(data)`` of every ``renderer`` entry; a malformed one is skipped."""
    out = []
    for content in contents or []:
        data = content.get(renderer) if isinstance(content, dict) else None
        if not isinstance(data, dict):
            continue
        try:
            value = build(data)
            value = raw.pin_fields(content, value)  # G2: each item's Pin to Listen again
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            norm.log(f"skipped malformed {what}: {type(exc).__name__}")
            continue
        if value is not None:
            out.append(value)
    return out


# ---- items ---------------------------------------------------------------------------------


def playlist_item(item):
    """A library playlist: norm.playlist plus the served subtitle (``description``,
    "Auto playlist" for those without an author) and ``special`` (liked,
    episodes, or None)."""
    value = norm.playlist(item)
    value["description"] = item.get("description") or None
    value["special"] = SPECIAL.get(value["playlistId"])
    return value


def _playlist(data):
    # The "New playlist" tile opens a dialog, not a playlist.
    if "createPlaylistEndpoint" in (data.get("navigationEndpoint") or {}):
        return None
    return playlist_item(parse_playlist(data))


def _playlists(contents):
    return _each(contents, MTRIR, _playlist, "library playlist")


def _album(data):
    # parse_albums leaves the explicit badge out (S3).
    return norm.album(dict(parse_albums([{MTRIR: data}])[0], isExplicit=raw.explicit(data.get("subtitleBadges"))))


def _albums(contents):
    return _each(contents, MTRIR, _album, "library album")


def artist_item(data):
    """A library artist or subscription row: norm.artist (MPLA stripped), or
    norm.profile for a user channel, with the row's own subtitle ("149
    songs", "4.11K subscribers") and YouTube's ``shuffleId`` / ``radioId``
    mixes from its menu."""
    parsed = parse_artists([{MRLIR: data}])[0]
    item = norm.profile(parsed) if parsed.get("type") == "channel" else norm.artist(parsed)
    column = get_flex_column_item(data, 1)
    item["subtitle"] = (raw.text(column.get("text")) if column else None) or item["subtitle"]
    item["shuffleId"] = parsed.get("shuffleId")
    item["radioId"] = parsed.get("radioId")
    return item


def _artists(contents):
    return _each(contents, MRLIR, artist_item, "library artist")


def _songs(contents):
    # raw.items leaves out the leading Shuffle all row (it plays no song).
    return [i for i in raw.items(contents) if i.get("type") == "track"]


def _shuffle_header(container):
    """``{shuffleId}`` from the songs page's leading row (a watch playlist, not
    a song), else ``{}``."""
    contents = container.get("contents") or []
    data = contents[0].get(MRLIR) if contents and isinstance(contents[0], dict) else None
    if not isinstance(data, dict) or nav(data, ["playlistItemData", "videoId"], True):
        return {}
    playlist_id = nav(data, ["navigationEndpoint", "watchPlaylistEndpoint", "playlistId"], True)
    return {"shuffleId": playlist_id} if playlist_id else {}


# ---- methods -----------------------------------------------------------------------------


def m_library_playlists(worker, params):
    """``{order?}``: every playlist in the library, ``{items, orders, order}``.
    Without an order, get_library_playlists(limit=None); with one, the same
    page re-sorted (its grid read to the end)."""
    worker.require_auth()
    order = order_param(params)
    yt = worker.yt
    if order is None:
        with raw.capture(("browse",)) as cap:
            items = yt.get_library_playlists(limit=None)
        orders, current = _orders(cap.first("browse"), None)
        items = norm.normalize_list(items, playlist_item, "library playlist")
        # G2: ytmusicapi reads no card menu; each pin comes from the grid pages it read.
        items = raw.with_card_pins(items, raw.card_pins(cap.all("browse")))
        return {"items": items, "orders": orders, "order": current}
    browse_id = BROWSE_IDS["libraryPlaylists"]
    body = _body(browse_id, order)
    first = _first_page(worker, browse_id, body, order)
    orders, current = _orders(first[3], order)
    container = _container(first[0])
    items = []
    if container is not None:
        items, _ = pages.fetch(yt, "browse", body, "ctoken", lambda _: container, _playlists,
                               max_http=PLAYLISTS_MAX_HTTP, first=first, enough=lambda got: False)
    return {"items": items, "orders": orders, "order": current}


def m_library_songs(worker, params):
    """``{order?, cursor?}``: songs saved to the library. The first page's
    ``header.shuffleId`` is YouTube's shuffle of all of them (Shuffle all)."""
    return _read_list(worker, params, "librarySongs", _songs, "shelf", header_of=_shuffle_header)


def m_library_albums(worker, params):
    return _read_list(worker, params, "libraryAlbums", _albums, "grid")


def m_library_artists(worker, params):
    """``{order?, cursor?}``: artists of the library's songs (orders include
    the served Most songs)."""
    return _read_list(worker, params, "libraryArtists", _artists, "shelf")


def m_library_subscriptions(worker, params):
    return _read_list(worker, params, "librarySubscriptions", _artists, "shelf")


def m_library_landing(worker, params):
    """``{order?, cursor?}``: the library landing grid (Recent activity):
    playlists, albums, artists and podcasts mixed, in the served orders
    (Recent activity, Recently saved, Recently played)."""
    return _read_list(worker, params, "libraryLanding", raw.items, "grid", offered=())


# ---- Shuffle all (tracksFor) ------------------------------------------------------------------


def is_shuffle(playlist_id):
    return isinstance(playlist_id, str) and playlist_id.startswith(SHUFFLE_ID)


def shuffle_tracks(worker, playlist_id, limit=SHUFFLE_TRACKS):
    """tracksFor's reply for the library's server shuffle: at most ``limit``
    (and SHUFFLE_TRACKS) songs, in YouTube's shuffled order."""
    worker.require_auth()
    limit = max(1, min(limit, SHUFFLE_TRACKS))
    yt = worker.yt
    body = {"playlistId": playlist_id, "params": SHUFFLE_PARAMS, "enablePersistentPlaylistPanel": True,
            "isAudioOnly": True, "tunerSettingValue": "AUTOMIX_SETTING_NORMAL"}
    panel = nav(yt._send_request("next", body), _PANEL, True)
    if not isinstance(panel, dict):
        raise WorkerError("UPSTREAM", "YouTube Music sent no shuffle of your library")
    tracks = parse_watch_tracks(panel.get("contents") or [])
    http = 1
    while len(tracks) < limit and http < SHUFFLE_MAX_HTTP:
        token = nav(panel, ["continuations", 0, "nextContinuationData", "continuation"], True)
        if not token:
            break
        response = yt._send_request("next", body, get_continuation_string(token))
        http += 1
        panel = nav(response, ["continuationContents", "playlistPanelContinuation"], True)
        page = parse_watch_tracks(panel.get("contents") or []) if isinstance(panel, dict) else []
        if not page:
            break
        tracks.extend(page)
    items, seen = [], set()
    for track in norm.normalize_list(tracks, norm.track, "library shuffle track"):
        if track["videoId"] not in seen:
            seen.add(track["videoId"])
            items.append(track)
    # A shuffle has no end: like a mix, what came back is the whole of it.
    return {"playlistId": playlist_id, "items": items[:limit], "complete": True, "total": None}


# ---- registry (merged by ytm.py) ------------------------------------------------------------

METHODS = {
    "libraryPlaylists": m_library_playlists,
    "librarySongs": m_library_songs,
    "libraryAlbums": m_library_albums,
    "libraryArtists": m_library_artists,
    "librarySubscriptions": m_library_subscriptions,
    "libraryLanding": m_library_landing,
}
# First pages only (the key holds the order); cursor pages are never cached.
CACHEABLE = set(METHODS)
LANES = {name: "pages" for name in METHODS}
INVALIDATES = {}
# Private pages: no recorders (fixtures would hold the library).
RECORDERS = {}
MUTATES = set()
