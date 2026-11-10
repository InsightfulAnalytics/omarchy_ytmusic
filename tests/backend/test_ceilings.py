"""No ceilings (G1): the library played in its order, Shuffle all read on
through the server shuffle, and albums over the 200 rows YouTube serves.

The library's watch queues are private, so they are hand-built in the
served shapes; the big album is recorded anonymously (tests/fixtures/
album_big.json, trimmed to the fields the album page reads).
"""

import pytest
from ytmusicapi.continuations import get_continuation_string

import ytm
import ytm_collections as collections
import ytm_library as library
import ytm_play as play
from test_library import LibraryYT, browse, call, items_of, shuffle_row, sort_menu, STANDARD, SONGS
from test_library import panel_continuation, panel_track, watch_panel, worker_for
from test_worker import track_item


# ---- the library in its order --------------------------------------------------------------------


def queue_song(place, prefix="lib"):
    """A playlist panel song of the MLCT queue at ``place`` (its 0-based place
    in the list, as the served watchEndpoint index counts it)."""
    video_id = f"{prefix}{place:08d}"
    return {"playlistPanelVideoRenderer": {
        "videoId": video_id, "title": {"runs": [{"text": f"Song {place}"}]},
        "longBylineText": {"runs": [{"text": "Artist"}]},
        "lengthText": {"runs": [{"text": "3:00"}]},
        "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/vi/x/hq.jpg", "width": 120, "height": 90}]},
        "menu": {"menuRenderer": {"items": []}},
        "navigationEndpoint": {"watchEndpoint": {"videoId": video_id, "playlistId": "MLCT", "index": place}}}}


def queue(first, last, token=None):
    return watch_panel([queue_song(p) for p in range(first, last)], token)


def queue_more(first, last, token=None):
    return panel_continuation([queue_song(p) for p in range(first, last)], token)


def vid(place):
    return f"lib{place:08d}"


def test_a_song_plays_the_library_on_in_its_order_from_its_place():
    # YouTube's first response is the queue around the song (25 before it).
    worker = worker_for(queue(75, 125, "Q1"), queue_more(125, 149, "Q2"), queue_more(149, 173, "Q3"),
                        queue_more(173, 197, "Q4"), queue_more(197, 221, "Q5"))
    reply = call(worker, "tracksFor", kind="librarySongs", id="MLCT", videoId=vid(100), index=101, params="PLAY",
                 limit=500)
    assert reply["ok"], reply
    result = reply["result"]
    got = [t["videoId"] for t in result["items"]]
    assert got[0] == vid(100) and got == [vid(p) for p in range(100, 197)], "from the song, in order"
    assert [t["libraryIndex"] for t in result["items"][:2]] == [101, 102], "places counted as index is"
    assert result["playlistId"] == "MLCT" and result["complete"] is False and result["total"] is None
    sent = worker.yt.requests
    assert len(sent) == library.ORDERED_MAX_HTTP
    assert sent[0][1] == {"videoId": vid(100), "playlistId": "MLCT", "index": 101, "params": "PLAY",
                          "enablePersistentPlaylistPanel": True, "isAudioOnly": True,
                          "tunerSettingValue": "AUTOMIX_SETTING_NORMAL"}
    assert "params" not in sent[0][1] or sent[0][1]["params"] != library.SHUFFLE_PARAMS, "never the shuffle"
    assert [r[2] for r in sent[1:]] == [get_continuation_string(t) for t in ("Q1", "Q2", "Q3")]


def test_the_limit_and_the_end_of_the_queue():
    worker = worker_for(queue(0, 50, "Q1"), queue_more(50, 74, "Q2"))
    result = call(worker, "tracksFor", kind="librarySongs", id="MLCT", videoId=vid(20), index=21, limit=40)["result"]
    assert len(result["items"]) == 40 and result["complete"] is False
    assert "params" not in worker.yt.requests[0][1] and worker.yt.requests[0][1]["index"] == 21
    worker = worker_for(queue(3700, 3720))
    result = call(worker, "tracksFor", kind="librarySongs", id="MLCT", videoId=vid(3710), index=3711)["result"]
    assert [t["videoId"] for t in result["items"]] == [vid(p) for p in range(3710, 3720)]
    assert result["complete"] is True, "no continuation: the end"


