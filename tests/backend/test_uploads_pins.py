"""Uploads (S10) next to the pins and song tokens on every row (G2).

``raw.items`` now reads each row's Listen again pin by default and song
cards read their whole song menu; an uploaded song's menu serves neither
(the web's upload menus have no Save to library and no Pin), so an upload
listed outside the uploads views (a See all page, Related, a card) is kept
as kind ``upload`` with no tokens, and the uploads views' own reader
(``ytm_uploads.songs_of``) is untouched. Hand-built rows: uploads are
private."""

import copy

from ytmusicapi.navigation import MRLIR, MTRIR

import ytm_normalize as norm
import ytm_now as now
import ytm_raw as raw
import ytm_uploads as uploads
from test_uploads import ALBUM_ID, ARTIST_ID, upload_row
from test_worker import THUMBS

VIDEO_ID = "up000000001"
WATCH = {"watchEndpoint": {"videoId": VIDEO_ID, "watchEndpointMusicSupportedConfigs": {
    "watchEndpointMusicConfig": {"musicVideoType": norm.UPLOAD_VIDEO_TYPE}}}}
# The menu an uploaded song is served with: no library or Listen again toggle.
QUEUE_ONLY = {"menuRenderer": {"items": [{"menuServiceItemRenderer": {"serviceEndpoint": {
    "queueAddEndpoint": {"queueTarget": {"videoId": VIDEO_ID}}}}}]}}


def link(text, browse_id, page_type):
    return {"text": text, "navigationEndpoint": {"browseEndpoint": {
        "browseId": browse_id,
        "browseEndpointContextSupportedConfigs": {"browseEndpointContextMusicConfig": {"pageType": page_type}}}}}


def column(runs):
    return {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": runs}}}


def listed_row(menu=QUEUE_ONLY):
    """An uploaded song as a list row outside the uploads views (YouTube
    links uploaded artists with MUSIC_PAGE_TYPE_UNKNOWN)."""
    data = {"playlistItemData": {"videoId": VIDEO_ID}, "thumbnail": THUMBS,
            "overlay": {"musicItemThumbnailOverlayRenderer": {"content": {
                "musicPlayButtonRenderer": {"playNavigationEndpoint": WATCH}}}},
            "flexColumns": [column([{"text": "Song 1", "navigationEndpoint": WATCH}]),
                            column([link("Someone", ARTIST_ID, "MUSIC_PAGE_TYPE_UNKNOWN")]),
                            column([link("Demo", ALBUM_ID, "MUSIC_PAGE_TYPE_ALBUM")])],
            "fixedColumns": [{"musicResponsiveListItemFixedColumnRenderer": {"text": {"runs": [{"text": "3:05"}]}}}]}
    if menu is not None:
        data["menu"] = menu
    return {MRLIR: data}


def song_card(menu=QUEUE_ONLY):
    """An uploaded song as a two-row card (a watchEndpoint card)."""
    data = {"title": {"runs": [{"text": "Song 1", "navigationEndpoint": WATCH}]},
            "subtitle": {"runs": [link("Someone", ARTIST_ID, "MUSIC_PAGE_TYPE_UNKNOWN")]},
            "navigationEndpoint": WATCH, "thumbnailRenderer": THUMBS}
    if menu is not None:
        data["menu"] = menu
    return {MTRIR: data}


def no_tokens(item):
    return all(item.get(key) is None for key in ("libraryTokens", "listenAgainTokens"))


def test_an_upload_row_or_card_is_kept_as_an_upload_without_tokens():
    for content in (listed_row(), listed_row(menu=None), listed_row(menu={"menuRenderer": {"items": []}}),
                    song_card(), song_card(menu={"somethingElse": {}})):
        [item] = raw.items([content])
        assert (item["type"], item["kind"], item["videoId"]) == ("track", "upload", VIDEO_ID), content
        assert no_tokens(item), item
        # The default pin reader adds nothing to it: the same item as with no extra at all.
        assert raw.items([copy.deepcopy(content)], extra=lambda _c, value: value) == [item]
    [row] = raw.items([listed_row()])
    assert row["album"] == {"name": "Demo", "id": ALBUM_ID} and row["artists"][0]["id"] == ARTIST_ID
    assert "entityId" not in row, "no Delete outside the uploads views"


def test_the_uploads_views_reader_is_untouched_by_the_pins():
    songs = uploads.songs_of([upload_row(1), upload_row(2)])
    assert [(s["kind"], s["entityId"]) for s in songs] == [("upload", "t_po_1"), ("upload", "t_po_2")]
    assert all(no_tokens(s) for s in songs)


def test_now_context_current_of_an_upload_has_no_tokens():
    current = {"videoId": VIDEO_ID, "title": "Song 1", "videoType": norm.UPLOAD_VIDEO_TYPE,
               "artists": [{"name": "Someone", "id": ARTIST_ID}], "album": {"name": "Demo", "id": ALBUM_ID}}
    assert now.song_tokens(current) == {"inLibrary": None, "libraryTokens": None, "pinned": None, "listenAgainTokens": None}
