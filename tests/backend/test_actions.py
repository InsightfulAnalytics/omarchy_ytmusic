"""ytm_actions (S4): save albums and playlists, subscribe, songs in the library.

Every call here is pinned against a fake client: the real ones change the
account, so they are only run live on throwaway objects."""

import pytest
from ytmusicapi.models.content.enums import LikeStatus

import ytm
import ytm_actions as actions
import ytm_artist
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


PROCESSED = {"feedbackResponses": [{"isProcessed": True}]}


def call(worker, method, **params):
    return worker.handle({"id": 9, "method": method, "params": params})


def signed_in(**responses):
    return ytm.Worker(FakeYT(**responses), "ok")


# ---- rateCollection ------------------------------------------------------------------------


@pytest.mark.parametrize("rating", ["LIKE", "INDIFFERENT", "DISLIKE"])
def test_a_playlist_is_rated_with_rate_playlist(rating):
    worker = signed_in(rate_playlist={})
    reply = call(worker, "rateCollection", kind="playlist", playlistId="VLPLx", rating=rating)
    assert reply["ok"], reply
    assert worker.yt.edits() == [("rate_playlist", ("PLx", LikeStatus[rating]), {})], "VL stripped"
    assert reply["result"] == {"playlistId": "PLx", "likeStatus": rating, "kind": "playlist", "browseId": None}


def test_an_album_is_saved_through_its_audio_playlist():
    worker = signed_in(rate_playlist={})
    reply = call(worker, "rateCollection", kind="album", browseId="MPREb_ok", playlistId="OLAK5uy_ok", rating="LIKE")
    assert reply["ok"] and reply["result"]["browseId"] == "MPREb_ok"
    assert worker.yt.edits() == [("rate_playlist", ("OLAK5uy_ok", LikeStatus.LIKE), {})]


def test_an_album_without_its_playlist_reads_it_from_the_page(tmp_path):
    worker = signed_in(rate_playlist={}, get_album={"audioPlaylistId": "OLAK5uy_read"})
    params = {"kind": "album", "browseId": "MPREb_ok", "rating": "INDIFFERENT"}
    reply = worker.handle({"id": 1, "method": "rateCollection", "params": params})
    assert reply["ok"] and reply["result"]["playlistId"] == "OLAK5uy_read"
    assert worker.yt.edits() == [("get_album", ("MPREb_ok",), {}),
                                 ("rate_playlist", ("OLAK5uy_read", LikeStatus.INDIFFERENT), {})]
    assert params["playlistId"] == "OLAK5uy_read", "the resolved playlist is what INVALIDATES drops"
    # A disk copy of the album page answers without a request.
    worker.cache().put("album", {"browseId": "MPREb_c"}, {"type": "collection", "playlistId": "OLAK5uy_cached"})
    worker.yt.calls.clear()
    assert call(worker, "rateCollection", kind="album", browseId="MPREb_c", rating="LIKE")["ok"]
    assert worker.yt.edits() == [("rate_playlist", ("OLAK5uy_cached", LikeStatus.LIKE), {})]
    # No audio playlist anywhere: nothing is sent.
    worker = signed_in(rate_playlist={}, get_album={"audioPlaylistId": None})
    assert call(worker, "rateCollection", kind="album", browseId="MPREb_x", rating="LIKE")["error"]["code"] == "NOT_FOUND"
    assert [c[0] for c in worker.yt.edits()] == ["get_album"]


def test_an_album_lookup_also_names_its_artists():
    worker = signed_in(rate_playlist={}, get_album={"audioPlaylistId": "OLAK5uy_read",
                                                    "artists": [{"name": "Radiohead", "id": "UCr"}, {"name": "X"}]})
    params = {"kind": "album", "browseId": "MPREb_ok", "rating": "LIKE"}
    assert worker.handle({"id": 1, "method": "rateCollection", "params": params})["ok"]
    assert params["artistIds"] == ["UCr"], "what INVALIDATES drops: that artist's page"


@pytest.mark.parametrize("params", [
    {"kind": "playlist", "playlistId": "PLx", "rating": "LOVE"},
    {"kind": "playlist", "playlistId": "PLx"},
    {"kind": "playlist", "rating": "LIKE"},
    {"kind": "playlist", "playlistId": "", "rating": "LIKE"},
    {"kind": "song", "playlistId": "PLx", "rating": "LIKE"},
    {"playlistId": "PLx", "rating": "LIKE"},
    {"kind": "album", "playlistId": "OLAK5uy_ok", "rating": "LIKE"},
    {"kind": "album", "browseId": "", "playlistId": "OLAK5uy_ok", "rating": "LIKE"},
])
def test_rate_collection_bad_requests(params):
    worker = signed_in(rate_playlist={})
    assert call(worker, "rateCollection", **params)["error"]["code"] == "BAD_REQUEST"
    assert worker.yt.calls == [], "nothing reaches YouTube"


