"""Continuation styles of ytm_pages.fetch, on hand-built responses only."""

import pytest
from ytmusicapi.continuations import get_continuation_string

import ytm_pages as pages
from ytm_proto import WorkerError


class FakeClient:
    """Answers _send_request from a queue and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def _send_request(self, endpoint, body, additionalParams=""):
        self.requests.append((endpoint, dict(body), additionalParams))
        return self.responses.pop(0)


def entries(first, count):
    return [{"n": i} for i in range(first, first + count)]


def parse(contents):
    return [c["n"] for c in contents if "n" in c]


def next_data(token):
    return [{"nextContinuationData": {"continuation": token}}] if token else []


def browse_grid(items, token=None, header=None):
    section = {"contents": [{"gridRenderer": {"items": items, "continuations": next_data(token)}}]}
    if header:
        section["header"] = header
    return {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
        {"tabRenderer": {"content": {"sectionListRenderer": section}}}]}}}


def grid_continuation(items, token=None):
    return {"continuationContents": {"gridContinuation": {"items": items, "continuations": next_data(token)}}}


def locate_grid(response):
    return pages.unwrap(pages.section_rows(response)[0])[1]


BODY = {"browseId": "FEmusic_x"}


def test_ctoken_first_middle_and_last_page():
    yt = FakeClient(browse_grid(entries(0, 10), "T1"))
    items, cursor = pages.fetch(yt, "browse", BODY, "ctoken", locate_grid, parse, min_items=1)
    assert items == list(range(10)) and cursor == "c.T1"
    assert yt.requests == [("browse", BODY, "")]

    yt = FakeClient(grid_continuation(entries(10, 10), "T2"))
    items, cursor = pages.fetch(yt, "browse", BODY, "ctoken", locate_grid, parse, cursor="c.T1", min_items=1)
    assert items == list(range(10, 20)) and cursor == "c.T2"
    assert yt.requests == [("browse", BODY, get_continuation_string("T1"))]

    yt = FakeClient(grid_continuation(entries(20, 5)))
    items, cursor = pages.fetch(yt, "browse", BODY, "ctoken", locate_grid, parse, cursor="c.T2", min_items=1)
    assert items == list(range(20, 25)) and cursor is None, "no continuation left: the list is complete"


def test_ctoken_on_the_search_endpoint():
    body = {"query": "radiohead", "params": "EgWKAQIIAWoKEAkQBRAKEAMQBA%3D%3D"}
    first = {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
        "sectionListRenderer": {"contents": [{"musicShelfRenderer": {"contents": entries(0, 20),
                                                                     "continuations": next_data("S1")}}]}}}}]}}}
    more = {"continuationContents": {"musicShelfContinuation": {"contents": entries(20, 20)}}}
    yt = FakeClient(first, more)
    items, cursor = pages.fetch(yt, "search", body, "ctoken", locate_grid, parse, min_items=40)
    assert items == list(range(40)) and cursor is None
    assert yt.requests == [("search", body, ""), ("search", body, get_continuation_string("S1"))]


def playlist_page(items, token):
    contents = items + [{"continuationItemRenderer": {"continuationEndpoint": {
        "continuationCommand": {"token": token}}}}]
    return {"contents": {"twoColumnBrowseResultsRenderer": {"secondaryContents": {"sectionListRenderer": {
        "contents": [{"musicPlaylistShelfRenderer": {"contents": contents}}]}}}}}


def body_continuation(items, token=None):
    tail = [{"continuationItemRenderer": {"continuationEndpoint": {"continuationCommand": {"token": token}}}}] \
        if token else []
    return {"onResponseReceivedActions": [{"appendContinuationItemsAction": {"continuationItems": items + tail}}]}


def test_body_style_sends_only_the_token():
    yt = FakeClient(playlist_page(entries(0, 100), "B1"))
    items, cursor = pages.fetch(yt, "browse", {"browseId": "VLPL1"}, "body", locate_grid, parse, min_items=1)
    assert len(items) == 100 and cursor == "b.B1"

    yt = FakeClient(body_continuation(entries(100, 100), "B2"))
    items, cursor = pages.fetch(yt, "browse", {"browseId": "VLPL1"}, "body", locate_grid, parse, cursor=cursor,
                                min_items=1)
    assert yt.requests == [("browse", {"continuation": "B1"}, "")]
    assert items == list(range(100, 200)) and cursor == "b.B2"

    # A bare token continues in the style the caller names.
    yt = FakeClient(body_continuation(entries(200, 10)))
    items, cursor = pages.fetch(yt, "browse", {"browseId": "VLPL1"}, "body", locate_grid, parse, cursor="B2",
                                min_items=1)
    assert yt.requests == [("browse", {"continuation": "B2"}, "")]
    assert cursor is None


def sort_header(*tokens):
    options = []
    for i, token in enumerate(tokens):
        options.append({"musicMultiSelectMenuItemRenderer": {
            "title": {"runs": [{"text": f"Order {i}"}]},
            "formItemEntityKey": f"key{i}",
            "selectedCommand": {"commandExecutorCommand": {"commands": [
                {"musicCheckboxFormItemMutatedCommand": {}},
                {"browseSectionListReloadEndpoint": {"continuation": {
                    "reloadContinuationData": {"continuation": token}}}}]}}}})
    return {"musicSideAlignedItemRenderer": {"endItems": [{"musicSortFilterButtonRenderer": {
        "menu": {"musicMultiSelectMenuRenderer": {"options": options}}}}]}}


def test_reload_style_uses_the_chosen_option():
    base = browse_grid(entries(0, 10), "T1", header=sort_header("R0", "R1"))
    base["frameworkUpdates"] = {"entityBatchUpdate": {"mutations": [
        {"entityKey": "key0", "payload": {"musicFormBooleanChoice": {"selected": True}}},
        {"entityKey": "key1", "payload": {"musicFormBooleanChoice": {"selected": False}}}]}}
    assert pages.sort_options(base) == [{"index": 0, "title": "Order 0", "selected": True},
                                        {"index": 1, "title": "Order 1", "selected": False}]
    resorted = {"continuationContents": {"sectionListContinuation": {"contents": [
        {"gridRenderer": {"items": entries(50, 10), "continuations": next_data("T9")}}]}}}
    yt = FakeClient(base, resorted)
    items, cursor = pages.fetch(yt, "browse", BODY, "reload", locate_grid, parse, order=1, min_items=1)
    assert items == list(range(50, 60)) and cursor == "c.T9"
    assert yt.requests == [("browse", BODY, ""), ("browse", BODY, get_continuation_string("R1"))]

    # Later pages continue as ctoken.
    yt = FakeClient(grid_continuation(entries(60, 10)))
    items, _ = pages.fetch(yt, "browse", BODY, "reload", locate_grid, parse, cursor="T9", min_items=1)
    assert yt.requests == [("browse", BODY, get_continuation_string("T9"))]

    with pytest.raises(WorkerError) as err:
        pages.fetch(FakeClient(base), "browse", BODY, "reload", locate_grid, parse, order=2)
    assert err.value.code == "BAD_REQUEST"


def test_min_items_loop_stops_at_max_http():
    yt = FakeClient(browse_grid(entries(0, 10), "T1"), grid_continuation(entries(10, 10), "T2"),
                    grid_continuation(entries(20, 10), "T3"), grid_continuation(entries(30, 10), "T4"))
    items, cursor = pages.fetch(yt, "browse", BODY, "ctoken", locate_grid, parse, min_items=100, max_http=3)
    assert len(yt.requests) == 3 and items == list(range(30))
    assert cursor == "c.T3", "the caller can still load more"


def test_min_items_reached_stops_early():
    yt = FakeClient(browse_grid(entries(0, 30), "T1"), grid_continuation(entries(30, 30), "T2"))
    items, cursor = pages.fetch(yt, "browse", BODY, "ctoken", locate_grid, parse, min_items=40)
    assert len(yt.requests) == 2 and len(items) == 60 and cursor == "c.T2"


def test_empty_continuation_ends_the_list():
    yt = FakeClient(browse_grid(entries(0, 10), "T1"), grid_continuation([], "T2"))
    items, cursor = pages.fetch(yt, "browse", BODY, "ctoken", locate_grid, parse, min_items=40)
    assert items == list(range(10)) and cursor is None
    yt = FakeClient({"responseContext": {}})
    assert pages.fetch(yt, "browse", BODY, "ctoken", locate_grid, parse, cursor="c.T1") == ([], None)


def test_cursor_is_none_at_the_end():
    yt = FakeClient(browse_grid(entries(0, 3)))
    assert pages.fetch(yt, "browse", BODY, "ctoken", locate_grid, parse) == ([0, 1, 2], None)
    assert pages.fetch(FakeClient({}), "browse", BODY, "ctoken", lambda r: None, parse) == ([], None)


def test_unknown_style_is_rejected():
    with pytest.raises(ValueError):
        pages.fetch(FakeClient(), "browse", BODY, "pages", locate_grid, parse)
