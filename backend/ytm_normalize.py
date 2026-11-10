"""Turn ytmusicapi responses into the plugin's stable JSON shapes.

ytmusicapi's shapes differ per endpoint (``thumbnails`` vs ``thumbnail``,
``duration_seconds`` vs ``"5:54"`` strings, album tracks without thumbnails,
``artist`` vs ``title``), so the QML side only ever sees these shapes. Every
normalizer returns all documented fields, using ``None`` when a value is
unknown. A single malformed item is skipped and logged rather than failing a
whole response.
"""

import re
import sys

THUMB_SIZE = 120
LARGE_SIZE = 544

LIKE_STATUSES = ("LIKE", "DISLIKE", "INDIFFERENT")
VIDEO_TYPES = (
    "MUSIC_VIDEO_TYPE_OMV",
    "MUSIC_VIDEO_TYPE_UGC",
    "MUSIC_VIDEO_TYPE_OFFICIAL_SOURCE_MUSIC",
)

# Size suffixes on googleusercontent / ggpht image URLs, e.g. "=w60-h60-l90-rj"
# or "=s88-c-k-c0x00ffffff-no-rj". Rewriting them fetches a fitting size.
_SIZE_WH = re.compile(r"=w\d+-h\d+[^/=]*$")
_SIZE_S = re.compile(r"=s\d+[^/=]*$")


def log(message):
    print(f"ytm: {message}", file=sys.stderr)


def resize(url, size):
    if not url:
        return None
    if "googleusercontent.com" in url or "ggpht.com" in url:
        if _SIZE_WH.search(url):
            return _SIZE_WH.sub(f"=w{size}-h{size}-l90-rj", url)
        if _SIZE_S.search(url):
            return _SIZE_S.sub(f"=s{size}", url)
    return url


def thumbs(thumbnails):
    """Return ``(thumb, thumbLarge)`` URLs from a ytmusicapi thumbnail list."""
    items = [t for t in (thumbnails or []) if isinstance(t, dict) and t.get("url")]
    if not items:
        return None, None
    items.sort(key=lambda t: t.get("width") or 0)
    small = next((t for t in items if (t.get("width") or 0) >= THUMB_SIZE), items[-1])
    return resize(small["url"], THUMB_SIZE), resize(items[-1]["url"], LARGE_SIZE)


def parse_duration(value):
    """Seconds from ``"5:54"``, ``"1:02:03"`` or a number; ``None`` if unknown."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    parts = str(value).strip().split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    total = 0
    for number in numbers:
        total = total * 60 + number
    return total


def to_int(value):
    """``25``, ``"25"``, ``"1,234 songs"`` -> int; ``None`` otherwise."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"\d[\d,.]*", str(value))
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", "").replace(".", ""))
    except ValueError:
        return None


def artists(value):
    out = []
    for artist in value or []:
        if isinstance(artist, dict) and artist.get("name"):
            out.append({"name": artist["name"], "id": artist.get("id")})
        elif isinstance(artist, str) and artist:
            out.append({"name": artist, "id": None})
    return out


def artist_text(artist_list):
    return ", ".join(artist["name"] for artist in artist_list)


def like_status(value):
    return value if value in LIKE_STATUSES else None


def track_kind(item):
    if item.get("resultType") == "video" or item.get("videoType") in VIDEO_TYPES:
        return "video"
    return "song"


def _album_ref(value):
    if isinstance(value, dict) and value.get("name"):
        return {"name": value["name"], "id": value.get("id")}
    if isinstance(value, str) and value:
        return {"name": value, "id": None}
    return None


def _pair(value, first, second):
    """``{first, second}`` of a token dict ytmusicapi read from a menu, or None."""
    if not isinstance(value, dict) or not (value.get(first) or value.get(second)):
        return None
    return {first: value.get(first), second: value.get(second)}


def _flag(value):
    return value if isinstance(value, bool) else None


def _vote_status(value):
    """A collaborative playlist entry's votes: ``{votes, status}`` or None."""
    if not isinstance(value, dict):
        return None
    status = getattr(value.get("status"), "value", value.get("status"))
    return {"votes": to_int(value.get("netVoteValue")), "status": status if isinstance(status, str) else None}


