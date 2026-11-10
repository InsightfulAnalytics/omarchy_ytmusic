const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

// Song tokens (G2): Save to library and Pin to Listen again on the song
// playing and every queue row (the queue keeps no tokens; the Store holds
// them in memory and Service.withLiveTokens puts them back), and the pin on
// podcast and episode pages.

const id11 = n => ("vid" + n).padEnd(11, "x")
const lib = { add: "ADD", remove: "REMOVE" }
const pins = { pin: "PIN", unpin: "UNPIN" }
const song = (n, extra = {}) => ({ type: "track", videoId: id11(n), title: "Song " + n, artistText: "A",
  artists: [{ name: "A", id: "UCa" }], album: { name: "Al", id: "MPREb_al" }, kind: "song", ...extra })
const withTokens = (n, extra = {}) => song(n, { inLibrary: false, libraryTokens: lib, pinned: false,
  listenAgainTokens: pins, ...extra })
const ids = (item, ctx) => Model.menuActions(item, ctx).map(a => a.id)
const action = (item, ctx, id) => Model.menuActions(item, ctx).find(a => a.id === id)
const nowCtx = { signedIn: true, pageKind: "nowPlaying", isCurrent: true }
const queueCtx = { signedIn: true, pageKind: "queue", removable: true, removeTitle: "Remove from queue" }

test("liveTokensOf: the library and pin pairs with their states, only where a token is", () => {
  assert.deepEqual(Model.liveTokensOf(withTokens(1, { inLibrary: true })),
    { inLibrary: true, libraryTokens: lib, pinned: false, listenAgainTokens: pins })
  // A nowContext's `current` has the same keys and no type.
  assert.deepEqual(Model.liveTokensOf({ inLibrary: false, libraryTokens: lib, pinned: true, listenAgainTokens: pins }),
    { inLibrary: false, libraryTokens: lib, pinned: true, listenAgainTokens: pins })
  assert.deepEqual(Model.liveTokensOf(song(2, { inLibrary: true, libraryTokens: lib, pinned: true })),
    { inLibrary: true, libraryTokens: lib, pinned: null, listenAgainTokens: null }, "a state without its tokens is not kept")
  assert.deepEqual(Model.liveTokensOf(song(3, { listenAgainTokens: { pin: null, unpin: "U" } })).listenAgainTokens,
    { pin: null, unpin: "U" })
  assert.equal(Model.liveTokensOf(song(4, { inLibrary: true, pinned: false })), null, "a disk copy's row")
  assert.equal(Model.liveTokensOf(song(5, { libraryTokens: { add: null, remove: null }, listenAgainTokens: {} })), null)
  assert.equal(Model.liveTokensOf(null), null)
  assert.equal(Model.liveTokensOf({ inLibrary: null, libraryTokens: null, pinned: null, listenAgainTokens: null }), null,
    "signed out: nowContext's current has no tokens")
})

test("liveTokensPut / liveTokensTrim: copy on write, a disk copy never wipes what is held", () => {
  const empty = {}
  assert.equal(Model.liveTokensPut(empty, [song(1), null, { title: "no id", libraryTokens: lib }]), empty, "nothing to keep")
  const one = Model.liveTokensPut(empty, [withTokens(1), song(2)])
  assert.notEqual(one, empty)
  assert.deepEqual(Object.keys(one), [id11(1)])
  assert.deepEqual(empty, {}, "the old map is untouched")
  assert.equal(Model.liveTokensPut(one, [song(1, { inLibrary: true })]), one, "a row without tokens keeps the held ones")
  const fresh = Model.liveTokensPut(one, [withTokens(1, { inLibrary: true, libraryTokens: { add: "A2", remove: "R2" } })])
  assert.equal(fresh[id11(1)].inLibrary, true, "a later read replaces it")
  assert.equal(fresh[id11(1)].libraryTokens.add, "A2")
  const two = Model.liveTokensPut(fresh, [withTokens(3)])
  assert.equal(Model.liveTokensTrim(two, { [id11(1)]: {}, [id11(3)]: {} }), two, "all still held: same map")
  const trimmed = Model.liveTokensTrim(two, { [id11(3)]: {} })
  assert.deepEqual(Object.keys(trimmed), [id11(3)])
  assert.deepEqual(Model.liveTokensTrim(two, null), {})
})

