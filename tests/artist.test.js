const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

// An artist page as the worker sends it (S1): the raw rows in order, the
// ones get_artist named carrying their key, the rest generic.
const id11 = n => ("vid" + n).padEnd(11, "x")
const tr = (n, extra = {}) => ({ type: "track", videoId: id11(n), title: "Song " + n, artistText: "Radiohead", available: true, kind: "song", ...extra })
const album = (n, type) => ({ type: "album", browseId: "MPREb_" + n, title: "Album " + n, artists: [], artistText: "", year: "199" + n, albumType: type })
const CHANNEL = "UCr_iyUANcn9OX_yy9piYoLw"
const section = (title, key, items, more) => ({ type: "section", title, key, items, more: more || null, play: null, layout: "carousel" })
const more = (browseId, params, pageType) => ({ browseId, params: params || null, pageType: pageType || null })

const page = {
  type: "page", v: 2, kind: "artist", id: CHANNEL, title: "Radiohead", subtitle: null, thumb: "t", thumbLarge: "T", round: true,
  header: {
    channelId: CHANNEL, subscribeId: "UCq19", subscribed: false, subscribers: "5.46M", monthlyListeners: "205M",
    views: "5,409,889,352 views", description: "Radiohead are an English rock band.\n\nFormed in Abingdon in 1985.",
    descriptionRuns: [{ text: "Radiohead are..." }, { text: "https://en.wikipedia.org/wiki/Radiohead", url: "https://en.wikipedia.org/wiki/Radiohead" },
                      { text: "again", url: "https://en.wikipedia.org/wiki/Radiohead" }, { text: "bad", url: "javascript:alert(1)" }],
    shuffleId: "RDAOk8x", radioId: "RDEMk8x", songsPlaylistId: "VLOLAK5uy_songs"
  },
  sections: [
    section(null, null, [tr(9, { kind: "video", title: "Radiohead TV" })]),
    section("Top songs", "songs", [tr(1), tr(2), tr(3)], more("VLOLAK5uy_songs", "ggMCCAI%3D", "MUSIC_PAGE_TYPE_PLAYLIST")),
    section("Albums", "albums", [album(1, "Album"), album(2, "Album")], more("MPAD" + CHANNEL, "pAlbums", "MUSIC_PAGE_TYPE_ARTIST_DISCOGRAPHY")),
    section("Singles & EPs", "singles", [album(3, "Single")], more("MPAD" + CHANNEL, "pSingles", "MUSIC_PAGE_TYPE_ARTIST_DISCOGRAPHY")),
    section("Videos", "videos", [tr(4, { kind: "video" }), tr(5, { kind: "video" })], more("VLOLAK5uy_videos", "ggMCCAI%3D")),
    section("Live performances", null, [tr(6, { kind: "video" })], more(CHANNEL, "pLive", "MUSIC_PAGE_TYPE_ARTIST")),
    section("From your library", null, [album(7, "Album")], more("MPLA" + CHANNEL, "ggMCCAI%3D")),
    section("Featured on", null, [{ type: "playlist", playlistId: "RDCLAK5uy_f", title: "This Is Radiohead" }]),
    section("Playlists by Radiohead", null, [{ type: "playlist", playlistId: "PLr", title: "Live" }], more(CHANNEL, "pPlaylists", "MUSIC_PAGE_TYPE_ARTIST")),
    section("Fans might also like", "related", [{ type: "artist", channelId: "UCthom", name: "Thom Yorke", subtitle: "1.2M" }]),
    section("Podcasts", null, [{ type: "podcast", browseId: "MPSPx", title: "A podcast" }])
  ],
  cursor: null, hasMore: false, orders: [], order: null, message: null
}
const target = { type: "artist", channelId: CHANNEL, name: "Radiohead" }
const ref = Model.pageRef(target)
const label = row => row.header ? "# " + row.title : row.item.type + (row.trackIndex !== undefined ? ":" + row.trackIndex : "")

