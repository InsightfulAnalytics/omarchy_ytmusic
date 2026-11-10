"""ytm_home (S5): the whole Home feed from the raw browse (shelves with their
See all and Play all, chips, continuation pages), its get_home fallback,
and shelfQueue (a shelf's Play all)."""

import copy
import json

import pytest
from ytmusicapi.continuations import get_continuation_string
from ytmusicapi.parsers import browsing as browsing_parsers
from ytmusicapi.parsers import watch as watch_parsers

import ytm
import ytm_cache as caches
import ytm_home as home
import ytm_raw as raw

CONTINUATION = "continuations"


class HomeYT:
    """Answers _send_request from a queue (recording each request) and
    get_home from a canned list."""

    def __init__(self, *responses, legacy=None):
        self.responses = list(responses)
        self.requests = []
        self.legacy = legacy if legacy is not None else []
        self.legacy_calls = []

    def _send_request(self, endpoint, body, additionalParams=""):
        self.requests.append((endpoint, dict(body), additionalParams))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get_home(self, limit=3):
        self.legacy_calls.append(limit)
        return self.legacy


def call(worker, method, **params):
    return worker.handle({"id": 7, "method": method, "params": params})


def next_data(token):
    return [{"nextContinuationData": {"continuation": token}}]


def with_token(first, token):
    """The recorded first response with a continuation token put back (the
    recorder's scrub drops them)."""
    out = copy.deepcopy(first)
    raw.section_list(out)[CONTINUATION] = next_data(token)
    return out


def continuation(rows, token=None):
    section = {"contents": copy.deepcopy(rows)}
    if token:
        section[CONTINUATION] = next_data(token)
    return {"continuationContents": {"sectionListContinuation": section}}


def single_column(rows, token=None, chip_cloud=None):
    section = {"contents": rows}
    if token:
        section[CONTINUATION] = next_data(token)
    if chip_cloud is not None:
        section["header"] = {"chipCloudRenderer": {"chips": chip_cloud}}
    return {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
        {"tabRenderer": {"content": {"sectionListRenderer": section}}}]}}}


def carousel(title, items, more=None, play=None):
    header = {"title": {"runs": [{"text": title}]}}
    if more:
        header["moreContentButton"] = {"buttonRenderer": {"navigationEndpoint": {"browseEndpoint": more}}}
    if play:
        header["moreContentButton"] = {"buttonRenderer": {"navigationEndpoint": {"watchEndpoint": play}}}
    return {"musicCarouselShelfRenderer": {"header": {"musicCarouselShelfBasicHeaderRenderer": header},
                                           "contents": items}}


def page_type(kind):
    return {"browseEndpointContextSupportedConfigs": {"browseEndpointContextMusicConfig": {
        "pageType": f"MUSIC_PAGE_TYPE_{kind}"}}}


def two_row(title, browse_id, kind, subtitle=None):
    endpoint = {"browseEndpoint": dict({"browseId": browse_id}, **page_type(kind))}
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": title, "navigationEndpoint": endpoint}]},
        "subtitle": {"runs": [{"text": subtitle or "Artist"}]},
        "navigationEndpoint": endpoint,
        "thumbnailRenderer": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
            {"url": "https://lh3.googleusercontent.com/x=w226-h226-l90-rj", "width": 226, "height": 226}]}}}}}


def album(n):
    return two_row(f"Album {n}", f"MPREb_{n:05d}", "ALBUM")


@pytest.fixture
def recorded(load_fixture):
    return load_fixture("home_raw.json")


def feed_pages(recorded):
    """The fixture's pages as responses: first, then sectionListContinuations."""
    return [recorded["first"]] + [continuation(rows) for rows in recorded["pages"]]


def worker_for(*responses, legacy=None, cache=None):
    return ytm.Worker(HomeYT(*responses, legacy=legacy), "none", cache=cache)


# ---- chips --------------------------------------------------------------------------------


