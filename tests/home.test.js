const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const tr = (videoId, extra = {}) => ({ type: "track", videoId, title: "Song " + videoId, ...extra })
const album = browseId => ({ type: "album", browseId, title: "Album " + browseId })
const playlist = playlistId => ({ type: "playlist", playlistId, title: "List " + playlistId })
const label = row => row.header ? "# " + row.title : row.item.type + ":" + (row.item.videoId || row.item.browseId
  || row.item.channelId || row.item.playlistId || row.item.title || "")
const shelf = (title, items, extra = {}) => ({ type: "section", title, items, more: null, play: null, layout: "carousel", ...extra })

const LISTEN_AGAIN = { browseId: "FEmusic_listen_again", params: "la", pageType: null }
const COVERS_PLAY = { videoId: "v1", playlistId: null, params: "wAEB" }

test("home rows: shelves, See all and Play all rows, Load more", () => {
  const shelves = [
    shelf("Listen again", [album("MPREb_1"), tr("a")], { more: LISTEN_AGAIN }),
    shelf("Empty", []),
    shelf("Covers and remixes", [tr("v1"), tr("v2", { kind: "video" })], { play: COVERS_PLAY }),
    shelf(null, [playlist("RDTMAK5uy_x")]),
  ]
  const rows = Model.homeRows(shelves, { more: true })
  assert.deepEqual(rows.map(label), [
    "# Listen again", "album:MPREb_1", "track:a", "more:See all Listen again",
    "# Covers and remixes", "track:v1", "track:v2", "action:Play all Covers and remixes",
    "playlist:RDTMAK5uy_x",
    "loadMore:Load more",
  ])
  const more = rows[3]
  assert.equal(more.shelfIndex, 0)
  assert.deepEqual(more.item.endpoint, LISTEN_AGAIN)
  assert.equal(more.item.sectionTitle, "Listen again")
  const playAll = rows[7]
  assert.ok(Model.isPlayAllRow(playAll.item) && playAll.shelfIndex === 2)
  assert.ok(!Model.isPlayAllRow(more.item) && !Model.isPlayAllRow({ type: "action", id: "other" }))
  assert.equal(playAll.item.icon, "󰐊")
  // Every row the cursor can rest on is selectable, and the two line rows have their own keys.
  for (const row of rows) if (!row.header) assert.ok(Model.selectableRow(row), label(row))
  assert.notEqual(Model.itemKey(more.item), Model.itemKey(playAll.item))
  // Tracks are numbered within their shelf, so Enter plays the shelf from there.
  assert.deepEqual(rows.filter(r => r.trackIndex !== undefined).map(r => [r.shelfIndex, r.trackIndex]), [[0, 0], [2, 0], [2, 1]])
  assert.deepEqual(Model.shelfTracks(shelves, 2).map(t => t.videoId), ["v1", "v2"])
  assert.deepEqual(Model.homeRows(shelves).map(label).slice(-1), ["playlist:RDTMAK5uy_x"], "no Load more without more")
})

test("home rows: a shelf with only a See all still shows it; two Play all rows keep apart", () => {
  const rows = Model.homeRows([
    shelf("Recaps", [], { more: { browseId: "UCx", params: "recap", pageType: "MUSIC_PAGE_TYPE_USER_CHANNEL" } }),
    shelf("A", [tr("a")], { play: { videoId: "a", params: "p" } }),
    shelf("B", [tr("b")], { play: { videoId: "b", params: "p" } }),
  ])
  assert.deepEqual(rows.map(label), ["# Recaps", "more:See all Recaps", "# A", "track:a", "action:Play all A",
    "# B", "track:b", "action:Play all B"])
  assert.notEqual(Model.itemKey(rows[4].item), Model.itemKey(rows[7].item))
  // The cursor stays on B's Play all when shelves are added before it.
  const grown = Model.homeRows([shelf("New", [tr("n")])].concat([
    shelf("Recaps", [], { more: { browseId: "UCx", params: "recap", pageType: "MUSIC_PAGE_TYPE_USER_CHANNEL" } }),
    shelf("A", [tr("a")], { play: { videoId: "a", params: "p" } }),
    shelf("B", [tr("b")], { play: { videoId: "b", params: "p" } }),
  ]))
  assert.equal(label(grown[Model.cursorAfterRows(rows, grown, 7)]), "action:Play all B")
})

