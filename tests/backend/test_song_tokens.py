"""Song tokens and pins everywhere (G2).

The song playing's own menu in nowContext (``current``), Pin to Listen
again on every row the worker parses (See all pages, Listen again's above
all, artist shelves, Related, Explore, library lists, podcast pages and
their episode rows), Save to library on song and video cards, and the pin
of podcast and episode page headers. Signed-in menus hold account-bound
tokens, so they are added to anonymous fixtures by hand; every check that
a token reaches the disk cache reads the cache files themselves."""

import copy
import json

import ytm
import ytm_artist as artist
import ytm_cache as caches
import ytm_now as now
import ytm_raw as raw
from test_now import KARMA, TWIN, client, panel, signed_in, signed_in_watch

PODCAST = "MPSPPLIJumiHv10VhoiQHAahw2E9ErgZ-jc0tC"
EPISODE = "ZAzWPE9CItg"
LISTEN_AGAIN = "FEmusic_listen_again"


# ---- hand-built menus ---------------------------------------------------------------------


def fb(token):
    return {"feedbackEndpoint": {"feedbackToken": token}}


def toggle(icon, default, toggled, on=None):
    """A toggleMenuServiceItemRenderer as served (matched by icon, not text)."""
    data = {"defaultText": {"runs": [{"text": "x"}]}, "defaultIcon": {"iconType": icon},
            "defaultServiceEndpoint": fb(default), "toggledServiceEndpoint": fb(toggled)}
    if on is not None:
        data["isToggled"] = on
    return {"toggleMenuServiceItemRenderer": data}


def keep(name, pinned=False):
    """The Listen again toggle of item ``name``; a pinned item is served KEEP_OFF."""
    if pinned:
        return toggle("KEEP_OFF", "UNPIN_" + name, "PIN_" + name)
    return toggle("KEEP", "PIN_" + name, "UNPIN_" + name)


def bookmark(name, saved=False):
    if saved:
        return toggle("BOOKMARK", "REMOVE_" + name, "ADD_" + name)
    return toggle("BOOKMARK_BORDER", "ADD_" + name, "REMOVE_" + name)


def share():
    return {"menuNavigationItemRenderer": {"text": {"runs": [{"text": "Share"}]}, "icon": {"iconType": "SHARE"},
                                           "navigationEndpoint": {"shareEntityEndpoint": {"serializedShareEntity": "x"}}}}


def with_menu(renderer, *entries):
    """A copy of ``{"<renderer>": data}`` with a menu of ``entries``."""
    out = copy.deepcopy(renderer)
    data = next(iter(out.values()))
    data["menu"] = {"menuRenderer": {"items": [share(), *entries]}}
    return out


def pins(name):
    return {"pin": "PIN_" + name, "unpin": "UNPIN_" + name}


def renderers(value, key, test=lambda data: True):
    """Every ``{key: data}`` in ``value``, in order."""
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == key and isinstance(v, dict) and test(v):
                out.append({k: v})
            else:
                out.extend(renderers(v, key, test))
    elif isinstance(value, list):
        for v in value:
            out.extend(renderers(v, key, test))
    return out


def page_type(data):
    endpoint = data.get("navigationEndpoint") or {}
    if "watchEndpoint" in endpoint:
        return "watch"
    return raw.browse_endpoint(endpoint)["pageType"] if raw.browse_endpoint(endpoint) else None


def two_rows(load_fixture):
    """One anonymous card of each kind from the artist page's rows: album,
    playlist, artist and a video (a watchEndpoint card)."""
    rows = load_fixture("artist_raw_rows.json")["rows"]
    found = {}
    for card in renderers(rows, "musicTwoRowItemRenderer"):
        found.setdefault(page_type(card["musicTwoRowItemRenderer"]), card)
    return [found[k] for k in (raw.ALBUM_PAGES[0], raw.PLAYLIST_PAGE, raw.ARTIST_PAGE, "watch")]


