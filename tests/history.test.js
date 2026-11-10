// S15: plays recorded in YouTube Music history, and History entries removed
// (YtmModel's "history" section).
const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const NOW = Date.UTC(2026, 10, 10, 12, 0, 0)
const MIN = 60 * 1000
const ids = actions => actions.map(a => a.id)
const id11 = n => ("v" + String(n).padStart(10, "0")).slice(0, 11)
const play = (n, ageMs, extra) => Object.assign({ videoId: id11(n), at: NOW - ageMs }, extra || {})

test("shouldReport: 10 s of time-pos, a YouTube video, recording on", () => {
  const entry = { key: Model.playKey(3, 1), videoId: "nbCOAPR33ME" }
  assert.equal(Model.REPORT_AFTER_SEC, 10)
  assert.equal(Model.shouldReport(entry, 9.99, {}, true), false, "not yet")
  assert.equal(Model.shouldReport(entry, 10, {}, true), true, "10 s exactly")
  assert.equal(Model.shouldReport(entry, 250, {}, true), true, "joined late in a long song")
  for (const pos of [null, undefined, "12", NaN, Infinity])
    assert.equal(Model.shouldReport(entry, pos, {}, true), false, "no time-pos: " + pos)
  assert.equal(Model.shouldReport({ key: "4:2", videoId: "" }, 30, {}, true), false, "not a YouTube entry")
  assert.equal(Model.shouldReport({ key: "4:2", videoId: "local.flac" }, 30, {}, true), false)
  assert.equal(Model.shouldReport({ videoId: "nbCOAPR33ME" }, 30, {}, true), false, "no play key")
  assert.equal(Model.shouldReport(null, 30, {}, true), false)
})

test("shouldReport: recording off (the pref, or no session) reports nothing", () => {
  const entry = { key: "1:1", videoId: "nbCOAPR33ME" }
  assert.equal(Model.shouldReport(entry, 30, {}, false), false)
  assert.equal(Model.shouldReport(entry, 30, {}), false, "on must be said")
  assert.equal(Model.shouldReport(entry, 30, {}, "yes"), false)
})

test("shouldReport: once per play of an entry; a loop or a replay is a new play", () => {
  const first = { key: Model.playKey(7, 1), videoId: "nbCOAPR33ME" }
  const reported = {}
  assert.equal(Model.shouldReport(first, 11, reported, true), true)
  reported[first.key] = true
  assert.equal(Model.shouldReport(first, 12, reported, true), false, "the same play is counted once")
  assert.equal(Model.shouldReport(first, 200, [first.key], true), false, "reported as a list too")
  // Repeat one: mpv restarts the same entry (same id); the shell counts a new play.
  assert.equal(Model.loopRestart("one", 0.2, 180000), true)
  const loop = { key: Model.playKey(7, 2), videoId: "nbCOAPR33ME" }
  assert.notEqual(loop.key, first.key)
  assert.equal(Model.shouldReport(loop, 10.5, reported, true), true, "a repeat-one loop counts again")
  // The same song elsewhere in the queue is another entry.
  assert.equal(Model.shouldReport({ key: Model.playKey(9, 3), videoId: "nbCOAPR33ME" }, 10, reported, true), true)
})

test("loopRestart: only a restart near 0 s with repeat one, after the play began", () => {
  assert.equal(Model.loopRestart("one", 0, Model.LOOP_MIN_MS), true)
  assert.equal(Model.loopRestart("one", 1.49, 5000), true)
  assert.equal(Model.loopRestart("one", 0.1, 400), false, "the restart right after the file started")
  assert.equal(Model.loopRestart("one", 42, 60000), false, "a seek into the song")
  assert.equal(Model.loopRestart("all", 0, 60000), false, "repeat all starts the file again (start-file)")
  assert.equal(Model.loopRestart("off", 0, 60000), false)
  assert.equal(Model.loopRestart("one", null, 60000), false)
})

test("playCheckMs: when 10 s will have played, at the speed", () => {
  assert.equal(Model.playCheckMs(0, 1), 10300)
  assert.equal(Model.playCheckMs(0, 2), 5300, "2x reaches 10 s of the song in 5 s")
  assert.equal(Model.playCheckMs(8, 1), 2300)
  assert.equal(Model.playCheckMs(9.9, 1), 1000, "at least 1 s")
  assert.equal(Model.playCheckMs(30, 1), 1000)
  assert.equal(Model.playCheckMs(0, 0.5), 15000, "at most 15 s")
  assert.equal(Model.playCheckMs(null, null), 10300)
})