def track(item, album=None, album_thumbs=None):
    """A playable song or video. ``album``/``album_thumbs`` fill gaps in album tracks.

    S3 keeps what the row menus of later slices act on: ``trackNumber`` (album
    pages), ``inLibrary`` and ``pinned`` (Listen again) with their feedback
    ``libraryTokens {add, remove}`` and ``listenAgainTokens {pin, unpin}``,
    ``historyToken`` (remove from history) and a collaborative playlist's
    ``voteStatus``. Tokens are account-bound and in fresh replies only: the
    disk cache drops them (ytm_cache.strip_tokens), so does the recorder's
    scrub, and the shell keeps them out of what it saves
    (Model.withoutTokens). A slice that acts on a token reads it from a
    fresh, non-cached reply.
    """
    video_id = item.get("videoId")
    if not video_id:
        raise ValueError("track without videoId")
    artist_list = artists(item.get("artists"))
    album_value = _album_ref(item.get("album"))
    if album and (album_value is None or album_value["id"] is None):
        album_value = album
    thumb, thumb_large = thumbs(item.get("thumbnails") or item.get("thumbnail"))
    if thumb is None and album_thumbs:
        thumb, thumb_large = album_thumbs
    duration = item.get("duration_seconds")
    if duration is None:
        duration = parse_duration(item.get("duration") or item.get("length"))
    number = item.get("trackNumber")
    return {
        "type": "track",
        "videoId": video_id,
        "title": item.get("title") or "",
        "artists": artist_list,
        "artistText": artist_text(artist_list),
        "album": album_value,
        "durationSec": duration,
        "thumb": thumb,
        "thumbLarge": thumb_large,
        "explicit": bool(item.get("isExplicit")),
        "likeStatus": like_status(item.get("likeStatus")),
        "setVideoId": item.get("setVideoId"),
        "available": item.get("isAvailable", True) is not False,
        "kind": track_kind(item),
        "played": item.get("played"),
        "trackNumber": number if isinstance(number, int) and not isinstance(number, bool) else None,
        "inLibrary": _flag(item.get("inLibrary")),
        "libraryTokens": _pair(item.get("feedbackTokens"), "add", "remove"),
        "pinned": _flag(item.get("pinnedToListenAgain")),
        "listenAgainTokens": _pair(item.get("listenAgainFeedbackTokens"), "pin", "unpin"),
        "historyToken": item.get("feedbackToken") if isinstance(item.get("feedbackToken"), str) else None,
        "voteStatus": _vote_status(item.get("communityVoteStatus")),
    }


def album_track(item, album_ref, album_thumbs):
    """A track on an album page. These are the album's songs even when YouTube
    tags them MUSIC_VIDEO_TYPE_OMV (it does for whole albums that have videos)."""
    value = track(item, album=album_ref, album_thumbs=album_thumbs)
    value["kind"] = "song"
    return value


def album(item, fallback_artists=None):
    browse_id = item.get("browseId")
    if not browse_id:
        raise ValueError("album without browseId")
    artist_list = artists(item.get("artists")) or list(fallback_artists or [])
    thumb, thumb_large = thumbs(item.get("thumbnails"))
    return {
        "type": "album",
        "browseId": browse_id,
        "playlistId": item.get("playlistId") or item.get("audioPlaylistId"),
        "title": item.get("title") or "",
        "artists": artist_list,
        "artistText": artist_text(artist_list),
        "year": str(item["year"]) if item.get("year") else None,
        "albumType": item.get("type"),
        "explicit": bool(item.get("isExplicit")),
        "thumb": thumb,
        "thumbLarge": thumb_large,
    }


def _author_name(value):
    if isinstance(value, list):
        return artist_text(artists(value)) or None
    if isinstance(value, dict):
        return value.get("name")
    return value or None


