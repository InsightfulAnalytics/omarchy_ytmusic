"""Account and privacy edge cases (worker side): mpv's cookie copy follows a sign-out
or sign-in made without a shell, a typed search never goes out
with the account, an upload or a batch of plays that waited on a
lane is refused when the identity changed meanwhile, whole
reads run on a lane of their own (pages-lane-hol-timeouts), and a read that
outlives its client keeps nothing for the new one
(shuffle-memo-generation-at-write)."""

import hashlib
import json
import os
import threading
import time

import pytest

import ytm
import ytm_account as identities
import ytm_auth as auth
import ytm_cache as caches
import ytm_raw as raw
import ytm_search as search
import ytm_uploads as uploads
import ytm_users as users
from test_long_lists import shuffle_pages
from test_history import FakeYT as HistoryYT
from test_library import LibraryYT, call, page_type
from test_search import FakeClient, response, shelf, song_row, continuation
from test_uploads import ResponseStatus, music_file, songs_page, upload_row, worker_for as uploads_worker
from test_users import MENU, MINE, YT as UsersYT, account_menu, link

BRAND_ID = "123456789012345678901"
BRAND_KEY = "0:" + hashlib.sha1(BRAND_ID.encode()).hexdigest()[:12] + ":"  # fakes send no cookie


# ---- mpv's copy of the cookies ---------------------------------------------------------


class Runs:
    """subprocess.run as ytm.py sees it: records the commands, runs none."""

    def __init__(self, fail=None):
        self.commands = []
        self.fail = fail

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        if self.fail is not None and command[0] == ytm.MPV_SCRIPT:
            raise self.fail
        return None


def test_the_player_script_is_the_plugins_own():
    assert ytm.MPV_SCRIPT.endswith(os.path.join("bin", "ytm-mpv")) and os.access(ytm.MPV_SCRIPT, os.X_OK)


@pytest.mark.parametrize("fail", [None, OSError("no such file"), ytm.subprocess.TimeoutExpired("ytm-mpv", 10)])
def test_logout_makes_mpvs_copy_follow_with_or_without_a_shell(monkeypatch, capsys, fail):
    runs = Runs(fail)
    monkeypatch.setattr(ytm.subprocess, "run", runs)
    assert ytm.main(["logout"]) == 0, "best effort: a missing or slow player never fails the sign-out"
    assert [ytm.MPV_SCRIPT, "cookies"] in runs.commands
    mpv_at = runs.commands.index([ytm.MPV_SCRIPT, "cookies"])
    shell_at = next(i for i, c in enumerate(runs.commands) if c[0] == "omarchy-shell")
    assert mpv_at < shell_at, "the copy is gone before a shell is even told"
    assert "Signed out" in capsys.readouterr().out


def test_a_sign_in_from_the_command_line_refreshes_mpvs_copy(monkeypatch, capsys):
    runs = Runs()
    monkeypatch.setattr(ytm.subprocess, "run", runs)
    monkeypatch.setattr(auth, "import_from_browser", lambda *args: {"name": "", "handle": ""})
    assert ytm.main(["auth"]) == 0
    assert [ytm.MPV_SCRIPT, "cookies"] in runs.commands
    runs.commands.clear()

    def refuse(*args):
        raise auth.AuthError("no cookies")

    monkeypatch.setattr(auth, "import_from_browser", refuse)
    assert ytm.main(["auth"]) == 1
    assert [ytm.MPV_SCRIPT, "cookies"] not in runs.commands, "a failed sign-in changed no session"


# ---- search as you type is signed out --------------------------------------------------


class Untouchable:
    """The account's client: any use is a failure."""

    def __getattr__(self, name):
        raise AssertionError(f"a typed search used the account's client ({name})")


@pytest.fixture
def typing(monkeypatch):
    clients = {}

    def anonymous_client(factory=None, settings=None):
        key = (settings or {}).get("location") or ""
        if key not in clients:
            clients[key] = raw.install(FakeClient(*answers))
        return clients[key]

    answers = []
    monkeypatch.setattr(auth, "anonymous_client", anonymous_client)
    return clients, answers


