const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../YtmModel.js")

// Playlist editing (S13): moves, invite links, the picker's duplicate retry,
// net votes, the editor's changes and rows, suggestions, menu entries and keys.

const entry = (n, extra = {}) => ({ type: "track", videoId: ("vid" + n).padEnd(11, "x"), title: "Song " + n,
  setVideoId: "SET" + n, ...extra })
const page = (n, extra = {}) => ({ type: "collection", kind: "playlist", id: "PLmine", playlistId: "PLmine",
  owned: true, editable: true, truncated: false, tracks: Array.from({ length: n }, (_, i) => entry(i)), ...extra })
const ids = (p) => p.tracks.map(t => t.setVideoId)
const TOKEN = "kM9wXdRj2p8v_qL3sHBkTz"

test("moveTrack: down goes before the song after next, up before the one above", () => {
  const down = Model.moveTrack(page(4), 1, 1)
  assert.deepEqual(ids(down.page), ["SET0", "SET2", "SET1", "SET3"])
  assert.equal(down.index, 2)
  assert.deepEqual(down.params, { playlistId: "PLmine", setVideoId: "SET1", beforeSetVideoId: "SET3" })
  const up = Model.moveTrack(page(4), 2, -1)
  assert.deepEqual(ids(up.page), ["SET0", "SET2", "SET1", "SET3"])
  assert.deepEqual(up.params, { playlistId: "PLmine", setVideoId: "SET2", beforeSetVideoId: "SET1" })
  const toEnd = Model.moveTrack(page(4), 2, 1)
  assert.deepEqual(ids(toEnd.page), ["SET0", "SET1", "SET3", "SET2"])
  assert.equal(toEnd.params.beforeSetVideoId, null, "no successor: to the end")
})

test("moveTrack: copies the page, and refuses what it cannot move", () => {
  const original = page(3)
  const before = ids(original)
  Model.moveTrack(original, 0, 1)
  assert.deepEqual(ids(original), before, "the page shown is not changed in place")
  assert.equal(Model.moveTrack(original, 0, -1).problem, "It is the first song already")
  assert.equal(Model.moveTrack(original, 2, 1).problem, "It is the last song already")
  assert.equal(Model.moveTrack(original, 9, 1).problem, "")
  assert.equal(Model.moveTrack(null, 0, 1).problem, "")
  const truncated = page(3, { truncated: true })
  assert.match(Model.moveTrack(truncated, 1, 1).problem, /Load all/, "the successor is not loaded")
  assert.match(Model.moveTrack(truncated, 2, 1).problem, /Load all/)
  assert.ok(Model.moveTrack(truncated, 0, 1).page, "one with a loaded successor moves")
  const bare = page(3)
  bare.tracks[1] = { ...bare.tracks[1], setVideoId: null }
  assert.match(Model.moveTrack(bare, 1, 1).problem, /cannot be moved/)
  assert.match(Model.moveTrack(bare, 2, -1).problem, /cannot be moved/, "nor before an entry without one")
  assert.equal(Model.moveTrack(page(2, { playlistId: "VLPLmine" }), 0, 1).params.playlistId, "PLmine")
})

test("moveTrack keeps the cursor on the moved song (rows are keyed by setVideoId)", () => {
  const before = Model.collectionRows(page(4))
  const moved = Model.moveTrack(page(4), 1, 1)
  const after = Model.collectionRows(moved.page)
  assert.equal(Model.cursorAfterRows(before, after, 1), 2)
})

test("orderBlocksMoves: only a known order other than Manual", () => {
  assert.equal(Model.orderBlocksMoves(null), "")
  assert.equal(Model.orderBlocksMoves({ sortOrder: "MANUAL" }), "")
  assert.equal(Model.orderBlocksMoves({ sortOrder: null, sortTitle: null }), "")
  assert.equal(Model.orderBlocksMoves({ sortOrder: "NEWEST_FIRST" }), "Newest first")
  assert.equal(Model.orderBlocksMoves({ sortOrder: "NEWEST_LAST" }), "Oldest first")
  assert.equal(Model.orderBlocksMoves({ sortOrder: null, sortTitle: "Artist" }), "Artist", "a view sort")
  assert.equal(Model.orderBlocksMoves({ sortOrder: "NEWEST_FIRST", sortTitle: "Le plus récent" }), "Le plus récent",
               "the order is named as YouTube serves it")
  assert.equal(Model.orderBlocksMoves({ sortOrder: "TOP_VOTED", sortTitle: null,
                                        sortOptions: [{ value: "TOP_VOTED", title: "Most votes" }] }), "Most votes")
})

