"""Now playing: what the web's Lyrics and Related tabs show, plus song credits.

- ``nowContext`` reads the watch playlist of the song playing once (one
  'next' request) and keeps what the Now tab needs from it: the like status,
  the Lyrics and Related tab ids, the song/video twin (``counterpart``), the
  queue header's "Playing from", its tuner chips and YouTube's own up-next
  list. ``likeStatus`` is a thin alias of it (the shell's refresh after an
  unsure rating).
- ``upNext`` (autoplay, S12) plays a finite context (an album, a playlist,
  Liked Music) on as its radio and a radio as it is; ``queueFrom`` is the
  queue a tuner chip (All, Discover, Popular...) plays. Their songs, and
  nowContext's, carry the explicit badge the responses show (G4).
- ``lyrics`` asks the signed-out client for time-synced lyrics (they come
  from the Android client context, which YouTube rejects with HTTP 400 for a
  browser-cookie session) and falls back to the account's plain lyrics
  (kept out of the disk cache when the signed-out request failed, rather
  than found no timestamps).
- ``related`` is the Related tab as a page of sections; ``credits`` the
  song credits dialog as a page with one section per role.

Area module (see AREA_MODULES in ytm.py).
"""

import os

from ytmusicapi.navigation import nav
from ytmusicapi.parsers.watch import parse_watch_playlist

import ytm_collections as collections
import ytm_normalize as norm
import ytm_raw as raw
from ytm_auth import anonymous_client
from ytm_proto import WorkerError, opt_bool, opt_int, opt_str, req_str

# get_watch_playlist's limit is a minimum (it reads continuation pages until
# it has that many): 1 keeps nowContext to the first response, which holds
# the whole first page of the queue (50 songs for a song's radio).
CONTEXT_LIMIT = 1
WATCH_NEXT = ["contents", "singleColumnMusicWatchNextResultsRenderer", "tabbedRenderer",
              "watchNextTabbedResultsRenderer"]
CREDITS_DIALOG = ["onResponseReceivedActions", 0, "openPopupAction", "popup", "dismissableDialogRenderer"]
LYRICS_FOOTER = ["contents", "sectionListRenderer", "contents", 0, "musicDescriptionShelfRenderer", "footer"]
PERSON_ICON = "\U000f0004"  # md-account: a credited name without a page
ABOUT_ICON = "\U000f02fd"  # md-information_outline


def _debug(message):
    if os.environ.get("YTM_DEBUG") == "1":
        norm.log(message)


# ---- nowContext ------------------------------------------------------------------


def _queue_renderer(response):
    """The musicQueueRenderer of a raw 'next' response (None when absent)."""
    for tab in nav(response, [*WATCH_NEXT, "tabs"], True) or []:
        queue = nav(tab, ["tabRenderer", "content", "musicQueueRenderer"], True)
        if isinstance(queue, dict):
            return queue
    return None


def _chips(queue):
    """The queue's tuner chips (All, Popular, Discover...): what each plays."""
    out = []
    for chip in nav(queue, ["subHeaderChipCloud", "chipCloudRenderer", "chips"], True) or []:
        data = chip.get("chipCloudChipRenderer") if isinstance(chip, dict) else None
        if not isinstance(data, dict):
            continue
        watch = nav(data, ["navigationEndpoint", "queueUpdateCommand", "fetchContentsCommand", "watchEndpoint"], True)
        if not isinstance(watch, dict) or not watch.get("playlistId"):
            continue
        out.append({"title": raw.text(data.get("text")) or "", "playlistId": watch["playlistId"],
                    "params": watch.get("params"), "selected": data.get("isSelected") is True})
    return out


def _counterpart(item):
    """``{videoId, title, kind}`` of a track's song/video twin, or None."""
    if not isinstance(item, dict) or not item.get("videoId"):
        return None
    return {"videoId": item["videoId"], "title": item.get("title") or "", "kind": norm.track_kind(item)}


def _with_counterpart(item):
    value = norm.track(item)
    value["counterpart"] = _counterpart(item.get("counterpart"))
    return value