def test_a_song_not_at_its_place_gives_nothing_rather_than_other_songs():
    # YouTube answers the list's start when it cannot place the song.
    worker = worker_for(queue(0, 25, "Q1"))
    result = call(worker, "tracksFor", kind="librarySongs", id="MLCT", videoId=vid(3740), index=3741)["result"]
    assert result["items"] == [] and result["complete"] is True
    assert len(worker.yt.requests) == 1


def test_near_the_end_the_queue_is_asked_from_an_earlier_song_and_read_on_to_it():
    # 3741 is not placed; the song 40 places earlier is, and the next page holds 3740.
    worker = worker_for(queue(0, 25, "Q0"), queue(3674, 3724, "Q1"), queue_more(3724, 3748, "Q2"),
                        queue_more(3748, 3772, "Q3"), queue_more(3772, 3796))
    reply = call(worker, "tracksFor", kind="librarySongs", id="MLCT", videoId=vid(3740), index=3741,
                 backVideoId=vid(3700), backIndex=3701)
    result = reply["result"]
    got = [t["videoId"] for t in result["items"]]
    assert got[0] == vid(3740) and got == [vid(p) for p in range(3740, 3796)]
    assert result["complete"] is True
    sent = worker.yt.requests
    assert sent[1][1]["videoId"] == vid(3700) and sent[1][1]["index"] == 3701
    assert len(sent) <= library.ORDERED_MAX_HTTP + 2


def test_a_response_with_no_song_is_not_the_end_of_the_library():
    # An empty continuation: the songs read so far, not complete, so the
    # shell asks again from the last of them.
    worker = worker_for(queue(75, 125, "Q1"), queue_more(125, 149, "Q2"), panel_continuation([], "Q3"))
    result = call(worker, "tracksFor", kind="librarySongs", id="MLCT", videoId=vid(100), index=101)["result"]
    assert [t["videoId"] for t in result["items"]] == [vid(p) for p in range(100, 149)]
    assert result["complete"] is False
    # An empty first response: nothing yet, and no back retry for it.
    worker = worker_for(watch_panel([], None))
    result = call(worker, "tracksFor", kind="librarySongs", id="MLCT", videoId=vid(100), index=101,
                  backVideoId=vid(60), backIndex=61)["result"]
    assert result["items"] == [] and result["complete"] is False
    assert len(worker.yt.requests) == 1
    # An empty page while the back retry reads on to the song.
    worker = worker_for(queue(0, 25, "Q0"), queue(3674, 3724, "Q1"), panel_continuation([], "Q2"))
    result = call(worker, "tracksFor", kind="librarySongs", id="MLCT", videoId=vid(3740), index=3741,
                  backVideoId=vid(3700), backIndex=3701)["result"]
    assert result["items"] == [] and result["complete"] is False


def test_library_songs_params_are_checked_and_need_the_account():
    worker = worker_for()
    assert call(worker, "tracksFor", kind="librarySongs", id="MLCT")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "tracksFor", kind="librarySongs", id="MLCT", videoId="v", index="3")["error"]["code"] == \
        "BAD_REQUEST"
    assert call(worker, "tracksFor", kind="librarySongs", id="MLCT", videoId="v", backVideoId="w")["error"]["code"] \
        == "BAD_REQUEST", "backIndex goes with backVideoId"
    signed_out = ytm.Worker(LibraryYT(), "none")
    assert call(signed_out, "tracksFor", kind="librarySongs", id="MLCT", videoId="v")["error"]["code"] == \
        "AUTH_REQUIRED"


def test_library_songs_route_before_the_shuffle():
    assert play.route("librarySongs", "MLCT") == ("librarySongs", "MLCT")
    assert play.route("playlist", "MLCT") == ("libraryShuffle", "MLCT")
    assert "librarySongs" in play.KINDS


def song_row(n):
    """A library song row whose play button plays MLCT from its place, with
    the served params (only the first response's rows carry them)."""
    row = track_item(n)
    endpoint = row["musicResponsiveListItemRenderer"]["overlay"]["musicItemThumbnailOverlayRenderer"]["content"][
        "musicPlayButtonRenderer"]["playNavigationEndpoint"]["watchEndpoint"]
    endpoint.update({"playlistId": "MLCT", "index": n + 1, "params": "PLAY"})
    return row


