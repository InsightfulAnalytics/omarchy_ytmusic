import json
import os
import re
import subprocess
import sys
import threading
import time

import pytest
import requests
from ytmusicapi.exceptions import YTMusicServerError, YTMusicUserError

import ytm
from conftest import ROOT


class FakeYT:
    def __init__(self, **responses):
        self.responses = responses
        self.calls = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            value = self.responses.get(name)
            if isinstance(value, Exception):
                raise value
            return value

        return method


def call(worker, method, **params):
    return worker.handle({"id": 7, "method": method, "params": params})


def test_ping_and_unknown_method():
    worker = ytm.Worker(FakeYT(), "none")
    reply = call(worker, "ping")
    assert reply == {"id": 7, "ok": True, "result": {"pong": True}, "ms": reply["ms"], "http": 0}
    reply = call(worker, "nope")
    assert reply["ok"] is False and reply["error"]["code"] == "BAD_REQUEST"


def test_bad_params_are_bad_request():
    worker = ytm.Worker(FakeYT(search=[]), "none")
    assert call(worker, "search")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "search", query="x", filter="nope")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "search", query="x", limit="many")["error"]["code"] == "BAD_REQUEST"
    assert worker.handle({"id": 1, "method": "search", "params": []})["error"]["code"] == "BAD_REQUEST"
    assert worker.handle("not an object")["error"]["code"] == "BAD_REQUEST"


def test_session_is_verified_once():
    fake = FakeYT(get_account_info={"accountName": "x"}, get_library_playlists=[])
    worker = ytm.Worker(fake, "ok")
    assert call(worker, "libraryPlaylists")["ok"]
    assert call(worker, "libraryPlaylists")["ok"]
    assert [c[0] for c in fake.calls].count("get_account_info") == 1


def test_expired_session_is_detected_by_account_lookup(config_dir):
    fake = FakeYT(get_account_info=KeyError("header"), get_library_playlists=[])
    worker = ytm.Worker(fake, "ok")
    assert call(worker, "libraryPlaylists")["error"]["code"] == "AUTH_EXPIRED"
    assert call(worker, "liked")["error"]["code"] == "AUTH_EXPIRED"
    names = [c[0] for c in fake.calls]
    assert names.count("get_account_info") == 1 and "get_library_playlists" not in names
    assert call(worker, "hello")["result"]["auth"] == "expired"
    assert call(worker, "search", query="x", filter="songs")["ok"]  # signed-out features keep working


def test_offline_verification_does_not_mark_expired():
    worker = ytm.Worker(FakeYT(get_account_info=requests.exceptions.ConnectionError("down")), "ok")
    assert call(worker, "libraryPlaylists")["error"]["code"] == "NETWORK"
    assert worker.auth == "ok"


def test_http_401_marks_session_expired():
    worker = ytm.Worker(FakeYT(get_library_playlists=YTMusicServerError("Server returned HTTP 401: Unauthorized.")), "ok")
    assert call(worker, "libraryPlaylists")["error"]["code"] == "AUTH_EXPIRED"
    assert worker.auth == "expired"


def test_verify_auth_method(config_dir):
    assert call(ytm.Worker(FakeYT(get_account_info=KeyError("x")), "ok"), "verifyAuth")["result"]["auth"] == "expired"
    assert call(ytm.Worker(FakeYT(), "ok"), "verifyAuth")["result"]["auth"] == "ok"
    assert call(ytm.Worker(FakeYT(), "none"), "verifyAuth")["result"]["auth"] == "none"


def test_library_needs_auth():
    worker = ytm.Worker(FakeYT(get_library_playlists=[]), "none")
    assert call(worker, "libraryPlaylists")["error"]["code"] == "AUTH_REQUIRED"
    assert call(worker, "rate", videoId="v", rating="LIKE")["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("exc, code, retryable", [
    (YTMusicServerError("Server returned HTTP 401: Unauthorized.\nx"), "AUTH_EXPIRED", False),
    (YTMusicServerError("Server returned HTTP 403: Forbidden."), "AUTH_EXPIRED", False),
    (YTMusicServerError("Server returned HTTP 429: Too Many Requests."), "RATE_LIMITED", True),
    (YTMusicServerError("Server returned HTTP 404: Not Found."), "NOT_FOUND", False),
    (YTMusicServerError("Server returned HTTP 500: oops"), "UPSTREAM", False),
    (YTMusicUserError("Please provide authentication before using this function"), "AUTH_REQUIRED", False),
    (requests.exceptions.ConnectionError("down"), "NETWORK", True),
    (requests.exceptions.ReadTimeout("slow"), "NETWORK", True),
    (KeyError("contents"), "UPSTREAM", False),
    (RuntimeError("boom"), "INTERNAL", False),
])
def test_errors_are_classified(exc, code, retryable):
    # S7: search sends its own request (ytm_search), so that is what fails.
    worker = ytm.Worker(FakeYT(_send_request=exc), "ok")
    error = call(worker, "search", query="x", filter="songs")["error"]
    assert (error["code"], error["retryable"]) == (code, retryable)
    assert error["message"]


