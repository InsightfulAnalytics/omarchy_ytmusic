"""ytm_collections (S3): playlists read whole, Liked Music, albums with their
shelves, mixes, history, and the per-track fields later slices act on."""

import pytest

import ytm
import ytm_cache
import ytm_collections as collections
import ytm_normalize as norm
import ytm_raw as raw
from conftest import FIXTURES


class FakeYT:
    def __init__(self, **responses):
        self.responses = responses
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            value = self.responses.get(name)
            if callable(value):
                return value(*args, **kwargs)
            if isinstance(value, Exception):
                raise value
            return value

        return method


def call(worker, method, **params):
    return worker.handle({"id": 5, "method": method, "params": params})


def songs(n, prefix="v", **extra):
    return [dict({"videoId": f"{prefix}{i}", "title": f"Song {i}", "duration": "3:00",
                  "artists": [{"name": "A", "id": "UCa"}]}, **extra) for i in range(n)]


def playlist_data(read, count, **extra):
    return dict({"id": "PLx", "title": "Mine", "owned": False, "trackCount": count, "tracks": songs(read)}, **extra)


# ---- the truncation rule -----------------------------------------------------------------


def test_truncation_rule_by_counts():
    # The plan's three cases, from hand-built dicts (no continuation seen).
    cut = norm.collection("playlist", playlist_data(300, 416), "PLx", limit=200)
    assert cut["truncated"] is True and cut["unavailable"] is None
    whole = norm.collection("playlist", playlist_data(250, 250), "PLx", limit=200)
    assert whole["truncated"] is False and whole["unavailable"] == 0
    liked = norm.collection("liked", playlist_data(377, 416), "LM", limit=None)
    assert liked["truncated"] is False and liked["unavailable"] == 39
    assert liked["trackCount"] == 416 and len(liked["tracks"]) == 377


def test_a_continuation_left_decides_over_the_counts():
    # 247 of 250 with nothing left to read: three are unavailable, not unread.
    done = norm.collection("playlist", playlist_data(247, 250), "PLx", limit=200, more=False)
    assert done["truncated"] is False and done["unavailable"] == 3
    assert norm.truncated(300, 416, 200, more=True) is True
    assert norm.truncated(300, 416, 200, more=False) is False
    # A header that counts no more than were read is never truncated.
    assert norm.truncated(300, 300, 200, more=True) is False
    assert norm.truncated(300, None, 200) is True and norm.truncated(5, None, 200) is False


# ---- playlist ----------------------------------------------------------------------------


def test_playlist_reads_200_by_default_and_strips_vl():
    worker = ytm.Worker(FakeYT(get_playlist=playlist_data(300, 1126)), "ok")
    reply = call(worker, "playlist", playlistId="VLPLx")
    assert reply["ok"], reply
    assert worker.yt.calls == [("get_playlist", ("PLx",), {"limit": 200})]
    page = reply["result"]
    assert page["id"] == "PLx" and page["playlistId"] == "PLx" and page["kind"] == "playlist"
    assert page["truncated"] is True and page["capped"] is False and page["unavailable"] is None


def test_limit_all_reads_to_the_cap_and_is_whole():
    worker = ytm.Worker(FakeYT(get_playlist=playlist_data(1120, 1126)), "ok")
    page = call(worker, "playlist", playlistId="PLx", limit="all")["result"]
    # ytmusicapi is asked for the cap rather than None, so a huge list stops
    # there instead of being read to its end and cut (see the slice notes).
    assert worker.yt.calls == [("get_playlist", ("PLx",), {"limit": collections.MAX_TRACKS})]
    assert page["truncated"] is False and page["capped"] is False and page["unavailable"] == 6
    assert len(page["tracks"]) == 1120


def test_limit_all_stops_at_the_cap():
    worker = ytm.Worker(FakeYT(get_liked_songs=playlist_data(5100, 9000), get_account_info={}), "ok")
    page = call(worker, "liked", limit="all")["result"]
    assert worker.yt.calls[-1] == ("get_liked_songs", (), {"limit": 5000})
    assert len(page["tracks"]) == 5000 and page["truncated"] is True and page["capped"] is True


