"""ytm_now (S11): nowContext, lyrics, related and credits.

Real ytmusicapi methods run on recorded (signed-out) responses: each test
client answers ``_send_request`` from a queue, so the requests a method
makes are counted exactly. Signed-in details (a like status, the song/video
twin) are added to copies of the fixtures by hand.
"""

import copy
import json

import pytest
import requests
from ytmusicapi import YTMusic
from ytmusicapi.exceptions import YTMusicServerError

import ytm
import ytm_now as now
import ytm_raw as raw

KARMA = "nbCOAPR33ME"
TWIN = "1uYWYWPc9HU"
LYRICS_ID = "MPLYt_b9Yfli6Cg79-6"
RELATED_ID = "MPTRt_b9Yfli6Cg79-6"


def client(*responses):
    """A real YTMusic whose requests are answered from ``responses`` in order
    (an exception is raised instead). Nothing reaches the network."""
    yt = YTMusic(language="en")
    queue = list(responses)
    yt.sent = []

    def send(endpoint, body, additionalParams=""):
        yt.sent.append((endpoint, dict(body), dict(yt.context["context"]["client"])))
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)

    yt._send_request = send
    return raw.install(yt)


def call(worker, method, **params):
    return worker.handle({"id": 11, "method": method, "params": params})


def panel(response):
    tabs = response["contents"]["singleColumnMusicWatchNextResultsRenderer"]["tabbedRenderer"][
        "watchNextTabbedResultsRenderer"]["tabs"]
    return tabs[0]["tabRenderer"]["content"]["musicQueueRenderer"]["content"]["playlistPanelRenderer"]


def signed_in_watch(load_fixture):
    """watch_raw as a signed-in account sees it: the song playing is liked
    and has a music video twin."""
    response = load_fixture("watch_raw.json")
    contents = panel(response)["contents"]
    song = contents[0]["playlistPanelVideoRenderer"]
    assert song["videoId"] == KARMA
    song["menu"]["menuRenderer"]["items"].append(
        {"toggleMenuServiceItemRenderer": {"defaultServiceEndpoint": {"likeEndpoint": {"status": "INDIFFERENT"}}}})
    video = copy.deepcopy(contents[1]["playlistPanelVideoRenderer"])
    video["videoId"] = TWIN
    video["title"] = {"runs": [{"text": "Karma Police (Official Video)"}]}
    video["navigationEndpoint"]["watchEndpoint"]["videoId"] = TWIN
    video["navigationEndpoint"]["watchEndpoint"]["watchEndpointMusicSupportedConfigs"] = {
        "watchEndpointMusicConfig": {"musicVideoType": "MUSIC_VIDEO_TYPE_OMV"}}
    contents[0] = {"playlistPanelVideoWrapperRenderer": {
        "primaryRenderer": contents[0], "counterpart": [{"counterpartRenderer": {"playlistPanelVideoRenderer": video}}]}}
    return response


# ---- nowContext ------------------------------------------------------------------------


def signed_in(yt):
    """A signed-in worker whose session was already checked (once per worker)."""
    worker = ytm.Worker(yt, "ok")
    worker.verified = True
    return worker


def test_now_context_reads_everything_from_one_next_request(load_fixture):
    response = signed_in_watch(load_fixture)
    # The recorder drops continuations: put one back, so a limit above the
    # first page would send a second 'next' (and find no answer for it).
    panel(response)["continuations"] = [{"nextRadioContinuationData": {"continuation": "x"}}]
    yt = client(response)
    reply = call(signed_in(yt), "nowContext", videoId=KARMA)
    assert reply["ok"], reply
    assert [s[0] for s in yt.sent] == ["next"], "one 'next' request, nothing else"
    assert yt.sent[0][1]["videoId"] == KARMA
    context = reply["result"]
    assert context["videoId"] == KARMA and context["playlistId"] == "RDAMVM" + KARMA
    assert context["likeStatus"] == "LIKE"
    assert context["lyricsId"] == LYRICS_ID and context["relatedId"] == RELATED_ID
    assert context["playingFrom"] == "Karma Police Mix"
    assert context["counterpart"] == {"videoId": TWIN, "title": "Karma Police (Official Video)", "kind": "video"}
    chips = context["chips"]
    assert chips[0]["title"] == "All" and chips[0]["selected"] is True
    assert [c["title"] for c in chips[1:4]] == ["Popular", "Discover", "Deep cuts"]
    assert all(c["playlistId"].startswith("RD") and c["params"] for c in chips)
    assert sum(c["selected"] for c in chips) == 1
    items = context["items"]
    assert KARMA not in [t["videoId"] for t in items] and len(items) == 5
    assert all(t["type"] == "track" and t["counterpart"] is None for t in items)
    assert "cursor" not in str(context).lower() and "token" not in str(context).lower()


