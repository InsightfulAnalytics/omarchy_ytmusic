"""S16: account identities (accounts, switchAccount), the content location
and what a brand account cannot do. Every response is hand-built with fake
names and ids: the account switcher is private, so nothing is recorded."""

import hashlib
import json
import os
import stat
import threading

import pytest
import requests
from requests.structures import CaseInsensitiveDict
from ytmusicapi.exceptions import YTMusicServerError

import ytm
import ytm_account as identities
import ytm_auth as auth
import ytm_cache as caches

OWN = {"name": "Test Person", "handle": "@testperson", "photo": "https://yt3.ggpht.com/own=s48"}
BRAND_ID = "123456789012345678901"
BRAND = {"name": "Test Brand", "handle": "@testbrand", "photo": "https://yt3.ggpht.com/brand=s48"}
OTHER_BRAND_ID = "998877665544332211000"


def account_item(who, selected=False, page_id=None, authuser=0):
    """An accountItem as account/accounts_list sends it (the live shape)."""
    query = f"action_handle_signin=true&authuser={authuser}" + (f"&pageid={page_id}" if page_id else "") \
        + "&next=%2F&feature=masthead_switcher"
    tokens = []
    if page_id:
        tokens.append({"pageIdToken": {"pageId": page_id}})
    tokens += [{"accountStateToken": {"hasChannel": True, "isMerged": True, "obfuscatedGaiaId": "GAIA"}},
               {"offlineCacheKeyToken": {"clientCacheKey": "KEY"}},
               {"accountSigninToken": {"signinUrl": "/signin?" + query}},
               {"datasyncIdToken": {"datasyncIdToken": "DSID||"}}]
    return {"accountItem": {
        "accountName": {"runs": [{"text": who["name"]}]},
        "accountPhoto": {"thumbnails": [{"url": who["photo"], "width": 48, "height": 48}]},
        "isSelected": selected, "isDisabled": False, "hasChannel": True,
        "serviceEndpoint": {"selectActiveIdentityEndpoint": {"supportedTokens": tokens}},
        "accountByline": {"runs": [{"text": "1 subscriber"}]},
        "channelHandle": {"runs": [{"text": who["handle"]}]},
    }}


def accounts_response(*sections):
    """``sections``: lists of account items, one list per Google login."""
    return {
        "selectText": {"runs": [{"text": "Hi"}]},
        "actions": [{"getMultiPageMenuAction": {"menu": {"multiPageMenuRenderer": {
            "header": {"simpleMenuHeaderRenderer": {"title": {"runs": [{"text": "Accounts"}]}}},
            "sections": [{"accountSectionListRenderer": {
                "contents": [{"accountItemSectionRenderer": {"contents": items}}],
                "header": {"googleAccountHeaderRenderer": {"name": {"runs": [{"text": "Test Person"}]},
                                                           "email": {"runs": [{"text": "test@example.com"}]}}},
            }} for items in sections],
            "footer": {"multiPageMenuSectionRenderer": {"items": []}},
        }}}}],
    }


TWO = accounts_response([account_item(OWN, selected=True), account_item(BRAND, page_id=BRAND_ID)])


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


# ---- accounts -------------------------------------------------------------------


def test_accounts_parse_the_switcher():
    items = identities.parse_accounts(TWO)
    assert items == [
        {"index": 0, "name": "Test Person", "handle": "@testperson", "photo": OWN["photo"], "selected": True,
         "brand": False, "authUser": 0, "otherLogin": False},
        {"index": 1, "name": "Test Brand", "handle": "@testbrand", "photo": BRAND["photo"], "selected": False,
         "brand": True, "authUser": 0, "otherLogin": False, "pageId": BRAND_ID},
    ]
    # Acting as the brand account selects it, whatever YouTube marks.
    as_brand = identities.parse_accounts(TWO, page_id=BRAND_ID)
    assert [i["selected"] for i in as_brand] == [False, True]


