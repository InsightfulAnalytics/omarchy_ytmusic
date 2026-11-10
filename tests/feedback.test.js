const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

// Recommendations (S14): Listen again pins, Home's Not interested and Don't
// recommend, and the taste builder's marks.

const id11 = n => ("vid" + n).padEnd(11, "x")
const song = (n, extra = {}) => ({ type: "track", videoId: id11(n), title: "Song " + n, artistText: "A",
  artists: [{ name: "A", id: "UCa" }], ...extra })
const pins = { pin: "PIN", unpin: "UNPIN" }
const album = (n, extra = {}) => ({ type: "album", browseId: "MPREb_" + n, title: "Album " + n, ...extra })
const ids = (item, ctx) => Model.menuActions(item, ctx).map(a => a.id)
const action = (item, ctx, id) => Model.menuActions(item, ctx).find(a => a.id === id)
const signedIn = { signedIn: true, pageKind: "album" }

test("pin: Pin or Unpin by state, only with the token that direction needs", () => {
  const free = song(1, { pinned: false, listenAgainTokens: pins })
  assert.equal(action(free, signedIn, "pin").title, "Pin to Listen again")
  assert.equal(action(free, { ...signedIn, pinned: true }, "pin").title, "Unpin from Listen again", "the Store's edit wins")
  assert.equal(action(song(2, { pinned: true, listenAgainTokens: pins }), signedIn, "pin").title, "Unpin from Listen again")
  assert.ok(!ids(song(3, { pinned: false, listenAgainTokens: { pin: null, unpin: "U" } }), signedIn).includes("pin"),
    "no pin token: nothing to offer")
  assert.ok(!ids(song(4, { pinned: true, listenAgainTokens: { pin: "P", unpin: null } }), signedIn).includes("pin"))
  assert.ok(!ids(song(5, { pinned: false }), signedIn).includes("pin"), "a disk copy's row has no tokens")
  assert.ok(!ids(free, { pageKind: "album" }).includes("pin"), "signed out")
  // Albums, playlists and artists from Home's cards carry the same tokens.
  for (const item of [album(1, { listenAgainTokens: pins }), { type: "playlist", playlistId: "PLx", title: "P", listenAgainTokens: pins },
                      { type: "artist", channelId: "UCx", name: "X", listenAgainTokens: pins }])
    assert.ok(ids(item, { signedIn: true, pageKind: "home" }).includes("pin"), item.type)
  // After Copy link, before Remove, as on the web.
  const order = ids(free, { ...signedIn, removable: true })
  assert.ok(order.indexOf("share") < order.indexOf("pin") && order.indexOf("pin") < order.indexOf("remove"))
})

test("pin: the page menu pins the page through its header's pin", () => {
  const page = { type: "collection", kind: "album", header: { pin: { pinned: true, listenAgainTokens: pins } } }
  const item = Model.withPagePin(album(7), page)
  assert.equal(item.pinned, true)
  assert.deepEqual(item.listenAgainTokens, pins)
  assert.equal(action(item, { signedIn: true, page: true, pageKind: "album" }, "pin").title, "Unpin from Listen again")
  const disk = Model.withPagePin(album(8), { type: "collection", header: { pin: { pinned: false } } })
  assert.ok(!ids(disk, { signedIn: true, page: true, pageKind: "album" }).includes("pin"), "a disk copy: no tokens yet")
  assert.equal(Model.withPagePin(album(9), { type: "collection", header: {} }).listenAgainTokens, undefined)
  const fromCard = Model.withPagePin(album(10, { listenAgainTokens: pins }), { type: "collection", header: { pin: { pinned: false } } })
  assert.deepEqual(fromCard.listenAgainTokens, pins, "a disk copy keeps the tokens the item was opened with")
  const artistPage = { type: "page", kind: "artist", header: { pin: { pinned: false, listenAgainTokens: pins } } }
  assert.equal(Model.pagePin(artistPage).pinned, false)
})

