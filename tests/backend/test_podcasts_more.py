"""G3 (podcasts more): a podcast's episode orders and filters, episode rows
read by S8's reader everywhere (artist pages, See all grids), Latest
episodes' See all on an artist page, a channel's avatar, and artist pages
YouTube serves with a header only.

Rows and headers are trimmed from anonymous reads (Trevor Jackson's Latest
episodes, Brandon Sanderson's header-only page); the podcast is the S8
fixture with stand-in reload tokens (the recorder scrubs the served ones).
"""

import copy

import pytest

import ytm
import ytm_artist as artist
import ytm_cache as caches
import ytm_podcasts as podcasts
import ytm_raw as raw
from test_artist import ArtistYT
from test_library import page_type
from test_podcasts import PODCAST, episode_row, next_data, result, worker_for

TREVOR = "UCUwDqhJmW41JlLaAgAjWeeA"
SANDERSON = "UCgS1xjEPl2ZoWBaLApLWpWA"
THUMB = {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
    {"url": "https://i.ytimg.com/vi/BVtNjTbzIAw/hq720.jpg", "width": 853, "height": 479}]}}}


# ---- episode rows everywhere ------------------------------------------------------------


def trevor_episode():
    """One of Trevor Jackson's 'Latest episodes' cards, as served (trimmed):
    the view count first in its subtitle, its length in the progress
    renderer, no menu link to a podcast."""
    return {"musicMultiRowListItemRenderer": {
        "thumbnail": THUMB,
        "onTap": {"watchEndpoint": {"videoId": "BVtNjTbzIAw", "index": 0, "watchEndpointMusicSupportedConfigs": {
            "watchEndpointMusicConfig": {"musicVideoType": "MUSIC_VIDEO_TYPE_PODCAST_EPISODE"}}}},
        "subtitle": {"runs": [{"text": "2.9K views"}, {"text": " • "}, {"text": "Apr 10, 2025"}]},
        "playbackProgress": {"musicPlaybackProgressRenderer": {
            "playbackProgressPercentage": 0,
            "playbackProgressText": {"runs": [{"text": " • "}, {"text": "56 min"}]},
            "durationText": {"runs": [{"text": " • "}, {"text": "56 min"}]},
            "playedText": {"runs": [{"text": " • "}, {"text": "Played"}]}}},
        "title": {"runs": [{"text": "THE SCENIC ROUTE (the BORNART way) ep. 014",
                            "navigationEndpoint": page_type("MPEDBVtNjTbzIAw", "NON_MUSIC_AUDIO_TRACK_PAGE")}]},
        "description": {"runs": [{"text": "we're back!"}]},
        "menu": {"menuRenderer": {"items": [
            {"menuServiceItemRenderer": {"text": {"runs": [{"text": "Play next"}]},
                                         "icon": {"iconType": "QUEUE_PLAY_NEXT"}}}]}},
    }}


def artist_carousel(title, items, params="EPISODES_PARAMS", kind="ARTIST"):
    endpoint = {"browseEndpoint": {"browseId": TREVOR, "params": params, "browseEndpointContextSupportedConfigs": {
        "browseEndpointContextMusicConfig": {"pageType": f"MUSIC_PAGE_TYPE_{kind}"}}}}
    return {"musicCarouselShelfRenderer": {
        "header": {"musicCarouselShelfBasicHeaderRenderer": {
            "title": {"runs": [{"text": title, "navigationEndpoint": endpoint}]},
            "moreContentButton": {"buttonRenderer": {"text": {"runs": [{"text": "More"}]},
                                                     "navigationEndpoint": endpoint}}}},
        "contents": items}}


def test_an_artist_pages_episodes_carry_their_date_and_length_not_the_view_count():
    page = artist.build(TREVOR, {"name": "Trevor Jackson", "channelId": TREVOR},
                        [artist_carousel("Latest episodes", [trevor_episode()])])
    [section] = page["sections"]
    [episode] = section["items"]
    assert (episode["type"], episode["kind"], episode["videoId"]) == ("track", "episode", "BVtNjTbzIAw")
    assert episode["date"] == "Apr 10, 2025", "the subtitle's last part, not '2.9K views'"
    assert episode["durationSec"] == 3360
    assert episode["progress"] == 0 and episode["progressText"] is None and episode["resumeSec"] is None
    assert episode["browseId"] == "MPEDBVtNjTbzIAw"
    assert episode["podcast"] is None, "the card names no podcast (its menu has no Go to podcast)"
    assert episode["description"] == "we're back!"
    assert section["more"]["browseId"] == TREVOR and section["more"]["params"] == "EPISODES_PARAMS"
    assert section["more"]["pageType"] == artist.CHANNEL_EPISODES == podcasts.CHANNEL_EPISODES, \
        "See all opens the channel's episode grid, not a browse page"


