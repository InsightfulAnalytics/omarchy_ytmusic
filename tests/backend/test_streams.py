import pytest
import requests
from ytmusicapi.exceptions import YTMusicServerError

import ytm
import ytm_streams as streams


class FakeYDL:
    created = 0

    def __init__(self, opts, info=None, error=None):
        FakeYDL.created += 1
        self.opts = opts
        self.info = info
        self.error = error
        self.calls = []

    def extract_info(self, url, download=True):
        self.calls.append((url, download))
        if self.error:
            raise self.error
        return self.info


def factory(info=None, error=None):
    made = []

    def make(opts):
        ydl = FakeYDL(opts, info, error)
        made.append(ydl)
        return ydl

    make.made = made
    return make


def test_parse_expiry():
    assert streams.parse_expiry("https://rr1.googlevideo.com/videoplayback?expire=1789100000&id=x") == 1789100000
    assert streams.parse_expiry("https://rr1.googlevideo.com/videoplayback/expire/1789100001/id/x") == 1789100001
    assert streams.parse_expiry("https://example.com/audio", now=1000) == 1000 + streams.DEFAULT_LIFETIME


def test_resolve_returns_url_and_expiry_and_reuses_one_instance(config_dir, monkeypatch):
    monkeypatch.delenv("YTM_STREAM_WITH_ACCOUNT", raising=False)
    make = factory(info={"url": "https://g.googlevideo.com/v?expire=1789100000", "format_id": "251",
                         "acodec": "opus", "abr": 131.4})
    resolver = streams.Resolver(factory=make)
    first = resolver.resolve("abcdefghijk")
    resolver.resolve("bcdefghijkl")
    assert first == {"videoId": "abcdefghijk", "url": "https://g.googlevideo.com/v?expire=1789100000",
                     "expires": 1789100000, "format": "251", "codec": "opus", "abr": 131.4, "account": False}
    assert len(make.made) == 1, "yt-dlp is created once per worker"
    ydl = make.made[0]
    assert ydl.calls == [("https://www.youtube.com/watch?v=abcdefghijk", False),
                         ("https://www.youtube.com/watch?v=bcdefghijkl", False)]
    assert ydl.opts["format"] == streams.FORMAT and ydl.opts["skip_download"]


def test_requested_formats_fallback_and_missing_url():
    resolver = streams.Resolver(factory=factory(info={"requested_formats": [{"url": "https://x/a?expire=5"}]}))
    assert resolver.resolve("v")["url"] == "https://x/a?expire=5"
    with pytest.raises(streams.StreamError) as err:
        streams.Resolver(factory=factory(info={})).resolve("v")
    assert err.value.code == "UPSTREAM"


@pytest.mark.parametrize("message, code, retryable", [
    ("ERROR: [youtube] abc: Video unavailable", "NOT_FOUND", False),
    ("ERROR: [youtube] abc: Private video. Sign in", "NOT_FOUND", False),
    ("ERROR: [youtube] abc: Unable to download API page: timed out", "NETWORK", True),
    ("ERROR: [youtube] abc: Some new signature problem", "UPSTREAM", False),
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser", "BOT_CHECK", False),
    ("ERROR: [youtube] abc: Unable to download webpage: HTTP Error 429: Too Many Requests", "BOT_CHECK", False),
])
def test_errors_are_mapped(message, code, retryable):
    with pytest.raises(streams.StreamError) as err:
        streams.Resolver(factory=factory(error=RuntimeError(message))).resolve("abc")
    assert (err.value.code, err.value.retryable) == (code, retryable)
    assert not err.value.message.startswith("ERROR:")


def test_account_cookies_only_when_opted_in(config_dir, monkeypatch):
    config_dir.mkdir(parents=True)
    (config_dir / "cookies.txt").write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.delenv("YTM_STREAM_WITH_ACCOUNT", raising=False)
    assert "cookiefile" not in streams.Resolver().options()
    monkeypatch.setenv("YTM_STREAM_WITH_ACCOUNT", "1")
    assert streams.Resolver().options()["cookiefile"] == str(config_dir / "cookies.txt")


class FakeResolver:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def resolve(self, video_id, account=None):
        if self.error:
            raise self.error
        return dict(self.result, videoId=video_id, account=bool(account))


def test_warm_loads_ytdlp_once_in_the_background():
    make = factory(info={"url": "https://x/a?expire=5"})
    resolver = streams.Resolver(factory=make)
    worker = ytm.Worker(object(), "none", resolver=resolver)
    assert worker.handle({"id": 1, "method": "warmStreams"})["ok"]
    assert worker.handle({"id": 2, "method": "warmStreams"})["ok"]
    assert worker.handle({"id": 3, "method": "resolve", "params": {"videoId": "v"}})["ok"]
    assert len(make.made) == 1, "warm-up and resolve share one yt-dlp instance"