def test_accounts_of_other_logins_carry_their_index():
    other = accounts_response([account_item(OWN, selected=True)],
                              [account_item({"name": "Second", "handle": "", "photo": None}, authuser=1),
                               account_item(BRAND, page_id=OTHER_BRAND_ID, authuser=1)])
    items = identities.parse_accounts(other)
    assert [(i["name"], i["authUser"], i["otherLogin"], i["brand"]) for i in items] == [
        ("Test Person", 0, False, False), ("Second", 1, True, False), ("Test Brand", 1, True, True)]
    assert items[1]["photo"] is None and items[1]["handle"] == ""
    # Acting as login 1: its own account is the selected one.
    assert [i["selected"] for i in identities.parse_accounts(other, login=1)] == [False, True, False]


def test_accounts_parse_tolerates_odd_shapes():
    assert identities.parse_accounts({}) == []
    assert identities.parse_accounts({"actions": [{"other": 1}]}) == []
    odd = accounts_response([{"somethingElse": {}}, account_item(OWN, selected=True),
                             account_item(BRAND, page_id="not-a-number")])
    items = identities.parse_accounts(odd)
    assert [i["name"] for i in items] == ["Test Person"], "an identity with an id the worker cannot send back is left out"
    no_url = accounts_response([account_item(OWN, selected=True)])
    no_url["actions"][0]["getMultiPageMenuAction"]["menu"]["multiPageMenuRenderer"]["sections"][0][
        "accountSectionListRenderer"]["contents"][0]["accountItemSectionRenderer"]["contents"][0]["accountItem"][
        "serviceEndpoint"]["selectActiveIdentityEndpoint"]["supportedTokens"] = []
    assert identities.parse_accounts(no_url, login=2)[0]["authUser"] == 2, "the first login is the session's"


def test_signin_auth_user():
    assert identities.signin_auth_user("/signin?action_handle_signin=true&authuser=3&pageid=1") == 3
    assert identities.signin_auth_user("/signin?authuser=x") is None
    assert identities.signin_auth_user(None) is None


# ---- a worker with a fake YTMusic class ----------------------------------------------


class FakeYTMusic:
    """Stands in for ytmusicapi.YTMusic: records how it was built, answers the
    account menu as the identity it acts as and the account switcher."""

    built = []
    refuse = set()  # pageIds (or "own") whose account menu fails
    failure = staticmethod(lambda: RuntimeError("Server returned HTTP 403"))  # how it fails
    switcher = TWO

    def __init__(self, auth=None, user=None, location=""):
        self.auth, self.user, self.location = auth, user, location
        self._auth_headers = CaseInsensitiveDict({"cookie": "SID=fake", "x-goog-authuser": "0"} if auth else {})
        self.headers = {"X-Goog-Visitor-Id": "VISITOR"}
        self.sent = []
        FakeYTMusic.built.append(self)

    def get_account_info(self):
        if (self.user or "own") in FakeYTMusic.refuse:
            raise FakeYTMusic.failure()
        who = BRAND if self.user == BRAND_ID else OWN
        return {"accountName": who["name"], "channelHandle": who["handle"], "accountPhotoUrl": who["photo"]}

    def _send_request(self, endpoint, body, additionalParams=""):
        self.sent.append(endpoint)
        if (self.user or "own") in FakeYTMusic.refuse:
            raise FakeYTMusic.failure()
        return FakeYTMusic.switcher


@pytest.fixture
def signed_in(config_dir, monkeypatch):
    config_dir.mkdir(parents=True)
    (config_dir / "browser.json").write_text(json.dumps({"cookie": "SID=fake", "x-goog-authuser": "0"}))
    FakeYTMusic.built = []
    FakeYTMusic.refuse = set()
    FakeYTMusic.failure = staticmethod(lambda: RuntimeError("Server returned HTTP 403"))
    FakeYTMusic.switcher = TWO
    monkeypatch.setattr(auth, "_ytmusic_class", lambda: FakeYTMusic)
    return config_dir


def call(worker, method, **params):
    return worker.handle({"id": 9, "method": method, "params": params})


