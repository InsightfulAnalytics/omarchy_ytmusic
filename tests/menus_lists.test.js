const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

// Menus and lists (G4): Liked Music's radio and chips, mixes saved into
// playlists, a playlist's author, Skip duplicates, the queue's autoplay
// rows and Remove custom image.

const ids = (item, ctx) => Model.menuActions(item, ctx).map(a => a.id)
const likedRow = { type: "liked", title: "Liked Music", thumb: null }
const likedPlaylist = { type: "playlist", playlistId: "LM", title: "Liked Music" }
const mix = { type: "playlist", playlistId: "RDTMAK5uy_kset8DisdE7LSD4TNjEVvrKRTmG7a56sY", title: "My Supermix" }
const curated = { type: "playlist", playlistId: "RDCLAK5uy_curated", title: "Curated" }
// As served: a chip's index is its id (YouTube's uniqueId), since the
// chips move between reads.
const CHIPS = ["Party", "Hip-hop", "Chill", "Focus", "Electronic dance music", "Rock"]
  .map(title => ({ index: title, title, selected: false }))
const likedPage = (extra = {}) => ({ type: "collection", kind: "liked", id: "LM", playlistId: "LM", title: "Liked Music",
  tracks: [{ type: "track", videoId: "v1xxxxxxxxx", title: "One" }], truncated: false, header: {},
  filters: CHIPS, filter: null, ...extra })
const playlistPage = (header) => ({ type: "collection", kind: "playlist", id: "PLx", playlistId: "PLx", title: "A list",
  tracks: [], header })

// ---- Start radio from Liked Music ------------------------------------------------------------

test("Liked Music's row and page start its radio (RDAMPLLM through the worker's playlistId)", () => {
  assert.deepEqual(Model.radioParams(likedRow), { playlistId: "LM" })
  assert.deepEqual(Model.radioParams(likedPlaylist), { playlistId: "LM" })
  assert.deepEqual(Model.radioParams({ type: "playlist", playlistId: "VLLM" }), { playlistId: "LM" })
  assert.ok(ids(likedRow, { signedIn: true, pageKind: "library" }).includes("radio"))
  const page = ids(likedPlaylist, { page: true, pageKind: "liked", hasTracks: true, signedIn: true })
  assert.ok(page.includes("radio") && page.indexOf("radio") > page.indexOf("addToQueue"))
  // Everything else as before.
  assert.deepEqual(Model.radioParams({ type: "playlist", playlistId: "PLx" }), { playlistId: "PLx" })
  assert.deepEqual(Model.radioParams(mix), { mixId: mix.playlistId })
  assert.equal(Model.radioParams({ type: "history" }), null)
  assert.ok(Model.keyHelp().some(r => r.item && /Liked Music/.test(r.item.title) && r.item.hint === "R"))
})

// ---- mixes -----------------------------------------------------------------------------------

test("a mix row shuffles and saves into a playlist (by its songs, never its id)", () => {
  assert.equal(Model.canSaveWhole(mix), true)
  assert.equal(Model.sourcePlaylistOf(mix), "", "an RD id is generated: never sent as a source")
  assert.equal(Model.canSaveWhole(curated), true, "a curated list is a playlist")
  assert.equal(Model.sourcePlaylistOf(curated), "RDCLAK5uy_curated")
  for (const none of [likedRow, likedPlaylist, { type: "track", videoId: "v" }, { type: "artist", channelId: "UC" }, null])
    assert.equal(Model.canSaveWhole(none), false, JSON.stringify(none))
  const row = ids(mix, { signedIn: true, pageKind: "home" })
  for (const id of ["shuffle", "saveToPlaylist", "copyToNewPlaylist"]) assert.ok(row.includes(id), id)
  const out = ids(mix, { signedIn: false, pageKind: "home" })
  assert.ok(out.includes("shuffle") && !out.includes("saveToPlaylist") && !out.includes("copyToNewPlaylist"))
  // A mix page's M (the page item is the mix).
  const page = ids(mix, { page: true, pageKind: "mix", hasTracks: true, signedIn: true })
  assert.ok(page.includes("saveToPlaylist") && page.includes("copyToNewPlaylist") && page.includes("shuffle"))
  assert.equal(Model.menuActions(mix, { signedIn: true }).find(a => a.id === "saveToPlaylist").hint, "p")
})

