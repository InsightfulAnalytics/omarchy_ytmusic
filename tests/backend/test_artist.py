"""ytm_artist: artist pages from get_artist plus the raw rows, and artistAlbums (S1).

Fixtures are anonymous recordings of Radiohead's page: ``artist.json``
(get_artist), ``artist_raw_rows.json`` (the raw rows get_artist read, menus
trimmed) and ``artist_albums.json`` (the raw first page of its albums list,
the releases' menus trimmed).
"""

import copy
import json

import pytest
from ytmusicapi import YTMusic
from ytmusicapi.continuations import get_continuation_string

import ytm
import ytm_artist as artist
import ytm_raw as raw
from test_worker import album_item

CHANNEL = "UCr_iyUANcn9OX_yy9piYoLw"


@pytest.fixture
def data(load_fixture):
    return load_fixture("artist.json")


@pytest.fixture
def rows(load_fixture):
    return load_fixture("artist_raw_rows.json")["rows"]


def by_key(page):
    return {s["key"]: s for s in page["sections"] if s["key"]}


def browse(browse_id, params=None, page_type=None):
    endpoint = {"browseId": browse_id}
    if params:
        endpoint["params"] = params
    if page_type:
        endpoint["browseEndpointContextSupportedConfigs"] = {
            "browseEndpointContextMusicConfig": {"pageType": page_type}}
    return {"browseEndpoint": endpoint}


def carousel(title, items, endpoint=None):
    run = {"text": title}
    if endpoint:
        run["navigationEndpoint"] = endpoint
    return {"musicCarouselShelfRenderer": {
        "header": {"musicCarouselShelfBasicHeaderRenderer": {"title": {"runs": [run]}}},
        "contents": items}}


def retitled(rows):
    """A copy of the rows with every shelf title replaced (another language)."""
    out = copy.deepcopy(rows)
    for i, row in enumerate(out):
        key, data = raw.unwrap(row)
        if key == "musicCarouselShelfRenderer":
            title = next(iter(data["header"].values()))["title"]
        elif key == "musicShelfRenderer":
            title = data["title"]
        else:
            continue
        title["runs"] = [dict(run, text=f"Titel {i}") for run in title["runs"]]
    return out


# ---- the page --------------------------------------------------------------------------


def test_sections_follow_the_raw_rows(data, rows):
    page = artist.build(CHANNEL, data, rows)
    assert (page["type"], page["kind"], page["id"], page["title"], page["round"]) == (
        "page", "artist", CHANNEL, "Radiohead", True)
    got = [(s["title"], s["key"], len(s["items"])) for s in page["sections"]]
    assert got == [("Top songs", "songs", 5), ("Albums", "albums", 10), ("Singles & EPs", "singles", 10),
                   ("Videos", "videos", 10), ("Live performances", None, 10), ("Featured on", None, 10),
                   ("Playlists by Radiohead", None, 10), ("Fans might also like", "related", 10)]
    keyed = by_key(page)
    assert all(t["type"] == "track" for t in keyed["songs"]["items"])
    assert all(a["type"] == "album" and a["albumType"] == "Album" for a in keyed["albums"]["items"])
    assert all(a["albumType"] in ("Single", "EP") for a in keyed["singles"]["items"])
    assert all(t["type"] == "track" and t["kind"] == "video" for t in keyed["videos"]["items"])
    assert all(a["type"] == "artist" and a["channelId"].startswith("UC") for a in keyed["related"]["items"])
    # See all: the songs and videos playlists, the discography pages with their params.
    assert keyed["songs"]["more"]["browseId"].startswith("VL")
    assert keyed["videos"]["more"]["browseId"].startswith("VL")
    for key in ("albums", "singles"):
        more = keyed[key]["more"]
        assert more["browseId"] == "MPAD" + CHANNEL and more["params"]
    assert keyed["albums"]["more"]["params"] != keyed["singles"]["more"]["params"]
    assert keyed["related"]["more"] is None


