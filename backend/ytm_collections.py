"""Albums, playlists, mixes, Liked Music and History (S3).

- ``playlist({playlistId, limit?, related?})``: a playlist's songs and
  header. ``limit`` is 200 by default (ytmusicapi reads 100 songs a request
  and stops once it has the limit, so a first read holds 200 to 300) or
  ``"all"``: every song, at most MAX_TRACKS (a whole read takes up to about
  50 requests). ``related`` adds the Related playlists shelf (one more
  request; two on your own playlist, where ytmusicapi reads the suggestions
  first), when it can be read. A mix id (``norm.is_mix``: RD..., not RDCLAK
  or RDPN) reads the watch playlist instead: kind ``mix``, its first
  MIX_TRACKS songs (explicit ones marked from the responses), titled as the
  queue header names it.
- ``liked({limit?, filter?})``: Liked Music, read the same way (signed in).
  G4: its mood and genre chips (Party, Chill, Rock, ...) as ``filters``
  (``[{index, title, selected}]``, titles as served; ``index`` is the
  chip's served id, its uniqueId) and ``filter`` (the chosen one, None for
  all); ``filter`` reads the page and reloads it with that chip (two
  requests), then reads on like a playlist, so Load all (``limit: "all"``)
  works on the filtered list too. The chips' reload tokens never leave the
  worker.
- ``album({browseId})``: the songs with their numbers, the header (artists,
  description, likeStatus, audio playlist) and the Other versions and
  recommendations shelves, under their served titles.
- Album, playlist and Liked Music headers carry ``pin`` (S14): the header
  menu's Listen again toggle, ``{pinned, listenAgainTokens: {pin, unpin}}``
  (raw.header_pin), or None.
- ``history({limit?})``: the last songs played (200, all YouTube serves in
  one request), each with its ``played`` group (Today, Yesterday, ...).

Whether a list was cut short is read from the responses themselves (a
continuation left after the last page of songs), never from the counts
alone (see norm.truncated). Whole reads run on the pages lane; the shell
gives them 120 s.

Area module (see AREA_MODULES in ytm.py). Like ytm_library.py it reads
ytmusicapi internals (continuation paths), pinned to 1.12.2.
"""

from ytmusicapi.continuations import CONTINUATION_ITEMS, get_continuation_string, get_continuation_token
from ytmusicapi.navigation import (RESPONSIVE_HEADER, SECTION_LIST_CONTINUATION, SECTION_LIST_ITEM, TAB_CONTENT,
                                   THUMBNAILS, TWO_COLUMN_RENDERER, nav)
from ytmusicapi.parsers.playlists import parse_playlist_items

import ytm_actions as actions
import ytm_normalize as norm
import ytm_raw as raw
from ytm_proto import WorkerError, opt_bool, opt_int, opt_limit, req_str

# A playlist's or Liked Music's first read; "all" reads up to MAX_TRACKS.
PAGE_TRACKS = 200
MAX_TRACKS = 5000
# A mix is its watch playlist's first songs (what tracksFor plays too).
MIX_TRACKS = 50
HISTORY_TRACKS = 200

# Where a playlist page's first songs are; later pages come as
# CONTINUATION_ITEMS (what get_playlist itself reads).
PLAYLIST_ITEMS = ["contents", "twoColumnBrowseResultsRenderer", "secondaryContents", "sectionListRenderer",
                  "contents", 0, "musicPlaylistShelfRenderer", "contents"]
QUEUE_HEADER = ["contents", "singleColumnMusicWatchNextResultsRenderer", "tabbedRenderer",
                "watchNextTabbedResultsRenderer", "tabs", 0, "tabRenderer", "content", "musicQueueRenderer",
                "header", "musicQueueHeaderRenderer"]
# A watch playlist's songs: the first response's, then each continuation's.
QUEUE_ITEMS = [*QUEUE_HEADER[:-2], "content", "playlistPanelRenderer", "contents"]
QUEUE_CONTINUATION = ["continuationContents", "playlistPanelContinuation", "contents"]
# ytmusicapi files an album's carousels by item size, not title; so does this.
ALBUM_SHELVES = {"COLLECTION_STYLE_ITEM_SIZE_MEDIUM": "otherVersions", "COLLECTION_STYLE_ITEM_SIZE_SMALL": "releases"}


def _strip_vl(playlist_id):
    return playlist_id[2:] if playlist_id.startswith("VL") else playlist_id


def _carousel_title(response):
    """The title of the first carousel in a sectionList continuation, or None."""
    for row in raw.rows(response):
        key, data = raw.unwrap(row)
        if isinstance(data, dict) and key in ("musicCarouselShelfRenderer", "musicImmersiveCarouselShelfRenderer"):
            return raw.row_parts(key, data)[1]
    return None