def test_chips_are_read_from_the_section_list_header(recorded):
    chips = home.chips(raw.section_list(recorded["first"]))
    assert len(chips) >= 5, "the anonymous feed serves its mood chips"
    assert all(c["title"] and c["params"] for c in chips)
    assert not any(c["selected"] for c in chips), "All is the page without params: no chip is selected"
    chosen = home.chips(raw.section_list(recorded["chip"]["first"]))
    assert [c["params"] for c in chosen if c["selected"]] == [recorded["chip"]["params"]]
    assert [c["title"] for c in chosen] == [c["title"] for c in chips], "the same chips, one selected"


def test_chips_that_leave_home_or_carry_no_params_are_skipped():
    def chip(title, browse_id, params, selected=False):
        endpoint = {"browseEndpoint": {"browseId": browse_id, "params": params}} if browse_id else {}
        return {"chipCloudChipRenderer": {"text": {"runs": [{"text": title}]}, "navigationEndpoint": endpoint,
                                          "isSelected": selected}}

    section = raw.section_list(single_column([], chip_cloud=[
        chip("Relax", "FEmusic_home", "p1", True), chip("Elsewhere", "FEmusic_explore", "p2"),
        chip("Bare", "FEmusic_home", None), chip("Broken", None, None), {"somethingElse": {}}]))
    assert home.chips(section) == [{"title": "Relax", "params": "p1", "selected": True}]
    assert home.chips(None) == [] and home.chips({}) == []


# ---- shelves --------------------------------------------------------------------------------


def test_each_shelf_keeps_its_see_all_and_play_all(recorded):
    rows = [row for page in [raw.rows(recorded["first"])] + recorded["pages"] for row in page]
    shelves = home.shelves(home.parse_rows(rows))
    assert shelves and all(s["type"] == "section" and s["layout"] == "carousel" for s in shelves)
    with_more = [s for s in shelves if s["more"]]
    assert with_more, "the recorded feed has a See all"
    for s in with_more:
        assert s["more"]["browseId"].startswith("FEmusic_") and s["more"]["params"], s["more"]
    chip_shelves = home.shelves(home.parse_rows(raw.rows(recorded["chip"]["first"])))
    with_play = [s for s in chip_shelves if s["play"]]
    assert with_play, "the recorded chip has a Quick picks with Play all"
    play = with_play[0]["play"]
    assert play["videoId"] and play["params"]
    assert all(item["type"] == "track" for item in with_play[0]["items"]) and with_play[0]["items"]


def test_a_row_without_contents_does_not_shift_later_shelves(recorded):
    rows = copy.deepcopy(raw.rows(recorded["chip"]["first"]))
    expected = [(s["title"], s["more"], s["play"], [home_key(i) for i in s["items"]])
                for s in home.shelves(home.parse_rows(rows))]
    empty = [{"musicTastebuilderShelfRenderer": {"primaryText": {"runs": [{"text": "Tell us"}]}}},
             {"musicCarouselShelfRenderer": {"header": {"musicCarouselShelfBasicHeaderRenderer": {
                 "title": {"runs": [{"text": "Nothing"}]}}}}},
             {"itemSectionRenderer": {"contents": []}}, "not a row"]
    mixed = [empty[0], rows[0], empty[1], empty[2], rows[1], empty[3]] + rows[2:]
    parsed = home.parse_rows(mixed)
    assert len(parsed) == len(mixed), "one entry per row, read or not"
    got = [(s["title"], s["more"], s["play"], [home_key(i) for i in s["items"]]) for s in home.shelves(parsed)]
    assert got == expected


def home_key(item):
    return item["type"] + ":" + str(item.get("videoId") or item.get("browseId") or item.get("playlistId")
                                    or item.get("channelId"))


