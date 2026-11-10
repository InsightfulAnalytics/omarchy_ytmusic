"""Raw InnerTube responses next to ytmusicapi's parsed ones.

ytmusicapi drops shelves it cannot name (it matches English titles), See all
and Play all endpoints, and rows without contents. This module keeps them:

- ``install(yt)`` wraps the client's ``_send_request`` once (Worker.load does
  it for every new client) and counts its HTTP requests per thread.
- ``with capture(("browse", "next")) as cap:`` records the responses of the
  calls made inside the block, on this thread only. Nothing is kept outside
  a block, so a 12-request ``get_playlist(limit=None)`` costs no extra memory.
  ``capture(endpoints, each=fn)`` hands each response to ``fn(endpoint,
  response)`` instead of keeping it (S3: a 50-request whole read holds none).
- ``parse_row``/``item``/``menu_token`` read rows, items and menus by
  renderer, endpoint and icon, never by their (localized) titles.

Every ytmusicapi internal used here (parsers, navigation paths) is imported
in this file or in ytm_pages.py, so an upgrade breaks two files at most.
Pinned to ytmusicapi 1.12.2; a KeyError from these shapes is reported as
UPSTREAM by the worker.
"""

import contextlib
import re
import threading

from ytmusicapi.navigation import (
    MMRIR,
    MRLIR,
    MTRIR,
    PAGE_TYPE,
    PLAY_BUTTON,
    SECTION_LIST_CONTINUATION,
    THUMBNAIL_RENDERER,
    THUMBNAILS,
    TITLE,
    TITLE_TEXT,
    nav,
)
from ytmusicapi.parsers._utils import get_flex_column_item
from ytmusicapi.parsers.browsing import (
    parse_album,
    parse_playlist,
    parse_related_artist,
    parse_song,
    parse_watch_playlist,
)
from ytmusicapi.parsers.library import parse_artists
from ytmusicapi.parsers.playlists import parse_playlist_item
from ytmusicapi.parsers.podcasts import parse_podcast
from ytmusicapi.parsers.songs import parse_song_menu_data

import ytm_normalize as norm

_MARK = "_ytm_raw_installed"
_local = threading.local()

ALBUM_PAGES = ("MUSIC_PAGE_TYPE_ALBUM", "MUSIC_PAGE_TYPE_AUDIOBOOK")
ARTIST_PAGE = "MUSIC_PAGE_TYPE_ARTIST"
LIBRARY_ARTIST_PAGE = "MUSIC_PAGE_TYPE_LIBRARY_ARTIST"
PLAYLIST_PAGE = "MUSIC_PAGE_TYPE_PLAYLIST"
USER_CHANNEL_PAGE = "MUSIC_PAGE_TYPE_USER_CHANNEL"
PODCAST_PAGE = "MUSIC_PAGE_TYPE_PODCAST_SHOW_DETAIL_PAGE"
EPISODE_PAGE = "MUSIC_PAGE_TYPE_NON_MUSIC_AUDIO_TRACK_PAGE"
VIDEO_TYPE = ["watchEndpointMusicSupportedConfigs", "watchEndpointMusicConfig", "musicVideoType"]

# Renderers that make up one row of a sectionList.
ROW_RENDERERS = (
    "musicCarouselShelfRenderer",
    "musicImmersiveCarouselShelfRenderer",
    "musicShelfRenderer",
    "musicPlaylistShelfRenderer",
    "gridRenderer",
    "musicDescriptionShelfRenderer",
    "itemSectionRenderer",
)


# ---- request hook and capture ----------------------------------------------------


def _count(response, *args, **kwargs):
    _local.http = getattr(_local, "http", 0) + 1


def reset_http():
    _local.http = 0


def http_count():
    return getattr(_local, "http", 0)


