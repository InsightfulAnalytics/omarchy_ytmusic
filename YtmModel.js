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
// S12: nor in `seen` (songs played and trimmed out of the queue; an array or
// a map), nor the song/video twin of a song that is.
function autoplayPicks(queue, candidates, max, seen) {
  var known = _knownIds(queue, seen)  // S12
  var songs = []
  var videos = []
  for (var j = 0; j < (candidates || []).length; j++) {
    var track = candidates[j]
    if (!playable(track) || known[track.videoId] || _twinKnown(track, known)) continue
    _markKnown(track, known)
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
  // S17: on the 0.25 step grid (speedStep, the speed chooser).
  return typeof value === "number" && isFinite(n) && n >= 0.5 && n <= 2 ? Math.round(n * 4) / 4 : 1
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

// The first playable track among result rows (a fresh search's likely pick).
function firstTrack(rows) {
  for (var i = 0; i < (rows || []).length; i++) {
    var row = rows[i]
    if (!row.header && row.item && row.item.type === "track" && playable(row.item)) return row.item
  }
  return null
}

// Next row a keyboard cursor may rest on (headers, message rows and text
// rows other than paragraphs are skipped). From -1, moving down lands on the
// first row; at either end the cursor stays put.
function nextSelectable(rows, index, delta) {
  var count = (rows || []).length
  var step = delta < 0 ? -1 : 1
  var i = index < 0 ? (step > 0 ? -1 : count) : index
  for (var j = i + step; j >= 0 && j < count; j += step) {
    if (selectableRow(rows[j])) return j
  }
  return index
}

// A text row ({type: "text", title, icon?}) is read-only page content, such
// as a credited name or About the artist (S11): the cursor skips it. A text
// row with `paragraph: true` (a description's paragraph: an artist's About
// page, a collection's About rows) takes the cursor so the keyboard can
// scroll through a long text; Enter and the menu do nothing there.
function selectableRow(row) {
  if (!row || row.header || !row.item || row.item.type === "message") return false
  return row.item.type !== "text" || row.item.paragraph === true
}

function joinParts(parts) {
  return parts.filter(function(part) { return part !== null && part !== undefined && String(part) !== "" }).join(" • ")
}

function itemSubtitle(item) {
  if (!item) return ""
  if (item.type === "track") return joinParts([item.artistText, item.kind === "video" ? "Video" : ""])
  if (item.type === "album") return joinParts([item.albumType, item.artistText, item.year])
  if (item.type === "playlist")
    return joinParts([item.author, item.count ? item.count + " songs" : ""]) || (item.description ? String(item.description) : "")
  if (item.type === "artist") return item.subtitle ? item.subtitle : "Artist"
  if (item.type === "liked") return "Songs you liked"
  if (item.type === "history") return "Recently played"
  return item.subtitle ? String(item.subtitle) : ""
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

// libraryRows(chip, data, playlists), the Library list: see the library (S2) section.

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
//   view          optional: a name that keeps this kind's pages apart from
//                 another kind's on the same method and params (S1's about
//                 page reads the artist method)
//   subtitle(p)   optional: the header subtitle of a page p (S1)
//   partial       optional: the page shows only a sample of its songs, so
//                 playing it reads the whole list (tracksFor; S1 artist)
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
    key: spec.method + (spec.view ? "/" + spec.view : "") + ":" + canonicalJson(params),
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
  if (kind === "artist") return item.radioId ? { mixId: item.radioId } : { channelId: item.channelId }
  return null
}

// The tracks of a collection the Store holds, when it holds all of them
// (null for part of a list, or an artist page's few songs).
function completeTracks(data) {
  if (!data || typeof data !== "object") return null
  if (data.type === "page") return data.cursor || data.hasMore || data.capped || pagePartial(data.kind) ? null : pageTracks(data)
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
  if (item && item.type === "link") return linkUrl(item.url)
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
    run: function(env) {
      // An artist row that carries YouTube's shuffle mix (S2: library artists) plays it.
      var mix = env.ctx && env.ctx.page ? null : artistMix(env.item, "shuffleId")
      if (mix) env.service.playItem(mix, "now")
      else _playMode("shuffle")(env)
    } },
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
  { id: "bottom", group: "Move", keys: ["G"], title: "last row (loads more), or a long list's Load all row" },
  { id: "open", group: "Move", caught: ["Enter"], title: "play or open" },
  { id: "horizontal", group: "Move", caught: ["h", "l", "←", "→"], title: "seek 10 s on Now, or the page's chips" },
  { id: "back", group: "Move", caught: ["Esc"], title: "leave the field → close the picker or menu → clear the filter → back → close" },
  { id: "playPause", group: "Play", caught: ["Space"], title: "play/pause" },
  { id: "playNext", group: "Play", keys: ["n"], title: "play next (a song, album or playlist)" },
  { id: "addToQueue", group: "Play", keys: ["a"], title: "add to queue" },
  { id: "radio", group: "Play", keys: ["R"], title: "start radio from the row, the artist page or the current song" },
  { id: "shuffle", group: "Play", keys: ["s"], title: "shuffle" },
  { id: "repeat", group: "Play", keys: ["r"], title: "repeat" },
  { id: "like", group: "Song", keys: ["L"], title: "like" },
  { id: "addToPlaylist", group: "Song", keys: ["p"], title: "add to playlist" },
  { id: "artist", group: "Song", keys: ["A"], title: "go to artist (of the row, the album page or the current song)" },
  { id: "album", group: "Song", keys: ["o"], title: "go to album" },
  { id: "queueMove", group: "Song", keys: ["J", "K"], title: "move a queued song down / up" },
  { id: "remove", group: "Song", caught: ["x"], title: "remove (queue, own playlist, Liked Music, recent search)" },
  { id: "menu", group: "Menus", keys: ["m", "."], title: "the row's menu (or right-click)" },
  { id: "pageMenu", group: "Menus", keys: ["M"], title: "the page's menu (Library: account)" },
  { id: "order", group: "Menus", keys: ["O"], title: "next sort order on a page that has one" },
  { id: "find", group: "Menus", keys: ["f"], title: "find in a page's songs (album, playlist, Liked Music, History)" },
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

// ==== artist (S1) ==========================================================================
// Artist pages are sectioned pages (the worker's `artist` method): every
// shelf of the web page in its served order, each ending in its See all.
// An artist's albums, singles and shows open as `artistAlbums` (the whole
// list, about 100 a page with Load more, and the served sort orders), and
// the header's About as an `about` page built from the artist page itself.
// Shuffle plays YouTube's shuffle mix and Radio (or R) its radio mix; Play
// and Queue all read the artist's whole songs playlist (tracksFor), not the
// few songs shown.

PAGE_KINDS.artist.partial = true
PAGE_KINDS.artist.subtitle = function(page) { return artistSubtitle(page) }

// See all of an artist's albums, singles or shows (MPAD... + params).
// Its orders' `index` is the served option title, sent back as `order`.
PAGE_KINDS.artistAlbums = {
  method: "artistAlbums", title: "Releases", round: false,
  params: function(t) { return t.browseId && t.params ? { browseId: t.browseId, params: t.params } : null },
  id: function(t) { return t.browseId }
}

// The artist's description and links: the artist page (the worker's disk
// copy answers at once) shown another way.
PAGE_KINDS.about = {
  method: "artist", view: "about", title: "About", round: true,
  params: function(t) { return t.channelId ? { channelId: t.channelId } : null },
  id: function(t) { return t.channelId },
  adapt: function(result) { return aboutPage(result) }
}

// A discography page needs its params; without them (and channel + params,
// MPLA + params) the endpoint falls through to a browse page.
ROUTES.push({ prefix: "MPAD", when: function(ep) { return !!ep.params },
  target: function(ep, id) { return { type: "artistAlbums", browseId: id, params: ep.params, title: ep.title || "" } } })

function pagePartial(kind) {
  return Object.prototype.hasOwnProperty.call(PAGE_KINDS, kind) && PAGE_KINDS[kind].partial === true
}

// The header subtitle of a page: its kind's own (an artist's audience),
// else the page's.
function pageSubtitle(ref, page) {
  var spec = pageKind(ref)
  if (spec && typeof spec.subtitle === "function" && page && page.type === "page") return String(spec.subtitle(page) || "")
  return page && page.subtitle ? String(page.subtitle) : ""
}

function artistHeader(page) {
  return page && page.type === "page" && page.kind === "artist" && page.header ? page.header : null
}

// "205M monthly listeners, 5.46M subscribers".
function artistSubtitle(page) {
  var header = artistHeader(page) || {}
  var parts = []
  if (header.monthlyListeners) parts.push(header.monthlyListeners + " monthly listeners")
  if (header.subscribers) parts.push(header.subscribers + " subscribers")
  return parts.length ? parts.join(", ") : "Artist"
}

// What the header's Shuffle plays: YouTube's shuffle mix (RDAO...), as a
// mix item for Service.playItem. Null without one.
function artistShuffleItem(page) {
  var header = artistHeader(page)
  if (!header || !header.shuffleId) return null
  return { type: "playlist", playlistId: header.shuffleId, title: page.title || "" }
}

// What the header's Radio starts: the artist's radio mix (RDEM...), as a
// mix item for Service.startRadio. Null without one.
function artistRadioItem(page) {
  var header = artistHeader(page)
  if (!header || !header.radioId) return null
  return { type: "playlist", playlistId: header.radioId, title: page.title || "" }
}

// The description links of an artist page, once each: { url, text }.
function _artistLinks(header) {
  var seen = {}
  var out = []
  var runs = header && header.descriptionRuns ? header.descriptionRuns : []
  for (var i = 0; i < runs.length; i++) {
    var url = linkUrl(runs[i] && runs[i].url)
    if (url === "" || seen[url]) continue
    seen[url] = true
    out.push({ url: url, text: String(runs[i].text || url) })
  }
  return out
}

function hasAbout(page) {
  var header = artistHeader(page)
  return !!header && (!!header.description || _artistLinks(header).length > 0)
}

// The item the header's About opens (an `about` page).
function aboutItem(page) {
  var header = artistHeader(page)
  if (!header || !header.channelId) return null
  return { type: "about", channelId: header.channelId, title: page.title || "", thumb: page.thumb || null,
           thumbLarge: page.thumbLarge || null }
}

// http(s) URLs only; "" for anything else.
function linkUrl(url) {
  var text = String(url || "")
  return /^https?:\/\/[^\s]+$/i.test(text) ? text : ""
}

// The about page of an artist page: the description in paragraphs (text
// rows, which wrap in full and take the cursor so the keyboard can scroll
// them) and a row per link, which copies it.
function aboutPage(result) {
  var header = artistHeader(result) || {}
  var paragraphs = String(header.description || "").split(/\n\s*\n/).map(function(text) { return text.trim() })
    .filter(function(text) { return text !== "" })
  var text = paragraphs.map(function(paragraph) { return { type: "text", title: paragraph, paragraph: true } })
  if (text.length === 0) text.push({ type: "message", title: "No description" })
  var links = _artistLinks(header).map(function(link) {
    return { type: "action", id: "link:" + link.url, title: link.url, icon: "󰌷", url: link.url }
  })
  return {
    type: "page", v: 2, kind: "about", id: result && result.id ? result.id : null,
    title: result && result.title ? result.title : "", subtitle: header.views || null,
    thumb: result ? result.thumb || null : null, thumbLarge: result ? result.thumbLarge || null : null, round: true,
    header: {}, sections: [
      { type: "section", title: null, items: text, more: null, play: null, layout: null, key: "description" },
      { type: "section", title: "Links", items: links, more: null, play: null, layout: null, key: "links" }
    ],
    cursor: null, hasMore: false, orders: [], order: null, message: null
  }
}

// M on an artist page: About, the header button's twin. (Play, Shuffle,
// Queue all and Start radio are the base entries; Shuffle goes to the
// page's playAll, which plays the shuffle mix.)
MENU_ACTIONS.push({ id: "about", title: "About", icon: "󰋽",
  applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "artist" && ctx.about === true },
  run: function(env) { if (env.owner && typeof env.owner.openAbout === "function") env.owner.openAbout() } })

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    pagePartial: pagePartial,
    pageSubtitle: pageSubtitle,
    artistHeader: artistHeader,
    artistSubtitle: artistSubtitle,
    artistShuffleItem: artistShuffleItem,
    artistRadioItem: artistRadioItem,
    hasAbout: hasAbout,
    aboutItem: aboutItem,
    linkUrl: linkUrl,
    aboutPage: aboutPage
  })
}

