const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const tr = (videoId, extra = {}) => ({ type: "track", videoId, title: "Song " + videoId, artistText: "Artist",
  artists: [{ name: "Artist", id: "UCa" }], album: { name: "Album", id: "MPREb_a" }, ...extra })
const tracks = (n, prefix = "v") => Array.from({ length: n }, (_, i) => tr(prefix + i, { setVideoId: "S" + prefix + i }))
const label = row => row.header ? "# " + row.title : row.item.type + ":" + (row.item.id || row.item.videoId
  || row.item.browseId || row.item.playlistId || row.item.title || "")
const collection = (extra = {}) => ({ type: "collection", kind: "playlist", id: "PLx", playlistId: "PLx", title: "Mine",
  subtitle: "", owned: false, editable: false, trackCount: 3, truncated: false, capped: false, unavailable: 0,
  thumb: null, thumbLarge: null, header: {}, tracks: tracks(3), sections: [], ...extra })

test("filterTracks: title, artists and album, ignoring case and accents, every word", () => {
  const list = [
    tr("a", { title: "Crazy in Love", artistText: "Beyoncé", artists: [{ name: "Beyoncé", id: "UCb" }] }),
    tr("b", { title: "Radio Ga Ga", artistText: "Queen", album: { name: "The Works", id: null } }),
    tr("c", { title: "Talk Show Host", artistText: "Radiohead", album: { name: "Romeo + Juliet", id: null } }),
    tr("d", { title: "Señorita", artistText: "Shawn Mendes", album: { name: "ÉTÉ RADIO", id: null } }),
  ]
  const ids = text => Model.filterTracks(list, text).map(t => t.videoId)
  assert.deepEqual(ids("radio"), ["b", "c", "d"], "title, artist and album (with accents)")
  assert.deepEqual(ids("BEYONCE"), ["a"])
  assert.deepEqual(ids("senorita"), ["d"])
  assert.deepEqual(ids("ete"), ["d"])
  assert.deepEqual(ids("radio queen"), ["b"], "every word, anywhere")
  assert.deepEqual(ids("juliet talk"), ["c"])
  assert.deepEqual(ids("nothing here"), [])
  assert.deepEqual(ids("   "), ["a", "b", "c", "d"], "an empty text keeps them all")
  // Artists from the list when there is no artistText (an item that went through QML).
  assert.deepEqual(Model.filterTracks([{ type: "track", videoId: "x", title: "T", artists: [{ name: "Björk" }] }], "bjork")
    .map(t => t.videoId), ["x"])
  assert.equal(Model.foldText("Ångström Été"), "angstrom ete")
})

test("find rows: matches keep their place in the whole list; history keeps its groups", () => {
  const list = [tr("a", { title: "One", played: "Today" }), tr("b", { title: "Two", played: "Today" }),
                tr("c", { title: "Two again", played: "Yesterday" })]
  const rows = Model.findRows(list, "two", false)
  assert.deepEqual(rows.map(r => [r.item.videoId, r.trackIndex]), [["b", 1], ["c", 2]])
  const grouped = Model.findRows(list, "two", true)
  assert.deepEqual(grouped.map(label), ["# Today", "track:b", "# Yesterday", "track:c"])
  const none = Model.findRows(list, " zzz ", false)
  assert.equal(none.length, 1)
  assert.equal(none[0].item.type, "message")
  assert.equal(none[0].item.title, 'No songs match "zzz"')
})

test("collection rows: Load all only while there are more, and not while finding", () => {
  const whole = Model.collectionRows(collection(), {})
  assert.deepEqual(whole.map(label), ["track:v0", "track:v1", "track:v2"])
  assert.deepEqual(whole.map(r => r.trackIndex), [0, 1, 2])

  const cut = collection({ trackCount: 1126, truncated: true, tracks: tracks(300) })
  const rows = Model.collectionRows(cut, {})
  assert.equal(rows.length, 301)
  const last = rows[300].item
  assert.equal(last.type, "loadMore", "a Load more row, so the cursor moves onto the first new song")
  assert.equal(last.id, Model.LOAD_ALL_ID)
  assert.equal(last.title, "Load all 1,126 songs")
  assert.equal(last.busyTitle, "Loading all 1,126 songs…")
  assert.equal(Model.canLoadAll(cut), true)

  const finding = Model.collectionRows(cut, { find: "v299" })
  assert.deepEqual(finding.map(label), ["track:v299"], "only matches, no Load all")

  const capped = Model.collectionRows(collection({ trackCount: 7000, truncated: true, capped: true, tracks: tracks(5) }), {})
  assert.equal(capped[5].item.type, "message")
  assert.equal(capped[5].item.title, "Showing the first 5 songs of 7,000")
  assert.equal(Model.canLoadAll(collection({ truncated: true, capped: true })), false)
  assert.equal(Model.canLoadAll({ type: "page", truncated: true }), false, "v2 pages page with cursors instead")
})

