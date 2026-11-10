"""ytm_edit (S13): playlist editing, moves, adds with duplicates, copies, joins.

Every edit is pinned against a fake client: the real ones change the account,
so they only run live on a throwaway playlist. Settings and suggestions are
read from hand-built responses shaped like the live ones (an owned playlist's
edit header and its songs' sort menu)."""

import pytest
from ytmusicapi.exceptions import YTMusicServerError
from ytmusicapi.models.content.enums import PlaylistSortOrder, PlaylistVoteEditOptions

import ytm
import ytm_edit as edit

TOKEN = "kM9wXdRj2p8v_qL3sHBkTz"


class FakeYT:
    """Records every call; `responses[name]` is a value, a callable or an exception."""

    def __init__(self, **responses):
        self.responses = dict({"get_account_info": {"accountName": "x"}}, **responses)
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            value = self.responses.get(name)
            if callable(value):
                return value(*args, **kwargs)
            if isinstance(value, Exception):
                raise value
            return value

        return method

    def edits(self):
        return [c for c in self.calls if c[0] != "get_account_info"]


def call(worker, method, **params):
    return worker.handle({"id": 9, "method": method, "params": params})


def signed_in(**responses):
    return ytm.Worker(FakeYT(**responses), "ok")


# ---- editPlaylist -----------------------------------------------------------------------


def test_every_edit_playlist_kwarg_and_enum_is_pinned():
    worker = signed_in(edit_playlist="STATUS_SUCCEEDED")
    reply = call(worker, "editPlaylist", playlistId="VLPLx", title=" New name ", description="About it",
                 privacy="UNLISTED", sortOrder="NEWEST_FIRST", addToTop=False, voteOption="EVERYONE_CAN_VOTE")
    assert reply["ok"], reply
    assert worker.yt.edits() == [("edit_playlist", ("PLx",), {
        "title": "New name", "description": "About it", "privacyStatus": "UNLISTED",
        "sortOrder": PlaylistSortOrder.NEWEST_FIRST, "addToTop": False,
        "voteOption": PlaylistVoteEditOptions.EVERYONE_CAN_VOTE})], "VL stripped, only the fields given"
    assert reply["result"] == {"playlistId": "PLx", "changed": ["addToTop", "description", "privacy", "sortOrder",
                                                                "title", "voteOption"]}


@pytest.mark.parametrize("name", ["MANUAL", "NEWEST_FIRST", "NEWEST_LAST", "TOP_VOTED"])
def test_every_sort_order_maps_to_its_enum(name):
    worker = signed_in(edit_playlist="STATUS_SUCCEEDED")
    assert call(worker, "editPlaylist", playlistId="PLx", sortOrder=name)["ok"]
    assert worker.yt.edits()[-1][2] == {"sortOrder": PlaylistSortOrder[name]}


@pytest.mark.parametrize("name", ["EVERYONE_CAN_VOTE", "COLLABORATORS_ONLY", "OFF"])
def test_every_vote_option_maps_to_its_enum(name):
    worker = signed_in(edit_playlist="STATUS_SUCCEEDED")
    assert call(worker, "editPlaylist", playlistId="PLx", voteOption=name)["ok"]
    assert worker.yt.edits()[-1][2] == {"voteOption": PlaylistVoteEditOptions[name]}


def test_add_to_top_true_and_false_are_both_sent():
    worker = signed_in(edit_playlist="STATUS_SUCCEEDED")
    for value in (True, False):
        assert call(worker, "editPlaylist", playlistId="PLx", addToTop=value)["ok"]
        assert worker.yt.edits()[-1][2] == {"addToTop": value}


@pytest.mark.parametrize("params", [
    {"sortOrder": "OLDEST"},
    {"sortOrder": 1},
    {"voteOption": "ALL"},
    {"privacy": "FRIENDS"},
    {"addToTop": "yes"},
    {"collaboration": 1},
    {"title": ""},
    {"title": "   "},
    {"title": "a <b>"},
    {"title": "a > b"},
    {"title": "x" * 151},
    {"description": ""},
    {"description": "  "},
    {},
])
def test_bad_edits_never_reach_youtube(params):
    worker = signed_in(edit_playlist="STATUS_SUCCEEDED")
    reply = call(worker, "editPlaylist", playlistId="PLx", **params)
    assert reply["error"]["code"] == "BAD_REQUEST", params
    assert worker.yt.calls == [] and reply["http"] == 0