def _current(tracks, video_id):
    """``(entry, current, twin)`` for ``video_id``: the queue entry that is
    this song (itself, or the entry it is the song/video twin of), the
    watch track of ``video_id`` and its twin. Nothing matches: all None."""
    for track in tracks:
        if track.get("videoId") == video_id:
            return track, track, _counterpart(track.get("counterpart"))
    for track in tracks:
        twin = track.get("counterpart")
        if isinstance(twin, dict) and twin.get("videoId") == video_id:
            return track, twin, _counterpart(track)
    return None, None, None


def now_context(worker, video_id, playlist_id=None):
    with raw.capture(("next",)) as cap:
        data = worker.yt.get_watch_playlist(videoId=video_id, playlistId=playlist_id, limit=CONTEXT_LIMIT)
    # G4: YouTube's up-next songs with their explicit badges.
    tracks = collections.mark_explicit([t for t in data.get("tracks") or [] if isinstance(t, dict)], cap.all("next"))
    entry, current, counterpart = _current(tracks, video_id)
    queue = _queue_renderer(cap.first("next"))
    items = norm.normalize_list([t for t in tracks if t is not entry and t.get("videoId") != video_id],
                                _with_counterpart, "up-next track")
    return {
        "videoId": video_id,
        "playlistId": data.get("playlistId"),
        "likeStatus": norm.like_status((current or {}).get("likeStatus")),
        "lyricsId": data.get("lyrics"),
        "relatedId": data.get("related"),
        "counterpart": counterpart,
        "playingFrom": raw.text(nav(queue, ["header", "musicQueueHeaderRenderer", "subtitle"], True)),
        "chips": _chips(queue),
        "items": items,
    }


def m_now_context(worker, params):
    """``{videoId, playlistId?}``: one 'next' request. Never cached (the like
    status must be current); the shell keeps a short memo per video.

    Signed in, the session is checked first (once per worker, about 0.1 s):
    an expired one answers AUTH_EXPIRED, so the shell marks it and tells the
    user, instead of a reply that looks signed out. Later requests then run
    signed out (the worker's auth is 'expired')."""
    video_id, playlist_id = req_str(params, "videoId"), opt_str(params, "playlistId")
    if worker.auth == "ok":
        worker.require_auth()
    return now_context(worker, video_id, playlist_id)


def m_like_status(worker, params):
    worker.require_auth()
    context = now_context(worker, req_str(params, "videoId"))
    return {"videoId": context["videoId"], "likeStatus": context["likeStatus"]}


# ---- up next (S12) -------------------------------------------------------------------
# Autoplay's lists. A finite context (an album's audio playlist, a playlist,
# Liked Music, a curated RDCLAK list) goes on as its radio, RDAMPL + id: its
# own window would only hold the songs already queued, which the shell's
# dedupe throws away, and autoplay would stall. The song goes with it:
# YouTube still plays the list's radio ("OK Computer Mix"), and for a list it
# has no radio for (a podcast's, an id that does not exist) it answers a
# "Your Queue" seeded by the song instead of one seeded by the account alone
# (it never answers "No content" for these). A radio (any other RD...) goes
# on as it is; no context is the song's own radio.

RADIO_PREFIX = "RDAMPL"
QUEUE_PANEL = ["content", "playlistPanelRenderer"]


def playing_from(response):
    """The queue header's "Playing from" text of a raw 'next' response (None
    without one)."""
    if not isinstance(response, dict):
        return None
    return raw.text(nav(_queue_renderer(response), ["header", "musicQueueHeaderRenderer", "subtitle"], True))


def up_next_source(video_id, playlist_id=None):
    """``(videoId, playlistId, radio)``: what get_watch_playlist is asked for
    autoplay after ``video_id`` in the context ``playlist_id``."""
    if not playlist_id:
        return video_id, None, True
    pid = playlist_id[2:] if playlist_id.startswith("VL") else playlist_id
    if norm.is_mix(pid):
        return video_id, pid, False
    return video_id, RADIO_PREFIX + pid, True


def _upcoming(tracks, video_id):
    """Watch tracks without ``video_id`` (nor the entry it is the twin of),
    normalized, each with its own ``counterpart``."""
    entry = _current(tracks, video_id)[0]
    rest = [t for t in tracks if isinstance(t, dict) and t is not entry and t.get("videoId") != video_id]
    return norm.normalize_list(rest, _with_counterpart, "up-next track")


