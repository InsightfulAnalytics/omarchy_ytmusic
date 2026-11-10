const test = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const Model = require("../YtmModel.js")

const ROOT = path.join(__dirname, "..")
const id11 = n => ("vid" + n).padEnd(11, "x")
const tr = (n, extra = {}) => ({ type: "track", videoId: id11(n), title: "Song " + n, artistText: "A", available: true, ...extra })
const ids = actions => actions.map(a => a.id)

const song = tr(1, { artists: [{ name: "Radiohead", id: "UCr" }], album: { name: "OK Computer", id: "MPREb_ok" } })
const duet = tr(2, { artists: [{ name: "A", id: "UCa" }, { name: "B", id: "UCb" }, { name: "Feat", id: null }] })
const album = { type: "album", browseId: "MPREb_ok", playlistId: "OLAK5uy_ok", title: "OK Computer", artists: [{ name: "Radiohead", id: "UCr" }] }
const playlist = { type: "playlist", playlistId: "PLabc", title: "Mine", owned: true }
const mix = { type: "playlist", playlistId: "RDTMAK5uy_mix", title: "My Supermix" }
const artist = { type: "artist", channelId: "UCr", name: "Radiohead" }

test("menuKind tells songs, albums, playlists, mixes, Liked Music and artists apart", () => {
  assert.equal(Model.menuKind(song), "track")
  assert.equal(Model.menuKind(album), "album")
  assert.equal(Model.menuKind(playlist), "playlist")
  assert.equal(Model.menuKind(mix), "mix")
  assert.equal(Model.menuKind({ type: "playlist", playlistId: "RDCLAK5uy_x" }), "playlist", "curated RDCLAK is a playlist")
  assert.equal(Model.menuKind({ type: "playlist", playlistId: "RDPN" }), "playlist", "New episodes is a playlist")
  assert.equal(Model.menuKind({ type: "playlist", playlistId: "VLRDAMPLx" }), "mix")
  assert.equal(Model.menuKind({ type: "playlist", playlistId: "LM" }), "liked")
  assert.equal(Model.menuKind({ type: "liked", title: "Liked Music" }), "liked")
  assert.equal(Model.menuKind(artist), "artist")
  for (const none of [null, { type: "history" }, { type: "browse", browseId: "X" }, { type: "more" }, { type: "track" }, { type: "playlist" }])
    assert.equal(Model.menuKind(none), "")
})

test("menuActions per type", () => {
  const ctx = { signedIn: true, pageKind: "search" }
  // S4: like, dislike, save and subscribe follow Start radio, as on the web.
  assert.deepEqual(ids(Model.menuActions(song, ctx)),
    ["play", "playNext", "addToQueue", "radio", "like", "dislike", "addToPlaylist", "goArtist", "goAlbum", "credits", "share"])
  // S13: Save to playlist and Copy to new playlist (signed in) follow Add to playlist.
  assert.deepEqual(ids(Model.menuActions(album, ctx)),
    ["play", "shuffle", "playNext", "addToQueue", "radio", "save", "saveToPlaylist", "copyToNewPlaylist", "goArtist", "share"])
  assert.deepEqual(ids(Model.menuActions(playlist, ctx)),
    ["play", "shuffle", "playNext", "addToQueue", "radio", "saveToPlaylist", "copyToNewPlaylist", "share"])
  assert.deepEqual(ids(Model.menuActions(artist, ctx)), ["open", "shuffle", "radio", "subscribe", "share"])
  assert.deepEqual(ids(Model.menuActions(mix, ctx)), ["play", "playNext", "addToQueue", "save"])
  assert.deepEqual(ids(Model.menuActions({ type: "liked" }, ctx)), ["play", "shuffle", "playNext", "addToQueue"])
  assert.deepEqual(ids(Model.menuActions({ type: "history" }, ctx)), [])
  // Titles, icons and key hints come resolved.
  const next = Model.menuActions(song, ctx).find(a => a.id === "playNext")
  assert.equal(next.title, "Play next")
  assert.equal(next.hint, "n")
  assert.ok(next.icon)
  assert.equal(Model.menuActions(song, ctx).find(a => a.id === "radio").hint, "R")
})

