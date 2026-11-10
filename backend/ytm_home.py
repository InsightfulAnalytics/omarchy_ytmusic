"""Home (S5): the whole feed, its mood chips and each shelf's Play all.

- ``homeFeed({params?, cursor?})`` reads the raw ``FEmusic_home`` browse
  (``params``: a chip's, for Relax, Workout, Podcasts...). Every row is
  parsed on its own (``raw.parse_row``), so a row ytmusicapi cannot read
  never shifts or drops the ones after it, and each shelf keeps its See all
  (``more``) and Play all (``play``) endpoints, which ``get_home`` drops.
  The first call reads pages of shelves (``sectionListContinuation``, three
  shelves each) until it has FIRST_SHELVES or has spent MAX_HTTP requests;
  the rest comes through ``cursor``, a page of MORE_SHELVES at a time. The
  chips are read from the section list's header, as served. Returns
  ``{shelves, chips, cursor, hasMore}``; shelves have the page section
  shape (``norm.section``), plus ``strapline`` when the web shows a line
  above the title (SIMILAR TO above an artist's name). When the first page
  cannot be read at all (no rows, or none it could parse), All falls back
  to ``get_home`` (shelves without See all or Play all, no chips); a chip
  fails instead, since get_home has no chips.
- ``shelfQueue({videoId?, playlistId?, params?})`` is a shelf's Play all:
  the list YouTube generates from the shelf's play endpoint (one ``next``
  request), parsed by ytmusicapi's watch-playlist parser.

Like ytm_pages.py this reads ytmusicapi internals (paths, parsers), pinned
to 1.12.2. Area module (see AREA_MODULES in ytm.py).
"""

from ytmusicapi.navigation import nav
from ytmusicapi.parsers.watch import parse_watch_playlist

import ytm_collections as collections
import ytm_normalize as norm
import ytm_pages as pages
import ytm_raw as raw
from ytm_proto import WorkerError, opt_cursor, opt_str

HOME = "FEmusic_home"
# The first call: about three pages of three shelves, at most MAX_HTTP
# requests. A cursor call reads one page (MORE_SHELVES) in one request.
FIRST_SHELVES = 8
MORE_SHELVES = 3
MAX_HTTP = 3
# The fallback reads get_home's rows (it follows every continuation).
LEGACY_ROWS = 20
CHIPS = ["header", "chipCloudRenderer", "chips"]
PLAYLIST_PANEL = [*collections.QUEUE_ITEMS[:-1]]


class Unreadable(Exception):
    """The home response has no rows to read (its shape changed)."""


# What a chip's unreadable feed answers (the worker's own wording for a parse failure).
UNREADABLE = "Unexpected response from YouTube Music (ytmusicapi may need an update)"


# The carousels whose header can hold a strapline.
CAROUSELS = ("musicCarouselShelfRenderer", "musicImmersiveCarouselShelfRenderer")


# ---- the feed ------------------------------------------------------------------------


def chips(section_list):
    """``[{title, params, selected}]`` of the chip cloud above the shelves."""
    out = []
    served = nav(section_list, CHIPS, True) if isinstance(section_list, dict) else None
    for chip in served if isinstance(served, list) else []:
        data = chip.get("chipCloudChipRenderer") if isinstance(chip, dict) else None
        if not isinstance(data, dict):
            continue
        browse = raw.browse_endpoint(data.get("navigationEndpoint"))
        title = raw.text(data.get("text"))
        # Only chips that reload Home itself (the All chip is the page without params).
        if not title or not browse or browse["browseId"] != HOME or not browse["params"]:
            continue
        out.append({"title": title, "params": browse["params"], "selected": data.get("isSelected") is True})
    return out


def strapline(row):
    """The line above a carousel's title, or None. Kept when the title opens
    an artist (SIMILAR TO above an artist's name) or the header is plain
    text (a theme above an editorial shelf: no picture, no link). Listen
    again puts the account's name and avatar above a title that links to
    its page; that one stays out of the feed and its disk copy."""
    key, data = raw.unwrap(row)
    if key not in CAROUSELS or not isinstance(data, dict):
        return None
    header = data.get("header")
    header = next(iter(header.values()), None) if isinstance(header, dict) and header else None
    if not isinstance(header, dict):
        return None
    line = raw.text(header.get("strapline"))
    # Without a title, parse_row already took the strapline as the title.
    if not line or not raw.text(header.get("title")):
        return None
    target = raw.browse_endpoint(nav(header, ["title", "runs", 0, "navigationEndpoint"], True))
    if target and target["pageType"] == raw.ARTIST_PAGE:
        return line
    return None if target or header.get("thumbnail") else line


