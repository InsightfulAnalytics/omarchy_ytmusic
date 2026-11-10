const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

const tr = (videoId, extra = {}) => ({ type: "track", videoId, title: "Song " + videoId, ...extra })
const album = browseId => ({ type: "album", browseId, title: "Album " + browseId })
const artist = (channelId, extra = {}) => ({ type: "artist", channelId, name: "Artist " + channelId, ...extra })
const label = row => row.header ? "# " + row.title : row.item.type + ":" + (row.item.videoId || row.item.browseId
  || row.item.channelId || row.item.playlistId || row.item.title || "")
const ORDERS = [
  { index: "recently_added", title: "Recently saved", selected: false },
  { index: "a_to_z", title: "A to Z", selected: true },
  { index: "z_to_a", title: "Z to A", selected: false },
  { index: "menu:3", title: "Most songs", selected: false },
]

test("chips: Playlists first, h / l cycle with wrap, unknown chips fall back", () => {
  // S8 adds Podcasts after Artists, S10 Uploads after Podcasts.
  assert.deepEqual(Model.LIBRARY_CHIPS.map(c => c.key), ["playlists", "songs", "albums", "artists", "podcasts", "uploads"])
  assert.equal(Model.libraryChip("albums"), "albums")
  assert.equal(Model.libraryChip("downloads"), "playlists", "a chip the plugin does not have")
  assert.equal(Model.libraryChip(undefined), "playlists")
  assert.equal(Model.nextLibraryChip("playlists", 1), "songs")
  assert.equal(Model.nextLibraryChip("podcasts", 1), "uploads")
  assert.equal(Model.nextLibraryChip("uploads", 1), "playlists")
  assert.equal(Model.nextLibraryChip("playlists", -1), "uploads")
  assert.equal(Model.nextLibraryChip("songs", -1), "playlists")
  assert.equal(Model.libraryChipSpec("songs").method, "librarySongs")
  for (const chip of Model.LIBRARY_CHIPS) assert.equal(Model.normalizePref("libraryChip", chip.key), chip.key)
})

test("orders: O cycles from the current one, titles come as served", () => {
  assert.equal(Model.nextOrder(ORDERS, "a_to_z"), "z_to_a")
  assert.equal(Model.nextOrder(ORDERS, "menu:3"), "recently_added", "wraps around")
  assert.equal(Model.nextOrder(ORDERS, null), "z_to_a", "unknown current: after the served selection")
  assert.equal(Model.nextOrder(ORDERS.slice(0, 1), null), null, "nothing to cycle")
  assert.equal(Model.nextOrder(null, null), null)
  assert.equal(Model.orderTitle(ORDERS, "menu:3"), "Most songs")
  assert.equal(Model.orderTitle(ORDERS, null), "A to Z")
  assert.equal(Model.orderTitle([], "a_to_z"), "")
  assert.ok(Model.sameOrder(null, undefined) && !Model.sameOrder("a_to_z", "z_to_a"))
})

test("prefs: the chip survives a round trip; no sort order is kept (P1, acct-1)", () => {
  let prefs = Model.parsePrefs("")
  assert.equal(prefs.libraryChip, "playlists")
  assert.equal("libraryOrders" in prefs, false)
  prefs = Model.withPref(Model.withPref(prefs, "libraryOrders", { albums: "a_to_z" }), "libraryChip", "artists")
  assert.equal("libraryOrders" in prefs, false, "not a pref any more")
  const back = Model.parsePrefs(Model.serializePrefs(prefs))
  assert.equal(back.libraryChip, "artists")
  assert.equal("libraryOrders" in back, false)
  assert.equal(Model.libraryOrderOf, undefined)
  assert.equal(Model.withLibraryOrder, undefined)
})

