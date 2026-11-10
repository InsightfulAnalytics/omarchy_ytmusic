"""ytm_podcasts (S8): podcast, episode and channel pages, Episodes for Later,
New episodes, the library's podcasts and channels, and the three edits.

The podcast, episode and channel pages are recorded signed out (Dan Carlin's
Hardcore History, a Lex Fridman episode with chapters, Dan Carlin's channel);
the library pages are private, so they are hand-built in the served shapes.
Every edit is pinned against a fake client: the real ones change the account.
"""

import copy

import pytest
from ytmusicapi.models.content.enums import LikeStatus

import ytm
import ytm_cache as caches
import ytm_podcasts as podcasts
import ytm_raw as raw
from test_library import continuation, page_type, sort_menu, STANDARD
from test_worker import THUMBS

PODCAST = "MPSPPLIJumiHv10VhoiQHAahw2E9ErgZ-jc0tC"
EPISODE = "ZAzWPE9CItg"
CHANNEL = "UC3RcjbuyF5M1U4R62zjE3hg"


class YT:
    """``_send_request`` answers from ``responses`` (one per request, the last
    repeated); other methods from ``methods`` (a value, a callable or an
    exception). Records both."""

    def __init__(self, *responses, **methods):
        self.responses = list(responses)
        self.methods = dict({"get_account_info": {"accountName": "x"}}, **methods)
        self.sent = []
        self.calls = []

    def _send_request(self, endpoint, body, additionalParams=""):
        self.sent.append((endpoint, dict(body)))
        index = min(len(self.sent), len(self.responses)) - 1
        return copy.deepcopy(self.responses[index])

    def __getattr__(self, name):
        if name.startswith("_") or name not in self.methods:
            raise AttributeError(name)

        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            value = self.methods[name]
            if isinstance(value, Exception):
                raise value
            return value(*args, **kwargs) if callable(value) else value

        return method

    def edits(self):
        return [c for c in self.calls if c[0] != "get_account_info"]


def worker_for(*responses, auth="ok", cache=None, **methods):
    return ytm.Worker(raw.install(YT(*responses, **methods)), auth, cache=cache)


def call(worker, method, **params):
    return worker.handle({"id": 8, "method": method, "params": params})


def result(worker, method, **params):
    reply = call(worker, method, **params)
    assert reply["ok"], reply
    return reply["result"]


# ---- builders ----------------------------------------------------------------------------


def runs(*parts):
    """Subtitle runs: strings, or (text, browseId, page type) links, with bullets between."""
    out = []
    for part in parts:
        if out:
            out.append({"text": " \u2022 "})
        if isinstance(part, tuple):
            text, browse_id, kind = part
            out.append({"text": text, "navigationEndpoint": page_type(browse_id, kind)})
        else:
            out.append({"text": part})
    return out


def episode_row(n, subtitle=None, length="1 hr 5 min", percent=None):
    """A musicMultiRowListItemRenderer episode as served on podcast and channel pages."""
    video_id = f"ep{n:09d}"
    progress = {"durationText": {"runs": [{"text": length}]}}
    if percent is not None:
        progress["playbackProgressPercentage"] = percent
    return {"musicMultiRowListItemRenderer": {
        "title": {"runs": [{"text": f"Episode {n}", "navigationEndpoint": page_type("MPED" + video_id,
                                                                                  "NON_MUSIC_AUDIO_TRACK_PAGE")}]},
        "subtitle": {"runs": subtitle if subtitle is not None else runs("12K views", "Jul 31")},
        "thumbnail": THUMBS,
        "onTap": {"watchEndpoint": {"videoId": video_id}},
        "playbackProgress": {"musicPlaybackProgressRenderer": progress}}}


def broadcast_menu(browse_id):
    """A row's menu with its Go to podcast entry (BROADCAST), as served on channel rows."""
    return {"menuRenderer": {"items": [
        {"menuServiceItemRenderer": {"icon": {"iconType": "QUEUE_PLAY_NEXT"}}},
        {"menuNavigationItemRenderer": {"text": {"runs": [{"text": "Go to podcast"}]}, "icon": {"iconType": "BROADCAST"},
                                        "navigationEndpoint": page_type(browse_id, "PODCAST_SHOW_DETAIL_PAGE")}}]}}


def next_data(token):
    return [{"nextContinuationData": {"continuation": token}}] if token else []


