"""ytm_library: the library lists and their sort orders (S2).

Hand-built responses only: these pages are private, so nothing is recorded
from them. The shapes follow the served ones (grid or music shelf inside an
item section, the sort menu in a musicSideAlignedItemRenderer header, one
reload token per option that carries the browse params it re-sorts by).
"""

import base64
import urllib.parse

import pytest
from ytmusicapi.continuations import get_continuation_string
from ytmusicapi.mixins._utils import prepare_order_params

import ytm
import ytm_cache as caches
import ytm_library as library
import ytm_play as play
import ytm_raw as raw
from test_worker import THUMBS, album_item, call, track_item

ALBUMS = "FEmusic_liked_albums"
ARTISTS = "FEmusic_library_corpus_track_artists"
SONGS = "FEmusic_liked_videos"
LANDING = "FEmusic_library_landing"
PLAYLISTS = "FEmusic_liked_playlists"


# ---- builders ----------------------------------------------------------------------------


def reload_token(browse_id, params):
    """A reload token as served: the browse request (browseId and params) as
    url-quoted base64."""
    data = b"\x12" + bytes([len(browse_id)]) + browse_id.encode() + b"\x1a" + bytes([len(params)]) + params.encode()
    return urllib.parse.quote(base64.urlsafe_b64encode(data).decode(), safe="")


def sort_menu(browse_id, options, selected=0):
    """The header with a sort menu: ``options`` are (title, params) pairs."""
    items = []
    for i, (title, params) in enumerate(options):
        items.append({"musicMultiSelectMenuItemRenderer": {
            "title": {"runs": [{"text": title}]},
            "formItemEntityKey": f"key{i}",
            "selectedCommand": {"commandExecutorCommand": {"commands": [
                {"musicCheckboxFormItemMutatedCommand": {"formItemEntityKey": f"key{i}", "newCheckedState": True}},
                {"browseSectionListReloadEndpoint": {"continuation": {"reloadContinuationData": {
                    "continuation": reload_token(browse_id, params)}}}}]}}}})
    header = {"musicSideAlignedItemRenderer": {"endItems": [{"musicSortFilterButtonRenderer": {
        "title": {"runs": [{"text": options[selected][0]}]},
        "menu": {"musicMultiSelectMenuRenderer": {"options": items}}}}]}}
    mutations = [{"entityKey": f"key{i}", "payload": {"musicFormBooleanChoice": {"selected": i == selected}}}
                 for i in range(len(options))]
    return header, {"entityBatchUpdate": {"mutations": mutations}}


STANDARD = [("Recently saved", prepare_order_params("recently_added")), ("A to Z", prepare_order_params("a_to_z")),
            ("Z to A", prepare_order_params("z_to_a"))]
MOST_SONGS = ("Most songs", "ggMGKgQIAhAB")


def next_data(token):
    return [{"nextContinuationData": {"continuation": token}}] if token else []


def browse(renderer, contents, token=None, menu=None):
    """A library page: the list in an item section, the sort menu above it."""
    key = "items" if renderer == "gridRenderer" else "contents"
    section = {"contents": [{"itemSectionRenderer": {"contents": [
        {renderer: {key: contents, "continuations": next_data(token)}}]}}]}
    response = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
        {"tabRenderer": {"content": {"sectionListRenderer": section}}}]}}}
    if menu:
        section["header"], response["frameworkUpdates"] = menu
    return response


def continuation(renderer, contents, token=None):
    kind = "gridContinuation" if renderer == "gridRenderer" else "musicShelfContinuation"
    key = "items" if renderer == "gridRenderer" else "contents"
    return {"continuationContents": {kind: {key: contents, "continuations": next_data(token)}}}


def resorted(renderer, contents, token=None):
    key = "items" if renderer == "gridRenderer" else "contents"
    return {"continuationContents": {"sectionListContinuation": {"contents": [{"itemSectionRenderer": {
        "contents": [{renderer: {key: contents, "continuations": next_data(token)}}]}}]}}}


def page_type(browse_id, kind):
    return {"browseEndpoint": {"browseId": browse_id, "browseEndpointContextSupportedConfigs": {
        "browseEndpointContextMusicConfig": {"pageType": f"MUSIC_PAGE_TYPE_{kind}"}}}}