def test_a_row_that_raises_is_an_empty_entry(monkeypatch):
    real = raw.parse_row

    def parse_row(row):
        if "boom" in row:
            raise KeyError("x")
        return real(row)

    monkeypatch.setattr(raw, "parse_row", parse_row)
    rows = [carousel("A", [album(1)]), {"boom": {}}, carousel("B", [album(2)])]
    parsed = home.parse_rows(rows)
    assert len(parsed) == 3 and parsed[1]["layout"] is None
    assert [s["title"] for s in home.shelves(parsed)] == ["A", "B"]


def test_unknown_items_are_dropped_and_mpla_artists_stripped():
    items = [album(1), {"somethingNew": {"title": "?"}},
             two_row("Your artist", "MPLAUCr_iyUANcn9OX_yy9piYoLw", "LIBRARY_ARTIST", "12 songs"),
             two_row("Profile", "UCprofile", "USER_CHANNEL"),
             two_row("Show", "MPSPPLx", "PODCAST_SHOW_DETAIL_PAGE"),
             {"musicTwoRowItemRenderer": {"title": {"runs": [{"text": "No endpoint"}]}}}]
    shelf = home.shelves(home.parse_rows([carousel("Mixed", items)]))[0]
    assert [i["type"] for i in shelf["items"]] == ["album", "artist", "profile", "podcast"]
    assert shelf["items"][1]["channelId"] == "UCr_iyUANcn9OX_yy9piYoLw", "MPLA is stripped"


def with_strapline(row, strapline, thumbnail=False, title_endpoint=None):
    header = row["musicCarouselShelfRenderer"]["header"]["musicCarouselShelfBasicHeaderRenderer"]
    header["strapline"] = {"runs": [{"text": strapline}]}
    if thumbnail:
        header["thumbnail"] = {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
            {"url": "https://yt3.ggpht.com/x=s88", "width": 88, "height": 88}]}}}
    if title_endpoint:
        header["title"]["runs"][0]["navigationEndpoint"] = {"browseEndpoint": title_endpoint}
    return row


def test_a_shelf_keeps_its_strapline(recorded):
    shelves = home.shelves(home.parse_rows(raw.rows(recorded["first"])))
    themed = [s for s in shelves if s.get("strapline")]
    assert [(s["strapline"], s["title"]) for s in themed] == [("FEEL THE WARMTH \u2600\ufe0f\U0001f33c", "Springtime")]
    artist = dict({"browseId": "UCHUartist"}, **page_type("ARTIST"))
    rows = [with_strapline(carousel("Nils Frahm", [album(1)]), "SIMILAR TO", thumbnail=True, title_endpoint=artist),
            # Listen again: the account's name and avatar above the title.
            with_strapline(carousel("Listen again", [album(2)]), "ACCOUNT NAME", thumbnail=True,
                           title_endpoint={"browseId": "FEmusic_listen_again"}),
            # ... also without the avatar: its title still links to its page.
            with_strapline(carousel("Listen again", [album(3)]), "ACCOUNT NAME",
                           title_endpoint={"browseId": "FEmusic_listen_again"}),
            with_strapline(carousel("Profile shelf", [album(4)]), "SOMEONE", thumbnail=True,
                           title_endpoint=dict({"browseId": "UCsomeone"}, **page_type("USER_CHANNEL"))),
            with_strapline(carousel("Pictured", [album(5)]), "WHO?", thumbnail=True),
            with_strapline(carousel("", [album(6)]), "ONLY A STRAPLINE")]
    got = [(s["title"], s.get("strapline")) for s in home.shelves(home.parse_rows(rows))]
    assert got == [("Nils Frahm", "SIMILAR TO"), ("Listen again", None), ("Listen again", None),
                   ("Profile shelf", None), ("Pictured", None), ("ONLY A STRAPLINE", None)]


def test_rows_that_show_nothing_are_left_out_but_a_see_all_alone_stays():
    more = {"browseId": "FEmusic_listen_again", "params": "p"}
    rows = [carousel("Empty", []), carousel("Only see all", [], more=more),
            {"musicDescriptionShelfRenderer": {"header": {"runs": [{"text": "About"}]}}}]
    assert [s["title"] for s in home.shelves(home.parse_rows(rows))] == ["Only see all"]