// ==== library (S2) ==========================================================================
// The Library tab's chips: Playlists (the default), Songs, Albums and
// Artists (S8 and S10 push Podcasts and Uploads), switched with h / l and
// kept in prefs.libraryChip. A chip's list is its worker method's, fetched
// only once the chip is shown and read about 50 at a time (Store's
// loadLibraryList / loadLibraryMore). O cycles a chip's sort orders, kept
// per chip in prefs.libraryOrders as the worker's ids: a_to_z, z_to_a,
// recently_added, or menu:<n> for an option only the served menu has (Most
// songs). Recent activity (the library landing) and Subscriptions open as
// pages, their orders shown as chips there.

var LIBRARY_CHIPS = [
  { key: "playlists", title: "Playlists", method: "libraryPlaylists" },
  { key: "songs", title: "Songs", method: "librarySongs" },
  { key: "albums", title: "Albums", method: "libraryAlbums" },
  { key: "artists", title: "Artists", method: "libraryArtists" }
]
// The Songs chip's first row: YouTube's shuffle of the whole library.
var LIBRARY_SHUFFLE = "library:shuffle"
var LIBRARY_SONGS_TITLE = "Library songs"

function libraryChipSpec(key) {
  for (var i = 0; i < LIBRARY_CHIPS.length; i++) if (LIBRARY_CHIPS[i].key === key) return LIBRARY_CHIPS[i]
  return null
}

// A chip that exists (a saved one a newer plugin added may not).
function libraryChip(key) {
  return libraryChipSpec(key) ? key : LIBRARY_CHIPS[0].key
}

// h / l: the chip before or after, wrapping around.
function nextLibraryChip(key, delta) {
  var at = 0
  for (var i = 0; i < LIBRARY_CHIPS.length; i++) if (LIBRARY_CHIPS[i].key === key) at = i
  var n = LIBRARY_CHIPS.length
  return LIBRARY_CHIPS[((at + (delta < 0 ? -1 : 1)) % n + n) % n].key
}

// prefs.libraryOrders[chip], or null for the order YouTube has saved.
function libraryOrderOf(orders, chip) {
  var value = orders && typeof orders === "object" ? orders[chip] : undefined
  return value === undefined || value === null ? null : value
}

// A copy of prefs.libraryOrders with chip's order set (null removes it).
function withLibraryOrder(orders, chip, order) {
  var next = {}
  for (var key in orders || {}) next[key] = orders[key]
  if (order === null || order === undefined) delete next[chip]
  else next[chip] = order
  return normalizePref("libraryOrders", next)
}