test("nowCurrentTokens / nowContextTokens: the song's own `current`, else its up-next entry", () => {
  const context = { videoId: id11(1), current: { inLibrary: true, libraryTokens: lib, pinned: false, listenAgainTokens: pins },
                    items: [withTokens(2, { inLibrary: true }), song(3)] }
  assert.deepEqual(Model.nowCurrentTokens(context), { videoId: id11(1), inLibrary: true, libraryTokens: lib, pinned: false,
                                                      listenAgainTokens: pins })
  assert.equal(Model.nowCurrentTokens({ videoId: id11(1), current: null }), null)
  assert.equal(Model.nowCurrentTokens(null), null)
  assert.equal(Model.nowContextTokens(context, id11(1)).inLibrary, true)
  assert.equal(Model.nowContextTokens(context, id11(2)).inLibrary, true, "an up-next song")
  assert.equal(Model.nowContextTokens(context, id11(3)), null, "an up-next song without tokens")
  assert.equal(Model.nowContextTokens(context, id11(9)), null)
  assert.equal(Model.nowContextTokens(null, id11(1)), null)
  // Every key the queue strips is one withLiveTokens can put back.
  for (const key of ["libraryTokens", "listenAgainTokens"]) assert.ok(Model.TOKEN_KEYS.includes(key), key)
})

test("withLiveTokens: a copy with the pairs the track lacks, from the first source that has each", () => {
  const stripped = Model.withoutTokens(withTokens(1))
  assert.equal(stripped.libraryTokens, undefined, "the queue's copy has none")
  const held = Model.liveTokensOf(withTokens(1, { inLibrary: true }))
  const later = { inLibrary: false, libraryTokens: { add: "A2", remove: "R2" }, pinned: true, listenAgainTokens: { pin: "P2", unpin: "U2" } }
  const filled = Model.withLiveTokens(stripped, [null, held, later])
  assert.notEqual(filled, stripped)
  assert.equal(stripped.libraryTokens, undefined, "the queue's track is not changed")
  assert.deepEqual(filled.libraryTokens, lib)
  assert.equal(filled.inLibrary, true)
  assert.deepEqual(filled.listenAgainTokens, pins)
  assert.equal(filled.pinned, false)
  assert.equal(filled.title, stripped.title)
  // Each pair on its own: a pin-only source fills the pin, the next the library.
  const mixed = Model.withLiveTokens(stripped, [{ pinned: true, listenAgainTokens: { pin: "P3", unpin: "U3" }, inLibrary: null, libraryTokens: null }, held])
  assert.equal(mixed.listenAgainTokens.pin, "P3")
  assert.equal(mixed.pinned, true)
  assert.deepEqual(mixed.libraryTokens, lib)
  // A row that came with its own tokens keeps them.
  const own = withTokens(2)
  assert.equal(Model.withLiveTokens(own, [later]), own)
  assert.equal(Model.withLiveTokens(stripped, [null, null]), stripped, "nothing held: the track itself")
  assert.equal(Model.withLiveTokens(null, [held]), null)
  // What the queue would save from it is token-free again.
  assert.deepEqual(Object.keys(Model.withoutTokens(filled)).filter(k => /Tokens?$/.test(k)), [])
})

test("menus: the song playing and a queue row offer Save to library and Pin once their tokens are back", () => {
  const stripped = Model.withoutTokens(withTokens(1))
  for (const ctx of [nowCtx, queueCtx]) {
    const before = ids(stripped, ctx)
    assert.ok(!before.includes("saveSong") && !before.includes("pin"), ctx.pageKind + ": no tokens, no entries")
    const item = Model.withLiveTokens(stripped, [Model.liveTokensOf(withTokens(1))])
    const after = ids(item, ctx)
    assert.ok(after.includes("saveSong"), ctx.pageKind + " Save")
    assert.ok(after.includes("pin"), ctx.pageKind + " Pin")
    assert.equal(action(item, ctx, "saveSong").title, "Save to library")
    assert.equal(action(item, { ...ctx, inLibrary: true }, "saveSong").title, "Remove from library")
    assert.equal(action(item, ctx, "pin").title, "Pin to Listen again")
    assert.equal(action(item, { ...ctx, pinned: true }, "pin").title, "Unpin from Listen again")
    // As on the web: Save after Start radio, Pin after Copy link.
    assert.ok(after.indexOf("radio") < after.indexOf("saveSong") && after.indexOf("share") < after.indexOf("pin"))
    assert.ok(!ids(item, { ...ctx, signedIn: false }).includes("saveSong"), "signed out")
  }
  // Now's page menu (M) is the song's too.
  const item = Model.withLiveTokens(stripped, [Model.liveTokensOf(withTokens(1))])
  const page = ids(item, { signedIn: true, pageKind: "now", page: true, isCurrent: true })
  assert.ok(page.includes("saveSong") && page.includes("pin"))
  // S on the filled track saves the song.
  assert.equal(Model.saveAction(item, {}), "saveSong")
  assert.equal(Model.saveTarget(item, {}), "row")
  assert.equal(Model.saveAction(stripped, {}), "", "without them S has nothing to do")
})