test("an artist page renders through pageRows, tracks numbered within their section", () => {
  const rows = Model.pageRows(page)
  assert.deepEqual(rows.map(label), [
    "track:0",
    "# Top songs", "track:0", "track:1", "track:2", "more",
    "# Albums", "album", "album", "more",
    "# Singles & EPs", "album", "more",
    "# Videos", "track:0", "track:1", "more",
    "# Live performances", "track:0", "more",
    "# From your library", "album", "more",
    "# Featured on", "playlist",
    "# Playlists by Radiohead", "playlist", "more",
    "# Fans might also like", "artist",
    "# Podcasts", "podcast"])
  // Enter on the third top song plays the top songs from there.
  const third = rows[4]
  assert.equal(third.sectionIndex, 1)
  assert.deepEqual(Model.sectionTracks(page, third.sectionIndex).map(t => t.videoId), [id11(1), id11(2), id11(3)])
  // See all rows are selectable and name their section.
  const seeAll = rows.filter(r => r.item && r.item.type === "more")
  assert.ok(seeAll.every(Model.selectableRow))
  assert.deepEqual(seeAll.map(r => r.item.title).slice(0, 3), ["See all Top songs", "See all Albums", "See all Singles & EPs"])
  // The cursor walks onto them like any row.
  assert.equal(Model.nextSelectable(rows, 4, 1), 5)
})

test("artist pages and their artists have round covers", () => {
  assert.equal(ref.kind, "artist")
  assert.equal(Model.pageRound(ref, page), true)
  assert.equal(Model.pageRound(ref, null), true, "round before the page arrives")
  const related = Model.pageRows(page).find(r => r.item && r.item.type === "artist").item
  assert.equal(Model.pageKindOf(related), "artist")
})

test("See all routes: discographies open artistAlbums, the rest as before", () => {
  const rows = Model.pageRows(page)
  const targetOf = title => Model.moreTarget(rows.find(r => r.item && r.item.type === "more" && r.item.sectionTitle === title).item)
  const albums = targetOf("Albums")
  assert.deepEqual(albums, { type: "artistAlbums", browseId: "MPAD" + CHANNEL, params: "pAlbums", title: "Albums" })
  const albumsRef = Model.pageRef(albums)
  assert.equal(albumsRef.method, "artistAlbums")
  assert.deepEqual(albumsRef.params, { browseId: "MPAD" + CHANNEL, params: "pAlbums" })
  const singlesRef = Model.pageRef(targetOf("Singles & EPs"))
  assert.notEqual(singlesRef.key, albumsRef.key, "albums and singles share a browseId, not a page")
  // An order chip sends the served title back.
  const sorted = Model.pageRef(albums, { order: "Recency" })
  assert.equal(sorted.params.order, "Recency")
  assert.notEqual(sorted.key, albumsRef.key)
  assert.deepEqual(targetOf("Top songs"), { type: "playlist", playlistId: "OLAK5uy_songs", title: "Top songs" })
  assert.equal(targetOf("Videos").type, "playlist")
  for (const title of ["Live performances", "Playlists by Radiohead", "From your library"]) {
    const t = targetOf(title)
    assert.equal(t.type, "browse", title)
    assert.ok(t.params, "channel + params and MPLA + params are browse pages")
  }
  // A discography endpoint without its params cannot open the list.
  assert.equal(Model.routeEndpoint({ browseId: "MPAD" + CHANNEL }).type, "browse")
  assert.equal(Model.pageRef({ type: "artistAlbums", browseId: "MPADx" }), null)
})

test("the header subtitle is the artist's audience", () => {
  assert.equal(Model.pageSubtitle(ref, page), "205M monthly listeners, 5.46M subscribers")
  assert.equal(Model.pageSubtitle(ref, Object.assign({}, page, { header: { channelId: CHANNEL } })), "Artist")
  const browse = Model.pageRef({ type: "browse", browseId: "FEx" })
  assert.equal(Model.pageSubtitle(browse, { type: "page", kind: "browse", subtitle: "S", header: {} }), "S")
  assert.equal(Model.pageSubtitle(browse, null), "")
})

test("Play and Queue all read the artist's whole songs list, not the songs shown", () => {
  assert.equal(Model.pagePartial("artist"), true)
  assert.equal(Model.pagePartial("browse"), false)
  assert.equal(Model.completeTracks(page), null, "so Service.itemTracks asks tracksFor")
  assert.deepEqual(Model.tracksForParams(target), { kind: "artist", id: CHANNEL })
})