def _watch(worker, video_id, playlist_id, radio, limit):
    """``(data, responses)``: the watch playlist and every raw 'next'
    response read for it (G4: continuation pages carry badges too)."""
    with raw.capture(("next",)) as cap:
        data = worker.yt.get_watch_playlist(videoId=video_id, playlistId=playlist_id, radio=radio, limit=limit)
    return data, cap.all("next")


def m_up_next(worker, params):
    """``{videoId, playlistId?, limit?}``: what autoplay appends after
    ``videoId`` (at least ``limit`` songs, 25 by default, at most 100), without
    it. ``playlistId`` is what plays (the shell's playContext)."""
    video_id = req_str(params, "videoId")
    limit = opt_int(params, "limit", 25, high=100)
    seed, playlist_id, radio = up_next_source(video_id, opt_str(params, "playlistId"))
    data, responses = _watch(worker, seed, playlist_id, radio, limit)
    tracks = collections.mark_explicit([t for t in data.get("tracks") or [] if isinstance(t, dict)], responses)  # G4
    return {"playlistId": data.get("playlistId"), "playingFrom": playing_from(responses[0] if responses else None),
            "items": _upcoming(tracks, video_id)}


def m_queue_from(worker, params):
    """``{videoId, playlistId, params?}`` (a nowContext chip): the queue that
    chip plays from ``videoId`` on, one raw 'next' request as the web sends
    it, without the song itself. Not cached."""
    video_id, playlist_id = req_str(params, "videoId"), req_str(params, "playlistId")
    body = {"videoId": video_id, "playlistId": playlist_id, "isAudioOnly": True,
            "enablePersistentPlaylistPanel": True}
    chip_params = opt_str(params, "params")
    if chip_params:
        body["params"] = chip_params
    response = worker.yt._send_request("next", body)
    panel = nav(_queue_renderer(response), QUEUE_PANEL, True)
    if not isinstance(panel, dict):
        raise WorkerError("UPSTREAM", "YouTube Music sent no queue for this chip")
    tracks = collections.mark_explicit(parse_watch_playlist(panel.get("contents") or []), [response])  # G4
    return {"playlistId": panel.get("playlistId") or playlist_id, "playingFrom": playing_from(response),
            "items": _upcoming(tracks, video_id)}


# ---- lyrics --------------------------------------------------------------------------


def _no_lyrics():
    return {"text": None, "source": None, "lines": None}


def _timed(browse_id):
    """``(lyrics, answered)``: time-synced lyrics through the signed-out
    client (None when there are none), and whether YouTube answered.

    A song without timestamps is an answer: the response arrived and has no
    timed lines (ytmusicapi then fails to parse it, KeyError 'cueRange', or
    says hasTimestamps false). A failed client or request (network, HTTP 429
    or 5xx, a rejected Android client, a body that is not JSON) is not, and
    asking again later may bring the synced lines."""
    try:
        yt = raw.install(anonymous_client())
        with raw.capture(("browse",)) as cap:
            try:
                data = yt.get_lyrics(browse_id, timestamps=True)
            except (KeyError, TypeError, IndexError, AttributeError, ValueError):
                if cap.first("browse") is None:
                    raise  # nothing arrived: the request itself failed
                data = None
    except Exception as exc:  # noqa: BLE001 - any failure falls back to plain lyrics
        _debug(f"timed lyrics unavailable ({type(exc).__name__}); asking for plain lyrics")
        return None, False
    if not isinstance(data, dict) or data.get("hasTimestamps") is not True:
        return None, True
    lines = []
    for line in data.get("lyrics") or []:
        start, end = getattr(line, "start_time", None), getattr(line, "end_time", None)
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        lines.append({"start": start / 1000, "end": end / 1000, "text": getattr(line, "text", "") or ""})
    if not lines:
        return None, True
    return {"text": "\n".join(line["text"] for line in lines), "source": data.get("source"), "lines": lines}, True


def _plain(yt, browse_id):
    with raw.capture(("browse",)) as cap:
        data = yt.get_lyrics(browse_id)
    if not isinstance(data, dict) or not isinstance(data.get("lyrics"), str):
        return _no_lyrics()
    # ytmusicapi 1.12.2 looks for the source in the wrong place: it is the
    # description shelf's footer.
    source = data.get("source") or raw.text(nav(cap.first("browse"), LYRICS_FOOTER, True))
    return {"text": data["lyrics"], "source": source, "lines": None}