def test_only_an_episode_row_on_the_artists_own_channel_gets_the_episode_grid():
    playlist = {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": "TMix", "navigationEndpoint": page_type("VLPLx", "PLAYLIST")}]},
        "navigationEndpoint": page_type("VLPLx", "PLAYLIST"), "thumbnailRenderer": THUMB}}
    rows = [artist_carousel("Playlists by Trevor Jackson", [playlist], params="PLAYLISTS_PARAMS"),
            artist_carousel("Latest episodes", [trevor_episode()], params=None)]
    page = artist.build(TREVOR, {"name": "Trevor Jackson", "channelId": TREVOR}, rows)
    playlists = page["sections"][0]
    assert playlists["more"]["pageType"] == "MUSIC_PAGE_TYPE_ARTIST", "playlists stay a browse page"
    assert page["sections"][1]["more"] is None, "no params: no See all back to the artist itself"


def test_ytm_raw_reads_every_episode_row_with_the_podcast_reader():
    item = raw.item(trevor_episode())
    assert item == podcasts.episode_item(trevor_episode()["musicMultiRowListItemRenderer"])
    assert item["date"] == "Apr 10, 2025" and item["durationSec"] == 3360
    grid = {"gridRenderer": {"items": [trevor_episode()]}}
    assert raw.parse_row(grid)["items"][0]["date"] == "Apr 10, 2025", "a See all grid of episodes too"


@pytest.mark.parametrize("text,expected", [
    (None, None), ("  ", None), ("short one", "short one"),
    ("word " * 200, ("word " * 80).strip()),
])
def test_an_episode_keeps_the_start_of_its_description(text, expected):
    assert podcasts.description_start(text) == expected
    long = podcasts.description_start("x" * 1000)
    assert long == "x" * podcasts.DESCRIPTION_MAX, "no space: cut at the limit"
    row = episode_row(1)
    row["musicMultiRowListItemRenderer"]["description"] = {"runs": [{"text": "Guest name, "}, {"text": "link"},
                                                                     {"text": " and more"}]}
    assert podcasts.episode_item(row["musicMultiRowListItemRenderer"])["description"] == "Guest name, link and more", \
        "every run (ytmusicapi reads the first only)"


# ---- the channel's avatar -----------------------------------------------------------------


def test_a_channel_page_shows_the_avatar_not_the_banner(load_fixture):
    response = load_fixture("channel.json")
    header = response["header"]["musicVisualHeaderRenderer"]
    avatar = header["foregroundThumbnail"]["musicThumbnailRenderer"]["thumbnail"]["thumbnails"][-1]["url"]
    banner = header["thumbnail"]["musicThumbnailRenderer"]["thumbnail"]["thumbnails"][-1]["url"]
    page = podcasts.build_channel("UC3RcjbuyF5M1U4R62zjE3hg", response)
    assert page["thumbLarge"].split("=")[0] == avatar.split("=")[0]
    assert page["thumbLarge"].split("=")[0] != banner.split("=")[0]
    del header["foregroundThumbnail"]
    page = podcasts.build_channel("UC3RcjbuyF5M1U4R62zjE3hg", response)
    assert page["thumbLarge"].split("=")[0] == banner.split("=")[0], "the banner when there is no avatar"


# ---- header-only artist pages ---------------------------------------------------------------


def header_only_response():
    """Brandon Sanderson's artist page as served signed out (trimmed): a
    musicImmersiveHeaderRenderer and a Music tab without content."""
    return {
        "header": {"musicImmersiveHeaderRenderer": {
            "title": {"runs": [{"text": "Brandon Sanderson"}]},
            "subscriptionButton": {"subscribeButtonRenderer": {
                "subscriberCountText": {"runs": [{"text": "8"}]}, "subscribed": False, "enabled": True,
                "channelId": SANDERSON, "longSubscriberCountText": {"runs": [{"text": "8 subscribers"}]},
                "shortSubscriberCountText": {"runs": [{"text": "8"}]}}},
            "thumbnail": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
                {"url": "https://yt3.googleusercontent.com/MbDVEXxofZs3Hhzk=w1200-h500-p-l90-rj", "width": 1200,
                 "height": 500}]}}}}},
        "contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {
            "endpoint": {"browseEndpoint": {"browseId": SANDERSON}}, "title": "Music", "selected": True}}]}},
    }


