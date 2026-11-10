const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

// Podcasts (S8): the pages, the Podcasts chip, resume, chapters and saving.

const episode = (n, extra = {}) => ({ type: "track", kind: "episode", videoId: ("ep" + n).padEnd(11, "x"), title: "Episode " + n,
  artists: [{ name: "Show", id: "MPSPPLshow" }], artistText: "Show", podcast: { name: "Show", id: "MPSPPLshow" },
  date: "Jul 31", durationSec: 3900, progress: null, ...extra })
const podcast = { type: "podcast", browseId: "MPSPPLshow", podcastId: "PLshow", title: "Show", authorText: "Host" }
const ids = (item, ctx) => Model.menuActions(item, ctx).map(a => a.id)

test("routes: MPSP opens a podcast, MPED an episode, RDPN and SE their own pages", () => {
  assert.deepEqual(Model.routeEndpoint({ browseId: "MPSPPLshow", title: "Show" }), { type: "podcast", browseId: "MPSPPLshow", title: "Show" })
  const ep = Model.routeEndpoint({ browseId: "MPEDabcdefghijk" })
  assert.equal(ep.type, "episode")
  assert.equal(ep.videoId, "abcdefghijk")
  for (const id of ["VLRDPN", "RDPN"]) assert.equal(Model.routeEndpoint({ browseId: id }).type, "newEpisodes", id)
  for (const id of ["VLSE", "SE"]) assert.equal(Model.routeEndpoint({ browseId: id }).type, "savedEpisodes", id)
  assert.equal(Model.routeEndpoint({ browseId: "VLPLother" }).type, "playlist", "other playlists as before")
  // A channel's Latest episodes See all (the worker marks its page type).
  const all = Model.routeEndpoint({ browseId: "UCchan", params: "P", pageType: Model.CHANNEL_EPISODES_PAGE, title: "Latest episodes" })
  assert.deepEqual(all, { type: "channelEpisodes", channelId: "UCchan", params: "P", title: "Latest episodes" })
  // Rows listed as playlists open the same pages; RDPN is no mix.
  assert.equal(Model.pageKindOf({ type: "playlist", playlistId: "SE" }), "savedEpisodes")
  assert.equal(Model.pageKindOf({ type: "playlist", playlistId: "VLRDPN" }), "newEpisodes")
  assert.equal(Model.isMixId("RDPN"), false)
})

test("page kinds: each reads its own worker method", () => {
  const show = Model.pageRef(podcast)
  assert.equal(show.method, "podcast")
  assert.deepEqual(show.params, { browseId: "MPSPPLshow" })
  assert.equal(Model.pageRef({ type: "podcast", podcastId: "PLshow" }).key, show.key, "by podcastId too")
  const page = Model.pageRef(Model.episodeItemOf(episode(1)))
  assert.equal(page.method, "episode")
  assert.deepEqual(page.params, { videoId: episode(1).videoId })
  assert.deepEqual(Model.pageRef({ type: "episode", browseId: "MPEDabcdefghijk" }).params, { videoId: "abcdefghijk" })
  assert.equal(Model.pageRef({ type: "channel", channelId: "UCc" }).method, "channel")
  assert.equal(Model.pageRef({ type: "channelEpisodes", channelId: "UCc" }), null, "needs its params")
  for (const type of ["savedEpisodes", "newEpisodes", "libraryChannels"]) assert.equal(Model.pageRef({ type }).method, type)
  assert.ok(Model.isPodcastKind("podcast") && !Model.isPodcastKind("album"))
})

test("loadfileOptions: an episode starts at its percent or second", () => {
  const track = episode(1, { title: "T" })
  const title = Model.loadfileOptions(track)
  assert.ok(title.indexOf("force-media-title=") === 0 && title.indexOf("start=") < 0)
  assert.equal(Model.loadfileOptions(track, { percent: 37.6 }), title + ",start=37%")
  assert.equal(Model.loadfileOptions(track, { seconds: 754.9 }), title + ",start=754")
  assert.equal(Model.loadfileOptions(track, { percent: 0 }), title, "0% is the start")
  assert.equal(Model.loadfileOptions(track, { percent: 100 }), title)
  assert.equal(Model.loadfileOptions(track, null), title)
  assert.equal(Model.loadfileOptions({ videoId: "untitled001" }, { percent: 5 }), "start=5%")
  const command = Model.withStartOption(Model.playCollectionCommands([track], 0).commands[1], { percent: 12 })
  assert.deepEqual(command.slice(0, 4), ["loadfile", "https://www.youtube.com/watch?v=" + track.videoId, "append", -1])
  assert.equal(command[4], title + ",start=12%")
  assert.equal(Model.withStartOption(["stop"], { seconds: 5 })[0], "stop", "only a loadfile")
})