test("menuActions follow ctx: signed out hides account entries; page, queue and current rows", () => {
  assert.ok(!ids(Model.menuActions(song, { signedIn: false })).includes("addToPlaylist"))
  // S14: the taste builder before Sign out.
  assert.deepEqual(ids(Model.menuActions(null, { page: true, pageKind: "library", signedIn: true })), ["tasteProfile", "signOut"])
  assert.deepEqual(ids(Model.menuActions(null, { page: true, pageKind: "library", signedIn: false })), ["signIn"])
  // A row's menu on Library or Home has no account or page entries.
  assert.deepEqual(ids(Model.menuActions(playlist, { pageKind: "library", signedIn: true })),
    ["play", "shuffle", "playNext", "addToQueue", "radio", "saveToPlaylist", "copyToNewPlaylist", "share"])
  assert.deepEqual(ids(Model.menuActions(mix, { pageKind: "home" })), ["play", "playNext", "addToQueue"])
  // The song playing: no Play, Play next or Add to queue.
  assert.deepEqual(ids(Model.menuActions(song, { signedIn: true, isCurrent: true })),
    ["radio", "like", "dislike", "addToPlaylist", "goArtist", "goAlbum", "credits", "share"])
  // A queued song: jump to it, remove it; no duplicates.
  const queued = ids(Model.menuActions(song, { pageKind: "queue", removable: true, removeTitle: "Remove from queue" }))
  assert.ok(queued.includes("play") && queued.includes("remove") && !queued.includes("playNext") && !queued.includes("addToQueue"))
  assert.equal(Model.menuActions(song, { removable: true, removeTitle: "Remove from queue" }).find(a => a.id === "remove").title, "Remove from queue")
  // On its album's page a song has no Go to album.
  assert.ok(!ids(Model.menuActions(song, { pageKind: "album" })).includes("goAlbum"))
  // Page menus (M): the header buttons' twins, and Delete for your own playlist.
  const page = ids(Model.menuActions(playlist, { page: true, pageKind: "playlist", owned: true, hasTracks: true }))
  // S13: Edit playlist (e) after Add to playlist's place; signed out here, so no Save to playlist.
  assert.deepEqual(page, ["play", "shuffle", "playNext", "addToQueue", "radio", "editPlaylist", "share", "deletePlaylist"])
  assert.ok(!ids(Model.menuActions(playlist, { page: true, owned: false, hasTracks: true })).includes("deletePlaylist"))
  assert.ok(!ids(Model.menuActions(artist, { page: true })).includes("open"))
  assert.deepEqual(ids(Model.menuActions({ type: "browse", browseId: "X" }, { page: true, hasTracks: true })),
    ["play", "shuffle", "playNext", "addToQueue"])
  assert.deepEqual(ids(Model.menuActions(null, { page: true, pageKind: "home" })), ["refresh"])
  assert.deepEqual(ids(Model.menuActions(null, { page: true, pageKind: "now", queueLength: 1, autoplay: true })),
    ["autoplay", "speed", "loudness"])  // S17: Playback speed and Loudness
  const now = Model.menuActions(song, { page: true, pageKind: "now", isCurrent: true, queueLength: 5, autoplay: false })
  assert.ok(ids(now).includes("clearQueue") && ids(now).includes("radio"))
  assert.equal(now.find(a => a.id === "autoplay").title, "Turn autoplay on")
  assert.equal(Model.menuActions(playlist, { page: true, owned: true }).find(a => a.id === "deletePlaylist").confirm,
    "Really delete? Choose again")
})

