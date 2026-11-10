const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

// Library actions (S4): Save, Subscribe, a song's Save to library, Like and Dislike.

const id11 = n => ("vid" + n).padEnd(11, "x")
const song = (n, extra = {}) => ({ type: "track", videoId: id11(n), title: "Song " + n, artistText: "A",
  artists: [{ name: "A", id: "UCa" }], ...extra })
const tokens = { add: "ADD", remove: "REMOVE" }
const album = { type: "album", browseId: "MPREb_ok", playlistId: "OLAK5uy_ok", title: "OK Computer" }
const bareAlbum = { type: "album", browseId: "MPREb_bare", playlistId: null, title: "No playlist" }
const theirs = { type: "playlist", playlistId: "PLtheirs", title: "Theirs", owned: false }
const mine = { type: "playlist", playlistId: "PLmine", title: "Mine", owned: true }
const mix = { type: "playlist", playlistId: "RDTMAK5uy_mix", title: "My Supermix" }
const artist = { type: "artist", channelId: "UCpage", name: "Radiohead" }
const ids = (item, ctx) => Model.menuActions(item, ctx).map(a => a.id)
const titleOf = (item, ctx, id) => Model.menuActions(item, ctx).find(a => a.id === id).title

test("menu: Save or Remove by state, only signed in and never on your own playlist", () => {
  const ctx = { signedIn: true, pageKind: "search" }
  assert.equal(titleOf(album, ctx, "save"), "Save to library", "unknown state offers Save")
  assert.equal(titleOf(album, { ...ctx, saved: false }, "save"), "Save to library")
  assert.equal(titleOf(album, { ...ctx, saved: true }, "save"), "Remove from library")
  assert.equal(titleOf(theirs, { ...ctx, saved: true }, "save"), "Remove from library")
  assert.equal(titleOf(mix, ctx, "save"), "Save to library", "mixes can be saved, as on the web")
  assert.ok(ids(bareAlbum, ctx).includes("save"), "an album without its playlist saves by its page")
  assert.ok(!ids(album, { pageKind: "search" }).includes("save"), "signed out: no entry")
  assert.ok(!ids(mine, ctx).includes("save"), "your own playlist")
  assert.ok(!ids(theirs, { ...ctx, ownItem: true }).includes("save"), "the Store knows it as yours")
  assert.ok(!ids(theirs, { ...ctx, page: true, owned: true }).includes("save"), "your own playlist's page menu")
  for (const special of ["LM", "SE"]) assert.ok(!ids({ type: "playlist", playlistId: special }, ctx).includes("save"), special)
  assert.equal(Model.menuActions(album, ctx).find(a => a.id === "save").hint, "S")
})

test("menu: Subscribe or Unsubscribe on artists; a song's Save by its tokens; Like and Dislike", () => {
  const ctx = { signedIn: true, pageKind: "search" }
  assert.equal(titleOf(artist, ctx, "subscribe"), "Subscribe")
  assert.equal(titleOf(artist, { ...ctx, subscribed: true }, "subscribe"), "Unsubscribe")
  assert.ok(!ids(artist, {}).includes("subscribe"))
  // A song with library tokens: Save, or Remove once in the library; without the token it needs, nothing.
  const withTokens = song(1, { libraryTokens: tokens, inLibrary: false })
  assert.equal(titleOf(withTokens, { ...ctx, inLibrary: false }, "saveSong"), "Save to library")
  assert.equal(titleOf(withTokens, { ...ctx, inLibrary: true }, "saveSong"), "Remove from library")
  assert.ok(!ids(song(2), ctx).includes("saveSong"), "a queued song has no tokens")
  assert.ok(!ids(song(3, { libraryTokens: { add: "A", remove: null } }), { ...ctx, inLibrary: true }).includes("saveSong"))
  assert.equal(titleOf(song(4), { ...ctx, disliked: true }, "dislike"), "Remove dislike")
  assert.equal(titleOf(song(4), ctx, "dislike"), "Dislike")
  assert.equal(Model.menuActions(song(4), ctx).find(a => a.id === "dislike").hint, "D")
  assert.equal(titleOf(song(4), { ...ctx, liked: true }, "like"), "Remove from Liked Music")
  assert.ok(!ids(song(4), { ...ctx, pageKind: "liked" }).includes("like"), "Liked Music rows have Remove")
  assert.ok(!ids(song(4), { pageKind: "search" }).some(id => ["like", "dislike", "saveSong"].includes(id)))
  // Order, as on the web: after Start radio.
  assert.deepEqual(ids(withTokens, ctx).slice(3, 7), ["radio", "saveSong", "like", "dislike"])
})

