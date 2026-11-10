const test = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const Model = require("../YtmModel.js")

const ROOT = path.join(__dirname, "..")
const lines = starts => starts.map((start, i) => ({ start, end: start + 1, text: "line " + i }))

test("currentLineIndex: the last line that has started, by binary search", () => {
  const list = lines([0, 26.7, 30.97, 33.25, 37.52])
  assert.equal(Model.currentLineIndex(list, 0), 0)
  assert.equal(Model.currentLineIndex(list, 26.69), 0)
  assert.equal(Model.currentLineIndex(list, 26.7), 1, "a line counts from its start")
  assert.equal(Model.currentLineIndex(list, 31), 2)
  assert.equal(Model.currentLineIndex(list, 37.52), 4)
  assert.equal(Model.currentLineIndex(list, 600), 4, "after the last line it stays lit")
})

test("currentLineIndex edges: before the first line, no lines, no time", () => {
  assert.equal(Model.currentLineIndex(lines([12, 15]), 3), -1)
  assert.equal(Model.currentLineIndex(lines([12, 15]), -1), -1)
  assert.equal(Model.currentLineIndex([], 10), -1)
  assert.equal(Model.currentLineIndex(null, 10), -1)
  assert.equal(Model.currentLineIndex(lines([0, 1]), NaN), -1)
  assert.equal(Model.currentLineIndex(lines([0, 1]), undefined), -1)
  assert.equal(Model.currentLineIndex(lines([5]), 5), 0)
  // Lines that start together (a duet): the last of them.
  assert.equal(Model.currentLineIndex(lines([0, 4, 4, 9]), 4.5), 2)
  // Many lines: every position finds the same line as a linear scan.
  const many = lines(Array.from({ length: 301 }, (_, i) => i * 1.5))
  for (let t = -1; t < 460; t += 0.37) {
    let expected = -1
    for (let i = 0; i < many.length; i++) if (many[i].start <= t) expected = i
    assert.equal(Model.currentLineIndex(many, t), expected, "at " + t)
  }
})

test("pane cycling: Queue, Lyrics, Related and round again", () => {
  assert.deepEqual(Model.NOW_PANES, ["queue", "lyrics", "related"])
  assert.equal(Model.nextNowPane("queue", 1), "lyrics")
  assert.equal(Model.nextNowPane("lyrics", 1), "related")
  assert.equal(Model.nextNowPane("related", 1), "queue")
  assert.equal(Model.nextNowPane("queue", -1), "related")
  assert.equal(Model.nextNowPane("bogus", 1), "lyrics", "an unknown pane counts as the queue")
  assert.equal(Model.nowPane("lyrics"), "lyrics")
  assert.equal(Model.nowPane(undefined), "queue")
  assert.deepEqual(Model.NOW_PANES.map(Model.nowPaneTitle), ["Queue", "Lyrics", "Related"])
  assert.ok(Model.NOW_PANES.every(p => Model.nowPaneIcon(p)))
  // The pane is a pref (F2 prefs v2): kept, and an unknown value is the queue.
  assert.equal(Model.parsePrefs(JSON.stringify({ version: 2, nowPane: "related" })).nowPane, "related")
  assert.equal(Model.parsePrefs(JSON.stringify({ version: 2, nowPane: "comments" })).nowPane, "queue")
  assert.equal(Model.withPref(Model.parsePrefs(""), "nowPane", Model.nextNowPane("queue", 1)).nowPane, "lyrics")
})

test("memo: bounded to the most recently stored, copy on write", () => {
  let memo = {}
  for (let i = 0; i < 25; i++) memo = Model.nowMemoPut(memo, "v" + i, { n: i }, 1000 + i, Model.NOW_MEMO_MAX)
  assert.equal(Object.keys(memo).length, 20)
  assert.equal(Model.nowMemoGet(memo, "v4", 2000, 0), null, "the oldest went")
  assert.deepEqual(Model.nowMemoGet(memo, "v5", 2000, 0), { n: 5 })
  // Storing a song again refreshes it: it is not the next to go.
  const before = memo
  memo = Model.nowMemoPut(memo, "v5", { n: 55 }, 3000, 20)
  assert.notEqual(memo, before)
  assert.deepEqual(Model.nowMemoGet(before, "v5", 2000, 0), { n: 5 }, "the old object is untouched")
  memo = Model.nowMemoPut(memo, "w", {}, 3001, 20)
  assert.deepEqual(Model.nowMemoGet(memo, "v5", 3001, 0), { n: 55 })
  assert.equal(Model.nowMemoGet(memo, "v6", 3001, 0), null)
  assert.equal(Object.keys(memo).length, 20)
})

test("memo: the TTL decides reuse, the sweep spares the song playing", () => {
  const ttl = Model.NOW_MEMO_TTL_MS
  assert.equal(ttl, 10 * 60 * 1000)
  let memo = Model.nowMemoPut({}, "a", { id: "a" }, 0, 20)
  memo = Model.nowMemoPut(memo, "b", { id: "b" }, 0, 20)
  memo = Model.nowMemoPut(memo, "c", { id: "c" }, ttl, 20)
  assert.deepEqual(Model.nowMemoGet(memo, "a", ttl - 1, ttl), { id: "a" })
  assert.equal(Model.nowMemoGet(memo, "a", ttl, ttl), null, "too old to reuse")
  assert.deepEqual(Model.nowMemoGet(memo, "a", ttl * 9, 0), { id: "a" }, "still there to show")
  assert.equal(Model.nowMemoGet(memo, "", 0, 0), null)
  const swept = Model.nowMemoSweep(memo, ttl, ttl, "b")
  assert.deepEqual(Object.keys(swept).sort(), ["b", "c"])
  assert.equal(Model.nowMemoSweep(swept, ttl, ttl, "b"), swept, "nothing went: the same object")
})

