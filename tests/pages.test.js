const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const tr = (videoId, extra = {}) => ({ type: "track", videoId, title: "Song " + videoId, ...extra })
const album = browseId => ({ type: "album", browseId, title: "Album " + browseId })
const page = (sections, extra = {}) => ({ type: "page", v: 2, kind: "browse", id: "X", title: "T", sections, cursor: null, hasMore: false, ...extra })
const section = (title, items, extra = {}) => ({ type: "section", title, items, more: null, play: null, layout: null, ...extra })
const label = row => row.header ? "# " + row.title : row.item.type + ":" + (row.item.videoId || row.item.browseId || row.item.title || "")

test("canonicalJson sorts keys at every level and is stable", () => {
  const a = Model.canonicalJson({ b: 1, a: { d: [3, { z: 1, y: 2 }], c: "x" } })
  const b = Model.canonicalJson({ a: { c: "x", d: [3, { y: 2, z: 1 }] }, b: 1 })
  assert.equal(a, b)
  assert.equal(a, '{"a":{"c":"x","d":[3,{"y":2,"z":1}]},"b":1}')
  assert.equal(Model.canonicalJson({ a: undefined, b: null }), '{"b":null}')
  assert.equal(Model.canonicalJson([undefined, "q\""]), '[null,"q\\""]')
  assert.equal(Model.canonicalJson("x"), '"x"')
})

test("pageRef: method + canonical params, so albums, singles and orders never collide", () => {
  const albums = Model.pageRef({ type: "browse", browseId: "MPADUC1", params: "albums" })
  const singles = Model.pageRef({ type: "browse", browseId: "MPADUC1", params: "singles" })
  assert.equal(albums.method, "browsePage")
  assert.notEqual(albums.key, singles.key)
  assert.equal(albums.id, singles.id, "both forget together by browseId")
  const plain = Model.pageRef({ type: "browse", browseId: "MPADUC1", params: "albums" })
  const sorted = Model.pageRef({ type: "browse", browseId: "MPADUC1", params: "albums" }, { order: 2 })
  assert.equal(plain.key, albums.key)
  assert.notEqual(sorted.key, plain.key)
  assert.notEqual(sorted.key, Model.pageRef({ type: "browse", browseId: "MPADUC1", params: "albums" }, { order: 3 }).key)
  assert.deepEqual(sorted.params, { browseId: "MPADUC1", params: "albums", order: 2 })
  // Empty params are left out: no params and null params are the same page.
  assert.equal(Model.pageRef({ type: "browse", browseId: "FEmusic_x" }).key,
    Model.pageRef({ type: "browse", browseId: "FEmusic_x", params: null }).key)
  assert.equal(Model.pageRef({ type: "browse", browseId: "FEmusic_x" }, { order: null }).key, 'browsePage:{"browseId":"FEmusic_x"}')
})

test("pageRef: the existing kinds keep their methods and params", () => {
  const cases = [
    [{ type: "album", browseId: "MPREb_1" }, "album", { browseId: "MPREb_1" }, "MPREb_1"],
    [{ type: "playlist", playlistId: "PL1" }, "playlist", { playlistId: "PL1" }, "PL1"],
    [{ type: "artist", channelId: "UC1" }, "artist", { channelId: "UC1" }, "UC1"],
    [{ type: "liked", title: "Liked Music" }, "liked", {}, "LM"],
    [{ type: "history", title: "History" }, "history", { limit: 100 }, "history"],
  ]
  for (const [item, method, params, id] of cases) {
    const ref = Model.pageRef(item)
    assert.equal(ref.kind, item.type)
    assert.equal(ref.method, method)
    assert.deepEqual(ref.params, params)
    assert.equal(ref.id, id)
    assert.equal(ref.key, method + ":" + Model.canonicalJson(params))
  }
  assert.equal(Model.pageRef({ type: "album", browseId: "A" }).key === Model.pageRef({ type: "playlist", playlistId: "A" }).key, false)
})