test("go to artist and album need ids; a song known by its album playlist looks it up", () => {
  assert.deepEqual(Model.linkedArtists(duet).map(a => a.id), ["UCa", "UCb"])
  // An array-like list (what a QML signal hands over) counts too.
  const listLike = { 0: { name: "A", id: "UCa" }, 1: { name: "N", id: null }, length: 2 }
  assert.deepEqual(Model.linkedArtists({ type: "track", artists: listLike }).map(a => a.id), ["UCa"])
  assert.ok(!ids(Model.menuActions(tr(3, { artists: [{ name: "X", id: null }] }), {})).includes("goArtist"))
  assert.deepEqual(Model.albumOf(song), { type: "album", browseId: "MPREb_ok", title: "OK Computer", thumb: null, thumbLarge: null })
  assert.equal(Model.albumOf(tr(4, { album: { name: "N", id: null } })), null)
  const olak = tr(5, { album: { name: "N", id: "OLAK5uy_n" } })
  assert.equal(Model.albumOf(olak), null)
  assert.equal(Model.albumPlaylistOf(olak), "OLAK5uy_n")
  assert.ok(ids(Model.menuActions(olak, {})).includes("goAlbum"))
  assert.equal(Model.albumPlaylistOf({ type: "album", playlistId: "VLOLAK5uy_a" }), "OLAK5uy_a")
  assert.equal(Model.albumPlaylistOf(album), "", "an album with a page id needs no lookup")
  assert.equal(Model.albumPlaylistOf({ type: "playlist", playlistId: "OLAK5uy_songs" }), "",
    "a playlist could be an artist's songs list: never looked up as an album")
})

test("the registry takes appended entries, and a broken applies never breaks the menu", () => {
  const before = Model.MENU_ACTIONS.length
  Model.MENU_ACTIONS.push({ id: "testSave", title: (item, ctx) => ctx.saved ? "Remove from library" : "Save to library",
    icon: "x", applies: (item, ctx) => ctx.signedIn && Model.menuKind(item) === "album", run: () => {} })
  Model.MENU_ACTIONS.push({ id: "testBroken", title: "Broken", applies: () => { throw new Error("no") }, run: () => {} })
  try {
    const list = Model.menuActions(album, { signedIn: true, saved: true })
    assert.equal(list.at(-1).id, "testSave")
    assert.equal(list.at(-1).title, "Remove from library")
    assert.ok(!ids(Model.menuActions(album, { signedIn: false })).includes("testSave"))
    assert.ok(!ids(list).includes("testBroken"))
    assert.equal(Model.menuAction("testSave").icon, "x")
  } finally {
    Model.MENU_ACTIONS.splice(before)
  }
  assert.equal(Model.MENU_ACTIONS.length, before)
})

test("actions run through env: the owner's row, the panel and the service", () => {
  const calls = []
  const env = (item, ctx, owner = {}) => ({
    item, ctx, index: 4, owner,
    panel: { push: i => calls.push(["push", i.type]), openPicker: t => calls.push(["picker", t.length]),
             goArtist: i => calls.push(["goArtist"]), goAlbum: i => calls.push(["goAlbum"]) },
    service: { playItem: (i, mode) => calls.push(["playItem", Model.menuKind(i), mode]), startRadio: () => calls.push(["radio"]),
               copyLink: i => calls.push(["copy", Model.shareUrl(i)]), signOut: () => calls.push(["signOut"]) }
  })
  const run = (id, e) => Model.menuAction(id).run(e)
  run("play", env(song, {}, { activate: i => calls.push(["activate", i]) }))
  run("play", env(album, {}))
  run("playNext", env(album, {}))
  run("addToQueue", env(playlist, {}))
  run("shuffle", env(artist, {}))
  run("shuffle", env(playlist, { page: true }, { playAll: mode => calls.push(["playAll", mode]) }))
  run("radio", env(song, {}))
  run("open", env(artist, {}))
  run("share", env(artist, {}))
  run("remove", env(song, { removable: true }, { remove: i => calls.push(["remove", i]) }))
  run("deletePlaylist", env(playlist, { page: true }, { deletePlaylist: confirmed => calls.push(["delete", confirmed]) }))
  // The row went away while the menu was open (index -1): Remove does
  // nothing, Play plays the song alone.
  const gone = (item, ctx, owner) => ({ ...env(item, ctx, owner), index: -1 })
  run("remove", gone(song, { removable: true }, { remove: i => calls.push(["remove", i]) }))
  run("play", gone(song, {}, { activate: i => calls.push(["activate", i]) }))
  assert.deepEqual(calls, [
    ["activate", 4], ["playItem", "album", "now"], ["playItem", "album", "next"], ["playItem", "playlist", "queue"],
    ["playItem", "artist", "shuffle"], ["playAll", "shuffle"], ["radio"], ["push", "artist"],
    ["copy", "https://music.youtube.com/channel/UCr"], ["remove", 4], ["delete", true], ["playItem", "track", "now"]
  ])
})