def podcast_response(rows, token=None, title="Show", saved=None):
    buttons = []
    if saved is not None:
        buttons.append({"toggleButtonRenderer": {"isToggled": saved, "defaultServiceEndpoint": {
            "likeEndpoint": {"status": "LIKE", "target": {"playlistId": "PLshow"}}}}})
    header = {"musicResponsiveHeaderRenderer": {
        "title": {"runs": [{"text": title}]}, "thumbnail": THUMBS, "buttons": buttons,
        "straplineTextOne": {"runs": [{"text": "Host", "navigationEndpoint": page_type("UChost", "USER_CHANNEL")}]},
        "description": {"musicDescriptionShelfRenderer": {"description": {"runs": [{"text": "About the show"}]}}}}}
    tab = {"tabRenderer": {"content": {"sectionListRenderer": {"contents": [header]}}}}
    shelf = {"musicShelfRenderer": {"contents": rows, "continuations": next_data(token)}}
    return {"contents": {"twoColumnBrowseResultsRenderer": {
        "tabs": [tab], "secondaryContents": {"sectionListRenderer": {"contents": [shelf]}}}}}


def podcast_tile(n):
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": f"Show {n}", "navigationEndpoint": page_type(f"MPSPPL{n:04d}",
                                                                               "PODCAST_SHOW_DETAIL_PAGE")}]},
        "subtitle": {"runs": [{"text": f"Host {n}", "navigationEndpoint": page_type(f"UCh{n}", "USER_CHANNEL")}]},
        "navigationEndpoint": page_type(f"MPSPPL{n:04d}", "PODCAST_SHOW_DETAIL_PAGE"),
        "thumbnailRenderer": THUMBS}}


def auto_tile(browse_id, title, subtitle):
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": title, "navigationEndpoint": page_type(browse_id, "PLAYLIST")}]},
        "subtitle": {"runs": runs(*subtitle)},
        "navigationEndpoint": page_type(browse_id, "PLAYLIST"), "thumbnailRenderer": THUMBS}}


ADD_PODCAST = {"musicTwoRowItemRenderer": {"title": {"runs": [{"text": "Add podcast"}]}, "subtitle": {},
                                           "navigationEndpoint": {"openPopupAction": {}}}}


def library_page(renderer, contents, token=None, menu=None):
    key = "items" if renderer == "gridRenderer" else "contents"
    section = {"contents": [{"itemSectionRenderer": {"contents": [
        {renderer: {key: contents, "continuations": next_data(token)}}]}}]}
    response = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
        {"tabRenderer": {"content": {"sectionListRenderer": section}}}]}}}
    if menu:
        section["header"], response["frameworkUpdates"] = menu
    return response


