const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

// Up next (S12): autoplay picks, per-entry origins, tuner replacement,
// trimming and the queue.json state.

const id11 = s => (s + "xxxxxxxxxxx").slice(0, 11)
const track = (name, extra) => Object.assign({ type: "track", videoId: id11(name), title: name, kind: "song", available: true }, extra || {})
// mpv's playlist for `names` (entry ids from 100), the current one at `pos`.
const playlist = (names, pos) => names.map((name, i) => ({ filename: Model.trackUrl(id11(name)), id: 100 + i, current: i === pos }))
const meta = names => Object.fromEntries(names.map(name => [id11(name), track(name)]))
const queueOf = (names, pos, origins) => Model.withOrigins(Model.buildQueue(playlist(names, pos), meta(names)), origins || {})
const auto = entries => Model.addOrigins({}, entries.map(([entryId, name]) => ({ entryId, videoId: id11(name), origin: "auto" })))

test("topUpNeeded: three or fewer entries after the current one", () => {
  assert.equal(Model.AUTOPLAY_LEFT, 3)
  assert.equal(Model.topUpNeeded(12, 7), false, "4 left")
  assert.equal(Model.topUpNeeded(12, 8), true, "3 left")
  assert.equal(Model.topUpNeeded(12, 11), true, "the last song")
  assert.equal(Model.topUpNeeded(1, 0), true)
  assert.equal(Model.topUpNeeded(12, -1), false, "nothing plays")
  assert.equal(Model.topUpNeeded(0, 0), false)
  assert.equal(Model.topUpNeeded(12, 12), false, "past the end")
  assert.equal(Model.topUpNeeded(12, 9, 1), false, "a threshold of 1")
  assert.equal(Model.topUpNeeded(12, 10, 1), true)
})

test("origins are per mpv entry: the same song can be a user and an auto entry", () => {
  const names = ["cur", "dup", "u1", "dup"]
  // The second "dup" (id 103) came from autoplay; the first is the user's.
  const queue = queueOf(names, 0, auto([[103, "dup"]]))
  assert.deepEqual(queue.map(e => e.origin), ["user", "user", "user", "auto"])
  // An origin recorded for another video under that id (a new player) is not one.
  assert.equal(Model.originOf(auto([[101, "other"]]), 101, id11("dup")), "user")
  assert.equal(Model.originOf(null, 101, id11("dup")), "user")
  // Picks skip the song whichever way it is queued.
  const picks = Model.autoplayPicks(queue, [track("dup"), track("new1"), track("u1")], 10)
  assert.deepEqual(picks.map(t => t.title), ["new1"])
})

test("autoplayPicks: seen songs, twins, songs before videos, the batch size", () => {
  const queue = queueOf(["cur", "u1"], 0)
  const candidates = [
    track("vid1", { kind: "video" }),
    track("played"),
    track("twinOfU1", { kind: "video", counterpart: { videoId: id11("u1"), title: "u1", kind: "song" } }),
    track("new1", { counterpart: { videoId: id11("new1v"), title: "new1 video", kind: "video" } }),
    track("new1v", { kind: "video" }),
    track("gone", { available: false }),
    track("new2")
  ]
  const picks = Model.autoplayPicks(queue, candidates, 10, [id11("played")])
  assert.deepEqual(picks.map(t => t.title), ["new1", "new2", "vid1"], "no played, twin or unavailable song; videos last")
  // seen as a map works too, and the old three-argument call is unchanged.
  assert.deepEqual(Model.autoplayPicks(queue, candidates, 10, { [id11("played")]: true }).map(t => t.title), ["new1", "new2", "vid1"])
  assert.ok(Model.autoplayPicks(queue, candidates, 10).some(t => t.title === "played"))
  // A queued entry whose twin is known skips that twin too.
  const twinQueue = Model.withOrigins(Model.buildQueue(playlist(["cur"], 0),
    { [id11("cur")]: track("cur", { counterpart: { videoId: id11("curv"), kind: "video" } }) }), {})
  assert.deepEqual(Model.autoplayPicks(twinQueue, [track("curv", { kind: "video" }), track("x")], 10).map(t => t.title), ["x"])
  const many = Array.from({ length: 30 }, (_, i) => track("m" + i))
  assert.equal(Model.autoplayPicks(queue, many, Model.AUTOPLAY_BATCH).length, 10)
})

