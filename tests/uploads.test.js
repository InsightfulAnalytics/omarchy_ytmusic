// S10: uploads. The Uploads chip, the upload pages and routes, the account
// cookies on an uploaded song's loadfile, and Delete upload's confirm.
const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const ALBUM_ID = Model.UPLOAD_ALBUM_PREFIX + "b_po_ALBUM"
const ARTIST_ID = Model.UPLOAD_ARTIST_PREFIX + "a_po_ARTIST"
const upload = (n, extra = {}) => ({
  type: "track", videoId: "up" + String(n).padStart(9, "0"), title: "Song " + n, kind: "upload", entityId: "t_po_" + n,
  artists: [{ name: "Someone", id: ARTIST_ID }], artistText: "Someone", album: { name: "Demo", id: ALBUM_ID },
  available: true, ...extra
})
const song = n => ({ type: "track", videoId: "pub" + String(n).padStart(8, "0"), title: "Public " + n, kind: "song",
                     artists: [{ name: "Radiohead", id: "UCr" }], artistText: "Radiohead", available: true })
const uploadAlbum = { type: "album", browseId: ALBUM_ID, title: "Demo", upload: true, entityId: "b_po_ALBUM" }
const uploadArtist = { type: "artist", channelId: ARTIST_ID, name: "Someone", upload: true, songs: 9, subtitle: "9 songs" }
const label = row => row.header ? "# " + row.title : row.item.type + ":" + (row.item.id || row.item.videoId || row.item.title || "")
// The media title option every loadfile carries (mediaTitle's own joiner).
const titled = t => "force-media-title=%" + Model.utf8Length(Model.mediaTitle(t)) + "%" + Model.mediaTitle(t)
const DASHES = /[\u2013\u2014]/
const ids = (item, ctx) => Model.menuActions(item, { signedIn: true, ...ctx }).map(a => a.id)
const ORDERS = [
  { index: "recently_added", title: "Recently added", selected: true },
  { index: "a_to_z", title: "A to Z", selected: false },
  { index: "z_to_a", title: "Z to A", selected: false },
]

test("the Uploads chip comes last and lists its songs with uploadSongs", () => {
  const chips = Model.LIBRARY_CHIPS.map(c => c.key)
  assert.equal(chips[chips.length - 1], "uploads")
  assert.equal(Model.libraryChipSpec("uploads").method, "uploadSongs")
  assert.equal(Model.libraryChipSpec("uploads").title, "Uploads")
  assert.equal(Model.nextLibraryChip("uploads", 1), "playlists")
})

test("loadfileOptions adds the account's cookies only for uploaded songs", () => {
  Model.setUploadCookies("")
  assert.equal(Model.loadfileOptions(upload(1)), titled(upload(1)), "no cookie path known: nothing")
  Model.setUploadCookies("/home/u/.config/omarchy-ytmusic/cookies.txt")
  try {
    const value = "cookies=/home/u/.config/omarchy-ytmusic/cookies.txt"
    const cookies = "ytdl-raw-options-append=%" + value.length + "%" + value
    assert.equal(Model.uploadCookiesOption(upload(1)), cookies)
    assert.equal(Model.loadfileOptions(upload(1)), titled(upload(1)) + "," + cookies)
    assert.equal(Model.loadfileOptions(song(1)), titled(song(1)), "a public song: unchanged")
    assert.equal(Model.uploadCookiesOption(song(1)), "")
    assert.equal(Model.uploadCookiesOption(uploadAlbum), "", "only songs load as files")
    // Every loadfile the queue sends carries it (play now, queue, collections, resume).
    const now = Model.playNowCommands(upload(2), { count: 0, pos: -1 })[0]
    assert.equal(now[0], "loadfile")
    assert.ok(now[4].endsWith("," + cookies), now[4])
    const all = Model.playCollectionCommands([song(1), upload(3)], 0, false, 1, true).commands
    const loads = all.filter(c => c[0] === "loadfile")
    assert.deepEqual(loads.map(c => c[4].includes("ytdl-raw-options-append")), [false, true])
    // With a start (S8) the options stay one comma-separated list.
    assert.equal(Model.loadfileOptions(upload(1), { seconds: 30 }), titled(upload(1)) + ",start=30," + cookies)
    // A path with commas or '=' goes in whole (the %n% form, byte length).
    Model.setUploadCookies("/tmp/a,b=c é/cookies.txt")
    const odd = "cookies=/tmp/a,b=c é/cookies.txt"
    assert.equal(Model.uploadCookiesOption(upload(1)), "ytdl-raw-options-append=%" + Model.utf8Length(odd) + "%" + odd)
    assert.equal(Model.utf8Length(odd), odd.length + 1)
  } finally {
    Model.setUploadCookies("")
  }
})

