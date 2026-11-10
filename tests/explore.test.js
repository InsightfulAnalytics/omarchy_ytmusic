const test = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const Model = require("../YtmModel.js")

// The Explore tab (S6): the worker's explore page as rows, Charts and its
// country, Moods & genres and a mood's page.
const ROOT = path.join(__dirname, "..")
const read = file => fs.readFileSync(path.join(ROOT, file), "utf8")
const id11 = n => ("vid" + n).padEnd(11, "x")
const tr = (n, extra = {}) => ({ type: "track", videoId: id11(n), title: "Song " + n, artistText: "A", available: true, kind: "song", ...extra })
const ep = (browseId, params, pageType) => ({ browseId, params: params || null, pageType: pageType || null })
const section = (title, key, items, more) => ({ type: "section", title, key, items, more: more || null, play: null, layout: "carousel" })
const mood = (title, params) => ({ type: "mood", id: params, title, params })

const page = {
  type: "page", v: 2, kind: "explore", id: "FEmusic_explore", title: "Explore", subtitle: null, round: false, header: {},
  sections: [
    { type: "section", title: null, key: "entries", layout: "grid", more: null, play: null, items: [
      { type: "more", title: "New releases", endpoint: ep("FEmusic_new_releases"), count: null },
      { type: "more", title: "Charts", endpoint: ep("FEmusic_charts", "sgYP"), count: null },
      { type: "more", title: "Moods & genres", endpoint: ep("FEmusic_moods_and_genres"), count: null }] },
    section("New albums & singles", "newReleases", [{ type: "album", browseId: "MPREb_1", title: "A1" }], ep("FEmusic_new_releases_albums")),
    section("Top songs", "topSongs", [tr(1, { rank: "1", trend: "up" }), tr(2, { rank: "2", trend: "down" })],
            ep("VLPL4fGSI1pDJn7xvYy-bP6UFeG5tITQgScd", null, "MUSIC_PAGE_TYPE_PLAYLIST")),
    section("Moods & genres", "moods", [mood("Chill", "pChill"), mood("Focus", "pFocus")], ep("FEmusic_moods_and_genres")),
    section("Trending", "trending", [tr(3, { rank: "1", trend: "neutral" })], ep("VLOLAK5uy_trend", null, "MUSIC_PAGE_TYPE_PLAYLIST")),
    section("New music videos", "newVideos", [tr(4, { kind: "video" })], ep("FEmusic_new_releases_videos"))
  ],
  cursor: null, hasMore: false, orders: [], order: null, message: null
}
const label = row => row.header ? "# " + row.title : row.item.type + ":" + (row.item.title || "") + (row.trackIndex !== undefined ? "@" + row.trackIndex : "")

