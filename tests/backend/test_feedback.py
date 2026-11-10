"""ytm_feedback (S14): Listen again pins, Not interested, the taste profile.

The menus are hand-built (signed-in menus hold account-bound tokens, so no
fixture has them), and every edit is pinned against a fake client: the real
ones change the account's recommendations (a Not interested only comes back
through its toast's Undo token, a taste profile addition not at all)."""

import pytest
from ytmusicapi.exceptions import YTMusicServerError, YTMusicUserError

import ytm
import ytm_cache as caches
import ytm_feedback as feedback
import ytm_home as home
import ytm_raw as raw


class FakeYT:
    """Records every call; `responses[name]` is a value, a callable or an exception."""

    def __init__(self, **responses):
        self.responses = dict({"get_account_info": {"accountName": "x"}}, **responses)
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

    def edits(self):
        return [c for c in self.calls if c[0] != "get_account_info"]


def call(worker, method, **params):
    return worker.handle({"id": 9, "method": method, "params": params})


def signed_in(**responses):
    return ytm.Worker(FakeYT(**responses), "ok")


PROCESSED = {"feedbackResponses": [{"isProcessed": True}]}
UNPROCESSED = {"feedbackResponses": [{"isProcessed": False}]}


# ---- hand-built menus ---------------------------------------------------------------------


def fb(token):
    return {"feedbackEndpoint": {"feedbackToken": token}}


def keep(icon="KEEP", toggled=None, text="Pin to Listen again", default="PIN", toggle="UNPIN"):
    data = {"defaultText": {"runs": [{"text": text}]}, "defaultIcon": {"iconType": icon},
            "toggledIcon": {"iconType": "KEEP_OFF" if icon == "KEEP" else "KEEP"},
            "defaultServiceEndpoint": fb(default), "toggledServiceEndpoint": fb(toggle)}
    if toggled is not None:
        data["isToggled"] = toggled
    return {"toggleMenuServiceItemRenderer": data}


def service(icon, token, text="x", undo=None, toast="Removed from Home"):
    """A menu entry; ``undo`` adds the toast the web opens, as served: its
    Undo button's own feedback token after a hideEnclosingAction."""
    endpoint = fb(token)
    if undo is not None:
        button = {"buttonRenderer": {"text": {"simpleText": "Undo"}, "command": {"feedbackEndpoint": {
            "feedbackToken": undo, "actions": [{"replaceEnclosingAction": {"targetId": "t"}}]}}}}
        endpoint["feedbackEndpoint"]["actions"] = [
            {"hideEnclosingAction": {"hack": True}},
            {"openPopupAction": {"popupType": "TOAST", "popup": {"notificationActionRenderer": {
                "responseText": {"runs": [{"text": toast}]}, "actionButton": button}}}}]
    return {"menuServiceItemRenderer": {"text": {"runs": [{"text": text}]}, "icon": {"iconType": icon},
                                        "serviceEndpoint": endpoint}}


def nav_entry(icon, text):
    return {"menuNavigationItemRenderer": {"text": {"runs": [{"text": text}]}, "icon": {"iconType": icon},
                                           "navigationEndpoint": {"browseEndpoint": {"browseId": "x"}}}}


def menu(*entries):
    return {"menuRenderer": {"items": list(entries)}}


def test_the_pin_is_found_by_its_icon_never_its_text():
    # An entry titled like the pin with another icon is not it; a KEEP
    # entry in another language is.
    found = raw.pin_state(menu(service("SHARE", "WRONG", text="Pin to Listen again"),
                               keep(text="An Wieder anhören anheften", default="RIGHT")))
    assert found == {"pinned": False, "listenAgainTokens": {"pin": "RIGHT", "unpin": "UNPIN"}}
    assert raw.pin_state(menu(service("SHARE", "x", text="Pin to Listen again"))) is None
    assert raw.pin_state(menu()) is None and raw.pin_state(None) is None


