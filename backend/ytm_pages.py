"""Paging through long YouTube Music lists, and the generic See all reader.

``fetch()`` reads one page of a list and follows its continuations until it
has ``min_items`` or has spent ``max_http`` requests, in one of three styles:

- ``ctoken``: browse or search with ``&ctoken=..&continuation=..`` appended
  (get_continuation_string); the reply is ``continuationContents.<x>Continuation``
  for grids, music shelves and section lists.
- ``body``: browse with the body ``{"continuation": token}``; the reply is
  ``onResponseReceivedActions[0]...continuationItems`` and the next token sits
  in its last item (get_playlist's style).
- ``reload``: the first page is re-requested with the reload token of one of
  the served sort options (see ``sort_options``); the reply is a
  ``sectionListContinuation``. Later pages continue as ``ctoken``.

Cursors handed to the shell are opaque: ``c.<token>`` or ``b.<token>``, so a
later call continues in the style the token came from. They are account-bound
and never logged, recorded or cached (the disk cache strips them).

This file is also an area module (see AREA_MODULES in ytm.py): it registers
``browsePage``. Like ytm_raw.py it holds ytmusicapi internals, pinned to
1.12.2.
"""

from ytmusicapi.continuations import CONTINUATION_ITEMS, get_continuation_string, get_continuation_token
from ytmusicapi.navigation import FRAMEWORK_MUTATIONS, nav

import ytm_normalize as norm
import ytm_raw as raw
from ytm_proto import WorkerError, opt_cursor, opt_order, opt_str, req_str

STYLES = ("ctoken", "body", "reload")
MAX_ORDERS = 32
_PREFIX = {"c.": "ctoken", "b.": "body"}
_SORT_BUTTON = "musicSortFilterButtonRenderer"


# ---- locating content ----------------------------------------------------------------

# Moved to ytm_raw (``raw.rows`` is the slice plan's ``rows``); kept here under
# their first names.
section_list = raw.section_list
section_rows = raw.rows
unwrap = raw.unwrap


def _find(node, key, depth=12):
    if depth < 0:
        return None
    if isinstance(node, dict):
        if key in node:
            return node[key]
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        found = _find(child, key, depth - 1)
        if found is not None:
            return found
    return None


def _sort_menu(response):
    found = section_list(response) or {}
    button = _find(found.get("header"), _SORT_BUTTON)
    return nav(button, ["menu", "musicMultiSelectMenuRenderer", "options"], True) or []


def sort_options(response):
    """``[{index, title, selected}]`` of the page's sort menu (reload tokens stay here).

    Which option is selected lives in frameworkUpdates, keyed by the option's
    formItemEntityKey.
    """
    selected = {}
    for mutation in nav(response, FRAMEWORK_MUTATIONS, True) or []:
        choice = nav(mutation, ["payload", "musicFormBooleanChoice"], True)
        if isinstance(choice, dict) and mutation.get("entityKey"):
            selected[mutation["entityKey"]] = bool(choice.get("selected"))
    out = []
    for index, option in enumerate(_sort_menu(response)):
        item = (option or {}).get("musicMultiSelectMenuItemRenderer") or {}
        out.append({"index": index, "title": raw.text(item.get("title")) or "",
                    "selected": selected.get(item.get("formItemEntityKey"), False)})
    return out


def _reload_token(response, order):
    options = _sort_menu(response)
    if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order < len(options):
        raise WorkerError("BAD_REQUEST", "'order' is not one of this page's sort options")
    commands = nav(options[order], ["musicMultiSelectMenuItemRenderer", "selectedCommand",
                                    "commandExecutorCommand", "commands"], True) or []
    for command in reversed(commands):
        token = nav(command, ["browseSectionListReloadEndpoint", "continuation", "reloadContinuationData",
                              "continuation"], True)
        if token:
            return token
    raise WorkerError("UPSTREAM", "That sort option cannot be loaded")


# The reload token of a served option (S2 reads which order it re-sorts by).
reload_token = _reload_token


# ---- fetching ------------------------------------------------------------------------


def _send(yt, endpoint, body, additional=""):
    # _send_request adds the client context to the body it is given.
    return yt._send_request(endpoint, dict(body), additional)


def _next_cursor(container, contents):
    token = nav(container, ["continuations", 0, "nextContinuationData", "continuation"], True) \
        if isinstance(container, dict) else None
    if token:
        return "c." + token
    if contents and isinstance(contents[-1], dict) and "continuationItemRenderer" in contents[-1]:
        token = get_continuation_token(contents)
        if token:
            return "b." + token
    return None


def _continuation(response):
    """``(contents, cursor)`` of a continuation reply in either shape."""
    by_type = response.get("continuationContents") if isinstance(response, dict) else None
    if isinstance(by_type, dict) and by_type:
        container = next(iter(by_type.values()))
        if not isinstance(container, dict):
            return [], None
        contents = container.get("contents") or container.get("items") or []
        return contents, _next_cursor(container, contents)
    contents = nav(response, CONTINUATION_ITEMS, True) or []
    return contents, _next_cursor(None, contents)


