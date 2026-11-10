"""Artist pages, everything on them, and their full discographies.

- ``artist({channelId})`` is one get_artist call (one HTTP request) with its
  raw response captured. ytmusicapi names only the shelves whose English
  titles it knows and drops the rest (Live performances, Featured on,
  Playlists by, From your library); the page keeps every row of the raw
  response, in the served order and with the served titles. The rows that
  ytmusicapi did parse are matched to its keys (songs, albums, ...) by their
  See all endpoint or by their items, never by title, so a section says what
  it holds (``key``); a row nothing matches is a generic section, and a row
  that fails to parse is left out. The header carries ``pin`` (S14): its
  menu's Listen again toggle (raw.header_pin), or None.
- ``artistAlbums({browseId, params, order?, artist?, cursor?})`` is an
  artist's list of albums, singles or shows (the See all of those shelves,
  ``MPAD...`` + params), read the way get_artist_albums reads it but a page
  (about 100 releases) at a time: ``cursor`` continues a long list. Its sort
  menu comes back as ``orders``; an option's ``index`` is its served title,
  which is sent back as ``order``.

Area module (see AREA_MODULES in ytm.py).
"""

from urllib.parse import parse_qs, urlparse

import ytm_normalize as norm
import ytm_pages as pages
import ytm_raw as raw
from ytm_proto import WorkerError, opt_cursor, opt_str, req_str

# get_artist's content keys, in the order they are shown when there is no
# raw response to follow.
KEYS = ("songs", "albums", "singles", "shows", "videos", "playlists", "related", "episodes", "podcasts")
# Titles for those sections when only get_artist's parse is at hand (the raw
# rows carry the served ones).
FALLBACK_TITLES = {
    "songs": "Top songs", "albums": "Albums", "singles": "Singles & EPs", "shows": "Audiobooks and shows",
    "videos": "Videos", "playlists": "Playlists", "related": "Fans might also like",
    "episodes": "Latest episodes", "podcasts": "Podcasts",
}
# Lists that only open with their params (an artist's discography pages).
_NEEDS_PARAMS = ("albums", "singles", "shows")
# What an album card without a type is in each list.
_DEFAULT_TYPE = {"albums": "Album", "singles": "Single"}


def _normalizer(key):
    if key in ("songs", "videos"):
        return norm.track
    if key in ("albums", "singles", "shows"):
        return norm.album
    if key == "playlists":
        return norm.playlist
    if key == "related":
        return norm.artist
    if key == "episodes":
        return norm.episode
    if key == "podcasts":
        return norm.podcast
    return None


def _item_id(item):
    """A normalized item's kind and id, e.g. ``album:MPREb_...``."""
    kind = item.get("type")
    value = {"track": item.get("videoId"), "album": item.get("browseId"), "playlist": item.get("playlistId"),
             "artist": item.get("channelId"), "profile": item.get("channelId"), "channel": item.get("channelId"),
             "podcast": item.get("browseId")}.get(kind)
    return f"{kind}:{value}" if value else None


def _ids(items):
    return {i for i in (_item_id(item) for item in items or []) if i}


def _fix_items(key, items):
    """What a key says about its items: videos are videos, an album card
    without a type is an album (or a single)."""
    out = []
    for item in items:
        if key == "videos" and item.get("type") == "track" and item.get("kind") == "song":
            item = dict(item, kind="video")
        elif item.get("type") == "album" and not item.get("albumType") and key in _DEFAULT_TYPE:
            item = dict(item, albumType=_DEFAULT_TYPE[key])
        out.append(item)
    return out


def _own_ids(channel_id, data):
    return {i for i in (channel_id, norm.strip_mpla(data.get("channelId"))) if i}