def m_lyrics(worker, params):
    """``{browseId, timed?}`` (the nowContext ``lyricsId``): ``{text, source,
    lines}``, ``lines`` ``[{start, end, text}]`` in seconds for synced lyrics,
    else null. No browseId (a song without lyrics) answers ``text: null``."""
    browse_id = opt_str(params, "browseId")
    if browse_id is None:
        return _no_lyrics()
    if not browse_id.startswith("MPLY"):
        raise WorkerError("BAD_REQUEST", "'browseId' must be a lyrics id (MPLY...)")
    if opt_bool(params, "timed"):
        found, answered = _timed(browse_id)
        if found is not None:
            return found
        if not answered:
            # The plain lyrics stand in for this request only: cached under
            # the timed key they would hide the synced ones for days.
            worker.skip_store()
    return _plain(worker.yt, browse_id)


# ---- related -------------------------------------------------------------------------


def _send(call):
    """``call()`` inside a raw capture of 'browse'; the raw response even when
    ytmusicapi's own parser of it fails (the rows are read from the raw)."""
    with raw.capture(("browse",)) as cap:
        try:
            call()
        except (KeyError, TypeError, IndexError, AttributeError, ValueError):
            if cap.first("browse") is None:
                raise
    response = cap.first("browse")
    if response is None:
        raise WorkerError("UPSTREAM", "YouTube Music sent nothing for this page")
    return response


def related_sections(response):
    """The Related tab's rows as sections; "About the artist" is a text section."""
    sections = []
    for row in nav(response, ["contents", "sectionListRenderer", "contents"], True) or []:
        key, data = raw.unwrap(row)
        if key == "musicDescriptionShelfRenderer" and isinstance(data, dict):
            text = raw.text(data.get("description"))
            if text:
                sections.append(norm.section(raw.text(data.get("header")),
                                             [{"type": "text", "title": text, "icon": ABOUT_ICON}],
                                             layout="description"))
            continue
        parsed = raw.parse_row(row)
        if parsed["layout"] is not None:
            sections.append(norm.section(parsed["title"], parsed["items"], parsed["more"], parsed["play"],
                                         parsed["layout"]))
    return sections


def m_related(worker, params):
    """``{browseId}`` (the nowContext ``relatedId``, MPTR...): a page."""
    browse_id = req_str(params, "browseId")
    response = _send(lambda: worker.yt.get_song_related(browse_id))
    return norm.page("related", browse_id, "Related", sections=related_sections(response))


# ---- credits -------------------------------------------------------------------------


def _artist_links(runs):
    """name -> channel id of the linked artists in a run list."""
    links = {}
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        browse = raw.browse_endpoint(run.get("navigationEndpoint"))
        name = (run.get("text") or "").strip()
        if name and browse and browse["pageType"] in (raw.ARTIST_PAGE, raw.LIBRARY_ARTIST_PAGE):
            links[name] = norm.strip_mpla(browse["browseId"])
    return links


def credits_page(browse_id, response):
    dialog = nav(response, CREDITS_DIALOG, True)
    if not isinstance(dialog, dict):
        return norm.page("credits", browse_id, "Song credits", message="No credits for this song")
    meta = nav(dialog, ["metadata", "musicMultiRowListItemRenderer"], True) or {}
    # The song's own artists are linked in its header; the same names in the
    # sections below are not.
    links = _artist_links(nav(meta, ["subtitle", "runs"], True))
    sections = []
    for entry in dialog.get("sections") or []:
        section = (entry or {}).get("dismissableDialogContentSectionRenderer")
        if not isinstance(section, dict):
            continue
        runs = nav(section, ["subtitle", "runs"], True) or []
        own = _artist_links(runs)
        items = []
        for run in runs:
            name = (run.get("text") or "").strip() if isinstance(run, dict) else ""
            if not name:
                continue  # the line breaks between names
            channel_id = own.get(name) or links.get(name)
            items.append(norm.artist({"browseId": channel_id, "title": name}) if channel_id
                         else {"type": "text", "title": name, "icon": PERSON_ICON})
        if items:
            sections.append(norm.section(raw.text(section.get("title")), items, layout="credits"))
    # The dialog's title comes as a plain string, the rest as runs.
    heading = dialog.get("title") if isinstance(dialog.get("title"), str) else raw.text(dialog.get("title"))
    title = raw.text(meta.get("title")) or heading or "Song credits"
    subtitle = " • ".join(part for part in (heading, raw.text(meta.get("subtitle"))) if part)
    thumbnails = nav(meta, ["thumbnail", "musicThumbnailRenderer", "thumbnail", "thumbnails"], True)
    return norm.page("credits", browse_id, title, subtitle or None, thumbnails, sections,
                     message=None if sections else "No credits for this song")