def test_accounts_method_needs_a_session_and_is_never_cached(config_dir, monkeypatch):
    monkeypatch.setattr(auth, "_ytmusic_class", lambda: FakeYTMusic)
    signed_out = ytm.Worker()
    assert call(signed_out, "accounts")["error"]["code"] == "AUTH_REQUIRED"
    assert "accounts" not in ytm.CACHEABLE and "switchAccount" not in ytm.CACHEABLE
    assert "accounts" not in ytm.RECORDERS


def test_accounts_method_lists_the_identities(signed_in):
    worker = ytm.Worker()
    reply = call(worker, "accounts")
    assert reply["ok"], reply
    assert [(i["name"], i["selected"]) for i in reply["result"]["items"]] == [("Test Person", True), ("Test Brand", False)]
    assert reply["result"]["identity"] == {"key": "0:", "brand": False, "own": True, "authUser": 0}
    assert worker.yt.sent == [identities.ACCOUNTS_ENDPOINT]


def test_switch_account_rebuilds_the_client_as_the_brand_account(signed_in, tmp_path):
    cache = caches.DiskCache(tmp_path / "data")
    album = {"browseId": "X"}
    cache.put("album", album, {"title": "own account's copy"})
    worker = ytm.Worker(cache=cache)
    assert worker.generation == 1 and FakeYTMusic.built[-1].user is None
    reply = call(worker, "switchAccount", pageId=BRAND_ID)
    assert reply["ok"], reply
    assert FakeYTMusic.built[-1].user == BRAND_ID, "YTMusic(auth, user=pageId)"
    assert worker.yt is FakeYTMusic.built[-1] and worker.identity == {"pageId": BRAND_ID, "authUser": None}
    assert worker.generation == 2, "a read that started before cannot be cached"
    assert cache.get("album", album) is None, "the own account's copies are gone"
    result = reply["result"]
    assert result["changed"] is True and result["account"] == BRAND
    assert result["identity"]["brand"] is True and result["identity"]["own"] is False
    assert result["identity"]["authUser"] == 0
    assert BRAND_ID not in json.dumps(result["identity"]), "the shell gets a hash, not the id"
    assert call(worker, "hello")["result"]["identity"] == result["identity"]
    # The switcher now marks the brand account.
    items = call(worker, "accounts")["result"]["items"]
    assert [i["selected"] for i in items] == [False, True]


def test_the_choice_is_saved_private_without_names_and_survives_a_restart(signed_in):
    worker = ytm.Worker()
    assert call(worker, "switchAccount", pageId=BRAND_ID)["ok"]
    path = signed_in / "identity.json"
    assert mode(path) == 0o600
    text = path.read_text()
    assert json.loads(text) == {"version": 1, "pageId": BRAND_ID, "authUser": None}
    for name in (OWN["name"], BRAND["name"], OWN["handle"], BRAND["handle"], "test@example.com"):
        assert name not in text
    # A new worker (idle exit, a CLI call) acts as the brand account too.
    again = ytm.Worker()
    assert FakeYTMusic.built[-1].user == BRAND_ID and again.identity == {"pageId": BRAND_ID, "authUser": None}
    # Back to the own account: the file goes.
    reply = call(again, "switchAccount", pageId=None)
    assert reply["ok"] and reply["result"]["identity"] == {"key": "0:", "brand": False, "own": True, "authUser": 0}
    assert not path.exists() and FakeYTMusic.built[-1].user is None and again.generation == 2


def test_switch_to_another_login_sets_its_authuser_header(signed_in):
    worker = ytm.Worker()
    reply = call(worker, "switchAccount", authUser=1)
    assert reply["ok"], reply
    client = FakeYTMusic.built[-1]
    assert client.user is None and client._auth_headers["X-Goog-AuthUser"] == "1"
    assert reply["result"]["identity"] == {"key": "1:", "brand": False, "own": False, "authUser": 1}
    assert json.loads((signed_in / "identity.json").read_text())["authUser"] == 1
    assert json.loads((signed_in / "browser.json").read_text())["x-goog-authuser"] == "0", \
        "the saved session is never rewritten"
    # A brand account of that login: both.
    assert call(worker, "switchAccount", pageId=OTHER_BRAND_ID, authUser=1)["ok"]
    client = FakeYTMusic.built[-1]
    assert client.user == OTHER_BRAND_ID and client._auth_headers["x-goog-authuser"] == "1"
    # The session's own login index is no override: only the pageId is kept.
    assert call(worker, "switchAccount", pageId=BRAND_ID, authUser=0)["ok"]
    assert json.loads((signed_in / "identity.json").read_text())["authUser"] is None