# ---- homeFeed --------------------------------------------------------------------------------


def test_home_feed_body_and_first_call_budget(recorded):
    pages = feed_pages(recorded)
    first = with_token(pages[0], "T1")
    second = copy.deepcopy(pages[1])
    raw.section_list(second)[CONTINUATION] = next_data("T2")
    third = copy.deepcopy(pages[2])
    raw.section_list(third)[CONTINUATION] = next_data("T3")
    worker = worker_for(first, second, third, continuation([carousel("Never", [album(9)])]))
    reply = call(worker, "homeFeed")
    assert reply["ok"], reply
    sent = worker.yt.requests
    assert sent[0] == ("browse", {"browseId": "FEmusic_home"}, "")
    assert sent[1] == ("browse", {"browseId": "FEmusic_home"}, get_continuation_string("T1"))
    assert len(sent) <= home.MAX_HTTP and reply["http"] == 0, "the fake client counts no HTTP; requests are"
    result = reply["result"]
    titles = [s["title"] for s in result["shelves"]]
    assert "Never" not in titles
    assert len(titles) >= home.FIRST_SHELVES or len(sent) == home.MAX_HTTP
    assert result["chips"] and result["cursor"] == ("c.T2" if len(sent) == 2 else "c.T3")
    assert result["hasMore"] is True


def test_home_feed_with_a_chip_sends_its_params(recorded):
    params = recorded["chip"]["params"]
    worker = worker_for(recorded["chip"]["first"])
    result = call(worker, "homeFeed", params=params)["result"]
    assert worker.yt.requests == [("browse", {"browseId": "FEmusic_home", "params": params}, "")]
    assert [c["params"] for c in result["chips"] if c["selected"]] == [params]
    assert result["cursor"] is None and result["hasMore"] is False


def test_the_first_call_stops_at_eight_shelves():
    rows = lambda first: [carousel(f"S{n}", [album(n)]) for n in range(first, first + 3)]  # noqa: E731
    worker = worker_for(single_column(rows(0), "T1"), continuation(rows(3), "T2"), continuation(rows(6), "T3"))
    result = call(worker, "homeFeed")["result"]
    assert len(worker.yt.requests) == 3 and len(result["shelves"]) == 9 and result["cursor"] == "c.T3"
    worker = worker_for(single_column(rows(0) * 3, "T1"))
    result = call(worker, "homeFeed")["result"]
    assert len(worker.yt.requests) == 1 and len(result["shelves"]) == 9, "eight shelves in one page are enough"


def test_a_request_made_before_the_first_page_counts_against_the_budget(monkeypatch):
    # A signed-out client fetches its visitor id before its first request.
    monkeypatch.setattr(raw, "http_count", lambda: 2)
    rows = lambda first: [carousel(f"S{n}", [album(n)]) for n in range(first, first + 3)]  # noqa: E731
    worker = worker_for(single_column(rows(0), "T1"), continuation(rows(3), "T2"), continuation(rows(6), "T3"))
    result = call(worker, "homeFeed")["result"]
    assert len(worker.yt.requests) == 2 and len(result["shelves"]) == 6 and result["cursor"] == "c.T2"


def test_a_cursor_reads_one_page_of_shelves():
    rows = [carousel(f"S{n}", [album(n)]) for n in range(3)]
    worker = worker_for(continuation(rows, "T5"), continuation(rows, "T6"))
    result = call(worker, "homeFeed", params="chip", cursor="c.T4")["result"]
    assert worker.yt.requests == [("browse", {"browseId": "FEmusic_home", "params": "chip"},
                                   get_continuation_string("T4"))]
    assert [s["title"] for s in result["shelves"]] == ["S0", "S1", "S2"]
    assert result["cursor"] == "c.T5" and result["chips"] == []


