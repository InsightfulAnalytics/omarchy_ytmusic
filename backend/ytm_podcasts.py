"""Podcasts (S8): shows, episodes, channels, Episodes for Later, New
episodes, and the library's podcasts and channels.

Reads (all CACHEABLE, on the pages lane):

- ``podcast({browseId | playlistId, cursor?})``: a show's page. The
  podcast's id is its browseId without ``MPSP`` (get_podcast returns none).
  The first call reads the raw ``MPSP`` browse and its ``musicShelfContinuation``
  pages up to PAGE_EPISODES episodes (at most MAX_HTTP requests); ``cursor``
  reads the next ones. Header ``{browseId, podcastId, author {name, id},
  description, saved}``; one section, Episodes (tracks of kind ``episode``).
- ``episode({videoId})`` (an ``MPED`` browseId too): an episode's page from
  the raw ``MPED`` browse. ytmusicapi 1.12.2's get_episode raises KeyError
  on a description whose 0:00 timestamp has no startTimeSeconds (most
  chapter lists), and reads the date from the subtitle's first run (the view
  count); this reads the response itself. Header ``{videoId, browseId,
  title, show {name, id}, date, subtitle, duration, durationSec, saved,
  progressPercentage, description, descriptionRuns, episode}`` (``episode``
  is the playable track); a Chapters section of ``{type: "chapter", title,
  seconds, time, videoId}`` rows when the description has timestamps.
- ``channel({channelId})``: a podcast channel (a user channel): its rows
  in the served order (Latest episodes, Podcasts, Videos, Playlists), each
  keyed by what it holds, never by its title. Latest episodes' See all
  carries the page type CHANNEL_EPISODES, which the shell routes to
  ``channelEpisodes``. Header ``{channelId, subscribeId, subscribed,
  subscribers}``. Its episode rows link no podcast in their subtitle; each
  names the one its menu's BROADCAST entry goes to (menu_podcast), titled
  from the Podcasts row.
- ``channelEpisodes({channelId, params, cursor?})``: all of a channel's
  episodes (get_channel_episodes' page; one request holds them all today),
  their podcast named as on the channel page (from its disk copy, else the
  channel's name).
- ``savedEpisodes({})``: Episodes for Later (get_saved_episodes, read whole:
  it is small), a collection of kind ``episodesForLater`` whose episodes keep
  their ``setVideoId`` (what removing one takes).
- ``newEpisodes({})``: the New episodes auto playlist (get_episodes_playlist
  RDPN); without saved podcasts YouTube serves no such page (KeyError), and
  the reply is an empty page with a message.
- ``libraryPodcasts({order?, cursor?})``: saved podcasts, read like the
  library's lists (ytm_library). The Add podcast tile (it opens a dialog)
  is skipped; the Episodes for Later and New episodes tiles become
  ``header.episodesForLater`` / ``header.newEpisodes`` ``{count, subtitle}``
  (the shell shows them as rows of their own).
- ``libraryChannels({order?, cursor?})``: channels in the library; a user
  channel is a ``channel``, an artist an ``artist`` (by page type).

Edits (MUTATES, signed in, retry:false in the shell):

- ``savePodcast({podcastId, save})``: rate_playlist(podcastId, LIKE /
  INDIFFERENT); ``podcastId`` may be the browseId (MPSP stripped).
- ``saveEpisode({videoId})``: add_playlist_items("SE", [videoId]).
- ``unsaveEpisode({videoId, setVideoId})``: remove_playlist_items("SE",
  ...); the setVideoId comes from Episodes for Later (BAD_REQUEST without).

Area module (see AREA_MODULES in ytm.py). Recorders are anonymous: the raw
podcast, episode and channel pages, trimmed.
"""

import re

from ytmusicapi.navigation import MMRIR, MNIR, MRLIR, MTRIR, nav
from ytmusicapi.parsers._utils import get_flex_column_item
from ytmusicapi.parsers.library import parse_artists
from ytmusicapi.parsers.podcasts import parse_episode, parse_podcast

import ytm_actions as actions
import ytm_artist as artist
import ytm_library as library
import ytm_normalize as norm
import ytm_pages as pages
import ytm_raw as raw
from ytm_proto import Fixed, Prefixed, WorkerError, opt_cursor, opt_str, req_str

PODCAST_PREFIX = "MPSP"
EPISODE_PREFIX = "MPED"
SAVED_EPISODES = "SE"
NEW_EPISODES = "RDPN"
PODCASTS_BROWSE_ID = "FEmusic_library_non_music_audio_list"
CHANNELS_BROWSE_ID = "FEmusic_library_non_music_audio_channels_list"
# The page type a channel's Latest episodes See all carries in a reply (the
# served endpoint has none): the shell opens it as channelEpisodes.
CHANNEL_EPISODES = "YTM_CHANNEL_EPISODES"
# A podcast page reads about this many episodes per call (20 a request).
PAGE_EPISODES = 40
CHANNEL_EPISODES_ITEMS = 100
MAX_HTTP = 3
NO_NEW_EPISODES = "No new episodes"
# Library tiles the Podcasts chip shows as rows of its own.
SPECIAL_TILES = {"VL" + SAVED_EPISODES: "episodesForLater", "VL" + NEW_EPISODES: "newEpisodes"}
_SEPARATORS = ("\u2022", "|")
_PARSE_ERRORS = (KeyError, TypeError, ValueError, IndexError, AttributeError)
_HEADER = ["contents", "twoColumnBrowseResultsRenderer", "tabs", 0, "tabRenderer", "content", "sectionListRenderer",
           "contents", 0, "musicResponsiveHeaderRenderer"]