def test_search_filtered_and_grouped(load_fixture):
    # S7: the raw response is parsed (tests/backend/test_search.py has the rest).
    worker = ytm.Worker(FakeYT(_send_request=load_fixture("search_episodes.json")), "none")
    items = call(worker, "search", query="x", filter="episodes", limit=1)["result"]["items"]
    assert items and all(t["kind"] == "episode" for t in items)
    worker = ytm.Worker(FakeYT(_send_request=load_fixture("search_raw.json")), "none")
    grouped = call(worker, "search", query="x")["result"]
    assert grouped["songs"] and all(t["type"] == "track" for t in grouped["songs"])


def test_suggestions_keep_text_and_history_flag():
    fake = FakeYT(get_search_suggestions=[{"text": "daft punk", "fromHistory": True, "runs": []},
                                          {"text": " daft punk get lucky "}, "plain", {"text": ""}])
    result = call(ytm.Worker(fake, "none"), "suggestions", query="daft p")["result"]
    assert result == {"items": [{"text": "daft punk", "fromHistory": True},
                                {"text": "daft punk get lucky", "fromHistory": False},
                                {"text": "plain", "fromHistory": False}], "entities": []}
    assert fake.calls[-1] == ("get_search_suggestions", ("daft p",), {"detailed_runs": True})
    # S7: an empty field asks for your past searches; signed out there are none.
    assert call(ytm.Worker(fake, "none"), "suggestions", query=" ")["result"] == {"items": [], "entities": []}
    assert call(ytm.Worker(fake, "none"), "suggestions")["error"]["code"] == "BAD_REQUEST"


def test_rate_passes_like_status_enum():
    fake = FakeYT(rate_song={})
    worker = ytm.Worker(fake, "ok")
    assert call(worker, "rate", videoId="v", rating="LIKE")["result"] == {"videoId": "v", "likeStatus": "LIKE"}
    name, args, _ = fake.calls[-1]
    assert name == "rate_song" and args[0] == "v" and args[1].name == "LIKE"
    assert call(worker, "rate", videoId="v", rating="LOVE")["error"]["code"] == "BAD_REQUEST"