function sameOrder(a, b) {
  return canonicalJson(a === undefined ? null : a) === canonicalJson(b === undefined ? null : b)
}

function _orderAt(orders, current) {
  var at = -1
  for (var i = 0; i < orders.length; i++) if (sameOrder(orders[i].index, current)) at = i
  if (at < 0) for (var j = 0; j < orders.length; j++) if (orders[j].selected) at = j
  return at
}

// O: the order after `current` among `orders` ({ index, title, selected }),
// wrapping around; null when there is nothing to cycle.
function nextOrder(orders, current) {
  var list = orders && typeof orders.length === "number" ? orders : []
  if (list.length < 2) return null
  return list[(_orderAt(list, current) + 1) % list.length].index
}

// The served title of the order a list is in ("" when unknown).
function orderTitle(orders, current) {
  var list = orders && typeof orders.length === "number" ? orders : []
  var at = _orderAt(list, current)
  return at >= 0 ? String(list[at].title || "") : ""
}

// A worker reply (a page, or libraryPlaylists' { items }) as the flat list
// the Store keeps for a chip.
function libraryList(result) {
  var r = result && typeof result === "object" ? result : {}
  var items = []
  if (r.items && typeof r.items.length === "number") items = _validItems(r.items)
  for (var s = 0; r.sections && s < r.sections.length; s++) items = items.concat(_validItems((r.sections[s] || {}).items))
  return {
    items: items, cursor: r.cursor || null, hasMore: !!(r.cursor || r.hasMore), capped: false,
    orders: r.orders && typeof r.orders.length === "number" ? r.orders : [],
    current: r.order === undefined ? null : r.order,
    header: r.header && typeof r.header === "object" ? r.header : {},
    message: r.message ? String(r.message) : null
  }
}

// `list` with the next cursor page's items appended (Model.appendPage: no
// repeated album, artist or playlist, at most `max` items).
function appendLibraryList(list, next, max) {
  var merged = appendPage({ items: list.items || [], cursor: list.cursor || null },
                          { items: (next && next.items) || [], cursor: next ? next.cursor || null : null }, max)
  var out = {}
  for (var key in list) out[key] = list[key]
  out.items = merged.items
  out.cursor = merged.cursor
  out.hasMore = merged.hasMore
  out.capped = merged.capped === true
  return out
}

// The songs a list holds, in the order trackIndex counts them.
function libraryTracks(list) {
  return ((list && list.items) || []).filter(function(item) { return item && item.type === "track" })
}

// Shuffle all on the Songs chip: the server shuffle as a playable item
// (tracksFor reads it; the worker routes its id).
function libraryShuffleItem(list) {
  var id = list && list.header ? list.header.shuffleId : null
  return id ? { type: "playlist", playlistId: String(id), title: LIBRARY_SONGS_TITLE } : null
}

// YouTube's shuffle (shuffleId) or radio (radioId) mix of an artist row
// (library artists and subscriptions carry both), as a mix item; else null.
function artistMix(item, key) {
  if (!item || item.type !== "artist" || !item[key]) return null
  return { type: "playlist", playlistId: String(item[key]), title: String(item.name || item.title || "") }
}

var _LIBRARY_ENTRIES = {
  landing: { type: "libraryLanding", title: "Recent activity", subtitle: "What you played and saved lately", thumb: null },
  subscriptions: { type: "librarySubscriptions", title: "Subscriptions", subtitle: "Artists you subscribe to", thumb: null }
}

function _entry(name) {
  var out = {}
  for (var key in _LIBRARY_ENTRIES[name]) out[key] = _LIBRARY_ENTRIES[name][key]
  return out
}

// The Library list for a chip. `data` is the chip's list (libraryList's
// shape; for Playlists only its orders and current), `playlists` the
// library's playlists. Playlists: Liked Music, History and Recent activity,
// then your playlists and saved ones (Liked Music and Episodes for later
// have rows of their own). Songs: Shuffle all, then the songs (Enter plays
// the loaded ones from there). Artists: Subscriptions, then the artists.
// The first heading names the order ("Albums, A to Z"); a list with more
// ends in Load more.
function libraryRows(chip, data, playlists) {
  var list = data || {}
  var label = orderTitle(list.orders, list.current)
  var titled = false
  var rows = []
  function heading(title) {
    var text = !titled && label ? title + ", " + label : title
    titled = true
    rows.push({ header: true, title: text })
  }
  if (chip === "playlists") {
    rows.push({ item: { type: "liked", title: "Liked Music", thumb: null } },
              { item: { type: "history", title: "History", thumb: null } },
              { item: _entry("landing") })
    var owned = []
    var saved = []
    for (var i = 0; i < (playlists || []).length; i++) {
      var p = playlists[i]
      if (isSpecialPlaylist(p)) continue
      if (p.owned) owned.push(p)
      else saved.push(p)
    }
    if (owned.length) heading("Your playlists")
    for (var j = 0; j < owned.length; j++) rows.push({ item: owned[j] })
    if (saved.length) heading("Saved playlists")
    for (var k = 0; k < saved.length; k++) rows.push({ item: saved[k] })
    return rows
  }
  var spec = libraryChipSpec(chip)
  var items = _validItems(list.items)
  if (chip === "artists") rows.push({ item: _entry("subscriptions") })
  if (chip === "songs" && libraryShuffleItem(list))
    rows.push({ item: { type: "action", id: LIBRARY_SHUFFLE, title: "Shuffle all", icon: "󰒝" } })
  if (items.length) heading(spec ? spec.title : chip)
  var trackIndex = 0
  for (var n = 0; n < items.length; n++) {
    var row = { item: items[n] }
    if (items[n].type === "track") row.trackIndex = trackIndex++
    rows.push(row)
  }
  if (list.message) rows.push({ item: { type: "message", title: String(list.message) } })
  if (list.capped) rows.push({ item: { type: "message", title: "Showing the first " + items.length } })
  else if (list.cursor || list.hasMore) rows.push({ item: { type: "loadMore", title: "Load more" } })
  return rows
}

// Recent activity: the library landing grid (playlists, albums, artists),
// in its served orders (Recent activity, Recently saved, Recently played).
PAGE_KINDS.libraryLanding = {
  method: "libraryLanding", title: "Recent activity", round: false,
  params: function() { return {} },
  id: function() { return "landing" }
}

// The artists you subscribe to.
PAGE_KINDS.librarySubscriptions = {
  method: "librarySubscriptions", title: "Subscriptions", round: false,
  params: function() { return {} },
  id: function() { return "subscriptions" }
}

// Liked Music listed as a playlist (Recent activity, Home) opens as Liked Music.
PAGE_KINDS.liked.claims = function(item) {
  return item.type === "playlist" && _stripVl(item.playlistId) === "LM"
}

// A user channel (Recent activity, Subscriptions) opens as its browse page
// until S9 gives profiles a page of their own.
if (!PAGE_KINDS.profile) {
  PAGE_KINDS.profile = {
    method: "browsePage", title: "Profile", round: true,
    params: function(t) { return t.channelId ? { browseId: t.channelId } : null },
    id: function(t) { return t.channelId }
  }
}

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    LIBRARY_CHIPS: LIBRARY_CHIPS,
    LIBRARY_SHUFFLE: LIBRARY_SHUFFLE,
    libraryChipSpec: libraryChipSpec,
    libraryChip: libraryChip,
    nextLibraryChip: nextLibraryChip,
    libraryOrderOf: libraryOrderOf,
    withLibraryOrder: withLibraryOrder,
    sameOrder: sameOrder,
    nextOrder: nextOrder,
    orderTitle: orderTitle,
    libraryList: libraryList,
    appendLibraryList: appendLibraryList,
    libraryTracks: libraryTracks,
    libraryShuffleItem: libraryShuffleItem,
    artistMix: artistMix
  })
}

