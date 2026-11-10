"""Search and its suggestions (S7): an area module (see AREA_MODULES in ytm.py).

``search`` returns what the web's search returns: songs, videos, albums,
artists, playlists, podcasts, episodes and profiles grouped in YouTube's
order under the top result card, the spelling YouTube corrected, the
library and uploads scopes, and filtered lists that page by cursor.
``suggestions`` adds the dropdown's rich rows (artists, songs, albums) and,
for an empty field, the account's own past searches; ``removeSuggestion``
deletes one of those from the account (MUTATES).

The search request is sent here and its raw response read section by
section: the top result is the ``musicCardShelfRenderer``, typed by its
endpoint (a podcast's is an untitled shelf of its own, ``lead_shelf``);
result rows are typed by page type or video type through
ytmusicapi's ``parse_search_result``; spelling comes from
``showingResultsForRenderer`` and ``didYouMeanRenderer``. Nothing is matched
by its (localized) title or label, and nothing by its position. A filtered
list continues with ``ytm_pages.fetch`` (style ``ctoken`` on the ``search``
endpoint, the request's own body, ``musicShelfContinuation`` replies).

Pinned to ytmusicapi 1.12.2 internals: get_search_params,
parse_search_result, parse_song_flat, parse_song_runs, parse_menu_playlists.
"""

from ytmusicapi.navigation import MRLIR, THUMBNAILS, nav
from ytmusicapi.parsers._utils import get_flex_column_item, parse_menu_playlists
from ytmusicapi.parsers.browsing import parse_song_flat
from ytmusicapi.parsers.search import get_search_params, parse_search_result
from ytmusicapi.parsers.songs import parse_song_runs

import ytm_normalize as norm
import ytm_pages as pages
import ytm_raw as raw
from ytm_proto import WorkerError, choice, opt_bool, opt_cursor, opt_int, req_str

FILTERS = ("songs", "videos", "albums", "artists", "playlists", "community_playlists", "featured_playlists",
           "profiles", "podcasts", "episodes")
SCOPES = ("library", "uploads")
# Your library has no featured or community playlists; uploads take no filter.
LIBRARY_FILTERS = tuple(f for f in FILTERS if f not in ("community_playlists", "featured_playlists"))
# A filtered list reads about this many results a call (two responses).
PAGE_ITEMS = 40
MAX_HTTP = 3
MAX_ENTITIES = 5
SUGGEST = "music/get_search_suggestions"
PODCAST_EPISODE = "MUSIC_VIDEO_TYPE_PODCAST_EPISODE"
SONG_VIDEO = "MUSIC_VIDEO_TYPE_ATV"
UPLOAD_PREFIX = "FEmusic_library_privately_owned"
_ARTIST_PAGES = (raw.ARTIST_PAGE, raw.LIBRARY_ARTIST_PAGE)
_TRIM = ("trackingParams", "clickTrackingParams", "accessibility", "accessibilityData", "loggingContext",
         "responseContext")


# ---- params --------------------------------------------------------------------------


def _opt_choice(params, key, options):
    """``params[key]`` when it is one of ``options``; None when absent or empty."""
    if params.get(key) in (None, ""):
        return None
    return choice(params, key, options)


def check_scope(search_filter, scope):
    """BAD_REQUEST for a filter the scope does not offer (as ytmusicapi refuses them)."""
    if scope == "uploads" and search_filter:
        raise WorkerError("BAD_REQUEST", "Uploads cannot be filtered")
    if scope == "library" and search_filter and search_filter not in LIBRARY_FILTERS:
        raise WorkerError("BAD_REQUEST", "Your library has no featured or community playlists")


def search_body(query, search_filter=None, scope=None, exact=False):
    """The search request body; a continuation is the same body plus its ctoken."""
    body = {"query": query}
    params = get_search_params(search_filter, scope, exact)
    if params:
        body["params"] = params
    return body


def result_type(search_filter, scope=None):
    """ytmusicapi's result type for every row of a filtered list (or the uploads scope)."""
    if scope == "uploads":
        return "upload"
    if not search_filter:
        return None
    return "playlist" if "playlists" in search_filter else search_filter[:-1]