def test_a_whole_read_past_the_cap_is_capped_even_with_nothing_left():
    # ytmusicapi does not count its first page against the limit: a list of
    # 5050 comes back whole, and the 50 cut here must not pass as whole.
    worker = ytm.Worker(FakeYT(get_liked_songs=playlist_data(5050, 5050), get_account_info={}), "ok")
    page = call(worker, "liked", limit="all")["result"]
    assert len(page["tracks"]) == 5000 and page["trackCount"] == 5050
    assert page["truncated"] is True and page["capped"] is True and page["unavailable"] is None
    # Exactly at the cap: nothing was cut.
    worker = ytm.Worker(FakeYT(get_playlist=playlist_data(5000, 5000)), "ok")
    page = call(worker, "playlist", playlistId="PLx", limit="all")["result"]
    assert page["truncated"] is False and page["capped"] is False and page["unavailable"] == 0


def test_limit_is_checked():
    worker = ytm.Worker(FakeYT(get_playlist=playlist_data(1, 1)), "ok")
    assert call(worker, "playlist", playlistId="PLx", limit="some")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "playlist", playlistId="PLx", related="yes")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "playlist", playlistId="PLx", limit=9000)["ok"]
    assert worker.yt.calls[-1][2]["limit"] == collections.MAX_TRACKS


def test_playlist_related_section(load_fixture):
    data = load_fixture("playlist_related.json")
    worker = ytm.Worker(FakeYT(get_playlist=data), "none")
    page = call(worker, "playlist", playlistId="PLAS6y7FS52CSJpn6jTq0kwadLlDECC59x", limit=100, related=True)["result"]
    assert worker.yt.calls[-1][2] == {"limit": 100, "related": True}
    assert [s["key"] for s in page["sections"]] == ["related"]
    related = page["sections"][0]
    assert related["title"] == "Related playlists" and related["layout"] == "carousel"
    assert len(related["items"]) == 10 and all(p["type"] == "playlist" and p["playlistId"] for p in related["items"])
    assert all(p["description"] for p in related["items"]), "the served subtitle stands in for author and count"
    # 28 of the 29 songs it counts came back; one is unavailable.
    assert len(page["tracks"]) == 28 and page["trackCount"] == 29
    assert page["truncated"] is False and page["unavailable"] == 1
    assert sum(1 for t in page["tracks"] if t["explicit"]) == 1
    header = page["header"]
    assert header["author"]["name"] and header["author"]["id"].startswith("UC")
    assert header["year"] == "2023" and header["views"] == 86 and header["duration"] == "2 hours, 14 minutes"
    assert header["privacy"] is None, "not yours: ytmusicapi only guesses PUBLIC"
    assert header["description"] is None and header["collaborators"] is None


def test_a_related_shelf_that_cannot_be_read_never_fails_the_page():
    # Your own empty playlist: ytmusicapi wants a continuation after the
    # suggestions that is not there, and raises before reading the songs.
    def get_playlist(playlist_id, limit=100, related=False):
        if related:
            raise KeyError("Unable to find 'continuations'")
        return playlist_data(2, 2, owned=True)

    worker = ytm.Worker(FakeYT(get_playlist=get_playlist), "ok")
    reply = call(worker, "playlist", playlistId="PLx", related=True)
    assert reply["ok"], reply
    assert [c[2] for c in worker.yt.calls] == [{"limit": 200, "related": True}, {"limit": 200}]
    page = reply["result"]
    assert len(page["tracks"]) == 2 and page["sections"] == []


def test_an_error_that_is_not_the_shelf_still_fails():
    worker = ytm.Worker(FakeYT(get_playlist=KeyError("contents")), "ok")
    reply = call(worker, "playlist", playlistId="PLx", related=True)
    assert reply["ok"] is False and reply["error"]["code"] == "UPSTREAM"
    assert len(worker.yt.calls) == 2


def test_playlist_header_fields_of_your_own():
    data = playlist_data(2, 2, owned=True, privacy="PRIVATE", description="Road trip  ", year="2025", views="1,234",
                         duration="6+ hours", duration_seconds=21600,
                         collaborators={"text": "by Sam and 1 other", "avatars": []})
    page = norm.collection("playlist", data, "PLx", limit=200)
    header = page["header"]
    assert header["privacy"] == "PRIVATE" and header["views"] == 1234 and header["durationSec"] == 21600
    assert header["description"] == "Road trip  " and header["collaborators"] == "by Sam and 1 other"
    assert page["editable"] is True
    assert set(header) == {"description", "privacy", "year", "views", "duration", "durationSec", "author",
                           "collaborators", "artists", "likeStatus", "audioPlaylistId", "explicit", "type"}


# ---- continuations and titles from the raw responses -------------------------------------------