def test_an_edit_without_a_playlist_is_bad():
    assert call(signed_in(), "editPlaylist", title="t")["error"]["code"] == "BAD_REQUEST"
    assert call(signed_in(), "editPlaylist", playlistId="PL x", title="t")["error"]["code"] == "BAD_REQUEST"


@pytest.mark.parametrize("answer", ["STATUS_FAILED", {"error": {"code": 400}}, None])
def test_an_edit_youtube_does_not_confirm_is_upstream(answer):
    reply = call(signed_in(edit_playlist=answer), "editPlaylist", playlistId="PLx", title="t")
    assert reply["error"]["code"] == "UPSTREAM"


def test_edits_need_a_session():
    worker = ytm.Worker(FakeYT(edit_playlist="STATUS_SUCCEEDED"), "none")
    assert call(worker, "editPlaylist", playlistId="PLx", title="t")["error"]["code"] == "AUTH_REQUIRED"
    assert worker.yt.edits() == []


def test_collaboration_returns_the_invite_link():
    worker = signed_in(edit_playlist={"status": "STATUS_SUCCEEDED", "joinCollaborationToken": TOKEN})
    reply = call(worker, "editPlaylist", playlistId="PLx", collaboration=True)
    assert worker.yt.edits() == [("edit_playlist", ("PLx",), {"collaboration": True})]
    assert reply["result"] == {"playlistId": "PLx", "changed": ["collaboration"],
                               "invite": f"https://music.youtube.com/playlist?list=PLx&jct={TOKEN}"}
    assert edit.parse_invite(reply["result"]["invite"]) == ("PLx", TOKEN)


def test_collaboration_without_a_token_is_upstream():
    reply = call(signed_in(edit_playlist={"status": "STATUS_FAILED"}), "editPlaylist", playlistId="PLx",
                 collaboration=True)
    assert reply["error"]["code"] == "UPSTREAM"


def test_closing_collaboration_sends_false():
    worker = signed_in(edit_playlist="STATUS_SUCCEEDED")
    assert call(worker, "editPlaylist", playlistId="PLx", collaboration=False)["result"]["changed"] == ["collaboration"]
    assert worker.yt.edits() == [("edit_playlist", ("PLx",), {"collaboration": False})]


@pytest.mark.parametrize("status", [400, 401, 403])
def test_a_refused_edit_is_upstream_and_keeps_the_session(status):
    error = YTMusicServerError(f"Server returned HTTP {status}: Forbidden.\nPrecondition check failed.")
    worker = signed_in(edit_playlist=error)
    reply = call(worker, "editPlaylist", playlistId="PLx", collaboration=status != 400)
    assert reply["error"]["code"] == "UPSTREAM", reply
    assert worker.auth == "ok", "a refusal is not a lost sign-in"
    assert "collaborat" in reply["error"]["message"]


@pytest.mark.parametrize("status", [400, 401, 403])
@pytest.mark.parametrize("source", [None, "PLsource"])
def test_a_refused_create_or_copy_is_upstream_and_keeps_the_session(status, source):
    error = YTMusicServerError(f"Server returned HTTP {status}: Forbidden.\nPrecondition check failed.")
    worker = signed_in(create_playlist=error)
    params = {"title": "Copy"} if source is None else {"title": "Copy", "sourcePlaylistId": source}
    reply = call(worker, "createPlaylist", **params)
    assert reply["error"]["code"] == "UPSTREAM", reply
    assert worker.auth == "ok", "a refusal is not a lost sign-in"
    assert ("copy" in reply["error"]["message"]) == (source is not None)


def test_other_failures_keep_their_codes():
    import requests

    worker = signed_in(edit_playlist=requests.exceptions.ConnectionError("down"))
    assert call(worker, "editPlaylist", playlistId="PLx", title="t")["error"]["code"] == "NETWORK"
    worker = signed_in(edit_playlist=YTMusicServerError("Server returned HTTP 429: Too Many Requests."))
    assert call(worker, "editPlaylist", playlistId="PLx", title="t")["error"]["code"] == "RATE_LIMITED"


# ---- movePlaylistItem ---------------------------------------------------------------------


def test_a_move_before_another_entry_sends_a_tuple():
    worker = signed_in(edit_playlist="STATUS_SUCCEEDED")
    reply = call(worker, "movePlaylistItem", playlistId="VLPLx", setVideoId="S3", beforeSetVideoId="S1")
    assert worker.yt.edits() == [("edit_playlist", ("PLx",), {"moveItem": ("S3", "S1")})]
    assert reply["result"] == {"playlistId": "PLx", "setVideoId": "S3", "beforeSetVideoId": "S1"}