def test_now_context_of_the_video_twin(load_fixture):
    yt = client(signed_in_watch(load_fixture))
    context = call(signed_in(yt), "nowContext", videoId=TWIN)["result"]
    assert context["counterpart"] == {"videoId": KARMA, "title": "Karma Police", "kind": "song"}
    # The song is the video playing, in its other form: not up next.
    assert KARMA not in [t["videoId"] for t in context["items"]] and len(context["items"]) == 5


def test_now_context_signed_out_has_no_like_status(load_fixture):
    yt = client(load_fixture("watch_raw.json"))
    context = call(ytm.Worker(yt, "none"), "nowContext", videoId=KARMA)["result"]
    assert context["likeStatus"] is None and context["counterpart"] is None
    assert context["lyricsId"] == LYRICS_ID and context["relatedId"] == RELATED_ID


def test_now_context_passes_the_playlist_and_is_never_cached(load_fixture):
    yt = client(load_fixture("watch_raw.json"), load_fixture("watch_raw.json"))
    worker = ytm.Worker(yt, "none")
    first = call(worker, "nowContext", videoId=KARMA, playlistId="RDAMVM" + KARMA, cache="prefer")
    second = call(worker, "nowContext", videoId=KARMA, playlistId="RDAMVM" + KARMA, cache="prefer")
    assert first["ok"] and second["ok"] and "cached" not in second
    assert len(yt.sent) == 2 and yt.sent[0][1]["playlistId"] == "RDAMVM" + KARMA
    assert call(worker, "nowContext")["error"]["code"] == "BAD_REQUEST"


def test_now_context_checks_the_session_first(load_fixture):
    # An expired session answers AUTH_EXPIRED once (the shell marks it and
    # tells the user); the worker then reads the context signed out.
    yt = client(load_fixture("watch_raw.json"))
    checks = []

    def expired():
        checks.append(1)
        raise YTMusicServerError("Server returned HTTP 401: Unauthorized.\nInvalid credentials")

    yt.get_account_info = expired
    worker = ytm.Worker(yt, "ok")
    reply = call(worker, "nowContext", videoId=KARMA)
    assert reply["error"]["code"] == "AUTH_EXPIRED" and worker.auth == "expired" and yt.sent == []
    again = call(worker, "nowContext", videoId=KARMA)
    assert again["ok"] and again["result"]["likeStatus"] is None
    assert [s[0] for s in yt.sent] == ["next"] and len(checks) == 1


def test_now_context_checks_a_good_session_once(load_fixture):
    yt = client(load_fixture("watch_raw.json"), load_fixture("watch_raw.json"))
    checks = []
    yt.get_account_info = lambda: checks.append(1) or {"accountName": "x"}
    worker = ytm.Worker(yt, "ok")
    assert call(worker, "nowContext", videoId=KARMA)["ok"] and call(worker, "nowContext", videoId=KARMA)["ok"]
    assert len(checks) == 1 and worker.auth == "ok" and len(yt.sent) == 2
    # Signed out, nothing is checked.
    anon = client(load_fixture("watch_raw.json"))
    anon.get_account_info = lambda: pytest.fail("no session to check")
    assert call(ytm.Worker(anon, "none"), "nowContext", videoId=KARMA)["ok"]


