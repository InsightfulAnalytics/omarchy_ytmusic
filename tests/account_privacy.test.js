// Account and privacy edge cases (YtmModel side): no library sort order is kept,
// typed searches have their own cache key, each play
// waiting for history keeps the identity it was made under, and
// your own channel's name never becomes a queue's context title.
const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const NOW = Date.UTC(2026, 10, 11, 12, 0, 0)
const MIN = 60 * 1000
const id11 = n => ("v" + String(n).padStart(10, "0")).slice(0, 11)
// The worker's keys (login:brandHash:sessionHash, ytm_account.identity_summary).
const OWN = "0::abcdef012345"
const BRAND = "0:0123456789ab:abcdef012345"

// ---- ------------------------------------------------------------------------------

test("a player.json that still has libraryOrders loses them on the next save", () => {
  const saved = '{"version":2,"libraryChip":"songs","libraryOrders":{"playlists":"a_to_z","songs":"menu:2"}}\n'
  const prefs = Model.parsePrefs(saved)
  assert.equal(prefs.libraryChip, "songs", "the chip stays")
  assert.equal("libraryOrders" in prefs, false)
  assert.equal(Model.serializePrefs(prefs).indexOf("libraryOrders"), -1)
  assert.equal(Model.normalizePref("libraryOrders", { songs: "a_to_z" }), undefined)
})

test("the chip names the order YouTube says the list is in", () => {
  // An unordered read: the worker marks YouTube's selected option as `order`.
  const list = Model.libraryList({ items: [], orders: [{ index: "recently_added", title: "Recently saved", selected: false },
    { index: "z_to_a", title: "Z to A", selected: true }], order: "z_to_a" })
  assert.equal(list.current, "z_to_a")
  assert.equal(Model.orderTitle(list.orders, list.current), "Z to A")
  assert.equal(Model.nextOrder(list.orders, list.current), "recently_added", "O sends the one after it")
})

// ---- ------------------------------------------------------------------------------

test("a typed search and the submitted one never share a cache entry", () => {
  assert.equal(Model.searchKey("Radiohead", "", ""), "|radiohead", "a submitted search keeps its key")
  assert.equal(Model.searchKey("Radiohead", "", "", false, true), "|radiohead|typed")
  assert.equal(Model.searchKey("radiohead", "songs", "", true, true), "songs|radiohead||exact|typed")
  assert.notEqual(Model.searchKey("q", "", "", false, true), Model.searchKey("q", "", ""))
  assert.equal(Model.searchKey("q", "", "", false, false), Model.searchKey("q", "", ""))
})

// ---- ------------------------------------------------------------------------------

test("a play keeps the identity key it was made under, through the file", () => {
  let pending = Model.withPendingPlay([], id11(1), NOW - 3 * MIN, OWN)
  pending = Model.withPendingPlay(pending, id11(2), NOW - 2 * MIN, "")
  pending = Model.withPendingPlay(pending, id11(3), NOW - MIN, BRAND)
  assert.deepEqual(pending.map(p => p.key), [OWN, BRAND], "a play made before any identity was known is not kept")
  const back = Model.parsePlayState(Model.playStateText({ plays: pending, notice: true }, NOW), NOW)
  assert.deepEqual(back.plays, pending)
  const odd = Model.prunePending([{ videoId: id11(4), at: NOW, key: "Tim's account" }, { videoId: id11(5), at: NOW, key: 7 },
    { videoId: id11(6), at: NOW, key: "0:" }, { videoId: id11(7), at: NOW, key: "0:0123456789ab" }], NOW)
  assert.deepEqual(odd, [], "no name, no number, no key of the old forms: such plays are dropped")
})