test("memo: a touch makes an entry the most recently used (the lyrics LRU)", () => {
  let memo = {}
  for (let i = 0; i < 20; i++) memo = Model.nowMemoPut(memo, "v" + i, { n: i }, 1000 + i, 20)
  const before = memo
  memo = Model.nowMemoTouch(memo, "v0", 5000)
  assert.notEqual(memo, before)
  assert.equal(before.v0.at, 1000, "copy on write")
  assert.deepEqual(Model.nowMemoGet(memo, "v0", 5000, 0), { n: 0 })
  // The next song in evicts v1, the least recently used, not v0.
  memo = Model.nowMemoPut(memo, "w", {}, 5001, 20)
  assert.deepEqual(Model.nowMemoGet(memo, "v0", 5001, 0), { n: 0 })
  assert.equal(Model.nowMemoGet(memo, "v1", 5001, 0), null)
  // And the sweep ages it from the touch.
  const swept = Model.nowMemoSweep(memo, 5000 + 1000, 1001, "")
  assert.deepEqual(Object.keys(swept).sort(), ["v0", "w"])
  assert.equal(Model.nowMemoTouch(memo, "missing", 9), memo, "nothing to touch: the same object")
  assert.equal(Model.nowMemoTouch(memo, "", 9), memo)
})

test("credits and related open as pages", () => {
  const song = { type: "track", videoId: "nbCOAPR33ME", title: "Karma Police", thumb: "t", kind: "song" }
  const credits = Model.pageRef(Model.creditsTarget(song))
  assert.deepEqual([credits.kind, credits.method, credits.id], ["credits", "credits", "nbCOAPR33ME"])
  assert.deepEqual(credits.params, { videoId: "nbCOAPR33ME" })
  assert.equal(Model.creditsTarget({ type: "track" }), null)
  const related = Model.pageRef(Model.relatedTarget("MPTRt_x"))
  assert.equal(related.key, 'related:{"browseId":"MPTRt_x"}')
  assert.equal(Model.relatedTarget(""), null)
  assert.equal(Model.pageKindTitle(credits), "Credits")
})

test("View credits: songs only, from any row menu", () => {
  const ids = actions => actions.map(a => a.id)
  const song = { type: "track", videoId: "v", title: "S", kind: "song" }
  assert.ok(ids(Model.menuActions(song, { pageKind: "search" })).includes("credits"))
  assert.ok(ids(Model.menuActions(song, { pageKind: "nowPlaying", isCurrent: true })).includes("credits"))
  assert.ok(!ids(Model.menuActions({ ...song, kind: "video" }, {})).includes("credits"))
  assert.ok(!ids(Model.menuActions({ ...song, kind: "episode" }, {})).includes("credits"))
  assert.ok(!ids(Model.menuActions(song, { page: true, pageKind: "now" })).includes("credits"))
  // Before Copy link, as on the web (View song credits, then Share).
  const queued = { ...song, album: { name: "A", id: "MPREb_a" } }
  const order = ids(Model.menuActions(queued, { pageKind: "queue", removable: true, removeTitle: "Remove" }))
  assert.deepEqual(order.slice(order.indexOf("goAlbum")), ["goAlbum", "credits", "share", "remove"])
  const pushed = []
  Model.menuAction("credits").run({ item: song, ctx: {}, panel: { push: item => pushed.push(item) } })
  assert.equal(pushed[0].type, "credits")
  assert.equal(pushed[0].videoId, "v")
})

test("page rows: credited names and About the artist are text rows the cursor skips", () => {
  const page = {
    type: "page", sections: [
      { type: "section", title: "Performed by", items: [{ type: "artist", channelId: "UCr", name: "Radiohead" }] },
      { type: "section", title: "Written by", items: [{ type: "text", title: "Thom Yorke" }, { type: "text", title: "Colin" }] }
    ]
  }
  const rows = Model.pageRows(page)
  assert.deepEqual(rows.map(r => r.header ? "#" : r.item.type), ["#", "artist", "#", "text", "text"])
  assert.equal(Model.nextSelectable(rows, 1, 1), 1, "nothing selectable after the artist")
  assert.equal(Model.nextSelectable(rows, -1, -1), 1)
  assert.ok(!Model.selectableRow(rows[3]))
})

test("y is the pane key, dispatched in Panel.qml and listed in help", () => {
  assert.equal(Model.keyId("y"), "nowPane")
  assert.ok(Model.keyHelp().some(row => row.item && row.item.id === "key:nowPane"))
  const panel = fs.readFileSync(path.join(ROOT, "Panel.qml"), "utf8")
  assert.ok(panel.includes('id === "nowPane"'))
  const queue = fs.readFileSync(path.join(ROOT, "components", "QueueView.qml"), "utf8")
  assert.ok(queue.includes("function cyclePane()"))
})

test("the lyrics timer runs only while the pane is shown in an open panel and synced lyrics play", () => {
  const view = fs.readFileSync(path.join(ROOT, "components", "LyricsView.qml"), "utf8")
  const timer = view.slice(view.indexOf("Timer {"), view.indexOf("}", view.indexOf("Timer {")))
  assert.match(timer, /interval: 250/)
  assert.match(timer, /running: root\.active && root\.synced && !!root\.service && root\.service\.playing/)
  const panel = fs.readFileSync(path.join(ROOT, "Panel.qml"), "utf8")
  assert.match(panel, /active: root\.opened && root\.tab === "now" && !root\.page && queueView\.pane === "lyrics"/)
})
