"""Library actions (S4): save albums and playlists, subscribe, songs in the library.

Every method here changes the account (MUTATES): the shell sends it with
retry:false and refreshes what it touched when the outcome is unknown.

- ``rateCollection({playlistId?, rating, kind, browseId?, artistIds?})``: an
  album's or a playlist's "Save to library" / "Remove from library"
  (``rating`` LIKE / INDIFFERENT; DISLIKE dislikes it). ``kind`` is
  ``album`` or ``playlist``; an album needs its ``browseId`` (its page is
  what the edit changes) and may leave out ``playlistId``, its audio
  playlist (OLAK5uy_...), which is then read from the album page (the disk
  copy, else one request). Saving an album also puts its songs and artists
  in the library: ``artistIds`` (the album's artist channels) names the
  artist pages whose "From your library" shelf changes; without it they
  come from the album page's disk copy, else every artist page is dropped.
  Returns ``{playlistId, likeStatus, kind, browseId}``.
- ``subscribe({channelId?, pageChannelId?, subscribe})``: subscribe to an
  artist or any channel (``subscribe`` true) or unsubscribe (false).
  ``channelId`` is the channel to subscribe to; an artist page's subscribe
  button names another channel than the page itself (S1's
  ``header.subscribeId``). ``pageChannelId`` is the artist page the edit
  changes; without ``channelId`` the subscribe channel is read from that
  page (the disk copy, else one request). Returns ``{channelId,
  pageChannelId, subscribed}``.
- ``songLibrary({videoId, token, artistIds?})``: a song's "Save to library"
  / "Remove from library" through the feedback token its row came with
  (S3's ``libraryTokens.add`` or ``.remove``; which one decides the
  direction). ``artistIds`` as for an album. Tokens are account-bound and
  may be stale: YouTube then answers without processing the feedback,
  which is UPSTREAM (``STALE_MESSAGE``) with ``stale: true`` in the error,
  and the shell reloads the page the token came from. Returns
  ``{videoId}``.

A lookup that fails before the edit is sent (an album's playlist, an
artist's subscribe channel) is ``unsent``: the worker then drops nothing.

Also ``library_toggle(response)``: whether an album or playlist page's
header says it is in the library (ytm_collections reads it; ytmusicapi
1.12.2 reports every album as INDIFFERENT).

Area module (see AREA_MODULES in ytm.py). Tokens and channel names never
reach a log: errors carry the first line of ytmusicapi's message only.
"""

from ytmusicapi.navigation import RESPONSIVE_HEADER, SECTION_LIST_ITEM, TAB_CONTENT, TWO_COLUMN_RENDERER, nav

import ytm_artist as artist
from ytm_proto import Fixed, Prefixed, WorkerError, choice, opt_str, req_str, unsent

RATINGS = ("LIKE", "INDIFFERENT", "DISLIKE")
KINDS = ("album", "playlist")
STALE_MESSAGE = "YouTube Music did not take the change: the page it came from is out of date"
TOKEN_MAX = 4096
ARTISTS_MAX = 50

# Where an album or playlist page keeps its header (the 2024 layout).
_HEADER = [*TWO_COLUMN_RENDERER, *TAB_CONTENT, *SECTION_LIST_ITEM, *RESPONSIVE_HEADER]


def _strip_vl(playlist_id):
    return playlist_id[2:] if playlist_id.startswith("VL") else playlist_id


class StaleToken(WorkerError):
    """YouTube did not process a song's library token: UPSTREAM, with
    ``stale: true`` so the shell tells it from any other server error."""

    def __init__(self):
        super().__init__("UPSTREAM", STALE_MESSAGE)

    def as_dict(self):
        return dict(super().as_dict(), stale=True)


def _artist_ids(params):
    """The optional ``artistIds``: channel ids (MPLA stripped, no repeats),
    written back into the request's params for INVALIDATES; None when absent."""
    value = params.get("artistIds")
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > ARTISTS_MAX or not all(
            isinstance(i, str) and i.strip() and len(i) <= 200 for i in value):
        raise WorkerError("BAD_REQUEST", "'artistIds' must be a list of channel ids")
    ids = []
    for channel_id in value:
        channel_id = channel_id.strip()
        channel_id = channel_id[4:] if channel_id.startswith("MPLA") else channel_id
        if channel_id not in ids:
            ids.append(channel_id)
    params["artistIds"] = ids
    return ids


def _like_toggle(button):
    """The header button that saves the page (its endpoints are likeEndpoints on a playlist)."""
    toggle = button.get("toggleButtonRenderer") if isinstance(button, dict) else None
    if not isinstance(toggle, dict):
        return None
    for key in ("defaultServiceEndpoint", "toggledServiceEndpoint"):
        like = nav(toggle, [key, "likeEndpoint", "target", "playlistId"], True)
        if isinstance(like, str) and like:
            return toggle
    return None