test("pageRef: unknown kinds and missing ids give null; collectionTarget wraps it", () => {
  assert.equal(Model.pageRef({ type: "podcast", browseId: "MPSPx" }), null)
  assert.equal(Model.pageRef({ type: "track", videoId: "v" }), null)
  assert.equal(Model.pageRef({ type: "toString" }), null)
  assert.equal(Model.pageRef({ type: "album" }), null, "no browseId")
  assert.equal(Model.pageRef(null), null)
  assert.deepEqual(Model.collectionTarget({ type: "playlist", playlistId: "PL1" }), { kind: "playlist", id: "PL1" })
  assert.deepEqual(Model.collectionTarget({ type: "liked" }), { kind: "liked", id: "LM" })
  assert.equal(Model.collectionTarget({ type: "podcast" }), null)
  assert.deepEqual(Model.cannotOpen(), { ok: false, error: { code: "BAD_REQUEST", message: "Cannot open this item yet" } })
  assert.equal(Model.pageKind({ kind: "nope" }), null)
  assert.equal(Model.pageKindTitle(Model.pageRef({ type: "liked" })), "Liked Music")
  assert.equal(Model.pageRound(Model.pageRef({ type: "artist", channelId: "UC1" }), null), true)
  assert.equal(Model.pageRound(Model.pageRef({ type: "browse", browseId: "X" }), { round: true }), true)
  assert.equal(Model.pageRound(Model.pageRef({ type: "album", browseId: "X" }), null), false)
})

test("a kind's claims hook takes over items of another type", () => {
  Model.PAGE_KINDS.testMix = {
    method: "mix", title: "Mix",
    claims: item => item.type === "playlist" && /^RD/.test(item.playlistId || ""),
    params: t => ({ playlistId: t.playlistId }), id: t => t.playlistId
  }
  try {
    assert.equal(Model.pageRef({ type: "playlist", playlistId: "RDCLAKx" }).kind, "testMix")
    assert.equal(Model.pageRef({ type: "playlist", playlistId: "PL1" }).kind, "playlist")
  } finally {
    delete Model.PAGE_KINDS.testMix
  }
})

test("history replies are adapted into a collection", () => {
  const ref = Model.pageRef({ type: "history" })
  const page = Model.pageKind(ref).adapt({ items: [tr("a", { thumb: "t" }), tr("b")] })
  assert.equal(page.type, "collection")
  assert.equal(page.kind, "history")
  assert.equal(page.subtitle, "2 songs")
  assert.equal(page.thumb, "t")
  assert.equal(page.tracks.length, 2)
  assert.equal(Model.historyCollection({}).tracks.length, 0)
})

test("routeEndpoint defaults", () => {
  assert.deepEqual(Model.routeEndpoint({ browseId: "VLPL123", title: "Songs" }), { type: "playlist", playlistId: "PL123", title: "Songs" })
  assert.deepEqual(Model.routeEndpoint({ browseId: "MPREb_1" }), { type: "album", browseId: "MPREb_1", title: "" })
  assert.deepEqual(Model.routeEndpoint({ browseId: "UC1", title: "Radiohead" }), { type: "artist", channelId: "UC1", name: "Radiohead" })
  assert.deepEqual(Model.routeEndpoint({ browseId: "MPLAUC1" }), { type: "artist", channelId: "UC1", name: "" })
  // A channel with params (videos, playlists of a profile) is a browse page.
  assert.deepEqual(Model.routeEndpoint({ browseId: "UC1", params: "p", title: "Videos" }),
    { type: "browse", browseId: "UC1", params: "p", title: "Videos" })
  assert.deepEqual(Model.routeEndpoint({ browseId: "MPLAUC1", params: "p" }),
    { type: "browse", browseId: "MPLAUC1", params: "p", title: "" })
  // S1 routes an artist's discography (MPAD + params) to artistAlbums.
  assert.deepEqual(Model.routeEndpoint({ browseId: "MPADUC1", params: "albums", title: "Albums" }),
    { type: "artistAlbums", browseId: "MPADUC1", params: "albums", title: "Albums" })
  assert.deepEqual(Model.routeEndpoint({ browseId: "FEmusic_new_releases_albums" }),
    { type: "browse", browseId: "FEmusic_new_releases_albums", params: null, title: "" })
  assert.equal(Model.routeEndpoint({}), null)
  assert.equal(Model.routeEndpoint(null), null)
  // Every routed target opens as a page.
  for (const id of ["VLPL1", "MPREb_1", "UC1", "MPLAUC1", "FEmusic_x"]) assert.notEqual(Model.pageRef(Model.routeEndpoint({ browseId: id })), null)
})

