"""ytm_play: tracksFor, radio and albumBrowseId (F3)."""

import pytest

import ytm
import ytm_play as play


class FakeYT:
    def __init__(self, **responses):
        self.responses = responses
        self.calls = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            value = self.responses.get(name)
            if callable(value):
                return value(*args, **kwargs)
            if isinstance(value, Exception):
                raise value
            return value

        return method


def call(worker, method, **params):
    return worker.handle({"id": 3, "method": method, "params": params})


def tracks(n, prefix="v"):
    return [{"videoId": f"{prefix}{i}", "title": f"Song {i}", "length": "3:00",
             "artists": [{"name": "A", "id": "UCa"}]} for i in range(n)]


WATCH = {"tracks": tracks(3), "playlistId": "RDAMVMv0"}


def worker_with(**responses):
    fake = FakeYT(**responses)
    return ytm.Worker(fake, "ok"), fake


# ---- radio ----------------------------------------------------------------------------


def test_radio_from_a_song_pins_the_call_and_drops_the_song():
    worker, fake = worker_with(get_watch_playlist=WATCH)
    reply = call(worker, "radio", videoId="v0")
    assert reply["ok"], reply
    assert fake.calls == [("get_watch_playlist", (), {"videoId": "v0", "radio": True, "limit": 50})]
    assert reply["result"]["playlistId"] == "RDAMVMv0"
    assert [t["videoId"] for t in reply["result"]["items"]] == ["v1", "v2"]


def test_radio_from_a_playlist_strips_vl_and_prefixes_rdampl():
    worker, fake = worker_with(get_watch_playlist=WATCH)
    assert call(worker, "radio", playlistId="VLPLabc", limit=500)["ok"]
    assert fake.calls[-1] == ("get_watch_playlist", (), {"playlistId": "RDAMPLPLabc", "radio": True, "limit": 200})
    assert call(worker, "radio", playlistId="OLAK5uy_x")["ok"]
    assert fake.calls[-1][2]["playlistId"] == "RDAMPLOLAK5uy_x"
    # The song is only dropped from a song's radio.
    assert len(call(worker, "radio", playlistId="PL1")["result"]["items"]) == 3


def test_radio_from_a_mix_plays_the_mix_as_it_is():
    worker, fake = worker_with(get_watch_playlist=WATCH)
    assert call(worker, "radio", mixId="RDTMAK5uy_mix", limit=25)["ok"]
    assert fake.calls == [("get_watch_playlist", (), {"playlistId": "RDTMAK5uy_mix", "limit": 25})]


def test_radio_from_an_album_page_uses_its_audio_playlist():
    worker, fake = worker_with(get_album={"audioPlaylistId": "OLAK5uy_a"}, get_watch_playlist=WATCH)
    assert call(worker, "radio", browseId="MPREb_1")["ok"]
    assert fake.calls[0] == ("get_album", ("MPREb_1",), {})
    assert fake.calls[1][2]["playlistId"] == "RDAMPLOLAK5uy_a" and fake.calls[1][2]["radio"] is True
    worker, _ = worker_with(get_album={"audioPlaylistId": None})
    assert call(worker, "radio", browseId="MPREb_1")["error"]["code"] == "NOT_FOUND"


def test_radio_from_an_artist_uses_its_radio_mix_cached_or_fresh(tmp_path):
    artist = {"name": "R", "radioId": "RDEMx", "shuffleId": "RDAOx", "songs": {"browseId": "VLOLAK5uy_s", "results": []}}
    cache = ytm.caches.DiskCache(tmp_path)
    fake = FakeYT(get_artist=artist, get_watch_playlist=WATCH)
    worker = ytm.Worker(fake, "ok", cache=cache)
    assert call(worker, "radio", channelId="UCr")["ok"]
    assert [c[0] for c in fake.calls] == ["get_artist", "get_watch_playlist"]
    assert fake.calls[1][2] == {"playlistId": "RDEMx", "limit": 50}
    # An artist page cached with its radio id saves the get_artist.
    assert call(worker, "artist", channelId="UCr", cache="refresh")["ok"]
    fake.calls.clear()
    assert call(worker, "radio", channelId="UCr")["ok"]
    assert [c[0] for c in fake.calls] == ["get_watch_playlist"]


