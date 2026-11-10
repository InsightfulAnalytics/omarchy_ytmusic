// Search (S7): groups in YouTube's order with Show all rows, filtered lists
// with Load more, spelling rows, the More chooser and scope chip, past
// searches merged with recent ones, and rich suggestion rows.
const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const song = (id, extra = {}) => ({ type: "track", videoId: id, title: "Song " + id, artistText: "A", available: true, ...extra })
const label = r => r.header ? "# " + r.title : r.item.type === "action" ? "> " + r.item.title : r.item.type

test("flattenSearch lists the new groups in served order, each with Show all", () => {
  const result = {
    top: [{ type: "artist", channelId: "UCr", name: "Radiohead", shuffleId: "RDAO", radioId: "RDEM" }, song("t1")],
    songs: [song("s1")], videos: [], albums: [], playlists: [], artists: [],
    podcasts: [{ type: "podcast", browseId: "MPSPx", title: "Song Exploder" }],
    episodes: [song("e1", { kind: "episode" })],
    profiles: [{ type: "profile", channelId: "UCp", name: "Someone", handle: "@someone" }],
    order: ["podcasts", "songs", "episodes", "profiles"]
  }
  const rows = Model.flattenSearch(result, "")
  assert.deepEqual(rows.map(label), [
    "# Top result", "artist", "track",
    "# Podcasts", "podcast", "> Show all podcasts",
    "# Songs", "track", "> Show all songs",
    "# Episodes", "track", "> Show all episodes",
    "# Profiles", "profile", "> Show all profiles"
  ])
  const showAll = rows.filter(r => r.item && r.item.type === "action")
  assert.deepEqual(showAll.map(r => Model.showAllFilter(r.item)), ["podcasts", "songs", "episodes", "profiles"])
  assert.equal(new Set(showAll.map(r => Model.itemKey(r.item))).size, 4, "each Show all row keeps its own place")
  assert.equal(Model.showAllFilter({ type: "action", id: "other" }), "")
  // Without `order` (an older reply), the old fixed order.
  assert.deepEqual(Model.flattenSearch({ songs: [song("a")], albums: [{ type: "album", browseId: "MPRE1" }] }).map(label),
    ["# Songs", "track", "> Show all songs", "# Albums", "album", "> Show all albums"])
  assert.equal(Model.searchResultCount(result), 6)
})

test("Show all only offers a filter the scope allows", () => {
  const result = { songs: [song("a")], playlists: [{ type: "playlist", playlistId: "PL1" }], order: ["songs", "playlists"] }
  const library = Model.flattenSearch(result, "", { scope: "library" }).map(label)
  assert.ok(library.includes("> Show all songs") && library.includes("> Show all playlists"))
  assert.equal(Model.searchFilterAllowed("community_playlists", "library"), false)
  assert.equal(Model.searchFilterAllowed("featured_playlists", "library"), false)
  assert.equal(Model.searchFilterAllowed("songs", "uploads"), false)
  assert.equal(Model.searchFilterAllowed("", "uploads"), true)
  assert.equal(Model.searchFilterAllowed("podcasts", ""), true)
  assert.equal(Model.searchFilterAllowed("nope", ""), false)
})

test("a filtered list ends in Load more while it has a cursor; uploads are a list", () => {
  const rows = Model.flattenSearch({ items: [song("a"), song("b")], cursor: "c.T", hasMore: true }, "songs")
  assert.deepEqual(rows.map(label), ["# Songs", "track", "track", "loadMore"])
  assert.deepEqual(Model.flattenSearch({ items: [song("a")], cursor: null, hasMore: false }, "songs").map(label), ["# Songs", "track"])
  // G1: a capped list says so instead of ending silently.
  assert.deepEqual(Model.flattenSearch({ items: [song("a")], cursor: null, capped: true }, "songs").map(label),
    ["# Songs", "track", "message"])
  const uploads = Model.flattenSearch({ items: [{ type: "album", browseId: "FEmusic_library_privately_owned_release_detailb_x",
                                                  upload: true, entityId: "b_x", title: "Demo", artists: [], artistText: "Me" },
                                                song("u1")], cursor: null }, "", { scope: "uploads" })
  assert.deepEqual(uploads.map(label), ["# Uploads", "album", "track"])
  // S10: an uploaded album opens as its upload album page, with the uploads menu (Delete upload).
  assert.deepEqual([Model.pageRef(uploads[1].item).method, Model.pageRef(uploads[1].item).params],
                   ["uploadAlbum", { browseId: "FEmusic_library_privately_owned_release_detailb_x" }])
  assert.ok(Model.menuActions(uploads[1].item, { signedIn: true, pageKind: "search" }).some(a => a.id === "deleteUpload"))
  // G1 with S10: an uploads search that reached the Store's cap says so too.
  const cappedUploads = Model.flattenSearch({ items: [song("u1")], cursor: null, capped: true }, "", { scope: "uploads" })
  assert.deepEqual(cappedUploads.map(label), ["# Uploads", "track", "message"])
  assert.equal(cappedUploads[2].item.title, "Showing the first 1")
  // appendPage grows a filtered result (under appendSearchPage, Store.searchMore).
  const more = Model.appendPage({ items: [song("a")], cursor: "c.1", spelling: null }, { items: [song("b")], cursor: null }, 500)
  assert.deepEqual(more.items.map(t => t.videoId), ["a", "b"])
  assert.equal(more.hasMore, false)
})