def library_album(n):
    """A grid album as parsers.library.parse_albums reads it."""
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": f"Album {n}", "navigationEndpoint": page_type(f"MPREb_{n}", "ALBUM")}]},
        "subtitle": {"runs": [{"text": "Album"}, {"text": " • "},
                              {"text": "Artist", "navigationEndpoint": page_type("UCart", "ARTIST")},
                              {"text": " • "}, {"text": "2020"}]},
        "navigationEndpoint": page_type(f"MPREb_{n}", "ALBUM"),
        "thumbnailRenderer": THUMBS}}


def column(text):
    return {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": text}]}}}


def watch_menu(icon, playlist_id):
    return {"menuNavigationItemRenderer": {"icon": {"iconType": icon}, "navigationEndpoint": {
        "watchPlaylistEndpoint": {"playlistId": playlist_id}}}}


def library_artist(n, browse_id=None, kind="LIBRARY_ARTIST", subtitle="12 songs"):
    return {"musicResponsiveListItemRenderer": {
        "flexColumns": [column(f"Artist {n}"), column(subtitle)],
        "navigationEndpoint": page_type(browse_id or f"MPLAUC{n:04d}", kind),
        "menu": {"menuRenderer": {"items": [watch_menu("MUSIC_SHUFFLE", f"RDAO{n}"), watch_menu("MIX", f"RDEM{n}")]}},
        "thumbnail": THUMBS}}


def shuffle_row():
    return {"musicResponsiveListItemRenderer": {
        "flexColumns": [column("Shuffle all")],
        "navigationEndpoint": {"watchPlaylistEndpoint": {"playlistId": "MLCT", "params": library.SHUFFLE_PARAMS}}}}


def library_playlist(n, owned=False):
    playlist_id = f"PL{n:04d}"
    menu = [{"menuNavigationItemRenderer": {"navigationEndpoint": {"playlistEditorEndpoint": {
        "playlistId": playlist_id}}}}] if owned else []
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": f"Playlist {n}", "navigationEndpoint": page_type("VL" + playlist_id, "PLAYLIST")}]},
        "subtitle": {"runs": [{"text": "Someone"}, {"text": " • "}, {"text": "12 tracks"}]},
        "navigationEndpoint": page_type("VL" + playlist_id, "PLAYLIST"),
        "menu": {"menuRenderer": {"items": menu}},
        "thumbnailRenderer": THUMBS}}


NEW_PLAYLIST = {"musicTwoRowItemRenderer": {"title": {"runs": [{"text": "New playlist"}]}, "subtitle": {},
                                            "navigationEndpoint": {"createPlaylistEndpoint": {}}}}


class LibraryYT:
    """A signed-in client: answers _send_request from a queue and records it.
    Every request counts toward the reply's http, as through the session hook."""

    def __init__(self, *responses, playlists=None):
        self.responses = list(responses)
        self.requests = []
        self.playlists = playlists or []
        self.calls = []

    def _send_request(self, endpoint, body, additionalParams=""):
        raw._local.http = raw.http_count() + 1
        self.requests.append((endpoint, dict(body), additionalParams))
        return self.responses.pop(0)

    def get_account_info(self):
        raw._local.http = raw.http_count() + 1
        return {"accountName": "test"}

    def get_library_playlists(self, limit=25):
        self.calls.append(("get_library_playlists", limit))
        return self.playlists


def worker_for(*responses, cache=None, **kwargs):
    return ytm.Worker(LibraryYT(*responses, **kwargs), "ok", cache=cache)


def items_of(page):
    return [i for s in page["sections"] for i in s["items"]]


# ---- albums: pages, cursor, orders -----------------------------------------------------------