test("collection rows: the page's shelves come after the songs and open, never play", () => {
  const ov = { type: "album", browseId: "MPREb_ov", title: "OKNOTOK", explicit: true }
  const rec = { type: "album", browseId: "MPREb_rec", title: "Dark Side" }
  const page = collection({ kind: "album", truncated: false, tracks: tracks(2), sections: [
    { type: "section", title: "Other versions", items: [ov], more: null, play: null, layout: "carousel", key: "otherVersions" },
    { type: "section", title: "Releases for you", items: [rec, tr("odd")], more: { browseId: "MPADUC1", params: "p" },
      play: null, layout: "carousel", key: "releases" },
  ] })
  const rows = Model.collectionRows(page, {})
  assert.deepEqual(rows.map(label), ["track:v0", "track:v1", "# Other versions", "album:MPREb_ov", "# Releases for you",
                                     "album:MPREb_rec", "track:odd", "more:See all Releases for you"])
  assert.equal(rows[3].item.explicit, true, "the explicit flag reaches the row")
  assert.equal(rows[6].trackIndex, undefined, "a shelf's song does not index the page's songs")
  assert.equal(rows[3].sectionIndex, 0)
  // Finding hides the shelves.
  assert.deepEqual(Model.collectionRows(page, { find: "v1" }).map(label), ["track:v1"])
})

test("collection rows: an About row when the header cut the description short", () => {
  const page = collection({ header: { description: "First line.\nSecond line.\n\nFrom Wikipedia." } })
  assert.equal(Model.collectionRows(page, {})[0].item.type, "track", "no About row while the header shows it all")
  const shut = Model.collectionRows(page, { about: true })
  assert.deepEqual(shut.slice(0, 2).map(label), ["action:" + Model.COLLECTION_ABOUT_ID, "track:v0"])
  assert.equal(shut[0].item.title, "About")
  const open = Model.collectionRows(page, { about: true, aboutOpen: true })
  assert.equal(open[0].item.title, "Hide the description")
  assert.deepEqual(open.slice(1, 4).map(r => [r.item.type, r.item.title]),
                   [["text", "First line."], ["text", "Second line."], ["text", "From Wikipedia."]])
  assert.equal(open[4].trackIndex, 0)
  assert.equal(Model.collectionRows(page, { about: true, find: "v1" })[0].item.videoId, "v1", "no About while finding")
})

test("About in the page menu (M) only when the About row is there to show", () => {
  const short = collection({ header: { description: "A short line." } })
  const long = collection({ header: { description: "First line.\nSecond line." } })
  // The header shows a short description whole: no row, and M offers nothing that would do nothing.
  assert.equal(Model.showsAbout(short, { about: false }), false)
  assert.equal(Model.showsAbout(long, { about: true }), true)
  assert.equal(Model.showsAbout(long, { about: true, find: "v1" }), false, "not while finding")
  assert.equal(Model.showsAbout(long, { about: true, find: "  " }), true)
  assert.equal(Model.showsAbout(collection(), { about: true }), false, "no description")
  const ids = description => Model.menuActions({ type: "playlist", playlistId: "PLx" },
    { page: true, pageKind: "playlist", hasTracks: true, description }).map(a => a.id)
  assert.ok(!ids(Model.showsAbout(short, { about: false })).includes("collectionAbout"))
  assert.ok(ids(Model.showsAbout(long, { about: true })).includes("collectionAbout"))
  // The menu and the rows agree.
  for (const [page, opts] of [[short, { about: false }], [long, { about: true }], [long, { about: true, find: "v" }]])
    assert.equal(Model.collectionRows(page, opts).some(r => r.item && r.item.id === Model.COLLECTION_ABOUT_ID),
                 Model.showsAbout(page, opts))
})

