"""G2 (the Listen again pin on every row) with G3 (every episode row ytm_raw
reads goes through ytm_podcasts.episode_item, and podcast pages reload in
an order or filter): those episode rows and pages keep their pins."""

import ytm_artist as artist
import ytm_raw as raw
from test_podcasts import PODCAST, episode_row, result
from test_podcasts_more import TREVOR, artist_carousel, podcast_page, reload_reply, reload_worker, trevor_episode
from test_song_tokens import header_menu_items, items_of, keep, pins


def with_pin(row, name, pinned=False):
    """An episode row with the Listen again toggle in its menu."""
    data = row["musicMultiRowListItemRenderer"]
    data.setdefault("menu", {"menuRenderer": {"items": []}})["menuRenderer"]["items"].append(keep(name, pinned))
    return row


def test_an_artist_pages_episode_rows_keep_their_pin_through_the_podcast_reader():
    page = artist.build(TREVOR, {"name": "Trevor Jackson", "channelId": TREVOR},
                        [artist_carousel("Latest episodes", [with_pin(trevor_episode(), "EP", pinned=True)])])
    [episode] = page["sections"][0]["items"]
    assert episode["date"] == "Apr 10, 2025" and episode["durationSec"] == 3360, "G3's reader"
    assert episode["pinned"] is True and episode["listenAgainTokens"] == pins("EP"), "G2's pin"


def test_a_see_all_grid_of_episodes_keeps_each_rows_pin():
    grid = raw.parse_row({"gridRenderer": {"items": [with_pin(trevor_episode(), "G"), trevor_episode()]}})
    pinned, plain = grid["items"]
    assert pinned["date"] == "Apr 10, 2025" and pinned["kind"] == "episode"
    assert pinned["pinned"] is False and pinned["listenAgainTokens"] == pins("G")
    assert plain["listenAgainTokens"] is None, "a row whose menu has no pin has none"


def test_an_ordered_podcast_page_keeps_its_header_pin_and_each_rows(load_fixture):
    first = podcast_page(load_fixture)
    header_menu_items(first).append(keep("POD", pinned=True))
    oldest = reload_reply([with_pin(episode_row(i), f"EP{i}") for i in range(100, 103)], order=1, chosen=1)
    worker = reload_worker(first, {"Rorder1": oldest, "Rfilter1": oldest})
    page = result(worker, "podcast", browseId=PODCAST, order=1, filter=1)
    assert page["order"] == 1 and page["filter"] == 1
    assert page["header"]["pin"] == {"pinned": True, "listenAgainTokens": pins("POD")}
    assert [e["listenAgainTokens"] for e in items_of(page)] == [pins(f"EP{i}") for i in range(100, 103)]