def _more(endpoint, own, key=None):
    """A See all endpoint worth a row: not this artist's own page again, and
    not a discography page without its params."""
    if not isinstance(endpoint, dict) or not endpoint.get("browseId"):
        return None
    browse_id, params = endpoint["browseId"], endpoint.get("params")
    if not params and (norm.strip_mpla(browse_id) in own or key in _NEEDS_PARAMS or browse_id.startswith("MPAD")):
        return None
    return {"browseId": browse_id, "params": params, "pageType": endpoint.get("pageType")}


def _key_items(key, data):
    """The normalized items ytmusicapi parsed for ``key``."""
    block = data.get(key) if isinstance(data.get(key), dict) else {}
    fn = _normalizer(key)
    return norm.normalize_list(block.get("results"), fn, f"artist {key} item") if fn else []


def _match(rows, data):
    """``{row index: key}``: which raw rows ytmusicapi parsed as which key.

    First by See all endpoint (browseId, and params when ytmusicapi kept
    them), then by items: ytmusicapi parsed this very response, so a key's
    items are the items of its row. Titles are never compared.
    """
    keys = [k for k in KEYS if isinstance(data.get(k), dict)]
    matched, taken = {}, set()
    for key in keys:
        want = data[key]
        if not want.get("browseId"):
            continue
        for i, row in enumerate(rows):
            more = (row or {}).get("more") or {}
            if i in matched or row is None or more.get("browseId") != want["browseId"]:
                continue
            if want.get("params") and want["params"] != more.get("params"):
                continue
            matched[i] = key
            taken.add(key)
            break
    for key in keys:
        ids = _ids(_key_items(key, data)) if key not in taken else set()
        if not ids:
            continue
        for i, row in enumerate(rows):
            if i in matched or row is None or not ids & _ids(row["items"]):
                continue
            matched[i] = key
            taken.add(key)
            break
    return matched


def _parse_rows(rows):
    """``raw.parse_row`` of each row; None for a row that failed to parse."""
    out = []
    for row in rows or []:
        try:
            out.append(raw.parse_row(row))
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            norm.log(f"skipped artist row: {type(exc).__name__}")
            out.append(None)
    return out


def _key_section(key, data, own):
    block = data[key]
    more = _more({"browseId": block.get("browseId"), "params": block.get("params")}, own, key)
    return norm.section(FALLBACK_TITLES[key], _fix_items(key, _key_items(key, data)), more, layout="carousel", key=key)


def sections(channel_id, data, rows):
    """The page's sections: the raw rows in order (matched ones carry their
    key), then any key ytmusicapi parsed that no row matched. Without raw
    rows, get_artist's keys in KEYS order."""
    own = _own_ids(channel_id, data)
    parsed = _parse_rows(rows)
    matched = _match(parsed, data)
    out = []
    for i, row in enumerate(parsed):
        if row is None or row["layout"] in (None, "description"):
            continue
        key = matched.get(i)
        items = row["items"]
        if not items and key:
            items = _key_items(key, data)  # a row whose items raw.item cannot read
        items = _fix_items(key, items)
        more = _more(row["more"], own, key)
        if items or more:
            out.append(norm.section(row["title"], items, more, row["play"], row["layout"], key=key))
    shown = set(matched.values())
    for key in KEYS:
        if key not in shown and isinstance(data.get(key), dict) and data[key].get("results"):
            section = _key_section(key, data, own)
            if section["items"]:
                out.append(section)
    return out