test("a menu's row is found again by its place after the rows change", () => {
  // Liked Music: the disk copy, then the fresh one with a new like on top.
  const rows = list => list.map(t => ({ item: t }))
  const old = rows([tr(1, { setVideoId: "S1" }), tr(2, { setVideoId: "S2" }), tr(3, { setVideoId: "S3" })])
  const fresh = rows([tr(9, { setVideoId: "S9" })].concat(old.map(r => r.item)))
  const place = Model.rowPlace(old, 1)
  assert.equal(Model.findPlace(fresh, place), 2)
  assert.equal(fresh[2].item.setVideoId, "S2")
  assert.equal(Model.findPlace(rows([tr(1, { setVideoId: "S1" })]), place), -1, "gone: -1")
})

test("actionRows show the confirm title while armed", () => {
  const actions = Model.menuActions(playlist, { page: true, owned: true, hasTracks: true })
  const rows = Model.actionRows(actions, "")
  assert.ok(rows.every(r => r.item.type === "action" && r.item.id && r.item.title))
  assert.equal(rows.at(-1).item.title, "Delete playlist")
  assert.equal(Model.actionRows(actions, "deletePlaylist").at(-1).item.title, "Really delete? Choose again")
  assert.equal(Model.actionRows(actions, "share").find(r => r.item.id === "share").item.title, "Copy link")
})

test("shareUrl per type", () => {
  assert.equal(Model.shareUrl(song), "https://music.youtube.com/watch?v=" + id11(1))
  assert.equal(Model.shareUrl(album), "https://music.youtube.com/browse/MPREb_ok")
  assert.equal(Model.shareUrl({ type: "album", playlistId: "OLAK5uy_x" }), "https://music.youtube.com/playlist?list=OLAK5uy_x")
  assert.equal(Model.shareUrl({ type: "playlist", playlistId: "VLPLabc" }), "https://music.youtube.com/playlist?list=PLabc")
  assert.equal(Model.shareUrl(artist), "https://music.youtube.com/channel/UCr")
  assert.equal(Model.shareUrl({ type: "profile", channelId: "UCp" }), "https://music.youtube.com/channel/UCp")
  assert.equal(Model.shareUrl(mix), "")
  assert.equal(Model.shareUrl({ type: "liked" }), "")
  assert.equal(Model.shareUrl(null), "")
})

test("tracksFor and radio params per kind", () => {
  assert.deepEqual(Model.tracksForParams(album), { kind: "album", id: "MPREb_ok" })
  assert.deepEqual(Model.tracksForParams({ type: "album", playlistId: "OLAK5uy_x" }), { kind: "playlist", id: "OLAK5uy_x" })
  assert.deepEqual(Model.tracksForParams({ type: "playlist", playlistId: "VLPLabc" }), { kind: "playlist", id: "PLabc" })
  assert.deepEqual(Model.tracksForParams(mix), { kind: "mix", id: "RDTMAK5uy_mix" })
  assert.deepEqual(Model.tracksForParams({ type: "liked" }), { kind: "liked", id: "LM" })
  assert.deepEqual(Model.tracksForParams(artist), { kind: "artist", id: "UCr" })
  assert.equal(Model.tracksForParams(song), null)
  assert.deepEqual(Model.radioParams(song), { videoId: id11(1) })
  assert.deepEqual(Model.radioParams(album), { playlistId: "OLAK5uy_ok" })
  assert.deepEqual(Model.radioParams({ type: "album", browseId: "MPREb_single" }), { browseId: "MPREb_single" })
  assert.deepEqual(Model.radioParams(playlist), { playlistId: "PLabc" })
  assert.deepEqual(Model.radioParams(mix), { mixId: "RDTMAK5uy_mix" })
  assert.deepEqual(Model.radioParams(artist), { channelId: "UCr" })
  assert.equal(Model.radioParams({ type: "history" }), null)
  assert.ok(Model.canQueue(album) && Model.canQueue(mix) && Model.canQueue(song) && !Model.canQueue(artist))
})