test("menu entries run the Service, or the page's own toggle on a page menu", () => {
  const calls = []
  const service = {
    saveItem: (item, save, cb) => calls.push(["saveItem", item.browseId || item.playlistId, save, typeof cb]),
    subscribe: (item, on, cb) => calls.push(["subscribe", item.channelId, on]),
    setSongLibrary: (track, add, cb) => calls.push(["setSongLibrary", track.videoId, add]),
    setLike: (track, like) => calls.push(["setLike", like]),
    dislike: (track, on) => calls.push(["dislike", on])
  }
  const run = (id, item, ctx, owner) => Model.menuAction(id).run({ item, ctx, index: 0, owner: owner || null, panel: {}, service })
  run("save", album, { saved: true })
  run("save", theirs, { saved: false }, { editCallback: () => function() {} })
  run("subscribe", artist, {})
  run("saveSong", song(1, { libraryTokens: tokens }), { inLibrary: true })
  run("like", song(1), { liked: true })
  run("dislike", song(1), { disliked: false })
  let toggled = 0
  run("save", album, { page: true, saved: false }, { toggleSave: () => { toggled++ } })
  run("subscribe", artist, { page: true }, { toggleSave: () => { toggled++ } })
  assert.deepEqual(calls, [["saveItem", "MPREb_ok", false, "object"], ["saveItem", "PLtheirs", true, "function"],
    ["subscribe", "UCpage", true], ["setSongLibrary", id11(1), false], ["setLike", false], ["dislike", true]])
  assert.equal(toggled, 2, "a page menu's entry is the header button's twin")
})

test("a page menu offers Save or Subscribe only with its header button", () => {
  const ctx = { signedIn: true, page: true, pageKind: "album" }
  assert.ok(ids(album, { ...ctx, canSavePage: true }).includes("save"))
  assert.ok(!ids(album, { ...ctx, canSavePage: false }).includes("save"), "the page is loading or failed")
  assert.ok(ids(album, ctx).includes("save"), "a view that says nothing keeps the entry")
  const artistCtx = { signedIn: true, page: true, pageKind: "artist" }
  assert.ok(ids(artist, { ...artistCtx, canSubscribePage: true }).includes("subscribe"))
  assert.ok(!ids(artist, { ...artistCtx, canSubscribePage: false }).includes("subscribe"))
  assert.ok(ids(album, { signedIn: true, pageKind: "search", canSavePage: false }).includes("save"),
    "a row menu does not follow the page's header")
})

test("S: the row's item, else the page; nothing on an item without anything to save", () => {
  assert.equal(Model.saveTarget(null), "page", "no row has the cursor")
  for (const type of ["more", "loadMore", "action", "message", "text"])
    assert.equal(Model.saveTarget({ type, title: "x" }), "page", type)
  assert.equal(Model.saveTarget(album), "row")
  assert.equal(Model.saveTarget(mix), "row")
  assert.equal(Model.saveTarget(artist), "row")
  assert.equal(Model.saveTarget(song(1, { libraryTokens: tokens })), "row")
  assert.equal(Model.saveTarget(song(2)), "", "a song without tokens never falls through to the page")
  assert.equal(Model.saveTarget(mine), "")
  assert.equal(Model.saveTarget(theirs, { ownItem: true }), "")
  assert.equal(Model.saveTarget({ type: "liked", title: "Liked Music" }), "")
  assert.equal(Model.saveAction(album), "save")
  assert.equal(Model.saveAction(artist), "subscribe")
  assert.equal(Model.saveAction(song(1, { libraryTokens: tokens })), "saveSong")
  assert.equal(Model.saveAction({ type: "profile", channelId: "UCuser" }), "subscribe", "S9: profiles subscribe")
})

