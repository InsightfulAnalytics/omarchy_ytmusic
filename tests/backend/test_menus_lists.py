"""G4 (menus and lists): the library profiles' sort orders, Liked Music's
chips, explicit badges on watch playlists, Skip duplicates and Remove custom
image.

Liked Music, the library's profiles and an owned playlist's header are
private, so they are hand-built in the shapes measured live (chip clouds,
sort menus, the cover's edit menu). The explicit badges run real ytmusicapi
code over the anonymous watch_raw fixture. Every edit is pinned against a
fake client: the real ones change the account.
"""

import copy

import pytest
from ytmusicapi.continuations import get_continuation_string
from ytmusicapi.mixins._utils import prepare_order_params

import ytm
import ytm_cache as caches
import ytm_collections as collections
import ytm_edit as edit
import ytm_raw as raw
import ytm_users as users
from test_library import STANDARD, continuation, panel_continuation, panel_track, sort_menu, watch_panel
from test_library import worker_for as library_worker
from test_now import KARMA, client, panel
from test_podcasts import channel_row
from test_users import MENU, profiles_page, worker_for

EXPLICIT_SONG = "9RfVp-GhKfs"  # badged explicit in watch_raw.json


def call(worker, method, **params):
    return worker.handle({"id": 4, "method": method, "params": params})


def result(worker, method, **params):
    reply = call(worker, method, **params)
    assert reply["ok"], reply
    return reply["result"]


# ---- library profiles: sort orders ------------------------------------------------------------


def ordered_profiles(rows, token=None, selected=0):
    """The profiles page with its served sort menu (Recently saved, A to Z,
    Z to A, as measured), the note after the shelf."""
    response = profiles_page(rows, token)
    found = response["contents"]["singleColumnBrowseResultsRenderer"]["tabs"][0]["tabRenderer"]["content"][
        "sectionListRenderer"]
    found["header"], response["frameworkUpdates"] = sort_menu(users.PROFILES_BROWSE_ID, STANDARD, selected)
    return response


def test_profiles_report_the_three_served_orders_and_read_the_plain_list():
    worker = worker_for({users.PROFILES_BROWSE_ID: ordered_profiles([channel_row(i) for i in range(3)])}, MENU)
    page = result(worker, "libraryProfiles")
    assert worker.yt.sent[-1] == ("browse", {"browseId": users.PROFILES_BROWSE_ID, "params": users.PROFILES_PARAMS})
    assert page["orders"] == [{"index": "recently_added", "title": "Recently saved", "selected": True},
                              {"index": "a_to_z", "title": "A to Z", "selected": False},
                              {"index": "z_to_a", "title": "Z to A", "selected": False}]
    assert page["order"] == "recently_added"
    assert len(page["sections"][0]["items"]) == 3
    assert page["message"] == "Only profiles with music activity are shown."


@pytest.mark.parametrize("order", ["a_to_z", "z_to_a", "recently_added"])
def test_an_order_replaces_the_plain_params_as_the_served_options_do(order):
    worker = worker_for({users.PROFILES_BROWSE_ID: ordered_profiles([channel_row(i) for i in range(3)])}, MENU)
    page = result(worker, "libraryProfiles", order=order)
    body = {"browseId": users.PROFILES_BROWSE_ID, "params": prepare_order_params(order)}
    assert [s for s in worker.yt.sent if s[0] == "browse"] == [("browse", body)], "one request"
    assert page["order"] == order
    assert [o["index"] for o in page["orders"] if o["selected"]] == [order]
    assert users.profiles_body(order) == body and users.profiles_body(None)["params"] == users.PROFILES_PARAMS


def test_later_pages_keep_the_order():
    first = ordered_profiles([channel_row(i) for i in range(50)], token="T1")
    rest = continuation("musicShelfRenderer", [channel_row(i) for i in range(50, 55)])
    worker = worker_for({users.PROFILES_BROWSE_ID: [first, rest]}, MENU)
    page = result(worker, "libraryProfiles", order="z_to_a")
    assert page["cursor"]
    more = result(worker, "libraryProfiles", order="z_to_a", cursor=page["cursor"])
    body = {"browseId": users.PROFILES_BROWSE_ID, "params": prepare_order_params("z_to_a")}
    assert worker.yt.sent[-1] == ("browse", body)
    assert [i["channelId"] for i in more["sections"][0]["items"]] == [f"UCc{i:04d}" for i in range(50, 55)]
    assert more["order"] == "z_to_a"


@pytest.mark.parametrize("order", ["newest", "A to Z", 3, True, "menu:x", ""])
def test_a_bad_profiles_order_is_a_bad_request(order):
    worker = worker_for({users.PROFILES_BROWSE_ID: ordered_profiles([channel_row(0)])}, MENU)
    reply = call(worker, "libraryProfiles", order=order)
    assert reply["error"]["code"] == "BAD_REQUEST"
    assert [s for s in worker.yt.sent if s[0] == "browse"] == []