class Capture:
    def __init__(self, endpoints, each=None):
        self.endpoints = tuple(endpoints)
        self.responses = []  # [(endpoint, response)] in request order
        self.each = each

    def add(self, endpoint, response):
        if self.each is not None:
            self.each(endpoint, response)
        else:
            self.responses.append((endpoint, response))

    def first(self, endpoint=None):
        return next((r for e, r in self.responses if endpoint is None or e == endpoint), None)

    def all(self, endpoint=None):
        return [r for e, r in self.responses if endpoint is None or e == endpoint]


def install(yt):
    """Wrap ``yt._send_request`` and count HTTP requests; a no-op if already done."""
    try:
        if vars(yt).get(_MARK):
            return yt
    except TypeError:  # no __dict__: nothing to wrap
        return yt
    original = yt._send_request

    def send(endpoint, body, additionalParams=""):
        response = original(endpoint, body, additionalParams)
        for cap in getattr(_local, "captures", None) or ():
            if endpoint in cap.endpoints:
                cap.add(endpoint, response)
        return response

    yt._send_request = send
    session = getattr(yt, "_session", None)
    hooks = getattr(session, "hooks", None)
    if isinstance(hooks, dict):
        hooks.setdefault("response", []).append(_count)
    setattr(yt, _MARK, True)
    return yt


@contextlib.contextmanager
def capture(endpoints=("browse", "next"), each=None):
    """Record the raw responses of ``endpoints`` sent on this thread inside the
    block (or hand each to ``each(endpoint, response)`` instead)."""
    cap = Capture(endpoints, each)
    stack = getattr(_local, "captures", None)
    if stack is None:
        stack = _local.captures = []
    stack.append(cap)
    try:
        yield cap
    finally:
        stack.remove(cap)


# ---- rows ----------------------------------------------------------------------


def section_list(response):
    """The sectionListRenderer (or sectionListContinuation) holding a response's rows."""
    if not isinstance(response, dict):
        return None
    found = nav(response, SECTION_LIST_CONTINUATION, True)
    if isinstance(found, dict):
        return found
    contents = response.get("contents") or {}
    two = contents.get("twoColumnBrowseResultsRenderer") or {}
    found = nav(two, ["secondaryContents", "sectionListRenderer"], True)
    if isinstance(found, dict):
        return found
    for column in (contents.get("singleColumnBrowseResultsRenderer") or {}, two):
        for tab in column.get("tabs") or []:
            found = nav(tab, ["tabRenderer", "content", "sectionListRenderer"], True)
            if isinstance(found, dict):
                return found
    found = nav(contents, ["tabbedSearchResultsRenderer", "tabs", 0, "tabRenderer", "content", "sectionListRenderer"],
                True)
    return found if isinstance(found, dict) else None


def rows(response):
    """The rows of a browse, search or sectionListContinuation response, in order."""
    found = section_list(response)
    return (found or {}).get("contents") or []


def unwrap(row):
    """``(renderer key, data)`` of a row, looking inside a one-item itemSectionRenderer."""
    if not isinstance(row, dict) or not row:
        return None, None
    key, data = next(iter(row.items()))
    if key == "itemSectionRenderer":
        inner = (data or {}).get("contents") or []
        if len(inner) == 1 and isinstance(inner[0], dict) and inner[0]:
            return next(iter(inner[0].items()))
    return key, data



def text(value):
    """The plain text of a ``{"runs": [...]}`` or ``{"simpleText": ...}`` value."""
    if not isinstance(value, dict):
        return None
    if "simpleText" in value:
        return value["simpleText"]
    runs = value.get("runs")
    if not isinstance(runs, list):
        return None
    joined = "".join(run.get("text", "") for run in runs if isinstance(run, dict))
    return joined or None


# S3: the explicit badge, matched by its icon (labels are localized).
EXPLICIT_BADGE = "MUSIC_EXPLICIT_BADGE"