def test_the_pin_state_follows_the_toggle():
    assert raw.pin_state(menu(keep(toggled=True)))["pinned"] is True
    assert raw.pin_state(menu(keep(toggled=False)))["pinned"] is False
    # KEEP_OFF is served on a pinned item: its default endpoint unpins.
    assert raw.pin_state(menu(keep("KEEP_OFF", default="UNPIN_D", toggle="PIN_T"))) == {
        "pinned": True, "listenAgainTokens": {"pin": "PIN_T", "unpin": "UNPIN_D"}}
    # A toggle without tokens (a scrubbed fixture) offers nothing.
    bare = {"toggleMenuServiceItemRenderer": {"defaultIcon": {"iconType": "KEEP"}}}
    assert raw.pin_state(menu(bare)) is None


def test_home_feedback_is_found_by_icon():
    found = raw.item_feedback(menu(keep(), service("NOT_INTERESTED", "NI", text="Kein Interesse"),
                                   service("REMOVE_CIRCLE", "DR", text="Don't recommend artist")))
    assert found == {"notInterested": "NI", "dontRecommend": "DR", "notInterestedUndo": None, "dontRecommendUndo": None}
    assert raw.item_feedback(menu(service("REMOVE", "OLD"))) == {
        "notInterested": None, "dontRecommend": "OLD", "notInterestedUndo": None, "dontRecommendUndo": None}
    assert raw.item_feedback(menu(service("SHARE", "x", text="Not interested"), keep())) is None


def test_the_undo_token_is_read_from_the_toast_by_structure():
    # The toast in another language, an Undo button with any text: found by
    # where it sits. Each entry keeps its own.
    found = raw.item_feedback(menu(service("NOT_INTERESTED", "NI", undo="NI_UNDO", toast="Von Startseite entfernt"),
                                   service("REMOVE_CIRCLE", "DR", undo="DR_UNDO")))
    assert found == {"notInterested": "NI", "dontRecommend": "DR",
                     "notInterestedUndo": "NI_UNDO", "dontRecommendUndo": "DR_UNDO"}
    # A toast without a button, odd action shapes: no Undo, the entry stays.
    odd = service("NOT_INTERESTED", "NI", undo="U")
    actions = odd["menuServiceItemRenderer"]["serviceEndpoint"]["feedbackEndpoint"]["actions"]
    del actions[1]["openPopupAction"]["popup"]["notificationActionRenderer"]["actionButton"]
    actions.extend(["text", None, {"openPopupAction": "not a dict"}])
    assert raw.item_feedback(menu(odd)) == {"notInterested": "NI", "dontRecommend": None,
                                            "notInterestedUndo": None, "dontRecommendUndo": None}
    # An Undo token alone (no token to send first) offers nothing.
    bare = service("NOT_INTERESTED", "", undo="U")
    assert raw.item_feedback(menu(bare)) is None


# Header shapes: an album's or playlist's buttons, your own playlist's
# editable wrapper, an artist's own header.
def tab_header(header):
    return {"contents": {"twoColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": [header]}}}}]}}}


def album_header(*entries):
    play = {"musicPlayButtonRenderer": {}}
    return tab_header({"musicResponsiveHeaderRenderer": {"buttons": [play, {"menuRenderer": {"items": list(entries)}}]}})


def own_header(*entries):
    inner = album_header(*entries)["contents"]["twoColumnBrowseResultsRenderer"]["tabs"][0]["tabRenderer"]["content"]
    inner = inner["sectionListRenderer"]["contents"][0]
    return tab_header({"musicEditablePlaylistDetailHeaderRenderer": {"header": inner}})


def artist_header(*entries):
    return {"header": {"musicImmersiveHeaderRenderer": {"title": {"runs": [{"text": "Radiohead"}]},
                                                        "menu": {"menuRenderer": {"items": list(entries)}}}}}


def test_header_pin_of_album_playlist_own_playlist_and_artist():
    assert raw.header_pin(album_header(nav_entry("SHARE", "Share"), keep())) == {
        "pinned": False, "listenAgainTokens": {"pin": "PIN", "unpin": "UNPIN"}}
    assert raw.header_pin(own_header(keep("KEEP_OFF", default="U", toggle="P")))["pinned"] is True
    assert raw.header_pin(artist_header(keep(toggled=True)))["pinned"] is True
    assert raw.header_pin(album_header(nav_entry("SHARE", "Share"))) is None, "no pin in the menu"
    assert raw.header_pin({"contents": {}}) is None and raw.header_pin(None) is None