def channel_row(n, kind="USER_CHANNEL", subtitle="140K subscribers"):
    def column(text):
        return {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": text}]}}}
    return {"musicResponsiveListItemRenderer": {
        "flexColumns": [column(f"Channel {n}"), column(subtitle)],
        "navigationEndpoint": page_type(f"UCc{n:04d}", kind), "thumbnail": THUMBS}}


# ---- podcast -------------------------------------------------------------------------------


def test_a_podcast_lists_its_episodes_as_tracks_of_kind_episode(load_fixture):
    worker = worker_for(load_fixture("podcast.json"))
    page = result(worker, "podcast", browseId=PODCAST)
    assert page["kind"] == "podcast" and page["id"] == PODCAST
    assert page["title"] == "Dan Carlin's Hardcore History"
    assert page["header"]["podcastId"] == PODCAST[4:], "the id is the browseId without MPSP"
    assert page["header"]["author"] == {"name": "Dan Carlin", "id": CHANNEL}
    assert page["header"]["saved"] is False, "signed out"
    [section] = page["sections"]
    assert section["title"] == "Episodes" and section["key"] == "episodes"
    episodes = section["items"]
    assert len(episodes) == 20
    for episode in episodes:
        assert episode["type"] == "track" and episode["kind"] == "episode"
        assert episode["browseId"] == "MPED" + episode["videoId"]
        assert episode["podcast"] == {"name": page["title"], "id": PODCAST}
        assert episode["artists"] == [episode["podcast"]], "the podcast stands in for the artist"
        assert isinstance(episode["durationSec"], int) and episode["durationSec"] > 600
        assert episode["date"] and "views" not in episode["date"], "the date is the subtitle's last part"
        assert episode["progress"] == 0 and episode["progressText"] is None, "unplayed: its length is no played text"
    assert worker.yt.sent[0] == ("browse", {"browseId": PODCAST})


@pytest.mark.parametrize("params", [{"browseId": PODCAST}, {"playlistId": PODCAST[4:]}, {"browseId": PODCAST[4:]}])
def test_a_podcast_is_read_by_its_mpsp_browse_whatever_id_comes(load_fixture, params):
    worker = worker_for(load_fixture("podcast.json"))
    page = result(worker, "podcast", **params)
    assert page["id"] == PODCAST and page["header"]["podcastId"] == PODCAST[4:]
    assert worker.yt.sent[0][1] == {"browseId": PODCAST}


def test_a_podcast_reads_on_by_its_shelf_continuation_and_a_cursor_reads_the_rest(tmp_path):
    first = podcast_response([episode_row(i) for i in range(20)], "T1", saved=True)
    more = continuation("musicShelfRenderer", [episode_row(i) for i in range(20, 40)], "T2")
    rest = continuation("musicShelfRenderer", [episode_row(i, percent=37) for i in range(40, 45)])
    cache = caches.DiskCache(tmp_path / "data")
    worker = worker_for(first, more, rest, cache=cache)
    page = result(worker, "podcast", browseId="MPSPPLshow", cache="refresh")
    assert len(page["sections"][0]["items"]) == podcasts.PAGE_EPISODES
    assert page["cursor"] and page["hasMore"]
    assert page["header"] == {"browseId": "MPSPPLshow", "podcastId": "PLshow", "author": {"name": "Host", "id": "UChost"},
                              "description": "About the show", "saved": True}
    assert worker.yt.sent[1] == ("browse", {"browseId": "MPSPPLshow"}), "the same browse, by ctoken"
    later = result(worker, "podcast", browseId="MPSPPLshow", cursor=page["cursor"])
    episodes = later["sections"][0]["items"]
    assert [e["videoId"] for e in episodes] == [f"ep{i:09d}" for i in range(40, 45)]
    assert later["cursor"] is None and later["sections"][0]["title"] is None
    assert episodes[0]["progress"] == 37
    assert episodes[0]["podcast"] == {"name": "Show", "id": "MPSPPLshow"}, "the title from the cached first page"


def test_a_podcast_without_its_header_is_not_found():
    worker = worker_for({"contents": {}})
    assert call(worker, "podcast", browseId="MPSPPLx")["error"]["code"] == "NOT_FOUND"
    assert call(worker, "podcast", browseId="MPSP")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "podcast")["error"]["code"] == "BAD_REQUEST"


# ---- episode -------------------------------------------------------------------------------


def test_an_episode_page_has_its_header_and_chapters_from_timestamp_runs(load_fixture):
    worker = worker_for(load_fixture("episode.json"))
    page = result(worker, "episode", videoId=EPISODE)
    assert worker.yt.sent == [("browse", {"browseId": "MPED" + EPISODE})]
    header = page["header"]
    assert header["title"].endswith("Lex Fridman Podcast #503")
    assert header["show"] == {"name": "Lex Fridman Podcast", "id": "MPSPPLrAXtmErZgOdP_8GztsuKi9nrraNbKKp4"}
    assert header["date"] == "Oct 26" and "views" in header["subtitle"]
    assert header["duration"] == "3 hr 34 min" and header["durationSec"] == 3 * 3600 + 34 * 60
    assert header["saved"] is False and header["progressPercentage"] == 0
    assert header["progressText"] is None and header["resumeSec"] is None, "served: its length, nothing played"
    assert header["episode"]["progressText"] is None
    assert header["description"].startswith("Ankur Moitra")
    assert header["episode"]["kind"] == "episode" and header["episode"]["videoId"] == EPISODE
    assert header["episode"]["podcast"] == header["show"]
    [section] = page["sections"]
    assert section["title"] == "Chapters" and section["key"] == "chapters"
    chapters = section["items"]
    assert len(chapters) == 15
    assert chapters[0] == {"type": "chapter", "title": "Introduction", "seconds": 0, "time": "0:00", "videoId": EPISODE}
    assert chapters[1]["seconds"] == 116 and chapters[1]["title"] == "Crisis moment in mathematics"
    assert chapters[4]["seconds"] == 3956 and chapters[4]["time"] == "1:05:56"
    assert [c["seconds"] for c in chapters] == sorted(c["seconds"] for c in chapters)
    links = [r for r in header["descriptionRuns"] if "url" in r]
    assert links and all("youtube.com/redirect" not in r["url"] for r in links)
    assert any(r.get("seconds") == 116 for r in header["descriptionRuns"])


