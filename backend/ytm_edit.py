"""Playlist editing (S13): everything the web lets a playlist's owner do.

Every edit here changes the account (MUTATES): the shell sends it with
retry:false and refreshes what it touched when the outcome is unknown.

- ``editPlaylist({playlistId, title?, description?, privacy?, sortOrder?,
  addToTop?, voteOption?, collaboration?})``: one ``edit_playlist`` call
  with only the fields given. ``sortOrder`` is MANUAL, NEWEST_FIRST,
  NEWEST_LAST or TOP_VOTED; ``voteOption`` EVERYONE_CAN_VOTE,
  COLLABORATORS_ONLY or OFF (ytmusicapi's enums). A title cannot be empty
  or hold ``<`` or ``>``; a description cannot be cleared (YouTube ignores
  an empty one, so an empty one is BAD_REQUEST). ``collaboration`` true
  opens the playlist to collaborators and returns ``invite``, the
  playlist's invite link (the shell copies it; it is never logged or
  cached); false closes it. Returns ``{playlistId, changed, invite?}``.
- ``movePlaylistItem({playlistId, setVideoId, beforeSetVideoId?})``: the
  entry goes before ``beforeSetVideoId``, or to the end without one.
  YouTube answers success in an auto-sorted playlist too and then shows
  its own order, so the shell asks for Manual order first (it knows the
  order from ``playlistSettings``).
- ``addToPlaylist({playlistId, videoIds?, sourcePlaylistId?, duplicates?,
  skipDuplicates?})``: songs, or all of another playlist or album (its
  audio playlist), at the end. Songs already there are refused with code
  DUPLICATE (the error carries ``videoIds``, empty for a whole playlist:
  YouTube does not say which) unless ``duplicates`` is true (Add anyway).
  G4: ``skipDuplicates`` adds only the songs not there yet, as the web's
  Skip duplicates: a whole playlist goes with DEDUPE_OPTION_DROP_DUPLICATE
  (the command the Duplicates dialog serves); for songs, once YouTube
  refuses them, the refusal's own Skip duplicates command when it serves
  one, else the songs not there yet (the ones a refusal of some of them
  names, or, when it names every song, the ones the playlist, read again,
  does not hold). Returns
  ``{added: [{videoId, setVideoId}]}``, plus ``skipped`` (the songs left
  out) and ``status`` ("Nothing new to add" when none was left) for songs.
- ``createPlaylist({title, description?, privacy?, videoIds?,
  sourcePlaylistId?})``: a new playlist, empty or with the songs (the
  queue) or a copy of another playlist (not both: YouTube answers 400).
  A copy fills in on YouTube's side a few seconds later. Returns
  ``{playlistId}``.
- ``joinPlaylist({playlistId, token})``: join a collaborative playlist
  through the ``jct`` token of its invite link (``parse_invite``); the
  token never reaches a log. Returns ``{playlistId}``.

Read only, never cached (an owner's settings change with every edit):

- ``playlistSettings({playlistId})``: one browse of the playlist. Its
  ``owned``, ``privacy``, ``sortOrder`` (from the songs' sort menu; null
  when a view sort such as Title is chosen) with the chosen entry's served
  title ``sortTitle`` and the orders the menu offers, ``sortOptions``
  (``[{value, title}]``, titles as served), ``voteOption`` and
  ``voteOptions`` (the ones offered),
  ``collaboration`` and ``collaborators`` (from the header's collaborator
  faces) and the ``title`` and ``description`` (empty when the edit form
  has none). ``addToTop`` is not served: null. Anything not served is null
  ("not reported"). A playlist created a moment ago has no page yet.
- ``playlistSuggestions({playlistId})``: the "Suggestions" YouTube shows
  under your own playlist (14, in two requests after the page), with the
  page's settings. None for a playlist that is not yours.

G4: ``settings`` also has ``removeImage``, the served title of the
header's "Remove custom image" entry (its cover's edit menu), else None;
``removePlaylistImage({playlistId})`` (MUTATES) reads the page again and
sends that entry's own edit (two requests), NOT_FOUND when it is not
served. Setting a custom image is not offered: it is an image upload
through YouTube's uploader, which ytmusicapi has no method for.

Area module (see AREA_MODULES in ytm.py).
"""