# ---- the response ----------------------------------------------------------------------


def section_list(response, scope=None, filtered=False):
    """The sectionListRenderer of the results a search asked for.

    A tabbed response (signed in) has a tab per scope; like ytmusicapi, a
    filtered search reads tab 0 and an unfiltered scoped one its scope's tab
    (library 1, uploads 2). A missing tab falls back to the selected one.
    """
    contents = (response or {}).get("contents") if isinstance(response, dict) else None
    if not isinstance(contents, dict):
        return None
    tabbed = contents.get("tabbedSearchResultsRenderer")
    if isinstance(tabbed, dict):
        tabs = [tab for tab in tabbed.get("tabs") or [] if isinstance(tab, dict)]
        index = 0 if not scope or filtered else SCOPES.index(scope) + 1
        if index >= len(tabs):
            index = next((i for i, tab in enumerate(tabs) if nav(tab, ["tabRenderer", "selected"], True)), 0)
        if not tabs:
            return None
        contents = nav(tabs[index], ["tabRenderer", "content"], True) or {}
    found = contents.get("sectionListRenderer") if isinstance(contents, dict) else None
    return found if isinstance(found, dict) else None


def rows(response, scope=None, filtered=False):
    found = section_list(response, scope, filtered) or {}
    return [row for row in found.get("contents") or [] if isinstance(row, dict) and row]


def _first(row):
    key, data = next(iter(row.items()))
    return key, data if isinstance(data, dict) else {}


def spelling(response_rows, query):
    """``{kind, corrected, original}`` when YouTube corrected the query or suggests a correction.

    ``corrected``: results are for ``corrected`` (Showing results for), and
    ``original`` can be searched exactly. ``didYouMean``: results are for the
    query as typed, and ``corrected`` is offered.
    """
    for row in response_rows:
        key, data = _first(row)
        if key != "itemSectionRenderer":
            continue
        for content in data.get("contents") or []:
            if not isinstance(content, dict):
                continue
            found = content.get("showingResultsForRenderer")
            if isinstance(found, dict):
                corrected = raw.text(found.get("correctedQuery")) \
                    or nav(found, ["correctedQueryEndpoint", "searchEndpoint", "query"], True)
                original = raw.text(found.get("originalQuery")) \
                    or nav(found, ["originalQueryEndpoint", "searchEndpoint", "query"], True) or query
                if corrected:
                    return {"kind": "corrected", "corrected": corrected, "original": original}
            found = content.get("didYouMeanRenderer")
            if isinstance(found, dict):
                corrected = raw.text(found.get("correctedQuery")) \
                    or nav(found, ["correctedQueryEndpoint", "searchEndpoint", "query"], True)
                if corrected:
                    return {"kind": "didYouMean", "corrected": corrected, "original": query}
    return None


# ---- rows ------------------------------------------------------------------------------


def _column_runs(data, index):
    column = get_flex_column_item(data, index)
    return nav(column, ["text", "runs"], True) or [] if column else []


def _credits(runs):
    """Linked runs (artists, channels, podcasts, albums) as ``{name, id, pageType}``."""
    out = []
    for run in runs or []:
        browse = raw.browse_endpoint(run.get("navigationEndpoint")) if isinstance(run, dict) else None
        if browse and run.get("text"):
            out.append({"name": run["text"], "id": browse["browseId"], "pageType": browse["pageType"]})
    return out


def _row_type(data):
    """A row's type when ytmusicapi's own guess (browseId prefix, video type)
    would get it wrong: a user channel is a profile (its id starts UC, like an
    artist's), a row that plays a playlist is a station. None otherwise."""
    endpoint = data.get("navigationEndpoint") if isinstance(data.get("navigationEndpoint"), dict) else {}
    browse = raw.browse_endpoint(endpoint)
    if browse:
        return "profile" if browse["pageType"] == raw.USER_CHANNEL_PAGE else None
    if "watchPlaylistEndpoint" in endpoint or nav(endpoint, ["watchEndpoint", "playlistId"], True):
        return "station"
    return None


