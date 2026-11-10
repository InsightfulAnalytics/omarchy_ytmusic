#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "ytmusicapi==1.12.2",
#   "yt-dlp",
#   "secretstorage",
# ]
# ///
"""damir.ytmusic backend: ytmusicapi behind a JSON-lines worker and a CLI.

The shell runs ``ytm.py serve``: one JSON request per stdin line, one JSON
reply per stdout line. It exits on stdin EOF (so it can never outlive the
shell) or after ``--idle`` seconds without requests (so it costs nothing when
you are not browsing).

    request  {"id": 1, "method": "search", "params": {"query": "..."}}
    success  {"id": 1, "ok": true, "result": {...}, "ms": 612, "http": 1}
    failure  {"id": 1, "ok": false, "error": {"code": "...", "message": "...", "retryable": false}}
    events   {"event": "ready", ...}  {"event": "idle-exit"}

Methods come from this file (the core) and from the area modules in
AREA_MODULES, each of which exports the same tables (METHODS, CACHEABLE,
LANES, INVALIDATES, RECORDERS, MUTATES, and optionally SETTINGS,
CLIENT_SETTINGS and NO_INVALIDATES). They are merged at import; a name
registered twice is an error.
"""

import sys

# The plugin directory is watched by the shell; a __pycache__ appearing there
# would trigger plugin reloads.
sys.dont_write_bytecode = True

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import queue  # noqa: E402
import select  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import ytm_actions as actions  # noqa: E402
import ytm_artist as artist  # noqa: E402
import ytm_auth as auth  # noqa: E402
import ytm_cache as caches  # noqa: E402
import ytm_collections as collections  # noqa: E402
import ytm_explore as explore  # noqa: E402
import ytm_history as history  # noqa: E402
import ytm_home as home  # noqa: E402
import ytm_library as library  # noqa: E402
import ytm_normalize as norm  # noqa: E402
import ytm_now as now  # noqa: E402
import ytm_pages as pages  # noqa: E402
import ytm_play as play  # noqa: E402
import ytm_raw as raw  # noqa: E402
import ytm_streams as streams  # noqa: E402
from ytm_proto import (  # noqa: E402, F401 - re-exported for tests and tools
    Fixed,
    Prefixed,
    WorkerError,
    choice,
    opt_bool,
    opt_cursor,
    opt_int,
    opt_limit,
    opt_order,
    opt_str,
    req_list,
    req_str,
)

PROTOCOL = 1
VERSION = "1.0.0"
SEARCH_FILTERS = ("songs", "videos", "albums", "artists", "playlists", "community_playlists", "featured_playlists")
RATINGS = ("LIKE", "DISLIKE", "INDIFFERENT")
PRIVACY = ("PRIVATE", "UNLISTED", "PUBLIC")
CACHE_MODES = ("prefer", "refresh", "bypass")
# Background threads ("lanes") for methods that must not hold up the requests
# behind them: a stream lookup takes ~1 s, completions must show while a
# search is still running, long page reads ("pages"), whole-collection reads
# for playing ("tracks"), uploads and play reports run beside browsing. A
# lane handles its own requests one at a time, in order; replies carry the
# request id, so they may overtake each other.
# (ytmusicapi's requests session is shared between lanes; requests' connection
# pool and cookie jar are thread-safe, and ytm_raw keeps its state per thread.)
KNOWN_LANES = ("streams", "suggest", "pages", "tracks", "upload", "report")


def classify(exc):
    """Map any exception to a WorkerError with one of the protocol's codes."""
    if isinstance(exc, WorkerError):
        return exc
    from ytmusicapi.exceptions import YTMusicServerError, YTMusicUserError

    message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    if isinstance(exc, YTMusicServerError):
        if "HTTP 401" in message or "HTTP 403" in message:
            return WorkerError("AUTH_EXPIRED", "YouTube Music rejected the saved session; sign in again")
        if "HTTP 429" in message:
            return WorkerError("RATE_LIMITED", "YouTube Music is rate limiting requests", True)
        if "HTTP 404" in message:
            return WorkerError("NOT_FOUND", "Not found on YouTube Music")
        return WorkerError("UPSTREAM", message)
    if isinstance(exc, YTMusicUserError):
        if "authentication" in message.lower():
            return WorkerError("AUTH_REQUIRED", "Sign in to use your library")
        return WorkerError("BAD_REQUEST", message)
    try:
        import requests

        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return WorkerError("NETWORK", "Could not reach YouTube Music", True)
    except ImportError:
        pass
    if isinstance(exc, (KeyError, TypeError, IndexError, AttributeError, json.JSONDecodeError)):
        return WorkerError("UPSTREAM", "Unexpected response from YouTube Music (ytmusicapi may need an update)")
    return WorkerError("INTERNAL", f"{type(exc).__name__}: {message}")


def _succeeded(result):
    status = result.get("status") if isinstance(result, dict) else result
    return status == "STATUS_SUCCEEDED"


# ---- methods ------------------------------------------------------------------


def m_ping(worker, params):
    return {"pong": True}


def m_hello(worker, params):
    return {"auth": worker.auth, "account": auth.whoami()}