import re
from urllib.parse import parse_qs, quote, urlparse

from ytmusicapi.models.content.enums import PlaylistSortOrder, PlaylistVoteEditOptions
from ytmusicapi.navigation import SECTION_LIST_ITEM, TAB_CONTENT, TWO_COLUMN_RENDERER, nav

import ytm_normalize as norm
import ytm_raw as raw
from ytm_proto import Fixed, Prefixed, WorkerError, choice, opt_bool, opt_str, req_str

PRIVACY = ("PRIVATE", "UNLISTED", "PUBLIC")
SORT_ORDERS = tuple(order.name for order in PlaylistSortOrder)
VOTE_OPTIONS = tuple(option.name for option in PlaylistVoteEditOptions)
# The vote dropdown's int32Value of each option (ytmusicapi's request values).
VOTE_VALUES = {option.get_argument_for_request(): option.name for option in PlaylistVoteEditOptions}
TITLE_MAX = 150
DESCRIPTION_MAX = 5000
VIDEOS_MAX = 5000
SUGGESTIONS = 14
INVITE_URL = "https://music.youtube.com/playlist?list={}&jct={}"
SUCCEEDED = "STATUS_SUCCEEDED"
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,512}$")


class Duplicate(WorkerError):
    """YouTube refused songs already in the playlist: nothing was added."""

    def __init__(self, video_ids):
        super().__init__("DUPLICATE", "Already in that playlist" if video_ids
                         else "Some of its songs are already in that playlist")
        self.video_ids = video_ids

    def as_dict(self):
        return dict(super().as_dict(), videoIds=self.video_ids)


# ---- params ---------------------------------------------------------------------------


def _strip_vl(value):
    return value[2:] if value.startswith("VL") else value


def _playlist_id(params, key="playlistId"):
    """A playlist id (VL stripped), written back into params so INVALIDATES
    matches the cached pages."""
    value = _strip_vl(req_str(params, key))
    if not _ID.match(value):
        raise WorkerError("BAD_REQUEST", f"'{key}' is not a playlist id")
    params[key] = value
    return value


def _opt_playlist_id(params, key):
    value = opt_str(params, key, high=130)
    if value is None:
        return None
    return _playlist_id(params, key)


def _title(params, required):
    """The title, stripped; None when absent and not required."""
    value = params.get("title")
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise WorkerError("BAD_REQUEST", "A playlist needs a title")
    value = value.strip()
    if "<" in value or ">" in value:
        raise WorkerError("BAD_REQUEST", "A title cannot contain < or >")
    if len(value) > TITLE_MAX:
        raise WorkerError("BAD_REQUEST", f"A title can have at most {TITLE_MAX} characters")
    return value


def _description(params, clearing_ok):
    value = params.get("description")
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > DESCRIPTION_MAX:
        raise WorkerError("BAD_REQUEST", f"A description is text of at most {DESCRIPTION_MAX} characters")
    if not value.strip() and not clearing_ok:
        raise WorkerError("BAD_REQUEST", "YouTube Music cannot clear a description; write a new one instead")
    return value.strip()


def _video_ids(params, required=False):
    value = params.get("videoIds")
    if value is None or value == []:
        if required:
            raise WorkerError("BAD_REQUEST", "'videoIds' must be a non-empty list")
        return None
    if not isinstance(value, list) or len(value) > VIDEOS_MAX \
            or not all(isinstance(v, str) and _ID.match(v) for v in value):
        raise WorkerError("BAD_REQUEST", f"'videoIds' must be a list of at most {VIDEOS_MAX} video ids")
    return value


def _succeeded(result):
    status = result.get("status") if isinstance(result, dict) else result
    return status == SUCCEEDED


def _refused(exc, message):
    """A 400, 401 or 403 from an edit, once the session was checked
    (require_auth), is YouTube refusing this change, not a lost session:
    UPSTREAM with ``message``, so the worker keeps its sign-in. Anything
    else is raised as it is."""
    from ytmusicapi.exceptions import YTMusicServerError

    first = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    if isinstance(exc, YTMusicServerError) and any(f"HTTP {code}" in first for code in (400, 401, 403)):
        return WorkerError("UPSTREAM", message)
    return exc


# ---- invite links ---------------------------------------------------------------------


