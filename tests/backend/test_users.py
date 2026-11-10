"""ytm_users (S9): profile pages, your channel and the library's profiles.

The profile page is recorded signed out (a public profile found by the
anonymous 'lofi' search); your channel, its account menu and the library's
profiles are private, so they are hand-built in the served shapes with
made-up names. Subscribing is an account edit: pinned against a fake client.
"""

import copy

import pytest

import ytm
import ytm_cache as caches
import ytm_pages as pages
import ytm_raw as raw
import ytm_users as users
from test_library import continuation, page_type
from test_podcasts import channel_row
from test_worker import THUMBS

PROFILE = "UCyiSb0FWStgbPE1CfC0WGTw"
MINE = "UCmine000000000000000000"
FAKE_NAME = "Zebulon Quackenbush"


class YT:
    """Answers ``browse`` by browseId from ``pages`` (a response, or a list
    served in turn, the last repeated) and the account menu from ``menu``;
    get_account_info sends its request through ``_send_request`` as
    ytmusicapi does. Other methods from ``methods``. Records everything."""

    def __init__(self, pages=None, menu=None, **methods):
        self.pages = {key: list(value) if isinstance(value, list) else [value] for key, value in (pages or {}).items()}
        self.menu = menu
        self.methods = methods
        self.sent = []
        self.calls = []

    def _send_request(self, endpoint, body, additionalParams=""):
        self.sent.append((endpoint, dict(body)))
        if endpoint == "account/account_menu":
            return copy.deepcopy(self.menu)
        answers = self.pages[body.get("browseId")]
        return copy.deepcopy(answers.pop(0) if len(answers) > 1 else answers[0])

    def get_account_info(self):
        self.calls.append(("get_account_info", (), {}))
        response = self._send_request("account/account_menu", {})
        if response is None:
            raise KeyError("activeAccountHeaderRenderer")
        return {"accountName": FAKE_NAME, "channelHandle": "@zq", "accountPhotoUrl": None}

    def __getattr__(self, name):
        if name.startswith("_") or name not in self.methods:
            raise AttributeError(name)

        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.methods[name]

        return method

    def browses(self):
        return [body["browseId"] for endpoint, body in self.sent if endpoint == "browse"]

    def menus(self):
        return [endpoint for endpoint, _ in self.sent if endpoint == "account/account_menu"]


def worker_for(pages=None, menu=None, auth="ok", cache=None, **methods):
    return ytm.Worker(raw.install(YT(pages, menu, **methods)), auth, cache=cache)


def call(worker, method, **params):
    return worker.handle({"id": 9, "method": method, "params": params})


def result(worker, method, **params):
    reply = call(worker, method, **params)
    assert reply["ok"], reply
    return reply["result"]


# ---- builders ----------------------------------------------------------------------------


def link(icon, title, endpoint):
    return {"compactLinkRenderer": {"icon": {"iconType": icon}, "title": {"runs": [{"text": title}]},
                                    "navigationEndpoint": endpoint}}


def account_menu(*items):
    """account/account_menu as served: the header names the account; the
    links come in sections (only their endpoints say where they go)."""
    header = {"activeAccountHeaderRenderer": {"accountName": {"runs": [{"text": FAKE_NAME}]},
                                              "channelHandle": {"runs": [{"text": "@zq"}]}}}
    sections = [{"multiPageMenuSectionRenderer": {"items": list(items)}},
                {"multiPageMenuSectionRenderer": {"items": [
                    link("WATCH_HISTORY", "History", {"browseEndpoint": {"browseId": "FEmusic_history"}}),
                    link("SETTINGS", "Settings", {"applicationSettingsEndpoint": {}})]}}]
    return {"actions": [{"openPopupAction": {"popup": {"multiPageMenuRenderer": {"header": header,
                                                                                 "sections": sections}}}}]}


MENU = account_menu(
    # Labels are made up and in another language: only the endpoint counts.
    link("ACCOUNT_BOX", "Ma chaîne", page_type(MINE, "USER_CHANNEL")),
    link("MONETIZATION_ON", "Premium", {"browseEndpoint": {"browseId": "SPunlimited"}}),
    link("EXIT_TO_APP", "Your channel", {"signOutEndpoint": {}}))


def carousel(title, items, more=None):
    header = {"title": {"runs": [{"text": title, **({"navigationEndpoint": more} if more else {})}]}}
    return {"musicCarouselShelfRenderer": {"header": {"musicCarouselShelfBasicHeaderRenderer": header},
                                           "contents": items}}