def m_reload_auth(worker, params):
    worker.load()
    worker.clear_cache()
    return m_hello(worker, params)


def m_configure(worker, params):
    """Store the shell's settings (sent first after every start).

    Each key belongs to the area module that validates it (SETTINGS); unknown
    keys are ignored and listed. A change to a key in CLIENT_SETTINGS rebuilds
    the client, which bumps the generation, and clears the cache unless it was
    made with the new settings (see Worker.configure).
    """
    accepted, ignored = {}, []
    for key, value in params.items():
        validate = SETTINGS.get(key)
        if validate is None:
            ignored.append(key)
        else:
            accepted[key] = validate(value)
    reloaded = worker.configure(accepted)
    return {"settings": dict(worker.settings), "ignored": sorted(ignored), "reloaded": reloaded}


def m_cache_thumbs(worker, params):
    urls = params.get("urls")
    if not isinstance(urls, list) or any(not isinstance(u, str) for u in urls):
        raise WorkerError("BAD_REQUEST", "'urls' must be a list of strings")
    thumbs = worker.thumbs()
    urls = urls[:200]
    have = thumbs.cached(urls)
    missing = [u for u in urls if u not in have and thumbs.allowed(u)]
    if missing and worker.emit is None:
        have.update(thumbs.fetch(missing))
        missing = []
    elif missing:
        # Downloads must not hold up searches queued behind this request; the
        # new paths follow as a {"event": "thumbs"} line.
        def download():
            got = thumbs.fetch(missing)
            if got:
                worker.emit({"event": "thumbs", "paths": got})

        threading.Thread(target=download, daemon=True).start()
    return {"paths": have, "pending": len(missing)}


def m_verify_auth(worker, params):
    worker.verify_session()
    return m_hello(worker, params)


def m_search(worker, params):
    query = req_str(params, "query")
    search_filter = choice(params, "filter", SEARCH_FILTERS)
    limit = opt_int(params, "limit", 20, high=100)
    if search_filter:
        results = worker.yt.search(query, filter=search_filter, limit=limit)
        return {"items": norm.normalize_list(results, norm.search_item, "search result")}
    return norm.search_grouped(worker.yt.search(query, limit=limit))


def m_suggestions(worker, params):
    """Completions for a half-typed search (with your own searches when signed in)."""
    query = req_str(params, "query")
    limit = opt_int(params, "limit", 8, high=20)
    items = []
    for entry in worker.yt.get_search_suggestions(query, detailed_runs=True) or []:
        text = entry.get("text") if isinstance(entry, dict) else entry
        if isinstance(text, str) and text.strip():
            items.append({"text": text.strip(), "fromHistory": bool(isinstance(entry, dict) and entry.get("fromHistory"))})
    return {"items": items[:limit]}


# S5: Home is homeFeed (ytm_home); this get_home reader stays as homeLegacy,
# the shape Home had before (no See all, Play all or chips).
def m_home(worker, params):
    return {"shelves": norm.home(worker.yt.get_home(limit=opt_int(params, "limit", 6, high=20)))}


# history, playlist, liked and album: ytm_collections (S3).


def m_song(worker, params):
    return norm.song(worker.yt.get_song(req_str(params, "videoId")))


def m_warm_streams(worker, params):
    worker.warm_resolver()
    return {"ok": True}


def m_resolve(worker, params):
    video_id = req_str(params, "videoId")
    account = params.get("account")
    if account is not None and not isinstance(account, bool):
        raise WorkerError("BAD_REQUEST", "'account' must be true or false")
    worker.wait_for_warm()
    # S17: the song's loudness too, while the shell levels by YouTube's.
    loudness = {"loudness_from": worker.yt} if worker.settings.get("normalize") == "youtube" else {}
    try:
        return worker.resolver().resolve(video_id, account=account, **loudness)
    except streams.StreamError as exc:
        raise WorkerError(exc.code, exc.message, exc.retryable) from exc


def m_rate(worker, params):
    worker.require_auth()
    from ytmusicapi.models.content.enums import LikeStatus

    video_id = req_str(params, "videoId")
    rating = choice(params, "rating", RATINGS)
    if rating is None:
        raise WorkerError("BAD_REQUEST", "missing 'rating'")
    worker.yt.rate_song(video_id, LikeStatus[rating])
    return {"videoId": video_id, "likeStatus": rating}


def m_create_playlist(worker, params):
    worker.require_auth()
    title = req_str(params, "title")
    description = params.get("description") or ""
    privacy = choice(params, "privacy", PRIVACY, "PRIVATE")
    video_ids = params.get("videoIds") or None
    result = worker.yt.create_playlist(title, description, privacy_status=privacy, video_ids=video_ids)
    if isinstance(result, str):
        return {"playlistId": result}
    raise WorkerError("UPSTREAM", "YouTube Music did not create the playlist")