def explicit(badges):
    """True when ``badges`` (an item's ``badges`` or ``subtitleBadges``) holds the explicit badge."""
    for badge in badges if isinstance(badges, list) else ():
        icon = nav(badge, ["musicInlineBadgeRenderer", "icon", "iconType"], True) if isinstance(badge, dict) else None
        if icon == EXPLICIT_BADGE:
            return True
    return False


def browse_endpoint(endpoint):
    """``{browseId, params, pageType}`` of a navigation endpoint that browses, else None."""
    browse = (endpoint or {}).get("browseEndpoint") if isinstance(endpoint, dict) else None
    if not isinstance(browse, dict) or not browse.get("browseId"):
        return None
    return {"browseId": browse["browseId"], "params": browse.get("params"),
            "pageType": nav(browse, PAGE_TYPE, True)}


def watch_endpoint(endpoint):
    """``{videoId, playlistId, params}`` of a navigation endpoint that plays, else None."""
    if not isinstance(endpoint, dict):
        return None
    watch = endpoint.get("watchEndpoint") or endpoint.get("watchPlaylistEndpoint")
    if not isinstance(watch, dict) or not (watch.get("videoId") or watch.get("playlistId")):
        return None
    return {"videoId": watch.get("videoId"), "playlistId": watch.get("playlistId"), "params": watch.get("params")}


def row_parts(key, data):
    """``(layout, title, endpoints, contents)`` of one row renderer."""
    if key in ("musicCarouselShelfRenderer", "musicImmersiveCarouselShelfRenderer"):
        header = data.get("header") or {}
        header = next(iter(header.values()), {}) if header else {}
        button = nav(header, ["moreContentButton", "buttonRenderer", "navigationEndpoint"], True)
        title_endpoint = nav(header, ["title", "runs", 0, "navigationEndpoint"], True)
        title = text(header.get("title")) or text(header.get("strapline"))
        return "carousel", title, [button, title_endpoint], data.get("contents")
    if key in ("musicShelfRenderer", "musicPlaylistShelfRenderer"):
        title_endpoint = nav(data, ["title", "runs", 0, "navigationEndpoint"], True)
        return "shelf", text(data.get("title")), [data.get("bottomEndpoint"), title_endpoint], data.get("contents")
    if key == "gridRenderer":
        title = text(nav(data, ["header", "gridHeaderRenderer", "title"], True))
        return "grid", title, [], data.get("items")
    if key == "musicDescriptionShelfRenderer":
        return "description", text(data.get("header")), [], None
    return None, None, [], None


def parse_row(row, extra=None):
    """One sectionList row as ``{title, items, more, play, layout}``.

    Call it once per row and keep the result even when ``items`` is empty, so
    rows stay aligned with the response (ytmusicapi's parse_mixed_content skips
    rows without contents). ``more`` is the See all endpoint, ``play`` the
    header's Play all endpoint. Unknown renderers give an empty row.
    ``extra`` is passed on to ``items`` (S14: Home's menu tokens).
    """
    out = {"title": None, "items": [], "more": None, "play": None, "layout": None}
    if not isinstance(row, dict) or not row:
        return out
    key, data = next(iter(row.items()))
    if key == "itemSectionRenderer":
        inner = (data or {}).get("contents") or []
        return parse_row(inner[0], extra) if len(inner) == 1 else out
    if not isinstance(data, dict):
        return out
    layout, title, endpoints, contents = row_parts(key, data)
    out["layout"], out["title"] = layout, title
    for endpoint in endpoints:
        out["more"] = out["more"] or browse_endpoint(endpoint)
        out["play"] = out["play"] or watch_endpoint(endpoint)
    out["items"] = items(contents, extra)
    return out