def _link(url):
    """A description link as it should be copied: YouTube's redirect wrapper
    (which carries a token) is replaced by its target."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    parsed = urlparse(url)
    if parsed.netloc.endswith("youtube.com") and parsed.path == "/redirect":
        target = (parse_qs(parsed.query).get("q") or [None])[0]
        return target if isinstance(target, str) and target.startswith(("http://", "https://")) else None
    return url


def _description_runs(runs):
    out = []
    for run in runs or []:
        if not isinstance(run, dict) or not isinstance(run.get("text"), str):
            continue
        link = _link(run.get("url"))
        out.append({"text": run["text"], "url": link} if link else {"text": run["text"]})
    return out


def header(channel_id, data):
    """What the page header and its buttons need. ``channelId`` is the page
    asked for; ``subscribeId`` is the channel get_artist names, which is what
    subscribing takes (it can differ)."""
    songs = data.get("songs") if isinstance(data.get("songs"), dict) else {}
    return {
        "channelId": channel_id,
        "subscribeId": data.get("channelId"),
        "subscribed": bool(data.get("subscribed")),
        "subscribers": data.get("subscribers"),
        "monthlyListeners": data.get("monthlyListeners"),
        "views": data.get("views"),
        "description": data.get("description") or None,
        "descriptionRuns": _description_runs(data.get("descriptionRuns")),
        # YouTube's own mixes for the Shuffle and Radio buttons (RDAO / RDEM).
        "shuffleId": data.get("shuffleId"),
        "radioId": norm.artist_radio_id(data),
        # The Top songs See all: the artist's whole songs playlist (VL...).
        "songsPlaylistId": songs.get("browseId"),
    }


def build(channel_id, data, rows=None):
    """The artist page from get_artist's parse and the raw rows it came from."""
    return norm.page("artist", channel_id, data.get("name") or "", thumbnails=data.get("thumbnails"),
                     sections=sections(channel_id, data, rows), header=header(channel_id, data), round=True)


# ---- reading the page ------------------------------------------------------------------
# get_artist parses the first row as Top songs only when it is a music shelf,
# and fails on a carousel without a header (signed in, Radiohead's page now
# opens with a headerless "Radiohead TV" card). The captured response is
# then parsed again by get_artist's own code, given rows it can take: the
# Top songs shelf first and the headerless carousels left out. No second
# request; the page still shows every row.

_PARSE_ERRORS = (KeyError, IndexError, TypeError, AttributeError)


class _Fetched:
    """Stands in for the client inside a ytmusicapi read method: hands back a
    response already fetched instead of sending a request."""

    def __init__(self, yt, response):
        self.parser = yt.parser
        self._response = response

    def _send_request(self, endpoint, body, additionalParams=""):
        return self._response


def _titled(row):
    data = row.get("musicCarouselShelfRenderer") if isinstance(row, dict) else None
    if data is None:
        return True
    header = (data.get("header") or {}).get("musicCarouselShelfBasicHeaderRenderer") or {}
    return bool(((header.get("title") or {}).get("runs") or [None])[0])


def _songs_moved(rows):
    shelves = [i for i, row in enumerate(rows) if isinstance(row, dict) and "musicShelfRenderer" in row]
    return bool(shelves) and shelves[0] != 0


def reparse(yt, channel_id, response):
    """get_artist's parse of an artist page already fetched, with the rows it
    can read (see above)."""
    from ytmusicapi import YTMusic

    rows = raw.rows(response)
    shelves = [row for row in rows if isinstance(row, dict) and "musicShelfRenderer" in row]
    rest = [row for row in rows if isinstance(row, dict) and "musicShelfRenderer" not in row and _titled(row)]
    fixed = {"header": response.get("header"), "contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
        {"tabRenderer": {"content": {"sectionListRenderer": {"contents": shelves[:1] + rest}}}}]}}}
    return YTMusic.get_artist(_Fetched(yt, fixed), channel_id)


def fetch(worker, channel_id):
    """One get_artist request, its raw rows captured, as a page."""
    yt = worker.yt
    data = None
    with raw.capture(("browse",)) as cap:
        try:
            data = yt.get_artist(channel_id)
        except _PARSE_ERRORS as exc:
            if cap.first("browse") is None:
                raise
            norm.log(f"get_artist could not read this artist page ({type(exc).__name__}); reading it again")
    response = cap.first("browse")
    rows = raw.rows(response)
    if data is None or _songs_moved(rows):
        data = reparse(yt, channel_id, response)
    page = build(channel_id, data, rows)
    # S14: Pin to / Unpin from Listen again (the header menu's), or None.
    page["header"]["pin"] = raw.header_pin(response)
    return page