def test_a_typed_search_never_uses_the_accounts_client(typing):
    clients, answers = typing
    answers.extend([response(shelf([song_row("k", "Karma Police")]))])
    worker = ytm.Worker(Untouchable(), "ok")
    reply = call(worker, "search", query="radioh", anonymous=True)
    assert reply["ok"], reply
    assert clients[""].requests == [("search", {"query": "radioh"}, "")]


def test_a_typed_filtered_list_pages_on_through_the_same_client(typing):
    clients, answers = typing
    answers.extend([response(shelf([song_row("a", "A")], token="T0")),
                    continuation([song_row("b", "B")], token="T1"),
                    continuation([song_row("c", "C")])])
    worker = ytm.Worker(Untouchable(), "ok")
    first = call(worker, "search", query="radioh", filter="songs", anonymous=True, limit=2)["result"]
    assert [t["videoId"] for t in first["items"]] == ["a", "b"] and first["cursor"] == "c.T1"
    more = call(worker, "search", query="radioh", filter="songs", anonymous=True, cursor=first["cursor"])["result"]
    assert [t["videoId"] for t in more["items"]] == ["c"]
    assert len(clients[""].requests) == 3


def test_a_typed_search_reads_in_the_content_location(typing):
    clients, answers = typing
    answers.extend([response()])
    worker = ytm.Worker(Untouchable(), "ok")
    worker.settings = {"location": "DE"}
    assert call(worker, "search", query="radioh", anonymous=True)["ok"]
    assert list(clients) == ["DE"]


def test_a_submitted_search_goes_with_the_account(typing):
    clients, _answers = typing
    account = raw.install(FakeClient(response(shelf([song_row("k", "Karma Police")]))))
    worker = ytm.Worker(account, "ok")
    assert call(worker, "search", query="radiohead")["ok"]
    assert call(worker, "search", query="radiohead", anonymous=False)["ok"]
    assert len(account.requests) == 2 and clients == {}


def test_a_typed_search_of_the_library_or_uploads_is_refused(typing):
    clients, _answers = typing
    worker = ytm.Worker(Untouchable(), "ok")
    for scope in ("library", "uploads"):
        reply = call(worker, "search", query="radioh", scope=scope, anonymous=True)
        assert reply["error"]["code"] == "BAD_REQUEST", scope
    assert call(worker, "search", query="radioh", anonymous="yes")["error"]["code"] == "BAD_REQUEST"
    assert clients == {}


def test_the_anonymous_client_is_one_per_location(monkeypatch):
    made = []
    monkeypatch.setattr(auth, "_anonymous", None)
    monkeypatch.setattr(auth, "_anonymous_located", {})

    def factory(**kwargs):
        made.append(kwargs)
        return object()

    plain = auth.anonymous_client(factory)
    assert auth.anonymous_client(factory, settings={}) is plain
    located = auth.anonymous_client(factory, settings={"location": "DE"})
    assert located is not plain and auth.anonymous_client(factory, settings={"location": "DE"}) is located
    assert auth.anonymous_client(factory, settings={"location": "nowhere"}) is plain, "an unknown location: the default"
    assert made == [{"language": "en"}, {"language": "en", "location": "DE"}]


# ---- an ordered uploads read --------------------------------------------------------------


@pytest.mark.parametrize("method,browse_id", [("uploadSongs", uploads.SONGS_ID), ("uploadAlbums", uploads.ALBUMS_ID),
                                              ("uploadArtists", uploads.ARTISTS_ID)])