test("flushDue: worker up, 5 waiting or 15 min; nothing pending, nothing due", () => {
  assert.equal(Model.flushDue([], NOW, true), false)
  assert.equal(Model.flushDue(null, NOW, true), false)
  const one = [play(1, MIN)]
  assert.equal(Model.flushDue(one, NOW, false), false, "one play waits for the worker")
  assert.equal(Model.flushDue(one, NOW, true), true, "the worker is up anyway")
  const four = [1, 2, 3, 4].map(n => play(n, n * MIN))
  assert.equal(Model.flushDue(four, NOW, false), false)
  assert.equal(Model.flushDue(four.concat([play(5, 0)]), NOW, false), true, "the fifth starts the worker")
  assert.equal(Model.flushDue([play(1, 15 * MIN - 1)], NOW, false), false)
  assert.equal(Model.flushDue([play(1, 15 * MIN)], NOW, false), true, "15 min after the oldest")
})

test("flushDue: plays waiting after a passing failure count for nothing until their time", () => {
  const waiting = [1, 2, 3, 4, 5, 6].map(n => play(n, 20 * MIN, { retryAt: NOW + MIN }))
  assert.equal(Model.flushDue(waiting, NOW, true), false, "not even with the worker up: no retry loop")
  assert.equal(Model.flushDue(waiting, NOW + MIN, false), true, "due again once they may go")
  assert.equal(Model.nextFlushAt(waiting, NOW), NOW + MIN)
  assert.deepEqual(Model.reportBatch(waiting, NOW, false), [])
  assert.equal(Model.reportBatch(waiting, NOW, true).length, 6, "opening a panel sends them anyway")
})

test("nextFlushAt: the oldest play's 15 min, or 0", () => {
  assert.equal(Model.nextFlushAt([], NOW), 0)
  assert.equal(Model.nextFlushAt([play(1, 5 * MIN), play(2, MIN)], NOW), NOW - 5 * MIN + Model.REPORT_WAIT_MS)
})

test("pending plays: at most 200, none older than 24 h, invalid ones dropped", () => {
  let pending = []
  for (let n = 0; n < 205; n++) pending = Model.withPendingPlay(pending, id11(n), NOW - (205 - n))
  assert.equal(pending.length, Model.PENDING_MAX)
  assert.equal(pending[0].videoId, id11(5), "the oldest go first")
  assert.equal(pending[199].videoId, id11(204))
  const day = Model.PENDING_TTL_MS
  assert.deepEqual(Model.prunePending([play(1, day), play(2, day - 1)], NOW).map(p => p.videoId), [id11(2)])
  assert.deepEqual(Model.prunePending([{ videoId: "bad", at: NOW }, { videoId: id11(3) }, play(4, -5 * MIN), null,
    { videoId: id11(5), at: "now" }], NOW), [], "bad ids, missing or future times")
  assert.deepEqual(Model.prunePending([play(6, MIN, { retryAt: NOW - 1, extra: 1 })], NOW), [play(6, MIN)],
    "a retry time that passed is dropped, unknown keys too")
  assert.equal(Model.flushDue([play(1, day + MIN)], NOW, true), false, "a play older than 24 h is never sent")
})

test("reportBatch: oldest first, at most 20 per call", () => {
  const pending = []
  for (let n = 0; n < 25; n++) pending.push(play(n, (25 - n) * MIN))
  const batch = Model.reportBatch(pending, NOW, false)
  assert.equal(batch.length, Model.REPORT_CALL_MAX)
  assert.equal(batch[0].videoId, id11(0))
  assert.ok(Model.reportTimeoutMs(20) >= 20 * 2 * 1000, "two requests per play fit")
})

test("afterReport: taken and final ones go, passing ones wait, plays added meanwhile stay", () => {
  const a = play(1, 3 * MIN), b = play(2, 2 * MIN), c = play(3, MIN)
  const later = play(4, 0)
  const reply = { ok: true, result: { reported: 1, results: [
    { videoId: a.videoId, ok: true, retry: false },
    { videoId: b.videoId, ok: false, retry: false },
    { videoId: c.videoId, ok: false, retry: true }] } }
  const after = Model.afterReport([a, b, c, later], [a, b, c], reply, NOW)
  assert.equal(after.reported, 1)
  assert.equal(after.dropped, 1)
  assert.equal(after.waiting, 1, "a passing failure: the shell sends no next batch")
  assert.deepEqual(after.pending, [{ videoId: c.videoId, at: c.at, retryAt: NOW + Model.REPORT_RETRY_MS }, later])
  assert.equal(Model.afterReport([a, b], [a, b], { ok: true, result: { results: [
    { videoId: a.videoId, ok: true, retry: false }, { videoId: b.videoId, ok: false, retry: false }] } }, NOW).waiting, 0,
    "taken or final: the next batch may follow")
  // A reply that does not name a play the same way drops it (never re-sent blind).
  const odd = Model.afterReport([a], [a], { ok: true, result: { results: [{ videoId: "other", ok: true }] } }, NOW)
  assert.deepEqual(odd.pending, [])
  assert.equal(odd.reported, 0)
})

