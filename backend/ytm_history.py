"""History (S15): plays recorded in YouTube Music history, entries removed from it.

Both methods change the account (MUTATES): the shell sends them with
retry:false and never sends one again when its outcome is unknown.

- ``reportPlays({videoIds})``, on the ``report`` lane: for each id in
  order, ``get_song(videoId)`` and then ``add_history_item(song)``, both
  on the same account client (ytmusicapi: the tracking URL get_song hands
  out belongs to the client that asked). That is the ping the web player
  sends when a song starts, and what puts it in History, Listen again,
  Recaps and the recommendations. Returns ``{results, reported}``, one
  result ``{videoId, ok, retry}`` per id, in order: ``ok`` when YouTube
  answered 204; otherwise ``retry`` says whether a later try may work and
  cannot count twice (the song lookup failed on the network or was rate
  limited, so no ping went out). Any other failure is final: a song with
  no tracking URL, an answer other than 204, or a ping that failed on its
  way (it may have arrived). One player request and one ping per id, until
  the first passing failure: the ids after it are ``retry`` with nothing
  sent (the next lookup would meet the same throttle or outage).
- ``removeHistory({tokens})``: ``remove_history_items(tokens)`` with the
  ``historyToken`` of History rows (S3; fresh replies only, never cached).
  It succeeds only when YouTube processed every token; otherwise UPSTREAM
  with ``stale: true`` (the page was out of date: the shell puts the row
  back and reads History again). Returns ``{removed}``. ytmusicapi says it
  does not work for brand accounts (S16 hides it there).

Both drop the cached History. The tracking URL, its parameters and any
error text that could quote them never reach a reply or a log, and neither
do tokens.

Area module (see AREA_MODULES in ytm.py).
"""

import re

import ytm_actions as actions
import ytm_normalize as norm
from ytm_proto import Fixed, WorkerError

# At most this many plays per reportPlays call (the shell sends 20).
REPORT_MAX = 50
TOKENS_MAX = 50
TOKEN_MAX = 4096
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
NOT_REMOVED = "YouTube Music did not remove it from your history: the page it came from is out of date"


class NotRemoved(actions.StaleToken):
    """YouTube did not process a history token: UPSTREAM with ``stale: true``."""

    def __init__(self):
        WorkerError.__init__(self, "UPSTREAM", NOT_REMOVED)


def _video_ids(params):
    value = params.get("videoIds")
    if not isinstance(value, list) or not value or len(value) > REPORT_MAX:
        raise WorkerError("BAD_REQUEST", f"'videoIds' must be a list of 1 to {REPORT_MAX} video ids")
    if not all(isinstance(v, str) and VIDEO_ID.fullmatch(v) for v in value):
        raise WorkerError("BAD_REQUEST", "'videoIds' must hold 11-character video ids")
    return list(value)


def _tokens(params):
    value = params.get("tokens")
    if not isinstance(value, list) or not value or len(value) > TOKENS_MAX or not all(
            isinstance(t, str) and t.strip() and len(t) <= TOKEN_MAX for t in value):
        raise WorkerError("BAD_REQUEST", "'tokens' must be a list of history tokens from a fresh History page")
    return list(value)


def _passing(exc):
    """A failure that sent nothing that could count and may pass: the network
    or a rate limit. (ytm.classify lives in ytm.py, which modules cannot import.)"""
    try:
        import requests

        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return True
    except ImportError:
        pass
    from ytmusicapi.exceptions import YTMusicServerError

    return isinstance(exc, YTMusicServerError) and "HTTP 429" in str(exc).split("\n", 1)[0]


def _tracking_url(song):
    tracking = song.get("playbackTracking") if isinstance(song, dict) else None
    url = (((tracking or {}).get("videostatsPlaybackUrl") or {}).get("baseUrl")) if isinstance(tracking, dict) else None
    return url if isinstance(url, str) and url else None


def report_one(yt, video_id):
    """One play: ``{videoId, ok, retry}``. Logs exception types only (a
    requests error quotes the URL it was sending to)."""
    try:
        song = yt.get_song(video_id)
    except Exception as exc:  # noqa: BLE001 - per song, never the whole batch
        norm.log(f"reportPlays: song lookup failed ({type(exc).__name__})")
        return {"videoId": video_id, "ok": False, "retry": _passing(exc)}
    if _tracking_url(song) is None:
        return {"videoId": video_id, "ok": False, "retry": False}
    try:
        response = yt.add_history_item(song)
    except Exception as exc:  # noqa: BLE001 - it may have reached YouTube: final
        norm.log(f"reportPlays: ping failed ({type(exc).__name__})")
        return {"videoId": video_id, "ok": False, "retry": False}
    return {"videoId": video_id, "ok": getattr(response, "status_code", None) == 204, "retry": False}


def m_report_plays(worker, params):
    video_ids = _video_ids(params)
    worker.require_auth()
    # One client for the whole batch: get_song and add_history_item must
    # share it, even if the session is swapped while this lane works.
    yt = worker.yt
    results = []
    for video_id in video_ids:
        # Rate limited or offline: the rest wait too (no ping went out for them).
        if results and results[-1]["retry"]:
            results.append({"videoId": video_id, "ok": False, "retry": True})
        else:
            results.append(report_one(yt, video_id))
    return {"results": results, "reported": sum(1 for r in results if r["ok"])}


def m_remove_history(worker, params):
    tokens = _tokens(params)
    worker.require_auth()
    if not actions.processed(worker.yt.remove_history_items(feedbackTokens=tokens)):
        raise NotRemoved()
    return {"removed": len(tokens)}


METHODS = {"reportPlays": m_report_plays, "removeHistory": m_remove_history}
CACHEABLE = set()
# Reports run beside browsing: a batch of 20 is about 40 requests.
LANES = {"reportPlays": "report"}
# History itself, and its browsePage copy (a See all that lands there).
_HISTORY = [("history", None, None), ("browsePage", Fixed("FEmusic_history"), "browseId")]
INVALIDATES = {"reportPlays": _HISTORY, "removeHistory": _HISTORY}
RECORDERS = {}
MUTATES = {"reportPlays", "removeHistory"}
