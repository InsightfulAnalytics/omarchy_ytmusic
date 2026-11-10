"""Search (S7): every result type, scopes, cursor paging, spelling, suggestions.

Fixtures are anonymous raw responses (`bin/ytm record searchRaw ...`); the
private shapes (library and uploads scopes, past searches) are hand-built.
"""

import copy

import pytest
from ytmusicapi.continuations import get_continuation_string
from ytmusicapi.parsers.search import get_search_params, parse_search_suggestions

import ytm
import ytm_raw as raw
import ytm_search as search

SUGGEST = search.SUGGEST


class FakeClient:
    """Answers ``_send_request`` with canned responses in order, recording each request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.calls = []

    def _send_request(self, endpoint, body, additionalParams=""):
        self.requests.append((endpoint, body, additionalParams))
        return self.responses.pop(0) if self.responses else {}

    def get_account_info(self):
        return {}

    def get_search_suggestions(self, query, detailed_runs=False):
        self.calls.append(("get_search_suggestions", query, detailed_runs))
        return parse_search_suggestions(self._send_request(SUGGEST, {"input": query}), detailed_runs)

    def remove_search_suggestions(self, suggestions, indices=None):
        self.calls.append(("remove_search_suggestions", suggestions, indices))
        return True


def worker(*responses, auth="none"):
    client = raw.install(FakeClient(*responses))
    return ytm.Worker(client, auth), client


def call(w, method, **params):
    return w.handle({"id": 1, "method": method, "params": params})


# ---- hand-built raw shapes ------------------------------------------------------------------


def run(text, browse_id=None, page_type=None, watch=None):
    out = {"text": text}
    if browse_id:
        out["navigationEndpoint"] = {"browseEndpoint": {"browseId": browse_id, "browseEndpointContextSupportedConfigs": {
            "browseEndpointContextMusicConfig": {"pageType": page_type}}}}
    if watch:
        out["navigationEndpoint"] = {"watchEndpoint": watch}
    return out


DOT = {"text": " • "}
THUMBS = {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/x.jpg", "width": 120,
                                                                    "height": 120}]}}}


def column(*runs):
    return {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": list(runs)}}}


def song_row(video_id, title, artist="Radiohead"):
    return {"musicResponsiveListItemRenderer": {
        "thumbnail": THUMBS,
        "overlay": {"musicItemThumbnailOverlayRenderer": {"content": {"musicPlayButtonRenderer": {
            "playNavigationEndpoint": {"watchEndpoint": {"videoId": video_id, "watchEndpointMusicSupportedConfigs": {
                "watchEndpointMusicConfig": {"musicVideoType": "MUSIC_VIDEO_TYPE_ATV"}}}}}}}},
        "flexColumns": [column(run(title, watch={"videoId": video_id})),
                        column(run(artist, "UCr_iyUANcn9OX_yy9piYoLw", raw.ARTIST_PAGE), DOT, run("4:24"))]}}


def shelf(rows, token=None):
    data = {"contents": rows}
    if token:
        data["continuations"] = [{"nextContinuationData": {"continuation": token}}]
    return {"musicShelfRenderer": data}


def response(*section_rows, tabs=None):
    sections = {"sectionListRenderer": {"contents": list(section_rows)}}
    if tabs is None:
        return {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": sections}}]}}}
    out = [{"tabRenderer": {"content": {"sectionListRenderer": {"contents": []}}}} for _ in range(3)]
    out[tabs] = {"tabRenderer": {"selected": True, "content": sections}}
    return {"contents": {"tabbedSearchResultsRenderer": {"tabs": out}}}


def continuation(rows, token=None):
    data = {"contents": rows}
    if token:
        data["continuations"] = [{"nextContinuationData": {"continuation": token}}]
    return {"continuationContents": {"musicShelfContinuation": data}}


def artist_card(contents=()):
    mixes = [{"menuNavigationItemRenderer": {"icon": {"iconType": icon}, "navigationEndpoint": {
        "watchPlaylistEndpoint": {"playlistId": pid}}}} for icon, pid in (("MUSIC_SHUFFLE", "RDAOk8"), ("MIX", "RDEMk8"))]
    return {"musicCardShelfRenderer": {
        "title": {"runs": [run("Radiohead", "UCr_iyUANcn9OX_yy9piYoLw", raw.ARTIST_PAGE)]},
        # A localized label: the card is typed by its endpoint, not by this.
        "subtitle": {"runs": [run("Kunstler"), DOT, run("205M monthly audience")]},
        "onTap": {"browseEndpoint": {"browseId": "UCr_iyUANcn9OX_yy9piYoLw", "browseEndpointContextSupportedConfigs": {
            "browseEndpointContextMusicConfig": {"pageType": raw.ARTIST_PAGE}}}},
        "thumbnail": THUMBS, "menu": {"menuRenderer": {"items": mixes}}, "contents": list(contents)}}


# ---- grouped results ------------------------------------------------------------------------


def test_grouped_has_every_kind_in_served_order(load_fixture):
    result = search.grouped(load_fixture("search_raw.json"), "song exploder")
    for key in ("podcasts", "episodes", "profiles", "songs", "albums", "playlists", "artists", "videos"):
        assert result[key], key
        assert key in result["order"]
    assert sorted(result["order"]) == sorted(set(result["order"])), "each group once"
    assert all(p["type"] == "podcast" and p["browseId"].startswith("MPSP") and p["podcastId"] == p["browseId"][4:]
               for p in result["podcasts"])
    assert all(p["authorText"] for p in result["podcasts"]), "the podcast's linked author"
    assert all(p["subtitle"] == "Podcast \u2022 " + p["authorText"] for p in result["podcasts"])
    assert all(e["type"] == "track" and e["kind"] == "episode" and e["podcast"] and e["videoId"]
               for e in result["episodes"])
    assert all(e["browseId"] == "MPED" + e["videoId"] for e in result["episodes"]), "the title links the episode page"
    assert all(p["type"] == "profile" and p["channelId"].startswith("UC") and p["handle"].startswith("@")
               and p["name"] and not p["name"].startswith("@") for p in result["profiles"])
    assert all(a["type"] == "artist" for a in result["artists"]), "user channels are profiles, not artists"
    assert all(t["kind"] == "song" for t in result["songs"]) and all(t["kind"] == "video" for t in result["videos"])
    assert result["spelling"] is None


def test_top_result_only_from_the_card(load_fixture):
    fixture = load_fixture("search_raw.json")
    assert search.grouped(fixture, "song exploder")["top"], "the fixture has a card"
    without = copy.deepcopy(fixture)
    sections = search.section_list(without)["contents"]
    sections[:] = [row for row in sections if "musicCardShelfRenderer" not in row]
    result = search.grouped(without, "song exploder")
    assert result["top"] == []
    assert result["songs"] and result["order"], "the rest is still there"


def test_a_podcast_leads_in_a_shelf_of_its_own(load_fixture):
    # 'joe rogan experience' (anonymous): no card; the podcast comes first in
    # an untitled musicShelfRenderer, every other result in an itemSectionRenderer.
    fixture = load_fixture("search_podcast_top.json")
    assert search.lead_shelf(search.rows(fixture)) == 0
    result = search.grouped(fixture, "joe rogan experience")
    assert [(t["type"], t["browseId"][:4], bool(t["authorText"])) for t in result["top"]] == [("podcast", "MPSP", True)]
    assert result["top"][0]["browseId"] not in [p["browseId"] for p in result["podcasts"]], "not a podcast row too"
    assert result["podcasts"] and result["episodes"] and "podcasts" in result["order"]


def podcast_row(browse_id, title):
    return {"musicResponsiveListItemRenderer": {
        "thumbnail": THUMBS, "navigationEndpoint": {"browseEndpoint": {
            "browseId": browse_id, "browseEndpointContextSupportedConfigs": {
                "browseEndpointContextMusicConfig": {"pageType": raw.PODCAST_PAGE}}}},
        "flexColumns": [column(run(title)), column(run("Podcast"), DOT, run("Host", "UChost", raw.USER_CHANNEL_PAGE))]}}


def test_lead_shelf_is_read_by_renderer():
    def lead(*rows_, title=None):
        data = {"contents": list(rows_), "shelfDivider": {"musicShelfDividerRenderer": {"hidden": True}}}
        if title:
            data["title"] = {"runs": [{"text": title}]}
        return {"musicShelfRenderer": data}

    def item(row):
        return {"itemSectionRenderer": {"contents": [row]}}

    did = {"itemSectionRenderer": {"contents": [{"didYouMeanRenderer": {
        "correctedQueryEndpoint": {"searchEndpoint": {"query": "the daily"}}}}]}}
    result = search.grouped(response(did, lead(podcast_row("MPSPa", "A")), item(song_row("s", "S")),
                                     item(podcast_row("MPSPb", "B"))), "the dialy")
    assert [t["browseId"] for t in result["top"]] == ["MPSPa"], "a spelling section before it is passed over"
    assert [p["browseId"] for p in result["podcasts"]] == ["MPSPb"] and result["order"] == ["songs", "podcasts"]
    a, s = podcast_row("MPSPa", "A"), song_row("s", "S")
    for case in (response(lead(a, title="Podcasts"), item(s)),  # a titled shelf
                 response(artist_card(), lead(a), item(s)),  # the card is the top result
                 response(item(s), lead(a), item(s)),  # a result came first
                 response(lead(a), shelf([s])),  # shelves all the way down (as a library search)
                 response(lead(a))):
        assert search.lead_shelf(search.rows(case)) is None
        assert "MPSPa" not in [t.get("browseId") for t in search.grouped(case, "q")["top"]]


def test_artist_card_keeps_its_mixes_and_its_songs():
    card = artist_card([song_row("a1", "Let Down", artist=""), song_row("a2", "Creep")])
    result = search.grouped(response(card, {"itemSectionRenderer": {"contents": [song_row("b1", "Airbag")]}}), "x")
    artist, *songs = result["top"]
    assert (artist["type"], artist["name"], artist["channelId"]) == ("artist", "Radiohead", "UCr_iyUANcn9OX_yy9piYoLw")
    assert (artist["shuffleId"], artist["radioId"]) == ("RDAOk8", "RDEMk8"), "the menu's Shuffle and Radio"
    assert artist["subtitle"] == "205M monthly audience"
    assert [s["videoId"] for s in songs] == ["a1", "a2"], "the card's rows stay in the top section"
    assert [s["videoId"] for s in result["songs"]] == ["b1"]


def test_card_is_typed_by_its_endpoint():
    video = {"musicCardShelfRenderer": {
        "title": {"runs": [{"text": "Radiohed Creep", "navigationEndpoint": {"watchEndpoint": {
            "videoId": "NCfjlW-NzLg", "watchEndpointMusicSupportedConfigs": {
                "watchEndpointMusicConfig": {"musicVideoType": "MUSIC_VIDEO_TYPE_UGC"}}}}}]},
        # No type label at all: ytmusicapi would call this an album.
        "subtitle": {"runs": [run("Tarantinos", "UCWT9q", raw.USER_CHANNEL_PAGE), DOT, run("250 views"), DOT,
                              run("3:35")]},
        "thumbnail": THUMBS}}
    top = search.grouped(response(video), "radiohed")["top"]
    assert [(t["type"], t["kind"], t["videoId"], t["durationSec"], t["artistText"]) for t in top] == [
        ("track", "video", "NCfjlW-NzLg", 215, "Tarantinos")]
    podcast = {"musicCardShelfRenderer": {
        "title": {"runs": [run("Song Exploder", "MPSPPLx", raw.PODCAST_PAGE)]},
        "subtitle": {"runs": [run("Podcast"), DOT, run("Hrishikesh Hirway", "UChh", raw.USER_CHANNEL_PAGE)]},
        "thumbnail": THUMBS}}
    top = search.grouped(response(podcast), "song exploder")["top"]
    assert [(t["type"], t["browseId"], t["podcastId"], t["authorText"]) for t in top] == [
        ("podcast", "MPSPPLx", "PLx", "Hrishikesh Hirway")]


def test_station_maps_to_a_mix():
    row = {"musicResponsiveListItemRenderer": {
        "thumbnail": THUMBS, "navigationEndpoint": {"watchEndpoint": {"videoId": "v1", "playlistId": "RDEMk8"}},
        "flexColumns": [column(run("Radiohead Radio")), column(run("Station"), DOT, run("YouTube Music"))]}}
    listed = {"musicResponsiveListItemRenderer": {
        "thumbnail": THUMBS, "navigationEndpoint": {"watchPlaylistEndpoint": {"playlistId": "RDCLAKmix"}},
        "flexColumns": [column(run("Mix")), column(run("Station"))]}}
    card = {"musicCardShelfRenderer": {"title": {"runs": [run("Radiohead Radio")]},
                                       "onTap": {"watchPlaylistEndpoint": {"playlistId": "RDEMcard"}},
                                       "subtitle": {"runs": [run("Station")]}, "thumbnail": THUMBS}}
    result = search.grouped(response(card, {"itemSectionRenderer": {"contents": [row, listed]}}), "x")
    assert [(p["type"], p["playlistId"], p["title"]) for p in result["playlists"]] == [
        ("playlist", "RDEMk8", "Radiohead Radio"), ("playlist", "RDCLAKmix", "Mix")]
    assert [(t["type"], t["playlistId"]) for t in result["top"]] == [("playlist", "RDEMcard")]
    assert result["order"] == ["playlists"]


# ---- filtered lists, scopes and paging -----------------------------------------------------------


@pytest.mark.parametrize("name, search_filter, kind", [
    ("search_podcasts.json", "podcasts", "podcast"),
    ("search_episodes.json", "episodes", "track"),
    ("search_profiles.json", "profiles", "profile"),
])
def test_filtered_fixtures(load_fixture, name, search_filter, kind):
    w, client = worker(load_fixture(name))
    result = call(w, "search", query="q", filter=search_filter, limit=1)["result"]
    assert result["items"] and all(item["type"] == kind for item in result["items"])
    if search_filter == "episodes":
        assert all(e["browseId"].startswith("MPED") for e in result["items"]), "the episode page"
    assert result["cursor"] is None, "the recorder scrubs continuations"
    assert client.requests == [("search", search.search_body("q", search_filter), "")]


def test_cursor_pages_through_the_search_endpoint():
    first = response(shelf([song_row(f"s{i}", f"S{i}") for i in range(20)], token="S1"))
    second = continuation([song_row(f"s{i}", f"S{i}") for i in range(20, 40)], token="S2")
    w, client = worker(first, second)
    result = call(w, "search", query="radiohead", filter="songs")["result"]
    body = {"query": "radiohead", "params": get_search_params("songs", None, False)}
    assert [t["videoId"] for t in result["items"]] == [f"s{i}" for i in range(40)]
    assert result["cursor"] == "c.S2" and result["hasMore"] is True
    assert client.requests == [("search", body, ""), ("search", body, get_continuation_string("S1"))]

    third = continuation([song_row(f"s{i}", f"S{i}") for i in range(40, 60)])
    w, client = worker(third)
    result = call(w, "search", query="radiohead", filter="songs", cursor="c.S2", limit=20)["result"]
    assert [t["videoId"] for t in result["items"]] == [f"s{i}" for i in range(40, 60)]
    assert (result["cursor"], result["hasMore"]) == (None, False)
    assert client.requests == [("search", body, get_continuation_string("S2"))], "the same body, the cursor's ctoken"


def test_a_page_stays_within_three_requests():
    pages_ = [response(shelf([song_row("a", "A")], token="T0"))] + \
        [continuation([song_row(f"x{i}", "X")], token=f"T{i + 1}") for i in range(5)]
    w, client = worker(*pages_)
    result = call(w, "search", query="q", filter="songs")["result"]
    assert len(client.requests) == 3 and len(result["items"]) == 3 and result["cursor"] == "c.T2"


def test_a_list_drops_what_youtube_lists_again():
    # Live 'radiohead' Songs: the continuation repeated two songs of the first response.
    first = response(shelf([song_row("a", "A"), song_row("b", "B")], token="T0"))
    second = continuation([song_row("b", "B"), song_row("c", "C"), song_row("a", "A")], token="T1")
    w, client = worker(first, second)
    result = call(w, "search", query="q", filter="songs", limit=4)["result"]
    assert [t["videoId"] for t in result["items"]] == ["a", "b", "c"] and result["cursor"] == "c.T1"

    w, client = worker(continuation([song_row("d", "D"), song_row("d", "D"), song_row("e", "E")]))
    result = call(w, "search", query="q", filter="songs", cursor="c.T1")["result"]
    assert [t["videoId"] for t in result["items"]] == ["d", "e"], "a cursor page too"
    assert search.unique([{"type": "album", "browseId": "MPRE1"}, {"type": "playlist", "playlistId": "PL1"},
                          {"type": "album", "browseId": "MPRE1"}, {"type": "x"}, {"type": "x"}]) == [
        {"type": "album", "browseId": "MPRE1"}, {"type": "playlist", "playlistId": "PL1"}, {"type": "x"}, {"type": "x"}]


def test_cursor_needs_a_filtered_search():
    w, client = worker()
    assert call(w, "search", query="q", cursor="c.T")["error"]["code"] == "BAD_REQUEST"
    assert client.requests == []


def test_exact_passes_ignore_spelling():
    w, client = worker(response(), response())
    call(w, "search", query="radiohed", exact=True)
    call(w, "search", query="radiohed", filter="songs", exact=True)
    assert client.requests[0][1] == {"query": "radiohed", "params": get_search_params(None, None, True)}
    assert client.requests[1][1] == {"query": "radiohed", "params": get_search_params("songs", None, True)}
    w, client = worker(response())
    call(w, "search", query="radiohead")
    assert client.requests[0][1] == {"query": "radiohead"}, "a plain search sends no params"


def test_library_scope_needs_auth():
    w, client = worker(response(tabs=1))
    assert call(w, "search", query="radiohead", scope="library")["error"]["code"] == "AUTH_REQUIRED"
    assert call(w, "search", query="radiohead", scope="uploads")["error"]["code"] == "AUTH_REQUIRED"
    assert client.requests == []


def test_scope_filter_combinations_are_bad_requests():
    w, client = worker(auth="ok")
    assert call(w, "search", query="x", scope="uploads", filter="songs")["error"]["code"] == "BAD_REQUEST"
    assert call(w, "search", query="x", scope="library", filter="community_playlists")["error"]["code"] == "BAD_REQUEST"
    assert call(w, "search", query="x", scope="library", filter="featured_playlists")["error"]["code"] == "BAD_REQUEST"
    assert call(w, "search", query="x", scope="everywhere")["error"]["code"] == "BAD_REQUEST"
    assert client.requests == []


def test_library_scope_reads_its_tab():
    shelves = response(shelf([{"musicResponsiveListItemRenderer": {
        "thumbnail": THUMBS, "navigationEndpoint": {"browseEndpoint": {
            "browseId": "MPREb_yXhSI4FCUo6", "browseEndpointContextSupportedConfigs": {
                "browseEndpointContextMusicConfig": {"pageType": "MUSIC_PAGE_TYPE_ALBUM"}}}},
        "flexColumns": [column(run("OK Computer")), column(run("Album"), DOT, run("Radiohead", "UCr", raw.ARTIST_PAGE),
                                                           DOT, run("1997"))]}}]), shelf([song_row("k", "Karma Police")]),
        tabs=1)
    w, client = worker(shelves, auth="ok")
    result = call(w, "search", query="radiohead", scope="library")["result"]
    assert client.requests == [("search", {"query": "radiohead", "params": get_search_params(None, "library", False)},
                                "")]
    assert result["order"] == ["albums", "songs"]
    assert result["albums"][0]["browseId"] == "MPREb_yXhSI4FCUo6" and result["songs"][0]["videoId"] == "k"


def test_uploads_scope_lists_songs_and_upload_pages():
    album = {"musicResponsiveListItemRenderer": {
        "thumbnail": THUMBS, "navigationEndpoint": {"browseEndpoint": {
            "browseId": "FEmusic_library_privately_owned_release_detailb_x", "browseEndpointContextSupportedConfigs": {
                "browseEndpointContextMusicConfig": {"pageType": "MUSIC_PAGE_TYPE_ALBUM"}}}},
        "flexColumns": [column(run("Demo tape")), column(run("Album"), DOT, run("Me"), DOT, run("2020"))]}}
    song = {"musicResponsiveListItemRenderer": {
        "thumbnail": THUMBS,
        "flexColumns": [column(run("Track one", watch={"videoId": "up1", "playlistId": "PLup"})),
                        column(run("Me"), DOT, run("Demo tape"))]}}
    w, client = worker(response(shelf([album, song]), tabs=2), auth="ok")
    result = call(w, "search", query="demo", scope="uploads")["result"]
    assert client.requests[0][1] == {"query": "demo", "params": get_search_params(None, "uploads", False)}
    page, track = result["items"]
    # S10: an uploaded album as the uploads views list it (its menu, Delete, its upload page).
    assert (page["type"], page["upload"], page["browseId"], page["title"], page["artistText"], page["year"]) == (
        "album", True, "FEmusic_library_privately_owned_release_detailb_x", "Demo tape", "Me", "2020")
    assert page["entityId"] == "b_x"
    assert (track["type"], track["videoId"], track["kind"]) == ("track", "up1", "upload")


# ---- spelling ------------------------------------------------------------------------------------


def test_spelling_from_the_raw_response():
    showing = {"itemSectionRenderer": {"contents": [{"showingResultsForRenderer": {
        "showingResultsFor": {"runs": [{"text": "Mostrando resultados de"}]},
        "correctedQuery": {"runs": [{"text": "radiohead", "italics": True}]},
        "correctedQueryEndpoint": {"searchEndpoint": {"query": "radiohead"}},
        "originalQuery": {"runs": [{"text": "radiohed"}]},
        "originalQueryEndpoint": {"searchEndpoint": {"query": "radiohed", "params": "QgIIAQ%3D%3D"}}}}]}}
    result = search.grouped(response(showing, artist_card()), "radiohed")
    assert result["spelling"] == {"kind": "corrected", "corrected": "radiohead", "original": "radiohed"}
    assert result["top"][0]["name"] == "Radiohead", "the spelling row is not a result"
    did = {"itemSectionRenderer": {"contents": [{"didYouMeanRenderer": {
        "didYouMean": {"runs": [{"text": "Did you mean:"}]},
        "correctedQueryEndpoint": {"searchEndpoint": {"query": "radiohead"}}}}]}}
    w, _ = worker(response(did, shelf([song_row("a", "A")])))
    result = call(w, "search", query="radiohed", filter="songs", exact=True)["result"]
    assert result["spelling"] == {"kind": "didYouMean", "corrected": "radiohead", "original": "radiohed"}
    assert [t["videoId"] for t in result["items"]] == ["a"]
    assert search.grouped(response(shelf([song_row("a", "A")])), "q")["spelling"] is None


# ---- suggestions ---------------------------------------------------------------------------------


def suggestions_response(texts, history=(), rows=()):
    entries = []
    for text in texts:
        renderer = "historySuggestionRenderer" if text in history else "searchSuggestionRenderer"
        entry = {"suggestion": {"runs": [{"text": text}]}, "navigationEndpoint": {"searchEndpoint": {"query": text}}}
        if text in history:
            entry["serviceEndpoint"] = {"feedbackEndpoint": {"feedbackToken": "TOKEN-" + text}}
        entries.append({renderer: entry})
    contents = [{"searchSuggestionsSectionRenderer": {"contents": entries}}]
    if rows:
        contents.append({"searchSuggestionsSectionRenderer": {"contents": list(rows)}})
    return {"contents": contents}


def entity_artist():
    return {"musicResponsiveListItemRenderer": {
        "thumbnail": THUMBS,
        "navigationEndpoint": {"browseEndpoint": {"browseId": "UCr_iyUANcn9OX_yy9piYoLw",
                                                  "browseEndpointContextSupportedConfigs": {
                                                      "browseEndpointContextMusicConfig": {"pageType": raw.ARTIST_PAGE}}}},
        "menu": artist_card()["musicCardShelfRenderer"]["menu"],
        "flexColumns": [column(run("Radiohead")), column(run("205M monthly audience"))]}}


def entity_song(video_id):
    return {"musicResponsiveListItemRenderer": {
        "thumbnail": THUMBS, "playlistItemData": {"videoId": video_id},
        "navigationEndpoint": {"watchEndpoint": {"videoId": video_id, "watchEndpointMusicSupportedConfigs": {
            "watchEndpointMusicConfig": {"musicVideoType": "MUSIC_VIDEO_TYPE_ATV"}}}},
        "flexColumns": [column(run("Let Down")),
                        column(run("Song"), DOT, run("Radiohead", "UCr_iyUANcn9OX_yy9piYoLw", raw.ARTIST_PAGE), DOT,
                               run("226M plays")),
                        column(run("OK Computer", "MPREb_yXhSI4FCUo6", "MUSIC_PAGE_TYPE_ALBUM"))]}}


def entity_album():
    return {"musicResponsiveListItemRenderer": {
        "thumbnail": THUMBS,
        "navigationEndpoint": {"browseEndpoint": {"browseId": "MPREb_yXhSI4FCUo6",
                                                  "browseEndpointContextSupportedConfigs": {
                                                      "browseEndpointContextMusicConfig": {
                                                          "pageType": "MUSIC_PAGE_TYPE_ALBUM"}}}},
        "flexColumns": [column(run("OK Computer")),
                        column(run("Album"), DOT, run("Radiohead", "UCr_iyUANcn9OX_yy9piYoLw", raw.ARTIST_PAGE), DOT,
                               run("1997"))]}}


def test_entities_from_the_raw_suggestions():
    rows = [entity_artist(), entity_song("ZVgHPSyEIqk"), entity_album()] + [entity_song(f"v{i}") for i in range(5)]
    found = search.entities(suggestions_response(["radiohead"], rows=rows))
    assert len(found) == search.MAX_ENTITIES
    artist, song, album = found[:3]
    assert (artist["type"], artist["channelId"], artist["subtitle"]) == (
        "artist", "UCr_iyUANcn9OX_yy9piYoLw", "205M monthly audience")
    assert (artist["shuffleId"], artist["radioId"]) == ("RDAOk8", "RDEMk8")
    assert (song["type"], song["videoId"], song["kind"], song["artistText"], song["album"]) == (
        "track", "ZVgHPSyEIqk", "song", "Radiohead", {"name": "OK Computer", "id": "MPREb_yXhSI4FCUo6"})
    assert (album["type"], album["browseId"], album["artistText"], album["albumType"], album["year"]) == (
        "album", "MPREb_yXhSI4FCUo6", "Radiohead", "Album", "1997")
    assert search.entities(suggestions_response(["x"])) == [], "no second section, no rows"


def test_suggestions_reply_with_entities_and_no_tokens():
    w, client = worker(suggestions_response(["radiohead", "radiohead creep"], history={"radiohead creep"},
                                            rows=[entity_artist()]))
    result = call(w, "suggestions", query="radioh")["result"]
    assert result["items"] == [{"text": "radiohead", "fromHistory": False},
                               {"text": "radiohead creep", "fromHistory": True}]
    assert [e["type"] for e in result["entities"]] == ["artist"]
    assert "TOKEN" not in str(result)
    assert client.requests == [(SUGGEST, {"input": "radioh"}, "")]


def test_empty_query_is_your_past_searches():
    w, client = worker(suggestions_response(["a", "b"]))
    assert call(w, "suggestions", query="")["result"] == {"items": [], "entities": []}
    assert client.requests == [], "signed out: none, and no request"
    w, client = worker(suggestions_response(["daft punk", "nils frahm"], history={"daft punk", "nils frahm"}),
                       auth="ok")
    result = call(w, "suggestions", query="  ")["result"]
    assert result == {"items": [{"text": "daft punk", "fromHistory": True},
                                {"text": "nils frahm", "fromHistory": True}], "entities": []}
    assert client.calls == [("get_search_suggestions", "", True)]


def test_remove_suggestion_removes_that_history_entry():
    w, client = worker(suggestions_response(["radiohead", "radiohead creep", "radiohead live"],
                                            history={"radiohead creep", "radiohead live"}), auth="ok")
    result = call(w, "removeSuggestion", query="radioh", text="radiohead live")["result"]
    assert result == {"removed": True, "text": "radiohead live"}
    name, suggestions, indices = client.calls[-1]
    assert name == "remove_search_suggestions" and indices == [2]
    assert suggestions[2]["feedbackToken"] == "TOKEN-radiohead live"
    assert client.calls[0] == ("get_search_suggestions", "radioh", True)


def test_remove_suggestion_rejects_what_is_not_history():
    w, client = worker(suggestions_response(["radiohead", "radiohead creep"], history={"radiohead creep"}), auth="ok")
    assert call(w, "removeSuggestion", query="radioh", text="radiohead")["error"]["code"] == "BAD_REQUEST"
    assert not [c for c in client.calls if c[0] == "remove_search_suggestions"]
    w, client = worker(auth="none")
    assert call(w, "removeSuggestion", query="", text="x")["error"]["code"] == "AUTH_REQUIRED"
    assert client.calls == []


def test_remove_suggestion_matches_case_when_it_must():
    w, client = worker(suggestions_response(["Daft Punk"], history={"Daft Punk"}), auth="ok")
    assert call(w, "removeSuggestion", text="daft punk")["result"]["text"] == "Daft Punk"
    assert client.calls[-1][2] == [0]


# ---- registry and recorders ----------------------------------------------------------------------


def test_registry():
    assert "removeSuggestion" in ytm.MUTATES and ytm.NO_INVALIDATES["removeSuggestion"]
    assert ytm.LANES["suggestions"] == "suggest"
    assert not {"search", "suggestions", "removeSuggestion"} & ytm.CACHEABLE, "search stays uncached"
    assert ytm.METHODS["search"] is search.m_search and {"search", "searchRaw", "suggestionsRaw"} <= set(ytm.RECORDERS)


def test_raw_recorder_trims_and_keeps_mixes():
    client = FakeClient(response(artist_card()))
    recorded = search.record_search_raw(client, {"query": "q", "filter": "podcasts"})
    assert client.requests == [("search", {"query": "q", "params": get_search_params("podcasts", None, False)}, "")]
    card = search.section_list(recorded)["contents"][0]["musicCardShelfRenderer"]
    assert [e["menuNavigationItemRenderer"]["icon"]["iconType"] for e in card["menu"]["menuRenderer"]["items"]] == [
        "MUSIC_SHUFFLE", "MIX"]


def test_recorded_text_has_plain_hyphens(load_fixture):
    # The repo carries no en or em dash; YouTube titles do ("Ep. 79 \u2013 Ferocity").
    assert search._trim({"title": {"runs": [{"text": "Ep. 79 \u2013 A \u2014 B"}]}, "n": 1}) == {
        "title": {"runs": [{"text": "Ep. 79 - A - B"}]}, "n": 1}
    for name in ("search_raw", "search_podcast_top", "search_podcasts", "search_episodes", "search_profiles"):
        text = repr(load_fixture(name + ".json"))
        assert "\u2013" not in text and "\u2014" not in text, name
