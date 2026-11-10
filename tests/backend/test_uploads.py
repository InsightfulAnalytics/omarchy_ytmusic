"""ytm_uploads: your uploaded songs, albums and artists, uploading and
deleting (S10).

Hand-built responses only: these pages are private. Their shapes follow the
served ones: the uploads list pages put Library and Downloads tabs (no rows)
before the selected Uploads tab, serve no sort menu, and name their orders
in frameworkUpdates as musicFormBooleanChoice entities with opaque tokens.
"""

import os

import pytest
from ytmusicapi.continuations import get_continuation_string
from ytmusicapi.enums import ResponseStatus
from ytmusicapi.mixins._utils import prepare_order_params
from ytmusicapi.navigation import MRLIR

import ytm
import ytm_cache as caches
import ytm_normalize as norm
import ytm_play as play
import ytm_raw as raw
import ytm_search as search
import ytm_uploads as uploads
from test_library import LibraryYT, continuation, next_data
from test_worker import THUMBS, call

ALBUM_ID = norm.UPLOAD_ALBUM_PREFIX + "b_po_ALBUM"
ARTIST_ID = norm.UPLOAD_ARTIST_PREFIX + "a_po_ARTIST"
SORT_KEY = "form0"
TOKENS = {"recently_added": "SortSpec_Type_ADDED_TIMESTAMP_SortSpec_Order_DESCENDING",
          "a_to_z": "SortSpec_Type_ENTITY_NAME_SortSpec_Order_ASCENDING",
          "z_to_a": "SortSpec_Type_ENTITY_NAME_SortSpec_Order_DESCENDING"}


# ---- builders ----------------------------------------------------------------------------


def column(runs):
    return {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": runs}}}


def upload_row(n, entity=True):
    """An uploaded song's row as parse_uploaded_items reads it."""
    items = [{"menuServiceItemRenderer": {"serviceEndpoint": {"queueAddEndpoint": {
        "queueTarget": {"videoId": f"up{n:09d}"}}}}}]
    if entity:
        items.append({"menuNavigationItemRenderer": {"navigationEndpoint": {"confirmDialogEndpoint": {"content": {
            "confirmDialogRenderer": {"confirmButton": {"buttonRenderer": {"command": {
                "musicDeletePrivatelyOwnedEntityCommand": {"entityId": f"t_po_{n}"}}}}}}}}}})
    return {MRLIR: {
        "flexColumns": [column([{"text": f"Song {n}"}]),
                        column([{"text": "Someone", "navigationEndpoint": {"browseEndpoint": {"browseId": ARTIST_ID}}}]),
                        column([{"text": "Demo", "navigationEndpoint": {"browseEndpoint": {"browseId": ALBUM_ID}}}])],
        "fixedColumns": [{"musicResponsiveListItemFixedColumnRenderer": {"text": {"runs": [{"text": "3:05"}]}}}],
        "menu": {"menuRenderer": {"items": items, "topLevelButtons": [{"likeButtonRenderer": {"likeStatus": "LIKE"}}]}},
        "thumbnail": THUMBS}}


def shuffle_row():
    return {MRLIR: {"flexColumns": [column([{"text": "Shuffle all"}])],
                    "navigationEndpoint": {"watchPlaylistEndpoint": {"playlistId": "MLPT"}}}}


def sort_state(selected="recently_added"):
    mutations = [{"entityKey": SORT_KEY, "payload": {"musicForm": {"booleanChoiceEntityKeys": list(TOKENS)}}}]
    for order, token in TOKENS.items():
        mutations.append({"entityKey": f"choice-{order}", "payload": {"musicFormBooleanChoice": {
            "parentFormEntityKey": SORT_KEY, "selected": order == selected, "opaqueToken": token}}})
    return {"entityBatchUpdate": {"mutations": mutations}}


def empty_tab(title):
    return {"tabRenderer": {"title": title, "selected": False, "content": {"sectionListRenderer": {
        "continuations": [{"reloadContinuationData": {"continuation": "R"}}]}}}}


def uploads_page(rows, selected="recently_added"):
    """An uploads list page: two tabs without rows, then the selected Uploads tab."""
    return {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
        empty_tab("Library"), empty_tab("Downloads"),
        {"tabRenderer": {"title": "Uploads", "selected": True, "content": {"sectionListRenderer": {"contents": rows}}}}]}},
        "frameworkUpdates": sort_state(selected)}