def test_a_move_to_the_end_sends_the_entry_alone():
    worker = signed_in(edit_playlist="STATUS_SUCCEEDED")
    reply = call(worker, "movePlaylistItem", playlistId="PLx", setVideoId="S1")
    assert worker.yt.edits() == [("edit_playlist", ("PLx",), {"moveItem": "S1"})]
    assert reply["result"]["beforeSetVideoId"] is None


def test_bad_and_failed_moves():
    worker = signed_in(edit_playlist="STATUS_SUCCEEDED")
    for params in ({}, {"setVideoId": ""}, {"setVideoId": "S1", "beforeSetVideoId": "S1"},
                   {"setVideoId": "S1", "beforeSetVideoId": 5}):
        assert call(worker, "movePlaylistItem", playlistId="PLx", **params)["error"]["code"] == "BAD_REQUEST", params
    assert worker.yt.edits() == []
    reply = call(signed_in(edit_playlist="STATUS_FAILED"), "movePlaylistItem", playlistId="PLx", setVideoId="S1")
    assert reply["error"]["code"] == "UPSTREAM"


# ---- addToPlaylist ---------------------------------------------------------------------------

ADDED = {"status": "STATUS_SUCCEEDED", "playlistEditResults": [{"videoId": "a", "setVideoId": "S1"}]}
# What YouTube answers for a song already in the playlist (measured on a throwaway
# playlist): a failed status and a toast whose "Add anyway" button repeats the add
# with DEDUPE_OPTION_SKIP. ytmusicapi hands the whole response back.
DUPLICATE = {
    "status": "STATUS_FAILED",
    "actions": [{"addToToastAction": {"item": {"notificationActionRenderer": {
        "responseText": {"runs": [{"text": "This track is already in the playlist"}]},
        "actionButton": {"buttonRenderer": {
            "text": {"runs": [{"text": "Add anyway"}]},
            "command": {"playlistEditEndpoint": {"playlistId": "PLx", "actions": [
                {"addedVideoId": "nbCOAPR33ME", "action": "ACTION_ADD_VIDEO", "dedupeOption": "DEDUPE_OPTION_SKIP"},
            ]}}}}}}}}],
}


def test_songs_are_added_as_before():
    worker = signed_in(add_playlist_items=ADDED)
    reply = call(worker, "addToPlaylist", playlistId="PLx", videoIds=["a"])
    assert reply["result"] == {"added": [{"videoId": "a", "setVideoId": "S1"}]}
    assert worker.yt.edits() == [("add_playlist_items", ("PLx", ["a"]), {"duplicates": False})]


def test_a_song_already_there_is_duplicate():
    worker = signed_in(add_playlist_items=DUPLICATE)
    reply = call(worker, "addToPlaylist", playlistId="PLx", videoIds=["nbCOAPR33ME"])
    assert reply["error"]["code"] == "DUPLICATE"
    assert reply["error"]["videoIds"] == ["nbCOAPR33ME"]
    assert edit.duplicates(DUPLICATE) == ["nbCOAPR33ME"]
    assert edit.duplicates({"status": "STATUS_FAILED"}) is None, "no Add anyway: a plain failure"
    assert edit.duplicates(ADDED) is None


# A whole playlist (album) with songs already there (measured on a throwaway
# playlist): a "Duplicates" dialog that names no song; its Add anyway repeats
# the add with DEDUPE_OPTION_SKIP on both actions.
SOURCE_DUPLICATE = {
    "status": "STATUS_FAILED",
    "actions": [{"confirmDialogEndpoint": {"content": {"confirmDialogRenderer": {
        "title": {"runs": [{"text": "Duplicates"}]},
        "confirmButton": {"buttonRenderer": {"text": {"runs": [{"text": "Add anyway"}]}, "command": {
            "playlistEditEndpoint": {"playlistId": "PLx", "actions": [
                {"action": "ACTION_ADD_PLAYLIST", "addedFullListId": "OLAK5uy_album", "dedupeOption": "DEDUPE_OPTION_SKIP"},
                {"action": "ACTION_ADD_VIDEO", "dedupeOption": "DEDUPE_OPTION_SKIP"}]}}}},
        "cancelButton": {"buttonRenderer": {"text": {"runs": [{"text": "Skip duplicates"}]}, "command": {
            "playlistEditEndpoint": {"playlistId": "PLx", "actions": [
                {"action": "ACTION_ADD_PLAYLIST", "addedFullListId": "OLAK5uy_album",
                 "dedupeOption": "DEDUPE_OPTION_DROP_DUPLICATE"}]}}}}}}}}],
}