test("duplicate fallback: the sources one top-up tries, in order", () => {
  const memo = { items: [track("a")] }
  const seed = id11("seed")
  // An album or playlist context: the song's nowContext items, the
  // context's radio, then once the song's own radio.
  assert.deepEqual(Model.autoplaySources(seed, null, memo, "OLAK5uy_x"), [
    { items: memo.items },
    { method: "upNext", params: { videoId: seed, limit: 25, playlistId: "OLAK5uy_x" } },
    { method: "upNext", params: { videoId: seed, limit: 25 } }
  ])
  // No context and no memo (the panel was closed): only the song's radio.
  assert.deepEqual(Model.autoplaySources(seed, null, null, null), [{ method: "upNext", params: { videoId: seed, limit: 25 } }])
  assert.deepEqual(Model.autoplaySources(seed, null, { items: [] }, null).length, 1, "an empty memo is skipped")
  // A chosen tuner chip goes on with that chip, then the song's radio.
  const tuner = { title: "Discover", playlistId: "RDATiX", params: "p" }
  assert.deepEqual(Model.autoplaySources(seed, tuner, memo, "OLAK5uy_x"), [
    { method: "queueFrom", params: { videoId: seed, playlistId: "RDATiX", params: "p" } },
    { method: "upNext", params: { videoId: seed, limit: 25 } }
  ])
  assert.ok(!("params" in Model.autoplaySources(seed, { title: "All", playlistId: "RDAMVM", params: null }, null, null)[0].params))
  // Walking them: every pick of the first two is a duplicate, the third has new ones.
  const queue = queueOf(["seed", "a", "b"], 0)
  const answers = [[track("a")], [track("b"), track("a")], [track("b"), track("c")]]
  const found = answers.map(items => Model.autoplayPicks(queue, items, 10))
  assert.deepEqual(found.map(p => p.length), [0, 0, 1])
})

test("queueInsertAt: added songs go before autoplay's upcoming picks", () => {
  const names = ["p1", "cur", "u1", "a1", "u2", "a2"]
  const queue = queueOf(names, 1, auto([[100, "p1"], [103, "a1"], [105, "a2"]]))
  assert.equal(Model.queueInsertAt(queue, 1), 3, "before the first upcoming auto entry, not a played one")
  assert.equal(Model.queueInsertAt(queueOf(names, 1), 1), -1, "no auto entries: the end")
  assert.equal(Model.queueInsertAt(queue, -1), -1, "nothing plays: the end")
  const plan = Model.queueAtCommands([track("n1"), track("gone", { available: false }), track("n2")], 3)
  assert.deepEqual(plan.commands.map(c => [c[0], Model.videoIdFromUrl(c[1]), c[2], c[3]]),
    [["loadfile", id11("n1"), "insert-at-play", 3], ["loadfile", id11("n2"), "insert-at", 4]])
  assert.ok(plan.commands[0][4].indexOf("force-media-title=") === 0)
  assert.deepEqual(Model.queueAtCommands([track("n1")], -1).commands[0][2], "append-play")
})

