// No ceilings (G1): lists load to their end and say so at a cap, every
// completion YouTube serves shows, and a list longer than the queue plays on.
const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const song = (id, extra) => Object.assign({ type: "track", videoId: id, title: "Song " + id }, extra || {})
const songs = (n, prefix, from) => Array.from({ length: n }, (_, i) => song((prefix || "s") + ((from || 0) + i)))
const album = id => ({ type: "album", browseId: id, title: id })
const label = row => row.header ? "# " + row.title : row.item.type + (row.item.type === "message" ? ":" + row.item.title : "")

// ---- lists that reach their end -----------------------------------------------------------

test("library songs page past 2000 to the end, and a real cap shows how to reach the rest", () => {
  const orders = [{ index: "recently_added", title: "Recently saved", selected: true }, { index: "a_to_z", title: "A to Z" }]
  let list = Model.libraryList({ sections: [{ items: songs(75) }], cursor: "c.0", orders, order: "recently_added",
                                 header: { shuffleId: "MLCT", playParams: "P" } })
  let n = 75
  while (n < 3755) {
    const size = Math.min(50, 3755 - n)
    const last = n + size >= 3755
    list = Model.appendLibraryList(list, Model.libraryList({ sections: [{ items: songs(size, "s", n) }], cursor: last ? null : "c." + n }), 10000)
    n += size
  }
  assert.equal(list.items.length, 3755)
  assert.equal(list.capped, false)
  assert.equal(Model.libraryRows("songs", list, []).at(-1).item.type, "track", "the end: no Load more, no message")
  const capped = Model.appendLibraryList(Object.assign({}, list, { cursor: "c.x" }), Model.libraryList({ sections: [{ items: songs(10, "t") }], cursor: "c.y" }), 3760)
  assert.equal(capped.capped, true)
  assert.deepEqual(Model.libraryRows("songs", capped, []).slice(-1).map(label),
    ["message:Showing the first 3,760; change the order (O) for the rest"])
})

test("an artistAlbums page appends past 3000 and keeps its cursor; at the cap it says so", () => {
  const page = { type: "page", kind: "artistAlbums", sections: [{ title: null, items: [] }], cursor: "c.0",
                 orders: [{ index: "Default", title: "Default" }, { index: "Recency", title: "Recency" }] }
  let merged = page
  for (let k = 0; k < 31; k++) {
    const items = Array.from({ length: k === 30 ? 33 : 100 }, (_, i) => album("MPRE" + (k * 100 + i)))
    merged = Model.appendPage(merged, { sections: [{ title: null, items }], cursor: k === 30 ? null : "c." + k }, 10000)
    if (k === 29) assert.equal(merged.cursor, "c.29", "3000 items, still a cursor")
  }
  assert.equal(Model.pageItemCount(merged), 3033)
  assert.equal(merged.capped, undefined)
  assert.equal(Model.pageRows(merged).at(-1).item.type, "album", "the end of the list")
  const cut = Model.appendPage(Object.assign({}, merged, { cursor: "c.more" }), { sections: [{ title: null, items: [album("X1"), album("X2")] }], cursor: "c.z" }, 3034)
  assert.equal(cut.capped, true)
  assert.deepEqual(Model.pageRows(cut).slice(-1).map(label), ["message:Showing the first 3,034; change the order (O) for the rest"])
  assert.equal(Model.pageRows(cut).some(r => r.item && r.item.type === "loadMore"), false)
  const plain = Model.appendPage({ type: "page", sections: [{ items: [album("A")] }], cursor: "c" }, { sections: [{ items: [album("B")] }], cursor: "d" }, 1)
  assert.deepEqual(Model.pageRows(plain).slice(-1).map(label), ["message:Showing the first 1"], "no orders: no O hint")
})

test("a filtered search loads past 500 and a capped one shows the cut", () => {
  let result = { items: songs(40), cursor: "c.0", hasMore: true }
  for (let k = 1; k <= 13; k++) {
    result = Model.appendSearchPage(result, { items: songs(40, "s", k * 40 - 2), cursor: k === 13 ? null : "c." + k, hasMore: k !== 13 }, 2000)
  }
  assert.equal(result.items.length, 558, "past 500; the two repeats of the first page dropped")
  assert.equal(result.cursor, null)
  assert.equal(Model.searchRows(result, "songs").at(-1).item.type, "track", "the end")
  const capped = Model.appendSearchPage({ items: songs(3), cursor: "c" }, { items: songs(3, "t"), cursor: "d" }, 4)
  assert.deepEqual(Model.searchRows(capped, "songs").slice(-1).map(label), ["message:Showing the first 4"])
})