test("afterReport: an unknown result drops the batch (a missing play beats a double count)", () => {
  const a = play(1, MIN), b = play(2, 0)
  for (const reply of [
    { ok: false, unknown: true, error: { code: "WORKER_EXITED", message: "stopped", unknown: true } },
    { ok: false, unknown: true, error: { code: "TIMEOUT", message: "timed out", unknown: true } }]) {
    const after = Model.afterReport([a, b], [a], reply, NOW)
    assert.deepEqual(after.pending, [b], reply.error.code)
    assert.equal(after.dropped, 1)
    assert.equal(after.waiting, 0)
  }
})

test("afterReport: offline before anything went waits; signed out drops everything", () => {
  const a = play(1, MIN), b = play(2, 0)
  for (const code of ["NETWORK", "RATE_LIMITED", "TIMEOUT", "WORKER_EXITED"]) {
    const after = Model.afterReport([a, b], [a], { ok: false, error: { code, message: "x" } }, NOW)
    assert.deepEqual(after.pending, [Object.assign({}, a, { retryAt: NOW + Model.REPORT_RETRY_MS }), b], code)
    assert.equal(after.waiting, 1, code)
  }
  for (const code of ["AUTH_REQUIRED", "AUTH_EXPIRED"])
    assert.deepEqual(Model.afterReport([a, b], [a], { ok: false, error: { code } }, NOW).pending, [], code)
  for (const code of ["BAD_REQUEST", "UPSTREAM", "INTERNAL"])
    assert.deepEqual(Model.afterReport([a, b], [a], { ok: false, error: { code } }, NOW).pending, [b], code)
})

test("pending-plays.json round-trips the plays and the notice flag", () => {
  const text = Model.playStateText({ plays: [play(1, MIN), { videoId: "bad", at: NOW }], notice: true }, NOW)
  assert.ok(text.endsWith("\n"))
  assert.deepEqual(JSON.parse(text), { version: 1, plays: [play(1, MIN)], notice: true })
  assert.deepEqual(Model.parsePlayState(text, NOW), { plays: [play(1, MIN)], notice: true })
  assert.deepEqual(Model.parsePlayState(text, NOW + Model.PENDING_TTL_MS), { plays: [], notice: true }, "a day later")
  assert.deepEqual(Model.parsePlayState("", NOW), { plays: [], notice: false })
  assert.deepEqual(Model.parsePlayState('{"version":9,"plays":[],"notice":true}', NOW), { plays: [], notice: false })
})

test("a batch out in a call is marked sent: in no other batch, and dropped when the file is read again", () => {
  const a = play(1, 20 * MIN), b = play(2, 19 * MIN), c = play(3, 0)
  const out = Model.markSent([a, b, c], [a, b])
  assert.deepEqual(out.map(p => p.sent === true), [true, true, false])
  assert.equal(a.sent, undefined, "copy on write")
  assert.deepEqual(Model.reportBatch(out, NOW, true), [c], "not even forced")
  assert.deepEqual(Model.readyPlays(out, NOW), [c])
  assert.equal(Model.flushDue(Model.markSent([a], [a]), NOW, true), false, "nothing ready while it is out")
  assert.equal(Model.nextFlushAt(Model.markSent([a], [a]), NOW), 0)
  // The reply: taken, or waiting again without the mark.
  const reply = { ok: true, result: { results: [{ videoId: a.videoId, ok: true }, { videoId: b.videoId, ok: false, retry: true }] } }
  assert.deepEqual(Model.afterReport(out, [a, b], reply, NOW).pending,
    [{ videoId: b.videoId, at: b.at, retryAt: NOW + Model.REPORT_RETRY_MS }, c])
  // A reload mid-call: the file still lists the batch as sent; it goes.
  const text = Model.playStateText({ plays: out, notice: false }, NOW)
  assert.deepEqual(JSON.parse(text).plays.filter(p => p.sent).length, 2)
  assert.deepEqual(Model.parsePlayState(text, NOW).plays, [c], "a missing play beats a double count")
})

