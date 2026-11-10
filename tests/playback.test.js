// S17: playback speed and loudness levelling (YtmModel's "playback" section).
const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const song = { type: "track", videoId: "nbCOAPR33ME", title: "Karma Police", artistText: "Radiohead", kind: "song" }
const video = { type: "track", videoId: "1uYWYWPc9HU", title: "Karma Police", artistText: "Radiohead", kind: "video" }
const episode = { type: "track", videoId: "abcdefghijk", title: "Episode 1", artistText: "A podcast", kind: "episode" }

test("speedStep moves one 0.25 step and stops at 0.5 and 2", () => {
  assert.equal(Model.speedStep(1, 1), 1.25)
  assert.equal(Model.speedStep(Model.speedStep(1, 1), 1), 1.5, "] twice from 1x is 1.5x")
  assert.equal(Model.speedStep(1, -1), 0.75)
  assert.equal(Model.speedStep(1.75, 1), 2)
  assert.equal(Model.speedStep(2, 1), 2, "never above 2x")
  assert.equal(Model.speedStep(0.75, -1), 0.5)
  assert.equal(Model.speedStep(0.5, -1), 0.5, "never below 0.5x")
  // A speed set elsewhere (MPRIS) between steps goes to the step on that side.
  assert.equal(Model.speedStep(1.1, 1), 1.25)
  assert.equal(Model.speedStep(1.1, -1), 1)
  assert.equal(Model.speedStep(3, -1), 2)
  assert.equal(Model.speedStep(0.1, 1), 0.5)
  // Not a number: from 1x.
  assert.equal(Model.speedStep(undefined, 1), 1.25)
  assert.equal(Model.speedStep("2", -1), 0.75)
  assert.equal(Model.speedStep(NaN, -1), 0.75)
  // Walking the whole range lands on every chooser speed, in order.
  const up = [0.5]
  while (up[up.length - 1] < 2) up.push(Model.speedStep(up[up.length - 1], 1))
  assert.deepEqual(up, Model.SPEEDS)
})

test("clampSpeed keeps a speed on the grid and within bounds", () => {
  assert.equal(Model.clampSpeed(1.5), 1.5)
  assert.equal(Model.clampSpeed(1.3), 1.25)
  assert.equal(Model.clampSpeed(9), 2)
  assert.equal(Model.clampSpeed(0), 0.5)
  assert.equal(Model.clampSpeed("1.5"), 1)
  assert.equal(Model.clampSpeed(Infinity), 1)
})

test("each kind of entry has its own speed", () => {
  assert.equal(Model.speedKind(song), "music")
  assert.equal(Model.speedKind(video), "music", "a music video plays at the music speed")
  assert.equal(Model.speedKind(episode), "episode")
  assert.equal(Model.speedKind(null), "music")
  const speeds = { music: 1.25, episode: 1.75 }
  assert.equal(Model.speedFor(speeds, Model.speedKind(song)), 1.25)
  assert.equal(Model.speedFor(speeds, Model.speedKind(episode)), 1.75)
  assert.equal(Model.speedFor(null, "episode"), 1)
  assert.equal(Model.speedFor({ music: 7 }, "music"), 1, "an invalid saved speed plays at 1x")
  // Setting one kind keeps the other.
  const faster = Model.withSpeed(speeds, "episode", 2)
  assert.deepEqual(faster, { music: 1.25, episode: 2 })
  assert.deepEqual(speeds, { music: 1.25, episode: 1.75 }, "copy on write")
  assert.deepEqual(Model.withSpeed(faster, "music", 0.8), { music: 0.75, episode: 2 })
  assert.deepEqual(Model.withSpeed(undefined, "music", 1.5), { music: 1.5, episode: 1 })
  // An episode keeps its speed after music played in between (Service applies
  // speedFor whenever the kind changes).
  const plays = [episode, song, song, episode].map(t => Model.speedFor(faster, Model.speedKind(t)))
  assert.deepEqual(plays, [2, 1.25, 1.25, 2])
})