def parse_invite(url):
    """``(playlistId, token)`` of a music.youtube.com (or youtube.com) invite
    link, ``.../playlist?list=<id>&jct=<token>``; None for anything else."""
    if not isinstance(url, str) or len(url) > 2048:
        return None
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not (host == "youtube.com" or host.endswith(".youtube.com")):
        return None
    if parsed.path.rstrip("/") != "/playlist":
        return None
    query = parse_qs(parsed.query)
    playlist_id = _strip_vl((query.get("list") or [""])[0])
    token = (query.get("jct") or [""])[0]
    if not _ID.match(playlist_id) or not _TOKEN.match(token):
        return None
    return playlist_id, token


def invite_url(playlist_id, token):
    return INVITE_URL.format(quote(playlist_id, safe=""), quote(token, safe=""))


# ---- settings, read from a playlist page ------------------------------------------------


def _walk(value, key):
    """Every dict under ``value`` that holds ``key``, depth first."""
    if isinstance(value, dict):
        if key in value:
            yield value
        for child in value.values():
            yield from _walk(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, key)


def _runs_text(value):
    text = raw.text(value) if value is not None else None
    return text if isinstance(text, str) else None


def _order_of(entry):
    """The PlaylistSortOrder name a sort menu entry sets, or None (a view
    sort, or a value ytmusicapi does not know)."""
    for action in nav(entry, ["serviceEndpoint", "playlistEditEndpoint", "actions"], True) or []:
        if isinstance(action, dict) and action.get("action") == "ACTION_SET_PLAYLIST_VIDEO_ORDER":
            try:
                return PlaylistSortOrder(action.get("playlistVideoOrder")).name
            except ValueError:
                return None
    return None


def _sort(response):
    """``(sortOrder, sortTitle, sortOptions)`` from the songs' sort menu: the
    chosen entry's ACTION_SET_PLAYLIST_VIDEO_ORDER value as a
    PlaylistSortOrder name, its served title, and every order the menu
    offers as ``{value, title}`` with the title as served (None when it
    offers none). A chosen view sort (Title, Artist, Album: a dynamic sort
    preference) has no order name; its served title tells what it is."""
    section = nav(response, [*TWO_COLUMN_RENDERER, "secondaryContents", "sectionListRenderer"], True)
    offered = None
    for menu in _walk(section, "sortFilterSubMenuRenderer"):
        entries = [e for e in menu["sortFilterSubMenuRenderer"].get("subMenuItems") or [] if isinstance(e, dict)]
        options = []
        for entry in entries:
            value, title = _order_of(entry), entry.get("title")
            if value is not None and isinstance(title, str) and title \
                    and all(o["value"] != value for o in options):
                options.append({"value": value, "title": title})
        for entry in entries:
            if entry.get("selected") is True:
                title = entry.get("title")
                return _order_of(entry), title if isinstance(title, str) else None, options or None
        offered = offered or options or None
    return None, None, offered


def _dropdown(edit, key):
    return [nav(e, ["dropdownItemRenderer"], True) or {}
            for e in nav(edit, [key, "dropdownRenderer", "entries"], True) or []]


def _vote(edit):
    """``(voteOption, voteOptions)``: the chosen and the offered options of
    the vote dropdown (Collaborators only is offered once collaboration is
    on), matched by value, not label."""
    chosen, offered = None, []
    for entry in _dropdown(edit, "voteDropdown"):
        name = VOTE_VALUES.get(entry.get("int32Value"))
        if name is None:
            continue
        if entry.get("disabled") is not True:
            offered.append(name)
        if entry.get("isSelected") is True:
            chosen = name
    return chosen, offered or None


def _privacy(edit):
    value = edit.get("privacy")
    if value in PRIVACY:
        return value
    for entry in _dropdown(edit, "privacyDropdown"):
        if entry.get("isSelected") is True and str(entry.get("stringValue") or "").upper() in PRIVACY:
            return str(entry["stringValue"]).upper()
    return None


def _collaborators(header):
    """``(collaboration, collaborators)`` from the header's faces: the
    collaborate panel's tag (PAplaylist_collaborate, as ytmusicapi reads
    it) means the playlist is open to collaborators; its label names them.
    Without it nothing is reported."""
    stack = nav(header, ["facepile", "avatarStackViewModel"], True)
    context = nav(stack, ["rendererContext"], True)
    tag = nav(context, ["commandContext", "onTap", "innertubeCommand", "showEngagementPanelEndpoint", "identifier",
                        "tag"], True)
    if tag != "PAplaylist_collaborate":
        return None, None
    label = nav(context, ["accessibilityContext", "label"], True)
    return True, label if isinstance(label, str) else None