# ---- ids and small readers ----------------------------------------------------------------


def podcast_id(value):
    """A podcast's playlist id from its browseId (MPSP...) or its id as is."""
    value = (value or "").strip()
    if value.startswith(PODCAST_PREFIX):
        value = value[len(PODCAST_PREFIX):]
    return value


def episode_id(value):
    """An episode's videoId from its browseId (MPED...) or its videoId."""
    value = (value or "").strip()
    return value[len(EPISODE_PREFIX):] if value.startswith(EPISODE_PREFIX) else value


def _parts(runs):
    """The texts of subtitle runs, without the bullets between them."""
    out = []
    for run in runs or []:
        text = (run.get("text") or "").strip() if isinstance(run, dict) else ""
        if text and text not in _SEPARATORS:
            out.append(text)
    return out


def _date(runs):
    """An episode's date: the last part of its subtitle ("241K views \u2022 Jul 31";
    ytmusicapi reads the first, the view count)."""
    parts = _parts(runs)
    return parts[-1] if parts else None


def _credit(runs):
    """The first linked run of a subtitle (a podcast or a channel), or None."""
    for run in runs or []:
        browse = raw.browse_endpoint(run.get("navigationEndpoint")) if isinstance(run, dict) else None
        if browse and run.get("text"):
            return {"name": run["text"], "id": browse["browseId"]}
    return None


def _length(progress):
    """``(text, seconds)`` of a musicPlaybackProgressRenderer's duration ("4 hr 1 min")."""
    parts = _parts(nav(progress, ["durationText", "runs"], True))
    text = parts[-1] if parts else None
    # ytm_raw reads the same "2 min 56 sec" lengths in Home's episode subtitles.
    return text, raw._length(text) if text else None


_LEFT = re.compile(r"\s*left$", re.IGNORECASE)


def _left_seconds(text):
    """Seconds of a "2 hr 16 min left" text, or None (another language too)."""
    if not isinstance(text, str) or not _LEFT.search(text):
        return None
    return raw._length(_LEFT.sub("", text))


def progress_of(progress, seconds):
    """``{progress, progressText, resumeSec}`` of a musicPlaybackProgressRenderer:
    YouTube's played percentage, its "2 hr 16 min left" text, and the second
    that leaves (length minus time left; None without both). The percentage
    alone misleads: a barely started episode is served as 10% played. For
    an episode nobody started, the progress text is its length ("4 hr 1
    min", the durationText's): no played text then (None)."""
    parts = _parts(nav(progress, ["playbackProgressText", "runs"], True))
    text = parts[-1] if parts else None
    left = _left_seconds(text)
    percent = _percent(progress.get("playbackProgressPercentage")) if isinstance(progress, dict) else None
    if text is not None and left is None and (text == _length(progress)[0] or percent == 0):
        text = None
    resume = max(0, seconds - left) if isinstance(seconds, int) and left is not None else None
    return {"progress": percent, "progressText": text, "resumeSec": resume}


def _percent(value):
    """A progress percentage 0 to 100, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
    return max(0, min(100, int(value)))


def _with_page(item):
    """An episode with its page (MPED + videoId) when the row named none."""
    if item.get("videoId") and not item.get("browseId"):
        item["browseId"] = EPISODE_PREFIX + item["videoId"]
    return item


def menu_podcast(data):
    """The podcast (its MPSP browseId) a row's menu goes to: the BROADCAST
    entry, as ytmusicapi reads an episode header's; None without one."""
    for entry in nav(data, ["menu", "menuRenderer", "items"], True) or []:
        item = entry.get(MNIR) if isinstance(entry, dict) else None
        if not isinstance(item, dict) or nav(item, ["icon", "iconType"], True) != "BROADCAST":
            continue
        browse = raw.browse_endpoint(item.get("navigationEndpoint"))
        if browse and browse["browseId"].startswith(PODCAST_PREFIX):
            return browse["browseId"]
    return None


def _namer(names=None, default=None):
    """``name_of(browseId)`` for a podcast a row's menu names: the title a
    podcast row of the page gave it, else ``default`` (the channel's name)."""
    return lambda browse_id: (names or {}).get(browse_id) or default


