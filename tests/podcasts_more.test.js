const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

// Podcasts more (G3): a podcast's episode orders and filters, Find in show,
// and the artist page's Latest episodes See all.

const episode = (n, extra = {}) => ({ type: "track", kind: "episode", videoId: ("ep" + n).padEnd(11, "x"), title: "Episode " + n,
  artists: [{ name: "Show", id: "MPSPPLshow" }], artistText: "Show", podcast: { name: "Show", id: "MPSPPLshow" },
  date: "Jul 31", durationSec: 3900, progress: null, description: null, ...extra })
const ORDERS = ["Latest", "Oldest", "Popular", "Creator provided"].map((title, index) => ({ index, title, selected: index === 0 }))
const FILTERS = ["In progress", "Unplayed", "Played"].map((title, index) => ({ index, title, selected: false }))
const podcastPage = (items, extra = {}) => ({ type: "page", kind: "podcast", id: "MPSPPLshow", title: "Show", subtitle: "Podcast",
  header: { browseId: "MPSPPLshow", podcastId: "PLshow", description: null },
  sections: [{ type: "section", title: "Episodes", key: "episodes", items, more: null }], cursor: null, hasMore: false,
  orders: ORDERS, order: 0, filters: FILTERS, filter: null, message: null, ...extra })
const ids = (item, ctx) => Model.menuActions(item, ctx).map(a => a.id)

test("an order and a filter are params of their own: each is its own page", () => {
  assert.deepEqual(Model.pageExtra(1, false, 2), { order: 1, filter: 2 })
  assert.deepEqual(Model.pageExtra(null, false, 0), { filter: 0 }, "filter 0 (In progress) is a filter")
  assert.deepEqual(Model.pageExtra(2, false, null), { order: 2 })
  assert.equal(Model.pageExtra(null, false, null), null)
  assert.deepEqual(Model.pageExtra(null, true), { limit: "all" }, "S3's Load all as before")
  const item = { type: "podcast", browseId: "MPSPPLshow" }
  const plain = Model.pageRef(item, Model.pageExtra(null, false, null))
  const unplayed = Model.pageRef(item, Model.pageExtra(null, false, 1))
  const both = Model.pageRef(item, Model.pageExtra(1, false, 1))
  assert.deepEqual(unplayed.params, { browseId: "MPSPPLshow", filter: 1 })
  assert.deepEqual(both.params, { browseId: "MPSPPLshow", order: 1, filter: 1 })
  assert.equal(new Set([plain.key, unplayed.key, both.key]).size, 3, "three pages in the Store")
})

test("h / l cycle the filters with all episodes between the last and the first", () => {
  const at = (current, dx) => Model.nextEpisodeFilter(FILTERS, current, dx)
  assert.deepEqual([at(null, 1), at(0, 1), at(1, 1), at(2, 1)], [0, 1, 2, null])
  assert.deepEqual([at(null, -1), at(2, -1), at(1, -1), at(0, -1)], [2, 1, 0, null])
  assert.equal(at(7, 1), 0, "an unknown filter starts over")
  assert.equal(Model.nextEpisodeFilter([], null, 1), null)
})

test("the chip titles and the choosers tick what is shown", () => {
  assert.equal(Model.episodeOrderTitle(ORDERS, 2), "Popular")
  assert.equal(Model.episodeOrderTitle(ORDERS, null), "Latest", "the served selection")
  assert.equal(Model.episodeFilterTitle(FILTERS, null), "All episodes")
  assert.equal(Model.episodeFilterTitle(FILTERS, 1), "Unplayed")
  const orders = Model.episodeOrderChoices(ORDERS, 1)
  assert.deepEqual(orders.map(c => c.value), [0, 1, 2, 3])
  assert.deepEqual(orders.map(c => c.icon === "󰄬"), [false, true, false, false])
  const filters = Model.episodeFilterChoices(FILTERS, null)
  assert.deepEqual(filters.map(c => [c.title, c.value]), [["All episodes", null], ["In progress", 0], ["Unplayed", 1], ["Played", 2]])
  assert.equal(filters[0].icon, "󰄬")
  assert.equal(Model.episodeFilterChoices(FILTERS, 2)[3].icon, "󰄬")
  assert.deepEqual(Model.episodeFilters(podcastPage([])), FILTERS)
  assert.deepEqual(Model.episodeFilters({ type: "page", kind: "artistAlbums", filters: FILTERS }), [], "podcasts only")
})