def playlist(item):
    playlist_id = item.get("playlistId") or item.get("browseId")
    if not playlist_id:
        raise ValueError("playlist without id")
    if playlist_id.startswith("VL"):
        playlist_id = playlist_id[2:]
    thumb, thumb_large = thumbs(item.get("thumbnails"))
    return {
        "type": "playlist",
        "playlistId": playlist_id,
        "title": item.get("title") or "",
        "author": _author_name(item.get("author")),
        "count": to_int(item.get("count") if item.get("count") is not None else item.get("itemCount")),
        "owned": bool(item.get("owned")),
        # The served subtitle ("Playlist • YouTube Music"), shown when the
        # playlist names no author or count.
        "description": item.get("description") if isinstance(item.get("description"), str) else None,
        "thumb": thumb,
        "thumbLarge": thumb_large,
    }


def strip_mpla(browse_id):
    """A library artist's browseId (``MPLAUC...``) as the artist's channel id."""
    if isinstance(browse_id, str) and browse_id.startswith("MPLA"):
        return browse_id[4:]
    return browse_id


def is_mix(playlist_id):
    """Radio-style playlists (``RD...``) that play through the watch playlist.

    ``RDCLAK`` (curated) and ``RDPN`` (New episodes) are ordinary pages.
    """
    pid = str(playlist_id or "")
    if pid.startswith("VL"):
        pid = pid[2:]
    return pid.startswith("RD") and not pid.startswith(("RDCLAK", "RDPN"))


def artist(item):
    # A "Top result" artist carries its name and id only in artists[0].
    listed = artists(item.get("artists"))
    first = listed[0] if listed else {"name": None, "id": None}
    channel_id = strip_mpla(item.get("browseId") or item.get("channelId") or first["id"])
    if not channel_id:
        raise ValueError("artist without channel id")
    thumb, thumb_large = thumbs(item.get("thumbnails"))
    return {
        "type": "artist",
        "channelId": channel_id,
        "name": item.get("artist") or item.get("title") or item.get("name") or first["name"] or "",
        "subtitle": item.get("subscribers") or item.get("monthlyListeners"),
        "thumb": thumb,
        "thumbLarge": thumb_large,
    }


def _channel_like(kind, item):
    channel_id = strip_mpla(item.get("browseId") or item.get("channelId"))
    if not channel_id:
        raise ValueError(f"{kind} without channel id")
    thumb, thumb_large = thumbs(item.get("thumbnails"))
    return {
        "type": kind,
        "channelId": channel_id,
        "name": item.get("name") or item.get("title") or item.get("artist") or "",
        "subtitle": item.get("subscribers") or item.get("subtitle"),
        "thumb": thumb,
        "thumbLarge": thumb_large,
    }


def profile(item):
    """A user's channel (MUSIC_PAGE_TYPE_USER_CHANNEL): playlists and videos, no discography."""
    return _channel_like("profile", item)


def channel(item):
    """A podcast or library channel."""
    return _channel_like("channel", item)


def podcast(item):
    browse_id = item.get("browseId") or (f"MPSP{item['podcastId']}" if item.get("podcastId") else None)
    if not browse_id:
        raise ValueError("podcast without browseId")
    author = _album_ref(item.get("channel") or item.get("author"))
    thumb, thumb_large = thumbs(item.get("thumbnails"))
    return {
        "type": "podcast",
        "browseId": browse_id,
        "podcastId": item.get("podcastId") or (browse_id[4:] if browse_id.startswith("MPSP") else None),
        "title": item.get("title") or "",
        "author": author,
        "authorText": author["name"] if author else None,
        "thumb": thumb,
        "thumbLarge": thumb_large,
    }


def episode(item):
    """A podcast episode: a track of kind ``episode`` plus its page and podcast."""
    value = track(item)
    podcast_ref = _album_ref(item.get("podcast") or item.get("author"))
    if not value["artists"] and podcast_ref:
        value["artists"] = [dict(podcast_ref)]
        value["artistText"] = podcast_ref["name"]
    value.update({
        "kind": "episode",
        "browseId": item.get("browseId"),
        "date": item.get("date"),
        "podcast": podcast_ref,
    })
    return value