test("positions: kept per episode, bounded, played ones marked done", () => {
  let positions = {}
  positions = Model.withPosition(positions, "a", 754.4, 3600, 1000)
  assert.deepEqual(positions.a, { pos: 754, dur: 3600, at: 1000, done: false })
  assert.equal(Model.withPosition(positions, "b", 4, 3600, 1001).b, undefined, "the first seconds are no place")
  assert.equal(Model.withPosition(positions, "a", 3, 3600, 1002).a, undefined, "back at the start: forgotten")
  assert.equal(Model.withPosition(positions, "a", 3590, 3600, 1003).a.done, true, "near the end: played")
  assert.equal(Model.withFinished(positions, "a", 3600, 1004).a.done, true)
  // At most POSITIONS_MAX, the oldest dropped.
  let many = {}
  for (let i = 0; i < Model.POSITIONS_MAX + 5; i++) many = Model.withPosition(many, "v" + i, 100, 3600, 10 + i)
  assert.equal(Object.keys(many).length, Model.POSITIONS_MAX)
  for (let i = 0; i < 5; i++) assert.equal(many["v" + i], undefined, "v" + i + " was the oldest")
  assert.ok(many["v" + (Model.POSITIONS_MAX + 4)])
  // positions.json round trip; a bad or foreign file is empty.
  assert.deepEqual(Model.parsePositions(Model.positionsText(many)), many)
  assert.deepEqual(Model.parsePositions("{nope"), {})
  assert.deepEqual(Model.parsePositions(JSON.stringify({ version: 2, positions: { a: { pos: 1, at: 1 } } })), {})
})

test("resume: the place kept here first, else YouTube's percent, else the start", () => {
  const track = episode(1, { progress: 40 })
  assert.deepEqual(Model.episodeResume(track, {}), { percent: 40 })
  const kept = Model.withPosition({}, track.videoId, 1200, 3900, 5)
  assert.deepEqual(Model.episodeResume(track, kept), { seconds: 1200 })
  assert.equal(Model.episodeResume(track, Model.withFinished({}, track.videoId, 3900, 6)), null, "played here")
  assert.equal(Model.episodeResume(episode(2, { progress: 97 }), {}), null, "YouTube counts it played")
  assert.equal(Model.episodeResume(episode(3, { progress: 0 }), {}), null)
  assert.equal(Model.episodeResume({ type: "track", videoId: "song0000001", progress: 50 }, {}), null, "songs never")
  // YouTube's time left beats its percent: a barely started episode is served as 10% played.
  assert.equal(Model.episodeResume(episode(4, { progress: 10, resumeSec: 0 }), {}), null)
  assert.deepEqual(Model.episodeResume(episode(5, { progress: 10, resumeSec: 900 }), {}), { seconds: 900 })
  assert.equal(Model.episodeResume(episode(6, { progress: 100, resumeSec: 900 }), {}), null, "played")
  assert.equal(Model.episodePlayedText({ progress: 10, progressText: "2 hr 16 min left" }), "2 hr 16 min left")
  assert.equal(Model.episodePlayedText({ progress: 100, progressText: "2 min left" }), "Played")
  assert.equal(Model.episodePlayedText({ progressPercentage: 25 }), "25% played", "an episode page's header")
})

test("chapters: a seek in the episode playing, else a play from that second", () => {
  const chapter = { type: "chapter", title: "Riemann hypothesis", seconds: 3956, time: "1:05:56", videoId: "ZAzWPE9CItg" }
  assert.deepEqual(Model.chapterAction(chapter, "ZAzWPE9CItg"), { action: "seek", seconds: 3956, videoId: "ZAzWPE9CItg" })
  assert.deepEqual(Model.chapterAction(chapter, "other000001"), { action: "play", seconds: 3956, videoId: "ZAzWPE9CItg" })
  assert.equal(Model.chapterAction(chapter, "").action, "play", "nothing playing")
  const row = Model.chapterRow(chapter)
  assert.equal(row.type, "action")
  assert.equal(row.id, Model.CHAPTER_PREFIX + "3956")
  assert.equal(row.hint, "1:05:56")
  assert.deepEqual(row.chapter, { seconds: 3956, videoId: "ZAzWPE9CItg", title: "Riemann hypothesis" })
  // The episode page's chapters become action rows; Enter on one runs it.
  const page = { type: "page", kind: "episode", id: "ZAzWPE9CItg", title: "E",
    header: { episode: episode(1, { videoId: "ZAzWPE9CItg" }), progressPercentage: 25, date: "Oct 26", duration: "3 hr 34 min",
              show: { name: "Lex Fridman Podcast", id: "MPSPPLlex" }, description: "Line one\nLine two" },
    sections: [{ type: "section", title: "Chapters", key: "chapters", items: [chapter, { type: "chapter", title: "Intro", seconds: 0, videoId: "ZAzWPE9CItg" }] }] }
  const adapted = Model.PAGE_KINDS.episode.adapt(page)
  const rows = Model.pageRows(adapted)
  assert.deepEqual(rows.map(r => r.header ? "#" + r.title : r.item.id), ["#Chapters", "chapter:3956", "chapter:0"])
  assert.equal(rows[2].item.hint, "0:00")
  assert.equal(page.sections[0].items[0].type, "chapter", "the reply itself is left as it was")
  assert.equal(Model.episodeOfPage(adapted).progress, 25, "the header's progress goes with the episode")
  assert.equal(Model.pageSubtitle(Model.pageRef({ type: "episode", videoId: "ZAzWPE9CItg" }), adapted), "Oct 26 • 3 hr 34 min • 25% played")
  assert.deepEqual(Model.podcastByline(adapted), [{ text: "Lex Fridman Podcast", item: { type: "podcast", browseId: "MPSPPLlex", title: "Lex Fridman Podcast", thumb: null } }])
  // The About row, then the description, then the chapters.
  const about = Model.podcastRows(adapted, { about: true, aboutOpen: true })
  assert.deepEqual(about.slice(0, 3).map(r => r.item.title), ["Hide the description", "Line one", "Line two"])
  assert.equal(Model.podcastRows(adapted, {}).length, rows.length, "without About when it all shows")
})