test("parseInviteUrl takes music.youtube.com and youtube.com invite links only", () => {
  const expected = { playlistId: "PLx", token: TOKEN }
  assert.deepEqual(Model.parseInviteUrl(`https://music.youtube.com/playlist?list=PLx&jct=${TOKEN}`), expected)
  assert.deepEqual(Model.parseInviteUrl(` https://music.youtube.com/playlist?jct=${TOKEN}&list=VLPLx&si=a#x `), expected)
  assert.deepEqual(Model.parseInviteUrl(`https://www.youtube.com/playlist/?list=PLx&jct=${TOKEN}`), expected)
  assert.deepEqual(Model.parseInviteUrl(`http://youtube.com/playlist?list=PLx&jct=${TOKEN}`), expected)
  for (const bad of ["", null, "PLx", "https://music.youtube.com/playlist?list=PLx",
    `https://music.youtube.com/watch?v=a&list=PLx&jct=${TOKEN}`, `https://evil.example/playlist?list=PLx&jct=${TOKEN}`,
    `https://youtube.com.evil.example/playlist?list=PLx&jct=${TOKEN}`, `https://music.youtube.com/playlist?list=P%20x&jct=${TOKEN}`,
    `https://music.youtube.com/playlist?list=PLx&jct=a%3Cb`, `ftp://music.youtube.com/playlist?list=PLx&jct=${TOKEN}`])
    assert.equal(Model.parseInviteUrl(bad), null, String(bad))
})

test("picker: DUPLICATE arms that playlist; Enter again sends duplicates; anything else disarms", () => {
  let state = { duplicateOf: "" }
  assert.deepEqual(Model.pickerAddOptions(state, "PLa"), { duplicates: false })
  const duplicate = { ok: false, error: { code: "DUPLICATE", message: "Already in that playlist", videoIds: ["v1"] } }
  state = Model.pickerAfterReply(state, "PLa", duplicate, 1)
  assert.deepEqual(state, { duplicateOf: "PLa", status: "Already there: Enter again to add anyway", done: false })
  assert.deepEqual(Model.pickerAddOptions(state, "PLa"), { duplicates: true })
  assert.deepEqual(Model.pickerAddOptions(state, "PLb"), { duplicates: false }, "another playlist asks first")
  const done = Model.pickerAfterReply(state, "PLa", { ok: true, result: { added: [] } }, 1)
  assert.deepEqual(done, { duplicateOf: "", status: "", done: true })
  const failed = Model.pickerAfterReply(state, "PLa", { ok: false, error: { code: "UPSTREAM", message: "No" } }, 1)
  assert.deepEqual(failed, { duplicateOf: "", status: "No", done: false })
  const unknown = Model.pickerAfterReply(state, "PLa", { ok: false, error: { code: "DUPLICATE", message: "x", unknown: true } }, 1)
  assert.equal(unknown.duplicateOf, "", "an unknown outcome never arms")
  const some = Model.pickerAfterReply({}, "PLa", { ok: false, error: { code: "DUPLICATE", videoIds: ["a", "b"] } }, 23)
  assert.equal(some.status, "2 of 23 songs are already there: Enter again to add anyway")
  const all = Model.pickerAfterReply({}, "PLa", { ok: false, error: { code: "DUPLICATE", videoIds: ["a", "b"] } }, 2)
  assert.equal(all.status, "All 2 songs are already there: Enter again to add anyway")
  // A whole album or playlist (no songs of its own; YouTube names none of them).
  const whole = Model.pickerAfterReply({}, "PLa", { ok: false, error: { code: "DUPLICATE", videoIds: [] } }, 0)
  assert.deepEqual(whole, { duplicateOf: "PLa", status: "Some of its songs are already there: Enter again to add them anyway",
                            done: false })
})

test("voteLabel: net votes on a playlist with voting, nothing without", () => {
  assert.equal(Model.voteLabel(entry(1, { voteStatus: { votes: 3, status: "VOTE_STATUS_UPVOTED" } })), "+3")
  assert.equal(Model.voteLabel(entry(1, { voteStatus: { votes: -2, status: null } })), "-2")
  assert.equal(Model.voteLabel(entry(1, { voteStatus: { votes: 0, status: "VOTE_STATUS_UNSPECIFIED" } })), "",
               "0 is not shown (every song of a voting playlist starts there)")
  assert.equal(Model.voteLabel(entry(1, { voteStatus: { votes: null } })), "")
  assert.equal(Model.voteLabel(entry(1, { voteStatus: null })), "")
  assert.equal(Model.voteLabel(entry(1)), "")
  assert.equal(Model.voteLabel(null), "")
})