def shelf_page(n, token=None):
    contents = [{"musicResponsiveListItemRenderer": {}} for _ in range(n)]
    if token:
        contents.append({"continuationItemRenderer": {"continuationEndpoint": {"continuationCommand": {"token": token}}}})
    return {"contents": {"twoColumnBrowseResultsRenderer": {"secondaryContents": {"sectionListRenderer": {
        "contents": [{"musicPlaylistShelfRenderer": {"contents": contents}}]}}}}}


def continued(n, token=None):
    items = [{"musicResponsiveListItemRenderer": {}} for _ in range(n)]
    if token:
        items.append({"continuationItemRenderer": {"continuationEndpoint": {"continuationCommand": {"token": token}}}})
    return {"onResponseReceivedActions": [{"appendContinuationItemsAction": {"continuationItems": items}}]}


def carousel(title, size=None):
    data = {"header": {"musicCarouselShelfBasicHeaderRenderer": {"title": {"runs": [{"text": title}]}}}, "contents": []}
    if size:
        data["itemSize"] = size
    return {"musicCarouselShelfRenderer": data}


RELATED = {"continuationContents": {"sectionListContinuation": {"contents": [carousel("More like this")]}}}


class Client:
    """A client ytm_raw can hook: get_playlist sends `pages` through _send_request."""

    def __init__(self, pages, result):
        self.pages = list(pages)
        self.result = result
        self.calls = []

    def _send_request(self, endpoint, body, additionalParams=""):
        return self.pages.pop(0)

    def get_playlist(self, playlist_id, limit=100, related=False):
        self.calls.append((playlist_id, limit, related))
        while self.pages:
            self._send_request("browse", {})
        return self.result


def hooked(pages, result):
    client = raw.install(Client(pages, result))
    return ytm.Worker(client, "ok"), client


def test_a_read_that_ran_out_of_pages_is_whole_whatever_the_counts():
    # Three missing of 250: get_playlist stopped because nothing was left.
    worker, _ = hooked([shelf_page(100, "T1"), RELATED, continued(100, "T2"), continued(47)], playlist_data(247, 250))
    page = call(worker, "playlist", playlistId="PLx", related=True)["result"]
    assert page["truncated"] is False and page["unavailable"] == 3


def test_a_read_that_stopped_at_the_limit_is_truncated_and_names_the_served_shelf():
    data = playlist_data(300, 1126, related=[{"title": "R", "playlistId": "PLr"}])
    worker, client = hooked([shelf_page(100, "T1"), RELATED, continued(100, "T2"), continued(100, "T3")], data)
    page = call(worker, "playlist", playlistId="PLx", related=True)["result"]
    assert client.calls == [("PLx", 200, True)]
    assert page["truncated"] is True
    assert page["sections"][0]["title"] == "More like this", "the served title, not the fallback"


def test_reading_keeps_no_responses():
    reading = collections._Reading()
    with raw.capture(("browse",), each=reading) as cap:
        client = raw.install(Client([shelf_page(3, "T")], {}))
        client._send_request("browse", {})
    assert cap.responses == [] and reading.more is True


# ---- mixes -------------------------------------------------------------------------------------


@pytest.mark.parametrize("playlist_id", ["RDTMAK5uy_mix", "VLRDTMAK5uy_mix", "RDAMVMnbCOAPR33ME"])
def test_a_mix_id_reads_the_watch_playlist(load_fixture, playlist_id):
    worker = ytm.Worker(FakeYT(get_watch_playlist=load_fixture("radio.json")), "none")
    page = call(worker, "playlist", playlistId=playlist_id, limit="all", related=True)["result"]
    stripped = playlist_id[2:] if playlist_id.startswith("VL") else playlist_id
    assert worker.yt.calls == [("get_watch_playlist", (), {"playlistId": stripped, "limit": 50})]
    assert page["kind"] == "mix" and page["id"] == stripped and page["playlistId"] == stripped
    assert len(page["tracks"]) == 50 and page["truncated"] is False and page["editable"] is False
    assert page["subtitle"].startswith("Mix")


def test_curated_playlists_are_not_mixes():
    worker = ytm.Worker(FakeYT(get_playlist=playlist_data(5, 5)), "none")
    assert call(worker, "playlist", playlistId="RDCLAK5uy_curated")["result"]["kind"] == "playlist"
    assert worker.yt.calls[0][0] == "get_playlist"