def video(n):
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": f"Video {n}"}]}, "subtitle": {"runs": [{"text": "12 views"}]},
        "navigationEndpoint": {"watchEndpoint": {"videoId": f"vid{n:08d}", "watchEndpointMusicSupportedConfigs": {
            "watchEndpointMusicConfig": {"musicVideoType": "MUSIC_VIDEO_TYPE_UGC"}}}},
        "thumbnailRenderer": THUMBS}}


def playlist_tile(n):
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": f"Playlist {n}", "navigationEndpoint": page_type(f"VLPL{n:04d}", "PLAYLIST")}]},
        "subtitle": {"runs": [{"text": "Playlist"}]},
        "navigationEndpoint": page_type(f"VLPL{n:04d}", "PLAYLIST"), "thumbnailRenderer": THUMBS}}


def artist_tile(n):
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": f"Artist {n}", "navigationEndpoint": page_type(f"UCart{n}", "ARTIST")}]},
        "subtitle": {"runs": [{"text": "12 plays"}]},
        "navigationEndpoint": page_type(f"UCart{n}", "ARTIST"), "thumbnailRenderer": THUMBS}}


def mix_card(title):
    return {"musicCardShelfRenderer": {
        "title": {"runs": [{"text": title, "navigationEndpoint": page_type("VLRDATjuUCmine", "PLAYLIST")}]},
        "subtitle": {"runs": [{"text": "Made for sharing."}]}, "thumbnail": THUMBS,
        "onTap": page_type("VLRDATjuUCmine", "PLAYLIST")}}


def channel_page(title, rows, enabled=True, subscribed=False, channel_id=MINE):
    button = {"subscribeButtonRenderer": {"channelId": channel_id, "subscribed": subscribed, "enabled": enabled,
                                          "subscriberCountText": {"runs": [{"text": "2"}]},
                                          "longSubscriberCountText": {"runs": [{"text": "2 subscribers"}]}}}
    return {"header": {"musicVisualHeaderRenderer": {"title": {"runs": [{"text": title}]},
                                                     "subscriptionButton": button, "foregroundThumbnail": THUMBS}},
            "contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
                "sectionListRenderer": {"contents": rows}}}}]}}}


def own_page():
    return channel_page(FAKE_NAME, [mix_card(f"{FAKE_NAME}'s Mix"), carousel("Artists on repeat", [artist_tile(1)]),
                                    carousel("Playlists", [playlist_tile(1), playlist_tile(2)])], enabled=False)


def profiles_page(rows, token=None, message="Only profiles with music activity are shown.", bare=False):
    """The library's profiles as served: the note comes after the shelf,
    inside an itemSectionRenderer (``bare``: a section-list row itself)."""
    contents = []
    if rows is not None:
        shelf = {"contents": rows}
        if token:
            shelf["continuations"] = [{"nextContinuationData": {"continuation": token}}]
        contents.append({"musicShelfRenderer": shelf})
    if message:
        note = {"messageRenderer": {"text": {"runs": [{"text": message}]}}}
        contents.append(note if bare else {"itemSectionRenderer": {"contents": [note]}})
    return {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": contents}}}}]}}}


# ---- a profile's page ------------------------------------------------------------------------


def test_a_profile_has_its_rows_keyed_by_what_they_hold_and_see_all_by_channel_and_params(load_fixture):
    worker = worker_for({PROFILE: load_fixture("user.json")}, auth="none")
    page = result(worker, "user", channelId=PROFILE)
    assert worker.yt.sent == [("browse", {"browseId": PROFILE})], "one request: get_user's"
    assert page["kind"] == "user" and page["id"] == PROFILE and page["round"] and page["title"]
    assert page["header"] == {"channelId": PROFILE, "subscribeId": PROFILE, "subscribed": False,
                              "subscribers": "164 subscribers", "own": False}
    assert page["subtitle"] == "164 subscribers" and page["thumbLarge"]
    # Its cover is the avatar (foregroundThumbnail), never the banner (thumbnail).
    header = load_fixture("user.json")["header"]["musicVisualHeaderRenderer"]
    avatar = header["foregroundThumbnail"]["musicThumbnailRenderer"]["thumbnail"]["thumbnails"][-1]["url"]
    banner = header["thumbnail"]["musicThumbnailRenderer"]["thumbnail"]["thumbnails"][-1]["url"]
    assert page["thumbLarge"].split("=")[0] == avatar.split("=")[0] != banner.split("=")[0]
    videos, playlists = page["sections"]
    assert (videos["key"], playlists["key"]) == ("videos", "playlists")
    assert len(videos["items"]) == 10 and all(v["type"] == "track" and v["kind"] == "video" for v in videos["items"])
    assert all(p["type"] == "playlist" and p["playlistId"].startswith("PL") for p in playlists["items"])
    # See all is the channel with params (no page type): the shell's browse page.
    more = videos["more"]
    assert more["browseId"] == PROFILE and more["params"] and more["pageType"] is None
    assert playlists["more"] is None, "two playlists: no See all, as on the web"


def test_a_profiles_see_all_reads_through_browse_page(load_fixture):
    more = users.build_user(PROFILE, load_fixture("user.json"))["sections"][0]["more"]
    grid = {"header": {"musicHeaderRenderer": {"title": {"runs": [{"text": "Someone"}]}}},
            "contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
                "sectionListRenderer": {"contents": [{"gridRenderer": {"items": [video(i) for i in range(30)]}}]}}}}]}}}
    worker = worker_for({PROFILE: grid}, auth="none")
    page = result(worker, "browsePage", browseId=more["browseId"], params=more["params"])
    assert worker.yt.sent == [("browse", {"browseId": PROFILE, "params": more["params"]})]
    assert len(page["sections"][0]["items"]) == 30 and page["round"]