def episode_item(data, podcast=None, name_of=None):
    """A musicMultiRowListItemRenderer episode (podcast, channel and New
    episodes pages) as a track of kind ``episode``: its date and length read
    from the raw row, how much of it was played (progress_of: ``progress``,
    ``progressText``, ``resumeSec``) and ``podcast`` (given, else the row's
    linked credit, else the podcast its menu goes to, named by ``name_of``:
    a channel's episode rows link nothing in their subtitle)."""
    parsed = parse_episode(data)
    runs = nav(data, ["subtitle", "runs"], True)
    progress = nav(data, ["playbackProgress", "musicPlaybackProgressRenderer"], True) or {}
    _, seconds = _length(progress)
    credit = podcast or _credit(runs)
    if credit is None and name_of is not None:
        browse_id = menu_podcast(data)
        name = name_of(browse_id) if browse_id else None
        credit = {"name": name, "id": browse_id} if name else None
    value = norm.episode(dict(parsed, date=_date(runs), duration=None, duration_seconds=seconds, podcast=credit))
    value.update(progress_of(progress, seconds))
    return _with_page(value)


def _items(contents, podcast=None, name_of=None):
    """Normalized items of a row: episodes read here, anything else by ytm_raw."""
    out = []
    for content in contents or []:
        if not isinstance(content, dict):
            continue
        try:
            value = episode_item(content[MMRIR], podcast, name_of) if MMRIR in content else raw.item(content)
        except _PARSE_ERRORS as exc:
            norm.log(f"skipped malformed podcast item: {type(exc).__name__}")
            continue
        if value is not None:
            out.append(value)
    return out


def _episodes(podcast=None, name_of=None):
    """``parse`` for pages.fetch: the episodes of a shelf or grid page."""
    def parse(contents):
        return [i for i in _items(contents, podcast, name_of) if i.get("type") == "track"]
    return parse


def _shelf(response, keys=("musicShelfRenderer",)):
    """The first row of a page that is one of ``keys`` (its renderer data)."""
    for row in raw.rows(response):
        key, data = raw.unwrap(row)
        if key in keys and isinstance(data, dict):
            return data
    return None


def _thumbnails(header):
    return nav(header, ["thumbnail", "musicThumbnailRenderer", "thumbnail", "thumbnails"], True) \
        or nav(header, ["foregroundThumbnail", "musicThumbnailRenderer", "thumbnail", "thumbnails"], True)


def _like_menu_toggle(header):
    """The header menu's Save to library entry (a toggle that likes the
    playlist): its isToggled, or None."""
    for button in header.get("buttons") or []:
        for entry in nav(button, ["menuRenderer", "items"], True) or []:
            toggle = entry.get("toggleMenuServiceItemRenderer") if isinstance(entry, dict) else None
            if not isinstance(toggle, dict):
                continue
            for key in ("defaultServiceEndpoint", "toggledServiceEndpoint"):
                if nav(toggle, [key, "likeEndpoint", "target", "playlistId"], True):
                    return toggle.get("isToggled") is True
    return None


def podcast_saved(header):
    """Whether a podcast header says the show is in the library: its library
    toggle (matched by its like endpoint, as S4's library_toggle), else the
    menu's; signed out, the toggle opens a sign-in dialog and the menu's
    still names the like (False). None when neither is there."""
    for button in header.get("buttons") or []:
        toggle = actions._like_toggle(button)
        if toggle is not None:
            return toggle.get("isToggled") is True
    return _like_menu_toggle(header)


def episode_saved(header):
    """Whether an episode header says it is in Episodes for Later: the toggle
    whose endpoint edits the SE playlist, else the first toggle; None when
    there is none."""
    first = None
    for button in header.get("buttons") or []:
        toggle = button.get("toggleButtonRenderer") if isinstance(button, dict) else None
        if not isinstance(toggle, dict):
            continue
        first = first if first is not None else toggle
        for key in ("defaultServiceEndpoint", "toggledServiceEndpoint"):
            if nav(toggle, [key, "playlistEditEndpoint", "playlistId"], True) == SAVED_EPISODES:
                return toggle.get("isToggled") is True
    return None if first is None else first.get("isToggled") is True


# ---- podcast -----------------------------------------------------------------------------


def _cached_title(worker, browse_id):
    """The podcast's title from its first page's disk copy (a cursor page
    carries no header), or None."""
    try:
        hit = worker.cache().get("podcast", {"browseId": browse_id})
    except Exception:  # noqa: BLE001 - an unreadable cache is a miss
        hit = None
    page = hit[0] if hit is not None else None
    return page.get("title") or None if isinstance(page, dict) else None


def _podcast_page(browse_id, title, items, cursor, header=None, thumbnails=None, first=True):
    # A cursor page's section is untitled, so the shell appends it to the first.
    section = norm.section("Episodes" if first else None, items, layout="shelf", key="episodes")
    return norm.page("podcast", browse_id, title, "Podcast" if first else None, thumbnails, [section], cursor,
                     header=header)


def podcast_header(browse_id, header):
    author = _credit(nav(header, ["straplineTextOne", "runs"], True))
    description = raw.text(nav(header, ["description", "musicDescriptionShelfRenderer", "description"], True))
    return {"browseId": browse_id, "podcastId": podcast_id(browse_id), "author": author,
            "description": description or None, "saved": podcast_saved(header)}


