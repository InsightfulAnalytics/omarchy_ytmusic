"""On-disk caches for the worker, outside the plugin directory.

- DiskCache: recent library / playlist / album / artist / home / history
  responses, so the panel can show them instantly and refresh them in the
  background (stale-while-revalidate). Cleared whenever the account or a
  client setting changes; the client settings it was made with are recorded
  beside it (``client-settings``). File names start with the schema
  (``v2-``), so entries in an older shape are never read. Continuation
  cursors and feedback tokens (TOKEN_KEYS) never reach disk.
- ThumbCache: cover thumbnails as files, so covers appear instantly without
  the shell keeping decoded images in memory.

Both live in ~/.cache/omarchy-ytmusic (override: YTM_CACHE_DIR), 0700 / 0600.
"""

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

THUMB_HOSTS = ("googleusercontent.com", "ggpht.com", "ytimg.com")


def cache_root():
    return Path(os.environ.get("YTM_CACHE_DIR") or Path.home() / ".cache/omarchy-ytmusic")


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def write_private(path, data):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def strip_cursors(result):
    """A copy of ``result`` safe to store: no continuation cursor.

    A page that had more keeps ``hasMore: true`` with ``cursor: None``, so the
    shell knows to refresh it before loading more.
    """
    if not isinstance(result, dict):
        return result
    out = dict(result)
    if out.get("cursor") is not None:
        out["cursor"] = None
        out["hasMore"] = True
    if isinstance(out.get("sections"), list):
        out["sections"] = [strip_cursors(s) for s in out["sections"]]
    return out


# Feedback tokens a track carries (S3: ytm_normalize.track); account-bound,
# so a copy on disk drops them and the fresh read that follows brings them.
TOKEN_KEYS = ("libraryTokens", "listenAgainTokens", "historyToken")


def strip_tokens(value):
    """A copy of ``value`` without TOKEN_KEYS at any depth (the same object
    when it holds none)."""
    if isinstance(value, list):
        out = [strip_tokens(v) for v in value]
        return value if all(a is b for a, b in zip(out, value)) else out
    if not isinstance(value, dict):
        return value
    changed = any(k in value for k in TOKEN_KEYS)
    out = {}
    for k, v in value.items():
        if k in TOKEN_KEYS:
            continue
        stripped = strip_tokens(v)
        changed = changed or stripped is not v
        out[k] = stripped
    return out if changed else value