def test_like_status_is_a_thin_alias(load_fixture):
    yt = client(signed_in_watch(load_fixture))
    worker = ytm.Worker(yt, "ok")
    worker.verified = True  # the account check is the worker's, once per worker
    reply = call(worker, "likeStatus", videoId=KARMA)
    assert reply["result"] == {"videoId": KARMA, "likeStatus": "LIKE"}
    assert [s[0] for s in yt.sent] == ["next"]
    assert call(ytm.Worker(client(), "none"), "likeStatus", videoId=KARMA)["error"]["code"] == "AUTH_REQUIRED"


# ---- lyrics ----------------------------------------------------------------------------


def lyrics_clients(monkeypatch, anonymous, account):
    anon = client(*anonymous)
    monkeypatch.setattr(now, "anonymous_client", lambda: anon)
    return anon, client(*account)


def test_timed_lyrics_come_from_the_signed_out_client_in_seconds(monkeypatch, load_fixture):
    anon, yt = lyrics_clients(monkeypatch, [load_fixture("lyrics_timed.json")], [])
    reply = call(ytm.Worker(yt, "ok"), "lyrics", browseId=LYRICS_ID, timed=True)
    assert reply["ok"], reply
    lyrics = reply["result"]
    assert lyrics["source"] == "Source: Musixmatch"
    assert lyrics["lines"][:2] == [{"start": 0.0, "end": 26.7, "text": "♪"},
                                   {"start": 26.7, "end": 30.97, "text": "Karma police"}]
    assert lyrics["lines"][3] == {"start": 33.25, "end": 37.52, "text": "He talks in maths"}
    assert lyrics["text"].split("\n")[1:] == ["Karma police", "Arrest this man", "He talks in maths"]
    # The Android client context, on the signed-out client only.
    assert anon.sent[0][2]["clientName"] == "ANDROID_MUSIC" and yt.sent == []
    assert anon.context["context"]["client"]["clientName"] == "WEB_REMIX", "as_mobile() put the context back"


def test_timed_lyrics_fall_back_to_plain_when_the_signed_out_client_fails(monkeypatch, load_fixture):
    rejected = YTMusicServerError("Server returned HTTP 400: Bad Request.\nRequest contains an invalid argument.")
    anon, yt = lyrics_clients(monkeypatch, [rejected], [load_fixture("lyrics_plain.json")])
    reply = call(ytm.Worker(yt, "ok"), "lyrics", browseId=LYRICS_ID, timed=True)
    assert reply["ok"], reply
    assert reply["result"] == {"text": "Karma police\nArrest this man\nHe talks in maths",
                               "source": "Source: Musixmatch", "lines": None}
    assert len(anon.sent) == 1 and [s[0] for s in yt.sent] == ["browse"]
    assert yt.sent[0][2]["clientName"] == "WEB_REMIX"


def test_timed_lyrics_fall_back_when_there_are_no_timestamps(monkeypatch, load_fixture):
    # A song without synced lyrics: the mobile answer has only the plain text.
    anon, yt = lyrics_clients(monkeypatch, [load_fixture("lyrics_plain.json")], [load_fixture("lyrics_plain.json")])
    lyrics = call(ytm.Worker(yt, "ok"), "lyrics", browseId=LYRICS_ID, timed=True)["result"]
    assert lyrics["lines"] is None and lyrics["text"].startswith("Karma police")
    assert lyrics["source"] == "Source: Musixmatch", "read from the footer ytmusicapi misses"
    assert len(anon.sent) == 1 and len(yt.sent) == 1


def test_plain_lyrics_ask_the_account_only(monkeypatch, load_fixture):
    anon, yt = lyrics_clients(monkeypatch, [], [load_fixture("lyrics_plain.json")])
    lyrics = call(ytm.Worker(yt, "ok"), "lyrics", browseId=LYRICS_ID)["result"]
    assert lyrics["lines"] is None and lyrics["source"] == "Source: Musixmatch"
    assert anon.sent == [] and len(yt.sent) == 1