def m_podcast(worker, params):
    """``{browseId | playlistId, cursor?}``: a podcast page (see the module docstring)."""
    given = opt_str(params, "browseId") or opt_str(params, "playlistId")
    pid = podcast_id(given)
    if not pid:
        raise WorkerError("BAD_REQUEST", "missing 'browseId' (MPSP...) or 'playlistId'")
    browse_id = PODCAST_PREFIX + pid
    body = {"browseId": browse_id}
    cursor = opt_cursor(params)
    yt = worker.yt
    if cursor:
        title = _cached_title(worker, browse_id)
        credit = {"name": title, "id": browse_id} if title else None
        items, cursor = pages.fetch(yt, "browse", body, "ctoken", None, _episodes(credit), cursor=cursor,
                                    min_items=PAGE_EPISODES, max_http=MAX_HTTP)
        return _podcast_page(browse_id, None, items, cursor, first=False)
    first = pages.first_page(yt, "browse", body)
    response = first[0]
    header = nav(response, _HEADER, True)
    if not isinstance(header, dict):
        raise WorkerError("NOT_FOUND", "YouTube Music has no such podcast")
    title = raw.text(header.get("title")) or ""
    credit = {"name": title, "id": browse_id} if title else None
    shelf = _shelf(response)
    items, cursor = [], None
    if shelf is not None:
        items, cursor = pages.fetch(yt, "browse", body, "ctoken", lambda _: shelf, _episodes(credit),
                                    min_items=PAGE_EPISODES, max_http=MAX_HTTP, first=first)
    return _podcast_page(browse_id, title, items, cursor, podcast_header(browse_id, header), _thumbnails(header))


# ---- episode -------------------------------------------------------------------------------

# What may sit between a chapter's timestamp and its title ("0:00 - Intro",
# "(00:00) Intro", "00:00 | Intro").
_LEAD = re.compile(r"^[\s\-\u2013\u2014:|)\].\u2022]+")
_TRAIL = re.compile(r"[\s\-\u2013\u2014:|(\[\u2022]+$")


def _timestamp(run, video_id):
    """The seconds a description run links to in this episode, or None."""
    watch = nav(run, ["navigationEndpoint", "watchEndpoint"], True) if isinstance(run, dict) else None
    if not isinstance(watch, dict) or watch.get("videoId") not in (None, video_id):
        return None
    seconds = watch.get("startTimeSeconds")
    if isinstance(seconds, int) and not isinstance(seconds, bool):
        return max(0, seconds)
    # The first chapter (0:00) links without startTimeSeconds.
    return norm.parse_duration(run.get("text"))


def chapters(runs, video_id):
    """Chapter rows from description runs that link to a time in this episode:
    ``{type: "chapter", title, seconds, time, videoId}`` in the served order,
    one per second. The title is the text after the timestamp on its line,
    else the text before it."""
    runs = [r for r in runs or [] if isinstance(r, dict)]
    out, seen = [], set()
    for i, run in enumerate(runs):
        seconds = _timestamp(run, video_id)
        if seconds is None or seconds in seen:
            continue
        after = ""
        for later in runs[i + 1:]:
            if _timestamp(later, video_id) is not None:
                break
            after += later.get("text") or ""
            if "\n" in after:
                break
        title = _LEAD.sub("", after.split("\n", 1)[0]).strip()
        if not title:
            before = ""
            for earlier in reversed(runs[:i]):
                text = earlier.get("text") or ""
                if "\n" in text:
                    before = text.rsplit("\n", 1)[1] + before
                    break
                before = text + before
            title = _TRAIL.sub("", before).strip()
        seen.add(seconds)
        out.append({"type": "chapter", "title": title or (run.get("text") or "").strip(), "seconds": seconds,
                    "time": (run.get("text") or "").strip(), "videoId": video_id})
    return out


def description_runs(runs, video_id):
    """``[{text, url?, seconds?}]``: links (YouTube's redirect wrapper, which
    carries a token, replaced by its target) and timestamps kept."""
    out = []
    for run in runs or []:
        if not isinstance(run, dict) or not isinstance(run.get("text"), str):
            continue
        value = {"text": run["text"]}
        url = artist._link(nav(run, ["navigationEndpoint", "urlEndpoint", "url"], True))
        seconds = _timestamp(run, video_id)
        if url:
            value["url"] = url
        elif seconds is not None:
            value["seconds"] = seconds
        out.append(value)
    return out