def test_an_ordered_profiles_read_drops_the_cached_browse_page(tmp_path):
    cache = caches.DiskCache(tmp_path / "data")
    cache.put("browsePage", {"browseId": users.PROFILES_BROWSE_ID}, {"type": "page"})
    worker = worker_for({users.PROFILES_BROWSE_ID: ordered_profiles([channel_row(0)])}, MENU, cache=cache)
    assert call(worker, "libraryProfiles", order="a_to_z", cache="refresh")["ok"]
    assert cache.get("browsePage", {"browseId": users.PROFILES_BROWSE_ID}) is None, "YouTube saved the order"
    assert cache.get("libraryProfiles", {"order": "a_to_z"}) is not None


def test_an_empty_profiles_list_keeps_its_orders_and_note():
    worker = worker_for({users.PROFILES_BROWSE_ID: ordered_profiles(None)}, MENU)
    page = result(worker, "libraryProfiles", order="a_to_z")
    assert page["sections"] == [] and page["message"] == "Only profiles with music activity are shown."
    assert len(page["orders"]) == 3 and page["order"] == "a_to_z"


# ---- Liked Music's chips ------------------------------------------------------------------------

CHIPS = ["Party", "Hip-hop", "Chill", "Focus", "Rock"]
RELOAD = "chip-reload-{}"


def chip(title, n, selected=False, unique=None):
    token = {"reloadContinuationData": {"continuation": RELOAD.format(n)}}
    return {"chipCloudChipRenderer": {"text": {"runs": [{"text": title}]}, "isSelected": selected,
                                      "navigationEndpoint": {"browseSectionListReloadEndpoint": {"continuation": token}},
                                      "onDeselectedCommand": {"browseSectionListReloadEndpoint": {"continuation": {
                                          "reloadContinuationData": {"continuation": "deselect"}}}},
                                      "uniqueId": unique or title}}


def song_row(video_id, explicit=False):
    """A Liked Music song row as parse_playlist_items reads it."""
    data = {
        "flexColumns": [
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [
                {"text": f"Song {video_id}", "navigationEndpoint": {"watchEndpoint": {"videoId": video_id}}}]}}},
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [
                {"text": "Artist", "navigationEndpoint": {"browseEndpoint": {
                    "browseId": "UCartist", "browseEndpointContextSupportedConfigs": {
                        "browseEndpointContextMusicConfig": {"pageType": "MUSIC_PAGE_TYPE_ARTIST"}}}}}]}}},
        ],
        "overlay": {"musicItemThumbnailOverlayRenderer": {"content": {"musicPlayButtonRenderer": {
            "playNavigationEndpoint": {"watchEndpoint": {"videoId": video_id}}}}}},
    }
    if explicit:
        data["badges"] = [{"musicInlineBadgeRenderer": {"icon": {"iconType": "MUSIC_EXPLICIT_BADGE"},
                                                        "accessibilityData": {"accessibilityData": {"label": "Explicit"}}}}]
    return {"musicResponsiveListItemRenderer": data}


def more_row(token):
    return {"continuationItemRenderer": {"continuationEndpoint": {"continuationCommand": {"token": token}}}}


def liked_page(chips=CHIPS, rows=3):
    """VLLM as served signed in: the header, the chip cloud in the songs'
    sectionList header, the playlist shelf."""
    header = {"musicResponsiveHeaderRenderer": {
        "title": {"runs": [{"text": "Liked Music"}]},
        "subtitle": {"runs": [{"text": "Auto playlist"}, {"text": " • "}, {"text": "2026"}]},
        "secondSubtitle": {"runs": [{"text": f"{rows} songs"}, {"text": " • "}, {"text": "12 minutes"}]},
        "thumbnail": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
            {"url": "https://i.ytimg.com/liked.jpg", "width": 60, "height": 60}]}}}}}
    section = {"contents": [{"musicPlaylistShelfRenderer": {"contents": [song_row(f"lk{i:09d}") for i in range(rows)]}}]}
    if chips:
        section["header"] = {"chipCloudRenderer": {"chips": [chip(t, i) for i, t in enumerate(chips)]}}
    return {"contents": {"twoColumnBrowseResultsRenderer": {
        "tabs": [{"tabRenderer": {"content": {"sectionListRenderer": {"contents": [header]}}}}],
        "secondaryContents": {"sectionListRenderer": section}}}}


def reloaded(video_ids, token=None, explicit=()):
    """A chip's reload: the filtered shelf, and a narrower chip set (Clear
    filters, the chosen chip, refinements) that is not offered."""
    contents = [song_row(v, v in explicit) for v in video_ids]
    if token:
        contents.append(more_row(token))
    clear = {"chipCloudChipRenderer": {"icon": {"iconType": "CLOSE"}, "isSelected": True, "uniqueId": "Clear filters",
                                       "navigationEndpoint": {"browseSectionListReloadEndpoint": {"continuation": {
                                           "reloadContinuationData": {"continuation": "clear"}}}}}}
    return {"continuationContents": {"sectionListContinuation": {
        "header": {"chipCloudRenderer": {"chips": [clear, chip("Chill", 0, True), chip("Jazz", 1)]}},
        "contents": [{"musicPlaylistShelfRenderer": {"contents": contents}}]}}}


def appended(video_ids, token=None):
    items = [song_row(v) for v in video_ids]
    if token:
        items.append(more_row(token))
    return {"onResponseReceivedActions": [{"appendContinuationItemsAction": {"continuationItems": items}}]}