def test_no_lyrics_id_means_no_lyrics(monkeypatch):
    anon, yt = lyrics_clients(monkeypatch, [], [])
    worker = ytm.Worker(yt, "ok")
    for params in ({}, {"browseId": None}, {"browseId": ""}):
        reply = worker.handle({"id": 1, "method": "lyrics", "params": dict(params, timed=True)})
        assert reply["result"] == {"text": None, "source": None, "lines": None} and reply["http"] == 0
    assert anon.sent == [] and yt.sent == []
    assert call(worker, "lyrics", browseId="MPTRt_x")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "lyrics", browseId=LYRICS_ID, timed="yes")["error"]["code"] == "BAD_REQUEST"


def test_lyrics_are_cached_per_id_and_timing(monkeypatch, load_fixture):
    anon, yt = lyrics_clients(monkeypatch, [load_fixture("lyrics_timed.json")], [])
    worker = ytm.Worker(yt, "ok")
    first = call(worker, "lyrics", browseId=LYRICS_ID, timed=True, cache="prefer")
    again = call(worker, "lyrics", browseId=LYRICS_ID, timed=True, cache="prefer")
    assert first["cached"] is False and again["cached"] is True and again["result"] == first["result"]
    assert len(anon.sent) == 1


def test_lyrics_without_timestamps_are_an_answer_and_cached(monkeypatch, load_fixture):
    # The mobile answer arrived but its lines have no timing: ytmusicapi's
    # parse fails (KeyError 'cueRange'), and the plain lyrics are the answer.
    untimed = load_fixture("lyrics_timed.json")
    data = untimed["contents"]["elementRenderer"]["newElement"]["type"]["componentType"]["model"][
        "timedLyricsModel"]["lyricsData"]["timedLyricsData"]
    for line in data:
        del line["cueRange"]
    anon, yt = lyrics_clients(monkeypatch, [untimed], [load_fixture("lyrics_plain.json")])
    worker = ytm.Worker(yt, "ok")
    first = call(worker, "lyrics", browseId=LYRICS_ID, timed=True, cache="prefer")
    again = call(worker, "lyrics", browseId=LYRICS_ID, timed=True, cache="prefer")
    assert first["ok"] and first["result"]["lines"] is None and first["cached"] is False
    assert again["cached"] is True and len(anon.sent) == 1 and len(yt.sent) == 1


@pytest.mark.parametrize("failure", [
    YTMusicServerError("Server returned HTTP 429: Too Many Requests.\nQuota"),
    YTMusicServerError("Server returned HTTP 503: Service Unavailable.\nBackend"),
    YTMusicServerError("Server returned HTTP 400: Bad Request.\nRequest contains an invalid argument."),
    requests.exceptions.ConnectionError("down"),
    requests.exceptions.Timeout("slow"),
    json.JSONDecodeError("Expecting value", "<html>", 0),
], ids=["429", "503", "400", "offline", "timeout", "not-json"])
def test_a_failed_timed_request_is_not_cached(monkeypatch, load_fixture, failure):
    # The plain lyrics stand in once; the next request asks for synced ones again.
    anon, yt = lyrics_clients(monkeypatch, [failure, load_fixture("lyrics_timed.json")],
                              [load_fixture("lyrics_plain.json")])
    worker = ytm.Worker(yt, "ok")
    first = call(worker, "lyrics", browseId=LYRICS_ID, timed=True, cache="prefer")
    assert first["ok"] and first["cached"] is False and first["result"]["lines"] is None
    second = call(worker, "lyrics", browseId=LYRICS_ID, timed=True, cache="prefer")
    assert second["cached"] is False and len(second["result"]["lines"]) == 4
    assert len(anon.sent) == 2, "the second request reached the signed-out client again"
    third = call(worker, "lyrics", browseId=LYRICS_ID, timed=True, cache="prefer")
    assert third["cached"] is True and third["result"] == second["result"] and len(anon.sent) == 2