def songs_page(contents, token=None, selected="recently_added"):
    return uploads_page([{"musicShelfRenderer": {"contents": contents, "continuations": next_data(token)}}], selected)


class UploadsYT(LibraryYT):
    """LibraryYT plus ytmusicapi's upload methods, answering from ``answers``."""

    def __init__(self, *responses, **answers):
        super().__init__(*responses)
        self.answers = answers

    def _answer(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        value = self.answers.get(name)
        if isinstance(value, Exception):
            raise value
        return value(*args, **kwargs) if callable(value) else value

    def get_library_upload_albums(self, limit=25, order=None):
        return self._answer("get_library_upload_albums", limit=limit, order=order)

    def get_library_upload_artists(self, limit=25, order=None):
        return self._answer("get_library_upload_artists", limit=limit, order=order)

    def get_library_upload_album(self, browseId):
        return self._answer("get_library_upload_album", browseId)

    def get_library_upload_artist(self, browseId, limit=25):
        return self._answer("get_library_upload_artist", browseId, limit=limit)

    def upload_song(self, filepath):
        return self._answer("upload_song", filepath)

    def delete_upload_entity(self, entityId):
        return self._answer("delete_upload_entity", entityId)


def worker_for(*responses, cache=None, **answers):
    return ytm.Worker(UploadsYT(*responses, **answers), "ok", cache=cache)


def items_of(page):
    return [i for s in page["sections"] for i in s["items"]]


def song_dict(n, album=True):
    """A song as ytmusicapi's upload readers return it."""
    return {"entityId": f"t_po_{n}", "videoId": f"up{n:09d}", "title": f"Song {n}", "duration": "3:05",
            "duration_seconds": 185, "artists": [{"name": "Someone", "id": ARTIST_ID}],
            "album": {"name": "Demo", "id": ALBUM_ID} if album else None, "likeStatus": "INDIFFERENT",
            "thumbnails": [{"url": "https://lh3.googleusercontent.com/x=w60-h60-l90-rj", "width": 60, "height": 60}]}


# ---- normalizers -----------------------------------------------------------------------------


def test_normalize_upload_song_album_and_artist():
    song = norm.upload_track(song_dict(1))
    assert (song["type"], song["kind"], song["entityId"], song["videoId"]) == ("track", "upload", "t_po_1", "up000000001")
    assert song["album"] == {"name": "Demo", "id": ALBUM_ID}
    assert song["artists"] == [{"name": "Someone", "id": ARTIST_ID}] and song["artistText"] == "Someone"
    assert song["durationSec"] == 185 and song["likeStatus"] == "INDIFFERENT"
    assert norm.upload_track(dict(song_dict(2), entityId=None))["entityId"] is None
    album = norm.upload_album({"browseId": ALBUM_ID, "playlistId": "MLPRb_po_ALBUM", "title": "Demo", "type": "Album",
                               "thumbnails": []})
    assert (album["type"], album["browseId"], album["upload"], album["entityId"]) == ("album", ALBUM_ID, True, "b_po_ALBUM")
    assert album["albumType"] == "Album"
    artist = norm.upload_artist({"browseId": ARTIST_ID, "artist": "Someone", "subscribers": "9", "shuffleId": "MLPAx",
                                 "radioId": "MLPAy", "thumbnails": []})
    assert (artist["type"], artist["channelId"], artist["name"], artist["upload"]) == ("artist", ARTIST_ID, "Someone", True)
    assert (artist["songs"], artist["subtitle"]) == (9, "9 songs")
    assert "shuffleId" not in artist and "radioId" not in artist, "their mixes play nothing elsewhere"
    assert norm.upload_artist({"browseId": ARTIST_ID, "artist": "One", "songs": "1"})["subtitle"] == "1 song"
    assert norm.is_upload_album(ALBUM_ID) and not norm.is_upload_album(norm.UPLOAD_ALBUM_PREFIX)
    assert norm.is_upload_artist(ARTIST_ID) and not norm.is_upload_artist("UCx")
    assert norm.upload_entity("MPREb_x") is None


# ---- uploadSongs -------------------------------------------------------------------------------


def test_upload_songs_reads_the_selected_tab_then_pages_by_cursor():
    worker = worker_for(songs_page([shuffle_row()] + [upload_row(i) for i in range(30)], "S1"),
                        continuation("musicShelfRenderer", [upload_row(i) for i in range(30, 60)], "S2"))
    reply = call(worker, "uploadSongs")
    assert reply["ok"], reply
    page = reply["result"]
    assert (page["type"], page["kind"], page["id"]) == ("page", "uploadSongs", uploads.SONGS_ID)
    songs = items_of(page)
    assert len(songs) == 60 and songs[0]["kind"] == "upload" and songs[0]["entityId"] == "t_po_0"
    assert songs[0]["videoId"] == "up000000000", "the Shuffle all row plays no song: left out"
    assert page["cursor"] == "c.S2" and page["hasMore"] is True
    assert page["orders"] == [{"index": "recently_added", "title": "Recently added", "selected": True},
                              {"index": "a_to_z", "title": "A to Z", "selected": False},
                              {"index": "z_to_a", "title": "Z to A", "selected": False}]
    assert page["order"] == "recently_added"
    assert worker.yt.requests == [("browse", {"browseId": uploads.SONGS_ID}, ""),
                                  ("browse", {"browseId": uploads.SONGS_ID}, get_continuation_string("S1"))]
    assert reply["http"] == 3, "the session check, the page and one continuation"
    worker.yt.responses.append(continuation("musicShelfRenderer", [upload_row(60)]))
    more = call(worker, "uploadSongs", cursor="c.S2")
    assert more["ok"] and [s["entityId"] for s in items_of(more["result"])] == ["t_po_60"]
    assert more["result"]["cursor"] is None and more["http"] == 1


def test_upload_songs_skip_malformed_rows_and_continuation_entries():
    rows = [upload_row(1), upload_row(2, entity=False), {"continuationItemRenderer": {}}, upload_row(3)]
    reply = call(worker_for(songs_page(rows)), "uploadSongs")
    assert [s["entityId"] for s in items_of(reply["result"])] == ["t_po_1", "t_po_3"]


def test_upload_songs_order_goes_in_the_body_and_is_a_saved_order(tmp_path):
    cache = caches.DiskCache(tmp_path / "cache")
    worker = worker_for(songs_page([upload_row(1)], selected="a_to_z"), cache=cache)
    cache.put("browsePage", {"browseId": uploads.SONGS_ID}, {"type": "page"})
    reply = call(worker, "uploadSongs", order="a_to_z")
    assert reply["ok"] and reply["result"]["order"] == "a_to_z"
    assert [o["selected"] for o in reply["result"]["orders"]] == [False, True, False]
    assert worker.yt.requests[0][1] == {"browseId": uploads.SONGS_ID, "params": prepare_order_params("a_to_z")}
    assert cache.get("browsePage", {"browseId": uploads.SONGS_ID}) is None, "YouTube keeps the order: the page copy goes"
    for bad in ("menu:1", "newest", True):
        assert call(worker, "uploadSongs", order=bad)["error"]["code"] == "BAD_REQUEST"


def test_upload_songs_empty_and_signed_out():
    empty = uploads_page([{"itemSectionRenderer": {"contents": [{"messageRenderer": {
        "text": {"runs": [{"text": "No uploads yet"}]}}}]}}])
    reply = call(worker_for(empty), "uploadSongs")
    assert reply["ok"] and reply["result"]["sections"] == [] and reply["result"]["message"] == "No uploads yet"
    signed_out = ytm.Worker(UploadsYT(), "none")
    for method, params in (("uploadSongs", {}), ("uploadAlbums", {}), ("uploadArtists", {}),
                           ("uploadAlbum", {"browseId": ALBUM_ID}), ("uploadArtist", {"browseId": ARTIST_ID})):
        assert call(signed_out, method, **params)["error"]["code"] == "AUTH_REQUIRED", method
    assert signed_out.yt.requests == []


def test_served_orders_read_the_opaque_tokens_only():
    assert uploads.token_order(TOKENS["z_to_a"]) == "z_to_a"
    assert uploads.token_order("SortSpec_Type_PLAY_COUNT_SortSpec_Order_DESCENDING") is None
    assert uploads.token_order(None) is None
    orders, current = uploads.orders_of({}, None)
    assert [o["index"] for o in orders] == list(uploads.ORDERS) and current is None, "none named: all three, unknown current"
    orders, current = uploads.orders_of({"frameworkUpdates": sort_state("z_to_a")}, None)
    assert current == "z_to_a" and orders[2]["selected"]


# ---- albums, artists and their pages ---------------------------------------------------------------


def test_upload_albums_and_artists_are_read_whole():
    album = {"browseId": ALBUM_ID, "playlistId": "MLPRb_po_ALBUM", "title": "Demo", "type": "Album", "thumbnails": []}
    artist = {"browseId": ARTIST_ID, "artist": "Someone", "subscribers": "9", "thumbnails": []}
    worker = worker_for(get_library_upload_albums=[album, {"title": "broken"}], get_library_upload_artists=[artist])
    albums = call(worker, "uploadAlbums", order="z_to_a")
    assert albums["ok"], albums
    page = albums["result"]
    assert (page["kind"], page["id"], page["order"]) == ("uploadAlbums", uploads.ALBUMS_ID, "z_to_a")
    assert [a["entityId"] for a in items_of(page)] == ["b_po_ALBUM"], "a malformed album is skipped"
    assert page["sections"][0]["layout"] == "grid" and page["cursor"] is None
    artists = call(worker, "uploadArtists")["result"]
    assert [a["channelId"] for a in items_of(artists)] == [ARTIST_ID] and items_of(artists)[0]["subtitle"] == "9 songs"
    assert worker.yt.calls[-2:] == [("get_library_upload_albums", (), {"limit": uploads.LIST_MAX, "order": "z_to_a"}),
                                    ("get_library_upload_artists", (), {"limit": uploads.LIST_MAX, "order": None})]
    none = call(worker_for(get_library_upload_albums=[]), "uploadAlbums")["result"]
    assert none["sections"] == [] and none["message"] == "No uploaded albums yet"


def test_upload_album_page():
    data = {"title": "Demo", "type": "Album", "thumbnails": [], "isExplicit": False, "trackCount": 2,
            "duration": "6 minutes", "audioPlaylistId": "MLPRb_po_ALBUM", "duration_seconds": 370,
            "tracks": [dict(song_dict(1), artists=None, album=None), dict(song_dict(2), artists=None, album=None)]}
    worker = worker_for(get_library_upload_album=data)
    page = call(worker, "uploadAlbum", browseId=ALBUM_ID)["result"]
    assert (page["type"], page["kind"], page["id"], page["title"]) == ("collection", "album", ALBUM_ID, "Demo")
    assert page["upload"] is True and page["entityId"] == "b_po_ALBUM"
    assert page["playlistId"] is None and page["header"]["audioPlaylistId"] is None, "its MLPR id plays nothing elsewhere"
    assert [t["kind"] for t in page["tracks"]] == ["upload", "upload"] and page["trackCount"] == 2
    assert page["tracks"][0]["album"] == {"name": "Demo", "id": ALBUM_ID}, "the songs link back to the album"
    assert page["unavailable"] == 0 and page["header"]["duration"] == "6 minutes"
    assert worker.yt.calls[-1] == ("get_library_upload_album", (ALBUM_ID,), {})
    with_artists = dict(data, tracks=[song_dict(1)])
    assert call(worker_for(get_library_upload_album=with_artists), "uploadAlbum",
                browseId=ALBUM_ID)["result"]["header"]["artists"] == [{"name": "Someone", "id": ARTIST_ID}]
    for bad in ("MPREb_x", norm.UPLOAD_ALBUM_PREFIX, ARTIST_ID):
        assert call(worker, "uploadAlbum", browseId=bad)["error"]["code"] == "BAD_REQUEST"


def test_upload_artist_page():
    worker = worker_for(get_library_upload_artist=[song_dict(1), song_dict(2)])
    page = call(worker, "uploadArtist", browseId=ARTIST_ID)["result"]
    assert (page["type"], page["kind"], page["id"], page["title"]) == ("collection", "uploadArtist", ARTIST_ID, "Someone")
    assert page["subtitle"] == "2 songs" and [t["entityId"] for t in page["tracks"]] == ["t_po_1", "t_po_2"]
    assert worker.yt.calls[-1] == ("get_library_upload_artist", (ARTIST_ID,), {"limit": uploads.MAX_TRACKS})
    assert call(worker, "uploadArtist", browseId="UCx")["error"]["code"] == "BAD_REQUEST"


def test_tracks_for_reads_upload_albums_and_artists():
    assert play.route("album", ALBUM_ID) == ("uploadAlbum", ALBUM_ID)
    assert play.route("artist", ARTIST_ID) == ("uploadArtist", ARTIST_ID)
    assert play.route("album", "MPREb_x") == ("album", "MPREb_x")
    data = {"title": "Demo", "trackCount": 2, "tracks": [song_dict(1), song_dict(2)]}
    worker = worker_for(get_library_upload_album=data, get_library_upload_artist=[song_dict(3)])
    album = call(worker, "tracksFor", kind="album", id=ALBUM_ID, limit=1)["result"]
    assert [t["videoId"] for t in album["items"]] == ["up000000001"] and album["complete"] is False
    assert album["total"] == 2 and album["playlistId"] is None
    artist = call(worker, "tracksFor", kind="artist", id=ARTIST_ID)["result"]
    assert [t["kind"] for t in artist["items"]] == ["upload"] and artist["complete"] is True
    assert artist["total"] == 1


def paged_artist(count, page=25):
    """get_library_upload_artist as ytmusicapi reads it: whole pages of 25
    until it holds at least ``limit`` (a minimum, not a cut)."""
    songs = [song_dict(n) for n in range(1, count + 1)]

    def read(browse_id, limit=25):
        held = songs[:page]
        while len(held) < len(songs) and (limit is None or len(held) < limit):
            held = songs[:len(held) + page]
        return held
    return read


@pytest.mark.parametrize("count, limit, complete, total", [
    (600, 500, False, None),  # Play: the queue's 500 of more
    (500, 500, True, 500),    # exactly the limit: all of it
    (499, 500, True, 499),
    (26, 25, False, None),
    (25, 25, True, 25),
])
def test_tracks_for_an_upload_artist_says_when_it_has_more(count, limit, complete, total):
    worker = worker_for(get_library_upload_artist=paged_artist(count))
    reply = call(worker, "tracksFor", kind="artist", id=ARTIST_ID, limit=limit)["result"]
    assert len(reply["items"]) == min(count, limit)
    assert (reply["complete"], reply["total"]) == (complete, total)
    assert worker.yt.calls[-1] == ("get_library_upload_artist", (ARTIST_ID,), {"limit": limit + 1}), \
        "one more than wanted: ytmusicapi stops at the limit"


def test_search_uploads_scope_rows_are_uploads():
    found = search._normalize([{"resultType": "song", "videoId": "up000000001", "title": "Song 1",
                                "artists": [{"name": "Someone", "id": ARTIST_ID}]},
                               {"resultType": "album", "browseId": ALBUM_ID, "title": "Demo", "artist": "Someone",
                                "releaseDate": "2020", "thumbnails": THUMBS["musicThumbnailRenderer"]["thumbnail"][
                                    "thumbnails"]},
                               {"resultType": "artist", "browseId": ARTIST_ID, "title": "Someone"}], "upload")
    assert (found[0]["type"], found[0]["kind"], found[0]["entityId"]) == ("track", "upload", None)
    # An album and an artist as the uploads views list them (menus, Delete, their upload pages).
    album = found[1]
    assert (album["type"], album["browseId"], album["upload"], album["entityId"]) == ("album", ALBUM_ID, True, "b_po_ALBUM")
    assert (album["title"], album["artistText"], album["year"], album["playlistId"]) == ("Demo", "Someone", "2020", None)
    assert album["artists"] == [{"name": "Someone", "id": None}] and album["thumb"]
    artist = found[2]
    assert (artist["type"], artist["channelId"], artist["name"], artist["upload"]) == ("artist", ARTIST_ID, "Someone", True)
    # Not an uploads id: skipped, not opened as a public page.
    assert search._normalize([{"resultType": "album", "browseId": "MPREb_x", "title": "x"},
                              {"resultType": "artist", "browseId": "UCx", "title": "x"}], "upload") == []


# ---- uploads elsewhere (a playlist, Liked Music, History, a watch list) ------------------------


def listed_upload(**extra):
    """An uploaded song as a playlist, History or watch list row gives it:
    YouTube's video type, the uploads ids, and no entityId."""
    return dict({"videoId": "up000000001", "title": "Song 1", "videoType": "MUSIC_VIDEO_TYPE_PRIVATELY_OWNED_TRACK",
                 "artists": [{"name": "Someone", "id": ARTIST_ID}], "album": {"name": "Demo", "id": ALBUM_ID},
                 "setVideoId": "S1"}, **extra)


def test_an_upload_is_one_wherever_it_is_listed():
    assert norm.track(listed_upload())["kind"] == "upload"
    # No video type (an older reply or another parser): its uploads ids say so.
    assert norm.track(listed_upload(videoType=None))["kind"] == "upload"
    assert norm.track(listed_upload(videoType=None, album=None))["kind"] == "upload", "by its artist"
    assert norm.track(listed_upload(videoType=None, artists=[]))["kind"] == "upload", "by its album"
    plain = norm.track(listed_upload(videoType="MUSIC_VIDEO_TYPE_ATV", artists=[{"name": "R", "id": "UCr"}],
                                     album={"name": "OK", "id": "MPREb_x"}))
    assert plain["kind"] == "song"
    assert norm.track(listed_upload(videoType="MUSIC_VIDEO_TYPE_OMV", artists=[], album=None))["kind"] == "video"
    # No entityId there: Delete stays off (the shell needs one).
    assert "entityId" not in norm.track(listed_upload())
    public = dict(song_dict(9, album=False), artists=[{"name": "R", "id": "UCr"}])
    page = norm.collection("playlist", {"id": "PLx", "title": "Mine", "tracks": [listed_upload(), public]}, "PLx")
    assert [t["kind"] for t in page["tracks"]] == ["upload", "song"]


# ---- uploadSong --------------------------------------------------------------------------------------


def music_file(tmp_path, name, size=16):
    path = tmp_path / "music" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def upload_reply(worker, path):
    return call(worker, "uploadSong", path=str(path))


@pytest.mark.parametrize("name,problem", [("missing.mp3", "No such file"), ("song.wav", "mp3, m4a, wma, flac or ogg"),
                                           ("noext", "mp3, m4a, wma, flac or ogg")])
def test_upload_song_refuses_what_youtube_would_not_take(tmp_path, name, problem):
    worker = worker_for(upload_song=ResponseStatus.SUCCEEDED)
    path = tmp_path / "music" / name if name == "missing.mp3" else music_file(tmp_path, name)
    reply = upload_reply(worker, path)
    assert reply["error"]["code"] == "BAD_REQUEST" and problem in reply["error"]["message"]
    assert worker.yt.calls == [] and worker.yt.requests == [], "nothing sent, not even the session check"


def test_upload_song_refuses_a_folder_a_relative_path_and_an_oversize_file(tmp_path, monkeypatch):
    worker = worker_for(upload_song=ResponseStatus.SUCCEEDED)
    folder = tmp_path / "album.mp3"
    folder.mkdir()
    assert "folder" in upload_reply(worker, folder)["error"]["message"]
    assert "full path" in call(worker, "uploadSong", path="music/song.mp3")["error"]["message"]
    assert call(worker, "uploadSong", path="")["error"]["code"] == "BAD_REQUEST"
    big = music_file(tmp_path, "big.flac", size=11)
    monkeypatch.setattr(uploads, "MAX_BYTES", 10)
    reply = upload_reply(worker, big)
    assert reply["error"]["code"] == "BAD_REQUEST" and "300 MB" in reply["error"]["message"]
    monkeypatch.setattr(uploads, "MAX_BYTES", 12)
    assert upload_reply(worker, big)["ok"], "under the limit"
    assert worker.yt.calls == [("upload_song", (str(big),), {})]


def test_upload_song_sends_the_file_and_reports_processing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = music_file(tmp_path, "song.m4a")
    worker = worker_for(upload_song=ResponseStatus.SUCCEEDED)
    reply = call(worker, "uploadSong", path="~/music/song.m4a")
    assert reply["ok"] and reply["result"] == {"ok": True, "processing": True, "name": "song.m4a"}
    assert worker.yt.calls == [("upload_song", (str(path),), {})], "~ expanded; a lower-case suffix goes as it is"


def test_upload_song_sends_an_upper_case_suffix_through_a_lower_case_symlink(tmp_path):
    path = music_file(tmp_path, "Loud Song.MP3")
    seen = {}

    def upload_song(filepath):
        link = os.path.realpath(filepath)
        seen.update(path=filepath, target=link, exists=os.path.islink(filepath), name=os.path.basename(filepath))
        return ResponseStatus.SUCCEEDED

    worker = worker_for(upload_song=upload_song)
    reply = upload_reply(worker, path)
    assert reply["ok"], reply
    assert seen["name"] == "Loud Song.mp3" and seen["exists"] and seen["target"] == str(path)
    assert seen["path"].startswith(str(uploads.tmp_root()) + os.sep)
    assert not os.path.lexists(seen["path"]) and list(uploads.tmp_root().iterdir()) == [], "removed afterwards"
    assert path.exists(), "the file itself stays"


def test_upload_song_failures(tmp_path):
    path = music_file(tmp_path, "song.ogg")

    class Response:
        status_code = 413

    refused = upload_reply(worker_for(upload_song=Response()), path)
    assert refused["error"]["code"] == "UPSTREAM" and "HTTP 413" in refused["error"]["message"]
    upper = music_file(tmp_path, "SONG.OGG")
    broken = upload_reply(worker_for(upload_song=KeyError("X-Goog-Upload-URL")), upper)
    assert broken["error"]["code"] == "UPSTREAM"
    assert list(uploads.tmp_root().iterdir()) == [], "the symlink goes on a failure too"
    signed_out = ytm.Worker(UploadsYT(upload_song=ResponseStatus.SUCCEEDED), "none")
    assert upload_reply(signed_out, path)["error"]["code"] == "AUTH_REQUIRED"
    assert signed_out.yt.calls == []


def test_upload_song_is_refused_as_a_brand_account(tmp_path):
    worker = worker_for(upload_song=ResponseStatus.SUCCEEDED)
    worker.identity = {"pageId": "123456789012345678901", "authUser": 0}
    reply = upload_reply(worker, music_file(tmp_path, "song.mp3"))
    assert reply["error"]["code"] == "BAD_REQUEST" and "Uploading songs" in reply["error"]["message"]
    assert worker.yt.calls == [] and worker.yt.requests == []


# ---- deleteUpload -----------------------------------------------------------------------------------


@pytest.mark.parametrize("answer,ok", [(ResponseStatus.SUCCEEDED, True), ("STATUS_SUCCEEDED", True),
                                       ("STATUS_FAILED", False), ({"code": 400, "message": "nope"}, False)])
def test_delete_upload(answer, ok):
    worker = worker_for(delete_upload_entity=answer)
    reply = call(worker, "deleteUpload", entityId="t_po_1")
    assert worker.yt.calls == [("delete_upload_entity", ("t_po_1",), {})]
    if ok:
        assert reply["ok"] and reply["result"] == {"ok": True, "entityId": "t_po_1"}
    else:
        assert reply["error"]["code"] == "UPSTREAM" and "nope" not in reply["error"]["message"]


def test_delete_upload_checks_its_id_first():
    worker = worker_for(delete_upload_entity=ResponseStatus.SUCCEEDED)
    for bad in ("", "t po 1", "x" * 600, None):
        assert call(worker, "deleteUpload", entityId=bad)["error"]["code"] == "BAD_REQUEST"
    assert worker.yt.calls == []
    assert call(ytm.Worker(UploadsYT(), "none"), "deleteUpload", entityId="t_po_1")["error"]["code"] == "AUTH_REQUIRED"


def test_edits_drop_every_uploads_read(tmp_path):
    cache = caches.DiskCache(tmp_path / "cache")
    for method, params in (("uploadSongs", {}), ("uploadAlbums", {}), ("uploadArtists", {}),
                           ("uploadAlbum", {"browseId": ALBUM_ID}), ("uploadArtist", {"browseId": ARTIST_ID}),
                           ("librarySongs", {}), ("browsePage", {"browseId": uploads.SONGS_ID}),
                           ("browsePage", {"browseId": "VLPLkept"})):
        cache.put(method, params, {"kept": method})
    worker = worker_for(cache=cache, delete_upload_entity=ResponseStatus.SUCCEEDED)
    assert call(worker, "deleteUpload", entityId="t_po_1")["ok"]
    for method, params in (("uploadSongs", {}), ("uploadAlbums", {}), ("uploadArtists", {}),
                           ("uploadAlbum", {"browseId": ALBUM_ID}), ("uploadArtist", {"browseId": ARTIST_ID}),
                           ("librarySongs", {}), ("browsePage", {"browseId": uploads.SONGS_ID})):
        assert cache.get(method, params) is None, method
    assert cache.get("browsePage", {"browseId": "VLPLkept"}) is not None
    # A failed delete may have applied: it drops them too; a refused id (sent nothing) does not.
    cache.put("uploadSongs", {}, {"kept": True})
    failing = worker_for(cache=cache, delete_upload_entity="STATUS_FAILED")
    assert call(failing, "deleteUpload", entityId="t_po_1")["error"]["code"] == "UPSTREAM"
    assert cache.get("uploadSongs", {}) is None
    cache.put("uploadSongs", {}, {"kept": True})
    assert call(failing, "deleteUpload", entityId="t po")["error"]["code"] == "BAD_REQUEST"
    assert cache.get("uploadSongs", {}) is not None


# ---- registry -----------------------------------------------------------------------------------------


def test_registry_lanes_and_mutations():
    assert {"uploadSong", "deleteUpload"} <= ytm.MUTATES
    assert ytm.LANES["uploadSong"] == "upload", "a long POST: its own lane, off the main thread"
    assert "deleteUpload" not in ytm.LANES, "one quick request, answered in order like the other edits"
    for name in uploads.READS:
        assert ytm.LANES[name] == "pages" and name in ytm.CACHEABLE and name not in ytm.MUTATES
    for edit in ("uploadSong", "deleteUpload"):
        targets = {triple[0] for triple in ytm.INVALIDATES[edit]}
        assert set(uploads.READS) <= targets and "browsePage" in targets and "librarySongs" in targets
    assert not set(uploads.METHODS) & set(ytm.RECORDERS), "private pages: nothing recorded"
    assert ytm.LANES["resolve"] == "streams"


def test_cli_upload_resolves_a_relative_path(tmp_path, monkeypatch):
    seen = []

    class Worker:
        def handle(self, request):
            seen.append(request)
            return {"ok": True, "result": {"ok": True, "processing": True, "name": "a.mp3"}}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ytm, "Worker", Worker)
    assert ytm.main(["upload", "a.mp3"]) == 0
    assert seen[0]["method"] == "uploadSong" and seen[0]["params"] == {"path": str(tmp_path / "a.mp3")}