def test_a_cursor_page_of_unreadable_rows_reads_on():
    odd = [{"musicTastebuilderShelfRenderer": {}}]
    worker = worker_for(continuation(odd, "T5"), continuation([carousel("Late", [album(1)])]))
    result = call(worker, "homeFeed", cursor="c.T4")["result"]
    assert len(worker.yt.requests) == 2, "a page that shows nothing is not the end of the feed"
    assert [s["title"] for s in result["shelves"]] == ["Late"] and result["cursor"] is None


def test_the_last_page_ends_the_feed(recorded):
    worker = worker_for(continuation(recorded["pages"][-1]))
    result = call(worker, "homeFeed", cursor="c.T9")["result"]
    assert result["shelves"] and result["cursor"] is None and result["hasMore"] is False


def test_an_unreadable_response_falls_back_to_get_home():
    legacy = [{"title": "Quick picks", "contents": [{"videoId": "nbCOAPR33ME", "title": "Karma Police",
                                                     "artists": [{"name": "Radiohead", "id": "UCr"}]}]},
              {"title": "Albums", "contents": [{"title": "OK Computer", "browseId": "MPREb_yXhSI4FCUo6"}]}]
    worker = worker_for({"contents": {"somethingNew": {}}}, legacy=legacy)
    reply = call(worker, "homeFeed")
    assert reply["ok"], reply
    assert worker.yt.legacy_calls == [home.LEGACY_ROWS]
    result = reply["result"]
    assert [s["title"] for s in result["shelves"]] == ["Quick picks", "Albums"]
    assert all(s["more"] is None and s["play"] is None for s in result["shelves"])
    assert result["chips"] == [] and result["cursor"] is None


def test_a_parser_crash_falls_back_too(monkeypatch, recorded):
    def broken(contents):
        raise TypeError("shape changed")

    monkeypatch.setattr(home, "parse_rows", broken)
    worker = worker_for(recorded["first"], legacy=[{"title": "Old", "contents": [{"videoId": "a1", "title": "A"}]}])
    result = call(worker, "homeFeed")["result"]
    assert [s["title"] for s in result["shelves"]] == ["Old"]


def unreadable_row(row):
    raise KeyError("shape changed")


def test_a_first_page_of_rows_that_all_fail_falls_back(monkeypatch, recorded):
    monkeypatch.setattr(raw, "parse_row", unreadable_row)
    worker = worker_for(recorded["first"], legacy=[{"title": "Old", "contents": [{"videoId": "a1", "title": "A"}]}])
    result = call(worker, "homeFeed", cache="refresh")["result"]
    assert worker.yt.legacy_calls == [home.LEGACY_ROWS]
    assert [s["title"] for s in result["shelves"]] == ["Old"] and result["chips"] == []


def test_an_empty_first_page_is_not_a_failure():
    worker = worker_for(single_column([carousel("Empty", [])]))
    result = call(worker, "homeFeed", cache="refresh")["result"]
    assert result["shelves"] == [] and worker.yt.legacy_calls == []


@pytest.mark.parametrize("broken", ["no section list", "every row fails"])
def test_an_unreadable_chip_feed_fails_and_is_not_cached(monkeypatch, tmp_path, recorded, broken):
    """get_home has no chips: its All shelves must never stand in for a chip's."""
    if broken == "no section list":
        response = {"contents": {"somethingNew": {}}}
    else:
        response = recorded["chip"]["first"]
        monkeypatch.setattr(raw, "parse_row", unreadable_row)
    cache = caches.DiskCache(tmp_path / "data")
    worker = worker_for(response, legacy=[{"title": "Old", "contents": [{"videoId": "a1", "title": "A"}]}],
                        cache=cache)
    reply = call(worker, "homeFeed", params="RELAX_CHIP", cache="refresh")
    assert not reply["ok"] and reply["error"]["code"] == "UPSTREAM"
    assert worker.yt.requests[0][1] == {"browseId": "FEmusic_home", "params": "RELAX_CHIP"}
    assert worker.yt.legacy_calls == [], "no get_home for a chip"
    assert list(cache.dir.glob("*.json")) == [], "nothing reaches the disk cache"
    monkeypatch.undo()
    worker.yt.responses.append(single_column([carousel("Calm", [album(1)])]))
    again = call(worker, "homeFeed", params="RELAX_CHIP", cache="prefer")
    assert again["ok"] and again["cached"] is False
    assert [s["title"] for s in again["result"]["shelves"]] == ["Calm"]