def m_add_to_playlist(worker, params):
    worker.require_auth()
    playlist_id = req_str(params, "playlistId")
    video_ids = req_list(params, "videoIds")
    result = worker.yt.add_playlist_items(playlist_id, video_ids, duplicates=bool(params.get("duplicates")))
    if not _succeeded(result):
        raise WorkerError("UPSTREAM", "Not added (is it already in that playlist?)")
    added = result.get("playlistEditResults") if isinstance(result, dict) else None
    return {"added": [{"videoId": a.get("videoId"), "setVideoId": a.get("setVideoId")} for a in added or []]}


def m_remove_from_playlist(worker, params):
    worker.require_auth()
    playlist_id = req_str(params, "playlistId")
    items = req_list(params, "items")
    videos = []
    for item in items:
        if not isinstance(item, dict) or not item.get("videoId") or not item.get("setVideoId"):
            raise WorkerError("BAD_REQUEST", "each item needs videoId and setVideoId")
        videos.append({"videoId": item["videoId"], "setVideoId": item["setVideoId"]})
    if not _succeeded(worker.yt.remove_playlist_items(playlist_id, videos)):
        raise WorkerError("UPSTREAM", "YouTube Music did not remove the songs")
    return {"ok": True}


def m_delete_playlist(worker, params):
    worker.require_auth()
    result = worker.yt.delete_playlist(req_str(params, "playlistId"))
    # ytmusicapi returns the status string when YouTube sends one and the whole
    # response otherwise; a successful delete comes back without any status.
    failed = (isinstance(result, str) and result != "STATUS_SUCCEEDED") or (isinstance(result, dict) and "error" in result)
    if failed:
        raise WorkerError("UPSTREAM", "YouTube Music did not delete the playlist")
    return {"ok": True}


# ---- registry -------------------------------------------------------------------
# The core tables; area modules export the same names. Merged below into
# METHODS, CACHEABLE, LANES, INVALIDATES, RECORDERS, MUTATES, SETTINGS,
# CLIENT_SETTINGS and NO_INVALIDATES.
#
# CACHEABLE: responses kept in the disk cache. Requests pick a mode with
#   params.cache: "prefer" answers from disk when possible (reply has
#   cached/age), "refresh" always asks YouTube and stores the answer, "bypass"
#   (default) neither. A request with a cursor (a later page) is never cached.
# LANES: method -> lane (one of KNOWN_LANES).
# INVALIDATES: what an edit makes stale, as (method, srcKey, dstKey) triples:
#   cached `method` entries whose params[dstKey] equals this request's
#   params[srcKey] are dropped (srcKey None: every entry of `method`). srcKey
#   may also be a ytm_proto source (Fixed values, a Prefixed param) when the
#   cached key is not a request param as is.
# MUTATES: methods that change the account. The shell sends them with
#   retry:false (never re-sent after a worker exit) and refreshes on an
#   unknown result. Each one has INVALIDATES or a NO_INVALIDATES reason.
# SETTINGS: configure() keys -> validator; CLIENT_SETTINGS: keys that need a
#   new client.

CORE_METHODS = {
    "ping": m_ping,
    "hello": m_hello,
    "reloadAuth": m_reload_auth,
    "verifyAuth": m_verify_auth,
    "configure": m_configure,
    "search": m_search,
    "suggestions": m_suggestions,
    "homeLegacy": m_home,
    "song": m_song,
    "resolve": m_resolve,
    "warmStreams": m_warm_streams,
    "cacheThumbs": m_cache_thumbs,
    "rate": m_rate,
    "createPlaylist": m_create_playlist,
    "addToPlaylist": m_add_to_playlist,
    "removeFromPlaylist": m_remove_from_playlist,
    "deletePlaylist": m_delete_playlist,
}
CORE_CACHEABLE = {"homeLegacy"}
CORE_LANES = {"resolve": "streams", "suggestions": "suggest"}
# The browsePage pages the core edits change: a playlist's own page, the
# library's playlist pages, and the liked songs pages.
_PLAYLIST_PAGE = ("browsePage", Prefixed("VL", "playlistId"), "browseId")
_LIBRARY_PAGES = ("browsePage", Fixed("FEmusic_liked_playlists", "FEmusic_library_landing"), "browseId")
_LIKED_PAGES = ("browsePage", Fixed("VLLM", "FEmusic_liked_videos"), "browseId")
# S2 (ytm_library): the landing's recent activity shows playlists, and a
# rating can change the library's songs.
_LANDING = ("libraryLanding", None, None)
_PLAYLIST_EDIT = [("playlist", "playlistId", "playlistId"), ("libraryPlaylists", None, None), _PLAYLIST_PAGE,
                  _LIBRARY_PAGES, _LANDING]
CORE_INVALIDATES = {
    "rate": [("liked", None, None), _LIKED_PAGES, ("librarySongs", None, None)],
    "addToPlaylist": _PLAYLIST_EDIT,
    "removeFromPlaylist": _PLAYLIST_EDIT,
    "createPlaylist": [("libraryPlaylists", None, None), _LIBRARY_PAGES, _LANDING],
    "deletePlaylist": _PLAYLIST_EDIT,
}
CORE_MUTATES = {"rate", "createPlaylist", "addToPlaylist", "removeFromPlaylist", "deletePlaylist"}
CORE_RECORDERS = {
    "search": lambda yt, p: yt.search(p["query"], filter=p.get("filter"), limit=p.get("limit", 20)),
    "song": lambda yt, p: yt.get_song(p["videoId"]),
    "watch": lambda yt, p: yt.get_watch_playlist(videoId=p["videoId"], limit=p.get("limit", 25), radio=p.get("radio", False)),
    "home": lambda yt, p: yt.get_home(limit=p.get("limit", 3)),
}