# ---- subscribe -------------------------------------------------------------------------------


def test_subscribe_and_unsubscribe_any_channel():
    worker = signed_in(subscribe_artist={}, unsubscribe_artists={})
    reply = call(worker, "subscribe", channelId="UCsub", pageChannelId="UCpage", subscribe=True)
    assert reply["ok"] and reply["result"] == {"channelId": "UCsub", "pageChannelId": "UCpage", "subscribed": True}
    reply = call(worker, "subscribe", channelId="UCsub", pageChannelId="UCpage", subscribe=False)
    assert reply["ok"] and reply["result"]["subscribed"] is False
    # A channel that is no artist page (S9's profiles): its own id, no page.
    assert call(worker, "subscribe", channelId="UCuser", subscribe=True)["result"]["pageChannelId"] is None
    assert worker.yt.edits() == [("subscribe_artist", ("UCsub",), {}), ("unsubscribe_artists", (["UCsub"],), {}),
                                 ("subscribe_artist", ("UCuser",), {})]


def test_subscribe_reads_the_subscribe_channel_from_the_artist_page(monkeypatch):
    asked = []

    def page(worker, channel_id, needs):
        asked.append((channel_id, needs))
        return {"type": "page", "header": {"channelId": channel_id, "subscribeId": "UCofficial"}}

    monkeypatch.setattr(ytm_artist, "cached_or_fetch", page)
    worker = signed_in(subscribe_artist={})
    reply = call(worker, "subscribe", pageChannelId="UCpage", subscribe=True)
    assert reply["ok"] and reply["result"]["channelId"] == "UCofficial"
    assert asked == [("UCpage", "subscribeId")]
    assert worker.yt.edits() == [("subscribe_artist", ("UCofficial",), {})]


@pytest.mark.parametrize("params", [
    {"channelId": "UCx"},
    {"channelId": "UCx", "subscribe": "yes"},
    {"subscribe": True},
    {"channelId": "", "pageChannelId": "", "subscribe": True},
])
def test_subscribe_bad_requests(params):
    worker = signed_in(subscribe_artist={})
    assert call(worker, "subscribe", **params)["error"]["code"] == "BAD_REQUEST"
    assert worker.yt.calls == []


# ---- songLibrary ------------------------------------------------------------------------------


def test_a_song_is_saved_or_removed_with_its_token():
    worker = signed_in(edit_song_library_status=PROCESSED)
    reply = call(worker, "songLibrary", videoId="nbCOAPR33ME", token="TOKEN-ADD")
    assert reply["ok"] and reply["result"] == {"videoId": "nbCOAPR33ME"}
    assert worker.yt.edits() == [("edit_song_library_status", (), {"feedbackTokens": ["TOKEN-ADD"]})]


@pytest.mark.parametrize("response", [
    {"feedbackResponses": [{"isProcessed": False}]},
    {"feedbackResponses": [{"isProcessed": True}, {"isProcessed": False}]},
    {"feedbackResponses": []},
    {"responseContext": {}},
    None,
])
def test_a_token_youtube_did_not_process_is_upstream(response):
    worker = signed_in(edit_song_library_status=response)
    reply = call(worker, "songLibrary", videoId="v", token="STALE")
    assert reply["error"]["code"] == "UPSTREAM"
    assert reply["error"]["message"] == actions.STALE_MESSAGE
    assert reply["error"]["stale"] is True, "the shell tells a stale token from any other server error"
    assert "STALE" not in reply["error"]["message"].replace(actions.STALE_MESSAGE, ""), "the token is never echoed"


def test_any_other_server_error_is_not_a_stale_token():
    from ytmusicapi.exceptions import YTMusicServerError

    worker = signed_in(edit_song_library_status=YTMusicServerError("Server returned HTTP 500: Internal Server Error."),
                       rate_playlist=YTMusicServerError("Server returned HTTP 400: Bad Request."))
    for method, params in (("songLibrary", {"videoId": "v", "token": "T"}),
                           ("rateCollection", {"kind": "playlist", "playlistId": "PLx", "rating": "LIKE"})):
        error = call(worker, method, **params)["error"]
        assert error["code"] == "UPSTREAM" and "stale" not in error, method