test("uploads are recognised by kind and by their ids", () => {
  assert.ok(Model.isUploadTrack(upload(1)) && !Model.isUploadTrack(song(1)))
  assert.ok(Model.isUploadItem(uploadAlbum) && Model.isUploadItem(uploadArtist) && Model.isUploadItem(upload(1)))
  assert.ok(!Model.isUploadItem({ type: "album", browseId: "MPREb_x" }) && !Model.isUploadItem({ type: "artist", channelId: "UCx" }))
  assert.ok(!Model.isUploadAlbumId(Model.UPLOAD_ALBUM_PREFIX), "the bare prefix is no album")
  assert.equal(Model.uploadEntityId(upload(4)), "t_po_4")
  assert.equal(Model.uploadEntityId(uploadAlbum), "b_po_ALBUM")
  assert.equal(Model.uploadEntityId({ type: "album", browseId: ALBUM_ID }), "b_po_ALBUM", "an album row without one: its browseId less the prefix")
  assert.equal(Model.uploadEntityId(upload(5, { entityId: null })), "", "a song from Search's uploads scope has none")
  assert.equal(Model.uploadEntityId(uploadArtist), "", "artists cannot be deleted")
  assert.equal(Model.uploadEntityId(song(1)), "")
})

test("an upload listed elsewhere (a playlist, History) plays as the account but cannot be deleted there", () => {
  // The worker gives it kind upload by its video type; only the uploads views give an entityId.
  const listed = upload(6, { entityId: undefined, setVideoId: "S6" })
  Model.setUploadCookies("/run/user/1000/omarchy-ytmusic/cookies-mpv.txt")
  try {
    const value = "cookies=/run/user/1000/omarchy-ytmusic/cookies-mpv.txt"
    const cookies = "ytdl-raw-options-append=%" + value.length + "%" + value
    assert.ok(Model.isUploadTrack(listed))
    assert.equal(Model.loadfileOptions(listed), titled(listed) + "," + cookies)
    const loads = Model.playCollectionCommands([song(1), listed], 0, false, 1, true).commands.filter(c => c[0] === "loadfile")
    assert.deepEqual(loads.map(c => c[4].endsWith("," + cookies)), [false, true])
  } finally {
    Model.setUploadCookies("")
  }
  const playlistIds = ids(listed, { pageKind: "playlist", removable: true })
  assert.ok(!playlistIds.includes("deleteUpload") && !playlistIds.includes("radio") && !playlistIds.includes("save"),
            JSON.stringify(playlistIds))
  assert.ok(playlistIds.includes("remove"), "its playlist can still remove it")
})