def test_a_whole_album_with_songs_already_there_is_duplicate():
    worker = signed_in(add_playlist_items=SOURCE_DUPLICATE)
    reply = call(worker, "addToPlaylist", playlistId="PLx", sourcePlaylistId="OLAK5uy_album")
    assert reply["error"]["code"] == "DUPLICATE" and reply["error"]["videoIds"] == []
    assert reply["error"]["message"] == "Some of its songs are already in that playlist"
    assert edit.duplicates(SOURCE_DUPLICATE) == []


def test_a_whole_album_added_anyway_goes_as_the_web_sends_it():
    answer = {"status": "STATUS_SUCCEEDED", "playlistEditResults": [
        {"playlistEditVideoAddedResultData": {"videoId": "b", "setVideoId": "S2"}}, {}]}
    worker = signed_in(_send_request=answer)
    reply = call(worker, "addToPlaylist", playlistId="PLx", sourcePlaylistId="OLAK5uy_album", duplicates=True)
    assert reply["result"] == {"added": [{"videoId": "b", "setVideoId": "S2"}]}
    assert worker.yt.edits() == [("_send_request", ("browse/edit_playlist", {"playlistId": "PLx", "actions": [
        {"action": "ACTION_ADD_PLAYLIST", "addedFullListId": "OLAK5uy_album", "dedupeOption": "DEDUPE_OPTION_SKIP"},
        {"action": "ACTION_ADD_VIDEO", "dedupeOption": "DEDUPE_OPTION_SKIP"}]}), {})]
    worker = signed_in(_send_request=answer)
    call(worker, "addToPlaylist", playlistId="PLx", videoIds=["a"], sourcePlaylistId="PLs", duplicates=True)
    assert worker.yt.edits()[0][1][1]["actions"] == [
        {"action": "ACTION_ADD_VIDEO", "addedVideoId": "a", "dedupeOption": "DEDUPE_OPTION_SKIP"},
        {"action": "ACTION_ADD_PLAYLIST", "addedFullListId": "PLs", "dedupeOption": "DEDUPE_OPTION_SKIP"}]
    failed = call(signed_in(_send_request={"status": "STATUS_FAILED"}), "addToPlaylist", playlistId="PLx",
                  sourcePlaylistId="PLs", duplicates=True)
    assert failed["error"]["code"] == "UPSTREAM"


def test_add_anyway_sends_duplicates_true():
    worker = signed_in(add_playlist_items=ADDED)
    assert call(worker, "addToPlaylist", playlistId="PLx", videoIds=["a"], duplicates=True)["ok"]
    assert worker.yt.edits()[-1][2] == {"duplicates": True}


def test_a_whole_playlist_or_album_is_added_by_its_id():
    # ytmusicapi adds an empty entry to get the new set ids back: it comes back as None.
    answer = {"status": "STATUS_SUCCEEDED", "playlistEditResults": [None, {"videoId": "b", "setVideoId": "S2"}]}
    worker = signed_in(add_playlist_items=answer)
    reply = call(worker, "addToPlaylist", playlistId="PLx", sourcePlaylistId="VLOLAK5uy_album")
    assert reply["result"] == {"added": [{"videoId": "b", "setVideoId": "S2"}]}
    assert worker.yt.edits() == [("add_playlist_items", ("PLx", None),
                                  {"duplicates": False, "source_playlist": "OLAK5uy_album"})]


def test_bad_adds():
    worker = signed_in(add_playlist_items=ADDED)
    for params in ({}, {"videoIds": []}, {"videoIds": "a"}, {"videoIds": [5]}, {"videoIds": ["a b"]},
                   {"sourcePlaylistId": "PLx"}, {"sourcePlaylistId": "VLPLx"}, {"videoIds": ["a"], "duplicates": 1}):
        assert call(worker, "addToPlaylist", playlistId="PLx", **params)["error"]["code"] == "BAD_REQUEST", params
    assert worker.yt.edits() == []
    reply = call(signed_in(add_playlist_items={"status": "STATUS_FAILED"}), "addToPlaylist", playlistId="P",
                 videoIds=["a"])
    assert reply["error"]["code"] == "UPSTREAM"