def _unpack(cursor, style):
    prefix = cursor[:2]
    if prefix in _PREFIX:
        return _PREFIX[prefix], cursor[2:]
    return ("ctoken" if style == "reload" else style), cursor


def resort(yt, endpoint, body, base, order):
    """The first page again in the sort option ``order`` (its index in
    ``sort_options(base)``), from the unsorted response ``base``: one request."""
    return _send(yt, endpoint, body, get_continuation_string(_reload_token(base, order)))


def first_page(yt, endpoint, body, order=None):
    """``(response, http, options, base)``: the first page, re-sorted when
    ``order`` is the index of one of the served sort options. ``base`` is the
    unsorted response, which holds the page header and the sort menu."""
    base = _send(yt, endpoint, body)
    options = sort_options(base)
    if order is None:
        return base, 1, options, base
    return resort(yt, endpoint, body, base, order), 2, options, base


def fetch(yt, endpoint, body, style, locate, parse, cursor=None, min_items=40, max_http=3, order=None,
          first=None, enough=None):
    """``(items, cursor)``: one page of a list plus continuations up to ``min_items``.

    ``locate(response)`` returns the renderer holding the list (its
    ``contents`` or ``items``, plus ``continuations``); ``parse(contents)``
    returns the parsed items and must skip continuation entries. ``first``
    is a ``first_page()`` result already fetched by the caller (its requests
    count against ``max_http``). ``enough(items)``, when given, decides in
    place of ``min_items`` whether the items make a page. An empty
    continuation ends the list.
    """
    if style not in STYLES:
        raise ValueError(f"unknown paging style {style!r}")
    if enough is None:
        enough = lambda got: len(got) >= min_items  # noqa: E731
    items = []
    http = 0
    if cursor is None:
        response, http = (first or first_page(yt, endpoint, body, order if style == "reload" else None))[:2]
        container = locate(response)
        if not isinstance(container, dict):
            return [], None
        contents = container.get("contents") or container.get("items") or []
        items.extend(parse(contents))
        cursor = _next_cursor(container, contents)
        if not items and not cursor:
            return [], None
    while cursor and http < max_http and (http == 0 or not enough(items)):
        step, token = _unpack(cursor, style)
        if step == "body":
            response = yt._send_request(endpoint, {"continuation": token})
        else:
            response = _send(yt, endpoint, body, get_continuation_string(token))
        http += 1
        contents, cursor = _continuation(response)
        page = parse(contents) if contents else []
        if not page:
            return items, None
        items.extend(page)
    return items, cursor


# ---- browsePage: the generic See all reader ------------------------------------------------

LIST_RENDERERS = ("gridRenderer", "musicShelfRenderer", "musicPlaylistShelfRenderer")
CAROUSELS = ("musicCarouselShelfRenderer", "musicImmersiveCarouselShelfRenderer")


def detect(response):
    """``(kind, container)``: what the first section of a browse response holds.

    ``grid`` and ``shelf`` are one list (the renderer is returned); ``sections``
    is a page of carousels (the section list is returned); ``message`` is an
    empty page (the message text is returned); ``empty`` has nothing.
    """
    found = section_list(response)
    rows = (found or {}).get("contents") or []
    keys = [unwrap(row)[0] for row in rows]
    if not rows:
        return "empty", None
    if any(key in CAROUSELS for key in keys) or len(rows) > 1:
        return "sections", found
    key, data = unwrap(rows[0])
    if key == "gridRenderer":
        return "grid", data
    if key in ("musicShelfRenderer", "musicPlaylistShelfRenderer"):
        return "shelf", data
    if key == "messageRenderer":
        data = data or {}
        # The text sits in `text`, or under `subtext` (bare or in a messageSubtextRenderer).
        subtext = data.get("subtext") if isinstance(data.get("subtext"), dict) else {}
        return "message", raw.text(data.get("text")) or raw.text(subtext) \
            or raw.text(nav(subtext, ["messageSubtextRenderer", "text"], True)) or ""
    return "sections", found


def _entries(contents):
    """Rows become sections; anything else is parsed as an item."""
    out = []
    for content in contents or []:
        key = next(iter(content), None) if isinstance(content, dict) else None
        if key in raw.ROW_RENDERERS:
            row = raw.parse_row(content)
            if row["layout"] is not None:
                out.append(norm.section(row["title"], row["items"], row["more"], row["play"], row["layout"]))
        else:
            out.extend(raw.items([content]))
    return out


def _enough(entries):
    """A continuation of shelves is a page as it comes (each entry is a
    whole section); a list reads on to 40 items."""
    return any(e.get("type") == "section" for e in entries) or len(entries) >= 40