test("routes: the most specific match wins, ties go to the route added last", () => {
  const added = [
    { prefix: "VLRDPN", target: () => ({ type: "newEpisodes" }) },
    { pageType: "MUSIC_PAGE_TYPE_USER_CHANNEL", target: (ep, id) => ({ type: "user", channelId: id }) },
    { browseId: "FEmusic_moods_and_genres", target: () => ({ type: "moods" }) },
    { prefix: "FEmusic_moods_and_genres", target: () => ({ type: "moodsPrefix" }) },
    { prefix: "MPRE", target: () => ({ type: "albumLater" }) },
    { prefix: "FEx", target: () => null },
  ]
  Model.ROUTES.push(...added)
  try {
    assert.equal(Model.routeEndpoint({ browseId: "VLRDPN" }).type, "newEpisodes")
    assert.equal(Model.routeEndpoint({ browseId: "VLPL1" }).type, "playlist")
    assert.equal(Model.routeEndpoint({ browseId: "UC1", pageType: "MUSIC_PAGE_TYPE_USER_CHANNEL" }).type, "user")
    assert.equal(Model.routeEndpoint({ browseId: "FEmusic_moods_and_genres" }).type, "moods")
    assert.equal(Model.routeEndpoint({ browseId: "FEmusic_moods_and_genres_category", params: "p" }).type, "moodsPrefix")
    assert.equal(Model.routeEndpoint({ browseId: "MPREb_1" }).type, "albumLater")
    assert.equal(Model.routeEndpoint({ browseId: "FExyz" }).type, "browse", "a null target falls back to browse")
  } finally {
    Model.ROUTES.splice(Model.ROUTES.length - added.length, added.length)
  }
  assert.equal(Model.routeEndpoint({ browseId: "MPREb_1" }).type, "album")
})

test("pageRows: headers, items with per-section trackIndex, See all, message and Load more", () => {
  const p = page([
    section("Songs", [tr("a"), album("B1"), tr("b")], { more: { browseId: "VLPL1", params: null, pageType: null } }),
    section("Empty", []),
    section("Albums", [album("B2")]),
    section(null, [tr("c")]),
    section("Only more", [], { more: { browseId: "MPADUC1", params: "x", pageType: null } }),
  ], { cursor: "c.tok", message: "Nothing else here" })
  const rows = Model.pageRows(p)
  assert.deepEqual(rows.map(label), [
    "# Songs", "track:a", "album:B1", "track:b", "more:See all Songs",
    "# Albums", "album:B2",
    "track:c",
    "# Only more", "more:See all Only more",
    "message:Nothing else here", "loadMore:Load more",
  ])
  assert.deepEqual(rows.filter(r => r.item && r.item.type === "track").map(r => [r.sectionIndex, r.trackIndex]), [[0, 0], [0, 1], [3, 0]])
  assert.equal(rows[2].trackIndex, undefined)
  assert.equal(rows[2].sectionIndex, 0)
  assert.equal(rows[4].sectionIndex, 0)
  assert.deepEqual(rows[4].item.endpoint, { browseId: "VLPL1", params: null, pageType: null })
  assert.deepEqual(Model.moreTarget(rows[4].item), { type: "playlist", playlistId: "PL1", title: "Songs" })
  assert.deepEqual(Model.moreTarget(rows[9].item), { type: "artistAlbums", browseId: "MPADUC1", params: "x", title: "Only more" })
  assert.deepEqual(Model.sectionTracks(p, 0).map(t => t.videoId), ["a", "b"])
  assert.deepEqual(Model.sectionTracks(p, 9), [])
  assert.deepEqual(Model.pageTracks(p).map(t => t.videoId), ["a", "b", "c"])
  assert.equal(Model.pageItemCount(p), 5)
  // A cached copy has no cursor but still has more: the row stays.
  assert.equal(Model.pageRows(page([section("S", [tr("a")])], { hasMore: true })).at(-1).item.type, "loadMore")
  assert.equal(Model.pageRows(page([section("S", [tr("a")])])).at(-1).item.type, "track")
  assert.deepEqual(Model.pageRows(null), [])
})

