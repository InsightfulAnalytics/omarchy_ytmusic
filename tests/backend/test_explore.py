"""ytm_explore (S6): the Explore page, Charts, Moods & genres and a mood's page.

The fixtures are anonymous raw responses (``bin/ytm record explore``,
``charts {"country": "ZZ"}``, ``moods``, ``moodRaw`` with Focus's params;
item menus dropped, item rows cut to a few). ytmusicapi's own get_explore,
get_charts, get_mood_categories and get_mood_playlists run over them through
a stand-in client, so the worker reads exactly what it reads live.
"""

import copy

import pytest
from ytmusicapi import YTMusic

import ytm
import ytm_cache
import ytm_explore as explore
import ytm_raw as raw
from conftest import FIXTURES


class Served:
    """A client that answers every request with ``responses`` (one per
    request, the last one repeated), recording what was sent."""

    get_explore = YTMusic.get_explore
    get_charts = YTMusic.get_charts
    get_mood_categories = YTMusic.get_mood_categories
    get_mood_playlists = YTMusic.get_mood_playlists

    def __init__(self, *responses):
        self.responses = list(responses)
        self.sent = []

    def _send_request(self, endpoint, body, additionalParams=""):
        self.sent.append((endpoint, body))
        index = min(len(self.sent), len(self.responses)) - 1
        return copy.deepcopy(self.responses[index])


def call(worker, method, **params):
    return worker.handle({"id": 7, "method": method, "params": params})


def served(*responses):
    client = raw.install(Served(*responses))
    return ytm.Worker(client, "none"), client


def with_codes(response):
    """The recorded charts response with its country codes back where YouTube
    serves them (the recorder keeps them as countryCode; see record_charts)."""
    def walk(value):
        if isinstance(value, dict):
            if "countryCode" in value:
                value["opaqueToken"] = value["countryCode"]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(response)
    return response


@pytest.fixture
def explore_raw(load_fixture):
    return load_fixture("explore.json")


@pytest.fixture
def charts_raw(load_fixture):
    return with_codes(load_fixture("charts.json"))


def by_key(page):
    return {s["key"]: s for s in page["sections"]}


def rows_without(response, browse_ids):
    """The response without the shelves whose See all is one of ``browse_ids``."""
    out = copy.deepcopy(response)
    found = raw.section_list(out)
    found["contents"] = [row for row in found["contents"]
                         if (raw.parse_row(row)["more"] or {}).get("browseId") not in browse_ids]
    return out


# ---- explore -----------------------------------------------------------------------------------


def test_explore_sections_in_served_order_with_their_see_all(explore_raw):
    worker, client = served(explore_raw)
    reply = call(worker, "explore")
    page = reply["result"]
    assert client.sent == [("browse", {"browseId": "FEmusic_explore"})], "one request"
    assert (page["type"], page["kind"], page["id"], page["title"]) == ("page", "explore", "FEmusic_explore", "Explore")
    got = [(s["key"], s["title"]) for s in page["sections"]]
    assert got == [("entries", None), ("newReleases", "New albums & singles"), ("moods", "Moods & genres"),
                   ("trending", "Trending"), ("newVideos", "New music videos")]
    sections = by_key(page)
    assert sections["newReleases"]["more"]["browseId"] == "FEmusic_new_releases_albums"
    assert sections["newVideos"]["more"]["browseId"] == "FEmusic_new_releases_videos"
    assert sections["moods"]["more"]["browseId"] == "FEmusic_moods_and_genres"
    # The backing playlist, as served: the shell's route strips the VL.
    assert sections["trending"]["more"]["browseId"].startswith("VLOLA")
    assert {i["type"] for i in sections["newReleases"]["items"]} == {"album"}
    assert all(i["type"] == "track" for i in sections["trending"]["items"] + sections["newVideos"]["items"])


def test_explore_entries_are_the_pages_navigation_buttons(explore_raw):
    page = call(served(explore_raw)[0], "explore")["result"]
    entries = page["sections"][0]
    assert entries["key"] == "entries" and entries["title"] is None
    assert [(e["type"], e["title"], e["endpoint"]["browseId"]) for e in entries["items"]] == [
        ("more", "New releases", "FEmusic_new_releases"), ("more", "Charts", "FEmusic_charts"),
        ("more", "Moods & genres", "FEmusic_moods_and_genres")]