def test_albums_first_page_then_cursor():
    menu = sort_menu(ALBUMS, STANDARD)
    worker = worker_for(browse("gridRenderer", [library_album(i) for i in range(25)], "G1", menu),
                        continuation("gridRenderer", [library_album(i) for i in range(25, 70)], "G2"))
    reply = call(worker, "libraryAlbums")
    assert reply["ok"], reply
    page = reply["result"]
    assert (page["type"], page["kind"], page["id"]) == ("page", "libraryAlbums", ALBUMS)
    albums = items_of(page)
    assert len(albums) == 70 and albums[0]["type"] == "album" and albums[0]["browseId"] == "MPREb_0"
    assert albums[0]["artists"] == [{"name": "Artist", "id": "UCart"}] and albums[0]["year"] == "2020"
    assert albums[0]["albumType"] == "Album"
    assert page["cursor"] == "c.G2" and page["hasMore"] is True
    assert page["orders"] == [{"index": "recently_added", "title": "Recently saved", "selected": True},
                              {"index": "a_to_z", "title": "A to Z", "selected": False},
                              {"index": "z_to_a", "title": "Z to A", "selected": False}]
    assert page["order"] == "recently_added"
    sent = worker.yt.requests
    assert sent == [("browse", {"browseId": ALBUMS}, ""), ("browse", {"browseId": ALBUMS}, get_continuation_string("G1"))]
    assert reply["http"] == 3, "session check, first page, one continuation"

    worker.yt.responses = [continuation("gridRenderer", [library_album(i) for i in range(70, 75)])]
    reply = call(worker, "libraryAlbums", cursor="c.G2")
    assert reply["ok"] and [a["browseId"] for a in items_of(reply["result"])] == [f"MPREb_{i}" for i in range(70, 75)]
    assert reply["result"]["cursor"] is None and reply["result"]["hasMore"] is False
    assert worker.yt.requests[-1] == ("browse", {"browseId": ALBUMS}, get_continuation_string("G2"))


def test_an_order_goes_into_the_body_as_ytmusicapi_sends_it():
    menu = sort_menu(ALBUMS, STANDARD, selected=1)
    worker = worker_for(browse("gridRenderer", [library_album(i) for i in range(60)], "G1", menu),
                        continuation("gridRenderer", [library_album(99)]))
    reply = call(worker, "libraryAlbums", order="a_to_z")
    assert reply["ok"]
    body = {"browseId": ALBUMS, "params": prepare_order_params("a_to_z")}
    assert worker.yt.requests == [("browse", body, "")], "60 albums: no continuation needed"
    assert reply["result"]["order"] == "a_to_z"
    assert [o["selected"] for o in reply["result"]["orders"]] == [False, True, False]
    reply = call(worker, "libraryAlbums", order="a_to_z", cursor=reply["result"]["cursor"])
    assert worker.yt.requests[-1] == ("browse", body, get_continuation_string("G1")), "later pages keep the order"


@pytest.mark.parametrize("order", ["newest", "A to Z", 3, True, "menu:x", "menu:123", ""])
def test_bad_orders_are_bad_requests(order):
    worker = worker_for()
    reply = call(worker, "libraryAlbums", order=order)
    assert reply["error"]["code"] == "BAD_REQUEST"
    assert worker.yt.requests == []


@pytest.mark.parametrize("method", sorted(library.METHODS))
def test_signed_out_is_auth_required(method):
    yt = LibraryYT()
    reply = call(ytm.Worker(yt, "none"), method)
    assert reply["error"]["code"] == "AUTH_REQUIRED"
    assert yt.requests == [] and yt.calls == []


# ---- served orders: matched by the params in their tokens, extras by reload ---------------


def test_served_orders_are_matched_by_token_not_title():
    # Another language, another position: the token still says which order it is.
    retitled = [("Z bis A", prepare_order_params("z_to_a")), ("Zuletzt gespeichert",
                prepare_order_params("recently_added")), ("Meiste Songs", MOST_SONGS[1])]
    worker = worker_for(browse("musicShelfRenderer", [library_artist(i) for i in range(60)], None,
                               sort_menu(ARTISTS, retitled, selected=2)))
    page = call(worker, "libraryArtists")["result"]
    assert page["orders"] == [{"index": "z_to_a", "title": "Z bis A", "selected": False},
                              {"index": "recently_added", "title": "Zuletzt gespeichert", "selected": False},
                              {"index": "menu:2", "title": "Meiste Songs", "selected": True}]
    assert page["order"] == "menu:2"