def test_switching_to_the_identity_in_use_changes_nothing(signed_in):
    worker = ytm.Worker()
    reply = call(worker, "switchAccount", pageId=None, authUser=0)
    assert reply["ok"] and reply["result"]["changed"] is False and worker.generation == 1
    assert len(FakeYTMusic.built) == 1 and not (signed_in / "identity.json").exists()


def test_an_identity_youtube_does_not_answer_for_is_not_kept(signed_in, tmp_path):
    cache = caches.DiskCache(tmp_path / "data")
    album = {"browseId": "X"}
    worker = ytm.Worker(cache=cache)
    cache.put("album", album, {"title": "cached"})
    FakeYTMusic.refuse = {BRAND_ID}
    reply = call(worker, "switchAccount", pageId=BRAND_ID)
    assert reply["ok"] is False and reply["error"]["code"] == "UPSTREAM"
    assert "previous" in reply["error"]["message"] and reply["error"]["retryable"] is False
    assert not (signed_in / "identity.json").exists() and worker.identity is None
    assert FakeYTMusic.built[-1].user is None and worker.yt is FakeYTMusic.built[-1]
    assert cache.get("album", album) is None, "copies cached while the switch was under way are gone"
    assert worker.generation == 3, "a read from before or during the switch cannot be cached"
    assert worker.auth == "ok", "the own account still answers"


@pytest.mark.parametrize("failure, code", [
    (lambda: requests.exceptions.ConnectionError("offline"), "NETWORK"),
    (lambda: YTMusicServerError("Server returned HTTP 429: Too Many Requests."), "RATE_LIMITED"),
])
def test_a_switch_that_cannot_tell_is_retryable(signed_in, failure, code):
    worker = ytm.Worker()
    FakeYTMusic.refuse = {BRAND_ID}
    FakeYTMusic.failure = staticmethod(failure)
    reply = call(worker, "switchAccount", pageId=BRAND_ID)
    assert reply["error"]["code"] == code and reply["error"]["retryable"] is True
    assert "previous" in reply["error"]["message"]
    assert worker.identity is None and not (signed_in / "identity.json").exists() and worker.auth == "ok"


def stale_brand(config_dir):
    """A saved brand account YouTube no longer answers as (access removed, a
    deleted brand): measured live, an unknown pageId gets HTTP 500 from the
    account menu and from the account switcher alike."""
    auth.save_identity({"pageId": BRAND_ID, "authUser": None})
    FakeYTMusic.refuse = {BRAND_ID}
    FakeYTMusic.failure = staticmethod(lambda: YTMusicServerError("Server returned HTTP 500: Internal Server Error."))
    return ytm.Worker()


def test_an_identity_that_stopped_answering_can_be_left(signed_in):
    worker = stale_brand(signed_in)
    assert worker.identity == {"pageId": BRAND_ID, "authUser": None}
    # The session check runs as the brand account: the session reads as expired.
    reply = call(worker, "libraryPlaylists")
    assert reply["error"]["code"] == "AUTH_EXPIRED" and "account in use" in reply["error"]["message"]
    assert worker.auth == "expired"
    # The identities are still listed (as the session's own account) ...
    reply = call(worker, "accounts")
    assert reply["ok"], reply
    shown = [(i["name"], i["selected"]) for i in reply["result"]["items"]]
    assert shown == [("Test Person", False), ("Test Brand", True)]
    assert FakeYTMusic.built[-1].user is None and FakeYTMusic.built[-1].sent == [identities.ACCOUNTS_ENDPOINT]
    assert worker.yt.user == BRAND_ID and worker.yt.sent == [], "the worker's own client is left as it is"
    # ... and going back to the own account works and says the session answers.
    reply = call(worker, "switchAccount", pageId=None)
    assert reply["ok"], reply
    assert reply["result"]["changed"] is True and reply["result"]["auth"] == "ok"
    assert reply["result"]["identity"] == {"key": "0:", "brand": False, "own": True, "authUser": 0}
    assert not (signed_in / "identity.json").exists() and worker.identity is None and worker.auth == "ok"
    assert call(worker, "hello")["result"]["auth"] == "ok"
    assert call(ytm.Worker(), "hello")["result"]["identity"]["own"] is True, "a new worker starts as the own account"


