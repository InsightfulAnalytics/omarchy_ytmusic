"""Explore (S6): the web's Explore page, Charts, Moods & genres and a mood's playlists.

- ``explore({})`` is ``get_explore()`` with its raw rows: a page of kind
  ``explore``. Its first section (key ``entries``) holds the page's
  navigation buttons that open a page of their own (New releases, Charts,
  Moods & genres) as See all style ``more`` items. Then one section per shelf, in the served
  order with the served title and See all endpoint: ``newReleases`` (albums
  and singles), ``topSongs`` (Premium only; each song with its ``rank`` and
  ``trend``), ``moods`` (every category of the shelf: 17 of its 51 are on no
  other page, not even its See all; the shell shows a few and the rest on
  request), ``trending``, ``newVideos`` and ``episodes`` (only when YouTube
  serves them). Shelves are
  told apart by their See all endpoint, as ytmusicapi does, never by title;
  a shelf ytmusicapi does not know is kept as a generic section, and every
  shelf may be missing.
- ``charts({country?})`` is ``get_charts(country)`` with its raw rows:
  ``country`` is ``ZZ`` (Global, the default) or a two-letter code. One
  section per chart shelf as served (daily, weekly or video chart
  playlists, genres, Top artists with ``rank`` and ``trend``, podcast
  shows), plus ``country`` ``{code, label}`` (what YouTube served) and
  ``countries`` ``[{code, label}]`` (every option of the page's country
  menu, in its order).
- ``moods({})`` is ``get_mood_categories()``: one grid section per group
  (For you, Moods & moments, Genres) of ``mood`` items ``{type, id, title,
  params}``.
- ``mood({params, title?})`` reads the raw category page and parses each
  row on its own, so titled shelves keep their titles and See all (a
  shelf's See all is another category). When no row holds anything, it
  falls back to ``get_mood_playlists``' parse of the same response (no
  second request).

Area module (see AREA_MODULES in ytm.py). Like ytm_raw.py it reads
ytmusicapi internals (navigation paths, parsers), pinned to 1.12.2.
"""

import re

from ytmusicapi.navigation import (CAROUSEL_CONTENTS, FRAMEWORK_MUTATIONS, GRID_ITEMS, MMRIR, MRLIR, MTRIR,
                                   SECTION_LIST, SINGLE_COLUMN_TAB, nav)
from ytmusicapi.parsers._utils import get_flex_column_item
from ytmusicapi.parsers.browsing import parse_playlist
from ytmusicapi.parsers.explore import parse_chart_episode, parse_ranking
from ytmusicapi.parsers.songs import parse_song_runs

import ytm_normalize as norm
import ytm_raw as raw
from ytm_proto import WorkerError, opt_str, req_str

EXPLORE_ID = "FEmusic_explore"
CHARTS_ID = "FEmusic_charts"
MOODS_ID = "FEmusic_moods_and_genres"
MOOD_ID = "FEmusic_moods_and_genres_category"
NEW_RELEASES_ID = "FEmusic_new_releases"
NAV_BUTTON = "musicNavigationButtonRenderer"
GLOBAL = "ZZ"
_COUNTRY = re.compile(r"[A-Za-z]{2}")

# Explore's shelves by See all endpoint (ytmusicapi's own rule; titles are
# localized). VLPL is the Top songs chart, VLOLA the Trending playlist.
EXPLORE_KEYS = {
    "FEmusic_new_releases_albums": "newReleases",
    MOODS_ID: "moods",
    "FEmusic_new_releases_videos": "newVideos",
    "FEmusic_top_non_music_audio_episodes": "episodes",
}
# The navigation buttons the Explore page lists as entry rows (all three
# the web shows; Moods & genres is also its shelf's See all).
ENTRY_IDS = (NEW_RELEASES_ID, CHARTS_ID, MOODS_ID)
# Titles when only ytmusicapi's parse is at hand (no raw rows).
FALLBACK_TITLES = {
    "newReleases": "New albums & singles", "topSongs": "Top songs", "moods": "Moods & genres",
    "trending": "Trending", "newVideos": "New music videos", "episodes": "Popular episodes",
    "daily": "Daily charts", "weekly": "Weekly charts", "videos": "Video charts", "genres": "Genres",
    "languages": "Languages", "artists": "Top artists",
}
FALLBACK_ENTRIES = ((NEW_RELEASES_ID, "New releases"), (CHARTS_ID, "Charts"), (MOODS_ID, "Moods & genres"))
# get_charts' playlist keys, in the order the page serves them.
CHART_PLAYLIST_KEYS = ("daily", "weekly", "videos", "genres", "languages")