def test_an_ordered_uploads_read_drops_every_cached_copy_of_that_list(tmp_path, method, browse_id):
    # YouTube saves the order (ytm_pages.ORDER_DEPENDENTS): the shell reads
    # the list next with no order, so no copy in the order before may answer.
    other = "uploadArtists" if method == "uploadAlbums" else "uploadAlbums"
    cache = caches.DiskCache(tmp_path / "data")
    cache.put(method, {}, {"type": "page"})
    cache.put(method, {"order": "z_to_a"}, {"type": "page"})
    cache.put("browsePage", {"browseId": browse_id}, {"type": "page"})
    cache.put(other, {}, {"type": "page"})
    worker = uploads_worker(songs_page([upload_row(1)], selected="a_to_z"), cache=cache,
                            get_library_upload_albums=[], get_library_upload_artists=[])
    reply = call(worker, method, order="a_to_z", cache="refresh")
    assert reply["ok"], reply
    assert cache.get(method, {}) is None, "the plain copy is in the order before"
    assert cache.get(method, {"order": "z_to_a"}) is None
    assert cache.get(method, {"order": "a_to_z"}) is None, "not kept: no load asks in it again"
    assert cache.get("browsePage", {"browseId": browse_id}) is None
    assert cache.get(other, {}) is not None, "another list stays"


# ---- requests that waited on a lane --------------------------------------------


def test_identity_keys_as_the_shell_holds_them():
    worker = ytm.Worker(HistoryYT(), "ok")
    assert identities.identity_summary(worker)["key"] == "0::"
    worker.identity = {"pageId": BRAND_ID, "authUser": None}
    assert identities.identity_summary(worker)["key"] == BRAND_KEY


class Cookied:
    """A client signed in to one browser profile's Google session (its cookie)."""

    def __init__(self, cookie, auth_user="0"):
        self._auth_headers = {"cookie": cookie, "x-goog-authuser": auth_user}


def test_the_key_names_the_sign_in_session_not_only_the_login_index():
    profile_a = "SID=a1; __Secure-1PSIDTS=t1; SAPISID=sapA/x; HSID=h"
    profile_b = "SID=b1; __Secure-1PSIDTS=t1; SAPISID=sapB/y; HSID=h"
    tag_a = identities.session_tag(Cookied(profile_a))
    assert len(tag_a) == 12 and tag_a != identities.session_tag(Cookied(profile_b)), "login 0 of two profiles"
    rotated = "SID=a1; __Secure-1PSIDTS=t2; SAPISID=sapA/x; HSID=h2"
    assert identities.session_tag(Cookied(rotated)) == tag_a, "a rotating cookie leaves the key alone"
    assert identities.session_tag(Cookied("SID=only")) != "", "SID when there is no SAPISID"
    assert identities.session_tag(Cookied("NID=x")) == "" and identities.session_tag(object()) == ""
    worker = ytm.Worker(Cookied(profile_a), "ok")
    key = identities.identity_summary(worker)["key"]
    assert key == f"0::{tag_a}" and "sapA" not in key


def test_an_upload_queued_before_a_sign_in_from_another_profile_is_not_sent(tmp_path):
    path = music_file(tmp_path, "song.mp3")
    worker = uploads_worker(upload_song=ResponseStatus.SUCCEEDED)
    worker.yt._auth_headers = {"cookie": "SAPISID=accountA", "x-goog-authuser": "0"}
    asked = identities.identity_summary(worker)["key"]
    # Signed in again from another browser profile while it waited: login 0 again, another account.
    worker.yt._auth_headers = {"cookie": "SAPISID=accountB", "x-goog-authuser": "0"}
    refused = call(worker, "uploadSong", path=str(path), identityKey=asked)
    assert refused["error"]["code"] == "BAD_REQUEST" and worker.yt.calls == [], "nothing sent to account B"
    worker.yt._auth_headers = {"cookie": "SAPISID=accountA", "x-goog-authuser": "0"}
    assert call(worker, "uploadSong", path=str(path), identityKey=asked)["ok"], "the same session: sent"