def build_episode(video_id, response):
    """The episode page from its raw MPED browse response."""
    header = nav(response, _HEADER, True)
    if not isinstance(header, dict):
        raise WorkerError("NOT_FOUND", "YouTube Music has no such episode")
    title = raw.text(header.get("title")) or ""
    subtitle_runs = nav(header, ["subtitle", "runs"], True)
    progress = nav(header, ["progress", "musicPlaybackProgressRenderer"], True) or {}
    duration, seconds = _length(progress)
    show = _credit(nav(header, ["straplineTextOne", "runs"], True))
    thumbnails = _thumbnails(header)
    shelf = _shelf(response, ("musicDescriptionShelfRenderer",))
    runs = nav(shelf, ["description", "runs"], True) if shelf else None
    description = raw.text({"runs": runs}) if runs else None
    episode = _with_page(norm.episode({"videoId": video_id, "title": title, "duration_seconds": seconds,
                                       "thumbnails": thumbnails, "podcast": show, "date": _date(subtitle_runs),
                                       "browseId": EPISODE_PREFIX + video_id}))
    played = progress_of(progress, seconds)
    episode.update(played)
    percent = played["progress"]
    found = chapters(runs, video_id)
    sections = [norm.section("Chapters", found, layout="shelf", key="chapters")] if found else []
    header_out = {
        "videoId": video_id, "browseId": EPISODE_PREFIX + video_id, "title": title, "show": show,
        "date": _date(subtitle_runs), "subtitle": " \u2022 ".join(_parts(subtitle_runs)) or None,
        "duration": duration, "durationSec": seconds, "saved": episode_saved(header),
        "progressPercentage": percent, "progressText": played["progressText"], "resumeSec": played["resumeSec"],
        "description": description,
        "descriptionRuns": description_runs(runs, video_id), "episode": episode,
    }
    return norm.page("episode", video_id, title, header_out["date"], thumbnails, sections, header=header_out)


def m_episode(worker, params):
    video_id = episode_id(req_str(params, "videoId"))
    if not video_id:
        raise WorkerError("BAD_REQUEST", "missing or empty 'videoId'")
    return build_episode(video_id, worker.yt._send_request("browse", {"browseId": EPISODE_PREFIX + video_id}))


# ---- channel -------------------------------------------------------------------------------


def _section_key(items):
    """What a row holds, from its items: episodes, podcasts, videos,
    playlists, or None for anything mixed."""
    kinds = set()
    for item in items:
        kind = item.get("type")
        if kind == "track":
            kind = "episodes" if item.get("kind") == "episode" else "videos" if item.get("kind") == "video" else "songs"
        else:
            kind = {"podcast": "podcasts", "playlist": "playlists", "album": "albums"}.get(kind, kind)
        kinds.add(kind)
    return kinds.pop() if len(kinds) == 1 else None


def podcast_names(items):
    """``{MPSP browseId: title}`` of the podcasts among normalized items."""
    return {i["browseId"]: i["title"] for i in items or []
            if i.get("type") == "podcast" and str(i.get("browseId") or "").startswith(PODCAST_PREFIX) and i.get("title")}


def channel_sections(channel_id, rows, name=None):
    """The channel's rows in the served order, as sections keyed by what they
    hold. Its episode rows name their podcast by the menu's link to it, with
    the title the Podcasts row gives it (else ``name``, the channel's)."""
    parsed = []
    for row in rows or []:
        key, data = raw.unwrap(row)
        if key not in raw.ROW_RENDERERS or not isinstance(data, dict):
            continue
        try:
            layout, title, endpoints, contents = raw.row_parts(key, data)
        except _PARSE_ERRORS as exc:
            norm.log(f"skipped channel row: {type(exc).__name__}")
            continue
        if layout not in (None, "description"):
            parsed.append((layout, title, endpoints, contents))
    names = {}
    for parts in parsed:
        names.update(podcast_names(_items([c for c in parts[3] or [] if isinstance(c, dict) and MMRIR not in c])))
    out = []
    for layout, title, endpoints, contents in parsed:
        items = _items(contents, name_of=_namer(names, name))
        more = next((m for m in (raw.browse_endpoint(e) for e in endpoints) if m), None)
        play = next((p for p in (raw.watch_endpoint(e) for e in endpoints) if p), None)
        section_key = _section_key(items)
        if section_key == "episodes" and more and more["browseId"] == channel_id and more.get("params"):
            more = dict(more, pageType=CHANNEL_EPISODES)
        if items or more:
            out.append(norm.section(title, items, more, play, layout, key=section_key))
    return out


def build_channel(channel_id, response):
    header = (response or {}).get("header") or {}
    data = next(iter(header.values()), {}) if isinstance(header, dict) and header else {}
    data = data if isinstance(data, dict) else {}
    button = nav(data, ["subscriptionButton", "subscribeButtonRenderer"], True) or {}
    subscribers = raw.text(button.get("longSubscriberCountText")) or raw.text(button.get("subscriberCountText"))
    subscribed = button.get("subscribed") if isinstance(button.get("subscribed"), bool) else None
    head = {"channelId": channel_id, "subscribeId": button.get("channelId") or channel_id,
            "subscribed": subscribed, "subscribers": subscribers}
    title = raw.text(data.get("title")) or ""
    return norm.page("channel", channel_id, title, subscribers, _thumbnails(data),
                     channel_sections(channel_id, raw.rows(response), title or None), header=head, round=True)


def m_channel(worker, params):
    channel_id = req_str(params, "channelId")
    return build_channel(channel_id, worker.yt._send_request("browse", {"browseId": channel_id}))