def recording_client(*responses):
    """test_now's client (a real YTMusic answered from ``responses``) that
    also records the continuation params of each request."""
    yt = client(*responses)
    answer = yt._send_request
    yt.params = []

    def send(endpoint, body, additionalParams=""):
        yt.params.append(additionalParams)
        return answer(endpoint, body, additionalParams)

    yt._send_request = send
    # Liked Music is signed in only (get_liked_songs checks the client).
    yt._check_auth = lambda: None
    return yt


def liked_worker(*responses):
    yt = recording_client(*responses)
    return ytm.Worker(yt, "ok"), yt


def ids(prefix, start, stop):
    return [f"{prefix}{i:09d}"[:11] for i in range(start, stop)]


def test_liked_music_serves_its_chips_without_another_request(monkeypatch):
    worker, yt = liked_worker(liked_page())
    monkeypatch.setattr(worker, "require_auth", lambda: None)
    page = result(worker, "liked")
    assert [s[0] for s in yt.sent] == ["browse"] and yt.sent[0][1]["browseId"] == "VLLM"
    assert page["filters"] == [{"index": t, "title": t, "selected": False} for t in CHIPS]
    assert page["filter"] is None and len(page["tracks"]) == 3


def test_a_chip_reloads_the_list_filtered_to_it(monkeypatch):
    chill = ids("ch", 0, 4)
    worker, yt = liked_worker(liked_page(), reloaded(chill, explicit=(chill[1],)))
    monkeypatch.setattr(worker, "require_auth", lambda: None)
    page = result(worker, "liked", filter="Chill")
    assert [(s[0], s[1].get("browseId")) for s in yt.sent] == [("browse", "VLLM"), ("browse", "VLLM")]
    assert yt.params[1] == get_continuation_string(RELOAD.format(2)), "the Chill chip's own reload"
    assert page["kind"] == "liked" and page["playlistId"] == "LM" and page["title"] == "Liked Music"
    assert [t["videoId"] for t in page["tracks"]] == chill
    assert [t["explicit"] for t in page["tracks"]] == [False, True, False, False]
    assert page["filter"] == "Chill" and [f["title"] for f in page["filters"]] == CHIPS, "the plain page's chips"
    assert [f["selected"] for f in page["filters"]] == [False, False, True, False, False]
    assert page["truncated"] is False and page["trackCount"] == 4 and page["thumb"]
    text = str(page)
    assert "chip-reload" not in text and "deselect" not in text, "reload tokens stay in the worker"


def test_a_chip_is_found_by_its_id_wherever_the_page_serves_it_now():
    # The chips are made from your likes and move between reads (live: Rock
    # and Jazz swapped places): the id, not the place, picks the chip.
    moved = liked_page(chips=["Rock", "Party", "Chill"])
    yt = recording_client(moved, reloaded(ids("rk", 0, 2)))
    page = collections.read_liked_filtered(ytm.Worker(yt, "ok"), "Rock", 200)
    assert [s[1]["browseId"] for s in yt.sent] == ["VLLM", "VLLM"]
    assert yt.params == ["", get_continuation_string(RELOAD.format(0))], "Rock's reload, at its new place"
    assert [f["index"] for f in page["filters"] if f["selected"]] == ["Rock"]


def test_a_chip_id_is_its_unique_id_and_the_first_of_an_id_wins():
    response = liked_page(chips=None)
    section = response["contents"]["twoColumnBrowseResultsRenderer"]["secondaryContents"]["sectionListRenderer"]
    section["header"] = {"chipCloudRenderer": {"chips": [chip("Fiesta", 0, unique="Party"), chip("Chill", 1),
                                                         chip("Chill again", 2, unique="Chill")]}}
    assert [(i, t) for i, t, _ in collections.liked_chips(response)] == [("Party", "Fiesta"), ("Chill", "Chill")]
    assert collections.liked_filters(response, "Party")[0] == {"index": "Party", "title": "Fiesta", "selected": True}


def test_a_filtered_list_reads_on_and_load_all_reads_it_whole(monkeypatch):
    first, second, third = ids("a", 0, 100), ids("b", 0, 100), ids("c", 0, 30)
    worker, yt = liked_worker(liked_page(), reloaded(first, "P2"), appended(second, "P3"))
    monkeypatch.setattr(worker, "require_auth", lambda: None)
    page = result(worker, "liked", filter="Party")
    assert len(page["tracks"]) == 200 and page["truncated"] is True and page["capped"] is False
    assert page["trackCount"] is None, "only Liked Music's own count is served"
    assert yt.sent[2][1] == {"continuation": "P2"} and len(yt.sent) == 3, "body continuations, as get_playlist reads"
    worker, yt = liked_worker(liked_page(), reloaded(first, "P2"), appended(second, "P3"), appended(third))
    monkeypatch.setattr(worker, "require_auth", lambda: None)
    whole = result(worker, "liked", filter="Party", limit="all")
    assert len(whole["tracks"]) == 230 and whole["truncated"] is False and whole["capped"] is False
    assert whole["trackCount"] == 230 and len(yt.sent) == 4


