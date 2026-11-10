const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

// Profiles (S9): profile pages, Your channel, the library's Profiles row
// and subscribing to a profile.

const profile = (id, extra = {}) => ({ type: "profile", channelId: id, name: "Someone", subtitle: null, thumb: null, ...extra })
const ids = (item, ctx) => Model.menuActions(item, ctx).map(a => a.id)
const label = row => row.header ? "# " + row.title : row.item.type + ":" + (row.item.channelId || row.item.title || "")
const userPage = (header, extra = {}) => ({
  type: "page", v: 2, kind: "user", id: header.channelId, title: "Someone", subtitle: "164 subscribers", thumb: "t", thumbLarge: "T",
  round: true, header, cursor: null, hasMore: false, orders: [], order: null, message: null,
  sections: [
    { type: "section", title: "Videos", key: "videos", layout: "carousel", play: null,
      items: [{ type: "track", kind: "video", videoId: "vid00000001", title: "A video", artists: [] }],
      more: { browseId: header.channelId, params: "VIDEOS", pageType: null } },
    { type: "section", title: "Playlists", key: "playlists", layout: "carousel", play: null, more: null,
      items: [{ type: "playlist", playlistId: "PL1", title: "A playlist" }] }
  ],
  ...extra
})

test("profile rows have round covers, as artists and channels do", () => {
  assert.equal(Model.roundCover(profile("UCp")), true)
  assert.equal(Model.roundCover({ type: "artist", channelId: "UCa" }), true)
  assert.equal(Model.roundCover({ type: "channel", channelId: "UCc" }), true)
  for (const item of [{ type: "album", browseId: "MPRE1" }, { type: "playlist", playlistId: "PL1" }, { type: "track", videoId: "v" }, null])
    assert.equal(Model.roundCover(item), false, JSON.stringify(item))
  // Their pages are round too.
  assert.equal(Model.pageRound(Model.pageRef(profile("UCp")), null), true)
  assert.equal(Model.pageRound(Model.pageRef(Model.myChannelItem()), null), true)
  assert.equal(Model.pageRound(Model.pageRef({ type: "libraryProfiles" }), null), false)
})

test("a USER_CHANNEL endpoint opens the profile; with params it stays a browse page", () => {
  const target = Model.routeEndpoint({ browseId: "UCuser", pageType: Model.USER_CHANNEL_PAGE, title: "Someone" })
  assert.deepEqual(target, { type: "profile", channelId: "UCuser", name: "Someone" })
  const ref = Model.pageRef(target)
  assert.deepEqual([ref.kind, ref.method, ref.params, ref.id], ["user", "user", { channelId: "UCuser" }, "UCuser"])
  // Recaps (a channel with params) and a profile's See all (no page type) are lists, read by browsePage.
  assert.equal(Model.routeEndpoint({ browseId: "UCrecap", params: "rp", pageType: Model.USER_CHANNEL_PAGE }).type, "browse")
  const more = Model.pageRows(userPage({ channelId: "UCuser" })).find(r => r.item && r.item.type === "more")
  const seeAll = Model.moreTarget(more.item)
  assert.deepEqual(seeAll, { type: "browse", browseId: "UCuser", params: "VIDEOS", title: "Videos" })
  assert.equal(Model.pageRef(seeAll).method, "browsePage")
  // An artist (no page type) still opens as an artist.
  assert.equal(Model.routeEndpoint({ browseId: "UCartist" }).type, "artist")
})

test("profile items from every list open the profile page; Your channel and Profiles have their own", () => {
  // Search, Home, Recent activity, Subscriptions and a playlist's author all give type profile.
  assert.equal(Model.pageKindOf(profile("UCp")), "user")
  assert.equal(Model.pageRef({ type: "profile" }), null, "no channel id: nothing to open")
  const mine = Model.pageRef(Model.myChannelItem())
  assert.deepEqual([mine.method, mine.params, mine.id], ["myChannel", {}, "myChannel"])
  assert.equal(Model.pageKindTitle(mine), "Your channel")
  const profiles = Model.pageRef({ type: "libraryProfiles", title: "Profiles" })
  assert.deepEqual([profiles.method, profiles.params, profiles.id], ["libraryProfiles", {}, "profiles"])
  assert.ok(Model.isProfileKind("user") && Model.isProfileKind("myChannel") && !Model.isProfileKind("channel"))
})