# ---- items ------------------------------------------------------------------------------


def mood(title, params):
    """A Moods & genres category: opens as a ``mood`` page. ``id`` is its
    params, so two categories with one title stay apart."""
    if not params:
        raise ValueError("mood without params")
    return {"type": "mood", "id": params, "title": title or "", "params": params}


def _mood_button(data):
    endpoint = raw.browse_endpoint(data.get("clickCommand")) or {}
    return mood(raw.text(data.get("buttonText")), endpoint.get("params"))


def _ranking(data):
    """``{rank, trend}`` of a chart row (Top songs, Top artists), else {}."""
    found = parse_ranking(data, none_if_absent=True)
    return found if found.get("rank") is not None else {}


def _song_credits(value, data):
    """A chart or Trending song's artists as ytmusicapi reads them
    (parse_song_flat, through parse_chart_song and parse_trending_item): the
    second column's runs, where a view count ("70M views") or a year is not
    an artist. raw.item reads every run of that column as one."""
    runs = nav(get_flex_column_item(data, 1) or {}, ["text", "runs"], True)
    if not runs:
        return
    try:
        found = norm.artists(parse_song_runs(runs, skip_type_spec=True).get("artists"))
    except (KeyError, TypeError, AttributeError):
        return
    value.update(artists=found, artistText=norm.artist_text(found))


def _item(content):
    key, data = next(iter(content.items()))
    if key == NAV_BUTTON:
        return _mood_button(data)
    if key == MMRIR:
        # Popular episodes name their podcast in the second title.
        try:
            return norm.episode(parse_chart_episode(data))
        except (KeyError, TypeError, IndexError):
            return raw.item(content)
    value = raw.item(content)
    if value is not None and key == MRLIR:
        if value.get("type") == "track":
            _song_credits(value, data)
        value.update(_ranking(data))
    return value


def items(contents):
    """Normalized items of a row: raw.item's, plus mood categories, chart ranks
    and song credits without the view count."""
    out = []
    for content in contents or []:
        if not isinstance(content, dict) or len(content) != 1:
            continue
        try:
            value = _item(content)
            value = raw.pin_fields(content, value)  # G2: Pin to Listen again, as on the web's cards
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            norm.log(f"skipped malformed explore item: {type(exc).__name__}")
            continue
        if value is not None:
            out.append(value)
    return out


def section(row, key=None):
    """One raw row as a section (see ytm_raw.parse_row), with these items; None
    for a row that is not a shelf."""
    name, data = raw.unwrap(row)
    if name not in raw.ROW_RENDERERS or not isinstance(data, dict):
        return None
    layout, title, endpoints, contents = raw.row_parts(name, data)
    if layout in (None, "description"):
        return None
    more = next((found for found in map(raw.browse_endpoint, endpoints) if found), None)
    play = next((found for found in map(raw.watch_endpoint, endpoints) if found), None)
    return norm.section(title, items(contents), more, play, layout, key=key)


def _header_title(response):
    return raw.text(nav(response, ["header", "musicHeaderRenderer", "title"], True)) if response else None


def _nav_buttons(data):
    """The navigation buttons of a grid row, or [] for any other grid."""
    entries = (data or {}).get("items") or []
    if not entries or not all(isinstance(e, dict) and NAV_BUTTON in e for e in entries):
        return []
    return [e[NAV_BUTTON] for e in entries]


# ---- explore ------------------------------------------------------------------------------


def explore_key(more):
    browse_id = (more or {}).get("browseId") or ""
    if browse_id in EXPLORE_KEYS:
        return EXPLORE_KEYS[browse_id]
    if browse_id.startswith("VLPL"):
        return "topSongs"
    if browse_id.startswith("VLOLA"):
        return "trending"
    return None


