// Pure helpers for damir.ytmusic, shared by the QML side and the Node tests
// (`node --test tests/`). Plain functions only — no `.pragma library` — so the
// same file loads in both.

var PLUGIN_ID = "damir.ytmusic"

// www.youtube.com is the only URL form mpv-mpris derives cover art from (S4).
var WATCH_URL = "https://www.youtube.com/watch?v="

function trackUrl(videoId) {
  return WATCH_URL + videoId
}

function videoIdFromUrl(url) {
  var text = String(url || "")
  var match = text.match(/[?&]v=([A-Za-z0-9_-]{11})/) || text.match(/youtu\.be\/([A-Za-z0-9_-]{11})/)
  return match ? match[1] : ""
}

function mediaTitle(track) {
  if (!track) return ""
  var title = String(track.title || "")
  var artists = String(track.artistText || "")
  return artists ? title + " — " + artists : title
}

function utf8Length(text) {
  var length = 0
  for (var i = 0; i < text.length; i++) {
    var code = text.charCodeAt(i)
    if (code < 0x80) length += 1
    else if (code < 0x800) length += 2
    else if (code >= 0xd800 && code <= 0xdbff) { length += 4; i++ }
    else length += 3
  }
  return length
}

// Per-file loadfile options. mpv's %n% form carries the value's byte length,
// so titles with commas, quotes or '=' need no escaping.
function loadfileOptions(track) {
  var title = mediaTitle(track)
  return title ? "force-media-title=%" + utf8Length(title) + "%" + title : ""
}

function loadfile(track, flag) {
  return ["loadfile", trackUrl(track.videoId), flag, -1, loadfileOptions(track)]
}

function playable(track) {
  return !!(track && track.videoId && track.available !== false)
}

// ---- queue commands ---------------------------------------------------------
// `state` is { count, pos } from mpv's playlist-count / playlist-pos.

function playNowCommands(track, state) {
  var count = state && state.count > 0 ? state.count : 0
  var pos = state && state.pos >= 0 ? state.pos : -1
  if (count === 0) return [loadfile(track, "replace"), ["set_property", "pause", false]]
  if (pos < 0) return [loadfile(track, "append"), ["playlist-play-index", count], ["set_property", "pause", false]]
  return [loadfile(track, "insert-next"), ["playlist-play-index", pos + 1], ["set_property", "pause", false]]
}

function playNextCommands(track) {
  return [loadfile(track, "insert-next-play")]
}

function addToQueueCommands(track) {
  return [loadfile(track, "append-play")]
}

// Replace the queue with a collection. `stop` clears mpv's playlist; loading
// everything with `append` and then jumping to the start index avoids
// resolving the first track for nothing. `start` indexes `tracks`
// (unplayable ones are skipped). With `shuffle` the order is random; with
// `keepStart` too, the start track plays first and the rest is shuffled
// (shuffle mode on and a song clicked). `originalIndex[k]` is the position
// the k-th loaded track had in the collection, so the service can restore
// that order when shuffle is turned off.
function playCollectionCommands(tracks, start, shuffle, seed, keepStart) {
  var source = tracks || []
  var playableAt = []
  for (var i = 0; i < source.length; i++) if (playable(source[i])) playableAt.push(i)
  if (playableAt.length === 0) return { commands: [], tracks: [], originalIndex: [], index: -1 }
  var wanted = Number(start) || 0
  var index = playableAt.length - 1
  for (var p = 0; p < playableAt.length; p++) {
    if (playableAt[p] >= wanted) {
      index = p
      break
    }
  }
  var positions = playableAt.map(function(_, k) { return k })
  if (shuffle) {
    if (keepStart) positions = [index].concat(shuffled(positions.filter(function(k) { return k !== index }), seed))
    else positions = shuffled(positions, seed)
    index = 0
  }
  var list = positions.map(function(k) { return source[playableAt[k]] })
  var commands = [["stop"]]
  for (var j = 0; j < list.length; j++) commands.push(loadfile(list[j], "append"))
  commands.push(["playlist-play-index", index], ["set_property", "pause", false])
  return { commands: commands, tracks: list, originalIndex: positions, index: index }
}

// mpv entry ids (as loaded) listed in the collection's own order.
function originalOrder(entryIds, originalIndex) {
  var pairs = []
  for (var i = 0; i < entryIds.length && i < originalIndex.length; i++) pairs.push([originalIndex[i], entryIds[i]])
  pairs.sort(function(a, b) { return a[0] - b[0] })
  return pairs.map(function(pair) { return pair[1] })
}

// ---- shuffle and repeat ---------------------------------------------------------

var REPEAT_MODES = ["off", "all", "one"]

function normalizeRepeat(mode) {
  return REPEAT_MODES.indexOf(mode) >= 0 ? mode : "off"
}

function nextRepeat(mode) {
  return REPEAT_MODES[(REPEAT_MODES.indexOf(normalizeRepeat(mode)) + 1) % REPEAT_MODES.length]
}

// mpv reports loop-file / loop-playlist as false, "inf", "force" or a count.
function loopOn(value) {
  return value === true || value === "inf" || value === "force" || (typeof value === "number" && value > 0)
}

function repeatMode(loopFile, loopPlaylist) {
  if (loopOn(loopFile)) return "one"
  if (loopOn(loopPlaylist)) return "all"
  return "off"
}

function repeatCommands(mode) {
  var m = normalizeRepeat(mode)
  return [["set_property", "loop-file", m === "one" ? "inf" : "no"],
          ["set_property", "loop-playlist", m === "all" ? "inf" : "no"]]
}

// Shuffle on: the entries after the current one (`pos`) in random order.
function shuffleOrder(ids, pos, seed) {
  var head = ids.slice(0, pos + 1)
  return head.concat(shuffled(ids.slice(pos + 1), seed))
}

// Shuffle off: the shuffled entries go back to their original order, in the
// slots they occupy now. Entries added since (play next, add to queue) keep
// their places.
function unshuffleOrder(ids, original) {
  var rank = {}
  for (var i = 0; i < (original || []).length; i++) rank[original[i]] = i
  var members = ids.filter(function(id) { return rank[id] !== undefined })
  members.sort(function(a, b) { return rank[a] - rank[b] })
  var k = 0
  return ids.map(function(id) { return rank[id] !== undefined ? members[k++] : id })
}

// playlist-move commands that turn the order `ids` into `target` (the same
// ids rearranged). Each step moves an entry up into place; mpv puts an entry
// moved up exactly at the target index (spike S7).
function reorderCommands(ids, target) {
  var list = ids.slice()
  var commands = []
  for (var i = 0; i < target.length; i++) {
    var j = list.indexOf(target[i])
    if (j <= i) continue
    commands.push(["playlist-move", j, i])
    list.splice(j, 1)
    list.splice(i, 0, target[i])
  }
  return commands
}

// ---- autoplay ---------------------------------------------------------------------

// Songs from YouTube Music's up-next list worth appending: playable, not in
// the queue yet (played ones included), songs before videos, at most `max`.
function autoplayPicks(queue, candidates, max) {
  var seen = {}
  for (var i = 0; i < (queue || []).length; i++) if (queue[i].videoId) seen[queue[i].videoId] = true
  var songs = []
  var videos = []
  for (var j = 0; j < (candidates || []).length; j++) {
    var track = candidates[j]
    if (!playable(track) || seen[track.videoId]) continue
    seen[track.videoId] = true
    if (track.kind === "video") videos.push(track)
    else songs.push(track)
  }
  return songs.concat(videos).slice(0, max || 10)
}

// ---- preferences (player.json, version 2) ------------------------------------------
// Every key a slice will need, with its default, so a newer file read by an
// older plugin (or the other way round) keeps what it doesn't know about.
// Version 1 held autoplay, shuffle and repeat only; it parses the same way.

var PREFS_VERSION = 2
var CHIP_PATTERN = /^[a-z][A-Za-z]{0,31}$/

function _oneOf(options, fallback) {
  return function(value) { return options.indexOf(value) >= 0 ? value : fallback }
}

function _bool(fallback) {
  return function(value) { return typeof value === "boolean" ? value : fallback }
}

function _speed(value) {
  var n = Number(value)
  return typeof value === "number" && isFinite(n) && n >= 0.5 && n <= 2 ? n : 1
}

// key -> rule(value): the value when valid, else the default (rule(undefined)
// is the default).
var PREF_RULES = {
  autoplay: _bool(true),
  shuffle: _bool(false),
  repeat: normalizeRepeat,
  // S2: the Library chip, and the sort order chosen per chip.
  libraryChip: function(value) { return typeof value === "string" && CHIP_PATTERN.test(value) ? value : "playlists" },
  libraryOrders: function(value) {
    var out = {}
    if (!value || typeof value !== "object" || Array.isArray(value)) return out
    for (var chip in value) {
      var order = value[chip]
      if (!CHIP_PATTERN.test(chip)) continue
      if ((typeof order === "string" && order !== "" && order.length <= 64)
          || (typeof order === "number" && order >= 0 && Math.floor(order) === order)) out[chip] = order
    }
    return out
  },
  // S6: chart country, ZZ is global.
  chartsCountry: function(value) { return typeof value === "string" && /^[A-Z]{2}$/.test(value) ? value : "ZZ" },
  // S16: content location ("" is the account's own) and audio quality.
  location: function(value) { return typeof value === "string" && /^[A-Z]{2}$/.test(value) ? value : "" },
  quality: _oneOf(["low", "normal", "high"], "high"),
  // S15: report plays to YouTube Music history (off until turned on).
  recordHistory: _bool(false),
  // S17: playback speed per kind of entry, and loudness levelling.
  speed: function(value) {
    var v = value && typeof value === "object" ? value : {}
    return { music: _speed(v.music), episode: _speed(v.episode) }
  },
  normalize: _oneOf(["off", "youtube", "dynamic"], "off"),
  // S11: the pane under Now Playing.
  nowPane: _oneOf(["queue", "lyrics", "related"], "queue")
}