test("upload albums and artists open their upload pages, from rows, links and routes", () => {
  const ref = Model.pageRef(uploadAlbum)
  assert.deepEqual([ref.kind, ref.method, ref.params, ref.id], ["uploadAlbum", "uploadAlbum", { browseId: ALBUM_ID }, ALBUM_ID])
  const artistRef = Model.pageRef(uploadArtist)
  assert.deepEqual([artistRef.kind, artistRef.params], ["uploadArtist", { browseId: ARTIST_ID }])
  assert.ok(Model.PAGE_KINDS.uploadArtist.round)
  assert.equal(Model.pageRef({ type: "album", browseId: "MPREb_x" }).method, "album", "public albums stay albums")
  assert.equal(Model.pageRef({ type: "artist", channelId: "UCx" }).method, "artist")
  // A song's links: Go to album and the artist credit.
  assert.deepEqual(Model.albumOf(upload(1)), { type: "album", browseId: ALBUM_ID, title: "Demo", thumb: null, thumbLarge: null })
  assert.equal(Model.pageRef(Model.creditItem(upload(1).artists[0])).method, "uploadArtist")
  assert.equal(Model.pageRef(Model.albumOf(upload(1))).method, "uploadAlbum")
  // Routes (a See all or a link with an uploads id).
  assert.equal(Model.pageRef(Model.routeEndpoint({ browseId: ALBUM_ID, title: "Demo" })).method, "uploadAlbum")
  assert.equal(Model.pageRef(Model.routeEndpoint({ browseId: ARTIST_ID })).method, "uploadArtist")
  assert.equal(Model.pageRef(Model.routeEndpoint({ browseId: Model.UPLOAD_SONGS_ID })).method, "uploadSongs")
  assert.equal(Model.pageRef(Model.routeEndpoint({ browseId: Model.UPLOAD_ALBUMS_ID })).method, "uploadAlbums")
  assert.equal(Model.pageRef(Model.routeEndpoint({ browseId: Model.UPLOAD_ARTISTS_ID })).method, "uploadArtists")
  // Playing a row reads its upload page through tracksFor (the worker routes the ids).
  assert.deepEqual(Model.tracksForParams(uploadAlbum), { kind: "album", id: ALBUM_ID })
  assert.deepEqual(Model.tracksForParams(uploadArtist), { kind: "artist", id: ARTIST_ID })
  assert.ok(Model.UPLOAD_KINDS.every(kind => Model.isUploadKind(kind) && Model.PAGE_KINDS[kind]))
})

test("Delete upload: on uploaded songs and albums, signed in, with a second press", () => {
  assert.ok(ids(upload(1), { pageKind: "library" }).includes("deleteUpload"))
  assert.ok(ids(uploadAlbum, { pageKind: "uploadAlbums" }).includes("deleteUpload"))
  assert.ok(ids(uploadAlbum, { page: true, pageKind: "uploadAlbum" }).includes("deleteUpload"), "the album page's M")
  assert.ok(!ids(uploadArtist, {}).includes("deleteUpload"))
  assert.ok(!ids(song(1), {}).includes("deleteUpload"))
  assert.ok(!ids(upload(1, { entityId: null }), {}).includes("deleteUpload"), "no id, no delete")
  assert.ok(!Model.menuActions(upload(1), { signedIn: false }).some(a => a.id === "deleteUpload"))
  const actions = Model.menuActions(upload(1), { signedIn: true, pageKind: "library" })
  const entry = actions.find(a => a.id === "deleteUpload")
  assert.equal(entry.title, "Delete upload")
  assert.equal(entry.confirm, Model.DELETE_UPLOAD_CONFIRM)
  const row = rows => rows.find(r => r.item.id === "deleteUpload").item.title
  assert.equal(row(Model.actionRows(actions, "")), "Delete upload")
  assert.equal(row(Model.actionRows(actions, "deleteUpload")), "Really delete this upload? Choose again", "armed: the confirm title")
  assert.equal(row(Model.actionRows(actions, "addToPlaylist")), "Delete upload", "another entry armed")
  assert.ok(!DASHES.test(Model.DELETE_UPLOAD_CONFIRM + Model.UPLOAD_PROCESSING))
})