test("nextSelectable skips headers and message rows only", () => {
  const rows = [
    { header: true, title: "H" },
    { item: { type: "message", title: "m" } },
    { item: { type: "action", id: "x", title: "Do" } },
    { item: tr("a") },
    { item: { type: "more", title: "See all", endpoint: { browseId: "VLx" } } },
    { item: { type: "message", title: "m2" } },
    { item: { type: "loadMore", title: "Load more" } },
  ]
  assert.equal(Model.nextSelectable(rows, -1, 1), 2)
  assert.equal(Model.nextSelectable(rows, 2, 1), 3)
  assert.equal(Model.nextSelectable(rows, 3, 1), 4)
  assert.equal(Model.nextSelectable(rows, 4, 1), 6)
  assert.equal(Model.nextSelectable(rows, 6, 1), 6)
  assert.equal(Model.nextSelectable(rows, 6, -1), 4)
  assert.equal(Model.nextSelectable(rows, 2, -1), 2)
  assert.equal(Model.nextSelectable(rows, -1, -1), 6)
  assert.equal(Model.selectableRow(rows[1]), false)
  assert.equal(Model.selectableRow(rows[0]), false)
  assert.equal(Model.selectableRow(rows[6]), true)
})

test("itemKey: setVideoId first, then type and id", () => {
  assert.equal(Model.itemKey(tr("a", { setVideoId: "S1" })), "set:S1")
  assert.equal(Model.itemKey(tr("a")), "track:a")
  assert.equal(Model.itemKey(album("B")), "album:B")
  assert.equal(Model.itemKey({ type: "playlist", playlistId: "PL" }), "playlist:PL")
  assert.equal(Model.itemKey({ type: "artist", channelId: "UC" }), "artist:UC")
  assert.equal(Model.itemKey({ type: "liked" }), "liked:")
  assert.equal(Model.itemKey({ type: "loadMore", title: "Loading" }), "loadMore:")
  assert.equal(Model.itemKey({ type: "query", text: "abc" }), "query:abc")
  assert.equal(Model.itemKey({ type: "action", id: "about", title: "About" }), "action:about")
  assert.notEqual(Model.itemKey({ type: "more", endpoint: { browseId: "MPAD1", params: "a" } }),
    Model.itemKey({ type: "more", endpoint: { browseId: "MPAD1", params: "s" } }))
  assert.equal(Model.itemKey({ type: "podcast", browseId: "MPSP1" }), "podcast:MPSP1")
  assert.equal(Model.itemKey(null), "")
})

test("appendPage keeps distinct setVideoIds of one song and drops a re-sent entry", () => {
  const first = page([section(null, [tr("a", { setVideoId: "S1" }), tr("b", { setVideoId: "S2" })])], { cursor: "c.1", hasMore: true })
  const next = page([section(null, [tr("a", { setVideoId: "S3" }), tr("b", { setVideoId: "S2" }), tr("c", { setVideoId: "S4" })])], { cursor: "c.2" })
  const merged = Model.appendPage(first, next)
  assert.deepEqual(merged.sections[0].items.map(t => t.setVideoId), ["S1", "S2", "S3", "S4"])
  assert.equal(merged.cursor, "c.2")
  assert.equal(merged.hasMore, true)
  assert.equal(first.sections[0].items.length, 2, "the first page is not mutated")
  assert.equal(merged.title, "T")
})

test("appendPage keeps repeated history tracks and drops a re-sent album", () => {
  const history = Model.appendPage(page([section("Today", [tr("a"), tr("b")])], { cursor: "c.1" }),
    page([section(null, [tr("a"), tr("a")])], { cursor: null }))
  assert.deepEqual(history.sections[0].items.map(t => t.videoId), ["a", "b", "a", "a"])
  assert.equal(history.cursor, null)
  assert.equal(history.hasMore, false)
  const grid = Model.appendPage(page([section("Albums", [album("A"), album("B")])], { cursor: "c.1" }),
    page([section(null, [album("B"), album("C"), album("C")])], { cursor: "c.2" }))
  assert.deepEqual(grid.sections[0].items.map(a => a.browseId), ["A", "B", "C"])
})