def test_artist_ids_are_channel_ids_written_back_for_invalidates():
    worker = signed_in(edit_song_library_status=PROCESSED, rate_playlist={})
    params = {"videoId": "v", "token": "T", "artistIds": ["MPLAUCa", "UCa", " UCb "]}
    assert worker.handle({"id": 1, "method": "songLibrary", "params": params})["ok"]
    assert params["artistIds"] == ["UCa", "UCb"], "MPLA stripped, no repeats"
    params = {"kind": "album", "browseId": "MPREb_ok", "playlistId": "OLAK5uy_ok", "rating": "LIKE", "artistIds": ["UCa"]}
    assert worker.handle({"id": 2, "method": "rateCollection", "params": params})["ok"]
    assert params["artistIds"] == ["UCa"]
    assert [c[0] for c in worker.yt.edits()] == ["edit_song_library_status", "rate_playlist"], "no lookup needed"


@pytest.mark.parametrize("artist_ids", ["UCa", [""], [5], ["x" * 201], ["UC"] * (actions.ARTISTS_MAX + 1)])
def test_bad_artist_ids_are_bad_requests(artist_ids):
    worker = signed_in(edit_song_library_status=PROCESSED, rate_playlist={})
    assert call(worker, "songLibrary", videoId="v", token="T", artistIds=artist_ids)["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "rateCollection", kind="album", browseId="MPREb_ok", playlistId="OLAK5uy_ok", rating="LIKE",
                artistIds=artist_ids)["error"]["code"] == "BAD_REQUEST"
    assert worker.yt.calls == []


@pytest.mark.parametrize("params", [
    {"videoId": "v"},
    {"videoId": "v", "token": ""},
    {"videoId": "v", "token": 5},
    {"videoId": "v", "token": "x" * (actions.TOKEN_MAX + 1)},
    {"token": "T"},
])
def test_song_library_needs_a_token_and_a_song(params):
    worker = signed_in(edit_song_library_status=PROCESSED)
    assert call(worker, "songLibrary", **params)["error"]["code"] == "BAD_REQUEST"
    assert worker.yt.calls == []


# ---- signed out, the registry --------------------------------------------------------------


@pytest.mark.parametrize("method, params", [
    ("rateCollection", {"kind": "playlist", "playlistId": "PLx", "rating": "LIKE"}),
    ("subscribe", {"channelId": "UCx", "subscribe": True}),
    ("songLibrary", {"videoId": "v", "token": "T"}),
])
def test_every_action_needs_a_session(method, params):
    worker = ytm.Worker(FakeYT(rate_playlist={}, subscribe_artist={}, edit_song_library_status=PROCESSED), "none")
    assert call(worker, method, **params)["error"]["code"] == "AUTH_REQUIRED"
    assert worker.yt.calls == []


def test_every_action_mutates_and_invalidates():
    for name in ("rateCollection", "subscribe", "songLibrary"):
        assert ytm.METHODS[name] is actions.METHODS[name]
        assert name in ytm.MUTATES and name not in ytm.CACHEABLE and name not in ytm.LANES
        assert ytm.INVALIDATES[name], name
    assert ("librarySongs", None, None) in ytm.INVALIDATES["songLibrary"]
    assert ("librarySubscriptions", None, None) in ytm.INVALIDATES["subscribe"]
    assert ("artist", "pageChannelId", "channelId") in ytm.INVALIDATES["subscribe"]
    for triple in (("playlist", "playlistId", "playlistId"), ("libraryPlaylists", None, None),
                   ("libraryAlbums", None, None), ("libraryLanding", None, None)):
        assert triple in ytm.INVALIDATES["rateCollection"]
    # The existing rate takes DISLIKE (the D key) and clears it with INDIFFERENT.
    assert "DISLIKE" in ytm.RATINGS


def test_the_ready_event_lists_the_actions_as_mutating():
    lines = []

    class Out:
        def write(self, text):
            lines.append(text)

        def flush(self):
            pass

    import json
    import os

    read, write = os.pipe()
    os.close(write)  # stdin at EOF: serve answers nothing and returns
    try:
        ytm.serve(0, worker=ytm.Worker(FakeYT(), "none"), stdin_fd=read, out=Out())
    finally:
        os.close(read)
    ready = json.loads(lines[0])
    assert {"rateCollection", "subscribe", "songLibrary"} <= set(ready["mutates"])