def test_an_upload_asked_as_one_identity_is_not_sent_as_another(tmp_path):
    path = music_file(tmp_path, "song.mp3")
    worker = uploads_worker(upload_song=ResponseStatus.SUCCEEDED)
    sent = call(worker, "uploadSong", path=str(path), identityKey="0::")
    assert sent["ok"], sent
    assert len(worker.yt.calls) == 1
    # Another login took over while the upload waited behind another (authUser 1).
    worker.identity = {"pageId": None, "authUser": 1}
    refused = call(worker, "uploadSong", path=str(path), identityKey="0::")
    assert refused["error"]["code"] == "BAD_REQUEST" and "changed" in refused["error"]["message"]
    assert len(worker.yt.calls) == 1 and worker.yt.requests == [], "nothing sent, not even the session check"
    assert call(worker, "uploadSong", path=str(path), identityKey="1::")["ok"], "asked as that login: sent"
    assert call(worker, "uploadSong", path=str(path))["ok"], "the command line's own upload names no identity"
    assert call(worker, "uploadSong", path=str(path), identityKey=7)["error"]["code"] == "BAD_REQUEST"


def test_the_shell_cannot_upload_without_naming_the_account(tmp_path):
    """An upload asked before the shell knew the identity carried no key, and
    so was checked against nothing when its turn on the lane came."""
    path = music_file(tmp_path, "song.mp3")
    worker = uploads_worker(upload_song=ResponseStatus.SUCCEEDED)
    worker.serving = True  # as under serve(): the shell's request
    refused = call(worker, "uploadSong", path=str(path))
    assert refused["error"]["code"] == "BAD_REQUEST" and "not known yet" in refused["error"]["message"]
    assert worker.yt.calls == [] and worker.yt.requests == [], "nothing sent"
    assert call(worker, "uploadSong", path=str(path), identityKey="0::")["ok"]


def test_an_upload_waiting_while_the_session_signed_out_is_not_sent(tmp_path):
    path = music_file(tmp_path, "song.mp3")
    worker = uploads_worker(upload_song=ResponseStatus.SUCCEEDED)
    worker.auth = "none"
    reply = call(worker, "uploadSong", path=str(path), identityKey="0::")
    assert reply["error"]["code"] == "BAD_REQUEST" and worker.yt.calls == []


def test_plays_made_as_one_identity_are_not_reported_as_another():
    yt = HistoryYT()
    worker = ytm.Worker(yt, "ok")
    worker.identity = {"pageId": BRAND_ID, "authUser": None}  # switched from the command line meanwhile
    reply = call(worker, "reportPlays", videoIds=["nbCOAPR33ME"], identityKey="0::")
    assert reply["error"]["code"] == "BAD_REQUEST" and yt.calls == []
    assert call(worker, "reportPlays", videoIds=["nbCOAPR33ME"], identityKey=BRAND_KEY)["ok"]
    assert [c[1] for c in yt.calls] == ["get_song", "add_history_item"]
    assert call(worker, "reportPlays", videoIds=["nbCOAPR33ME"])["ok"], "no key: as before"


def test_a_refused_edit_drops_nothing_from_the_cache(tmp_path):
    cache = caches.DiskCache(tmp_path / "data")
    cache.put("history", {}, {"items": []})
    worker = ytm.Worker(HistoryYT(), "ok", cache=cache)
    assert call(worker, "reportPlays", videoIds=["nbCOAPR33ME"], identityKey=BRAND_KEY)["error"]["code"] == "BAD_REQUEST"
    assert cache.get("history", {}) is not None, "unsent: nothing reached YouTube"


# ---- pages-lane-hol-timeouts: whole reads on a lane of their own -----------------------------------


@pytest.mark.parametrize("method,params,lane", [
    ("playlist", {"playlistId": "PLx", "limit": "all"}, "whole"),
    ("liked", {"limit": "all", "cache": "refresh"}, "whole"),
    ("playlist", {"playlistId": "PLx"}, "pages"),
    ("playlist", {"playlistId": "PLx", "limit": 200}, "pages"),
    ("album", {"browseId": "MPREb_yXhSI4FCUo6"}, "pages"),
    ("nowContext", {"videoId": "nbCOAPR33ME"}, "pages"),
    ("tracksFor", {"kind": "playlist", "id": "PLx", "limit": "all"}, "tracks"),
    ("uploadSong", {"path": "/x.mp3"}, "upload"),
])
def test_lane_of(method, params, lane):
    assert ytm.lane_of({"method": method, "params": params}) == lane
    assert lane in ytm.KNOWN_LANES