def stamp(text, seconds=None, video_id="vid00000001"):
    watch = {"videoId": video_id}
    if seconds is not None:
        watch["startTimeSeconds"] = seconds
    return {"text": text, "navigationEndpoint": {"watchEndpoint": watch}}


def test_chapters_take_the_text_after_or_before_their_timestamp():
    runs_ = [{"text": "Chapters:\n"}, stamp("00:00"), {"text": " - Intro\n"},
             stamp("1:30", 90), {"text": " \u2013 Part one: the start\nmore words\n"},
             {"text": "Outro "}, stamp("(2:00:05)", 7205), {"text": "\n"},
             stamp("1:30", 90), {"text": " again\n"},
             stamp("9:99", 30, video_id="other000001"), {"text": " another video\n"}]
    found = podcasts.chapters(runs_, "vid00000001")
    assert [(c["seconds"], c["title"]) for c in found] == [(0, "Intro"), (90, "Part one: the start"), (7205, "Outro")]
    assert podcasts.chapters([{"text": "no timestamps"}], "vid00000001") == []


def test_an_episode_without_chapters_has_no_sections(load_fixture):
    response = load_fixture("episode.json")
    shelf = podcasts._shelf(response, ("musicDescriptionShelfRenderer",))
    shelf["description"]["runs"] = [{"text": "Just words"}]
    page = podcasts.build_episode(EPISODE, response)
    assert page["sections"] == [] and page["header"]["description"] == "Just words"
    worker = worker_for({"contents": {}})
    assert call(worker, "episode", videoId="MPED" + EPISODE)["error"]["code"] == "NOT_FOUND"
    assert worker.yt.sent[0][1] == {"browseId": "MPED" + EPISODE}, "an MPED id is read as the episode"


# ---- channel -------------------------------------------------------------------------------


def test_a_channel_has_its_rows_keyed_by_what_they_hold_and_episodes_see_all(load_fixture):
    worker = worker_for(load_fixture("channel.json"))
    page = result(worker, "channel", channelId=CHANNEL)
    assert page["kind"] == "channel" and page["title"] == "Dan Carlin" and page["round"]
    assert page["header"]["channelId"] == CHANNEL and page["header"]["subscribeId"] == CHANNEL
    assert page["header"]["subscribed"] is False and page["header"]["subscribers"].endswith("subscribers")
    keys = [s["key"] for s in page["sections"]]
    assert keys[:2] == ["episodes", "podcasts"]
    episodes, shows = page["sections"][0], page["sections"][1]
    assert episodes["items"] and all(e["kind"] == "episode" for e in episodes["items"])
    assert episodes["more"]["browseId"] == CHANNEL and episodes["more"]["params"]
    assert episodes["more"]["pageType"] == podcasts.CHANNEL_EPISODES, "See all opens channelEpisodes"
    assert shows["items"][0]["type"] == "podcast" and shows["items"][0]["browseId"].startswith("MPSP")
    # The rows link no podcast in their subtitle ("241K views • Jul 31"): their
    # menu's Go to podcast does, titled by the Podcasts row.
    show = {"name": shows["items"][0]["title"], "id": PODCAST}
    for episode in episodes["items"]:
        assert episode["podcast"] == show and episode["artists"] == [show], episode["title"]
        assert episode["artistText"] == "Dan Carlin's Hardcore History"
        assert episode["progressText"] is None


def test_a_channel_episode_whose_podcast_is_not_listed_takes_the_channel_name():
    rows = [episode_row(1), episode_row(2)]
    rows[0]["musicMultiRowListItemRenderer"]["menu"] = broadcast_menu("MPSPPLother")
    sections = podcasts.channel_sections("UCc", [{"musicShelfRenderer": {"title": {"runs": [{"text": "Latest"}]},
                                                                        "contents": rows}}], "The channel")
    first, second = sections[0]["items"]
    assert first["podcast"] == {"name": "The channel", "id": "MPSPPLother"}
    assert second["podcast"] is None and second["artistText"] == "", "no menu link: no podcast"
    assert podcasts.menu_podcast({"menu": broadcast_menu("UCnotapodcast")}) is None


def test_channel_episodes_reads_the_whole_grid():
    grid = {"gridRenderer": {"items": [episode_row(i) for i in range(46)]}}
    response = {"header": {"musicHeaderRenderer": {"title": {"runs": [{"text": "Dan Carlin"}]}}},
                "contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
                    "sectionListRenderer": {"contents": [grid]}}}}]}}}
    worker = worker_for(response)
    page = result(worker, "channelEpisodes", channelId=CHANNEL, params="P")
    assert worker.yt.sent == [("browse", {"browseId": CHANNEL, "params": "P"})]
    assert page["kind"] == "channelEpisodes" and page["title"] == "Dan Carlin"
    assert len(page["sections"][0]["items"]) == 46 and page["cursor"] is None
    assert call(worker, "channelEpisodes", channelId=CHANNEL)["error"]["code"] == "BAD_REQUEST"