// ==== collections (S3) ======================================================================
// Albums, playlists, mixes, Liked Music and History (the worker's
// `collection` shape). A playlist or Liked Music first shows 200 to 300
// songs; when it has more (`truncated`), its songs end in a selectable
// "Load all N songs" row that reloads it with limit "all" (every song, up
// to 5000) and keeps the cursor. Under the songs come the page's shelves
// (an album's other versions and recommendations, a playlist's related
// playlists). The header shows the byline (album artists and a playlist's
// author as links), the details and two lines of the description; an
// About row at the top shows all of it. f filters the songs (title,
// artists, album; case and accents ignored). A mix (RD..., not RDCLAK or
// RDPN) opens as the watch playlist it plays.

// Playlists come with their related playlists; History with all YouTube
// serves (200).
PAGE_KINDS.playlist.params = function(t) { return t.playlistId ? { playlistId: t.playlistId, related: true } : null }
PAGE_KINDS.history.params = function() { return { limit: 200 } }

PAGE_KINDS.mix = {
  method: "playlist", title: "Mix", round: false,
  claims: function(item) { return item.type === "playlist" && isMixId(item.playlistId) },
  params: function(t) { return t.playlistId ? { playlistId: _stripVl(t.playlistId) } : null },
  id: function(t) { return _stripVl(t.playlistId) }
}

var LOAD_ALL_ID = "loadAll"
var COLLECTION_ABOUT_ID = "collection:about"
// Track fields that carry account-bound tokens: kept in memory for the row
// menus, never in what the shell saves or prints (Service.remember).
var TOKEN_KEYS = ["libraryTokens", "listenAgainTokens", "historyToken"]

// The params a page is asked with beyond its kind's own: its sort order
// and, once Load all was chosen, limit "all". Null when neither.
function pageExtra(order, all) {
  var extra = null
  if (order !== null && order !== undefined) extra = { order: order }
  if (all === true) {
    extra = extra || {}
    extra.limit = "all"
  }
  return extra
}

function isCollection(page) {
  return !!page && page.type === "collection"
}

// "1,126"
function groupedCount(n) {
  return String(Math.max(0, Math.floor(Number(n) || 0))).replace(/\B(?=(\d{3})+(?!\d))/g, ",")
}

function songsText(n) {
  return Number(n) === 1 ? "1 song" : groupedCount(n) + " songs"
}

// The page has more songs than it shows and Load all can bring them.
function canLoadAll(page) {
  return isCollection(page) && page.truncated === true && page.capped !== true
}

// The page can be filtered with f: a page of songs.
function canFind(page) {
  return isCollection(page) && (page.tracks || []).length > 0
}

function loadAllTitle(page) {
  return "Load all " + (page && page.trackCount ? songsText(page.trackCount) : "songs")
}

function collectionHeader(page) {
  return isCollection(page) && page.header && typeof page.header === "object" ? page.header : {}
}

function collectionDescription(page) {
  var text = collectionHeader(page).description
  return typeof text === "string" ? text.trim() : ""
}

// The header's second line as parts { text, item? }: an album's type,
// artists (each with a page is a link) and year; a playlist's author (a
// link to their channel) or its collaborators; "Mix" for a mix.
function collectionByline(page) {
  if (!isCollection(page)) return []
  var header = collectionHeader(page)
  var groups = []
  if (page.kind === "album") {
    if (header.type) groups.push([{ text: String(header.type) }])
    var names = []
    var list = header.artists && typeof header.artists.length === "number" ? header.artists : []
    for (var i = 0; i < list.length; i++) {
      if (!list[i] || !list[i].name) continue
      if (names.length) names.push({ text: ", " })
      names.push(list[i].id ? { text: String(list[i].name), item: { type: "artist", channelId: list[i].id, name: list[i].name, thumb: null } }
                            : { text: String(list[i].name) })
    }
    if (names.length) groups.push(names)
    if (header.year) groups.push([{ text: String(header.year) }])
  } else if (page.kind === "playlist") {
    var author = header.author
    if (header.collaborators) groups.push([{ text: String(header.collaborators) }])
    else if (author && author.name)
      groups.push([String(author.id || "").indexOf("UC") === 0
        ? { text: String(author.name), item: { type: "profile", channelId: author.id, name: author.name } }
        : { text: String(author.name) }])
  } else if (page.kind === "mix") {
    groups.push([{ text: "Mix" }])
  }
  var parts = []
  for (var g = 0; g < groups.length; g++) {
    if (g > 0) parts.push({ text: " • " })
    parts = parts.concat(groups[g])
  }
  return parts
}

// The header's details: your playlist's privacy, the song count, length,
// views and year, how many songs are unavailable, and how many are shown
// while there are more.
function collectionMeta(page, shown) {
  if (!isCollection(page)) return ""
  var header = collectionHeader(page)
  var privacy = header.privacy ? String(header.privacy).charAt(0) + String(header.privacy).slice(1).toLowerCase() : ""
  var playlist = page.kind === "playlist"
  return joinParts([
    privacy,
    page.trackCount ? songsText(page.trackCount) : "",
    header.duration || "",
    playlist && header.views ? groupedCount(header.views) + (header.views === 1 ? " view" : " views") : "",
    playlist ? header.year || "" : "",
    page.unavailable > 0 ? groupedCount(page.unavailable) + " unavailable" : "",
    page.truncated === true ? "showing " + groupedCount(shown === undefined ? (page.tracks || []).length : shown) : ""
  ])
}

// ---- find --------------------------------------------------------------------------------

// Lower case without accents ("Beyoncé" finds "beyonce").
function foldText(text) {
  var s = String(text === undefined || text === null ? "" : text).toLowerCase()
  if (typeof s.normalize === "function") s = s.normalize("NFD").replace(/[̀-ͯ]/g, "")
  return s
}

function _findText(track) {
  if (!track || typeof track !== "object") return ""
  var artists = track.artistText || ""
  if (!artists && track.artists && typeof track.artists.length === "number")
    artists = Array.prototype.map.call(track.artists, function(a) { return a && a.name ? a.name : "" }).join(" ")
  return foldText([track.title || "", artists, track.album && track.album.name ? track.album.name : ""].join("\n"))
}

// Indexes of the tracks every word of `text` matches (title, artists or
// album), in order; every index for an empty text.
function _findIndexes(tracks, text) {
  var list = tracks || []
  var words = foldText(text).split(/\s+/).filter(function(w) { return w !== "" })
  var out = []
  for (var i = 0; i < list.length; i++) {
    var hay = words.length ? _findText(list[i]) : ""
    var all = true
    for (var w = 0; w < words.length && all; w++) all = hay.indexOf(words[w]) >= 0
    if (all) out.push(i)
  }
  return out
}

function filterTracks(tracks, text) {
  var list = tracks || []
  return _findIndexes(list, text).map(function(i) { return list[i] })
}

// The songs matching `text` as rows; trackIndex still points into the
// whole list, so Enter plays the page from that song. History keeps its
// groups. A message row when nothing matches.
function findRows(tracks, text, grouped) {
  var list = tracks || []
  var rows = []
  var group = null
  var hits = _findIndexes(list, text)
  for (var h = 0; h < hits.length; h++) {
    var i = hits[h]
    if (grouped) {
      var label = list[i].played || "Earlier"
      if (label !== group) {
        rows.push({ header: true, title: label })
        group = label
      }
    }
    rows.push({ item: list[i], trackIndex: i })
  }
  if (rows.length === 0) rows.push({ item: { type: "message", title: "No songs match \"" + String(text).trim() + "\"" } })
  return rows
}