def test_a_filtered_whole_read_stops_at_the_cap(monkeypatch):
    monkeypatch.setattr(collections, "MAX_TRACKS", 150)
    worker, _ = liked_worker(liked_page(), reloaded(ids("a", 0, 100), "P2"), appended(ids("b", 0, 100), "P3"))
    monkeypatch.setattr(worker, "require_auth", lambda: None)
    page = result(worker, "liked", filter="Hip-hop", limit="all")
    assert len(page["tracks"]) == 150 and page["truncated"] is True and page["capped"] is True


@pytest.mark.parametrize("value", [2, -1, True, "", "x" * 201, 1.5, ["Chill"]])
def test_a_bad_filter_is_refused_before_any_request(monkeypatch, value):
    worker, yt = liked_worker(liked_page())
    monkeypatch.setattr(worker, "require_auth", lambda: None)
    assert call(worker, "liked", filter=value)["error"]["code"] == "BAD_REQUEST"
    assert yt.sent == []


def test_a_chip_the_page_no_longer_serves_is_not_found(monkeypatch):
    worker, yt = liked_worker(liked_page())
    monkeypatch.setattr(worker, "require_auth", lambda: None)
    reply = call(worker, "liked", filter="Polka")
    assert reply["error"]["code"] == "NOT_FOUND" and len(yt.sent) == 1


def test_liked_chips_skip_untitled_ones_and_need_a_reload():
    response = liked_page()
    chips = response["contents"]["twoColumnBrowseResultsRenderer"]["secondaryContents"]["sectionListRenderer"][
        "header"]["chipCloudRenderer"]["chips"]
    del chips[1]["chipCloudChipRenderer"]["navigationEndpoint"]
    assert [t for _, t, _ in collections.liked_chips(response)] == ["Party", "Chill", "Focus", "Rock"]
    assert [t for _, t, _ in collections.liked_chips(reloaded([]))] == ["Chill", "Jazz"], "Clear filters has no title"
    assert collections.liked_filters(None) == [] and collections.liked_filters(liked_page(chips=None)) == []


def test_liked_music_without_chips_has_none(monkeypatch):
    worker, _ = liked_worker(liked_page(chips=None))
    monkeypatch.setattr(worker, "require_auth", lambda: None)
    page = result(worker, "liked")
    assert page["filters"] == [] and page["filter"] is None


def test_a_filtered_liked_page_is_cached_apart_and_holds_no_tokens(tmp_path, monkeypatch):
    cache = caches.DiskCache(tmp_path / "data")
    worker = ytm.Worker(recording_client(liked_page(), reloaded(ids("ch", 0, 2))), "ok", cache=cache)
    monkeypatch.setattr(worker, "require_auth", lambda: None)
    assert call(worker, "liked", filter="Chill", cache="refresh")["ok"]
    stored = cache.get("liked", {"filter": "Chill"})
    assert stored is not None and cache.get("liked", {}) is None
    assert "chip-reload" not in str(stored)


# ---- explicit badges on watch playlists -------------------------------------------------------


def test_mark_explicit_reads_every_response():
    response = {"continuationContents": {"playlistPanelContinuation": {"contents": [{"playlistPanelVideoRenderer": {
        "videoId": "v2", "badges": [{"musicInlineBadgeRenderer": {"icon": {"iconType": "MUSIC_EXPLICIT_BADGE"}}}]}}]}}}
    tracks = [{"videoId": "v1"}, {"videoId": "v2"}, None]
    marked = collections.mark_explicit(tracks, [None, response])
    assert marked[1]["isExplicit"] is True and "isExplicit" not in marked[0] and marked[2] is None
    assert "isExplicit" not in tracks[1], "copies, never the parsed tracks"
    assert collections.mark_explicit(tracks, []) == tracks and collections.mark_explicit(None, [response]) == []


def explicit_ids(items):
    return [t["videoId"] for t in items if t["explicit"]]


def test_start_radio_marks_the_badged_song(load_fixture):
    reply = call(ytm.Worker(client(load_fixture("watch_raw.json")), "none"), "radio", videoId=KARMA)
    assert reply["ok"], reply
    assert explicit_ids(reply["result"]["items"]) == [EXPLICIT_SONG]


def test_autoplay_marks_the_badged_song(load_fixture):
    reply = call(ytm.Worker(client(load_fixture("watch_raw.json")), "none"), "upNext", videoId=KARMA)
    assert explicit_ids(reply["result"]["items"]) == [EXPLICIT_SONG]
    assert reply["result"]["playingFrom"] == "Karma Police Mix", "the first response still names it"


def test_now_context_marks_the_badged_song(load_fixture):
    reply = call(ytm.Worker(client(load_fixture("watch_raw.json")), "none"), "nowContext", videoId=KARMA)
    assert explicit_ids(reply["result"]["items"]) == [EXPLICIT_SONG]


def test_a_tuner_chip_marks_the_badged_song(load_fixture):
    recorded = load_fixture("queue_raw.json")
    chip_data = recorded.pop("chip")
    entry = panel(recorded)["contents"][2]["playlistPanelVideoRenderer"]
    entry["badges"] = [{"musicInlineBadgeRenderer": {"icon": {"iconType": "MUSIC_EXPLICIT_BADGE"}}}]
    reply = call(ytm.Worker(client(recorded), "none"), "queueFrom", videoId=KARMA, playlistId=chip_data["playlistId"])
    assert explicit_ids(reply["result"]["items"]) == [entry["videoId"]]