def row_dict(data, rtype=None, category=None):
    """One search row (a musicResponsiveListItemRenderer) in ytmusicapi's shape."""
    rtype = rtype or _row_type(data)
    endpoint = data.get("navigationEndpoint") if isinstance(data.get("navigationEndpoint"), dict) else {}
    if rtype == "station" and "watchPlaylistEndpoint" in endpoint:
        return {"resultType": "station", "title": raw.text(nav(get_flex_column_item(data, 0), ["text"], True)),
                "playlistId": nav(endpoint, ["watchPlaylistEndpoint", "playlistId"], True),
                "thumbnails": nav(data, THUMBNAILS, True)}
    parsed = parse_search_result(data, rtype, category)
    if parsed.get("resultType") == "podcast" and not parsed.get("author"):
        credit = next(iter(_credits(_column_runs(data, 1))), None)
        parsed["author"] = {"name": credit["name"], "id": credit["id"]} if credit else None
    if parsed.get("resultType") == "episode" and not parsed.get("browseId"):
        # ytmusicapi keeps no page id for an episode; its title links the episode page.
        page = next((c for c in _credits(_column_runs(data, 0)) if c["pageType"] == raw.EPISODE_PAGE), None)
        parsed["browseId"] = page["id"] if page else None
    return parsed


def parse_rows(contents, rtype=None, category=None):
    """The rows of a shelf, item section or continuation in ytmusicapi's shape.

    Anything that is not a row (a continuation entry, a message) is skipped,
    and so is a row that fails to parse (logged by exception type only).
    """
    out = []
    for content in contents or []:
        data = content.get(MRLIR) if isinstance(content, dict) else None
        if not isinstance(data, dict):
            continue
        try:
            out.append(row_dict(data, rtype, category))
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            norm.log(f"skipped malformed search result: {type(exc).__name__}")
    return out


def upload_page(item):
    """An uploaded album or artist as a browse item, until S10 gives uploads
    pages of their own (``upload`` says which it is)."""
    browse_id = item.get("browseId")
    if not browse_id:
        raise ValueError("upload without browseId")
    thumb, thumb_large = norm.thumbs(item.get("thumbnails"))
    kind = item.get("resultType")
    parts = (item.get("artist"), item.get("releaseDate")) if kind == "album" else ()
    subtitle = " \u2022 ".join(str(part) for part in parts if part) or None
    return {"type": "browse", "browseId": browse_id, "params": None, "upload": kind,
            "title": item.get("title") or item.get("artist") or "", "subtitle": subtitle,
            "thumb": thumb, "thumbLarge": thumb_large}


def _normalize(parsed, rtype=None):
    if rtype == "upload":
        out = []
        for item in parsed:
            try:
                if item.get("resultType") in ("album", "artist") and item.get("browseId"):
                    out.append(upload_page(item))
                else:
                    out.extend(norm.normalize_list([item], norm.search_item, "upload"))
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                norm.log(f"skipped malformed upload: {type(exc).__name__}")
        return out
    return norm.normalize_list(parsed, norm.search_item, "search result")


def items(contents, rtype=None):
    """Normalized items of a list's rows (also a continuation's)."""
    return _normalize(parse_rows(contents, rtype), rtype)


# ---- the top result card -----------------------------------------------------------------


def _label(runs):
    """The unlinked type label that starts a card's subtitle ("Album", "Single"), else None."""
    if len(runs) > 2 and isinstance(runs[0], dict) and "navigationEndpoint" not in runs[0]:
        return runs[0].get("text") or None
    return None


def _last_text(runs):
    plain = [run.get("text") for run in runs if isinstance(run, dict) and "navigationEndpoint" not in run]
    return plain[-1] if len(runs) > 2 and plain and plain[-1] else None


def _button_endpoint(card, key):
    """The first ``key`` endpoint (watchEndpoint, watchPlaylistEndpoint) among the card's buttons."""
    for button in card.get("buttons") or []:
        found = nav(button, ["buttonRenderer", "command", key], True) if isinstance(button, dict) else None
        if isinstance(found, dict):
            return found
    return {}