// ---- rows ----------------------------------------------------------------------------------

// The description in paragraphs (one per line).
function descriptionParagraphs(text) {
  return String(text || "").split(/\n+/).map(function(p) { return p.trim() }).filter(function(p) { return p !== "" })
}

// The About row is shown (and M offers About): the header cut the
// description short (opts.about) and no filter is on (opts.find).
function showsAbout(page, opts) {
  var o = opts || {}
  return o.about === true && String(o.find || "").trim() === "" && collectionDescription(page) !== ""
}

// A collection page as rows. opts: { history (group by played), find (the
// filter's text), about (the header cut the description short: an About
// row shows it), aboutOpen }. While finding, only the matching songs.
// Otherwise the songs, Load all while there are more ("Showing the first
// N" once a whole read stopped at its cap), then the page's shelves
// (pageRows: a header, the items, See all).
function collectionRows(page, opts) {
  var o = opts || {}
  var rows = []
  if (!page) return rows
  var tracks = page.tracks || []
  var finding = String(o.find || "").trim() !== ""
  var description = collectionDescription(page)
  if (showsAbout(page, o)) {
    rows.push({ item: { type: "action", id: COLLECTION_ABOUT_ID, title: o.aboutOpen ? "Hide the description" : "About",
                        icon: "󰋽" } })
    if (o.aboutOpen) {
      var paragraphs = descriptionParagraphs(description)
      for (var p = 0; p < paragraphs.length; p++) {
        rows.push({ item: { type: "text", title: paragraphs[p], paragraph: true } })
      }
    }
  }
  if (finding) return rows.concat(findRows(tracks, o.find, o.history === true))
  rows = rows.concat(o.history === true ? historyRows(tracks) : trackRows(tracks))
  if (canLoadAll(page)) {
    rows.push({ item: { type: "loadMore", id: LOAD_ALL_ID, title: loadAllTitle(page),
                        busyTitle: "Loading all " + (page.trackCount ? songsText(page.trackCount) : "songs") + "…" } })
  } else if (page.capped === true) {
    rows.push({ item: { type: "message", title: "Showing the first " + songsText(tracks.length) + " of "
                                              + groupedCount(page.trackCount) } })
  }
  var shelves = pageRows({ sections: page.sections || [] })
  for (var s = 0; s < shelves.length; s++) {
    // Enter on a shelf's row opens it; trackIndex is for the page's songs.
    if (shelves[s].trackIndex !== undefined) delete shelves[s].trackIndex
    rows.push(shelves[s])
  }
  return rows
}

// Where the Load all row is in `rows` (-1 when there is none): G stops
// there, since the page's shelves come after it.
function loadAllIndex(rows) {
  var list = rows || []
  for (var i = 0; i < list.length; i++) {
    var item = list[i] && list[i].item
    if (item && item.type === "loadMore" && item.id === LOAD_ALL_ID) return i
  }
  return -1
}

// The first artist of an album page with a page of their own (A with no
// song selected), as an item to open; null elsewhere.
function pageArtistItem(page) {
  if (!isCollection(page) || page.kind !== "album") return null
  var artists = linkedArtists({ artists: collectionHeader(page).artists })
  return artists.length ? { type: "artist", channelId: artists[0].id, name: artists[0].name, thumb: null } : null
}

// A track without its token fields (what the queue keeps and saves).
function withoutTokens(track) {
  if (!track || typeof track !== "object") return track
  var has = false
  for (var t = 0; t < TOKEN_KEYS.length; t++) if (track[TOKEN_KEYS[t]] !== undefined) has = true
  if (!has) return track
  var out = {}
  for (var key in track) if (TOKEN_KEYS.indexOf(key) < 0) out[key] = track[key]
  return out
}

// M on a collection page: the twins of Load all, the About row and f.
MENU_ACTIONS.push(
  { id: "loadAll", title: function(item, ctx) { return ctx.loadAllTitle || "Load all songs" }, icon: "󰅀",
    applies: function(item, ctx) { return ctx.page === true && ctx.canLoadAll === true },
    run: function(env) { if (env.owner && typeof env.owner.loadAll === "function") env.owner.loadAll() } },
  { id: "find", title: "Find in this list", icon: "󰍉", key: "find",
    applies: function(item, ctx) { return ctx.page === true && ctx.canFind === true },
    run: function(env) { if (env.owner && typeof env.owner.find === "function") env.owner.find() } },
  { id: "collectionAbout", title: function(item, ctx) { return ctx.aboutOpen ? "Hide the description" : "About" }, icon: "󰋽",
    applies: function(item, ctx) { return ctx.page === true && ctx.description === true },
    run: function(env) { if (env.owner && typeof env.owner.toggleAbout === "function") env.owner.toggleAbout() } })

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    LOAD_ALL_ID: LOAD_ALL_ID,
    COLLECTION_ABOUT_ID: COLLECTION_ABOUT_ID,
    TOKEN_KEYS: TOKEN_KEYS,
    pageExtra: pageExtra,
    isCollection: isCollection,
    groupedCount: groupedCount,
    songsText: songsText,
    canLoadAll: canLoadAll,
    canFind: canFind,
    loadAllTitle: loadAllTitle,
    collectionHeader: collectionHeader,
    collectionDescription: collectionDescription,
    collectionByline: collectionByline,
    collectionMeta: collectionMeta,
    foldText: foldText,
    filterTracks: filterTracks,
    findRows: findRows,
    descriptionParagraphs: descriptionParagraphs,
    showsAbout: showsAbout,
    collectionRows: collectionRows,
    loadAllIndex: loadAllIndex,
    pageArtistItem: pageArtistItem,
    withoutTokens: withoutTokens
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

// ==== up next (S12) =======================================================================
// The queue as the web's Up next. Autoplay tops it up whenever AUTOPLAY_LEFT
// or fewer entries follow the current one (AUTOPLAY_BATCH songs at a time)
// and goes on as radio for every context: the song playing's nowContext
// items first (no request), then the worker's upNext for the playContext
// (a finite album or playlist goes on as its radio). Entries that autoplay,
// Start radio or a tuner chip added are "auto"; every other entry is the
// user's. The origin is kept per mpv playlist entry id, so one song can be
// both. A tuner chip ("Tune: All", t, or the Now page menu) replaces only the
// upcoming auto entries; played entries more than TRIM_KEEP behind the
// current one leave mpv's playlist (their ids stay `seen`, so autoplay does
// not bring them back). Service keeps the origins, context, tuner and seen
// ids in ~/.cache/omarchy-ytmusic/queue.json for hot reloads.

var AUTOPLAY_LEFT = 3
var AUTOPLAY_BATCH = 10
var TRIM_KEEP = 50
var SEEN_MAX = 300
var QUEUE_STATE_VERSION = 1
var _VIDEO_ID = /^[A-Za-z0-9_-]{11}$/

// True when `pos` plays and `left` or fewer of `count` entries follow it.
function topUpNeeded(count, pos, left) {
  var n = Number(count) || 0
  var p = typeof pos === "number" ? pos : -1
  if (n <= 0 || p < 0 || p >= n) return false
  return n - p - 1 <= (typeof left === "number" ? left : AUTOPLAY_LEFT)
}