test("replaceUpcomingAuto: a tuner chip replaces only upcoming auto entries", () => {
  const names = ["a0", "cur", "a1", "user", "a2", "a3"]
  // a0 (played) and a1, a2, a3 (upcoming) are auto; "user" was queued by hand.
  const queue = queueOf(names, 1, auto([[100, "a0"], [102, "a1"], [104, "a2"], [105, "a3"]]))
  const plan = Model.replaceUpcomingAuto(queue, 1, [track("a2"), track("user"), track("d1"), track("d2"), track("cur")], 10, [])
  assert.equal(plan.removed, 3)
  const removes = plan.commands.filter(c => c[0] === "playlist-remove").map(c => c[1])
  assert.deepEqual(removes, [5, 4, 2], "from the end, so the indexes hold; the played auto entry stays")
  assert.ok(!removes.includes(3) && !removes.includes(1) && !removes.includes(0), "the user's entry and the current one stay")
  // a2 was only an auto entry (removed): it may come back; user and cur are queued.
  assert.deepEqual(plan.tracks.map(t => t.title), ["a2", "d1", "d2"])
  const loads = plan.commands.filter(c => c[0] === "loadfile")
  assert.deepEqual(loads.map(c => c[2]), ["append-play", "append", "append"])
  assert.ok(plan.commands.indexOf(loads[0]) > plan.commands.indexOf(plan.commands.find(c => c[0] === "playlist-remove")),
    "removed before the new ones go in")
  // Simulate mpv: what is left is the current song, the user's song, then the chip's picks.
  let list = names.slice()
  for (const c of plan.commands) {
    if (c[0] === "playlist-remove") list.splice(c[1], 1)
    else list.push(plan.tracks[list.length - 3].title)
  }
  assert.deepEqual(list, ["a0", "cur", "user", "a2", "d1", "d2"])
  // Nothing new: nothing is removed either.
  assert.deepEqual(Model.replaceUpcomingAuto(queue, 1, [track("user"), track("cur")], 10, []), { commands: [], tracks: [], removed: 0 })
  // Seen songs stay out; the batch is AUTOPLAY_BATCH by default.
  const many = Array.from({ length: 20 }, (_, i) => track("m" + i))
  assert.equal(Model.replaceUpcomingAuto(queue, 1, many).tracks.length, 10)
  assert.deepEqual(Model.replaceUpcomingAuto(queue, 1, [track("old"), track("n")], 10, [id11("old")]).tracks.map(t => t.title), ["n"])
})

test("trimPlayed keeps 50 played entries", () => {
  assert.equal(Model.TRIM_KEEP, 50)
  assert.equal(Model.trimPlayed(50), 0)
  assert.equal(Model.trimPlayed(51), 1)
  assert.equal(Model.trimPlayed(120), 70)
  assert.equal(Model.trimPlayed(-1), 0)
  assert.equal(Model.trimPlayed(10, 5), 5)
  assert.deepEqual(Model.trimCommands(3), [["playlist-remove", 0], ["playlist-remove", 0], ["playlist-remove", 0]])
  assert.deepEqual(Model.trimCommands(0), [])
  // After trimming, 50 entries stay before the current one.
  const pos = 73
  const after = pos - Model.trimPlayed(pos)
  assert.equal(after, 50)
})

test("meta and origins are pruned to the playlist", () => {
  const names = ["a", "b", "c"]
  const full = Object.assign(meta(names), meta(["old1", "old2", "fresh"]))
  const pruned = Model.trimMeta(full, playlist(names, 0), { [id11("fresh")]: true })
  assert.deepEqual(Object.keys(pruned).sort(), [id11("a"), id11("b"), id11("c"), id11("fresh")].sort(),
    "queued songs and ones remembered moments ago stay")
  const kept = meta(names)
  assert.equal(Model.trimMeta(kept, playlist(names, 0), {}), kept, "the same object when nothing went")
  assert.deepEqual(Model.trimMeta(full, [], null), {})

  const origins = auto([[101, "b"], [102, "c"], [140, "gone"]])
  const left = Model.pruneOrigins(origins, playlist(names, 0))
  assert.deepEqual(Object.keys(left), ["101", "102"])
  assert.equal(Model.pruneOrigins(left, playlist(names, 0)), left, "unchanged: the same object")
  // An id now holding another video (a new player) goes.
  assert.deepEqual(Model.pruneOrigins(auto([[100, "zzz"]]), playlist(names, 0)), {})
  assert.deepEqual(Model.pruneOrigins(origins, []), {})
  // addOrigins copies, takes auto entries only.
  const base = {}
  const next = Model.addOrigins(base, [{ entryId: 7, videoId: id11("x"), origin: "auto" }, { entryId: 8, videoId: id11("y"), origin: "user" },
                                       { entryId: "9", videoId: id11("z"), origin: "auto" }])
  assert.deepEqual(next, { 7: { videoId: id11("x"), origin: "auto" } })
  assert.deepEqual(base, {})
})