test("pin: keys, states over the item's own, sources and params", () => {
  assert.equal(Model.pinKey(song(1)), "track:" + id11(1))
  assert.equal(Model.pinKey(album(1, { playlistId: "OLAK5uy_x" })), "album:MPREb_1")
  assert.equal(Model.pinKey({ type: "playlist", playlistId: "VLPLx" }), "playlist:PLx")
  assert.equal(Model.pinKey({ type: "liked" }), "playlist:LM")
  assert.equal(Model.pinKey({ type: "artist", channelId: "UCx" }), "artist:UCx")
  assert.equal(Model.pinKey({ type: "podcast", browseId: "MPSPx" }), "podcast:MPSPx")
  const item = song(1, { pinned: true })
  assert.equal(Model.pinnedState(item, {}), true)
  assert.equal(Model.pinnedState(item, { ["track:" + id11(1)]: false }), false, "an edit made here")
  const edit = Model.optimistic({}, Model.pinKey(item), false)
  assert.equal(Model.pinnedState(item, Model.restoreStates(edit.map, edit.previous)), true, "rolled back")
  assert.deepEqual(Model.pinSource({ kind: "album", params: { browseId: "MPREb_1" } }), { browseId: "MPREb_1" })
  assert.deepEqual(Model.pinSource({ kind: "playlist", params: { playlistId: "VLPLx", related: true } }), { playlistId: "PLx" })
  assert.deepEqual(Model.pinSource({ kind: "liked", params: {} }), { playlistId: "LM" })
  assert.deepEqual(Model.pinSource({ kind: "artist", params: { channelId: "UCx" } }), { channelId: "UCx" })
  assert.equal(Model.pinSource({ kind: "browse", params: { browseId: "FEmusic_x" } }), null)
  assert.deepEqual(Model.feedbackParams("T", { playlistId: "PLx", other: 1 }), { tokens: ["T"], playlistId: "PLx" })
  assert.deepEqual(Model.feedbackParams("T", null), { tokens: ["T"] })
})

test("pin: the entry calls setPinned with the owner's callback and the page", () => {
  const calls = []
  const service = { setPinned: (...args) => calls.push(args) }
  const owner = { editCallback: () => "cb" }
  const item = song(1, { pinned: false, listenAgainTokens: pins })
  const ctx = { ...signedIn, pinned: false, pinSource: { browseId: "MPREb_1" } }
  action(item, ctx, "pin").run({ item, ctx, index: 0, owner, service })
  assert.deepEqual(calls, [[item, true, "cb", { browseId: "MPREb_1" }]])
  const pinnedCtx = { ...ctx, pinned: true }
  action(item, pinnedCtx, "pin").run({ item, ctx: pinnedCtx, index: 0, owner: null, service })
  assert.deepEqual(calls[1], [item, false, null, { browseId: "MPREb_1" }], "unpin; no owner callback")
})

test("home: Not interested and Don't recommend only on Home's cards, with a confirm", () => {
  const card = album(1, { feedback: { notInterested: "NI", dontRecommend: "DR" } })
  const home = { signedIn: true, pageKind: "home" }
  assert.ok(ids(card, home).includes("notInterested") && ids(card, home).includes("dontRecommend"))
  assert.ok(action(card, home, "notInterested").confirm !== "", "a second press")
  assert.ok(action(card, home, "dontRecommend").confirm !== "")
  assert.equal(action(card, home, "dontRecommend").title, "Don't recommend artist")
  assert.equal(action({ type: "podcast", browseId: "MPSPx", title: "Pod", feedback: { dontRecommend: "DR" } }, home, "dontRecommend").title,
    "Don't recommend channel")
  assert.ok(!ids(card, { signedIn: true, pageKind: "search" }).includes("notInterested"), "not off Home")
  assert.ok(!ids(card, { pageKind: "home" }).includes("notInterested"), "signed out")
  assert.ok(!ids(album(2, { feedback: { notInterested: "NI" } }), home).includes("dontRecommend"))
  assert.ok(!ids(album(3), home).includes("notInterested"), "a disk copy's card")
  assert.equal(Model.homeFeedbackToken(card, "dontRecommend"), "DR")
  assert.equal(Model.homeFeedbackToken(album(3), "notInterested"), "")
  const calls = []
  action(card, home, "notInterested").run({ item: card, ctx: home, index: 3, owner: null,
    service: { homeFeedback: (...args) => calls.push(args) } })
  assert.deepEqual(calls, [[card, "notInterested"]])
  assert.ok(Model.TOKEN_KEYS.includes("feedback"), "kept out of the queue and session")
  assert.equal("feedback" in Model.withoutTokens(card), false)
})