// videoId -> true for the queue's songs, their known twins and `seen`.
function _knownIds(queue, seen) {
  var known = {}
  for (var i = 0; i < (queue || []).length; i++) {
    var entry = queue[i]
    if (!entry || !entry.videoId) continue
    known[entry.videoId] = true
    var twin = entry.track && entry.track.counterpart
    if (twin && twin.videoId) known[twin.videoId] = true
  }
  if (seen && typeof seen.length === "number") {
    for (var j = 0; j < seen.length; j++) if (seen[j]) known[seen[j]] = true
  } else if (seen && typeof seen === "object") {
    for (var key in seen) if (seen[key]) known[key] = true
  }
  return known
}

function _twinKnown(track, known) {
  var twin = track && track.counterpart
  return !!(twin && twin.videoId && known[twin.videoId])
}

// A pick and its twin are queued from now on.
function _markKnown(track, known) {
  known[track.videoId] = true
  if (track.counterpart && track.counterpart.videoId) known[track.counterpart.videoId] = true
}

// ---- origins ---------------------------------------------------------------------------
// { "<mpv entry id>": { videoId, origin: "auto" } }; an entry missing from
// the map (or holding another video) is the user's.

function originOf(origins, entryId, videoId) {
  var o = origins && entryId !== undefined && entryId !== null ? origins[String(entryId)] : null
  return o && o.origin === "auto" && o.videoId === videoId ? "auto" : "user"
}

// Copies of the queue entries with their `origin`.
function withOrigins(queue, origins) {
  return (queue || []).map(function(entry) {
    var out = {}
    for (var k in entry) out[k] = entry[k]
    out.origin = originOf(origins, entry.entryId, entry.videoId)
    return out
  })
}

// `origins` plus the entries in `added` ([{ entryId, videoId, origin }]),
// copy on write.
function addOrigins(origins, added) {
  var out = {}
  for (var k in origins || {}) out[k] = origins[k]
  for (var i = 0; i < (added || []).length; i++) {
    var a = added[i]
    if (a && typeof a.entryId === "number" && a.videoId && a.origin === "auto")
      out[String(a.entryId)] = { videoId: a.videoId, origin: "auto" }
  }
  return out
}

// The origins of entries still in mpv's playlist (by id and video); the
// same object when none went. mpv answers in order, so a snapshot never
// lacks an entry whose origin was already recorded.
function pruneOrigins(origins, playlist) {
  var present = {}
  for (var i = 0; i < (playlist || []).length; i++) {
    var entry = playlist[i]
    if (entry && entry.id !== undefined) present[String(entry.id)] = videoIdFromUrl(entry.filename)
  }
  var out = {}
  var dropped = false
  for (var key in origins || {}) {
    if (present[key] !== undefined && present[key] === origins[key].videoId) out[key] = origins[key]
    else dropped = true
  }
  return dropped ? out : origins
}

// ---- queue changes --------------------------------------------------------------------

// Where an added song goes so it plays before autoplay's picks, as on the
// web: the first upcoming auto entry's index, -1 (the end) when none.
function queueInsertAt(queue, pos) {
  if (typeof pos !== "number" || pos < 0) return -1
  for (var i = pos + 1; i < (queue || []).length; i++) if (queue[i].origin === "auto") return i
  return -1
}

// Queue `tracks` at `at` (in order; the first starts playback when nothing
// plays), or at the end when `at` < 0.
function queueAtCommands(tracks, at) {
  if (!(at >= 0)) return queueAllCommands(tracks)
  var list = (tracks || []).filter(playable)
  var commands = []
  for (var i = 0; i < list.length; i++)
    commands.push(["loadfile", trackUrl(list[i].videoId), i === 0 ? "insert-at-play" : "insert-at", at + i, loadfileOptions(list[i])])
  return { commands: commands, tracks: list }
}

// A tuner chip's songs in place of the upcoming auto entries: those are
// removed (from the end, so indexes hold), user entries stay, and up to
// `max` new picks (autoplayPicks against what stays and `seen`) are
// appended. Nothing changes when no pick is new.
function replaceUpcomingAuto(queue, pos, candidates, max, seen) {
  var list = queue || []
  var kept = []
  var remove = []
  for (var i = 0; i < list.length; i++) {
    if (typeof pos === "number" && pos >= 0 && i > pos && list[i].origin === "auto") remove.push(i)
    else kept.push(list[i])
  }
  var picks = autoplayPicks(kept, candidates, max || AUTOPLAY_BATCH, seen)
  if (picks.length === 0) return { commands: [], tracks: [], removed: 0 }
  var commands = remove.reverse().map(function(index) { return ["playlist-remove", index] })
  return { commands: commands.concat(queueAllCommands(picks).commands), tracks: picks, removed: remove.length }
}

// How many played entries leave the front of the playlist so at most `keep`
// stay behind the current one.
function trimPlayed(pos, keep) {
  var k = typeof keep === "number" && keep >= 0 ? keep : TRIM_KEEP
  return typeof pos === "number" && pos > k ? pos - k : 0
}

function trimCommands(count) {
  var out = []
  for (var i = 0; i < count; i++) out.push(["playlist-remove", 0])
  return out
}

// `meta` without songs that are neither in the playlist nor in `keep`
// (videoId -> true: remembered moments ago, their loadfile may not have
// landed yet); the same object when none went.
function trimMeta(meta, playlist, keep) {
  var queued = {}
  for (var i = 0; i < (playlist || []).length; i++) {
    var videoId = videoIdFromUrl(playlist[i] && playlist[i].filename)
    if (videoId) queued[videoId] = true
  }
  var out = {}
  var dropped = false
  for (var key in meta || {}) {
    if (queued[key] || (keep && keep[key])) out[key] = meta[key]
    else dropped = true
  }
  return dropped ? out : meta
}

// `seen` (oldest first) with `ids` added, at most `max`, each once.
function withSeen(seen, ids, max) {
  var limit = max > 0 ? max : SEEN_MAX
  var out = (seen || []).filter(function(id) { return (ids || []).indexOf(id) < 0 })
  for (var i = 0; i < (ids || []).length; i++) if (ids[i] && out.indexOf(ids[i]) < 0) out.push(ids[i])
  return out.length > limit ? out.slice(out.length - limit) : out
}

// Where one top-up looks for new songs after `seedId`, in order, until one
// gives a pick that is not a duplicate: the chosen tuner chip's queue, else
// the song's nowContext items (`memo`, no request) and the context's
// upNext; then, once, the song's own radio when the first ones were
// something else. A step is { items } or { method, params }.
function autoplaySources(seedId, tuner, memo, playlistId) {
  var out = []
  if (tuner && tuner.playlistId) {
    var chip = { videoId: seedId, playlistId: tuner.playlistId }
    if (tuner.params) chip.params = tuner.params
    out.push({ method: "queueFrom", params: chip })
  } else {
    if (memo && memo.items && memo.items.length > 0) out.push({ items: memo.items })
    var context = { videoId: seedId, limit: 25 }
    if (playlistId) context.playlistId = playlistId
    out.push({ method: "upNext", params: context })
  }
  if ((tuner && tuner.playlistId) || playlistId) out.push({ method: "upNext", params: { videoId: seedId, limit: 25 } })
  return out
}

// ---- Playing from and the tuner -----------------------------------------------------------

// The queue header's "Playing from" text: what the queue was started from,
// else what YouTube plays the song from ("Karma Police Mix"); "" when unknown.
function playingFrom(context, nowContext) {
  if (context && context.title) return String(context.title)
  if (nowContext && nowContext.playingFrom) return String(nowContext.playingFrom)
  return ""
}

