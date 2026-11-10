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

G1: a song plays on through the library in the list's order
(ordered_tracks), and Shuffle all goes on through the server shuffle
(shuffle_more), both read by tracksFor.

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
# 50 songs (two requests) are read; G1: the shell reads on from there
# (shuffle_more), as the web's queue does.
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
    a song), else ``{}``. G1: and ``playParams``, the params a song row plays
    the library in its order with (library_play_params)."""
    contents = container.get("contents") or []
    header = library_play_params(contents)
    data = contents[0].get(MRLIR) if contents and isinstance(contents[0], dict) else None
    if not isinstance(data, dict) or nav(data, ["playlistItemData", "videoId"], True):
        return header
    playlist_id = nav(data, ["navigationEndpoint", "watchPlaylistEndpoint", "playlistId"], True)
    return dict(header, shuffleId=playlist_id) if playlist_id else header


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
        return {"items": norm.normalize_list(items, playlist_item, "library playlist"), "orders": orders,
                "order": current}
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


def _shuffle_body(playlist_id):
    return {"playlistId": playlist_id, "params": SHUFFLE_PARAMS, "enablePersistentPlaylistPanel": True,
            "isAudioOnly": True, "tunerSettingValue": "AUTOMIX_SETTING_NORMAL"}


def is_shuffle(playlist_id):
    return isinstance(playlist_id, str) and playlist_id.startswith(SHUFFLE_ID)


def shuffle_tracks(worker, playlist_id, limit=SHUFFLE_TRACKS):
    """tracksFor's reply for the library's server shuffle: at most ``limit``
    (and SHUFFLE_TRACKS) songs, in YouTube's shuffled order."""
    worker.require_auth()
    limit = max(1, min(limit, SHUFFLE_TRACKS))
    items, seen = [], set()
    # G1: read by _new_shuffle; where the shuffle goes on (its next page, and
    # what was read past the limit) stays for the shell's next `more` read.
    token, lost = _new_shuffle(worker.yt, playlist_id, limit, items, seen)
    _remember_shuffle(worker, playlist_id, token, items[limit:], seen, lost)
    # A shuffle has no end: like a mix, what came back is the whole of it.
    return {"playlistId": playlist_id, "items": items[:limit], "complete": True, "total": None}


# ---- no ceilings (G1) --------------------------------------------------------------------------------
# A song row of the library plays the watch playlist SHUFFLE_ID ("MLCT") from
# its index without the shuffle params: the song, then the rest of the
# library in the list's order (what the web plays on a click). YouTube
# places the song by ``index`` (its place in the list, counting from 1:
# the Shuffle all row is 0) and builds the queue around that place, so the
# shell asks for the songs after any of them with its place:
# tracksFor {kind: "librarySongs", id: "MLCT", videoId, index, params}
# (ordered_tracks; params: the songs page's header.playParams).
#
# Shuffle all goes on through the server shuffle's own next page, which
# stays in this worker (one slot per worker and client, never in a reply,
# a log or the disk cache): tracksFor {kind: "playlist", id: "MLCT", more:
# true} reads the next songs of the last Shuffle all, or starts a new
# shuffle when this worker holds none (it restarted, or the account
# changed).
#
# Now and then YouTube answers a continuation with no song (seen live once
# in 24 shuffle reads). That is never taken for the end of either queue:
# Shuffle all goes on in a new shuffle, without the songs it gave, and the
# ordered queue answers `complete: false`, so the shell asks again from its
# last song.

ORDERED_TRACKS = 500
# The first response holds about 25 songs past the one asked for, each
# continuation 24: about 100 songs a call.
ORDERED_MAX_HTTP = 4
# Near the list's end the queue is asked from a song this many places
# earlier (the shell names it).
ORDERED_BACK = 40
_ROW_PLAY = ["overlay", "musicItemThumbnailOverlayRenderer", "content", "musicPlayButtonRenderer",
             "playNavigationEndpoint", "watchEndpoint"]
_PPVR = "playlistPanelVideoRenderer"
_PPVWR = "playlistPanelVideoWrapperRenderer"


def library_play_params(contents):
    """``{playParams}``: the params of the first song row's play endpoint
    (watch MLCT from that song), or ``{}``. Only the first response's rows
    carry them (the same on each), and their ``index`` counts within that
    response only, so the shell numbers songs by their place in the list."""
    for content in contents or []:
        data = content.get(MRLIR) if isinstance(content, dict) else None
        endpoint = nav(data, _ROW_PLAY, True) if isinstance(data, dict) else None
        if isinstance(endpoint, dict) and endpoint.get("playlistId") == SHUFFLE_ID and endpoint.get("videoId"):
            params = endpoint.get("params")
            return {"playParams": params} if isinstance(params, str) and params else {}
    return {}


