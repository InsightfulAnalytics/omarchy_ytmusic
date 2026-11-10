"""Recommendations (S14): Listen again pins, Not interested, taste profile.

Every edit here changes the account (MUTATES): the shell sends it with
retry:false and refreshes what it touched when the outcome is unknown.

- ``feedback({tokens, browseId?, playlistId?, channelId?})`` sends menu
  feedback tokens (``edit_song_library_status``): Pin to / Unpin from Listen
  again (a track's ``listenAgainTokens`` (S3), a page header's ``pin``
  (album, playlist, Liked Music, artist), a Home card's), Home's Not
  interested / Don't recommend (a card's ``feedback``) and their Undo (the
  same card's ``notInterestedUndo`` / ``dontRecommendUndo``: the web's
  "Removed from Home" toast sends it). It succeeds only
  when YouTube processed every token; otherwise the token was stale (the
  page it came from is out of date): UPSTREAM with ``stale: true`` (S4's
  StaleToken). ``browseId`` / ``playlistId`` / ``channelId`` name the page
  the token came from (an album, a playlist or Liked Music as ``LM``, an
  artist), whose cached copy then goes too. Home and Listen again's See all
  always go. YouTube is known to ignore some unpins (ytmusicapi's warning):
  the call still succeeds. Returns ``{applied: n}``.
- ``tasteProfile({})``: the artists the taste builder suggests
  (music.youtube.com/tasteprofile), ``{artists: [{name}]}`` in served order.
  Names only: YouTube does not say which were picked before, and the
  selection tokens never leave the worker. Not cached.
- ``setTasteProfile({artists})`` adds artists (names from tasteProfile) to
  the taste profile: ``set_tasteprofile`` with the profile read just
  before (two requests), at most TASTE_MAX at once (the web's form takes
  any number of the suggestions, about 900). A name no longer suggested is
  BAD_REQUEST and nothing is sent. There is no API to take an artist out
  again (the web only). Returns ``{added: [names]}``.

The menu parsing (pins by icon KEEP / KEEP_OFF, Not interested and Don't
recommend by NOT_INTERESTED / REMOVE_CIRCLE, their Undo by the toast's
structure) is ytm_raw's, so the artist, collection and Home modules read it
without importing this one.

Area module (see AREA_MODULES in ytm.py). Tokens never reach a log, a
message or the disk cache.
"""

import ytm_actions as actions
from ytm_proto import Fixed, WorkerError, opt_str, unsent

TOKENS_MAX = 10
# Above every suggestion list seen (889 names); it bounds the request.
TASTE_MAX = 1000
NAME_MAX = 200
LISTEN_AGAIN = "FEmusic_listen_again"
REFUSED = "YouTube Music did not take the change"
# The request params that name the page a token came from (for INVALIDATES).
SOURCES = ("browseId", "playlistId", "channelId")


def _refused(exc):
    """A 400, 401 or 403 from an edit, once the session was checked
    (require_auth), is YouTube refusing it, not a lost session: UPSTREAM,
    so the worker keeps its sign-in (as ytm_edit does). Anything else is
    raised as it is."""
    from ytmusicapi.exceptions import YTMusicServerError

    first = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    if isinstance(exc, YTMusicServerError) and any(f"HTTP {code}" in first for code in (400, 401, 403)):
        return WorkerError("UPSTREAM", REFUSED)
    return exc


def _tokens(params):
    value = params.get("tokens")
    if not isinstance(value, list) or not value or len(value) > TOKENS_MAX or not all(
            isinstance(t, str) and t.strip() and len(t) <= actions.TOKEN_MAX for t in value):
        raise WorkerError("BAD_REQUEST", f"'tokens' must be 1 to {TOKENS_MAX} feedback tokens from a fresh list")
    return value


def _sources(params):
    """Checks the optional source params; a playlist's is kept without VL
    (written back, so INVALIDATES matches the cached page)."""
    for key in SOURCES:
        value = opt_str(params, key, high=NAME_MAX)
        if key == "playlistId" and value and value.startswith("VL"):
            params[key] = value[2:]


