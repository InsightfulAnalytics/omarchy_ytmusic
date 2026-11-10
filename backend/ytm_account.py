"""Account identities and the content location (S16).

- ``accounts({})``: the identities of the saved session, from YouTube Music's
  own account switcher (raw ``account/accounts_list``): the session's own
  account and its brand accounts and, when the browser was signed in to
  several Google accounts and YouTube lists them, theirs. Names, handles and
  photos are for the shell only: the method is not cacheable and nothing
  here logs them.
- ``switchAccount({pageId|null, authUser?})``: act as another identity from
  now on. The choice is saved (``ytm_auth.save_identity``: identity.json,
  0600, ids only, no names), the client is rebuilt with it
  (``YTMusic(auth, user=pageId)``; another login's X-Goog-AuthUser, in
  memory), which bumps the generation, and the disk cache is cleared. One
  request (the account menu) checks that YouTube answers as the new
  identity; when it does not, the previous identity is restored.
- When YouTube stops answering as the saved identity (a brand account no
  longer managed, a login signed out), the session check marks the session
  expired, but both methods still work (``require_session``), so the shell
  can go back to the session's own account.
- Setting ``location`` (a client setting: a new client, a cleared cache):
  the content location sent as ``YTMusic(location=...)``, one of ytmusicapi's
  SUPPORTED_LOCATIONS, or "" for YouTube's own guess. The language stays
  English (the parsers match English text).

Brand accounts: YouTube Music removes history entries and takes uploads only
for the session's own account (ytmusicapi's remove_history_items and
upload_song), so those methods call ``require_own_identity`` first
(``removeHistory`` in ytm_history; S10's ``uploadSong`` through
``require_upload_identity``).
"""

import hashlib
from urllib.parse import parse_qsl, urlsplit

import ytm_auth as auth
import ytm_normalize as norm
import ytm_raw as raw
from ytm_proto import WorkerError

ACCOUNTS_ENDPOINT = "account/accounts_list"


# ---- identities ---------------------------------------------------------------


def _menu(response):
    for action in (response or {}).get("actions") or []:
        menu = (((action or {}).get("getMultiPageMenuAction") or {}).get("menu") or {}).get("multiPageMenuRenderer")
        if isinstance(menu, dict):
            return menu
    return {}


def _tokens(item):
    """The identity's supportedTokens merged into one dict (pageIdToken, accountSigninToken, ...)."""
    endpoint = (item.get("serviceEndpoint") or {}).get("selectActiveIdentityEndpoint") or {}
    out = {}
    for token in endpoint.get("supportedTokens") or []:
        if isinstance(token, dict):
            out.update(token)
    return out


def signin_auth_user(url):
    """The Google login index (``authuser``) of an identity's sign-in URL, or None."""
    try:
        value = dict(parse_qsl(urlsplit(url or "").query)).get("authuser")
        return int(value) if value is not None and str(value).isdigit() else None
    except (TypeError, ValueError):
        return None


def _photo(item):
    thumbnails = ((item.get("accountPhoto") or {}).get("thumbnails")) or []
    urls = [t.get("url") for t in thumbnails if isinstance(t, dict) and t.get("url")]
    return urls[-1] if urls else None