function isPrefKey(key) {
  return Object.prototype.hasOwnProperty.call(PREF_RULES, key)
}

// The value as it will be kept: valid, or the key's default.
function normalizePref(key, value) {
  return isPrefKey(key) ? PREF_RULES[key](value) : undefined
}

function parsePrefs(text) {
  var saved = null
  try {
    saved = JSON.parse(text)
  } catch (e) {
    saved = null
  }
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) saved = {}
  var prefs = {}
  for (var key in PREF_RULES) prefs[key] = PREF_RULES[key](saved[key])
  return prefs
}

// A copy of `prefs` with one key changed (copy on write: bindings on the
// prefs object only see a new value).
function withPref(prefs, key, value) {
  var next = {}
  for (var k in PREF_RULES) next[k] = prefs && prefs[k] !== undefined ? prefs[k] : PREF_RULES[k](undefined)
  if (isPrefKey(key)) next[key] = PREF_RULES[key](value)
  return next
}

function serializePrefs(prefs) {
  var out = { version: PREFS_VERSION }
  var clean = parsePrefs(JSON.stringify(prefs || {}))
  for (var key in clean) out[key] = clean[key]
  return JSON.stringify(out) + "\n"
}

// mpv inserts before the target index, so moving down needs target + 1 (S7).
function moveArgs(from, to) {
  return ["playlist-move", from, to > from ? to + 1 : to]
}

function previousCommands(timePos) {
  return Number(timePos) > 5 ? [["seek", 0, "absolute"]] : [["playlist-prev", "weak"]]
}

// ---- queue model ------------------------------------------------------------

function fallbackTrack(videoId, entry) {
  return {
    type: "track", videoId: videoId, title: String(entry.title || videoId || entry.filename || ""),
    artists: [], artistText: "", album: null, durationSec: null, thumb: null, thumbLarge: null,
    explicit: false, likeStatus: null, setVideoId: null, available: true, kind: "song", played: null
  }
}

// mpv's `playlist` property joined with the per-track metadata map.
function buildQueue(playlist, meta) {
  var out = []
  var list = playlist || []
  for (var i = 0; i < list.length; i++) {
    var entry = list[i] || {}
    var videoId = videoIdFromUrl(entry.filename)
    var known = !!(meta && videoId && meta[videoId])
    out.push({
      index: i,
      entryId: entry.id !== undefined ? entry.id : i,
      videoId: videoId,
      current: !!entry.current,
      known: known,
      track: known ? meta[videoId] : fallbackTrack(videoId, entry)
    })
  }
  return out
}

function pruneMeta(meta, playlist) {
  var keep = {}
  var list = playlist || []
  for (var i = 0; i < list.length; i++) {
    var videoId = videoIdFromUrl(list[i] && list[i].filename)
    if (videoId && meta && meta[videoId]) keep[videoId] = meta[videoId]
  }
  return keep
}

// The queue entry with mpv's playlist entry id (end-file events carry it).
function entryById(queue, id) {
  if (id === undefined || id === null) return null
  for (var i = 0; i < (queue || []).length; i++) {
    if (queue[i].entryId === id) return queue[i]
  }
  return null
}

// What to tell you after a track failed to play. `streak` counts failures in
// a row; at 3 the service stops instead of running through the whole queue.
// `offline`: the worker couldn't reach YouTube recently either.
function playbackNotice(streak, title, detail, offline) {
  if (streak >= 3 && offline)
    return { key: "offline", title: "Can't reach YouTube Music",
             body: "Playback stopped. Check the connection, then press play to continue." }
  if (streak >= 3)
    return { key: "errors-in-a-row", title: "YouTube Music playback keeps failing",
             body: "Playback stopped. yt-dlp may need an update (omarchy update); press play to try again." }
  return { key: "play-error:" + title, title: "Couldn't play " + title, body: detail || "" }
}

function unknownVideoIds(queue, limit) {
  var ids = []
  for (var i = 0; i < (queue || []).length && ids.length < (limit || 10); i++) {
    var entry = queue[i]
    if (!entry.known && entry.videoId && ids.indexOf(entry.videoId) === -1) ids.push(entry.videoId)
  }
  return ids
}

// ---- small utilities ----------------------------------------------------------