def test_shelves_ytmusicapi_drops_come_from_the_raw_rows(data, rows):
    page = artist.build(CHANNEL, data, rows)
    generic = {s["title"]: s for s in page["sections"] if s["key"] is None}
    live, featured, playlists = (generic["Live performances"], generic["Featured on"],
                                 generic["Playlists by Radiohead"])
    assert all(t["type"] == "track" for t in live["items"])
    assert live["more"]["browseId"] == CHANNEL and live["more"]["params"]
    assert all(p["type"] == "playlist" for p in featured["items"]) and featured["more"] is None
    assert all(p["type"] == "playlist" for p in playlists["items"])
    assert playlists["more"]["browseId"] == CHANNEL and playlists["more"]["params"] != live["more"]["params"]


def test_titles_are_never_matched(data, rows):
    page = artist.build(CHANNEL, data, rows)
    other = artist.build(CHANNEL, data, retitled(rows))
    assert [s["key"] for s in other["sections"]] == [s["key"] for s in page["sections"]]
    assert [len(s["items"]) for s in other["sections"]] == [len(s["items"]) for s in page["sections"]]
    assert [s["more"] for s in other["sections"]] == [s["more"] for s in page["sections"]]
    assert [s["title"] for s in other["sections"]][4:7] == ["Titel 4", "Titel 5", "Titel 6"]
    # A language get_artist cannot read: no keys at all, every row still shown.
    header_only = {k: v for k, v in data.items() if k not in artist.KEYS}
    bare = artist.build(CHANNEL, header_only, retitled(rows))
    assert [s["key"] for s in bare["sections"]] == [None] * 8
    assert [len(s["items"]) for s in bare["sections"]] == [5, 10, 10, 10, 10, 10, 10, 10]


def test_a_broken_row_is_skipped_without_failing_the_page(data, rows):
    broken = [{"musicCarouselShelfRenderer": {"header": 5, "contents": []}}, {"somethingNew": {"x": 1}},
              {"musicCarouselShelfRenderer": {"header": {"musicCarouselShelfBasicHeaderRenderer": {
                  "title": {"runs": [{"text": "Empty"}]}}}, "contents": [{"oddRenderer": {}}]}}]
    page = artist.build(CHANNEL, data, rows[:2] + broken + rows[2:])
    assert [s["key"] for s in page["sections"]][:4] == ["songs", "albums", "singles", "videos"]
    assert len(page["sections"]) == 8


def test_without_raw_rows_the_keys_make_the_sections(data):
    page = artist.build(CHANNEL, data, None)
    assert [(s["key"], s["title"], len(s["items"])) for s in page["sections"]] == [
        ("songs", "Top songs", 5), ("albums", "Albums", 10), ("singles", "Singles & EPs", 10),
        ("videos", "Videos", 10), ("related", "Fans might also like", 10)]
    keyed = by_key(page)
    assert keyed["albums"]["more"]["params"] and keyed["songs"]["more"]["params"] is None
    assert all(a["albumType"] == "Album" for a in keyed["albums"]["items"])


def test_an_artist_without_params_gets_no_more_rows():
    songs = [{"videoId": "s1", "title": "S", "artists": [{"name": "N", "id": "UCn"}]}]
    albums = [{"browseId": "MPREb_1", "title": "A", "year": "2001"}]
    data = {"name": "N", "channelId": "UCsub",
            "songs": {"browseId": None, "results": songs},
            "albums": {"browseId": "UCn", "params": None, "results": albums},
            "singles": {"browseId": "MPADUCn", "params": None, "results": albums}}
    page = artist.build("UCn", data, None)
    assert [s["more"] for s in page["sections"]] == [None, None, None]
    # A raw shelf whose title links back to this artist's page opens nothing new.
    row = carousel("Albums", [album_item(1)], browse("UCn"))
    assert artist.build("UCn", {"name": "N"}, [row])["sections"][0]["more"] is None
    row = carousel("Albums", [album_item(1)], browse("MPLAUCsub"))
    assert artist.build("UCn", {"name": "N", "channelId": "UCsub"}, [row])["sections"][0]["more"] is None