def test_an_artist_page_with_a_header_only_opens_as_that_header():
    yt = ArtistYT(header_only_response())
    with pytest.raises((KeyError, IndexError)):
        yt.get_artist(SANDERSON)  # ytmusicapi reads no such page
    yt.calls.clear()
    reply = ytm.Worker(yt, "none").handle({"id": 1, "method": "artist", "params": {"channelId": SANDERSON}})
    assert reply["ok"], reply
    assert len(yt.calls) == 1, "one request"
    page = reply["result"]
    assert (page["kind"], page["title"], page["round"], page["sections"]) == ("artist", "Brandon Sanderson", True, [])
    assert page["thumbLarge"] and "MbDVEXxofZs3Hhzk" in page["thumbLarge"]
    assert page["message"] == artist.HEADER_ONLY_MESSAGE
    header = page["header"]
    assert (header["channelId"], header["subscribeId"], header["subscribed"], header["subscribers"]) == (
        SANDERSON, SANDERSON, False, "8"), "Subscribe works; the shell adds 'subscribers'"
    assert header["shuffleId"] is None and header["radioId"] is None, "no Shuffle or Radio"
    assert header["pin"] is None


def test_a_page_without_a_header_or_rows_is_not_found():
    response = header_only_response()
    del response["header"]
    reply = ytm.Worker(ArtistYT(response), "none").handle({"id": 1, "method": "artist", "params": {"channelId": "UCx"}})
    assert reply["error"]["code"] == "NOT_FOUND"


# ---- podcast orders and filters --------------------------------------------------------------

ORDERS = ("Latest", "Oldest", "Popular", "Creator provided")
FILTERS = ("In progress", "Unplayed", "Played")


def reload_endpoint(token):
    return {"browseSectionListReloadEndpoint": {"continuation": {"reloadContinuationData": {"continuation": token}}}}


def chip_cloud(order=0, chosen=None, tag=""):
    """The served chip cloud: the sort chip's menu, then the filter chips.
    Tokens name what they reload (``tag`` tells two responses apart)."""
    entries = []
    for i, title in enumerate(ORDERS):
        entry = {"text": {"simpleText": title}, "navigationEndpoint": reload_endpoint(f"R{tag}order{i}")}
        if i == order:
            entry["icon"] = {"iconType": "CHECK"}
        entries.append({"menuNavigationItemRenderer": entry})
    chips = [{"chipCloudChipRenderer": {
        "text": {"runs": [{"text": ORDERS[order]}]}, "icon": {"iconType": "EXPAND"},
        "navigationEndpoint": {"openPopupAction": {"popup": {"menuPopupRenderer": {"items": entries}}}}}}]
    for i, title in enumerate(FILTERS):
        chips.append({"chipCloudChipRenderer": {
            "text": {"simpleText": title}, "isSelected": i == chosen,
            "navigationEndpoint": reload_endpoint(f"R{tag}filter{i}"),
            "onDeselectedCommand": reload_endpoint(f"R{tag}clear")}})
    return {"chipCloudRenderer": {"chips": chips}}


def podcast_page(load_fixture):
    response = load_fixture("podcast.json")
    raw.section_list(response)["header"] = chip_cloud()
    return response


def reload_reply(rows, token=None, order=0, chosen=None, tag=""):
    """A reload's reply: the chips as they now stand, and the episodes."""
    return {"continuationContents": {
        "sectionListContinuation": {"header": chip_cloud(order, chosen, tag)},
        "musicShelfContinuation": {"contents": rows, "continuations": next_data(token)}}}


class ReloadYT:
    """Answers each request by its continuation (``&ctoken=``): the first
    page without one, a reload or a cursor page by its token."""

    def __init__(self, first, by_token):
        self.first, self.by_token, self.sent = first, by_token, []

    def _send_request(self, endpoint, body, additionalParams=""):
        token = additionalParams.split("ctoken=")[1].split("&")[0] if "ctoken=" in additionalParams else None
        self.sent.append(token)
        return copy.deepcopy(self.first if token is None else self.by_token[token])


def reload_worker(first, by_token, cache=None):
    return ytm.Worker(raw.install(ReloadYT(first, by_token)), "none", cache=cache)