def search_item(item):
    """One search result (ytmusicapi's shape, typed by ``resultType``), or None.

    S7: podcasts, episodes (tracks of kind ``episode``), profiles (with their
    ``handle``) and stations (a mix playlist) too; an artist keeps YouTube's
    ``shuffleId`` and ``radioId`` mixes, which the row menu plays.
    """
    kind = item.get("resultType")
    if kind in ("song", "video"):
        return track(item)
    if kind == "album":
        return album(item)
    if kind == "playlist":
        return playlist(item)
    if kind == "artist":
        value = artist(item)
        value["shuffleId"] = item.get("shuffleId") or None
        value["radioId"] = item.get("radioId") or None
        return value
    if kind == "podcast":
        value = podcast(item)
        value["subtitle"] = "Podcast" + (f" \u2022 {value['authorText']}" if value["authorText"] else "")
        return value
    if kind == "episode":
        return episode(item)
    if kind == "profile":
        # ytmusicapi reads the display name as ``title`` and the @handle as ``name``.
        handle = item.get("name") if isinstance(item.get("name"), str) and item["name"].startswith("@") else None
        value = profile({"browseId": item.get("browseId"), "name": item.get("title") or item.get("name"),
                         "subtitle": handle, "thumbnails": item.get("thumbnails")})
        value["handle"] = handle
        return value
    if kind == "station":
        return playlist({"playlistId": item.get("playlistId"), "title": item.get("title"),
                         "author": item.get("author"), "thumbnails": item.get("thumbnails")})
    return None


def home_item(item):
    browse_id = str(item.get("browseId") or "")
    if item.get("videoId"):
        return track(item)
    if browse_id.startswith("MPRE"):
        return album(item)
    if browse_id.startswith("UC"):
        return artist(item)
    if item.get("playlistId"):
        return playlist(item)
    return None


def normalize_list(items, fn, what="item"):
    out = []
    for item in items or []:
        try:
            value = fn(item)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            log(f"skipped malformed {what}: {type(exc).__name__}: {exc}")
            continue
        if value is not None:
            out.append(value)
    return out


SEARCH_GROUPS = {"song": "songs", "video": "videos", "album": "albums", "playlist": "playlists",
                 "station": "playlists", "artist": "artists", "podcast": "podcasts", "episode": "episodes",
                 "profile": "profiles"}
# Every group key; each is also the search filter its Show all row sets.
SEARCH_GROUP_KEYS = ("songs", "videos", "albums", "playlists", "artists", "podcasts", "episodes", "profiles")


def search_grouped(results, top=None):
    """Unfiltered search: the top result plus one list per kind.

    ``results`` are search results in served order, ``top`` the top result
    card's (its item, then the rows it holds) or a podcast's lead shelf, both
    in ytmusicapi's shape. The top section comes only from what the search
    module read as the top result (S7: never from a position or the English
    "Top result" label), so without ``top`` it is empty. ``order``
    lists the groups in the order YouTube served their first result.
    """
    groups = {"top": normalize_list(top, search_item, "top result")}
    for key in SEARCH_GROUP_KEYS:
        groups[key] = []
    order = []
    for raw in results or []:
        key = SEARCH_GROUPS.get(raw.get("resultType")) if isinstance(raw, dict) else None
        items = normalize_list([raw], search_item, "search result") if key else []
        if not items:
            continue
        groups[key].append(items[0])
        if key not in order:
            order.append(key)
    groups["order"] = order
    # When the top result is an artist, YouTube Music's card lists a few of
    # their popular songs with only title and plays; ytmusicapi returns those
    # without artists. They belong to the top-result artist.
    top_artist = next((item for item in groups["top"] if item["type"] == "artist"), None)
    if top_artist and top_artist["name"]:
        credit = [{"name": top_artist["name"], "id": top_artist["channelId"]}]
        for song in groups["top"][1:] + groups["songs"]:
            if song["type"] == "track" and not song["artists"]:
                song["artists"] = list(credit)
                song["artistText"] = artist_text(credit)
    return groups


def home(shelves):
    out = []
    for shelf in shelves or []:
        items = normalize_list(shelf.get("contents"), home_item, "home item")
        if items:
            out.append({"title": shelf.get("title") or "", "items": items})
    return out


def _join(*parts):
    return " • ".join(str(part) for part in parts if part)