test("prefs keep speeds on the step grid", () => {
  const prefs = Model.parsePrefs(JSON.stringify({ speed: { music: 1.3, episode: 1.75 } }))
  assert.deepEqual(prefs.speed, { music: 1.25, episode: 1.75 })
  const next = Model.withPref(prefs, "speed", Model.withSpeed(prefs.speed, "music", 1.5))
  assert.deepEqual(JSON.parse(Model.serializePrefs(next)).speed, { music: 1.5, episode: 1.75 })
  assert.deepEqual(Model.parsePrefs(JSON.stringify({ speed: { music: 2.5, episode: "x" } })).speed, { music: 1, episode: 1 })
})

test("the speed badge, menu title and chooser", () => {
  assert.equal(Model.speedLabel(1), "")
  assert.equal(Model.speedLabel(1.0000001), "")
  assert.equal(Model.speedLabel(1.5), "1.5x")
  assert.equal(Model.speedLabel(0.75), "0.75x")
  assert.equal(Model.speedLabel(2), "2x")
  assert.equal(Model.speedLabel(undefined), "")
  assert.equal(Model.speedTitle(1), "Normal")
  assert.equal(Model.speedTitle(1.25), "1.25x")
  const rows = Model.speedChoices(1.5)
  assert.deepEqual(rows.map(r => r.value), [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2])
  assert.deepEqual(rows.map(r => r.title), ["0.5x", "0.75x", "Normal", "1.25x", "1.5x", "1.75x", "2x"])
  assert.deepEqual(rows.filter(r => r.icon === "󰄬").map(r => r.value), [1.5])
  assert.equal(new Set(rows.map(r => r.id)).size, rows.length)
})

test("YouTube's loudness turns a song down, never up", () => {
  assert.equal(Model.loudnessGain(2.5), -2.5)
  assert.equal(Model.loudnessGain(3.14159), -3.14)
  assert.equal(Model.loudnessGain(-1.4300003), 0, "quieter than the target: left alone")
  assert.equal(Model.loudnessGain(-10.25), 0)
  assert.equal(Model.loudnessGain(0), 0)
  assert.ok(!Object.is(Model.loudnessGain(-3), -0))
  for (const absent of [null, undefined, "2", NaN, Infinity, true]) assert.equal(Model.loudnessGain(absent), null)
})

test("a stream carries its gain only when the worker was asked for the loudness", () => {
  const plain = Model.streamEntry({ videoId: "x", url: "https://g/v?expire=5", expires: 5, format: "251", account: false })
  assert.deepEqual(plain, { url: "https://g/v?expire=5", expires: 5 })
  assert.ok(!("gain" in plain), "normalize off: no gain at all")
  assert.equal(Model.streamHasLoudness(plain), false)
  const loud = Model.streamEntry({ url: "u", expires: 9, loudnessDb: 4.2 })
  assert.deepEqual(loud, { url: "u", expires: 9, gain: -4.2 })
  assert.deepEqual(Model.streamEntry({ url: "u", expires: 9, loudnessDb: -6 }), { url: "u", expires: 9, gain: 0 })
  const unknown = Model.streamEntry({ url: "u", expires: 9, loudnessDb: null })
  assert.deepEqual(unknown, { url: "u", expires: 9, gain: null })
  assert.equal(Model.streamHasLoudness(unknown), true, "asked: YouTube had none")
  // Pruning keeps the gain (mpv/ytm-levels.lua reads it from the same map).
  const fresh = Model.streamEntry({ url: "u", expires: 2000000000, loudnessDb: 4.2 })
  assert.deepEqual(Model.pruneStreams({ a: fresh }, 1000, 50).a, fresh)
})