test("Shuffle plays the shuffle mix and Radio the radio mix", () => {
  const shuffle = Model.artistShuffleItem(page)
  assert.equal(Model.menuKind(shuffle), "mix")
  assert.deepEqual(Model.tracksForParams(shuffle), { kind: "mix", id: "RDAOk8x" })
  assert.equal(Model.itemTitle(shuffle), "Radiohead")
  const radio = Model.artistRadioItem(page)
  assert.deepEqual(Model.radioParams(radio), { mixId: "RDEMk8x" })
  const bare = Object.assign({}, page, { header: { channelId: CHANNEL } })
  assert.equal(Model.artistShuffleItem(bare), null)
  assert.equal(Model.artistRadioItem(bare), null)
  assert.equal(Model.artistShuffleItem({ type: "page", kind: "browse", header: { shuffleId: "RDAOx" } }), null)
})

test("the page menu has the header buttons' twins and About", () => {
  const ctx = { page: true, pageKind: "artist", hasTracks: true, about: true, signedIn: true }
  const actions = Model.menuActions(target, ctx)
  assert.deepEqual(actions.map(a => a.id), ["play", "shuffle", "playNext", "addToQueue", "radio", "share", "about"])
  assert.ok(!Model.menuActions(target, Object.assign({}, ctx, { about: false })).some(a => a.id === "about"))
  assert.ok(!Model.menuActions(target, { pageKind: "artist", about: true }).some(a => a.id === "about"), "a row menu has no About")
  const calls = []
  const owner = { playAll: mode => calls.push("playAll:" + mode), openAbout: () => calls.push("about") }
  const service = { startRadio: item => calls.push("radio:" + item.channelId), playItem: () => calls.push("playItem") }
  for (const id of ["play", "shuffle", "addToQueue", "radio", "about"])
    actions.find(a => a.id === id).run({ item: target, ctx, index: -1, owner, panel: {}, service })
  assert.deepEqual(calls, ["playAll:now", "playAll:shuffle", "playAll:queue", "radio:" + CHANNEL, "about"])
})

test("About opens a page of the artist's description and links", () => {
  assert.equal(Model.hasAbout(page), true)
  assert.equal(Model.hasAbout(Object.assign({}, page, { header: { channelId: CHANNEL } })), false)
  const item = Model.aboutItem(page)
  assert.deepEqual(item, { type: "about", channelId: CHANNEL, title: "Radiohead", thumb: "t", thumbLarge: "T" })
  const aboutRef = Model.pageRef(item)
  assert.equal(aboutRef.method, "artist", "the artist page, from the worker's disk copy")
  assert.deepEqual(aboutRef.params, { channelId: CHANNEL })
  assert.notEqual(aboutRef.key, ref.key, "its own Store entry")
  assert.equal(Model.pageRound(aboutRef, null), true)
  const about = Model.pageKind(aboutRef).adapt(page, aboutRef)
  assert.equal(about.title, "Radiohead")
  assert.equal(about.subtitle, "5,409,889,352 views")
  const rows = Model.pageRows(about)
  assert.deepEqual(rows.map(r => r.header ? "# " + r.title : r.item.type + ":" + r.item.title), [
    "text:Radiohead are an English rock band.", "text:Formed in Abingdon in 1985.",
    "# Links", "action:https://en.wikipedia.org/wiki/Radiohead"])
  // Paragraphs take the cursor (the keyboard scrolls through them) but have no menu.
  assert.ok(rows.filter(r => !r.header).every(Model.selectableRow))
  assert.equal(Model.menuKind(rows[0].item), "")
  assert.equal(Model.itemKey(rows[0].item), "text:Radiohead are an English rock band.")
  // A link row copies its URL.
  const link = rows[3].item
  assert.equal(Model.shareUrl({ type: "link", url: link.url }), "https://en.wikipedia.org/wiki/Radiohead")
  assert.equal(Model.shareUrl({ type: "link", url: "javascript:alert(1)" }), "")
  assert.equal(Model.linkUrl("https://a.org/x y"), "")
  // Without a description: a note, and no Links header.
  const empty = Model.aboutPage(Object.assign({}, page, { header: { channelId: CHANNEL } }))
  assert.deepEqual(Model.pageRows(empty).map(label), ["message"])
})

test("artist-less album rows say what they are", () => {
  assert.equal(Model.itemSubtitle(album(1, "Album")), "Album • 1991")
  assert.equal(Model.itemSubtitle(album(3, "Single")), "Single • 1993")
  const withArtist = Object.assign(album(2, "EP"), { artistText: "Radiohead" })
  assert.equal(Model.itemSubtitle(withArtist), "EP • Radiohead • 1992")
})