function tunerChips(nowContext) {
  var chips = nowContext && nowContext.chips && typeof nowContext.chips.length === "number" ? nowContext.chips : []
  var out = []
  for (var i = 0; i < chips.length; i++) if (chips[i] && chips[i].playlistId && chips[i].title) out.push(chips[i])
  return out
}

function _tunerIndex(chips, tuner) {
  for (var i = 0; i < chips.length; i++) {
    if (tuner ? chips[i].title === tuner.title : chips[i].selected === true) return i
  }
  return tuner ? -1 : 0
}

// The chip shown: the one chosen, else YouTube's own (All).
function tunerTitle(tuner, chips) {
  if (tuner && tuner.title) return String(tuner.title)
  var list = chips || []
  var i = _tunerIndex(list, null)
  return list.length > 0 ? String(list[i].title) : "All"
}

// The chip after (or before, `delta` < 0) the one shown; null without chips.
function nextTuner(chips, tuner, delta) {
  var list = chips || []
  if (list.length === 0) return null
  var i = _tunerIndex(list, tuner)
  var step = delta < 0 ? -1 : 1
  return list[((i < 0 ? (step > 0 ? -1 : 0) : i) + step + list.length) % list.length]
}

// A chip as kept by the service and queue.json.
function tunerOf(chip) {
  if (!chip || !chip.playlistId || !chip.title) return null
  return { title: String(chip.title), playlistId: String(chip.playlistId), params: chip.params ? String(chip.params) : null }
}

// ItemMenu chooser rows for the chips; the one shown is ticked.
function tunerChoices(chips, tuner) {
  var shown = _tunerIndex(chips || [], tuner)
  return (chips || []).map(function(chip, i) {
    return { id: "tune" + i, title: String(chip.title), icon: i === shown ? "󰄬" : "󰐹", value: chip }
  })
}

// ---- song / video versions ----------------------------------------------------------------

// The current song's twin: the nowContext's (the song playing's), else what
// the queue entry learned from an up-next list.
function counterpartOf(track, nowContext) {
  if (!track || !track.videoId) return null
  var fromContext = nowContext && nowContext.videoId === track.videoId ? nowContext.counterpart : null
  var twin = fromContext || track.counterpart || null
  return twin && twin.videoId && twin.videoId !== track.videoId ? twin : null
}

// The queue track of `track`'s twin (the other version of the same song).
function counterpartTrack(track, counterpart) {
  if (!track || !counterpart || !counterpart.videoId) return null
  var out = {}
  for (var k in track) out[k] = track[k]
  out.videoId = counterpart.videoId
  out.title = counterpart.title || track.title || ""
  out.kind = counterpart.kind === "video" ? "video" : "song"
  out.counterpart = { videoId: track.videoId, title: track.title || "", kind: track.kind === "video" ? "video" : "song" }
  if (out.kind === "video") out.album = null
  out.likeStatus = null
  out.setVideoId = null
  out.played = null
  return out
}

// Play `track` from its start in place of the entry at `pos`: insert it
// next, jump to it, then take the old entry out (not before: removing the
// entry playing would start the one after it).
function switchVersionCommands(track, pos) {
  return [loadfile(track, "insert-next"), ["playlist-play-index", pos + 1], ["set_property", "pause", false],
          ["playlist-remove", pos]]
}

// ---- queue.json ---------------------------------------------------------------------------
// { version, pid, context, tuner, origins, seen }: `pid` is the mpv process
// the entry ids belong to (a new player starts its ids over, so its origins
// go).

function _context(value) {
  if (!value || typeof value !== "object" || typeof value.title !== "string") return null
  return { playlistId: typeof value.playlistId === "string" && value.playlistId ? value.playlistId : null,
           title: value.title, radio: value.radio === true }
}

function parseQueueState(text) {
  var saved = null
  try {
    saved = JSON.parse(text)
  } catch (e) {
    saved = null
  }
  var state = { pid: null, context: null, tuner: null, origins: {}, seen: [] }
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) return state
  state.pid = typeof saved.pid === "number" && saved.pid > 0 ? saved.pid : null
  state.context = _context(saved.context)
  state.tuner = saved.tuner && typeof saved.tuner === "object" ? tunerOf(saved.tuner) : null
  var origins = saved.origins && typeof saved.origins === "object" && !Array.isArray(saved.origins) ? saved.origins : {}
  for (var key in origins) {
    var o = origins[key]
    if (/^\d+$/.test(key) && o && o.origin === "auto" && _VIDEO_ID.test(String(o.videoId || "")))
      state.origins[key] = { videoId: o.videoId, origin: "auto" }
  }
  var seen = Array.isArray(saved.seen) ? saved.seen : []
  state.seen = withSeen([], seen.filter(function(id) { return _VIDEO_ID.test(String(id)) }), SEEN_MAX)
  return state
}

function queueStateText(state) {
  var s = state || {}
  return JSON.stringify({ version: QUEUE_STATE_VERSION, pid: typeof s.pid === "number" ? s.pid : null,
                          context: _context(s.context), tuner: tunerOf(s.tuner), origins: s.origins || {},
                          seen: s.seen || [] }) + "\n"
}

// Song or video version entries for the menu of the song playing
// (ctx.counterpart, filled by Panel and QueueView from counterpartOf).
MENU_ACTIONS.splice(nowIndexOfAction("radio"), 0, { id: "switchVersion",
  title: function(item, ctx) { return ctx.counterpart.kind === "video" ? "Play video version" : "Play song version" },
  icon: "󰓡",
  applies: function(item, ctx) {
    return menuKind(item) === "track" && ctx.isCurrent === true && !!ctx.counterpart && !!ctx.counterpart.videoId
      && ctx.counterpart.videoId !== item.videoId
  },
  run: function(env) { env.service.switchVersion(env.ctx.counterpart, env.item) } })

// The Now page menu (M): every tuner chip in a chooser (ctx.tunerChips,
// ctx.tunerTitle from Panel.nowMenu).
MENU_ACTIONS.push({ id: "tune", title: function(item, ctx) { return "Tune the queue (" + ctx.tunerTitle + ")" }, icon: "󰘮",
  key: "tune",
  applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "now" && ctx.tunerChips > 0 },
  run: function(env) { env.panel.openTuner() } })

KEYS.push({ id: "tune", group: "Now", keys: ["t"], title: "cycle the queue tuner chip: All, Discover, Popular and the rest" })

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    AUTOPLAY_LEFT: AUTOPLAY_LEFT,
    AUTOPLAY_BATCH: AUTOPLAY_BATCH,
    TRIM_KEEP: TRIM_KEEP,
    SEEN_MAX: SEEN_MAX,
    topUpNeeded: topUpNeeded,
    originOf: originOf,
    withOrigins: withOrigins,
    addOrigins: addOrigins,
    pruneOrigins: pruneOrigins,
    queueInsertAt: queueInsertAt,
    queueAtCommands: queueAtCommands,
    replaceUpcomingAuto: replaceUpcomingAuto,
    autoplaySources: autoplaySources,
    trimPlayed: trimPlayed,
    trimCommands: trimCommands,
    trimMeta: trimMeta,
    withSeen: withSeen,
    playingFrom: playingFrom,
    tunerChips: tunerChips,
    tunerTitle: tunerTitle,
    nextTuner: nextTuner,
    tunerOf: tunerOf,
    tunerChoices: tunerChoices,
    counterpartOf: counterpartOf,
    counterpartTrack: counterpartTrack,
    switchVersionCommands: switchVersionCommands,
    parseQueueState: parseQueueState,
    queueStateText: queueStateText
  })
}