def parse_accounts(response, page_id=None, login=0):
    """``[{index, name, handle, photo, selected, brand, pageId?, authUser, otherLogin}]``.

    ``page_id`` and ``login`` are the identity the client acts as (the own
    account: None, and the session's login index). An identity is selected
    when it is that one; when none matches, YouTube's own isSelected is used.
    ``authUser`` is the identity's Google login index from its sign-in URL
    (None when the URL names none and it is not in the first section);
    ``otherLogin`` is true for another Google login's identities.
    """
    items = []
    for number, section in enumerate(_menu(response).get("sections") or []):
        listing = (section or {}).get("accountSectionListRenderer") or {}
        for content in listing.get("contents") or []:
            for entry in ((content or {}).get("accountItemSectionRenderer") or {}).get("contents") or []:
                item = entry.get("accountItem") if isinstance(entry, dict) else None
                if not isinstance(item, dict):
                    continue
                tokens = _tokens(item)
                brand_id = (tokens.get("pageIdToken") or {}).get("pageId")
                if brand_id is not None and not auth.PAGE_ID.fullmatch(str(brand_id)):
                    continue  # not an id this worker could send back
                user = signin_auth_user((tokens.get("accountSigninToken") or {}).get("signinUrl"))
                if user is None and number == 0:
                    user = login
                out = {
                    "index": len(items),
                    "name": raw.text(item.get("accountName")) or "",
                    "handle": raw.text(item.get("channelHandle")) or "",
                    "photo": _photo(item),
                    "selected": False,
                    "youtubeSelected": item.get("isSelected") is True,
                    "brand": brand_id is not None,
                    "authUser": user,
                    "otherLogin": user is None or user != login,
                }
                if brand_id is not None:
                    out["pageId"] = str(brand_id)
                items.append(out)
    matched = [i for i in items if i.get("pageId") == page_id and i["authUser"] == login]
    for item in items:
        youtube = item.pop("youtubeSelected")
        item["selected"] = any(m is item for m in matched) if matched else youtube
    return items


def login_of(worker):
    """The Google login index the worker's client sends as."""
    chosen = getattr(worker, "identity", None) or {}
    if chosen.get("authUser") is not None:
        return chosen["authUser"]
    return auth.session_auth_user(worker.yt)


def identity_summary(worker):
    """``{key, brand, own, authUser}`` of the identity the worker acts as; None signed out.

    ``own``: the session's own account (no identity saved). No names, and the
    key holds no id: a hash of the pageId, so the shell can tell identities
    apart without holding one.
    """
    if getattr(worker, "auth", None) not in ("ok", "expired"):
        return None
    chosen = getattr(worker, "identity", None) or {}
    page_id = chosen.get("pageId")
    login = login_of(worker)
    tag = hashlib.sha1(page_id.encode()).hexdigest()[:12] if page_id else ""
    return {"key": f"{login}:{tag}", "brand": bool(page_id), "own": not chosen, "authUser": login}


def require_own_identity(worker, what):
    """BAD_REQUEST (nothing sent) while the worker acts as a brand account."""
    chosen = getattr(worker, "identity", None) or {}
    if chosen.get("pageId"):
        raise WorkerError("BAD_REQUEST", f"{what} works only for your own account, not a brand account, on "
                                         "YouTube Music; switch back first (Library, M, Switch account)")


# Hook for S10 (backend/ytm_uploads.py, not built yet): uploadSong calls this
# before anything else. upload_song sends as the session's login (its
# x-goog-authuser header, which switchAccount sets for another login) but
# never as a brand account.
def require_upload_identity(worker):
    require_own_identity(worker, "Uploading songs")


def _account_info(info):
    info = info if isinstance(info, dict) else {}
    return {"name": info.get("accountName") or "", "handle": info.get("channelHandle") or "",
            "photo": info.get("accountPhotoUrl") or None}


def _network(exc):
    try:
        import requests

        return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))
    except ImportError:
        return False


def require_session(worker):
    """A saved session, checked as the identity in use; for ``accounts`` and
    ``switchAccount`` only.

    YouTube may stop answering as a saved brand account or login (no longer
    managed, signed out: an unknown pageId gets HTTP 500, the account switcher
    too) while the session itself is fine. The session check then marks it
    expired, but these two still work, so it can be left.
    """
    try:
        worker.require_auth()
    except WorkerError as exc:
        if exc.code != "AUTH_EXPIRED" or not getattr(worker, "identity", None):
            raise


def _identity_unanswered(worker):
    return worker.auth == "expired" and bool(getattr(worker, "identity", None))