function mulberry32(seed) {
  var state = seed >>> 0
  return function() {
    state = (state + 0x6D2B79F5) >>> 0
    var t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function shuffled(items, seed) {
  var out = (items || []).slice()
  var random = mulberry32(seed === undefined ? Date.now() : seed)
  for (var i = out.length - 1; i > 0; i--) {
    var j = Math.floor(random() * (i + 1))
    var swap = out[i]
    out[i] = out[j]
    out[j] = swap
  }
  return out
}

function formatDuration(seconds) {
  var total = Math.floor(Number(seconds))
  if (!isFinite(total) || total < 0) return ""
  var hours = Math.floor(total / 3600)
  var minutes = Math.floor((total % 3600) / 60)
  var secs = total % 60
  var ss = secs < 10 ? "0" + secs : String(secs)
  if (hours > 0) return hours + ":" + (minutes < 10 ? "0" + minutes : minutes) + ":" + ss
  return minutes + ":" + ss
}

// Queue a whole list; the first track starts playback if nothing plays.
function queueAllCommands(tracks) {
  var list = (tracks || []).filter(playable)
  var commands = []
  for (var i = 0; i < list.length; i++) commands.push(loadfile(list[i], i === 0 ? "append-play" : "append"))
  return { commands: commands, tracks: list }
}

// ---- result lists ---------------------------------------------------------------
// Lists in the panel are flat arrays of rows: { header: true, title } or
// { item, trackIndex? }. trackIndex points into the list a click plays from.

var SEARCH_SECTIONS = [
  { key: "top", title: "Top result" },
  { key: "songs", title: "Songs" },
  { key: "albums", title: "Albums" },
  { key: "artists", title: "Artists" },
  { key: "playlists", title: "Playlists" },
  { key: "videos", title: "Videos" }
]

var FILTER_TITLES = { songs: "Songs", albums: "Albums", artists: "Artists", playlists: "Playlists", videos: "Videos" }

function flattenSearch(result, filter) {
  var rows = []
  if (!result) return rows
  if (filter) {
    var items = result.items || []
    if (items.length) rows.push({ header: true, title: FILTER_TITLES[filter] || "Results" })
    for (var i = 0; i < items.length; i++) rows.push({ item: items[i] })
    return rows
  }
  for (var s = 0; s < SEARCH_SECTIONS.length; s++) {
    var group = result[SEARCH_SECTIONS[s].key] || []
    if (!group.length) continue
    rows.push({ header: true, title: SEARCH_SECTIONS[s].title })
    for (var j = 0; j < group.length; j++) rows.push({ item: group[j] })
  }
  return rows
}

function trackRows(tracks) {
  var rows = []
  for (var i = 0; i < (tracks || []).length; i++) rows.push({ item: tracks[i], trackIndex: i })
  return rows
}

function flattenArtist(page) {
  var rows = []
  if (!page) return rows
  var songs = page.songs || []
  if (songs.length) {
    rows.push({ header: true, title: "Top songs" })
    for (var i = 0; i < songs.length; i++) rows.push({ item: songs[i], trackIndex: i })
  }
  var groups = [["Albums", page.albums || []], ["Singles", page.singles || []]]
  for (var g = 0; g < groups.length; g++) {
    if (!groups[g][1].length) continue
    rows.push({ header: true, title: groups[g][0] })
    for (var j = 0; j < groups[g][1].length; j++) rows.push({ item: groups[g][1][j] })
  }
  return rows
}

// The first playable track among result rows (a fresh search's likely pick).
function firstTrack(rows) {
  for (var i = 0; i < (rows || []).length; i++) {
    var row = rows[i]
    if (!row.header && row.item && row.item.type === "track" && playable(row.item)) return row.item
  }
  return null
}

// Next row a keyboard cursor may rest on (headers, message and text rows
// are skipped). From -1, moving down lands on the first row; at either end the
// cursor stays put.
function nextSelectable(rows, index, delta) {
  var count = (rows || []).length
  var step = delta < 0 ? -1 : 1
  var i = index < 0 ? (step > 0 ? -1 : count) : index
  for (var j = i + step; j >= 0 && j < count; j += step) {
    if (selectableRow(rows[j])) return j
  }
  return index
}

function selectableRow(row) {
  return !!row && !row.header && !!row.item && row.item.type !== "message" && row.item.type !== "text"
}

function joinParts(parts) {
  return parts.filter(function(part) { return part !== null && part !== undefined && String(part) !== "" }).join(" • ")
}

function itemSubtitle(item) {
  if (!item) return ""
  if (item.type === "track") return joinParts([item.artistText, item.kind === "video" ? "Video" : ""])
  if (item.type === "album") return joinParts([item.albumType, item.artistText, item.year])
  if (item.type === "playlist") return joinParts([item.author, item.count ? item.count + " songs" : ""])
  if (item.type === "artist") return item.subtitle ? item.subtitle : "Artist"
  if (item.type === "liked") return "Songs you liked"
  if (item.type === "history") return "Recently played"
  return ""
}

// Home: a header per shelf; tracks carry their index among the shelf's
// tracks so a click plays the shelf from there.
function homeRows(shelves) {
  var rows = []
  for (var s = 0; s < (shelves || []).length; s++) {
    var items = shelves[s].items || []
    if (!items.length) continue
    rows.push({ header: true, title: shelves[s].title })
    var trackIndex = 0
    for (var i = 0; i < items.length; i++) {
      var row = { item: items[i], shelfIndex: s }
      if (items[i].type === "track") row.trackIndex = trackIndex++
      rows.push(row)
    }
  }
  return rows
}

function shelfTracks(shelves, index) {
  var shelf = shelves && shelves[index]
  return shelf ? (shelf.items || []).filter(function(item) { return item.type === "track" }) : []
}

// History grouped under its "played" labels (Today, Yesterday, …).
function historyRows(tracks) {
  var rows = []
  var group = null
  for (var i = 0; i < (tracks || []).length; i++) {
    var label = tracks[i].played || "Earlier"
    if (label !== group) {
      rows.push({ header: true, title: label })
      group = label
    }
    rows.push({ item: tracks[i], trackIndex: i })
  }
  return rows
}

// ---- resume -----------------------------------------------------------------------

function sessionSnapshot(queue, index, timePos, volume, nowMs) {
  var ids = []
  var meta = {}
  for (var i = 0; i < (queue || []).length; i++) {
    var entry = queue[i]
    if (!entry.videoId) continue
    ids.push(entry.videoId)
    if (entry.known) meta[entry.videoId] = entry.track
  }
  return {
    version: 1,
    savedAt: nowMs,
    index: index,
    timePos: Math.max(0, Math.floor(Number(timePos) || 0)),
    volume: volume,
    videoIds: ids,
    meta: meta
  }
}

function parseSession(text) {
  try {
    var session = JSON.parse(text)
    return session && session.version === 1 && Array.isArray(session.videoIds) ? session : null
  } catch (e) {
    return null
  }
}

// Rebuild the saved queue and continue the saved track where it was.
function resumeCommands(session) {
  if (!session || !Array.isArray(session.videoIds) || session.videoIds.length === 0)
    return { commands: [], tracks: [], index: -1 }
  var tracks = session.videoIds.map(function(id) {
    return (session.meta && session.meta[id]) || fallbackTrack(id, {})
  })
  var index = Math.max(0, Math.min(Number(session.index) || 0, tracks.length - 1))
  var commands = [["stop"]]
  for (var i = 0; i < tracks.length; i++) {
    var command = loadfile(tracks[i], "append")
    if (i === index && session.timePos > 0)
      command[4] = (command[4] ? command[4] + "," : "") + "start=" + Math.floor(session.timePos)
    commands.push(command)
  }
  commands.push(["playlist-play-index", index], ["set_property", "pause", false])
  return { commands: commands, tracks: tracks, index: index }
}

function isSpecialPlaylist(playlist) {
  return !playlist || playlist.playlistId === "LM" || playlist.playlistId === "SE"
}

// Playlists you can add songs to.
function ownedPlaylists(playlists) {
  return (playlists || []).filter(function(p) { return p.owned && !isSpecialPlaylist(p) })
}

// The Library list: Liked Music and History, then your playlists, then saved ones.
function libraryRows(playlists) {
  var rows = [
    { item: { type: "liked", title: "Liked Music", thumb: null } },
    { item: { type: "history", title: "History", thumb: null } }
  ]
  var owned = []
  var saved = []
  for (var i = 0; i < (playlists || []).length; i++) {
    var p = playlists[i]
    if (isSpecialPlaylist(p)) continue
    if (p.owned) owned.push(p)
    else saved.push(p)
  }
  if (owned.length) rows.push({ header: true, title: "Your playlists" })
  for (var j = 0; j < owned.length; j++) rows.push({ item: owned[j] })
  if (saved.length) rows.push({ header: true, title: "Saved playlists" })
  for (var k = 0; k < saved.length; k++) rows.push({ item: saved[k] })
  return rows
}

// Remove one track from a loaded collection (by setVideoId when it has one,
// since a playlist may hold the same song twice).
function withoutTrack(page, target) {
  if (!page || !target) return page
  var next = {}
  for (var key in page) next[key] = page[key]
  var removed = false
  next.tracks = (page.tracks || []).filter(function(t) {
    if (removed) return true
    var same = target.setVideoId ? t.setVideoId === target.setVideoId : t.videoId === target.videoId
    if (same) removed = true
    return !same
  })
  if (removed && typeof page.trackCount === "number") next.trackCount = Math.max(0, page.trackCount - 1)
  return next
}

// The page kind and id an item opens as (a thin wrapper over pageRef).
function collectionTarget(item) {
  var ref = pageRef(item)
  return ref ? { kind: ref.kind, id: ref.id } : null
}

// ---- pre-resolved streams ---------------------------------------------------------
// streams: videoId -> { url, expires } (expires in seconds since the epoch).

function streamFresh(stream, nowSec, marginSec) {
  var margin = marginSec === undefined ? 120 : marginSec
  return !!(stream && stream.url && Number(stream.expires) > nowSec + margin)
}

// Fresh streams only, at most `max` (the ones valid longest).
function pruneStreams(streams, nowSec, max) {
  var ids = Object.keys(streams || {}).filter(function(id) { return streamFresh(streams[id], nowSec) })
  ids.sort(function(a, b) { return streams[b].expires - streams[a].expires })
  var out = {}
  for (var i = 0; i < ids.length && i < (max || 50); i++) out[ids[i]] = streams[ids[i]]
  return out
}

// Upcoming queue entries (after `pos`) that still need a stream. With `wrap`
// (repeat all) the queue continues from the start.
function streamsToResolve(queue, pos, streams, nowSec, ahead, wrap) {
  var ids = []
  var count = (queue || []).length
  if (pos < 0 || count === 0) return ids
  for (var step = 1; step <= (ahead || 1); step++) {
    var i = pos + step
    if (i >= count) {
      if (!wrap) break
      i = i % count
    }
    var id = queue[i].videoId
    if (id && !streamFresh(streams && streams[id], nowSec) && ids.indexOf(id) === -1) ids.push(id)
  }
  return ids
}

// ---- recent searches and suggestions ------------------------------------------------
// Recent searches are plain strings, newest first.

function rememberQuery(list, text, max) {
  var query = String(text || "").trim()
  if (query === "") return list || []
  var key = query.toLowerCase()
  var rest = (list || []).filter(function(item) { return String(item).toLowerCase() !== key })
  return [query].concat(rest).slice(0, max || 20)
}

function forgetQuery(list, text) {
  var key = String(text || "").trim().toLowerCase()
  return (list || []).filter(function(item) { return String(item).toLowerCase() !== key })
}

function parseRecent(text) {
  try {
    var saved = JSON.parse(text)
    var queries = saved && Array.isArray(saved.queries) ? saved.queries : []
    return queries.filter(function(q) { return typeof q === "string" && q.trim() !== "" }).slice(0, 50)
  } catch (e) {
    return []
  }
}

// Up to `max` completions for what's typed: at most two of your recent
// searches that start with it, then YouTube Music's; never the typed text.
function suggestionList(recent, remote, typed, max) {
  var key = String(typed || "").trim().toLowerCase()
  var limit = max || 5
  var out = []
  var seen = {}
  seen[key] = true
  function add(text, fromHistory) {
    var value = String(text || "").trim()
    var k = value.toLowerCase()
    if (value === "" || seen[k] || out.length >= limit) return
    seen[k] = true
    out.push({ text: value, recent: fromHistory })
  }
  for (var i = 0; i < (recent || []).length && out.length < 2; i++) {
    if (String(recent[i]).toLowerCase().indexOf(key) === 0) add(recent[i], true)
  }
  for (var j = 0; j < (remote || []).length; j++) add(remote[j].text, !!remote[j].fromHistory)
  return out
}

function recentRows(recent, max) {
  var list = (recent || []).slice(0, max || 8)
  var rows = list.length ? [{ header: true, title: "Recent searches" }] : []
  for (var i = 0; i < list.length; i++) rows.push({ item: { type: "query", text: list[i], recent: true, removable: true } })
  return rows
}

// `selected` marks the one Tab put into the field.
function suggestionRows(suggestions, selected) {
  var list = suggestions || []
  var rows = list.length ? [{ header: true, title: "Suggestions" }] : []
  for (var i = 0; i < list.length; i++) {
    rows.push({ item: { type: "query", text: list[i].text, recent: !!list[i].recent, removable: false, selected: i === selected } })
  }
  return rows
}

// ---- caches ---------------------------------------------------------------------

function searchKey(query, filter) {
  return (filter || "") + "|" + String(query || "").trim().toLowerCase()
}

// Every cover URL (thumb / thumbLarge) inside a result, deduplicated.
function collectThumbs(value, max) {
  var limit = max || 150
  var seen = {}
  var out = []
  function walk(node, depth) {
    if (out.length >= limit || depth > 6 || node === null || typeof node !== "object") return
    if (Array.isArray(node)) {
      for (var i = 0; i < node.length && out.length < limit; i++) walk(node[i], depth + 1)
      return
    }
    var keys = ["thumb", "thumbLarge", "photo"]
    for (var k = 0; k < keys.length; k++) {
      var url = node[keys[k]]
      if (typeof url === "string" && url.indexOf("https://") === 0 && !seen[url]) {
        seen[url] = true
        out.push(url)
      }
    }
    for (var key in node) {
      if (node[key] && typeof node[key] === "object") walk(node[key], depth + 1)
    }
  }
  walk(value, 0)
  return out
}

// ---- bar widget -----------------------------------------------------------------

function normalizeDisplay(value) {
  var mode = String(value || "").toLowerCase()
  return mode === "player" || mode === "status" || mode === "mini" ? "player" : "icon"
}

function nextDisplay(value) {
  return normalizeDisplay(value) === "player" ? "icon" : "player"
}

// Qt.LeftButton = 1, Qt.RightButton = 2, Qt.MiddleButton = 4.
function clickAction(button) {
  if (button === 4) return "playPause"
  if (button === 2) return "toggleDisplay"
  return "panel"
}

function nowPlayingLabel(track) {
  if (!track) return ""
  var title = String(track.title || "")
  var artists = String(track.artistText || "")
  return artists ? title + "  ·  " + artists : title
}

function parseMpvLine(line) {
  try {
    var message = JSON.parse(line)
    return message && typeof message === "object" ? message : null
  } catch (e) {
    return null
  }
}

if (typeof module !== "undefined") {
  module.exports = {
    PLUGIN_ID: PLUGIN_ID,
    trackUrl: trackUrl,
    videoIdFromUrl: videoIdFromUrl,
    mediaTitle: mediaTitle,
    utf8Length: utf8Length,
    loadfileOptions: loadfileOptions,
    playNowCommands: playNowCommands,
    playNextCommands: playNextCommands,
    addToQueueCommands: addToQueueCommands,
    playCollectionCommands: playCollectionCommands,
    originalOrder: originalOrder,
    normalizeRepeat: normalizeRepeat,
    nextRepeat: nextRepeat,
    repeatMode: repeatMode,
    repeatCommands: repeatCommands,
    shuffleOrder: shuffleOrder,
    unshuffleOrder: unshuffleOrder,
    reorderCommands: reorderCommands,
    autoplayPicks: autoplayPicks,
    parsePrefs: parsePrefs,
    isPrefKey: isPrefKey,
    normalizePref: normalizePref,
    withPref: withPref,
    serializePrefs: serializePrefs,
    moveArgs: moveArgs,
    previousCommands: previousCommands,
    buildQueue: buildQueue,
    pruneMeta: pruneMeta,
    unknownVideoIds: unknownVideoIds,
    entryById: entryById,
    playbackNotice: playbackNotice,
    shuffled: shuffled,
    formatDuration: formatDuration,
    queueAllCommands: queueAllCommands,
    flattenSearch: flattenSearch,
    trackRows: trackRows,
    flattenArtist: flattenArtist,
    nextSelectable: nextSelectable,
    selectableRow: selectableRow,
    firstTrack: firstTrack,
    itemSubtitle: itemSubtitle,
    collectionTarget: collectionTarget,
    ownedPlaylists: ownedPlaylists,
    libraryRows: libraryRows,
    homeRows: homeRows,
    shelfTracks: shelfTracks,
    historyRows: historyRows,
    sessionSnapshot: sessionSnapshot,
    parseSession: parseSession,
    resumeCommands: resumeCommands,
    withoutTrack: withoutTrack,
    streamFresh: streamFresh,
    pruneStreams: pruneStreams,
    streamsToResolve: streamsToResolve,
    rememberQuery: rememberQuery,
    forgetQuery: forgetQuery,
    parseRecent: parseRecent,
    suggestionList: suggestionList,
    recentRows: recentRows,
    suggestionRows: suggestionRows,
    searchKey: searchKey,
    collectThumbs: collectThumbs,
    normalizeDisplay: normalizeDisplay,
    nextDisplay: nextDisplay,
    clickAction: clickAction,
    nowPlayingLabel: nowPlayingLabel,
    parseMpvLine: parseMpvLine
  }
}

// ==== pages (F2) ==========================================================================
// One way to open any page. An item maps to a page kind, the kind names the
// worker method and its params, and the page's identity is method + params
// as canonical JSON, so two views of one browseId (an artist's albums and
// singles, two sort orders) never share a cache entry or a stale reply.
// Slices register their kinds in PAGE_KINDS and their See all endpoints in
// ROUTES, each in its own section of this file.

// JSON with object keys sorted at every level (undefined values dropped, as
// JSON.stringify does): the same params always give the same text.
function canonicalJson(value) {
  if (value === undefined || typeof value === "function") return "null"
  if (value === null || typeof value !== "object") return JSON.stringify(value)
  if (Array.isArray(value)) return "[" + value.map(canonicalJson).join(",") + "]"
  var keys = Object.keys(value).filter(function(k) {
    return value[k] !== undefined && typeof value[k] !== "function"
  }).sort()
  return "{" + keys.map(function(k) { return JSON.stringify(k) + ":" + canonicalJson(value[k]) }).join(",") + "}"
}

// kind -> {
//   method        worker method
//   params(t)     its params for the target item t (null: t lacks its id)
//   id(t)         the id Store.forgetCollection(kind, id) matches
//   title         header title while nothing better names the page
//   round         round cover (artists, profiles); a page's own `round` also counts
//   adapt(r, ref) optional: turns the reply into what CollectionView shows
//   claims(item)  optional: takes over items of another type (checked first)
//   ttlMs         optional: how long the Store's copy counts as fresh (5 min)
// }
var PAGE_KINDS = {
  album: {
    method: "album", title: "Album", round: false,
    params: function(t) { return t.browseId ? { browseId: t.browseId } : null },
    id: function(t) { return t.browseId }
  },
  playlist: {
    method: "playlist", title: "Playlist", round: false,
    params: function(t) { return t.playlistId ? { playlistId: t.playlistId } : null },
    id: function(t) { return t.playlistId }
  },
  artist: {
    method: "artist", title: "Artist", round: true,
    params: function(t) { return t.channelId ? { channelId: t.channelId } : null },
    id: function(t) { return t.channelId }
  },
  liked: {
    method: "liked", title: "Liked Music", round: false,
    params: function() { return {} },
    id: function() { return "LM" }
  },
  history: {
    method: "history", title: "History", round: false,
    params: function() { return { limit: 100 } },
    id: function() { return "history" },
    adapt: function(result) { return historyCollection(result) }
  },
  // Any endpoint nothing better reads, through the worker's browsePage (F1).
  browse: {
    method: "browsePage", title: "", round: false,
    params: function(t) { return t.browseId ? { browseId: t.browseId, params: t.params } : null },
    id: function(t) { return t.browseId }
  }
}

function pageKindOf(item) {
  if (!item || typeof item !== "object") return null
  for (var name in PAGE_KINDS) {
    var claims = PAGE_KINDS[name].claims
    if (claims && claims(item)) return name
  }
  return Object.prototype.hasOwnProperty.call(PAGE_KINDS, item.type) ? item.type : null
}

// { kind, method, params, key, id } for an item that opens as a page, else
// null. `extra` adds params such as { order: 2 }; empty values are left out,
// so a missing `params` and a null one give the same key.
function pageRef(item, extra) {
  var kind = pageKindOf(item)
  if (!kind) return null
  var spec = PAGE_KINDS[kind]
  var given = spec.params(item)
  if (!given) return null
  var params = {}
  var k
  for (k in given) if (given[k] !== undefined && given[k] !== null && given[k] !== "") params[k] = given[k]
  for (k in extra || {}) if (extra[k] !== undefined && extra[k] !== null) params[k] = extra[k]
  return {
    kind: kind, method: spec.method, params: params,
    key: spec.method + ":" + canonicalJson(params),
    id: spec.id ? spec.id(item) : null
  }
}

// The PAGE_KINDS entry of a ref (null when its kind is unknown).
function pageKind(ref) {
  return ref && Object.prototype.hasOwnProperty.call(PAGE_KINDS, ref.kind) ? PAGE_KINDS[ref.kind] : null
}

function pageKindTitle(ref) {
  var spec = pageKind(ref)
  return spec && spec.title ? String(spec.title) : ""
}

function pageRound(ref, page) {
  var spec = pageKind(ref)
  return !!(page && page.round === true) || !!(spec && spec.round)
}

// What Store.loadCollection answers for an item no kind can open.
function cannotOpen() {
  return { ok: false, error: { code: "BAD_REQUEST", message: "Cannot open this item yet" } }
}

// History comes back as a plain list; its page shows it as a collection.
function historyCollection(result) {
  var items = (result && result.items) || []
  return {
    type: "collection", kind: "history", id: "history", playlistId: null,
    title: "History", subtitle: items.length + " songs", owned: false, editable: false,
    trackCount: items.length, truncated: false,
    thumb: items.length ? items[0].thumb : null, thumbLarge: items.length ? items[0].thumbLarge : null,
    tracks: items
  }
}

// ---- routes: See all endpoints to the item that opens them ---------------------------
// An endpoint is { browseId, params, pageType, title }. A route matches on
// `browseId` (exact), `pageType` and `prefix`, each optional, plus an
// optional `when(endpoint, id)`. The most specific match wins (an exact id,
// then a page type, then the longest prefix; a tie goes to the route added
// last), so a slice adds a narrower route without touching these.
// `target(endpoint, id)` returns the item to open; null falls through to a
// browse page.
var ROUTES = [
  { prefix: "VL", target: function(ep, id) { return { type: "playlist", playlistId: id.slice(2), title: ep.title || "" } } },
  { prefix: "MPRE", target: function(ep, id) { return { type: "album", browseId: id, title: ep.title || "" } } },
  { prefix: "UC", when: function(ep) { return !ep.params },
    target: function(ep, id) { return { type: "artist", channelId: id, name: ep.title || "" } } },
  // A library artist page (MPLAUC...) opens the artist's own page.
  { prefix: "MPLA", when: function(ep) { return !ep.params },
    target: function(ep, id) { return { type: "artist", channelId: id.slice(4), name: ep.title || "" } } }
]

function _routeScore(route, ep, id) {
  if (route.browseId !== undefined && route.browseId !== id) return -1
  if (route.pageType !== undefined && route.pageType !== ep.pageType) return -1
  if (route.prefix !== undefined && id.indexOf(route.prefix) !== 0) return -1
  if (route.when && !route.when(ep, id)) return -1
  return (route.browseId !== undefined ? 10000 : 0) + (route.pageType !== undefined ? 1000 : 0)
    + (route.prefix ? route.prefix.length : 0)
}

function routeEndpoint(endpoint) {
  var ep = endpoint || {}
  var id = typeof ep.browseId === "string" ? ep.browseId : ""
  if (id === "") return null
  var best = null
  var bestScore = -1
  for (var i = 0; i < ROUTES.length; i++) {
    var score = _routeScore(ROUTES[i], ep, id)
    if (score >= 0 && score >= bestScore) {
      best = ROUTES[i]
      bestScore = score
    }
  }
  var target = best ? best.target(ep, id) : null
  return target || { type: "browse", browseId: id, params: ep.params || null, title: ep.title || "" }
}

// The item a See all row opens.
function moreTarget(more) {
  var ep = more && more.endpoint ? more.endpoint : {}
  return routeEndpoint({ browseId: ep.browseId, params: ep.params, pageType: ep.pageType, title: more ? more.sectionTitle : "" })
}

// ---- page rows ------------------------------------------------------------------------
// A sectioned page (the worker's `page` shape) as result rows: a header per
// titled section, its items (tracks numbered within the section, so Enter
// plays that section from there), a selectable See all row when the
// section has one, the page's message, and a trailing Load more row while
// there is more. Item rows carry their sectionIndex.

function _validItems(items) {
  return (items || []).filter(function(item) { return !!item && typeof item === "object" })
}

function pageRows(page) {
  var rows = []
  if (!page) return rows
  var sections = page.sections || []
  for (var s = 0; s < sections.length; s++) {
    var section = sections[s] || {}
    var items = _validItems(section.items)
    if (!items.length && !section.more) continue
    if (section.title) rows.push({ header: true, title: String(section.title) })
    var trackIndex = 0
    for (var i = 0; i < items.length; i++) {
      var row = { item: items[i], sectionIndex: s }
      if (items[i].type === "track") row.trackIndex = trackIndex++
      rows.push(row)
    }
    if (section.more) {
      var title = section.title ? String(section.title) : ""
      rows.push({ item: { type: "more", title: title ? "See all " + title : "See all", endpoint: section.more, sectionTitle: title },
                  sectionIndex: s })
    }
  }
  if (page.message) rows.push({ item: { type: "message", title: String(page.message) } })
  if (page.cursor || page.hasMore) rows.push({ item: { type: "loadMore", title: "Load more" } })
  return rows
}

// The tracks of one section, in the order trackIndex counts them.
function sectionTracks(page, index) {
  var section = page && page.sections ? page.sections[index] : null
  return section ? _validItems(section.items).filter(function(item) { return item.type === "track" }) : []
}

// Every track of a page, section after section (header Play / Queue all).
function pageTracks(page) {
  var out = []
  for (var s = 0; page && s < (page.sections || []).length; s++) out = out.concat(sectionTracks(page, s))
  return out
}

function pageItemCount(page) {
  if (!page) return 0
  var count = (page.items || []).length + (page.tracks || []).length
  for (var s = 0; s < (page.sections || []).length; s++) count += ((page.sections[s] || {}).items || []).length
  return count
}

// ---- identity: item keys, row places, the cursor across row changes ---------------------

// What a row's item is, for keeping the cursor and dropping repeats: a
// playlist entry by its setVideoId (a playlist may hold a song twice),
// anything else by type and id.
function itemKey(item) {
  if (!item || typeof item !== "object") return ""
  if (item.setVideoId) return "set:" + item.setVideoId
  var type = String(item.type || "")
  var id
  if (type === "track") id = item.videoId
  else if (type === "album") id = item.browseId
  else if (type === "playlist") id = item.playlistId
  else if (type === "artist" || type === "profile" || type === "channel") id = item.channelId
  else if (type === "more") id = canonicalJson(item.endpoint || null)
  else if (type === "browse") id = canonicalJson({ browseId: item.browseId, params: item.params || null })
  else if (type === "query") id = item.text
  else if (type === "action") id = item.id
  else if (type === "loadMore" || type === "liked" || type === "history") id = ""
  else id = item.browseId || item.playlistId || item.channelId || item.videoId || item.id || item.title
  return type + ":" + (id === undefined || id === null ? "" : String(id))
}

// Keys appendPage drops as repeats: playlist entries (setVideoId), albums,
// artists and playlists. A track without a setVideoId (history, search)
// can rightly be listed twice.
function _repeatKey(item) {
  if (!item || typeof item !== "object") return ""
  if (item.setVideoId) return "set:" + item.setVideoId
  var id = item.type === "album" ? item.browseId : item.type === "playlist" ? item.playlistId
    : item.type === "artist" ? item.channelId : null
  return id ? item.type + ":" + id : ""
}

function rowKey(row) {
  if (!row) return ""
  return row.header ? "#:" + String(row.title || "") : itemKey(row.item)
}

// A row's place: its key and how many rows before it share that key, so the
// second listing of a song is told from the first.
function rowPlaces(rows) {
  var counts = {}
  var out = []
  for (var i = 0; i < (rows || []).length; i++) {
    var key = rowKey(rows[i])
    var n = counts[key] || 0
    counts[key] = n + 1
    out.push({ key: key, n: n })
  }
  return out
}

function rowPlace(rows, index) {
  if (!rows || index < 0 || index >= rows.length) return null
  var key = rowKey(rows[index])
  var n = 0
  for (var i = 0; i < index; i++) if (rowKey(rows[i]) === key) n++
  return { key: key, n: n }
}

function _placeIndex(rows) {
  var map = {}
  for (var i = 0; i < rows.length; i++) {
    var key = rowKey(rows[i])
    if (!Object.prototype.hasOwnProperty.call(map, key)) map[key] = []
    map[key].push(i)
  }
  return map
}

// Fewer rows with that key now: the last of them.
function _lookup(map, place) {
  if (!place || !Object.prototype.hasOwnProperty.call(map, place.key)) return -1
  var list = map[place.key]
  return list[Math.min(place.n, list.length - 1)]
}

function findPlace(rows, place) {
  return place ? _lookup(_placeIndex(rows || []), place) : -1
}

// Rows that hold content, as opposed to See all, Load more, action and
// message rows.
function _contentRow(row) {
  if (!selectableRow(row)) return false
  var type = row.item.type
  return type !== "more" && type !== "loadMore" && type !== "action"
}

// Where the cursor goes when `oldRows` become `newRows`: the same row by
// place, else the nearest following row that is still there, else the
// nearest preceding one, else nowhere (-1). From the Load more row it moves
// onto the first new row after the content it followed (an append), else
// it stays on Load more (a refresh, or nothing new came). When that content
// is gone the rows were replaced, not appended to (a refresh that pushed
// the old last item off the page), so it stays on Load more too.
function cursorAfterRows(oldRows, newRows, oldIndex) {
  var before = oldRows || []
  var after = newRows || []
  if (oldIndex < 0 || oldIndex >= before.length || after.length === 0) return -1
  var places = rowPlaces(before)
  var map = _placeIndex(after)
  var old = before[oldIndex]
  if (old && old.item && old.item.type === "loadMore") {
    var known = {}
    for (var k = 0; k < places.length; k++) known[places[k].key + "\n" + places[k].n] = true
    // -1 with no content before Load more: every row after is new.
    var start = -1
    for (var p = oldIndex - 1; p >= 0; p--) {
      if (!_contentRow(before[p])) continue
      start = _lookup(map, places[p])
      if (start < 0) start = after.length  // gone: no append scan
      break
    }
    var placesAfter = rowPlaces(after)
    for (var j = start + 1; j < after.length; j++) {
      if (selectableRow(after[j]) && !known[placesAfter[j].key + "\n" + placesAfter[j].n]) return j
    }
  }
  var found = _lookup(map, places[oldIndex])
  if (found >= 0 && selectableRow(after[found])) return found
  var i
  for (i = oldIndex + 1; i < before.length; i++) {
    if (!selectableRow(before[i])) continue
    found = _lookup(map, places[i])
    if (found >= 0 && selectableRow(after[found])) return found
  }
  for (i = oldIndex - 1; i >= 0; i--) {
    if (!selectableRow(before[i])) continue
    found = _lookup(map, places[i])
    if (found >= 0 && selectableRow(after[found])) return found
  }
  return -1
}

// ---- appending a cursor page ----------------------------------------------------------

// `page` with the next cursor page `next` appended, taking next's cursor.
// Untitled sections of `next` (a list's continuation) join the page's last
// section; titled ones are added after it. Repeats with an id-based key
// (_repeatKey) are dropped; other items never are. Flat `items` lists
// append the same way. At most `max` items in all: past that the page ends
// there (no cursor, `capped: true`).
function appendPage(page, next, max) {
  if (!page) return next || null
  if (!next) return page
  var limit = max > 0 ? max : Infinity
  var seen = {}
  var total = 0
  var capped = false
  function note(items) {
    for (var i = 0; i < (items || []).length; i++) {
      var key = _repeatKey(items[i])
      if (key) seen[key] = true
      total++
    }
  }
  function fresh(items) {
    var out = []
    for (var i = 0; i < (items || []).length; i++) {
      var item = items[i]
      if (!item || typeof item !== "object") continue
      var key = _repeatKey(item)
      if (key && seen[key]) continue
      if (total >= limit) {
        capped = true
        break
      }
      if (key) seen[key] = true
      out.push(item)
      total++
    }
    return out
  }
  var out = {}
  for (var k in page) out[k] = page[k]
  var sections = (page.sections || []).slice()
  for (var s = 0; s < sections.length; s++) note(sections[s] && sections[s].items)
  note(page.items)
  var incoming = next.sections || []
  for (var n = 0; n < incoming.length; n++) {
    var section = incoming[n] || {}
    var items = fresh(section.items)
    var last = sections.length ? sections[sections.length - 1] : null
    var copy = {}
    var key
    if (!section.title && last) {
      for (key in last) copy[key] = last[key]
      copy.items = (last.items || []).concat(items)
      sections[sections.length - 1] = copy
    } else if (items.length || section.more) {
      for (key in section) copy[key] = section[key]
      copy.items = items
      sections.push(copy)
    }
  }
  if (page.sections || next.sections) out.sections = sections
  if (Array.isArray(page.items) || Array.isArray(next.items)) out.items = (page.items || []).concat(fresh(next.items))
  out.cursor = capped ? null : (next.cursor || null)
  out.hasMore = !!out.cursor
  if (capped) out.capped = true
  return out
}

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    canonicalJson: canonicalJson,
    PAGE_KINDS: PAGE_KINDS,
    pageKindOf: pageKindOf,
    pageRef: pageRef,
    pageKind: pageKind,
    pageKindTitle: pageKindTitle,
    pageRound: pageRound,
    cannotOpen: cannotOpen,
    historyCollection: historyCollection,
    ROUTES: ROUTES,
    routeEndpoint: routeEndpoint,
    moreTarget: moreTarget,
    pageRows: pageRows,
    sectionTracks: sectionTracks,
    pageTracks: pageTracks,
    pageItemCount: pageItemCount,
    itemKey: itemKey,
    rowKey: rowKey,
    rowPlaces: rowPlaces,
    rowPlace: rowPlace,
    findPlace: findPlace,
    cursorAfterRows: cursorAfterRows,
    appendPage: appendPage
  })
}

