// S10 with G2 and G3: uploads (S10) get none of the song tokens' menu
// entries (G2's Save to library and Pin to Listen again on the song
// playing, queue rows and every list), even when a list or a nowContext
// brings tokens with them, since the web's upload menus have neither. The
// cookies option stays on upload loadfiles only, next to S8's start.
const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const ALBUM_ID = Model.UPLOAD_ALBUM_PREFIX + "b_po_ALBUM"
const ARTIST_ID = Model.UPLOAD_ARTIST_PREFIX + "a_po_ARTIST"
const lib = { add: "ADD", remove: "REMOVE" }
const pins = { pin: "PIN", unpin: "UNPIN" }
const tokens = { inLibrary: false, libraryTokens: lib, pinned: false, listenAgainTokens: pins }
const upload = (n, extra = {}) => ({
  type: "track", videoId: "up" + String(n).padStart(9, "0"), title: "Song " + n, kind: "upload", entityId: "t_po_" + n,
  artists: [{ name: "Someone", id: ARTIST_ID }], artistText: "Someone", album: { name: "Demo", id: ALBUM_ID },
  available: true, ...extra
})
const song = (n, extra = {}) => ({ type: "track", videoId: "pub" + String(n).padStart(8, "0"), title: "Public " + n,
  kind: "song", artists: [{ name: "Radiohead", id: "UCr" }], artistText: "Radiohead", available: true, ...extra })
const uploadAlbum = { type: "album", browseId: ALBUM_ID, title: "Demo", upload: true, entityId: "b_po_ALBUM", ...tokens }
const uploadArtist = { type: "artist", channelId: ARTIST_ID, name: "Someone", upload: true, ...tokens }
const ids = (item, ctx) => Model.menuActions(item, { signedIn: true, ...ctx }).map(a => a.id)
const CTXS = [{ pageKind: "library" }, { pageKind: "playlist" }, { pageKind: "queue", removable: true },
              { pageKind: "nowPlaying", isCurrent: true }, { pageKind: "now", page: true, isCurrent: true },
              { pageKind: "uploadAlbum", page: true }, { pageKind: "home" }]

test("an upload carrying tokens still offers no Save to library or Pin, anywhere", () => {
  const withTokens = upload(1, tokens)
  for (const ctx of CTXS) {
    for (const item of [withTokens, uploadAlbum, uploadArtist]) {
      const got = ids(item, ctx)
      assert.ok(!got.includes("saveSong") && !got.includes("pin") && !got.includes("save"), JSON.stringify([ctx, got]))
    }
  }
  // The uploads chip's song menu is S10's, tokens or not.
  assert.deepEqual(ids(withTokens, { pageKind: "library" }), ids(upload(1), { pageKind: "library" }))
  assert.equal(Model.saveAction(withTokens, {}), "", "S does nothing to an upload")
  assert.equal(Model.saveTarget(withTokens, {}), "")
  assert.equal(Model.songTokens(withTokens), null)
  assert.equal(Model.songLibraryParams(withTokens, true), null)
  assert.equal(Model.canPin(withTokens, false), false)
  assert.equal(Model.canPin(uploadAlbum, false), false)
  // A public song with the same tokens keeps both (G2).
  const pub = song(1, tokens)
  assert.ok(ids(pub, { pageKind: "queue" }).includes("saveSong") && ids(pub, { pageKind: "queue" }).includes("pin"))
  assert.equal(Model.saveAction(pub, {}), "saveSong")
  assert.deepEqual(Model.songTokens(pub), lib)
  assert.equal(Model.canPin(pub, false), true)
})

test("live tokens: none held for an upload and none put back on one", () => {
  const empty = {}
  assert.equal(Model.liveTokensPut(empty, [upload(1, tokens)]), empty, "Service.remember holds nothing for an upload")
  const mixed = Model.liveTokensPut(empty, [upload(1, tokens), song(2, tokens)])
  assert.deepEqual(Object.keys(mixed), [song(2).videoId])
  // The Now menu (and a queue row's) of an upload playing: the upload as it is,
  // even when its nowContext's `current` or the held map had tokens for it.
  const playing = upload(3)
  const held = Model.liveTokensOf(tokens)
  assert.equal(Model.withLiveTokens(playing, [held, held]), playing)
  const ctx = { signedIn: true, pageKind: "nowPlaying", isCurrent: true }
  const got = Model.menuActions(Model.withLiveTokens(playing, [held]), ctx).map(a => a.id)
  assert.ok(!got.includes("saveSong") && !got.includes("pin"), JSON.stringify(got))
  // A public song still gets them back.
  const pub = Model.withoutTokens(song(4, tokens))
  assert.deepEqual(Model.withLiveTokens(pub, [held]).libraryTokens, lib)
})

test("S on Now with an upload: says why without reading its menu", () => {
  assert.equal(Model.nowSaveNotice(upload(1), { signedIn: true, menu: null }),
               "YouTube Music offers no library control for uploads")
  assert.equal(Model.nowSaveNotice(upload(1), { signedIn: false }), "Sign in to use your library")
  assert.equal(Model.nowSaveNotice(song(1), { signedIn: true, menu: { state: "loading" } }),
               "Its menu is still loading; try again in a moment", "a song keeps G2's notices")
})

test("loadfileOptions: the cookies option on uploads only, next to S8's start", () => {
  Model.setUploadCookies("/run/user/1000/omarchy-ytmusic/cookies-mpv.txt")
  try {
    const up = Model.loadfileOptions(upload(1, tokens), { seconds: 30 })
    assert.match(up, /,start=30,ytdl-raw-options-append=%\d+%cookies=\/run\/user\/1000\/omarchy-ytmusic\/cookies-mpv\.txt$/)
    const pub = Model.loadfileOptions(song(1, tokens), { seconds: 30 })
    assert.match(pub, /,start=30$/)
    assert.ok(!/cookies|ADD|PIN/.test(pub) && !/ADD|PIN/.test(up), "no token in a loadfile")
  } finally {
    Model.setUploadCookies("")
  }
})

test("G3's podcast menus and the find title leave the upload menus alone", () => {
  const got = ids(uploadAlbum, { pageKind: "uploadAlbum", page: true })
  assert.ok(got.includes("deleteUpload"), JSON.stringify(got))
  assert.ok(!got.includes("episodeOrder") && !got.includes("episodeFilter"))
  const title = Model.menuAction("find").title
  const named = ctx => typeof title === "function" ? title(uploadAlbum, ctx) : title
  assert.equal(named({ pageKind: "podcast" }), "Find in show")
  assert.notEqual(named({ pageKind: "uploadAlbum" }), "Find in show")
  assert.equal(named({ pageKind: "uploadAlbum" }), named({ pageKind: "album" }))
})