def test_playlist_edits():
    fake = FakeYT(
        create_playlist="PLnew",
        add_playlist_items={"status": "STATUS_SUCCEEDED",
                            "playlistEditResults": [{"videoId": "a", "setVideoId": "S1"}]},
        remove_playlist_items="STATUS_SUCCEEDED",
        delete_playlist="STATUS_SUCCEEDED",
    )
    worker = ytm.Worker(fake, "ok")
    assert call(worker, "createPlaylist", title="ytm-test")["result"] == {"playlistId": "PLnew"}
    assert call(worker, "addToPlaylist", playlistId="PLnew", videoIds=["a"])["result"] == \
        {"added": [{"videoId": "a", "setVideoId": "S1"}]}
    assert call(worker, "removeFromPlaylist", playlistId="PLnew",
                items=[{"videoId": "a", "setVideoId": "S1"}])["result"] == {"ok": True}
    assert call(worker, "removeFromPlaylist", playlistId="PLnew", items=[{"videoId": "a"}])["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "deletePlaylist", playlistId="PLnew")["result"] == {"ok": True}


@pytest.mark.parametrize("response, ok", [
    ("STATUS_SUCCEEDED", True),
    ({"responseContext": {}}, True),  # what a successful delete actually returns (no status)
    ("STATUS_FAILED", False),
    ({"error": {"code": 400}}, False),
])
def test_delete_playlist_responses(response, ok):
    reply = call(ytm.Worker(FakeYT(delete_playlist=response), "ok"), "deletePlaylist", playlistId="PLx")
    assert reply["ok"] is ok
    if not ok:
        assert reply["error"]["code"] == "UPSTREAM"


def test_failed_edits_are_upstream_errors():
    worker = ytm.Worker(FakeYT(add_playlist_items={"status": "STATUS_FAILED"}, create_playlist={"error": 1}), "ok")
    assert call(worker, "addToPlaylist", playlistId="P", videoIds=["a"])["error"]["code"] == "UPSTREAM"
    assert call(worker, "createPlaylist", title="t")["error"]["code"] == "UPSTREAM"


def test_up_next_drops_the_seed_and_normalizes():
    watch = json.loads((ROOT / "tests" / "fixtures" / "watch.json").read_text())
    seed = watch["tracks"][0]["videoId"]
    fake = FakeYT(get_watch_playlist=watch)
    result = call(ytm.Worker(fake, "none"), "upNext", videoId=seed)["result"]
    assert result["items"] and all(t["videoId"] != seed for t in result["items"])
    assert all(t["type"] == "track" and t["title"] and t["thumb"] for t in result["items"])
    # No context: the song's own radio (S12).
    assert fake.calls[-1][2] == {"videoId": seed, "playlistId": None, "radio": True, "limit": 25}
    assert call(ytm.Worker(fake, "none"), "upNext")["error"]["code"] == "BAD_REQUEST"


def test_like_status_picks_matching_track():
    fake = FakeYT(get_watch_playlist={"tracks": [{"videoId": "v", "likeStatus": "LIKE"}]})
    assert call(ytm.Worker(fake, "ok"), "likeStatus", videoId="v")["result"] == {"videoId": "v", "likeStatus": "LIKE"}


# ---- the real serve loop, as the shell drives it ----------------------------


def spawn(config_dir, *args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1", YTM_CONFIG_DIR=str(config_dir))
    return subprocess.Popen([sys.executable, str(ROOT / "backend" / "ytm.py"), "serve", *args],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)


def test_serve_protocol_and_eof_exit(config_dir):
    proc = spawn(config_dir, "--idle", "30")
    ready = json.loads(proc.stdout.readline())
    assert ready["event"] == "ready" and ready["protocol"] == 1 and ready["auth"] == "none"
    proc.stdin.write('{"id":1,"method":"ping"}\nnot json\n{"id":2,"method":"hello"}\n')
    proc.stdin.flush()
    replies = [json.loads(proc.stdout.readline()) for _ in range(3)]
    assert replies[0]["id"] == 1 and replies[0]["result"] == {"pong": True}
    assert replies[1]["error"]["code"] == "BAD_REQUEST"
    assert replies[2]["result"] == {"auth": "none", "account": None}
    started = time.monotonic()
    proc.stdin.close()
    assert proc.wait(timeout=5) == 0
    assert time.monotonic() - started < 1.0
    assert proc.stdout.read() == ""  # nothing but protocol lines on stdout


def test_serve_idle_exit(config_dir):
    proc = spawn(config_dir, "--idle", "0.3")
    assert json.loads(proc.stdout.readline())["event"] == "ready"
    assert json.loads(proc.stdout.readline()) == {"event": "idle-exit"}
    assert proc.wait(timeout=5) == 0


def test_stream_lookups_do_not_hold_up_other_requests():
    class SlowResolver:
        def resolve(self, video_id, account=None):
            time.sleep(0.4)
            return {"url": "https://example.com/" + video_id, "expires": 1}

    class Out:
        def __init__(self):
            self.lines = []

        def write(self, text):
            self.lines.append(json.loads(text))

        def flush(self):
            pass

    out = Out()
    read_fd, write_fd = os.pipe()
    worker = ytm.Worker(FakeYT(), "none", resolver=SlowResolver())
    thread = threading.Thread(target=ytm.serve, args=(5,), kwargs={"worker": worker, "stdin_fd": read_fd, "out": out},
                              daemon=True)
    thread.start()
    os.write(write_fd, b'{"id":1,"method":"resolve","params":{"videoId":"abcdefghijk"}}\n'
                       b'{"id":2,"method":"resolve","params":{"videoId":"bcdefghijkl"}}\n'
                       b'{"id":3,"method":"ping"}\n')
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(out.lines) < 4:
        time.sleep(0.01)
    replies = [line for line in out.lines if "id" in line]
    assert [r["id"] for r in replies] == [3, 1, 2], "ping answered first; lookups one at a time, in order"
    assert replies[1]["result"]["url"].endswith("abcdefghijk")
    os.close(write_fd)
    thread.join(timeout=5)
    assert not thread.is_alive()
    os.close(read_fd)


def test_serve_shutdown(config_dir):
    proc = spawn(config_dir)
    proc.stdout.readline()
    proc.stdin.write('{"id":9,"method":"shutdown"}\n')
    proc.stdin.flush()
    assert json.loads(proc.stdout.readline())["id"] == 9
    assert proc.wait(timeout=5) == 0


# ---- protocol v2: http counts, params, paging lanes, configure, record ------------------


class Out:
    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.append(json.loads(text))

    def flush(self):
        pass


def run_serve(worker, payload, until, idle=5, timeout=5):
    out = Out()
    read_fd, write_fd = os.pipe()
    thread = threading.Thread(target=ytm.serve, args=(idle,),
                              kwargs={"worker": worker, "stdin_fd": read_fd, "out": out}, daemon=True)
    thread.start()
    os.write(write_fd, payload)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not until(out.lines):
        time.sleep(0.01)
    return out, thread, read_fd, write_fd


def test_prefer_hit_is_answered_without_entering_a_lane(tmp_path, monkeypatch):
    import ytm_cache as caches

    cache = caches.DiskCache(tmp_path / "data")
    cache.put("browsePage", {"browseId": "FEmusic_new_releases_albums"}, {"type": "page", "title": "cached"})
    blocked = threading.Event()

    def slow(worker, params):
        blocked.wait(5)
        return {"type": "page", "title": "fresh"}

    monkeypatch.setitem(ytm.METHODS, "browsePage", slow)
    worker = ytm.Worker(FakeYT(), "none", cache=cache)
    payload = (b'{"id":1,"method":"browsePage","params":{"browseId":"FEmusic_x"}}\n'
               b'{"id":2,"method":"browsePage","params":{"browseId":"FEmusic_new_releases_albums","cache":"prefer"}}\n')
    out, thread, read_fd, write_fd = run_serve(worker, payload,
                                               lambda lines: any(line.get("id") == 2 for line in lines))
    reply = next(line for line in out.lines if line.get("id") == 2)
    assert reply["cached"] is True and reply["result"]["title"] == "cached" and reply["http"] == 0
    assert not any(line.get("id") == 1 for line in out.lines), "the lane is still busy with request 1"
    blocked.set()
    os.close(write_fd)
    thread.join(timeout=5)
    os.close(read_fd)


def test_idle_exit_waits_for_lane_work(monkeypatch):
    def slow(worker, params):
        time.sleep(0.6)
        return {"type": "page"}

    monkeypatch.setitem(ytm.METHODS, "browsePage", slow)
    worker = ytm.Worker(FakeYT(), "none")
    out, thread, read_fd, write_fd = run_serve(worker, b'{"id":1,"method":"browsePage","params":{"browseId":"FEx"}}\n',
                                               lambda lines: any(line.get("event") == "idle-exit" for line in lines),
                                               idle=0.2)
    events = [line.get("event") or line.get("id") for line in out.lines]
    assert events == ["ready", 1, "idle-exit"], "the reply comes first, then the idle exit"
    thread.join(timeout=5)
    os.close(write_fd)
    os.close(read_fd)


def test_ready_event_lists_mutations(config_dir):
    proc = spawn(config_dir, "--idle", "5")
    ready = json.loads(proc.stdout.readline())
    assert set(ready["mutates"]) == ytm.MUTATES
    proc.stdin.close()
    assert proc.wait(timeout=5) == 0


class CountingYT(FakeYT):
    """Each call counts as `n` HTTP requests, like ytm_raw's response hook does."""

    def __init__(self, n, **responses):
        super().__init__(**responses)
        self.n = n

    def __getattr__(self, name):
        method = super().__getattr__(name)

        def counted(*args, **kwargs):
            import ytm_raw

            for _ in range(self.n):
                ytm_raw._count(None)
            return method(*args, **kwargs)

        return counted


def test_every_reply_carries_http(monkeypatch, capsys):
    worker = ytm.Worker(CountingYT(2, search=[]), "none")
    assert call(worker, "search", query="x", filter="songs")["http"] == 2
    assert call(worker, "ping")["http"] == 0, "counted per request"
    assert call(worker, "nope")["http"] == 0
    monkeypatch.setenv("YTM_DEBUG", "1")
    call(worker, "search", query="secret words", filter="songs")
    log = capsys.readouterr().err
    assert re.search(r"ytm: search ok http=2 ms=\d+", log)
    assert "secret" not in log, "debug lines never carry params"


def test_opt_limit_and_bool():
    assert ytm.opt_limit({"limit": "all"}, "limit", 100, 5000) is None
    assert ytm.opt_limit({}, "limit", 100, 5000) == 100
    assert ytm.opt_limit({"limit": 9000}, "limit", 100, 5000) == 5000
    with pytest.raises(ytm.WorkerError):
        ytm.opt_limit({"limit": "lots"}, "limit", 100, 5000)
    assert ytm.opt_bool({}, "radio") is False and ytm.opt_bool({"radio": True}, "radio") is True
    with pytest.raises(ytm.WorkerError):
        ytm.opt_bool({"radio": "yes"}, "radio")


def test_opt_order_and_cursor():
    assert ytm.opt_order({}, ("a_to_z", "z_to_a")) is None
    assert ytm.opt_order({"order": "z_to_a"}, ("a_to_z", "z_to_a")) == "z_to_a"
    for bad in ("newest", True, 3):
        with pytest.raises(ytm.WorkerError) as err:
            ytm.opt_order({"order": bad}, ("a_to_z", "z_to_a"))
        assert err.value.code == "BAD_REQUEST"
    assert ytm.opt_cursor({}) is None and ytm.opt_cursor({"cursor": "c.abc"}) == "c.abc"
    for bad in (5, "x" * 5000):
        with pytest.raises(ytm.WorkerError):
            ytm.opt_cursor({"cursor": bad})
    worker = ytm.Worker(FakeYT(), "none")
    assert call(worker, "browsePage", browseId="FEx", order="newest")["error"]["code"] == "BAD_REQUEST"


# ---- browsePage on hand-built responses -------------------------------------------------

THUMBS = {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
    {"url": "https://lh3.googleusercontent.com/x=w226-h226-l90-rj", "width": 226, "height": 226}]}}}