test("Subscribe label by state, and S on a profile subscribes", () => {
  assert.equal(Model.subscribeLabel(true), "Subscribed")
  assert.equal(Model.subscribeLabel(false), "Subscribe")
  assert.equal(Model.subscribeLabel(null), "Subscribe", "unknown reads as not subscribed")
  assert.equal(Model.subscribeActionTitle(true), "Unsubscribe")
  assert.equal(Model.subscribeActionTitle(false), "Subscribe")
  const p = profile("UCp")
  assert.equal(Model.canSubscribe(p), true)
  assert.equal(Model.saveAction(p), "subscribe")
  assert.equal(Model.saveTarget(p), "row")
  // A profile is its own subscribe channel and page.
  assert.deepEqual(Model.subscribeParams(p, true), { pageChannelId: "UCp", subscribe: true, channelId: "UCp" })
  assert.deepEqual(Model.subscribeParams(p, false), { pageChannelId: "UCp", subscribe: false, channelId: "UCp" })
  // Optimistic, then rolled back: the S4 state helpers take any channel.
  const edit = Model.optimistic({}, "UCp", true)
  assert.equal(Model.subscribedState("UCp", { subscribed: edit.map, fallback: false }), true, "the edit wins over the header")
  assert.equal(Model.subscribedState("UCp", { subscribed: Model.restoreStates(edit.map, edit.previous), fallback: false }), false)
  assert.equal(Model.subscribedState("UCp", { subscriptions: [p] }), true, "listed in the library's profiles")
})

test("lists of your subscriptions: every row is subscribed, under any edit made here", () => {
  for (const kind of ["librarySubscriptions", "libraryChannels", "libraryProfiles"])
    assert.equal(Model.listsSubscriptions(kind), true, kind)
  for (const kind of ["libraryArtists", "user", "myChannel", "channel", "browse", "", undefined])
    assert.equal(Model.listsSubscriptions(kind), false, String(kind))
  // An edit drops the list from the Store; the view's fallback (true) keeps
  // the other rows subscribed, and the edit made here wins on its own row.
  const edits = Model.optimistic({}, "UCp1", false).map
  assert.equal(Model.subscribedState("UCp2", { subscribed: edits, fallback: true, subscriptions: [] }), true)
  assert.equal(Model.subscribedState("UCp1", { subscribed: edits, fallback: true, subscriptions: [] }), false)
  assert.equal(Model.subscribedState("UCp2", { subscribed: edits, subscriptions: [] }), null, "without it: unknown (Subscribe)")
})

test("a profile page: Subscribe unless it is your own, and its item for the menu", () => {
  const page = userPage({ channelId: "UCp", subscribeId: "UCp", subscribed: true, subscribers: "164 subscribers", own: false })
  assert.equal(Model.canSubscribeProfile(page), true)
  assert.deepEqual(Model.profileOfPage(page), { type: "profile", channelId: "UCp", name: "Someone", thumb: "t", thumbLarge: "T" })
  const own = userPage({ channelId: "UCme", subscribeId: "UCme", subscribed: null, subscribers: "2 subscribers", own: true })
  assert.equal(Model.canSubscribeProfile(own), false)
  assert.equal(Model.profileHeader(own).own, true)
  assert.equal(Model.canSubscribeProfile(null), false)
  assert.equal(Model.profileOfPage({ type: "page", kind: "channel", header: { channelId: "UCc" } }), null, "a podcast channel is S8's")
  // The page menu (M) on a profile: Subscribe follows the header; Copy link.
  const ctx = { page: true, pageKind: "user", signedIn: true, canSubscribePage: true, subscribed: true }
  const menu = Model.menuActions(Model.profileOfPage(page), ctx)
  assert.deepEqual(menu.map(a => a.id), ["subscribe", "share"])
  assert.equal(menu[0].title, "Unsubscribe")
  assert.deepEqual(ids(Model.profileOfPage(own), Object.assign({}, ctx, { canSubscribePage: false })), ["share"])
  // Its rows: the sections, See all on Videos only (as served).
  assert.deepEqual(Model.pageRows(page).map(label), ["# Videos", "track:A video", "more:See all Videos", "# Playlists", "playlist:A playlist"])
})