def _channel_name(response):
    header = (response or {}).get("header") or {}
    data = next(iter(header.values()), {}) if isinstance(header, dict) and header else {}
    return raw.text(data.get("title")) if isinstance(data, dict) else None


def _cached_channel(worker, channel_id):
    """``(podcast names, channel name)`` from the channel page's disk copy
    (See all is opened from it); ``({}, None)`` when there is none."""
    try:
        hit = worker.cache().get("channel", {"channelId": channel_id})
    except Exception:  # noqa: BLE001 - an unreadable cache is a miss
        hit = None
    page = hit[0] if hit is not None else None
    if not isinstance(page, dict):
        return {}, None
    names = {}
    for section in page.get("sections") or []:
        names.update(podcast_names(section.get("items") if isinstance(section, dict) else None))
    return names, page.get("title") or None


def m_channel_episodes(worker, params):
    """``{channelId, params, cursor?}``: every episode of a channel (its
    Latest episodes See all). The rows link no podcast: each names the one
    its menu goes to, titled from the channel page's Podcasts row (its disk
    copy), else by the channel's name."""
    channel_id = req_str(params, "channelId")
    page_params = req_str(params, "params")
    cursor = opt_cursor(params)
    body = {"browseId": channel_id, "params": page_params}
    yt = worker.yt
    names, cached_name = _cached_channel(worker, channel_id)

    def page(name, items, cursor=None, message=None):
        # The channel's name over "Episodes"; a cursor page has neither.
        sections = [] if message else [norm.section(None, items, layout="grid", key="episodes")]
        return norm.page("channelEpisodes", channel_id, name, "Episodes" if name else None, sections=sections,
                         cursor=cursor, message=message)

    if cursor:
        items, cursor = pages.fetch(yt, "browse", body, "ctoken", None, _episodes(name_of=_namer(names, cached_name)),
                                    cursor=cursor, min_items=CHANNEL_EPISODES_ITEMS, max_http=MAX_HTTP)
        return page(None, items, cursor)
    first = pages.first_page(yt, "browse", body)
    name = _channel_name(first[0])
    kind, container = pages.detect(first[0])
    if kind in ("message", "empty"):
        return page(name, [], message=(container or None) if kind == "message" else None)
    if kind == "sections":
        container = _shelf(first[0], ("gridRenderer", "musicShelfRenderer"))
    if not isinstance(container, dict):
        raise WorkerError("UPSTREAM", "Unexpected response from YouTube Music (ytmusicapi may need an update)")
    items, cursor = pages.fetch(yt, "browse", body, "ctoken", lambda _: container,
                                _episodes(name_of=_namer(names, name or cached_name)),
                                min_items=CHANNEL_EPISODES_ITEMS, max_http=MAX_HTTP, first=first)
    return page(name, items, cursor)


# ---- Episodes for Later and New episodes ---------------------------------------------------------


def saved_episode(track):
    """An Episodes for Later entry (get_playlist's shape): its linked credit
    is the podcast, the other subtitle texts ("2 hr 16 min left", "May 25")
    its date line; the setVideoId stays (removing it takes it)."""
    listed = track.get("artists") or []
    credits = [a for a in listed if isinstance(a, dict) and a.get("id") and a.get("name")]
    texts = [a["name"] for a in listed if isinstance(a, dict) and a.get("name") and not a.get("id")]
    left = next((t for t in texts if _left_seconds(t) is not None), None)
    value = _with_page(norm.episode(dict(track, artists=[], album=None, podcast=credits[0] if credits else None,
                                         date=" \u2022 ".join(t for t in texts if t != left) or None)))
    seconds = value["durationSec"]
    value.update({"progress": None, "progressText": left,
                  "resumeSec": max(0, seconds - _left_seconds(left)) if left and isinstance(seconds, int) else None})
    return value


def m_saved_episodes(worker, params):
    worker.require_auth()
    data = worker.yt.get_saved_episodes(limit=None) or {}
    episodes = norm.normalize_list(data.get("tracks"), saved_episode, "saved episode")
    page = norm.collection("episodesForLater", dict(data, tracks=[]), SAVED_EPISODES)
    count = norm.to_int(data.get("trackCount"))
    count = len(episodes) if count is None else count
    page.update(tracks=episodes, trackCount=count, unavailable=max(0, count - len(episodes)), truncated=False,
                playlistId=SAVED_EPISODES, title=data.get("title") or "Episodes for Later",
                subtitle="1 episode" if count == 1 else f"{count} episodes")
    return page


def m_new_episodes(worker, params):
    worker.require_auth()
    with raw.capture(("browse",)) as cap:
        try:
            data = worker.yt.get_episodes_playlist(NEW_EPISODES) or {}
        except KeyError:
            # No saved podcasts: YouTube serves no New episodes page.
            return norm.page("newEpisodes", NEW_EPISODES, "New episodes", message=NO_NEW_EPISODES)
    shelf = _shelf(cap.first("browse"))
    if shelf is not None:
        items = _episodes()(shelf.get("contents"))
    else:
        items = norm.normalize_list(data.get("episodes"), lambda e: _with_page(norm.episode(e)), "new episode")
    sections = [norm.section(None, items, layout="shelf", key="episodes")] if items else []
    return norm.page("newEpisodes", NEW_EPISODES, data.get("title") or "New episodes", None, data.get("thumbnails"),
                     sections, message=None if items else NO_NEW_EPISODES)