test("seen ids: bounded, oldest out, each once", () => {
  assert.deepEqual(Model.withSeen(["a", "b"], ["b", "c"], 10), ["a", "b", "c"])
  assert.deepEqual(Model.withSeen(["a", "b", "c"], ["d"], 3), ["b", "c", "d"])
  assert.equal(Model.withSeen([], Array.from({ length: 400 }, (_, i) => "v" + i)).length, Model.SEEN_MAX)
  assert.deepEqual(Model.withSeen(null, null), [])
})

test("Playing from: the queue's context, else YouTube's name for the song's queue", () => {
  assert.equal(Model.playingFrom({ playlistId: "OLAK5uy_x", title: "OK Computer", radio: false }, { playingFrom: "Airbag Mix" }), "OK Computer")
  assert.equal(Model.playingFrom(null, { playingFrom: "Karma Police Mix" }), "Karma Police Mix")
  assert.equal(Model.playingFrom({ playlistId: null, title: "" }, null), "")
  assert.equal(Model.playingFrom(null, null), "")
})

test("tuner: one cycling chip, YouTube's selected one first", () => {
  const chips = ["All", "Popular", "Discover", "Deep cuts"].map((title, i) => ({ title, playlistId: "RD" + i, params: "p" + i, selected: i === 0 }))
  const context = { chips: chips.concat([{ title: "", playlistId: "RDx" }, { title: "No id" }]) }
  assert.deepEqual(Model.tunerChips(context).map(c => c.title), ["All", "Popular", "Discover", "Deep cuts"])
  assert.deepEqual(Model.tunerChips(null), [])
  assert.equal(Model.tunerTitle(null, chips), "All")
  assert.equal(Model.tunerTitle(null, []), "All")
  assert.equal(Model.tunerTitle({ title: "Discover" }, chips), "Discover")
  assert.equal(Model.nextTuner(chips, null, 1).title, "Popular")
  assert.equal(Model.nextTuner(chips, { title: "Deep cuts" }, 1).title, "All", "round again")
  assert.equal(Model.nextTuner(chips, { title: "All" }, -1).title, "Deep cuts")
  // Another song's chips that lack the chosen one: start from the first.
  assert.equal(Model.nextTuner(chips, { title: "1990s" }, 1).title, "All")
  assert.equal(Model.nextTuner([], null, 1), null)
  assert.deepEqual(Model.tunerOf(chips[2]), { title: "Discover", playlistId: "RD2", params: "p2" })
  assert.equal(Model.tunerOf({ title: "x" }), null)
  const rows = Model.tunerChoices(chips, { title: "Discover" })
  assert.deepEqual(rows.map(r => r.title), ["All", "Popular", "Discover", "Deep cuts"])
  assert.equal(rows[2].icon, "󰄬")
  assert.equal(rows[2].value, chips[2])
  assert.equal(Model.tunerChoices(chips, null)[0].icon, "󰄬", "nothing chosen: YouTube's own is ticked")
})