test("tabs: Explore is the fifth tab, key 5, with its view in Panel.qml", () => {
  assert.equal(Model.keyId("5"), "tab")
  assert.equal(Model.keyHint("tab"), "1-5")
  const panel = read("Panel.qml")
  const tabs = panel.slice(panel.indexOf("readonly property var tabs: ["), panel.indexOf("]", panel.indexOf("readonly property var tabs: [")))
  assert.deepEqual([...tabs.matchAll(/key: "(\w+)"/g)].map(m => m[1]), ["now", "search", "library", "home", "explore"])
  assert.equal([...tabs.matchAll(/icon: "[^"]+"/g)].length, 5, "every tab has an icon for the narrow tab row")
  assert.ok(panel.includes('if (tab === "explore") return exploreView'), "Explore's list takes the keys")
  assert.ok(/ExploreView \{\s*id: exploreView/.test(panel))
  assert.ok(panel.includes("visible: root.tab === \"explore\" && !root.page"))
  assert.ok(panel.includes("compactTabs"), "the tab row falls back to icons")
  // No text field: inputOwners is untouched.
  assert.ok(panel.includes("return [libraryView, picker, itemMenu, collectionLoader.item]"))
  assert.ok(read("components/NowPlayingView.qml").includes("Search (2), Home (4) or Explore (5)"))
})

test("exploreRows: entry rows open New releases, Charts and Moods & genres, sections end in See all", () => {
  const rows = Model.exploreRows(page)
  assert.deepEqual(rows.map(label), [
    "action:New releases", "action:Charts", "action:Moods & genres",
    "# New albums & singles", "album:A1", "more:See all New albums & singles",
    "# Top songs", "track:Song 1@0", "track:Song 2@1", "more:See all Top songs",
    "# Moods & genres", "mood:Chill", "mood:Focus", "more:See all Moods & genres",
    "# Trending", "track:Song 3@0", "more:See all Trending",
    "# New music videos", "track:Song 4@0", "more:See all New music videos"])
  const [releases, charts, moods] = rows
  assert.deepEqual(releases.item.target, { type: "browse", browseId: "FEmusic_new_releases", params: null, title: "New releases" })
  assert.deepEqual(charts.item.target, { type: "charts", title: "Charts" })
  assert.deepEqual(moods.item.target, { type: "moods", title: "Moods & genres" })
  assert.equal(new Set([releases.item.icon, charts.item.icon, moods.item.icon]).size, 3)
  assert.equal(moods.item.icon, Model.MOOD_ICON)
  // Entry rows and moods are selectable; nothing else changed from pageRows.
  assert.ok(rows.every(r => r.header || Model.selectableRow(r)))
  assert.deepEqual(rows.slice(3).map(label), Model.pageRows(page).slice(3).map(label))
  assert.deepEqual(Model.exploreRows(null), [])
})

test("Moods & genres: eight categories, then a row that shows them all (and fewer again)", () => {
  const many = Array.from({ length: 11 }, (_, i) => mood("M" + i, "p" + i))
  const p = { ...page, sections: [page.sections[0], section("Moods & genres", "moods", many, ep("FEmusic_moods_and_genres"))] }
  const closed = Model.exploreRows(p).slice(3)
  assert.deepEqual(closed.map(label), ["# Moods & genres", ...many.slice(0, 8).map(m => "mood:" + m.title),
                                       "action:All 11 moods and genres", "more:See all Moods & genres"])
  const toggle = closed[9]
  assert.equal(toggle.item.id, Model.EXPLORE_MOODS_ID)
  const open = Model.exploreRows(p, { moodsOpen: true }).slice(3)
  assert.deepEqual(open.map(label), ["# Moods & genres", ...many.map(m => "mood:" + m.title),
                                     "action:Fewer moods and genres", "more:See all Moods & genres"])
  assert.equal(Model.MOOD_PREVIEW, 8)
  // Eight or fewer: no toggle.
  assert.ok(!Model.exploreRows(page).some(r => !r.header && r.item.id === Model.EXPLORE_MOODS_ID))
})

test("the moods toggle moves the cursor: onto the first category it shows, or back onto it", () => {
  // The live count: 51 categories after 40 Top songs.
  const many = Array.from({ length: 51 }, (_, i) => mood("M" + i, "p" + i))
  const p = { ...page, sections: [page.sections[0], page.sections[2], section("Moods & genres", "moods", many, ep("FEmusic_moods_and_genres")),
                                  page.sections[4]] }
  const closed = Model.exploreRows(p)
  const open = Model.exploreRows(p, { moodsOpen: true })
  const toggleClosed = closed.findIndex(r => !r.header && r.item.id === Model.EXPLORE_MOODS_ID)
  const toggleOpen = open.findIndex(r => !r.header && r.item.id === Model.EXPLORE_MOODS_ID)
  assert.equal(toggleOpen - toggleClosed, 43)
  // Opened: the ninth category, in the toggle's old place (the rows before
  // are the same, so the view need not move).
  const first = Model.exploreMoodsCursor(open, true)
  assert.equal(first, toggleClosed)
  assert.equal(open[first].item.title, "M8")
  // Closed: the toggle again, now 43 rows up.
  assert.equal(Model.exploreMoodsCursor(closed, false), toggleClosed)
  assert.equal(Model.exploreMoodsCursor(Model.exploreRows(page), true), -1, "no toggle")
  assert.equal(Model.exploreMoodsCursor(null, false), -1)
  // ExploreView moves the cursor and the view itself once the rows are shown.
  const view = read("components/ExploreView.qml")
  assert.ok(view.includes("onRowsUpdated: root._afterMoodsToggle()"))
  assert.ok(view.includes("resultList.positionViewAtIndex(i, ListView.Contain)"))
})

test("See all: albums and videos browse, Top songs and Trending open their playlist without VL, moods its page", () => {
  const targets = Model.exploreRows(page).filter(r => !r.header && r.item.type === "more").map(r => Model.moreTarget(r.item))
  assert.deepEqual(targets, [
    { type: "browse", browseId: "FEmusic_new_releases_albums", params: null, title: "New albums & singles" },
    { type: "playlist", playlistId: "PL4fGSI1pDJn7xvYy-bP6UFeG5tITQgScd", title: "Top songs" },
    { type: "moods", title: "Moods & genres" },
    { type: "playlist", playlistId: "OLAK5uy_trend", title: "Trending" },
    { type: "browse", browseId: "FEmusic_new_releases_videos", params: null, title: "New music videos" }])
  for (const t of targets) assert.notEqual(Model.pageRef(t), null)
})

test("mood routes and page kinds", () => {
  const moods = Model.routeEndpoint({ browseId: "FEmusic_moods_and_genres", title: "Moods & genres" })
  assert.deepEqual(moods, { type: "moods", title: "Moods & genres" })
  const ref = Model.pageRef(moods)
  assert.deepEqual([ref.method, ref.params, ref.key], ["moods", {}, "moods:{}"])
  assert.equal(Model.pageKind(ref).ttlMs, Model.MOODS_TTL_MS)
  assert.equal(Model.MOODS_TTL_MS, 24 * 60 * 60 * 1000)
  // The sweep keeps it that long unseen too (not only 10 min).
  const store = read("Store.qml")
  assert.ok(store.includes("t - entry.seen >= _unseenFor(entry.ref)"))
  assert.ok(store.includes("return Math.max(unseenMs, _ttl(ref))"))
  // A category: by its params, from a row or from a shelf's See all.
  const sub = Model.routeEndpoint({ browseId: "FEmusic_moods_and_genres_category", params: "pFlow", title: "Flow state" })
  assert.deepEqual(sub, { type: "mood", id: "pFlow", params: "pFlow", title: "Flow state" })
  const fromRow = Model.pageRef(mood("Focus", "pFocus"))
  assert.deepEqual([fromRow.kind, fromRow.method, fromRow.params, fromRow.id], ["mood", "mood", { params: "pFocus" }, "pFocus"])
  assert.equal(Model.pageRef({ type: "mood", title: "Focus", params: "pFocus" }).key, fromRow.key, "the title is not part of the page")
  assert.equal(Model.pageRef({ type: "mood", title: "No params" }), null)
  assert.equal(Model.routeEndpoint({ browseId: "FEmusic_moods_and_genres_category" }).type, "browse", "no params: browse")
  // Two categories with one title stay apart (keyed by params).
  assert.notEqual(Model.itemKey(mood("Focus", "p1")), Model.itemKey(mood("Focus", "p2")))
})

test("charts: the item takes the chosen country, the ref asks for it", () => {
  assert.equal(Model.chartsCountry("au"), "AU")
  assert.equal(Model.chartsCountry("ZZ"), "ZZ")
  for (const bad of [undefined, null, "", "USA", 7, "1A"]) assert.equal(Model.chartsCountry(bad), "ZZ")
  const routed = Model.routeEndpoint({ browseId: "FEmusic_charts", params: "sgYP", title: "Charts" })
  assert.deepEqual(routed, { type: "charts", title: "Charts" })
  const opened = Model.withChartsCountry(routed, "AU")
  assert.deepEqual(opened, { type: "charts", title: "Charts", country: "AU" })
  assert.equal(routed.country, undefined, "copy on write")
  assert.equal(Model.withChartsCountry(Model.chartsItem("US"), "AU").country, "US", "a country already set stays")
  assert.equal(Model.withChartsCountry(tr(1), "AU").country, undefined)
  assert.equal(Model.withChartsCountry(null, "AU"), null)
  const ref = Model.pageRef(opened)
  assert.deepEqual([ref.kind, ref.method, ref.params, ref.key], ["charts", "charts", { country: "AU" }, 'charts:{"country":"AU"}'])
  assert.notEqual(Model.pageRef(Model.chartsItem("US")).key, ref.key, "one cache entry per country")
  // Panel opens every pushed item through it, and a replaced one too.
  const panel = read("Panel.qml")
  assert.equal(panel.split("Model.withChartsCountry(item, service ? service.prefs.chartsCountry").length - 1, 2)
})

const charts = {
  type: "page", v: 2, kind: "charts", id: "AU", title: "Charts", subtitle: "Australia",
  sections: [
    { type: "section", title: "Daily charts", key: "playlists", items: [{ type: "playlist", playlistId: "PLd", title: "Trending 20 Australia" }] },
    { type: "section", title: "Top artists", key: "artists", items: [
      { type: "artist", channelId: "UCa", name: "A", subtitle: "8.22M", rank: "1", trend: "up" },
      { type: "artist", channelId: "UCb", name: "B", subtitle: "2M", rank: "2", trend: "neutral" }] }],
  country: { code: "AU", label: "Australia" },
  countries: [{ code: "AU", label: "Australia" }, { code: "ZZ", label: "Global" }, { code: "AR", label: "Argentina" }, { code: "bad", label: "x" }]
}

test("charts page: a Country row first, then the shelves; the chooser ticks the country shown", () => {
  const shown = Model.PAGE_KINDS.charts.adapt(charts)
  assert.notEqual(shown, charts)
  assert.equal(charts.sections.length, 2, "the reply is not changed")
  const rows = Model.pageRows(shown)
  assert.deepEqual(rows.map(label), ["action:Country: Australia", "# Daily charts", "playlist:Trending 20 Australia",
                                     "# Top artists", "artist:", "artist:"])
  assert.equal(rows[0].item.id, Model.CHARTS_COUNTRY_ID)
  assert.equal(rows[0].item.hint, "O")
  assert.ok(Model.selectableRow(rows[0]))
  assert.equal(Model.completeTracks(shown).length, 0, "a page without songs")
  const choices = Model.countryChoices(shown)
  assert.deepEqual(choices.map(c => [c.id, c.title, c.value]), [
    ["country:AU", "Australia", "AU"], ["country:ZZ", "Global", "ZZ"], ["country:AR", "Argentina", "AR"]])
  assert.equal(choices[0].icon, "󰄬")
  assert.notEqual(choices[1].icon, "󰄬")
  assert.deepEqual(Model.chartsCountryOf(null), { code: "ZZ", label: "Global" })
  assert.equal(Model.chartsCountryTitle({ country: { code: "ZZ", label: "Global" } }), "Country: Global")
  assert.deepEqual(Model.countryChoices(null), [])
  assert.equal(Model.PAGE_KINDS.charts.adapt(null), null)
})

test("rank and trend lead a chart row's subtitle", () => {
  assert.equal(Model.itemSubtitle(tr(1, { rank: "1", trend: "up" })), "#1 " + Model.TREND_ICONS.up + " • A")
  assert.equal(Model.itemSubtitle(tr(1, { rank: "12", trend: "down", kind: "video" })), "#12 " + Model.TREND_ICONS.down + " • A • Video")
  assert.equal(Model.itemSubtitle(charts.sections[1].items[1]), "#2 " + Model.TREND_ICONS.neutral + " • 2M")
  assert.equal(Model.itemSubtitle({ type: "podcast", title: "P", rank: "3", trend: null }), "#3")
  // Any other row is as before.
  assert.equal(Model.itemSubtitle(tr(1)), "A")
  assert.equal(Model.itemSubtitle({ type: "artist", channelId: "UCa", name: "A" }), "Artist")
  assert.equal(Model.rankText(tr(1, { rank: null })), "")
})

test("a chart song's rank stays on the chart: the queue keeps it without", () => {
  const ranked = tr(1, { rank: "2", trend: "up" })
  const kept = Model.withoutRank(ranked)
  assert.notEqual(kept, ranked, "copy on write")
  assert.equal(ranked.rank, "2")
  assert.equal(kept.rank, undefined)
  assert.equal(kept.trend, undefined)
  assert.equal(kept.videoId, ranked.videoId)
  assert.equal(Model.itemSubtitle(kept), "A", "a queued chart song reads as any song")
  assert.ok(!Model.itemSubtitle(Model.withoutRank(tr(2, { rank: "1", trend: "neutral", kind: "video" }))).includes("#"))
  const plain = tr(3)
  assert.equal(Model.withoutRank(plain), plain, "nothing to drop: the same object")
  assert.equal(Model.withoutRank(null), null)
  // Every track the queue takes passes Service.remember.
  assert.ok(read("Service.qml").includes("next[track.videoId] = Model.withoutRank(Model.withoutTokens(track))"))
})

test("a section's songs play with the section as context; the chart playlist without VL", () => {
  assert.deepEqual(Model.exploreContext(page, 2), { playlistId: "PL4fGSI1pDJn7xvYy-bP6UFeG5tITQgScd", title: "Top songs", radio: false })
  assert.deepEqual(Model.exploreContext(page, 5), { playlistId: null, title: "New music videos", radio: false })
  assert.deepEqual(Model.exploreContext(null, 0), { playlistId: null, title: "Explore", radio: false })
  assert.deepEqual(Model.sectionTracks(page, 2).map(t => t.title), ["Song 1", "Song 2"])
})

test("page menus: the Country entry on Charts, Refresh on Explore; mood rows have no menu", () => {
  const ids = (item, ctx) => Model.menuActions(item, ctx).map(a => a.id)
  assert.deepEqual(ids(Model.chartsItem("AU"), { page: true, pageKind: "charts", countryLabel: "Australia", hasTracks: false }),
                   ["chartsCountry"])
  const country = Model.menuActions(null, { page: true, pageKind: "charts", countryLabel: "Australia" })[0]
  assert.equal(country.title, "Country: Australia")
  assert.equal(country.hint, "O")
  assert.deepEqual(ids(null, { page: true, pageKind: "explore" }), ["exploreRefresh"])
  assert.deepEqual(ids(null, { page: true, pageKind: "home" }), ["refresh"])
  assert.equal(Model.menuKind(mood("Focus", "p")), "")
  // Rows on Explore get the usual menus.
  assert.ok(ids(tr(1, { rank: "1" }), { pageKind: "explore" }).includes("playNext"))
  let chosen = 0
  Model.menuAction("chartsCountry").run({ owner: { chooseCountry: () => chosen++ } })
  assert.equal(chosen, 1)
})

test("the explore status line", () => {
  assert.equal(Model.exploreStatus({ page: null, loading: true, error: "" }), "Loading Explore…")
  assert.equal(Model.exploreStatus({ page: null, loading: false, error: "No network" }), "No network")
  assert.equal(Model.exploreStatus({ page, loading: true, error: "" }), "New releases, charts, moods and genres")
  assert.equal(Model.exploreStatus(null), "New releases, charts, moods and genres")
  assert.equal(Model.EXPLORE_TTL_MS, 30 * 60 * 1000)
})

test("O on Charts opens the chooser; the Country row and the stack replacement are wired", () => {
  const view = read("components/CollectionView.qml")
  assert.ok(view.includes('if (ref && ref.kind === "charts") return chooseCountry()'))
  assert.ok(view.includes("else if (item.id === Model.CHARTS_COUNTRY_ID) chooseCountry()"))
  assert.ok(view.includes('service.setPref("chartsCountry", country)'))
  const panel = read("Panel.qml")
  assert.ok(panel.includes("onChooserRequested: function(request) { root.openChooser(request) }"))
  assert.ok(panel.includes("onReplaceRequested: function(item) { root.replaceTop(item) }"))
  const list = read("components/ResultList.qml")
  assert.ok(list.includes('|| kind === "mood" ? lineComponent'), "a mood is a line row")
})
