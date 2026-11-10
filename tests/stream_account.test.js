// Play with your account (prefs.streamAccount): the worker setting, every
// song's loadfile cookies for mpv's yt-dlp fallback, and the Library menu entry.
const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const song = { type: "track", videoId: "pub00000001", title: "Public 1", kind: "song", available: true }
const titled = t => "force-media-title=%" + Model.utf8Length(Model.mediaTitle(t)) + "%" + Model.mediaTitle(t)
const ids = actions => actions.map(a => a.id)

test("streamAccount is a pref, off until turned on, sent to the worker only when on", () => {
  const prefs = Model.parsePrefs("")
  assert.equal(prefs.streamAccount, false)
  assert.ok(!("streamAccount" in Model.workerSettings(prefs)), "off: left out, so configure turns it off")
  const on = Model.withPref(prefs, "streamAccount", true)
  assert.equal(Model.workerSettings(on).streamAccount, true)
  assert.equal(Model.withPref(prefs, "streamAccount", "yes").streamAccount, false, "only true turns it on")
  assert.equal(Model.parsePrefs(Model.serializePrefs(on)).streamAccount, true, "kept in player.json")
})

test("with streamAccount every song's loadfile carries the cookies, signed in only", () => {
  const path = "/run/user/1000/omarchy-ytmusic/cookies-mpv.txt"
  const value = "cookies=" + path
  const cookies = "ytdl-raw-options-append=%" + value.length + "%" + value
  try {
    Model.setUploadCookies(path)
    assert.equal(Model.loadfileOptions(song), titled(song), "off: a public song has none")
    Model.setStreamAccount(true)
    assert.equal(Model.loadfileOptions(song), titled(song) + "," + cookies)
    Model.setUploadCookies("")
    assert.equal(Model.loadfileOptions(song), titled(song), "signed out: no cookie copy, nothing added")
  } finally {
    Model.setStreamAccount(false)
    Model.setUploadCookies("")
  }
})

test("Library's menu has Play with your account before Sign out, signed in only", () => {
  const library = { page: true, pageKind: "library", signedIn: true }
  const menu = ids(Model.menuActions(null, library))
  assert.deepEqual(menu.slice(-3), ["recordHistory", "streamAccount", "signOut"])
  const off = Model.menuActions(null, library).find(a => a.id === "streamAccount")
  assert.equal(off.title, "Play with your account")
  const onCtx = Object.assign({ streamAccount: true }, library)
  const on = Model.menuActions(null, onCtx).find(a => a.id === "streamAccount")
  assert.equal(on.title, "Stop playing with your account")
  const set = []
  off.run({ ctx: library, service: { setStreamAccount: v => set.push(v) } })
  on.run({ ctx: onCtx, service: { setStreamAccount: v => set.push(v) } })
  assert.deepEqual(set, [true, false])
  assert.ok(!ids(Model.menuActions(null, { page: true, pageKind: "library", signedIn: false })).includes("streamAccount"))
})