# ---- the library's podcasts and channels ------------------------------------------------------


def _tile_endpoint(data):
    endpoint = data.get("navigationEndpoint")
    if not (isinstance(endpoint, dict) and "browseEndpoint" in endpoint):
        endpoint = nav(data, ["title", "runs", 0, "navigationEndpoint"], True)
    return raw.browse_endpoint(endpoint)


def library_podcast(data):
    """A library tile: a saved podcast, or None for the Add podcast tile
    (it opens a dialog, no page) and the auto playlists shown as rows."""
    browse = _tile_endpoint(data)
    if browse is None or browse["browseId"] in SPECIAL_TILES:
        return None
    if browse["pageType"] == raw.PODCAST_PAGE:
        return norm.podcast(parse_podcast(data))
    return raw.item({MTRIR: data})


def _library_podcasts(contents):
    return library._each(contents, MTRIR, library_podcast, "library podcast")


def library_specials(container):
    """``{episodesForLater?, newEpisodes?}`` from the first page's auto
    playlist tiles: ``{count, subtitle}``, count read from the subtitle part
    that has a number ("2 episodes")."""
    out = {}
    for content in (container or {}).get("items") or []:
        data = content.get(MTRIR) if isinstance(content, dict) else None
        browse = _tile_endpoint(data) if isinstance(data, dict) else None
        name = SPECIAL_TILES.get(browse["browseId"]) if browse else None
        if not name:
            continue
        parts = _parts(nav(data, ["subtitle", "runs"], True))
        counted = [p for p in parts if re.search(r"\d", p)]
        out[name] = {"count": norm.to_int(counted[-1]) if counted else None,
                     "subtitle": " \u2022 ".join(parts) or None}
    return out


def library_channel(data):
    """A library channel row: a user channel is a ``channel`` (its page is
    ``channel``), an artist an ``artist``; the row's subtitle kept."""
    parsed = parse_artists([{MRLIR: data}])[0]
    item = norm.channel(parsed) if parsed.get("type") == "channel" else norm.artist(parsed)
    column = get_flex_column_item(data, 1)
    item["subtitle"] = (raw.text(column.get("text")) if column else None) or item["subtitle"]
    return item


def _library_channels(contents):
    return library._each(contents, MRLIR, library_channel, "library channel")


def m_library_podcasts(worker, params):
    return library._read_list(worker, params, "libraryPodcasts", _library_podcasts, "grid",
                              header_of=library_specials, browse_id=PODCASTS_BROWSE_ID)


def m_library_channels(worker, params):
    return library._read_list(worker, params, "libraryChannels", _library_channels, "shelf",
                              browse_id=CHANNELS_BROWSE_ID)


# ---- edits ---------------------------------------------------------------------------------


def _succeeded(result):
    status = result.get("status") if isinstance(result, dict) else result
    return status == "STATUS_SUCCEEDED"


def _save_flag(params, key):
    value = params.get(key)
    if not isinstance(value, bool):
        raise WorkerError("BAD_REQUEST", f"'{key}' must be true or false")
    return value


def m_save_podcast(worker, params):
    """``{podcastId, save}``: a podcast into the library (save) or out of it."""
    from ytmusicapi.models.content.enums import LikeStatus

    save = _save_flag(params, "save")
    pid = podcast_id(opt_str(params, "podcastId") or opt_str(params, "browseId"))
    if not pid:
        raise WorkerError("BAD_REQUEST", "missing 'podcastId'")
    params["podcastId"] = pid  # what INVALIDATES matches (MPSP + id)
    worker.require_auth()
    worker.yt.rate_playlist(pid, LikeStatus.LIKE if save else LikeStatus.INDIFFERENT)
    return {"podcastId": pid, "browseId": PODCAST_PREFIX + pid, "saved": save}


def _video_id(params):
    video_id = episode_id(req_str(params, "videoId"))
    if not video_id:
        raise WorkerError("BAD_REQUEST", "missing or empty 'videoId'")
    params["videoId"] = video_id  # what INVALIDATES matches
    return video_id


def m_save_episode(worker, params):
    """``{videoId}``: an episode into Episodes for Later."""
    video_id = _video_id(params)
    worker.require_auth()
    result = worker.yt.add_playlist_items(SAVED_EPISODES, [video_id])
    if not _succeeded(result):
        raise WorkerError("UPSTREAM", "Not saved (is it in Episodes for Later already?)")
    added = result.get("playlistEditResults") if isinstance(result, dict) else None
    set_video_id = next((a.get("setVideoId") for a in added or [] if isinstance(a, dict)
                         and a.get("videoId") == video_id), None)
    return {"videoId": video_id, "setVideoId": set_video_id, "saved": True}