// ==== playback (S17) =======================================================================
// Speed and loudness, the mpv way. Speed is mpv's `speed`, SPEED_MIN to
// SPEED_MAX in SPEED_STEP steps ([ and ], or the Now page menu), remembered
// per kind of entry in prefs.speed { music, episode } and applied whenever
// the kind of the entry playing changes, so an episode keeps its own speed.
// Loudness is prefs.normalize: with "youtube" the worker answers each
// resolved song's loudnessDb (its `normalize` setting, workerSettings), the
// stream keeps it as `gain` (loudnessGain: turned down only, as on the web)
// and mpv/ytm-levels.lua sets it as that file's own volume-gain when it
// loads; "dynamic" runs mpv's dynaudnorm filter (ytm-levels.lua too); "off"
// neither. The shell tells mpv the mode in user-data/ytm/normalize.

var SPEED_MIN = 0.5
var SPEED_MAX = 2
var SPEED_STEP = 0.25
var SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]
var NORMALIZE_MODES = ["off", "youtube", "dynamic"]

// "episode" for a podcast episode, else "music" (songs and videos).
function speedKind(track) {
  return track && track.kind === "episode" ? "episode" : "music"
}

// A speed on the step grid within the bounds; 1 for anything not a number.
function clampSpeed(value) {
  var n = Number(value)
  if (typeof value !== "number" || !isFinite(n)) return 1
  return Math.min(SPEED_MAX, Math.max(SPEED_MIN, Math.round(n / SPEED_STEP) * SPEED_STEP))
}

// One step up (`delta` > 0) or down from `speed`, within the bounds; a speed
// between steps (set over MPRIS) goes to the step on that side.
function speedStep(speed, delta) {
  var n = Number(speed)
  if (typeof speed !== "number" || !isFinite(n) || n <= 0) n = 1
  var steps = n / SPEED_STEP
  var next = delta > 0 ? (Math.floor(steps + 1e-6) + 1) * SPEED_STEP
    : delta < 0 ? (Math.ceil(steps - 1e-6) - 1) * SPEED_STEP
    : clampSpeed(n)
  return Math.min(SPEED_MAX, Math.max(SPEED_MIN, next))
}

// The saved speed for a kind ("music" or "episode") of prefs.speed.
function speedFor(speeds, kind) {
  var value = speeds && typeof speeds === "object" ? speeds[kind === "episode" ? "episode" : "music"] : undefined
  return typeof value === "number" && value >= SPEED_MIN && value <= SPEED_MAX ? value : 1
}

// prefs.speed with `kind`'s speed set to `value` (copy on write).
function withSpeed(speeds, kind, value) {
  var episode = kind === "episode"
  return { music: episode ? speedFor(speeds, "music") : clampSpeed(value),
           episode: episode ? clampSpeed(value) : speedFor(speeds, "episode") }
}

// The Now badge: "1.5x", "" at normal speed.
function speedLabel(speed) {
  var n = Number(speed)
  if (typeof speed !== "number" || !isFinite(n) || Math.abs(n - 1) < 0.001) return ""
  return String(Math.round(n * 100) / 100) + "x"
}

function speedTitle(speed) {
  return speedLabel(speed) || "Normal"
}

// ItemMenu chooser rows for the speeds; the one playing is ticked.
function speedChoices(speed) {
  return SPEEDS.map(function(value) {
    var current = typeof speed === "number" && Math.abs(speed - value) < 0.001
    return { id: "speed" + value, title: speedTitle(value), icon: current ? "󰄬" : "󰓅", value: value }
  })
}

function normalizeMode(value) {
  return NORMALIZE_MODES.indexOf(value) >= 0 ? value : "off"
}

function normalizeTitle(mode) {
  var m = normalizeMode(mode)
  return m === "youtube" ? "YouTube" : m === "dynamic" ? "Dynamic" : "Off"
}

// ItemMenu chooser rows for the loudness modes; the one on is ticked.
function normalizeChoices(mode) {
  var on = normalizeMode(mode)
  var about = { off: "Off (the music as it comes)", youtube: "YouTube (loud songs turned down, like the web)",
                dynamic: "Dynamic (evens out quiet and loud parts)" }
  return NORMALIZE_MODES.map(function(m) {
    return { id: "normalize-" + m, title: about[m], icon: m === on ? "󰄬" : "󰺢", value: m }
  })
}

// The volume-gain (dB) for YouTube's loudnessDb: songs above YouTube's target
// are turned down by that much, quieter ones are left alone (never boosted).
// null when there is no loudness.
function loudnessGain(loudnessDb) {
  var n = Number(loudnessDb)
  if (typeof loudnessDb !== "number" || !isFinite(n)) return null
  return n > 0 ? -Math.round(n * 100) / 100 : 0
}

// A stream as the shell keeps it (and mpv reads it): { url, expires }, plus
// `gain` (a number, or null when YouTube gave none) when the worker was asked
// for the loudness; without that key nobody asked.
function streamEntry(stream) {
  var entry = { url: stream ? stream.url : "", expires: stream ? stream.expires : 0 }
  if (stream && stream.loudnessDb !== undefined) entry.gain = loudnessGain(stream.loudnessDb)
  return entry
}

function streamHasLoudness(entry) {
  return !!entry && entry.gain !== undefined
}

// Only the streams resolved with their loudness (the same object when that
// is all of them): turning YouTube levelling on forgets the others, so
// they are resolved again, with it.
function streamsWithLoudness(streams) {
  var out = {}
  var dropped = false
  for (var id in streams || {}) {
    if (streamHasLoudness(streams[id])) out[id] = streams[id]
    else dropped = true
  }
  return dropped ? out : streams
}

// The worker's settings from the prefs (Backend.configureParams); S16 adds
// location and quality here.
function workerSettings(prefs) {
  return { normalize: normalizeMode(prefs && prefs.normalize) }
}

// The Now page menu (M): Playback speed and Loudness, each a chooser
// (ctx.speed and ctx.normalize from Panel.nowMenu).
MENU_ACTIONS.push({ id: "speed", title: function(item, ctx) { return "Playback speed (" + speedTitle(ctx.speed) + ")" },
  icon: "󰓅", key: "speed",
  applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "now" },
  run: function(env) { env.panel.openSpeedChooser() } })
MENU_ACTIONS.push({ id: "loudness", title: function(item, ctx) { return "Loudness (" + normalizeTitle(ctx.normalize) + ")" },
  icon: "󰺢",
  applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "now" },
  run: function(env) { env.panel.openLoudnessChooser() } })

KEYS.push({ id: "speed", group: "Now", keys: ["[", "]"], title: "playback speed down / up (0.5x to 2x, kept apart for episodes)" })

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    SPEED_MIN: SPEED_MIN,
    SPEED_MAX: SPEED_MAX,
    SPEED_STEP: SPEED_STEP,
    SPEEDS: SPEEDS,
    NORMALIZE_MODES: NORMALIZE_MODES,
    speedKind: speedKind,
    clampSpeed: clampSpeed,
    speedStep: speedStep,
    speedFor: speedFor,
    withSpeed: withSpeed,
    speedLabel: speedLabel,
    speedTitle: speedTitle,
    speedChoices: speedChoices,
    normalizeMode: normalizeMode,
    normalizeTitle: normalizeTitle,
    normalizeChoices: normalizeChoices,
    loudnessGain: loudnessGain,
    streamEntry: streamEntry,
    streamHasLoudness: streamHasLoudness,
    streamsWithLoudness: streamsWithLoudness,
    workerSettings: workerSettings
  })
}
