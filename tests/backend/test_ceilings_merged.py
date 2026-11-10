"""No ceilings (G1) with lane A's slices: what the two lanes change in the
same reads holds together. Shuffle all's `more` pages and the library in
its order carry the explicit badges (G4), an album over 200 songs keeps its
rest, its explicit marks and its shelf pins (G2), and tracksFor routes
every kind: uploads (S10), the library in its order and the server shuffle
(G1), Liked Music, mixes.

Hand-built in the served shapes, as test_ceilings and test_menus_lists.
"""

import pytest

import ytm
import ytm_collections as collections
import ytm_play as play
import ytm_raw as raw
from test_ceilings import AlbumYT, big, queue_more, queue_song, vid  # noqa: F401 (big is a fixture)
from test_library import call, panel_continuation, panel_track, watch_panel, worker_for
from test_menus_lists import BADGE, explicit_ids


def badged(track):
    track["playlistPanelVideoRenderer"]["badges"] = BADGE
    return track


def test_shuffle_all_more_pages_carry_the_explicit_badges():
    # The first read takes 40 of 50 and keeps 10 for `more`; `more` then
    # reads the next page. A badge on a kept song and on the new page counts.
    first = [panel_track(i) for i in range(25)]
    second = [panel_track(i) for i in range(25, 50)]
    third = [panel_track(i) for i in range(50, 75)]
    badged(first[2])
    badged(second[20])  # shuf0000045: read past the first limit, held in the worker
    badged(third[5])  # shuf0000055: a `more` page
    worker = worker_for(watch_panel(first, "W1"), panel_continuation(second, "W2"), panel_continuation(third, None))
    reply = call(worker, "tracksFor", kind="playlist", id="MLCT", limit=40)
    assert reply["ok"], reply
    assert explicit_ids(reply["result"]["items"]) == ["shuf0000002"]
    more = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True, limit=50)
    assert more["ok"], more
    ids = [t["videoId"] for t in more["result"]["items"]]
    assert ids[:10] == [f"shuf{i:07d}" for i in range(40, 50)], "the held songs first"
    assert explicit_ids(more["result"]["items"]) == ["shuf0000045", "shuf0000055"]


def test_a_new_shuffle_from_more_carries_the_badges_too():
    # No memo (the worker restarted): `more` starts a new shuffle.
    first = [panel_track(i) for i in range(25)]
    badged(first[7])
    worker = worker_for(watch_panel(first, "W1"), panel_continuation([panel_track(i) for i in range(25, 50)], None))
    result = call(worker, "tracksFor", kind="playlist", id="MLCT", more=True)["result"]
    assert result["complete"] is False and explicit_ids(result["items"]) == ["shuf0000007"]


def test_the_library_in_its_order_carries_the_explicit_badges():
    first = [queue_song(p) for p in range(75, 125)]
    badged(first[30])  # place 105
    later = [queue_song(p) for p in range(125, 149)]
    badged(later[3])  # place 128
    worker = worker_for(watch_panel(first, "Q1"), panel_continuation(later, "Q2"), queue_more(149, 173, "Q3"),
                        queue_more(173, 197))
    reply = call(worker, "tracksFor", kind="librarySongs", id="MLCT", videoId=vid(100), index=101)
    assert reply["ok"], reply
    items = reply["result"]["items"]
    assert items[0]["videoId"] == vid(100) and items[0]["libraryIndex"] == 101
    assert explicit_ids(items) == [vid(105), vid(128)]


@pytest.fixture
def pinned_cards(monkeypatch):
    """raw.card_pins as if the album page's other version served its pin."""
    pin = {"pinned": True, "listenAgainTokens": {"pin": "P", "unpin": "U"}}
    monkeypatch.setattr(raw, "card_pins", lambda responses: {"MPREb_other": pin})
    return pin


def test_a_big_album_gets_its_rest_explicit_marks_and_shelf_pins(big, pinned_cards):
    album, playlist = big
    album = dict(album, other_versions=[{"browseId": "MPREb_other", "title": "Load (Other)", "type": "Album",
                                         "artists": [{"name": "Metallica", "id": "UC1"}], "year": "1996"}])
    songs = [dict(t) for t in playlist["tracks"]]
    songs[220]["isExplicit"] = True  # past the page's 200 rows
    playlist = dict(playlist, tracks=songs)
    page = call(ytm.Worker(AlbumYT(album, playlist), "none"), "album", browseId="MPREb_pCTElvzIlpy")["result"]
    assert len(page["tracks"]) == 232 and page["truncated"] is False
    assert [t["videoId"] for t in page["tracks"] if t["explicit"]] == [songs[220]["videoId"]]
    versions = [s for s in page["sections"] if s["items"] and s["items"][0]["browseId"] == "MPREb_other"]
    assert len(versions) == 1 and versions[0]["items"][0]["pinned"] is True
    assert versions[0]["items"][0]["listenAgainTokens"] == pinned_cards["listenAgainTokens"]
    result = call(ytm.Worker(AlbumYT(album, playlist), "none"), "tracksFor", kind="album",
                  id="MPREb_pCTElvzIlpy")["result"]
    assert len(result["items"]) == 232 and result["complete"] is True
    assert [t["videoId"] for t in result["items"] if t["explicit"]] == [songs[220]["videoId"]]


@pytest.mark.parametrize("kind, collection_id, reader", [
    ("librarySongs", "MLCT", "librarySongs"),
    ("librarySongs", "VLMLCT", "librarySongs"),
    ("playlist", "MLCT", "libraryShuffle"),
    ("album", "FEmusic_library_privately_owned_release_detailb_x", "uploadAlbum"),
    ("artist", "FEmusic_library_privately_owned_artist_detaila_x", "uploadArtist"),
    ("liked", "LM", "liked"),
    ("playlist", "VLLM", "liked"),
    ("mix", "RDAMVMabc", "mix"),
    ("album", "MPREb_x", "album"),
    ("artist", "UC1", "artist"),
    ("playlist", "PL1", "playlist"),
])
def test_one_router_sends_every_kind(kind, collection_id, reader):
    assert play.route(kind, collection_id)[0] == reader
    assert kind in play.KINDS


def test_more_and_the_library_order_need_the_account_as_before():
    reply = call(ytm.Worker(object(), "none"), "tracksFor", kind="librarySongs", id="MLCT", videoId=vid(1), index=2)
    assert reply["ok"] is False and reply["error"]["code"] in ("AUTH_REQUIRED", "AUTH_EXPIRED", "NOT_SIGNED_IN")


def test_collections_keep_one_explicit_marker():
    # G4's helper is the one the library's readers use.
    assert collections.mark_explicit([{"videoId": "a"}], []) == [{"videoId": "a"}]