test("spelling: Search instead for (exact) and Did you mean", () => {
  const corrected = { items: [song("a")], spelling: { kind: "corrected", corrected: "radiohead", original: "radiohed" } }
  const rows = Model.flattenSearch(corrected, "songs")
  assert.deepEqual(rows[0].item, { type: "action", id: Model.SEARCH_EXACT, title: "Search instead for \"radiohed\"", icon: "󰓆", query: "radiohed" })
  assert.equal(Model.spellingStatus(corrected), "Showing results for radiohead")
  const did = { top: [], songs: [song("a")], order: ["songs"], spelling: { kind: "didYouMean", corrected: "radiohead", original: "radiohed" } }
  const didRows = Model.flattenSearch(did, "")
  assert.deepEqual([didRows[0].item.id, didRows[0].item.query, didRows[0].item.title], [Model.SEARCH_CORRECTED, "radiohead", "Did you mean \"radiohead\"?"])
  assert.equal(Model.spellingStatus(did), "")
  assert.equal(Model.spellingStatus({ items: [] }), "")
})

test("searchKey keeps scope and exact apart, and a plain search keeps its old key", () => {
  assert.equal(Model.searchKey("Radiohead", ""), "|radiohead")
  assert.equal(Model.searchKey("Radiohead", "", "", false), "|radiohead")
  const keys = [Model.searchKey("x", ""), Model.searchKey("x", "", "library"), Model.searchKey("x", "", "uploads"),
                Model.searchKey("x", "", "", true), Model.searchKey("x", "songs", "library", true), Model.searchKey("x", "songs", "library")]
  assert.equal(new Set(keys).size, keys.length)
})

test("chips: More shows the chosen filter; h / l cycle every allowed filter", () => {
  const titles = chips => chips.map(c => c.title + (c.selected ? "*" : "") + (c.enabled ? "" : "-"))
  assert.deepEqual(titles(Model.searchChips("", "")), ["All*", "Songs", "Albums", "Artists", "Playlists", "More"])
  assert.deepEqual(titles(Model.searchChips("podcasts", "")), ["All", "Songs", "Albums", "Artists", "Playlists", "Podcasts*"])
  assert.deepEqual(titles(Model.searchChips("songs", "")), ["All", "Songs*", "Albums", "Artists", "Playlists", "More"])
  assert.deepEqual(titles(Model.searchChips("", "uploads")), ["All*", "Songs-", "Albums-", "Artists-", "Playlists-", "More-"])
  assert.equal(Model.searchChips("", "").pop().key, Model.SEARCH_MORE_CHIP)
  const choices = Model.moreFilterChoices("episodes", "")
  assert.deepEqual(choices.map(c => c.id), ["videos", "featured_playlists", "community_playlists", "podcasts", "episodes", "profiles"])
  assert.deepEqual(choices.filter(c => c.icon === "󰄬").map(c => c.id), ["episodes"], "the current one is ticked")
  assert.deepEqual(Model.moreFilterChoices("", "library").map(c => c.id), ["videos", "podcasts", "episodes", "profiles"])
  assert.equal(Model.nextSearchFilter("", "", 1), "songs")
  assert.equal(Model.nextSearchFilter("", "", -1), "profiles", "wraps")
  assert.equal(Model.nextSearchFilter("playlists", "library", 1), "videos")
  assert.equal(Model.nextSearchFilter("videos", "library", 1), "podcasts", "library skips featured and community")
  assert.equal(Model.nextSearchFilter("community_playlists", "library", 1), "songs", "an unknown current counts as All")
  assert.deepEqual(["", "library", "uploads"].map(Model.nextSearchScope), ["library", "uploads", ""])
  assert.deepEqual(["", "library", "uploads", "x"].map(Model.searchScopeTitle), ["YouTube Music", "Library", "Uploads", "YouTube Music"])
})