test("worker params: albums by page, playlists by id, artists by page, songs by token", () => {
  assert.deepEqual(Model.rateCollectionParams(album, true),
    { kind: "album", browseId: "MPREb_ok", playlistId: "OLAK5uy_ok", rating: "LIKE" })
  // An album's and a song's artists name the artist pages the edit changes (From your library).
  const byArtists = { ...album, artists: [{ name: "Radiohead", id: "UCr" }, { name: "Guest", id: null }] }
  assert.deepEqual(Model.rateCollectionParams(byArtists, false),
    { kind: "album", browseId: "MPREb_ok", playlistId: "OLAK5uy_ok", rating: "INDIFFERENT", artistIds: ["UCr"] })
  assert.deepEqual(Model.rateCollectionParams({ ...theirs, artists: [{ name: "A", id: "UCa" }] }, true),
    { kind: "playlist", playlistId: "PLtheirs", rating: "LIKE" }, "a playlist save changes no artist page")
  assert.deepEqual(Model.rateCollectionParams(bareAlbum, false), { kind: "album", browseId: "MPREb_bare", rating: "INDIFFERENT" })
  assert.deepEqual(Model.rateCollectionParams({ type: "playlist", playlistId: "VLPLx" }, true),
    { kind: "playlist", playlistId: "PLx", rating: "LIKE" })
  assert.deepEqual(Model.rateCollectionParams({ type: "album", playlistId: "OLAK5uy_only" }, true),
    { kind: "playlist", playlistId: "OLAK5uy_only", rating: "LIKE" })
  assert.equal(Model.rateCollectionParams(artist, true), null)
  assert.deepEqual(Model.subscribeParams(artist, true), { pageChannelId: "UCpage", subscribe: true })
  assert.deepEqual(Model.subscribeParams({ ...artist, subscribeId: "UCofficial" }, false),
    { pageChannelId: "UCpage", subscribe: false, channelId: "UCofficial" })
  assert.equal(Model.subscribeParams(album, true), null)
  const track = song(1, { libraryTokens: tokens })
  assert.deepEqual(Model.songLibraryParams(track, true), { videoId: id11(1), token: "ADD", artistIds: ["UCa"] })
  assert.deepEqual(Model.songLibraryParams(track, false), { videoId: id11(1), token: "REMOVE", artistIds: ["UCa"] })
  assert.deepEqual(Model.songLibraryParams({ ...track, artists: [] }, true), { videoId: id11(1), token: "ADD" })
  assert.equal(Model.songLibraryParams(song(2), true), null)
  assert.deepEqual(Model.saveKeys(album), ["OLAK5uy_ok", "MPREb_ok"])
  assert.deepEqual(Model.saveKeys({ type: "playlist", playlistId: "VLPLx" }), ["PLx"])
})

test("optimistic state and its rollback", () => {
  const start = { PLa: true }
  const edit = Model.optimistic(start, ["OLAK5uy_ok", "MPREb_ok"], true)
  assert.deepEqual(edit.map, { PLa: true, OLAK5uy_ok: true, MPREb_ok: true })
  assert.deepEqual(edit.previous, { OLAK5uy_ok: null, MPREb_ok: null })
  assert.deepEqual(start, { PLa: true }, "the original map is untouched")
  assert.deepEqual(Model.restoreStates(edit.map, edit.previous), start, "a definite error puts it back")
  const flip = Model.optimistic(start, "PLa", false)
  assert.deepEqual(flip.map, { PLa: false })
  assert.deepEqual(Model.restoreStates(flip.map, flip.previous), { PLa: true })
  // An unknown outcome forgets the edit: the state is unknown again.
  assert.deepEqual(Model.stateWith(flip.map, "PLa", null), {})
  assert.equal(Model.stateOf({ a: false }, "a"), false)
  assert.equal(Model.stateOf({ a: "LIKE" }, "a"), null)
  assert.equal(Model.firstState({ b: true }, ["a", "b"]), true)
})

