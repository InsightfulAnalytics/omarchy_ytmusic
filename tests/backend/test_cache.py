import json
import stat
import time

import pytest
import requests

import ytm
import ytm_cache as caches


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def cache(tmp_path):
    return caches.DiskCache(tmp_path / "data")


def test_put_get_age_and_private_files(cache):
    cache.put("playlist", {"playlistId": "P1", "limit": 200}, {"title": "Mine"}, now=1000)
    assert cache.get("playlist", {"limit": 200, "playlistId": "P1"}, now=1060) == ({"title": "Mine"}, 60)
    assert cache.get("playlist", {"playlistId": "P2", "limit": 200}, now=1060) is None
    assert cache.get("playlist", {"playlistId": "P1", "limit": 200}, now=1000 + caches.DiskCache.MAX_AGE + 1) is None
    assert mode(cache.dir) == 0o700
    assert mode(cache.dir.parent) == 0o700, "the cache root holds session.json too"
    assert all(mode(p) == 0o600 for p in cache.dir.iterdir())


def test_invalidate_by_method_and_param(cache):
    cache.put("playlist", {"playlistId": "P1"}, 1)
    cache.put("playlist", {"playlistId": "P2"}, 2)
    cache.put("liked", {}, 3)
    cache.invalidate("playlist", {"playlistId": "P1"})
    assert cache.get("playlist", {"playlistId": "P1"}) is None
    assert cache.get("playlist", {"playlistId": "P2"}) is not None
    cache.invalidate("liked")
    assert cache.get("liked", {}) is None
    cache.clear()
    assert list(cache.dir.glob("*.json")) == []
    caches.DiskCache(cache.dir / "missing").invalidate("liked")  # no directory: nothing to do


def test_corrupt_entries_are_misses(cache):
    cache.put("liked", {}, {"tracks": []})
    path = cache.dir / cache.key("liked", {})
    path.write_text('{"at": 1, "result": ')  # cut off mid-write
    assert cache.get("liked", {}) is None
    worker = ytm.Worker(Upstream(), "ok", cache=cache)
    reply = request(worker, "playlist", playlistId="P", cache="prefer")
    assert reply["ok"] and reply["cached"] is False, "a broken cache never breaks the request"


def test_prune_keeps_the_newest(cache, monkeypatch):
    monkeypatch.setattr(caches.DiskCache, "MAX_FILES", 3)
    for i in range(5):
        cache.put("album", {"browseId": str(i)}, i)
        time.sleep(0.01)
    assert len(list(cache.dir.glob("*.json"))) == 3
    assert cache.get("album", {"browseId": "4"}) is not None
    assert cache.get("album", {"browseId": "0"}) is None


class Upstream:
    def __init__(self):
        self.calls = 0

    def get_playlist(self, playlist_id, limit=200):
        self.calls += 1
        return {"id": playlist_id, "title": f"v{self.calls}", "owned": True, "trackCount": 0, "tracks": []}

    def add_playlist_items(self, playlist_id, video_ids, duplicates=False):
        return {"status": "STATUS_SUCCEEDED", "playlistEditResults": []}

    def get_account_info(self):
        return {"accountName": "x"}


def request(worker, method, **params):
    return worker.handle({"id": 1, "method": method, "params": params})


def test_worker_cache_modes(cache):
    upstream = Upstream()
    worker = ytm.Worker(upstream, "ok", cache=cache)
    first = request(worker, "playlist", playlistId="P", cache="prefer")
    assert first["ok"] and first["cached"] is False and upstream.calls == 1
    again = request(worker, "playlist", playlistId="P", cache="prefer")
    assert again["cached"] is True and again["result"]["title"] == "v1" and upstream.calls == 1
    assert "age" in again
    fresh = request(worker, "playlist", playlistId="P", cache="refresh")
    assert fresh["cached"] is False and fresh["result"]["title"] == "v2" and upstream.calls == 2
    assert request(worker, "playlist", playlistId="P", cache="prefer")["result"]["title"] == "v2"
    plain = request(worker, "playlist", playlistId="P")
    assert plain["result"]["title"] == "v3", "bypass (default) always asks YouTube"
    assert request(worker, "playlist", playlistId="P", cache="sometimes")["error"]["code"] == "BAD_REQUEST"