# ---- createPlaylist ---------------------------------------------------------------------------


def test_create_sends_description_privacy_songs_and_source():
    worker = signed_in(create_playlist="PLnew")
    reply = call(worker, "createPlaylist", title=" Mix ", description="Saved queue", privacy="UNLISTED",
                 videoIds=["a", "b"])
    assert reply["result"] == {"playlistId": "PLnew"}
    assert worker.yt.edits() == [("create_playlist", ("Mix", "Saved queue"),
                                  {"privacy_status": "UNLISTED", "video_ids": ["a", "b"]})]
    reply = call(worker, "createPlaylist", title="Copy", sourcePlaylistId="VLPLsource")
    assert reply["ok"]
    assert worker.yt.edits()[-1] == ("create_playlist", ("Copy", ""), {"privacy_status": "PRIVATE", "video_ids": None,
                                                                       "source_playlist": "PLsource"})


def test_bad_creates():
    worker = signed_in(create_playlist="PLnew")
    for params in ({}, {"title": ""}, {"title": "a<b"}, {"title": "t", "privacy": "OPEN"},
                   {"title": "t", "videoIds": "a"}, {"title": "t", "description": 5},
                   {"title": "t", "videoIds": ["a"], "sourcePlaylistId": "PLs"}):
        assert call(worker, "createPlaylist", **params)["error"]["code"] == "BAD_REQUEST", params
    assert worker.yt.edits() == []


# ---- joinPlaylist ---------------------------------------------------------------------------


def test_join_sends_the_token_once():
    worker = signed_in(join_collaborative_playlist="STATUS_SUCCEEDED")
    reply = call(worker, "joinPlaylist", playlistId="PLx", token=TOKEN)
    assert reply["result"] == {"playlistId": "PLx"}
    assert worker.yt.edits() == [("join_collaborative_playlist", ("PLx", TOKEN), {})]


@pytest.mark.parametrize("url, expected", [
    (f"https://music.youtube.com/playlist?list=PLx&jct={TOKEN}", ("PLx", TOKEN)),
    (f"https://music.youtube.com/playlist?jct={TOKEN}&list=VLPLx&si=abc", ("PLx", TOKEN)),
    (f" https://www.youtube.com/playlist?list=PLx&jct={TOKEN} ", ("PLx", TOKEN)),
    ("https://music.youtube.com/playlist?list=PLx", None),
    (f"https://music.youtube.com/watch?v=abc&list=PLx&jct={TOKEN}", None),
    (f"https://evil.example/playlist?list=PLx&jct={TOKEN}", None),
    (f"https://youtube.com.evil.example/playlist?list=PLx&jct={TOKEN}", None),
    (f"ftp://music.youtube.com/playlist?list=PLx&jct={TOKEN}", None),
    ("not a url", None),
    (None, None),
])
def test_parse_invite(url, expected):
    assert edit.parse_invite(url) == expected


def test_the_join_token_never_reaches_a_log(capsys, monkeypatch):
    monkeypatch.setenv("YTM_DEBUG", "1")
    failures = [
        "STATUS_FAILED",
        YTMusicServerError(f"Server returned HTTP 401: Unauthorized.\n{TOKEN}"),
        YTMusicServerError(f"Server returned HTTP 500: {TOKEN}"),
        KeyError(f"Unable to find 'x' on {{'jct': '{TOKEN}'}}"),
        "STATUS_SUCCEEDED",
    ]
    for answer in failures:
        worker = signed_in(join_collaborative_playlist=answer)
        reply = call(worker, "joinPlaylist", playlistId="PLx", token=TOKEN)
        assert TOKEN not in repr(reply)
    bad = call(signed_in(), "joinPlaylist", playlistId="PLx", token=TOKEN + " !")
    assert bad["error"]["code"] == "BAD_REQUEST" and TOKEN not in repr(bad)
    captured = capsys.readouterr()
    assert "joinPlaylist" in captured.err, "the debug lines were written"
    assert TOKEN not in captured.err + captured.out


def test_the_invite_link_never_reaches_a_log(capsys, monkeypatch):
    monkeypatch.setenv("YTM_DEBUG", "1")
    worker = signed_in(edit_playlist={"status": "STATUS_SUCCEEDED", "joinCollaborationToken": TOKEN})
    assert TOKEN in call(worker, "editPlaylist", playlistId="PLx", collaboration=True)["result"]["invite"]
    captured = capsys.readouterr()
    assert "editPlaylist ok" in captured.err and TOKEN not in captured.err + captured.out