// ==== menu (F3) ===========================================================================
// One menu for every item (m, '.' or right-click), like the web's three-dot
// menu. MENU_ACTIONS is a registry: a slice appends its entries in its own
// section, and menuActions(item, ctx) lists the ones that apply. KEYS is the
// one list of panel keys: Panel dispatches text keys through it, and the ?
// overlay, the README keys row, Panel.qml's header comment and tooltips are
// made from it. Whole albums, playlists, mixes and artists play through
// Service.playItem (the worker's tracksFor), at most QUEUE_CAP at a time.

var MUSIC_URL = "https://music.youtube.com/"
var QUEUE_CAP = 500

function _stripVl(id) {
  var text = String(id || "")
  return text.indexOf("VL") === 0 ? text.slice(2) : text
}

// Radio-style playlists (RD...) that play through the watch playlist;
// RDCLAK (curated) and RDPN (New episodes) are ordinary playlists.
function isMixId(id) {
  var pid = _stripVl(id)
  return pid.indexOf("RD") === 0 && pid.indexOf("RDCLAK") !== 0 && pid.indexOf("RDPN") !== 0
}

// What an item is to its menu and to whole-collection play: track, album,
// playlist, mix, liked, artist, or "" (nothing to play or share).
function menuKind(item) {
  if (!item || typeof item !== "object") return ""
  var type = item.type
  if (type === "track") return item.videoId ? "track" : ""
  if (type === "album") return item.browseId || item.playlistId ? "album" : ""
  if (type === "playlist") {
    if (!item.playlistId) return ""
    if (_stripVl(item.playlistId) === "LM") return "liked"
    return isMixId(item.playlistId) ? "mix" : "playlist"
  }
  if (type === "liked") return "liked"
  if (type === "artist") return item.channelId ? "artist" : ""
  return ""
}