class _Core:
    METHODS = CORE_METHODS
    CACHEABLE = CORE_CACHEABLE
    LANES = CORE_LANES
    INVALIDATES = CORE_INVALIDATES
    RECORDERS = CORE_RECORDERS
    MUTATES = CORE_MUTATES
    SETTINGS = streams.SETTINGS  # S17: normalize (resolve's loudness)


# Area modules, in a fixed order. A slice that rewrites a core method moves it
# out of the core tables into its module.
AREA_MODULES = (pages, play, now, artist, library, collections, actions, home, explore, history)


def merge_registries(sources):
    """The merged tables of ``sources``; raises ValueError on a duplicate or a stray name."""
    merged = {"METHODS": {}, "CACHEABLE": set(), "LANES": {}, "INVALIDATES": {}, "RECORDERS": {},
              "MUTATES": set(), "SETTINGS": {}, "CLIENT_SETTINGS": set(), "NO_INVALIDATES": {}}
    owner = {}
    for source in sources:
        label = getattr(source, "__name__", repr(source))
        methods = getattr(source, "METHODS", {})
        for kind, names in (("method", methods), ("recorder", getattr(source, "RECORDERS", {})),
                            ("setting", getattr(source, "SETTINGS", {}))):
            for name in names:
                if (kind, name) in owner:
                    raise ValueError(f"{kind} {name!r} is registered by {owner[(kind, name)]} and {label}")
                owner[(kind, name)] = label
        for table in ("CACHEABLE", "LANES", "INVALIDATES", "MUTATES", "NO_INVALIDATES"):
            stray = [name for name in getattr(source, table, ()) if name not in methods]
            if stray:
                raise ValueError(f"{label}.{table} names methods it does not register: {stray}")
        bad_lanes = {n: lane for n, lane in getattr(source, "LANES", {}).items() if lane not in KNOWN_LANES}
        if bad_lanes:
            raise ValueError(f"{label}.LANES uses unknown lanes: {bad_lanes}")
        merged["METHODS"].update(methods)
        merged["CACHEABLE"] |= set(getattr(source, "CACHEABLE", ()))
        merged["LANES"].update(getattr(source, "LANES", {}))
        merged["INVALIDATES"].update(getattr(source, "INVALIDATES", {}))
        merged["RECORDERS"].update(getattr(source, "RECORDERS", {}))
        merged["MUTATES"] |= set(getattr(source, "MUTATES", ()))
        merged["SETTINGS"].update(getattr(source, "SETTINGS", {}))
        merged["CLIENT_SETTINGS"] |= set(getattr(source, "CLIENT_SETTINGS", ()))
        merged["NO_INVALIDATES"].update(getattr(source, "NO_INVALIDATES", {}))
    for name, triples in merged["INVALIDATES"].items():
        for triple in triples:
            ok = len(triple) == 3 and triple[0] in merged["CACHEABLE"] and (
                (triple[1] is None and triple[2] is None)
                or ((isinstance(triple[1], str) or callable(triple[1])) and isinstance(triple[2], str)))
            if not ok:
                raise ValueError(f"INVALIDATES[{name!r}] entry {triple!r} is not (cacheable method, srcKey, dstKey)")
    return merged


def invalidation_matches(src, dst, params):
    """The cache matches one INVALIDATES triple drops for ``params``; ``[None]`` drops every entry."""
    if src is None:
        return [None]
    if callable(src):
        values = src(params)
        values = list(values) if isinstance(values, (list, tuple)) else [values]
    else:
        values = [params.get(src)]
    if not values or any(value is None for value in values):
        return [None]  # a missing source value drops every entry: too much beats stale
    return [{dst: value} for value in values]


_REGISTRY = merge_registries((_Core, *AREA_MODULES))
METHODS = _REGISTRY["METHODS"]
CACHEABLE = _REGISTRY["CACHEABLE"]
LANES = _REGISTRY["LANES"]
INVALIDATES = _REGISTRY["INVALIDATES"]
RECORDERS = _REGISTRY["RECORDERS"]
MUTATES = _REGISTRY["MUTATES"]
SETTINGS = _REGISTRY["SETTINGS"]
CLIENT_SETTINGS = _REGISTRY["CLIENT_SETTINGS"]
NO_INVALIDATES = _REGISTRY["NO_INVALIDATES"]


def describe(exc):
    """An exception for the log: type and first line, without the response data
    ytmusicapi's navigation errors append (it may hold tokens)."""
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    message = message.split(" on {", 1)[0].split(" on [", 1)[0]
    return f"{type(exc).__name__}: {message[:200]}"


def debug_enabled():
    return os.environ.get("YTM_DEBUG") == "1"