def m_credits(worker, params):
    """``{videoId}`` or ``{browseId}`` (a track's creditsBrowseId, MPTC...):
    a page, one section per role; names with an artist page are artist items."""
    browse_id, video_id = opt_str(params, "browseId"), opt_str(params, "videoId")
    if (browse_id is None) == (video_id is None):
        raise WorkerError("BAD_REQUEST", "give exactly one of videoId, browseId")
    browse_id = browse_id or "MPTC" + video_id
    if not browse_id.startswith("MPTC"):
        raise WorkerError("BAD_REQUEST", "'browseId' must be a credits id (MPTC...)")
    return credits_page(browse_id, _send(lambda: worker.yt.get_song_credits(browse_id)))


# ---- recorders (signed out; fixtures are trimmed to what the tests read) -----------------


def _record(yt, endpoint, call):
    raw.install(yt)
    with raw.capture((endpoint,)) as cap:
        try:
            call()
        except (KeyError, TypeError, IndexError, AttributeError, ValueError):
            pass
    return cap.first(endpoint)


def record_watch_raw(yt, params):
    """The raw 'next' response, trimmed to its tabs and the queue renderer
    (header, chips and the first ``keep`` entries)."""
    response = _record(yt, "next", lambda: yt.get_watch_playlist(videoId=params["videoId"], limit=CONTEXT_LIMIT))
    tabs = []
    for tab in nav(response, [*WATCH_NEXT, "tabs"], True) or []:
        renderer = tab.get("tabRenderer") or {}
        kept = {key: renderer[key] for key in ("title", "endpoint", "unselectable") if key in renderer}
        queue = nav(renderer, ["content", "musicQueueRenderer"], True)
        if isinstance(queue, dict):
            panel = dict(nav(queue, ["content", "playlistPanelRenderer"], True) or {})
            panel = {key: panel[key] for key in ("playlistId", "contents") if key in panel}
            panel["contents"] = (panel.get("contents") or [])[:params.get("keep", 6)]
            kept["content"] = {"musicQueueRenderer": {"header": queue.get("header"),
                                                      "subHeaderChipCloud": queue.get("subHeaderChipCloud"),
                                                      "content": {"playlistPanelRenderer": panel}}}
        tabs.append({"tabRenderer": kept})
    return {"contents": {"singleColumnMusicWatchNextResultsRenderer": {"tabbedRenderer": {
        "watchNextTabbedResultsRenderer": {"tabs": tabs}}}}}


def record_queue_raw(yt, params):
    """The raw 'next' response of one of a song's tuner chips (``chip``, an
    index into its chips, default 2), trimmed like watchRaw."""
    first = _record(yt, "next", lambda: yt.get_watch_playlist(videoId=params["videoId"], limit=CONTEXT_LIMIT))
    chips = _chips(_queue_renderer(first))
    chip = chips[min(params.get("chip", 2), len(chips) - 1)] if chips else None
    if chip is None:
        raise ValueError("this song has no tuner chips")
    body = {"videoId": params["videoId"], "playlistId": chip["playlistId"], "params": chip["params"],
            "isAudioOnly": True, "enablePersistentPlaylistPanel": True}
    response = yt._send_request("next", body)
    panel = dict(nav(_queue_renderer(response), QUEUE_PANEL, True) or {})
    panel = {key: panel[key] for key in ("playlistId", "contents") if key in panel}
    panel["contents"] = (panel.get("contents") or [])[:params.get("keep", 6)]
    queue = _queue_renderer(response) or {}
    tab = {"tabRenderer": {"content": {"musicQueueRenderer": {
        "header": queue.get("header"), "content": {"playlistPanelRenderer": panel}}}}}
    return {"chip": {"title": chip["title"], "playlistId": chip["playlistId"], "params": chip["params"]},
            "contents": {"singleColumnMusicWatchNextResultsRenderer": {"tabbedRenderer": {
                "watchNextTabbedResultsRenderer": {"tabs": [tab]}}}}}