def items(contents, extra=None):
    """Normalized items of a contents/items list; unknown or malformed ones are dropped.
    ``extra(content, item)`` (optional) returns the item with more of its
    raw renderer read (S14: ``feedback_fields``); when that fails, the item
    is kept as it was read. Without one, ``pin_fields`` reads each item's
    Listen again pin (G2: See all pages, artist shelves, Related)."""
    out = []
    for content in contents or []:
        try:
            value = item(content)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            norm.log(f"skipped malformed item: {type(exc).__name__}")
            continue
        if value is not None:
            try:
                value = (extra or pin_fields)(content, value)
            except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
                norm.log(f"item menu unreadable: {type(exc).__name__}")
        if value is not None:
            out.append(value)
    return out


# ---- items -----------------------------------------------------------------------


def item(renderer):
    """One normalized item from ``{"<renderer>": data}``, chosen by its page type.

    Album, artist, library artist (MPLA ids become UC), playlist, user channel
    (a profile), podcast and episode pages, plus songs, videos and watch
    playlists (mixes). Anything else is ``None``.
    """
    if not isinstance(renderer, dict) or len(renderer) != 1:
        return None
    key, data = next(iter(renderer.items()))
    if key == MTRIR:
        return _two_row(data)
    if key == MRLIR:
        return _list_row(data)
    if key == MMRIR:
        # G3: every episode row through S8's reader (its date, length, played
        # part and page), not parse_episode's (the view count as its date).
        # Imported here: ytm_podcasts imports this module.
        import ytm_podcasts

        return ytm_podcasts.episode_item(data)
    return None


def _endpoint(data):
    endpoint = data.get("navigationEndpoint")
    if isinstance(endpoint, dict) and ("browseEndpoint" in endpoint or "watchEndpoint" in endpoint
                                       or "watchPlaylistEndpoint" in endpoint):
        return endpoint
    return nav(data, [*TITLE, "navigationEndpoint"], True) or {}


# page type -> (ytmusicapi parser, normalizer) for musicTwoRowItemRenderer items
_TWO_ROW = {
    ALBUM_PAGES[0]: (parse_album, norm.album),
    ALBUM_PAGES[1]: (parse_album, norm.album),
    ARTIST_PAGE: (parse_related_artist, norm.artist),
    LIBRARY_ARTIST_PAGE: (parse_related_artist, norm.artist),
    USER_CHANNEL_PAGE: (parse_related_artist, norm.profile),
    PLAYLIST_PAGE: (parse_playlist, norm.playlist),
    PODCAST_PAGE: (parse_podcast, norm.podcast),
}


def _two_row(data):
    endpoint = _endpoint(data)
    browse = browse_endpoint(endpoint)
    if browse:
        page_type = browse["pageType"]
        if page_type == EPISODE_PAGE:
            overlay = nav(data, ["thumbnailOverlay", "musicItemThumbnailOverlayRenderer", "content",
                                 "musicPlayButtonRenderer", "playNavigationEndpoint"], True)
            return norm.episode(dict(_episode_subtitle(nav(data, ["subtitle", "runs"], True)),
                                     videoId=(watch_endpoint(overlay) or {}).get("videoId"),
                                     title=nav(data, TITLE_TEXT, True), browseId=browse["browseId"],
                                     thumbnails=nav(data, THUMBNAIL_RENDERER, True)))
        if page_type not in _TWO_ROW:
            return None
        parser, normalizer = _TWO_ROW[page_type]
        try:
            parsed = parser(data)
        except (KeyError, IndexError, TypeError):
            # Some items carry the endpoint on the item rather than on the title.
            parsed = {"title": nav(data, TITLE_TEXT, True), "browseId": browse["browseId"],
                      "thumbnails": nav(data, THUMBNAIL_RENDERER, True)}
        return normalizer(parsed)
    if "watchPlaylistEndpoint" in endpoint:
        return norm.playlist(parse_watch_playlist(data))
    if "watchEndpoint" in endpoint:
        song = parse_song(data)
        song["videoType"] = nav(endpoint, ["watchEndpoint", *VIDEO_TYPE], True)
        song.update(song_menu(data))  # G2: Save to library and the pin, as on list rows
        return norm.track(song)
    return None