def m_accounts(worker, params):
    require_session(worker)
    chosen = getattr(worker, "identity", None) or {}
    if not _identity_unanswered(worker):
        response = worker.yt._send_request(ACCOUNTS_ENDPOINT, {})
        items = parse_accounts(response, chosen.get("pageId"), login_of(worker))
        return {"items": items, "identity": identity_summary(worker)}
    # YouTube does not answer as the identity in use: list as the session's
    # own account (a client of its own; the worker's stays as it is). Only
    # that identity, if listed, is selected: YouTube marks the own account.
    own, _state = auth.load_client(settings=worker.client_settings() or None)
    raw.install(own)
    items = parse_accounts(own._send_request(ACCOUNTS_ENDPOINT, {}), None, auth.saved_auth_user())
    login = login_of(worker)
    for item in items:
        item["selected"] = item.get("pageId") == chosen.get("pageId") and item["authUser"] == login
    return {"items": items, "identity": identity_summary(worker)}


def _switch_error(exc, chosen):
    """What a switch reports when the new identity did not answer (the previous one is back)."""
    if _network(exc):
        return WorkerError("NETWORK", "Could not reach YouTube Music; still using the previous account", True)
    if "HTTP 429" in str(exc):
        return WorkerError("RATE_LIMITED", "YouTube Music is rate limiting requests; still using the previous account",
                           True)
    if not chosen:
        # The session's own account answers whenever the session does
        # (Worker.verify_session): it is the session that expired.
        return WorkerError("AUTH_EXPIRED", "YouTube Music no longer accepts the saved session; sign in again")
    return WorkerError("UPSTREAM", "YouTube Music did not answer as that account; still using the previous one")


def m_switch_account(worker, params):
    page_id = params.get("pageId")
    auth_user = params.get("authUser")
    try:
        chosen = auth.checked_identity(None if page_id in (None, "") else page_id, auth_user)
    except ValueError as exc:
        raise WorkerError("BAD_REQUEST", str(exc)) from exc
    # Not the identity in use: the switch checks the new one itself.
    require_session(worker)
    # The session's own login needs no header of its own: only the pageId.
    if chosen and chosen["authUser"] == auth.saved_auth_user():
        chosen = auth.checked_identity(chosen["pageId"], None)
    previous = worker.identity
    if chosen == previous:
        return {"identity": identity_summary(worker), "changed": False, "auth": worker.auth}
    auth.save_identity(chosen)
    worker.load()
    worker.clear_cache()
    try:
        info = worker.yt.get_account_info()
    except Exception as exc:  # noqa: BLE001 - any failure: back to the identity before
        norm.log(f"switchAccount: the new identity did not answer ({type(exc).__name__}); restored the previous one")
        auth.save_identity(previous)
        worker.load()
        worker.clear_cache()
        raise _switch_error(exc, chosen) from exc
    worker.verified = True
    # auth: the shell marked the session expired when the identity left
    # stopped answering; the new one answered.
    return {"identity": identity_summary(worker), "changed": True, "account": _account_info(info),
            "auth": worker.auth}


# ---- location -------------------------------------------------------------------


def validate_location(value):
    """"" (YouTube's own guess) or a code of ytmusicapi's SUPPORTED_LOCATIONS."""
    from ytmusicapi.constants import SUPPORTED_LOCATIONS

    if value is None or value == "":
        return ""
    if isinstance(value, str) and value.upper() in SUPPORTED_LOCATIONS:
        return value.upper()
    raise WorkerError("BAD_REQUEST", "'location' must be a country code YouTube Music supports, or \"\" for the default")


# ---- registry -------------------------------------------------------------------

METHODS = {
    "accounts": m_accounts,
    "switchAccount": m_switch_account,
}
# Not cacheable: names and photos never reach the disk cache.
CACHEABLE = set()
# Both unlaned: one request each, answered in order (switchAccount replaces
# the client, as reloadAuth does).
LANES = {}
INVALIDATES = {}
# The account's identities are private: no recorder.
RECORDERS = {}
# switchAccount changes nothing on YouTube (and a repeat is harmless).
MUTATES = set()
SETTINGS = {"location": validate_location}
CLIENT_SETTINGS = {"location"}