def test_channel_episodes_name_the_podcast_their_menu_goes_to(tmp_path, load_fixture):
    # As served: "241K views • Jul 31", the podcast only in the row's menu.
    rows = [episode_row(i) for i in range(3)]
    for row in rows[:2]:
        row["musicMultiRowListItemRenderer"]["menu"] = broadcast_menu(PODCAST)
    grid = {"gridRenderer": {"items": rows}}
    response = {"header": {"musicHeaderRenderer": {"title": {"runs": [{"text": "Dan Carlin"}]}}},
                "contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
                    "sectionListRenderer": {"contents": [grid]}}}}]}}}
    # No channel page read before: the channel's name.
    worker = worker_for(response, cache=caches.DiskCache(tmp_path / "none"))
    items = result(worker, "channelEpisodes", channelId=CHANNEL, params="P")["sections"][0]["items"]
    assert items[0]["podcast"] == {"name": "Dan Carlin", "id": PODCAST}
    assert items[2]["podcast"] is None, "a row without the menu link"
    # The channel page read first (See all is opened from it): the podcast's title.
    cache = caches.DiskCache(tmp_path / "data")
    worker = worker_for(load_fixture("channel.json"), response, cache=cache)
    assert result(worker, "channel", channelId=CHANNEL, cache="refresh")["sections"]
    items = result(worker, "channelEpisodes", channelId=CHANNEL, params="P")["sections"][0]["items"]
    assert items[0]["podcast"] == {"name": "Dan Carlin's Hardcore History", "id": PODCAST}
    assert items[0]["artistText"] == "Dan Carlin's Hardcore History"


# ---- Episodes for Later and New episodes ---------------------------------------------------------


def saved_track(n):
    return {"videoId": f"se{n:09d}", "title": f"Saved {n}", "setVideoId": f"SET{n}", "duration_seconds": 8210,
            "videoType": None, "thumbnails": [], "album": None,
            "artists": [{"name": "2 hr 16 min left", "id": None}, {"name": "May 25", "id": None},
                        {"name": "Huberman Lab", "id": "MPSPPLhub"}]}


def test_the_time_left_gives_the_second_to_resume_at():
    def progress(percent, left=None):
        value = {"playbackProgressPercentage": percent, "durationText": {"runs": runs("2 hr 16 min")}}
        if left:
            value["playbackProgressText"] = {"runs": [{"text": " \u2022 "}, {"text": left}]}
        return value
    # Served for an episode played for under a minute: 10%, and all of it left.
    assert podcasts.progress_of(progress(10, "2 hr 16 min left"), 8160) == {
        "progress": 10, "progressText": "2 hr 16 min left", "resumeSec": 0}
    assert podcasts.progress_of(progress(60, "54 min left"), 8160)["resumeSec"] == 8160 - 54 * 60
    assert podcasts.progress_of(progress(37), 8160) == {"progress": 37, "progressText": None, "resumeSec": None}
    assert podcasts.progress_of(progress(37, "noch 5 Min."), 8160)["resumeSec"] is None, "another language: the percent"
    assert podcasts.progress_of({}, None) == {"progress": None, "progressText": None, "resumeSec": None}
    # Served for an episode nobody started: 0%, and its length as the progress text.
    assert podcasts.progress_of(progress(0, "2 hr 16 min"), 8160) == {
        "progress": 0, "progressText": None, "resumeSec": None}
    assert podcasts.progress_of(progress(None, "2 hr 16 min"), 8160)["progressText"] is None, "its length"
    assert podcasts.progress_of(progress(0, "1 hr"), 8160)["progressText"] is None, "0%: nothing played"
    assert podcasts.progress_of(progress(100, "Played"), 8160)["progressText"] == "Played"
    row = episode_row(1, percent=60)
    row["musicMultiRowListItemRenderer"]["playbackProgress"]["musicPlaybackProgressRenderer"]["playbackProgressText"] = {
        "runs": [{"text": "5 min left"}]}
    item = podcasts.episode_item(row["musicMultiRowListItemRenderer"])
    assert item["resumeSec"] == 3900 - 300 and item["progressText"] == "5 min left" and item["progress"] == 60