class Out:
    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.append(json.loads(text))

    def flush(self):
        pass


def test_a_whole_read_never_holds_up_the_pages_behind_it(monkeypatch):
    released = threading.Event()

    def whole_read(worker, params):
        released.wait(5)
        return {"type": "collection", "tracks": []}

    def now_context(worker, params):
        return {"videoId": params["videoId"]}

    monkeypatch.setitem(ytm.METHODS, "playlist", whole_read)
    monkeypatch.setitem(ytm.METHODS, "nowContext", now_context)
    worker = ytm.Worker(HistoryYT(), "none")
    out = Out()
    read_fd, write_fd = os.pipe()
    thread = threading.Thread(target=ytm.serve, args=(5,), kwargs={"worker": worker, "stdin_fd": read_fd, "out": out},
                              daemon=True)
    thread.start()
    os.write(write_fd, b'{"id":1,"method":"playlist","params":{"playlistId":"PLx","limit":"all"}}\n'
                       b'{"id":2,"method":"nowContext","params":{"videoId":"nbCOAPR33ME"}}\n')
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(line.get("id") == 2 for line in out.lines):
        time.sleep(0.01)
    try:
        assert any(line.get("id") == 2 and line["ok"] for line in out.lines), "the Now tab read answered"
        assert not any(line.get("id") == 1 for line in out.lines), "while the whole read still runs"
    finally:
        released.set()
        os.close(write_fd)
        thread.join(timeout=5)
        os.close(read_fd)


# ---- shuffle-memo-generation-at-write: a read that outlives its client --------------------------------


def swapping(worker, old, new):
    """``old``'s next request swaps the worker's client for ``new`` while it is
    out (Worker.load on the main thread: a new client, generation + 1)."""
    send = old._send_request

    def swap(endpoint, body, extra=""):
        reply = send(endpoint, body, extra)
        if worker.yt is old:
            worker.yt = new
            worker.generation += 1
        return reply

    old._send_request = swap


def test_a_shuffle_read_outliving_its_client_keeps_no_memo_for_the_new_one():
    first_client = LibraryYT(*shuffle_pages(6))
    worker = ytm.Worker(first_client, "ok")
    first = call(worker, "tracksFor", kind="playlist", id="MLCT", limit=40)["result"]
    assert len(first["items"]) == 40 and worker.library_shuffle["generation"] == worker.generation
    second_client = LibraryYT(*shuffle_pages(3, first=900))
    swapping(worker, first_client, second_client)
    more = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True, limit=40)["result"]
    assert more["items"], "the read that was out still answers its own request"
    memo = worker.library_shuffle
    assert memo["generation"] != worker.generation, "its slot is not taken for the new client's"
    after = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True, limit=40)["result"]
    assert all(t["videoId"].startswith("shuf00009") for t in after["items"]), "a new shuffle as the new client"
    assert all(extra == "" for _, _, extra in second_client.requests[:1]), "it starts with no continuation"
    assert not any("W4" in extra for _, _, extra in second_client.requests), "the old continuation never goes out"


def test_a_first_shuffle_outliving_its_client_keeps_no_memo_either():
    first_client = LibraryYT(*shuffle_pages(3))
    worker = ytm.Worker(first_client, "ok")
    swapping(worker, first_client, LibraryYT(*shuffle_pages(3, first=900)))
    call(worker, "tracksFor", kind="playlist", id="MLCT", limit=40)
    assert getattr(worker, "library_shuffle", None) is None


class MenuYT:
    """A client whose account menu check runs ``during`` while it is out."""

    def __init__(self, fail=None):
        self.fail = fail
        self.during = None

    def get_account_info(self):
        if self.during:
            self.during()
        if self.fail:
            raise self.fail
        return {"accountName": "x"}