class DiskCache:
    SCHEMA = 2
    MAX_AGE = 7 * 24 * 3600
    MAX_FILES = 400
    # Not a .json name: clear() and pruning leave it alone.
    SETTINGS_FILE = "client-settings"

    def __init__(self, root=None):
        self.dir = Path(root) if root else cache_root() / "data"
        self.prefix = f"v{self.SCHEMA}-"
        # Writes and invalidations are serialized, and every invalidation bumps
        # the method's epoch: a read that started before an edit (or before
        # the cache was cleared) passes its epoch to put(), which then refuses
        # to store the stale result.
        self._lock = threading.Lock()
        self._epochs = {}
        self._clears = 0

    def _ensure_dir(self):
        # The parent (~/.cache/omarchy-ytmusic) also holds session.json, so it
        # must be private too, not just this subdirectory.
        private_dir(self.dir.parent)
        private_dir(self.dir)

    def key(self, method, params):
        canonical = json.dumps(params or {}, sort_keys=True, separators=(",", ":"))
        return f"{self.prefix}{method}-{hashlib.sha1(canonical.encode()).hexdigest()[:20]}.json"

    def epoch(self, method):
        with self._lock:
            return self._clears, self._epochs.get(method, 0)

    def get(self, method, params, now=None):
        """``(result, age_seconds)`` or ``None``."""
        try:
            entry = json.loads((self.dir / self.key(method, params)).read_text())
        except (OSError, ValueError):
            return None
        age = (now if now is not None else time.time()) - entry.get("at", 0)
        if age < 0 or age > self.MAX_AGE or "result" not in entry:
            return None
        return entry["result"], int(age)

    def put(self, method, params, result, now=None, epoch=None):
        """Store ``result``; skipped (returns False) when ``epoch`` is no longer current."""
        stored_params = {k: v for k, v in (params or {}).items() if k != "cursor"}
        entry = {"method": method, "params": stored_params, "at": now if now is not None else time.time(),
                 "result": strip_cursors(strip_tokens(result))}
        data = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode()
        with self._lock:
            if epoch is not None and epoch != (self._clears, self._epochs.get(method, 0)):
                return False
            self._ensure_dir()
            write_private(self.dir / self.key(method, params), data)
            self._prune()
        return True

    def invalidate(self, method, match=None):
        with self._lock:
            self._epochs[method] = self._epochs.get(method, 0) + 1
            for path in self.dir.glob(f"{self.prefix}{method}-*.json"):
                if match:
                    try:
                        params = json.loads(path.read_text()).get("params", {})
                    except (OSError, ValueError):
                        params = {}
                    if any(params.get(k) != v for k, v in match.items()):
                        continue
                path.unlink(missing_ok=True)

    def clear(self):
        with self._lock:
            self._clears += 1
            for path in self.dir.glob("*.json"):
                path.unlink(missing_ok=True)

    def client_settings(self):
        """The client settings the cached responses were made with (``{}``: defaults)."""
        try:
            value = json.loads((self.dir / self.SETTINGS_FILE).read_text())
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def record_client_settings(self, settings):
        with self._lock:
            path = self.dir / self.SETTINGS_FILE
            if not settings:
                path.unlink(missing_ok=True)
                return
            self._ensure_dir()
            write_private(path, json.dumps(settings, sort_keys=True, separators=(",", ":")).encode())

    def _prune(self):
        files = []
        for path in self.dir.glob("*.json"):
            if not path.name.startswith(self.prefix):
                path.unlink(missing_ok=True)  # an older schema: never read again
                continue
            try:
                files.append((path.stat().st_mtime, path))
            except OSError:
                continue
        files.sort()
        for _, path in files[:-self.MAX_FILES]:
            path.unlink(missing_ok=True)


def _http_get(url):
    import requests

    response = requests.get(url, timeout=6)
    if response.status_code != 200 or not response.headers.get("content-type", "").startswith("image/"):
        return None
    if len(response.content) > 2_000_000:
        return None
    return response.content


class ThumbCache:
    MAX_BYTES = 40 * 1024 * 1024

    def __init__(self, root=None, downloader=None, workers=6):
        self.dir = Path(root) if root else cache_root() / "thumbs"
        self._download = downloader or _http_get
        self._workers = workers
        self._lock = threading.Lock()

    @staticmethod
    def allowed(url):
        try:
            parsed = urlparse(url)
        except (TypeError, ValueError):
            return False
        host = parsed.hostname or ""
        return parsed.scheme == "https" and any(host == h or host.endswith("." + h) for h in THUMB_HOSTS)

    def path_for(self, url):
        return self.dir / (hashlib.sha1(url.encode()).hexdigest() + ".img")

    def cached(self, urls):
        """``{url: path}`` for the ones already on disk."""
        found = {}
        for url in urls:
            if self.allowed(url):
                path = self.path_for(url)
                if path.exists():
                    found[url] = str(path)
        return found

    def fetch(self, urls):
        """Download the missing ones; ``{url: path}`` for everything now on disk."""
        wanted = [u for u in dict.fromkeys(urls) if self.allowed(u)]
        have = self.cached(wanted)
        missing = [u for u in wanted if u not in have]
        if missing:
            private_dir(self.dir.parent)
            private_dir(self.dir)
            with ThreadPoolExecutor(max_workers=self._workers) as pool:
                for url, data in zip(missing, pool.map(self._safe_download, missing)):
                    if data:
                        path = self.path_for(url)
                        write_private(path, data)
                        have[url] = str(path)
            self._prune()
        return have

    def _safe_download(self, url):
        try:
            return self._download(url)
        except Exception:  # noqa: BLE001 - a missing cover is not an error
            return None

    def _prune(self):
        with self._lock:
            files = sorted(self.dir.glob("*.img"), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            for path in files:
                if total <= self.MAX_BYTES:
                    break
                total -= path.stat().st_size
                path.unlink(missing_ok=True)