function itemTitle(item) {
  return item ? String(item.title || item.name || "") : ""
}

// Rows that n and a can put in the queue (a song, or all of a collection).
function canQueue(item) {
  var kind = menuKind(item)
  return kind === "track" || kind === "album" || kind === "playlist" || kind === "mix" || kind === "liked"
}

// The worker's tracksFor params for a collection (null for a song or nothing).
function tracksForParams(item) {
  var kind = menuKind(item)
  if (kind === "album") return item.browseId ? { kind: "album", id: item.browseId } : { kind: "playlist", id: _stripVl(item.playlistId) }
  if (kind === "playlist") return { kind: "playlist", id: _stripVl(item.playlistId) }
  if (kind === "mix") return { kind: "mix", id: _stripVl(item.playlistId) }
  if (kind === "liked") return { kind: "liked", id: "LM" }
  if (kind === "artist") return { kind: "artist", id: item.channelId }
  return null
}

// The worker's radio params: a song, an album (its audio playlist, else its
// page), a playlist, a mix (played as it is) or an artist.
function radioParams(item) {
  var kind = menuKind(item)
  if (kind === "track") return { videoId: item.videoId }
  if (kind === "album") return item.playlistId ? { playlistId: _stripVl(item.playlistId) } : { browseId: item.browseId }
  if (kind === "playlist") return { playlistId: _stripVl(item.playlistId) }
  if (kind === "mix") return { mixId: _stripVl(item.playlistId) }
  if (kind === "artist") return { channelId: item.channelId }
  return null
}