def test_a_failed_signed_out_client_is_not_cached(monkeypatch, load_fixture):
    def broken():
        raise requests.exceptions.ConnectionError("visitor id")

    monkeypatch.setattr(now, "anonymous_client", broken)
    yt = client(load_fixture("lyrics_plain.json"), load_fixture("lyrics_plain.json"))
    worker = ytm.Worker(yt, "ok")
    for _ in range(2):
        reply = call(worker, "lyrics", browseId=LYRICS_ID, timed=True, cache="prefer")
        assert reply["ok"] and reply["cached"] is False and reply["result"]["lines"] is None
    assert len(yt.sent) == 2
    # Only that request: the next cacheable answer on this thread is stored.
    monkeypatch.setattr(now, "anonymous_client", lambda: client(load_fixture("lyrics_timed.json")))
    assert call(worker, "lyrics", browseId=LYRICS_ID, timed=True, cache="prefer")["cached"] is False
    assert call(worker, "lyrics", browseId=LYRICS_ID, timed=True, cache="prefer")["cached"] is True


# ---- related ---------------------------------------------------------------------------


def test_related_is_a_page_of_sections_with_an_about_text(load_fixture):
    yt = client(load_fixture("related.json"))
    reply = call(ytm.Worker(yt, "none"), "related", browseId=RELATED_ID)
    assert reply["ok"], reply
    assert [s[0] for s in yt.sent] == ["browse"] and yt.sent[0][1]["browseId"] == RELATED_ID
    page = reply["result"]
    assert page["type"] == "page" and page["kind"] == "related" and page["id"] == RELATED_ID
    sections = page["sections"]
    assert [s["title"] for s in sections] == ["You might also like", "Recommended playlists", "Other performances",
                                              "Similar artists", "Radiohead", "About the artist"]
    assert [{i["type"] for i in s["items"]} for s in sections] == [
        {"track"}, {"playlist"}, {"track"}, {"artist"}, {"album"}, {"text"}]
    assert sections[4]["more"]["browseId"] == "UCr_iyUANcn9OX_yy9piYoLw"
    about = sections[5]
    assert about["layout"] == "description" and len(about["items"]) == 1
    assert about["items"][0]["title"].startswith("Radiohead are an English rock band")
    assert page["cursor"] is None and page["hasMore"] is False


def test_related_needs_its_id():
    assert call(ytm.Worker(client(), "none"), "related")["error"]["code"] == "BAD_REQUEST"


# ---- credits ---------------------------------------------------------------------------


def test_credits_one_section_per_role_artists_linked(load_fixture):
    yt = client(load_fixture("credits.json"))
    reply = call(ytm.Worker(yt, "none"), "credits", videoId=KARMA)
    assert reply["ok"], reply
    assert yt.sent[0][:2] == ("browse", {"browseId": "MPTC" + KARMA})
    page = reply["result"]
    assert page["kind"] == "credits" and page["id"] == "MPTC" + KARMA
    assert page["title"] == "Karma Police" and page["subtitle"] == "Song credits • Radiohead"
    assert page["thumb"]
    sections = page["sections"]
    assert [s["title"] for s in sections] == ["Performed by", "Written by", "Produced by",
                                              "Music metadata provided by"]
    performed = sections[0]["items"]
    # Radiohead has a page (linked in the song's header); the people have none.
    assert performed == [{"type": "artist", "channelId": "UCr_iyUANcn9OX_yy9piYoLw", "name": "Radiohead",
                          "subtitle": None, "thumb": None, "thumbLarge": None}]
    written = sections[1]["items"]
    assert [i["title"] for i in written] == ["Colin Greenwood", "Ed O'Brien", "Jonny Greenwood", "Phil Selway",
                                             "Thom Yorke"]
    assert all(i["type"] == "text" and i["icon"] for i in written)
    assert sections[2]["items"][-1]["title"] == "Nigel Godrich"