test("playlists chip: entry rows, then your and saved playlists, the order in the first heading", () => {
  const playlists = [
    { type: "playlist", playlistId: "LM", title: "Liked Music", owned: false, special: "liked" },
    { type: "playlist", playlistId: "SE", title: "Episodes for later", owned: false, special: "episodes" },
    { type: "playlist", playlistId: "PL1", title: "Mine", owned: true },
    { type: "playlist", playlistId: "PL2", title: "Theirs", owned: false },
  ]
  const rows = Model.libraryRows("playlists", { orders: ORDERS, current: "a_to_z" }, playlists)
  assert.deepEqual(rows.map(label), ["liked:Liked Music", "history:History", "libraryLanding:Recent activity",
    "# Your playlists, A to Z", "playlist:PL1", "# Saved playlists", "playlist:PL2"])
  assert.equal(Model.itemSubtitle(rows[2].item), "What you played and saved lately")
  const target = Model.pageRef(rows[2].item)
  assert.equal(target.method, "libraryLanding")
  assert.deepEqual(target.params, {})
  // Without orders or playlists: the entry rows only.
  assert.deepEqual(Model.libraryRows("playlists", null, []).map(label),
    ["liked:Liked Music", "history:History", "libraryLanding:Recent activity"])
  // Only saved playlists: the order goes on their heading.
  assert.deepEqual(Model.libraryRows("playlists", { orders: ORDERS, current: "z_to_a" }, [playlists[3]]).map(label).slice(3),
    ["# Saved playlists, Z to A", "playlist:PL2"])
})

test("songs chip: Shuffle all, then indexed songs, then Load more", () => {
  const list = Model.libraryList({ type: "page", kind: "librarySongs", header: { shuffleId: "MLCT" }, cursor: "c.T",
    orders: ORDERS.slice(0, 3), order: "recently_added", sections: [{ type: "section", items: [tr("a"), tr("b")] }] })
  assert.equal(list.current, "recently_added")
  const rows = Model.libraryRows("songs", list, [])
  assert.deepEqual(rows.map(label), ["action:Shuffle all", "# Songs, Recently saved", "track:a", "track:b", "loadMore:Load more"])
  assert.deepEqual(rows.filter(r => r.trackIndex !== undefined).map(r => r.trackIndex), [0, 1])
  assert.equal(rows[0].item.id, Model.LIBRARY_SHUFFLE)
  assert.deepEqual(Model.libraryTracks(list).map(t => t.videoId), ["a", "b"])
  const mix = Model.libraryShuffleItem(list)
  assert.deepEqual(mix, { type: "playlist", playlistId: "MLCT", title: "Library songs" })
  assert.deepEqual(Model.tracksForParams(mix), { kind: "playlist", id: "MLCT" }, "the worker routes the shuffle id")
  // No shuffle row without the mix, and nothing to load once the list ends.
  const plain = Model.libraryList({ sections: [{ items: [tr("a")] }] })
  assert.deepEqual(Model.libraryRows("songs", plain, []).map(label), ["# Songs", "track:a"])
})

test("albums and artists chips: heading with the order, Subscriptions (and S9's Profiles) first on Artists", () => {
  const albums = Model.libraryList({ sections: [{ items: [album("MPRE1"), album("MPRE2")] }], hasMore: true,
    orders: ORDERS.slice(0, 3), order: "z_to_a" })
  assert.deepEqual(Model.libraryRows("albums", albums, []).map(label),
    ["# Albums, Z to A", "album:MPRE1", "album:MPRE2", "loadMore:Load more"])
  const artists = Model.libraryList({ sections: [{ items: [artist("UC1")] }], orders: ORDERS, order: "menu:3" })
  const rows = Model.libraryRows("artists", artists, [])
  assert.deepEqual(rows.map(label), ["librarySubscriptions:Subscriptions", "libraryProfiles:Profiles", "# Artists, Most songs",
                                     "artist:UC1"])
  assert.equal(Model.pageRef(rows[0].item).method, "librarySubscriptions")
  // An empty list: only the entry row (the view says it is loading or empty).
  assert.deepEqual(Model.libraryRows("artists", null, []).map(label),
    ["librarySubscriptions:Subscriptions", "libraryProfiles:Profiles"])
  // A served message.
  assert.deepEqual(Model.libraryRows("albums", Model.libraryList({ sections: [], message: "No albums yet" }), []).map(label),
    ["message:No albums yet"])
})