test("menus: Save and Pin hand the owner's edit callback the item they act on", () => {
  const item = Model.withLiveTokens(Model.withoutTokens(withTokens(1)), [Model.liveTokensOf(withTokens(1))])
  const seen = []
  const owner = { editCallback: function(target) { seen.push(target); return () => false } }
  const sent = []
  const service = {
    setSongLibrary: (track, add, callback) => sent.push(["songLibrary", track.videoId, add, typeof callback]),
    setPinned: (target, pin, callback, source) => sent.push(["pin", target.videoId, pin, typeof callback, source])
  }
  action(item, queueCtx, "saveSong").run({ item, ctx: queueCtx, owner, service })
  action(item, { ...queueCtx, pinned: true }, "pin").run({ item, ctx: { ...queueCtx, pinned: true }, owner, service })
  assert.deepEqual(sent, [["songLibrary", id11(1), true, "function"], ["pin", id11(1), false, "function", null]])
  assert.deepEqual(seen.map(t => t.videoId), [id11(1), id11(1)])
  assert.equal(Model.editCallbackOf({ editCallback: () => "cb" }, item), "cb", "owners without the parameter still work")
  assert.equal(Model.editCallbackOf(null, item), null)
})

test("nowSaveNotice: S on Now never stays silent", () => {
  assert.equal(Model.nowSaveNotice(null, { signedIn: true }), "Nothing is playing")
  assert.equal(Model.nowSaveNotice(song(1), { signedIn: false }), "Sign in to use your library")
  assert.equal(Model.nowSaveNotice(song(1), { signedIn: false, menu: { state: "loading" } }), "Sign in to use your library")
  assert.equal(Model.nowSaveNotice(song(1), { signedIn: true, menu: { state: "loading" } }),
    "Its menu is still loading; try again in a moment")
  assert.equal(Model.nowSaveNotice(song(1), { signedIn: true, menu: { state: "failed", message: "offline" } }),
    "Its menu did not load: offline. Asking again; try in a moment", "a failure is named, never 'loading'")
  assert.equal(Model.nowSaveNotice(song(1), { signedIn: true, menu: { state: "failed" } }),
    "Its menu did not load. Asking again; try in a moment")
  assert.equal(Model.nowSaveNotice(song(1), { signedIn: true, menu: { state: "unread" } }),
    "Its library control is not loaded; play it or open its album", "nothing read and nothing asked")
  const karma = song(1, { title: "Karma Police", artistText: "Radiohead" })
  const none = "YouTube Music offers no library control for " + Model.mediaTitle(karma)
  assert.equal(Model.nowSaveNotice(karma, { signedIn: true, menu: { state: "read" } }), none, "only once its menu was read")
  assert.equal(Model.nowSaveNotice(karma, { signedIn: true }), none)
})

test("podcast and episode pages: the page menu pins the page, on a copy of the bound item", () => {
  const episode = { type: "track", kind: "episode", videoId: id11(7), browseId: "MPED" + id11(7), title: "Ep" }
  const page = { type: "page", kind: "episode", header: { episode, pin: { pinned: false, listenAgainTokens: pins } } }
  const item = Model.withPagePinCopy(episode, page)
  assert.notEqual(item, episode)
  assert.equal(episode.listenAgainTokens, undefined, "the bound episode is not changed")
  assert.deepEqual(item.listenAgainTokens, pins)
  assert.equal(action(item, { signedIn: true, page: true, pageKind: "episode" }, "pin").title, "Pin to Listen again")
  assert.equal(Model.pinKey(item), "track:" + id11(7), "the same key as its rows")
  const podcast = { type: "podcast", browseId: "MPSPPLx", podcastId: "PLx", title: "Show" }
  const pinned = Model.withPagePinCopy(podcast, { type: "page", kind: "podcast", header: { pin: { pinned: true, listenAgainTokens: pins } } })
  assert.equal(pinned.pinned, true)
  assert.equal(action(pinned, { signedIn: true, page: true, pageKind: "podcast" }, "pin").title, "Unpin from Listen again")
  assert.equal(Model.pinKey(pinned), "podcast:MPSPPLx")
  assert.equal(Model.withPagePinCopy(podcast, { type: "page", kind: "podcast", header: {} }), podcast, "no pin served: as it is")
  const disk = Model.withPagePinCopy(podcast, { type: "page", kind: "podcast", header: { pin: { pinned: true } } })
  assert.ok(!ids(disk, { signedIn: true, page: true, pageKind: "podcast" }).includes("pin"), "a disk copy has no tokens")
  // Episode rows and podcast cards with their own tokens offer it on any list.
  assert.ok(ids({ ...episode, pinned: false, listenAgainTokens: pins }, { signedIn: true, pageKind: "podcast" }).includes("pin"))
  assert.ok(ids({ ...podcast, pinned: false, listenAgainTokens: pins }, { signedIn: true, pageKind: "browse" }).includes("pin"))
})