def test_a_profile_page_is_cached_and_a_subscribe_drops_it(tmp_path, load_fixture):
    cache = caches.DiskCache(tmp_path / "data")
    worker = worker_for({PROFILE: load_fixture("user.json")}, auth="none", cache=cache)
    result(worker, "user", channelId=PROFILE, cache="refresh")
    assert cache.get("user", {"channelId": PROFILE}) is not None, "a public profile is kept on disk"
    cache.put("user", {"channelId": "UCother"}, {"type": "page"})
    cache.put("libraryProfiles", {}, {"type": "page"})
    cache.put("browsePage", {"browseId": users.PROFILES_BROWSE_ID}, {"type": "page"})
    worker = worker_for(cache=cache, subscribe_artist={}, unsubscribe_artists={})
    worker.verified = worker.own_channel_known = True
    reply = call(worker, "subscribe", channelId=PROFILE, pageChannelId=PROFILE, subscribe=True)
    assert reply["ok"], reply
    assert worker.yt.calls == [("subscribe_artist", (PROFILE,), {})]
    assert cache.get("user", {"channelId": PROFILE}) is None
    assert cache.get("user", {"channelId": "UCother"}) is not None
    assert cache.get("libraryProfiles", {}) is None
    assert cache.get("browsePage", {"browseId": users.PROFILES_BROWSE_ID}) is None
    for triple in (("user", "pageChannelId", "channelId"), ("libraryProfiles", None, None)):
        assert triple in ytm.INVALIDATES["subscribe"], triple


def test_your_own_channel_has_no_subscribe_and_never_reaches_the_disk(tmp_path):
    cache = caches.DiskCache(tmp_path / "data")
    worker = worker_for({MINE: own_page()}, cache=cache)
    worker.verified = worker.own_channel_known = True  # the account menu was read (and named no channel)
    page = result(worker, "user", channelId=MINE, cache="refresh")
    assert page["header"]["own"] is True and page["header"]["subscribed"] is None, "its button is disabled"
    assert cache.get("user", {"channelId": MINE}) is None
    assert FAKE_NAME not in "".join(p.read_text(errors="ignore") for p in (tmp_path / "data").rglob("*") if p.is_file())
    # Your mix (the card at the top) leads, as a playlist.
    mix, artists, playlists = page["sections"]
    assert mix["key"] == "mix" and mix["title"] is None
    assert artists["key"] == "artists" and artists["items"][0]["type"] == "artist"
    assert mix["items"][0]["type"] == "playlist" and mix["items"][0]["playlistId"] == "RDATjuUCmine"
    assert playlists["key"] == "playlists" and len(playlists["items"]) == 2