def test_a_mix_played_from_a_row_marks_the_badged_song(load_fixture):
    reply = call(ytm.Worker(client(load_fixture("watch_raw.json")), "none"), "tracksFor", kind="mix",
                 id="RDAMVM" + KARMA)
    assert explicit_ids(reply["result"]["items"]) == [EXPLICIT_SONG]


def test_a_radio_read_over_continuations_marks_every_page(load_fixture):
    first = load_fixture("watch_raw.json")
    more = {"continuationContents": {"playlistPanelContinuation": {"contents": [{"playlistPanelVideoRenderer": {
        "videoId": "MoreExplic1", "badges": [{"musicInlineBadgeRenderer": {"icon": {"iconType": "MUSIC_EXPLICIT_BADGE"}}}]}}]}}}

    class Watch:
        """get_watch_playlist reading a first page and a continuation."""

        def __init__(self):
            self.pages = [first, more]

        def _send_request(self, endpoint, body, additionalParams=""):
            return self.pages.pop(0)

        def get_watch_playlist(self, **kwargs):
            while self.pages:
                self._send_request("next", {})
            return {"playlistId": "RDAMPLPLx", "tracks": [
                {"videoId": v, "title": v, "length": "3:00", "artists": [{"name": "A", "id": "UCa"}]}
                for v in (EXPLICIT_SONG, "MoreExplic1", "PlainSong01")]}

    reply = call(ytm.Worker(raw.install(Watch()), "none"), "radio", playlistId="PLx")
    assert reply["ok"], reply
    assert explicit_ids(reply["result"]["items"]) == [EXPLICIT_SONG, "MoreExplic1"]
    assert reply["result"]["playingFrom"] == "Karma Police Mix", "m_radio's own capture still sees the first page"


BADGE = [{"musicInlineBadgeRenderer": {"icon": {"iconType": "MUSIC_EXPLICIT_BADGE"}}}]


def with_continuation(response):
    """``response`` (a first watch page) serving a next page, as a radio's
    first 50 songs do."""
    data = copy.deepcopy(response)
    panel(data)["continuations"] = [{"nextRadioContinuationData": {"continuation": "next-page"},
                                     "nextContinuationData": {"continuation": "next-page"}}]
    return data


def continuation_page(*songs):
    """A playlistPanelContinuation page; ``songs`` are (videoId, explicit)."""
    contents = []
    for n, (video_id, explicit) in enumerate(songs):
        entry = panel_track(n)
        data = entry["playlistPanelVideoRenderer"]
        data["videoId"] = data["navigationEndpoint"]["watchEndpoint"]["videoId"] = video_id
        if explicit:
            data["badges"] = BADGE
        contents.append(entry)
    return {"continuationContents": {"playlistPanelContinuation": {"contents": contents}}}


def test_autoplay_marks_the_badged_songs_of_every_page(load_fixture):
    first = with_continuation(load_fixture("watch_raw.json"))
    yt = client(first, continuation_page(("MoreExplic1", True), ("PlainSong01", False)))
    reply = call(ytm.Worker(yt, "none"), "upNext", videoId=KARMA, limit=25)
    assert reply["ok"], reply
    assert len(yt.sent) == 2, "the first page holds fewer than 25: ytmusicapi reads on"
    assert explicit_ids(reply["result"]["items"]) == [EXPLICIT_SONG, "MoreExplic1"]
    assert reply["result"]["playingFrom"] == "Karma Police Mix", "still named from the first page"


def test_now_context_marks_a_later_pages_badged_song(load_fixture):
    # nowContext asks for one song: ytmusicapi reads on only when the first
    # page holds none, and then that page's badge counts.
    first = with_continuation(load_fixture("watch_raw.json"))
    panel(first)["contents"] = []
    yt = client(first, continuation_page(("MoreExplic1", True), ("PlainSong01", False)))
    reply = call(ytm.Worker(yt, "none"), "nowContext", videoId=KARMA)
    assert reply["ok"], reply
    assert len(yt.sent) == 2
    assert explicit_ids(reply["result"]["items"]) == ["MoreExplic1"]


def test_library_shuffle_all_marks_the_badged_songs_of_every_page():
    first = [panel_track(i) for i in range(25)]
    first[3]["playlistPanelVideoRenderer"]["badges"] = BADGE
    more = [panel_track(i) for i in range(25, 50)]
    more[1]["playlistPanelVideoRenderer"]["badges"] = BADGE
    worker = library_worker(watch_panel(first, "W1"), panel_continuation(more, "W2"))
    reply = call(worker, "tracksFor", kind="playlist", id="MLCT", limit=50)
    assert reply["ok"], reply
    assert explicit_ids(reply["result"]["items"]) == ["shuf0000003", "shuf0000026"]
    assert len(reply["result"]["items"]) == 50


# ---- Skip duplicates ------------------------------------------------------------------------------


class FakeYT:
    """Records every call; ``responses[name]`` is a value, a list served in
    turn, or an exception."""

    def __init__(self, **responses):
        self.responses = dict({"get_account_info": {"accountName": "x"}}, **responses)
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            value = self.responses.get(name)
            if isinstance(value, list):
                value = value.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        return method

    def edits(self):
        return [c for c in self.calls if c[0] != "get_account_info"]