def test_edits_invalidate_the_cache(cache):
    worker = ytm.Worker(Upstream(), "ok", cache=cache)
    request(worker, "playlist", playlistId="P", cache="prefer")
    cache.put("libraryPlaylists", {"limit": 200}, {"items": []})
    assert request(worker, "addToPlaylist", playlistId="P", videoIds=["v"])["ok"]
    assert cache.get("playlist", {"playlistId": "P"}) is None
    assert cache.get("libraryPlaylists", {"limit": 200}) is None


def test_reload_auth_clears_the_cache(cache, config_dir, monkeypatch):
    cache.put("liked", {}, {"tracks": []})
    worker = ytm.Worker(Upstream(), "ok", cache=cache)
    monkeypatch.setattr(worker, "load", lambda: None)
    assert request(worker, "reloadAuth")["ok"]
    assert cache.get("liked", {}) is None


# ---- thumbnails ----------------------------------------------------------------


def test_thumb_hosts_are_restricted():
    assert caches.ThumbCache.allowed("https://lh3.googleusercontent.com/a=w120")
    assert caches.ThumbCache.allowed("https://i.ytimg.com/vi/x/hqdefault.jpg")
    assert caches.ThumbCache.allowed("https://yt3.ggpht.com/x=s88")
    assert not caches.ThumbCache.allowed("http://lh3.googleusercontent.com/a")
    assert not caches.ThumbCache.allowed("https://evil.example.com/googleusercontent.com.jpg")
    assert not caches.ThumbCache.allowed("file:///etc/passwd")


def test_thumb_fetch_downloads_once_and_prunes(tmp_path, monkeypatch):
    downloads = []

    def downloader(url):
        downloads.append(url)
        return None if "broken" in url else b"x" * 100

    thumbs = caches.ThumbCache(tmp_path / "thumbs", downloader=downloader)
    urls = ["https://i.ytimg.com/a.jpg", "https://i.ytimg.com/broken.jpg", "https://example.com/x.jpg"]
    got = thumbs.fetch(urls)
    assert set(got) == {"https://i.ytimg.com/a.jpg"}
    assert mode(tmp_path / "thumbs") == 0o700
    assert thumbs.fetch(urls) == got
    assert downloads.count("https://i.ytimg.com/a.jpg") == 1, "cached files are not downloaded again"
    monkeypatch.setattr(caches.ThumbCache, "MAX_BYTES", 150)
    thumbs.fetch(["https://i.ytimg.com/b.jpg"])
    assert len(list((tmp_path / "thumbs").glob("*.img"))) == 1


def test_cache_thumbs_method_sync_and_background(tmp_path):
    thumbs = caches.ThumbCache(tmp_path / "thumbs", downloader=lambda url: b"img")
    worker = ytm.Worker(Upstream(), "ok", thumbs=thumbs)
    reply = request(worker, "cacheThumbs", urls=["https://i.ytimg.com/a.jpg"])
    assert list(reply["result"]["paths"]) == ["https://i.ytimg.com/a.jpg"] and reply["result"]["pending"] == 0
    events = []
    worker.emit = events.append
    reply = request(worker, "cacheThumbs", urls=["https://i.ytimg.com/a.jpg", "https://i.ytimg.com/c.jpg"])
    assert list(reply["result"]["paths"]) == ["https://i.ytimg.com/a.jpg"]
    assert reply["result"]["pending"] == 1
    for _ in range(100):
        if events:
            break
        time.sleep(0.01)
    assert events and list(events[0]["paths"]) == ["https://i.ytimg.com/c.jpg"]
    assert request(worker, "cacheThumbs", urls="nope")["error"]["code"] == "BAD_REQUEST"


