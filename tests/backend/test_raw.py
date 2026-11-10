"""ytm_raw: capture, request counting, row and item parsing, menu tokens."""

import threading

import requests

import ytm
import ytm_auth as auth
import ytm_raw as raw


class Client:
    """Just enough of YTMusic for install(): _send_request and a requests session."""

    def __init__(self):
        self._session = requests.Session()
        self.sent = []

    def _send_request(self, endpoint, body, additionalParams=""):
        self.sent.append(endpoint)
        return {"endpoint": endpoint, "n": len(self.sent)}


def test_capture_records_only_inside_the_block_and_on_its_thread():
    yt = raw.install(Client())
    yt._send_request("browse", {})
    with raw.capture(("browse", "next")) as cap:
        yt._send_request("browse", {})
        yt._send_request("player", {})
        other = threading.Thread(target=yt._send_request, args=("next", {}))
        other.start()
        other.join()
        yt._send_request("next", {})
    yt._send_request("browse", {})
    assert [r["n"] for r in cap.all()] == [2, 5], "not before or after the block, not player, not other threads"
    assert cap.first("next")["n"] == 5 and cap.first("search") is None


def test_nested_captures_both_record():
    yt = raw.install(Client())
    with raw.capture(("browse",)) as outer:
        with raw.capture(("browse",)) as inner:
            yt._send_request("browse", {})
        yt._send_request("browse", {})
    assert len(outer.responses) == 2 and len(inner.responses) == 1


def test_install_is_idempotent_and_counts_http_per_thread():
    yt = Client()
    raw.install(yt)
    wrapped = yt._send_request
    raw.install(yt)
    assert yt._send_request is wrapped, "a second install wraps nothing"
    assert yt._session.hooks["response"].count(raw._count) == 1
    raw.reset_http()
    for _ in range(3):
        raw._count(None)
    seen = []
    other = threading.Thread(target=lambda: seen.append(raw.http_count()))
    other.start()
    other.join()
    assert raw.http_count() == 3 and seen == [0]


def test_install_runs_again_for_every_new_client(monkeypatch):
    made = []

    def load_client():
        made.append(Client())
        return made[-1], "none"

    monkeypatch.setattr(auth, "load_client", load_client)
    worker = ytm.Worker()
    first = worker.yt
    worker.load()
    assert worker.yt is not first and worker.generation == 2
    for client in made:
        assert vars(client).get("_ytm_raw_installed")
        assert client._session.hooks["response"].count(raw._count) == 1


# ---- rows and items ----------------------------------------------------------------


def browse(browse_id, page_type=None, params=None):
    endpoint = {"browseId": browse_id}
    if params:
        endpoint["params"] = params
    if page_type:
        endpoint["browseEndpointContextSupportedConfigs"] = {
            "browseEndpointContextMusicConfig": {"pageType": f"MUSIC_PAGE_TYPE_{page_type}"}}
    return {"browseEndpoint": endpoint}


THUMBS = {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
    {"url": "https://lh3.googleusercontent.com/x=w226-h226-l90-rj", "width": 226, "height": 226}]}}}


def two_row(title, endpoint, subtitle_runs=None):
    return {"musicTwoRowItemRenderer": {
        "title": {"runs": [{"text": title, "navigationEndpoint": endpoint}]},
        "subtitle": {"runs": subtitle_runs or [{"text": "Album"}]},
        "navigationEndpoint": endpoint,
        "thumbnailRenderer": THUMBS,
    }}


def carousel(title, contents, more=None):
    header = {"title": {"runs": [{"text": title}]}}
    if more:
        header["moreContentButton"] = {"buttonRenderer": {"navigationEndpoint": more}}
    row = {"header": {"musicCarouselShelfBasicHeaderRenderer": header}}
    if contents is not None:
        row["contents"] = contents
    return {"musicCarouselShelfRenderer": row}