def test_credits_by_browse_id_and_bad_requests(load_fixture):
    yt = client(load_fixture("credits.json"))
    worker = ytm.Worker(yt, "none")
    assert call(worker, "credits", browseId="MPTC" + KARMA)["ok"]
    for params in ({}, {"videoId": KARMA, "browseId": "MPTC" + KARMA}, {"browseId": "MPLYt_x"}):
        assert worker.handle({"id": 1, "method": "credits", "params": params})["error"]["code"] == "BAD_REQUEST"
    assert len(yt.sent) == 1


def test_a_song_without_credits_is_a_message_page():
    yt = client({"responseContext": {}})
    page = call(ytm.Worker(yt, "none"), "credits", videoId=KARMA)["result"]
    assert page["sections"] == [] and page["message"] == "No credits for this song"


# ---- registry ----------------------------------------------------------------------------


def test_registry():
    assert ytm.METHODS["nowContext"] is now.m_now_context
    assert ytm.METHODS["likeStatus"] is now.m_like_status and ytm.METHODS["upNext"] is now.m_up_next
    assert {"lyrics", "related", "credits"} <= ytm.CACHEABLE and "nowContext" not in ytm.CACHEABLE
    assert all(ytm.LANES[m] == "pages" for m in ("nowContext", "lyrics", "related", "credits", "upNext", "queueFrom"))
    assert "likeStatus" not in ytm.LANES
    assert {"watchRaw", "queueRaw", "lyricsTimed", "lyricsPlain", "related", "credits"} <= set(ytm.RECORDERS)
    assert ytm.METHODS["queueFrom"] is now.m_queue_from and not {"upNext", "queueFrom"} & ytm.CACHEABLE
    assert not (set(now.METHODS) & ytm.MUTATES)


@pytest.mark.parametrize("name", ["watch_raw.json", "related.json", "credits.json", "lyrics_timed.json",
                                  "lyrics_plain.json", "queue_raw.json"])
def test_fixtures_hold_no_tokens(load_fixture, name):
    text = str(load_fixture(name)).lower()
    for part in ("token", "continuation", "cursor", "feedback", "onbehalfofuser", "clicktrackingparams"):
        assert part not in text, (name, part)


# ---- up next (S12) -------------------------------------------------------------------------
# upNext turns a finite context into its radio and passes a radio through;
# queueFrom is a tuner chip's queue. Both run on real ytmusicapi code over the
# anonymous watch_raw / queue_raw fixtures (no continuations: one request).

OLAK = "OLAK5uy_nc6aI2v4Wzz-QzHn2vjvJ1PJr3yP2vx4M"


def up_next(load_fixture, playlist_id=None, response=None, **extra):
    yt = client(response or load_fixture("watch_raw.json"))
    params = dict({"videoId": KARMA}, **extra)
    if playlist_id is not None:
        params["playlistId"] = playlist_id
    reply = call(ytm.Worker(yt, "none"), "upNext", **params)
    assert reply["ok"], reply
    return yt, reply["result"]


@pytest.mark.parametrize("context, radio", [
    ("PLIPfake", "RDAMPLPLIPfake"),
    (OLAK, "RDAMPL" + OLAK),
    ("VLPLIPfake", "RDAMPLPLIPfake"),
    ("LM", "RDAMPLLM"),
    ("VLLM", "RDAMPLLM"),
    ("RDCLAK5uy_curated", "RDAMPLRDCLAK5uy_curated"),
])
def test_up_next_plays_a_finite_context_on_as_its_radio(load_fixture, context, radio):
    yt, result = up_next(load_fixture, context)
    assert [s[0] for s in yt.sent] == ["next"]
    body = yt.sent[0][1]
    # Never the context's own window (only songs already queued); the song
    # goes with it (a list without a radio is then seeded by the song).
    assert body["playlistId"] == radio and body["params"] == "wAEB" and body["videoId"] == KARMA
    assert "watchEndpointMusicSupportedConfigs" not in body


@pytest.mark.parametrize("context", ["RDAMVM" + KARMA, "RDTMAKmix", "RDEMartist", "RDAMPL" + OLAK, "VLRDAMVM" + KARMA])
def test_up_next_passes_a_radio_context_as_it_is(load_fixture, context):
    yt, _ = up_next(load_fixture, context)
    body = yt.sent[0][1]
    assert body["playlistId"] == context.removeprefix("VL") and body["videoId"] == KARMA
    assert "params" not in body, "a radio goes on as it is, not re-seeded"