def test_saved_episodes_keep_their_set_video_id_and_name_their_podcast():
    worker = worker_for(get_saved_episodes={"title": "Episodes for Later", "trackCount": 2,
                                            "tracks": [saved_track(1), saved_track(2)]})
    page = result(worker, "savedEpisodes")
    assert worker.yt.edits() == [("get_saved_episodes", (), {"limit": None})]
    assert page["kind"] == "episodesForLater" and page["playlistId"] == "SE" and page["trackCount"] == 2
    first = page["tracks"][0]
    assert first["kind"] == "episode" and first["setVideoId"] == "SET1" and first["browseId"] == "MPEDse000000001"
    assert first["podcast"] == {"name": "Huberman Lab", "id": "MPSPPLhub"}
    assert first["artists"] == [first["podcast"]] and first["date"] == "May 25"
    assert first["progressText"] == "2 hr 16 min left" and first["resumeSec"] == 8210 - 8160
    assert call(worker_for(auth="none"), "savedEpisodes")["error"]["code"] == "AUTH_REQUIRED"


def test_new_episodes_without_saved_podcasts_is_an_empty_page():
    worker = worker_for(get_episodes_playlist=KeyError("musicResponsiveHeaderRenderer"))
    page = result(worker, "newEpisodes")
    assert page["kind"] == "newEpisodes" and page["sections"] == []
    assert page["message"] == "No new episodes"
    assert call(worker_for(auth="none"), "newEpisodes")["error"]["code"] == "AUTH_REQUIRED"


def test_new_episodes_read_the_raw_shelf():
    response = podcast_response([episode_row(1, runs(("Show", "MPSPPLs", "PODCAST_SHOW_DETAIL_PAGE"), "Jul 2"))])

    def served(playlist_id):
        assert playlist_id == "RDPN"
        worker.yt._send_request("browse", {"browseId": "VLRDPN"})
        return {"title": "New episodes", "episodes": []}

    worker = worker_for(response, get_episodes_playlist=served)
    page = result(worker, "newEpisodes")
    [episode] = page["sections"][0]["items"]
    assert episode["podcast"] == {"name": "Show", "id": "MPSPPLs"} and episode["date"] == "Jul 2"


# ---- the library's podcasts and channels ------------------------------------------------------


def test_library_podcasts_skip_the_add_tile_and_lift_the_auto_playlists():
    tiles = [ADD_PODCAST, auto_tile("VLSE", "Episodes for Later", ("Auto playlist", "2 episodes")),
             auto_tile("VLRDPN", "New episodes", ("Auto playlist",)), podcast_tile(1), podcast_tile(2)]
    worker = worker_for(library_page("gridRenderer", tiles, menu=sort_menu(podcasts.PODCASTS_BROWSE_ID, STANDARD)))
    page = result(worker, "libraryPodcasts")
    assert worker.yt.sent[0] == ("browse", {"browseId": podcasts.PODCASTS_BROWSE_ID})
    items = page["sections"][0]["items"]
    assert [i["browseId"] for i in items] == ["MPSPPL0001", "MPSPPL0002"], "Add podcast and the auto playlists go"
    assert items[0]["type"] == "podcast" and items[0]["podcastId"] == "PL0001"
    assert page["header"]["episodesForLater"] == {"count": 2, "subtitle": "Auto playlist \u2022 2 episodes"}
    assert page["header"]["newEpisodes"]["count"] is None
    assert page["orders"] and page["kind"] == "libraryPodcasts"
    assert call(worker_for(auth="none"), "libraryPodcasts")["error"]["code"] == "AUTH_REQUIRED"


def test_library_channels_are_channels_or_artists_by_page_type_and_page_on():
    rows = [channel_row(i) for i in range(24)] + [channel_row(24, "ARTIST", "2M subscribers")]
    worker = worker_for(library_page("musicShelfRenderer", rows, "C1"),
                        continuation("musicShelfRenderer", [channel_row(i) for i in range(25, 50)], "C2"),
                        continuation("musicShelfRenderer", [channel_row(i) for i in range(50, 74)]))
    page = result(worker, "libraryChannels", order="a_to_z")
    items = page["sections"][0]["items"]
    assert len(items) == 50 and page["cursor"]
    assert items[0] == dict(items[0], type="channel", channelId="UCc0000", name="Channel 0", subtitle="140K subscribers")
    assert items[24]["type"] == "artist" and items[24]["subtitle"] == "2M subscribers"
    rest = result(worker, "libraryChannels", order="a_to_z", cursor=page["cursor"])
    assert len(items) + len(rest["sections"][0]["items"]) == 74 and rest["cursor"] is None