test("the page menu offers the other scopes signed in, and More filters", () => {
  const ids = ctx => Model.menuActions(null, Object.assign({ page: true, pageKind: "search" }, ctx)).map(a => a.id)
  assert.deepEqual(ids({ signedIn: true, scope: "" }), ["searchMoreFilters", "searchScope:library", "searchScope:uploads"])
  assert.deepEqual(ids({ signedIn: true, scope: "uploads" }), ["searchScope:", "searchScope:library"])
  assert.deepEqual(ids({ signedIn: false, scope: "" }), ["searchMoreFilters"])
  assert.deepEqual(Model.menuActions(null, { page: true, pageKind: "home" }).map(a => a.id).filter(id => id.indexOf("search") === 0), [])
  const calls = []
  const owner = { setScope: s => calls.push(["scope", s]), openMoreFilters: () => calls.push(["more"]) }
  Model.menuAction("searchScope:library").run({ owner })
  Model.menuAction("searchMoreFilters").run({ owner })
  assert.deepEqual(calls, [["scope", "library"], ["more"]])
})

test("past searches: recent ones first, YouTube's merged without repeats, all removable", () => {
  const rows = Model.pastSearchRows(["Daft Punk", "air"], [{ text: "daft punk" }, { text: "nils frahm" }, "boards"], 10)
  assert.deepEqual(rows[0], { header: true, title: "Recent searches" })
  assert.deepEqual(rows.slice(1).map(r => [r.item.text, !!r.item.remote, r.item.recent, r.item.removable]), [
    ["Daft Punk", true, true, true], ["air", false, true, true], ["nils frahm", true, true, true], ["boards", true, true, true]
  ])
  assert.equal(Model.pastSearchRows(["a", "b", "c"], [{ text: "d" }], 2).length, 3, "header and two")
  assert.deepEqual(Model.pastSearchRows([], [], 10), [])
  assert.deepEqual(Model.pastSearchRows(["only here"], null, 10).slice(1).map(r => r.item.remote), [undefined])
})

test("typing rows: completions, then up to five rich rows", () => {
  const entities = [{ type: "artist", channelId: "UCr", name: "Radiohead" }, song("a"), song("b"), song("c"), song("d"), song("e")]
  const rows = Model.typingRows([{ text: "radiohead", recent: false }], -1, entities)
  assert.deepEqual(rows.map(label), ["# Suggestions", "query", "artist", "track", "track", "track", "track"])
  assert.ok(rows.slice(2).every(r => r.suggestion === true))
  assert.deepEqual(Model.typingRows([], -1, [song("a")]).map(label), ["# Suggestions", "track"])
  assert.deepEqual(Model.typingRows([], -1, []), [])
  assert.deepEqual(Model.typingRows([], -1, null), [])
})

test("a remote completion that repeats a recent search is marked remote", () => {
  const list = Model.suggestionList(["radiohead creep"], [{ text: "Radiohead Creep", fromHistory: true }, { text: "radiohead" }], "radio", 5)
  assert.deepEqual(list, [{ text: "radiohead creep", recent: true, remote: true }, { text: "radiohead", recent: false }])
})

test("searchMore's page drops what the list already has, then appends as appendPage does", () => {
  const listed = { items: [song("a"), song("b"), { type: "album", browseId: "MPREx", title: "X" }], cursor: "c.1", hasMore: true }
  const next = { items: [song("b"), song("c"), song("c"), { type: "album", browseId: "MPREx", title: "X" }, song("d")], cursor: "c.2", hasMore: true }
  const out = Model.appendSearchPage(listed, next, 500)
  assert.deepEqual(out.items.map(t => t.videoId || t.browseId), ["a", "b", "MPREx", "c", "d"])
  assert.equal(out.cursor, "c.2")
  assert.equal(out.hasMore, true)
  assert.equal(listed.items.length, 3, "the held result is not changed")
  const capped = Model.appendSearchPage(listed, next, 4)
  assert.deepEqual(capped.items.map(t => t.videoId || t.browseId), ["a", "b", "MPREx", "c"])
  assert.equal(capped.cursor, null, "the cap ends the list")
  const last = Model.appendSearchPage(listed, { items: [song("a")], cursor: null, hasMore: false }, 500)
  assert.equal(last.items.length, 3)
  assert.equal(last.hasMore, false)
})