def test_a_signed_in_profile_is_kept_off_the_disk_until_the_account_menu_is_read(tmp_path):
    cache = caches.DiskCache(tmp_path / "data")
    public = channel_page("Someone", [carousel("Videos", [video(1)])], channel_id="UCsomeone")
    worker = worker_for({"UCsomeone": public, MINE: channel_page(FAKE_NAME, [], enabled=True)}, MENU, cache=cache)
    result(worker, "user", channelId="UCsomeone", cache="refresh")
    assert cache.get("user", {"channelId": "UCsomeone"}) is None, "it could be yours: nothing says yet"
    worker.verify_session()
    assert worker.own_channel == MINE
    result(worker, "user", channelId="UCsomeone", cache="refresh")
    assert cache.get("user", {"channelId": "UCsomeone"}) is not None
    # Your channel by its id, even with a button that is not disabled.
    page = result(worker, "user", channelId=MINE, cache="refresh")
    assert page["header"]["own"] is True and cache.get("user", {"channelId": MINE}) is None


def test_your_channels_see_all_pages_never_reach_the_disk(tmp_path):
    cache = caches.DiskCache(tmp_path / "data")
    grid = {"header": {"musicHeaderRenderer": {"title": {"runs": [{"text": FAKE_NAME}]}}},
            "contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
                "sectionListRenderer": {"contents": [{"gridRenderer": {"items": [playlist_tile(1)]}}]}}}}]}}}
    worker = worker_for({MINE: grid, "UCsomeone": grid}, MENU, cache=cache)
    worker.verify_session()
    result(worker, "browsePage", browseId=MINE, params="P", cache="refresh")
    assert cache.get("browsePage", {"browseId": MINE, "params": "P"}) is None
    result(worker, "browsePage", browseId="UCsomeone", params="P", cache="refresh")
    assert cache.get("browsePage", {"browseId": "UCsomeone", "params": "P"}) is not None, "another channel's is kept"


# ---- your channel ------------------------------------------------------------------------------