def library_toggle(response):
    """True or False when an album or playlist page's header has a library
    toggle (isToggled: in the library), None when it has none (your own
    playlist, an older layout). Matched by its like endpoint, never by
    icon or title."""
    if not isinstance(response, dict):
        return None
    header = nav(response, _HEADER, True)
    buttons = header.get("buttons") if isinstance(header, dict) else None
    for button in buttons if isinstance(buttons, list) else []:
        toggle = _like_toggle(button)
        if toggle is not None:
            return toggle.get("isToggled") is True
    return None


def like_status(saved):
    """A collection header's likeStatus from ``library_toggle``."""
    return None if saved is None else ("LIKE" if saved else "INDIFFERENT")


def _cached_album(worker, browse_id):
    """The disk copy of an album page, or {}."""
    try:
        hit = worker.cache().get("album", {"browseId": browse_id})
    except Exception:  # noqa: BLE001 - an unreadable cache is a miss
        hit = None
    page = hit[0] if hit is not None else None
    return page if isinstance(page, dict) else {}


def _linked_ids(artists):
    return [a["id"] for a in artists or [] if isinstance(a, dict) and isinstance(a.get("id"), str) and a["id"]]


def _album_facts(worker, browse_id, need_playlist):
    """An album's audio playlist (when ``need_playlist``) and its artists'
    channel ids: the disk copy of its page, else get_album for the playlist.
    A failure here is ``unsent``: nothing was asked of YouTube yet."""
    try:
        page = _cached_album(worker, browse_id)
        playlist_id = page.get("playlistId")
        artist_ids = _linked_ids((page.get("header") or {}).get("artists"))
        if need_playlist and not playlist_id:
            data = worker.yt.get_album(browse_id) or {}
            playlist_id = data.get("audioPlaylistId")
            artist_ids = artist_ids or _linked_ids(data.get("artists"))
        if need_playlist and (not isinstance(playlist_id, str) or not playlist_id):
            raise WorkerError("NOT_FOUND", "This album has no playlist to save")
    except Exception as exc:
        raise unsent(exc)
    return playlist_id, artist_ids


def m_rate_collection(worker, params):
    from ytmusicapi.models.content.enums import LikeStatus

    kind = choice(params, "kind", KINDS)
    if kind is None:
        raise WorkerError("BAD_REQUEST", "missing 'kind' (album or playlist)")
    rating = choice(params, "rating", RATINGS)
    if rating is None:
        raise WorkerError("BAD_REQUEST", "missing 'rating'")
    browse_id = opt_str(params, "browseId")
    if kind == "album" and not browse_id:
        raise WorkerError("BAD_REQUEST", "an album needs its 'browseId'")
    playlist_id = opt_str(params, "playlistId")
    if playlist_id is None and kind == "playlist":
        raise WorkerError("BAD_REQUEST", "missing or empty 'playlistId'")
    artist_ids = _artist_ids(params) if kind == "album" else None
    worker.require_auth()
    if playlist_id:
        playlist_id = _strip_vl(playlist_id)
    if kind == "album" and (not playlist_id or not artist_ids):
        found, found_artists = _album_facts(worker, browse_id, not playlist_id)
        # Back into the request's params, so INVALIDATES drops the pages of
        # the playlist and artists it resolved to instead of every one.
        playlist_id = params["playlistId"] = playlist_id or found
        if not artist_ids and found_artists:
            params["artistIds"] = found_artists
    worker.yt.rate_playlist(playlist_id, LikeStatus[rating])
    return {"playlistId": playlist_id, "likeStatus": rating, "kind": kind, "browseId": browse_id}


def m_subscribe(worker, params):
    on = params.get("subscribe")
    if not isinstance(on, bool):
        raise WorkerError("BAD_REQUEST", "'subscribe' must be true or false")
    channel_id = opt_str(params, "channelId")
    page_id = opt_str(params, "pageChannelId")
    if not channel_id and not page_id:
        raise WorkerError("BAD_REQUEST", "missing 'channelId'")
    worker.require_auth()
    if not channel_id:
        try:
            page = artist.cached_or_fetch(worker, page_id, "subscribeId")
        except Exception as exc:
            raise unsent(exc)
        channel_id = (page.get("header") or {}).get("subscribeId") or page_id
    if on:
        worker.yt.subscribe_artist(channel_id)
    else:
        worker.yt.unsubscribe_artists([channel_id])
    return {"channelId": channel_id, "pageChannelId": page_id, "subscribed": on}


def processed(response):
    """Every feedback in a ``feedback`` response was processed (none: not)."""
    replies = response.get("feedbackResponses") if isinstance(response, dict) else None
    if not isinstance(replies, list) or not replies:
        return False
    return all(isinstance(r, dict) and r.get("isProcessed") is True for r in replies)