def test_serve_reports_background_thumbs_as_events(config_dir, tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    from conftest import ROOT

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", YTM_CONFIG_DIR=str(config_dir),
               YTM_CACHE_DIR=str(tmp_path / "cache"))
    proc = subprocess.Popen([sys.executable, str(ROOT / "backend" / "ytm.py"), "serve", "--idle", "10"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    assert json.loads(proc.stdout.readline())["event"] == "ready"
    proc.stdin.write('{"id":1,"method":"cacheThumbs","params":{"urls":["https://example.com/not-allowed.jpg"]}}\n')
    proc.stdin.flush()
    reply = json.loads(proc.stdout.readline())
    assert reply["ok"] and reply["result"] == {"paths": {}, "pending": 0}
    proc.stdin.close()
    assert proc.wait(timeout=5) == 0


# ---- schema, cursors, guards (protocol v2) -------------------------------------------


def test_schema_prefix_ignores_old_files_and_invalidate_matches_only_new(cache):
    cache.put("playlist", {"playlistId": "P1"}, {"title": "new"})
    assert all(p.name.startswith("v2-") for p in cache.dir.glob("*.json"))
    old = cache.dir / "playlist-0123456789abcdef0123.json"
    old.write_text(json.dumps({"method": "playlist", "params": {"playlistId": "P1"}, "at": time.time(),
                               "result": {"title": "old"}}))
    other = cache.dir / "v2-playlistExtra-0123456789abcdef0123.json"
    other.write_text("{}")
    assert cache.get("playlist", {"playlistId": "P1"})[0] == {"title": "new"}
    cache.invalidate("playlist", {"playlistId": "P1"})
    assert old.exists(), "invalidate's glob only matches current-schema files"
    assert other.exists(), "and only the named method"
    cache.put("liked", {}, {})
    assert not old.exists(), "pruning drops files of an older schema"


def test_max_files():
    assert caches.DiskCache.MAX_FILES == 400


def test_put_strips_cursors(cache):
    page = {"type": "page", "cursor": "c.SECRET", "hasMore": True,
            "sections": [{"type": "section", "items": [], "cursor": "c.INNER"}]}
    cache.put("browsePage", {"browseId": "FEx", "cursor": "c.PARAM"}, page)
    text = "".join(p.read_text() for p in cache.dir.glob("*.json"))
    assert "SECRET" not in text and "INNER" not in text and "PARAM" not in text
    stored = cache.get("browsePage", {"browseId": "FEx", "cursor": "c.PARAM"})[0]
    assert stored["cursor"] is None and stored["hasMore"] is True
    cache.put("browsePage", {"browseId": "FEy"}, {"cursor": None, "hasMore": False})
    assert cache.get("browsePage", {"browseId": "FEy"})[0] == {"cursor": None, "hasMore": False}


def test_invalidation_triples_map_source_to_destination_keys(cache, monkeypatch):
    monkeypatch.setitem(ytm.INVALIDATES, "subscribe", [("artist", "pageChannelId", "channelId")])
    monkeypatch.setitem(ytm.METHODS, "subscribe", lambda worker, params: {"ok": True})
    cache.put("artist", {"channelId": "UC1"}, {"name": "one"})
    cache.put("artist", {"channelId": "UC2"}, {"name": "two"})
    worker = ytm.Worker(Upstream(), "ok", cache=cache)
    assert request(worker, "subscribe", pageChannelId="UC1")["ok"]
    assert cache.get("artist", {"channelId": "UC1"}) is None
    assert cache.get("artist", {"channelId": "UC2"}) is not None
    assert request(worker, "subscribe")["ok"]
    assert cache.get("artist", {"channelId": "UC2"}) is None, "no source value: drop them all"


def test_epoch_guard_skips_a_put_that_started_before_an_invalidation(cache):
    epoch = cache.epoch("playlist")
    cache.invalidate("playlist", {"playlistId": "P"})
    assert cache.put("playlist", {"playlistId": "P"}, {"title": "stale"}, epoch=epoch) is False
    assert cache.get("playlist", {"playlistId": "P"}) is None
    epoch = cache.epoch("playlist")
    cache.clear()
    assert cache.put("playlist", {"playlistId": "P"}, {"title": "stale"}, epoch=epoch) is False
    assert cache.put("playlist", {"playlistId": "P"}, {"title": "fresh"}, epoch=cache.epoch("playlist"))


class EditDuringRead(Upstream):
    """get_playlist runs a callback mid-read, as an edit on another lane would."""

    def __init__(self, during):
        super().__init__()
        self.during = during

    def get_playlist(self, playlist_id, limit=200):
        result = super().get_playlist(playlist_id, limit)
        self.during()
        return result


def test_a_read_that_overlaps_an_edit_is_not_cached(cache):
    worker = None
    upstream = EditDuringRead(lambda: worker.handle({"id": 2, "method": "addToPlaylist",
                                                     "params": {"playlistId": "P", "videoIds": ["v"]}}))
    worker = ytm.Worker(upstream, "ok", cache=cache)
    reply = request(worker, "playlist", playlistId="P", cache="refresh")
    assert reply["ok"] and reply["cached"] is False
    assert cache.get("playlist", {"playlistId": "P"}) is None, "the edit made this read stale"
    upstream.during = lambda: None
    request(worker, "playlist", playlistId="P", cache="refresh")
    assert cache.get("playlist", {"playlistId": "P"}) is not None


def test_a_read_that_overlaps_an_account_change_is_not_cached(cache):
    worker = None

    def switch():
        worker.generation += 1  # what load() does when the session changes

    worker = ytm.Worker(EditDuringRead(switch), "ok", cache=cache)
    assert request(worker, "playlist", playlistId="P", cache="refresh")["ok"]
    assert cache.get("playlist", {"playlistId": "P"}) is None


def test_failed_edit_that_may_have_applied_still_invalidates(cache):
    class Failing(Upstream):
        def add_playlist_items(self, playlist_id, video_ids, duplicates=False):
            raise requests.exceptions.ReadTimeout("slow")

    cache.put("playlist", {"playlistId": "P"}, {"title": "before"})
    worker = ytm.Worker(Failing(), "ok", cache=cache)
    assert request(worker, "addToPlaylist", playlistId="P", videoIds=["v"])["error"]["code"] == "NETWORK"
    assert cache.get("playlist", {"playlistId": "P"}) is None


# ---- browsePage pages touched by edits ---------------------------------------------------


class BrowseDuringEdit(Upstream):
    """A browsePage read that runs a callback mid-read, as an edit on the main thread would."""

    def __init__(self, during):
        super().__init__()
        self.during = during

    def _send_request(self, endpoint, body, additionalParams=""):
        self.during()
        return {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
            "sectionListRenderer": {"contents": [{"gridRenderer": {"items": []}}]}}}}]}}}