def card_dict(card):
    """The top result of a musicCardShelfRenderer in ytmusicapi's shape, typed by
    its endpoint (a page type, or what it plays), never by its label."""
    title = raw.text(card.get("title")) or ""
    endpoint = card.get("onTap") if isinstance(card.get("onTap"), dict) else {}
    if not (raw.browse_endpoint(endpoint) or raw.watch_endpoint(endpoint)):
        endpoint = nav(card, ["title", "runs", 0, "navigationEndpoint"], True) or {}
    runs = [run for run in nav(card, ["subtitle", "runs"], True) or [] if isinstance(run, dict)]
    credits = _credits(runs)
    thumbnails = nav(card, THUMBNAILS, True)
    browse = raw.browse_endpoint(endpoint)
    if browse:
        page_type, browse_id = browse["pageType"], browse["browseId"]
        if page_type in _ARTIST_PAGES:
            out = {"resultType": "artist", "browseId": browse_id, "artist": title, "subscribers": _last_text(runs),
                   "thumbnails": thumbnails}
            parse_menu_playlists(card, out)
            return out
        if page_type in raw.ALBUM_PAGES:
            info = parse_song_runs(runs, skip_type_spec=True)
            return {"resultType": "album", "browseId": browse_id, "title": title, "type": _label(runs),
                    "artists": [{"name": c["name"], "id": c["id"]} for c in credits if c["pageType"] in _ARTIST_PAGES]
                    or info.get("artists"), "year": info.get("year"),
                    "playlistId": _button_endpoint(card, "watchPlaylistEndpoint").get("playlistId"),
                    "isExplicit": raw.explicit(card.get("subtitleBadges")), "thumbnails": thumbnails}
        if page_type == raw.PLAYLIST_PAGE:
            return {"resultType": "playlist", "browseId": browse_id, "title": title,
                    "author": [{"name": c["name"], "id": c["id"]} for c in credits] or None, "thumbnails": thumbnails}
        if page_type == raw.PODCAST_PAGE:
            author = next(iter(credits), None)
            return {"resultType": "podcast", "browseId": browse_id, "title": title, "thumbnails": thumbnails,
                    "author": {"name": author["name"], "id": author["id"]} if author else None}
        if page_type == raw.USER_CHANNEL_PAGE:
            return {"resultType": "profile", "browseId": browse_id, "title": title, "name": _last_text(runs),
                    "thumbnails": thumbnails}
        if page_type == raw.EPISODE_PAGE:
            podcast = next((c for c in credits if c["pageType"] == raw.PODCAST_PAGE), None)
            watch = _button_endpoint(card, "watchEndpoint")
            return {"resultType": "episode", "browseId": browse_id, "title": title, "videoId": watch.get("videoId"),
                    "podcast": {"name": podcast["name"], "id": podcast["id"]} if podcast else None,
                    "thumbnails": thumbnails}
        return None
    watch = endpoint.get("watchEndpoint") if isinstance(endpoint.get("watchEndpoint"), dict) else None
    if watch and watch.get("videoId"):
        video_type = nav(watch, raw.VIDEO_TYPE, True)
        if video_type == PODCAST_EPISODE:
            podcast = next((c for c in credits if c["pageType"] == raw.PODCAST_PAGE), None)
            return {"resultType": "episode", "videoId": watch["videoId"], "videoType": video_type, "title": title,
                    "podcast": {"name": podcast["name"], "id": podcast["id"]} if podcast else None,
                    "thumbnails": thumbnails}
        info = parse_song_runs(runs, skip_type_spec=True)
        return dict(info, resultType="song" if video_type == SONG_VIDEO else "video", videoId=watch["videoId"],
                    videoType=video_type, title=title, thumbnails=thumbnails)
    playlist_id = (raw.watch_endpoint(endpoint) or {}).get("playlistId")
    if playlist_id:
        return {"resultType": "station", "playlistId": playlist_id, "title": title, "thumbnails": thumbnails,
                "author": [{"name": c["name"], "id": c["id"]} for c in credits] or None}
    return None