def truncated(read, track_count, limit, more=None):
    """True when a playlist or Liked Music has more songs than were read.

    ``read`` is how many ytmusicapi returned for ``limit`` (None: all of
    them). It reads whole pages of 100 and stops once it has ``limit``, so
    300 songs for a limit of 200 may or may not be all there is. ``more``
    says whether a continuation was left after the last page (None when
    unknown); without it the counts decide. Never by the counts alone in the
    other direction: Liked Music counts 416 songs and serves 377.
    """
    if limit is None:
        return False
    within = track_count is None or read < track_count
    if more is not None:
        return bool(more) and within
    return read >= limit and within


# Section titles when the served ones are not known (a recorded response).
COLLECTION_TITLES = {"otherVersions": "Other versions", "releases": "More releases", "related": "Related playlists"}


def _collection_header(kind, data):
    """Every header field for every kind (None when the kind has none)."""
    owned = bool(data.get("owned"))
    author = _album_ref(data.get("author")) if kind in ("playlist", "liked") else None
    collaborators = data.get("collaborators")
    return {
        "description": data.get("description") if isinstance(data.get("description"), str) and data["description"].strip() else None,
        # ytmusicapi says PUBLIC for any playlist that is not yours.
        "privacy": data.get("privacy") if owned and kind == "playlist" else None,
        "year": str(data["year"]) if data.get("year") else None,
        "views": to_int(data.get("views")),
        "duration": data.get("duration") if isinstance(data.get("duration"), str) else None,
        "durationSec": to_int(data.get("duration_seconds")) or None,
        "author": author,
        "collaborators": collaborators.get("text") if isinstance(collaborators, dict) else None,
        "artists": artists(data.get("artists")) if kind == "album" else [],
        "likeStatus": like_status(data.get("likeStatus")) if kind == "album" else None,
        "audioPlaylistId": data.get("audioPlaylistId") if kind == "album" else None,
        "explicit": bool(data.get("isExplicit")) if kind == "album" else False,
        "type": data.get("type") if kind == "album" else None,
    }


def _collection_sections(kind, data, titles):
    """The shelves under the songs: an album's other versions and the served
    recommendations, a playlist's related playlists."""
    names = dict(COLLECTION_TITLES, **{k: v for k, v in (titles or {}).items() if v})
    shelves = []
    if kind == "album":
        shelves = [("otherVersions", data.get("other_versions"), album, "album"),
                   ("releases", data.get("related_recommendations"), album, "album")]
    elif kind == "playlist":
        shelves = [("related", data.get("related"), playlist, "playlist")]
    out = []
    for key, items, fn, what in shelves:
        values = normalize_list(items, fn, f"{key} {what}")
        if values:
            out.append(section(names[key], values, layout="carousel", key=key))
    return out


def collection(kind, data, collection_id=None, limit=None, more=None, titles=None):
    """An album, playlist, mix or Liked Music with its tracks.

    ``limit`` and ``more`` describe the read (see ``truncated``); a list read
    whole (``limit`` None) is never truncated, and its ``unavailable`` counts
    the songs the header counts that did not come back. ``header`` has the
    page's details, ``sections`` the shelves under the songs (``titles``:
    served titles by section key). ``capped`` is set by the caller when a
    whole read stopped at its cap.
    """
    thumb, thumb_large = thumbs(data.get("thumbnails"))
    collection_id = collection_id or data.get("id")
    title = data.get("title") or ("Liked Music" if kind == "liked" else "")
    if kind == "album":
        ref = {"name": title, "id": collection_id}
        tracks = normalize_list(data.get("tracks"), lambda t: album_track(t, ref, (thumb, thumb_large)), "album track")
        album_artists = artists(data.get("artists"))
        subtitle = _join(data.get("type"), artist_text(album_artists), data.get("year"))
        playlist_id = data.get("audioPlaylistId")
    else:
        tracks = normalize_list(data.get("tracks"), track, f"{kind} track")
        count_text = f"{to_int(data.get('trackCount')) or len(tracks)} songs"
        if kind == "mix":
            subtitle = _join("Mix", count_text)
        else:
            subtitle = count_text if kind == "liked" else _join(_author_name(data.get("author")), count_text)
        playlist_id = "LM" if kind == "liked" else collection_id
    track_count = to_int(data.get("trackCount"))
    if track_count is None:
        track_count = len(tracks)
    owned = bool(data.get("owned"))
    cut = truncated(len(data.get("tracks") or []), to_int(data.get("trackCount")), limit, more)
    return {
        "type": "collection",
        "kind": kind,
        "id": collection_id,
        "playlistId": playlist_id,
        "title": title,
        "subtitle": subtitle,
        "owned": owned,
        "editable": owned and kind == "playlist",
        "trackCount": track_count,
        "truncated": cut,
        "capped": False,
        "unavailable": None if cut else max(0, track_count - len(tracks)),
        "thumb": thumb,
        "thumbLarge": thumb_large,
        "header": _collection_header(kind, data),
        "tracks": tracks,
        "sections": _collection_sections(kind, data, titles),
    }