def _entries(buttons):
    out = []
    for button in buttons:
        endpoint = raw.browse_endpoint(button.get("clickCommand"))
        if endpoint and endpoint["browseId"] in ENTRY_IDS:
            out.append(norm.more(raw.text(button.get("buttonText")), endpoint))
    return out


def _explore_rows(rows):
    entries, sections = [], []
    for row in rows:
        name, data = raw.unwrap(row)
        if name == "gridRenderer" and _nav_buttons(data):
            entries.extend(_entries(_nav_buttons(data)))
            continue
        try:
            found = section(row)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            norm.log(f"skipped an explore row: {type(exc).__name__}")
            continue
        if not found or not (found["items"] or found["more"]):
            continue
        found["key"] = explore_key(found["more"])
        sections.append(found)
    return entries, sections


def _fallback_track(item, kind=None):
    value = norm.track(item)
    if kind:
        value["kind"] = kind
    if item.get("rank") is not None:
        value.update(rank=item["rank"], trend=item.get("trend"))
    return value


def _explore_from(data):
    """The sections from ytmusicapi's parse alone (no raw rows were seen)."""
    data = data or {}
    sections = []

    def add(key, values, browse_id):
        if values:
            more = {"browseId": browse_id, "params": None, "pageType": None} if browse_id else None
            sections.append(norm.section(FALLBACK_TITLES[key], values, more, layout="carousel", key=key))

    add("newReleases", norm.normalize_list(data.get("new_releases"), norm.album, "new release"),
        "FEmusic_new_releases_albums")
    for key, name in (("topSongs", "top_songs"), ("trending", "trending")):
        block = data.get(name) or {}
        add(key, norm.normalize_list(block.get("items"), _fallback_track, f"{name} item"), block.get("playlist"))
    moods = norm.normalize_list(data.get("moods_and_genres"), lambda m: mood(m.get("title"), m.get("params")), "mood")
    add("moods", moods, MOODS_ID)
    add("newVideos", norm.normalize_list(data.get("new_videos"), lambda v: _fallback_track(v, "video"), "new video"),
        "FEmusic_new_releases_videos")
    add("episodes", norm.normalize_list(data.get("top_episodes"), norm.episode, "episode"),
        "FEmusic_top_non_music_audio_episodes")
    entries = [norm.more(title, {"browseId": browse_id}) for browse_id, title in FALLBACK_ENTRIES]
    return entries, sections


def build_explore(data, response):
    """The Explore page from get_explore's result and its raw response (None
    when it was not seen)."""
    entries, sections = _explore_rows(raw.rows(response)) if response else ([], [])
    if not sections:
        entries, sections = _explore_from(data)
    if entries:
        sections.insert(0, norm.section(None, entries, layout="grid", key="entries"))
    return norm.page("explore", EXPLORE_ID, _header_title(response) or "Explore", sections=sections)


def m_explore(worker, params):
    with raw.capture(("browse",)) as cap:
        try:
            data = worker.yt.get_explore()
        except (KeyError, TypeError, IndexError) as exc:
            # ytmusicapi could not read the page; its rows may still be readable.
            if cap.first("browse") is None:
                raise
            norm.log(f"get_explore failed ({type(exc).__name__}); reading the raw rows")
            data = {}
    return build_explore(data, cap.first("browse"))


# ---- charts -------------------------------------------------------------------------------


def country_param(params):
    """The chart country asked for: ZZ (Global) when none, else two letters."""
    value = params.get("country")
    if value is None or value == "":
        return GLOBAL
    if not isinstance(value, str) or not _COUNTRY.fullmatch(value):
        raise WorkerError("BAD_REQUEST", "'country' must be ZZ (Global) or a two-letter country code")
    return value.upper()


def _country_button(response):
    rows = raw.rows(response)
    return nav(rows[0] if rows else {}, ["musicShelfRenderer", "subheaders", 0, "musicSideAlignedItemRenderer",
                                         "startItems", 0, "musicSortFilterButtonRenderer"], True) or {}