class _Reading:
    """What a playlist read showed beyond ytmusicapi's result, response by
    response (raw.capture ``each``, so a whole read keeps no responses):
    whether a continuation was left after the last page of songs, and the
    related shelf's served title."""

    def __init__(self):
        self.more = None
        self.related_title = None
        # S4: the header's library toggle (saved or not; None when it has none).
        self.saved = None
        # S14: the header's Listen again pin (raw.header_pin), or None.
        self.pin = None
        # G2: the related shelf's card pins (raw.card_pins).
        self.card_pins = {}

    def __call__(self, endpoint, response):
        if not isinstance(response, dict):
            return
        if self.saved is None:
            self.saved = actions.library_toggle(response)
        if self.pin is None:
            self.pin = raw.header_pin(response)  # S14
        songs = nav(response, PLAYLIST_ITEMS, True) or nav(response, CONTINUATION_ITEMS, True)
        if songs:
            self.more = get_continuation_token(songs) is not None
        elif self.related_title is None and nav(response, SECTION_LIST_CONTINUATION, True):
            self.related_title = _carousel_title(response)
            self.card_pins.update(raw.card_pins([response]))  # G2


def _read(fetch):
    """``fetch()`` under a fresh _Reading: (its result, the reading)."""
    reading = _Reading()
    with raw.capture(("browse",), each=reading):
        return fetch(), reading


def read_list(worker, kind, playlist_id, limit, related=False):
    """A playlist or Liked Music (``kind``) as a collection. ``limit`` None
    reads every song up to MAX_TRACKS; a read that stops there is ``capped``
    (and still ``truncated``). The related shelf is best-effort: a playlist
    whose shelf cannot be read comes without it."""
    asked = MAX_TRACKS if limit is None else limit
    if kind == "liked":
        data, reading = _read(lambda: worker.yt.get_liked_songs(limit=asked))
    elif related:
        try:
            data, reading = _read(lambda: worker.yt.get_playlist(playlist_id, limit=asked, related=True))
        except (KeyError, IndexError, TypeError) as exc:
            # ytmusicapi reads the shelf before the songs, and on your own
            # playlist wants a continuation after the suggestions that an
            # empty one does not have. Read it again without the shelf (an
            # error that is not the shelf's comes back from this read).
            norm.log(f"related playlists skipped: {type(exc).__name__}")
            data, reading = _read(lambda: worker.yt.get_playlist(playlist_id, limit=asked))
    else:
        # related only when asked: it costs a request or two.
        data, reading = _read(lambda: worker.yt.get_playlist(playlist_id, limit=asked))
    page = norm.collection(kind, data, playlist_id, limit=asked, more=reading.more,
                           titles={"related": reading.related_title})
    if kind == "playlist" and reading.saved is not None:
        # S4: a playlist that is not yours says whether it is in the library.
        page["header"]["likeStatus"] = actions.like_status(reading.saved)
    # S14: Pin to / Unpin from Listen again (the page menu's).
    page["header"]["pin"] = reading.pin
    shelf_pins(page, reading.card_pins)  # G2
    if limit is None:
        page["capped"] = page["truncated"]
        if len(page["tracks"]) > MAX_TRACKS:
            # ytmusicapi does not count the first page (100) against the
            # limit, so it may read up to 100 songs past the cap, even when
            # nothing was left to read: the songs cut here are not shown,
            # whatever the responses said.
            page["tracks"] = page["tracks"][:MAX_TRACKS]
            page["truncated"] = page["capped"] = True
            page["unavailable"] = None
    return page


def explicit_videos(response):
    """The videoIds of a watch playlist response's songs that carry the
    explicit badge (ytmusicapi's watch tracks have no isExplicit)."""
    out = set()
    if not isinstance(response, dict):
        return out
    contents = nav(response, QUEUE_ITEMS, True) or nav(response, QUEUE_CONTINUATION, True) or []
    for entry in contents if isinstance(contents, list) else []:
        if not isinstance(entry, dict):
            continue
        # A song with a video counterpart is wrapped; the song is the primary.
        entry = nav(entry, ["playlistPanelVideoWrapperRenderer", "primaryRenderer"], True) or entry
        data = entry.get("playlistPanelVideoRenderer") if isinstance(entry, dict) else None
        if isinstance(data, dict) and data.get("videoId") and raw.explicit(data.get("badges")):
            out.add(data["videoId"])
    return out


# ---- G4: explicit badges on watch playlists ------------------------------------------------
# ytmusicapi's watch tracks have no isExplicit: Start radio, autoplay and a
# tuner chip's queue mark theirs from the 'next' responses they read.


def mark_explicit(tracks, responses):
    """``tracks`` (ytmusicapi watch tracks) with ``isExplicit`` set on the
    songs any of ``responses`` (raw 'next' responses, continuation pages
    included) badges explicit; the others as they are."""
    badged = set()
    for response in responses or ():
        badged |= explicit_videos(response)
    return [dict(t, isExplicit=True) if isinstance(t, dict) and t.get("videoId") in badged else t
            for t in tracks or []]
