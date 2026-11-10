"""ytm_history (S15): plays reported to YouTube Music history, entries removed.

Both methods change the account, so every call is pinned against a fake
client; the real ones are only run live by the dispatching session."""

import json
import os
import threading
import time

import pytest
import requests
from ytmusicapi.exceptions import YTMusicServerError

import ytm
import ytm_history as history

TRACKING = "https://s.youtube.com/api/stats/playback?cl=1&docid=x&secretparam=leak"
PROCESSED = {"feedbackResponses": [{"isProcessed": True}]}


class Response:
    def __init__(self, status_code):
        self.status_code = status_code


def song(video_id, tracking=True):
    out = {"videoDetails": {"videoId": video_id}, "playabilityStatus": {"status": "OK"}}
    if tracking:
        out["playbackTracking"] = {"videostatsPlaybackUrl": {"baseUrl": TRACKING + "&v=" + video_id}}
    return out


class FakeYT:
    """Records every call as (client name, method, args, kwargs). `songs`
    maps a videoId to a song dict or an exception; `pings` to a status code
    or an exception (default 204)."""

    def __init__(self, name="a", songs=None, pings=None, removed=PROCESSED, log=None):
        self.name = name
        self.songs = songs or {}
        self.pings = pings or {}
        self.removed = removed
        self.calls = [] if log is None else log
        self.on_song = None

    def get_account_info(self):
        return {"accountName": "x"}

    def get_song(self, video_id):
        self.calls.append((self.name, "get_song", video_id))
        if self.on_song:
            self.on_song(video_id)
        value = self.songs.get(video_id, song(video_id))
        if isinstance(value, Exception):
            raise value
        return value

    def add_history_item(self, item):
        video_id = item["videoDetails"]["videoId"]
        self.calls.append((self.name, "add_history_item", video_id))
        value = self.pings.get(video_id, 204)
        if isinstance(value, Exception):
            raise value
        return Response(value)

    def remove_history_items(self, feedbackTokens):  # noqa: N803 - ytmusicapi's name
        self.calls.append((self.name, "remove_history_items", list(feedbackTokens)))
        if isinstance(self.removed, Exception):
            raise self.removed
        return self.removed


def call(worker, method, **params):
    return worker.handle({"id": 7, "method": method, "params": params})


def signed_in(yt=None):
    return ytm.Worker(yt or FakeYT(), "ok")


IDS = ["nbCOAPR33ME", "7fv84nPfTH0", "1uYWYWPc9HU"]


# ---- reportPlays -----------------------------------------------------------------------------


def test_each_play_is_get_song_then_add_history_item_on_the_same_client():
    worker = signed_in()
    reply = call(worker, "reportPlays", videoIds=IDS)
    assert reply["ok"], reply
    assert worker.yt.calls == [(name, method, v) for v in IDS for name, method in
                               (("a", "get_song"), ("a", "add_history_item"))], "in order, one pair per play"
    assert reply["result"] == {"results": [{"videoId": v, "ok": True, "retry": False} for v in IDS], "reported": 3}


def test_a_session_swapped_mid_batch_does_not_split_a_pair():
    log = []
    first, second = FakeYT("a", log=log), FakeYT("b", log=log)
    worker = signed_in(first)
    first.on_song = lambda video_id: setattr(worker, "yt", second)  # reloadAuth on another thread
    assert call(worker, "reportPlays", videoIds=IDS[:2])["ok"]
    assert {name for name, _, _ in log} == {"a"}, "the batch keeps the client it started with"


def test_repeated_ids_are_separate_plays():
    worker = signed_in()
    reply = call(worker, "reportPlays", videoIds=[IDS[0], IDS[0]])
    assert reply["result"]["reported"] == 2
    assert [c[1] for c in worker.yt.calls] == ["get_song", "add_history_item"] * 2


def test_an_answer_other_than_204_is_a_failed_entry_and_the_rest_go_on():
    worker = signed_in(FakeYT(pings={IDS[0]: 200, IDS[1]: 403}))
    reply = call(worker, "reportPlays", videoIds=IDS)
    assert reply["ok"]
    assert reply["result"]["results"] == [
        {"videoId": IDS[0], "ok": False, "retry": False},
        {"videoId": IDS[1], "ok": False, "retry": False},
        {"videoId": IDS[2], "ok": True, "retry": False},
    ]
    assert reply["result"]["reported"] == 1