test("every completion YouTube serves shows, after at most two recent searches", () => {
  const remote = ["radiohead", "radiohead creep", "radiohead karma police", "radiohead no surprises", "radiohead songs",
                  "radiohead live", "radiohead in rainbows"].map(text => ({ text }))
  const recent = ["radiohead creep", "radiohead ok computer", "radiohead tour", "air"]
  const list = Model.suggestionList(recent, remote, "radioh")
  assert.deepEqual(list.map(e => e.text), ["radiohead creep", "radiohead ok computer", "radiohead", "radiohead karma police",
                                           "radiohead no surprises", "radiohead songs", "radiohead live", "radiohead in rainbows"])
  assert.equal(list.filter(e => !e.recent).length, 6, "all six new completions; the repeat merges into the recent one")
  assert.equal(Model.suggestionList([], remote, "radioh", 3).length, 3, "max limits YouTube's only")
  assert.equal(Model.suggestionList(recent, remote, "radioh", 3).length, 5)
})

// ---- a list longer than the queue plays on ---------------------------------------------------

test("capRest is what capWindow leaves after its window", () => {
  const list = songs(1126)
  assert.deepEqual(Model.capRest(list, 0, 500).map(t => t.videoId).slice(0, 2), ["s500", "s501"])
  assert.equal(Model.capRest(list, 0, 500).length, 626)
  assert.equal(Model.capRest(list, 300, 500)[0].videoId, "s800")
  assert.deepEqual(Model.capRest(list, 1000, 500), [], "a late start keeps the last 500")
  assert.deepEqual(Model.capRest(songs(20), 0, 500), [])
  const gaps = songs(600).map((t, i) => i % 2 ? Object.assign({}, t, { available: false }) : t)
  assert.equal(Model.capWindow(gaps, 0, 200).tracks.at(-1).videoId, "s398")
  assert.equal(Model.capRest(gaps, 0, 200)[0].videoId, "s399")
})

test("a held list longer than 500 goes on 500 at a time, then ends", () => {
  const list = songs(1126)
  let rest = Model.listRest(list, 0, null, false)
  assert.equal(rest.tracks.length, 626)
  assert.equal(rest.more, null)
  assert.equal(Model.restNote(rest), "Then 626 more songs of the list")
  let taken = Model.restTake(rest, 500)
  assert.equal(taken.tracks[0].videoId, "s500", "song 501 after song 500")
  assert.equal(taken.tracks.length, 500)
  taken = Model.restTake(taken.rest, 500)
  assert.equal(taken.tracks.length, 126)
  assert.equal(taken.tracks.at(-1).videoId, "s1125")
  assert.equal(Model.restTake(taken.rest, 500).tracks.length, 0)
  assert.equal(Model.restRequest(taken.rest, 500), null, "nothing more to read")
  assert.equal(Model.listRest(songs(300), 0, null, false), null, "a list that fits has no rest")
})

test("Play on a playlist read only in part reads it whole later, past the songs queued", () => {
  const item = { type: "playlist", playlistId: "PLbig", title: "Big" }
  const first = { tracks: songs(500), complete: false, total: 1126 }
  const partial = Model.itemPartial(item, first)
  assert.deepEqual(partial, { total: 1126, more: { kind: "item", item, anchor: "s499", offset: 500 } })
  assert.equal(Model.itemPartial(item, { tracks: songs(20), complete: true }), null)
  const rest = Model.listRest(first.tracks, 0, partial, false)
  assert.equal(rest.tracks.length, 0)
  assert.equal(rest.capped, false)
  assert.equal(Model.restNote(rest), "Then the rest of its 1,126 songs")
  assert.deepEqual(Model.restRequest(rest, 500), { item })
  const read = Model.restRead(rest, { tracks: songs(1126), complete: true, total: 1126 }, {})
  assert.equal(read.tracks.length, 626)
  assert.equal(read.tracks[0].videoId, "s500")
  assert.equal(read.more, null)
  assert.equal(read.capped, false)
  // The playlist changed meanwhile: the queued last song is found where it is now.
  const moved = [song("new")].concat(songs(1126))
  assert.equal(Model.restRead(rest, { tracks: moved, complete: true }, {}).tracks[0].videoId, "s500")
  // Gone: the songs after the same count follow.
  const other = songs(1126, "o")
  assert.equal(Model.restRead(rest, { tracks: other, complete: true }, {}).tracks[0].videoId, "o500")
})