test("Find in show: a podcast page with episodes can be found in, by title and description", () => {
  const page = podcastPage([episode(1, { title: "Ankur Moitra: AI and math" }),
                            episode(2, { title: "Tim Dillon", description: "Comedy, power and conspiracy theories" }),
                            episode(3, { title: "Regina Barzilay", description: "Deep learning for cancer diagnosis" })])
  assert.ok(Model.canFind(page))
  assert.ok(!Model.canFind(podcastPage([])), "nothing to find in")
  assert.ok(!Model.canFind({ type: "page", kind: "artist", sections: page.sections }), "a podcast only")
  const rows = Model.podcastRows(page, { find: "  CANCER  " })
  assert.deepEqual(rows.map(r => r.item.title), ["Regina Barzilay"])
  assert.equal(rows[0].sectionIndex, 0)
  assert.equal(rows[0].trackIndex, 2, "Enter plays the episodes from there")
  assert.deepEqual(Model.podcastRows(page, { find: "moitra math" }).map(r => r.trackIndex), [0], "every word, any order")
  const none = Model.podcastRows(page, { find: "zebra" })
  assert.deepEqual(none.map(r => [r.item.type, r.item.title]), [["message", "No episodes match \"zebra\""]])
  assert.equal(Model.podcastRows(page, { find: "" }).filter(r => r.item && r.item.type === "track").length, 3,
               "no text: the page's rows")
})

test("Find in show while pages are left ends in a busy Load more row, not 'no match'", () => {
  const page = podcastPage([episode(1), episode(2)], { cursor: "c.next", hasMore: true })
  const rows = Model.episodeFindRows(page, "zebra", {})
  assert.equal(rows.length, 1)
  assert.equal(rows[0].item.type, "loadMore")
  assert.equal(rows[0].item.id, Model.FIND_MORE_ID)
  assert.match(rows[0].item.busyTitle, /^Loading every episode… 2 so far$/)
  const loading = Model.episodeFindRows(podcastPage([episode(1)]), "episode", { loading: true })
  assert.deepEqual(loading.map(r => r.item.type), ["track", "loadMore"], "the last page on its way")
  const disk = Model.episodeFindRows(podcastPage([episode(1)], { hasMore: true }), "episode", {})
  assert.equal(disk[disk.length - 1].item.id, Model.FIND_MORE_ID, "a disk copy has more too")
})

test("Find in show over a list the Store capped says the search stopped there", () => {
  // The Store merges cursor pages with appendPage(..., maxCollectionItems):
  // past the cap the page ends (no cursor, capped), with the match unread.
  const first = podcastPage([episode(1), episode(2), episode(3)], { cursor: "c.next", hasMore: true })
  const next = { type: "page", kind: "podcast", sections: [{ type: "section", title: null, key: "episodes",
    items: [episode(4), episode(5, { title: "Zanzibar" })] }], cursor: "c.after", hasMore: true }
  const merged = Model.appendPage(first, next, 4)
  assert.equal(merged.capped, true)
  assert.equal(merged.cursor, null)
  const none = Model.podcastRows(merged, { find: "zanzibar" })
  assert.deepEqual(none.map(r => [r.item.type, r.item.title]),
                   [["message", "No episodes match \"zanzibar\""], ["message", "Searched the first 4 episodes only; the show has more"]])
  const some = Model.podcastRows(merged, { find: "episode 2" })
  assert.deepEqual(some.map(r => r.item.type), ["track", "message"], "a match, then the note")
  const whole = Model.podcastRows(Model.appendPage(first, { ...next, cursor: null, hasMore: false }, 3000), { find: "zanzibar" })
  assert.deepEqual(whole.map(r => r.item.title), ["Zanzibar"], "not capped: no note")
})