def test_explore_keeps_every_mood_category(explore_raw):
    # The shelf's See all page lacks some of them: the shell shows a few
    # and the rest on request (Model.MOOD_PREVIEW).
    moods = by_key(call(served(explore_raw)[0], "explore")["result"])["moods"]
    assert len(moods["items"]) == 35, "every category of the (signed-out) shelf"
    first = moods["items"][0]
    assert first["type"] == "mood" and first["title"] and first["params"] and first["id"] == first["params"]
    assert len({m["id"] for m in moods["items"]}) == 35


def test_explore_ranks_come_from_the_chart_rows(explore_raw):
    trending = by_key(call(served(explore_raw)[0], "explore")["result"])["trending"]["items"]
    assert [t["rank"] for t in trending] == [str(n) for n in range(1, len(trending) + 1)]


def test_top_songs_carry_rank_and_trend(explore_raw):
    # Top songs is served to Premium accounts only: Trending's row, as the
    # Top songs chart (a VLPL See all), with a rising song.
    response = copy.deepcopy(explore_raw)
    found = raw.section_list(response)
    trending = next(row for row in found["contents"] if explore.explore_key(raw.parse_row(row)["more"]) == "trending")
    header = next(iter(trending["musicCarouselShelfRenderer"]["header"].values()))
    header["title"] = {"runs": [{"text": "Top songs", "navigationEndpoint": {
        "browseEndpoint": {"browseId": "VLPL4fGSI1pDJn7xvYy-bP6UFeG5tITQgScd"}}}]}
    header.pop("moreContentButton", None)
    first = trending["musicCarouselShelfRenderer"]["contents"][0]["musicResponsiveListItemRenderer"]
    first["customIndexColumn"] = {"musicCustomIndexColumnRenderer": {
        "text": {"runs": [{"text": "1"}]}, "icon": {"iconType": "ARROW_DROP_UP"}}}
    top = by_key(call(served(response)[0], "explore")["result"])["topSongs"]
    assert top["title"] == "Top songs" and top["more"]["browseId"] == "VLPL4fGSI1pDJn7xvYy-bP6UFeG5tITQgScd"
    assert (top["items"][0]["rank"], top["items"][0]["trend"]) == ("1", "up")
    assert top["items"][0]["type"] == "track" and top["items"][0]["artistText"]


def test_song_credits_leave_out_the_view_count(explore_raw):
    # Trending's rows (and Top songs' videos) end their credits with a view
    # count: ytmusicapi reads it as views (parse_trending_item), not as an artist.
    trending = by_key(call(served(explore_raw)[0], "explore")["result"])["trending"]["items"]
    assert trending and not any("views" in t["artistText"] for t in trending)
    assert all(a["name"] and "views" not in a["name"] for t in trending for a in t["artists"])
    assert trending[0]["artistText"] == "Ed Sheeran, Kumaar, Arijit Singh, Madhubanti Bagchi"
    assert trending[0]["artists"][1] == {"name": "Kumaar", "id": "UCrLyx_mZBvXD1Xt6UG7kYBg"}
    expected = Served(explore_raw).get_explore()["trending"]["items"]
    assert [t["artists"] for t in trending] == [e["artists"] for e in expected]


def test_missing_shelves_are_tolerated(explore_raw):
    response = rows_without(explore_raw, {"FEmusic_moods_and_genres", "FEmusic_new_releases_albums"})
    page = call(served(response)[0], "explore")["result"]
    assert [s["key"] for s in page["sections"]] == ["entries", "trending", "newVideos"]
    # No navigation buttons either: no entry rows.
    found = raw.section_list(response)
    found["contents"] = found["contents"][1:]
    page = call(served(response)[0], "explore")["result"]
    assert [s["key"] for s in page["sections"]] == ["trending", "newVideos"]