def test_network_errors_are_not_hidden_by_the_fallback():
    import requests

    worker = worker_for(requests.exceptions.ConnectionError("down"))
    reply = call(worker, "homeFeed")
    assert reply["error"]["code"] == "NETWORK" and worker.yt.legacy_calls == []


def test_first_pages_are_cached_per_chip_and_cursor_pages_never(tmp_path):
    cache = caches.DiskCache(tmp_path / "data")
    rows = [carousel(f"S{n}", [album(n)]) for n in range(9)]
    worker = worker_for(single_column(rows, "T1"), single_column(rows[:3]), cache=cache)
    assert call(worker, "homeFeed", cache="refresh")["ok"]
    assert call(worker, "homeFeed", params="relax", cache="refresh")["ok"]
    hit = call(worker, "homeFeed", cache="prefer")
    assert hit["cached"] is True and hit["http"] == 0
    assert hit["result"]["cursor"] is None and hit["result"]["hasMore"] is True, "the token never reaches disk"
    relax = call(worker, "homeFeed", params="relax", cache="prefer")
    assert relax["cached"] is True and len(relax["result"]["shelves"]) == 3
    worker.yt = HomeYT(continuation(rows[:3]))
    assert call(worker, "homeFeed", cursor="c.T1", cache="prefer")["cached"] is False
    assert len(list(cache.dir.glob("*.json"))) == 2
    assert "T1" not in "".join(p.read_text() for p in cache.dir.glob("*.json"))


# ---- shelfQueue ---------------------------------------------------------------------------------


def queue_entry(video_id, explicit=False, menu=True):
    data = {
        "videoId": video_id,
        "title": {"runs": [{"text": f"Song {video_id}"}]},
        "lengthText": {"runs": [{"text": "3:05"}]},
        "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/vi/x/sddefault.jpg", "width": 120, "height": 90}]},
        "longBylineText": {"runs": [{"text": "Radiohead", "navigationEndpoint": {"browseEndpoint": dict(
            {"browseId": "UCr_iyUANcn9OX_yy9piYoLw"}, **page_type("ARTIST"))}}]},
        "navigationEndpoint": {"watchEndpoint": {"videoId": video_id, "playlistId": "TLGGq"}},
    }
    if menu:
        data["menu"] = {"menuRenderer": {"items": []}}
    if explicit:
        data["badges"] = [{"musicInlineBadgeRenderer": {"icon": {"iconType": "MUSIC_EXPLICIT_BADGE"}}}]
    return {"playlistPanelVideoRenderer": data}


def next_response(entries, playlist_id="TLGGq"):
    panel = {"contents": entries}
    if playlist_id:
        panel["playlistId"] = playlist_id
    return {"contents": {"singleColumnMusicWatchNextResultsRenderer": {"tabbedRenderer": {
        "watchNextTabbedResultsRenderer": {"tabs": [{"tabRenderer": {"content": {"musicQueueRenderer": {
            "content": {"playlistPanelRenderer": panel}}}}}]}}}}}