def settings(response, playlist_id=None):
    """A playlist page's owner settings (see the module doc); every field
    the page does not serve is None."""
    header_data = nav(response, [*TWO_COLUMN_RENDERER, *TAB_CONTENT, *SECTION_LIST_ITEM], True) or {}
    editable = header_data.get("musicEditablePlaylistDetailHeaderRenderer") if isinstance(header_data, dict) else None
    out = {"playlistId": playlist_id, "owned": isinstance(editable, dict), "title": None, "description": None,
           "privacy": None, "sortOrder": None, "sortTitle": None, "sortOptions": None, "addToTop": None,
           "voteOption": None, "voteOptions": None, "collaboration": None, "collaborators": None,
           "removeImage": None}
    if not out["owned"]:
        return out
    edit = nav(editable, ["editHeader", "musicPlaylistEditHeaderRenderer"], True) or {}
    out["playlistId"] = playlist_id or editable.get("playlistId")
    # The values; editTitle and editDescription are the form's labels.
    out["title"] = _runs_text(edit.get("title"))
    if "description" in edit:
        out["description"] = _runs_text(edit.get("description"))
    elif out["title"] is not None:
        out["description"] = ""  # a served form without one: none set
    out["privacy"] = _privacy(edit)
    out["sortOrder"], out["sortTitle"], out["sortOptions"] = _sort(response)
    out["voteOption"], out["voteOptions"] = _vote(edit)
    header = nav(editable, ["header", "musicResponsiveHeaderRenderer"], True) or {}
    out["collaboration"], out["collaborators"] = _collaborators(header)
    entry = remove_image_entry(response)  # G4
    out["removeImage"] = entry[0] if entry else None
    return out


# ---- duplicates -------------------------------------------------------------------------


def duplicates(result):
    """The video ids YouTube refused as already in the playlist: a failed
    add that offers "Add anyway" (an add action with DEDUPE_OPTION_SKIP;
    matched by the option, not the words). Songs come back in a toast that
    names them; a whole playlist in a "Duplicates" dialog that does not
    (then the list is empty). None when the answer is anything else."""
    if not isinstance(result, dict) or _succeeded(result):
        return None
    found = []
    offered = False
    for action in _walk(result.get("actions"), "dedupeOption"):
        if action.get("dedupeOption") == "DEDUPE_OPTION_SKIP":
            offered = True
            video_id = action.get("addedVideoId")
            if isinstance(video_id, str) and video_id not in found:
                found.append(video_id)
    return found if offered else None


def _add_anyway(worker, playlist_id, video_ids, source):
    """A whole playlist added again with its duplicates, as the web's "Add
    anyway" sends it: ytmusicapi's ``duplicates`` reaches the songs'
    actions only, and YouTube refuses the playlist's again without it.
    Answers like ``add_playlist_items``."""
    actions = [{"action": "ACTION_ADD_VIDEO", "addedVideoId": v, "dedupeOption": "DEDUPE_OPTION_SKIP"}
               for v in video_ids or []]
    actions.append({"action": "ACTION_ADD_PLAYLIST", "addedFullListId": source, "dedupeOption": "DEDUPE_OPTION_SKIP"})
    if not video_ids:
        actions.append({"action": "ACTION_ADD_VIDEO", "dedupeOption": "DEDUPE_OPTION_SKIP"})
    response = worker.yt._send_request("browse/edit_playlist", {"playlistId": playlist_id, "actions": actions})
    if not _succeeded(response):
        return response
    return {"status": response.get("status"),
            "playlistEditResults": [r.get("playlistEditVideoAddedResultData") if isinstance(r, dict) else None
                                    for r in response.get("playlistEditResults") or []]}


# ---- methods ----------------------------------------------------------------------------