def album_item(n):
    endpoint = {"browseEndpoint": {"browseId": f"MPREb_{n}", "browseEndpointContextSupportedConfigs": {
        "browseEndpointContextMusicConfig": {"pageType": "MUSIC_PAGE_TYPE_ALBUM"}}}}
    return {"musicTwoRowItemRenderer": {"title": {"runs": [{"text": f"Album {n}", "navigationEndpoint": endpoint}]},
                                        "subtitle": {"runs": [{"text": "Album"}]}, "navigationEndpoint": endpoint,
                                        "thumbnailRenderer": THUMBS}}


def track_item(n):
    artist = {"browseEndpoint": {"browseId": "UCa", "browseEndpointContextSupportedConfigs": {
        "browseEndpointContextMusicConfig": {"pageType": "MUSIC_PAGE_TYPE_ARTIST"}}}}
    column = lambda run: {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [run]}}}  # noqa: E731
    return {"musicResponsiveListItemRenderer": {
        "flexColumns": [
            column({"text": f"Song {n}", "navigationEndpoint": {"watchEndpoint": {"videoId": f"vid{n:08d}"}}}),
            column({"text": "Artist", "navigationEndpoint": artist})],
        "overlay": {"musicItemThumbnailOverlayRenderer": {"content": {"musicPlayButtonRenderer": {
            "playNavigationEndpoint": {"watchEndpoint": {"videoId": f"vid{n:08d}"}}}}}},
        "thumbnail": THUMBS,
    }}