def test_a_session_check_outliving_its_client_says_nothing_about_the_new_one():
    worker = ytm.Worker(MenuYT(fail=Exception("HTTP 401: Unauthorized")), "ok")
    new = MenuYT()

    def swap():
        worker.yt, worker.auth, worker.verified = new, "ok", False
        worker.own_channel, worker.own_channel_known = None, False
        worker.generation += 1

    worker.yt.during = swap
    worker.verify_session()
    assert worker.auth == "ok", "the old client's failure is not the new one's"
    assert worker.verified is False and worker.own_channel_known is False, "the new one is checked by itself"
    worker.verify_session()
    assert worker.verified is True and worker.own_channel_known is True


def test_a_session_check_that_passes_for_an_old_client_marks_nothing():
    worker = ytm.Worker(MenuYT(), "ok")
    new = MenuYT()

    def swap():
        worker.yt, worker.verified = new, False
        worker.generation += 1

    worker.yt.during = swap
    worker.verify_session()
    assert worker.verified is False and worker.own_channel_known is False


@pytest.mark.parametrize("checked", [False, True])
def test_a_channel_lookup_outliving_its_client_keeps_the_new_ones_channel(checked):
    # myChannel's menu read (known_own_channel) on a session checked another
    # way (a switch of identity); Worker.load() runs while it is out.
    # `checked`: the new client's session was checked by the switch too.
    old = raw.install(UsersYT(menu=account_menu(link("ACCOUNT_BOX", "Old", page_type("UCold", "USER_CHANNEL")))))
    new = raw.install(UsersYT(menu=MENU))
    worker = ytm.Worker(old, "ok")
    worker.verified = True
    read = old.get_account_info

    def load_meanwhile():
        reply = read()
        worker.yt, worker.verified = new, checked
        worker.own_channel, worker.own_channel_known = None, False
        worker.generation += 1
        return reply

    old.get_account_info = load_meanwhile
    assert users.known_own_channel(worker) == MINE
    assert worker.own_channel == MINE and worker.own_channel_known is True, "never the old client's channel"
    assert new.menus() == ["account/account_menu"], "the new client's own menu was read"


# ---- every edit is bound to the identity it was asked under ---------------------------------


def serving(yt):
    worker = ytm.Worker(yt, "ok")
    worker.serving = True  # as under serve(): the shell's requests
    return worker


def test_an_edit_from_the_shell_names_its_account_or_is_not_sent():
    yt = HistoryYT()
    worker = serving(yt)
    sent = []
    worker_rate = lambda w, p: sent.append(dict(p)) or {"ok": True}  # noqa: E731
    original = ytm.METHODS["rate"]
    ytm.METHODS["rate"] = worker_rate
    try:
        cold = call(worker, "rate", videoId="nbCOAPR33ME", rating="LIKE")
        assert cold["error"]["code"] == "BAD_REQUEST" and "not known yet" in cold["error"]["message"]
        other = call(worker, "rate", videoId="nbCOAPR33ME", rating="LIKE", identityKey=BRAND_KEY)
        assert other["error"]["code"] == "BAD_REQUEST" and "changed" in other["error"]["message"]
        assert sent == [], "nothing sent either time"
        assert call(worker, "rate", videoId="nbCOAPR33ME", rating="LIKE", identityKey="0::")["ok"]
        assert sent == [{"videoId": "nbCOAPR33ME", "rating": "LIKE"}], "the key itself never reaches the method"
        worker.serving = False
        assert call(worker, "rate", videoId="nbCOAPR33ME", rating="LIKE")["ok"], "the command line names none"
    finally:
        ytm.METHODS["rate"] = original


def test_every_shell_edit_is_checked_reads_are_not():
    worker = serving(HistoryYT())
    for name in sorted(ytm.MUTATES - set(ytm.LANES)):
        reply = call(worker, name)
        assert reply["error"]["code"] == "BAD_REQUEST" and "not known yet" in reply["error"]["message"], name
    assert worker.yt.calls == []