test("a filter nothing matched shows YouTube's message and Show all episodes", () => {
  const page = podcastPage([], { filter: 0, message: "No episodes match the filters you applied." })
  const rows = Model.podcastRows(page, {})
  assert.deepEqual(rows.map(r => r.item.type + ":" + (r.item.id || r.item.title)),
                   ["message:No episodes match the filters you applied.", "action:" + Model.CLEAR_FILTER_ID])
  assert.equal(rows[1].item.title, "Show all episodes")
  assert.equal(rows[1].item.hint, undefined, "Enter is its key: h / l cycle the filters, they do not clear one")
  assert.equal(Model.clearFilterRow(podcastPage([episode(1)], { filter: 1 })), null, "it matched something")
  assert.equal(Model.clearFilterRow(podcastPage([])), null, "no filter on")
})

test("M on a podcast: Find in show, Sort and Filter, each with its key", () => {
  const ctx = { page: true, pageKind: "podcast", canFind: true, episodeOrders: true, episodeOrderTitle: "Oldest",
                episodeFilters: true, episodeFilterTitle: "Unplayed" }
  const item = { type: "podcast", browseId: "MPSPPLshow", podcastId: "PLshow", title: "Show" }
  const menu = Model.menuActions(item, ctx)
  const find = menu.find(a => a.id === "find")
  assert.equal(find.title, "Find in show")
  const order = menu.find(a => a.id === "episodeOrder")
  const filter = menu.find(a => a.id === "episodeFilter")
  assert.equal(order.title, "Sort: Oldest")
  assert.equal(order.hint, "O")
  assert.equal(filter.title, "Filter: Unplayed")
  assert.equal(filter.hint, Model.keyHint("horizontal"))
  const at = ids(item, ctx)
  assert.ok(at.indexOf("find") < at.indexOf("episodeOrder") && at.indexOf("episodeOrder") < at.indexOf("episodeFilter"))
  // Elsewhere as before.
  const album = { type: "album", browseId: "MPREb_x", title: "A" }
  const other = Model.menuActions(album, { page: true, pageKind: "album", canFind: true })
  assert.equal(other.find(a => a.id === "find").title, "Find in this list")
  assert.ok(!ids(album, { page: true, pageKind: "album", canFind: true }).includes("episodeOrder"))
  assert.ok(!ids(item, { page: true, pageKind: "podcast" }).includes("episodeFilter"), "a podcast without chips")
  let chosen = ""
  order.run({ owner: { chooseOrder: () => { chosen = "order" } } })
  assert.equal(chosen, "order")
  filter.run({ owner: { chooseFilter: () => { chosen = "filter" } } })
  assert.equal(chosen, "filter")
})

test("f's help names a podcast's episodes", () => {
  assert.match(Model.keysReadme(), /`f` find in a page's songs \(album, playlist, Liked Music, History\), a podcast's episodes/)
})

test("an artist's Latest episodes See all opens the channel's episodes; its rows show date and length", () => {
  const more = { type: "more", title: "See all Latest episodes", sectionTitle: "Latest episodes",
                 endpoint: { browseId: "UCUwDqhJmW41JlLaAgAjWeeA", params: "P", pageType: Model.CHANNEL_EPISODES_PAGE } }
  assert.deepEqual(Model.moreTarget(more), { type: "channelEpisodes", channelId: "UCUwDqhJmW41JlLaAgAjWeeA", params: "P",
                                             title: "Latest episodes" })
  const plain = Model.moreTarget({ type: "more", sectionTitle: "Playlists by Trevor Jackson",
                                   endpoint: { browseId: "UCUwDqhJmW41JlLaAgAjWeeA", params: "Q", pageType: "MUSIC_PAGE_TYPE_ARTIST" } })
  assert.equal(plain.type, "browse", "another See all of the artist stays a browse page")
  const row = episode(14, { date: "Apr 10, 2025", durationSec: 3360, podcast: null, artists: [], artistText: "", progress: 0 })
  assert.equal(Model.itemSubtitle(row), "Apr 10, 2025 • 56 min")
})