def test_an_unknown_shelf_is_kept_as_a_generic_section(explore_raw):
    response = copy.deepcopy(explore_raw)
    found = raw.section_list(response)
    extra = copy.deepcopy(found["contents"][-1])
    header = next(iter(extra["musicCarouselShelfRenderer"]["header"].values()))
    header["title"] = {"runs": [{"text": "Something new"}]}
    header.pop("moreContentButton", None)
    found["contents"].append(extra)
    sections = call(served(response)[0], "explore")["result"]["sections"]
    assert (sections[-1]["key"], sections[-1]["title"], sections[-1]["more"]) == (None, "Something new", None)
    assert sections[-1]["items"]


def test_explore_from_the_parse_alone_tolerates_missing_keys():
    class Parsed:
        def get_explore(self):
            return {"new_releases": [{"title": "A", "browseId": "MPREb_a", "artists": [], "type": "Single"}],
                    "trending": {"playlist": "VLOLAK5uy_t", "items": [{"title": "T", "videoId": "v1", "artists": []}]},
                    "new_videos": [{"title": "V", "videoId": "v2", "artists": []}]}

    page = call(ytm.Worker(Parsed(), "none"), "explore")["result"]
    got = [(s["key"], s["title"], (s["more"] or {}).get("browseId"), len(s["items"])) for s in page["sections"]]
    assert got == [("entries", None, None, 3), ("newReleases", "New albums & singles", "FEmusic_new_releases_albums", 1),
                   ("trending", "Trending", "VLOLAK5uy_t", 1), ("newVideos", "New music videos", "FEmusic_new_releases_videos", 1)]
    assert by_key(page)["newVideos"]["items"][0]["kind"] == "video"
    empty = call(ytm.Worker(type("Empty", (), {"get_explore": lambda self: {}})(), "none"), "explore")["result"]
    assert [s["key"] for s in empty["sections"]] == ["entries"]


def test_explore_reads_the_rows_when_ytmusicapi_cannot(explore_raw):
    class Broken(Served):
        def get_explore(self):
            self._send_request("browse", {"browseId": "FEmusic_explore"})
            raise KeyError("musicCarouselShelfRenderer")

    worker = ytm.Worker(raw.install(Broken(explore_raw)), "none")
    reply = call(worker, "explore")
    assert reply["ok"] and "newReleases" in by_key(reply["result"])


def episode_row(title, browse_id, video_id, podcast=None):
    """A Popular episodes item as the chart serves it (musicMultiRowListItemRenderer);
    ``podcast`` ``(name, browseId)`` is its second title."""
    data = {
        "title": {"runs": [{"text": title, "navigationEndpoint": {"browseEndpoint": {
            "browseId": browse_id, "browseEndpointContextSupportedConfigs": {"browseEndpointContextMusicConfig": {
                "pageType": "MUSIC_PAGE_TYPE_NON_MUSIC_AUDIO_TRACK_PAGE"}}}}}]},
        "subtitle": {"runs": [{"text": "3 days ago"}]},
        "onTap": {"watchEndpoint": {"videoId": video_id}},
        "thumbnail": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
            {"url": "https://i.ytimg.com/vi/x/hq.jpg", "width": 120, "height": 120}]}}},
    }
    if podcast:
        data["secondTitle"] = {"runs": [{"text": podcast[0], "navigationEndpoint": {"browseEndpoint": {"browseId": podcast[1]}}}]}
    return {"musicMultiRowListItemRenderer": data}