def _next_token(panel):
    return nav(panel, ["continuations", 0, "nextContinuationData", "continuation"], True) \
        if isinstance(panel, dict) else None


def _panel_songs(panel):
    """``[(track, index)]`` of a playlist panel: each playable entry (its
    counterpart kept) with its watchEndpoint index (None when it has none)."""
    out = []
    for content in panel.get("contents") or []:
        if not isinstance(content, dict):
            continue
        primary = nav(content, [_PPVWR, "primaryRenderer"], True) if _PPVWR in content else content
        index = nav(primary, [_PPVR, "navigationEndpoint", "watchEndpoint", "index"], True) \
            if isinstance(primary, dict) else None
        try:
            parsed = parse_watch_tracks([content])
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            norm.log(f"skipped malformed library song: {type(exc).__name__}")
            continue
        if parsed:
            out.append((parsed[0], index if isinstance(index, int) and not isinstance(index, bool) else None))
    return out


def _ordered_panel(yt, body, video_id, look, budget):
    """``(entries, token, http, lost)`` of the MLCT queue ``body`` asks for,
    from the song ``video_id`` on; ``entries`` is None when it is not in the
    first response nor in the next ``look`` continuations (within
    ``budget`` requests). ``lost``: a response came with no song before the
    song was found, so whether it is there is not known."""
    panel = nav(yt._send_request("next", body), _PANEL, True)
    if not isinstance(panel, dict):
        raise WorkerError("UPSTREAM", "YouTube Music sent no queue for your library")
    entries, token, http, at = _panel_songs(panel), _next_token(panel), 1, None
    while entries:
        at = next((i for i, (track, _) in enumerate(entries) if track.get("videoId") == video_id), None)
        if at is not None or not token or look <= 0 or http >= budget:
            break
        look -= 1
        panel = nav(yt._send_request("next", body, get_continuation_string(token)),
                    ["continuationContents", "playlistPanelContinuation"], True)
        http += 1
        entries, token = _panel_songs(panel) if isinstance(panel, dict) else [], _next_token(panel)
    if not entries:
        return None, None, http, True
    if at is None:
        return None, None, http, False
    return entries[at:], token, http, False


def ordered_tracks(worker, video_id, index=None, params=None, limit=ORDERED_TRACKS, back=None):
    """tracksFor's reply for a library song played in the list's order: that
    song and the ones after it (the ones before it in YouTube's first page
    are left out), at most ``limit`` in about ORDERED_MAX_HTTP requests.
    Each song has its ``libraryIndex`` (its place, counted as ``index`` is),
    so the shell asks for the next ones from the last. ``complete`` is false
    while YouTube has more (past the list's end, YouTube's queue goes on
    with other songs of your library, as on the web, and while a response
    came with no song); no songs and complete when the song cannot be found
    near ``index``.

    YouTube places a song only at its own place (``index`` with that
    ``videoId``) and none within about 30 of the list's end (it answers the
    list's start instead). ``back`` (``(videoId, index)`` of a song some 40
    places earlier, ORDERED_BACK) is then asked instead, and its queue read
    on to the song."""
    worker.require_auth()
    limit = max(1, min(limit, ORDERED_TRACKS))
    yt = worker.yt
    body = {"videoId": video_id, "playlistId": SHUFFLE_ID, "enablePersistentPlaylistPanel": True,
            "isAudioOnly": True, "tunerSettingValue": "AUTOMIX_SETTING_NORMAL"}
    if params:
        body["params"] = params
    if index is not None:
        body["index"] = index
    entries, token, http, lost = _ordered_panel(yt, body, video_id, 0, ORDERED_MAX_HTTP)
    if entries is None and not lost and back:
        body = dict(body, videoId=back[0], index=back[1])
        entries, token, http, lost = _ordered_panel(yt, body, video_id, 3, ORDERED_MAX_HTTP + 1)
    if entries is None:
        # Not where ``index`` says (it left the library, or the list is in
        # another order): nothing after it can be told. Unless YouTube sent
        # no song: then the shell asks again from the same song.
        return {"playlistId": SHUFFLE_ID, "items": [], "complete": not lost, "total": None}
    while len(entries) < limit and token and http < ORDERED_MAX_HTTP:
        response = yt._send_request("next", body, get_continuation_string(token))
        http += 1
        panel = nav(response, ["continuationContents", "playlistPanelContinuation"], True)
        page = _panel_songs(panel) if isinstance(panel, dict) else []
        token = _next_token(panel)
        if not page:
            # Not the end: the shell asks again from the last song.
            lost = True
            break
        entries.extend(page)
    items, seen = [], set()
    for track, position in entries[:limit]:
        try:
            value = norm.track(track)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            norm.log(f"skipped malformed library song: {type(exc).__name__}")
            continue
        if value["videoId"] in seen:
            continue
        seen.add(value["videoId"])
        value["libraryIndex"] = position + 1 if position is not None else None
        items.append(value)
    complete = not token and not lost and len(entries) <= limit
    return {"playlistId": SHUFFLE_ID, "items": items, "complete": complete, "total": None}