test("G stops on the Load all row, which the shelves follow", () => {
  const related = [{ type: "section", title: "Related playlists", layout: "carousel", key: "related", more: null, play: null,
                     items: [{ type: "playlist", playlistId: "PLr0", title: "R0" }, { type: "playlist", playlistId: "PLr1", title: "R1" }] }]
  const cut = Model.collectionRows(collection({ trackCount: 1126, truncated: true, tracks: tracks(300), sections: related }), {})
  const at = Model.loadAllIndex(cut)
  assert.equal(at, 300)
  assert.equal(cut[at].item.id, Model.LOAD_ALL_ID)
  assert.ok(cut.length > at + 1, "the related playlists come after it")
  assert.equal(Model.loadAllIndex(Model.collectionRows(collection({ sections: related }), {})), -1, "a whole list")
  assert.equal(Model.loadAllIndex([{ item: { type: "loadMore", id: "more" } }]), -1, "a page's Load more is not it")
  assert.equal(Model.loadAllIndex(null), -1)
})

test("explicit flags pass through to the rows of songs, history and finds", () => {
  const list = [tr("e", { explicit: true }), tr("c", { explicit: false })]
  for (const rows of [Model.collectionRows(collection({ tracks: list }), {}),
                      Model.collectionRows(collection({ kind: "history", tracks: list }), { history: true }),
                      Model.findRows(list, "song", false)]) {
    const items = rows.filter(r => r.item && r.item.type === "track").map(r => r.item)
    assert.deepEqual(items.map(t => t.explicit), [true, false])
  }
})

test("header: byline with links, details with unavailable and showing", () => {
  const album = collection({ kind: "album", trackCount: 12, header: { type: "Album", year: "1997", duration: "53 minutes",
    artists: [{ name: "Radiohead", id: "UCr" }, { name: "Guest", id: null }], explicit: true } })
  const byline = Model.collectionByline(album)
  assert.deepEqual(byline.map(p => p.text), ["Album", " • ", "Radiohead", ", ", "Guest", " • ", "1997"])
  assert.deepEqual(byline[2].item, { type: "artist", channelId: "UCr", name: "Radiohead", thumb: null })
  assert.equal(byline[4].item, undefined, "an artist without a page is plain text")
  assert.equal(Model.collectionMeta(album), "12 songs • 53 minutes")
  assert.deepEqual(Model.pageArtistItem(album), { type: "artist", channelId: "UCr", name: "Radiohead", thumb: null })
  assert.equal(Model.pageArtistItem(collection()), null, "A on a playlist page goes to the song playing")

  const mine = collection({ trackCount: 1126, truncated: true, tracks: tracks(300), unavailable: null,
    header: { privacy: "PRIVATE", duration: "3 days", views: 13, year: "2026", author: { name: "Me", id: "UCme" } } })
  assert.equal(Model.collectionMeta(mine, 300), "Private • 1,126 songs • 3 days • 13 views • 2026 • showing 300")
  assert.deepEqual(Model.collectionByline(mine), [{ text: "Me", item: { type: "profile", channelId: "UCme", name: "Me" } }])
  const liked = collection({ kind: "liked", trackCount: 416, tracks: tracks(377), unavailable: 39 })
  assert.equal(Model.collectionMeta(liked, 377), "416 songs • 39 unavailable", "a whole list: no 'showing N'")
  assert.deepEqual(Model.collectionByline(liked), [])
  const shared = collection({ header: { collaborators: "by Sam and 1 other", author: { name: "Sam", id: "UCt" } } })
  assert.deepEqual(Model.collectionByline(shared), [{ text: "by Sam and 1 other" }])
  assert.deepEqual(Model.collectionByline(collection({ kind: "mix" })), [{ text: "Mix" }])
  assert.equal(Model.collectionMeta({ type: "page" }), "")
})