def test_the_songs_page_header_names_the_params_songs_play_with():
    menu = sort_menu(SONGS, STANDARD)
    worker = worker_for(browse("musicShelfRenderer", [shuffle_row()] + [song_row(i) for i in range(60)], "S1", menu))
    page = call(worker, "librarySongs")["result"]
    assert page["header"] == {"shuffleId": "MLCT", "playParams": "PLAY"}
    assert len(items_of(page)) == 60
    assert library.library_play_params([track_item(1)]) == {}, "a row that plays no MLCT names none"


# ---- Shuffle all, read on ---------------------------------------------------------------------------


def shuffle_pages(n_pages, size=25, first=0):
    pages = [watch_panel([panel_track(i) for i in range(first, first + size)], "W1")]
    for k in range(1, n_pages):
        start = first + k * size
        token = f"W{k + 1}" if k < n_pages - 1 else None
        pages.append(panel_continuation([panel_track(i) for i in range(start, start + size)], token))
    return pages


def test_shuffle_all_goes_on_through_the_server_shuffle_without_repeats():
    worker = worker_for(*shuffle_pages(6))
    first = call(worker, "tracksFor", kind="playlist", id="MLCT", limit=40)["result"]
    assert len(first["items"]) == 40 and first["complete"] is True
    assert "W" not in str(first), "the continuation stays in the worker"
    more = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True, limit=50)["result"]
    ids = [t["videoId"] for t in more["items"]]
    # The 10 read past the first limit come first, then the next pages.
    assert ids[:10] == [f"shuf{i:07d}" for i in range(40, 50)]
    assert len(ids) == 50 and not set(ids) & {t["videoId"] for t in first["items"]}
    assert more["complete"] is False
    sent = worker.yt.requests
    assert sent[2][2] == get_continuation_string("W2") and sent[2][1]["params"] == library.SHUFFLE_PARAMS
    last = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True, limit=50)["result"]
    assert [t["videoId"] for t in last["items"]] == [f"shuf{i:07d}" for i in range(90, 140)]
    assert last["complete"] is False, "10 more were read"
    tail = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True, limit=50)["result"]
    assert [t["videoId"] for t in tail["items"]] == [f"shuf{i:07d}" for i in range(140, 150)]
    assert tail["complete"] is True, "the shuffle ended: every song came"
    done = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True)["result"]
    assert done["items"] == [] and done["complete"] is True
    assert len(worker.yt.requests) == 6, "nothing asked once it ended"


def test_a_worker_without_the_shuffle_starts_a_new_one():
    # The usual `more`: the shell lets the worker go between songs.
    worker = worker_for(*shuffle_pages(3))
    result = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True)["result"]
    assert len(result["items"]) == 50 and worker.yt.requests[0][2] == "", "a fresh shuffle"
    assert result["complete"] is False, "a new shuffle goes on, so the shell asks again"
    on = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True)["result"]
    assert [t["videoId"] for t in on["items"]] == [f"shuf{i:07d}" for i in range(50, 75)]
    assert worker.yt.requests[2][2] == get_continuation_string("W2"), "then it reads on from the new one"
    assert on["complete"] is True
    # A new client (sign-in, account switch) drops it too.
    worker.yt.responses.extend(shuffle_pages(2, first=500))
    worker.generation += 1
    again = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True)["result"]
    assert again["items"][0]["videoId"] == "shuf0000500" and worker.yt.requests[3][2] == ""
    assert again["complete"] is False


def test_a_page_with_no_song_is_not_the_end_of_the_shuffle():
    # Seen live once in 24 reads: a continuation with no song. A new shuffle
    # goes on, without the songs the first one gave.
    worker = worker_for(watch_panel([panel_track(i) for i in range(25)], "W1"),
                        panel_continuation([panel_track(i) for i in range(25, 50)], "W2"),
                        panel_continuation([], "W3"), *shuffle_pages(3, first=30))
    first = call(worker, "tracksFor", kind="playlist", id="MLCT", limit=50)["result"]
    assert len(first["items"]) == 50 and worker.library_shuffle["token"] == "W2"
    more = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True, limit=50)["result"]
    ids = [t["videoId"] for t in more["items"]]
    assert ids == [f"shuf{i:07d}" for i in range(50, 80)], "the new shuffle's songs not given yet"
    assert more["complete"] is False, "it goes on"
    sent = worker.yt.requests
    assert [r[2] for r in sent[2:]] == [get_continuation_string("W2"), "", get_continuation_string("W1")]
    assert sent[3][1]["params"] == library.SHUFFLE_PARAMS, "a new shuffle of the library"
    assert worker.library_shuffle["token"] == "W2" and worker.library_shuffle["lost"] is False
    # The new shuffle's own end is the end.
    last = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True, limit=50)["result"]
    assert [t["videoId"] for t in last["items"]] == [f"shuf{i:07d}" for i in range(80, 105)]
    assert last["complete"] is True
    done = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True)["result"]
    assert done == {"playlistId": "MLCT", "items": [], "complete": True, "total": None}
    assert len(worker.yt.requests) == 6