def test_a_session_that_expired_stays_expired(signed_in):
    worker = stale_brand(signed_in)
    FakeYTMusic.refuse = {BRAND_ID, "own"}
    reply = call(worker, "switchAccount", pageId=None)
    assert reply["error"]["code"] == "AUTH_EXPIRED" and "sign in again" in reply["error"]["message"]
    assert worker.auth == "expired" and worker.identity == {"pageId": BRAND_ID, "authUser": None}
    assert auth.read_identity() == {"pageId": BRAND_ID, "authUser": None}, "a failed switch changes nothing"
    # Acting as the own account, an expired session allows neither method.
    auth.save_identity(None)
    own = ytm.Worker()
    for method, params in (("accounts", {}), ("switchAccount", {"pageId": BRAND_ID})):
        reply = own.handle({"id": 1, "method": method, "params": params})
        assert reply["error"]["code"] == "AUTH_EXPIRED" and "no longer accepts" in reply["error"]["message"], method
    assert not (signed_in / "identity.json").exists()


def test_switch_account_params_are_checked_before_anything(signed_in):
    worker = ytm.Worker()
    for params in ({"pageId": "abc"}, {"pageId": 123}, {"authUser": -1}, {"authUser": 10}, {"authUser": "1"},
                   {"authUser": True}):
        reply = call(worker, "switchAccount", **params)
        assert reply["error"]["code"] == "BAD_REQUEST", params
    assert len(FakeYTMusic.built) == 1 and worker.generation == 1


def test_switch_account_needs_a_session(config_dir, monkeypatch):
    monkeypatch.setattr(auth, "_ytmusic_class", lambda: FakeYTMusic)
    worker = ytm.Worker()
    assert call(worker, "switchAccount", pageId=BRAND_ID)["error"]["code"] == "AUTH_REQUIRED"
    assert not (config_dir / "identity.json").exists()


def test_no_account_name_reaches_the_debug_log(signed_in, monkeypatch, capsys):
    monkeypatch.setenv("YTM_DEBUG", "1")
    worker = ytm.Worker()
    call(worker, "accounts")
    call(worker, "switchAccount", pageId=BRAND_ID)
    FakeYTMusic.refuse = {"own", OTHER_BRAND_ID}
    call(worker, "switchAccount", pageId=OTHER_BRAND_ID)
    call(worker, "switchAccount", pageId=None)
    err = capsys.readouterr().err
    assert "accounts ok" in err and "switchAccount ok" in err and "switchAccount UPSTREAM" in err
    assert "switchAccount AUTH_EXPIRED" in err, "the own account not answering: the session expired"
    for secret in (OWN["name"], BRAND["name"], OWN["handle"], BRAND["handle"], BRAND_ID, "test@example.com"):
        assert secret not in err