test("page kinds: playlists with related, History 200, mixes by id, Load all as limit all", () => {
  const playlist = Model.pageRef({ type: "playlist", playlistId: "PLx" })
  assert.deepEqual(playlist.params, { playlistId: "PLx", related: true })
  const all = Model.pageRef({ type: "playlist", playlistId: "PLx" }, Model.pageExtra(null, true))
  assert.deepEqual(all.params, { playlistId: "PLx", related: true, limit: "all" })
  assert.notEqual(all.key, playlist.key, "the whole read is its own Store entry")
  assert.equal(all.id, playlist.id, "forgetCollection drops both")
  assert.deepEqual(Model.pageExtra(2, false), { order: 2 })
  assert.deepEqual(Model.pageExtra(null, false), null)
  assert.deepEqual(Model.pageRef({ type: "liked" }, Model.pageExtra(null, true)).params, { limit: "all" })
  assert.deepEqual(Model.pageRef({ type: "history" }).params, { limit: 200 })

  const mix = Model.pageRef({ type: "playlist", playlistId: "VLRDTMAK5uy_supermix", title: "My Supermix" })
  assert.equal(mix.kind, "mix")
  assert.equal(mix.method, "playlist")
  assert.deepEqual(mix.params, { playlistId: "RDTMAK5uy_supermix" })
  assert.equal(mix.id, "RDTMAK5uy_supermix")
  assert.equal(Model.pageKindTitle(mix), "Mix")
  assert.equal(Model.pageRef({ type: "playlist", playlistId: "RDCLAK5uy_curated" }).kind, "playlist")
  assert.equal(Model.pageRef({ type: "playlist", playlistId: "LM" }).kind, "liked")
  assert.equal(Model.menuKind({ type: "playlist", playlistId: "RDTMAK5uy_supermix" }), "mix")
  // Store's whole-read timeout keys off the same param.
  assert.equal(all.params.limit, "all")
})

test("find and Load all twins in the page menu (M)", () => {
  const ids = ctx => Model.menuActions({ type: "playlist", playlistId: "PLx", title: "Mine" }, ctx).map(a => a.id)
  const ctx = { page: true, pageKind: "playlist", hasTracks: true, canLoadAll: true, loadAllTitle: "Load all 1,126 songs",
                canFind: true, description: true, aboutOpen: false }
  const listed = ids(ctx)
  for (const id of ["loadAll", "find", "collectionAbout"]) assert.ok(listed.includes(id), id)
  const actions = Model.menuActions({ type: "playlist", playlistId: "PLx" }, ctx)
  assert.equal(actions.find(a => a.id === "loadAll").title, "Load all 1,126 songs")
  assert.equal(actions.find(a => a.id === "find").hint, "f")
  assert.equal(Model.menuActions(null, { ...ctx, aboutOpen: true }).find(a => a.id === "collectionAbout").title,
               "Hide the description")
  const none = ids({ page: true, pageKind: "playlist", hasTracks: true })
  for (const id of ["loadAll", "find", "collectionAbout"]) assert.ok(!none.includes(id), id)
  assert.ok(!ids({ ...ctx, page: false }).includes("loadAll"), "row menus never offer them")
  let ran = ""
  const owner = { loadAll: () => { ran += "L" }, find: () => { ran += "F" }, toggleAbout: () => { ran += "A" } }
  for (const action of actions.filter(a => ["loadAll", "find", "collectionAbout"].includes(a.id)))
    action.run({ item: null, ctx, index: -1, owner, panel: null, service: null })
  assert.equal(ran, "LFA")
})

test("withoutTokens keeps the queue free of feedback tokens", () => {
  const track = tr("t", { libraryTokens: { add: "A", remove: "R" }, listenAgainTokens: { pin: "P", unpin: "U" },
                         historyToken: "H", inLibrary: true, trackNumber: 3 })
  const clean = Model.withoutTokens(track)
  for (const key of Model.TOKEN_KEYS) assert.equal(key in clean, false, key)
  assert.equal(clean.inLibrary, true)
  assert.equal(clean.trackNumber, 3)
  assert.equal(track.historyToken, "H", "the original is untouched")
  const plain = tr("p")
  assert.equal(Model.withoutTokens(plain), plain, "nothing to drop: the same object")
  assert.equal(Model.withoutTokens(null), null)
})

test("counts read like the web", () => {
  assert.equal(Model.groupedCount(1126), "1,126")
  assert.equal(Model.groupedCount(377), "377")
  assert.equal(Model.groupedCount(1234567), "1,234,567")
  assert.equal(Model.songsText(1), "1 song")
  assert.equal(Model.songsText(5000), "5,000 songs")
  assert.equal(Model.loadAllTitle({ trackCount: 416 }), "Load all 416 songs")
  assert.equal(Model.canFind(collection({ tracks: [] })), false)
  assert.equal(Model.canFind(collection()), true)
})