def signed_in(**responses):
    return ytm.Worker(FakeYT(**responses), "ok")


def toast(*video_ids, served_skip=None):
    """YouTube's refusal of songs already there, as measured with one song: a
    toast whose Add anyway repeats them with DEDUPE_OPTION_SKIP. Whether a
    refusal of several names only the ones there or repeats every song asked
    for (as the measured whole-album dialog repeats its request) is not
    measured, so both are pinned. ``served_skip``: the ids of a Skip
    duplicates command the toast also serves (DEDUPE_OPTION_DROP_DUPLICATE),
    as that dialog does."""
    item = {"actionButton": {"buttonRenderer": {"command": {"playlistEditEndpoint": {"playlistId": "PLx", "actions": [
        {"addedVideoId": v, "action": "ACTION_ADD_VIDEO", "dedupeOption": "DEDUPE_OPTION_SKIP"}
        for v in video_ids]}}}}}
    if served_skip is not None:
        item["secondaryActionButton"] = {"buttonRenderer": {"command": {"playlistEditEndpoint": {
            "playlistId": "PLx", "actions": [
                {"addedVideoId": v, "action": "ACTION_ADD_VIDEO", "dedupeOption": "DEDUPE_OPTION_DROP_DUPLICATE"}
                for v in served_skip]}}}}
    return {"status": "STATUS_FAILED", "actions": [{"addToToastAction": {"item": {"notificationActionRenderer": item}}}]}


def holding(*video_ids):
    """get_playlist of the playlist added to: these songs, and one more."""
    return {"tracks": [{"videoId": v} for v in ("zzzzzzzzzzz", *video_ids)]}


ADDED = {"status": "STATUS_SUCCEEDED", "playlistEditResults": [{"videoId": "c", "setVideoId": "S3"}]}


def test_skip_duplicates_of_a_whole_album_sends_the_served_command():
    answer = {"status": "STATUS_SUCCEEDED", "playlistEditResults": []}
    worker = signed_in(_send_request=answer)
    reply = call(worker, "addToPlaylist", playlistId="VLPLx", sourcePlaylistId="OLAK5uy_album", skipDuplicates=True)
    assert reply["ok"], reply
    assert reply["result"] == {"added": [], "skipped": []}
    # Exactly the Duplicates dialog's Skip duplicates (tests/backend/test_edit.py SOURCE_DUPLICATE).
    assert worker.yt.edits() == [("_send_request", ("browse/edit_playlist", {"playlistId": "PLx", "actions": [
        {"action": "ACTION_ADD_PLAYLIST", "addedFullListId": "OLAK5uy_album",
         "dedupeOption": "DEDUPE_OPTION_DROP_DUPLICATE"}]}), {})]


def test_skip_duplicates_of_songs_adds_the_rest_when_youtube_names_some():
    # A refusal naming some but not all names the ones there: no read needed.
    worker = signed_in(add_playlist_items=[toast("a", "b"), ADDED])
    reply = call(worker, "addToPlaylist", playlistId="PLx", videoIds=["a", "b", "c"], skipDuplicates=True)
    assert reply["ok"], reply
    assert worker.yt.edits() == [("add_playlist_items", ("PLx", ["a", "b", "c"]), {"duplicates": False}),
                                 ("add_playlist_items", ("PLx", ["c"]), {"duplicates": False})]
    assert reply["result"] == {"added": [{"videoId": "c", "setVideoId": "S3"}], "skipped": ["a", "b"]}


def test_a_refusal_naming_every_song_reads_the_playlist_to_tell():
    # It may only repeat the request: the playlist says which are there.
    worker = signed_in(add_playlist_items=[toast("a", "b", "c"), ADDED], get_playlist=holding("a", "b"))
    reply = call(worker, "addToPlaylist", playlistId="PLx", videoIds=["a", "b", "c"], skipDuplicates=True)
    assert reply["ok"], reply
    assert worker.yt.edits() == [("add_playlist_items", ("PLx", ["a", "b", "c"]), {"duplicates": False}),
                                 ("get_playlist", ("PLx",), {"limit": None}),
                                 ("add_playlist_items", ("PLx", ["c"]), {"duplicates": False})]
    assert reply["result"] == {"added": [{"videoId": "c", "setVideoId": "S3"}], "skipped": ["a", "b"]}


def test_skip_duplicates_with_nothing_new_adds_nothing():
    worker = signed_in(add_playlist_items=[toast("a", "b")], get_playlist=holding("b", "a"))
    reply = call(worker, "addToPlaylist", playlistId="PLx", videoIds=["a", "b"], skipDuplicates=True)
    assert reply["result"] == {"added": [], "skipped": ["a", "b"], "status": "Nothing new to add"}
    assert [c[0] for c in worker.yt.edits()] == ["add_playlist_items", "get_playlist"]
    worker = signed_in(add_playlist_items=[toast("a")])
    reply = call(worker, "addToPlaylist", playlistId="PLx", videoIds=["a"], skipDuplicates=True)
    assert reply["result"] == {"added": [], "skipped": ["a"], "status": "Nothing new to add"}
    assert len(worker.yt.edits()) == 1, "one song refused is there: no read"