def single_column(rows, header=None, continuations=None):
    section = {"contents": rows}
    if continuations:
        section["continuations"] = continuations
    response = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
        {"tabRenderer": {"content": {"sectionListRenderer": section}}}]}}}
    if header:
        response["header"] = {"musicHeaderRenderer": {"title": {"runs": [{"text": header}]}}}
    return response


class BrowseYT:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def _send_request(self, endpoint, body, additionalParams=""):
        self.requests.append((endpoint, dict(body), additionalParams))
        return self.responses.pop(0)


def browse_page(responses, **params):
    yt = BrowseYT(*responses)
    reply = call(ytm.Worker(yt, "none"), "browsePage", **params)
    assert reply["ok"], reply
    return reply["result"], yt.requests


def test_browse_page_grid():
    grid = {"gridRenderer": {"items": [album_item(i) for i in range(3)],
                             "continuations": [{"nextContinuationData": {"continuation": "G1"}}]}}
    more = {"continuationContents": {"gridContinuation": {"items": [album_item(i) for i in range(3, 50)]}}}
    page, sent = browse_page([single_column([grid], header="Albums & singles"), more],
                             browseId="FEmusic_new_releases_albums")
    assert page["type"] == "page" and page["v"] == 2 and page["title"] == "Albums & singles"
    assert [s["layout"] for s in page["sections"]] == ["grid"]
    assert len(page["sections"][0]["items"]) == 50 and page["sections"][0]["items"][0]["type"] == "album"
    assert page["cursor"] is None and page["hasMore"] is False
    assert sent[0] == ("browse", {"browseId": "FEmusic_new_releases_albums"}, "") and len(sent) == 2