test("Play all params come from the shelf's play endpoint", () => {
  assert.deepEqual(Model.shelfQueueParams(shelf("A", [], { play: COVERS_PLAY })), { videoId: "v1", params: "wAEB" })
  assert.deepEqual(Model.shelfQueueParams(shelf("A", [], { play: { playlistId: "RDx", params: null } })), { playlistId: "RDx" })
  assert.equal(Model.shelfQueueParams(shelf("A", [], { play: { params: "p" } })), null)
  assert.equal(Model.shelfQueueParams(shelf("A", [])), null)
  assert.equal(Model.shelfQueueParams(null), null)
  assert.deepEqual(Model.shelfContext(shelf("Quick picks", [])), { playlistId: null, title: "Quick picks", radio: false })
})

test("See all routes by endpoint", () => {
  const target = (endpoint, title = "Shelf") => {
    const row = Model.homeRows([shelf(title, [tr("x")], { more: endpoint })]).find(r => r.item && r.item.type === "more")
    return Model.moreTarget(row.item)
  }
  assert.deepEqual(target({ browseId: "VLPLabc", params: null }), { type: "playlist", playlistId: "PLabc", title: "Shelf" })
  assert.deepEqual(target({ browseId: "MPADUCx", params: "disc" }), { type: "artistAlbums", browseId: "MPADUCx", params: "disc", title: "Shelf" })
  assert.deepEqual(target({ browseId: "UCsimilar", params: null, pageType: "MUSIC_PAGE_TYPE_ARTIST" }, "Similar"),
    { type: "artist", channelId: "UCsimilar", name: "Similar" })
  // Everything else is a browse page, which decides by renderer.
  for (const browseId of ["FEmusic_listen_again", "FEmusic_mixed_for_you", "FEmusic_new_releases_albums",
                          "FEmusic_moods_and_genres_category", "FEmusic_library_landing"]) {
    assert.deepEqual(target({ browseId, params: "p" }), { type: "browse", browseId, params: "p", title: "Shelf" }, browseId)
  }
  // Recaps: a channel with params is a browse page, not the channel's own page.
  const recaps = target({ browseId: "UCrecap", params: "rp", pageType: "MUSIC_PAGE_TYPE_USER_CHANNEL" }, "Recaps")
  assert.deepEqual(recaps, { type: "browse", browseId: "UCrecap", params: "rp", title: "Recaps" })
  const ref = Model.pageRef(recaps)
  assert.equal(ref.method, "browsePage")
  assert.deepEqual(ref.params, { browseId: "UCrecap", params: "rp" })
  // ... also once a profile route for user channels exists (S9).
  Model.ROUTES.push({ pageType: "MUSIC_PAGE_TYPE_USER_CHANNEL", target: (ep, id) => ({ type: "profile", channelId: id }) })
  try {
    assert.equal(target({ browseId: "UCrecap", params: "rp", pageType: "MUSIC_PAGE_TYPE_USER_CHANNEL" }).type, "browse")
    assert.equal(target({ browseId: "UCuser", params: null, pageType: "MUSIC_PAGE_TYPE_USER_CHANNEL" }).type, "profile")
  } finally {
    Model.ROUTES.pop()
  }
  // Mixes open as mix pages and play through their watch playlist.
  assert.equal(Model.pageKindOf(playlist("RDTMAK5uy_mix")), "mix")
  assert.equal(Model.menuKind(playlist("RDTMAK5uy_mix")), "mix")
  assert.deepEqual(Model.tracksForParams(playlist("RDTMAK5uy_mix")), { kind: "mix", id: "RDTMAK5uy_mix" })
  // Episodes play as songs do.
  assert.equal(Model.menuKind(tr("ep1", { kind: "episode" })), "track")
})