def test_popular_episodes_name_their_podcast(explore_raw):
    response = copy.deepcopy(explore_raw)
    found = raw.section_list(response)
    shelf = {"musicCarouselShelfRenderer": {
        "header": {"musicCarouselShelfBasicHeaderRenderer": {"title": {"runs": [{
            "text": "Popular episodes",
            "navigationEndpoint": {"browseEndpoint": {"browseId": "FEmusic_top_non_music_audio_episodes"}}}]}}},
        "contents": [episode_row("Episode one", "MPEDep1", "epvideo0001", ("The Show", "MPSPPLshow")),
                     # No second title: read as any episode row, without its podcast.
                     episode_row("Episode two", "MPEDep2", "epvideo0002")]}}
    found["contents"].append(shelf)
    episodes = by_key(call(served(response)[0], "explore")["result"])["episodes"]
    assert episodes["title"] == "Popular episodes"
    assert episodes["more"]["browseId"] == "FEmusic_top_non_music_audio_episodes"
    first, second = episodes["items"]
    assert (first["type"], first["kind"], first["title"], first["videoId"], first["browseId"]) == (
        "track", "episode", "Episode one", "epvideo0001", "MPEDep1")
    assert first["podcast"] == {"name": "The Show", "id": "MPSPPLshow"}
    assert first["artistText"] == "The Show", "the podcast names the episode, as an artist would"
    assert (second["kind"], second["title"], second["videoId"], second["browseId"]) == (
        "episode", "Episode two", "epvideo0002", "MPEDep2")
    assert second["podcast"] is None


def test_popular_episodes_only_when_served(explore_raw):
    assert "episodes" not in by_key(call(served(explore_raw)[0], "explore")["result"])
    assert explore.explore_key({"browseId": "FEmusic_top_non_music_audio_episodes"}) == "episodes"
    assert explore.explore_key({"browseId": "VLPLx"}) == "topSongs" and explore.explore_key({"browseId": "VLOLAKx"}) == "trending"
    assert explore.explore_key({"browseId": "VLRDCLAKx"}) is None and explore.explore_key(None) is None


# ---- charts ------------------------------------------------------------------------------------


def test_charts_global_sections_ranks_and_countries(charts_raw):
    worker, client = served(charts_raw)
    page = call(worker, "charts")["result"]
    assert client.sent == [("browse", {"browseId": "FEmusic_charts", "formData": {"selectedValues": ["ZZ"]}})]
    assert (page["kind"], page["id"], page["title"], page["subtitle"]) == ("charts", "ZZ", "Charts", "Global")
    assert page["country"] == {"code": "ZZ", "label": "Global"}
    assert [(s["title"], s["key"]) for s in page["sections"]] == [("Video charts", "playlists"), ("Top artists", "artists")]
    artists = page["sections"][1]["items"]
    assert [a["rank"] for a in artists] == [str(n) for n in range(1, len(artists) + 1)]
    assert all(a["trend"] in ("up", "down", "neutral") for a in artists)
    assert all(a["type"] == "artist" and a["channelId"].startswith("UC") for a in artists)
    assert all(p["type"] == "playlist" and not p["playlistId"].startswith("VL") for p in page["sections"][0]["items"])


def test_charts_country_list_has_every_option_once_in_served_order(charts_raw):
    page = call(served(charts_raw)[0], "charts")["result"]
    codes = [c["code"] for c in page["countries"]]
    assert len(codes) == len(set(codes)) == 69
    assert codes[:3] == ["AU", "ZZ", "AR"], "the suggested countries first, as served"
    labels = {c["code"]: c["label"] for c in page["countries"]}
    assert (labels["ZZ"], labels["US"], labels["DE"]) == ("Global", "United States", "Germany")


@pytest.mark.parametrize("given, sent", [("au", "AU"), ("US", "US"), ("", "ZZ"), (None, "ZZ")])
def test_charts_country_param(charts_raw, given, sent):
    worker, client = served(charts_raw)
    reply = call(worker, "charts", **({} if given is None else {"country": given}))
    assert reply["ok"] and client.sent[0][1]["formData"] == {"selectedValues": [sent]}


@pytest.mark.parametrize("bad", ["USA", "U", "1A", 12, True, ["US"], "Z Z"])
def test_charts_bad_country_is_bad_request(charts_raw, bad):
    worker, client = served(charts_raw)
    reply = call(worker, "charts", country=bad)
    assert reply["error"]["code"] == "BAD_REQUEST" and client.sent == []


def test_charts_report_the_country_youtube_served(charts_raw):
    # Asked for AU, served Global (a country the menu does not list): the page says Global.
    page = call(served(charts_raw)[0], "charts", country="XX")["result"]
    assert page["country"] == {"code": "ZZ", "label": "Global"} and page["id"] == "ZZ"