test("Delete upload's run: the service deletes; a page that showed it reads again or closes", () => {
  const sent = []
  let after = null
  const service = { deleteUpload: (item, cb) => { sent.push(Model.uploadEntityId(item)); after = cb; return true } }
  const owner = { ref: { kind: "uploadAlbum", key: "k" }, loads: [], closed: 0, showing: true,
                  shows(ref) { return this.showing && ref.key === "k" }, load(fresh) { this.loads.push(fresh) },
                  closeRequested() { this.closed++ } }
  Model.deleteUploadRun({ item: upload(7), ctx: { signedIn: true }, owner, service })
  assert.deepEqual(sent, ["t_po_7"])
  after({ ok: false, error: { code: "UPSTREAM", message: "no" } })
  assert.deepEqual([owner.loads, owner.closed], [[], 0], "refused: nothing to reread")
  after({ ok: true, result: { ok: true } })
  assert.deepEqual([owner.loads, owner.closed], [[true], 0], "a song on the album page: the page reads again")
  Model.deleteUploadRun({ item: uploadAlbum, ctx: { signedIn: true, page: true }, owner, service })
  after({ ok: true, result: { ok: true } })
  assert.equal(owner.closed, 1, "the page's own album: the page closes")
  owner.showing = false
  Model.deleteUploadRun({ item: upload(8), ctx: { signedIn: true }, owner, service })
  after({ ok: true, result: { ok: true } })
  assert.deepEqual([owner.loads.length, owner.closed], [1, 1], "another page shows by now: left alone")
  Model.deleteUploadRun({ item: upload(9), ctx: { signedIn: true }, owner: { }, service })
  after({ ok: true, result: { ok: true } })  // the Library chip (no ref): Service and the Store handle its row
  assert.deepEqual(sent, ["t_po_7", "b_po_ALBUM", "t_po_8", "t_po_9"])
})

test("uploads offer no Save, Subscribe, Start radio, View credits or Copy link, as on the web", () => {
  assert.ok(!ids(uploadAlbum, {}).includes("save"))
  assert.ok(!ids(uploadArtist, {}).includes("subscribe"))
  assert.ok(!ids(upload(1), {}).includes("radio") && !ids(uploadAlbum, {}).includes("radio") && !ids(uploadArtist, {}).includes("radio"))
  assert.equal(Model.radioParams(upload(1)), null)
  assert.equal(Model.saveAction(uploadAlbum, {}), "", "S does nothing on an upload album")
  assert.equal(Model.saveAction(uploadArtist, {}), "")
  assert.ok(ids(upload(1), {}).includes("goAlbum") && ids(upload(1), {}).includes("goArtist"))
  assert.ok(!ids(upload(1), { pageKind: "uploadAlbum" }).includes("goAlbum"), "not on its own album's page")
  assert.ok(ids(upload(1), {}).includes("addToPlaylist") && ids(upload(1), {}).includes("like"))
  // The web's upload menus have no Share, and YouTube has no credits for an upload.
  for (const [item, ctx] of [[upload(1), { pageKind: "library" }], [upload(1, { entityId: null }), { pageKind: "playlist" }],
                             [uploadAlbum, {}], [uploadAlbum, { page: true, pageKind: "uploadAlbum" }], [uploadArtist, {}]])
    assert.ok(!ids(item, ctx).includes("share") && !ids(item, ctx).includes("credits"), JSON.stringify(ids(item, ctx)))
  assert.deepEqual(ids(upload(1), { pageKind: "library" }),
                   ["play", "playNext", "addToQueue", "like", "dislike", "addToPlaylist", "goArtist", "goAlbum", "deleteUpload"])
  // Public items keep theirs.
  assert.ok(ids({ type: "album", browseId: "MPREb_x", title: "x" }, {}).includes("save"))
  assert.ok(ids({ type: "artist", channelId: "UCx", name: "x" }, {}).includes("subscribe"))
  assert.ok(ids(song(1), {}).includes("credits") && ids(song(1), {}).includes("share"))
})