test("a batch holds the plays of one identity, and its params name it", () => {
  const pending = [
    { videoId: id11(1), at: NOW - 20 * MIN, key: OWN },
    { videoId: id11(2), at: NOW - 19 * MIN, key: BRAND },
    { videoId: id11(3), at: NOW - 18 * MIN, key: OWN },
  ]
  const batch = Model.reportBatch(pending, NOW, true)
  assert.deepEqual(batch.map(p => p.videoId), [id11(1), id11(3)])
  assert.deepEqual(Model.reportParams(batch), { videoIds: [id11(1), id11(3)], identityKey: OWN })
  const rest = pending.filter(p => p.key !== OWN)
  assert.deepEqual(Model.reportBatch(rest, NOW, true).map(p => p.videoId), [id11(2)])
  const unknown = Model.prunePending([{ videoId: id11(4), at: NOW - MIN }], NOW)
  assert.deepEqual(Model.reportBatch(unknown, NOW, true), [], "a play made before any worker said who it acts as never goes")
  assert.deepEqual(Model.reportParams([]), { videoIds: [] })
})

test("the worker refusing a batch (another identity) drops it; a retry keeps its key", () => {
  const pending = [{ videoId: id11(1), at: NOW - MIN, key: OWN, sent: true }, { videoId: id11(2), at: NOW, key: OWN }]
  const refused = Model.afterReport(pending, [pending[0]], {
    ok: false, error: { code: "BAD_REQUEST", message: "The account in use changed after these plays were asked for; nothing was sent" }
  }, NOW)
  assert.deepEqual(refused.pending.map(p => p.videoId), [id11(2)])
  assert.equal(refused.dropped, 1)
  const later = Model.afterReport(pending, [pending[0]], { ok: false, error: { code: "NETWORK", message: "offline" } }, NOW)
  assert.equal(later.pending[0].key, OWN, "a play that waits after a passing failure stays the identity's")
  assert.equal(later.pending[0].retryAt > NOW, true)
})

// ---- ------------------------------------------------------------------------------

test("your own channel's page is played as Your channel, never by its name", () => {
  assert.equal(Model.contextTitle({ kind: "myChannel" }, { title: "Owner Name" }, "Owner Name"), "Your channel")
  assert.equal(Model.contextTitle({ kind: "user" }, { title: "Owner Name", header: { own: true } }, "Owner Name"),
               "Your channel", "your channel opened as a profile (a playlist's author)")
  assert.equal(Model.contextTitle({ kind: "user" }, { title: "Someone", header: { own: false } }, "Someone"), "Someone")
  assert.equal(Model.contextTitle({ kind: "playlist" }, { title: "Road trip" }, "Road trip"), "Road trip")
  assert.equal(Model.contextTitle({ kind: "myChannel" }, null, ""), "Your channel", "before the page loads")
  assert.equal(Model.ownChannelPage(null, null), false)
  // What queue.json and the status then hold.
  const text = Model.queueStateText({ pid: 1, context: { playlistId: null, title: Model.contextTitle({ kind: "myChannel" }, null, "Owner Name"), radio: false } })
  assert.equal(text.indexOf("Owner Name"), -1)
  assert.equal(Model.playingFrom({ title: "Your channel" }, null), "Your channel")
})

test("a switch names the identity its account list was read as", () => {
  const row = { brand: false, pageId: null, authUser: 1, listKey: OWN }
  assert.deepEqual(Model.switchParams(row), { pageId: null, authUser: 1, identityKey: OWN })
  assert.deepEqual(Model.switchParams(Object.assign({}, row, { listKey: "not a key" })), { pageId: null, authUser: 1 })
  const fs = require("node:fs")
  const service = fs.readFileSync(require("node:path").join(__dirname, "..", "Service.qml"), "utf8")
  assert.ok(service.includes("row.listKey = listKey"), "Service.loadAccounts tags each row")
})

test("an order on a page goes with the identity it was picked under", () => {
  const fs = require("node:fs")
  const view = fs.readFileSync(require("node:path").join(__dirname, "..", "components", "CollectionView.qml"), "utf8")
  assert.ok(view.includes("extra.identityKey = orderKey"), "every read in the order names it")
  assert.ok(/orderKey = service && typeof service\._identityKey === "string"/.test(view), "taken when O picks it")
  assert.ok(view.includes("if (root._orderOutlived()) root._dropOrder()"), "dropped after a switch")
  const ref = Model.pageRef({ type: "page", browseId: "FEmusic_library_landing" }, { order: 1, identityKey: OWN })
  assert.ok(!ref || ref.params.identityKey === OWN)
})