def card(data):
    """``(top, extra)``: the card's own result and the rows it shows, and rows
    after a message inside it (More from YouTube), which are not the card's."""
    top, extra = [], []
    try:
        found = card_dict(data)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        norm.log(f"skipped malformed top result: {type(exc).__name__}")
        found = None
    if found:
        top.append(found)
    target = top if top else extra
    for content in data.get("contents") or []:
        if isinstance(content, dict) and "messageRenderer" in content:
            target = extra
            continue
        target.extend(parse_rows([content]))
    return top, extra


# ---- search ------------------------------------------------------------------------------


def _has_rows(data):
    return any(isinstance(content, dict) and MRLIR in content for content in data.get("contents") or [])


def lead_shelf(response_rows):
    """The index of a top result served without a card, else None.

    A podcast leads its search as an untitled musicShelfRenderer of its own
    (with a hidden shelf divider) ahead of the results, which come one row
    per itemSectionRenderer. Read by renderer only: a card anywhere, a titled
    shelf, or a result before it means there is none; sections without
    result rows (spelling) are passed over.
    """
    lead = None
    for index, row in enumerate(response_rows):
        key, data = _first(row)
        if key == "musicCardShelfRenderer":
            return None
        if not _has_rows(data):
            continue
        if lead is not None:
            return lead if key == "itemSectionRenderer" else None
        if key != "musicShelfRenderer" or data.get("title"):
            return None
        lead = index
    return None


def grouped(response, query, scope=None):
    """An unfiltered search: the top result (the card, or a podcast's lead
    shelf) and a group per kind, in served order."""
    response_rows = rows(response, scope)
    lead = lead_shelf(response_rows)
    top, results = [], []
    for index, row in enumerate(response_rows):
        key, data = _first(row)
        if key == "musicCardShelfRenderer" and not top:
            found, extra = card(data)
            top = found
            results.extend(extra)
        elif index == lead:
            top = parse_rows(data.get("contents"))
        elif key in ("musicShelfRenderer", "itemSectionRenderer"):
            category = raw.text(data.get("title")) if key == "musicShelfRenderer" else None
            results.extend(parse_rows(data.get("contents"), None, category))
    out = norm.search_grouped(results, top)
    out["spelling"] = spelling(response_rows, query)
    return out


def _results_shelf(response_rows):
    """The shelf a filtered list reads: the first music shelf that continues, else the first.
    (YouTube may pad a filtered list with a shelf of another kind; it never continues.)"""
    shelves = [data for key, data in (_first(row) for row in response_rows) if key == "musicShelfRenderer"]
    return next((s for s in shelves if s.get("continuations")), shelves[0] if shelves else None)


def _cursor(shelf):
    token = nav(shelf, ["continuations", 0, "nextContinuationData", "continuation"], True) if shelf else None
    return "c." + token if token else None


def unique(found):
    """``found`` without the rows YouTube lists again: a continuation can
    repeat a result of the response before it (same type and id)."""
    seen, out = set(), []
    for item in found:
        key = (item.get("type"), item.get("videoId") or item.get("browseId") or item.get("playlistId")
               or item.get("channelId"))
        if key[1] and key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def listing(yt, response, body, query, search_filter, scope, limit, http):
    """A filtered search (or the uploads scope): ``{items, cursor, hasMore, spelling}``.

    The first response's results shelf, then its continuations until
    ``limit`` items, within MAX_HTTP requests in all.
    """
    rtype = result_type(search_filter, scope)
    response_rows = rows(response, scope, filtered=bool(search_filter))
    shelf = _results_shelf(response_rows)
    found = items(shelf.get("contents"), rtype) if shelf else []
    cursor = _cursor(shelf)
    if cursor and len(found) < limit and http < MAX_HTTP:
        more, cursor = pages.fetch(yt, "search", body, "ctoken", None, lambda c: items(c, rtype), cursor=cursor,
                                   min_items=limit - len(found), max_http=MAX_HTTP - http)
        found.extend(more)
    return {"items": unique(found), "cursor": cursor, "hasMore": bool(cursor),
            "spelling": spelling(response_rows, query)}