test("home: a card leaves every shelf it is on and comes back where it was", () => {
  const a = album(1), b = album(2), c = album(3), s = song(9)
  const shelves = [{ title: "One", items: [a, b, c], more: null }, { title: "Two", items: [s, b] }, { title: "Three", items: [c] }]
  const cut = Model.withoutHomeItem(shelves, album(2))
  assert.deepEqual(cut.shelves.map(x => x.items.map(i => Model.itemKey(i))),
    [["album:MPREb_1", "album:MPREb_3"], ["track:" + id11(9)], ["album:MPREb_3"]])
  assert.equal(cut.shelves[2], shelves[2], "an untouched shelf is the same object")
  assert.equal(cut.shelves[0].title, "One")
  assert.deepEqual(cut.removed.map(r => [r.shelf, r.index]), [[0, 1], [1, 1]])
  assert.equal(shelves[0].items.length, 3, "the feed itself is not changed")
  const back = Model.withHomeItems(cut.shelves, cut.removed)
  assert.deepEqual(back.map(x => x.items.map(i => Model.itemKey(i))), shelves.map(x => x.items.map(i => Model.itemKey(i))))
  // Shelves appended meanwhile (Load more) keep their place; a shelf gone is skipped.
  const grown = cut.shelves.concat([{ title: "Four", items: [song(8)] }])
  assert.equal(Model.withHomeItems(grown, cut.removed)[0].items.length, 3)
  assert.equal(Model.withHomeItems([cut.shelves[0]], cut.removed).length, 1)
  // Nothing matched: the same array.
  const none = Model.withoutHomeItem(shelves, album(42))
  assert.equal(none.shelves, shelves)
  assert.deepEqual(none.removed, [])
  // Never twice: a card already back (a refresh) is not added again.
  assert.equal(Model.withHomeItems(shelves, cut.removed)[0].items.length, 3)
})

test("home: the rows follow the shelves without the card", () => {
  const shelves = [{ title: "Listen again", items: [album(1), album(2)], more: { browseId: "FEmusic_listen_again" } }]
  const before = Model.homeRows(shelves, {})
  const after = Model.homeRows(Model.withoutHomeItem(shelves, album(1)).shelves, {})
  assert.equal(before.length - after.length, 1)
  assert.ok(!after.some(r => r.item && r.item.browseId === "MPREb_1"))
})

test("taste: marks, the Add row, find, and the note", () => {
  const page = Model.tastePage({ artists: [{ name: "Radiohead" }, { name: "Björk" }, { name: "Björk" }, { name: "constructor" },
                                          { name: "" }, null] })
  assert.deepEqual(page.artists, ["Radiohead", "Björk", "constructor"], "once each, in served order")
  assert.equal(page.subtitle, "3 suggested artists")
  assert.equal(Model.pageRef(Model.tasteItem()).method, "tasteProfile")
  assert.equal(Model.isTasteMarked({}, "constructor"), false, "any name is safe as a key")
  let marks = Model.toggleTasteMark({}, "Björk")
  marks = Model.toggleTasteMark(marks, "constructor")
  assert.deepEqual(Model.tasteMarked(page, marks), ["Björk", "constructor"])
  marks = Model.toggleTasteMark(marks, "constructor")
  assert.deepEqual(Model.tasteMarked(page, marks), ["Björk"], "Enter again unmarks")
  assert.deepEqual(Model.tasteMarked(page, Model.toggleTasteMark(marks, "Gone")), ["Björk"], "only names still listed")
  const rows = Model.tasteRows(page, { marked: marks })
  assert.equal(rows[0].item.id, Model.TASTE_ADD_ID)
  assert.equal(rows[0].item.title, "Add 1 artist")
  assert.equal(rows[1].item.type, "message")
  assert.ok(/web/.test(rows[1].item.title), "the page says removals happen on the web")
  assert.deepEqual(rows.slice(2).map(r => [r.item.title, r.item.icon === "󰄲"]), [["Radiohead", false], ["Björk", true], ["constructor", false]])
  assert.equal(Model.tasteNameOf(rows[3].item.id), "Björk")
  assert.ok(Model.isTasteAction(rows[0].item.id) && Model.isTasteAction(rows[3].item.id) && !Model.isTasteAction("loadAll"))
  const found = Model.tasteRows(page, { marked: marks, find: "bjo" })
  assert.deepEqual(found.slice(2).map(r => r.item.title), ["Björk"], "accents and case ignored")
  assert.equal(Model.tasteRows(page, { marked: marks, find: "zzz" }).slice(-1)[0].item.type, "message")
  assert.ok(/mark the ones you like/.test(Model.tasteAddTitle(0, {})), "nothing marked yet says how")
  assert.ok(/Really add 2 artists/.test(Model.tasteAddTitle(2, { armed: true })), "the second press asks")
  assert.equal(Model.tasteAddTitle(2, { sending: true }), "Adding 2 artists…")
  assert.equal(Model.tasteRows(Model.tastePage({ artists: [] }), {}).slice(-1)[0].item.type, "message")
})