class Editor(Upstream):
    def remove_playlist_items(self, playlist_id, videos):
        return "STATUS_SUCCEEDED"

    def rate_song(self, video_id, status):
        return {}

    def create_playlist(self, title, description, privacy_status=None, video_ids=None):
        return "PLnew"

    def delete_playlist(self, playlist_id):
        return "STATUS_SUCCEEDED"


def test_core_edits_drop_the_browse_pages_they_change(cache):
    pages = ("VLPLx", "VLPLother", "FEmusic_liked_playlists", "FEmusic_library_landing", "VLLM",
             "FEmusic_liked_videos", "FEmusic_new_releases_albums")

    def fill():
        for browse_id in pages:
            cache.put("browsePage", {"browseId": browse_id}, {"type": "page", "id": browse_id})
        cache.put("browsePage", {"browseId": "VLPLx", "order": 1}, {"type": "page"})

    def cached():
        return {b for b in pages if cache.get("browsePage", {"browseId": b})}

    worker = ytm.Worker(Editor(), "ok", cache=cache)
    fill()
    assert request(worker, "addToPlaylist", playlistId="PLx", videoIds=["v"])["ok"]
    assert cached() == {"VLPLother", "VLLM", "FEmusic_liked_videos", "FEmusic_new_releases_albums"}
    assert cache.get("browsePage", {"browseId": "VLPLx", "order": 1}) is None, "in every order"
    fill()
    assert request(worker, "removeFromPlaylist", playlistId="VLPLx", items=[{"videoId": "v", "setVideoId": "s"}])["ok"]
    assert "VLPLx" not in cached() and "VLPLother" in cached(), "a VL-prefixed id names the same page"
    fill()
    assert request(worker, "rate", videoId="v", rating="LIKE")["ok"]
    assert cached() == {"VLPLx", "VLPLother", "FEmusic_liked_playlists", "FEmusic_library_landing",
                        "FEmusic_new_releases_albums"}
    fill()
    assert request(worker, "createPlaylist", title="t")["ok"]
    assert not {"FEmusic_liked_playlists", "FEmusic_library_landing"} & cached() and "VLPLx" in cached()
    fill()
    assert request(worker, "deletePlaylist", playlistId="PLx")["ok"]
    assert cached() == {"VLPLother", "VLLM", "FEmusic_liked_videos", "FEmusic_new_releases_albums"}