def test_cli_join_prints_no_token(capsys, monkeypatch):
    worker = signed_in(join_collaborative_playlist="STATUS_SUCCEEDED")
    monkeypatch.setattr(ytm, "Worker", lambda: worker)
    assert ytm.main(["join", f"https://music.youtube.com/playlist?list=PLx&jct={TOKEN}"]) == 0
    out = capsys.readouterr()
    assert '"playlistId": "PLx"' in out.out and TOKEN not in out.out + out.err
    assert ytm.main(["join", "https://music.youtube.com/playlist?list=PLx"]) == 2


# ---- playlistSettings and playlistSuggestions ------------------------------------------------


def _sort_item(title, selected, order=None, dynamic=None):
    action = ({"action": "ACTION_SET_PLAYLIST_VIDEO_ORDER", "playlistVideoOrder": order} if order is not None
              else {"action": "ACTION_SET_PLAYLIST_DYNAMIC_SORT_PREFERENCE", "playlistDynamicSortPreference": dynamic})
    return {"title": title, "selected": selected,
            "serviceEndpoint": {"playlistEditEndpoint": {"playlistId": "PLx", "actions": [action]}}}


def _vote_entry(label, value, selected, disabled=False):
    entry = {"label": {"runs": [{"text": label}]}, "isSelected": selected, "int32Value": value}
    if disabled:
        entry["disabled"] = True
    return {"dropdownItemRenderer": entry}


def owned_page(edit_header=None, sort=None, facepile=None, owned=True):
    """A playlist browse response shaped like the live one."""
    header = {"title": {"runs": [{"text": "Mine"}]}}
    if facepile:
        header["facepile"] = facepile
    if owned:
        top = {"musicEditablePlaylistDetailHeaderRenderer": {
            "header": {"musicResponsiveHeaderRenderer": header},
            "editHeader": {"musicPlaylistEditHeaderRenderer": edit_header or {}},
            "playlistId": "PLx"}}
    else:
        top = {"musicResponsiveHeaderRenderer": header}
    shelf = {"musicPlaylistShelfRenderer": {"contents": []}}
    if sort is not None:
        shelf["musicPlaylistShelfRenderer"]["header"] = {"musicSideAlignedItemRenderer": {"startItems": [
            {"sortFilterSubMenuRenderer": {"title": "Sort", "subMenuItems": sort}}]}}
    return {"contents": {"twoColumnBrowseResultsRenderer": {
        "tabs": [{"tabRenderer": {"content": {"sectionListRenderer": {"contents": [top]}}}}],
        "secondaryContents": {"sectionListRenderer": {"contents": [shelf]}}}}}


# The live form: title and description hold the values, editTitle and
# editDescription the field labels.
FULL_EDIT = {
    "title": {"runs": [{"text": "Mine"}]},
    "editTitle": {"runs": [{"text": "Title"}]},
    "description": {"runs": [{"text": "About "}, {"text": "it"}]},
    "editDescription": {"runs": [{"text": "Description"}]},
    "privacy": "UNLISTED",
    "voteDropdown": {"dropdownRenderer": {"entries": [
        _vote_entry("Everyone", 1, False), _vote_entry("Collaborators only", 2, False, disabled=True),
        _vote_entry("Voting off", 3, True)]}},
}
SORT = [_sort_item("Top voted", False, order=6), _sort_item("Manual ordering", False, order=0),
        _sort_item("Newest first", True, order=1), _sort_item("Oldest first", False, order=2),
        _sort_item("Title", False, dynamic=1)]
COLLAB_FACES = {"avatarStackViewModel": {"rendererContext": {
    "accessibilityContext": {"label": "by You and 1 other"},
    "commandContext": {"onTap": {"innertubeCommand": {"showEngagementPanelEndpoint": {
        "identifier": {"tag": "PAplaylist_collaborate"}}}}}}}}


def test_settings_from_an_owned_page():
    out = edit.settings(owned_page(FULL_EDIT, SORT, COLLAB_FACES), "PLx")
    assert out == {"playlistId": "PLx", "owned": True, "title": "Mine", "description": "About it",
                   "privacy": "UNLISTED", "sortOrder": "NEWEST_FIRST", "sortTitle": "Newest first",
                   "sortOptions": [{"value": "TOP_VOTED", "title": "Top voted"},
                                   {"value": "MANUAL", "title": "Manual ordering"},
                                   {"value": "NEWEST_FIRST", "title": "Newest first"},
                                   {"value": "NEWEST_LAST", "title": "Oldest first"}],
                   "addToTop": None, "voteOption": "OFF", "voteOptions": ["EVERYONE_CAN_VOTE", "OFF"],
                   "collaboration": True, "collaborators": "by You and 1 other"}