class Hooked:
    """A client ytm_raw can hook: each read sends `response` through _send_request."""

    def __init__(self, response, **data):
        self.response = response
        self.data = data

    def _send_request(self, endpoint, body, additionalParams=""):
        return self.response

    def _read(self, name):
        self._send_request("browse", {})
        return self.data[name]

    def get_album(self, browse_id):
        return self._read("album")

    def get_playlist(self, playlist_id, limit=100, related=False):
        return self._read("playlist")

    def get_liked_songs(self, limit=100):
        return self._read("playlist")

    def get_artist(self, channel_id):
        return self._read("artist")

    def get_account_info(self):
        return {"accountName": "x"}


ALBUM = {"title": "OK Computer", "type": "Album", "audioPlaylistId": "OLAK5uy_ok", "trackCount": 1,
         "artists": [{"name": "Radiohead", "id": "UCr"}],
         "tracks": [{"videoId": "v1", "title": "Airbag", "trackNumber": 1, "artists": [{"name": "Radiohead", "id": "UCr"}]}]}
PLAYLIST = {"id": "PLx", "title": "Theirs", "owned": False, "trackCount": 1,
            "tracks": [{"videoId": "v1", "title": "One", "artists": [{"name": "A", "id": "UCa"}]}]}


def test_album_playlist_liked_and_artist_pages_carry_the_header_pin():
    pinned = {"pinned": True, "listenAgainTokens": {"pin": "P", "unpin": "U"}}
    worker = ytm.Worker(raw.install(Hooked(album_header(keep("KEEP_OFF", default="U", toggle="P")), album=ALBUM)), "ok")
    assert call(worker, "album", browseId="MPREb_ok")["result"]["header"]["pin"] == pinned
    worker = ytm.Worker(raw.install(Hooked(own_header(keep("KEEP_OFF", default="U", toggle="P")),
                                           playlist=dict(PLAYLIST, owned=True))), "ok")
    assert call(worker, "playlist", playlistId="PLx")["result"]["header"]["pin"] == pinned
    worker = ytm.Worker(raw.install(Hooked(album_header(keep()), playlist=PLAYLIST)), "ok")
    assert call(worker, "liked")["result"]["header"]["pin"]["pinned"] is False
    worker = ytm.Worker(raw.install(Hooked(artist_header(keep()), artist={"name": "Radiohead", "channelId": "UCr"})), "ok")
    assert call(worker, "artist", channelId="UCr")["result"]["header"]["pin"]["pinned"] is False
    worker = ytm.Worker(raw.install(Hooked(album_header(), album=ALBUM)), "ok")
    assert call(worker, "album", browseId="MPREb_ok")["result"]["header"]["pin"] is None


def test_the_disk_copy_keeps_the_pin_state_but_no_token():
    page = {"type": "collection", "header": {"pin": {"pinned": True, "listenAgainTokens": {"pin": "P", "unpin": "U"}}},
            "tracks": [{"type": "track", "videoId": "v1", "pinned": False,
                        "listenAgainTokens": {"pin": "P1", "unpin": "U1"}}]}
    stripped = caches.strip_tokens(page)
    assert stripped["header"]["pin"] == {"pinned": True}
    assert stripped["tracks"][0] == {"type": "track", "videoId": "v1", "pinned": False}


# ---- Home cards --------------------------------------------------------------------------


def card(title, browse_id, page_type, entries):
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": title, "navigationEndpoint": {"browseEndpoint": {
            "browseId": browse_id,
            "browseEndpointContextSupportedConfigs": {"browseEndpointContextMusicConfig": {"pageType": page_type}}}}}]},
        "subtitle": {"runs": [{"text": "Album"}, {"text": " • "}, {"text": "Radiohead"}]},
        "navigationEndpoint": {"browseEndpoint": {
            "browseId": browse_id,
            "browseEndpointContextSupportedConfigs": {"browseEndpointContextMusicConfig": {"pageType": page_type}}}},
        "thumbnailRenderer": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [{"url": "https://x/1", "width": 60}]}}},
        "menu": {"menuRenderer": {"items": entries}}}}


def carousel(title, contents):
    return {"musicCarouselShelfRenderer": {
        "header": {"musicCarouselShelfBasicHeaderRenderer": {"title": {"runs": [{"text": title}]}}},
        "contents": contents}}