test("editor: what changed, and what YouTube cannot take", () => {
  const initial = Model.editorInitial({ title: "Mine", description: "Old", privacy: "PRIVATE" },
    { title: "Mine", description: "Old", privacy: "PRIVATE", sortOrder: "MANUAL", voteOption: null, addToTop: null })
  assert.equal(initial.voteOption, null, "not reported stays null")
  assert.deepEqual(Model.editorChanges(initial, { ...initial }), { params: {}, problem: "", count: 0 })
  const all = Model.editorChanges(initial, { ...initial, title: " New ", description: "New text", privacy: "UNLISTED",
    sortOrder: "NEWEST_FIRST", addToTop: true, voteOption: "OFF" })
  assert.deepEqual(all.params, { title: "New", description: "New text", privacy: "UNLISTED", sortOrder: "NEWEST_FIRST",
    addToTop: true, voteOption: "OFF" })
  assert.equal(all.problem, "")
  assert.equal(all.count, 6)
  assert.equal(Model.editorChanges(initial, { ...initial, description: "  " }).problem, Model.DESCRIPTION_NOTE)
  assert.deepEqual(Model.editorChanges(initial, { ...initial, description: "  " }).params, {})
  assert.equal(Model.editorChanges(initial, { ...initial, title: "" }).problem, "A playlist needs a title")
  assert.equal(Model.editorChanges(initial, { ...initial, title: "a <b>" }).problem, "A title cannot contain < or >")
  assert.match(Model.editorChanges(initial, { ...initial, title: "x".repeat(151) }).problem, /at most 150/)
  assert.equal(Model.playlistTitleProblem("Fine"), "")
})

test("editor: rows say what YouTube did not report, and h / l cycle the options", () => {
  const initial = Model.editorInitial({ title: "Mine", privacy: "PRIVATE" }, null)
  const rows = Model.editorRows(initial, { ...initial }, {})
  const titles = rows.filter(r => r.item).map(r => r.item.title)
  assert.deepEqual(titles.slice(0, 5), ["No changes to save", "Privacy: Private", "Order: not reported by YouTube",
    "New songs go: not reported by YouTube", "Voting: not reported by YouTube"])
  assert.ok(rows.some(r => r.header && r.title === "Advanced"))
  const changed = Model.editorRows(initial, { ...initial, sortOrder: "MANUAL" }, {})
  assert.ok(changed.some(r => r.item && r.item.title === "Order: Manual (changed)"))
  assert.equal(changed[0].item.title, "Save 1 change")
  const viewSort = Model.editorInitial({}, { sortOrder: null, sortTitle: "Artist" })
  assert.ok(Model.editorRows(viewSort, { ...viewSort }, {}).some(r => r.item && r.item.title === "Order: Artist"))
  assert.equal(Model.nextOption(Model.SORT_OPTIONS, null, 1), "MANUAL")
  assert.equal(Model.nextOption(Model.SORT_OPTIONS, null, -1), "TOP_VOTED")
  assert.equal(Model.nextOption(Model.SORT_OPTIONS, "TOP_VOTED", 1), "MANUAL")
  assert.equal(Model.nextOption(Model.ADD_TO_TOP_OPTIONS, false, 1), true)
  assert.equal(Model.nextOption(Model.PRIVACY_OPTIONS, "PRIVATE", -1), "PUBLIC")
  assert.equal(Model.optionTitle(Model.VOTE_OPTIONS, "COLLABORATORS_ONLY"), "Collaborators only")
  for (const [id, key] of Object.entries(Model.EDITOR_OPTIONS)) assert.ok(Model.editOptions(key).length > 1, id)
  const withCollaborators = Model.editorInitial({}, { collaborators: "by You and 1 other" })
  assert.ok(Model.editorRows(withCollaborators, withCollaborators, {}).some(r => r.item && r.item.type === "message"))
})

test("editor: Order cycles the orders YouTube serves, under their served titles", () => {
  const served = [{ value: "TOP_VOTED", title: "Top voted" }, { value: "MANUAL", title: "Manual ordering" },
                  { value: "NEWEST_FIRST", title: "Newest first" }, { value: "NEWEST_LAST", title: "Oldest first" }]
  const initial = Model.editorInitial({ title: "Mine" }, { sortOrder: "NEWEST_FIRST", sortTitle: "Newest first",
                                                           sortOptions: served })
  assert.deepEqual(initial.sortOptions, served)
  const options = Model.editOptions("sortOrder", initial)
  assert.deepEqual(options.map(o => o.value), ["TOP_VOTED", "MANUAL", "NEWEST_FIRST", "NEWEST_LAST"], "in served order")
  assert.equal(Model.nextOption(options, "NEWEST_FIRST", -1), "MANUAL")
  const rows = Model.editorRows(initial, { ...initial, sortOrder: "MANUAL" }, {})
  assert.ok(rows.some(r => r.item && r.item.title === "Order: Manual ordering (changed)"), "the served title")
  assert.deepEqual(Model.editOptions("sortOrder", Model.editorInitial({}, null)), Model.SORT_OPTIONS, "none served")
  assert.deepEqual(Model.editOptions("sortOrder", { sortOptions: [] }), Model.SORT_OPTIONS)
})