test("episode rows: date, length, podcast and progress in the subtitle", () => {
  assert.equal(Model.itemSubtitle(episode(1, { durationSec: 14460, progress: 37 })), "Jul 31 • 4 hr 1 min • Show • 37% played")
  assert.equal(Model.itemSubtitle(episode(1, { durationSec: 45, date: null, progress: 99 })), "45 sec • Show • Played")
  assert.equal(Model.itemSubtitle({ type: "podcast", title: "P", authorText: "Host" }), "Host")
  assert.equal(Model.itemSubtitle({ type: "podcast", title: "P", subtitle: "Podcast • Host" }), "Podcast • Host", "a served subtitle stays")
  assert.equal(Model.itemSubtitle({ type: "track", videoId: "song0000001", artistText: "A" }), "A", "songs as before")
  assert.equal(Model.episodeLength(3600), "1 hr")
  assert.equal(Model.episodeLength(2280), "38 min")
  // YouTube serves an unplayed episode's length as its progress text: not a played text.
  assert.equal(Model.itemSubtitle(episode(1, { durationSec: 14460, progress: 0, progressText: "4 hr 1 min" })),
               "Jul 31 • 4 hr 1 min • Show")
  assert.equal(Model.itemSubtitle(episode(1, { durationSec: 14460, progress: 10, progressText: "3 hr 40 min left" })),
               "Jul 31 • 4 hr 1 min • Show • 3 hr 40 min left")
  const header = { date: "Oct 26", duration: "3 hr 34 min", durationSec: 12840, progressPercentage: 0, progressText: "3 hr 34 min" }
  assert.equal(Model.episodeSubtitle({ type: "page", kind: "episode", header }), "Oct 26 • 3 hr 34 min")
  assert.equal(Model.episodeSubtitle({ type: "page", kind: "episode", header: { ...header, progressText: null } }), "Oct 26 • 3 hr 34 min")
})

test("the Podcasts chip: Episodes for Later with its count, New episodes, Channels, then the podcasts", () => {
  assert.ok(Model.LIBRARY_CHIPS.some(c => c.key === "podcasts" && c.method === "libraryPodcasts"))
  const list = Model.libraryList({ type: "page", sections: [{ items: [podcast] }], cursor: "c.x", header: { episodesForLater: { count: 2 } },
                                   orders: [{ index: "recently_added", title: "Recently saved", selected: true }], order: null })
  const rows = Model.libraryRows("podcasts", list, [])
  assert.deepEqual(rows.map(r => r.header ? "#" + r.title : r.item.type),
                   ["savedEpisodes", "newEpisodes", "libraryChannels", "#Podcasts, Recently saved", "podcast", "loadMore"])
  assert.equal(rows[0].item.subtitle, "2 episodes")
  assert.equal(Model.libraryRows("podcasts", { items: [], header: { episodesForLater: { count: 1 } } }, [])[0].item.subtitle, "1 episode")
  assert.equal(Model.libraryRows("podcasts", null, []).length, 3, "the entries show before the list loads")
  // Each entry opens its page.
  for (const r of rows.slice(0, 3)) assert.ok(Model.pageRef(r.item), r.item.type)
  assert.deepEqual(Model.libraryRows("albums", { items: [] }, []), [], "other chips as before")
  assert.equal(Model.collectionMeta({ type: "collection", kind: "episodesForLater", trackCount: 3, header: {} }), "3 episodes")
})