@pytest.mark.parametrize("error, retry", [
    (requests.exceptions.ConnectionError("no route"), True),
    (requests.exceptions.ReadTimeout("slow"), True),
    (YTMusicServerError("Server returned HTTP 429: Too Many Requests."), True),
    (YTMusicServerError("Server returned HTTP 400: Bad Request."), False),
    (KeyError("videoDetails"), False),
])
def test_a_failed_song_lookup_sends_no_ping(error, retry):
    worker = signed_in(FakeYT(songs={IDS[0]: error}))
    reply = call(worker, "reportPlays", videoIds=IDS[:2])
    assert reply["ok"]
    assert reply["result"]["results"][0] == {"videoId": IDS[0], "ok": False, "retry": retry}
    assert ("a", "add_history_item", IDS[0]) not in worker.yt.calls
    # A final failure is that song's own; a passing one holds the rest back.
    assert reply["result"]["results"][1] == {"videoId": IDS[1], "ok": not retry, "retry": retry}


@pytest.mark.parametrize("error", [
    YTMusicServerError("Server returned HTTP 429: Too Many Requests."),
    requests.exceptions.ConnectionError("no route"),
    requests.exceptions.ConnectTimeout("stalled"),
])
def test_the_first_passing_failure_sends_nothing_more(error):
    worker = signed_in(FakeYT(songs={IDS[1]: error}))
    reply = call(worker, "reportPlays", videoIds=IDS + [IDS[0]])
    assert reply["ok"]
    assert worker.yt.calls == [("a", "get_song", IDS[0]), ("a", "add_history_item", IDS[0]),
                               ("a", "get_song", IDS[1])], "no lookup after the throttle or outage"
    assert reply["result"] == {"reported": 1, "results": [
        {"videoId": IDS[0], "ok": True, "retry": False},
        {"videoId": IDS[1], "ok": False, "retry": True},
        {"videoId": IDS[2], "ok": False, "retry": True},
        {"videoId": IDS[0], "ok": False, "retry": True},
    ]}


def test_a_song_without_tracking_is_final_and_sends_no_ping():
    worker = signed_in(FakeYT(songs={IDS[0]: song(IDS[0], tracking=False)}))
    reply = call(worker, "reportPlays", videoIds=[IDS[0]])
    assert reply["result"]["results"] == [{"videoId": IDS[0], "ok": False, "retry": False}]
    assert [c[1] for c in worker.yt.calls] == ["get_song"]


def test_a_ping_that_fails_on_its_way_is_final_and_never_quotes_the_url(capsys, monkeypatch):
    monkeypatch.setenv("YTM_DEBUG", "1")
    leak = requests.exceptions.ConnectionError(f"Max retries exceeded with url: {TRACKING}")
    worker = signed_in(FakeYT(pings={IDS[0]: leak}))
    reply = call(worker, "reportPlays", videoIds=[IDS[0]])
    assert reply["ok"] and reply["result"]["results"] == [{"videoId": IDS[0], "ok": False, "retry": False}], \
        "it may have reached YouTube: never sent again"
    err = capsys.readouterr().err
    for text in (json.dumps(reply), err):
        assert "secretparam" not in text and "s.youtube.com" not in text
    assert "reportPlays ok" in err, "the debug line names the method and outcome only"


def test_report_plays_needs_a_session_and_sends_nothing_without_one():
    worker = ytm.Worker(FakeYT(), "none")
    assert call(worker, "reportPlays", videoIds=IDS)["error"]["code"] == "AUTH_REQUIRED"
    assert worker.yt.calls == []


@pytest.mark.parametrize("video_ids", [None, [], "nbCOAPR33ME", ["short"], [5], ["nbCOAPR33ME", None],
                                       ["nbCOAPR33ME\n"], ["\nnbCOAPR33ME"],
                                       ["nbCOAPR33ME"] * (history.REPORT_MAX + 1)])
def test_report_plays_bad_requests(video_ids):
    worker = signed_in()
    params = {} if video_ids is None else {"videoIds": video_ids}
    assert worker.handle({"id": 1, "method": "reportPlays", "params": params})["error"]["code"] == "BAD_REQUEST"
    assert worker.yt.calls == []


def test_report_plays_runs_on_the_report_lane():
    assert ytm.LANES["reportPlays"] == "report" and "report" in ytm.KNOWN_LANES
    assert "removeHistory" not in ytm.LANES, "one request: answered in order like the other edits"