test("appendPage adds titled sections after the last one, and flat item lists", () => {
  const merged = Model.appendPage(page([section("One", [album("A")])], { cursor: "c.1" }),
    page([section("Two", [album("A"), album("B")]), section("Three", [album("A")]), section("Four", [], { more: { browseId: "VLx" } })], { cursor: "c.2" }))
  assert.deepEqual(merged.sections.map(s => [s.title, s.items.length]), [["One", 1], ["Two", 1], ["Four", 0]])
  const flat = Model.appendPage({ items: [album("A")], cursor: "c.1" }, { items: [album("A"), album("B")], cursor: null })
  assert.deepEqual(flat.items.map(a => a.browseId), ["A", "B"])
  assert.equal(flat.sections, undefined)
  assert.equal(Model.appendPage(null, flat), flat)
  assert.equal(Model.appendPage(flat, null), flat)
})

test("appendPage stops at the item cap", () => {
  const merged = Model.appendPage(page([section(null, [tr("a"), tr("b")])], { cursor: "c.1" }),
    page([section(null, [tr("c"), tr("d"), tr("e")])], { cursor: "c.2" }), 3)
  assert.deepEqual(merged.sections[0].items.map(t => t.videoId), ["a", "b", "c"])
  assert.equal(merged.cursor, null)
  assert.equal(merged.hasMore, false)
  assert.equal(merged.capped, true)
  assert.equal(Model.pageRows(merged).at(-1).item.type, "track", "no Load more row once capped")
})

test("rowPlace and findPlace tell repeated rows apart", () => {
  const rows = [{ header: true, title: "Today" }, { item: tr("a") }, { item: tr("b") }, { item: tr("a") }]
  assert.deepEqual(Model.rowPlace(rows, 3), { key: "track:a", n: 1 })
  assert.deepEqual(Model.rowPlaces(rows).map(p => p.n), [0, 0, 0, 1])
  assert.equal(Model.findPlace(rows, { key: "track:a", n: 1 }), 3)
  assert.equal(Model.findPlace(rows, { key: "track:a", n: 5 }), 3, "fewer now: the last one")
  assert.equal(Model.findPlace(rows, { key: "track:z", n: 0 }), -1)
  assert.equal(Model.findPlace(rows, null), -1)
  assert.equal(Model.rowPlace(rows, 9), null)
})

test("cursorAfterRows keeps the row after an insert above it", () => {
  const before = [{ header: true, title: "S" }, { item: tr("a") }, { item: tr("b") }, { item: tr("c") }]
  const after = [{ header: true, title: "S" }, { item: tr("new") }, { item: tr("a") }, { item: tr("b") }, { item: tr("c") }]
  assert.equal(Model.cursorAfterRows(before, after, 2), 3)
  assert.equal(Model.cursorAfterRows(before, after, -1), -1)
  assert.equal(Model.cursorAfterRows(before, [], 2), -1)
})

test("cursorAfterRows after a removal moves to the next row, else the previous one", () => {
  const before = [{ item: tr("a", { setVideoId: "1" }) }, { item: tr("a", { setVideoId: "2" }) }, { item: tr("c") }]
  const removed = [{ item: tr("a", { setVideoId: "1" }) }, { item: tr("c") }]
  assert.equal(Model.cursorAfterRows(before, removed, 1), 1, "the following row")
  assert.equal(Model.cursorAfterRows(before, [{ item: tr("a", { setVideoId: "1" }) }, { item: tr("a", { setVideoId: "2" }) }], 2), 1,
    "the last row went: the one before it")
  const headers = [{ header: true, title: "X" }, { item: tr("a") }, { header: true, title: "Y" }, { item: tr("b") }]
  assert.equal(Model.cursorAfterRows(headers, [{ header: true, title: "X" }, { header: true, title: "Y" }, { item: tr("b") }], 1), 2,
    "never onto a header")
})