def test_your_channel_comes_from_the_account_menu_the_session_check_reads(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("YTM_DEBUG", "1")
    cache = caches.DiskCache(tmp_path / "data")
    worker = worker_for({MINE: own_page()}, MENU, cache=cache)
    page = result(worker, "myChannel")
    assert worker.yt.menus() == ["account/account_menu"] and worker.yt.browses() == [MINE], "no extra request"
    assert page["kind"] == "user" and page["id"] == MINE and page["header"]["own"] is True
    assert page["title"] == FAKE_NAME, "the shell shows it; it never leaves memory"
    result(worker, "myChannel", cache="refresh")
    assert worker.yt.menus() == ["account/account_menu"], "the session is checked once per worker"
    assert "myChannel" not in ytm.CACHEABLE and cache.get("myChannel", {}) is None
    assert FAKE_NAME not in capsys.readouterr().err, "YTM_DEBUG logs no names"
    assert not any(FAKE_NAME in p.read_text(errors="ignore") for p in (tmp_path / "data").rglob("*") if p.is_file())


def test_a_session_checked_another_way_has_its_account_menu_read_once(tmp_path):
    # A switch of identity (S16) checks the new one with get_account_info
    # itself and marks the session verified: the menu was never captured.
    cache = caches.DiskCache(tmp_path / "data")
    worker = worker_for({MINE: own_page(), "UCsomeone": channel_page("Someone", [], channel_id="UCsomeone")}, MENU,
                        cache=cache)
    worker.verified = True
    result(worker, "user", channelId="UCsomeone", cache="refresh")
    assert cache.get("user", {"channelId": "UCsomeone"}) is None, "unknown yet: kept off the disk"
    assert result(worker, "myChannel")["id"] == MINE
    result(worker, "myChannel")
    assert worker.yt.menus() == ["account/account_menu"], "read once, when it was needed"
    result(worker, "user", channelId="UCsomeone", cache="refresh")
    assert cache.get("user", {"channelId": "UCsomeone"}) is not None


def test_the_channel_link_is_matched_by_its_endpoint_never_its_label():
    assert users.own_channel_id(MENU) == MINE
    # A link titled "Your channel" that goes elsewhere is not it; a channel
    # link that is no user channel page counts only when there is no other.
    assert users.own_channel_id(account_menu(link("ACCOUNT_BOX", "Your channel", {"signOutEndpoint": {}}))) is None
    other = account_menu(link("X", "a", page_type("UCartist", "ARTIST")), link("Y", "b", page_type(MINE, "USER_CHANNEL")))
    assert users.own_channel_id(other) == MINE
    assert users.own_channel_id(account_menu(link("X", "a", {"browseEndpoint": {"browseId": "UCplain"}}))) == "UCplain"
    assert users.own_channel_id(None) is None and users.own_channel_id({"actions": []}) is None


def test_an_account_menu_without_a_channel_says_so():
    worker = worker_for(menu=account_menu(link("EXIT_TO_APP", "Sign out", {"signOutEndpoint": {}})))
    reply = call(worker, "myChannel")
    assert reply["error"]["code"] == "NOT_FOUND" and reply["error"]["noChannel"] is True
    assert reply["error"]["message"] == users.NO_CHANNEL
    assert worker.yt.browses() == [], "nothing to read"
    assert call(worker_for(auth="none"), "myChannel")["error"]["code"] == "AUTH_REQUIRED"


def test_a_new_client_forgets_the_channel(monkeypatch):
    worker = worker_for({MINE: own_page()}, MENU)
    worker.verify_session()
    assert worker.own_channel == MINE
    monkeypatch.setattr(ytm.auth, "load_client", lambda *a, **k: (raw.install(YT(menu=None)), "ok"))
    worker.load()
    assert worker.own_channel is None and worker.own_channel_known is False and worker.verified is False


# ---- the library's profiles --------------------------------------------------------------------


def test_library_profiles_are_profiles_with_the_note_youtube_serves():
    rows = [channel_row(i, subtitle=f"@handle{i}") for i in range(3)] + [channel_row(3, "ARTIST", "2M subscribers")]
    worker = worker_for({users.PROFILES_BROWSE_ID: profiles_page(rows)}, MENU)
    page = result(worker, "libraryProfiles")
    assert worker.yt.sent[-1] == ("browse", {"browseId": users.PROFILES_BROWSE_ID, "params": users.PROFILES_PARAMS})
    items = page["sections"][0]["items"]
    assert [i["type"] for i in items] == ["profile", "profile", "profile", "artist"]
    assert items[0] == dict(items[0], channelId="UCc0000", name="Channel 0", subtitle="@handle0")
    assert page["message"] == "Only profiles with music activity are shown." and page["cursor"] is None
    assert page["kind"] == "libraryProfiles" and page["orders"] == []
    assert call(worker_for(auth="none"), "libraryProfiles")["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("bare", [False, True])
def test_library_profiles_empty_is_the_served_message_and_no_items(bare):
    worker = worker_for({users.PROFILES_BROWSE_ID: profiles_page(None, message="No profiles yet", bare=bare)}, MENU)
    page = result(worker, "libraryProfiles")
    assert page["sections"] == [] and page["message"] == "No profiles yet" and page["cursor"] is None


def test_library_profiles_page_on_by_cursor():
    first = profiles_page([channel_row(i) for i in range(30)], token="T1")
    rest = continuation("musicShelfRenderer", [channel_row(i) for i in range(30, 60)], "T2")
    last = continuation("musicShelfRenderer", [channel_row(i) for i in range(60, 70)])
    worker = worker_for({users.PROFILES_BROWSE_ID: [first, rest, last]}, MENU)
    page = result(worker, "libraryProfiles")
    assert len(page["sections"][0]["items"]) == 60 and page["cursor"]
    more = result(worker, "libraryProfiles", cursor=page["cursor"])
    assert [i["channelId"] for i in more["sections"][0]["items"]] == [f"UCc{i:04d}" for i in range(60, 70)]
    assert more["cursor"] is None and more["message"] is None


# ---- registry ------------------------------------------------------------------------------


def test_the_methods_are_registered_and_the_recorder_is_anonymous():
    assert {"user", "libraryProfiles"} <= ytm.CACHEABLE and "myChannel" not in ytm.CACHEABLE
    for name in ("user", "myChannel", "libraryProfiles"):
        assert ytm.LANES[name] == "pages" and name not in ytm.MUTATES, name
    assert ytm.RECORDERS["user"].__module__ == "ytm_users"
    assert pages.ORDER_DEPENDENTS[users.PROFILES_BROWSE_ID] == ("libraryProfiles",)


@pytest.mark.parametrize("enabled, own", [(False, True), (True, False), (None, False)])
def test_a_disabled_subscribe_button_is_your_channel(enabled, own):
    response = channel_page("x", [], enabled=enabled)
    if enabled is None:
        del response["header"]["musicVisualHeaderRenderer"]["subscriptionButton"]["subscribeButtonRenderer"]["enabled"]
    assert pages.is_own_channel(None, "UCx", response) is own