def cached_or_fetch(worker, channel_id, needs):
    """The artist page from the disk cache when it holds ``header[needs]``,
    else a fresh one (an older copy, or one in an older shape, is read again)."""
    try:
        hit = worker.cache().get("artist", {"channelId": channel_id})
    except Exception:  # noqa: BLE001 - an unreadable cache is a miss
        hit = None
    page = hit[0] if hit is not None else None
    if isinstance(page, dict) and page.get("type") == "page" and (page.get("header") or {}).get(needs):
        return page
    return fetch(worker, channel_id)


def songs(page):
    """The Top songs shown on an artist page (its ``songs`` section)."""
    for section in page.get("sections") or []:
        if section.get("key") == "songs":
            return [item for item in section.get("items") or [] if item.get("type") == "track"]
    return []


def m_artist(worker, params):
    return fetch(worker, req_str(params, "channelId"))


# ---- artistAlbums ----------------------------------------------------------------------
# get_artist_albums(limit=None) follows the grid's continuations to the end
# of the list: Bach's singles are over 3000 releases, 31 requests in one
# call. Here a call reads the first grid page (about 100 releases; one
# request, two when sorted) and hands back the rest as a cursor (Load more),
# like browsePage. A served message (YouTube cannot show the list) is an
# empty page carrying it.

_ALBUMS_MAX_HTTP = 3


def _header_title(response):
    header = (response or {}).get("header") if isinstance(response, dict) else None
    data = next(iter(header.values()), {}) if isinstance(header, dict) and header else {}
    return raw.text(data.get("title")) if isinstance(data, dict) else None


def _grid(response):
    """The renderer holding the releases: the first row's grid (or carousel,
    which get_artist_albums also reads)."""
    key, data = raw.unwrap((raw.rows(response) or [None])[0])
    return data if key in ("gridRenderer", "musicCarouselShelfRenderer") and isinstance(data, dict) else None


def _releases(fallback):
    """``parse`` for pages.fetch: the items of a grid page, the releases
    credited to the page artist (``fallback``) when they name none."""
    def parse(contents):
        out = []
        for item in raw.items(contents):
            if item.get("type") == "album" and not item.get("artists") and fallback:
                item = dict(item, artists=list(fallback), artistText=norm.artist_text(fallback))
            out.append(item)
        return out
    return parse


def _first_page_artist(worker, browse_id, page_params, order):
    """The artist the list's first page named (its disk copy), for a later
    page: a continuation carries no header."""
    key = {"browseId": browse_id, "params": page_params}
    if order is not None:
        key["order"] = order
    try:
        hit = worker.cache().get("artistAlbums", key)
    except Exception:  # noqa: BLE001 - an unreadable cache is a miss
        hit = None
    page = hit[0] if hit is not None else None
    return page.get("subtitle") if isinstance(page, dict) else None


def _albums_page(browse_id, name, items, cursor=None, orders=None, order=None, message=None):
    sections = [] if message is not None else [norm.section(None, items, layout="grid")]
    return norm.page("artistAlbums", browse_id, None, subtitle=name, sections=sections, cursor=cursor,
                     orders=orders, order=order, message=message)