class Served:
    """Answers _send_request from a queue (the last answer repeats) and records it."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.sent = []

    def _send_request(self, endpoint, body, additionalParams=""):
        self.sent.append((endpoint, dict(body)))
        return copy.deepcopy(self.responses[min(len(self.sent), len(self.responses)) - 1])

    def get_account_info(self):
        return {"accountName": "x"}


def call(worker, method, **params):
    return worker.handle({"id": 7, "method": method, "params": params})


def result(worker, method, **params):
    reply = call(worker, method, **params)
    assert reply["ok"], reply
    return reply["result"]


def disk_text(cache):
    return "".join(path.read_text() for path in sorted(cache.dir.glob("*.json")))


def items_of(page):
    return [i for s in page.get("sections") or [] for i in s.get("items") or []]


# ---- nowContext: the song playing's own menu ----------------------------------------------


def test_now_context_carries_the_song_playings_library_and_pin_state_with_tokens(load_fixture):
    response = signed_in_watch(load_fixture)
    contents = panel(response)["contents"]
    song = contents[0]["playlistPanelVideoWrapperRenderer"]["primaryRenderer"]["playlistPanelVideoRenderer"]
    song["menu"]["menuRenderer"]["items"] += [bookmark("K"), toggle("KEEP", "PIN_K", "UNPIN_K", on=True)]
    contents[1]["playlistPanelVideoRenderer"]["menu"]["menuRenderer"]["items"].append(bookmark("U", saved=True))
    context = result(signed_in(client(response)), "nowContext", videoId=KARMA)
    assert context["current"] == {"inLibrary": False, "libraryTokens": {"add": "ADD_K", "remove": "REMOVE_K"},
                                  "pinned": True, "listenAgainTokens": pins("K")}
    # Up next rows keep theirs too (norm.track), for the queue rows' menus.
    up = context["items"][0]
    assert up["inLibrary"] is True and up["libraryTokens"] == {"add": "ADD_U", "remove": "REMOVE_U"}
    # Never on disk: nowContext is not cached, and the scrub drops them anyway.
    assert "nowContext" not in ytm.CACHEABLE
    assert caches.strip_tokens(context)["current"] == {"inLibrary": False, "pinned": True}


def test_now_context_current_is_the_twins_own_menu_when_the_video_plays(load_fixture):
    response = signed_in_watch(load_fixture)
    wrapper = panel(response)["contents"][0]["playlistPanelVideoWrapperRenderer"]
    video = wrapper["counterpart"][0]["counterpartRenderer"]["playlistPanelVideoRenderer"]
    video["menu"]["menuRenderer"]["items"].append(toggle("KEEP", "PIN_V", "UNPIN_V"))
    context = result(signed_in(client(response)), "nowContext", videoId=TWIN)
    assert context["current"]["listenAgainTokens"] == pins("V") and context["current"]["pinned"] is False
    assert context["current"]["libraryTokens"] is None, "the video's menu has no library entry here"


def test_now_context_signed_out_has_a_current_without_tokens(load_fixture):
    context = result(ytm.Worker(client(load_fixture("watch_raw.json")), "none"), "nowContext", videoId=KARMA)
    assert context["current"]["libraryTokens"] is None and context["current"]["listenAgainTokens"] is None
    assert set(context["current"]) == set(now.SONG_TOKEN_KEYS)
    assert now.song_tokens(None) is None and now.song_tokens({"title": "no id"}) is None


# ---- Listen again's See all and every other browsePage --------------------------------------


def listen_again(load_fixture):
    """A Listen again See all grid as a signed-in account gets it: every card
    with its pin (the second one pinned), the video with its library entry."""
    album, playlist, artist_card, video = two_rows(load_fixture)
    cards = [with_menu(album, keep("AL")), with_menu(playlist, keep("PL", pinned=True)),
             with_menu(artist_card, keep("AR")), with_menu(video, bookmark("VI"), keep("VI"))]
    grid = {"gridRenderer": {"items": cards, "header": {"gridHeaderRenderer": {"title": {"runs": [{"text": "Listen again"}]}}}}}
    return {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
        {"tabRenderer": {"content": {"sectionListRenderer": {"contents": [grid]}}}}]}}}


def test_listen_again_see_all_items_carry_their_pin_and_the_disk_copy_keeps_only_the_state(load_fixture, tmp_path):
    cache = caches.DiskCache(tmp_path / "data")
    worker = ytm.Worker(raw.install(Served(listen_again(load_fixture))), "ok", cache=cache)
    page = result(worker, "browsePage", browseId=LISTEN_AGAIN, cache="refresh")
    items = items_of(page)
    assert [i["type"] for i in items] == ["album", "playlist", "artist", "track"]
    assert [i["listenAgainTokens"] for i in items] == [pins("AL"), pins("PL"), pins("AR"), pins("VI")]
    assert [i["pinned"] for i in items] == [False, True, False, False]
    # The video card reads its whole song menu (Save to library too).
    assert items[3]["libraryTokens"] == {"add": "ADD_VI", "remove": "REMOVE_VI"} and items[3]["inLibrary"] is False
    assert "feedback" not in items[0], "Not interested is Home's only"
    text = disk_text(cache)
    assert LISTEN_AGAIN in text and '"pinned":true' in text
    for token in ("PIN_", "ADD_VI", "REMOVE_VI"):
        assert token not in text, token
    cached = call(worker, "browsePage", browseId=LISTEN_AGAIN, cache="prefer")
    assert cached["cached"] is True
    assert [i.get("pinned") for i in items_of(cached["result"])] == [False, True, False, False]
    assert all("listenAgainTokens" not in i for i in items_of(cached["result"]))


def test_any_see_all_page_and_its_cursor_pages_read_the_pin(load_fixture):
    album = two_rows(load_fixture)[0]
    first = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": [{"gridRenderer": {"items": [with_menu(album, keep("A1"))] * 40,
                                                               "continuations": [{"nextContinuationData": {"continuation": "T"}}]}}]}}}}]}}}
    more = {"continuationContents": {"gridContinuation": {"items": [with_menu(album, keep("A2", pinned=True))] * 3}}}
    worker = ytm.Worker(raw.install(Served(first, more)), "ok")
    page = result(worker, "browsePage", browseId="MPLAUCr_iyUANcn9OX_yy9piYoLw")
    assert all(i["listenAgainTokens"] == pins("A1") for i in items_of(page))
    later = result(worker, "browsePage", browseId="MPLAUCr_iyUANcn9OX_yy9piYoLw", cursor=page["cursor"])
    assert [i["pinned"] for i in items_of(later)] == [True] * 3


def test_pin_fields_keeps_a_rows_own_tokens_and_skips_items_without_a_pin(load_fixture):
    album = two_rows(load_fixture)[0]
    assert raw.items([album]) == raw.items([with_menu(album)]), "a menu without the pin adds nothing"
    value = {"type": "track", "videoId": "x" * 11, "listenAgainTokens": {"pin": "OWN", "unpin": "OWN_U"}}
    assert raw.pin_fields(with_menu(album, keep("NEW")), value) is value, "a song's own (song menu) tokens stay"
    assert raw.pin_fields({"a": 1, "b": 2}, {"type": "album"}) == {"type": "album"}
    assert raw.pin_fields(album, None) is None
    # Home still reads its own extra (pin plus Not interested), not the default.
    seen = []
    raw.items([with_menu(album, keep("H"))], extra=lambda content, item: seen.append(item) or item)
    assert seen and "listenAgainTokens" not in seen[0]


def test_a_card_whose_menu_has_no_item_list_is_kept_without_tokens(load_fixture):
    video = copy.deepcopy(two_rows(load_fixture)[3])
    video["musicTwoRowItemRenderer"]["menu"] = {"somethingElse": {}}
    [card] = raw.items([video])
    assert card["type"] == "track" and card["libraryTokens"] is None and card["listenAgainTokens"] is None
    assert raw.song_menu({"menu": {"menuRenderer": {"items": [bookmark("X")]}}})["feedbackTokens"] == {
        "add": "ADD_X", "remove": "REMOVE_X"}


def test_artist_shelves_and_related_rows_read_the_pin(load_fixture):
    rows = load_fixture("artist_raw_rows.json")["rows"]
    rows = json.loads(json.dumps(rows))
    for card in renderers(rows, "musicTwoRowItemRenderer"):
        card["musicTwoRowItemRenderer"]["menu"] = {"menuRenderer": {"items": [keep("S")]}}
    parsed = [row for row in artist._parse_rows(rows) if row and row["layout"] == "carousel"]
    cards = [i for row in parsed for i in row["items"]]
    assert cards and all(i["listenAgainTokens"] == pins("S") for i in cards)
    assert {i["type"] for i in cards} >= {"album", "playlist", "artist", "track"}
    related = load_fixture("related.json")
    for card in renderers(related, "musicTwoRowItemRenderer"):
        card["musicTwoRowItemRenderer"]["menu"] = {"menuRenderer": {"items": [keep("R", pinned=True)]}}
    sections = now.related_sections(related)
    shown = [i for s in sections for i in s["items"] if i.get("type") in ("album", "playlist", "artist")]
    assert shown and all(i["pinned"] is True and i["listenAgainTokens"] == pins("R") for i in shown)


def test_explore_cards_read_the_pin(load_fixture):
    import ytm_explore as explore
    response = load_fixture("explore.json")
    for card in renderers(response, "musicTwoRowItemRenderer"):
        card["musicTwoRowItemRenderer"]["menu"] = {"menuRenderer": {"items": [bookmark("E"), keep("E")]}}
    sections = [explore.section(row) for row in raw.rows(response)]
    keys = {"newReleases": [], "newVideos": []}
    for section in sections:
        if section and explore.explore_key(section.get("more")) in keys:
            keys[explore.explore_key(section["more"])] = section["items"]
    cards = keys["newReleases"] + keys["newVideos"]
    assert keys["newReleases"] and keys["newVideos"]
    assert all(i["listenAgainTokens"] == pins("E") for i in cards)
    assert all(i["libraryTokens"] == {"add": "ADD_E", "remove": "REMOVE_E"} for i in keys["newVideos"])


def test_library_lists_read_each_items_pin():
    import ytm_library as library
    row = {"musicResponsiveListItemRenderer": {
        "navigationEndpoint": {"browseEndpoint": {"browseId": "MPLAUCx", "browseEndpointContextSupportedConfigs": {
            "browseEndpointContextMusicConfig": {"pageType": raw.LIBRARY_ARTIST_PAGE}}}},
        "flexColumns": [{"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": "Artist"}]}}}],
        "thumbnail": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": []}}}}}
    [plain] = library._artists([row])
    assert "listenAgainTokens" not in plain
    [pinned] = library._artists([with_menu(row, keep("LA", pinned=True))])
    assert pinned["pinned"] is True and pinned["listenAgainTokens"] == pins("LA")


# ---- podcast and episode pages ------------------------------------------------------------


def header_menu_items(response):
    header = response["contents"]["twoColumnBrowseResultsRenderer"]["tabs"][0]["tabRenderer"]["content"][
        "sectionListRenderer"]["contents"][0]["musicResponsiveHeaderRenderer"]
    button = next(b for b in header["buttons"] if "menuRenderer" in b)
    return button["menuRenderer"]["items"]


def test_a_podcast_page_pins_itself_and_each_episode_row(load_fixture, tmp_path):
    response = load_fixture("podcast.json")
    header_menu_items(response).append(keep("POD", pinned=True))
    for n, row in enumerate(renderers(response, "musicMultiRowListItemRenderer")):
        row["musicMultiRowListItemRenderer"]["menu"] = {"menuRenderer": {"items": [keep(f"EP{n}")]}}
    cache = caches.DiskCache(tmp_path / "data")
    worker = ytm.Worker(raw.install(Served(response)), "ok", cache=cache)
    page = result(worker, "podcast", browseId=PODCAST, cache="refresh")
    assert page["header"]["pin"] == {"pinned": True, "listenAgainTokens": pins("POD")}
    episodes = items_of(page)
    assert episodes and [e["listenAgainTokens"] for e in episodes] == [pins(f"EP{n}") for n in range(len(episodes))]
    assert all(e["pinned"] is False and e["kind"] == "episode" for e in episodes)
    text = disk_text(cache)
    assert "PIN_" not in text and '"pin":{"pinned":true}' in text


def test_a_podcast_header_without_the_pin_has_no_pin_key(load_fixture):
    page = result(ytm.Worker(raw.install(Served(load_fixture("podcast.json"))), "none"), "podcast", browseId=PODCAST)
    assert "pin" not in page["header"], "signed out: no Listen again entry is served"
    assert all(e["listenAgainTokens"] is None for e in items_of(page))


def test_an_episode_page_pins_itself(load_fixture):
    response = load_fixture("episode.json")
    header_menu_items(response).append(keep("EPI"))
    page = result(ytm.Worker(raw.install(Served(response)), "ok"), "episode", videoId=EPISODE)
    assert page["header"]["pin"] == {"pinned": False, "listenAgainTokens": pins("EPI")}
    plain = result(ytm.Worker(raw.install(Served(load_fixture("episode.json"))), "none"), "episode", videoId=EPISODE)
    assert "pin" not in plain["header"]


def test_a_channels_podcast_cards_and_episode_rows_read_the_pin(load_fixture):
    response = load_fixture("channel.json")
    for card in renderers(response, "musicTwoRowItemRenderer"):
        card["musicTwoRowItemRenderer"]["menu"] = {"menuRenderer": {"items": [keep("SHOW")]}}
    for row in renderers(response, "musicMultiRowListItemRenderer"):
        row["musicMultiRowListItemRenderer"]["menu"]["menuRenderer"]["items"].append(keep("ROW"))
    page = result(ytm.Worker(raw.install(Served(response)), "ok"), "channel", channelId="UC3RcjbuyF5M1U4R62zjE3hg")
    shows = [i for i in items_of(page) if i["type"] == "podcast"]
    rows = [i for i in items_of(page) if i.get("kind") == "episode"]
    assert shows and all(s["listenAgainTokens"] == pins("SHOW") for s in shows)
    assert rows and all(r["listenAgainTokens"] == pins("ROW") for r in rows)


# ---- lists ytmusicapi parses without card menus: the pin from the raw cards ------------------


def playlist_card(n, *entries):
    """A two-row playlist card as served (``VL`` browse id), with a menu of ``entries``."""
    from test_library import library_playlist
    return with_menu(library_playlist(n), *entries)


def album_card(name, *entries):
    from test_feedback import card
    return card(name, "MPREb_" + name, "MUSIC_PAGE_TYPE_ALBUM", [share(), *entries])


def test_library_playlists_in_the_default_order_read_each_cards_pin(tmp_path):
    from ytmusicapi.mixins.library import LibraryMixin
    from test_library import NEW_PLAYLIST, LibraryYT, browse

    class Grid(LibraryYT):
        """ytmusicapi's own get_library_playlists over the served grid pages."""

        def _check_auth(self):
            pass

        def get_library_playlists(self, limit=25):
            self.calls.append(("get_library_playlists", limit))
            return LibraryMixin.get_library_playlists(self, limit)

    first = browse("gridRenderer", [NEW_PLAYLIST, playlist_card(1, keep("L1", pinned=True)), playlist_card(2)], "P1")
    last = {"continuationContents": {"gridContinuation": {"items": [playlist_card(3, keep("L3"))]}}}
    cache = caches.DiskCache(tmp_path / "data")
    worker = ytm.Worker(raw.install(Grid(first, last)), "ok", cache=cache)
    got = result(worker, "libraryPlaylists", cache="refresh")["items"]
    assert worker.yt.calls == [("get_library_playlists", None)], "no order: still ytmusicapi's read"
    assert [p["playlistId"] for p in got] == ["PL0001", "PL0002", "PL0003"]
    assert got[0]["pinned"] is True and got[0]["listenAgainTokens"] == pins("L1")
    assert "listenAgainTokens" not in got[1], "a card whose menu has no pin"
    assert got[2]["pinned"] is False and got[2]["listenAgainTokens"] == pins("L3"), "from the continuation"
    text = disk_text(cache)
    assert '"pinned":true' in text and "PIN_" not in text