test("History rows: removed by token, put back where they were", () => {
  const t = (n, token, played) => ({ type: "track", videoId: id11(n), title: "S" + n, played: played || "Today",
                                     historyToken: token })
  const page = Model.historyCollection({ items: [t(1, "a"), t(2, "b"), t(1, "c", "Yesterday")] })
  assert.equal(Model.historyEntryIndex(page, t(1, "c")), 2, "by token, not by video")
  const without = Model.withoutHistoryEntry(page, t(1, "c"))
  assert.notEqual(without, page)
  assert.deepEqual(without.tracks.map(x => x.historyToken), ["a", "b"])
  assert.equal(without.trackCount, 2)
  assert.equal(without.subtitle, "2 songs")
  assert.equal(page.tracks.length, 3, "copy on write")
  assert.equal(Model.withoutHistoryEntry(without, t(1, "c")), without, "not there: the same page")
  const back = Model.withHistoryEntry(without, t(1, "c"), 2)
  assert.deepEqual(back.tracks.map(x => x.historyToken), ["a", "b", "c"])
  assert.equal(Model.withHistoryEntry(back, t(1, "c"), 0), back, "a reload brought it back: no twin")
  assert.deepEqual(Model.withHistoryEntry(Model.withoutHistoryEntry(page, t(1, "a")), t(1, "a"), 0).tracks
    .map(x => x.historyToken), ["a", "b", "c"])
  // The group of a removed entry goes with its last song.
  const rows = Model.collectionRows(without, { history: true })
  assert.deepEqual(rows.filter(r => r.header).map(r => r.title), ["Today"])
})

test("History rows: the token, the params, and none on a disk copy", () => {
  const track = { type: "track", videoId: "nbCOAPR33ME", historyToken: "tok" }
  assert.equal(Model.historyTokenOf(track), "tok")
  assert.deepEqual(Model.removeHistoryParams(track), { tokens: ["tok"] })
  assert.equal(Model.historyTokenOf({ type: "track", videoId: "nbCOAPR33ME" }), "")
  assert.equal(Model.removeHistoryParams({ type: "album", historyToken: "tok" }), null)
  assert.equal(Model.withoutTokens(track).historyToken, undefined, "the queue never keeps it (S3)")
})

test("menus: Remove from history on History rows, confirmed; Record plays on Library's account menu", () => {
  const row = { type: "track", videoId: "nbCOAPR33ME", title: "Karma Police", historyToken: "tok" }
  const ctx = { pageKind: "history", signedIn: true }
  const entry = Model.menuActions(row, ctx).find(a => a.id === "removeHistory")
  assert.ok(entry)
  assert.equal(entry.hint, "x")
  assert.match(entry.confirm, /again/)
  assert.ok(!ids(Model.menuActions(row, ctx)).includes("remove"), "not the unconfirmed Remove")
  assert.ok(!ids(Model.menuActions({ type: "track", videoId: "nbCOAPR33ME" }, ctx)).includes("removeHistory"), "no token")
  assert.ok(!ids(Model.menuActions(row, { pageKind: "playlist", signedIn: true })).includes("removeHistory"))
  assert.ok(!ids(Model.menuActions(row, { pageKind: "history", signedIn: false })).includes("removeHistory"))
  assert.ok(!ids(Model.menuActions(row, Object.assign({ canRemoveHistory: false }, ctx))).includes("removeHistory"),
    "S16: a brand account")
  const removed = []
  entry.run({ item: row, ctx, index: 4, owner: { removeFromHistory: (i, confirmed) => removed.push([i, confirmed]) } })
  entry.run({ item: row, ctx, index: -1, owner: { removeFromHistory: (i, confirmed) => removed.push([i, confirmed]) } })
  assert.deepEqual(removed, [[4, true]], "confirmed by the menu; a row that is gone does nothing")

  const library = { page: true, pageKind: "library", signedIn: true }
  // S16 puts its account entries first: Record plays comes right before Sign out.
  const record = actions => actions.find(a => a.id === "recordHistory")
  const off = Model.menuActions(null, library)
  assert.deepEqual(ids(off).slice(-2), ["recordHistory", "signOut"])
  assert.equal(record(off).title, "Record plays in history")
  const on = Model.menuActions(null, Object.assign({ recordHistory: true }, library))
  assert.equal(record(on).title, "Stop recording plays in history")
  const set = []
  record(on).run({ ctx: Object.assign({ recordHistory: true }, library), service: { setRecordHistory: v => set.push(v) } })
  record(off).run({ ctx: library, service: { setRecordHistory: v => set.push(v) } })
  assert.deepEqual(set, [false, true])
  assert.ok(!ids(Model.menuActions(null, { page: true, pageKind: "library", signedIn: false })).includes("recordHistory"))
})

test("notices and key text name the keys and have no dashes", () => {
  assert.equal(Model.removeHistoryPrompt(false), "Press x again to remove from history")
  assert.equal(Model.removeHistoryPrompt(true), "Click again to remove from history")
  assert.match(Model.historyNotice().body, /Library, M, Record plays in history/)
  assert.match(Model.recordHistoryNotice(true).body, /turns it off/)
  assert.match(Model.keyHelp().map(r => r.item ? r.item.title : "").join(" "), /History: press twice/)
  const texts = [Model.historyNotice(), Model.recordHistoryNotice(true), Model.recordHistoryNotice(false)]
    .map(n => n.title + n.body).concat([Model.removeHistoryPrompt(false), Model.keysReadme()])
  for (const text of texts) assert.ok(!/[\u2013\u2014]/.test(text), text)
})