def m_search(worker, params):
    """Search YouTube Music, your library (``scope: "library"``) or your uploads (``"uploads"``).

    Unfiltered: ``{top, songs, videos, albums, playlists, artists, podcasts,
    episodes, profiles, order, spelling}``. Filtered (or uploads): ``{items,
    cursor, hasMore, spelling}``; send ``cursor`` back for the next page.
    ``exact`` searches the words as typed (no spelling correction).
    """
    query = req_str(params, "query")
    search_filter = _opt_choice(params, "filter", FILTERS)
    scope = _opt_choice(params, "scope", SCOPES)
    exact = opt_bool(params, "exact")
    limit = opt_int(params, "limit", PAGE_ITEMS, high=100)
    cursor = opt_cursor(params)
    check_scope(search_filter, scope)
    if scope:
        worker.require_auth()
    listed = bool(search_filter) or scope == "uploads"
    body = search_body(query, search_filter, scope, exact)
    if cursor:
        if not listed:
            raise WorkerError("BAD_REQUEST", "Only a filtered search has more pages")
        rtype = result_type(search_filter, scope)
        found, cursor = pages.fetch(worker.yt, "search", body, "ctoken", None, lambda c: items(c, rtype),
                                    cursor=cursor, min_items=limit, max_http=MAX_HTTP)
        return {"items": unique(found), "cursor": cursor, "hasMore": bool(cursor)}
    response, http = pages.first_page(worker.yt, "search", body)[:2]
    if listed:
        return listing(worker.yt, response, body, query, search_filter, scope, limit, http)
    return grouped(response, query, scope)


# ---- suggestions ------------------------------------------------------------------------------


def entity(data):
    """One rich suggestion row: a song or video by what it plays, anything else
    (artist, album, playlist, ...) by its page type."""
    browse = raw.browse_endpoint(data.get("navigationEndpoint"))
    if browse is None:
        song = parse_song_flat(data)
        endpoint = data.get("navigationEndpoint") if isinstance(data.get("navigationEndpoint"), dict) else {}
        watch = endpoint.get("watchEndpoint") if isinstance(endpoint.get("watchEndpoint"), dict) else {}
        song["videoId"] = song.get("videoId") or watch.get("videoId") or nav(data, ["playlistItemData", "videoId"], True)
        song["videoType"] = song.get("videoType") or nav(watch, raw.VIDEO_TYPE, True)
        if not song["videoId"]:
            return None
        if song["videoType"] == PODCAST_EPISODE:
            return norm.episode(song)
        return norm.track(dict(song, resultType="song" if song["videoType"] == SONG_VIDEO else "video"))
    value = raw.item({MRLIR: data})
    if value is None:
        return None
    runs = _column_runs(data, 1)
    if value["type"] == "artist":
        mixes = {}
        parse_menu_playlists(data, mixes)
        value.update(subtitle=raw.text({"runs": runs}) or value.get("subtitle"),
                     shuffleId=mixes.get("shuffleId"), radioId=mixes.get("radioId"))
    elif value["type"] == "album":
        info = parse_song_runs(runs, skip_type_spec=True)
        value.update(albumType=value.get("albumType") or _label(runs), year=value.get("year") or info.get("year"))
    return value


def entities(response):
    """Up to MAX_ENTITIES rich rows from a raw suggestions response (its second section)."""
    contents = (response or {}).get("contents") if isinstance(response, dict) else None
    out = []
    for section in (contents or [])[1:]:
        for content in nav(section, ["searchSuggestionsSectionRenderer", "contents"], True) or []:
            data = content.get(MRLIR) if isinstance(content, dict) else None
            if not isinstance(data, dict) or len(out) >= MAX_ENTITIES:
                continue
            try:
                value = entity(data)
            except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
                norm.log(f"skipped malformed suggestion: {type(exc).__name__}")
                continue
            if value is not None:
                out.append(value)
    return out


def _query(params):
    value = params.get("query")
    if not isinstance(value, str):
        raise WorkerError("BAD_REQUEST", "'query' must be a string")
    return value.strip()