def test_up_next_without_a_context_is_the_songs_radio(load_fixture):
    yt, result = up_next(load_fixture)
    body = yt.sent[0][1]
    assert body["videoId"] == KARMA and body["playlistId"] == "RDAMVM" + KARMA and body["params"] == "wAEB"
    assert result["playlistId"] == "RDAMVM" + KARMA and result["playingFrom"] == "Karma Police Mix"
    items = result["items"]
    assert len(items) == 5 and KARMA not in [t["videoId"] for t in items]
    assert all(t["type"] == "track" and "counterpart" in t for t in items)
    assert "token" not in str(result).lower() and "feedback" not in str(result).lower()


def test_up_next_drops_the_song_and_its_twin_and_keeps_counterparts(load_fixture):
    # Signed in, entries come with their song/video twins: the twin of the
    # song playing is not up next, every other entry keeps its own.
    response = signed_in_watch(load_fixture)
    contents = panel(response)["contents"]
    other = copy.deepcopy(contents[2]["playlistPanelVideoRenderer"])
    other["videoId"] = "OtherTwin01"
    other["navigationEndpoint"]["watchEndpoint"]["videoId"] = "OtherTwin01"
    other["navigationEndpoint"]["watchEndpoint"]["watchEndpointMusicSupportedConfigs"] = {
        "watchEndpointMusicConfig": {"musicVideoType": "MUSIC_VIDEO_TYPE_OMV"}}
    contents[2] = {"playlistPanelVideoWrapperRenderer": {
        "primaryRenderer": contents[2], "counterpart": [{"counterpartRenderer": {"playlistPanelVideoRenderer": other}}]}}
    _, result = up_next(load_fixture, response=response)
    ids = [t["videoId"] for t in result["items"]]
    assert KARMA not in ids and TWIN not in ids
    twin = [t for t in result["items"] if t["counterpart"]]
    assert len(twin) == 1 and twin[0]["counterpart"]["videoId"] == "OtherTwin01"
    assert twin[0]["counterpart"]["kind"] == "video" and twin[0]["counterpart"]["title"]
    # Asked for the twin (the video plays), the song is not up next either.
    _, from_video = up_next(load_fixture, response=signed_in_watch(load_fixture), videoId=TWIN)
    assert KARMA not in [t["videoId"] for t in from_video["items"]]


def test_up_next_takes_youtubes_queue_for_a_list_without_a_radio(load_fixture):
    # Live, YouTube answers RDAMPL + an id it has no radio for (a podcast's
    # list, an unknown id) with HTTP 200 and a "Your Queue" (seeded by the
    # song sent with it), never "No content": one request, passed on as it is.
    response = load_fixture("watch_raw.json")
    queue = response["contents"]["singleColumnMusicWatchNextResultsRenderer"]["tabbedRenderer"][
        "watchNextTabbedResultsRenderer"]["tabs"][0]["tabRenderer"]["content"]["musicQueueRenderer"]
    queue.pop("subHeaderChipCloud")  # no chips either
    queue["header"]["musicQueueHeaderRenderer"]["subtitle"] = {"runs": [{"text": "Your Queue"}]}
    yt, result = up_next(load_fixture, "RDPNepisodes", response=response)
    assert len(yt.sent) == 1 and yt.sent[0][1]["playlistId"] == "RDAMPLRDPNepisodes"
    assert yt.sent[0][1]["videoId"] == KARMA
    assert result["playingFrom"] == "Your Queue" and len(result["items"]) == 5
    # An HTTP error is the worker's to report: no second request.
    yt = client(YTMusicServerError("Server returned HTTP 429: Too Many Requests."))
    reply = call(ytm.Worker(yt, "none"), "upNext", videoId=KARMA, playlistId=OLAK)
    assert reply["error"]["code"] == "RATE_LIMITED" and len(yt.sent) == 1