test("editor: Voting cycles only the options YouTube offers", () => {
  const offered = Model.editorInitial({}, { voteOption: "EVERYONE_CAN_VOTE", voteOptions: ["EVERYONE_CAN_VOTE", "OFF"] })
  assert.deepEqual(Model.editOptions("voteOption", offered).map(o => o.value), ["EVERYONE_CAN_VOTE", "OFF"])
  assert.equal(Model.nextOption(Model.editOptions("voteOption", offered), "EVERYONE_CAN_VOTE", 1), "OFF")
  assert.equal(Model.nextOption(Model.editOptions("voteOption", offered), "OFF", 1), "EVERYONE_CAN_VOTE",
               "never Collaborators only while collaboration is off")
  const unknown = Model.editorInitial({}, null)
  assert.deepEqual(Model.editOptions("voteOption", unknown), Model.VOTE_OPTIONS, "not reported: every option")
  const open = Model.withCollaboration(offered, true)
  assert.deepEqual(Model.editOptions("voteOption", open).map(o => o.value),
                   ["EVERYONE_CAN_VOTE", "COLLABORATORS_ONLY", "OFF"], "offered once collaboration is on")
  const closed = Model.withCollaboration(open, false)
  assert.deepEqual(closed.voteOptions, ["EVERYONE_CAN_VOTE", "OFF"])
  assert.deepEqual(offered.voteOptions, ["EVERYONE_CAN_VOTE", "OFF"], "a copy: initial is not changed in place")
  assert.equal(Model.withCollaboration(unknown, true).voteOptions, null, "unknown stays unknown")
})

test("editor: Close collaboration only while collaboration is on", () => {
  const has = (initial) => Model.editorRows(initial, { ...initial }, {})
    .some(r => r.item && r.item.id === Model.EDITOR_IDS.close)
  const off = Model.editorInitial({ title: "Mine" }, { collaboration: null })
  assert.equal(has(off), false, "not reported: YouTube refuses a close when it is off")
  const on = Model.editorInitial({ title: "Mine" }, { collaboration: true, collaborators: "by You and 1 other" })
  assert.equal(has(on), true)
  assert.equal(has(Model.withCollaboration(off, true)), true, "after Get invite link")
  assert.equal(has(Model.withCollaboration(on, false)), false, "after Close")
  assert.ok(Model.editorRows(off, { ...off }, {}).some(r => r.item && r.item.id === Model.EDITOR_IDS.invite))
})

test("editor: the invite link needs a saved privacy other than Private", () => {
  assert.match(Model.inviteProblem({ privacy: "PRIVATE" }, { privacy: "PRIVATE" }), /private playlist/)
  assert.equal(Model.inviteProblem({ privacy: "PRIVATE" }, { privacy: "UNLISTED" }), "Save the new privacy first")
  assert.equal(Model.inviteProblem({ privacy: "UNLISTED" }, { privacy: "UNLISTED" }), "")
  assert.equal(Model.inviteProblem({ privacy: null }, { privacy: null }), "", "unknown: YouTube decides")
})

test("suggestions follow the songs and Load all, before the shelves", () => {
  const shelves = [{ type: "section", title: "Related playlists", items: [{ type: "playlist", playlistId: "PLr", title: "R" }] }]
  const rows = Model.collectionRows(page(2, { sections: shelves, truncated: true, trackCount: 500 }))
  const state = { items: [entry(7, { setVideoId: null })], loading: false, error: "", loaded: true }
  const out = Model.withSuggestionRows(rows, state)
  const kinds = out.map(r => r.header ? "header:" + r.title : r.suggestion ? "suggestion" : r.item.type + (r.item.id ? ":" + r.item.id : ""))
  assert.deepEqual(kinds, ["track", "track", "loadMore:loadAll", "header:Suggestions", "suggestion",
    "action:" + Model.SUGGESTIONS_REFRESH_ID, "header:Related playlists", "playlist"])
  assert.equal(out.find(r => r.suggestion).trackIndex, undefined, "Enter on a suggestion does not play the list")
  const loading = Model.playlistSuggestionRows({ items: [], loading: true })
  assert.deepEqual(loading.map(r => r.item ? r.item.title : r.title),
    ["Suggestions", "Loading suggestions…", "Refreshing suggestions…"])
  assert.equal(Model.playlistSuggestionRows({ items: [], error: "No" })[1].item.title, "Couldn't load suggestions: No")
  assert.equal(Model.playlistSuggestionRows({ items: [], loaded: true })[1].item.title, "YouTube has no suggestions for this playlist")
  assert.equal(Model.withSuggestionRows([], state)[0].title, "Suggestions", "an empty playlist")
  assert.deepEqual(Model.withoutSuggestion(state.items, state.items[0]), [])
  assert.equal(Model.itemKey(state.items[0]), "track:" + state.items[0].videoId, "no set id: keyed by video")
})