def m_edit_playlist(worker, params):
    playlist_id = _playlist_id(params)
    kwargs = {}
    title = _title(params, required=False)
    if title is not None:
        kwargs["title"] = title
    description = _description(params, clearing_ok=False)
    if description is not None:
        kwargs["description"] = description
    privacy = choice(params, "privacy", PRIVACY)
    if privacy is not None:
        kwargs["privacyStatus"] = privacy
    sort = choice(params, "sortOrder", SORT_ORDERS)
    if sort is not None:
        kwargs["sortOrder"] = PlaylistSortOrder[sort]
    add_to_top = opt_bool(params, "addToTop", None)
    if add_to_top is not None:
        kwargs["addToTop"] = add_to_top
    vote = choice(params, "voteOption", VOTE_OPTIONS)
    if vote is not None:
        kwargs["voteOption"] = PlaylistVoteEditOptions[vote]
    collaboration = opt_bool(params, "collaboration", None)
    if collaboration is not None:
        kwargs["collaboration"] = collaboration
    if not kwargs:
        raise WorkerError("BAD_REQUEST", "Nothing to change")
    worker.require_auth()
    if collaboration is True:
        refusal = "YouTube Music would not open the playlist to collaborators (a private playlist cannot have any)"
    elif collaboration is False:
        refusal = "YouTube Music would not close collaboration (is it on for this playlist?)"
    elif vote == "COLLABORATORS_ONLY":
        refusal = "Collaborators only voting needs collaboration on first"
    else:
        refusal = "YouTube Music refused the change"
    try:
        result = worker.yt.edit_playlist(playlist_id, **kwargs)
    except Exception as exc:
        raise _refused(exc, refusal) from None
    changed = sorted(k for k in ("title", "description", "privacy", "sortOrder", "addToTop", "voteOption",
                                 "collaboration") if params.get(k) is not None)
    if collaboration is True:
        token = result.get("joinCollaborationToken") if isinstance(result, dict) and _succeeded(result) else None
        if not isinstance(token, str) or not token:
            raise WorkerError("UPSTREAM", "YouTube Music did not give an invite link")
        return {"playlistId": playlist_id, "changed": changed, "invite": invite_url(playlist_id, token)}
    if not _succeeded(result):
        raise WorkerError("UPSTREAM", "YouTube Music did not change the playlist")
    return {"playlistId": playlist_id, "changed": changed}


def m_move_playlist_item(worker, params):
    playlist_id = _playlist_id(params)
    set_video_id = req_str(params, "setVideoId")
    before = opt_str(params, "beforeSetVideoId", high=128)
    if len(set_video_id) > 128 or before == set_video_id:
        raise WorkerError("BAD_REQUEST", "'beforeSetVideoId' must name another entry")
    worker.require_auth()
    move = (set_video_id, before) if before else set_video_id
    try:
        result = worker.yt.edit_playlist(playlist_id, moveItem=move)
    except Exception as exc:
        raise _refused(exc, "YouTube Music refused the move (the playlist changed; refresh it)") from None
    if not _succeeded(result):
        raise WorkerError("UPSTREAM", "YouTube Music did not move the song")
    return {"playlistId": playlist_id, "setVideoId": set_video_id, "beforeSetVideoId": before}


def _added(result):
    added = result.get("playlistEditResults") if isinstance(result, dict) else None
    # A whole playlist added alone comes back with an empty entry (ytmusicapi
    # asks for one so YouTube answers with the new entries).
    return [{"videoId": a.get("videoId"), "setVideoId": a.get("setVideoId")}
            for a in added or [] if isinstance(a, dict) and a.get("videoId")]


def _checked(result):
    """``result`` of an add, or the error it is (Duplicate, UPSTREAM)."""
    refused = duplicates(result)
    if refused is not None:
        raise Duplicate(refused)
    if not _succeeded(result):
        raise WorkerError("UPSTREAM", "YouTube Music did not add the songs")
    return result