def test_a_podcast_page_lists_the_served_orders_and_filters_without_tokens(load_fixture):
    page = result(worker_for(podcast_page(load_fixture)), "podcast", browseId=PODCAST)
    assert page["orders"] == [{"index": i, "title": t, "selected": i == 0} for i, t in enumerate(ORDERS)]
    assert page["order"] == 0
    assert page["filters"] == [{"index": i, "title": t, "selected": False} for i, t in enumerate(FILTERS)]
    assert page["filter"] is None and page["message"] is None
    assert "Rorder" not in str(page) and "Rfilter" not in str(page), "the reload tokens stay in the worker"


def test_the_recorded_podcast_page_serves_its_chips_by_what_they_do(load_fixture):
    response = load_fixture("podcast.json")
    assert [o["title"] for o in podcasts.episode_orders(response)] == list(ORDERS)
    assert [f["title"] for f in podcasts.episode_filters(response)] == list(FILTERS)
    assert podcasts.episode_orders(response)[0]["selected"] is True


def test_an_order_reloads_the_list_and_its_cursor_pages_go_on_in_it(load_fixture, tmp_path):
    first = podcast_page(load_fixture)
    oldest = reload_reply([episode_row(i) for i in range(100, 200)], "C2", order=1)
    later = {"continuationContents": {"musicShelfContinuation": {
        "contents": [episode_row(i) for i in range(200, 210)]}}}
    worker = reload_worker(first, {"Rorder1": oldest, "C2": later}, cache=caches.DiskCache(tmp_path / "data"))
    page = result(worker, "podcast", browseId=PODCAST, order=1, cache="refresh")
    assert worker.yt.sent == [None, "Rorder1"], "the first page again, then its Oldest reload: 2 requests"
    episodes = page["sections"][0]["items"]
    assert [e["videoId"] for e in episodes][:2] == ["ep000000100", "ep000000101"] and len(episodes) == 100
    assert page["order"] == 1 and page["orders"][1]["selected"] and not page["orders"][0]["selected"]
    assert page["title"] == "Dan Carlin's Hardcore History" and page["header"]["podcastId"] == PODCAST[4:]
    assert page["cursor"] and page["hasMore"]
    more = result(worker, "podcast", browseId=PODCAST, order=1, cursor=page["cursor"])
    assert worker.yt.sent[-1] == "C2", "the reloaded shelf's own continuation"
    assert [e["videoId"] for e in more["sections"][0]["items"]] == [f"ep{i:09d}" for i in range(200, 210)]
    assert more["sections"][0]["items"][0]["podcast"]["name"] == "Dan Carlin's Hardcore History", \
        "named from the ordered first page's disk copy"
    assert more["cursor"] is None


def test_a_filter_reloads_the_list_and_one_nothing_matches_gives_the_served_message(load_fixture):
    first = podcast_page(load_fixture)
    unplayed = reload_reply([episode_row(i) for i in range(5)], chosen=1)
    message = {"messageRenderer": {"text": {"runs": [{"text": "No episodes match the filters you applied."}]},
                                   "button": {"buttonRenderer": {"text": {"runs": [{"text": "Clear filters"}]}}}}}
    none = reload_reply([message], chosen=0)
    worker = reload_worker(first, {"Rfilter1": unplayed, "Rfilter0": none})
    page = result(worker, "podcast", browseId=PODCAST, filter=1)
    assert worker.yt.sent == [None, "Rfilter1"]
    assert len(page["sections"][0]["items"]) == 5 and page["filter"] == 1 and page["filters"][1]["selected"]
    assert page["order"] == 0 and page["message"] is None
    empty = result(worker, "podcast", browseId=PODCAST, filter=0)
    assert empty["sections"][0]["items"] == [] and empty["cursor"] is None
    assert empty["message"] == "No episodes match the filters you applied."
    assert empty["filter"] == 0


def test_an_order_and_a_filter_together_take_the_filter_from_the_ordered_list(load_fixture):
    first = podcast_page(load_fixture)
    oldest = reload_reply([episode_row(i) for i in range(100)], "C2", order=1, tag="O")
    both = reload_reply([episode_row(i) for i in range(300, 310)], order=1, chosen=2)
    worker = reload_worker(first, {"Rorder1": oldest, "ROfilter2": both})
    page = result(worker, "podcast", browseId=PODCAST, order=1, filter=2)
    assert worker.yt.sent == [None, "Rorder1", "ROfilter2"], "3 requests: page, order, then the ordered filter"
    assert (page["order"], page["filter"]) == (1, 2)
    assert [e["videoId"] for e in page["sections"][0]["items"]] == [f"ep{i:09d}" for i in range(300, 310)]