def m_feedback(worker, params):
    tokens = _tokens(params)
    _sources(params)
    worker.require_auth()
    try:
        response = worker.yt.edit_song_library_status(feedbackTokens=tokens)
    except Exception as exc:
        raise _refused(exc) from None
    if not actions.processed(response):
        raise actions.StaleToken()
    return {"applied": len(tokens)}


def m_taste_profile(worker, params):
    worker.require_auth()
    profile = worker.yt.get_tasteprofile() or {}
    return {"artists": [{"name": name} for name in profile if isinstance(name, str) and name.strip()]}


def _artists(params):
    """The names asked for, in order, without repeats (exactly as served:
    they are the taste profile's keys)."""
    value = params.get("artists")
    if not isinstance(value, list) or not value or len(value) > TASTE_MAX or not all(
            isinstance(a, str) and a.strip() and len(a) <= NAME_MAX for a in value):
        raise WorkerError("BAD_REQUEST", f"'artists' must be 1 to {TASTE_MAX} artist names from tasteProfile")
    names = []
    for name in value:
        if name not in names:
            names.append(name)
    return names


def _missing_message(missing):
    shown = ", ".join(missing[:3]) + (f" and {len(missing) - 3} more" if len(missing) > 3 else "")
    return f"No longer suggested: {shown}. Open the list again"


def m_set_taste_profile(worker, params):
    names = _artists(params)
    worker.require_auth()
    try:
        profile = worker.yt.get_tasteprofile() or {}
    except Exception as exc:
        raise unsent(exc)
    missing = [name for name in names if name not in profile]
    if missing:
        raise unsent(WorkerError("BAD_REQUEST", _missing_message(missing)))
    try:
        worker.yt.set_tasteprofile(names, taste_profile=profile)
    except Exception as exc:
        raise _refused(exc) from None
    return {"added": names}


# ---- registry (merged by ytm.py) --------------------------------------------------------

# A value no cached entry has: a missing source would drop every entry.
_NONE = "\0none"


class _Given:
    """The page a token came from, when the request names it (``key``,
    ``prefix`` + it for a browsePage), else nothing: a Home card's feedback
    drops no album, playlist or artist page."""

    def __init__(self, key, prefix=""):
        self.key, self.prefix = key, prefix

    def __call__(self, params):
        value = params.get(self.key)
        if not isinstance(value, str) or not value:
            return _NONE
        return value if value.startswith(self.prefix) else self.prefix + value

    def __repr__(self):
        return f"_Given({self.key!r}, {self.prefix!r})"


class _Liked:
    """Liked Music's pages (keyed by their limit) when the token came from
    it (``playlistId`` LM): None drops every one; otherwise none."""

    def __call__(self, params):
        return None if params.get("playlistId") == "LM" else _NONE

    def __repr__(self):
        return "_Liked()"


METHODS = {"feedback": m_feedback, "tasteProfile": m_taste_profile, "setTasteProfile": m_set_taste_profile}
CACHEABLE = set()
# The taste builder's suggestions are a page read (about 900 names).
LANES = {"tasteProfile": "pages"}
INVALIDATES = {
    # Home (Listen again, and the cards a Not interested removes) and
    # Listen again's See all; the page the token came from, when named.
    "feedback": [
        ("homeFeed", None, None),
        ("browsePage", Fixed(LISTEN_AGAIN), "browseId"),
        ("album", _Given("browseId"), "browseId"),
        ("playlist", _Given("playlistId"), "playlistId"),
        ("browsePage", _Given("playlistId", "VL"), "browseId"),
        ("liked", _Liked(), "limit"),
        ("artist", _Given("channelId"), "channelId"),
    ],
    # Home's recommendations follow the taste profile.
    "setTasteProfile": [("homeFeed", None, None)],
}
RECORDERS = {}
MUTATES = {"feedback", "setTasteProfile"}