# "2 min 56 sec", "1 hr 5 min", "45 sec": an episode's length in a subtitle.
_LENGTH = re.compile(r"^(?:(\d+)\s*hrs?\s*)?(?:(\d+)\s*mins?\s*)?(?:(\d+)\s*secs?)?$")


def _length(value):
    match = _LENGTH.match(value.strip()) if isinstance(value, str) else None
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _episode_subtitle(runs):
    """Credit, length and date of an episode's subtitle runs.

    A run that links to a page is the credit (a podcast, or the artist or
    channel that posted it); a length like "2 min 56 sec" is the duration;
    the remaining text (e.g. "Mar 3, 2025") is the date.
    """
    out = {"artists": [], "podcast": None, "duration_seconds": None, "date": None}
    dates = []
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        value = (run.get("text") or "").strip()
        browse = browse_endpoint(run.get("navigationEndpoint"))
        if browse and value:
            credit = {"name": value, "id": browse["browseId"]}
            if browse["pageType"] == PODCAST_PAGE:
                out["podcast"] = out["podcast"] or credit
            else:
                out["artists"].append(credit)
        elif value and value != "\u2022":
            length = _length(value)
            if length is not None and out["duration_seconds"] is None:
                out["duration_seconds"] = length
            else:
                dates.append(value)
    out["date"] = " \u2022 ".join(dates) or None
    return out


def _column_text(data, index):
    column = get_flex_column_item(data, index)
    return text(column.get("text")) if column else None


def _list_row(data):
    """A musicResponsiveListItemRenderer: a track, or a row that opens a page."""
    browse = browse_endpoint(data.get("navigationEndpoint"))
    if browse is None:
        play = nav(data, [*PLAY_BUTTON, "playNavigationEndpoint"], True)
        if nav(data, ["playlistItemData", "videoId"], True) or watch_endpoint(play) or \
                nav(data, ["flexColumns", 0, "musicResponsiveListItemFlexColumnRenderer", "text", "runs", 0,
                           "navigationEndpoint", "watchEndpoint"], True):
            parsed = parse_playlist_item(data)
            return norm.track(parsed) if parsed else None
        return None
    page_type = browse["pageType"]
    if page_type in (ARTIST_PAGE, LIBRARY_ARTIST_PAGE, USER_CHANNEL_PAGE):
        parsed = parse_artists([{MRLIR: data}])[0]
        return norm.profile(parsed) if page_type == USER_CHANNEL_PAGE else norm.artist(parsed)
    title = _column_text(data, 0)
    subtitle_runs = nav(get_flex_column_item(data, 1) or {}, ["text", "runs"], True) or []
    credits = [{"name": run.get("text"), "id": nav(run, ["navigationEndpoint", "browseEndpoint", "browseId"], True)}
               for run in subtitle_runs if isinstance(run, dict) and "navigationEndpoint" in run]
    basic = {"title": title, "browseId": browse["browseId"], "thumbnails": nav(data, THUMBNAILS, True)}
    if page_type in ALBUM_PAGES:
        return norm.album(dict(basic, artists=credits, isExplicit=explicit(data.get("badges"))))
    if page_type == PLAYLIST_PAGE:
        return norm.playlist(dict(basic, author=credits or None))
    if page_type == PODCAST_PAGE:
        return norm.podcast(dict(basic, channel=credits[0] if credits else None))
    if page_type == EPISODE_PAGE:
        play = nav(data, [*PLAY_BUTTON, "playNavigationEndpoint"], True)
        return norm.episode(dict(basic, videoId=(watch_endpoint(play) or {}).get("videoId"),
                                 podcast=credits[0] if credits else None))
    return None


# ---- menus ------------------------------------------------------------------------


_MENU_RENDERERS = ("menuServiceItemRenderer", "toggleMenuServiceItemRenderer", "menuNavigationItemRenderer")
_ENDPOINT_KEYS = ("serviceEndpoint", "defaultServiceEndpoint", "navigationEndpoint")