test("a profile row's menu: Open, Subscribe (signed in), Copy link", () => {
  const p = profile("UCp")
  assert.equal(Model.menuKind(p), "profile")
  assert.equal(Model.menuKind({ type: "profile" }), "")
  assert.deepEqual(ids(p, { pageKind: "search", signedIn: true, subscribed: false }), ["open", "subscribe", "share"])
  assert.deepEqual(ids(p, { pageKind: "search", signedIn: false }), ["open", "share"])
  assert.equal(Model.menuActions(p, { signedIn: true, subscribed: true }).find(a => a.id === "subscribe").title, "Unsubscribe")
  assert.equal(Model.shareUrl(p), "https://music.youtube.com/channel/UCp")
  const opened = []
  Model.menuAction("open").run({ item: p, ctx: {}, panel: { push: item => opened.push(item) } })
  assert.deepEqual(opened, [p])
  // Not a queue or radio target.
  assert.equal(Model.canQueue(p), false)
  assert.equal(Model.radioParams(p), null)
})

test("profile rows say who they are: handle and served subtitle, else Profile", () => {
  assert.equal(Model.itemSubtitle(profile("UCp", { handle: "@someone", subtitle: "164 subscribers" })), "@someone • 164 subscribers")
  assert.equal(Model.itemSubtitle(profile("UCp", { subtitle: "@sample_profile" })), "@sample_profile")
  assert.equal(Model.itemSubtitle(profile("UCp", { handle: "@same", subtitle: "@same" })), "@same")
  assert.equal(Model.itemSubtitle(profile("UCp")), "Profile")
  assert.equal(Model.itemSubtitle({ type: "artist", subtitle: "2M subscribers" }), "2M subscribers", "artists as before")
})

test("Library: Your channel heads the account menu, hidden once the worker says there is none", () => {
  const library = { page: true, pageKind: "library", signedIn: true }
  assert.deepEqual(ids(null, library), ["myChannel", "signOut"])
  assert.deepEqual(ids(null, Object.assign({}, library, { noChannel: true })), ["signOut"])
  assert.deepEqual(ids(null, { page: true, pageKind: "library", signedIn: false }), ["signIn"])
  assert.ok(!ids(null, { page: true, pageKind: "home", signedIn: true }).includes("myChannel"))
  const pushed = []
  Model.menuAction("myChannel").run({ item: null, ctx: library, panel: { push: item => pushed.push(item) } })
  assert.deepEqual(pushed, [{ type: "myChannel", title: "Your channel", thumb: null }])
  assert.equal(Model.noChannel({ ok: false, error: { code: "NOT_FOUND", message: "x", noChannel: true } }), true)
  assert.equal(Model.noChannel({ ok: false, error: { code: "NOT_FOUND", message: "x" } }), false)
  assert.equal(Model.noChannel({ ok: true, result: {} }), false)
})

test("Library: the Artists chip lists Profiles after Subscriptions; other chips are unchanged", () => {
  const artists = Model.libraryList({ sections: [{ items: [{ type: "artist", channelId: "UC1", name: "A" }] }] })
  const rows = Model.libraryRows("artists", artists, [])
  assert.deepEqual(rows.map(label).slice(0, 2), ["librarySubscriptions:Subscriptions", "libraryProfiles:Profiles"])
  assert.equal(rows[1].item.subtitle, "Profiles you subscribe to")
  assert.equal(Model.pageRef(rows[1].item).method, "libraryProfiles")
  rows[1].item.title = "changed"
  assert.equal(Model.libraryRows("artists", null, [])[1].item.title, "Profiles", "each list gets its own entry")
  assert.ok(!Model.libraryRows("albums", null, []).some(r => r.item && r.item.type === "libraryProfiles"))
  assert.ok(!Model.libraryRows("podcasts", null, []).some(r => r.item && r.item.type === "libraryProfiles"))
})

test("the library's profiles page: its profiles, then the note YouTube serves, then Load more", () => {
  const page = { type: "page", kind: "libraryProfiles", id: "FEmusic_library_user_profile_channels_list", title: "",
                 sections: [{ type: "section", title: null, items: [profile("UCa", { subtitle: "@a" })], more: null }],
                 cursor: "c.T", hasMore: true, message: "Only profiles with music activity are shown." }
  assert.deepEqual(Model.pageRows(page).map(label),
    ["profile:UCa", "message:Only profiles with music activity are shown.", "loadMore:Load more"])
  const empty = { type: "page", kind: "libraryProfiles", sections: [], cursor: null, message: "No profiles yet" }
  assert.deepEqual(Model.pageRows(empty).map(label), ["message:No profiles yet"])
})