def test_token_order_reads_only_known_params():
    assert library.token_order(reload_token(ALBUMS, prepare_order_params("a_to_z"))) == "a_to_z"
    assert library.token_order(reload_token(ARTISTS, MOST_SONGS[1])) is None
    for junk in (None, "", "!!!", 5):
        assert library.token_order(junk) is None


def test_a_menu_order_uses_the_reload_style_with_that_option():
    options = STANDARD + [MOST_SONGS]
    base = browse("musicShelfRenderer", [library_artist(i) for i in range(5)], "S0", sort_menu(ARTISTS, options, 1))
    worker = worker_for(base, resorted("musicShelfRenderer", [library_artist(i) for i in range(50, 100)], "S1"))
    reply = call(worker, "libraryArtists", order="menu:3")
    assert reply["ok"], reply
    plain = {"browseId": ARTISTS}
    token = reload_token(ARTISTS, MOST_SONGS[1])
    assert worker.yt.requests == [("browse", plain, ""), ("browse", plain, get_continuation_string(token))]
    page = reply["result"]
    assert [a["name"] for a in items_of(page)][:2] == ["Artist 50", "Artist 51"]
    assert page["order"] == "menu:3" and [o["selected"] for o in page["orders"]] == [False, False, False, True]
    assert page["cursor"] == "c.S1"
    # Its later pages continue as ctoken on the plain body.
    worker.yt.responses = [continuation("musicShelfRenderer", [library_artist(200)])]
    assert call(worker, "libraryArtists", order="menu:3", cursor="c.S1")["ok"]
    assert worker.yt.requests[-1] == ("browse", plain, get_continuation_string("S1"))


def test_a_menu_order_of_a_standard_option_reports_that_order():
    base = browse("gridRenderer", [library_album(0)], None, sort_menu(ALBUMS, STANDARD))
    worker = worker_for(base, resorted("gridRenderer", [library_album(1)]))
    page = call(worker, "libraryAlbums", order="menu:2")["result"]
    assert page["order"] == "z_to_a" and [o["selected"] for o in page["orders"]] == [False, False, True]


def test_without_a_served_menu_extra_orders_are_hidden_and_refused():
    shelf = browse("musicShelfRenderer", [library_artist(i) for i in range(3)])
    page = call(worker_for(shelf), "libraryArtists")["result"]
    assert [(o["index"], o["title"]) for o in page["orders"]] == [
        ("a_to_z", "A to Z"), ("z_to_a", "Z to A"), ("recently_added", "Recently saved")]
    assert page["order"] is None and not any(o["selected"] for o in page["orders"])
    reply = call(worker_for(browse("musicShelfRenderer", [library_artist(0)])), "libraryArtists", order="menu:3")
    assert reply["error"]["code"] == "BAD_REQUEST"
    # The landing has no standard orders of its own to fall back on.
    landing = call(worker_for(browse("gridRenderer", [album_item(0)])), "libraryLanding")["result"]
    assert landing["orders"] == []


def test_an_ordered_first_page_drops_the_cached_browse_pages(tmp_path):
    cache = caches.DiskCache(tmp_path / "data")
    cache.put("browsePage", {"browseId": ALBUMS}, {"type": "page"})
    cache.put("browsePage", {"browseId": ARTISTS}, {"type": "page"})
    cache.put("libraryAlbums", {}, {"type": "page"})
    worker = worker_for(browse("gridRenderer", [library_album(0)], None, sort_menu(ALBUMS, STANDARD)), cache=cache)
    reply = call(worker, "libraryAlbums", order="z_to_a", cache="refresh")
    assert reply["ok"]
    assert cache.get("browsePage", {"browseId": ALBUMS}) is None, "YouTube saved the order: that copy is stale"
    assert cache.get("browsePage", {"browseId": ARTISTS}) is not None
    assert cache.get("libraryAlbums", {"order": "z_to_a"}) is not None, "the ordered first page is kept"
    assert cache.get("libraryAlbums", {}) is not None, "the plain copy is refreshed behind its next showing"