def test_parse_row_keeps_alignment_when_a_row_lacks_contents():
    rows = [
        carousel("Albums", [two_row("OK Computer", browse("MPREb_1", "ALBUM"))],
                 more=browse("MPADUC1", "ARTIST_DISCOGRAPHY", "p1")),
        carousel("Featured on", None),
        {"musicDescriptionShelfRenderer": {"header": {"runs": [{"text": "About"}]}}},
        {"somethingNewRenderer": {}},
        carousel("Quick picks", [], more={"watchEndpoint": {"videoId": "v1", "playlistId": "RDv1", "params": "pp"}}),
    ]
    parsed = [raw.parse_row(row) for row in rows]
    assert len(parsed) == len(rows)
    assert parsed[0]["title"] == "Albums" and parsed[0]["layout"] == "carousel"
    assert parsed[0]["items"][0]["type"] == "album" and parsed[0]["items"][0]["browseId"] == "MPREb_1"
    assert parsed[0]["more"] == {"browseId": "MPADUC1", "params": "p1",
                                 "pageType": "MUSIC_PAGE_TYPE_ARTIST_DISCOGRAPHY"}
    assert parsed[1] == {"title": "Featured on", "items": [], "more": None, "play": None, "layout": "carousel"}
    assert parsed[2]["title"] == "About" and parsed[2]["layout"] == "description"
    assert parsed[3]["layout"] is None and parsed[3]["items"] == []
    assert parsed[4]["play"] == {"videoId": "v1", "playlistId": "RDv1", "params": "pp"} and parsed[4]["more"] is None


def test_item_maps_page_types():
    profile = raw.item(two_row("Some listener", browse("UCuser", "USER_CHANNEL"), [{"text": "12 subscribers"}]))
    assert profile["type"] == "profile" and profile["channelId"] == "UCuser"
    artist = raw.item(two_row("Radiohead", browse("MPLAUCr_iyUANcn9OX_yy9piYoLw", "LIBRARY_ARTIST"),
                              [{"text": "1M subscribers"}]))
    assert artist["type"] == "artist" and artist["channelId"] == "UCr_iyUANcn9OX_yy9piYoLw"
    playlist = raw.item(two_row("Mix", browse("VLPLabc", "PLAYLIST"), [{"text": "Playlist"}]))
    assert playlist["type"] == "playlist" and playlist["playlistId"] == "PLabc"
    podcast = raw.item(two_row("Song Exploder", browse("MPSPPLx", "PODCAST_SHOW_DETAIL_PAGE"),
                               [{"text": "Hrishikesh", "navigationEndpoint": browse("UCh")}]))
    assert podcast["type"] == "podcast" and podcast["podcastId"] == "PLx"
    assert raw.item(two_row("Discography", browse("MPADUC1", "ARTIST_DISCOGRAPHY"))) is None
    assert raw.item(two_row("Unknown", browse("FEsomething", "SOMETHING_NEW"))) is None
    assert raw.item({"musicNavigationButtonRenderer": {}}) is None


def test_item_parses_songs_and_mixes():
    song = two_row("Karma Police", {"watchEndpoint": {"videoId": "nbCOAPR33ME", "watchEndpointMusicSupportedConfigs": {
        "watchEndpointMusicConfig": {"musicVideoType": "MUSIC_VIDEO_TYPE_OMV"}}}},
        [{"text": "Radiohead", "navigationEndpoint": browse("UCr", "ARTIST")}])
    song["musicTwoRowItemRenderer"]["title"]["runs"][0].pop("navigationEndpoint")
    track = raw.item(song)
    assert track["type"] == "track" and track["videoId"] == "nbCOAPR33ME" and track["kind"] == "video"
    assert track["artists"] == [{"name": "Radiohead", "id": "UCr"}]
    mix = two_row("My Mix", {"watchPlaylistEndpoint": {"playlistId": "RDTMAK5uy"}})
    mix["musicTwoRowItemRenderer"]["title"]["runs"][0].pop("navigationEndpoint")
    value = raw.item(mix)
    assert value["type"] == "playlist" and value["playlistId"] == "RDTMAK5uy"


def test_malformed_items_are_skipped_not_fatal():
    broken = {"musicTwoRowItemRenderer": {"navigationEndpoint": {"watchEndpoint": {"videoId": "x"}}}}
    good = two_row("OK Computer", browse("MPREb_1", "ALBUM"))
    assert [i["browseId"] for i in raw.items([broken, good])] == ["MPREb_1"]