def test_browse_page_music_shelf_tracks_and_cursor():
    shelf = {"musicShelfRenderer": {"title": {"runs": [{"text": "Songs"}]},
                                    "contents": [track_item(i) for i in range(45)],
                                    "continuations": [{"nextContinuationData": {"continuation": "S1"}}]}}
    page, sent = browse_page([single_column([shelf])], browseId="MPLAUCx", params="ggMCCAI%3D")
    section = page["sections"][0]
    assert section["layout"] == "shelf" and section["title"] == "Songs"
    assert len(section["items"]) == 45 and all(t["type"] == "track" for t in section["items"])
    assert page["cursor"] == "c.S1" and page["hasMore"] is True
    assert sent == [("browse", {"browseId": "MPLAUCx", "params": "ggMCCAI%3D"}, "")]
    more = {"continuationContents": {"musicShelfContinuation": {"contents": [track_item(i) for i in range(45, 50)]}}}
    page, sent = browse_page([more], browseId="MPLAUCx", params="ggMCCAI%3D", cursor="c.S1")
    assert [t["videoId"] for t in page["sections"][0]["items"]][0] == "vid00000045" and page["cursor"] is None
    assert sent[0][2].startswith("&ctoken=S1")


def test_browse_page_carousels_become_sections():
    carousel = lambda title, items: {"musicCarouselShelfRenderer": {  # noqa: E731
        "header": {"musicCarouselShelfBasicHeaderRenderer": {"title": {"runs": [{"text": title}]}}},
        "contents": items}}
    page, sent = browse_page([single_column([carousel("New albums", [album_item(1)]), carousel("Empty", []),
                                             carousel("More", [album_item(2), album_item(3)])],
                                            continuations=[{"nextContinuationData": {"continuation": "H1"}}])],
                             browseId="FEmusic_explore")
    assert [(s["title"], len(s["items"])) for s in page["sections"]] == [("New albums", 1), ("Empty", 0), ("More", 2)]
    assert all(s["layout"] == "carousel" for s in page["sections"])
    assert len(sent) == 1, "a page of shelves needs no more than its first response"
    assert page["hasMore"] is True


def test_browse_page_cursor_of_shelves_is_one_request():
    carousel = lambda title, items: {"musicCarouselShelfRenderer": {  # noqa: E731
        "header": {"musicCarouselShelfBasicHeaderRenderer": {"title": {"runs": [{"text": title}]}}},
        "contents": items}}
    more = lambda titles, token: {"continuationContents": {"sectionListContinuation": {  # noqa: E731
        "contents": [carousel(t, [album_item(i)]) for i, t in enumerate(titles)],
        "continuations": [{"nextContinuationData": {"continuation": token}}]}}}
    page, sent = browse_page([more(["A", "B", "C"], "H2"), more(["D"], "H3")], browseId="FEmusic_home",
                             cursor="c.H1")
    assert len(sent) == 1, "three shelves are a page: no second continuation"
    assert [s["title"] for s in page["sections"]] == ["A", "B", "C"] and page["cursor"] == "c.H2"
    # A list continuation still reads on to 40 items.
    shelf = lambda first, count, token: {"continuationContents": {"musicShelfContinuation": {  # noqa: E731
        "contents": [track_item(i) for i in range(first, first + count)],
        "continuations": [{"nextContinuationData": {"continuation": token}}]}}}
    page, sent = browse_page([shelf(0, 25, "S2"), shelf(25, 25, "S3")], browseId="MPLAUCx", params="p",
                             cursor="c.S1")
    assert len(sent) == 2 and len(page["sections"][0]["items"]) == 50 and page["cursor"] == "c.S3"


def test_browse_page_message_is_an_empty_page():
    message = {"itemSectionRenderer": {"contents": [{"messageRenderer": {
        "text": {"runs": [{"text": "No albums yet"}]}}}]}}
    page, _ = browse_page([single_column([message], header="Albums")], browseId="FEmusic_liked_albums")
    assert page["sections"] == [] and page["message"] == "No albums yet" and page["title"] == "Albums"
    assert page["cursor"] is None