# ---- the library toggle of a page header ------------------------------------------------------


def header(*buttons):
    return {"contents": {"twoColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": [{"musicResponsiveHeaderRenderer": {"buttons": list(buttons)}}]}}}}]}}}


def save_toggle(toggled, playlist_id="OLAK5uy_ok"):
    return {"toggleButtonRenderer": {
        "isToggled": toggled,
        "defaultIcon": {"iconType": "BOOKMARK_BORDER"}, "toggledIcon": {"iconType": "BOOKMARK"},
        "defaultServiceEndpoint": {"likeEndpoint": {"status": "LIKE", "target": {"playlistId": playlist_id}}},
        "toggledServiceEndpoint": {"likeEndpoint": {"status": "INDIFFERENT", "target": {"playlistId": playlist_id}}}}}


SHUFFLE = {"toggleButtonRenderer": {"isToggled": True, "defaultIcon": {"iconType": "MUSIC_SHUFFLE"},
                                    "defaultServiceEndpoint": {"commandExecutorCommand": {}}}}
PLAY = {"musicPlayButtonRenderer": {"playNavigationEndpoint": {"watchEndpoint": {"playlistId": "OLAK5uy_ok"}}}}


def test_library_toggle_reads_is_toggled_by_its_like_endpoint():
    assert actions.library_toggle(header(PLAY, save_toggle(True), SHUFFLE)) is True
    assert actions.library_toggle(header(SHUFFLE, save_toggle(False))) is False
    assert actions.library_toggle(header(PLAY, SHUFFLE)) is None, "a shuffle toggle is not the library's"
    assert actions.library_toggle(header()) is None
    assert actions.library_toggle({"contents": {}}) is None and actions.library_toggle(None) is None
    assert actions.like_status(True) == "LIKE" and actions.like_status(False) == "INDIFFERENT"
    assert actions.like_status(None) is None


class Hooked:
    """A client ytm_raw can hook: each read sends `response` through _send_request."""

    def __init__(self, response, album=None, playlist=None):
        self.response = response
        self.album = album
        self.playlist = playlist

    def _send_request(self, endpoint, body, additionalParams=""):
        return self.response

    def get_album(self, browse_id):
        self._send_request("browse", {})
        return self.album

    def get_playlist(self, playlist_id, limit=100, related=False):
        self._send_request("browse", {})
        return self.playlist


ALBUM = {"title": "OK Computer", "type": "Album", "audioPlaylistId": "OLAK5uy_ok", "likeStatus": "INDIFFERENT",
         "trackCount": 1, "artists": [{"name": "Radiohead", "id": "UCr"}],
         "tracks": [{"videoId": "v1", "title": "Airbag", "trackNumber": 1, "artists": [{"name": "Radiohead", "id": "UCr"}]}]}


@pytest.mark.parametrize("toggled, status", [(True, "LIKE"), (False, "INDIFFERENT")])
def test_an_album_page_says_whether_it_is_saved(toggled, status):
    worker = ytm.Worker(raw.install(Hooked(header(PLAY, save_toggle(toggled)), album=ALBUM)), "ok")
    page = call(worker, "album", browseId="MPREb_ok")["result"]
    assert page["header"]["likeStatus"] == status, "ytmusicapi said INDIFFERENT for both"


def test_an_album_page_without_the_toggle_keeps_ytmusicapis_status():
    worker = ytm.Worker(raw.install(Hooked(header(PLAY), album=dict(ALBUM, likeStatus="LIKE"))), "ok")
    assert call(worker, "album", browseId="MPREb_ok")["result"]["header"]["likeStatus"] == "LIKE"


def test_a_playlist_page_says_whether_it_is_saved():
    data = {"id": "PLx", "title": "Theirs", "owned": False, "trackCount": 1,
            "tracks": [{"videoId": "v1", "title": "One", "artists": [{"name": "A", "id": "UCa"}]}]}
    for toggled, status in ((True, "LIKE"), (False, "INDIFFERENT")):
        worker = ytm.Worker(raw.install(Hooked(header(save_toggle(toggled, "PLx")), playlist=data)), "ok")
        assert call(worker, "playlist", playlistId="PLx")["result"]["header"]["likeStatus"] == status
    # Your own playlist has an edit header without the toggle: no status.
    worker = ytm.Worker(raw.install(Hooked({"header": {}}, playlist=dict(data, owned=True))), "ok")
    assert call(worker, "playlist", playlistId="PLx")["result"]["header"]["likeStatus"] is None