def test_mix_title_comes_from_the_queue_header():
    response = {"contents": {"singleColumnMusicWatchNextResultsRenderer": {"tabbedRenderer": {
        "watchNextTabbedResultsRenderer": {"tabs": [{"tabRenderer": {"content": {"musicQueueRenderer": {
            "header": {"musicQueueHeaderRenderer": {"title": {"runs": [{"text": "Playing from"}]},
                                                    "subtitle": {"runs": [{"text": "My Supermix"}]}}}}}}}]}}}}}

    class Watch:
        def _send_request(self, endpoint, body, additionalParams=""):
            return response

        def get_watch_playlist(self, playlistId=None, limit=25):
            self._send_request("next", {})
            return {"tracks": songs(60), "playlistId": playlistId}

    worker = ytm.Worker(raw.install(Watch()), "none")
    page = call(worker, "playlist", playlistId="RDTMAK5uy_x")["result"]
    assert page["title"] == "My Supermix" and len(page["tracks"]) == 50


EXPLICIT = [{"musicInlineBadgeRenderer": {"icon": {"iconType": "MUSIC_EXPLICIT_BADGE"},
                                         "accessibilityData": {"accessibilityData": {"label": "Explicit"}}}}]


def panel_video(video_id, badges=None):
    data = {"videoId": video_id, "title": {"runs": [{"text": video_id}]}}
    if badges:
        data["badges"] = badges
    return {"playlistPanelVideoRenderer": data}


def test_mix_songs_keep_the_explicit_badge_of_the_responses():
    first = {"contents": {"singleColumnMusicWatchNextResultsRenderer": {"tabbedRenderer": {
        "watchNextTabbedResultsRenderer": {"tabs": [{"tabRenderer": {"content": {"musicQueueRenderer": {
            "content": {"playlistPanelRenderer": {"contents": [
                panel_video("v0", EXPLICIT),
                panel_video("v1"),
                # A song with a video counterpart: the song is the primary.
                {"playlistPanelVideoWrapperRenderer": {"primaryRenderer": panel_video("v2", EXPLICIT),
                                                       "counterpart": [{"counterpartRenderer": panel_video("w2")}]}},
                panel_video("v3", [{"musicInlineBadgeRenderer": {"icon": {"iconType": "SOMETHING_ELSE"}}}]),
            ]}}}}}}]}}}}}
    more = {"continuationContents": {"playlistPanelContinuation": {"contents": [panel_video("v4", EXPLICIT)]}}}
    assert collections.explicit_videos(first) == {"v0", "v2"}
    assert collections.explicit_videos(more) == {"v4"} and collections.explicit_videos(None) == set()

    class Watch:
        def __init__(self):
            self.pages = [first, more]

        def _send_request(self, endpoint, body, additionalParams=""):
            return self.pages.pop(0)

        def get_watch_playlist(self, playlistId=None, limit=25):
            while self.pages:
                self._send_request("next", {})
            return {"tracks": songs(6), "playlistId": playlistId}

    worker = ytm.Worker(raw.install(Watch()), "none")
    page = call(worker, "playlist", playlistId="RDTMAK5uy_x")["result"]
    assert [t["videoId"] for t in page["tracks"] if t["explicit"]] == ["v0", "v2", "v4"]


# ---- album -------------------------------------------------------------------------------------


def test_album_fixture_keeps_shelves_numbers_header_and_explicit(load_fixture):
    worker = ytm.Worker(FakeYT(get_album=load_fixture("album.json")), "none")
    page = call(worker, "album", browseId="MPREb_yXhSI4FCUo6")["result"]
    assert page["title"] == "OK Computer" and page["kind"] == "album"
    assert [t["trackNumber"] for t in page["tracks"]] == list(range(1, 13))
    header = page["header"]
    assert header["artists"] == [{"name": "Radiohead", "id": "UCr_iyUANcn9OX_yy9piYoLw"}]
    assert header["likeStatus"] == "INDIFFERENT" and header["audioPlaylistId"].startswith("OLAK5uy_")
    assert header["explicit"] is False and header["type"] == "Album" and header["year"] == "1997"
    assert header["description"] and header["duration"]
    assert [(s["key"], s["title"], len(s["items"])) for s in page["sections"]] == [
        ("otherVersions", "Other versions", 1), ("releases", "More releases", 10)]
    other = page["sections"][0]["items"][0]
    assert other["type"] == "album" and other["browseId"].startswith("MPRE") and other["explicit"] is True
    assert other["artists"] and other["artists"][0]["name"] == "Radiohead"
    assert page["truncated"] is False and page["unavailable"] == 0