# ---- edits ---------------------------------------------------------------------------------


@pytest.mark.parametrize("save, status", [(True, LikeStatus.LIKE), (False, LikeStatus.INDIFFERENT)])
def test_save_podcast_rates_its_playlist_without_mpsp(save, status):
    worker = worker_for(rate_playlist={})
    reply = call(worker, "savePodcast", podcastId="MPSPPLshow", save=save)
    assert reply["ok"], reply
    assert worker.yt.edits() == [("rate_playlist", ("PLshow", status), {})]
    assert reply["result"] == {"podcastId": "PLshow", "browseId": "MPSPPLshow", "saved": save}


def test_save_episode_adds_it_to_episodes_for_later():
    worker = worker_for(add_playlist_items={"status": "STATUS_SUCCEEDED", "playlistEditResults": [
        {"videoId": "ep000000001", "setVideoId": "S1"}]})
    reply = call(worker, "saveEpisode", videoId="MPEDep000000001")
    assert reply["ok"], reply
    assert worker.yt.edits() == [("add_playlist_items", ("SE", ["ep000000001"]), {})]
    assert reply["result"] == {"videoId": "ep000000001", "setVideoId": "S1", "saved": True}
    worker = worker_for(add_playlist_items={"status": "STATUS_FAILED"})
    assert call(worker, "saveEpisode", videoId="ep000000001")["error"]["code"] == "UPSTREAM"


def test_unsave_episode_removes_its_entry_by_set_video_id():
    worker = worker_for(remove_playlist_items="STATUS_SUCCEEDED")
    reply = call(worker, "unsaveEpisode", videoId="ep000000001", setVideoId="S1")
    assert reply["ok"], reply
    assert worker.yt.edits() == [("remove_playlist_items", ("SE", [{"videoId": "ep000000001", "setVideoId": "S1"}]),
                                  {})]
    worker = worker_for(remove_playlist_items="STATUS_SUCCEEDED")
    assert call(worker, "unsaveEpisode", videoId="ep000000001")["error"]["code"] == "BAD_REQUEST"
    assert worker.yt.calls == [], "nothing reaches YouTube"


@pytest.mark.parametrize("method, params", [
    ("savePodcast", {"podcastId": "PLx", "save": "yes"}),
    ("savePodcast", {"save": True}),
    ("saveEpisode", {}),
    ("saveEpisode", {"videoId": "MPED"}),
    ("unsaveEpisode", {"setVideoId": "S1"}),
])
def test_edit_bad_requests(method, params):
    worker = worker_for(rate_playlist={}, add_playlist_items={}, remove_playlist_items={})
    assert call(worker, method, **params)["error"]["code"] == "BAD_REQUEST"
    assert worker.yt.calls == []


@pytest.mark.parametrize("method, params", [
    ("savePodcast", {"podcastId": "PLx", "save": True}),
    ("saveEpisode", {"videoId": "ep000000001"}),
    ("unsaveEpisode", {"videoId": "ep000000001", "setVideoId": "S1"}),
])
def test_edits_need_an_account(method, params):
    worker = worker_for(auth="none", rate_playlist={}, add_playlist_items={}, remove_playlist_items={})
    assert call(worker, method, **params)["error"]["code"] == "AUTH_REQUIRED"
    assert worker.yt.edits() == []