def m_add_to_playlist(worker, params):
    playlist_id = _playlist_id(params)
    video_ids = _video_ids(params)
    source = _opt_playlist_id(params, "sourcePlaylistId")
    if not video_ids and not source:
        raise WorkerError("BAD_REQUEST", "'videoIds' or 'sourcePlaylistId' is needed")
    if source == playlist_id:
        raise WorkerError("BAD_REQUEST", "A playlist cannot be added to itself")
    allow = opt_bool(params, "duplicates", False)
    skip = opt_bool(params, "skipDuplicates", False)  # G4
    if skip and (allow or (video_ids and source)):
        raise WorkerError("BAD_REQUEST", "'skipDuplicates' takes songs or a whole playlist, without 'duplicates'")
    worker.require_auth()
    if skip:
        return _skip_duplicates(worker, playlist_id, video_ids, source)  # G4
    kwargs = {"duplicates": allow}
    if source:
        kwargs["source_playlist"] = source
    try:
        if source and allow:
            result = _add_anyway(worker, playlist_id, video_ids, source)
        else:
            result = worker.yt.add_playlist_items(playlist_id, video_ids, **kwargs)
    except Exception as exc:
        raise _refused(exc, "YouTube Music refused to add to that playlist") from None
    return {"added": _added(_checked(result))}


# ---- G4: Skip duplicates, Remove custom image -----------------------------------------------

NOTHING_NEW = "Nothing new to add"


def _skip_duplicates(worker, playlist_id, video_ids, source):
    """The web's Skip duplicates. A whole playlist: the Duplicates dialog's
    command, its ACTION_ADD_PLAYLIST with DEDUPE_OPTION_DROP_DUPLICATE (one
    request; YouTube answers with no entries for it). Songs: added as
    usual. A refusal adds nothing (ytmusicapi), so then the refusal's own
    Skip duplicates command goes when it serves one (served_skip), else the
    songs not there yet (_already_there) are added; none left (or a served
    command that adds none) is ``status`` NOTHING_NEW."""
    try:
        if source:
            action = {"action": "ACTION_ADD_PLAYLIST", "addedFullListId": source,
                      "dedupeOption": "DEDUPE_OPTION_DROP_DUPLICATE"}
            result = worker.yt._send_request("browse/edit_playlist", {"playlistId": playlist_id, "actions": [action]})
            return {"added": _added(_checked(result)), "skipped": []}
        result = worker.yt.add_playlist_items(playlist_id, video_ids, duplicates=False)
        refused = duplicates(result)
        if refused is None:
            return {"added": _added(_checked(result)), "skipped": []}
        command = served_skip(result, playlist_id, video_ids)
        if command is not None:
            added = _added(_unwrapped(_checked(worker.yt._send_request("browse/edit_playlist", command))))
            wanted = list(dict.fromkeys(video_ids))
            if not added:
                # Measured live (2026-11-11): this command answers with an entry
                # per song it adds, and with none when every song was already
                # there.
                return {"added": [], "skipped": wanted, "status": NOTHING_NEW}
            got = {a["videoId"] for a in added}
            return {"added": added, "skipped": [v for v in wanted if v not in got]}
        there = _already_there(worker, playlist_id, video_ids, refused)
        rest = [v for v in video_ids if v not in there]
        if not rest:
            return {"added": [], "skipped": there, "status": NOTHING_NEW}
        result = worker.yt.add_playlist_items(playlist_id, rest, duplicates=False)
    except WorkerError:
        raise
    except Exception as exc:
        raise _refused(exc, "YouTube Music refused to add to that playlist") from None
    return {"added": _added(_checked(result)), "skipped": there}


def served_skip(result, playlist_id, video_ids):
    """The body of a refusal's own Skip duplicates command for songs (as the
    whole-playlist Duplicates dialog serves one): a playlistEditEndpoint for
    this playlist whose actions all add songs of ``video_ids`` with
    DEDUPE_OPTION_DROP_DUPLICATE. None when none is served, so nothing else
    is ever sent from a refusal."""
    wanted = set(video_ids or ())
    for found in _walk(result.get("actions") if isinstance(result, dict) else None, "playlistEditEndpoint"):
        edit = found.get("playlistEditEndpoint")
        actions = edit.get("actions") if isinstance(edit, dict) else None
        if not isinstance(actions, list) or not actions \
                or _strip_vl(str(edit.get("playlistId") or "")) != playlist_id:
            continue
        named = [a.get("addedVideoId") for a in actions if isinstance(a, dict)]
        if all(isinstance(a, dict) and a.get("action") == "ACTION_ADD_VIDEO"
               and a.get("dedupeOption") == "DEDUPE_OPTION_DROP_DUPLICATE" for a in actions) \
                and any(named) and all(v is None or v in wanted for v in named):
            body = {"playlistId": playlist_id, "actions": actions}
            if edit.get("params"):
                body["params"] = edit["params"]
            return body
    return None