test("chips: All first, h / l cycle with wrap, chips without params are left out", () => {
  const served = [
    { title: "Relax", params: "r", selected: false },
    { title: "Broken", params: "", selected: false },
    { title: "Focus", params: "f", selected: true },
  ]
  const chips = Model.homeChipList(served)
  assert.deepEqual(chips, [{ title: "All", params: "" }, { title: "Relax", params: "r" }, { title: "Focus", params: "f" }])
  assert.equal(Model.nextHomeChip(chips, "", 1), "r")
  assert.equal(Model.nextHomeChip(chips, "r", 1), "f")
  assert.equal(Model.nextHomeChip(chips, "f", 1), "", "wraps to All")
  assert.equal(Model.nextHomeChip(chips, "", -1), "f", "wraps back")
  assert.equal(Model.nextHomeChip(chips, "gone", 1), "r", "an unknown chip counts as All")
  assert.deepEqual(Model.homeChipList(null), [{ title: "All", params: "" }])
  assert.equal(Model.nextHomeChip([], "", 1), "", "only All: stays")
})

test("appending pages of shelves stops at the cap", () => {
  const page = n => Array.from({ length: 3 }, (_, i) => shelf("S" + (n * 3 + i), [tr("t" + n + i)]))
  let shelves = []
  let capped = false
  for (let n = 0; n < 20 && !capped; n++) ({ shelves, capped } = Model.appendShelves(shelves, page(n), 40))
  assert.equal(shelves.length, 40)
  assert.ok(capped)
  const some = Model.appendShelves(page(0), page(1), 40)
  assert.deepEqual(some.shelves.map(s => s.title), ["S0", "S1", "S2", "S3", "S4", "S5"])
  assert.equal(some.capped, false)
  assert.deepEqual(Model.appendShelves(page(0), null, 40).shelves.length, 3)
})

test("the Load more row of Home keeps the cursor on the first new shelf", () => {
  const first = [shelf("A", [tr("a1"), tr("a2")])]
  const old = Model.homeRows(first, { more: true })
  const next = Model.homeRows(first.concat([shelf("B", [tr("b1")])]), { more: true })
  const at = old.findIndex(r => r.item && r.item.type === "loadMore")
  assert.equal(label(next[Model.cursorAfterRows(old, next, at)]), "track:b1")
})

test("Play all keeps its row when YouTube serves new params for the same shelf", () => {
  const before = Model.homeRows([shelf("Covers", [tr("v1")], { play: { videoId: "v1", playlistId: "RDx", params: "p1" } })])
  const after = Model.homeRows([shelf("Covers", [tr("v1")], { play: { videoId: "v1", playlistId: "RDx", params: "p2" } })])
  const at = before.findIndex(r => r.item && Model.isPlayAllRow(r.item))
  assert.equal(Model.itemKey(before[at].item), Model.itemKey(after[at].item))
  assert.equal(Model.cursorAfterRows(before, after, at), at)
  // Another list is another row.
  const other = Model.homeRows([shelf("Covers", [tr("v1")], { play: { videoId: "v1", playlistId: "RDy", params: "p1" } })])
  assert.notEqual(Model.itemKey(before[at].item), Model.itemKey(other[at].item))
})

test("a shelf's strapline heads it; its rows keep the title", () => {
  const artist = { browseId: "UCnils", params: null, pageType: "MUSIC_PAGE_TYPE_ARTIST" }
  const rows = Model.homeRows([shelf("Nils Frahm", [album("MPREb_1")], { strapline: "SIMILAR TO", more: artist })])
  assert.deepEqual(rows.map(label), ["# SIMILAR TO · Nils Frahm", "album:MPREb_1", "more:See all Nils Frahm"])
  assert.equal(rows[2].item.sectionTitle, "Nils Frahm")
})

test("a podcast opens as its browse page until S8", () => {
  const ref = Model.pageRef({ type: "podcast", browseId: "MPSPPLx", title: "A show" })
  assert.equal(ref.kind, "podcast")
  assert.equal(ref.method, "browsePage")
  assert.deepEqual(ref.params, { browseId: "MPSPPLx" })
  assert.equal(ref.id, "MPSPPLx")
  assert.equal(Model.pageRef({ type: "podcast" }), null, "no browseId")
  assert.equal(Model.pageKindTitle(ref), "Podcast")
})