// The tracks of a collection the Store holds, when it holds all of them
// (null for part of a list, or an artist page's few songs).
function completeTracks(data) {
  if (!data || typeof data !== "object") return null
  if (data.type === "page") return data.cursor || data.hasMore || data.capped ? null : pageTracks(data)
  if (data.type === "collection") return data.truncated ? null : (data.tracks || [])
  return null
}

function shareUrl(item) {
  var kind = menuKind(item)
  if (kind === "track") return MUSIC_URL + "watch?v=" + item.videoId
  if (kind === "album") return item.browseId ? MUSIC_URL + "browse/" + item.browseId : MUSIC_URL + "playlist?list=" + _stripVl(item.playlistId)
  if (kind === "playlist") return MUSIC_URL + "playlist?list=" + _stripVl(item.playlistId)
  if (kind === "artist") return MUSIC_URL + "channel/" + item.channelId
  if (item && (item.type === "profile" || item.type === "channel") && item.channelId) return MUSIC_URL + "channel/" + item.channelId
  return ""
}

// Artists of a song or album that have a page. (An item that went through
// a QML signal holds its artists as a list Array.isArray does not accept.)
function linkedArtists(item) {
  var list = item && item.artists && typeof item.artists.length === "number" ? item.artists : []
  var out = []
  for (var i = 0; i < list.length; i++) if (list[i] && list[i].id) out.push(list[i])
  return out
}

// The album page a song belongs to ({ type: "album", ... }), when its id is
// known. A song whose album is known only by its audio playlist (OLAK5uy_)
// has albumPlaylistOf() instead, looked up through the worker.
function albumOf(item) {
  var album = item && item.type === "track" ? item.album : null
  if (!album || !album.id || String(album.id).indexOf("MPRE") !== 0) return null
  return { type: "album", browseId: album.id, title: album.name || "", thumb: item.thumb || null, thumbLarge: item.thumbLarge || null }
}

function albumPlaylistOf(item) {
  if (!item) return ""
  var id = item.type === "track" && item.album ? item.album.id
    : item.type === "album" && !item.browseId ? item.playlistId : ""
  id = _stripVl(id)
  return id.indexOf("OLAK5uy_") === 0 ? id : ""
}

// ---- the queue cap ------------------------------------------------------------------

// At most `cap` playable tracks of a list, starting at the track `start`
// indexes (or ending at the list's end, so a late start still keeps `cap`).
// Returns { tracks, start (into tracks), total (playable), capped }.
function capWindow(tracks, start, cap) {
  var source = tracks || []
  var limit = cap > 0 ? cap : QUEUE_CAP
  var playableAt = []
  for (var i = 0; i < source.length; i++) if (playable(source[i])) playableAt.push(i)
  if (playableAt.length <= limit) return { tracks: source, start: Number(start) || 0, total: playableAt.length, capped: false }
  var wanted = Number(start) || 0
  var k = playableAt.length - 1
  for (var p = 0; p < playableAt.length; p++) {
    if (playableAt[p] >= wanted) {
      k = p
      break
    }
  }
  var from = Math.max(0, Math.min(k, playableAt.length - limit))
  var kept = playableAt.slice(from, from + limit).map(function(j) { return source[j] })
  return { tracks: kept, start: k - from, total: playableAt.length, capped: true }
}

// `total` is the list's size; unknown (null), it says "the first".
function queueCapNotice(kept, total) {
  return { title: total > kept ? "Queued " + kept + " of " + total + " songs" : "Queued the first " + kept + " songs",
           body: "At most " + QUEUE_CAP + " songs of one list go into the queue." }
}

// The cap notice when not all of a list went into the queue, else null.
// `cut` is capWindow's reply; `partial` ({ total }, total null when
// unknown) says the shell read only the start of the list (tracksFor with
// a limit), so the list is longer than what it had.
function capNotice(kept, cut, partial) {
  if (partial) return queueCapNotice(kept, partial.total)
  return cut && cut.capped ? queueCapNotice(kept, cut.total) : null
}

// Many tracks to play next, in their order. With a current entry each one
// goes right after it, last first, so they end up in order; with none
// they are appended and the first starts (as insert-next-play does).
function playNextAllCommands(tracks, state) {
  var list = (tracks || []).filter(playable)
  if (list.length === 0) return { commands: [], tracks: [] }
  if (!state || !(state.count > 0) || !(state.pos >= 0)) return queueAllCommands(list)
  var commands = []
  for (var i = list.length - 1; i >= 0; i--) commands.push(loadfile(list[i], "insert-next"))
  return { commands: commands, tracks: list }
}

// ---- menu actions -------------------------------------------------------------------
// An entry is { id, title, icon, key?, confirm?, applies(item, ctx), run(env) }.
//   title    a string, or function(item, ctx)
//   key      the KEYS id whose keys show as the row's hint
//   confirm  the title shown after a first press; a second press within 4 s runs it
//   applies  ctx: { signedIn, owned, inLibrary, liked, saved, subscribed,
//            pageKind, isCurrent } plus page (the page's own menu, M),
//            hasTracks, removable, removeTitle, autoplay, queueLength
//   run      env: { item, ctx, index, owner, panel, service }; owner is the
//            view the row is in, index the row's index when the entry runs
//            (Panel finds the row again: rows may change while the menu is
//            open), -1 on a page menu or when the row is gone; panel the Panel
// A page menu (M) passes the page's item with ctx.page; play, shuffle and
// queue entries then go to the owner's playAll(mode), the header buttons'
// twin.