def test_from_your_library_is_a_section_with_its_own_see_all(data, rows):
    library = carousel("From your library", [album_item(7)], browse("MPLA" + CHANNEL, "ggMCCAI%3D"))
    for row in (library, retitled([library])[0]):
        page = artist.build(CHANNEL, data, rows[:5] + [row] + rows[5:])
        section = page["sections"][5]
        assert section["key"] is None and [a["browseId"] for a in section["items"]] == ["MPREb_7"]
        assert section["more"] == {"browseId": "MPLA" + CHANNEL, "params": "ggMCCAI%3D", "pageType": None}


def test_header_keeps_both_channel_ids_and_the_mix_ids(data, rows):
    header = artist.build(CHANNEL, data, rows)["header"]
    assert header["channelId"] == CHANNEL and header["subscribeId"] == data["channelId"]
    assert header["subscribeId"] != CHANNEL, "get_artist names another channel for subscribing"
    assert header["shuffleId"].startswith("RDAO") and header["radioId"].startswith("RDEM")
    assert header["songsPlaylistId"].startswith("VL") and header["subscribed"] is False
    assert header["monthlyListeners"] and header["subscribers"] and header["views"]
    assert header["description"] and header["descriptionRuns"]
    assert any(run.get("url", "").startswith("https://") for run in header["descriptionRuns"])


def test_description_links_lose_youtubes_redirect_wrapper():
    runs = artist._description_runs([
        {"text": "site", "url": "https://www.youtube.com/redirect?event=x&redir_token=SECRET&q=https%3A%2F%2Fa.org%2F"},
        {"text": "bad", "url": "javascript:alert(1)"}, {"text": "plain"}, {"url": "https://x.org"}])
    assert runs == [{"text": "site", "url": "https://a.org/"}, {"text": "bad"}, {"text": "plain"}]


# ---- the method ------------------------------------------------------------------------


class ArtistYT:
    """get_artist that sends one browse request (so the capture sees it) and
    parses it with ytmusicapi's own code, or returns ``parsed``."""

    def __init__(self, response, parsed=None):
        self.response = response
        self.parsed = parsed
        self.parser = YTMusic().parser
        self.calls = []
        raw.install(self)

    def _send_request(self, endpoint, body, additionalParams=""):
        self.calls.append((endpoint, dict(body)))
        return self.response

    def get_artist(self, channelId):
        if self.parsed is not None:
            self._send_request("browse", {"browseId": channelId})
            return self.parsed
        return YTMusic.get_artist(self, channelId)


HEADER = {"musicImmersiveHeaderRenderer": {
    "title": {"runs": [{"text": "Radiohead"}]},
    "subscriptionButton": {"subscribeButtonRenderer": {
        "channelId": "UCsubscribe", "subscribed": True, "subscriberCountText": {"runs": [{"text": "5.46M"}]}}},
    "playButton": {"buttonRenderer": {"navigationEndpoint": {"watchEndpoint": {"playlistId": "RDAOk8x"}}}},
    "monthlyListenerCount": {"runs": [{"text": "205M monthly audience"}]},
}}


def response_of(rows):
    return dict(ytm_single_column(rows), header=HEADER)


def ytm_single_column(rows):
    return {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
        {"tabRenderer": {"content": {"sectionListRenderer": {"contents": rows}}}}]}}}


def test_artist_method_reads_one_response_and_its_rows(data, rows):
    yt = ArtistYT(response_of(rows), parsed=data)
    reply = ytm.Worker(yt, "none").handle({"id": 1, "method": "artist", "params": {"channelId": CHANNEL}})
    assert reply["ok"], reply
    assert yt.calls == [("browse", {"browseId": CHANNEL})]
    assert len(reply["result"]["sections"]) == 8 and reply["result"]["header"]["radioId"]