test("the picker names what is saved whole", () => {
  assert.equal(Model.pickerSourceText(mix), "The mix: My Supermix")
  assert.equal(Model.pickerSourceText({ type: "album", browseId: "MPREb_x", playlistId: "OLAK5uy_x", title: "OK" }),
    "The whole album: OK")
  assert.equal(Model.pickerSourceText({ type: "playlist", playlistId: "PLx", title: "P" }), "The whole playlist: P")
  assert.equal(Model.pickerSourcePlaceholder(mix), "New playlist from this mix")
  assert.equal(Model.pickerSourcePlaceholder({ type: "album", browseId: "MPREb_x" }), "New playlist from this album")
  assert.equal(Model.pickerSourcePlaceholder({ type: "playlist", playlistId: "PLx" }), "New playlist from this playlist")
})

// ---- a playlist's author ---------------------------------------------------------------------

test("a playlist's author is the byline's profile link; A and M open it", () => {
  const page = playlistPage({ author: { name: "Polymer", id: "UCpolymer" } })
  const author = { type: "profile", channelId: "UCpolymer", name: "Polymer" }
  assert.deepEqual(Model.playlistAuthorItem(page), author)
  const byline = Model.collectionByline(page)
  assert.deepEqual(byline[0].item, author, "the same item the byline links")
  assert.deepEqual(Model.pageArtistItem(page), author, "A with no row")
  assert.equal(Model.playlistAuthorItem(playlistPage({ author: { name: "YouTube Music", id: null } })), null)
  assert.equal(Model.playlistAuthorItem(playlistPage({ author: { name: "x", id: "MPLAx" } })), null)
  assert.equal(Model.playlistAuthorItem(playlistPage({ collaborators: "by You and 1 other", author: { name: "x", id: "UCx" } })), null)
  assert.equal(Model.playlistAuthorItem(likedPage()), null)
  assert.equal(Model.playlistAuthorItem(null), null)
  // An album page still opens its artist.
  const album = { type: "collection", kind: "album", header: { artists: [{ name: "Radiohead", id: "UCr_iyUANcn9OX_yy9piYoLw" }] } }
  assert.equal(Model.pageArtistItem(album).type, "artist")
  // M: Go to author on a playlist page with an author, after Go to album's place.
  const ctx = { page: true, pageKind: "playlist", hasTracks: true, signedIn: true, authorItem: author }
  const entry = Model.menuActions({ type: "playlist", playlistId: "PLx" }, ctx).find(a => a.id === "goAuthor")
  assert.ok(entry && entry.title === "Go to author" && entry.hint === "A")
  assert.ok(!ids({ type: "playlist", playlistId: "PLx" }, Object.assign({}, ctx, { authorItem: null })).includes("goAuthor"))
  assert.ok(!ids({ type: "playlist", playlistId: "PLx" }, { pageKind: "playlist", authorItem: author }).includes("goAuthor"),
    "not on a row's menu")
  let pushed = null
  Model.menuAction("goAuthor").run({ ctx, panel: { push: item => { pushed = item } } })
  assert.deepEqual(pushed, author)
  assert.match(Model.KEYS.find(k => k.id === "artist").title, /playlist's author/)
})

// ---- Liked Music's chips -------------------------------------------------------------------------

test("Liked Music's chips are its page's filters, and a chip is a page of its own", () => {
  assert.deepEqual(Model.likedFilters(likedPage()), CHIPS)
  assert.deepEqual(Model.likedFilters(playlistPage({})), [])
  assert.deepEqual(Model.likedFilters({ type: "page", kind: "podcast", filters: CHIPS }), [], "a podcast's are G3's")
  assert.deepEqual(Model.likedFilters(null), [])
  const plain = Model.pageRef(likedRow, Model.pageExtra(null, false, null))
  const chill = Model.pageRef(likedRow, Model.pageExtra(null, false, "Chill"))
  const chillAll = Model.pageRef(likedRow, Model.pageExtra(null, true, "Chill"))
  assert.deepEqual(chill.params, { filter: "Chill" })
  assert.deepEqual(chillAll.params, { filter: "Chill", limit: "all" })
  assert.equal(new Set([plain.key, chill.key, chillAll.key]).size, 3)
  assert.equal(chill.method, "liked")
})

test("the chip row: All, the first three, More (named after a chip it hides)", () => {
  const row = Model.likedChipRow(CHIPS, null)
  assert.deepEqual(row.map(c => c.title), ["All", "Party", "Hip-hop", "Chill", "More"])
  assert.deepEqual(row.map(c => c.selected), [true, false, false, false, false])
  assert.deepEqual(row.map(c => c.more), [false, false, false, false, true])
  assert.deepEqual(row.map(c => c.value), [null, "Party", "Hip-hop", "Chill", null])
  const shown = Model.likedChipRow(CHIPS, "Hip-hop")
  assert.deepEqual(shown.map(c => c.selected), [false, false, true, false, false])
  assert.equal(shown[4].title, "More")
  const hidden = Model.likedChipRow(CHIPS, "Rock")
  assert.equal(hidden[4].title, "Rock")
  assert.equal(hidden[4].selected, true)
  assert.deepEqual(Model.likedChipRow(CHIPS.slice(0, 2), null).map(c => c.title), ["All", "Party", "Hip-hop"], "no More")
  assert.deepEqual(Model.likedChipRow([], null), [])
  assert.equal(Model.likedFilterTitle(CHIPS, null), "All songs")
  assert.equal(Model.likedFilterTitle(CHIPS, "Chill"), "Chill")
  assert.equal(Model.likedFilterTitle(CHIPS, "Polka"), "All songs")
})

test("h / l cycle every chip with all songs between; the chooser ticks the current one", () => {
  const at = (current, dx) => Model.nextEpisodeFilter(CHIPS, current, dx)
  assert.deepEqual([at(null, 1), at("Electronic dance music", 1), at("Rock", 1)], ["Party", "Rock", null])
  assert.deepEqual([at(null, -1), at("Party", -1)], ["Rock", null])
  const choices = Model.likedFilterChoices(CHIPS, "Focus")
  assert.equal(choices.length, CHIPS.length + 1)
  assert.deepEqual(choices[0], { id: "liked:all", title: "All songs", icon: choices[0].icon, value: null })
  assert.equal(choices.filter(c => c.icon === choices[4].icon).length, 1, "one ticked")
  assert.equal(choices[4].value, "Focus")
  assert.equal(choices[4].id, "liked:Focus")
  assert.notEqual(choices[0].icon, choices[4].icon)
})

test("M on Liked Music has Filter (h / l), and on no other page", () => {
  const ctx = { page: true, pageKind: "liked", hasTracks: true, signedIn: true, likedFilters: true, likedFilterTitle: "Chill" }
  const entry = Model.menuActions(likedPlaylist, ctx).find(a => a.id === "likedFilter")
  assert.equal(entry.title, "Filter: Chill")
  assert.equal(entry.hint, Model.keyHint("horizontal"))
  assert.ok(!ids(likedPlaylist, Object.assign({}, ctx, { likedFilters: false })).includes("likedFilter"))
  let chosen = false
  Model.menuAction("likedFilter").run({ ctx, owner: { chooseLikedFilter: () => { chosen = true } } })
  assert.ok(chosen)
})

// ---- Skip duplicates ---------------------------------------------------------------------------

test("Skip duplicates is offered for any refused add of several songs or a whole list", () => {
  assert.equal(Model.canSkipDuplicates(0, 0), true, "a whole album or playlist")
  assert.equal(Model.canSkipDuplicates(2, 5), true)
  // A refusal may name every song asked for (its Add anyway can repeat the
  // whole request), so a full count does not mean nothing is new.
  assert.equal(Model.canSkipDuplicates(5, 5), true, "every song named: the worker finds out")
  assert.equal(Model.canSkipDuplicates(0, 3), true, "YouTube named none")
  assert.equal(Model.canSkipDuplicates(1, 1), false, "one song: Esc is the skip")
  const dup = ids => ({ ok: false, error: { code: "DUPLICATE", message: "Already in that playlist", videoIds: ids } })
  const some = Model.pickerAfterReply({}, "PLa", dup(["a", "b"]), 5)
  assert.equal(some.skip, true)
  assert.equal(some.status, "2 of 5 songs are already there: Enter again adds them anyway, or choose Skip duplicates")
  assert.equal(Model.pickerAfterReply({}, "PLa", dup(["a"]), 1).skip, undefined)
  const every = Model.pickerAfterReply({}, "PLa", dup(["a", "b"]), 2)
  assert.equal(every.skip, true)
  assert.equal(every.status, "Some of the 2 songs are already there: Enter again adds them anyway, or choose Skip duplicates",
               "never claims all of them are")
  assert.equal(Model.pickerAfterReply({}, "PLa", dup(Array.from({ length: 50 }, (_, i) => "v" + i)), 50).status,
               "Some of the 50 songs are already there: Enter again adds them anyway, or choose Skip duplicates",
               "a mix of 50 refused whole")
  assert.equal(Model.pickerAfterReply({}, "PLa", { ok: true, result: { added: [] } }, 5).skip, undefined)
  const unknown = Model.pickerAfterReply({}, "PLa", { ok: false, error: { code: "DUPLICATE", unknown: true, videoIds: [] } }, 0)
  assert.equal(unknown.duplicateOf, "")
  assert.equal(unknown.skip, undefined)
})

test("the skip goes to the playlist YouTube refused, never with Add anyway", () => {
  const armed = { duplicateOf: "PLa" }
  assert.deepEqual(Model.pickerAddOptions(armed, "PLa", true), { duplicates: false, skipDuplicates: true })
  assert.deepEqual(Model.pickerAddOptions(armed, "PLa"), { duplicates: true })
  assert.deepEqual(Model.pickerAddOptions(armed, "PLb", true), { duplicates: false }, "another playlist asks first")
  assert.deepEqual(Model.pickerAddOptions({ duplicateOf: "" }, "PLa", true), { duplicates: false })
})

test("the Skip duplicates row sits under the refused playlist while offered", () => {
  const rows = ["PLa", "PLb", "PLc"].map(id => ({ item: { type: "playlist", playlistId: id, title: id } }))
  const withRow = Model.withSkipRow(rows, "PLb", true)
  assert.deepEqual(withRow.map(r => r.item.playlistId || r.item.id), ["PLa", "PLb", Model.SKIP_DUPLICATES_ID, "PLc"])
  assert.equal(withRow[2].item.type, "action")
  assert.match(withRow[2].item.title, /^Skip duplicates/)
  assert.equal(Model.withSkipRow(rows, "PLb", false), rows)
  assert.equal(Model.withSkipRow(rows, "", true), rows)
  assert.equal(Model.withSkipRow(rows, "PLgone", true), rows)
})

test("the notice after a skip says what was left out", () => {
  assert.deepEqual(Model.skippedNotice("Mine", "5 songs", { added: [{}, {}, {}], skipped: ["a", "b"] }),
    { title: "Added to Mine", body: "5 songs (2 songs already there, skipped)" })
  assert.deepEqual(Model.skippedNotice("Mine", "OK Computer", { added: [], skipped: [] }),
    { title: "Added to Mine", body: "OK Computer (songs already there skipped)" })
  assert.deepEqual(Model.skippedNotice("Mine", "2 songs", { added: [], skipped: ["a", "b"], status: "Nothing new to add" }),
    { title: "Nothing new to add", body: "Every song is already in Mine" })
})

// ---- the queue's autoplay rows ---------------------------------------------------------------

test("autoplay's songs are set apart only outside a radio or a tuned queue", () => {
  assert.equal(Model.autoplaySetApart({ playlistId: "OLAK5uy_x", title: "OK Computer", radio: false }, null), true)
  assert.equal(Model.autoplaySetApart({ playlistId: null, title: "Karma Police", radio: false }, null), true, "one song")
  assert.equal(Model.autoplaySetApart(null, null), true)
  assert.equal(Model.autoplaySetApart({ playlistId: "RDAMVMx", title: "Karma Police Mix", radio: true }, null), false,
               "Start radio")
  assert.equal(Model.autoplaySetApart({ playlistId: "RDAMPLLM", title: "Liked Music Mix", radio: true }, null), false)
  assert.equal(Model.autoplaySetApart({ playlistId: "OLAK5uy_x", title: "OK Computer", radio: false },
                                      { title: "Discover", playlistId: "RDATx" }), false, "a tuner chip")
})

test("the Autoplay caption goes above the first song autoplay added after the current one", () => {
  const q = ["user", "user", "auto", "user", "auto", "auto"].map((origin, i) => ({ entryId: i, origin }))
  assert.equal(Model.firstAutoIndex(q, 0), 2)
  assert.equal(Model.firstAutoIndex(q, 2), 4, "the one playing is no longer up next")
  assert.equal(Model.firstAutoIndex(q, 5), -1)
  assert.equal(Model.firstAutoIndex(q, -1), 2, "nothing playing: from the top")
  assert.equal(Model.firstAutoIndex([], 0), -1)
  assert.equal(Model.firstAutoIndex(null, 0), -1)
  assert.equal(Model.autoplayAdded(q[4], 4, 2), true)
  assert.equal(Model.autoplayAdded(q[2], 2, 2), false, "playing: not dimmed")
  assert.equal(Model.autoplayAdded(q[2], 2, 3), false, "played")
  assert.equal(Model.autoplayAdded(q[3], 3, 0), false)
  assert.equal(Model.AUTOPLAY_CAPTION, "Autoplay")
  // Origins come from S12's withOrigins.
  const queue = Model.withOrigins([{ entryId: 7, videoId: "v7" }, { entryId: 8, videoId: "v8" }], { 8: { videoId: "v8", origin: "auto" } })
  assert.equal(Model.firstAutoIndex(queue, 0), 1)
})

// ---- Remove custom image ---------------------------------------------------------------------

test("the editor offers Remove custom image only when YouTube serves it, under Privacy", () => {
  const target = { playlistId: "PLx", title: "Mine" }
  const served = Model.editorInitial(target, { owned: true, privacy: "PRIVATE", removeImage: "Remove custom image" })
  assert.equal(served.removeImage, "Remove custom image")
  assert.equal(Model.editorInitial(target, { owned: true, removeImage: null }).removeImage, null)
  assert.equal(Model.editorInitial(target, null).removeImage, null)
  const rows = Model.editorRows(served, served, {})
  const at = rows.findIndex(r => r.item && r.item.id === Model.EDITOR_REMOVE_IMAGE_ID)
  assert.ok(at > 0)
  assert.equal(rows[at].item.title, "Remove custom image", "the served title")
  assert.equal(rows[at - 1].item.id, Model.EDITOR_IDS.privacy)
  assert.equal(rows[at + 1].header, true, "before Advanced")
  const armed = Model.editorRows(served, served, { imageArmed: true })
  assert.match(armed[at].item.title, /^Really remove/)
  const plain = Model.editorInitial(target, { owned: true })
  assert.ok(!Model.editorRows(plain, plain, {}).some(r => r.item && r.item.id === Model.EDITOR_REMOVE_IMAGE_ID))
  assert.equal(Model.editorRows(plain, plain, {}).length, Model.editorRows(served, served, {}).length - 1)
})