function _kindIn(kinds) {
  return function(item) { return kinds.indexOf(menuKind(item)) >= 0 }
}

function _pagePlays(ctx) {
  return !!ctx && ctx.page === true && ctx.hasTracks === true
}

function _playMode(mode) {
  return function(env) {
    if (env.ctx && env.ctx.page && env.owner && typeof env.owner.playAll === "function") env.owner.playAll(mode)
    else env.service.playItem(env.item, mode)
  }
}

var _PLAYS = ["track", "album", "playlist", "mix", "liked"]
var _SHUFFLES = ["album", "playlist", "liked", "artist"]
var _RADIOS = ["track", "album", "playlist", "artist"]

var MENU_ACTIONS = [
  { id: "open", title: "Open", icon: "󰅂",
    applies: function(item, ctx) { return menuKind(item) === "artist" && !ctx.page },
    run: function(env) { env.panel.push(env.item) } },
  { id: "play", title: "Play", icon: "󰐊",
    applies: function(item, ctx) { return (_kindIn(_PLAYS)(item) && !ctx.isCurrent) || _pagePlays(ctx) },
    run: function(env) {
      // A song row plays as Enter does (the list around it); a row that is
      // gone plays the song alone.
      if (menuKind(env.item) === "track" && !env.ctx.page && env.index >= 0 && env.owner && typeof env.owner.activate === "function")
        env.owner.activate(env.index)
      else _playMode("now")(env)
    } },
  { id: "shuffle", title: "Shuffle", icon: "󰒝",
    applies: function(item, ctx) { return _kindIn(_SHUFFLES)(item) || _pagePlays(ctx) },
    run: _playMode("shuffle") },
  { id: "playNext", title: "Play next", icon: "󰒭", key: "playNext",
    applies: function(item, ctx) { return (_kindIn(_PLAYS)(item) || _pagePlays(ctx)) && !ctx.isCurrent && ctx.pageKind !== "queue" },
    run: _playMode("next") },
  { id: "addToQueue", title: "Add to queue", icon: "󰐕", key: "addToQueue",
    applies: function(item, ctx) { return (_kindIn(_PLAYS)(item) || _pagePlays(ctx)) && !ctx.isCurrent && ctx.pageKind !== "queue" },
    run: _playMode("queue") },
  { id: "radio", title: "Start radio", icon: "󰐹", key: "radio",
    applies: _kindIn(_RADIOS),
    run: function(env) { env.service.startRadio(env.item) } },
  { id: "addToPlaylist", title: "Add to playlist", icon: "󰐒", key: "addToPlaylist",
    applies: function(item, ctx) { return menuKind(item) === "track" && ctx.signedIn === true },
    run: function(env) { env.panel.openPicker([env.item]) } },
  { id: "goArtist", title: "Go to artist", icon: "󰠃", key: "artist",
    applies: function(item, ctx) {
      var kind = menuKind(item)
      return (kind === "track" || kind === "album") && linkedArtists(item).length > 0
    },
    run: function(env) { env.panel.goArtist(env.item) } },
  { id: "goAlbum", title: "Go to album", icon: "󰀥", key: "album",
    applies: function(item, ctx) {
      return menuKind(item) === "track" && ctx.pageKind !== "album" && (!!albumOf(item) || albumPlaylistOf(item) !== "")
    },
    run: function(env) { env.panel.goAlbum(env.item) } },
  { id: "share", title: "Copy link", icon: "󰌷",
    applies: function(item) { return shareUrl(item) !== "" },
    run: function(env) { env.service.copyLink(env.item) } },
  { id: "remove", title: function(item, ctx) { return ctx.removeTitle || "Remove" }, icon: "󰅖", key: "remove",
    applies: function(item, ctx) { return ctx.removable === true && !ctx.page },
    run: function(env) { if (env.index >= 0) env.owner.remove(env.index) } },
  { id: "deletePlaylist", title: "Delete playlist", icon: "󰩺", confirm: "Really delete? Choose again",
    applies: function(item, ctx) { return ctx.page === true && ctx.owned === true && menuKind(item) === "playlist" },
    run: function(env) { env.owner.deletePlaylist(true) } },
  // Now (M): the queue header's twins.
  { id: "autoplay", title: function(item, ctx) { return ctx.autoplay ? "Turn autoplay off" : "Turn autoplay on" }, icon: "󰐹",
    applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "now" },
    run: function(env) { env.service.toggleAutoplay() } },
  { id: "clearQueue", title: "Clear the queue (keeps the current song)", icon: "󰆴",
    applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "now" && ctx.queueLength > 1 },
    run: function(env) { env.service.clearUpcoming() } },
  // Home (M): the Refresh button's twin.
  { id: "refresh", title: "Refresh", icon: "󰑐",
    applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "home" },
    run: function(env) { env.owner.refresh(true) } },
  // Library (M): the account row. S9 and S14-S16 append Your channel,
  // Improve recommendations, Record plays, Account, Quality and Location
  // (applies: ctx.page && ctx.pageKind === "library").
  { id: "signOut", title: "Sign out", icon: "󰍃",
    applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "library" && ctx.signedIn === true },
    run: function(env) { env.service.signOut() } },
  { id: "signIn", title: "Sign in", icon: "󰍂",
    applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "library" && ctx.signedIn !== true },
    run: function(env) { env.service.signIn() } }
]

function _title(action, item, ctx) {
  return typeof action.title === "function" ? String(action.title(item, ctx)) : String(action.title || "")
}

// The entries that apply to `item` (may be null on a page menu), in
// registry order, with their titles and key hints resolved.
function menuActions(item, ctx) {
  var c = ctx || {}
  var out = []
  for (var i = 0; i < MENU_ACTIONS.length; i++) {
    var action = MENU_ACTIONS[i]
    var applies = false
    try {
      applies = !!action.applies(item || null, c)
    } catch (e) {
      applies = false
    }
    if (!applies) continue
    out.push({ id: action.id, title: _title(action, item, c), icon: action.icon || "", hint: action.key ? keyHint(action.key) : "",
               confirm: action.confirm || "", run: action.run })
  }
  return out
}

function menuAction(id) {
  for (var i = 0; i < MENU_ACTIONS.length; i++) if (MENU_ACTIONS[i].id === id) return MENU_ACTIONS[i]
  return null
}

// Result rows of resolved actions (action rows); `armed` is the id waiting
// for its confirming second press.
function actionRows(actions, armed) {
  return (actions || []).map(function(action) {
    var waiting = !!action.confirm && action.id === armed
    return { item: { type: "action", id: action.id, title: waiting ? action.confirm : action.title,
                     icon: action.icon, hint: action.hint || "" } }
  })
}

// ---- keys ---------------------------------------------------------------------------
// { id, keys, caught, title, group }: `keys` are the texts PanelKeyCatcher
// hands to textKey (Panel dispatches them through keyId); `caught` are keys
// the catcher handles itself, listed for help only. A key is bound once.

var KEYS = [
  { id: "tab", group: "Move", keys: ["1", "2", "3", "4"], label: ["1-4"], title: "switch tab: Now, Search, Library, Home" },
  { id: "search", group: "Move", keys: ["/"], title: "search" },
  { id: "move", group: "Move", caught: ["j", "k", "↑", "↓"], title: "move" },
  { id: "top", group: "Move", keys: ["g"], title: "first row" },
  { id: "bottom", group: "Move", keys: ["G"], title: "last row (loads more)" },
  { id: "open", group: "Move", caught: ["Enter"], title: "play or open" },
  { id: "horizontal", group: "Move", caught: ["h", "l", "←", "→"], title: "seek 10 s on Now, or the page's chips" },
  { id: "back", group: "Move", caught: ["Esc"], title: "leave the field → close the picker or menu → back → close" },
  { id: "playPause", group: "Play", caught: ["Space"], title: "play/pause" },
  { id: "playNext", group: "Play", keys: ["n"], title: "play next (a song, album or playlist)" },
  { id: "addToQueue", group: "Play", keys: ["a"], title: "add to queue" },
  { id: "radio", group: "Play", keys: ["R"], title: "start radio from the row or the current song" },
  { id: "shuffle", group: "Play", keys: ["s"], title: "shuffle" },
  { id: "repeat", group: "Play", keys: ["r"], title: "repeat" },
  { id: "like", group: "Song", keys: ["L"], title: "like" },
  { id: "addToPlaylist", group: "Song", keys: ["p"], title: "add to playlist" },
  { id: "artist", group: "Song", keys: ["A"], title: "go to artist (of the row or the current song)" },
  { id: "album", group: "Song", keys: ["o"], title: "go to album" },
  { id: "queueMove", group: "Song", keys: ["J", "K"], title: "move a queued song down / up" },
  { id: "remove", group: "Song", caught: ["x"], title: "remove (queue, own playlist, Liked Music, recent search)" },
  { id: "menu", group: "Menus", keys: ["m", "."], title: "the row's menu (or right-click)" },
  { id: "pageMenu", group: "Menus", keys: ["M"], title: "the page's menu (Library: account)" },
  { id: "order", group: "Menus", keys: ["O"], title: "next sort order on a page that has one" },
  { id: "find", group: "Menus", keys: ["f"], title: "find, on pages that offer it" },
  { id: "help", group: "Menus", keys: ["?"], title: "these keys" }
]

function _keyEntry(id) {
  for (var i = 0; i < KEYS.length; i++) if (KEYS[i].id === id) return KEYS[i]
  return null
}

function _keyLabel(entry) {
  return entry.label || (entry.keys || []).concat(entry.caught || [])
}