test("what is saved: an edit here, then the page, then the library's lists", () => {
  const lists = { playlists: [{ playlistId: "PLtheirs" }], playlistsLoaded: true,
                  albums: [{ browseId: "MPREb_lib", playlistId: "OLAK5uy_lib" }], albumsComplete: false }
  assert.equal(Model.savedState(theirs, lists), true, "in the library's playlists")
  assert.equal(Model.savedState({ type: "playlist", playlistId: "PLother" }, lists), false, "the whole list lacks it")
  assert.equal(Model.savedState({ type: "playlist", playlistId: "PLother" }, { ...lists, playlistsLoaded: false }), null)
  assert.equal(Model.savedState({ type: "album", browseId: "MPREb_lib" }, lists), true, "by browseId")
  assert.equal(Model.savedState(album, lists), null, "albums read so far do not say no")
  assert.equal(Model.savedState(album, { ...lists, albumsComplete: true }), false)
  assert.equal(Model.savedState(album, { ...lists, fallback: true }), true, "the album page's header")
  assert.equal(Model.savedState(theirs, { ...lists, fallback: false }), false, "the page is fresher than the list")
  assert.equal(Model.savedState(theirs, { ...lists, saved: { PLtheirs: false }, fallback: true }), false, "an edit wins")
  assert.equal(Model.savedState(album, { saved: { MPREb_ok: true } }), true)
  assert.equal(Model.subscribedState("UCpage", { subscriptions: [{ channelId: "UCpage" }] }), true)
  assert.equal(Model.subscribedState("UCx", { subscriptions: [], subscriptionsComplete: true }), false)
  assert.equal(Model.subscribedState("UCx", {}), null)
  assert.equal(Model.subscribedState("UCx", { fallback: true, subscribed: { UCx: false } }), false)
  assert.equal(Model.inLibraryState(song(1, { inLibrary: true }), {}), true)
  assert.equal(Model.inLibraryState(song(1, { inLibrary: true }), { inLibrary: { [id11(1)]: false } }), false)
  assert.equal(Model.inLibraryState(song(1), {}), null)
})

test("a dislike lasts the session; a like ends it", () => {
  const v = id11(1)
  assert.equal(Model.dislikedState(v, {}, {}, "DISLIKE"), true, "the row says so")
  assert.equal(Model.dislikedState(v, { [v]: "DISLIKE" }, {}, null), true)
  assert.equal(Model.dislikedState(v, { [v]: "INDIFFERENT" }, { [v]: true }, null), true,
    "YouTube reports a disliked song INDIFFERENT")
  assert.equal(Model.dislikedState(v, { [v]: "LIKE" }, { [v]: true }, null), false)
  assert.equal(Model.dislikedState(v, { [v]: "INDIFFERENT" }, {}, "DISLIKE"), false)
  assert.equal(Model.dislikedState(v, {}, {}, "INDIFFERENT"), false)
})

test("after an edit: stale tokens and the owner's callback", () => {
  assert.equal(Model.staleToken({ ok: false, error: { code: "UPSTREAM", message: "out of date", stale: true } }), true)
  assert.equal(Model.staleToken({ ok: false, error: { code: "UPSTREAM", message: "HTTP 500" } }), false,
    "any other server error is no stale token")
  assert.equal(Model.staleToken({ ok: false, error: { code: "UPSTREAM", stale: true, unknown: true } }), false)
  assert.equal(Model.staleToken({ ok: false, error: { code: "NETWORK" } }), false)
  assert.equal(Model.staleToken({ ok: true }), false)
  const reload = () => true
  assert.equal(Model.editCallbackOf({ editCallback: () => reload }), reload)
  assert.equal(Model.editCallbackOf({}), null)
  assert.equal(Model.editCallbackOf(null), null)
})

test("S and D are keys of their own, next to L", () => {
  assert.equal(Model.keyId("S"), "save")
  assert.equal(Model.keyId("D"), "dislike")
  assert.equal(Model.keyId("s"), "shuffle", "lower case s still shuffles")
  const order = Model.KEYS.map(k => k.id)
  assert.deepEqual(order.slice(order.indexOf("like"), order.indexOf("like") + 3), ["like", "save", "dislike"])
  assert.equal(Model.KEYS.find(k => k.id === "save").group, "Song")
  assert.ok(!/one playing.*again to undo/.test(Model.KEYS.find(k => k.id === "dislike").title),
    "D on the song playing skips it: a second D acts on the next song")
})