def test_artist_radio_id_falls_back_to_the_shuffle_mix_suffix():
    import ytm_normalize as norm

    assert norm.artist_radio_id({"radioId": "RDEMa", "shuffleId": "RDAOb"}) == "RDEMa"
    assert norm.artist_radio_id({"radioId": None, "shuffleId": "RDAOk8x"}) == "RDEMk8x"
    assert norm.artist_radio_id({"shuffleId": None}) is None
    page = norm.artist_page("UCr", {"name": "R", "shuffleId": "RDAOk8x"})
    assert (page["radioId"], page["shuffleId"]) == ("RDEMk8x", "RDAOk8x")
    # A cached page without the id is read again.
    worker, fake = worker_with(get_artist={"name": "R", "shuffleId": "RDAOk8x"}, get_watch_playlist=WATCH)
    worker.cache().put("artist", {"channelId": "UCr"}, {"type": "artistPage", "radioId": None})
    assert call(worker, "radio", channelId="UCr")["ok"]
    assert [c[0] for c in fake.calls] == ["get_artist", "get_watch_playlist"]
    assert fake.calls[1][2]["playlistId"] == "RDEMk8x"


def test_watch_lists_are_cut_to_the_limit():
    many = {"tracks": tracks(72), "playlistId": "RDTMAK5uy_m"}
    worker, _ = worker_with(get_watch_playlist=many)
    assert len(call(worker, "tracksFor", kind="mix", id="RDTMAK5uy_m")["result"]["items"]) == 50
    assert len(call(worker, "radio", mixId="RDTMAK5uy_m", limit=30)["result"]["items"]) == 30
    assert len(call(worker, "radio", videoId="v0")["result"]["items"]) == 50, "seed dropped, then cut"


def test_radio_needs_exactly_one_source():
    worker, fake = worker_with(get_watch_playlist=WATCH)
    assert call(worker, "radio")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "radio", videoId="")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "radio", videoId="v", playlistId="PL1")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "radio", videoId="v", limit="many")["error"]["code"] == "BAD_REQUEST"
    assert fake.calls == []


def test_radio_fixture_recorded_anonymously(load_fixture):
    data = load_fixture("radio.json")
    worker, _ = worker_with(get_watch_playlist=data)
    seed = data["tracks"][0]["videoId"]
    result = call(worker, "radio", videoId=seed)["result"]
    assert result["playlistId"] == "RDAMVM" + seed
    assert 40 <= len(result["items"]) <= 50 and seed not in [t["videoId"] for t in result["items"]]
    first = result["items"][0]
    assert first["type"] == "track" and first["title"] and first["artists"] and first["durationSec"]
    assert first["album"] is None or first["album"]["name"]


# ---- tracksFor ------------------------------------------------------------------------


def playlist_data(n, count=None):
    return {"id": "PL", "title": "P", "trackCount": count if count is not None else n, "tracks": tracks(n)}


@pytest.mark.parametrize("kind, given, reader, target", [
    ("playlist", "RDTMAK5uy_mix", "mix", "RDTMAK5uy_mix"),
    ("mix", "VLRDAMPLPLx", "mix", "RDAMPLPLx"),
    ("playlist", "RDCLAK5uy_curated", "playlist", "RDCLAK5uy_curated"),
    ("mix", "RDPN", "playlist", "RDPN"),
    ("playlist", "VLPLabc", "playlist", "PLabc"),
    ("playlist", "OLAK5uy_album", "playlist", "OLAK5uy_album"),
    ("album", "OLAK5uy_album", "playlist", "OLAK5uy_album"),
    ("album", "MPREb_1", "album", "MPREb_1"),
    ("playlist", "LM", "liked", "LM"),
    ("liked", "LM", "liked", "LM"),
    ("artist", "UCx", "artist", "UCx"),
])
def test_tracks_for_routes_by_id_then_kind(kind, given, reader, target):
    assert play.route(kind, given) == (reader, target)