def test_settings_missing_fields_are_null():
    out = edit.settings(owned_page({}, None), "PLx")
    assert out["owned"] is True
    for key in ("title", "description", "privacy", "sortOrder", "sortTitle", "sortOptions", "addToTop", "voteOption",
                "voteOptions", "collaboration", "collaborators"):
        assert out[key] is None, key


def test_settings_a_served_form_without_a_description_has_an_empty_one():
    form = {"title": {"runs": [{"text": "Mine"}]}, "editTitle": {"runs": [{"text": "Title"}]},
            "editDescription": {"runs": [{"text": "Description"}]}}
    out = edit.settings(owned_page(form), "PLx")
    assert (out["title"], out["description"]) == ("Mine", ""), "the labels are never the values"


def test_settings_privacy_from_the_dropdown_and_a_view_sort():
    header = {"privacyDropdown": {"dropdownRenderer": {"entries": [
        {"dropdownItemRenderer": {"stringValue": "public", "isSelected": False}},
        {"dropdownItemRenderer": {"stringValue": "private", "isSelected": True}}]}}}
    sort = [_sort_item("Manual ordering", False, order=0), _sort_item("Artist", True, dynamic=2)]
    out = edit.settings(owned_page(header, sort), "PLx")
    assert out["privacy"] == "PRIVATE"
    assert (out["sortOrder"], out["sortTitle"]) == (None, "Artist"), "a view sort has no order name"
    assert out["sortOptions"] == [{"value": "MANUAL", "title": "Manual ordering"}], "view sorts are no orders"
    manual = edit.settings(owned_page({}, [_sort_item("Manual ordering", True, order=0)]), "PLx")
    assert manual["sortOrder"] == "MANUAL"


def test_settings_of_a_playlist_that_is_not_yours():
    out = edit.settings(owned_page(owned=False), "PLx")
    assert out["owned"] is False and out["privacy"] is None and out["sortOrder"] is None
    assert edit.settings({}, "PLx")["owned"] is False


def test_playlist_settings_reads_one_page():
    page = owned_page(FULL_EDIT, SORT)
    worker = signed_in(_send_request=page)
    reply = call(worker, "playlistSettings", playlistId="VLPLx")
    assert reply["result"]["sortOrder"] == "NEWEST_FIRST" and reply["result"]["playlistId"] == "PLx"
    assert worker.yt.edits() == [("_send_request", ("browse", {"browseId": "VLPLx"}), {})]
    assert "playlistSettings" not in ytm.MUTATES and "playlistSettings" not in ytm.CACHEABLE


def _suggestion(n):
    return {"videoId": f"sug{n}".ljust(11, "x"), "title": f"Suggested {n}", "artists": [{"name": "A", "id": "UCa"}],
            "setVideoId": f"SET{n}", "duration": "3:00", "thumbnails": [{"url": "https://i/1", "width": 60}]}


def _browsing(worker, page, answer):
    """get_playlist that browses `page` first (as ytmusicapi does, through the
    _send_request ytm_raw wraps), then answers `answer` (a value or an
    exception)."""
    from ytm_raw import install

    worker.yt.responses["_send_request"] = page

    def get_playlist(*args, **kwargs):
        worker.yt._send_request("browse", {"browseId": "VL" + args[0]})
        if isinstance(answer, Exception):
            raise answer
        return answer

    worker.yt.responses["get_playlist"] = get_playlist
    install(worker.yt)
    return worker


def test_suggestions_for_your_own_playlist():
    data = {"owned": True, "tracks": [], "suggestions": [_suggestion(n) for n in range(16)]}
    worker = _browsing(signed_in(), owned_page(FULL_EDIT, SORT), data)
    reply = call(worker, "playlistSuggestions", playlistId="VLPLx")
    result = reply["result"]
    assert [c for c in worker.yt.edits() if c[0] == "get_playlist"] == [
        ("get_playlist", ("PLx",), {"limit": 0, "suggestions_limit": 14})]
    assert result["settings"]["sortOrder"] == "NEWEST_FIRST" and result["settings"]["owned"] is True, \
        "the page's settings come along (CollectionView's J / K gate)"
    assert result["settings"]["privacy"] == "UNLISTED"
    assert result["owned"] is True and len(result["items"]) == 14
    assert all(t["type"] == "track" and t["setVideoId"] is None for t in result["items"]), \
        "a suggestion is no entry of the playlist"
    assert "playlistSuggestions" not in ytm.CACHEABLE and ytm.LANES["playlistSuggestions"] == "pages"


