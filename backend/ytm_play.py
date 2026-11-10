"""Play anything: whole collections as track lists, Start radio, Go to album.

- ``tracksFor`` reads every track of an album, playlist, Liked Music, mix or
  artist (at most ``limit``, MAX_TRACKS by default), so the shell can play,
  queue or shuffle it from any list without opening it first. It runs on a
  lane of its own: a 5000-song playlist takes about 50 requests (35 s), and
  page opens must not wait behind it. Play, Play next and Add to queue ask
  for the 500 songs the queue takes; Shuffle reads all of it.
- ``radio`` is YouTube Music's Start radio for a song, an album or
  playlist, an artist or a mix (the watch playlist with ``radio=True``).
- ``albumBrowseId`` finds an album page (``MPRE...``) from its audio
  playlist (``OLAK5uy_...``). ytmusicapi reads it from the playlist's whole
  HTML page, so replies are cached and the shell only asks when an item has
  no album id.

Area module (see AREA_MODULES in ytm.py).
"""

import ytm_normalize as norm
from ytm_proto import WorkerError, choice, opt_int, req_str

MAX_TRACKS = 5000
MIX_TRACKS = 50
KINDS = ("album", "playlist", "liked", "mix", "artist")
# Exactly one of these names what a radio starts from.
RADIO_SOURCES = ("videoId", "playlistId", "mixId", "browseId", "channelId")
ALBUM_PLAYLIST = "OLAK5uy_"


def _strip_vl(playlist_id):
    return playlist_id[2:] if playlist_id.startswith("VL") else playlist_id


def _reply(playlist_id, items, total, complete):
    return {"playlistId": playlist_id, "items": items, "complete": bool(complete), "total": total}


def _cut(reply, limit):
    """At most ``limit`` items; a reply cut short is not complete."""
    if len(reply["items"]) > limit:
        reply["items"] = reply["items"][:limit]
        reply["complete"] = False
    return reply


def _album(worker, browse_id):
    page = norm.collection("album", worker.yt.get_album(browse_id), browse_id)
    return _reply(page["playlistId"], page["tracks"], page["trackCount"], True)


def _playlist(worker, playlist_id, kind="playlist", limit=MAX_TRACKS):
    """A playlist's first ``limit`` tracks (the limit, not None, so a huge
    playlist is not read to its end only to be cut)."""
    data = worker.yt.get_playlist(playlist_id, limit=limit)
    page = norm.collection(kind, data, playlist_id)
    read = len(data.get("tracks") or [])
    items = page["tracks"][:limit]
    # Fewer than the limit means ytmusicapi ran out of pages: that is all of it.
    complete = read < limit or (page["trackCount"] is not None and len(items) >= page["trackCount"])
    return _reply(page["playlistId"], items, page["trackCount"], complete)


def _mix(worker, mix_id, limit=MIX_TRACKS):
    # ytmusicapi's limit is a minimum (it reads whole continuation pages).
    data = worker.yt.get_watch_playlist(playlistId=mix_id, limit=limit)
    items = norm.normalize_list(data.get("tracks"), norm.track, "mix track")[:limit]
    return _reply(data.get("playlistId") or mix_id, items, len(items), True)


def _artist_page(worker, channel_id, needs):
    """The artist's normalized page: the disk-cached copy when it holds the
    id ``needs`` (copies cached before it was read lack it), else a fresh one."""
    try:
        hit = worker.cache().get("artist", {"channelId": channel_id})
    except Exception:  # noqa: BLE001 - an unreadable cache is a miss
        hit = None
    if hit is not None and isinstance(hit[0], dict) and hit[0].get(needs):
        return hit[0]
    return norm.artist_page(channel_id, worker.yt.get_artist(channel_id))


def _artist(worker, channel_id, limit):
    page = _artist_page(worker, channel_id, "songsPlaylistId")
    songs_id = page.get("songsPlaylistId")
    if songs_id:
        return _playlist(worker, _strip_vl(songs_id), limit=limit)
    # No "See all" on the songs shelf: the page's own songs are all there is.
    items = page.get("songs") or []
    return _reply(None, items, len(items), True)


def route(kind, collection_id):
    """``(reader, id)`` for a tracksFor request.

    The id decides before the kind does: a mix (RD..., not RDCLAK or RDPN)
    plays through the watch playlist, Liked Music is LM, an album without an
    MPRE id is its audio playlist, and any other playlist id is a playlist.
    """
    if kind == "liked" or collection_id in ("LM", "VLLM"):
        return "liked", "LM"
    if kind == "artist":
        return "artist", collection_id
    if kind == "album" and collection_id.startswith("MPRE"):
        return "album", collection_id
    playlist_id = _strip_vl(collection_id)
    if norm.is_mix(playlist_id):
        return "mix", playlist_id
    return "playlist", playlist_id