@pytest.mark.parametrize("method", ["uploadSongs", "uploadAlbums", "uploadArtists"])
def test_a_sort_is_saved_only_as_the_identity_it_was_asked_under(method):
    """An order on your library's pages is saved on the account: from the
    shell it names its identity and goes through the client checked."""
    def fresh():
        worker = uploads_worker(songs_page([upload_row(1)], selected="a_to_z"),
                                get_library_upload_albums=[], get_library_upload_artists=[])
        worker.serving = True
        return worker

    for key, problem in ((None, "not known yet"), (BRAND_KEY, "changed")):
        worker = fresh()
        params = {"order": "a_to_z"} if key is None else {"order": "a_to_z", "identityKey": key}
        reply = call(worker, method, **params)
        assert reply["error"]["code"] == "BAD_REQUEST" and problem in reply["error"]["message"]
        assert worker.yt.requests == [] and worker.yt.calls == [], "no sort saved"
    worker = fresh()
    asked = worker.yt
    assert call(worker, method, order="a_to_z", identityKey="0::")["ok"]
    assert asked.requests or asked.calls
    assert call(fresh(), method)["ok"], "a read in the saved order names nothing"


def test_a_sort_goes_through_the_client_checked_not_one_swapped_in():
    worker = uploads_worker(get_library_upload_albums=[], get_library_upload_artists=[])
    worker.serving = True
    checked = worker.yt
    later = type(checked)(get_library_upload_albums=[], get_library_upload_artists=[])
    real_bound = identities.Bound

    def bound_then_swap(w):  # a switch loads another client right after the check
        view = real_bound(w)
        w.yt = later
        return view

    identities.Bound = bound_then_swap
    try:
        assert call(worker, "uploadAlbums", order="a_to_z", identityKey="0::")["ok"]
    finally:
        identities.Bound = real_bound
    assert [c[0] for c in checked.calls] == ["get_library_upload_albums"] and later.calls == []


def test_the_shell_names_its_identity_on_every_ordered_read():
    text = open(os.path.join(os.path.dirname(__file__), "..", "..", "Backend.qml")).read()
    assert "params.order !== undefined && params.order !== null" in text


def test_the_cache_ignores_the_identity_key(tmp_path):
    cache = caches.DiskCache(tmp_path / "data")
    cache.put("browsePage", {"browseId": "FEmusic_library_x", "identityKey": "0::"}, {"type": "page"})
    assert cache.get("browsePage", {"browseId": "FEmusic_library_x"}) is not None


def test_plays_counted_before_the_account_was_known_are_never_sent():
    yt = HistoryYT()
    worker = serving(yt)
    reply = call(worker, "reportPlays", videoIds=["nbCOAPR33ME"])
    assert reply["error"]["code"] == "BAD_REQUEST" and yt.calls == []
    assert call(worker, "reportPlays", videoIds=["nbCOAPR33ME"], identityKey="0::")["ok"]


def test_a_switch_between_the_check_and_the_send_sends_nothing(tmp_path):
    """load() runs on the main thread while a lane works: the client checked
    is the client used, and a swap after the check refuses the request."""
    path = music_file(tmp_path, "song.mp3")
    worker = uploads_worker(upload_song=ResponseStatus.SUCCEEDED)
    worker.serving = True
    first = worker.yt

    def switched():  # the session check, as another identity loads meanwhile
        with worker.swap_lock:
            worker.yt = HistoryYT("b")
            worker.generation += 1

    worker.require_auth = switched
    reply = call(worker, "uploadSong", path=str(path), identityKey="0::")
    assert reply["error"]["code"] == "BAD_REQUEST" and "changed" in reply["error"]["message"]
    assert first.calls == [] and worker.yt.calls == [], "neither account got the file"