def test_no_suggestions_for_a_playlist_that_is_not_yours():
    data = {"owned": False, "tracks": [], "suggestions": [_suggestion(1)]}
    result = call(signed_in(get_playlist=data), "playlistSuggestions", playlistId="PLx")["result"]
    assert result["owned"] is False and result["items"] == []


def test_an_empty_playlist_has_no_suggestions():
    result = call(signed_in(get_playlist=KeyError("continuations")), "playlistSuggestions", playlistId="PLx")
    assert result["ok"] and result["result"]["items"] == []
    assert result["result"]["owned"] is False and result["result"]["settings"] is None, "no page was read"


def test_an_empty_playlist_of_yours_is_owned_by_its_page():
    # ytmusicapi gives up on the suggestions of an empty playlist after its
    # first browse: the page read says whose it is.
    worker = _browsing(signed_in(), owned_page(FULL_EDIT, SORT), KeyError("continuations"))
    result = call(worker, "playlistSuggestions", playlistId="PLx")["result"]
    assert result["owned"] is True and result["items"] == []
    assert result["settings"]["sortOrder"] == "NEWEST_FIRST"
    worker = _browsing(signed_in(), owned_page(owned=False), KeyError("continuations"))
    assert call(worker, "playlistSuggestions", playlistId="PLx")["result"]["owned"] is False


# ---- registry and cache -------------------------------------------------------------------------


def test_every_edit_mutates_and_invalidates():
    edits = {"editPlaylist", "movePlaylistItem", "addToPlaylist", "createPlaylist", "joinPlaylist"}
    assert edit.MUTATES == edits and edits <= ytm.MUTATES
    for name in edits:
        triples = ytm.INVALIDATES[name]
        assert ("libraryPlaylists", None, None) in triples, name
        assert any(method == "browsePage" for method, _, _ in triples), name
        if name != "createPlaylist":
            assert ("playlist", "playlistId", "playlistId") in triples, name
            assert any(method == "browsePage" and getattr(src, "prefix", None) == "VL" for method, src, _ in triples), \
                name
    for name in ("playlistSettings", "playlistSuggestions"):
        assert name not in ytm.MUTATES and name not in ytm.CACHEABLE
    assert ytm.METHODS["addToPlaylist"] is edit.m_add_to_playlist
    assert ytm.METHODS["createPlaylist"] is edit.m_create_playlist


def test_an_edit_drops_the_playlist_cached_under_its_id(tmp_path):
    from ytm_cache import DiskCache

    cache = DiskCache(tmp_path)
    worker = ytm.Worker(FakeYT(edit_playlist="STATUS_SUCCEEDED"), "ok", cache=cache)
    cache.put("playlist", {"playlistId": "PLx"}, {"title": "before"})
    cache.put("playlist", {"playlistId": "PLother"}, {"title": "other"})
    cache.put("libraryPlaylists", {}, {"items": []})
    cache.put("browsePage", {"browseId": "VLPLx"}, {"title": "before"})
    cache.put("browsePage", {"browseId": "VLPLother"}, {"title": "other"})
    assert call(worker, "movePlaylistItem", playlistId="VLPLx", setVideoId="S1")["ok"]
    assert cache.get("playlist", {"playlistId": "PLx"}) is None, "the VL-stripped id matches"
    assert cache.get("playlist", {"playlistId": "PLother"}) is not None
    assert cache.get("libraryPlaylists", {}) is None
    assert cache.get("browsePage", {"browseId": "VLPLx"}) is None, "the playlist's own browse page"
    assert cache.get("browsePage", {"browseId": "VLPLother"}) is not None


def test_a_bad_request_drops_nothing(tmp_path):
    from ytm_cache import DiskCache

    cache = DiskCache(tmp_path)
    worker = ytm.Worker(FakeYT(), "ok", cache=cache)
    cache.put("playlist", {"playlistId": "PLx"}, {"title": "before"})
    assert call(worker, "editPlaylist", playlistId="PLx", title="")["error"]["code"] == "BAD_REQUEST"
    assert cache.get("playlist", {"playlistId": "PLx"}) is not None