test("completeTracks: the Store's copy is used only when it holds the whole list", () => {
  const tracks = [tr(1), tr(2)]
  assert.deepEqual(Model.completeTracks({ type: "collection", truncated: false, tracks }), tracks)
  assert.equal(Model.completeTracks({ type: "collection", truncated: true, tracks }), null)
  const page = { type: "page", sections: [{ type: "section", title: "", items: tracks }], cursor: null, hasMore: false }
  assert.deepEqual(Model.completeTracks(page), tracks)
  assert.equal(Model.completeTracks({ ...page, hasMore: true }), null)
  assert.equal(Model.completeTracks({ ...page, cursor: "c.1" }), null)
  assert.equal(Model.completeTracks({ ...page, capped: true }), null)
  assert.equal(Model.completeTracks({ type: "artistPage", songs: tracks }), null)
  assert.equal(Model.completeTracks(null), null)
})

// mpv's playlist as a list, for the loadfile flags these commands use.
function simulate(playlist, current, commands) {
  const list = playlist.slice()
  let pos = current
  for (const c of commands) {
    if (c[0] !== "loadfile") continue
    const id = Model.videoIdFromUrl(c[1])
    if (c[2] === "insert-next") list.splice(pos + 1, 0, id)
    else if (c[2] === "append" || c[2] === "append-play") {
      list.push(id)
      if (c[2] === "append-play" && pos < 0) pos = list.length - 1
    } else throw new Error("unexpected flag " + c[2])
  }
  return { list, pos }
}

test("playNext of many tracks keeps their order after the current song", () => {
  const many = [1, 2, 3, 4, 5].map(n => tr(n))
  const queue = ["q0", "q1", "q2"]
  const plan = Model.playNextAllCommands(many, { count: 3, pos: 1 })
  assert.equal(plan.tracks.length, 5)
  assert.deepEqual(simulate(queue, 1, plan.commands).list, ["q0", "q1", ...many.map(t => t.videoId), "q2"])
  // Nothing current (or nothing queued): appended, and the first starts.
  const idle = Model.playNextAllCommands(many, { count: 3, pos: -1 })
  assert.equal(idle.commands[0][2], "append-play")
  const out = simulate(queue, -1, idle.commands)
  assert.deepEqual(out.list, [...queue, ...many.map(t => t.videoId)])
  assert.equal(out.list[out.pos], many[0].videoId)
  assert.equal(Model.playNextAllCommands(many, { count: 0, pos: -1 }).commands[0][2], "append-play")
  // Unplayable tracks are skipped; nothing playable, no commands.
  assert.equal(Model.playNextAllCommands([tr(1), tr(2, { available: false })], { count: 1, pos: 0 }).tracks.length, 1)
  assert.deepEqual(Model.playNextAllCommands([], { count: 1, pos: 0 }).commands, [])
})

test("the 500 cap keeps the start track and tells how many were queued", () => {
  const big = Array.from({ length: 1234 }, (_, i) => tr(i))
  const head = Model.capWindow(big, 0, 500)
  assert.equal(head.tracks.length, 500)
  assert.equal(head.start, 0)
  assert.equal(head.total, 1234)
  assert.equal(head.capped, true)
  assert.equal(head.tracks[0].videoId, id11(0))
  const mid = Model.capWindow(big, 300, 500)
  assert.equal(mid.tracks[mid.start].videoId, id11(300))
  assert.equal(mid.tracks.length, 500)
  // Near the end the window reaches back, so 500 are still queued.
  const late = Model.capWindow(big, 1200, 500)
  assert.equal(late.tracks.length, 500)
  assert.equal(late.tracks[late.start].videoId, id11(1200))
  assert.equal(late.tracks.at(-1).videoId, id11(1233))
  // Under the cap nothing changes.
  const small = [tr(1), tr(2, { available: false }), tr(3)]
  assert.deepEqual(Model.capWindow(small, 2, 500), { tracks: small, start: 2, total: 2, capped: false })
  assert.equal(Model.QUEUE_CAP, 500)
  assert.deepEqual(Model.queueCapNotice(500, 1234),
    { title: "Queued 500 of 1234 songs", body: "At most 500 songs of one list go into the queue." })
  assert.equal(Model.queueCapNotice(500, null).title, "Queued the first 500 songs")
})