def m_tracks_for(worker, params):
    """``{kind, id, limit?}``: at most ``limit`` tracks (MAX_TRACKS by default
    and at most). ``complete`` is false when the collection has more."""
    kind = choice(params, "kind", KINDS)
    if kind is None:
        raise WorkerError("BAD_REQUEST", "missing 'kind'")
    collection_id = "LM" if kind == "liked" and not params.get("id") else req_str(params, "id")
    limit = opt_int(params, "limit", MAX_TRACKS, high=MAX_TRACKS)
    reader, target = route(kind, collection_id)
    if reader == "liked":
        worker.require_auth()
        return _playlist(worker, "LM", "liked", limit)
    if reader == "album":
        return _cut(_album(worker, target), limit)
    if reader == "artist":
        return _cut(_artist(worker, target, limit), limit)
    if reader == "mix":
        return _cut(_mix(worker, target), limit)
    return _playlist(worker, target, limit=limit)


def _radio_source(params):
    given = [key for key in RADIO_SOURCES if params.get(key) is not None]
    if len(given) != 1:
        raise WorkerError("BAD_REQUEST", f"give exactly one of {', '.join(RADIO_SOURCES)}")
    return given[0], req_str(params, given[0])


def m_radio(worker, params):
    """Start radio: ``{videoId}``, ``{playlistId}`` (a playlist or an album's
    audio playlist), ``{mixId}`` (played as it is), ``{browseId}`` (an album
    page: its audio playlist) or ``{channelId}`` (an artist: its radio mix).
    A song's radio leaves the song itself out."""
    limit = opt_int(params, "limit", 50, high=200)
    key, value = _radio_source(params)
    if key == "videoId":
        data = worker.yt.get_watch_playlist(videoId=value, radio=True, limit=limit)
    elif key == "mixId":
        data = worker.yt.get_watch_playlist(playlistId=_strip_vl(value), limit=limit)
    elif key == "channelId":
        radio_id = _artist_page(worker, value, "radioId").get("radioId")
        if not radio_id:
            raise WorkerError("NOT_FOUND", "This artist has no radio")
        data = worker.yt.get_watch_playlist(playlistId=radio_id, limit=limit)
    else:
        playlist_id = value
        if key == "browseId":
            playlist_id = worker.yt.get_album(value).get("audioPlaylistId")
            if not playlist_id:
                raise WorkerError("NOT_FOUND", "This album has no radio")
        data = worker.yt.get_watch_playlist(playlistId="RDAMPL" + _strip_vl(playlist_id), radio=True, limit=limit)
    items = norm.normalize_list(data.get("tracks"), norm.track, "radio track")
    if key == "videoId":
        # The song itself comes first; the shell already has it (as upNext).
        items = [t for t in items if t["videoId"] != value]
    # ytmusicapi's limit is a minimum (it reads whole continuation pages).
    return {"playlistId": data.get("playlistId"), "items": items[:limit]}


def m_album_browse_id(worker, params):
    playlist_id = _strip_vl(req_str(params, "playlistId"))
    # Any other page would match some unrelated MPRE id in its HTML.
    if not playlist_id.startswith(ALBUM_PLAYLIST):
        raise WorkerError("BAD_REQUEST", "'playlistId' must be an album's audio playlist (OLAK5uy_...)")
    browse_id = worker.yt.get_album_browse_id(playlist_id)
    if not browse_id or not browse_id.startswith("MPRE"):
        raise WorkerError("NOT_FOUND", "No album page found for this playlist")
    return {"playlistId": playlist_id, "browseId": browse_id}


def record_radio(yt, params):
    return yt.get_watch_playlist(videoId=params["videoId"], radio=True, limit=params.get("limit", 50))


METHODS = {"tracksFor": m_tracks_for, "radio": m_radio, "albumBrowseId": m_album_browse_id}
CACHEABLE = {"albumBrowseId"}
# tracksFor has a lane of its own, so a long read (a shuffle of a big
# playlist) never holds up browsePage on the pages lane. albumBrowseId is one
# GET, answered in order like search.
LANES = {"tracksFor": "tracks"}
INVALIDATES = {}
RECORDERS = {"radio": record_radio}
MUTATES = set()