def _shuffle_memo(worker, playlist_id):
    """The worker's Shuffle all slot for ``playlist_id`` under its current
    client, else None."""
    memo = getattr(worker, "library_shuffle", None)
    if not memo or memo["generation"] != worker.generation or memo["id"] != playlist_id:
        return None
    return memo


def _remember_shuffle(worker, playlist_id, token, pending, seen, lost=False):
    """``lost``: the shuffle's next page came with no song, so the next
    ``more`` starts a new shuffle (``token`` is None then)."""
    worker.library_shuffle = {"generation": worker.generation, "id": playlist_id, "token": token,
                              "pending": list(pending), "seen": set(seen), "lost": lost}


def _take_new(items, seen, page):
    """Adds the songs of ``page`` (parsed watch tracks) not in ``seen`` to
    ``items`` and ``seen``."""
    for track in norm.normalize_list(page, norm.track, "library shuffle track"):
        if track["videoId"] not in seen:
            seen.add(track["videoId"])
            items.append(track)


def _shuffle_pages(yt, body, token, limit, items, seen, http=0):
    """Reads the server shuffle on from its continuation ``token`` into
    ``items`` (as _take_new) while they are fewer than ``limit``, in at most
    SHUFFLE_MAX_HTTP requests (``http`` of them made already). ``(token,
    lost)``: where it goes on (None at its end), and true when a page came
    with no song, which is not its end."""
    while len(items) < limit and token and http < SHUFFLE_MAX_HTTP:
        response = yt._send_request("next", body, get_continuation_string(token))
        http += 1
        panel = nav(response, ["continuationContents", "playlistPanelContinuation"], True)
        page = parse_watch_tracks(panel.get("contents") or []) if isinstance(panel, dict) else []
        if not page:
            return None, True
        token = _next_token(panel)
        _take_new(items, seen, page)
    return token, False


def _new_shuffle(yt, playlist_id, limit, items, seen):
    """A new server shuffle of ``playlist_id``, its first page and on, read
    into ``items`` as _shuffle_pages reads (and answered as it is)."""
    body = _shuffle_body(playlist_id)
    panel = nav(yt._send_request("next", body), _PANEL, True)
    if not isinstance(panel, dict):
        raise WorkerError("UPSTREAM", "YouTube Music sent no shuffle of your library")
    page = parse_watch_tracks(panel.get("contents") or [])
    if not page:
        return None, True
    _take_new(items, seen, page)
    return _shuffle_pages(yt, body, _next_token(panel), limit, items, seen, 1)


def shuffle_more(worker, playlist_id, limit=SHUFFLE_TRACKS):
    """The next songs of the last Shuffle all (tracksFor ``more``): what was
    read past its limit, then its next pages, at most ``limit`` (and
    SHUFFLE_TRACKS) in SHUFFLE_MAX_HTTP requests, none it gave already.
    ``complete`` once the shuffle has no more (the whole library came). A
    worker that holds no shuffle of ``playlist_id`` starts a new one, which
    goes on (not complete): the common case, since the shell lets the
    worker go between songs while the panel is closed. The shell drops the
    songs it already queued. A page with no song is not the end: a new
    shuffle goes on from there (SHUFFLE_MAX_HTTP more requests), without the
    songs this one gave. A read that fails keeps the slot as it was."""
    worker.require_auth()
    memo = _shuffle_memo(worker, playlist_id)
    if memo is None:
        return dict(shuffle_tracks(worker, playlist_id, limit), complete=False)
    limit = max(1, min(limit, SHUFFLE_TRACKS))
    body = _shuffle_body(playlist_id)
    seen = set(memo["seen"])
    items = list(memo["pending"])
    token, lost = _shuffle_pages(worker.yt, body, memo["token"], limit, items, seen)
    lost = lost or memo["lost"]
    if lost and len(items) < limit:
        token, lost = _new_shuffle(worker.yt, playlist_id, limit, items, seen)
    _remember_shuffle(worker, playlist_id, token, items[limit:], seen, lost)
    return {"playlistId": playlist_id, "items": items[:limit],
            "complete": not token and not lost and len(items) <= limit, "total": None}


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