def countries(response):
    """``(options, selected)``: the country menu's options ``[{code, label}]``
    in served order (each code once), and the code marked selected (None
    when none is). The code of an option is its form choice's opaqueToken in
    frameworkUpdates, keyed by the option's formItemEntityKey."""
    choices = {}
    for mutation in nav(response, FRAMEWORK_MUTATIONS, True) or []:
        choice = nav(mutation, ["payload", "musicFormBooleanChoice"], True)
        if isinstance(choice, dict) and mutation.get("entityKey") and choice.get("opaqueToken"):
            choices[mutation["entityKey"]] = choice
    options, seen, selected = [], set(), None
    menu = nav(_country_button(response), ["menu", "musicMultiSelectMenuRenderer", "options"], True) or []
    for option in menu:
        item = (option or {}).get("musicMultiSelectMenuItemRenderer") if isinstance(option, dict) else None
        choice = choices.get((item or {}).get("formItemEntityKey"))
        if not choice:
            continue
        code = choice["opaqueToken"]
        if choice.get("selected"):
            selected = code
        if code not in seen:
            seen.add(code)
            options.append({"code": code, "label": raw.text(item.get("title")) or code})
    return options, selected


def chart_key(values):
    """What a chart shelf holds, by its items' type (titles are localized):
    ``artists``, ``podcasts`` or ``playlists`` (the song and video charts,
    genres); None when mixed."""
    types = {value.get("type") for value in values}
    return {"artist": "artists", "podcast": "podcasts", "playlist": "playlists"}.get(types.pop()) \
        if len(types) == 1 else None


def _charts_from(data):
    sections = []
    for key in (*CHART_PLAYLIST_KEYS, "artists"):
        normalize = norm.playlist if key != "artists" else _chart_artist
        values = norm.normalize_list(data.get(key), normalize, f"{key} chart")
        if values:
            sections.append(norm.section(FALLBACK_TITLES[key], values, layout="carousel", key=chart_key(values)))
    return sections


def _chart_artist(item):
    value = norm.artist(item)
    if item.get("rank") is not None:
        value.update(rank=item["rank"], trend=item.get("trend"))
    return value


def build_charts(country, data, response):
    """The Charts page for ``country`` from get_charts' result and its raw response."""
    data = data or {}
    options, selected = countries(response) if response else ([], None)
    if not options:
        codes = (data.get("countries") or {}).get("options") or []
        options = [{"code": code, "label": code} for code in dict.fromkeys(c for c in codes if isinstance(c, str))]
    code = selected or country
    served = raw.text(_country_button(response).get("title")) if response else None
    served = served or nav(data, ["countries", "selected", "text"], True)
    # (Options from ytmusicapi's parse alone are labelled with their codes.)
    label = next((o["label"] for o in options if o["code"] == code and o["label"] != code), None) or served or code
    sections = []
    for row in raw.rows(response)[1:] if response else []:
        try:
            found = section(row)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            norm.log(f"skipped a charts row: {type(exc).__name__}")
            continue
        if found and found["items"]:
            found["key"] = chart_key(found["items"])
            sections.append(found)
    if not sections:
        sections = _charts_from(data)
    page = norm.page("charts", code, _header_title(response) or "Charts", label, sections=sections)
    page["country"] = {"code": code, "label": label}
    page["countries"] = options
    return page


def m_charts(worker, params):
    country = country_param(params)
    with raw.capture(("browse",)) as cap:
        data = worker.yt.get_charts(country)
    return build_charts(country, data, cap.first("browse"))


# ---- moods --------------------------------------------------------------------------------


def build_moods(data, response=None):
    sections = []
    for title, categories in (data or {}).items():
        values = norm.normalize_list(categories, lambda m: mood(m.get("title"), m.get("params")), "mood")
        if values:
            sections.append(norm.section(title, values, layout="grid"))
    return norm.page("moods", MOODS_ID, _header_title(response) or "Moods & genres", sections=sections)


def m_moods(worker, params):
    with raw.capture(("browse",)) as cap:
        data = worker.yt.get_mood_categories()
    return build_moods(data, cap.first("browse"))


def mood_sections(response):
    """The category page's rows, each on its own: titled shelves with their See all."""
    out = []
    for row in raw.rows(response):
        try:
            found = section(row)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            norm.log(f"skipped a mood row: {type(exc).__name__}")
            continue
        if found and (found["items"] or found["more"]):
            out.append(found)
    return out