def test_library_and_list_row_albums_keep_the_explicit_badge():
    import ytm_library as library
    endpoint = {"browseEndpoint": {"browseId": "MPREb_e", "browseEndpointContextSupportedConfigs": {
        "browseEndpointContextMusicConfig": {"pageType": "MUSIC_PAGE_TYPE_ALBUM"}}}}
    grid = {"title": {"runs": [{"text": "E album", "navigationEndpoint": endpoint}]},
            "subtitle": {"runs": [{"text": "Album"}]}, "navigationEndpoint": endpoint,
            "thumbnailRenderer": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": []}}}}
    assert library._album(grid)["explicit"] is False
    assert library._album(dict(grid, subtitleBadges=EXPLICIT))["explicit"] is True
    row = {"flexColumns": [{"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": "E album"}]}}}],
           "navigationEndpoint": endpoint}
    assert raw.item({"musicResponsiveListItemRenderer": row})["explicit"] is False
    value = raw.item({"musicResponsiveListItemRenderer": dict(row, badges=EXPLICIT)})
    assert value["type"] == "album" and value["explicit"] is True
    assert raw.explicit(["not a badge", {}]) is False and raw.explicit(None) is False


def test_album_shelves_take_their_served_titles_by_item_size():
    response = {"contents": {"twoColumnBrowseResultsRenderer": {"secondaryContents": {"sectionListRenderer": {
        "contents": [{"musicShelfRenderer": {"contents": []}},
                     carousel("Otras versiones", "COLLECTION_STYLE_ITEM_SIZE_MEDIUM"),
                     carousel("Lanzamientos para ti", "COLLECTION_STYLE_ITEM_SIZE_SMALL")]}}}}}
    assert collections.album_titles(response) == {"otherVersions": "Otras versiones", "releases": "Lanzamientos para ti"}
    assert collections.album_titles(None) == {}
    page = norm.collection("album", {"title": "A", "tracks": [], "related_recommendations": [
        {"browseId": "MPREb_r", "title": "R"}]}, "MPREb_a", titles={"releases": "Releases for you"})
    assert [(s["key"], s["title"]) for s in page["sections"]] == [("releases", "Releases for you")]


# ---- history -------------------------------------------------------------------------------


def test_history_asks_for_200_by_default():
    played = songs(260, played="Today")
    worker = ytm.Worker(FakeYT(get_history=played, get_account_info={}), "ok")
    assert len(call(worker, "history")["result"]["items"]) == 200
    assert len(call(worker, "history", limit=50)["result"]["items"]) == 50
    assert len(call(worker, "history", limit=500)["result"]["items"]) == 200
    assert call(ytm.Worker(FakeYT(), "none"), "history")["error"]["code"] == "AUTH_REQUIRED"


# ---- per-track fields and tokens -----------------------------------------------------------


TOKENED = {"videoId": "v", "title": "T", "trackNumber": 3, "inLibrary": True, "pinnedToListenAgain": False,
           "feedbackTokens": {"add": "ADD-TOKEN", "remove": "REMOVE-TOKEN"},
           "listenAgainFeedbackTokens": {"pin": "PIN-TOKEN", "unpin": "UNPIN-TOKEN"},
           "feedbackToken": "HISTORY-TOKEN",
           "communityVoteStatus": {"netVoteValue": 12, "status": "VOTE_STATUS_UPVOTED"}}


def test_track_keeps_the_fields_later_slices_act_on():
    track = norm.track(TOKENED)
    assert track["trackNumber"] == 3 and track["inLibrary"] is True and track["pinned"] is False
    assert track["libraryTokens"] == {"add": "ADD-TOKEN", "remove": "REMOVE-TOKEN"}
    assert track["listenAgainTokens"] == {"pin": "PIN-TOKEN", "unpin": "UNPIN-TOKEN"}
    assert track["historyToken"] == "HISTORY-TOKEN"
    assert track["voteStatus"] == {"votes": 12, "status": "VOTE_STATUS_UPVOTED"}
    bare = norm.track({"videoId": "v", "trackNumber": True, "inLibrary": None, "feedbackTokens": {}})
    assert bare["trackNumber"] is None and bare["inLibrary"] is None and bare["libraryTokens"] is None
    assert bare["listenAgainTokens"] is None and bare["historyToken"] is None and bare["voteStatus"] is None


def test_vote_status_enum_becomes_its_value():
    from ytmusicapi.models.content.enums import VoteStatus

    vote = norm.track({"videoId": "v", "communityVoteStatus": {"netVoteValue": 1, "status": VoteStatus.DOWNVOTED}})
    assert vote["voteStatus"] == {"votes": 1, "status": "VOTE_STATUS_DOWNVOTED"}
    assert type(vote["voteStatus"]["status"]) is str