def m_unsave_episode(worker, params):
    """``{videoId, setVideoId}``: an episode out of Episodes for Later. The
    setVideoId is its entry's (savedEpisodes lists it)."""
    video_id = _video_id(params)
    set_video_id = opt_str(params, "setVideoId")
    if not set_video_id:
        raise WorkerError("BAD_REQUEST", "missing 'setVideoId' (the episode's entry in Episodes for Later)")
    worker.require_auth()
    result = worker.yt.remove_playlist_items(SAVED_EPISODES, [{"videoId": video_id, "setVideoId": set_video_id}])
    if not _succeeded(result):
        raise WorkerError("UPSTREAM", "YouTube Music did not remove the episode")
    return {"videoId": video_id, "saved": False}


# ---- recorders (anonymous) -------------------------------------------------------------------

# Parts of a raw page no parser here reads.
_TRIM = ("menu", "clickTrackingParams", "trackingParams", "accessibility", "accessibilityData", "loggingContext",
         "thumbnailCrop", "thumbnailScale", "overlay", "shareEntityEndpoint")
_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-"})


def _trim_menu(menu):
    """A row's menu for a fixture: only its link to the podcast (the
    BROADCAST entry menu_podcast reads), else nothing."""
    keep = [e for e in nav(menu, ["menuRenderer", "items"], True) or []
            if isinstance(e, dict) and nav(e, [MNIR, "icon", "iconType"], True) == "BROADCAST"]
    return {"menuRenderer": {"items": keep}} if keep else None


def trim(value):
    """A raw response for a fixture: menus (but a row's link to its podcast)
    and tracking dropped, one thumbnail per list, links without YouTube's
    redirect wrapper (it carries a token), en and em dashes as hyphens."""
    if isinstance(value, str):
        return value.translate(_DASHES)
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "menu":
                item = _trim_menu(item)
                if item is None:
                    continue
            elif key in _TRIM:
                continue
            if key == "thumbnails" and isinstance(item, list):
                item = item[-1:]
            if key == "urlEndpoint" and isinstance(item, dict):
                item = {"url": artist._link(item.get("url")) or ""}
            out[key] = trim(item)
        return out
    if isinstance(value, list):
        return [trim(v) for v in value]
    return value


def record_podcast(yt, params):
    """``{browseId}``: the raw podcast page (its first 20 episodes)."""
    return trim(yt._send_request("browse", {"browseId": PODCAST_PREFIX + podcast_id(params["browseId"])}))


def record_episode(yt, params):
    """``{videoId}``: the raw episode page."""
    return trim(yt._send_request("browse", {"browseId": EPISODE_PREFIX + episode_id(params["videoId"])}))


def record_channel(yt, params):
    """``{channelId}``: the raw channel page."""
    return trim(yt._send_request("browse", {"browseId": params["channelId"]}))


# ---- registry (merged by ytm.py) ------------------------------------------------------------------

METHODS = {
    "podcast": m_podcast,
    "episode": m_episode,
    "channel": m_channel,
    "channelEpisodes": m_channel_episodes,
    "savedEpisodes": m_saved_episodes,
    "newEpisodes": m_new_episodes,
    "libraryPodcasts": m_library_podcasts,
    "libraryChannels": m_library_channels,
    "savePodcast": m_save_podcast,
    "saveEpisode": m_save_episode,
    "unsaveEpisode": m_unsave_episode,
}
MUTATES = {"savePodcast", "saveEpisode", "unsaveEpisode"}
# Every read; the key holds the order of a library list, and cursor pages
# are never cached.
CACHEABLE = set(METHODS) - MUTATES
LANES = {name: "pages" for name in CACHEABLE}
# Episodes for Later's own page and list (the Podcasts chip shows its
# count) and the episode's page. Podcast pages hold no saved state of their
# episodes, and a cursor page names its podcast from the first page's copy.
_EPISODE_EDIT = [
    ("savedEpisodes", None, None),
    ("libraryPodcasts", None, None),
    ("episode", "videoId", "videoId"),
    ("playlist", Fixed(SAVED_EPISODES), "playlistId"),
    ("browsePage", Fixed("VL" + SAVED_EPISODES, PODCASTS_BROWSE_ID), "browseId"),
]
INVALIDATES = {
    # The podcast's page (keyed MPSP + id), the library's podcasts and
    # Recent activity, and New episodes (made from the saved podcasts).
    "savePodcast": [
        ("podcast", Prefixed(PODCAST_PREFIX, "podcastId"), "browseId"),
        ("libraryPodcasts", None, None),
        ("libraryLanding", None, None),
        ("newEpisodes", None, None),
        ("browsePage", Prefixed(PODCAST_PREFIX, "podcastId"), "browseId"),
        ("browsePage", Fixed(PODCASTS_BROWSE_ID, "FEmusic_library_landing"), "browseId"),
    ],
    "saveEpisode": _EPISODE_EDIT,
    "unsaveEpisode": _EPISODE_EDIT,
}
RECORDERS = {"podcast": record_podcast, "episode": record_episode, "channel": record_channel}