def test_tracks_for_calls_the_right_reader():
    album = {"title": "OK Computer", "audioPlaylistId": "OLAK5uy_ok", "trackCount": 12, "tracks": tracks(12),
             "artists": [{"name": "Radiohead", "id": "UCr"}], "thumbnails": []}
    worker, fake = worker_with(get_album=album, get_playlist=playlist_data(3),
                               get_watch_playlist={"tracks": tracks(50), "playlistId": "RDTMAK5uy_m"})
    reply = call(worker, "tracksFor", kind="album", id="MPREb_1")
    assert reply["ok"] and fake.calls[-1] == ("get_album", ("MPREb_1",), {})
    result = reply["result"]
    assert (result["playlistId"], len(result["items"]), result["total"], result["complete"]) == ("OLAK5uy_ok", 12, 12, True)
    assert result["items"][0]["album"] == {"name": "OK Computer", "id": "MPREb_1"}

    result = call(worker, "tracksFor", kind="playlist", id="VLPLabc")["result"]
    assert fake.calls[-1] == ("get_playlist", ("PLabc",), {"limit": 5000})
    assert (result["playlistId"], len(result["items"]), result["complete"]) == ("PLabc", 3, True)

    result = call(worker, "tracksFor", kind="playlist", id="RDTMAK5uy_m")["result"]
    assert fake.calls[-1] == ("get_watch_playlist", (), {"playlistId": "RDTMAK5uy_m", "limit": 50})
    assert len(result["items"]) == 50 and result["playlistId"] == "RDTMAK5uy_m"

    result = call(worker, "tracksFor", kind="liked")["result"]
    assert fake.calls[-1] == ("get_playlist", ("LM",), {"limit": 5000})
    assert result["playlistId"] == "LM"


def test_tracks_for_caps_at_5000():
    worker, _ = worker_with(get_playlist=playlist_data(5003, count=9000))
    result = call(worker, "tracksFor", kind="playlist", id="PLbig")["result"]
    assert len(result["items"]) == 5000 and result["total"] == 9000 and result["complete"] is False
    worker, _ = worker_with(get_playlist=playlist_data(120, count=123))
    result = call(worker, "tracksFor", kind="playlist", id="PLsmall")["result"]
    assert result["complete"] is True, "fewer than the cap: all ytmusicapi could read"


def test_tracks_for_reads_only_up_to_a_given_limit():
    # Play, Play next and Add to queue ask for the 500 songs the queue takes.
    worker, fake = worker_with(get_playlist=playlist_data(600, count=961))
    result = call(worker, "tracksFor", kind="playlist", id="PLbig", limit=500)["result"]
    assert fake.calls == [("get_playlist", ("PLbig",), {"limit": 500})]
    assert (len(result["items"]), result["total"], result["complete"]) == (500, 961, False)
    worker, fake = worker_with(get_playlist=playlist_data(320, count=320))
    result = call(worker, "tracksFor", kind="liked", limit=500)["result"]
    assert fake.calls[-1] == ("get_playlist", ("LM",), {"limit": 500})
    assert (len(result["items"]), result["complete"]) == (320, True)
    # A list of exactly the limit is complete when its count says so.
    worker, _ = worker_with(get_playlist=playlist_data(500, count=500))
    assert call(worker, "tracksFor", kind="playlist", id="PL5", limit=500)["result"]["complete"] is True
    # Readers that read everything at once are cut to the limit.
    album = {"title": "Box", "audioPlaylistId": "OLAK5uy_box", "trackCount": 12, "tracks": tracks(12), "thumbnails": []}
    worker, _ = worker_with(get_album=album)
    result = call(worker, "tracksFor", kind="album", id="MPREb_box", limit=5)["result"]
    assert (len(result["items"]), result["total"], result["complete"]) == (5, 12, False)
    # Out of range limits are clamped; a wrong type is a bad request.
    worker, fake = worker_with(get_playlist=playlist_data(3))
    assert call(worker, "tracksFor", kind="playlist", id="PL1", limit=99999)["ok"]
    assert fake.calls[-1] == ("get_playlist", ("PL1",), {"limit": 5000})
    assert call(worker, "tracksFor", kind="playlist", id="PL1", limit="all")["error"]["code"] == "BAD_REQUEST"