@pytest.mark.parametrize("method", sorted(library.BROWSE_IDS))
def test_a_browse_page_resort_drops_the_list_that_reads_that_page(tmp_path, method):
    # browsePage re-sorts the same page and YouTube saves that order: the
    # list read from it goes in every order (ORDER_DEPENDENTS); the others stay.
    browse_id = library.BROWSE_IDS[method]
    cache = caches.DiskCache(tmp_path / "data")
    for name in library.METHODS:
        cache.put(name, {}, {"type": "page"})
        cache.put(name, {"order": "a_to_z"}, {"type": "page"})
    worker = worker_for(browse("gridRenderer", [library_album(0)], None, sort_menu(browse_id, STANDARD)),
                        resorted("gridRenderer", [library_album(1)]), cache=cache)
    reply = call(worker, "browsePage", browseId=browse_id, order=1, cache="refresh")
    assert reply["ok"], reply
    assert reply["result"]["order"] == 1
    for name in library.METHODS:
        for params in ({}, {"order": "a_to_z"}):
            assert (cache.get(name, params) is None) == (name == method), (name, params)


def test_first_pages_are_cached_by_order_and_cursor_pages_never(tmp_path):
    cache = caches.DiskCache(tmp_path / "data")
    menu = sort_menu(ALBUMS, STANDARD)
    worker = worker_for(browse("gridRenderer", [library_album(i) for i in range(60)], "G1", menu), cache=cache)
    first = call(worker, "libraryAlbums", cache="prefer")
    assert first["ok"] and first["cached"] is False
    hit = call(worker, "libraryAlbums", cache="prefer")
    assert hit["cached"] is True and hit["http"] == 0
    assert hit["result"]["cursor"] is None and hit["result"]["hasMore"] is True, "no token on disk"
    assert cache.get("libraryAlbums", {"order": "a_to_z"}) is None, "another order is another entry"

    worker.yt.responses = [continuation("gridRenderer", [library_album(i) for i in range(60, 62)])]
    reply = call(worker, "libraryAlbums", cursor="c.G1", cache="prefer")
    assert reply["ok"] and reply["cached"] is False and len(items_of(reply["result"])) == 2
    assert len(list((tmp_path / "data").glob("v2-libraryAlbums-*.json"))) == 1, "the cursor page was not stored"


def test_a_call_stays_within_three_requests_with_the_session_check():
    options = STANDARD + [MOST_SONGS]
    base = browse("musicShelfRenderer", [library_artist(0)], "S0", sort_menu(ARTISTS, options))
    worker = worker_for(base, resorted("musicShelfRenderer", [library_artist(1)], "S1"),
                        continuation("musicShelfRenderer", [library_artist(2)]))
    reply = call(worker, "libraryArtists", order="menu:3")
    assert reply["ok"] and reply["http"] == 3, "session check, page, re-sort: no continuation"
    assert reply["result"]["cursor"] == "c.S1"
    reply = call(worker, "libraryArtists", order="menu:3", cursor="c.S1")
    assert reply["ok"] and reply["http"] == 1, "the session is checked once per worker"


# ---- artists, subscriptions, songs, landing -----------------------------------------------------


def test_artists_strip_mpla_and_keep_the_mixes():
    page = call(worker_for(browse("musicShelfRenderer", [library_artist(7)], None, sort_menu(ARTISTS, STANDARD))),
                "libraryArtists")["result"]
    artist = items_of(page)[0]
    assert artist["type"] == "artist" and artist["channelId"] == "UC0007" and artist["name"] == "Artist 7"
    assert artist["subtitle"] == "12 songs", "the row's own subtitle, not ytmusicapi's first word"
    assert (artist["shuffleId"], artist["radioId"]) == ("RDAO7", "RDEM7")


def test_subscriptions_are_artists_and_channels():
    rows = [library_artist(1, "UC0001", "ARTIST", "4.11K subscribers"),
            library_artist(2, "UC0002", "USER_CHANNEL", "12 subscribers")]
    page = call(worker_for(browse("musicShelfRenderer", rows)), "librarySubscriptions")["result"]
    got = items_of(page)
    assert [(i["type"], i["channelId"], i["subtitle"]) for i in got] == [
        ("artist", "UC0001", "4.11K subscribers"), ("profile", "UC0002", "12 subscribers")]
    assert page["kind"] == "librarySubscriptions" and page["id"] == "FEmusic_library_corpus_artists"