class Worker:
    def __init__(self, client=None, auth_state="none", resolver=None, cache=None, thumbs=None):
        self.verified = False
        self._lock = threading.Lock()
        self._resolver = resolver
        self._warm_thread = None
        self._cache = cache
        self._thumbs = thumbs
        # Bumped whenever the client is replaced (sign-in, sign-out, a setting
        # that needs a new client): a lane read that started before must not
        # cache what it got.
        self.generation = 0
        # What configure() last stored; keys belong to area modules (SETTINGS).
        self.settings = {}
        # The CLIENT_SETTINGS part of it that self.yt was built with.
        self.built_with = {}
        # Set by serve(): writes an unsolicited event line (thread-safe).
        self.emit = None
        if client is None:
            # Start with the client settings the disk cache was made with, so
            # the configure that follows every start finds nothing to change.
            self.settings = self.remembered_client_settings()
            self.load()
        else:
            self.yt, self.auth = client, auth_state

    def resolver(self):
        # Asked for from the streams lane and the main thread (warm-up).
        with self._lock:
            if self._resolver is None:
                self._resolver = streams.Resolver()
            return self._resolver

    def warm_resolver(self):
        """Load yt-dlp in the background (~0.3 s) while the user browses."""
        warm = getattr(self.resolver(), "warm", None)
        if warm is None or (self._warm_thread and self._warm_thread.is_alive()):
            return
        self._warm_thread = threading.Thread(target=warm, daemon=True)
        self._warm_thread.start()

    def wait_for_warm(self):
        if self._warm_thread:
            self._warm_thread.join()

    def cache(self):
        if self._cache is None:
            self._cache = caches.DiskCache()
        return self._cache

    def thumbs(self):
        if self._thumbs is None:
            self._thumbs = caches.ThumbCache()
        return self._thumbs

    def clear_cache(self):
        try:
            self.cache().clear()
        except OSError as exc:
            norm.log(f"could not clear the cache: {exc}")

    # ---- skip_store (S11) ----
    # Per thread: lanes handle requests beside the main thread. handle()
    # clears it when a request starts.
    _request_state = threading.local()

    def skip_store(self):
        """Keep the answer of the request this thread is handling out of the
        disk cache: a stand-in after a passing failure (plain lyrics when the
        synced ones could not be asked for), so the next request asks again."""
        self._request_state.no_store = True

    # ---- end skip_store (S11) ----

    def _store(self, name, params, result, guard):
        """Cache a result unless the account or the method's data changed since the
        request started (``guard`` = (generation, cache epoch) taken then)."""
        if guard is None or guard[0] != self.generation:
            return
        if getattr(self._request_state, "no_store", False):
            return  # skip_store() (S11)
        # A cache that can't be written must never fail the request itself.
        try:
            self.cache().put(name, params, result, epoch=guard[1])
        except OSError as exc:
            norm.log(f"cache update failed: {exc}")

    def invalidate(self, method, match=None):
        """Drop cached ``method`` entries whose params contain ``match`` (all when None).

        Also stops any read of ``method`` already under way from being stored.
        """
        try:
            self.cache().invalidate(method, match)
        except OSError as exc:
            norm.log(f"cache update failed: {exc}")

    def _invalidate(self, name, params):
        for method, src, dst in INVALIDATES.get(name, []):
            for match in invalidation_matches(src, dst, params):
                self.invalidate(method, match)

    def load(self):
        # A module that adds CLIENT_SETTINGS passes self.client_settings() here.
        yt, state = auth.load_client()
        raw.install(yt)
        self.yt, self.auth = yt, state
        self.built_with = self.client_settings()
        self.verified = False
        self.generation += 1

    def client_settings(self, settings=None):
        """The CLIENT_SETTINGS part of ``settings`` (default: the stored ones)."""
        settings = self.settings if settings is None else settings
        return {key: settings[key] for key in sorted(CLIENT_SETTINGS) if key in settings}

    def remembered_client_settings(self):
        """The client settings recorded with the disk cache, still valid ones only."""
        try:
            recorded = self.cache().client_settings()
        except Exception as exc:  # noqa: BLE001 - a broken record means defaults
            norm.log(f"cache read failed: {describe(exc)}")
            return {}
        out = {}
        for key, value in recorded.items():
            if key in CLIENT_SETTINGS and key in SETTINGS:
                try:
                    out[key] = SETTINGS[key](value)
                except Exception:  # noqa: BLE001 - no longer valid: left out
                    continue
        return out

    def configure(self, settings):
        """Store ``settings`` (already validated); True when the client was rebuilt.

        The client is rebuilt only when its settings differ from the ones it
        was built with, and the disk cache is cleared only when they differ
        from the ones it was made with (recorded beside it). A start that
        repeats the last run's settings keeps both.
        """
        self.settings = dict(settings)
        wanted = self.client_settings()
        rebuilt = wanted != self.built_with
        if rebuilt:
            self.load()
        try:
            if self.cache().client_settings() != wanted:
                self.clear_cache()
                self.cache().record_client_settings(wanted)
        except OSError as exc:
            norm.log(f"cache update failed: {exc}")
        return rebuilt

    def verify_session(self):
        """Check once per worker that YouTube still accepts the saved session.

        An expired session doesn't fail cleanly: the library comes back empty
        and other calls fail to parse. The account lookup fails fast (~0.1 s).
        """
        if self.auth != "ok" or self.verified:
            return
        try:
            self.yt.get_account_info()
        except Exception as exc:  # noqa: BLE001
            error = classify(exc)
            if error.retryable:
                raise error from exc  # offline or rate limited: can't tell, don't mark expired
            self.auth = "expired"
            return
        self.verified = True

    def require_auth(self):
        self.verify_session()
        if self.auth == "expired":
            raise WorkerError("AUTH_EXPIRED", "YouTube Music no longer accepts the saved session; sign in again")
        if self.auth != "ok":
            raise WorkerError("AUTH_REQUIRED", "Sign in to use your library")

    def cached_reply(self, request, started=None):
        """The disk-cache answer to a ``cache: "prefer"`` request, or None.

        serve() asks this on the main thread before handing a request to a lane,
        so a cached page never waits behind a long read.
        """
        if not isinstance(request, dict) or request.get("method") not in CACHEABLE:
            return None
        params = request.get("params") or {}
        if not isinstance(params, dict) or params.get("cache") != "prefer" or params.get("cursor"):
            return None
        params = {k: v for k, v in params.items() if k != "cache"}
        try:
            hit = self.cache().get(request["method"], params)
        except Exception as exc:  # noqa: BLE001 - a broken cache is a miss
            norm.log(f"cache read failed: {describe(exc)}")
            return None
        if hit is None:
            return None
        started = time.monotonic() if started is None else started
        reply = {"id": request.get("id"), "ok": True, "result": hit[0], "cached": True, "age": hit[1],
                 "ms": round((time.monotonic() - started) * 1000), "http": 0}
        self._debug(request["method"], reply)
        return reply

    def handle(self, request):
        request_id = request.get("id") if isinstance(request, dict) else None
        started = time.monotonic()
        raw.reset_http()
        self._request_state.no_store = False  # skip_store (S11)
        name = None
        try:
            if not isinstance(request, dict):
                raise WorkerError("BAD_REQUEST", "request must be a JSON object")
            method = METHODS.get(request.get("method"))
            if method is None:
                raise WorkerError("BAD_REQUEST", f"unknown method {request.get('method')!r}")
            name = request.get("method")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise WorkerError("BAD_REQUEST", "'params' must be an object")
            mode = "bypass"
            guard = None
            if name in CACHEABLE:
                params = dict(params)
                mode = params.pop("cache", "bypass")
                if mode not in CACHE_MODES:
                    raise WorkerError("BAD_REQUEST", f"'cache' must be one of {', '.join(CACHE_MODES)}")
                if params.get("cursor"):
                    mode = "bypass"  # later pages are never cached: the cursor is a token
                if mode == "prefer":
                    hit = self.cached_reply({"id": request_id, "method": name, "params": dict(params, cache=mode)},
                                            started)
                    if hit is not None:
                        return hit
                if mode != "bypass":
                    guard = (self.generation, self.cache().epoch(name))
            try:
                result = method(self, params)
            except Exception as exc:
                # An edit that may have reached YouTube still makes the cache stale
                # (S4: not one that failed before it was sent, ytm_proto.unsent).
                if name in MUTATES and not getattr(exc, "unsent", False) \
                        and classify(exc).code not in ("BAD_REQUEST", "AUTH_REQUIRED", "AUTH_EXPIRED"):
                    self._invalidate(name, params)
                raise
            self._store(name, params, result, guard)
            self._invalidate(name, params)
            reply = {"id": request_id, "ok": True, "result": result}
            if name in CACHEABLE:
                reply["cached"] = False
        except Exception as exc:  # noqa: BLE001 - every failure becomes a protocol error
            error = classify(exc)
            if error.code == "AUTH_EXPIRED":
                self.auth = "expired"
            if error.code in ("INTERNAL", "UPSTREAM"):
                norm.log(f"{name or '?'} failed: {describe(exc)}")
            reply = {"id": request_id, "ok": False, "error": error.as_dict()}
        reply["ms"] = round((time.monotonic() - started) * 1000)
        reply["http"] = raw.http_count()
        self._debug(name, reply)
        return reply

    @staticmethod
    def _debug(name, reply):
        # Method, outcome, request count and time only: never params or results.
        if debug_enabled():
            outcome = "ok" if reply.get("ok") else (reply.get("error") or {}).get("code", "?")
            norm.log(f"{name if name in METHODS else '?'} {outcome} http={reply.get('http', 0)} "
                     f"ms={reply.get('ms', 0)}{' cached' if reply.get('cached') else ''}")


