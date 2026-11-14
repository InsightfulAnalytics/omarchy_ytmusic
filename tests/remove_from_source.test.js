const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

// Remove from the playlist playing: the queue's rows, their menus and Now
// take a song out of your playlist the queue plays from.

const mine = { playlistId: "PLmine", title: "Mockasin", owned: true }
const theirs = { playlistId: "PLtheirs", title: "Theirs", owned: false }
const context = (playlistId, extra) => Object.assign({ playlistId, title: "Mockasin", radio: false }, extra || {})

test("sourcePlaylist: only your own playlist, played as a list", () => {
  assert.deepEqual(Model.sourcePlaylist(context("PLmine"), [mine, theirs]), { playlistId: "PLmine", title: "Mockasin" })
  assert.deepEqual(Model.sourcePlaylist(context("VLPLmine"), [mine]), { playlistId: "PLmine", title: "Mockasin" }, "VL prefix")
  assert.deepEqual(Model.sourcePlaylist(context("PLmine"), [{ ...mine, playlistId: "VLPLmine" }]).playlistId, "PLmine")
  assert.equal(Model.sourcePlaylist(context("PLtheirs"), [mine, theirs]), null, "not yours")
  assert.equal(Model.sourcePlaylist(context("PLmine", { radio: true }), [mine]), null, "a radio from it")
  assert.equal(Model.sourcePlaylist(context("PLmine"), []), null, "library not read yet")
  assert.equal(Model.sourcePlaylist(null, [mine]), null)
  assert.equal(Model.sourcePlaylist(context(null), [mine]), null)
  assert.equal(Model.sourcePlaylist(context("LM"), [{ playlistId: "LM", owned: true }]), null, "Liked Music")
  assert.equal(Model.sourcePlaylist(context("RDAMVMabc"), [{ playlistId: "RDAMVMabc", owned: true }]), null, "a mix")
  assert.equal(Model.sourcePlaylist(context("PLmine", { title: "" }), [mine]).title, "Mockasin", "the library's title")
})

test("removableFromSource: not autoplay's songs, nor one already taken out", () => {
  const source = { playlistId: "PLmine", title: "Mockasin" }
  const entry = { videoId: "aaaaaaaaaaa", origin: "user" }
  assert.equal(Model.removableFromSource(source, entry, {}), true)
  assert.equal(Model.removableFromSource(source, { ...entry, origin: "auto" }, {}), false)
  assert.equal(Model.removableFromSource(source, entry, { aaaaaaaaaaa: "PLmine" }), false)
  assert.equal(Model.removableFromSource(source, entry, { aaaaaaaaaaa: "PLother" }), true)
  assert.equal(Model.removableFromSource(null, entry, {}), false)
  assert.equal(Model.removableFromSource(source, null, {}), false)
})

test("playlistEntryOf: the track's own entry, else the song's first", () => {
  const tracks = [{ videoId: "a", setVideoId: "S1" }, { videoId: "b", setVideoId: "S2" }, { videoId: "a", setVideoId: "S3" }]
  assert.deepEqual(Model.playlistEntryOf(tracks, { videoId: "a" }), { videoId: "a", setVideoId: "S1" })
  assert.deepEqual(Model.playlistEntryOf(tracks, { videoId: "a", setVideoId: "S3" }), { videoId: "a", setVideoId: "S3" })
  assert.deepEqual(Model.playlistEntryOf(tracks, { videoId: "a", setVideoId: "Sx" }), { videoId: "a", setVideoId: "S1" })
  assert.equal(Model.playlistEntryOf(tracks, { videoId: "c" }), null)
  assert.equal(Model.playlistEntryOf([{ videoId: "a" }], { videoId: "a" }), null, "no setVideoId: not removable")
  assert.equal(Model.playlistEntryOf(null, { videoId: "a" }), null)
})

test("the menu: Remove from <playlist> after Remove from queue, signed in", () => {
  const song = { type: "track", videoId: "aaaaaaaaaaa", title: "Make It Rain" }
  const source = { playlistId: "PLmine", title: "Mockasin" }
  const ctx = { pageKind: "queue", removable: true, removeTitle: "Remove from queue", signedIn: true, sourcePlaylist: source }
  const actions = Model.menuActions(song, ctx)
  const ids = actions.map(a => a.id)
  assert.equal(ids.indexOf("removeFromSource"), ids.indexOf("remove") + 1)
  assert.equal(actions.find(a => a.id === "removeFromSource").title, "Remove from Mockasin")
  assert.ok(!Model.menuActions(song, { ...ctx, sourcePlaylist: null }).some(a => a.id === "removeFromSource"))
  assert.ok(!Model.menuActions(song, { ...ctx, signedIn: false }).some(a => a.id === "removeFromSource"))
  assert.ok(!Model.menuActions(song, { ...ctx, page: true }).some(a => a.id === "removeFromSource"))
  assert.equal(Model.removeFromSourceTitle({ playlistId: "PLmine", title: "" }), "Remove from playlist")
})