@pytest.mark.parametrize("params,code", [
    ({"order": 4}, "BAD_REQUEST"), ({"filter": 3}, "BAD_REQUEST"), ({"order": -1}, "BAD_REQUEST"),
    ({"order": True}, "BAD_REQUEST"), ({"filter": "Played"}, "BAD_REQUEST"),
])
def test_an_order_or_filter_the_podcast_does_not_serve_is_a_bad_request(load_fixture, params, code):
    worker = reload_worker(podcast_page(load_fixture), {})
    reply = worker.handle({"id": 1, "method": "podcast", "params": dict(params, browseId=PODCAST)})
    assert reply["error"]["code"] == code
    assert len(worker.yt.sent) <= 1, "nothing reloaded"


def test_a_podcast_without_chips_has_no_orders_or_filters(load_fixture):
    response = load_fixture("podcast.json")
    del raw.section_list(response)["header"]
    worker = worker_for(response)
    page = result(worker, "podcast", browseId=PODCAST)
    assert (page["orders"], page["order"], page["filters"], page["filter"]) == ([], None, [], None)
    assert worker_for(response).handle({"id": 1, "method": "podcast", "params": {"browseId": PODCAST, "order": 1}})[
        "error"]["code"] == "BAD_REQUEST"


def test_an_order_whose_served_entry_has_no_token_is_upstream(load_fixture):
    response = podcast_page(load_fixture)
    chip = raw.section_list(response)["header"]["chipCloudRenderer"]["chips"][0]["chipCloudChipRenderer"]
    del chip["navigationEndpoint"]["openPopupAction"]["popup"]["menuPopupRenderer"]["items"][2][
        "menuNavigationItemRenderer"]["navigationEndpoint"]
    reply = worker_for(response).handle({"id": 1, "method": "podcast", "params": {"browseId": PODCAST, "order": 2}})
    assert reply["error"]["code"] == "UPSTREAM"


def test_the_selected_order_is_the_one_the_chip_names_when_none_is_ticked(load_fixture):
    response = podcast_page(load_fixture)
    chip = raw.section_list(response)["header"]["chipCloudRenderer"]["chips"][0]["chipCloudChipRenderer"]
    for entry in chip["navigationEndpoint"]["openPopupAction"]["popup"]["menuPopupRenderer"]["items"]:
        entry["menuNavigationItemRenderer"].pop("icon", None)
    chip["text"] = {"runs": [{"text": "Popular"}]}
    assert [o["selected"] for o in podcasts.episode_orders(response)] == [False, False, True, False]


def test_menu_service_entries_are_orders_too():
    """The plan's shape: menuServiceItemRenderer entries with a serviceEndpoint."""
    response = {"contents": {"twoColumnBrowseResultsRenderer": {"secondaryContents": {"sectionListRenderer": {
        "header": {"chipCloudRenderer": {"chips": [{"chipCloudChipRenderer": {
            "text": {"runs": [{"text": "Latest"}]},
            "navigationEndpoint": {"openPopupAction": {"popup": {"menuPopupRenderer": {"items": [
                {"menuServiceItemRenderer": {"text": {"runs": [{"text": "Latest"}]},
                                             "serviceEndpoint": reload_endpoint("A")}},
                {"menuServiceItemRenderer": {"text": {"runs": [{"text": "Oldest"}]},
                                             "serviceEndpoint": reload_endpoint("B")}}]}}}}}}]}},
        "contents": []}}}}}
    assert podcasts.episode_orders(response) == [{"index": 0, "title": "Latest", "selected": True},
                                                 {"index": 1, "title": "Oldest", "selected": False}]
    assert podcasts._order_options(response)[1][2] == "B"


def test_the_ordered_and_filtered_pages_are_cached_apart_and_without_tokens(load_fixture, tmp_path):
    first = podcast_page(load_fixture)
    cache = caches.DiskCache(tmp_path / "data")
    worker = reload_worker(first, {"Rorder2": reload_reply([episode_row(i) for i in range(3)], order=2)}, cache=cache)
    result(worker, "podcast", browseId=PODCAST, cache="refresh")
    result(worker, "podcast", browseId=PODCAST, order=2, cache="refresh")
    plain = cache.get("podcast", {"browseId": PODCAST})[0]
    popular = cache.get("podcast", {"browseId": PODCAST, "order": 2})[0]
    assert plain["order"] == 0 and popular["order"] == 2 and len(popular["sections"][0]["items"]) == 3
    for path in (tmp_path / "data").glob("*.json"):
        assert "Rorder" not in path.read_text() and "Rfilter" not in path.read_text()