def test_skip_duplicates_sends_the_refusals_own_skip_command():
    refusal = toast("a", "b", "c", served_skip=["a", "b", "c"])
    answer = {"status": "STATUS_SUCCEEDED", "playlistEditResults": [
        {"playlistEditVideoAddedResultData": {"videoId": "c", "setVideoId": "S3"}}]}
    worker = signed_in(add_playlist_items=[refusal], _send_request=answer)
    reply = call(worker, "addToPlaylist", playlistId="PLx", videoIds=["a", "b", "c"], skipDuplicates=True)
    assert reply["ok"], reply
    assert worker.yt.edits()[1] == ("_send_request", ("browse/edit_playlist", {"playlistId": "PLx", "actions": [
        {"addedVideoId": v, "action": "ACTION_ADD_VIDEO", "dedupeOption": "DEDUPE_OPTION_DROP_DUPLICATE"}
        for v in ("a", "b", "c")]}), {})
    assert len(worker.yt.edits()) == 2, "no read, no other add"
    assert reply["result"] == {"added": [{"videoId": "c", "setVideoId": "S3"}], "skipped": ["a", "b"]}
    worker = signed_in(add_playlist_items=[refusal], _send_request={"status": "STATUS_SUCCEEDED"})
    # Measured live: the command answers with no entries when every song was
    # already there, so nothing was added.
    assert result(worker, "addToPlaylist", playlistId="PLx", videoIds=["a", "b", "c"], skipDuplicates=True) == \
        {"added": [], "skipped": ["a", "b", "c"], "status": "Nothing new to add"}


@pytest.mark.parametrize("change", [
    lambda e: e.update(playlistId="PLother"),
    lambda e: e["actions"].append({"action": "ACTION_REMOVE_VIDEO", "removedVideoId": "a"}),
    lambda e: e["actions"].append({"addedVideoId": "zzzzzzzzzzz", "action": "ACTION_ADD_VIDEO",
                                   "dedupeOption": "DEDUPE_OPTION_DROP_DUPLICATE"}),
    lambda e: e["actions"][0].update(dedupeOption="DEDUPE_OPTION_SKIP"),
])
def test_only_a_skip_command_for_these_songs_is_sent(change):
    refusal = toast("a", "b", served_skip=["a", "b"])
    served = next(edit._walk(refusal["actions"], "secondaryActionButton"))["secondaryActionButton"]
    change(served["buttonRenderer"]["command"]["playlistEditEndpoint"])
    assert edit.served_skip(refusal, "PLx", ["a", "b"]) is None
    worker = signed_in(add_playlist_items=[refusal, ADDED], get_playlist=holding("a"))
    call(worker, "addToPlaylist", playlistId="PLx", videoIds=["a", "b"], skipDuplicates=True)
    assert [c[0] for c in worker.yt.edits()] == ["add_playlist_items", "get_playlist", "add_playlist_items"]
    assert edit.served_skip(toast("a", served_skip=["a"]), "PLx", ["a"])["actions"][0]["addedVideoId"] == "a"
    assert edit.served_skip(toast("a"), "PLx", ["a"]) is None, "Add anyway is never a skip"


def test_skip_duplicates_when_none_is_there_is_one_add():
    worker = signed_in(add_playlist_items=[ADDED])
    reply = call(worker, "addToPlaylist", playlistId="PLx", videoIds=["c"], skipDuplicates=True)
    assert reply["result"] == {"added": [{"videoId": "c", "setVideoId": "S3"}], "skipped": []}
    assert len(worker.yt.edits()) == 1


def test_a_refusal_naming_no_song_reads_the_playlist_too():
    unnamed = toast("a")
    del next(edit._walk(unnamed["actions"], "addedVideoId"))["addedVideoId"]
    assert edit.duplicates(unnamed) == []
    worker = signed_in(add_playlist_items=[unnamed, ADDED], get_playlist=holding("a"))
    reply = call(worker, "addToPlaylist", playlistId="PLx", videoIds=["a", "b"], skipDuplicates=True)
    assert reply["result"]["skipped"] == ["a"]
    assert worker.yt.edits()[-1] == ("add_playlist_items", ("PLx", ["b"]), {"duplicates": False})
    worker = signed_in(add_playlist_items=[unnamed], get_playlist=holding())
    reply = call(worker, "addToPlaylist", playlistId="PLx", videoIds=["a", "b"], skipDuplicates=True)
    assert reply["error"]["code"] == "UPSTREAM", "the playlist shows none of them: nothing more is sent"
    assert [c[0] for c in worker.yt.edits()] == ["add_playlist_items", "get_playlist"]


def test_skip_duplicates_failures_keep_their_codes():
    worker = signed_in(_send_request={"status": "STATUS_FAILED"})
    reply = call(worker, "addToPlaylist", playlistId="PLx", sourcePlaylistId="PLs", skipDuplicates=True)
    assert reply["error"]["code"] == "UPSTREAM"
    worker = signed_in(add_playlist_items=[toast("a"), toast("b")])
    reply = call(worker, "addToPlaylist", playlistId="PLx", videoIds=["a", "b"], skipDuplicates=True)
    assert reply["error"]["code"] == "DUPLICATE", "a refusal of the rest is still a duplicate"


