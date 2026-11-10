// No ceilings (G1) with lane A's slices: uploads (S10), saved podcasts
// (S8), Liked Music's chips and the Autoplay caption (G4). What one lane
// built for its lists holds for the other's.
const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const ALBUM_ID = Model.UPLOAD_ALBUM_PREFIX + "b_po_ALBUM"
const ARTIST_ID = Model.UPLOAD_ARTIST_PREFIX + "a_po_ARTIST"
const upload = n => ({ type: "track", videoId: "up" + String(n).padStart(9, "0"), title: "Song " + n, kind: "upload",
                       entityId: "t_po_" + n, album: { name: "Demo", id: ALBUM_ID }, available: true })
const song = n => ({ type: "track", videoId: "s" + String(n).padStart(10, "0"), title: "Song " + n, available: true })
const label = row => row.header ? "# " + row.title : row.item.type + (row.item.type === "message" ? ":" + row.item.title : "")
const ORDERS = [{ index: "recently_added", title: "Recently added", selected: true },
                { index: "a_to_z", title: "A to Z", selected: false }]

test("the Uploads chip and the Podcasts chip say where a capped list stops, and how to reach the rest", () => {
  const uploads = { items: [upload(1), upload(2)], capped: true, at: 1, orders: ORDERS }
  assert.deepEqual(Model.uploadLibraryRows(uploads, {}).slice(-1).map(label),
    ["message:Showing the first 2; change the order (O) for the rest"])
  assert.deepEqual(Model.libraryRows("uploads", uploads, []).slice(-1).map(label),
    ["message:Showing the first 2; change the order (O) for the rest"])
  const podcasts = { items: [{ type: "podcast", browseId: "MPSPPLx", title: "Show" }], capped: true, at: 1, orders: ORDERS }
  assert.deepEqual(Model.libraryRows("podcasts", podcasts, []).slice(-1).map(label),
    ["message:Showing the first 1; change the order (O) for the rest"])
  // Below the cap: Load more, as before.
  assert.deepEqual(Model.libraryRows("uploads", { items: [upload(1)], cursor: "c", at: 1 }, []).slice(-1).map(label),
    ["loadMore"])
})

test("Enter on an uploaded song: the songs in hand past the queue's 500 play on; no library order", () => {
  const list = { items: Array.from({ length: 700 }, (_, i) => upload(i)), cursor: "c.next", at: 1 }
  assert.equal(Model.librarySongsPartial("uploads", list), null, "the library's order is the Songs chip's only")
  const tracks = Model.libraryTracks(list)
  const rest = Model.listRest(tracks, 10, Model.librarySongsPartial("uploads", list), false)
  assert.equal(rest.tracks.length, 190, "song 511 on")
  assert.equal(rest.tracks[0].videoId, upload(510).videoId)
  assert.equal(rest.more, null)
  assert.equal(Model.restNote(rest), "Then 190 more songs of the list")
  // The Songs chip goes on through the library from its last loaded song.
  const songs = { items: Array.from({ length: 3 }, (_, i) => song(i)), cursor: "c", header: { shuffleId: "MLCT", playParams: "P" } }
  assert.equal(Model.librarySongsPartial("songs", songs).more.kind, "librarySongs")
})

test("an uploaded artist or album read in part goes on through its own tracksFor (S10's reader)", () => {
  const artist = { type: "artist", channelId: ARTIST_ID, name: "Someone", upload: true }
  const read = { tracks: Array.from({ length: 500 }, (_, i) => upload(i)), complete: false, total: null }
  const partial = Model.itemPartial(artist, read)
  assert.equal(partial.more.kind, "item")
  assert.deepEqual(Model.tracksForParams(partial.more.item), { kind: "artist", id: ARTIST_ID })
  const album = { type: "album", browseId: ALBUM_ID, title: "Demo", upload: true, entityId: "b_po_ALBUM" }
  assert.deepEqual(Model.tracksForParams(Model.itemPartial(album, read).more.item), { kind: "album", id: ALBUM_ID })
  assert.equal(Model.itemPartial(album, { tracks: [upload(1)], complete: true }), null)
})

test("Liked Music filtered to a chip plays what it shows; unfiltered, Enter reads on through the rest", () => {
  const tracks = Array.from({ length: 300 }, (_, i) => song(i))
  const liked = { type: "collection", kind: "liked", playlistId: "LM", truncated: true, trackCount: 416, tracks,
                  filters: [{ index: "Chill", title: "Chill", selected: false }], filter: null }
  const item = { type: "liked", title: "Liked Music" }
  const whole = Model.collectionPartial(liked, item)
  assert.equal(whole.more.kind, "item")
  assert.equal(whole.total, 416)
  const chill = Object.assign({}, liked, { filter: "Chill", trackCount: null,
                                           filters: [{ index: "Chill", title: "Chill", selected: true }] })
  assert.equal(Model.collectionPartial(chill, item), null, "a chip's list never reads on through every liked song")
})

test("the list's next songs are the user's own: the Autoplay caption stays above autoplay's", () => {
  // Songs 0 to 2 queued by the list, the rest appended as user entries
  // (Service._appendList), then autoplay's two picks.
  const queue = [0, 1, 2, 3, 4].map(i => ({ id: i + 1, track: song(i), origin: "user" }))
    .concat([5, 6].map(i => ({ id: i + 1, track: song(i), origin: "auto" })))
  assert.equal(Model.firstAutoIndex(queue, 1), 5)
  assert.equal(Model.autoplayAdded(queue[3], 3, 1), false)
  assert.equal(Model.autoplayAdded(queue[5], 5, 1), true)
  // Shuffle all and an album over 500 songs are lists, not radio.
  assert.equal(Model.autoplaySetApart({ playlistId: null, title: Model.LIBRARY_SONGS_TITLE, radio: false }, null), true)
})

test("a capped uploads search and a capped See all of uploaded songs say so", () => {
  const result = { items: [upload(1)], cursor: null, capped: true }
  assert.deepEqual(Model.flattenSearch(result, "", { scope: "uploads" }).map(label), ["# Uploads", "track", "message:Showing the first 1"])
  const page = { type: "page", kind: "uploadSongs", sections: [{ title: null, items: [upload(1), upload(2)] }], capped: true,
                 orders: ORDERS }
  assert.deepEqual(Model.pageRows(page).slice(-1).map(label), ["message:Showing the first 2; change the order (O) for the rest"])
})