def test_a_first_shuffle_cut_short_goes_on_in_a_new_one():
    # The empty page came while Shuffle all read its first songs: `more`
    # starts a new shuffle at once rather than ending.
    worker = worker_for(watch_panel([panel_track(i) for i in range(25)], "W1"), panel_continuation([], "W2"),
                        *shuffle_pages(3, first=10))
    first = call(worker, "tracksFor", kind="playlist", id="MLCT", limit=50)["result"]
    assert len(first["items"]) == 25 and first["complete"] is True
    assert worker.library_shuffle["lost"] is True and worker.library_shuffle["token"] is None
    more = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True, limit=50)["result"]
    assert [t["videoId"] for t in more["items"]] == [f"shuf{i:07d}" for i in range(25, 60)]
    assert more["complete"] is False
    assert worker.yt.requests[2][2] == "" and worker.yt.requests[3][2] == get_continuation_string("W1")


def test_a_shuffle_read_that_fails_keeps_the_slot():
    worker = worker_for(*shuffle_pages(2)[:1], panel_continuation([panel_track(i) for i in range(25, 50)], "W2"),
                        panel_continuation([], "W3"), {})
    call(worker, "tracksFor", kind="playlist", id="MLCT", limit=50)
    before = dict(worker.library_shuffle, seen=set(worker.library_shuffle["seen"]))
    reply = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True)
    assert reply["error"]["code"] == "UPSTREAM", "the new shuffle came with no queue"
    assert worker.library_shuffle == before, "asked again, it reads from the same place"


def test_the_shuffle_slot_is_never_cached_or_logged(tmp_path):
    import ytm_cache as caches

    cache = caches.DiskCache(tmp_path / "data")
    worker = worker_for(*shuffle_pages(3), cache=cache)
    call(worker, "tracksFor", kind="playlist", id="MLCT", limit=50, cache="prefer")
    assert not list((tmp_path / "data").glob("*tracksFor*")), "tracksFor is not cached"
    assert worker.library_shuffle["token"] == "W2" and worker.library_shuffle["pending"] == []


# ---- albums over 200 songs ----------------------------------------------------------------------------


class AlbumYT:
    def __init__(self, album, playlist=None):
        self.album, self.playlist, self.calls = album, playlist, []

    def get_album(self, browse_id):
        self.calls.append(("get_album", browse_id))
        return self.album

    def get_playlist(self, playlist_id, limit=100, related=False):
        self.calls.append(("get_playlist", playlist_id, limit))
        return self.playlist


@pytest.fixture
def big(load_fixture):
    data = load_fixture("album_big.json")
    return data["album"], data["playlist"]


def test_an_album_past_200_rows_gets_the_rest_from_its_audio_playlist(big):
    album, playlist = big
    assert len(album["tracks"]) == 200 and album["trackCount"] == 233 and len(playlist["tracks"]) == 233
    fake = AlbumYT(album, playlist)
    page = call(ytm.Worker(fake, "none"), "album", browseId="MPREb_pCTElvzIlpy")["result"]
    tracks = page["tracks"]
    # One row of the page has no video (unavailable): 199 + the 33 the page left out.
    assert len(tracks) == 232 and page["trackCount"] == 233 and page["unavailable"] == 1
    assert page["truncated"] is False and page["capped"] is False
    assert [t["trackNumber"] for t in tracks[-33:]] == list(range(201, 234))
    assert tracks[199]["title"] == playlist["tracks"][200]["title"]
    assert all(t["setVideoId"] is None and t["album"]["id"] == "MPREb_pCTElvzIlpy" for t in tracks[199:])
    assert all(t["kind"] == "song" for t in tracks)
    assert fake.calls == [("get_album", "MPREb_pCTElvzIlpy"), ("get_playlist", album["audioPlaylistId"], 233)]