def m_artist_albums(worker, params):
    """``{browseId, params, order?, artist?, cursor?}``: a page of an artist's
    albums, singles or shows. ``order`` is the title of one of the served sort
    options (from ``orders``; case does not matter, as in get_artist_albums);
    ``artist`` names the artist when the page does not; ``cursor`` (from an
    earlier reply) reads the next page."""
    browse_id = req_str(params, "browseId")
    page_params = req_str(params, "params")
    order = opt_str(params, "order", high=200)
    artist_name = opt_str(params, "artist", high=200)
    cursor = opt_cursor(params)
    body = {"browseId": browse_id, "params": page_params}
    artist_id = browse_id[4:] if browse_id.startswith("MPAD") else None
    yt = worker.yt

    def credit(name):
        return [{"name": name, "id": artist_id}] if name else []

    if cursor:
        name = artist_name or _first_page_artist(worker, browse_id, page_params, order)
        items, cursor = pages.fetch(yt, "browse", body, "ctoken", None, _releases(credit(name)), cursor=cursor,
                                    max_http=_ALBUMS_MAX_HTTP)
        return _albums_page(browse_id, name, items, cursor, order=order)

    first = pages.first_page(yt, "browse", body)
    base, _, options, _ = first
    name = artist_name or _header_title(base)
    options = [o for o in options if o["title"]]
    current = next((o["title"] for o in options if o["selected"]), None)
    if pages.detect(base)[0] == "message":
        order = None  # nothing to sort: the message is the answer
    if order is not None:
        wanted = order.casefold()
        match = next((o for o in options if o["title"].casefold() == wanted), None)
        if match is None:
            raise WorkerError("BAD_REQUEST", "This list cannot be sorted")
        first = (pages.resort(yt, "browse", body, base, match["index"]), 2, first[2], base)
        current = match["title"]
    orders = [{"index": o["title"], "title": o["title"], "selected": o["title"] == current} for o in options]
    kind, message = pages.detect(first[0])
    if kind in ("message", "empty"):
        return _albums_page(browse_id, name, [], orders=orders, order=current,
                            message=(message or None) if kind == "message" else None)
    grid = _grid(first[0])
    if grid is None:
        raise WorkerError("UPSTREAM", "Unexpected response from YouTube Music (ytmusicapi may need an update)")
    items, cursor = pages.fetch(yt, "browse", body, "ctoken", lambda _: grid, _releases(credit(name)),
                                max_http=_ALBUMS_MAX_HTTP, first=first)
    return _albums_page(browse_id, name, items, cursor, orders, current)


# ---- recorders (anonymous) -------------------------------------------------------------


def record_artist(yt, params):
    return yt.get_artist(params["channelId"])


# Parts of a raw row no parser here reads (menus are a third of the page).
_TRIM = ("menu", "clickTrackingParams", "accessibility", "loggingContext", "thumbnailCrop", "thumbnailScale")


def _trim(value):
    if isinstance(value, dict):
        return {k: _trim(v) for k, v in value.items() if k not in _TRIM}
    if isinstance(value, list):
        return [_trim(v) for v in value]
    return value


def record_artist_raw(yt, params):
    """The rows of the raw artist page get_artist read, without the header and
    the menus, for fixtures of the shelves ytmusicapi drops."""
    raw.install(yt)
    with raw.capture(("browse",)) as cap:
        yt.get_artist(params["channelId"])
    return {"rows": _trim(raw.rows(cap.first("browse")))}


def record_artist_albums(yt, params):
    """``{channelId, key?}``: the raw first page of the artist's ``key`` list
    (albums by default), for artistAlbums fixtures. The releases lose their
    menus; the sort menu above them stays."""
    block = yt.get_artist(params["channelId"])[params.get("key", "albums")]
    response = yt._send_request("browse", {"browseId": block["browseId"], "params": block["params"]})
    rows = raw.rows(response)
    rows[:] = _trim(rows)
    return response


# ---- registry (merged by ytm.py) -------------------------------------------------------

METHODS = {"artist": m_artist, "artistAlbums": m_artist_albums}
CACHEABLE = {"artist", "artistAlbums"}
# artist is one request, answered in order like album; a discography and
# its Load more pages read on the pages lane.
LANES = {"artistAlbums": "pages"}
INVALIDATES = {}
RECORDERS = {"artist": record_artist, "artistRaw": record_artist_raw, "artistAlbums": record_artist_albums}
MUTATES = set()