def test_a_page_get_artist_cannot_read_is_read_again_from_the_same_response(rows):
    # Signed in, Radiohead's page opened with a carousel without a header; get_artist
    # fails on it (KeyError) and would only find Top songs as the first row.
    video = {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": "Radiohead TV"}]},
        "subtitle": {"runs": [{"text": "Radiohead"}]},
        "navigationEndpoint": {"watchEndpoint": {"videoId": "d6gatp9XyGc", "watchEndpointMusicSupportedConfigs": {
            "watchEndpointMusicConfig": {"musicVideoType": "MUSIC_VIDEO_TYPE_UGC"}}}},
        "thumbnailRenderer": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": []}}}}}
    spotlight = {"musicCarouselShelfRenderer": {"contents": [video], "itemSize": "COLLECTION_STYLE_ITEM_SIZE_MEDIUM"}}
    yt = ArtistYT(response_of([spotlight] + rows))
    with pytest.raises(KeyError):
        yt.get_artist(CHANNEL)
    yt.calls.clear()
    reply = ytm.Worker(yt, "none").handle({"id": 1, "method": "artist", "params": {"channelId": CHANNEL}})
    assert reply["ok"], reply
    assert len(yt.calls) == 1, "one request: the second parse reads the captured response"
    page = reply["result"]
    first = page["sections"][0]
    assert first["title"] is None and first["key"] is None
    assert [(t["title"], t["kind"]) for t in first["items"]] == [("Radiohead TV", "video")]
    assert [s["key"] for s in page["sections"][1:]] == [
        "songs", "albums", "singles", "videos", None, None, None, "related"]
    header = page["header"]
    assert (header["subscribeId"], header["subscribed"], header["subscribers"]) == ("UCsubscribe", True, "5.46M")
    assert (header["shuffleId"], header["radioId"], header["monthlyListeners"]) == ("RDAOk8x", "RDEMk8x", "205M")
    assert header["songsPlaylistId"].startswith("VLOLAK5uy_")


def test_a_page_whose_top_songs_are_not_first_is_read_again(data, rows):
    # get_artist parses this page without an error but finds Top songs only
    # in the first row; the captured response is parsed again with it first.
    moved = [rows[1], rows[0]] + rows[2:]
    yt = ArtistYT(response_of(moved))
    assert yt.get_artist(CHANNEL)["songs"] == {"browseId": None}, "get_artist alone misses the songs"
    yt.calls.clear()
    reply = ytm.Worker(yt, "none").handle({"id": 1, "method": "artist", "params": {"channelId": CHANNEL}})
    assert reply["ok"], reply
    assert len(yt.calls) == 1, "one request: the second parse reads the captured response"
    page = reply["result"]
    assert [s["key"] for s in page["sections"]][:3] == ["albums", "songs", "singles"], "served order kept"
    songs = by_key(page)["songs"]
    assert len(songs["items"]) == 5 and all(t["type"] == "track" for t in songs["items"])
    assert songs["more"]["browseId"].startswith("VL")
    assert page["header"]["songsPlaylistId"] == songs["more"]["browseId"]
    assert page["header"]["songsPlaylistId"].startswith("VLOLAK5uy_")
    assert artist.songs(page) == songs["items"]


def test_artist_registry():
    assert ytm.METHODS["artist"] is artist.m_artist and ytm.METHODS["artistAlbums"] is artist.m_artist_albums
    assert {"artist", "artistAlbums"} <= ytm.CACHEABLE
    assert ytm.LANES["artistAlbums"] == "pages" and "artist" not in ytm.LANES
    assert {"artist", "artistRaw", "artistAlbums"} <= set(ytm.RECORDERS)
    assert "artist" not in ytm.CORE_METHODS


def test_raw_recorder_keeps_only_the_rows_without_menus(rows):
    yt = ArtistYT(response_of([{"musicShelfRenderer": {"menu": {"x": 1}, "contents": [], "title": {"runs": [
        {"text": "T", "clickTrackingParams": "c"}]}}}]), parsed={})
    out = artist.record_artist_raw(yt, {"channelId": CHANNEL})
    assert out == {"rows": [{"musicShelfRenderer": {"contents": [], "title": {"runs": [{"text": "T"}]}}}]}
    assert '"menu":' not in json.dumps(rows)


# ---- artistAlbums ----------------------------------------------------------------------


class AlbumsYT:
    """Answers browse requests from a queue and records each one."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        raw.install(self)

    def _send_request(self, endpoint, body, additionalParams=""):
        self.requests.append((endpoint, dict(body), additionalParams))
        return self.responses.pop(0)


MPAD = "MPAD" + CHANNEL
BODY = {"browseId": MPAD, "params": "p"}


def albums_call(yt, worker=None, **params):
    worker = worker or ytm.Worker(yt, "none")
    return worker.handle({"id": 2, "method": "artistAlbums", "params": params})


def grid_page(items, token=None, header=None, sort=None):
    grid = {"items": items}
    if token:
        grid["continuations"] = [{"nextContinuationData": {"continuation": token}}]
    response = ytm_single_column([{"gridRenderer": grid}])
    if header:
        response["header"] = {"musicHeaderRenderer": {"title": {"runs": [{"text": header}]}}}
    if sort is not None:
        add_sort_menu(response, sort)
    return response


def grid_continuation(items, token=None):
    grid = {"items": items}
    if token:
        grid["continuations"] = [{"nextContinuationData": {"continuation": token}}]
    return {"continuationContents": {"gridContinuation": grid}}


def add_sort_menu(response, selected):
    from test_pages import sort_header

    response["contents"]["singleColumnBrowseResultsRenderer"]["tabs"][0]["tabRenderer"]["content"][
        "sectionListRenderer"]["header"] = sort_header("R0", "R1", "R2")
    response["frameworkUpdates"] = {"entityBatchUpdate": {"mutations": [
        {"entityKey": f"key{i}", "payload": {"musicFormBooleanChoice": {"selected": i == selected}}}
        for i in range(3)]}}
    return response


def resorted(items, token=None):
    grid = grid_continuation(items, token)["continuationContents"]["gridContinuation"]
    return {"continuationContents": {"sectionListContinuation": {"contents": [{"gridRenderer": grid}]}}}


def message_page(text="Something went wrong"):
    # As served for a list YouTube cannot show (Bach's albums, signed in or out).
    return ytm_single_column([{"itemSectionRenderer": {"contents": [{"messageRenderer": {
        "subtext": {"messageSubtextRenderer": {"text": {"runs": [{"text": text}]}}}}}]}}])


def test_artist_albums_reads_the_served_list(load_fixture):
    yt = AlbumsYT(load_fixture("artist_albums.json"))
    reply = albums_call(yt, browseId=MPAD, params="p")
    assert reply["ok"], reply
    assert yt.requests == [("browse", BODY, "")]
    page = reply["result"]
    items = page["sections"][0]["items"]
    assert (page["kind"], page["id"], page["title"], page["subtitle"], len(items)) == (
        "artistAlbums", MPAD, "", "Radiohead", 15)
    assert page["sections"][0]["layout"] == "grid" and page["sections"][0]["title"] is None
    assert all(a["type"] == "album" and a["albumType"] == "Album" and a["year"] for a in items)
    assert all(a["artists"] == [{"name": "Radiohead", "id": CHANNEL}] for a in items), "the page artist"
    assert all(a["artistText"] == "Radiohead" for a in items)
    assert [o["title"] for o in page["orders"]] == ["Default", "Recency", "Popularity", "Alphabetical order"]
    assert all(o["index"] == o["title"] for o in page["orders"])
    assert page["order"] == "Default" and [o["selected"] for o in page["orders"]] == [True, False, False, False]
    assert page["cursor"] is None and page["hasMore"] is False


def test_a_long_list_is_read_a_page_at_a_time(tmp_path):
    # Bach's singles: over 3000 releases, about 100 a request. get_artist_albums
    # (limit None) read them all in one call; Load more reads them now.
    worker = ytm.Worker(AlbumsYT(), "none", cache=ytm.caches.DiskCache(tmp_path))
    yt = worker.yt
    yt.responses = [grid_page([album_item(i) for i in range(100)], "G1", header="Johann Sebastian Bach")]
    reply = albums_call(yt, worker, browseId=MPAD, params="p", cache="refresh")
    page = reply["result"]
    assert reply["ok"] and len(yt.requests) == 1, reply
    assert len(page["sections"][0]["items"]) == 100 and page["cursor"] == "c.G1" and page["hasMore"] is True
    # Load more: one request per page, the releases still credited (the
    # continuation has no header; the first page's disk copy names the artist).
    yt.requests.clear()
    yt.responses = [grid_continuation([album_item(i) for i in range(100, 200)], "G2")]
    reply = albums_call(yt, worker, browseId=MPAD, params="p", cursor="c.G1")
    assert reply["ok"], reply
    assert yt.requests == [("browse", BODY, get_continuation_string("G1"))]
    more = reply["result"]
    assert [a["browseId"] for a in more["sections"][0]["items"]] == [f"MPREb_{i}" for i in range(100, 200)]
    assert more["sections"][0]["items"][0]["artists"] == [{"name": "Johann Sebastian Bach", "id": CHANNEL}]
    assert more["cursor"] == "c.G2" and more["sections"][0]["title"] is None
    # The last page ends the list.
    yt.responses = [grid_continuation([album_item(200)])]
    last = albums_call(yt, worker, browseId=MPAD, params="p", cursor="c.G2")["result"]
    assert len(last["sections"][0]["items"]) == 1 and last["cursor"] is None and last["hasMore"] is False
    # A short first page reads on (at most three requests a call).
    yt.requests.clear()
    yt.responses = [grid_page([album_item(i) for i in range(10)], "S1"),
                    grid_continuation([album_item(i) for i in range(10, 20)], "S2"),
                    grid_continuation([album_item(i) for i in range(20, 30)], "S3")]
    page = albums_call(yt, worker, browseId=MPAD, params="q")["result"]
    assert len(yt.requests) == 3 and len(page["sections"][0]["items"]) == 30 and page["cursor"] == "c.S3"


def test_artist_albums_takes_the_artist_from_the_page_header():
    yt = AlbumsYT(grid_page([album_item(1)], header="Aphex Twin"))
    page = albums_call(yt, browseId="MPADUCa", params="p")["result"]
    assert page["subtitle"] == "Aphex Twin"
    assert page["sections"][0]["items"][0]["artists"] == [{"name": "Aphex Twin", "id": "UCa"}]
    # `artist` names it when given; a release that names its artists keeps them.
    credited = album_item(2)
    credited["musicTwoRowItemRenderer"]["subtitle"] = {"runs": [
        {"text": "Album"}, {"text": " • "}, {"text": "Other", "navigationEndpoint": {"browseEndpoint": {
            "browseId": "UCo", "browseEndpointContextSupportedConfigs": {"browseEndpointContextMusicConfig": {
                "pageType": "MUSIC_PAGE_TYPE_ARTIST"}}}}}]}
    yt = AlbumsYT(grid_page([album_item(1), credited], header="Aphex Twin"))
    page = albums_call(yt, browseId="MPADUCa", params="p", artist="AFX")["result"]
    assert page["subtitle"] == "AFX"
    assert [a["artistText"] for a in page["sections"][0]["items"]] == ["AFX", "Other"]


def test_artist_albums_sorts_by_the_served_title_ignoring_case():
    yt = AlbumsYT(grid_page([album_item(1)], sort=0))
    page = albums_call(yt, browseId=MPAD, params="p")["result"]
    assert page["orders"] == [{"index": "Order 0", "title": "Order 0", "selected": True},
                              {"index": "Order 1", "title": "Order 1", "selected": False},
                              {"index": "Order 2", "title": "Order 2", "selected": False}]
    assert page["order"] == "Order 0"
    # The chosen option's reload token re-sorts the first page (two requests);
    # the reply names the served title, whatever the case sent.
    for sent in ("Order 2", "order 2", "ORDER 2"):
        yt = AlbumsYT(grid_page([album_item(1)], sort=0), resorted([album_item(9), album_item(8)]))
        reply = albums_call(yt, browseId=MPAD, params="p", order=sent)
        assert reply["ok"], reply
        assert yt.requests == [("browse", BODY, ""), ("browse", BODY, get_continuation_string("R2"))]
        page = reply["result"]
        assert page["order"] == "Order 2" and [o["selected"] for o in page["orders"]] == [False, False, True]
        assert [a["browseId"] for a in page["sections"][0]["items"]] == ["MPREb_9", "MPREb_8"]
        assert page["cursor"] is None


def test_artist_albums_bad_orders_are_bad_requests():
    yt = AlbumsYT(grid_page([album_item(1)], sort=0))
    reply = albums_call(yt, browseId=MPAD, params="p", order="Nope")
    assert reply["error"]["code"] == "BAD_REQUEST" and reply["error"]["message"] == "This list cannot be sorted"
    assert len(yt.requests) == 1, "no re-sort request for an order the page does not offer"
    # A list served without a sort menu cannot be sorted.
    reply = albums_call(AlbumsYT(grid_page([album_item(1)])), browseId=MPAD, params="p", order="Recency")
    assert reply["error"]["code"] == "BAD_REQUEST"
    for params in ({"params": "p"}, {"browseId": MPAD}, {"browseId": MPAD, "params": "p", "order": 1},
                   {"browseId": MPAD, "params": "p", "cursor": 5}):
        assert albums_call(AlbumsYT(), **params)["error"]["code"] == "BAD_REQUEST"


def test_a_served_message_is_an_empty_page():
    for order in (None, "Recency"):
        yt = AlbumsYT(message_page())
        reply = albums_call(yt, browseId=MPAD, params="p", order=order)
        assert reply["ok"] and len(yt.requests) == 1, reply
        page = reply["result"]
        assert page["sections"] == [] and page["message"] == "Something went wrong"
        assert page["orders"] == [] and page["order"] is None and page["cursor"] is None
    # Anything else unknown is an upstream change.
    yt = AlbumsYT(ytm_single_column([{"somethingNew": {}}]))
    assert albums_call(yt, browseId=MPAD, params="p")["error"]["code"] == "UPSTREAM"


def test_artist_albums_cache_key_includes_params_and_order(tmp_path):
    cache = ytm.caches.DiskCache(tmp_path)
    yt = AlbumsYT()
    worker = ytm.Worker(yt, "none", cache=cache)
    for params in ({"params": "albums"}, {"params": "singles"}, {"params": "albums", "order": "Order 1"}):
        yt.responses = [grid_page([album_item(1)], sort=0), resorted([album_item(1)])]
        reply = worker.handle({"id": 1, "method": "artistAlbums",
                               "params": dict(params, browseId="MPADUCa", cache="refresh")})
        assert reply["ok"], reply
    assert cache.get("artistAlbums", {"browseId": "MPADUCa", "params": "albums"}) is not None
    assert cache.get("artistAlbums", {"browseId": "MPADUCa", "params": "singles"}) is not None
    hit = cache.get("artistAlbums", {"browseId": "MPADUCa", "params": "albums", "order": "Order 1"})
    assert hit is not None and hit[0]["order"] == "Order 1"


def test_artist_albums_recorder_keeps_the_sort_menu():
    import ytm_pages as pages

    release = album_item(1)
    release["musicTwoRowItemRenderer"]["menu"] = {"menuRenderer": {"items": []}}
    first = grid_page([release], header="Radiohead", sort=0)

    class RecorderYT(AlbumsYT):
        def get_artist(self, channelId):
            return {"albums": {"browseId": MPAD, "params": "p"}}

    yt = RecorderYT(first)
    out = artist.record_artist_albums(yt, {"channelId": CHANNEL})
    assert yt.requests == [("browse", BODY, "")]
    assert [o["title"] for o in pages.sort_options(out)] == ["Order 0", "Order 1", "Order 2"]
    assert '"menu"' not in json.dumps(raw.rows(out)), "the releases lose their menus"