test("appending pages: repeats dropped, capped at the maximum", () => {
  let list = Model.libraryList({ sections: [{ items: [album("A"), album("B")] }], cursor: "c.1", orders: ORDERS, order: "a_to_z" })
  list = Model.appendLibraryList(list, Model.libraryList({ sections: [{ items: [album("B"), album("C")] }], cursor: "c.2" }), 2000)
  assert.deepEqual(list.items.map(a => a.browseId), ["A", "B", "C"])
  assert.equal(list.cursor, "c.2")
  assert.equal(list.current, "a_to_z", "the first page's order and orders stay")
  assert.equal(list.orders.length, 4)
  list = Model.appendLibraryList(list, Model.libraryList({ sections: [{ items: [album("D"), album("E")] }], cursor: "c.3" }), 4)
  assert.deepEqual(list.items.map(a => a.browseId), ["A", "B", "C", "D"])
  assert.equal(list.capped, true)
  assert.equal(list.cursor, null)
  // G1: the cap says how to reach the rest (the list has orders).
  assert.deepEqual(Model.libraryRows("albums", list, []).map(label).slice(-1),
    ["message:Showing the first 4; change the order (O) for the rest"])
})

test("playlists replies flatten too, and their subtitle falls back to the served one", () => {
  const list = Model.libraryList({ items: [{ type: "playlist", playlistId: "RDCLAKx", title: "Mix", description: "Auto playlist" }],
    orders: ORDERS.slice(0, 3), order: "recently_added" })
  assert.equal(list.items.length, 1)
  assert.equal(list.cursor, null)
  assert.equal(list.hasMore, false)
  assert.equal(Model.itemSubtitle(list.items[0]), "Auto playlist")
  assert.equal(Model.itemSubtitle({ type: "playlist", author: "Me", count: 3, description: "Me • 3 tracks" }), "Me • 3 songs")
  assert.equal(Model.itemSubtitle(artist("UC1", { subtitle: "149 songs" })), "149 songs")
})

test("library artists: menu Shuffle and Start radio play YouTube's mixes", () => {
  const row = artist("UC1", { shuffleId: "RDAO1", radioId: "RDEM1" })
  assert.deepEqual(Model.artistMix(row, "shuffleId"), { type: "playlist", playlistId: "RDAO1", title: "Artist UC1" })
  assert.equal(Model.artistMix(artist("UC2"), "shuffleId"), null)
  assert.deepEqual(Model.radioParams(row), { mixId: "RDEM1" })
  assert.deepEqual(Model.radioParams(artist("UC2")), { channelId: "UC2" })

  const played = []
  const service = { playItem: (item, mode) => played.push([item.playlistId || item.channelId, mode]) }
  const shuffle = Model.menuAction("shuffle")
  shuffle.run({ item: row, ctx: { pageKind: "library" }, service })
  shuffle.run({ item: artist("UC2"), ctx: { pageKind: "library" }, service })
  assert.deepEqual(played, [["RDAO1", "now"], ["UC2", "shuffle"]], "the mix when the row has one, else the artist's songs")
  const ids = Model.menuActions(row, { pageKind: "library", signedIn: true }).map(a => a.id)
  assert.ok(ids.includes("shuffle") && ids.includes("radio") && ids.includes("open"))
})

test("Liked Music listed as a playlist opens as Liked Music; profiles open their own page (S9)", () => {
  const liked = Model.pageRef({ type: "playlist", playlistId: "LM", title: "Liked Music" })
  assert.equal(liked.kind, "liked")
  assert.equal(liked.key, Model.pageRef({ type: "liked" }).key)
  assert.equal(Model.pageRef({ type: "playlist", playlistId: "VLLM" }).kind, "liked")
  assert.equal(Model.pageRef({ type: "playlist", playlistId: "PL1" }).kind, "playlist")
  const profile = Model.pageRef({ type: "profile", channelId: "UCp" })
  assert.equal(profile.method, "user")
  assert.deepEqual(profile.params, { channelId: "UCp" })
  assert.equal(Model.pageRound(profile, null), true)
})

test("landing and subscriptions pages take their orders through pageRef", () => {
  const plain = Model.pageRef({ type: "libraryLanding", title: "Recent activity" })
  const sorted = Model.pageRef({ type: "libraryLanding", title: "Recent activity" }, { order: "menu:2" })
  assert.notEqual(plain.key, sorted.key)
  assert.deepEqual(sorted.params, { order: "menu:2" })
  assert.equal(plain.id, sorted.id, "both forget together")
  assert.equal(Model.pageKindTitle(Model.pageRef({ type: "librarySubscriptions" })), "Subscriptions")
  // The page rows are F2's: one untitled section, Load more while there is more.
  const page = { type: "page", kind: "libraryLanding", sections: [{ title: null, items: [album("MPRE1")] }], cursor: "c.1" }
  assert.deepEqual(Model.pageRows(page).map(label), ["album:MPRE1", "loadMore:Load more"])
})