# ---- end G4


def read_mix(worker, mix_id):
    """A mix as the web plays it: its watch playlist's first MIX_TRACKS songs."""
    seen = {"explicit": set()}

    def header(endpoint, response):
        if "title" not in seen:
            seen["title"] = raw.text(nav(response, [*QUEUE_HEADER, "subtitle"], True)) if isinstance(response, dict) else None
        seen["explicit"] |= explicit_videos(response)

    with raw.capture(("next",), each=header):
        # ytmusicapi's limit is a minimum (it reads whole continuation pages).
        data = worker.yt.get_watch_playlist(playlistId=mix_id, limit=MIX_TRACKS)
    tracks = [dict(t, isExplicit=True) if isinstance(t, dict) and t.get("videoId") in seen["explicit"] else t
              for t in list(data.get("tracks") or [])[:MIX_TRACKS]]
    return norm.collection("mix", {"title": seen.get("title"), "tracks": tracks}, mix_id)


def album_titles(response):
    """Served titles of an album page's shelves under the songs, by the key
    ytmusicapi files them under (their item size)."""
    titles = {}
    for row in raw.rows(response):
        key, data = raw.unwrap(row)
        if key != "musicCarouselShelfRenderer" or not isinstance(data, dict):
            continue
        shelf = ALBUM_SHELVES.get(data.get("itemSize"))
        if shelf and shelf not in titles:
            titles[shelf] = raw.row_parts(key, data)[1]
    return titles


def m_playlist(worker, params):
    playlist_id = _strip_vl(req_str(params, "playlistId"))
    limit = opt_limit(params, "limit", PAGE_TRACKS, high=MAX_TRACKS)
    related = opt_bool(params, "related")
    if norm.is_mix(playlist_id):
        return read_mix(worker, playlist_id)
    return read_list(worker, "playlist", playlist_id, limit, related)


# ---- G4: Liked Music's mood and genre chips ------------------------------------------------
# The page's chipCloud (sectionList header): each chip reloads the songs
# shelf filtered to it (browseSectionListReloadEndpoint). The chips are
# made from your likes and their order changes between reads (live, two
# chips swapped places a minute apart), so a chip is named by its served
# uniqueId (its title when it has none), echoed back as served, never read
# for meaning. The reply after a reload serves a narrower set (a Clear
# filters chip, the chosen one, refinements), so the choices offered are
# always the plain page's, one at a time.

LIKED_BROWSE_ID = "VLLM"
_RELOAD = ["browseSectionListReloadEndpoint", "continuation", "reloadContinuationData", "continuation"]
_LIKED_SHELF = ["continuationContents", "sectionListContinuation", "contents", 0, "musicPlaylistShelfRenderer"]


def liked_chips(response):
    """``[(id, title, token)]`` of a Liked Music page's chips that reload
    the list (a chip without a title, such as Clear filters, is left out);
    ``id`` is the chip's uniqueId, else its title. The first of an id wins."""
    found = raw.section_list(response)
    chips = nav(found, ["header", "chipCloudRenderer", "chips"], True) if isinstance(found, dict) else None
    out = []
    seen = set()
    for chip in chips or []:
        data = chip.get("chipCloudChipRenderer") if isinstance(chip, dict) else None
        title = raw.text(data.get("text")) if isinstance(data, dict) else None
        token = nav(data, ["navigationEndpoint", *_RELOAD], True) if title else None
        unique = data.get("uniqueId") if title else None
        chip_id = unique if isinstance(unique, str) and unique else title
        if title and token and chip_id not in seen:
            seen.add(chip_id)
            out.append((chip_id, title, token))
    return out


def liked_filters(response, chosen=None):
    """``[{index, title, selected}]`` of a Liked Music page's chips
    (``index``: the chip's id); ``chosen`` is the selected one."""
    return [{"index": chip_id, "title": title, "selected": chip_id == chosen}
            for chip_id, title, _ in liked_chips(response)]


def _filter_id(params):
    value = params.get("filter")
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 200:
        raise WorkerError("BAD_REQUEST", "'filter' must be the id of one of Liked Music's chips")
    return value