def test_tracks_for_artist_reads_its_songs_playlist(tmp_path):
    artist = {"name": "R", "radioId": "RDEMx", "songs": {"browseId": "VLOLAK5uy_songs", "results": tracks(5, "s")}}
    cache = ytm.caches.DiskCache(tmp_path)
    fake = FakeYT(get_artist=artist, get_playlist=playlist_data(150))
    worker = ytm.Worker(fake, "ok", cache=cache)
    result = call(worker, "tracksFor", kind="artist", id="UCr")["result"]
    assert [c[0] for c in fake.calls] == ["get_artist", "get_playlist"]
    assert fake.calls[1] == ("get_playlist", ("OLAK5uy_songs",), {"limit": 5000})
    assert len(result["items"]) == 150
    # From the cached artist page: no get_artist.
    assert call(worker, "artist", channelId="UCr", cache="refresh")["ok"]
    fake.calls.clear()
    assert call(worker, "tracksFor", kind="artist", id="UCr")["ok"]
    assert [c[0] for c in fake.calls] == ["get_playlist"]
    fake.calls.clear()
    assert call(worker, "tracksFor", kind="artist", id="UCr", limit=500)["ok"]
    assert fake.calls == [("get_playlist", ("OLAK5uy_songs",), {"limit": 500})]
    # No See all on the songs shelf: the page's own songs.
    worker, fake = worker_with(get_artist={"name": "N", "songs": {"results": tracks(4, "n")}})
    result = call(worker, "tracksFor", kind="artist", id="UCn")["result"]
    assert [t["videoId"] for t in result["items"]] == ["n0", "n1", "n2", "n3"]
    assert [c[0] for c in fake.calls] == ["get_artist"]


def test_tracks_for_bad_requests_and_liked_needs_auth():
    worker, fake = worker_with()
    assert call(worker, "tracksFor", id="PL1")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "tracksFor", kind="podcast", id="x")["error"]["code"] == "BAD_REQUEST"
    assert call(worker, "tracksFor", kind="album")["error"]["code"] == "BAD_REQUEST"
    signed_out = ytm.Worker(FakeYT(get_playlist=playlist_data(1)), "none")
    assert call(signed_out, "tracksFor", kind="liked")["error"]["code"] == "AUTH_REQUIRED"
    assert call(signed_out, "tracksFor", kind="playlist", id="LM")["error"]["code"] == "AUTH_REQUIRED"
    assert fake.calls == []


# ---- albumBrowseId --------------------------------------------------------------------


def test_album_browse_id_pins_the_call_and_is_cached(tmp_path):
    fake = FakeYT(get_album_browse_id="MPREb_found")
    worker = ytm.Worker(fake, "none", cache=ytm.caches.DiskCache(tmp_path))
    reply = call(worker, "albumBrowseId", playlistId="VLOLAK5uy_x", cache="prefer")
    assert reply["ok"] and reply["cached"] is False
    assert reply["result"] == {"playlistId": "OLAK5uy_x", "browseId": "MPREb_found"}
    assert fake.calls == [("get_album_browse_id", ("OLAK5uy_x",), {})]
    again = call(worker, "albumBrowseId", playlistId="VLOLAK5uy_x", cache="prefer")
    assert again["cached"] is True and again["http"] == 0 and len(fake.calls) == 1


def test_album_browse_id_only_for_album_playlists():
    worker, fake = worker_with(get_album_browse_id=None)
    assert call(worker, "albumBrowseId", playlistId="PLabc")["error"]["code"] == "BAD_REQUEST"
    assert fake.calls == []
    assert call(worker, "albumBrowseId", playlistId="OLAK5uy_x")["error"]["code"] == "NOT_FOUND"


def test_registry():
    assert ytm.METHODS["tracksFor"] is play.m_tracks_for
    # A long tracksFor (a shuffle of 5000 songs takes about 35 s) must not
    # hold up browsePage on the pages lane; albumBrowseId is a single GET.
    assert ytm.LANES["tracksFor"] == "tracks" and ytm.LANES["browsePage"] == "pages"
    assert "tracks" in ytm.KNOWN_LANES
    assert "radio" not in ytm.LANES and "albumBrowseId" not in ytm.LANES, "one to three requests: no lane"
    assert ytm.CACHEABLE >= {"albumBrowseId"} and not {"tracksFor", "radio"} & ytm.CACHEABLE
    assert "radio" in ytm.RECORDERS
    assert not {"tracksFor", "radio", "albumBrowseId"} & ytm.MUTATES