def test_the_ready_event_says_which_identity(signed_in):
    def ready_of(worker):
        lines = []

        class Out:
            def write(self, text):
                lines.append(json.loads(text))

            def flush(self):
                pass

        read_fd, write_fd = os.pipe()
        os.close(write_fd)  # stdin closed: serve() returns after the ready event
        thread = threading.Thread(target=ytm.serve, args=(5,),
                                  kwargs={"worker": worker, "stdin_fd": read_fd, "out": Out()}, daemon=True)
        thread.start()
        thread.join(timeout=5)
        os.close(read_fd)
        assert lines and lines[0]["event"] == "ready"
        return lines[0]

    assert ready_of(ytm.Worker())["identity"] == {"key": "0:", "brand": False, "own": True, "authUser": 0}
    auth.save_identity({"pageId": BRAND_ID, "authUser": None})
    ready = ready_of(ytm.Worker())
    tag = hashlib.sha1(BRAND_ID.encode()).hexdigest()[:12]
    assert ready["identity"] == {"key": f"0:{tag}", "brand": True, "own": False, "authUser": 0}
    assert BRAND_ID not in json.dumps(ready)


def test_a_switch_from_the_command_line_tells_a_running_shell(signed_in, monkeypatch, capsys):
    told = []
    monkeypatch.setattr(ytm, "notify_shell", lambda: told.append(True))
    assert ytm.main(["call", "switchAccount", json.dumps({"pageId": BRAND_ID})]) == 0
    assert len(told) == 1
    assert ytm.main(["call", "switchAccount", json.dumps({"pageId": BRAND_ID})]) == 0
    assert len(told) == 1, "the identity in use already: nothing to tell"
    FakeYTMusic.refuse = {OTHER_BRAND_ID}
    assert ytm.main(["call", "switchAccount", json.dumps({"pageId": OTHER_BRAND_ID})]) == 1
    assert len(told) == 1, "a failed switch changed nothing"
    assert ytm.main(["call", "switchAccount", "{}"]) == 0 and len(told) == 2
    assert BRAND_ID not in capsys.readouterr().out, "the reply names no id"


def test_sign_in_and_out_reset_the_identity(signed_in, monkeypatch):
    auth.save_identity({"pageId": BRAND_ID, "authUser": None})
    assert auth.read_identity() == {"pageId": BRAND_ID, "authUser": None}
    assert "identity.json" in auth.logout()
    auth.save_identity({"pageId": BRAND_ID, "authUser": None})

    def setup(path, raw):
        path.write_text(json.dumps({"cookie": "SID=a; __Secure-3PAPISID=b", "x-goog-authuser": "0"}))

    monkeypatch.setattr(auth, "_setup_browser_json", setup)
    auth.import_from_headers("cookie: SID=a; __Secure-3PAPISID=b\n", factory=FakeYTMusic)
    assert auth.read_identity() is None, "a new session starts as its own account"


def test_a_damaged_identity_file_means_the_own_account(signed_in):
    for text in ("not json", '{"pageId": "x1"}', '{"authUser": 99}', "[]"):
        (signed_in / "identity.json").write_text(text)
        assert auth.read_identity() is None
        assert ytm.Worker().identity is None and FakeYTMusic.built[-1].user is None


# ---- what a brand account cannot do ---------------------------------------------------


def test_brand_identity_blocks_remove_history_before_sending(signed_in):
    worker = ytm.Worker()
    assert call(worker, "switchAccount", pageId=BRAND_ID)["ok"]
    sent = list(worker.yt.sent)
    reply = call(worker, "removeHistory", tokens=["tok"])
    assert reply["error"]["code"] == "BAD_REQUEST" and "brand account" in reply["error"]["message"]
    assert worker.yt.sent == sent and reply["http"] == 0


def test_brand_identity_blocks_uploads_hook(signed_in):
    worker = ytm.Worker()
    identities.require_upload_identity(worker)  # the own account: fine
    assert call(worker, "switchAccount", pageId=BRAND_ID)["ok"]
    with pytest.raises(ytm.WorkerError) as err:
        identities.require_upload_identity(worker)
    assert err.value.code == "BAD_REQUEST" and "Uploading songs" in err.value.message
    # Another Google login's own account may upload (upload_song sends its authuser).
    assert call(worker, "switchAccount", authUser=1)["ok"]
    identities.require_upload_identity(worker)


# ---- location -------------------------------------------------------------------------