test("cursorAfterRows after a replacement: by key, by place among repeats, or nowhere", () => {
  const before = [{ item: tr("a") }, { item: tr("b") }, { item: tr("a") }]
  const reordered = [{ item: tr("b") }, { item: tr("a") }, { item: tr("x") }, { item: tr("a") }]
  assert.equal(Model.cursorAfterRows(before, reordered, 1), 0)
  assert.equal(Model.cursorAfterRows(before, reordered, 2), 3, "the second listing stays the second")
  assert.equal(Model.cursorAfterRows(before, [{ item: tr("y") }, { item: tr("z") }], 1), -1, "a different list")
  // Stale-while-revalidate: the fresh copy replaces the cached one.
  const cached = Model.pageRows(page([section("S", [tr("a"), tr("b"), tr("c")])], { hasMore: true }))
  const fresh = Model.pageRows(page([section("S", [tr("z"), tr("a"), tr("b"), tr("c")])], { cursor: "c.1" }))
  assert.equal(Model.cursorAfterRows(cached, fresh, 3), 4)
  assert.equal(Model.cursorAfterRows(cached, fresh, 4), 5, "the Load more row with nothing new before it")
})

test("cursorAfterRows from the Load more row lands on the first new row", () => {
  const first = page([section("S", [tr("a"), tr("b")])], { cursor: "c.1" })
  const rows = Model.pageRows(first)
  const next = Model.appendPage(first, page([section(null, [tr("a"), tr("c")])], { cursor: "c.2" }))
  const rowsAfter = Model.pageRows(next)
  assert.equal(rows[3].item.type, "loadMore")
  assert.equal(Model.cursorAfterRows(rows, rowsAfter, 3), 3, "the repeated track a is new: history may repeat")
  assert.equal(rowsAfter[3].item.videoId, "a")
  const same = Model.appendPage(first, page([section(null, [])], { cursor: "c.2" }))
  assert.equal(Model.cursorAfterRows(rows, Model.pageRows(same), 3), 3, "nothing new: stays on Load more")
  const ended = Model.appendPage(first, page([section(null, [])], { cursor: null }))
  assert.equal(Model.cursorAfterRows(rows, Model.pageRows(ended), 3), 2, "no more: the last row")
})

test("cursorAfterRows from Load more stays there when a refresh replaced the last item", () => {
  const ids = n => Array.from({ length: n }, (_, i) => tr("a" + i))
  // The disk copy of a 100-item page, then the fresh one with a new item on
  // top that pushed a99 onto page 2.
  const cached = Model.pageRows(page([section("S", ids(100))], { hasMore: true }))
  const fresh = Model.pageRows(page([section("S", [tr("new")].concat(ids(99)))], { cursor: "c.1" }))
  assert.equal(cached.length, 102)
  assert.equal(cached[101].item.type, "loadMore")
  assert.equal(Model.cursorAfterRows(cached, fresh, 101), 101, "not the new item at the top")
  const before = Model.pageRows(page([section(null, [tr("a"), tr("b"), tr("c"), tr("d"), tr("e")])], { hasMore: true }))
  const after = Model.pageRows(page([section(null, [tr("a2"), tr("b"), tr("c"), tr("d"), tr("e2")])], { cursor: "c.1" }))
  assert.equal(Model.cursorAfterRows(before, after, 5), 5)
  // Nothing came before Load more: every row after it is new.
  const empty = Model.pageRows(page([section(null, [])], { cursor: "c.1" }))
  assert.deepEqual(empty.map(label), ["loadMore:Load more"])
  const appended = Model.pageRows(page([section(null, [tr("x"), tr("y")])], { cursor: "c.2" }))
  assert.equal(Model.cursorAfterRows(empty, appended, 0), 0)
})