def test_charts_from_the_parse_alone():
    class Parsed:
        def get_charts(self, country="ZZ"):
            return {"countries": {"selected": {"text": "United States"}, "options": ["US", "ZZ", "US"]},
                    "daily": [{"title": "Daily", "playlistId": "PLd", "thumbnails": []}],
                    "genres": [{"title": "Top 50 Jazz", "playlistId": "PLg", "thumbnails": []}],
                    "artists": [{"title": "A", "browseId": "UCa", "subscribers": "1M", "thumbnails": [],
                                 "rank": "1", "trend": "down"}]}

    page = call(ytm.Worker(Parsed(), "none"), "charts", country="US")["result"]
    assert page["country"] == {"code": "US", "label": "United States"}
    assert page["countries"] == [{"code": "US", "label": "US"}, {"code": "ZZ", "label": "ZZ"}]
    assert [(s["title"], s["key"]) for s in page["sections"]] == [
        ("Daily charts", "playlists"), ("Genres", "playlists"), ("Top artists", "artists")]
    assert page["sections"][2]["items"][0]["rank"] == "1" and page["sections"][2]["items"][0]["trend"] == "down"


def test_chart_key_by_item_type():
    assert explore.chart_key([{"type": "podcast"}, {"type": "podcast"}]) == "podcasts"
    assert explore.chart_key([{"type": "playlist"}, {"type": "artist"}]) is None


# ---- moods -------------------------------------------------------------------------------------


def test_moods_sections_as_served(load_fixture):
    worker, client = served(load_fixture("moods.json"))
    page = call(worker, "moods")["result"]
    assert client.sent == [("browse", {"browseId": "FEmusic_moods_and_genres"})]
    assert (page["kind"], page["id"], page["title"]) == ("moods", "FEmusic_moods_and_genres", "Moods & genres")
    # Signed out there is no For you group.
    assert [(s["title"], len(s["items"]), s["layout"]) for s in page["sections"]] == [
        ("Moods & moments", 11, "grid"), ("Genres", 24, "grid")]
    focus = next(m for m in page["sections"][0]["items"] if m["title"] == "Focus")
    assert focus == {"type": "mood", "id": focus["params"], "title": "Focus", "params": focus["params"]}


def test_moods_skip_categories_without_params():
    class Parsed:
        def get_mood_categories(self):
            return {"For you": [{"title": "Focus", "params": "p1"}, {"title": "Broken", "params": None}], "Empty": []}

    page = call(ytm.Worker(Parsed(), "none"), "moods")["result"]
    assert [(s["title"], [m["title"] for m in s["items"]]) for s in page["sections"]] == [("For you", ["Focus"])]


def test_mood_page_keeps_titled_shelves(load_fixture):
    worker, client = served(load_fixture("mood_raw.json"))
    page = call(worker, "mood", params="ggMPOg1uX0NvNGNhWThMYWRh", title="Focus")["result"]
    assert client.sent == [("browse", {"browseId": "FEmusic_moods_and_genres_category", "params": "ggMPOg1uX0NvNGNhWThMYWRh"})]
    assert (page["kind"], page["id"], page["title"]) == ("mood", "ggMPOg1uX0NvNGNhWThMYWRh", "Focus")
    titles = [s["title"] for s in page["sections"]]
    assert titles[:3] == ["Flow state", "Classical focus", "Quiet songs"] and len(titles) == 8
    for s in page["sections"]:
        assert s["items"] and all(i["type"] == "playlist" for i in s["items"])
        # A shelf's See all is a narrower category: it opens as a mood page.
        assert s["more"]["browseId"] == "FEmusic_moods_and_genres_category" and s["more"]["params"]