def record_lyrics_timed(yt, params):
    """The raw mobile lyrics response, cut to its first ``keep`` timed lines."""
    response = _record(yt, "browse", lambda: yt.get_lyrics(params["browseId"], timestamps=True))
    path = ["contents", "elementRenderer", "newElement", "type", "componentType", "model", "timedLyricsModel",
            "lyricsData"]
    data = nav(response, path, True) or {}
    kept = {"timedLyricsData": (data.get("timedLyricsData") or [])[:params.get("keep", 4)],
            "sourceMessage": data.get("sourceMessage")}
    value = kept
    for key in reversed(path):
        value = {key: value}
    return value


def record_lyrics_plain(yt, params):
    """The raw lyrics response, cut to its first ``keep`` lines."""
    response = _record(yt, "browse", lambda: yt.get_lyrics(params["browseId"]))
    shelf = nav(response, ["contents", "sectionListRenderer", "contents", 0, "musicDescriptionShelfRenderer"], True)
    if not isinstance(shelf, dict):
        return response
    text = raw.text(shelf.get("description")) or ""
    lines = "\n".join(text.split("\n")[:params.get("keep", 3)])
    shelf = {"description": {"runs": [{"text": lines}]}, "footer": shelf.get("footer")}
    return {"contents": {"sectionListRenderer": {"contents": [{"musicDescriptionShelfRenderer": shelf}]}}}


def _without_menu(item):
    """An item renderer without its menu (its parsers here never read it)."""
    if not isinstance(item, dict) or len(item) != 1:
        return item
    key, data = next(iter(item.items()))
    return {key: {k: v for k, v in data.items() if k != "menu"}} if isinstance(data, dict) else item


def record_related(yt, params):
    """The raw Related response, each row cut to its first ``keep`` items
    (without their menus)."""
    response = _record(yt, "browse", lambda: yt.get_song_related(params["browseId"]))
    rows = []
    for row in nav(response, ["contents", "sectionListRenderer", "contents"], True) or []:
        key, data = next(iter(row.items()))
        if isinstance(data, dict) and isinstance(data.get("contents"), list):
            data = dict(data, contents=[_without_menu(c) for c in data["contents"][:params.get("keep", 3)]])
        rows.append({key: data})
    return {"contents": {"sectionListRenderer": {"contents": rows}}}


def record_credits(yt, params):
    return _record(yt, "browse", lambda: yt.get_song_credits(params["browseId"]))


# ---- registry (merged by ytm.py) --------------------------------------------------------

METHODS = {
    "nowContext": m_now_context,
    "likeStatus": m_like_status,
    "upNext": m_up_next,
    "queueFrom": m_queue_from,
    "lyrics": m_lyrics,
    "related": m_related,
    "credits": m_credits,
}
CACHEABLE = {"lyrics", "related", "credits"}
# One or two reads each, beside browsing: a song change while you search
# never holds the search up (a 'next' request takes about a second). The
# Now tab's reads follow each other anyway (lyrics and Related need the ids
# nowContext brings). lyrics is the only user of the signed-out client in
# the worker, and a lane runs one request at a time, so its as_mobile()
# switch (not thread-safe) never overlaps another.
LANES = {"nowContext": "pages", "lyrics": "pages", "related": "pages", "credits": "pages",
         # S12: autoplay's top-ups and the tuner chips (about 1 s each).
         "upNext": "pages", "queueFrom": "pages"}
INVALIDATES = {}
RECORDERS = {
    "watchRaw": record_watch_raw,
    "queueRaw": record_queue_raw,
    "lyricsTimed": record_lyrics_timed,
    "lyricsPlain": record_lyrics_plain,
    "related": record_related,
    "credits": record_credits,
}
MUTATES = set()