def test_warm_is_harmless_without_warm_support():
    worker = ytm.Worker(object(), "none", resolver=FakeResolver({"url": "https://x", "expires": 5}))
    assert worker.handle({"id": 1, "method": "warmStreams"})["ok"]


def test_worker_resolve_method():
    worker = ytm.Worker(object(), "none", resolver=FakeResolver({"url": "https://x", "expires": 5}))
    reply = worker.handle({"id": 1, "method": "resolve", "params": {"videoId": "abc"}})
    assert reply["ok"] and reply["result"]["videoId"] == "abc" and reply["result"]["url"] == "https://x"
    failing = ytm.Worker(object(), "none", resolver=FakeResolver(error=streams.StreamError("NETWORK", "offline", True)))
    error = failing.handle({"id": 2, "method": "resolve", "params": {"videoId": "abc"}})["error"]
    assert (error["code"], error["retryable"]) == ("NETWORK", True)
    assert worker.handle({"id": 3, "method": "resolve", "params": {}})["error"]["code"] == "BAD_REQUEST"


def test_account_flag_selects_the_cookie_instance(config_dir, monkeypatch):
    monkeypatch.delenv("YTM_STREAM_WITH_ACCOUNT", raising=False)
    config_dir.mkdir(parents=True)
    (config_dir / "cookies.txt").write_text("# Netscape HTTP Cookie File\n")
    make = factory(info={"url": "https://x/a?expire=5"})
    resolver = streams.Resolver(factory=make)
    assert resolver.resolve("v")["account"] is False, "default stays anonymous"
    assert resolver.resolve("v", account=True)["account"] is True
    assert resolver.resolve("v", account=False)["account"] is False
    assert len(make.made) == 2, "one yt-dlp instance per mode, each built once"
    anonymous, account = make.made
    assert "cookiefile" not in anonymous.opts and account.opts["cookiefile"] == str(config_dir / "cookies.txt")
    assert len(anonymous.calls) == 2 and len(account.calls) == 1


def test_account_flag_without_a_session_falls_back_to_anonymous(config_dir, monkeypatch):
    monkeypatch.delenv("YTM_STREAM_WITH_ACCOUNT", raising=False)
    make = factory(info={"url": "https://x/a?expire=5"})
    assert streams.Resolver(factory=make).resolve("v", account=True)["account"] is False
    assert "cookiefile" not in make.made[0].opts


def test_worker_passes_the_account_flag():
    worker = ytm.Worker(object(), "none", resolver=FakeResolver({"url": "https://x", "expires": 5}))
    reply = worker.handle({"id": 1, "method": "resolve", "params": {"videoId": "abc", "account": True}})
    assert reply["result"]["account"] is True
    bad = worker.handle({"id": 2, "method": "resolve", "params": {"videoId": "abc", "account": "yes"}})
    assert bad["error"]["code"] == "BAD_REQUEST"


def test_stream_account_setting_makes_lookups_use_the_account():
    worker = ytm.Worker(object(), "none", resolver=FakeResolver({"url": "https://x", "expires": 5}))
    assert worker.handle({"id": 1, "method": "resolve", "params": {"videoId": "a"}})["result"]["account"] is False
    assert worker.handle({"id": 2, "method": "configure", "params": {"streamAccount": True}})["ok"]
    assert worker.handle({"id": 3, "method": "resolve", "params": {"videoId": "a"}})["result"]["account"] is True
    explicit = worker.handle({"id": 4, "method": "resolve", "params": {"videoId": "a", "account": False}})
    assert explicit["result"]["account"] is False, "a request's own flag wins"
    bad = worker.handle({"id": 5, "method": "configure", "params": {"streamAccount": "yes"}})
    assert not bad["ok"] or "streamAccount" not in bad["result"].get("settings", {})


def test_forget_account_drops_only_the_cookie_instances(config_dir, monkeypatch):
    monkeypatch.delenv("YTM_STREAM_WITH_ACCOUNT", raising=False)
    config_dir.mkdir(parents=True)
    (config_dir / "cookies.txt").write_text("# Netscape HTTP Cookie File\n")
    make = factory(info={"url": "https://x/a?expire=5"})
    resolver = streams.Resolver(factory=make)
    resolver.resolve("v")
    resolver.resolve("v", account=True)
    resolver.forget_account()
    resolver.resolve("v")
    resolver.resolve("v", account=True)
    assert len(make.made) == 3, "the anonymous instance is kept, the account one built again"