def parse_rows(contents):
    """One entry per row, unreadable rows included (as empty entries marked
    ``unreadable``): a page of rows is never mistaken for the end of the
    feed, and no row shifts another."""
    out = []
    for row in contents or []:
        try:
            parsed = raw.parse_row(row)
            parsed["strapline"] = strapline(row)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            norm.log(f"skipped a home row: {type(exc).__name__}")
            parsed = {"title": None, "items": [], "more": None, "play": None, "layout": None, "unreadable": True}
        out.append(parsed)
    return out


def shelf(row):
    """The section a parsed row makes, or None when it shows nothing (an
    unknown renderer, a row without items, See all or Play all)."""
    if row.get("layout") in (None, "description"):
        return None
    if not row["items"] and not row["more"] and not row["play"]:
        return None
    out = norm.section(row["title"], row["items"], row["more"], row["play"], row["layout"])
    if row.get("strapline"):
        out["strapline"] = row["strapline"]
    return out


def shelves(rows):
    return [s for s in (shelf(row) for row in rows) if s is not None]


def _enough(count):
    return lambda rows: sum(1 for row in rows if shelf(row) is not None) >= count


def _budget(counted):
    """fetch's max_http so the whole call stays within MAX_HTTP: a client's
    first request may be preceded by another one (a signed-out client asks
    for its visitor id first). ``counted`` is what fetch already counts."""
    spent = max(0, raw.http_count() - counted)
    return max(counted, 1, MAX_HTTP - spent)


def _body(params):
    body = {"browseId": HOME}
    chip = opt_str(params, "params")
    if chip:
        body["params"] = chip
    return body


def feed(chip_shelves, chip_list, cursor):
    return {"shelves": chip_shelves, "chips": chip_list, "cursor": cursor, "hasMore": cursor is not None}


def legacy(worker):
    """get_home's rows as shelves: what Home showed before S5."""
    return [norm.section(s["title"] or None, s["items"], layout="carousel")
            for s in norm.home(worker.yt.get_home(limit=LEGACY_ROWS))]


