"""The method registry: area modules merged into ytm.py's tables."""

import re
from types import SimpleNamespace

import pytest

import ytm
from conftest import ROOT


def module(name, **tables):
    return SimpleNamespace(__name__=name, **tables)


def noop(worker, params):
    return {}


def test_duplicate_method_names_across_modules_raise():
    a = module("ytm_a", METHODS={"thing": noop})
    b = module("ytm_b", METHODS={"thing": noop})
    with pytest.raises(ValueError, match="thing"):
        ytm.merge_registries([a, b])
    with pytest.raises(ValueError, match="'song'"):
        ytm.merge_registries([ytm._Core, module("ytm_x", METHODS={"song": noop})])


def test_duplicate_recorders_and_settings_raise():
    with pytest.raises(ValueError, match="recorder"):
        ytm.merge_registries([module("a", RECORDERS={"x": noop}), module("b", RECORDERS={"x": noop})])
    with pytest.raises(ValueError, match="setting"):
        ytm.merge_registries([module("a", SETTINGS={"k": str}), module("b", SETTINGS={"k": str})])


def test_tables_may_only_name_their_own_methods():
    with pytest.raises(ValueError, match="CACHEABLE"):
        ytm.merge_registries([module("a", METHODS={"x": noop}, CACHEABLE={"y"})])
    with pytest.raises(ValueError, match="lanes"):
        ytm.merge_registries([module("a", METHODS={"x": noop}, LANES={"x": "elsewhere"})])
    with pytest.raises(ValueError, match="INVALIDATES"):
        ytm.merge_registries([module("a", METHODS={"x": noop, "y": noop}, INVALIDATES={"x": [("y", None, None)]})])
    merged = ytm.merge_registries([
        module("a", METHODS={"read": noop, "edit": noop}, CACHEABLE={"read"}, LANES={"read": "pages"},
               INVALIDATES={"edit": [("read", "id", "readId")]}, MUTATES={"edit"}),
    ])
    assert merged["LANES"] == {"read": "pages"} and merged["MUTATES"] == {"edit"}


def test_area_modules_are_merged():
    assert ytm.METHODS["browsePage"] is ytm.pages.m_browse_page
    assert ytm.LANES["browsePage"] == "pages" and "browsePage" in ytm.CACHEABLE
    assert "browse" in ytm.RECORDERS
    assert set(ytm.LANES.values()) <= set(ytm.KNOWN_LANES)


def test_every_mutation_invalidates_or_says_why_not():
    assert {"rate", "createPlaylist", "addToPlaylist", "removeFromPlaylist", "deletePlaylist"} <= ytm.MUTATES
    for name in ytm.MUTATES:
        reason = ytm.NO_INVALIDATES.get(name)
        assert ytm.INVALIDATES.get(name) or (isinstance(reason, str) and reason.strip()), name
        assert name not in ytm.CACHEABLE, f"{name} changes the account; its reply must not be cached"


def test_invalidates_are_triples_of_cacheable_methods():
    for name, triples in ytm.INVALIDATES.items():
        for method, src, dst in triples:
            assert method in ytm.CACHEABLE
            assert (src is None) == (dst is None), f"{name}: srcKey and dstKey come as a pair"


def test_invalidation_sources_are_checked():
    def merge(triple):
        return ytm.merge_registries([module("a", METHODS={"read": noop, "edit": noop}, CACHEABLE={"read"},
                                            INVALIDATES={"edit": [triple]})])

    for src, dst in ((ytm.Fixed("A"), "browseId"), (ytm.Prefixed("VL", "playlistId"), "browseId"), ("id", "id")):
        merge(("read", src, dst))
    for bad in (("read", 5, "id"), ("read", "id", None), ("read", None, "id"), ("read", ytm.Fixed("A"), None)):
        with pytest.raises(ValueError, match="INVALIDATES"):
            merge(bad)


def test_every_core_edit_drops_browse_pages():
    for name in ("rate", "createPlaylist", "addToPlaylist", "removeFromPlaylist", "deletePlaylist"):
        assert any(method == "browsePage" for method, _, _ in ytm.INVALIDATES[name]), name


def test_readme_lists_every_method():
    readme = (ROOT / "README.md").read_text()
    section = readme.split("## Worker protocol", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"`([A-Za-z]+)`", section))
    missing = sorted(set(ytm.METHODS) - listed)
    assert not missing, f"README's Worker protocol section does not list {missing}"