# ---- loudness (S17) ---------------------------------------------------------------------
# With the worker setting normalize "youtube", resolve also answers the song's
# loudnessDb from its raw player response (one request through the account
# client), memoised per video. The responses below copy the shape YouTube
# sent live (Karma Police: audioConfig loudnessDb -1.43, formats -1.44).


def player_response(loudness=-1.4300003, formats=-1.4399996):
    response = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {"videoId": "nbCOAPR33ME", "title": "Karma Police"},
        "streamingData": {"adaptiveFormats": [
            {"itag": 137, "mimeType": "video/mp4; codecs=\"avc1.640028\"", "loudnessDb": 99.0},
            {"itag": 251, "mimeType": "audio/webm; codecs=\"opus\"", "loudnessDb": formats},
        ]},
    }
    if loudness is not None:
        response["playerConfig"] = {"audioConfig": {
            "loudnessDb": loudness, "perceptualLoudnessDb": -8.43, "enablePerFormatLoudness": True,
            "trackAbsoluteLoudnessLkfs": -8.43, "loudnessTargetLkfs": -7}}
    return response


class PlayerClient:
    """A ytmusicapi client whose raw requests answer `answers` in turn (an
    exception is raised)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def _send_request(self, endpoint, body, additionalParams=""):
        self.requests.append((endpoint, body))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def stream_worker(client):
    resolver = streams.Resolver(factory=factory(info={"url": "https://x/a?expire=5", "format_id": "251"}))
    return ytm.Worker(client, "ok", resolver=resolver)


def resolve(worker, video_id="nbCOAPR33ME"):
    return worker.handle({"id": 1, "method": "resolve", "params": {"videoId": video_id}})


def test_loudness_only_when_normalize_is_youtube():
    client = PlayerClient(player_response())
    worker = stream_worker(client)
    plain = resolve(worker)
    assert plain["ok"] and "loudnessDb" not in plain["result"] and client.requests == []
    for mode in ("off", "dynamic"):
        assert worker.handle({"id": 2, "method": "configure", "params": {"normalize": mode}})["ok"]
        assert "loudnessDb" not in resolve(worker)["result"] and client.requests == []
    reply = worker.handle({"id": 3, "method": "configure", "params": {"normalize": "youtube"}})
    assert reply["result"] == {"settings": {"normalize": "youtube"}, "ignored": [], "reloaded": False}
    result = resolve(worker)["result"]
    assert result["loudnessDb"] == pytest.approx(-1.43, abs=0.001) and result["url"] == "https://x/a?expire=5"
    (endpoint, body), = client.requests
    assert endpoint == "player" and body["video_id"] == "nbCOAPR33ME"
    stamp = body["playbackContext"]["contentPlaybackContext"]["signatureTimestamp"]
    assert isinstance(stamp, int) and stamp > 19000, "get_song's body: days since the epoch, less one"


def test_normalize_setting_is_validated():
    worker = stream_worker(PlayerClient(player_response()))
    for bad in ("loud", True, None, 1):
        error = worker.handle({"id": 1, "method": "configure", "params": {"normalize": bad}})["error"]
        assert error["code"] == "BAD_REQUEST"
    assert ytm.SETTINGS["normalize"] is streams.validate_normalize
    assert "normalize" not in ytm.CLIENT_SETTINGS, "levelling never rebuilds the client or clears the cache"


def test_loudness_is_memoised_per_video_least_recently_used_out():
    client = PlayerClient(player_response(loudness=2.5))
    memo = streams.Loudness(size=2)
    assert memo.get(client, "a") == 2.5 and memo.get(client, "a") == 2.5
    assert len(client.requests) == 1, "asked once per video"
    memo.get(client, "b")
    memo.get(client, "a")  # a is now the most recently used
    memo.get(client, "c")  # b goes
    assert len(memo) == 2 and len(client.requests) == 3
    memo.get(client, "a")
    assert len(client.requests) == 3, "a stayed"
    memo.get(client, "b")
    assert len(client.requests) == 4, "b was dropped and is asked again"
    assert streams.LOUDNESS_MEMO == 500 and streams.Resolver().loudness.size == 500


def test_worker_memo_spans_requests():
    client = PlayerClient(player_response(loudness=3.0))
    worker = stream_worker(client)
    worker.handle({"id": 1, "method": "configure", "params": {"normalize": "youtube"}})
    assert resolve(worker)["result"]["loudnessDb"] == 3.0
    assert resolve(worker)["result"]["loudnessDb"] == 3.0
    assert len(client.requests) == 1


def test_loudness_is_null_when_youtube_gives_none():
    assert streams.loudness_db(player_response(loudness=None, formats=-5.53)) == pytest.approx(-5.53)
    assert streams.loudness_db(player_response(loudness=None, formats=None)) is None
    assert streams.loudness_db({"playabilityStatus": {"status": "UNPLAYABLE"}}) is None
    assert streams.loudness_db({"playerConfig": {"audioConfig": {"loudnessDb": "loud"}}}) is None
    assert streams.loudness_db({"playerConfig": {"audioConfig": {"loudnessDb": True}}}) is None
    assert streams.loudness_db(None) is None
    client = PlayerClient({"playabilityStatus": {"status": "OK"}})
    worker = stream_worker(client)
    worker.handle({"id": 1, "method": "configure", "params": {"normalize": "youtube"}})
    reply = resolve(worker)
    assert reply["ok"] and reply["result"]["loudnessDb"] is None
    resolve(worker)
    assert len(client.requests) == 1, "an answer without loudness is kept too"


@pytest.mark.parametrize("failure", [
    requests.exceptions.ConnectionError("offline"),
    requests.exceptions.Timeout("slow"),
    YTMusicServerError("Server returned HTTP 429: Too Many Requests."),
    KeyError("playerConfig"),
    ValueError("not JSON"),
])
def test_loudness_never_fails_the_resolve(failure):
    client = PlayerClient(failure, player_response(loudness=1.25))
    worker = stream_worker(client)
    worker.handle({"id": 1, "method": "configure", "params": {"normalize": "youtube"}})
    reply = resolve(worker)
    assert reply["ok"] and reply["result"]["url"] == "https://x/a?expire=5"
    assert reply["result"]["loudnessDb"] is None
    assert resolve(worker)["result"]["loudnessDb"] == 1.25, "a failure is not kept: the next resolve asks again"
    assert len(client.requests) == 2


def test_a_failed_stream_asks_no_loudness():
    client = PlayerClient(player_response())
    resolver = streams.Resolver(factory=factory(error=RuntimeError("ERROR: Video unavailable")))
    worker = ytm.Worker(client, "ok", resolver=resolver)
    worker.handle({"id": 1, "method": "configure", "params": {"normalize": "youtube"}})
    assert resolve(worker)["error"]["code"] == "NOT_FOUND"
    assert client.requests == []


# ---- quality (S16) ------------------------------------------------------------------------
# The worker setting `quality` (prefs.quality) picks the resolver's format;
# the shell gives mpv's own fallback (ytdl-format) the same strings.


def test_quality_formats():
    assert streams.QUALITIES == ("low", "normal", "high")
    assert streams.QUALITY_FORMATS == {"low": "bestaudio[abr<=70]/worstaudio",
                                       "normal": "bestaudio[abr<=130]/bestaudio",
                                       "high": "bestaudio[acodec=opus]/bestaudio/best"}
    assert streams.format_for("high") == streams.FORMAT == streams.format_for(None) == streams.format_for("x")
    assert streams.format_for("low") == "bestaudio[abr<=70]/worstaudio"


def test_quality_formats_match_the_shell_and_the_player_script():
    from conftest import ROOT

    model = (ROOT / "YtmModel.js").read_text()
    player = (ROOT / "bin" / "ytm-mpv").read_text()
    for quality, fmt in streams.QUALITY_FORMATS.items():
        assert f'{quality}: "{fmt}"' in model, f"YtmModel.js QUALITY_FORMATS.{quality}"
        assert f"'{fmt}'" in player, f"bin/ytm-mpv {quality}"


def test_each_quality_has_its_own_instance_and_format(config_dir, monkeypatch):
    monkeypatch.delenv("YTM_STREAM_WITH_ACCOUNT", raising=False)
    make = factory(info={"url": "https://x/a?expire=5", "format_id": "249", "abr": 50.1})
    resolver = streams.Resolver(factory=make)
    resolver.resolve("v")
    resolver.resolve("v", quality="low")
    resolver.resolve("w", quality="low")
    resolver.resolve("v", quality="high")
    assert [y.opts["format"] for y in make.made] == [streams.FORMAT, "bestaudio[abr<=70]/worstaudio"]
    assert [len(y.calls) for y in make.made] == [2, 2]
    resolver.warm("normal")
    assert make.made[-1].opts["format"] == "bestaudio[abr<=130]/bestaudio" and len(make.made) == 3


class QualityResolver(FakeResolver):
    def __init__(self):
        super().__init__({"url": "https://x", "expires": 5})
        self.asked = []

    def resolve(self, video_id, account=None, quality=None):
        self.asked.append(quality)
        return super().resolve(video_id, account)


def test_worker_resolves_at_the_quality_set():
    resolver = QualityResolver()
    worker = ytm.Worker(object(), "none", resolver=resolver)
    assert worker.handle({"id": 1, "method": "resolve", "params": {"videoId": "abc"}})["ok"]
    reply = worker.handle({"id": 2, "method": "configure", "params": {"quality": "low"}})
    assert reply["result"] == {"settings": {"quality": "low"}, "ignored": [], "reloaded": False}
    assert "quality" not in ytm.CLIENT_SETTINGS, "quality never rebuilds the client or clears the cache"
    worker.handle({"id": 3, "method": "resolve", "params": {"videoId": "abc"}})
    worker.handle({"id": 4, "method": "resolve", "params": {"videoId": "abc", "quality": "normal"}})
    worker.handle({"id": 5, "method": "configure", "params": {"quality": "high"}})
    worker.handle({"id": 6, "method": "resolve", "params": {"videoId": "abc"}})
    assert resolver.asked == [None, "low", "normal", None], "high is the resolver's default (no argument)"
    bad = worker.handle({"id": 7, "method": "resolve", "params": {"videoId": "abc", "quality": "best"}})
    assert bad["error"]["code"] == "BAD_REQUEST" and resolver.asked[-1] is None and len(resolver.asked) == 4
    for value in ("ultra", None, 1):
        assert worker.handle({"id": 8, "method": "configure", "params": {"quality": value}})["error"]["code"] == "BAD_REQUEST"


def test_warm_up_loads_the_instance_for_the_quality_set():
    class WarmResolver(QualityResolver):
        def __init__(self):
            super().__init__()
            self.warmed = []

        def warm(self, quality=None):
            self.warmed.append(quality)

    resolver = WarmResolver()
    worker = ytm.Worker(object(), "none", resolver=resolver)
    assert worker.handle({"id": 1, "method": "warmStreams"})["ok"]
    worker.wait_for_warm()
    assert worker.handle({"id": 2, "method": "configure", "params": {"quality": "low"}})["ok"]
    assert worker.handle({"id": 3, "method": "warmStreams"})["ok"]
    worker.wait_for_warm()
    assert worker.handle({"id": 4, "method": "configure", "params": {"quality": "high"}})["ok"]
    assert worker.handle({"id": 5, "method": "warmStreams"})["ok"]
    worker.wait_for_warm()
    assert resolver.warmed == [None, "low", None], "high (and none set) warms the default instance"


# ---- uploads (S10) ------------------------------------------------------------------------------
# An uploaded song plays only as the account. The shell asks resolve with
# account true for tracks of kind "upload"; the worker answers from the
# yt-dlp instance that reads the session's cookies (F1's account mode),
# at the audio quality set, while other songs keep the anonymous one.


def test_upload_items_resolve_with_the_account_instance(config_dir, monkeypatch):
    monkeypatch.delenv("YTM_STREAM_WITH_ACCOUNT", raising=False)
    config_dir.mkdir(parents=True)
    (config_dir / "cookies.txt").write_text("# Netscape HTTP Cookie File\n")
    make = factory(info={"url": "https://x/a?expire=5", "format_id": "141", "acodec": "mp4a.40.2"})
    worker = ytm.Worker(object(), "none", resolver=streams.Resolver(factory=make))
    song = worker.handle({"id": 1, "method": "resolve", "params": {"videoId": "pubsong0001"}})
    upload = worker.handle({"id": 2, "method": "resolve", "params": {"videoId": "upsong00001", "account": True}})
    assert song["ok"] and upload["ok"]
    assert song["result"]["account"] is False and upload["result"]["account"] is True
    anonymous, account = make.made
    assert "cookiefile" not in anonymous.opts and account.opts["cookiefile"] == str(config_dir / "cookies.txt")
    assert account.calls == [(streams.WATCH_URL + "upsong00001", False)]
    assert anonymous.calls == [(streams.WATCH_URL + "pubsong0001", False)]
    # At another quality an upload still streams as the account.
    assert worker.handle({"id": 3, "method": "configure", "params": {"quality": "low"}})["ok"]
    low = worker.handle({"id": 4, "method": "resolve", "params": {"videoId": "upsong00002", "account": True}})
    assert low["result"]["account"] is True
    assert make.made[-1].opts["cookiefile"] == str(config_dir / "cookies.txt")
    assert make.made[-1].opts["format"] == streams.QUALITY_FORMATS["low"]