def test_browse_page_first_page_is_cacheable_later_pages_are_not(tmp_path):
    import ytm_cache as caches

    cache = caches.DiskCache(tmp_path / "data")
    shelf = {"musicShelfRenderer": {"contents": [track_item(i) for i in range(40)],
                                    "continuations": [{"nextContinuationData": {"continuation": "S1"}}]}}
    worker = ytm.Worker(BrowseYT(single_column([shelf])), "none", cache=cache)
    assert call(worker, "browsePage", browseId="FEx", cache="prefer")["cached"] is False
    hit = call(worker, "browsePage", browseId="FEx", cache="prefer")
    assert hit["cached"] is True and hit["result"]["cursor"] is None and hit["result"]["hasMore"] is True
    worker.yt = BrowseYT({"continuationContents": {"musicShelfContinuation": {"contents": [track_item(2)]}}})
    reply = call(worker, "browsePage", browseId="FEx", cursor="c.S1", cache="prefer")
    assert reply["cached"] is False
    assert len(list(cache.dir.glob("*.json"))) == 1, "a later page is never written"


# ---- configure ---------------------------------------------------------------------------


def location(value):
    if value not in (None, "AU", "US"):
        raise ytm.WorkerError("BAD_REQUEST", "unsupported location")
    return value


def test_configure_stores_settings(monkeypatch):
    worker = ytm.Worker(FakeYT(), "none")
    reply = call(worker, "configure", somethingNew=1)
    assert reply["ok"] and reply["result"] == {"settings": {}, "ignored": ["somethingNew"], "reloaded": False}
    monkeypatch.setitem(ytm.SETTINGS, "location", location)
    monkeypatch.setitem(ytm.SETTINGS, "theme", str)
    assert call(worker, "configure", location="XX")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "configure", theme="dark")["result"]["reloaded"] is False
    assert worker.settings == {"theme": "dark"}


def test_client_settings_rebuild_and_clear_only_when_they_change(tmp_path, monkeypatch):
    import ytm_auth as auth
    import ytm_cache as caches

    monkeypatch.setitem(ytm.SETTINGS, "location", location)
    monkeypatch.setitem(ytm.SETTINGS, "theme", str)
    monkeypatch.setattr(ytm, "CLIENT_SETTINGS", {"location"})
    builds = []
    monkeypatch.setattr(auth, "load_client", lambda: builds.append(1) or (FakeYT(), "none"))
    cache = caches.DiskCache(tmp_path / "data")
    album = {"browseId": "X"}
    cache.put("album", album, {"title": "cached"})

    worker = ytm.Worker(cache=cache)
    assert worker.settings == {} and builds == [1]
    reply = call(worker, "configure", theme="dark", location="AU")
    assert reply["result"]["reloaded"] is True and builds == [1, 1] and worker.generation == 2
    assert cache.get("album", album) is None, "the cache was made with other client settings"
    assert cache.client_settings() == {"location": "AU"}
    assert call(worker, "configure", theme="light", location="AU")["result"]["reloaded"] is False, \
        "only client settings rebuild the client"

    # A restart (idle exit, crash): the new worker starts with the recorded
    # settings, so the configure it gets first changes nothing.
    cache.put("album", album, {"title": "cached"})
    builds.clear()
    worker = ytm.Worker(cache=cache)
    assert worker.settings == {"location": "AU"} and builds == [1]
    reply = call(worker, "configure", theme="dark", location="AU")
    assert reply["result"]["reloaded"] is False and builds == [1] and worker.generation == 1
    assert cache.get("album", album) is not None, "same settings: the cache stays"

    assert call(worker, "configure", location="US")["result"]["reloaded"] is True and worker.generation == 2
    assert cache.get("album", album) is None and cache.client_settings() == {"location": "US"}
    cache.put("album", album, {"title": "cached"})
    assert call(worker, "configure")["result"]["reloaded"] is True, "back to the default client"
    assert cache.get("album", album) is None and cache.client_settings() == {}

    cache.record_client_settings({"location": "XX", "gone": 1})
    assert ytm.Worker(cache=cache).settings == {}, "recorded values that no longer validate are dropped"


def test_load_bumps_generation(monkeypatch):
    import ytm_auth as auth

    monkeypatch.setattr(auth, "load_client", lambda: (FakeYT(), "none"))
    worker = ytm.Worker()
    assert worker.generation == 1
    call(worker, "reloadAuth")
    assert worker.generation == 2


# ---- recording fixtures ------------------------------------------------------------------