def test_plays_go_through_the_client_that_was_checked():
    a, b = HistoryYT("a"), HistoryYT("b")
    worker = serving(a)

    def swap_on_first_song(video_id):  # another identity loads while the batch is out
        worker.yt = b
        a.on_song = None

    a.on_song = swap_on_first_song
    reply = call(worker, "reportPlays", videoIds=["nbCOAPR33ME", "aaaaaaaaaaa"], identityKey="0::")
    assert reply["ok"] and {c[0] for c in a.calls} == {"a"} and b.calls == [], "the whole batch as a"


def test_a_tracking_url_off_youtube_gets_no_cookies():
    import ytm_history as history

    for url in ("http://s.youtube.com/api/stats/playback?x", "https://evil.example/api?x",
                "https://s.youtube.com.evil.example/x", "file:///etc/passwd"):
        yt = HistoryYT(songs={"nbCOAPR33ME": {"videoDetails": {"videoId": "nbCOAPR33ME"},
                                              "playbackTracking": {"videostatsPlaybackUrl": {"baseUrl": url}}}})
        assert history.report_one(yt, "nbCOAPR33ME") == {"videoId": "nbCOAPR33ME", "ok": False, "retry": False}
        assert [c[1] for c in yt.calls] == ["get_song"], url


# ---- streams with the account -----------------------------------------------------------------


class AccountResolver:
    def __init__(self):
        self.asked = []

    def resolve(self, video_id, account=None, **kwargs):
        self.asked.append(bool(account))
        return {"videoId": video_id, "url": "https://r1.googlevideo.com/v?expire=9", "expires": 9,
                "account": bool(account)}


def with_cookies(worker):
    worker.cookies = auth.CookieSet([auth.cookie_record(".youtube.com", "PREF", "x")])  # tag "", as HistoryYT's
    return worker


def test_account_lookups_only_as_the_browsers_first_login():
    resolver = AccountResolver()
    worker = with_cookies(ytm.Worker(HistoryYT(), "ok", resolver=resolver))
    assert call(worker, "resolve", videoId="nbCOAPR33ME", account=True)["ok"]
    worker.identity = {"pageId": BRAND_ID, "authUser": None}
    refused = call(worker, "resolve", videoId="nbCOAPR33ME", account=True)
    assert refused["error"]["code"] == "BAD_REQUEST" and "first Google account" in refused["error"]["message"]
    worker.identity = {"pageId": None, "authUser": 1}
    assert call(worker, "resolve", videoId="nbCOAPR33ME", account=True)["error"]["code"] == "BAD_REQUEST"
    worker.settings = {"streamAccount": True}
    assert call(worker, "resolve", videoId="nbCOAPR33ME")["ok"], "the setting: anonymous instead"
    assert resolver.asked == [True, False]


def test_an_upload_queued_as_one_identity_is_not_looked_up_as_another():
    resolver = AccountResolver()
    worker = with_cookies(ytm.Worker(HistoryYT(), "ok", resolver=resolver))
    reply = call(worker, "resolve", videoId="nbCOAPR33ME", account=True, identityKey=BRAND_KEY)
    assert reply["error"]["code"] == "BAD_REQUEST" and resolver.asked == []
    assert call(worker, "resolve", videoId="nbCOAPR33ME", account=True, identityKey="0::")["ok"]


def test_only_youtube_stream_urls_reach_mpv():
    import ytm_streams as streams

    for url in ("file:///etc/passwd", "av://lavfi:sine", "http://r1.googlevideo.com/v", "https://evil.example/v",
                "https://googlevideo.com.evil.example/v", "edl://x"):
        assert not streams.stream_url_ok(url), url
    assert streams.stream_url_ok("https://rr3---sn-abc.googlevideo.com/videoplayback?expire=1")
    assert streams.stream_url_ok("https://rr1---sn-abc.c.youtube.com/videoplayback?expire=1"), "an upload's"
    assert not streams.stream_url_ok("https://www.youtube.com/watch?v=x") and not streams.stream_url_ok("https://c.youtube.com.evil/x")