test("song / video version: the twin, its track, and the swap of the current entry", () => {
  const song = track("song", { album: { name: "OK Computer", id: "MPREb_x" }, artists: [{ name: "Radiohead", id: "UCr" }], artistText: "Radiohead",
                               likeStatus: "LIKE" })
  const twin = { videoId: id11("video"), title: "Karma Police (Official Video)", kind: "video" }
  assert.deepEqual(Model.counterpartOf(song, { videoId: song.videoId, counterpart: twin }), twin)
  assert.equal(Model.counterpartOf(song, { videoId: id11("other"), counterpart: twin }), null, "another song's context")
  assert.deepEqual(Model.counterpartOf(Object.assign({}, song, { counterpart: twin }), null), twin, "learned from an up-next list")
  assert.equal(Model.counterpartOf(song, null), null)
  const video = Model.counterpartTrack(song, twin)
  assert.equal(video.videoId, twin.videoId)
  assert.equal(video.kind, "video")
  assert.equal(video.album, null)
  assert.equal(video.likeStatus, null)
  assert.equal(video.artistText, "Radiohead")
  assert.deepEqual(video.counterpart, { videoId: song.videoId, title: "song", kind: "song" })
  const back = Model.counterpartTrack(video, video.counterpart)
  assert.equal(back.videoId, song.videoId)
  assert.equal(back.kind, "song")
  const commands = Model.switchVersionCommands(video, 4)
  assert.deepEqual(commands.map(c => c[0] === "loadfile" ? [c[0], c[2]] : c),
    [["loadfile", "insert-next"], ["playlist-play-index", 5], ["set_property", "pause", false], ["playlist-remove", 4]])
  // Menu: the song playing with a twin.
  const ids = (item, ctx) => Model.menuActions(item, ctx).map(a => a.id)
  const entry = Model.menuActions(song, { isCurrent: true, counterpart: twin }).find(a => a.id === "switchVersion")
  assert.equal(entry.title, "Play video version")
  assert.equal(Model.menuActions(video, { isCurrent: true, counterpart: video.counterpart }).find(a => a.id === "switchVersion").title,
    "Play song version")
  assert.ok(!ids(song, { isCurrent: false, counterpart: twin }).includes("switchVersion"), "only the song playing")
  assert.ok(!ids(song, { isCurrent: true, counterpart: null }).includes("switchVersion"))
  assert.deepEqual(ids(song, { isCurrent: true, counterpart: twin }).slice(0, 2), ["switchVersion", "radio"])
  const calls = []
  // The song the menu was opened for goes too: the service leaves it once another song plays.
  entry.run({ item: song, ctx: { counterpart: twin }, service: { switchVersion: (c, item) => calls.push([c, item]) } })
  assert.deepEqual(calls, [[twin, song]])
})

test("Now page menu: the tuner chooser, and the t key", () => {
  const ids = ctx => Model.menuActions(null, ctx).map(a => a.id)
  assert.ok(ids({ page: true, pageKind: "now", tunerChips: 13, tunerTitle: "All" }).includes("tune"))
  assert.ok(!ids({ page: true, pageKind: "now", tunerChips: 0, tunerTitle: "All" }).includes("tune"), "no chips (signed out): hidden")
  assert.ok(!ids({ pageKind: "now", tunerChips: 13 }).includes("tune"), "a row menu has none")
  const tune = Model.menuActions(null, { page: true, pageKind: "now", tunerChips: 4, tunerTitle: "Discover" }).find(a => a.id === "tune")
  assert.equal(tune.title, "Tune the queue (Discover)")
  assert.equal(tune.hint, "t")
  assert.equal(Model.keyId("t"), "tune")
  let opened = 0
  tune.run({ panel: { openTuner: () => opened++ } })
  assert.equal(opened, 1)
})

test("queue.json: parsed defensively, written back the same", () => {
  const state = {
    pid: 4242, context: { playlistId: "OLAK5uy_x", title: "OK Computer", radio: false },
    tuner: { title: "Discover", playlistId: "RDATiX", params: "p" },
    origins: auto([[7, "a"], [9, "b"]]), seen: [id11("s1"), id11("s2")]
  }
  const text = Model.queueStateText(state)
  assert.ok(text.endsWith("\n"))
  const back = Model.parseQueueState(text)
  assert.deepEqual(back, state)
  assert.equal(JSON.parse(text).version, 1)
  const empty = { pid: null, context: null, tuner: null, origins: {}, seen: [] }
  for (const garbage of ["", "nope", "[]", "null", "42", JSON.stringify({ origins: [], seen: "x", pid: -1, context: 3 })])
    assert.deepEqual(Model.parseQueueState(garbage), empty, garbage)
  const odd = Model.parseQueueState(JSON.stringify({
    origins: { "7": { videoId: id11("a"), origin: "auto" }, "x": { videoId: id11("a"), origin: "auto" },
               "8": { videoId: "short", origin: "auto" }, "9": { videoId: id11("b"), origin: "user" } },
    seen: [id11("s1"), "bad id", 5], context: { title: "T", playlistId: 5, radio: "yes" }, tuner: { title: "x" }
  }))
  assert.deepEqual(odd.origins, { 7: { videoId: id11("a"), origin: "auto" } })
  assert.deepEqual(odd.seen, [id11("s1")])
  assert.deepEqual(odd.context, { playlistId: null, title: "T", radio: false })
  assert.equal(odd.tuner, null)
})