test("a list over 5000 songs says at once that only 5000 play", () => {
  const item = { type: "playlist", playlistId: "PLhuge" }
  const rest = Model.listRest(songs(500), 0, Model.itemPartial(item, { tracks: songs(500), complete: false, total: 7000 }), false)
  assert.equal(rest.capped, true)
  assert.deepEqual(Model.restCapNotice(rest, 500), { title: "Only 5,000 of 7,000 songs play",
                                                      body: "At most 5,000 songs of one list are read." })
  const shuffled = Model.listRest(songs(5000), 0, { total: 7000, more: { kind: "item", item } }, true)
  assert.equal(shuffled.more, null, "a shuffle draws from what was read")
  assert.equal(shuffled.tracks.length, 4500)
  assert.equal(Model.restCapNotice(shuffled, 5000).title, "Only 5,000 of 7,000 songs play")
  // Unknown size, read whole later and cut there: said when it runs out.
  const later = Model.restRead(Model.listRest(songs(500), 0, Model.itemPartial(item, { tracks: songs(500), complete: false }), false),
                               { tracks: songs(5000), complete: false, total: null }, {})
  assert.equal(later.capped, true)
  assert.equal(Model.restCapNotice(later).title, "Only the first 5,000 songs play")
  assert.equal(Model.restCapNotice(Model.listRest(songs(900), 0, null, false)), null)
  // A Shuffle that read 5000 of a list of unknown size: said at once too.
  const unknown = Model.listRest(songs(5000), 0, Model.itemPartial(item, { tracks: songs(5000), complete: false, total: null }), true)
  assert.equal(unknown.more, null)
  assert.equal(unknown.capped, true)
  assert.equal(Model.restCapNotice(unknown, 5000).title, "Only the first 5,000 songs play")
  const whole = Model.listRest(songs(5000), 0, Model.itemPartial(item, { tracks: songs(5000), complete: true, total: 5000 }), true)
  assert.equal(whole.capped, false, "a list read whole is not capped")
})

test("Shuffle all goes on through the server shuffle until it ends", () => {
  const item = { type: "playlist", playlistId: "MLCT", title: "Library songs" }
  const partial = Model.itemPartial(item, { tracks: songs(49), complete: true })
  assert.deepEqual(partial, { total: null, more: { kind: "libraryShuffle", id: "MLCT" } })
  const rest = Model.listRest(songs(49), 0, partial, false)
  assert.equal(Model.restNote(rest), "Shuffle all goes on through your library")
  assert.deepEqual(Model.restRequest(rest, 500), { method: "tracksFor", params: { kind: "playlist", id: "MLCT", more: true, limit: 50 } })
  const known = { s1: true }
  const read = Model.restRead(rest, { tracks: [song("s1"), song("n1"), song("n2"), song("n2")], complete: false }, known)
  assert.deepEqual(read.tracks.map(t => t.videoId), ["n1", "n2"], "nothing queued or played comes back")
  assert.equal(read.more.kind, "libraryShuffle")
  assert.equal(Model.restStalled(read), false)
  // Nothing new (an empty page, or only songs queued) is not the end: the
  // next song asks again.
  for (const tracks of [[song("s1")], []]) {
    const stalled = Model.restRead(read, { tracks, complete: false }, { s1: true })
    assert.deepEqual(stalled.more, read.more, "it goes on")
    assert.equal(Model.restStalled(stalled), true)
    assert.equal(Model.restNote(stalled), "Shuffle all goes on through your library")
    assert.equal(Model.restRequest(stalled, 500).params.more, true)
  }
  assert.equal(Model.restRead(read, { tracks: [song("z")], complete: true }, {}).more, null, "the shuffle ended")
  assert.equal(Model.restStalled(Model.restRead(read, { tracks: [], complete: true }, {})), false, "an end is no stall")
  // A worker that was let go starts a new shuffle (complete false): its
  // songs not yet queued follow, and it goes on.
  const again = Model.restRead(read, { tracks: [song("n1"), song("s1"), song("m1"), song("m2")], complete: false },
                               { s1: true, n1: true })
  assert.deepEqual(again.tracks.map(t => t.videoId), ["m1", "m2"])
  assert.equal(again.more.kind, "libraryShuffle")
  assert.equal(Model.restCapNotice(again), null, "never capped")
})