def test_menu_token_picks_by_icon_not_text():
    menu = {"menuRenderer": {"items": [
        {"menuServiceItemRenderer": {"text": {"runs": [{"text": "Remove from history"}]},
                                     "icon": {"iconType": "BOOKMARK_BORDER"},
                                     "serviceEndpoint": {"feedbackEndpoint": {"feedbackToken": "SAVE"}}}},
        {"menuServiceItemRenderer": {"text": {"runs": [{"text": "Save to library"}]},
                                     "icon": {"iconType": "REMOVE_FROM_HISTORY"},
                                     "serviceEndpoint": {"feedbackEndpoint": {"feedbackToken": "FORGET"}}}},
        {"toggleMenuServiceItemRenderer": {"defaultIcon": {"iconType": "KEEP"},
                                           "defaultServiceEndpoint": {"feedbackEndpoint": {"feedbackToken": "PIN"}}}},
    ]}}
    assert raw.menu_token(menu, ("REMOVE_FROM_HISTORY",)) == "FORGET"
    assert raw.menu_token(menu, "KEEP") == "PIN"
    assert raw.menu_token({"menu": menu}, ["BOOKMARK_BORDER"]) == "SAVE"
    assert raw.menu_token(menu, ("NOT_INTERESTED",)) is None
    assert raw.menu_token(None, ("KEEP",)) is None


def test_rows_reads_the_section_list_of_any_response_shape():
    row = carousel("Albums", [])
    single = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
        {"tabRenderer": {"content": {"sectionListRenderer": {"contents": [row]}}}}]}}}
    resorted = {"continuationContents": {"sectionListContinuation": {"contents": [row, row]}}}
    two = {"contents": {"twoColumnBrowseResultsRenderer": {"secondaryContents": {
        "sectionListRenderer": {"contents": [row]}}}}}
    assert raw.rows(single) == [row] and len(raw.rows(resorted)) == 2 and raw.rows(two) == [row]
    assert raw.rows({}) == [] and raw.rows(None) == []
    wrapped = {"itemSectionRenderer": {"contents": [{"messageRenderer": {"text": {"runs": [{"text": "x"}]}}}]}}
    assert raw.unwrap(wrapped)[0] == "messageRenderer"
    import ytm_pages as pages

    assert pages.section_rows is raw.rows and pages.section_list is raw.section_list, "old names still work"


def episode_item(subtitle_runs):
    item = two_row("A new video", browse("MPEDnkPOxBnRSaM", "NON_MUSIC_AUDIO_TRACK_PAGE"), subtitle_runs)
    item["musicTwoRowItemRenderer"]["thumbnailOverlay"] = {"musicItemThumbnailOverlayRenderer": {"content": {
        "musicPlayButtonRenderer": {"playNavigationEndpoint": {"watchEndpoint": {"videoId": "nkPOxBnRSaM"}}}}}}
    return item


def test_two_row_episode_subtitle_gives_credit_length_and_date():
    # As served in New music videos: length, separator, the channel that posted it.
    value = raw.item(episode_item([{"text": "2 min 56 sec"}, {"text": " • "},
                                   {"text": "Zeyxo Music", "navigationEndpoint": browse("UCzeyxo", "ARTIST")}]))
    assert value["kind"] == "episode" and value["videoId"] == "nkPOxBnRSaM"
    assert value["artists"] == [{"name": "Zeyxo Music", "id": "UCzeyxo"}] and value["artistText"] == "Zeyxo Music"
    assert value["durationSec"] == 176 and value["date"] is None and value["podcast"] is None
    # A podcast episode: date, separator, its podcast.
    value = raw.item(episode_item([{"text": "Mar 3, 2025"}, {"text": " • "},
                                   {"text": "Song Exploder", "navigationEndpoint": browse("MPSPPLx",
                                                                                         "PODCAST_SHOW_DETAIL_PAGE")}]))
    assert value["date"] == "Mar 3, 2025" and value["durationSec"] is None
    assert value["podcast"] == {"name": "Song Exploder", "id": "MPSPPLx"} and value["artistText"] == "Song Exploder"
    assert raw.item(episode_item([{"text": "1 hr 2 min"}]))["durationSec"] == 3720
    assert raw.item(episode_item([{"text": "45 sec"}]))["durationSec"] == 45