def m_suggestions(worker, params):
    """Completions for a half-typed search, plus rich rows: ``{items, entities}``.

    ``items`` are ``{text, fromHistory}`` (your own searches are fromHistory
    when signed in). An empty query asks for your past searches: signed in,
    YouTube answers with them (all fromHistory); signed out it is ``[]``
    without a request. Feedback tokens never leave the worker.
    """
    query = _query(params)
    limit = opt_int(params, "limit", 8, high=20)
    if not query and worker.auth != "ok":
        return {"items": [], "entities": []}
    raw.install(worker.yt)  # the rich rows come from the raw reply (a no-op once installed)
    with raw.capture((SUGGEST,)) as cap:
        found = worker.yt.get_search_suggestions(query, detailed_runs=True) or []
    out = []
    for entry in found:
        text = entry.get("text") if isinstance(entry, dict) else entry
        if isinstance(text, str) and text.strip():
            out.append({"text": text.strip(), "fromHistory": bool(isinstance(entry, dict) and entry.get("fromHistory"))})
    return {"items": out[:limit], "entities": entities(cap.first(SUGGEST)) if query else []}


def m_remove_suggestion(worker, params):
    """Remove one of your past searches (MUTATES): ``{query, text}``.

    ``query`` is what the field held when the suggestion was shown ("" for
    the past searches of an empty field). The suggestions are read again
    for their feedback tokens and the history entry with that text (exactly,
    else ignoring case) is removed; BAD_REQUEST when there is none.
    """
    worker.require_auth()
    text = req_str(params, "text")
    query = _query(params) if params.get("query") is not None else ""
    found = worker.yt.get_search_suggestions(query, detailed_runs=True) or []
    history = [(i, (s.get("text") or "").strip()) for i, s in enumerate(found)
               if isinstance(s, dict) and s.get("fromHistory")]
    index = next((i for i, t in history if t == text), None)
    if index is None:
        index = next((i for i, t in history if t.lower() == text.lower()), None)
    if index is None:
        raise WorkerError("BAD_REQUEST", "That is not one of your past searches")
    if not worker.yt.remove_search_suggestions(found, indices=[index]):
        raise WorkerError("UPSTREAM", "YouTube Music did not remove that search")
    return {"removed": True, "text": found[index]["text"]}


# ---- recorders (anonymous; tests/fixtures) --------------------------------------------------------


# Menu entries a parser reads: an artist's shuffle and radio mixes (by icon).
_MIX_ICONS = ("MUSIC_SHUFFLE", "MIX")
# En and em dashes in recorded titles become plain hyphens: the repo's text
# carries neither (no parser reads them).
_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-"})


def _trim(value):
    if isinstance(value, str):
        return value.translate(_DASHES)
    if isinstance(value, dict):
        if "menuRenderer" in value and isinstance(value["menuRenderer"], dict):
            entries = [entry for entry in value["menuRenderer"].get("items") or []
                       if nav(entry, ["menuNavigationItemRenderer", "icon", "iconType"], True) in _MIX_ICONS]
            return {"menuRenderer": {"items": _trim(entries)}}
        return {k: _trim(v) for k, v in value.items() if k not in _TRIM}
    if isinstance(value, list):
        return [_trim(v) for v in value]
    return value


def record_search(yt, params):
    """ytmusicapi's parsed results (search_songs.json, search_mixed.json)."""
    return yt.search(params["query"], filter=params.get("filter"), limit=params.get("limit", 20))


def record_search_raw(yt, params):
    """``{query, filter?, exact?}``: the raw first response, tracking trimmed."""
    body = search_body(params["query"], params.get("filter"), None, bool(params.get("exact")))
    return _trim(yt._send_request("search", body))


def record_suggestions_raw(yt, params):
    """``{query}``: the raw suggestions response (completions and rich rows)."""
    return _trim(yt._send_request(SUGGEST, {"input": params["query"]}))


# ---- registry (merged by ytm.py) ------------------------------------------------------------------

METHODS = {"search": m_search, "suggestions": m_suggestions, "removeSuggestion": m_remove_suggestion}
CACHEABLE = set()
# Completions must show while a search is still running.
LANES = {"suggestions": "suggest"}
INVALIDATES = {}
RECORDERS = {"search": record_search, "searchRaw": record_search_raw, "suggestionsRaw": record_suggestions_raw}
MUTATES = {"removeSuggestion"}
NO_INVALIDATES = {"removeSuggestion": "search suggestions are never cached"}