def test_location_is_validated_against_supported_locations():
    from ytmusicapi.constants import SUPPORTED_LOCATIONS

    worker = ytm.Worker(object(), "none")
    for bad in ("ZZ", "XX", "Australia", 1, True, ["AU"]):
        assert call(worker, "configure", location=bad)["error"]["code"] == "BAD_REQUEST", bad
    assert "ZZ" not in SUPPORTED_LOCATIONS and "AU" in SUPPORTED_LOCATIONS
    assert ytm.SETTINGS["location"] is identities.validate_location and "location" in ytm.CLIENT_SETTINGS
    assert identities.validate_location("au") == "AU" and identities.validate_location("") == ""
    for code in sorted(SUPPORTED_LOCATIONS):
        assert identities.validate_location(code) == code


def test_location_rebuilds_the_client_with_it(signed_in, tmp_path):
    cache = caches.DiskCache(tmp_path / "data")
    album = {"browseId": "X"}
    worker = ytm.Worker(cache=cache)
    assert FakeYTMusic.built[-1].location == ""
    cache.put("album", album, {"title": "cached"})
    reply = call(worker, "configure", location="AU", normalize="off", quality="high")
    assert reply["ok"] and reply["result"]["reloaded"] is True
    assert FakeYTMusic.built[-1].location == "AU" and worker.generation == 2
    assert cache.get("album", album) is None and cache.client_settings() == {"location": "AU"}
    # The same location again (every worker start): nothing rebuilt.
    assert call(worker, "configure", location="AU", normalize="off", quality="low")["result"]["reloaded"] is False
    # "" is the default: the same as no location at all.
    reply = call(worker, "configure", location="", normalize="off", quality="high")
    assert reply["result"]["reloaded"] is True and FakeYTMusic.built[-1].location == ""
    assert cache.client_settings() == {}
    assert call(worker, "configure", normalize="off")["result"]["reloaded"] is False
    # A brand account keeps its identity through a location change.
    assert call(worker, "switchAccount", pageId=BRAND_ID)["ok"]
    assert call(worker, "configure", location="DE")["ok"]
    assert (FakeYTMusic.built[-1].user, FakeYTMusic.built[-1].location) == (BRAND_ID, "DE")


def test_signed_out_client_gets_the_location_too(config_dir, monkeypatch):
    monkeypatch.setattr(auth, "_ytmusic_class", lambda: FakeYTMusic)
    worker = ytm.Worker()
    assert call(worker, "configure", location="JP")["ok"]
    assert FakeYTMusic.built[-1].auth is None and FakeYTMusic.built[-1].location == "JP"
    assert "identity" not in call(worker, "hello")["result"], "signed out: no identity"


def test_load_client_drops_a_location_ytmusicapi_would_refuse(config_dir):
    client, state = auth.load_client(factory=FakeYTMusic, settings={"location": "ZZ"})
    assert state == "none" and client.location == ""


def test_the_shell_lists_exactly_the_supported_locations():
    import re

    from conftest import ROOT
    from ytmusicapi.constants import SUPPORTED_LOCATIONS

    model = (ROOT / "YtmModel.js").read_text()
    table = model.split("var LOCATIONS = [", 1)[1].split("]\n]", 1)[0]
    codes = re.findall(r'\["([A-Z]{2})", "', table)
    assert len(codes) == len(set(codes)) and set(codes) == set(SUPPORTED_LOCATIONS)


def test_the_shell_knows_which_locations_charts_has(load_fixture):
    import re

    import ytm_explore as explore
    from conftest import ROOT
    from test_explore import with_codes
    from ytmusicapi.constants import SUPPORTED_LOCATIONS

    model = (ROOT / "YtmModel.js").read_text()
    table = model.split("var CHART_COUNTRIES = [", 1)[1].split("]", 1)[0]
    codes = re.findall(r'"([A-Z]{2})"', table)
    served, _selected = explore.countries(with_codes(load_fixture("charts.json")))
    assert len(codes) == len(set(codes))
    assert set(codes) == {c["code"] for c in served} - {"ZZ"}, "the recorded Charts page's country menu"
    assert set(codes) <= set(SUPPORTED_LOCATIONS)