// The action id a textKey text is bound to ("" when none).
function keyId(text) {
  for (var i = 0; i < KEYS.length; i++) {
    if ((KEYS[i].keys || []).indexOf(text) >= 0) return KEYS[i].id
  }
  return ""
}

// "L", "m / ." for an id; "" when unknown.
function keyHint(id) {
  var entry = _keyEntry(id)
  return entry ? _keyLabel(entry).join(" / ") : ""
}

// "Like (L)": a tooltip with its key.
function withKey(text, id) {
  var hint = keyHint(id)
  return hint ? text + " (" + hint + ")" : text
}

// Ctrl, Alt and Meta chords are not shortcuts (Shift and the keypad are).
var _CHORD_MODIFIERS = 0x04000000 | 0x08000000 | 0x10000000

function chordAllowed(modifiers) {
  return ((Number(modifiers) || 0) & _CHORD_MODIFIERS) === 0
}

function _capitalized(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : ""
}

// Rows for the ? overlay: a header per group, an action row per key (Enter
// on one runs it).
function keyHelp() {
  var rows = []
  var group = null
  for (var i = 0; i < KEYS.length; i++) {
    var entry = KEYS[i]
    if (entry.group !== group) {
      rows.push({ header: true, title: entry.group })
      group = entry.group
    }
    rows.push({ item: { type: "action", id: "key:" + entry.id, title: _capitalized(entry.title), icon: "󰌌", hint: keyHint(entry.id) } })
  }
  return rows
}

var KEYS_NOTE = "Shortcuts are off while typing, and Ctrl or Alt chords do nothing. Back returns to the row and scroll position you left."

// README's Panel keys cell.
function keysReadme() {
  return KEYS.map(function(entry) {
    return _keyLabel(entry).map(function(k) { return "`" + k + "`" }).join(" / ") + " " + entry.title
  }).join(" · ") + ". " + KEYS_NOTE
}

// Panel.qml's header comment lines, wrapped at `width` characters.
function keysComment(width) {
  var max = width || 78
  var words = ("Keys (Model.KEYS; ? lists them): " + KEYS.map(function(entry) {
    return _keyLabel(entry).join(" / ") + " " + entry.title
  }).join(" · ") + ".").split(" ")
  var lines = []
  var line = "//"
  for (var i = 0; i < words.length; i++) {
    if (line.length + 1 + words[i].length > max && line !== "//") {
      lines.push(line)
      line = "//"
    }
    line += " " + words[i]
  }
  lines.push(line)
  return lines
}

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    MUSIC_URL: MUSIC_URL,
    QUEUE_CAP: QUEUE_CAP,
    isMixId: isMixId,
    menuKind: menuKind,
    itemTitle: itemTitle,
    canQueue: canQueue,
    tracksForParams: tracksForParams,
    radioParams: radioParams,
    completeTracks: completeTracks,
    shareUrl: shareUrl,
    linkedArtists: linkedArtists,
    albumOf: albumOf,
    albumPlaylistOf: albumPlaylistOf,
    capWindow: capWindow,
    queueCapNotice: queueCapNotice,
    capNotice: capNotice,
    playNextAllCommands: playNextAllCommands,
    MENU_ACTIONS: MENU_ACTIONS,
    menuActions: menuActions,
    menuAction: menuAction,
    actionRows: actionRows,
    KEYS: KEYS,
    keyId: keyId,
    keyHint: keyHint,
    withKey: withKey,
    chordAllowed: chordAllowed,
    keyHelp: keyHelp,
    keysReadme: keysReadme,
    keysComment: keysComment
  })
}

// ==== now (S11) ===========================================================================
// The Now tab's pane under the song playing: Queue, Lyrics or Related (y or
// the queue header's pane chip cycles them; prefs.nowPane remembers it). The
// service reads one nowContext per track while a panel is open (like status,
// lyrics and Related ids, the song/video twin, queue chips, "Playing from",
// the up-next list) and the Store keeps it in a memo per video, which S12
// reuses for autoplay. Credits and Related open as pages; lyrics lines are
// highlighted from mpv's time-pos.

var NOW_PANES = ["queue", "lyrics", "related"]
var NOW_PANE_TITLES = { queue: "Queue", lyrics: "Lyrics", related: "Related" }
var NOW_PANE_ICONS = { queue: "󰲸", lyrics: "󰦪", related: "󰌳" }
// The nowContext memo: how long a reply is reused, and how many songs.
var NOW_MEMO_TTL_MS = 10 * 60 * 1000
var NOW_MEMO_MAX = 20

function nowPane(value) {
  return NOW_PANES.indexOf(value) >= 0 ? value : "queue"
}

// The pane after `pane` (before it with a negative delta), round the cycle.
function nextNowPane(pane, delta) {
  var i = NOW_PANES.indexOf(nowPane(pane))
  var step = delta < 0 ? -1 : 1
  return NOW_PANES[(i + step + NOW_PANES.length) % NOW_PANES.length]
}

function nowPaneTitle(pane) {
  return NOW_PANE_TITLES[nowPane(pane)]
}

function nowPaneIcon(pane) {
  return NOW_PANE_ICONS[nowPane(pane)]
}

// The synced line to highlight at `pos` seconds: the last one that has
// started (lines are sorted by start), -1 before the first or without lines.
function currentLineIndex(lines, pos) {
  var list = lines || []
  var t = Number(pos)
  if (list.length === 0 || !isFinite(t)) return -1
  var lo = 0
  var hi = list.length - 1
  var found = -1
  while (lo <= hi) {
    var mid = (lo + hi) >> 1
    if (Number(list[mid].start) <= t) {
      found = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return found
}

// A memo keyed by video: key -> { value, at }. Copy on write (bindings
// follow); `max` entries at most, the most recently stored kept.
function nowMemoPut(memo, key, value, now, max) {
  var source = memo || {}
  var limit = max > 0 ? max : NOW_MEMO_MAX
  var keys = Object.keys(source).filter(function(k) { return k !== key })
  keys.sort(function(a, b) { return source[b].at - source[a].at })
  var out = {}
  for (var i = 0; i < keys.length && i < limit - 1; i++) out[keys[i]] = source[keys[i]]
  out[key] = { value: value, at: now }
  return out
}

// The value stored for `key`, or null when there is none or it is older
// than `ttlMs` (no ttlMs: whatever its age).
function nowMemoGet(memo, key, now, ttlMs) {
  var entry = memo && key ? memo[key] : null
  if (!entry) return null
  return !(ttlMs > 0) || now - entry.at < ttlMs ? entry.value : null
}

// `memo` with `key` used `now` (an LRU's order and age go by it); the same
// object when it has no such entry.
function nowMemoTouch(memo, key, now) {
  var source = memo || {}
  if (!key || !source[key]) return source
  var out = {}
  for (var k in source) out[k] = source[k]
  out[key] = { value: source[key].value, at: now }
  return out
}

// `memo` without the entries older than `ttlMs`, except `keep` (the song
// playing); the same object when nothing went.
function nowMemoSweep(memo, now, ttlMs, keep) {
  var source = memo || {}
  var out = null
  for (var key in source) {
    if (key === keep || now - source[key].at < ttlMs) continue
    if (!out) {
      out = {}
      for (var k in source) out[k] = source[k]
    }
    delete out[key]
  }
  return out || source
}

// The credits page of a song (its menu's View credits).
function creditsTarget(track) {
  if (!track || !track.videoId) return null
  return { type: "credits", videoId: track.videoId, title: track.title || "", thumb: track.thumb || null,
           thumbLarge: track.thumbLarge || null }
}

// The Related page of a song, by the nowContext's relatedId.
function relatedTarget(relatedId) {
  return relatedId ? { type: "related", browseId: relatedId, title: "Related" } : null
}

// Credits: one section per role. Related: the web's Related tab, also the
// Now tab's Related pane.
PAGE_KINDS.credits = {
  method: "credits", title: "Credits", round: false,
  params: function(t) { return t.videoId ? { videoId: t.videoId } : null },
  id: function(t) { return t.videoId }
}
PAGE_KINDS.related = {
  method: "related", title: "Related", round: false,
  params: function(t) { return t.browseId ? { browseId: t.browseId } : null },
  id: function(t) { return t.browseId }
}

// Where MENU_ACTIONS has `id` (its end when it has none).
function nowIndexOfAction(id) {
  for (var i = 0; i < MENU_ACTIONS.length; i++) if (MENU_ACTIONS[i].id === id) return i
  return MENU_ACTIONS.length
}

// Songs have credits; music videos and episodes have none. Listed before
// Copy link, as on the web.
MENU_ACTIONS.splice(nowIndexOfAction("share"), 0, { id: "credits", title: "View credits", icon: "󰗒",
  applies: function(item, ctx) {
    return menuKind(item) === "track" && item.kind !== "video" && item.kind !== "episode" && !ctx.page
  },
  run: function(env) { env.panel.push(creditsTarget(env.item)) } })

KEYS.push({ id: "nowPane", group: "Now", keys: ["y"], title: "cycle the pane under the song: Queue, Lyrics, Related" })

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    NOW_PANES: NOW_PANES,
    NOW_MEMO_TTL_MS: NOW_MEMO_TTL_MS,
    NOW_MEMO_MAX: NOW_MEMO_MAX,
    nowPane: nowPane,
    nextNowPane: nextNowPane,
    nowPaneTitle: nowPaneTitle,
    nowPaneIcon: nowPaneIcon,
    currentLineIndex: currentLineIndex,
    nowMemoPut: nowMemoPut,
    nowMemoGet: nowMemoGet,
    nowMemoTouch: nowMemoTouch,
    nowMemoSweep: nowMemoSweep,
    creditsTarget: creditsTarget,
    relatedTarget: relatedTarget
  })
}