test("taste: Library's account menu opens it; its page menu adds and unmarks", () => {
  const library = { page: true, pageKind: "library", signedIn: true }
  // S9's Your channel, S16's Switch account, Audio quality and Content
  // location, then S15's Record plays in history, sit between it and Sign out.
  assert.deepEqual(ids(null, library),
    ["tasteProfile", "myChannel", "switchAccount", "audioQuality", "contentLocation", "recordHistory", "streamAccount", "signOut"])
  assert.ok(!ids(null, { ...library, signedIn: false }).includes("tasteProfile"))
  const pushed = []
  action(null, library, "tasteProfile").run({ item: null, ctx: library, index: -1, panel: { push: i => pushed.push(i) } })
  assert.deepEqual(pushed, [{ type: "taste", title: "Add artists you like" }])
  const taste = { page: true, pageKind: "taste", tasteMarked: 2, canFind: true, signedIn: true }
  assert.deepEqual(ids(null, taste), ["find", "tasteAdd", "tasteClear"])
  assert.equal(action(null, taste, "tasteAdd").title, "Add 2 artists")
  assert.ok(action(null, taste, "tasteAdd").confirm !== "", "the page menu asks twice too")
  assert.deepEqual(ids(null, { ...taste, tasteMarked: 0 }), ["find"])
  const owner = { calls: [], addTaste(c) { this.calls.push(["add", c]) }, clearTaste() { this.calls.push(["clear"]) } }
  action(null, taste, "tasteAdd").run({ item: null, ctx: taste, owner })
  action(null, taste, "tasteClear").run({ item: null, ctx: taste, owner })
  assert.deepEqual(owner.calls, [["add", true], ["clear"]])
})

test("home: the card's Undo token, and Home's menu offering it first", () => {
  const card = album(1, { feedback: { notInterested: "NI", dontRecommend: "DR", notInterestedUndo: "NU", dontRecommendUndo: null } })
  assert.equal(Model.homeUndoToken(card, "notInterested"), "NU")
  assert.equal(Model.homeUndoToken(card, "dontRecommend"), "", "none served")
  assert.equal(Model.homeUndoToken(card, "notInterestedUndo"), "", "only the two entries have one")
  assert.equal(Model.homeUndoToken(album(2), "notInterested"), "")
  const home = { page: true, pageKind: "home", signedIn: true }
  assert.deepEqual(ids(null, home), ["refresh"], "nothing to undo")
  assert.deepEqual(ids(null, { ...home, homeUndo: null }), ["refresh"])
  assert.deepEqual(ids(null, { ...home, homeUndo: "Album 1" }), ["homeUndo", "refresh"], "Undo first")
  assert.equal(action(null, { ...home, homeUndo: "Album 1" }, "homeUndo").title, "Undo: put Album 1 back on Home")
  assert.equal(action(null, { ...home, homeUndo: "" }, "homeUndo").title, "Undo: put it back on Home")
  assert.equal(action(null, { ...home, homeUndo: "Album 1" }, "homeUndo").confirm, "", "an undo needs no confirm")
  assert.ok(!ids(null, { ...home, signedIn: false, homeUndo: "Album 1" }).includes("homeUndo"), "signed out")
  assert.ok(!ids(null, { page: true, pageKind: "library", signedIn: true, homeUndo: "x" }).includes("homeUndo"), "Home only")
  const calls = []
  action(null, { ...home, homeUndo: "A" }, "homeUndo").run({ item: null, ctx: home, service: { undoHomeFeedback: () => calls.push(1) } })
  assert.deepEqual(calls, [1])
  assert.ok(Model.HOME_UNDO_MS >= 30000, "long enough to open the menu")
})

test("taste: Down or Enter from find lands on the first artist; marks stop at the worker's limit", () => {
  const page = Model.tastePage({ artists: [{ name: "Radiohead" }, { name: "Björk" }, { name: "Portishead" }] })
  const found = Model.tasteRows(page, { marked: Model.toggleTasteMark({}, "Björk"), find: "radio" })
  assert.equal(found[0].item.id, Model.TASTE_ADD_ID, "the Add row stays on top")
  assert.equal(Model.firstTasteArtist(found), 2)
  assert.equal(found[2].item.title, "Radiohead")
  assert.equal(Model.firstTasteArtist(Model.tasteRows(page, { find: "zzz" })), -1, "never the Add row")
  assert.equal(Model.firstTasteArtist(Model.tasteRows(page, {})), 2)
  // At TASTE_ADD_MAX marks a new mark is refused; an unmark never is.
  const many = Model.tastePage({ artists: Array.from({ length: Model.TASTE_ADD_MAX + 1 }, (_, i) => ({ name: "A" + i })) })
  let marks = {}
  for (let i = 0; i < Model.TASTE_ADD_MAX; i++) marks["a:A" + i] = true
  assert.equal(Model.tasteMarked(many, marks).length, Model.TASTE_ADD_MAX)
  assert.equal(Model.canTasteMark(many, marks, "A" + Model.TASTE_ADD_MAX), false)
  assert.equal(Model.canTasteMark(many, marks, "A0"), true, "unmarking")
  assert.equal(Model.canTasteMark(page, {}, "Radiohead"), true)
  assert.ok(Model.TASTE_ADD_MAX >= 889, "every suggestion of the live list fits in one add")
})