def _unwrapped(response):
    """A raw edit_playlist answer with its entries unwrapped, as
    ``add_playlist_items`` hands them back."""
    if not _succeeded(response):
        return response
    return {"status": response.get("status"),
            "playlistEditResults": [r.get("playlistEditVideoAddedResultData") if isinstance(r, dict) else None
                                    for r in response.get("playlistEditResults") or []]}


def _already_there(worker, playlist_id, video_ids, refused):
    """The songs of ``video_ids`` already in the playlist, once YouTube
    refused to add them. One song refused is there. A refusal that names
    some of several names the ones there; one that names every song (or
    none) may only be repeating the request in its Add anyway, as the
    measured whole-playlist dialog does, so the playlist itself is read (its
    videoIds) to tell."""
    wanted = list(dict.fromkeys(video_ids))
    if len(wanted) == 1:
        return wanted
    named = [v for v in wanted if v in refused]
    if named and len(named) < len(wanted):
        return named
    data = worker.yt.get_playlist(playlist_id, limit=None)
    present = {t.get("videoId") for t in (data or {}).get("tracks") or [] if isinstance(t, dict)}
    there = [v for v in wanted if v in present]
    if not there:
        raise WorkerError("UPSTREAM", "YouTube Music did not say which songs are already there")
    return there


def _thumbnail_menu(response):
    """The menu items of an owned playlist header's cover edit button
    (thumbnailEditButton), ``[]`` when it has none."""
    for found in _walk(response, "thumbnailEditButton"):
        button = found.get("thumbnailEditButton")
        items = nav(button, ["buttonRenderer", "command", "openPopupAction", "popup", "menuPopupRenderer", "items"],
                    True)
        return items if isinstance(items, list) else []
    return []


def remove_image_entry(response):
    """``(title, edit)``: the cover menu's entry that is a playlist edit
    (Remove custom image; the other entry uploads an image) with its served
    playlistEditEndpoint, matched by the endpoint, never the words. None
    when it is not served."""
    for item in _thumbnail_menu(response):
        data = next(iter(item.values()), None) if isinstance(item, dict) and item else None
        if not isinstance(data, dict):
            continue
        endpoint = data.get("serviceEndpoint") or data.get("navigationEndpoint")
        edit = endpoint.get("playlistEditEndpoint") if isinstance(endpoint, dict) else None
        if isinstance(edit, dict) and edit.get("actions"):
            return _runs_text(data.get("text")) or "Remove custom image", edit
    return None


def m_remove_playlist_image(worker, params):
    playlist_id = _playlist_id(params)
    worker.require_auth()
    entry = remove_image_entry(worker.yt._send_request("browse", {"browseId": "VL" + playlist_id}))
    if entry is None:
        raise WorkerError("NOT_FOUND", "YouTube Music offers no custom image to remove on this playlist")
    edit = entry[1]
    body = {"playlistId": _strip_vl(str(edit.get("playlistId") or playlist_id)), "actions": edit["actions"]}
    if edit.get("params"):
        body["params"] = edit["params"]
    if body["playlistId"] != playlist_id:
        raise WorkerError("UPSTREAM", "YouTube Music served the edit for another playlist")
    try:
        result = worker.yt._send_request("browse/edit_playlist", body)
    except Exception as exc:
        raise _refused(exc, "YouTube Music refused to remove the custom image") from None
    if not _succeeded(result):
        raise WorkerError("UPSTREAM", "YouTube Music did not remove the custom image")
    return {"playlistId": playlist_id}
# ---- end G4


def m_create_playlist(worker, params):
    title = _title(params, required=True)
    description = _description(params, clearing_ok=True) or ""
    privacy = choice(params, "privacy", PRIVACY, "PRIVATE")
    video_ids = _video_ids(params)
    source = _opt_playlist_id(params, "sourcePlaylistId")
    if video_ids and source:
        raise WorkerError("BAD_REQUEST", "A new playlist takes 'videoIds' or 'sourcePlaylistId', not both")
    worker.require_auth()
    kwargs = {"privacy_status": privacy, "video_ids": video_ids}
    if source:
        kwargs["source_playlist"] = source
    refusal = ("YouTube Music refused to copy that playlist (can it be copied?)" if source
               else "YouTube Music refused to create the playlist")
    try:
        result = worker.yt.create_playlist(title, description, **kwargs)
    except Exception as exc:
        raise _refused(exc, refusal) from None
    if isinstance(result, str) and result:
        return {"playlistId": result}
    raise WorkerError("UPSTREAM", "YouTube Music did not create the playlist")