def menu_token(menu, icons):
    """The feedback token of the first menu entry whose icon is one of ``icons``.

    ``menu`` is a ``menuRenderer``, an item carrying ``menu``, or the items
    list. Entries are matched by icon type, never by their (localized) text.
    """
    if isinstance(menu, dict) and "menu" in menu:
        menu = menu["menu"]
    if isinstance(menu, dict):
        menu = (menu.get("menuRenderer") or menu).get("items")
    wanted = {icons} if isinstance(icons, str) else set(icons)
    for entry in menu or []:
        if not isinstance(entry, dict):
            continue
        renderer = next((entry[k] for k in _MENU_RENDERERS if isinstance(entry.get(k), dict)), None)
        if renderer is None:
            continue
        icon = nav(renderer, ["icon", "iconType"], True) or nav(renderer, ["defaultIcon", "iconType"], True)
        if icon not in wanted:
            continue
        for key in _ENDPOINT_KEYS:
            token = nav(renderer, [key, "feedbackEndpoint", "feedbackToken"], True)
            if token:
                return token
    return None


# ---- Listen again pins and recommendation feedback (S14) ---------------------------
# Matched by icon, never by text. KEEP is "Pin to Listen again", a toggle:
# isToggled when the item is pinned, its default endpoint pins and its
# toggled one unpins. KEEP_OFF is the same toggle served on an item that is
# pinned (Listen again's own cards, your pinned playlist's header): its
# default endpoint unpins. NOT_INTERESTED is "Not interested", and
# REMOVE_CIRCLE "Don't recommend artist" (or "channel", on podcasts); both
# only on Home's cards. Either opens the web's "Removed from Home" toast,
# whose Undo button carries its own feedback token (read by structure):
# sent the same way, it takes the feedback back. Every token is
# account-bound: the disk cache drops them (ytm_cache), so does the
# recorder's scrub.

PIN_ICONS = ("KEEP", "KEEP_OFF")
NOT_INTERESTED_ICONS = ("NOT_INTERESTED",)
# The plan named REMOVE; the live feed serves REMOVE_CIRCLE.
DONT_RECOMMEND_ICONS = ("REMOVE_CIRCLE", "REMOVE")


def _menu_items(menu):
    if isinstance(menu, dict) and "menu" in menu:
        menu = menu["menu"]
    if isinstance(menu, dict):
        menu = (menu.get("menuRenderer") or menu).get("items")
    return menu if isinstance(menu, list) else []


def _entry_icon(renderer):
    return nav(renderer, ["icon", "iconType"], True) or nav(renderer, ["defaultIcon", "iconType"], True)


def menu_entry(menu, icons):
    """The renderer of the first entry of ``menu`` (as ``menu_token`` takes
    it) whose icon is one of ``icons``, or None."""
    wanted = {icons} if isinstance(icons, str) else set(icons)
    for entry in _menu_items(menu):
        if not isinstance(entry, dict):
            continue
        renderer = next((entry[k] for k in _MENU_RENDERERS if isinstance(entry.get(k), dict)), None)
        if renderer is not None and _entry_icon(renderer) in wanted:
            return renderer
    return None


def _feedback_token(renderer, key):
    token = nav(renderer, [key, "feedbackEndpoint", "feedbackToken"], True)
    return token if isinstance(token, str) and token else None


def pin_state(menu):
    """``{pinned, listenAgainTokens: {pin, unpin}}`` of a menu's Listen again
    toggle, or None when it has none (or no token)."""
    entry = menu_entry(menu, PIN_ICONS)
    if entry is None:
        return None
    default = _feedback_token(entry, "defaultServiceEndpoint") or _feedback_token(entry, "serviceEndpoint")
    toggled = _feedback_token(entry, "toggledServiceEndpoint")
    toggled_on = entry.get("isToggled") is True
    if _entry_icon(entry) == "KEEP":
        pinned, pin, unpin = toggled_on, default, toggled
    else:
        pinned, pin, unpin = not toggled_on, toggled, default
    if not pin and not unpin:
        return None
    return {"pinned": pinned, "listenAgainTokens": {"pin": pin, "unpin": unpin}}