def read_liked_filtered(worker, chosen, limit):
    """Liked Music filtered to the chip whose id is ``chosen``: the page
    (its header and chips), the reload with that chip, then its later pages
    (body continuations, as get_playlist reads them) until ``limit`` songs
    are read (None: every one, at most MAX_TRACKS). YouTube serves only
    Liked Music's own count, so a filtered list cut short has none. A chip
    the page no longer offers is NOT_FOUND."""
    yt = worker.yt
    body = {"browseId": LIKED_BROWSE_ID}
    base = yt._send_request("browse", dict(body))
    token = next((t for chip_id, _, t in liked_chips(base) if chip_id == chosen), None)
    if token is None:
        raise WorkerError("NOT_FOUND", "Liked Music does not offer that chip now")
    response = yt._send_request("browse", dict(body), get_continuation_string(token))
    shelf = nav(response, _LIKED_SHELF, True)
    if not isinstance(shelf, dict):
        raise WorkerError("UPSTREAM", "YouTube Music sent no songs for that chip")
    asked = MAX_TRACKS if limit is None else limit
    contents = shelf.get("contents") or []
    tracks = parse_playlist_items(contents)
    token = get_continuation_token(contents)
    while token and len(tracks) < asked:
        more = nav(yt._send_request("browse", {"continuation": token}), CONTINUATION_ITEMS, True) or []
        parsed = parse_playlist_items(more) if more else []
        if not parsed:
            token = None
            break
        tracks.extend(parsed)
        token = get_continuation_token(more)
    # The header get_playlist reads (title and cover); Liked Music's own name otherwise.
    header = nav(base, [*TWO_COLUMN_RENDERER, *TAB_CONTENT, *SECTION_LIST_ITEM, *RESPONSIVE_HEADER], True)
    header = header if isinstance(header, dict) else {}
    page = norm.collection("liked", {"title": raw.text(header.get("title")), "thumbnails": nav(header, THUMBNAILS, True),
                                     "tracks": tracks[:MAX_TRACKS]}, "LM", limit=asked,
                           more=token is not None or len(tracks) > MAX_TRACKS)
    if page["truncated"]:
        page["trackCount"] = None
        page["unavailable"] = None
    if limit is None:
        page["capped"] = page["truncated"]
    page["header"]["pin"] = raw.header_pin(base)
    page["filters"] = liked_filters(base, chosen)
    page["filter"] = chosen
    return page
# ---- end G4


def m_liked(worker, params):
    worker.require_auth()
    limit = opt_limit(params, "limit", PAGE_TRACKS, high=MAX_TRACKS)
    chosen = _filter_id(params)  # G4
    if chosen is not None:
        return read_liked_filtered(worker, chosen, limit)
    # G4: the chips of the page get_liked_songs reads first.
    first = []

    def keep_first(endpoint, response):
        if not first:
            first.append(response)

    with raw.capture(("browse",), each=keep_first):
        page = read_list(worker, "liked", "LM", limit)
    page["filters"] = liked_filters(first[0] if first else None)
    page["filter"] = None
    return page


def m_album(worker, params):
    browse_id = req_str(params, "browseId")
    with raw.capture(("browse",)) as cap:
        data = worker.yt.get_album(browse_id)
    # S4: ytmusicapi 1.12.2 reads the header's library button without its
    # isToggled, so every album came back INDIFFERENT; the raw header decides.
    saved = actions.library_toggle(cap.first("browse"))
    if saved is not None:
        data = dict(data, likeStatus=actions.like_status(saved))
    page = norm.collection("album", data, browse_id, titles=album_titles(cap.first("browse")))
    # S14: Pin to / Unpin from Listen again (the page menu's).
    page["header"]["pin"] = raw.header_pin(cap.first("browse"))
    return shelf_pins(page, raw.card_pins([cap.first("browse")]))  # G2


def m_history(worker, params):
    worker.require_auth()
    limit = opt_int(params, "limit", HISTORY_TRACKS, high=HISTORY_TRACKS)
    return {"items": norm.normalize_list(worker.yt.get_history()[:limit], norm.track, "history item")}


def record_playlist(yt, params):
    return yt.get_playlist(params["playlistId"], limit=params.get("limit", 100), related=params.get("related", False))


METHODS = {"playlist": m_playlist, "liked": m_liked, "album": m_album, "history": m_history}
# The cache key holds limit and related, so a whole read and a first read
# are separate entries (edits drop both: INVALIDATES match on playlistId).
CACHEABLE = {"playlist", "liked", "album", "history"}
# A whole read is long; album and history are one request each.
LANES = {"playlist": "pages", "liked": "pages"}
INVALIDATES = {}
RECORDERS = {
    "album": lambda yt, p: yt.get_album(p["browseId"]),
    "playlist": record_playlist,
}
MUTATES = set()


# ---- pins on the shelves under the songs (G2) --------------------------------------------


def shelf_pins(page, pins):
    """``page`` with each card of its shelves (an album's other versions and
    releases, a playlist's related) carrying its Listen again pin from
    ``pins`` (raw.card_pins of the responses read): ytmusicapi's
    parse_album and parse_playlist read no menu."""
    for section in page.get("sections") or []:
        section["items"] = raw.with_card_pins(section.get("items"), pins)
    return page