def m_join_playlist(worker, params):
    playlist_id = _playlist_id(params)
    token = params.get("token")
    # The message never repeats the token.
    if not isinstance(token, str) or not _TOKEN.match(token):
        raise WorkerError("BAD_REQUEST", "'token' is not an invite token")
    worker.require_auth()
    try:
        result = worker.yt.join_collaborative_playlist(playlist_id, token)
    except Exception as exc:
        refused = _refused(exc, "YouTube Music would not let you join (already a collaborator, or the link expired)")
        if refused is exc and token in str(exc):
            # An error that repeats the token would carry it into the log and the reply.
            refused = WorkerError("UPSTREAM", "YouTube Music did not let you join the playlist")
        raise refused from None
    if not _succeeded(result):
        raise WorkerError("UPSTREAM", "YouTube Music did not let you join the playlist")
    return {"playlistId": playlist_id}


def m_playlist_settings(worker, params):
    playlist_id = _playlist_id(params)
    worker.require_auth()
    response = worker.yt._send_request("browse", {"browseId": "VL" + playlist_id})
    return settings(response, playlist_id)


def m_playlist_suggestions(worker, params):
    playlist_id = _playlist_id(params)
    worker.require_auth()
    data = None
    with raw.capture(("browse",)) as cap:
        try:
            # limit 0: no songs past the page's first ones (the suggestions are
            # what is wanted), so 3 requests in all.
            data = worker.yt.get_playlist(playlist_id, limit=0, suggestions_limit=SUGGESTIONS)
        except (KeyError, IndexError, TypeError) as exc:
            # An empty playlist has no suggestions shelf to continue from.
            norm.log(f"playlist suggestions skipped: {type(exc).__name__}")
        first = cap.first("browse")
    page = settings(first, playlist_id) if isinstance(first, dict) else None
    owned = bool(data.get("owned")) if isinstance(data, dict) else bool(page and page["owned"])
    items = norm.normalize_list((data or {}).get("suggestions") if owned else [], norm.track, "suggestion")
    for item in items:
        # A suggestion is no entry of the playlist: nothing may remove it by this.
        item["setVideoId"] = None
    return {"playlistId": playlist_id, "owned": owned, "items": items[:SUGGESTIONS], "settings": page}


# ---- registry ---------------------------------------------------------------------------

# The library's playlist lists and Recent activity (a new title, a new
# playlist, a joined one), and the playlist's own page.
_LIBRARY = [
    ("libraryPlaylists", None, None),
    ("libraryLanding", None, None),
    ("browsePage", Fixed("FEmusic_liked_playlists", "FEmusic_library_landing"), "browseId"),
]
_PLAYLIST = [
    ("playlist", "playlistId", "playlistId"),
    ("browsePage", Prefixed("VL", "playlistId"), "browseId"),
    *_LIBRARY,
]

METHODS = {
    "editPlaylist": m_edit_playlist,
    "movePlaylistItem": m_move_playlist_item,
    "addToPlaylist": m_add_to_playlist,
    "createPlaylist": m_create_playlist,
    "joinPlaylist": m_join_playlist,
    "playlistSettings": m_playlist_settings,
    "playlistSuggestions": m_playlist_suggestions,
    "removePlaylistImage": m_remove_playlist_image,  # G4
}
CACHEABLE = set()
LANES = {"playlistSuggestions": "pages"}
INVALIDATES = {
    "editPlaylist": _PLAYLIST,
    "movePlaylistItem": _PLAYLIST,
    "addToPlaylist": _PLAYLIST,
    "createPlaylist": _LIBRARY,
    "joinPlaylist": _PLAYLIST,
    "removePlaylistImage": _PLAYLIST,  # G4: the cover shows in the library's lists too
}
RECORDERS = {}
MUTATES = {"editPlaylist", "movePlaylistItem", "addToPlaylist", "createPlaylist", "joinPlaylist",
           "removePlaylistImage"}