test("S and the menus: Save a podcast, an episode to Episodes for Later; no radio for episodes", () => {
  const ctx = { signedIn: true, pageKind: "search" }
  assert.equal(Model.saveAction(podcast, {}), "savePodcast")
  assert.equal(Model.saveAction(episode(1), {}), "saveEpisode")
  assert.equal(Model.saveTarget(episode(1), {}), "row")
  assert.equal(Model.saveAction({ type: "track", videoId: "song0000001" }, {}), "", "songs as before")
  assert.deepEqual(Model.savePodcastParams(podcast, true), { podcastId: "PLshow", save: true })
  assert.equal(Model.menuKind(podcast), "podcast")
  assert.ok(ids(podcast, ctx).includes("savePodcast") && ids(podcast, ctx).includes("share"))
  assert.equal(Model.shareUrl(podcast), "https://music.youtube.com/playlist?list=PLshow")
  assert.equal(Model.menuActions(podcast, { ...ctx, podcastSaved: true }).find(a => a.id === "savePodcast").title, "Remove from library")
  const row = ids(episode(1), ctx)
  assert.ok(row.includes("saveEpisode") && row.includes("goPodcast") && row.includes("goEpisode"))
  assert.ok(!row.includes("radio") && !row.includes("goArtist"), "an episode has no radio and no artist")
  assert.equal(Model.radioParams(episode(1)), null, "nor has R (Panel.startRadio goes by radioParams)")
  assert.deepEqual(Model.radioParams({ type: "track", videoId: "song0000001" }), { videoId: "song0000001" }, "songs as before")
  // Episodes for Later's own rows: Remove (x), not a second entry that does the same.
  const later = Model.menuActions(episode(1), { signedIn: true, pageKind: "savedEpisodes", removable: true, episodeSaved: true,
                                                removeTitle: "Remove from Episodes for Later" })
  assert.deepEqual(later.filter(a => a.title === "Remove from Episodes for Later").map(a => a.id), ["remove"])
  assert.ok(Model.KEYS.find(k => k.id === "remove").title.includes("Episodes for Later"), "x's help names it")
  // A podcast kind's page menu has no Shuffle, as its header has none.
  for (const pageKind of Model.PODCAST_KINDS)
    assert.ok(!ids(podcast, { page: true, pageKind, hasTracks: true }).includes("shuffle"), pageKind)
  assert.ok(ids({ type: "album", browseId: "MPREb_x" }, { page: true, pageKind: "album", hasTracks: true }).includes("shuffle"),
            "albums as before")
  assert.equal(Model.menuActions(episode(1), { ...ctx, episodeSaved: true }).find(a => a.id === "saveEpisode").title,
               "Remove from Episodes for Later")
  assert.ok(!ids(episode(1), { pageKind: "search" }).includes("saveEpisode"), "signed out")
  assert.ok(ids({ type: "track", videoId: "song0000001", artists: [{ name: "A", id: "UCa" }] }, ctx).includes("radio"))
  assert.ok(!ids(episode(1), { ...ctx, pageKind: "podcast" }).includes("goPodcast"), "on its podcast's page")
  assert.deepEqual(Model.podcastItemOf(episode(1)), { type: "podcast", browseId: "MPSPPLshow", title: "Show", thumb: null })
  assert.equal(Model.creditItem({ name: "Show", id: "MPSPPLshow" }).type, "podcast")
  assert.equal(Model.creditItem({ name: "A", id: "UCa" }).type, "artist")
  // A channel subscribes by its own id.
  assert.deepEqual(Model.subscribeParams({ type: "channel", channelId: "UCc" }, true), { pageChannelId: "UCc", subscribe: true, channelId: "UCc" })
})

test("saved states: an edit made here, then the page, then the lists", () => {
  assert.equal(Model.podcastSavedState("PLshow", { edits: { PLshow: false }, fallback: true }), false)
  assert.equal(Model.podcastSavedState("PLshow", { fallback: true }), true)
  assert.equal(Model.podcastSavedState("PLshow", { podcasts: [podcast] }), true)
  assert.equal(Model.podcastSavedState("PLother", { podcasts: [podcast], complete: true }), false)
  assert.equal(Model.podcastSavedState("PLother", { podcasts: [podcast] }), null)
  const saved = { type: "collection", tracks: [episode(1, { setVideoId: "S1" })] }
  assert.equal(Model.episodeSavedState(episode(1).videoId, { saved: saved.tracks }), true)
  assert.equal(Model.episodeSavedState(episode(2).videoId, { saved: saved.tracks, complete: true }), false)
  assert.equal(Model.savedEpisodeSetId(saved, episode(1).videoId), "S1")
  assert.equal(Model.savedEpisodeSetId(saved, episode(2).videoId), "")
  assert.equal(Model.savedEpisodeSetId(null, episode(1).videoId), "")
})