def test_shelf_queue_pins_the_next_body():
    worker = worker_for(next_response([queue_entry("a1"), queue_entry("b2", explicit=True)]))
    reply = call(worker, "shelfQueue", videoId="a1", params="wAEB", playlistId=None)
    assert reply["ok"], reply
    assert worker.yt.requests == [("next", {"videoId": "a1", "params": "wAEB", "isAudioOnly": True,
                                            "enablePersistentPlaylistPanel": True}, "")]
    result = reply["result"]
    assert result["playlistId"] == "TLGGq"
    assert [t["videoId"] for t in result["items"]] == ["a1", "b2"]
    assert result["items"][0]["artists"] == [{"name": "Radiohead", "id": "UCr_iyUANcn9OX_yy9piYoLw"}]
    assert [t["explicit"] for t in result["items"]] == [False, True]
    worker = worker_for(next_response([queue_entry("c3")], playlist_id=None))
    reply = call(worker, "shelfQueue", playlistId="RDx", params="p")
    assert worker.yt.requests[0][1] == {"playlistId": "RDx", "params": "p", "isAudioOnly": True,
                                        "enablePersistentPlaylistPanel": True}
    assert reply["result"]["playlistId"] == "TLGGq", "from the first song's endpoint"


def test_shelf_queue_uses_the_watch_parser_not_the_browsing_one():
    assert home.parse_watch_playlist is watch_parsers.parse_watch_playlist
    assert home.parse_watch_playlist is not browsing_parsers.parse_watch_playlist


def test_shelf_queue_skips_entries_it_cannot_read_and_needs_an_endpoint():
    worker = worker_for(next_response([queue_entry("a1", menu=False), {"automixPreviewVideoRenderer": {}},
                                       queue_entry("b2")]))
    result = call(worker, "shelfQueue", videoId="b2")["result"]
    assert [t["videoId"] for t in result["items"]] == ["b2"]
    assert call(worker_for(), "shelfQueue", params="p")["error"]["code"] == "BAD_REQUEST"
    reply = call(worker_for({"contents": {}}), "shelfQueue", videoId="a1")
    assert reply["error"]["code"] == "UPSTREAM"


# ---- registry and recorder --------------------------------------------------------------------


def test_registry():
    assert ytm.METHODS["homeFeed"] is home.m_home_feed and ytm.METHODS["shelfQueue"] is home.m_shelf_queue
    assert "homeFeed" in ytm.CACHEABLE and ytm.LANES["homeFeed"] == "pages"
    assert "shelfQueue" not in ytm.CACHEABLE and "shelfQueue" not in ytm.LANES
    assert "home" not in ytm.METHODS, "Home is homeFeed now"
    assert ytm.METHODS["homeLegacy"] is ytm.m_home and "homeLegacy" in ytm.CACHEABLE
    assert "homeRaw" in ytm.RECORDERS


def test_the_recorder_keeps_rows_and_chips_and_the_scrub_drops_tokens():
    first = single_column([carousel("A", [album(1)])], "T1", chip_cloud=[{"chipCloudChipRenderer": {
        "text": {"runs": [{"text": "Relax"}]},
        "navigationEndpoint": {"browseEndpoint": {"browseId": "FEmusic_home", "params": "rp"}}}}])
    yt = HomeYT(first, continuation([carousel("B", [album(2)])]), single_column([carousel("C", [album(3)])]))
    data = ytm.scrub(home.record_home_raw(yt, {"pages": 2, "chip": 0}))
    text = json.dumps(data)
    assert "T1" not in text and "continuation" not in text.lower()
    assert [r[0] for r in yt.requests] == ["browse"] * 3
    assert yt.requests[2][1] == {"browseId": "FEmusic_home", "params": "rp"}
    assert [s["title"] for s in home.shelves(home.parse_rows(raw.rows(data["first"])))] == ["A"]
    assert [s["title"] for s in home.shelves(home.parse_rows(data["pages"][0]))] == ["B"]
    assert data["chip"]["params"] == "rp"


def test_the_fixture_holds_no_tokens(recorded):
    text = json.dumps(recorded).lower()
    for word in ("continuation", "token", "feedback", "cookie", "sapisid"):
        assert word not in text, word