def test_a_report_never_holds_up_the_requests_behind_it(monkeypatch):
    lines = []
    blocked = threading.Event()

    class Out:
        def write(self, text):
            lines.append(json.loads(text))

        def flush(self):
            pass

    def slow(worker, params):
        blocked.wait(5)
        return {"results": [], "reported": 0}

    monkeypatch.setitem(ytm.METHODS, "reportPlays", slow)
    read_fd, write_fd = os.pipe()
    thread = threading.Thread(target=ytm.serve, args=(5,), daemon=True,
                              kwargs={"worker": signed_in(), "stdin_fd": read_fd, "out": Out()})
    thread.start()
    os.write(write_fd, b'{"id":1,"method":"reportPlays","params":{"videoIds":["nbCOAPR33ME"]}}\n'
                       b'{"id":2,"method":"ping"}\n')
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(line.get("id") == 2 for line in lines):
        time.sleep(0.01)
    assert any(line.get("id") == 2 for line in lines), "ping answered"
    assert not any(line.get("id") == 1 for line in lines), "while the report is still on its lane"
    blocked.set()
    while time.monotonic() < deadline and not any(line.get("id") == 1 for line in lines):
        time.sleep(0.01)
    assert any(line.get("id") == 1 and line.get("ok") for line in lines)
    os.close(write_fd)
    thread.join(timeout=5)
    os.close(read_fd)


# ---- removeHistory ---------------------------------------------------------------------------


def test_remove_history_sends_the_tokens_it_was_given():
    worker = signed_in()
    reply = call(worker, "removeHistory", tokens=["tok-1"])
    assert reply["ok"] and reply["result"] == {"removed": 1}
    assert worker.yt.calls == [("a", "remove_history_items", ["tok-1"])]


@pytest.mark.parametrize("response", [{"feedbackResponses": [{"isProcessed": False}]}, {"feedbackResponses": []}, {},
                                      None, {"feedbackResponses": [{"isProcessed": True}, {"isProcessed": False}]}])
def test_remove_history_is_upstream_and_stale_unless_processed(response):
    worker = signed_in(FakeYT(removed=response))
    reply = call(worker, "removeHistory", tokens=["tok-1", "tok-2"])
    assert reply["error"]["code"] == "UPSTREAM" and reply["error"]["stale"] is True
    assert "tok" not in reply["error"]["message"]


@pytest.mark.parametrize("tokens", [None, [], "tok", [""], ["  "], [5], ["x" * (history.TOKEN_MAX + 1)],
                                    ["t"] * (history.TOKENS_MAX + 1)])
def test_remove_history_bad_requests(tokens):
    worker = signed_in()
    params = {} if tokens is None else {"tokens": tokens}
    assert worker.handle({"id": 1, "method": "removeHistory", "params": params})["error"]["code"] == "BAD_REQUEST"
    assert worker.yt.calls == []


def test_remove_history_needs_a_session():
    worker = ytm.Worker(FakeYT(), "none")
    assert call(worker, "removeHistory", tokens=["tok"])["error"]["code"] == "AUTH_REQUIRED"
    assert worker.yt.calls == []


# ---- registry and cache ------------------------------------------------------------------------


def test_both_methods_are_mutations_that_drop_history():
    assert {"reportPlays", "removeHistory"} <= ytm.MUTATES
    for name in ("reportPlays", "removeHistory"):
        assert ("history", None, None) in ytm.INVALIDATES[name]
        assert name not in ytm.CACHEABLE
    assert ytm.METHODS["reportPlays"] is history.m_report_plays


@pytest.mark.parametrize("method, params, yt", [
    ("reportPlays", {"videoIds": IDS[:1]}, FakeYT()),
    ("removeHistory", {"tokens": ["tok"]}, FakeYT()),
    # A remove YouTube did not process may still have been sent: it drops too.
    ("removeHistory", {"tokens": ["tok"]}, FakeYT(removed={})),
])
def test_an_edit_drops_the_cached_history(method, params, yt):
    worker = signed_in(yt)
    worker.cache().put("history", {"limit": 200}, {"items": [{"videoId": IDS[0]}]})
    worker.cache().put("browsePage", {"browseId": "FEmusic_history"}, {"type": "page"})
    worker.cache().put("browsePage", {"browseId": "FEmusic_home"}, {"type": "page"})
    worker.handle({"id": 1, "method": method, "params": params})
    assert worker.cache().get("history", {"limit": 200}) is None
    assert worker.cache().get("browsePage", {"browseId": "FEmusic_history"}) is None
    assert worker.cache().get("browsePage", {"browseId": "FEmusic_home"}) is not None


def test_the_ready_event_lists_both_as_mutating():
    lines = []

    class Out:
        def write(self, text):
            lines.append(text)

        def flush(self):
            pass

    read, write = os.pipe()
    os.close(write)
    try:
        ytm.serve(0, worker=ytm.Worker(FakeYT(), "none"), stdin_fd=read, out=Out())
    finally:
        os.close(read)
    assert {"reportPlays", "removeHistory"} <= set(json.loads(lines[0])["mutates"])
