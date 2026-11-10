"""Resolve playable audio stream URLs with yt-dlp inside the long-running
worker, so each track skips yt-dlp's start-up and reuses its player cache
(measured: ~1.0-1.4 s warm vs ~2 s for a fresh yt-dlp per track).

mpv plays the returned URL through mpv/ytm-streams.lua while its playlist
keeps the youtube.com URL. Stream URLs expire (YouTube puts the time in the
URL) and are tied to this network; the Lua script falls back to yt-dlp when
one fails. Like mpv, this streams without the account unless
YTM_STREAM_WITH_ACCOUNT=1 or the request asks for it (``account: true``,
e.g. for uploads); each mode has its own yt-dlp instance.

With the worker setting ``normalize`` at "youtube" (S17), a lookup also
answers the song's loudness (see the loudness section below).
"""

import os
import re
import threading
import time
from collections import OrderedDict

import ytm_auth as auth
import ytm_normalize as norm
from ytm_proto import WorkerError

FORMAT = "bestaudio[acodec=opus]/bestaudio/best"
WATCH_URL = "https://www.youtube.com/watch?v="
DEFAULT_LIFETIME = 5 * 3600
_EXPIRE = re.compile(r"[?&/]expire[=/](\d+)")


class StreamError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def parse_expiry(url, now=None):
    match = _EXPIRE.search(url or "")
    if match:
        return int(match.group(1))
    return int((now if now is not None else time.time()) + DEFAULT_LIFETIME)


def stream_with_account():
    return os.environ.get("YTM_STREAM_WITH_ACCOUNT") == "1"


class _Quiet:
    def debug(self, message, **kwargs):
        pass

    def info(self, message, **kwargs):
        pass

    def warning(self, message, **kwargs):
        norm.log(f"yt-dlp: {message}")

    def error(self, message, **kwargs):
        pass  # raised as an exception right after; reported from there


def _error(exc):
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    message = re.sub(r"^ERROR:\s*", "", message)
    lowered = message.lower()
    if any(word in lowered for word in ("unavailable", "private video", "removed", "not available", "members-only")):
        return StreamError("NOT_FOUND", message)
    if any(word in lowered for word in ("unable to download", "timed out", "connection", "network", "temporary failure")):
        return StreamError("NETWORK", message, True)
    return StreamError("UPSTREAM", message)


class Resolver:
    """Two yt-dlp instances for the worker's lifetime, each imported and built
    on first use: an anonymous one and one with the account's cookies."""

    def __init__(self, factory=None):
        self._factory = factory
        self._ydl = {}
        # The warm-up thread and the worker's streams lane may both get here
        # first; lookups themselves only ever run on the lane.
        self._lock = threading.Lock()
        # S17: the songs' loudness, for the worker's lifetime.
        self.loudness = Loudness()

    def wants_account(self, account=None):
        """Whether a lookup uses the account: asked for (or the default) and signed in."""
        use = stream_with_account() if account is None else bool(account)
        return use and auth.cookies_path().exists()

    def options(self, account=None):
        opts = {
            "format": FORMAT,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "logger": _Quiet(),
        }
        if self.wants_account(account):
            opts["cookiefile"] = str(auth.cookies_path())
        return opts

    def _client(self, account=False):
        with self._lock:
            if account not in self._ydl:
                opts = self.options(account)
                if self._factory is None:
                    import yt_dlp

                    self._ydl[account] = yt_dlp.YoutubeDL(opts)
                else:
                    self._ydl[account] = self._factory(opts)
            return self._ydl[account]

    def warm(self):
        try:
            self._client(self.wants_account())
        except Exception as exc:  # noqa: BLE001 - resolve() reports it properly later
            norm.log(f"could not load yt-dlp: {type(exc).__name__}: {exc}")

    def resolve(self, video_id, account=None, loudness_from=None):
        """The stream URL; ``account`` None means the YTM_STREAM_WITH_ACCOUNT default.

        With ``loudness_from`` (a ytmusicapi client; S17) the reply also
        carries ``loudnessDb``: a number, or None when YouTube gave none or
        the lookup failed (it never fails the lookup itself).
        """
        use_account = self.wants_account(account)
        try:
            info = self._client(use_account).extract_info(WATCH_URL + video_id, download=False)
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises DownloadError and friends
            raise _error(exc) from exc
        url = info.get("url")
        if not url:
            requested = info.get("requested_formats") or []
            url = requested[0].get("url") if requested else None
        if not url:
            raise StreamError("UPSTREAM", "YouTube returned no playable audio stream")
        result = {
            "videoId": video_id,
            "url": url,
            "expires": parse_expiry(url),
            "format": info.get("format_id"),
            "codec": info.get("acodec"),
            "abr": info.get("abr"),
            "account": use_account,
        }
        if loudness_from is not None:
            result["loudnessDb"] = self.loudness.get(loudness_from, video_id)
        return result