# Where an album's or playlist's header sits (your own playlist's inside
# an editable header); an artist's is the response's own header.
_TAB_HEADER = ["contents", "twoColumnBrowseResultsRenderer", "tabs", 0, "tabRenderer", "content",
               "sectionListRenderer", "contents", 0]


def header_menu(response):
    """The menu of a page's header (``{"menuRenderer": ...}`` or a renderer's
    ``menu``), or None: an artist's header carries it, an album's or
    playlist's has it among its buttons."""
    if not isinstance(response, dict):
        return None
    top = response.get("header")
    data = next(iter(top.values()), None) if isinstance(top, dict) and top else None
    if isinstance(data, dict) and isinstance(data.get("menu"), dict):
        return data["menu"]
    header = nav(response, _TAB_HEADER, True)
    if isinstance(header, dict) and isinstance(header.get("musicEditablePlaylistDetailHeaderRenderer"), dict):
        header = header["musicEditablePlaylistDetailHeaderRenderer"].get("header")
    data = header.get("musicResponsiveHeaderRenderer") if isinstance(header, dict) else None
    for button in (data.get("buttons") if isinstance(data, dict) else None) or []:
        if isinstance(button, dict) and isinstance(button.get("menuRenderer"), dict):
            return button
    return None


def header_pin(response):
    """``pin_state`` of a page header's menu: an album's, a playlist's or an artist's."""
    return pin_state(header_menu(response))


# Where a feedback entry's toast keeps its Undo token, under each of
# serviceEndpoint.feedbackEndpoint.actions.
_UNDO_PATH = ("openPopupAction", "popup", "notificationActionRenderer", "actionButton", "buttonRenderer",
              "command", "feedbackEndpoint", "feedbackToken")


def _undo_token(renderer):
    """The Undo token of the toast a feedback entry opens, or None."""
    actions = nav(renderer, ["serviceEndpoint", "feedbackEndpoint", "actions"], True) if renderer else None
    for action in actions if isinstance(actions, list) else []:
        value = action
        for key in _UNDO_PATH:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, str) and value:
            return value
    return None


def item_feedback(menu):
    """``{notInterested, dontRecommend, notInterestedUndo, dontRecommendUndo}``
    tokens of a Home card's menu (None where it has none), or None when it
    has neither entry. An Undo token only comes with its entry's."""
    not_interested = menu_entry(menu, NOT_INTERESTED_ICONS)
    dont_recommend = menu_entry(menu, DONT_RECOMMEND_ICONS)
    out = {"notInterested": _feedback_token(not_interested, "serviceEndpoint") if not_interested else None,
           "dontRecommend": _feedback_token(dont_recommend, "serviceEndpoint") if dont_recommend else None}
    out["notInterestedUndo"] = _undo_token(not_interested) if out["notInterested"] else None
    out["dontRecommendUndo"] = _undo_token(dont_recommend) if out["dontRecommend"] else None
    return out if out["notInterested"] or out["dontRecommend"] else None


def feedback_fields(content, value):
    """``items``'s ``extra`` for Home: ``value`` (the item read from
    ``content``) with ``pinned`` and ``listenAgainTokens`` when its menu has
    the Listen again toggle, and ``feedback`` when it has Not interested or
    Don't recommend. An item whose menu has neither comes back as it is."""
    data = next(iter(content.values()), None) if isinstance(content, dict) and len(content) == 1 else None
    menu = data.get("menu") if isinstance(data, dict) else None
    if not isinstance(menu, dict) or not isinstance(value, dict):
        return value
    pin = pin_state(menu)
    if pin is not None:
        value = dict(value, **pin)
    feedback = item_feedback(menu)
    if feedback is not None:
        value = dict(value, feedback=feedback)
    return value