def test_songs_drop_the_shuffle_row_and_expose_its_mix():
    menu = sort_menu(SONGS, STANDARD)
    worker = worker_for(browse("musicShelfRenderer", [shuffle_row()] + [track_item(i) for i in range(60)], "S1", menu))
    page = call(worker, "librarySongs")["result"]
    songs = items_of(page)
    assert len(songs) == 60 and all(s["type"] == "track" for s in songs)
    assert songs[0]["videoId"] == "vid00000000"
    assert page["header"] == {"shuffleId": "MLCT"}
    # A page that starts with a song has no shuffle row to drop.
    page = call(worker_for(browse("musicShelfRenderer", [track_item(i) for i in range(2)])), "librarySongs")["result"]
    assert len(items_of(page)) == 2 and page["header"] == {}


def test_landing_mixes_item_types_in_its_served_orders():
    options = [("Recent activity", "ggMGKgQIBBAB"), ("Recently saved", prepare_order_params("recently_added")),
               ("Recently played", "ggMGKgQIAxAB")]
    grid = [album_item(1), library_playlist(2), {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": "Someone", "navigationEndpoint": page_type("UCsomeone", "ARTIST")}]},
        "subtitle": {"runs": [{"text": "Artist"}]}, "navigationEndpoint": page_type("UCsomeone", "ARTIST"),
        "thumbnailRenderer": THUMBS}}]
    page = call(worker_for(browse("gridRenderer", grid, None, sort_menu(LANDING, options))), "libraryLanding")["result"]
    assert [i["type"] for i in items_of(page)] == ["album", "playlist", "artist"]
    assert [(o["index"], o["title"]) for o in page["orders"]] == [
        ("menu:0", "Recent activity"), ("recently_added", "Recently saved"), ("menu:2", "Recently played")]
    assert page["order"] == "menu:0"


def test_an_empty_list_is_an_empty_page_or_its_message():
    message = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": [
            {"messageRenderer": {"text": {"runs": [{"text": "No albums yet"}]}}}]}}]}}}}]}}}
    page = call(worker_for(message), "libraryAlbums")["result"]
    assert page["sections"] == [] and page["message"] == "No albums yet" and page["cursor"] is None


# ---- playlists ----------------------------------------------------------------------------------


def test_playlists_without_an_order_read_them_all():
    listed = [{"title": "Liked Music", "playlistId": "LM", "description": "Auto playlist", "thumbnails": []},
              {"title": "Mine", "playlistId": "PL1", "owned": True, "count": "12", "author": [{"name": "Me"}],
               "description": "Me • 12 tracks"},
              {"title": "Episodes for later", "playlistId": "SE", "description": "Auto playlist"}]
    worker = worker_for(playlists=listed)
    reply = call(worker, "libraryPlaylists")
    assert reply["ok"], reply
    assert worker.yt.calls == [("get_library_playlists", None)], "every playlist, not the first 25 or 200"
    got = reply["result"]["items"]
    assert [(p["playlistId"], p["special"], p["description"]) for p in got] == [
        ("LM", "liked", "Auto playlist"), ("PL1", None, "Me • 12 tracks"), ("SE", "episodes", "Auto playlist")]
    assert got[1]["count"] == 12 and got[1]["owned"] is True
    # A client that captured nothing (no raw hook) offers the standard orders.
    assert [o["index"] for o in reply["result"]["orders"]] == ["a_to_z", "z_to_a", "recently_added"]


def test_playlists_in_an_order_read_the_raw_page_without_the_new_playlist_tile():
    menu = sort_menu(PLAYLISTS, STANDARD, selected=0)
    worker = worker_for(browse("gridRenderer", [NEW_PLAYLIST, library_playlist(1, owned=True)], "P1", menu),
                        continuation("gridRenderer", [library_playlist(2)], "P2"),
                        continuation("gridRenderer", [library_playlist(3)]))
    reply = call(worker, "libraryPlaylists", order="z_to_a")
    assert reply["ok"], reply
    body = {"browseId": PLAYLISTS, "params": prepare_order_params("z_to_a")}
    assert worker.yt.requests == [("browse", body, ""), ("browse", body, get_continuation_string("P1")),
                                  ("browse", body, get_continuation_string("P2"))], "read to the end"
    got = reply["result"]["items"]
    assert [p["playlistId"] for p in got] == ["PL0001", "PL0002", "PL0003"]
    assert got[0]["owned"] is True and got[0]["description"] == "Someone • 12 tracks"
    assert reply["result"]["order"] == "z_to_a" and worker.yt.calls == []