def test_a_browse_page_read_that_overlaps_an_edit_is_not_cached(cache):
    worker = None
    upstream = BrowseDuringEdit(lambda: worker.handle({"id": 2, "method": "addToPlaylist",
                                                      "params": {"playlistId": "PLx", "videoIds": ["v"]}}))
    worker = ytm.Worker(upstream, "ok", cache=cache)
    reply = request(worker, "browsePage", browseId="FEmusic_new_releases_albums", cache="refresh")
    assert reply["ok"] and reply["cached"] is False
    assert cache.get("browsePage", {"browseId": "FEmusic_new_releases_albums"}) is None, \
        "an edit bumps the browsePage epoch, whichever page it drops"
    upstream.during = lambda: None
    request(worker, "browsePage", browseId="FEmusic_new_releases_albums", cache="refresh")
    assert cache.get("browsePage", {"browseId": "FEmusic_new_releases_albums"}) is not None


def test_invalidation_sources(cache):
    assert ytm.invalidation_matches(None, None, {"playlistId": "P"}) == [None]
    assert ytm.invalidation_matches("playlistId", "playlistId", {"playlistId": "P"}) == [{"playlistId": "P"}]
    assert ytm.invalidation_matches("playlistId", "playlistId", {}) == [None]
    prefixed = ytm.Prefixed("VL", "playlistId")
    assert ytm.invalidation_matches(prefixed, "browseId", {"playlistId": "PL1"}) == [{"browseId": "VLPL1"}]
    assert ytm.invalidation_matches(prefixed, "browseId", {"playlistId": "VLPL1"}) == [{"browseId": "VLPL1"}]
    assert ytm.invalidation_matches(prefixed, "browseId", {}) == [None]
    assert ytm.invalidation_matches(ytm.Fixed("A", "B"), "browseId", {}) == [{"browseId": "A"}, {"browseId": "B"}]


# ---- client settings ----------------------------------------------------------------------


def test_client_settings_record_survives_clear(cache):
    assert cache.client_settings() == {}
    cache.record_client_settings({"location": "AU"})
    cache.put("album", {"browseId": "X"}, {"title": "kept"})
    cache.clear()
    assert cache.client_settings() == {"location": "AU"}, "clear() drops responses, not the record"
    cache.put("album", {"browseId": "X"}, {"title": "kept"})
    assert cache.client_settings() == {"location": "AU"}, "pruning leaves it alone"
    assert oct(mode(cache.dir / caches.DiskCache.SETTINGS_FILE)) == oct(0o600)
    cache.record_client_settings({})
    assert cache.client_settings() == {} and not (cache.dir / caches.DiskCache.SETTINGS_FILE).exists()