def test_up_next_limit_and_params(load_fixture):
    yt, _ = up_next(load_fixture, None, limit=100)
    assert len(yt.sent) == 1
    worker = ytm.Worker(client(), "none")
    assert call(worker, "upNext")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "upNext", videoId=KARMA, limit="25")["error"]["code"] == "BAD_REQUEST"


def test_queue_from_pins_the_web_body(load_fixture):
    recorded = load_fixture("queue_raw.json")
    chip = recorded.pop("chip")
    yt = client(recorded)
    reply = call(ytm.Worker(yt, "none"), "queueFrom", videoId=KARMA, playlistId=chip["playlistId"], params=chip["params"])
    assert reply["ok"], reply
    assert [s[0] for s in yt.sent] == ["next"]
    assert yt.sent[0][1] == {"videoId": KARMA, "playlistId": chip["playlistId"], "params": chip["params"],
                             "isAudioOnly": True, "enablePersistentPlaylistPanel": True}
    result = reply["result"]
    assert result["playlistId"] == chip["playlistId"] and result["playingFrom"].endswith(chip["title"])
    items = result["items"]
    assert len(items) == 5 and KARMA not in [t["videoId"] for t in items]
    assert all(t["type"] == "track" and t["title"] and "counterpart" in t for t in items)
    # Without params the body has none.
    yt = client(copy.deepcopy(recorded))
    assert call(ytm.Worker(yt, "none"), "queueFrom", videoId=KARMA, playlistId=chip["playlistId"])["ok"]
    assert "params" not in yt.sent[0][1] and yt.sent[0][1]["isAudioOnly"] is True


def test_queue_from_normalizes_counterparts_and_is_never_cached(load_fixture):
    recorded = load_fixture("queue_raw.json")
    chip = recorded.pop("chip")
    contents = panel(recorded)["contents"]
    video = copy.deepcopy(contents[1]["playlistPanelVideoRenderer"])
    video["videoId"] = "TwinVideo01"
    video["navigationEndpoint"]["watchEndpoint"]["watchEndpointMusicSupportedConfigs"] = {
        "watchEndpointMusicConfig": {"musicVideoType": "MUSIC_VIDEO_TYPE_OMV"}}
    contents[1] = {"playlistPanelVideoWrapperRenderer": {
        "primaryRenderer": contents[1], "counterpart": [{"counterpartRenderer": {"playlistPanelVideoRenderer": video}}]}}
    yt = client(recorded, recorded)
    worker = ytm.Worker(yt, "none")
    params = {"videoId": KARMA, "playlistId": chip["playlistId"], "params": chip["params"], "cache": "prefer"}
    first = worker.handle({"id": 1, "method": "queueFrom", "params": params})
    second = worker.handle({"id": 2, "method": "queueFrom", "params": params})
    assert first["ok"] and second["ok"] and "cached" not in second and len(yt.sent) == 2
    twins = [t["counterpart"] for t in first["result"]["items"] if t["counterpart"]]
    assert twins == [{"videoId": "TwinVideo01", "title": video["title"]["runs"][0]["text"], "kind": "video"}]


def test_queue_from_errors():
    worker = ytm.Worker(client({"contents": {}}), "none")
    assert call(worker, "queueFrom", videoId=KARMA, playlistId="RDATx")["error"]["code"] == "UPSTREAM"
    assert call(worker, "queueFrom", videoId=KARMA)["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "queueFrom", playlistId="RDATx")["error"]["code"] == "BAD_REQUEST"


def test_radio_names_what_plays(load_fixture):
    # Start radio's reply carries the queue header's name ("Karma Police Mix").
    yt = client(load_fixture("watch_raw.json"))
    result = call(ytm.Worker(yt, "none"), "radio", videoId=KARMA)["result"]
    assert result["playingFrom"] == "Karma Police Mix" and KARMA not in [t["videoId"] for t in result["items"]]
    assert yt.sent[0][1]["params"] == "wAEB"