test("cursorAfterRows from Load more: new items land before the last section's See all row", () => {
  const more = { browseId: "VLPL1", params: null, pageType: null }
  const first = page([section("S", [tr("a"), tr("b")], { more })], { cursor: "c.1" })
  const rows = Model.pageRows(first)
  assert.deepEqual(rows.map(r => r.item ? r.item.type : "#"), ["#", "track", "track", "more", "loadMore"])
  const next = Model.appendPage(first, page([section(null, [tr("c")]), section("T", [album("X")])], { cursor: "c.2" }))
  const rowsAfter = Model.pageRows(next)
  assert.deepEqual(rowsAfter.map(label), ["# S", "track:a", "track:b", "track:c", "more:See all S", "# T", "album:X", "loadMore:Load more"])
  assert.equal(Model.cursorAfterRows(rows, rowsAfter, 4), 3)
  const titled = Model.appendPage(first, page([section("T", [album("X")])], { cursor: "c.2" }))
  assert.equal(Model.cursorAfterRows(rows, Model.pageRows(titled), 4), 5, "past the known See all row and the new header")
})

test("prefs v2: every key has a default", () => {
  assert.deepEqual(Model.parsePrefs(""), {
    autoplay: true, shuffle: false, repeat: "off",
    libraryChip: "playlists", libraryOrders: {}, chartsCountry: "ZZ", location: "", quality: "high",
    recordHistory: false, speed: { music: 1, episode: 1 }, normalize: "off", nowPane: "queue",
  })
  assert.deepEqual(Model.parsePrefs("[1]"), Model.parsePrefs("not json"))
})

test("prefs v2 read version 1 files", () => {
  const v1 = Model.parsePrefs('{"version":1,"autoplay":false,"shuffle":true,"repeat":"all"}\n')
  assert.equal(v1.autoplay, false)
  assert.equal(v1.shuffle, true)
  assert.equal(v1.repeat, "all")
  assert.equal(v1.quality, "high")
  assert.deepEqual(v1.speed, { music: 1, episode: 1 })
})

test("prefs v2 keep valid values and replace invalid ones", () => {
  const saved = Model.parsePrefs(JSON.stringify({
    version: 2, libraryChip: "albums", libraryOrders: { albums: "a_to_z", songs: 2, "Bad Key": "x", artists: -1, landing: "" },
    chartsCountry: "AU", location: "DE", quality: "low", recordHistory: true,
    speed: { music: 1.25, episode: 9 }, normalize: "dynamic", nowPane: "lyrics", unknown: 1,
  }))
  assert.equal(saved.libraryChip, "albums")
  assert.deepEqual(saved.libraryOrders, { albums: "a_to_z", songs: 2 })
  assert.equal(saved.chartsCountry, "AU")
  assert.equal(saved.location, "DE")
  assert.equal(saved.quality, "low")
  assert.equal(saved.recordHistory, true)
  assert.deepEqual(saved.speed, { music: 1.25, episode: 1 })
  assert.equal(saved.normalize, "dynamic")
  assert.equal(saved.nowPane, "lyrics")
  assert.equal("unknown" in saved, false)
  const bad = Model.parsePrefs(JSON.stringify({ libraryChip: "x y", chartsCountry: "zz", location: "Australia",
    quality: "best", recordHistory: "yes", speed: "fast", normalize: true, nowPane: "art", libraryOrders: [1] }))
  const defaults = Model.parsePrefs("")
  for (const key of ["libraryChip", "chartsCountry", "location", "quality", "recordHistory", "speed", "normalize", "nowPane", "libraryOrders"])
    assert.deepEqual(bad[key], defaults[key], key)
})

test("withPref copies, serializePrefs round-trips as version 2", () => {
  const prefs = Model.parsePrefs("")
  const next = Model.withPref(prefs, "nowPane", "related")
  assert.notEqual(next, prefs)
  assert.equal(prefs.nowPane, "queue")
  assert.equal(next.nowPane, "related")
  assert.equal(Model.withPref(prefs, "quality", "ultra").quality, "high", "invalid values become the default")
  assert.deepEqual(Model.withPref(prefs, "nope", 1), prefs, "unknown keys are ignored")
  assert.equal(Model.isPrefKey("speed"), true)
  assert.equal(Model.isPrefKey("toString"), false)
  assert.equal(Model.normalizePref("repeat", "one"), "one")
  const text = Model.serializePrefs(next)
  assert.ok(text.endsWith("\n"))
  assert.equal(JSON.parse(text).version, 2)
  assert.deepEqual(Model.parsePrefs(text), next)
})