# ---- Shuffle all ----------------------------------------------------------------------------------


def panel_track(n):
    return {"playlistPanelVideoRenderer": {
        "videoId": f"shuf{n:07d}", "title": {"runs": [{"text": f"Song {n}"}]},
        "longBylineText": {"runs": [{"text": "Artist"}]},
        "lengthText": {"runs": [{"text": "3:00"}]},
        "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/vi/x/hq.jpg", "width": 120, "height": 90}]},
        "menu": {"menuRenderer": {"items": []}},
        "navigationEndpoint": {"watchEndpoint": {"videoId": f"shuf{n:07d}"}}}}


def watch_panel(tracks, token):
    return {"contents": {"singleColumnMusicWatchNextResultsRenderer": {"tabbedRenderer": {
        "watchNextTabbedResultsRenderer": {"tabs": [{"tabRenderer": {"content": {"musicQueueRenderer": {
            "content": {"playlistPanelRenderer": {"contents": tracks, "continuations": next_data(token)}}}}}}]}}}}}


def panel_continuation(tracks, token=None):
    return {"continuationContents": {"playlistPanelContinuation": {"contents": tracks,
                                                                   "continuations": next_data(token)}}}


def test_shuffle_all_reads_the_server_shuffle_through_tracks_for():
    worker = worker_for(watch_panel([panel_track(i) for i in range(25)], "W1"),
                        panel_continuation([panel_track(i) for i in range(25, 50)], "W2"),
                        panel_continuation([panel_track(i) for i in range(50, 75)], "W3"))
    reply = call(worker, "tracksFor", kind="playlist", id="MLCT", limit=40)
    assert reply["ok"], reply
    result = reply["result"]
    assert len(result["items"]) == 40 and result["items"][0]["videoId"] == "shuf0000000"
    assert result["playlistId"] == "MLCT" and result["complete"] is True
    body = worker.yt.requests[0][1]
    assert body["playlistId"] == "MLCT" and body["params"] == library.SHUFFLE_PARAMS
    assert [r[2] for r in worker.yt.requests] == ["", get_continuation_string("W1")], "panel pages until 40"
    assert play.route("playlist", "MLCT") == ("libraryShuffle", "MLCT")
    assert play.route("playlist", "PL1") == ("playlist", "PL1")


def test_shuffle_all_is_capped_and_needs_the_account():
    pages = [watch_panel([panel_track(i) for i in range(25)], "W1")]
    pages += [panel_continuation([panel_track(i) for i in range(n, n + 25)], f"W{n}") for n in range(25, 400, 25)]
    worker = worker_for(*pages)
    result = call(worker, "tracksFor", kind="playlist", id="MLCT", limit=500)["result"]
    assert len(result["items"]) == library.SHUFFLE_TRACKS
    assert len(worker.yt.requests) <= library.SHUFFLE_MAX_HTTP
    assert call(ytm.Worker(LibraryYT(), "none"), "tracksFor", kind="playlist", id="MLCT")["error"]["code"] == \
        "AUTH_REQUIRED"


# ---- registry ----------------------------------------------------------------------------------------


def test_registry():
    names = {"libraryPlaylists", "librarySongs", "libraryAlbums", "libraryArtists", "librarySubscriptions",
             "libraryLanding"}
    assert set(library.METHODS) == names
    assert names <= ytm.CACHEABLE and all(ytm.LANES[n] == "pages" for n in names)
    assert not names & set(ytm.RECORDERS), "private pages are never recorded"
    assert ytm.METHODS["libraryPlaylists"] is library.m_library_playlists
    for edit in ("createPlaylist", "deletePlaylist", "addToPlaylist", "removeFromPlaylist"):
        assert ("libraryLanding", None, None) in ytm.INVALIDATES[edit]
    assert ("librarySongs", None, None) in ytm.INVALIDATES["rate"]