def test_mood_falls_back_to_the_flat_list():
    # Playlists without a page type: the rows read no items, while
    # get_mood_playlists' parse (run on the same response) finds them.
    def playlist(title, browse_id=None):
        data = {"title": {"runs": [{"text": title}]}, "subtitle": {"runs": [{"text": "x"}]}}
        if browse_id:
            data["title"]["runs"][0]["navigationEndpoint"] = {"browseEndpoint": {"browseId": browse_id}}
        return {"musicTwoRowItemRenderer": data}

    flat = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": [
            {"gridRenderer": {"items": [playlist("Deep Focus", "VLRDCLAK5uy_f"), playlist("No id")]}},
            {"musicCarouselShelfRenderer": {"contents": [playlist("Lofi", "VLRDCLAK5uy_l")]}}]}}}}]}}}
    client = raw.install(Served(flat))
    page = call(ytm.Worker(client, "none"), "mood", params="pF", title="Focus")["result"]
    assert client.sent == [("browse", {"browseId": "FEmusic_moods_and_genres_category", "params": "pF"})], \
        "one request: the fallback reads the response at hand"
    assert page["title"] == "Focus", "the title asked for when the page names none"
    assert [(s["title"], [i["playlistId"] for i in s["items"]]) for s in page["sections"]] == [
        (None, ["RDCLAK5uy_f", "RDCLAK5uy_l"])]
    assert matches_get_mood_playlists(flat)


def matches_get_mood_playlists(response):
    """mood_playlists gives what get_mood_playlists gives for the same page
    (without its unreadable items)."""
    readable = copy.deepcopy(response)
    grid = raw.section_list(readable)["contents"][0]["gridRenderer"]
    grid["items"] = grid["items"][:1]
    return explore.mood_playlists(readable) == Served(readable).get_mood_playlists("pF")


def test_mood_with_nothing_to_read_is_empty():
    empty = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": []}}}}]}}}
    client = raw.install(Served(empty))
    page = call(ytm.Worker(client, "none"), "mood", params="pF")["result"]
    assert len(client.sent) == 1 and page["sections"] == [] and page["title"] == ""


def test_mood_requires_params():
    worker = ytm.Worker(Served({}), "none")
    assert call(worker, "mood")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "mood", params="")["error"]["code"] == "BAD_REQUEST"
    assert worker.yt.sent == []


# ---- registry, cache and fixtures -----------------------------------------------------------------


def test_registry():
    for name in ("explore", "charts", "moods", "mood"):
        assert ytm.METHODS[name] is explore.METHODS[name]
        assert name in ytm.CACHEABLE and ytm.LANES[name] == "pages" and name not in ytm.MUTATES
    assert {"explore", "charts", "moods", "moodRaw"} <= set(ytm.RECORDERS)


def test_charts_are_cached_per_country(charts_raw, tmp_path):
    cache = ytm_cache.DiskCache(tmp_path / "cache")
    client = raw.install(Served(charts_raw))
    worker = ytm.Worker(client, "none", cache=cache)
    first = call(worker, "charts", country="ZZ", cache="prefer")
    again = call(worker, "charts", country="ZZ", cache="prefer")
    other = call(worker, "charts", country="AU", cache="prefer")
    assert (first["cached"], again["cached"], other["cached"]) == (False, True, False)
    assert len(client.sent) == 2


def test_recorders_keep_the_country_codes_and_trim():
    response = {"frameworkUpdates": {"entityBatchUpdate": {"mutations": [
        {"entityKey": "k", "payload": {"musicFormBooleanChoice": {"opaqueToken": "AU", "selected": False}}}]}},
        "contents": [{"musicCarouselShelfRenderer": {"contents": [
            {"musicTwoRowItemRenderer": {"title": n, "menu": {"x": 1}}} for n in range(5)]}}]}
    kept = explore._codes_kept(explore._trimmed(response, 2))
    assert ytm.scrub(kept)["frameworkUpdates"]["entityBatchUpdate"]["mutations"][0]["payload"] == {
        "musicFormBooleanChoice": {"selected": False, "countryCode": "AU"}}
    assert kept["contents"][0]["musicCarouselShelfRenderer"]["contents"] == [
        {"musicTwoRowItemRenderer": {"title": 0}}, {"musicTwoRowItemRenderer": {"title": 1}}]


@pytest.mark.parametrize("name", ["explore.json", "charts.json", "moods.json", "mood_raw.json"])
def test_fixtures_hold_no_tokens(name):
    text = (FIXTURES / name).read_text().lower()
    for word in ("token", "continuation", "feedback", "cookie", "sapisid"):
        assert word not in text, word