def test_record_is_anonymous_and_never_loads_the_session(tmp_path, monkeypatch):
    import ytm_auth as auth

    anonymous = FakeYT(get_artist={"name": "Radiohead", "subscribed": False, "continuation": "tok",
                                   "nested": {"feedbackTokens": {"add": "x"}, "jct": "y", "cursorKey": "z",
                                              "onBehalfOfUser": "u", "keep": 1}})

    def no_session(*args, **kwargs):
        raise AssertionError("record must not load the saved session")

    monkeypatch.setattr(auth, "anonymous_client", lambda: anonymous)
    monkeypatch.setattr(auth, "load_client", no_session)
    out = tmp_path / "artist.json"
    assert ytm.main(["record", "artist", '{"channelId": "UCr_iyUANcn9OX_yy9piYoLw"}', str(out)]) == 0
    assert anonymous.calls == [("get_artist", ("UCr_iyUANcn9OX_yy9piYoLw",), {})]
    assert json.loads(out.read_text()) == {"name": "Radiohead", "subscribed": False, "nested": {"keep": 1}}


def test_browse_page_lists_and_applies_sort_orders():
    from test_pages import sort_header

    grid = {"gridRenderer": {"items": [album_item(i) for i in range(3)]}}
    base = single_column([grid], header="Albums")
    base["contents"]["singleColumnBrowseResultsRenderer"]["tabs"][0]["tabRenderer"]["content"][
        "sectionListRenderer"]["header"] = sort_header("R0", "R1")
    page, sent = browse_page([base], browseId="MPADUCx", params="p")
    assert [o["title"] for o in page["orders"]] == ["Order 0", "Order 1"] and page["order"] is None
    resorted = {"continuationContents": {"sectionListContinuation": {"contents": [
        {"gridRenderer": {"items": [album_item(9)]}}]}}}
    page, sent = browse_page([base, resorted], browseId="MPADUCx", params="p", order=1)
    assert page["order"] == 1 and page["title"] == "Albums", "the header comes from the unsorted page"
    assert [a["browseId"] for a in page["sections"][0]["items"]] == ["MPREb_9"]
    assert len(sent) == 2 and sent[1][2].startswith("&ctoken=R1")


def test_resorting_a_library_page_drops_its_cached_copies(tmp_path):
    import ytm_cache as caches
    from test_pages import sort_header

    def responses():
        grid = {"gridRenderer": {"items": [album_item(i) for i in range(3)]}}
        base = single_column([grid], header="Playlists")
        base["contents"]["singleColumnBrowseResultsRenderer"]["tabs"][0]["tabRenderer"]["content"][
            "sectionListRenderer"]["header"] = sort_header("R0", "R1")
        resorted = {"continuationContents": {"sectionListContinuation": {"contents": [
            {"gridRenderer": {"items": [album_item(9)]}}]}}}
        return BrowseYT(base, resorted)

    cache = caches.DiskCache(tmp_path / "data")
    library = {"browseId": "FEmusic_liked_playlists"}
    cache.put("browsePage", library, {"type": "page", "order": 0})
    cache.put("browsePage", dict(library, order=0), {"type": "page", "order": 0})
    cache.put("browsePage", {"browseId": "FEmusic_liked_albums"}, {"type": "page"})
    cache.put("libraryPlaylists", {"limit": 200}, {"items": []})
    worker = ytm.Worker(responses(), "ok", cache=cache)
    reply = call(worker, "browsePage", browseId="FEmusic_liked_playlists", order=1, cache="refresh")
    assert reply["ok"] and reply["result"]["order"] == 1
    assert [o["selected"] for o in reply["result"]["orders"]] == [False, True], "the applied order is selected"
    assert cache.get("browsePage", library) is None, "YouTube saved the new order: the plain copy is stale"
    assert cache.get("browsePage", dict(library, order=0)) is None, "and so is every other order"
    assert cache.get("browsePage", dict(library, order=1)) is None, "the re-sort's own reply is not stored"
    assert cache.get("libraryPlaylists", {"limit": 200}) is None, "get_library_playlists reads that page"
    assert cache.get("browsePage", {"browseId": "FEmusic_liked_albums"}) is not None

    # An artist's discography is re-sorted for that reply only.
    discography = {"browseId": "MPADUCx", "params": "p"}
    cache.put("browsePage", discography, {"type": "page"})
    cache.put("libraryPlaylists", {"limit": 200}, {"items": []})
    worker.yt = responses()
    assert call(worker, "browsePage", order=1, **discography)["ok"]
    assert cache.get("browsePage", discography) is not None
    assert cache.get("libraryPlaylists", {"limit": 200}) is not None