def m_home_feed(worker, params):
    body = _body(params)
    cursor = opt_cursor(params)
    yt = worker.yt
    if cursor:
        rows, cursor = pages.fetch(yt, "browse", body, "ctoken", None, parse_rows, cursor=cursor,
                                   max_http=_budget(0), enough=_enough(MORE_SHELVES))
        return feed(shelves(rows), [], cursor)
    response = pages._send(yt, "browse", body)
    # pages.fetch's `first`: (response, requests, sort options, unsorted response).
    first = (response, 1, [], response)
    try:
        container = raw.section_list(response)
        if not isinstance(container, dict) or not isinstance(container.get("contents"), list):
            raise Unreadable("no section list")
        chip_list = chips(container)
        rows, cursor = pages.fetch(yt, "browse", body, "ctoken", lambda _: container, parse_rows, first=first,
                                   max_http=_budget(1), enough=_enough(FIRST_SHELVES))
        read = shelves(rows)
        # Rows came but none could be read: as unreadable as no rows at all.
        if not read and any(row.get("unreadable") for row in rows):
            raise Unreadable("no row could be read")
    except (Unreadable, KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        # get_home reads All only: a chip's feed fails (not cached) rather
        # than showing All's shelves under that chip.
        if "params" in body:
            norm.log(f"home chip feed unreadable: {type(exc).__name__}")
            raise WorkerError("UPSTREAM", UNREADABLE) from exc
        norm.log(f"home feed unreadable, reading get_home instead: {type(exc).__name__}")
        return feed(legacy(worker), [], None)
    return feed(read, chip_list, cursor)


# ---- Play all --------------------------------------------------------------------------


def queue_body(params):
    """The ``next`` body of a shelf's Play all (the web's own, audio only)."""
    video_id = opt_str(params, "videoId")
    playlist_id = opt_str(params, "playlistId")
    if not video_id and not playlist_id:
        raise WorkerError("BAD_REQUEST", "give 'videoId' or 'playlistId'")
    body = {}
    if video_id:
        body["videoId"] = video_id
    if playlist_id:
        body["playlistId"] = playlist_id
    shelf_params = opt_str(params, "params")
    if shelf_params:
        body["params"] = shelf_params
    body["isAudioOnly"] = True
    body["enablePersistentPlaylistPanel"] = True
    return body


def queue_tracks(response):
    """``(playlistId, tracks)`` of a ``next`` response's queue: each entry
    parsed by ytmusicapi's watch-playlist parser on its own, so one it cannot
    read is skipped rather than failing the list."""
    panel = nav(response, PLAYLIST_PANEL, True) if isinstance(response, dict) else None
    if not isinstance(panel, dict):
        raise WorkerError("UPSTREAM", "YouTube Music sent no list to play")
    explicit = collections.explicit_videos(response)
    tracks = []
    for entry in panel.get("contents") or []:
        try:
            parsed = parse_watch_playlist([entry])
        except (KeyError, TypeError, IndexError, AttributeError) as exc:
            norm.log(f"skipped a shelf track: {type(exc).__name__}")
            continue
        tracks.extend(dict(t, isExplicit=True) if t.get("videoId") in explicit else t for t in parsed)
    playlist_id = panel.get("playlistId") or next(
        (nav(t, ["playlistPanelVideoRenderer", "navigationEndpoint", "watchEndpoint", "playlistId"], True)
         for t in panel.get("contents") or [] if isinstance(t, dict)), None)
    return playlist_id, norm.normalize_list(tracks, norm.track, "shelf track")


def m_shelf_queue(worker, params):
    response = pages._send(worker.yt, "next", queue_body(params))
    playlist_id, items = queue_tracks(response)
    return {"playlistId": playlist_id, "items": items}


# ---- recorder ---------------------------------------------------------------------------


# Parts of a raw row nothing here reads (overlays hold the play endpoints
# of songs and episodes, so they stay).
_TRIM = ("menu", "trackingParams", "clickTrackingParams", "accessibility", "accessibilityData", "loggingContext",
         "thumbnailCrop", "thumbnailScale")


def _trim(value):
    if isinstance(value, dict):
        if "playNavigationEndpoint" in value:  # a play button: only its endpoint is read
            return {"playNavigationEndpoint": _trim(value["playNavigationEndpoint"])}
        # One size of each picture is enough to parse.
        return {k: _trim(v[-1:] if k == "thumbnails" and isinstance(v, list) else v)
                for k, v in value.items() if k not in _TRIM}
    if isinstance(value, list):
        return [_trim(v) for v in value]
    return value


def record_home_raw(yt, params):
    """``{pages?, chip?}``: the raw Home feed, menus and tracking trimmed:
    ``first`` is the first response (the chip cloud and its rows), ``pages``
    the rows of the next ``pages`` - 1 continuations (bare, since the
    recorder's scrub drops every key that names a continuation), and with
    ``chip`` (an index into the served chips) ``chip`` is that chip's
    ``{params, first}``."""
    from ytmusicapi.continuations import get_continuation_string

    count = int(params.get("pages", 3))
    body = {"browseId": HOME}
    first = yt._send_request("browse", dict(body))
    out = {"first": _trim({"contents": first.get("contents")}), "pages": []}
    response = first
    while len(out["pages"]) + 1 < count:
        token = nav(raw.section_list(response), ["continuations", 0, "nextContinuationData", "continuation"], True)
        if not token:
            break
        response = yt._send_request("browse", dict(body), get_continuation_string(token))
        out["pages"].append(_trim(raw.rows(response)))
    if params.get("chip") is not None:
        chip = chips(raw.section_list(first))[int(params["chip"])]
        response = yt._send_request("browse", dict(body, params=chip["params"]))
        out["chip"] = {"params": chip["params"], "first": _trim({"contents": response.get("contents")})}
    return out


# ---- registry (merged by ytm.py) --------------------------------------------------------

METHODS = {"homeFeed": m_home_feed, "shelfQueue": m_shelf_queue}
# The first page of each chip is cached (the key holds the chip's params);
# cursor pages never are.
CACHEABLE = {"homeFeed"}
# Up to three requests a call, beside the other page reads. shelfQueue is one
# request, answered in order like radio.
LANES = {"homeFeed": "pages"}
INVALIDATES = {}
RECORDERS = {"homeRaw": record_home_raw}
MUTATES = set()