test("turning YouTube levelling on forgets streams resolved without loudness", () => {
  const streams = { a: { url: "a", expires: 9 }, b: { url: "b", expires: 9, gain: -1 }, c: { url: "c", expires: 9, gain: null } }
  assert.deepEqual(Object.keys(Model.streamsWithLoudness(streams)), ["b", "c"])
  const levelled = { b: streams.b }
  assert.equal(Model.streamsWithLoudness(levelled), levelled, "nothing dropped: the same object")
})

test("loadfile carries no volume-gain: the gain is set per file when it loads", () => {
  const options = Model.loadfileOptions(song)
  assert.ok(options.startsWith("force-media-title=%") && options.endsWith("%" + Model.mediaTitle(song)), options)
  assert.ok(!/volume-gain/.test(options))
  for (const command of Model.playNowCommands(song, { count: 0, pos: -1 }).concat(Model.queueAllCommands([song, video]).commands)) {
    if (command[0] === "loadfile") assert.ok(!/volume-gain/.test(command[4]), command[4])
  }
})

test("loudness modes, the chooser and the worker's settings", () => {
  assert.deepEqual(Model.NORMALIZE_MODES, ["off", "youtube", "dynamic"])
  assert.equal(Model.normalizeMode("dynamic"), "dynamic")
  assert.equal(Model.normalizeMode("loud"), "off")
  assert.deepEqual(["off", "youtube", "dynamic", "x"].map(Model.normalizeTitle), ["Off", "YouTube", "Dynamic", "Off"])
  const rows = Model.normalizeChoices("youtube")
  assert.deepEqual(rows.map(r => r.value), ["off", "youtube", "dynamic"])
  assert.deepEqual(rows.filter(r => r.icon === "󰄬").map(r => r.value), ["youtube"])
  // S16 adds quality (and location once one is set).
  assert.deepEqual(Model.workerSettings(Model.parsePrefs("")), { normalize: "off", quality: "high" })
  assert.deepEqual(Model.workerSettings(Model.withPref(Model.parsePrefs(""), "normalize", "youtube")),
    { normalize: "youtube", quality: "high" })
  assert.deepEqual(Model.workerSettings(null), { normalize: "off", quality: "high" })
})

test("the Now page menu has Playback speed and Loudness; [ and ] step the speed", () => {
  const ids = actions => actions.map(a => a.id)
  const now = Model.menuActions(song, { page: true, pageKind: "now", isCurrent: true, queueLength: 3, speed: 1.5, normalize: "dynamic" })
  assert.ok(ids(now).includes("speed") && ids(now).includes("loudness"))
  assert.equal(now.find(a => a.id === "speed").title, "Playback speed (1.5x)")
  assert.equal(now.find(a => a.id === "speed").hint, "[ / ]")
  assert.equal(now.find(a => a.id === "loudness").title, "Loudness (Dynamic)")
  assert.equal(Model.menuActions(null, { page: true, pageKind: "now", speed: 1 }).find(a => a.id === "speed").title,
    "Playback speed (Normal)")
  // Only there: not on the song's own menu, a row or another page.
  assert.ok(!ids(Model.menuActions(song, { pageKind: "nowPlaying", isCurrent: true })).includes("speed"))
  assert.ok(!ids(Model.menuActions(song, { pageKind: "search" })).includes("loudness"))
  assert.ok(!ids(Model.menuActions(null, { page: true, pageKind: "home" })).includes("speed"))
  const opened = []
  const panel = { openSpeedChooser: () => opened.push("speed"), openLoudnessChooser: () => opened.push("loudness") }
  for (const id of ["speed", "loudness"]) now.find(a => a.id === id).run({ item: song, ctx: {}, index: -1, panel, service: {} })
  assert.deepEqual(opened, ["speed", "loudness"])
  assert.equal(Model.keyId("["), "speed")
  assert.equal(Model.keyId("]"), "speed")
  assert.equal(Model.keyHint("speed"), "[ / ]")
})