def test_home_cards_keep_their_pin_and_feedback_tokens():
    rows = [carousel("Listen again", [
        card("OK Computer", "MPREb_ok", "MUSIC_PAGE_TYPE_ALBUM",
             [keep(), service("NOT_INTERESTED", "NI1", undo="NU1"), service("REMOVE_CIRCLE", "DR1", undo="DU1")]),
        card("Pinned mix", "VLPLpinned", "MUSIC_PAGE_TYPE_PLAYLIST", [keep("KEEP_OFF", default="U2", toggle="P2")]),
        card("Plain", "MPREb_plain", "MUSIC_PAGE_TYPE_ALBUM", [nav_entry("SHARE", "Share")])])]
    items = home.shelves(home.parse_rows(rows))[0]["items"]
    assert items[0]["pinned"] is False and items[0]["listenAgainTokens"] == {"pin": "PIN", "unpin": "UNPIN"}
    assert items[0]["feedback"] == {"notInterested": "NI1", "dontRecommend": "DR1",
                                    "notInterestedUndo": "NU1", "dontRecommendUndo": "DU1"}
    assert items[1]["pinned"] is True and items[1]["listenAgainTokens"] == {"pin": "P2", "unpin": "U2"}
    assert "feedback" not in items[1]
    assert not any(key in items[2] for key in ("pinned", "listenAgainTokens", "feedback")), "nothing to add"
    # None of it reaches the disk copy.
    stripped = caches.strip_tokens({"shelves": [{"items": items}]})["shelves"][0]["items"]
    assert all("feedback" not in i and "listenAgainTokens" not in i for i in stripped)
    assert stripped[1]["pinned"] is True


def test_a_card_whose_menu_cannot_be_read_still_shows(monkeypatch):
    broken = card("Odd", "MPREb_odd", "MUSIC_PAGE_TYPE_ALBUM", [])
    broken["musicTwoRowItemRenderer"]["menu"] = {"menuRenderer": {"items": "not a list"}}
    items = home.shelves(home.parse_rows([carousel("Shelf", [broken])]))[0]["items"]
    assert [i["browseId"] for i in items] == ["MPREb_odd"] and "feedback" not in items[0]

    # A menu shape that makes the reader raise: the card is kept, without tokens.
    def boom(menu):
        raise KeyError("x")

    monkeypatch.setattr(raw, "pin_state", boom)
    odd = card("Odd", "MPREb_odd", "MUSIC_PAGE_TYPE_ALBUM", [keep()])
    items = home.shelves(home.parse_rows([carousel("Shelf", [odd])]))[0]["items"]
    assert [i["browseId"] for i in items] == ["MPREb_odd"] and "listenAgainTokens" not in items[0]


# ---- feedback ---------------------------------------------------------------------------


def test_feedback_sends_the_tokens_and_needs_every_one_processed():
    worker = signed_in(edit_song_library_status=PROCESSED)
    reply = call(worker, "feedback", tokens=["PIN"])
    assert reply["ok"] and reply["result"] == {"applied": 1}
    assert worker.yt.edits() == [("edit_song_library_status", (), {"feedbackTokens": ["PIN"]})]
    two = {"feedbackResponses": [{"isProcessed": True}, {"isProcessed": False}]}
    for response in (UNPROCESSED, two, {}, None):
        worker = signed_in(edit_song_library_status=response)
        reply = call(worker, "feedback", tokens=["A", "B"])
        assert not reply["ok"] and reply["error"]["code"] == "UPSTREAM", response
        assert reply["error"].get("stale") is True, "the page the token came from is out of date"
        assert "A" not in reply["error"]["message"]


@pytest.mark.parametrize("tokens", [None, [], "PIN", [""], ["  "], [5], ["x" * 5000], ["t"] * 11])
def test_feedback_checks_its_tokens_before_anything_is_sent(tokens):
    worker = signed_in(edit_song_library_status=PROCESSED)
    params = {} if tokens is None else {"tokens": tokens}
    reply = worker.handle({"id": 1, "method": "feedback", "params": params})
    assert reply["error"]["code"] == "BAD_REQUEST"
    # Not even the session check went out (a fresh worker checks it first).
    assert worker.yt.calls == []
    # Signed out, bad tokens are still BAD_REQUEST: they are checked first.
    reply = ytm.Worker(FakeYT(), "none").handle({"id": 1, "method": "feedback", "params": params})
    assert reply["error"]["code"] == "BAD_REQUEST"