test("Enter on a library song: the loaded songs, then the library in its order from the last of them", () => {
  const list = Model.libraryList({ sections: [{ items: songs(75) }], cursor: "c.1", header: { shuffleId: "MLCT", playParams: "P" } })
  assert.equal(Model.librarySongsPartial("albums", list), null)
  assert.equal(Model.librarySongsPartial("songs", Object.assign({}, list, { cursor: null, hasMore: false })), null, "all loaded")
  const partial = Model.librarySongsPartial("songs", list)
  assert.deepEqual(partial.more, { kind: "librarySongs", id: "MLCT", anchor: { videoId: "s74", index: 75 },
                                   back: { videoId: "s34", index: 35 }, params: "P" })
  const rest = Model.listRest(Model.libraryTracks(list), 10, partial, false)
  assert.equal(rest.tracks.length, 0, "65 songs from the 11th: all queued")
  assert.equal(Model.restNote(rest), "Then the rest of your library, in its order")
  assert.deepEqual(Model.restRequest(rest, 500).params, { kind: "librarySongs", id: "MLCT", videoId: "s74", index: 75,
                                                         backVideoId: "s34", backIndex: 35, params: "P", limit: 500 })
  // The worker answers from the anchor on, each song with its place.
  const reply = [song("s74", { libraryIndex: 75 })].concat(Array.from({ length: 96 }, (_, i) => song("s" + (75 + i), { libraryIndex: 76 + i })))
  const read = Model.restRead(rest, { tracks: reply, complete: false }, {})
  assert.equal(read.tracks.length, 96)
  assert.equal(read.tracks[0].videoId, "s75", "song 76 after song 75")
  assert.deepEqual(read.more.anchor, { videoId: "s170", index: 171 })
  assert.deepEqual(read.more.back, { videoId: "s130", index: 131 })
  assert.equal(read.more.params, "P")
  // An empty answer that goes on keeps the place; a page of songs already
  // queued moves it on.
  const empty = Model.restRead(read, { tracks: [], complete: false }, {})
  assert.equal(Model.restStalled(empty), true)
  assert.deepEqual(empty.more, read.more)
  const queued = Model.restRead(read, { tracks: [song("s170", { libraryIndex: 171 }), song("s171", { libraryIndex: 172 })], complete: false },
                                { s171: true })
  assert.equal(Model.restStalled(queued), true)
  assert.deepEqual(queued.more.anchor, { videoId: "s171", index: 172 })
  const end = Model.restRead(read, { tracks: [song("s170", { libraryIndex: 171 })], complete: true }, {})
  assert.equal(end.tracks.length, 0)
  assert.equal(end.more, null)
  const short = Model.librarySongsPartial("songs", Model.libraryList({ sections: [{ items: songs(20) }], cursor: "c" }))
  assert.equal(short.more.back, null, "fewer than 40 loaded: no earlier song")
})

test("Enter on a song of a playlist's first read goes on into the rest of it", () => {
  const page = { type: "collection", kind: "playlist", truncated: true, trackCount: 1126, tracks: songs(300) }
  const item = { type: "playlist", playlistId: "PLbig" }
  assert.deepEqual(Model.collectionPartial(page, item), { total: 1126, more: { kind: "item", item, anchor: "s299", offset: 300 } })
  assert.equal(Model.collectionPartial(Object.assign({}, page, { truncated: false }), item), null)
  assert.equal(Model.collectionPartial(page, null), null)
  const rest = Model.listRest(page.tracks, 5, Model.collectionPartial(page, item), false)
  assert.equal(rest.tracks.length, 0)
  assert.deepEqual(Model.restRequest(rest, 500), { item })
})

test("status and failure copies keep the rest's songs", () => {
  const rest = Model.withRest(Model.listRest(songs(700), 0, null, false), { token: 3 })
  assert.deepEqual(Model.restStatus(rest), { songs: 200, from: null, capped: false })
  assert.equal(Model.withRest(rest, { failedAt: "x|3" }).tracks.length, 200)
  assert.equal(Model.restStatus(null), null)
  assert.equal(Model.restNote(null), "")
  const queue = [{ videoId: "a", track: { counterpart: { videoId: "b" } } }]
  assert.deepEqual(Model.queuedIds(queue, ["c"]), { a: true, b: true, c: true })
})

test("Play next or Add to queue of Shuffle all never says only its first songs went in", () => {
  const item = { type: "playlist", playlistId: "MLCT", title: "Library songs" }
  const partial = Model.itemPartial(item, { tracks: songs(49), complete: true })
  assert.equal(Model.cutPartial(partial), null)
  assert.equal(Model.capNotice(49, Model.capWindow(songs(49), 0, 500), Model.cutPartial(partial)), null)
  const long = { total: 1126, more: { kind: "item", item: { type: "playlist", playlistId: "PL1" }, anchor: "s499", offset: 500 } }
  assert.equal(Model.cutPartial(long), long, "a list read in part keeps its notice")
  assert.equal(Model.cutPartial(null), null)
  assert.equal(Model.capNotice(500, Model.capWindow(songs(500), 0, 500), Model.cutPartial({ total: 961 })).title, "Queued 500 of 961 songs")
})