# ---- pins and song tokens on every row (G2) ---------------------------------------------
# The web offers Pin to Listen again in the menu of nearly every card and
# row (albums, playlists, artists, podcasts, episodes, songs, videos), not
# only on Home's. ``items`` reads it for every row it parses (pin_fields);
# song and video cards read their whole song menu like list rows do
# (``_two_row``: Save to library too); podcast and episode pages read their
# header's; lists ytmusicapi parses without menus get theirs from the raw
# cards (``card_pins``). Tokens stay in fresh replies only: the disk cache
# drops them (ytm_cache.strip_tokens), and so does the recorder's scrub.


def pin_fields(content, value):
    """``items``'s default ``extra``: ``value`` (the item read from
    ``content``) with ``pinned`` and ``listenAgainTokens`` from its menu's
    Listen again toggle, unless it carries tokens of its own (a song's
    come from its song menu). As it is when the menu has no pin."""
    if not isinstance(value, dict) or value.get("listenAgainTokens"):
        return value
    data = next(iter(content.values()), None) if isinstance(content, dict) and len(content) == 1 else None
    pin = pin_state(data.get("menu")) if isinstance(data, dict) and isinstance(data.get("menu"), dict) else None
    return dict(value, **pin) if pin is not None else value


def song_menu(data):
    """ytmusicapi's song menu fields of a card (``inLibrary``,
    ``feedbackTokens``, ``pinnedToListenAgain``, ``listenAgainFeedbackTokens``);
    ``{}`` when its menu has no item list, so the card is kept without them."""
    try:
        return parse_song_menu_data(data)
    except (KeyError, TypeError, IndexError, AttributeError):
        return {}


def buttons_pin(header):
    """``pin_state`` of a musicResponsiveHeaderRenderer's menu button (a
    podcast's or an episode's page header), or None."""
    for button in (header.get("buttons") if isinstance(header, dict) else None) or []:
        if isinstance(button, dict) and isinstance(button.get("menuRenderer"), dict):
            return pin_state(button)
    return None


def card_pins(responses):
    """``{browseId: pin_state}`` of the two-row cards (albums, playlists) in
    raw ``responses`` that serve the Listen again toggle: for lists read
    through a ytmusicapi parser that reads no menu (Library > Playlists'
    grid, an album's other versions and releases, a playlist's related)."""
    out = {}
    stack = list(responses or [])
    while stack:
        value = stack.pop()
        if isinstance(value, list):
            stack.extend(value)
            continue
        if not isinstance(value, dict):
            continue
        data = value.get(MTRIR)
        if not isinstance(data, dict):
            stack.extend(value.values())
            continue
        browse_id = (nav(data, [*TITLE, "navigationEndpoint", "browseEndpoint", "browseId"], True)
                     or nav(data, ["navigationEndpoint", "browseEndpoint", "browseId"], True))
        pin = pin_state(data.get("menu")) if isinstance(data.get("menu"), dict) else None
        if isinstance(browse_id, str) and pin is not None:
            out.setdefault(browse_id, pin)
    return out


def with_card_pins(items, pins):
    """``items`` (normalized albums and playlists) with ``pinned`` and
    ``listenAgainTokens`` from ``pins`` (card_pins), matched by the card's
    browse id; an item with tokens of its own or no card is as it was."""
    if not pins:
        return items
    out = []
    for value in items or []:
        pin = None
        if isinstance(value, dict) and not value.get("listenAgainTokens"):
            playlist_id = value.get("playlistId") if value.get("type") == "playlist" else None
            key = "VL" + str(playlist_id) if playlist_id else value.get("browseId")
            pin = pins.get(key) or (pins.get(playlist_id) if playlist_id else None)
        out.append(dict(value, **pin) if pin else value)
    return out