def m_song_library(worker, params):
    video_id = req_str(params, "videoId")
    token = params.get("token")
    if not isinstance(token, str) or not token.strip() or len(token) > TOKEN_MAX:
        raise WorkerError("BAD_REQUEST", "missing 'token' (the song's library token from a fresh list)")
    _artist_ids(params)
    worker.require_auth()
    if not processed(worker.yt.edit_song_library_status(feedbackTokens=[token])):
        raise StaleToken()
    return {"videoId": video_id}


# A source value no cached entry has: a missing one would drop every entry.
_NONE = "\0none"


class _AlbumPage:
    """The album page a rateCollection changes: its browseId; for a playlist
    a value no cached page has (a missing source would drop every album)."""

    NONE = _NONE

    def __call__(self, params):
        if params.get("kind") != "album":
            return self.NONE
        return params.get("browseId")

    def __repr__(self):
        return "_AlbumPage()"


class _Artists:
    """The artist pages whose "From your library" shelf an album save or a
    song library edit changes: ``prefix`` + each of ``artistIds`` (MPLA: the
    shelf's See all page). Without them every artist page goes (too much
    beats stale) but no See all page (that would be every browsePage); a
    playlist save changes none."""

    def __init__(self, prefix=""):
        self.prefix = prefix

    def __call__(self, params):
        if params.get("kind") == "playlist":
            return _NONE
        ids = params.get("artistIds")
        if not isinstance(ids, list) or not ids:
            return _NONE if self.prefix else None
        return [self.prefix + i for i in ids]

    def __repr__(self):
        return f"_Artists({self.prefix!r})"


METHODS = {"rateCollection": m_rate_collection, "subscribe": m_subscribe, "songLibrary": m_song_library}
CACHEABLE = set()
LANES = {}
INVALIDATES = {
    # The album or playlist page itself, the library's playlists, albums and
    # Recent activity, and the browsePage copies of those pages. An album
    # also brings its songs and artists into the library: Songs, Artists,
    # and its artists' pages ("From your library"). A playlist save keeps
    # album and artist pages (_AlbumPage and _Artists name none for it);
    # the library's lists go for both kinds, since a triple without a
    # source cannot tell them apart.
    "rateCollection": [
        ("album", _AlbumPage(), "browseId"),
        ("playlist", "playlistId", "playlistId"),
        ("libraryPlaylists", None, None),
        ("libraryAlbums", None, None),
        ("libraryLanding", None, None),
        ("librarySongs", None, None),
        ("libraryArtists", None, None),
        ("artist", _Artists(), "channelId"),
        ("browsePage", Prefixed("VL", "playlistId"), "browseId"),
        ("browsePage", Fixed("FEmusic_liked_playlists", "FEmusic_liked_albums", "FEmusic_library_landing",
                             "FEmusic_liked_videos", "FEmusic_library_corpus_track_artists"), "browseId"),
        ("browsePage", _Artists("MPLA"), "browseId"),
    ],
    # The artist page (keyed by the page's channel id, not the subscribe
    # channel), Subscriptions, the library's artists and Recent activity.
    # S8 and S9 add their channel and user pages below.
    "subscribe": [
        ("artist", "pageChannelId", "channelId"),
        ("librarySubscriptions", None, None),
        ("libraryArtists", None, None),
        ("libraryLanding", None, None),
        ("browsePage", Fixed("FEmusic_library_corpus_artists", "FEmusic_library_corpus_track_artists",
                             "FEmusic_library_landing"), "browseId"),
        # S8: a podcast channel's page and the library's channels.
        ("channel", "pageChannelId", "channelId"),
        ("libraryChannels", None, None),
        ("browsePage", Fixed("FEmusic_library_non_music_audio_channels_list"), "browseId"),
        # S9: a profile's page and the library's profiles (ytm_users).
        ("user", "pageChannelId", "channelId"),
        ("libraryProfiles", None, None),
        ("browsePage", Fixed("FEmusic_library_user_profile_channels_list"), "browseId"),
    ],
    # The library's songs, the albums and artists it lists for them, and the
    # song's artists' pages ("From your library").
    "songLibrary": [
        ("librarySongs", None, None),
        ("libraryAlbums", None, None),
        ("libraryArtists", None, None),
        ("artist", _Artists(), "channelId"),
        ("browsePage", Fixed("FEmusic_liked_videos", "FEmusic_liked_albums", "FEmusic_library_corpus_track_artists"),
         "browseId"),
        ("browsePage", _Artists("MPLA"), "browseId"),
    ],
}
RECORDERS = {}
MUTATES = {"rateCollection", "subscribe", "songLibrary"}