@pytest.mark.parametrize("params", [
    {"videoIds": ["a"], "skipDuplicates": True, "duplicates": True},
    {"videoIds": ["a"], "sourcePlaylistId": "PLs", "skipDuplicates": True},
    {"videoIds": ["a"], "skipDuplicates": "yes"},
])
def test_bad_skip_duplicates_never_reach_youtube(params):
    worker = signed_in(add_playlist_items=ADDED)
    assert call(worker, "addToPlaylist", playlistId="PLx", **params)["error"]["code"] == "BAD_REQUEST"
    assert worker.yt.edits() == []


# ---- Remove custom image ----------------------------------------------------------------------

REMOVE_EDIT = {"playlistId": "PLx", "actions": [{"action": "ACTION_REMOVE_CUSTOM_THUMBNAIL"}], "params": "opaque"}


def owned_header(menu_items):
    """An owned playlist page whose header cover has an edit button with
    ``menu_items`` (as measured: Choose from library, Remove custom image)."""
    button = {"buttonRenderer": {"icon": {"iconType": "EDIT"}, "command": {"openPopupAction": {"popup": {
        "menuPopupRenderer": {"items": menu_items}}, "popupType": "DROPDOWN"}}}}
    header = {"musicResponsiveHeaderRenderer": {"title": {"runs": [{"text": "Mine"}]}, "thumbnailEditButton": button}}
    return {"contents": {"twoColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": [{"musicEditablePlaylistDetailHeaderRenderer": {
            "header": header, "editHeader": {"musicPlaylistEditHeaderRenderer": {}}}}]}}}}]}}}


CHOOSE = {"menuServiceItemRenderer": {"text": {"simpleText": "Choose from library"}, "icon": {"iconType": "IMAGE"},
                                      "serviceEndpoint": {"commandExecutorCommand": {"commands": [
                                          {"openPopupAction": {"popup": {}}}]}}}}
REMOVE = {"menuServiceItemRenderer": {"text": {"simpleText": "Remove custom image"}, "icon": {"iconType": "DELETE"},
                                      "serviceEndpoint": {"playlistEditEndpoint": REMOVE_EDIT}}}


def test_remove_custom_image_is_offered_only_when_served():
    assert edit.settings(owned_header([CHOOSE, REMOVE]), "PLx")["removeImage"] == "Remove custom image"
    assert edit.settings(owned_header([CHOOSE]), "PLx")["removeImage"] is None
    assert edit.settings(owned_header([]), "PLx")["removeImage"] is None
    # Matched by the endpoint, never the words.
    renamed = copy.deepcopy(REMOVE)
    renamed["menuServiceItemRenderer"]["text"] = {"simpleText": "Benutzerdefiniertes Bild entfernen"}
    assert edit.settings(owned_header([renamed]), "PLx")["removeImage"] == "Benutzerdefiniertes Bild entfernen"


def test_remove_custom_image_sends_the_served_edit():
    worker = signed_in(_send_request=[owned_header([CHOOSE, REMOVE]), {"status": "STATUS_SUCCEEDED"}])
    reply = call(worker, "removePlaylistImage", playlistId="VLPLx")
    assert reply["ok"] and reply["result"] == {"playlistId": "PLx"}
    assert worker.yt.edits() == [("_send_request", ("browse", {"browseId": "VLPLx"}), {}),
                                 ("_send_request", ("browse/edit_playlist", REMOVE_EDIT), {})]


def test_remove_custom_image_not_served_sends_nothing():
    worker = signed_in(_send_request=[owned_header([CHOOSE])])
    reply = call(worker, "removePlaylistImage", playlistId="PLx")
    assert reply["error"]["code"] == "NOT_FOUND" and len(worker.yt.edits()) == 1


def test_remove_custom_image_failures():
    worker = signed_in(_send_request=[owned_header([REMOVE]), {"status": "STATUS_FAILED"}])
    assert call(worker, "removePlaylistImage", playlistId="PLx")["error"]["code"] == "UPSTREAM"
    other = copy.deepcopy(REMOVE)
    other["menuServiceItemRenderer"]["serviceEndpoint"]["playlistEditEndpoint"]["playlistId"] = "PLother"
    worker = signed_in(_send_request=[owned_header([other])])
    assert call(worker, "removePlaylistImage", playlistId="PLx")["error"]["code"] == "UPSTREAM"
    assert len(worker.yt.edits()) == 1, "never an edit of another playlist"
    assert call(ytm.Worker(FakeYT(), "none"), "removePlaylistImage", playlistId="PLx")["error"]["code"] == "AUTH_REQUIRED"
    assert call(signed_in(), "removePlaylistImage")["error"]["code"] == "BAD_REQUEST"


def test_remove_custom_image_mutates_and_drops_the_playlist():
    assert "removePlaylistImage" in ytm.MUTATES and "removePlaylistImage" not in ytm.CACHEABLE
    triples = ytm.INVALIDATES["removePlaylistImage"]
    assert ("playlist", "playlistId", "playlistId") in triples and ("libraryPlaylists", None, None) in triples