test("sourcePlaylistOf: albums by their audio playlist, playlists, never mixes or Liked Music", () => {
  assert.equal(Model.sourcePlaylistOf({ type: "album", browseId: "MPREb_x", playlistId: "OLAK5uy_x" }), "OLAK5uy_x")
  assert.equal(Model.sourcePlaylistOf({ type: "album", browseId: "MPREb_x", playlistId: null }), "")
  assert.equal(Model.sourcePlaylistOf({ type: "playlist", playlistId: "VLPLx" }), "PLx")
  assert.equal(Model.sourcePlaylistOf({ type: "playlist", playlistId: "RDCLAK5uy_x" }), "RDCLAK5uy_x", "curated")
  for (const none of [{ type: "playlist", playlistId: "RDTMAK5uy_mix" }, { type: "playlist", playlistId: "LM" },
    { type: "playlist", playlistId: "SE" }, { type: "liked" }, entry(1), null])
    assert.equal(Model.sourcePlaylistOf(none), "", JSON.stringify(none))
})

test("menu: Save to playlist and Copy to new playlist on albums and playlists; Edit on your own page", () => {
  const ids = (item, ctx) => Model.menuActions(item, ctx).map(a => a.id)
  const album = { type: "album", browseId: "MPREb_x", playlistId: "OLAK5uy_x", title: "OK" }
  const signedIn = { signedIn: true, pageKind: "search" }
  const albumIds = ids(album, signedIn)
  assert.ok(albumIds.includes("saveToPlaylist") && albumIds.includes("copyToNewPlaylist"))
  assert.ok(!ids(album, { pageKind: "search" }).includes("saveToPlaylist"), "signed out")
  assert.ok(!ids(entry(1), signedIn).includes("saveToPlaylist"), "a song has Add to playlist")
  assert.equal(Model.menuActions(album, signedIn).find(a => a.id === "saveToPlaylist").hint, "p")
  const mine = { type: "playlist", playlistId: "PLmine", title: "Mine" }
  const pageCtx = { signedIn: true, page: true, owned: true, pageKind: "playlist", hasTracks: true }
  const pageIds = ids(mine, pageCtx)
  assert.ok(pageIds.includes("editPlaylist") && pageIds.includes("deletePlaylist"))
  assert.equal(Model.menuActions(mine, pageCtx).find(a => a.id === "editPlaylist").hint, "e")
  assert.ok(!ids(mine, { ...pageCtx, owned: false }).includes("editPlaylist"))
  const suggestion = ids(entry(1, { setVideoId: null }), { signedIn: true, pageKind: "playlist", owned: true, suggestion: true })
  assert.ok(suggestion.includes("addSuggestion") && !suggestion.includes("remove"))
  const now = ids(null, { page: true, pageKind: "now", queueLength: 3, signedIn: true })
  assert.ok(now.includes("saveQueue"))
  assert.ok(!ids(null, { page: true, pageKind: "now", queueLength: 0, signedIn: true }).includes("saveQueue"))
  assert.ok(!ids(null, { page: true, pageKind: "now", queueLength: 3 }).includes("saveQueue"), "signed out")
})

test("keys: e edits, i adds a suggestion, J / K and p say what they do now", () => {
  assert.equal(Model.keyId("e"), "editPlaylist")
  assert.equal(Model.keyId("i"), "addSuggestion")
  assert.equal(Model.keyId("J"), "queueMove")
  assert.match(Model.KEYS.find(k => k.id === "queueMove").title, /your own playlist/)
  assert.match(Model.KEYS.find(k => k.id === "addToPlaylist").title, /whole album or playlist/)
  const order = Model.KEYS.map(k => k.id)
  assert.equal(order[order.indexOf("queueMove") + 1], "addSuggestion")
  assert.equal(order[order.indexOf("find") + 1], "editPlaylist")
})