def test_tracks_for_plays_every_song_of_a_big_album(big):
    album, playlist = big
    fake = AlbumYT(album, playlist)
    result = call(ytm.Worker(fake, "none"), "tracksFor", kind="album", id="MPREb_pCTElvzIlpy")["result"]
    assert len(result["items"]) == 232 and result["complete"] is True and result["total"] == 233
    cut = call(ytm.Worker(AlbumYT(album, playlist), "none"), "tracksFor", kind="album", id="MPREb_pCTElvzIlpy",
               limit=100)["result"]
    assert len(cut["items"]) == 100 and cut["complete"] is False


def test_an_album_of_200_or_fewer_songs_costs_nothing_more(big):
    album, _ = big
    for data in (dict(album, trackCount=200), dict(album, tracks=album["tracks"][:150], trackCount=233),
                 dict(album, audioPlaylistId=None)):
        fake = AlbumYT(data, None)
        assert call(ytm.Worker(fake, "none"), "album", browseId="MPREb_x")["ok"]
        assert [c[0] for c in fake.calls] == ["get_album"]


def test_a_playlist_that_does_not_line_up_shows_the_cut_and_plays_the_playlist(big):
    album, playlist = big
    shifted = dict(playlist, tracks=playlist["tracks"][1:])
    page = call(ytm.Worker(AlbumYT(album, shifted), "none"), "album", browseId="MPREb_pCTElvzIlpy")["result"]
    assert len(page["tracks"]) == 199 and page["truncated"] is True and page["capped"] is True
    assert page["unavailable"] is None
    fake = AlbumYT(album, shifted)
    result = call(ytm.Worker(fake, "none"), "tracksFor", kind="album", id="MPREb_pCTElvzIlpy")["result"]
    playable = [t for t in shifted["tracks"] if t.get("videoId")]
    assert len(result["items"]) == len(playable) and result["playlistId"] == album["audioPlaylistId"]
    assert result["complete"] is True
    # The playlist read to check the page plays as it is: no second read.
    assert fake.calls == [("get_album", "MPREb_pCTElvzIlpy"), ("get_playlist", album["audioPlaylistId"], 233)]
    fake = AlbumYT(album, shifted)
    cut = call(ytm.Worker(fake, "none"), "tracksFor", kind="album", id="MPREb_pCTElvzIlpy", limit=100)["result"]
    assert len(cut["items"]) == 100 and cut["complete"] is False and len(fake.calls) == 2


def test_a_playlist_longer_than_the_album_is_read_on_to_the_limit(big):
    album, playlist = big
    other = [dict(t) for t in playlist["tracks"]]
    other[199]["title"] = "Another song"
    longer = dict(playlist, tracks=other, trackCount=300)
    fake = AlbumYT(album, longer)
    result = call(ytm.Worker(fake, "none"), "tracksFor", kind="album", id="MPREb_pCTElvzIlpy")["result"]
    assert fake.calls[1:] == [("get_playlist", album["audioPlaylistId"], 233),
                              ("get_playlist", album["audioPlaylistId"], play.MAX_TRACKS)]
    assert result["total"] == 300 and len(result["items"]) == 232


def test_album_rest_alone(big):
    album, playlist = big
    data, whole = collections.album_rest(ytm.Worker(AlbumYT(album, playlist), "none"), album)
    assert whole is True and len(data["tracks"]) == 233 and len(album["tracks"]) == 200, "a copy"
    assert collections.ALBUM_PAGE_ROWS == 200
    shifted = dict(playlist, tracks=playlist["tracks"][1:])
    data, whole, read = collections.album_read(ytm.Worker(AlbumYT(album, shifted), "none"), album)
    assert whole is False and data is album and read is shifted
    assert collections.album_read(ytm.Worker(AlbumYT(album, playlist), "none"), album)[2] is None


def test_an_album_page_is_read_on_the_pages_lane():
    # An album over 200 songs takes two or three more requests.
    assert ytm.LANES["album"] == "pages"


def test_the_big_album_fixture_holds_no_tokens():
    from conftest import FIXTURES

    text = (FIXTURES / "album_big.json").read_text()
    for word in ("token", "continuation", "cookie", "\u2013", "\u2014"):
        assert word not in text.lower(), word