def ytmusicapi_version():
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ytmusicapi")
    except PackageNotFoundError:
        return None


# ---- serve ------------------------------------------------------------------


def serve(idle_secs, worker=None, stdin_fd=None, out=None):
    """Answer JSON-lines requests until stdin closes or the idle timeout hits.

    The idle timeout counts from the last request or the last lane reply, and
    never fires while a lane is still working.
    """
    if out is None:
        out = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
        # Anything else that prints must never corrupt the protocol stream.
        sys.stdout = sys.stderr
    lock = threading.Lock()

    def emit(message):
        # Background downloads report through here too, hence the lock.
        with lock:
            out.write(json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n")
            out.flush()

    worker = worker or Worker()
    worker.emit = emit
    lanes = {}
    state = {"busy": 0, "last": time.monotonic()}
    state_lock = threading.Lock()

    def submit(request):
        name = LANES[request["method"]]
        jobs = lanes.get(name)
        if jobs is None:
            jobs = lanes[name] = queue.Queue()

            def run():
                while True:
                    job = jobs.get()
                    try:
                        emit(worker.handle(job))
                    finally:
                        with state_lock:
                            state["busy"] -= 1
                            state["last"] = time.monotonic()

            # Daemon: an unfinished lookup must not keep the process alive
            # after stdin closes.
            threading.Thread(target=run, name=f"lane-{name}", daemon=True).start()
        with state_lock:
            state["busy"] += 1
        jobs.put(request)

    emit({"event": "ready", "protocol": PROTOCOL, "version": VERSION,
          "ytmusicapi": ytmusicapi_version(), "auth": worker.auth, "mutates": sorted(MUTATES)})
    fd = sys.stdin.fileno() if stdin_fd is None else stdin_fd
    buffer = b""
    while True:
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except ValueError:
                emit({"id": None, "ok": False, "ms": 0, "http": 0,
                      "error": WorkerError("BAD_REQUEST", "request is not valid JSON").as_dict()})
                continue
            if isinstance(request, dict) and request.get("method") == "shutdown":
                emit({"id": request.get("id"), "ok": True, "result": {}, "ms": 0, "http": 0})
                return 0
            if isinstance(request, dict) and request.get("method") in LANES:
                hit = worker.cached_reply(request)
                if hit is not None:
                    emit(hit)
                else:
                    submit(request)
                continue
            emit(worker.handle(request))
        timeout = None
        if idle_secs > 0:
            with state_lock:
                busy, idle_for = state["busy"], time.monotonic() - state["last"]
            # While a lane works, look again every second; otherwise wake when
            # the idle time would be up.
            timeout = min(1.0, idle_secs) if busy else max(0.0, idle_secs - idle_for)
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            with state_lock:
                busy, idle_for = state["busy"], time.monotonic() - state["last"]
            if busy or idle_for < idle_secs:
                continue
            emit({"event": "idle-exit"})
            return 0
        chunk = os.read(fd, 65536)
        if not chunk:
            return 0  # stdin closed: the shell side is gone
        with state_lock:
            state["last"] = time.monotonic()
        buffer += chunk


# ---- CLI --------------------------------------------------------------------


def print_json(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def notify_shell():
    """Tell a running shell to re-read the session; harmless if none is running."""
    try:
        subprocess.run(["omarchy-shell", "-q", "ytmusic", "reloadAuth"], timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def cmd_auth(args):
    try:
        if args.paste:
            print("Paste the request headers of a music.youtube.com /browse request, then press Ctrl-D:",
                  file=sys.stderr)
            account = auth.import_from_headers(sys.stdin.read())
        elif args.clipboard:
            raw = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, text=True, check=False).stdout
            subprocess.run(["wl-copy", "--clear"], check=False)
            print("Clipboard cleared. Omarchy's clipboard history still holds a copy: delete it there.",
                  file=sys.stderr)
            account = auth.import_from_headers(raw)
        else:
            browser, _, profile = (args.from_browser or "chromium").partition(":")
            account = auth.import_from_browser(browser, profile or None, args.authuser, args.keyring)
    except auth.AuthError as exc:
        print(f"Sign-in failed: {exc}", file=sys.stderr)
        return 1
    name = account.get("name") or "your account"
    handle = f" ({account['handle']})" if account.get("handle") else ""
    caches.DiskCache().clear()  # cached library data belongs to the previous session
    print(f"Signed in to YouTube Music as {name}{handle}.")
    notify_shell()
    return 0


def cmd_selftest(args):
    checks = []

    def check(label, fn):
        try:
            detail = fn()
            checks.append((True, label, detail or ""))
        except Exception as exc:  # noqa: BLE001
            checks.append((False, label, f"{type(exc).__name__}: {exc}"))

    check("ytmusicapi import", lambda: f"ytmusicapi {ytmusicapi_version()}")
    check("normalizer", lambda: norm.track({"videoId": "x", "title": "t", "length": "1:05",
                                          "artists": [{"name": "a", "id": None}]})["durationSec"] == 65 or 1 / 0)

    class Offline:
        pass

    check("dispatcher", lambda: Worker(Offline(), "none").handle({"id": 1, "method": "ping"})["ok"] or 1 / 0)

    def permissions():
        directory = auth.config_dir()
        if not directory.exists():
            return "no session saved"
        bad = [p.name for p in directory.iterdir() if p.is_file() and p.stat().st_mode & 0o077]
        if directory.stat().st_mode & 0o077 or bad:
            raise PermissionError(f"too open: {directory} {bad}")
        return "0700/0600"

    check("session file permissions", permissions)

    def tools():
        import shutil

        missing = [tool for tool in ("mpv", "yt-dlp", "deno") if shutil.which(tool) is None]
        if not any(os.path.exists(p) for p in ("/etc/mpv/scripts/mpris.so", "/usr/lib/mpv-mpris/mpris.so")):
            missing.append("mpv-mpris")
        if missing:
            raise RuntimeError(f"missing {', '.join(missing)}; install with: omarchy-pkg-add {' '.join(missing)}")
        return "mpv, mpv-mpris, yt-dlp, deno"

    check("tools", tools)
    if args.online:
        worker = Worker()
        check("online search", lambda: f"{len(worker.handle({'method': 'search', 'params': {'query': 'daft punk', 'filter': 'songs', 'limit': 1}})['result']['items'])} result(s)")
        if worker.auth == "ok":
            def library():
                reply = worker.handle({"method": "libraryPlaylists", "params": {"limit": 1}})
                if not reply["ok"]:
                    raise RuntimeError(reply["error"]["code"])
                return "signed in"

            check("library (signed in)", library)
    for ok, label, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {label}{': ' + str(detail) if detail not in ('', True) else ''}")
    return 0 if all(ok for ok, _, _ in checks) else 1


SCRUB_KEYS = ("streamingData", "playbackTracking", "playerConfig", "microformat", "responseContext", "trackingParams",
              "clickTrackingParams")
# Any key containing one of these (case-insensitive) is dropped as well.
SCRUB_PARTS = ("token", "continuation", "cursor", "jct", "feedback", "onbehalfofuser")


def scrub(value):
    """Drop tokens and stream URLs before a response becomes a test fixture."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()
                if k not in SCRUB_KEYS and not any(part in k.lower() for part in SCRUB_PARTS)}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def cmd_record(args):
    # Always signed out: a fixture must never hold the account's library,
    # likes or subscriptions. The saved session is not even loaded.
    yt = auth.anonymous_client()
    data = RECORDERS[args.method](yt, json.loads(args.params))
    with open(args.out, "w") as f:
        json.dump(scrub(data), f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ytm", description="YouTube Music backend for damir.ytmusic")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve", help="JSON-lines worker for the shell")
    p.add_argument("--idle", type=float, default=180, help="exit after this many idle seconds (0 = never)")
    p = sub.add_parser("search", help="search YouTube Music")
    p.add_argument("query", nargs="+")
    p.add_argument("--filter", choices=SEARCH_FILTERS)
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("call", help="call any worker method")
    p.add_argument("method", choices=sorted(METHODS))
    p.add_argument("params", nargs="?", default="{}", help="JSON object")
    p.add_argument("--reply", action="store_true", help="print the whole reply (cached, age, http, ms)")
    p = sub.add_parser("auth", help="sign in (default: import from a Chromium profile)")
    source = p.add_mutually_exclusive_group()
    source.add_argument("--from-browser", metavar="BROWSER[:PROFILE]", help="default chromium, auto-detected profile")
    source.add_argument("--paste", action="store_true", help="read request headers from stdin")
    source.add_argument("--clipboard", action="store_true", help="read request headers from the clipboard")
    p.add_argument("--authuser", type=int, default=0, help="Google account index when signed in to several")
    p.add_argument("--keyring", default="GNOMEKEYRING", help="keyring holding the browser's cookie key")
    sub.add_parser("logout", help="delete the saved session")
    sub.add_parser("whoami", help="show the signed-in account")
    p = sub.add_parser("selftest", help="check the installation")
    p.add_argument("--online", action="store_true", help="also make read-only network calls")
    p = sub.add_parser("record", help="save a raw, scrubbed, signed-out ytmusicapi response (test fixtures)")
    p.add_argument("method", choices=sorted(RECORDERS))
    p.add_argument("params", help="JSON object")
    p.add_argument("out")
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        return serve(args.idle)
    if args.cmd in ("search", "call"):
        if args.cmd == "search":
            request = {"id": 1, "method": "search",
                       "params": {"query": " ".join(args.query), "filter": args.filter, "limit": args.limit}}
        else:
            try:
                params = json.loads(args.params)
            except ValueError:
                parser.error("params must be a JSON object")
            request = {"id": 1, "method": args.method, "params": params}
        reply = Worker().handle(request)
        if getattr(args, "reply", False):
            print_json(reply)
        else:
            print_json(reply["result"] if reply["ok"] else {"error": reply["error"]})
        return 0 if reply["ok"] else 1
    if args.cmd == "auth":
        return cmd_auth(args)
    if args.cmd == "logout":
        removed = auth.logout()
        caches.DiskCache().clear()
        print(f"Signed out ({', '.join(removed) if removed else 'no session was saved'}).")
        notify_shell()
        return 0
    if args.cmd == "whoami":
        print_json(auth.whoami() or {"signedIn": False})
        return 0
    if args.cmd == "selftest":
        return cmd_selftest(args)
    if args.cmd == "record":
        return cmd_record(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