def test_the_edits_mutate_and_drop_what_they_make_stale(tmp_path):
    for name in ("savePodcast", "saveEpisode", "unsaveEpisode"):
        assert name in ytm.MUTATES and ytm.INVALIDATES[name], name
    for name in ("podcast", "episode", "channel", "channelEpisodes", "savedEpisodes", "newEpisodes",
                 "libraryPodcasts", "libraryChannels"):
        assert name in ytm.CACHEABLE and name not in ytm.MUTATES and ytm.LANES[name] == "pages", name
    for name in ("saveEpisode", "unsaveEpisode"):
        targets = {t[0] for t in ytm.INVALIDATES[name]}
        assert {"savedEpisodes", "libraryPodcasts", "episode"} <= targets, name
        assert "podcast" not in targets, "a podcast page holds no saved state of its episodes"
    assert ("channel", "pageChannelId", "channelId") in ytm.INVALIDATES["subscribe"]
    assert ("libraryChannels", None, None) in ytm.INVALIDATES["subscribe"]
    cache = caches.DiskCache(tmp_path / "data")
    cache.put("podcast", {"browseId": "MPSPPLshow"}, {"type": "page"})
    cache.put("podcast", {"browseId": "MPSPPLother"}, {"type": "page"})
    cache.put("libraryPodcasts", {}, {"type": "page"})
    worker = worker_for(cache=cache, rate_playlist={})
    assert call(worker, "savePodcast", podcastId="MPSPPLshow", save=True)["ok"]
    assert cache.get("podcast", {"browseId": "MPSPPLshow"}) is None
    assert cache.get("podcast", {"browseId": "MPSPPLother"}) is not None
    assert cache.get("libraryPodcasts", {}) is None
    cache.put("episode", {"videoId": "ep000000001"}, {"type": "page"})
    cache.put("episode", {"videoId": "ep000000002"}, {"type": "page"})
    cache.put("savedEpisodes", {}, {"type": "collection"})
    worker = worker_for(cache=cache, add_playlist_items={"status": "STATUS_SUCCEEDED"})
    assert call(worker, "saveEpisode", videoId="ep000000001")["ok"]
    assert cache.get("episode", {"videoId": "ep000000001"}) is None
    assert cache.get("episode", {"videoId": "ep000000002"}) is not None
    assert cache.get("savedEpisodes", {}) is None


def test_a_podcast_cursor_page_names_its_podcast_after_an_episode_is_saved(tmp_path):
    first = podcast_response([episode_row(i) for i in range(40)], "T1")
    rest = continuation("musicShelfRenderer", [episode_row(i) for i in range(40, 45)])
    cache = caches.DiskCache(tmp_path / "data")
    worker = worker_for(first, rest, cache=cache,
                        add_playlist_items={"status": "STATUS_SUCCEEDED", "playlistEditResults": []})
    page = result(worker, "podcast", browseId="MPSPPLshow", cache="refresh")
    assert page["cursor"]
    assert call(worker, "saveEpisode", videoId="ep000000003")["ok"]
    later = result(worker, "podcast", browseId="MPSPPLshow", cursor=page["cursor"])
    assert later["sections"][0]["items"][0]["podcast"] == {"name": "Show", "id": "MPSPPLshow"}


def test_recorders_are_registered_and_trim_dashes_and_redirects():
    for name in ("podcast", "episode", "channel"):
        assert ytm.RECORDERS[name].__module__ == "ytm_podcasts"
    trimmed = podcasts.trim({"a": "x \u2013 y \u2014 z", "menu": {}, "thumbnails": [1, 2, 3],
                             "urlEndpoint": {"url": "https://www.youtube.com/redirect?event=x&q=https%3A%2F%2Fa.b%2F"}})
    assert trimmed == {"a": "x - y - z", "thumbnails": [3], "urlEndpoint": {"url": "https://a.b/"}}
    # A row's menu keeps only its link to the podcast (menu_podcast reads it).
    menu = broadcast_menu(PODCAST)
    menu["menuRenderer"]["items"][1]["menuNavigationItemRenderer"]["clickTrackingParams"] = "x"
    kept = podcasts.trim({"menu": menu})["menu"]["menuRenderer"]["items"]
    assert len(kept) == 1 and "clickTrackingParams" not in kept[0]["menuNavigationItemRenderer"]
    assert podcasts.menu_podcast({"menu": podcasts.trim({"menu": menu})["menu"]}) == PODCAST


@pytest.mark.parametrize("method, browse_id", [("libraryPodcasts", podcasts.PODCASTS_BROWSE_ID),
                                               ("libraryChannels", podcasts.CHANNELS_BROWSE_ID)])
def test_a_browse_page_resort_drops_the_list_read_from_that_page(tmp_path, method, browse_id):
    # YouTube saves the order of these pages too (ytm_pages.ORDER_DEPENDENTS).
    from test_library import browse, resorted
    cache = caches.DiskCache(tmp_path / "data")
    for name in ("libraryPodcasts", "libraryChannels", "libraryAlbums"):
        cache.put(name, {}, {"type": "page"})
    worker = worker_for(browse("gridRenderer", [podcast_tile(1)], None, sort_menu(browse_id, STANDARD)),
                        resorted("gridRenderer", [podcast_tile(2)]), cache=cache)
    assert call(worker, "browsePage", browseId=browse_id, order=1, cache="refresh")["ok"]
    for name in ("libraryPodcasts", "libraryChannels", "libraryAlbums"):
        assert (cache.get(name, {}) is None) == (name == method), name