test("capNotice: a capped list, or one the worker read only the start of", () => {
  const big = Array.from({ length: 1234 }, (_, i) => tr(i))
  assert.equal(Model.capNotice(500, Model.capWindow(big, 0, 500), null).title, "Queued 500 of 1234 songs")
  // tracksFor with limit 500: all 500 fit, but the list has 961.
  const read = big.slice(0, 500)
  const cut = Model.capWindow(read, 0, 500)
  assert.equal(cut.capped, false)
  assert.equal(Model.capNotice(500, cut, null), null, "a whole list that fits: no notice")
  assert.equal(Model.capNotice(500, cut, { total: 961 }).title, "Queued 500 of 961 songs")
  assert.equal(Model.capNotice(497, cut, { total: null }).title, "Queued the first 497 songs")
})

test("KEYS: no key is bound twice, and every bound key is in the help", () => {
  const seen = new Map()
  for (const entry of Model.KEYS) {
    for (const key of [...(entry.keys || []), ...(entry.caught || [])]) {
      assert.ok(!seen.has(key), `${key} is bound to ${seen.get(key)} and ${entry.id}`)
      seen.set(key, entry.id)
    }
    assert.ok(entry.title && entry.group && entry.id, JSON.stringify(entry))
  }
  const ids = new Set(Model.KEYS.map(e => e.id))
  assert.equal(ids.size, Model.KEYS.length, "ids are unique")
  const help = Model.keyHelp()
  const rows = help.filter(r => !r.header)
  assert.equal(rows.length, Model.KEYS.length)
  const hints = rows.map(r => r.item.hint).join(" / ").split(" / ")
  for (const entry of Model.KEYS) {
    for (const key of [...(entry.label || entry.keys || []), ...(entry.label ? [] : entry.caught || [])])
      assert.ok(hints.includes(key), `help lists ${key}`)
    for (const key of entry.keys || []) assert.equal(Model.keyId(key), entry.id)
  }
  assert.ok(rows.every(r => r.item.type === "action" && r.item.id.startsWith("key:")))
  assert.ok(help.filter(r => r.header).map(r => r.title).includes("Menus"))
  assert.equal(Model.keyId("z"), "")
  assert.equal(Model.keyHint("like"), "L")
  assert.equal(Model.keyHint("menu"), "m / .")
  assert.equal(Model.withKey("Like", "like"), "Like (L)")
  assert.equal(Model.withKey("Plain", "nope"), "Plain")
  for (const key of ["m", ".", "M", "R", "?", "n", "a"]) assert.ok(Model.keyId(key), key)
})

test("every text key is dispatched in Panel.qml, and the README and header comment come from KEYS", () => {
  const panel = fs.readFileSync(path.join(ROOT, "Panel.qml"), "utf8")
  const runKey = panel.slice(panel.indexOf("function runKey("))
  const body = runKey.slice(0, runKey.indexOf("\n  }\n"))
  for (const entry of Model.KEYS.filter(e => (e.keys || []).length))
    assert.ok(body.includes('"' + entry.id + '"'), `Panel.runKey handles ${entry.id}`)
  const comment = Model.keysComment().join("\n")
  assert.ok(panel.includes(comment), "Panel.qml header comment is Model.keysComment():\n" + comment)
  const readme = fs.readFileSync(path.join(ROOT, "README.md"), "utf8")
  const row = "| Panel keys | " + Model.keysReadme() + " |"
  assert.ok(readme.includes(row), "README keys row is Model.keysReadme():\n" + row)
})

test("chordAllowed: Ctrl, Alt and Meta chords are not shortcuts", () => {
  const SHIFT = 0x02000000, CTRL = 0x04000000, ALT = 0x08000000, META = 0x10000000, KEYPAD = 0x20000000
  assert.equal(Model.chordAllowed(0), true)
  assert.equal(Model.chordAllowed(undefined), true)
  assert.equal(Model.chordAllowed(SHIFT), true)
  assert.equal(Model.chordAllowed(KEYPAD), true)
  assert.equal(Model.chordAllowed(CTRL), false)
  assert.equal(Model.chordAllowed(ALT), false)
  assert.equal(Model.chordAllowed(SHIFT | ALT), false)
  assert.equal(Model.chordAllowed(META), false)
})