test("the Uploads chip's rows: Upload a file, Albums, Artists, then the songs", () => {
  const list = { items: [upload(1), upload(2)], cursor: "c.T", hasMore: true, orders: ORDERS, current: "recently_added", at: 1 }
  const rows = Model.uploadLibraryRows(list, { canUpload: true, uploading: [] })
  assert.deepEqual(rows.map(label), ["action:" + Model.UPLOAD_FILE_ID, "uploadAlbums:Albums", "uploadArtists:Artists",
                                     "# Songs, Recently added", "track:" + upload(1).videoId, "track:" + upload(2).videoId,
                                     "loadMore:Load more"])
  assert.deepEqual(rows.filter(r => r.trackIndex !== undefined).map(r => r.trackIndex), [0, 1])
  assert.equal(rows[0].item.hint, "")
  assert.equal(Model.libraryRows("uploads", list, []).length, rows.length, "libraryRows lists the chip the same way")
  // Uploading: the row says what is on its way; a brand account has no row.
  assert.equal(Model.uploadLibraryRows(list, { uploading: ["a.mp3"] })[0].item.hint, "uploading a.mp3")
  assert.equal(Model.uploadLibraryRows(list, { uploading: ["a.mp3", "b.flac"] })[0].item.hint, "uploading 2 files")
  assert.equal(Model.uploadLibraryRows(list, { canUpload: false })[0].item.type, "uploadAlbums")
  // Empty once read; a served message; a capped list.
  const empty = Model.uploadLibraryRows({ items: [], at: 5, orders: [] }, {}).map(label)
  assert.deepEqual(empty.slice(-1), ["message:No uploaded songs yet"])
  assert.ok(!Model.uploadLibraryRows({ items: [], at: 0, loading: true }, {}).some(r => r.item && r.item.type === "message"),
            "nothing said while the first page loads")
  assert.deepEqual(Model.uploadLibraryRows({ items: [upload(1)], capped: true, at: 1 }, {}).slice(-1).map(label), ["message:Showing the first 1"])
  // The entry rows open the lists as pages.
  assert.equal(Model.pageRef(rows[1].item).method, "uploadAlbums")
  assert.equal(Model.pageRef(rows[2].item).method, "uploadArtists")
  assert.ok(rows.every(r => r.header || !DASHES.test(r.item.title + (r.item.subtitle || "") + (r.item.hint || ""))))
})

test("a deleted upload leaves the list at once and can come back", () => {
  const other = { ...upload(9), album: { name: "Other", id: Model.UPLOAD_ALBUM_PREFIX + "b_po_OTHER" } }
  const list = { items: [upload(1), other, upload(2)], cursor: null, at: 3 }
  const one = Model.withoutUpload(list, "t_po_2", "")
  assert.deepEqual(one.list.items.map(t => t.entityId), ["t_po_1", "t_po_9"])
  assert.deepEqual(one.removed.map(r => r.index), [2])
  assert.equal(one.list.at, 3, "the rest of the list stays")
  const album = Model.withoutUpload(list, "b_po_ALBUM", ALBUM_ID)
  assert.deepEqual(album.list.items.map(t => t.entityId), ["t_po_9"], "an album takes its songs")
  const back = Model.withUploads(album.list, album.removed)
  assert.deepEqual(back.items.map(t => t.entityId), ["t_po_1", "t_po_9", "t_po_2"], "in their places")
  assert.equal(Model.withUploads(list, one.removed), list, "already there: unchanged")
  const none = Model.withoutUpload(list, "t_po_404", "")
  assert.equal(none.list, list)
  assert.equal(none.removed.length, 0)
})

test("the upload path field: a full path to a file YouTube Music takes", () => {
  assert.equal(Model.uploadPathProblem("~/Music/a.mp3"), "")
  assert.equal(Model.uploadPathProblem("/home/u/b.FLAC"), "", "any case")
  for (const ext of Model.UPLOAD_EXTENSIONS) assert.equal(Model.uploadPathProblem("/x/song." + ext), "")
  assert.match(Model.uploadPathProblem("Music/a.mp3"), /full path/)
  assert.match(Model.uploadPathProblem("/x/a.wav"), /mp3, m4a, wma, flac or ogg/)
  assert.match(Model.uploadPathProblem("/x/.mp3"), /mp3, m4a/, "a name that is only a suffix")
  assert.match(Model.uploadPathProblem("  "), /Type the path/)
  assert.equal(Model.uploadFileName(" ~/Music/My song.m4a "), "My song.m4a")
  assert.ok(Model.UPLOAD_TIMEOUT_MS >= 600000)
})