# ---- loudness (S17) ------------------------------------------------------------
# The shell's loudness levelling (prefs.normalize, sent as the worker setting
# `normalize`): with "youtube", resolve also answers YouTube's loudness for
# the song, playerConfig.audioConfig.loudnessDb of its player response (dB
# above YouTube Music's loudness target; the shell turns a song down by that
# much when it is positive, as the web player does). ytmusicapi's get_song()
# sends that request but drops playerConfig from its reply, so the worker
# sends the same `player` request itself, through the account client (one
# HTTP request, counted in the reply). Values are kept per video in an LRU of
# LOUDNESS_MEMO; a failed request answers None and is not kept.

NORMALIZE_MODES = ("off", "youtube", "dynamic")
LOUDNESS_MEMO = 500


def validate_normalize(value):
    if value not in NORMALIZE_MODES:
        raise WorkerError("BAD_REQUEST", f"'normalize' must be one of {', '.join(NORMALIZE_MODES)}")
    return value


# configure() keys this module owns (ytm.py's core lists them).
SETTINGS = {"normalize": validate_normalize}


def player_body(video_id):
    """get_song()'s request body (ytmusicapi 1.12.2 mixins/browsing.py)."""
    from ytmusicapi.mixins._utils import get_datestamp

    return {
        "playbackContext": {"contentPlaybackContext": {"signatureTimestamp": get_datestamp() - 1}},
        "video_id": video_id,
    }


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def loudness_db(response):
    """The song's loudnessDb in a player response: playerConfig.audioConfig's,
    else its first audio format's; None when there is neither."""
    if not isinstance(response, dict):
        return None
    audio = ((response.get("playerConfig") or {}).get("audioConfig") or {})
    value = _number(audio.get("loudnessDb"))
    if value is not None:
        return value
    formats = (response.get("streamingData") or {}).get("adaptiveFormats") or []
    for fmt in formats:
        if isinstance(fmt, dict) and str(fmt.get("mimeType") or "").startswith("audio/"):
            value = _number(fmt.get("loudnessDb"))
            if value is not None:
                return value
    return None


class Loudness:
    """loudnessDb per video, least recently used out first past ``size``."""

    def __init__(self, size=LOUDNESS_MEMO):
        self.size = size
        self._memo = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self):
        return len(self._memo)

    def get(self, client, video_id):
        with self._lock:
            if video_id in self._memo:
                self._memo.move_to_end(video_id)
                return self._memo[video_id]
        try:
            value = loudness_db(client._send_request("player", player_body(video_id)))
        except Exception as exc:  # noqa: BLE001 - levelling is optional; the stream still plays
            norm.log(f"no loudness for a song: {type(exc).__name__}")
            return None
        with self._lock:
            self._memo[video_id] = value
            self._memo.move_to_end(video_id)
            while len(self._memo) > self.size:
                self._memo.popitem(last=False)
        return value