def test_scrub_drops_the_tokens():
    scrubbed = ytm.scrub({"tracks": [dict(TOKENED), norm.track(TOKENED)]})
    raw_track, normalized = scrubbed["tracks"]
    assert "feedbackTokens" not in raw_track and "listenAgainFeedbackTokens" not in raw_track
    assert "feedbackToken" not in raw_track
    assert {"libraryTokens", "listenAgainTokens", "historyToken"}.isdisjoint(normalized)
    assert normalized["trackNumber"] == 3 and normalized["inLibrary"] is True
    assert "TOKEN" not in repr(scrubbed)


def test_the_disk_cache_keeps_no_tokens(tmp_path):
    cache = ytm_cache.DiskCache(tmp_path / "data")
    page = norm.collection("history", {"tracks": [dict(TOKENED)]}, "history")
    page["sections"] = [norm.section("More", [norm.track(TOKENED)], layout="carousel", key="related")]
    assert page["tracks"][0]["historyToken"] == "HISTORY-TOKEN", "the reply itself keeps them"
    cache.put("history", {"limit": 200}, {"items": page["tracks"]})
    cache.put("album", {"browseId": "MPREb_x"}, page)
    assert "TOKEN" not in "".join(p.read_text() for p in cache.dir.glob("*.json"))
    stored = cache.get("album", {"browseId": "MPREb_x"})[0]
    assert stored["tracks"][0]["trackNumber"] == 3 and stored["tracks"][0]["inLibrary"] is True
    assert "historyToken" not in stored["sections"][0]["items"][0]
    assert page["tracks"][0]["historyToken"] == "HISTORY-TOKEN", "the reply is not changed in place"
    clean = {"tracks": [{"videoId": "v"}]}
    assert ytm_cache.strip_tokens(clean) is clean


def test_recorded_fixtures_hold_no_tokens():
    for name in ("album.json", "playlist_related.json"):
        text = (FIXTURES / name).read_text().lower()
        assert "token" not in text and "continuation" not in text, name


# ---- registry and cache ----------------------------------------------------------------------


def test_registry():
    assert {"playlist", "liked", "album", "history"} <= set(collections.METHODS)
    assert {name: ytm.METHODS[name] for name in collections.METHODS} == collections.METHODS
    assert ytm.LANES["playlist"] == "pages" and ytm.LANES["liked"] == "pages"
    # G1: an album over 200 songs reads its audio playlist too.
    assert ytm.LANES["album"] == "pages" and "history" not in ytm.LANES
    assert {"playlist", "liked", "album", "history"} <= ytm.CACHEABLE
    assert ytm.RECORDERS["album"] is collections.RECORDERS["album"]
    assert ytm.RECORDERS["playlist"] is collections.record_playlist
    assert not ({"playlist", "liked", "album", "history"} & set(ytm.CORE_METHODS))
    with pytest.raises(ValueError, match="'album'"):
        ytm.merge_registries([collections, type("Again", (), {"METHODS": {"album": lambda w, p: {}}})])


def test_playlist_recorder_passes_related():
    fake = FakeYT(get_playlist={})
    collections.record_playlist(fake, {"playlistId": "PLx", "related": True})
    assert fake.calls == [("get_playlist", ("PLx",), {"limit": 100, "related": True})]


def test_cache_keys_hold_limit_and_related_and_edits_drop_them_all(tmp_path):
    from ytm_cache import DiskCache

    cache = DiskCache(tmp_path / "data")
    fake = FakeYT(get_playlist=playlist_data(5, 5), add_playlist_items={"status": "STATUS_SUCCEEDED"},
                  get_account_info={})
    worker = ytm.Worker(fake, "ok", cache=cache)
    for params in ({}, {"limit": "all"}, {"related": True}):
        assert call(worker, "playlist", playlistId="PLx", cache="prefer", **params)["cached"] is False
    assert call(worker, "playlist", playlistId="PLx", cache="prefer", limit="all")["cached"] is True
    assert len([c for c in fake.calls if c[0] == "get_playlist"]) == 3
    assert call(worker, "addToPlaylist", playlistId="PLx", videoIds=["v"])["ok"]
    for params in ({}, {"limit": "all"}, {"related": True}):
        assert cache.get("playlist", dict({"playlistId": "PLx"}, **params)) is None
