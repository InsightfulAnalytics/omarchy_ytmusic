const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

// G2 (the Listen again pin on podcast pages and episode rows) with G3 (a
// podcast's orders, filters and Find in show): both on the same page.

const pins = { pin: "PIN", unpin: "UNPIN" }
const episode = (n, extra = {}) => ({ type: "track", kind: "episode", videoId: ("ep" + n).padEnd(11, "x"), title: "Episode " + n,
  podcast: { name: "Show", id: "MPSPPLshow" }, date: "Jul 31", durationSec: 3900, description: "guest " + n,
  pinned: false, listenAgainTokens: { pin: "PIN" + n, unpin: "UNPIN" + n }, ...extra })
const ORDERS = ["Latest", "Oldest"].map((title, index) => ({ index, title, selected: index === 1 }))
const FILTERS = ["In progress", "Unplayed", "Played"].map((title, index) => ({ index, title, selected: index === 1 }))
const page = (items, extra = {}) => ({ type: "page", kind: "podcast", id: "MPSPPLshow", title: "Show", subtitle: "Podcast",
  header: { browseId: "MPSPPLshow", podcastId: "PLshow", pin: { pinned: true, listenAgainTokens: pins } },
  sections: [{ type: "section", title: "Episodes", key: "episodes", items, more: null }], cursor: null, hasMore: false,
  orders: ORDERS, order: 1, filters: FILTERS, filter: 1, message: null, ...extra })
const ids = (item, ctx) => Model.menuActions(item, ctx).map(a => a.id)

test("M on an ordered, filtered podcast page: Unpin, Find in show, Sort and Filter", () => {
  const bound = { type: "podcast", browseId: "MPSPPLshow", podcastId: "PLshow", title: "Show" }
  const item = Model.withPagePinCopy(bound, page([episode(1)]))
  assert.equal(bound.listenAgainTokens, undefined, "the bound podcast is not changed")
  const ctx = { signedIn: true, page: true, pageKind: "podcast", canFind: true, episodeOrders: true,
                episodeOrderTitle: "Oldest", episodeFilters: true, episodeFilterTitle: "Unplayed" }
  const at = ids(item, ctx)
  for (const id of ["pin", "find", "episodeOrder", "episodeFilter"]) assert.ok(at.includes(id), id)
  assert.equal(Model.menuActions(item, ctx).find(a => a.id === "pin").title, "Unpin from Listen again")
  assert.equal(Model.menuActions(item, ctx).find(a => a.id === "find").title, "Find in show")
})

test("Find in show and Show all episodes keep each episode row's pin", () => {
  const shown = page([episode(1), episode(2, { pinned: true })])
  const rows = Model.podcastRows(shown, { find: "guest 2" })
  assert.equal(rows.length, 1)
  assert.equal(rows[0].item.pinned, true)
  assert.deepEqual(rows[0].item.listenAgainTokens, { pin: "PIN2", unpin: "UNPIN2" })
  assert.equal(Model.menuActions(rows[0].item, { signedIn: true, pageKind: "podcast" }).find(a => a.id === "pin").title,
               "Unpin from Listen again")
  const empty = Model.podcastRows(page([]), {})
  assert.equal(empty[empty.length - 1].item.id, Model.CLEAR_FILTER_ID, "a filter nothing matched: Show all episodes")
})