def artist_radio_id(data):
    """The artist's radio mix (RDEM...). ytmusicapi reads it from the radio
    button's watchEndpoint only, and some artists' button (Radiohead's,
    signed in) is a watchPlaylistEndpoint; the radio and shuffle mixes share
    the artist's suffix (RDEM... / RDAO...)."""
    radio_id = data.get("radioId")
    shuffle_id = data.get("shuffleId") or ""
    if not radio_id and shuffle_id.startswith("RDAO"):
        radio_id = "RDEM" + shuffle_id[4:]
    return radio_id


def song(data):
    """A track from ``get_song`` (player response ``videoDetails``)."""
    details = data.get("videoDetails") or {}
    item = {
        "videoId": details.get("videoId"),
        "title": details.get("title"),
        "artists": [{"name": details.get("author"), "id": details.get("channelId")}] if details.get("author") else [],
        "duration_seconds": to_int(details.get("lengthSeconds")),
        "thumbnails": (details.get("thumbnail") or {}).get("thumbnails"),
        "videoType": details.get("musicVideoType"),
    }
    return track(item)


def account(info):
    return {
        "name": info.get("accountName"),
        "handle": info.get("channelHandle"),
        "photo": info.get("accountPhotoUrl"),
    }


# ---- pages (protocol v2) -------------------------------------------------------
# A page is what a "See all" or any other destination shows: a header and
# sections of normalized items. ``cursor`` pages on (opaque, never cached);
# ``hasMore`` stays true on a disk-cached first page whose cursor was stripped.


def section(title, items, more=None, play=None, layout=None, key=None):
    """One shelf: ``more`` is its See all endpoint ``{browseId, params, pageType}``,
    ``play`` its Play all endpoint ``{videoId, playlistId, params}``, ``key``
    what the shelf holds when the method knows it (an artist page's
    ``songs``, ``albums``, ...; never read from its title)."""
    return {
        "type": "section",
        "title": title,
        "items": list(items or []),
        "more": more,
        "play": play,
        "layout": layout,
        "key": key,
    }


def more(title, endpoint, count=None):
    """A selectable "See all" row that opens ``endpoint`` (``{browseId, params, pageType}``)."""
    endpoint = endpoint or {}
    return {
        "type": "more",
        "title": title or "",
        "endpoint": {"browseId": endpoint.get("browseId"), "params": endpoint.get("params"),
                     "pageType": endpoint.get("pageType")},
        "count": count,
    }


def page(kind, page_id, title, subtitle=None, thumbnails=None, sections=None, cursor=None, header=None,
         orders=None, order=None, round=False, message=None):
    thumb, thumb_large = thumbs(thumbnails)
    return {
        "type": "page",
        "v": 2,
        "kind": kind,
        "id": page_id,
        "title": title or "",
        "subtitle": subtitle,
        "thumb": thumb,
        "thumbLarge": thumb_large,
        "round": bool(round),
        "header": header or {},
        "sections": list(sections or []),
        "cursor": cursor,
        "hasMore": cursor is not None,
        "orders": list(orders or []),
        "order": order,
        "message": message,
    }