def test_with_card_pins_matches_by_browse_id_and_keeps_an_items_own_tokens():
    pins_by_id = raw.card_pins([{"a": [playlist_card(1, keep("P"))]}, {"b": album_card("X", keep("X"))}])
    assert set(pins_by_id) == {"VLPL0001", "MPREb_X"}
    own = {"type": "album", "browseId": "MPREb_X", "listenAgainTokens": pins("OWN")}
    other = {"type": "album", "browseId": "MPREb_Y"}
    playlist = {"type": "playlist", "playlistId": "PL0001"}
    got = raw.with_card_pins([own, other, playlist], pins_by_id)
    assert got[0] is own and got[1] is other
    assert got[2] == dict(playlist, pinned=False, listenAgainTokens=pins("P"))
    assert raw.with_card_pins([other], {}) == [other] and raw.card_pins([None, {}]) == {}


def test_album_shelves_read_each_cards_pin():
    from ytmusicapi.parsers.browsing import parse_album
    from test_feedback import ALBUM, Hooked

    versions = [album_card("V1", keep("V1", pinned=True))]
    releases = [album_card("R1", keep("R1")), album_card("R2")]

    def shelf(size, cards):
        return {"musicCarouselShelfRenderer": {"itemSize": size, "contents": cards, "header": {
            "musicCarouselShelfBasicHeaderRenderer": {"title": {"runs": [{"text": size}]}}}}}

    response = {"contents": {"twoColumnBrowseResultsRenderer": {"secondaryContents": {"sectionListRenderer": {
        "contents": [{"musicShelfRenderer": {"contents": []}}, shelf("COLLECTION_STYLE_ITEM_SIZE_MEDIUM", versions),
                     shelf("COLLECTION_STYLE_ITEM_SIZE_SMALL", releases)]}}}}}
    parsed = lambda cards: [parse_album(c["musicTwoRowItemRenderer"]) for c in cards]
    data = dict(ALBUM, other_versions=parsed(versions), related_recommendations=parsed(releases))
    page = result(ytm.Worker(raw.install(Hooked(response, album=data)), "ok"), "album", browseId="MPREb_ok")
    shelves = {s["key"]: s["items"] for s in page["sections"]}
    [version] = shelves["otherVersions"]
    assert version["browseId"] == "MPREb_V1" and version["pinned"] is True and version["listenAgainTokens"] == pins("V1")
    assert [i.get("listenAgainTokens") for i in shelves["releases"]] == [pins("R1"), None]
    assert caches.strip_tokens(page)["sections"][0]["items"][0]["pinned"] is True