# Where get_mood_playlists finds a row's playlists, by row renderer.
MOOD_PLAYLIST_PATHS = (("gridRenderer", GRID_ITEMS), ("musicCarouselShelfRenderer", CAROUSEL_CONTENTS),
                       ("musicImmersiveCarouselShelfRenderer", ["musicImmersiveCarouselShelfRenderer", "contents"]))


def mood_playlists(response):
    """get_mood_playlists' parse of a category page already at hand (calling
    it would send the same request again): every playlist of its grids and
    carousels, in one list. An item it cannot read is skipped (ytmusicapi
    would fail the whole list)."""
    out = []
    for row in nav(response, SINGLE_COLUMN_TAB + SECTION_LIST, True) or []:
        path = next((path for name, path in MOOD_PLAYLIST_PATHS if isinstance(row, dict) and name in row), None)
        for content in (nav(row, path, True) or []) if path else []:
            data = content.get(MTRIR) if isinstance(content, dict) else None
            if data is None:
                continue
            try:
                out.append(parse_playlist(data))
            except (KeyError, TypeError, IndexError, AttributeError) as exc:
                norm.log(f"skipped a mood playlist: {type(exc).__name__}")
    return out


def m_mood(worker, params):
    mood_params = req_str(params, "params")
    title = opt_str(params, "title", high=200)
    response = worker.yt._send_request("browse", {"browseId": MOOD_ID, "params": mood_params})
    sections = mood_sections(response)
    if not any(s["items"] for s in sections):
        playlists = norm.normalize_list(mood_playlists(response), norm.playlist, "mood playlist")
        sections = [norm.section(None, playlists, layout="grid")] if playlists else []
    return norm.page("mood", mood_params, _header_title(response) or title or "", sections=sections)


# ---- recorders (signed out, raw responses) ---------------------------------------------------

_ITEM_RENDERERS = ("musicTwoRowItemRenderer", MRLIR, MMRIR)


def _trimmed(value, keep, item=False):
    """``value`` without the items' menus (the country menu stays, without
    its options' commands), and each row of items (not navigation buttons)
    cut to ``keep`` (None keeps all)."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if (item and k == "menu") or k == "selectedCommand":
                continue
            if k in ("contents", "items") and isinstance(v, list) and keep is not None and v \
                    and isinstance(v[0], dict) and next(iter(v[0]), None) in _ITEM_RENDERERS:
                v = v[:keep]
            out[k] = _trimmed(v, keep, k in _ITEM_RENDERERS)
        return out
    if isinstance(value, list):
        return [_trimmed(v, keep) for v in value]
    return value


def _codes_kept(response):
    """The scrub drops every key containing "token", so the country codes
    (a form choice's opaqueToken) are kept as ``countryCode`` beside it; the
    tests put them back."""
    for mutation in nav(response, FRAMEWORK_MUTATIONS, True) or []:
        choice = nav(mutation, ["payload", "musicFormBooleanChoice"], True)
        if isinstance(choice, dict) and choice.get("opaqueToken"):
            choice["countryCode"] = choice["opaqueToken"]
    return response


def record_explore(yt, params):
    return _trimmed(yt._send_request("browse", {"browseId": EXPLORE_ID}), params.get("keep", 4))


def record_charts(yt, params):
    body = {"browseId": CHARTS_ID, "formData": {"selectedValues": [params.get("country") or GLOBAL]}}
    return _codes_kept(_trimmed(yt._send_request("browse", body), params.get("keep", 10)))


def record_moods(yt, params):
    return _trimmed(yt._send_request("browse", {"browseId": MOODS_ID}), None)


def record_mood_raw(yt, params):
    body = {"browseId": MOOD_ID, "params": params["params"]}
    return _trimmed(yt._send_request("browse", body), params.get("keep", 3))


# ---- registry (merged by ytm.py) --------------------------------------------------------

METHODS = {"explore": m_explore, "charts": m_charts, "moods": m_moods, "mood": m_mood}
CACHEABLE = {"explore", "charts", "moods", "mood"}
# One request each, about a second for Explore: on the pages lane, with the
# See all pages they lead to.
LANES = {"explore": "pages", "charts": "pages", "moods": "pages", "mood": "pages"}
INVALIDATES = {}
RECORDERS = {"explore": record_explore, "charts": record_charts, "moods": record_moods, "moodRaw": record_mood_raw}
MUTATES = set()