def test_feedback_needs_a_session():
    reply = call(ytm.Worker(FakeYT(), "none"), "feedback", tokens=["PIN"])
    assert reply["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("status", ["HTTP 400: Bad Request", "HTTP 401: Unauthorized", "HTTP 403: Forbidden"])
def test_a_refused_feedback_keeps_the_session(status):
    # A 401 or 403 would otherwise read as an expired session (ytm.classify).
    worker = signed_in(edit_song_library_status=YTMusicServerError(f"Server returned {status}."))
    reply = call(worker, "feedback", tokens=["PIN"])
    assert reply["error"]["code"] == "UPSTREAM" and worker.auth == "ok", "a refused token is no lost session"


class Recorder:
    def __init__(self):
        self.dropped = []

    def invalidate(self, method, match=None):
        self.dropped.append((method, match))

    def epoch(self, method):
        return 0


def dropped(params):
    worker = signed_in(edit_song_library_status=PROCESSED)
    recorder = Recorder()
    worker._cache = recorder
    assert call(worker, "feedback", **params)["ok"]
    return recorder.dropped


def test_feedback_drops_home_listen_again_and_the_page_it_came_from():
    none = feedback._NONE
    plain = dropped({"tokens": ["NI"]})
    assert ("homeFeed", None) in plain and ("browsePage", {"browseId": "FEmusic_listen_again"}) in plain
    assert ("album", {"browseId": none}) in plain and ("artist", {"channelId": none}) in plain
    assert ("playlist", {"playlistId": none}) in plain and ("liked", {"limit": none}) in plain
    assert not any(match is None for method, match in plain if method != "homeFeed"), "a Home card drops no page"
    album = dropped({"tokens": ["P"], "browseId": "MPREb_ok"})
    assert ("album", {"browseId": "MPREb_ok"}) in album
    playlist = dropped({"tokens": ["P"], "playlistId": "VLPLx"})
    assert ("playlist", {"playlistId": "PLx"}) in playlist and ("browsePage", {"browseId": "VLPLx"}) in playlist
    liked = dropped({"tokens": ["P"], "playlistId": "LM"})
    assert ("liked", None) in liked
    artist = dropped({"tokens": ["P"], "channelId": "UCr"})
    assert ("artist", {"channelId": "UCr"}) in artist


# ---- the taste profile ----------------------------------------------------------------------


PROFILE = {"Radiohead": {"selectionValue": "tastebuilder_selection=/m/1", "impressionValue": "tastebuilder_impression=/m/1"},
           "Björk": {"selectionValue": "tastebuilder_selection=/m/2", "impressionValue": "tastebuilder_impression=/m/2"},
           "Portishead": {"selectionValue": "tastebuilder_selection=/m/3", "impressionValue": "tastebuilder_impression=/m/3"}}


def test_taste_profile_lists_names_only_in_served_order():
    worker = signed_in(get_tasteprofile=PROFILE)
    reply = call(worker, "tasteProfile")
    assert reply["ok"] and reply["result"] == {"artists": [{"name": "Radiohead"}, {"name": "Björk"}, {"name": "Portishead"}]}
    assert "tastebuilder" not in str(reply), "the selection values stay in the worker"
    assert call(ytm.Worker(FakeYT(), "none"), "tasteProfile")["error"]["code"] == "AUTH_REQUIRED"


def test_set_taste_profile_sends_the_names_with_the_profile_read_first():
    worker = signed_in(get_tasteprofile=PROFILE, set_tasteprofile=None)
    reply = call(worker, "setTasteProfile", artists=["Björk", "Radiohead", "Björk"])
    assert reply["ok"] and reply["result"] == {"added": ["Björk", "Radiohead"]}
    assert worker.yt.edits() == [("get_tasteprofile", (), {}),
                                 ("set_tasteprofile", (["Björk", "Radiohead"],),
                                  {"taste_profile": {"Björk": PROFILE["Björk"], "Radiohead": PROFILE["Radiohead"]}})]


def test_only_the_chosen_artists_are_reported_as_seen():
    # ytmusicapi sends an impression for every artist in the profile it is given:
    # the rest of the ~900 suggestions must not be reported as seen and not chosen.
    worker = signed_in(get_tasteprofile=PROFILE, set_tasteprofile=None)
    assert call(worker, "setTasteProfile", artists=["Portishead"])["ok"]
    sent = worker.yt.edits()[-1][2]["taste_profile"]
    assert list(sent) == ["Portishead"] and sent["Portishead"] == PROFILE["Portishead"]


def test_a_name_no_longer_suggested_is_bad_request_and_nothing_is_sent():
    worker = signed_in(get_tasteprofile=PROFILE, set_tasteprofile=None)
    reply = call(worker, "setTasteProfile", artists=["Radiohead", "Gone"])
    assert reply["error"]["code"] == "BAD_REQUEST" and "Gone" in reply["error"]["message"]
    assert [c[0] for c in worker.yt.edits()] == ["get_tasteprofile"]


def test_set_taste_profile_errors_map_to_bad_request():
    # ytmusicapi's own check (a name not in the profile it was given).
    error = YTMusicUserError("The artist Radiohead was not present in taste!")
    worker = signed_in(get_tasteprofile=PROFILE, set_tasteprofile=error)
    assert call(worker, "setTasteProfile", artists=["Radiohead"])["error"]["code"] == "BAD_REQUEST"
    for bad in (None, [], "Radiohead", [""], [7], ["x" * 300], ["a"] * (feedback.TASTE_MAX + 1)):
        worker = signed_in(get_tasteprofile=PROFILE, set_tasteprofile=None)
        params = {} if bad is None else {"artists": bad}
        reply = worker.handle({"id": 1, "method": "setTasteProfile", "params": params})
        assert reply["error"]["code"] == "BAD_REQUEST" and worker.yt.calls == [], bad
    worker = signed_in(get_tasteprofile=PROFILE, set_tasteprofile=YTMusicServerError("Server returned HTTP 403: Forbidden."))
    reply = call(worker, "setTasteProfile", artists=["Radiohead"])
    assert reply["error"]["code"] == "UPSTREAM" and worker.auth == "ok"


def test_every_suggestion_can_be_added_at_once():
    # The web's form takes any number of them; the live list had 889.
    profile = {f"Artist {i}": {"selectionValue": f"s{i}", "impressionValue": f"i{i}"} for i in range(889)}
    worker = signed_in(get_tasteprofile=profile, set_tasteprofile=None)
    names = list(profile)
    reply = call(worker, "setTasteProfile", artists=names)
    assert reply["ok"] and reply["result"]["added"] == names
    assert worker.yt.edits()[-1] == ("set_tasteprofile", (names,), {"taste_profile": profile})
    assert list(worker.yt.edits()[-1][2]["taste_profile"]) == names
    assert feedback.TASTE_MAX >= 889


def test_a_failed_profile_read_is_unsent_and_drops_nothing():
    worker = signed_in(get_tasteprofile=YTMusicServerError("Server returned HTTP 500: x"), set_tasteprofile=None)
    recorder = Recorder()
    worker._cache = recorder
    reply = call(worker, "setTasteProfile", artists=["Radiohead"])
    assert not reply["ok"] and recorder.dropped == []
    worker = signed_in(get_tasteprofile=PROFILE, set_tasteprofile=None)
    worker._cache = recorder
    assert call(worker, "setTasteProfile", artists=["Radiohead"])["ok"]
    assert recorder.dropped == [("homeFeed", None)]


# ---- registry --------------------------------------------------------------------------------


def test_every_edit_is_in_mutates_and_nothing_here_is_cached():
    assert {"feedback", "setTasteProfile"} <= ytm.MUTATES
    assert "tasteProfile" not in ytm.MUTATES
    assert not ({"feedback", "tasteProfile", "setTasteProfile"} & ytm.CACHEABLE)
    assert ytm.LANES.get("tasteProfile") == "pages"
    assert feedback.METHODS.keys() == {"feedback", "tasteProfile", "setTasteProfile"}