def test_playlist_related_cards_read_their_pin():
    from ytmusicapi.parsers.browsing import parse_playlist
    from test_feedback import PLAYLIST

    cards = [playlist_card(7, keep("R7")), playlist_card(8, keep("R8", pinned=True))]
    related = {"continuationContents": {"sectionListContinuation": {"contents": [{"musicCarouselShelfRenderer": {
        "header": {"musicCarouselShelfBasicHeaderRenderer": {"title": {"runs": [{"text": "Related playlists"}]}}},
        "contents": cards}}]}}}

    class Related:
        def __init__(self):
            self.responses = [{}, related]

        def _send_request(self, endpoint, body, additionalParams=""):
            return self.responses.pop(0)

        def get_playlist(self, playlist_id, limit=100, related=False):
            self._send_request("browse", {})
            self._send_request("browse", {})
            return dict(PLAYLIST, related=[parse_playlist(c["musicTwoRowItemRenderer"]) for c in cards])

        def get_account_info(self):
            return {"accountName": "x"}

    page = result(ytm.Worker(raw.install(Related()), "ok"), "playlist", playlistId="PLx", related=True)
    [shelf] = page["sections"]
    assert shelf["key"] == "related" and shelf["title"] == "Related playlists"
    assert [(p["playlistId"], p["pinned"], p["listenAgainTokens"]) for p in shelf["items"]] == [
        ("PL0007", False, pins("R7")), ("PL0008", True, pins("R8"))]