def _sections(entries, title=None, layout=None):
    sections = [e for e in entries if e.get("type") == "section"]
    loose = [e for e in entries if e.get("type") != "section"]
    if loose:
        sections.append(norm.section(title, loose, layout=layout))
    return sections


def _header(response):
    header = (response or {}).get("header") or {}
    data = next(iter(header.values()), {}) if isinstance(header, dict) and header else {}
    if not isinstance(data, dict) or not data:
        # Albums and playlists put theirs above the list in the first column.
        data = nav(response, ["contents", "twoColumnBrowseResultsRenderer", "tabs", 0, "tabRenderer", "content",
                              "sectionListRenderer", "contents", 0, "musicResponsiveHeaderRenderer"], True) or {}
    thumbnails = nav(data, ["thumbnail", "musicThumbnailRenderer", "thumbnail", "thumbnails"], True) \
        or nav(data, ["foregroundThumbnail", "musicThumbnailRenderer", "thumbnail", "thumbnails"], True)
    return raw.text(data.get("title")), raw.text(data.get("subtitle")), thumbnails


# Library pages whose sort order YouTube saves on the account: re-sorting one
# changes what every later request (and the web app) gets without an order.
SAVED_ORDER_PREFIXES = ("FEmusic_liked_", "FEmusic_library_")
# Cached methods that read one of those pages without an order.
ORDER_DEPENDENTS = {"FEmusic_liked_playlists": ("libraryPlaylists",),
                    # S2: the library lists (ytm_library) read their pages in the saved order too.
                    "FEmusic_liked_videos": ("librarySongs",),
                    "FEmusic_liked_albums": ("libraryAlbums",),
                    "FEmusic_library_corpus_track_artists": ("libraryArtists",),
                    "FEmusic_library_corpus_artists": ("librarySubscriptions",),
                    "FEmusic_library_landing": ("libraryLanding",)}


def saves_order(browse_id):
    return isinstance(browse_id, str) and browse_id.startswith(SAVED_ORDER_PREFIXES)


def forget_order(worker, browse_id):
    """Drop what a re-sort of ``browse_id`` made stale: its cached pages in
    every order, and the methods that read it in the saved order."""
    worker.invalidate("browsePage", {"browseId": browse_id})
    for method in ORDER_DEPENDENTS.get(browse_id, ()):
        worker.invalidate(method)


def m_browse_page(worker, params):
    """Any browse endpoint as a page: what a See all shows when nothing better reads it."""
    browse_id = req_str(params, "browseId")
    body = {"browseId": browse_id}
    page_params = opt_str(params, "params")
    if page_params:
        body["params"] = page_params
    cursor = opt_cursor(params)
    order = opt_order(params, range(MAX_ORDERS))
    yt = worker.yt
    if cursor:
        entries, cursor = fetch(yt, "browse", body, "ctoken", None, _entries, cursor=cursor, enough=_enough)
        return norm.page("browse", browse_id, None, sections=_sections(entries), cursor=cursor, order=order)
    try:
        first = first_page(yt, "browse", body, order)
    finally:
        if order is not None and saves_order(browse_id):
            forget_order(worker, browse_id)
    response, _, options, base = first
    title, subtitle, thumbnails = _header(base)
    if order is None:
        order = next((o["index"] for o in options if o["selected"]), None)
    else:
        # The unsorted response marks the order it was served in, not this one.
        options = [dict(o, selected=o["index"] == order) for o in options]
    kind, container = detect(response)
    round_cover = norm.strip_mpla(browse_id).startswith("UC")  # artists and profiles have round photos
    if kind in ("message", "empty"):
        return norm.page("browse", browse_id, title, subtitle, thumbnails, orders=options, order=order,
                         round=round_cover, message=container or None)
    layout = {"grid": "grid", "shelf": "shelf"}.get(kind)
    min_items = 1 if kind == "sections" else 40
    entries, cursor = fetch(yt, "browse", body, "ctoken", lambda _: container, _entries, min_items=min_items,
                            first=first)
    list_title = raw.row_parts("gridRenderer" if kind == "grid" else "musicShelfRenderer", container)[1] \
        if layout else None
    return norm.page("browse", browse_id, title, subtitle, thumbnails, _sections(entries, list_title, layout),
                     cursor, orders=options, order=order, round=round_cover)


def record_browse(yt, params):
    """The raw browse response, for fixtures of pages ytmusicapi does not parse."""
    body = {"browseId": params["browseId"]}
    if params.get("params"):
        body["params"] = params["params"]
    return yt._send_request("browse", body)


# ---- registry (merged by ytm.py) --------------------------------------------------------

METHODS = {"browsePage": m_browse_page}
CACHEABLE = {"browsePage"}
LANES = {"browsePage": "pages"}
INVALIDATES = {}
RECORDERS = {"browse": record_browse}
MUTATES = set()
