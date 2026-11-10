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

// A search result as rows: searchRows in the search (S7) section (groups in
// YouTube's order, Show all rows, spelling rows, Load more).
function flattenSearch(result, filter, opts) {
  return searchRows(result, filter, opts)
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
  // S6: a chart row (Top songs, Trending, Top artists) leads with its rank and trend.
  var rank = rankText(item)
  if (item.type === "track") return joinParts([rank, item.artistText, item.kind === "video" ? "Video" : ""])
  if (item.type === "album") return joinParts([item.albumType, item.artistText, item.year])
  if (item.type === "playlist")
    return joinParts([item.author, item.count ? item.count + " songs" : ""]) || (item.description ? String(item.description) : "")
  if (item.type === "artist") return joinParts([rank, item.subtitle ? item.subtitle : "Artist"])
  if (item.type === "liked") return "Songs you liked"
  if (item.type === "history") return "Recently played"
  return joinParts([rank, item.subtitle ? String(item.subtitle) : ""])
}

// Home's rows (homeRows, shelfTracks): the home (S5) section.

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
// S7: one of your YouTube searches (fromHistory) is marked `remote`, also
// when it repeats a recent search, so x can remove it from YouTube too.
function suggestionList(recent, remote, typed, max) {
  var key = String(typed || "").trim().toLowerCase()
  var limit = max || 5
  var out = []
  var seen = {}
  seen[key] = true
  function add(text, fromHistory, isRemote) {
    var value = String(text || "").trim()
    var k = value.toLowerCase()
    if (isRemote && seen[k] && seen[k] !== true) seen[k].remote = true
    if (value === "" || seen[k] || out.length >= limit) return
    var entry = { text: value, recent: fromHistory }
    if (isRemote) entry.remote = true
    seen[k] = entry
    out.push(entry)
  }
  for (var i = 0; i < (recent || []).length && out.length < 2; i++) {
    if (String(recent[i]).toLowerCase().indexOf(key) === 0) add(recent[i], true, false)
  }
  for (var j = 0; j < (remote || []).length; j++) add(remote[j].text, !!remote[j].fromHistory, !!remote[j].fromHistory)
  return out
}

function recentRows(recent, max) {
  var list = (recent || []).slice(0, max || 8)
  var rows = list.length ? [{ header: true, title: "Recent searches" }] : []
  for (var i = 0; i < list.length; i++) rows.push({ item: { type: "query", text: list[i], recent: true, removable: true } })
  return rows
}

// `selected` marks the one Tab put into the field. S7: a recent or past
// search can be removed (x): `remote` ones from YouTube too.
function suggestionRows(suggestions, selected) {
  var list = suggestions || []
  var rows = list.length ? [{ header: true, title: "Suggestions" }] : []
  for (var i = 0; i < list.length; i++) {
    rows.push({ item: { type: "query", text: list[i].text, recent: !!list[i].recent, removable: !!list[i].recent,
                        remote: !!list[i].remote, selected: i === selected } })
  }
  return rows
}

// ---- caches ---------------------------------------------------------------------

// S7: the scope ("", "library", "uploads") and an exact search (no spelling
// fix) are part of the key; a plain search keeps its old key.
function searchKey(query, filter, scope, exact) {
  var key = (filter || "") + "|" + String(query || "").trim().toLowerCase()
  return scope || exact ? key + "|" + (scope || "") + (exact ? "|exact" : "") : key
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
//   ttlMs         optional: how long the Store's copy counts as fresh (5 min);
//                 one longer than Store.unseenMs (10 min) also keeps an
//                 unseen copy that long (S6 moods)
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
  { id: "tab", group: "Move", keys: ["1", "2", "3", "4", "5"], label: ["1-5"], title: "switch tab: Now, Search, Library, Home, Explore" },
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
  { id: "order", group: "Menus", keys: ["O"], title: "next sort order on a page that has one (Charts: the country)" },
  { id: "find", group: "Menus", keys: ["f"], title: "find in a page's songs (album, playlist, Liked Music, History) or the suggested artists" },
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

// ==== actions (S4) ==========================================================================
// The web's library toggles on every item: Save to library on albums,
// playlists and mixes (the worker's rateCollection), Subscribe on artists
// (subscribe), Save to library on a song through the library tokens its
// row came with (songLibrary), and Dislike (rate DISLIKE; Remove dislike
// clears it). S saves or subscribes the cursor row, else the page itself;
// D dislikes the cursor's song, else the one playing, which then skips.
// What is saved, subscribed, in the library or disliked lives in the
// Store (its "actions (S4)" section): an edit changes it at once
// (optimistic) and puts it back when the worker says no (restoreStates).
// Service.fillMenuState puts it in a menu's ctx: saved, subscribed,
// inLibrary, liked, disliked and ownItem (the row is your own playlist).

// What can be saved; what can be subscribed to. S9 pushes "profile" and
// "channel" to SUBSCRIBE_TYPES (their channelId is the one subscribed to).
var SAVE_KINDS = ["album", "playlist", "mix"]
var SUBSCRIBE_TYPES = ["artist"]
// Rows that are no item: S on one of them acts on the page.
var _CONTROL_ROWS = ["more", "loadMore", "action", "message", "text"]

// The playlist rate_playlist saves: an album's audio playlist, a playlist
// or mix id without VL; "" when the item has none.
function savePlaylistId(item) {
  if (SAVE_KINDS.indexOf(menuKind(item)) < 0) return ""
  return _stripVl(item.playlistId || "")
}

// The Store keys an album, playlist or mix is saved under: its playlist id
// and, for an album, its browseId (an album row may lack the playlist).
function saveKeys(item) {
  var keys = []
  var id = savePlaylistId(item)
  if (id) keys.push(id)
  if (menuKind(item) === "album" && item.browseId) keys.push(String(item.browseId))
  return keys
}

// An album, a playlist or a mix that is not yours (nor Liked Music or
// Episodes for later). ctx.ownItem: the Store knows it as yours; a page
// menu's ctx.owned is the page's.
function canSave(item, ctx) {
  var kind = menuKind(item)
  if (SAVE_KINDS.indexOf(kind) < 0 || saveKeys(item).length === 0) return false
  if (kind === "album") return true
  var c = ctx || {}
  return !isSpecialPlaylist({ playlistId: savePlaylistId(item) }) && item.owned !== true && c.ownItem !== true
    && !(c.page === true && c.owned === true)
}

function canSubscribe(item) {
  return !!item && typeof item === "object" && SUBSCRIBE_TYPES.indexOf(item.type) >= 0 && !!item.channelId
}

// A song's library tokens ({ add, remove }, S3), or null. Rows from a disk
// copy and songs in the queue have none.
function songTokens(track) {
  var tokens = track && track.type === "track" ? track.libraryTokens : null
  return tokens && typeof tokens === "object" && (tokens.add || tokens.remove) ? tokens : null
}

// The row carries the token that adds the song (add) or removes it.
function canSaveSong(track, add) {
  var tokens = songTokens(track)
  return !!tokens && !!(add ? tokens.add : tokens.remove)
}

// What S does to an item: "save", "subscribe", "saveSong", or "" (nothing).
function saveAction(item, ctx) {
  if (canSave(item, ctx)) return "save"
  if (canSubscribe(item)) return "subscribe"
  if (songTokens(item)) return "saveSong"
  return ""
}

// Where S acts: "row" (the cursor row's item), "page" (no row has the
// cursor, or the row is no item: See all, Load more, Load all, About, a
// text line), or "" (an item with nothing to save: a song without library
// tokens, Liked Music, your own playlist).
function saveTarget(rowItem, ctx) {
  if (!rowItem || typeof rowItem !== "object" || _CONTROL_ROWS.indexOf(rowItem.type) >= 0) return "page"
  return saveAction(rowItem, ctx) !== "" ? "row" : ""
}

// ---- worker params ---------------------------------------------------------------------

// The channel ids of an item's linked artists: the artist pages an album
// save or a song's library edit changes ("From your library").
function editArtistIds(item) {
  return linkedArtists(item).map(function(a) { return String(a.id) })
}

// rateCollection: an album by its page (the worker reads its playlist when
// the row lacks it) and its artists, anything else by its playlist. Null
// when neither.
function rateCollectionParams(item, save) {
  var playlistId = savePlaylistId(item)
  var rating = save ? "LIKE" : "INDIFFERENT"
  if (menuKind(item) === "album" && item.browseId) {
    var params = { kind: "album", browseId: String(item.browseId), rating: rating }
    if (playlistId) params.playlistId = playlistId
    var artistIds = editArtistIds(item)
    if (artistIds.length > 0) params.artistIds = artistIds
    return params
  }
  return playlistId ? { kind: "playlist", playlistId: playlistId, rating: rating } : null
}

// subscribe: an artist by its page (the worker reads the channel the page
// subscribes to unless the item names it: subscribeId); any other channel
// by its own id.
function subscribeParams(item, on) {
  if (!canSubscribe(item)) return null
  var id = String(item.channelId)
  var params = { pageChannelId: id, subscribe: on === true }
  if (item.subscribeId) params.channelId = String(item.subscribeId)
  else if (item.type !== "artist") params.channelId = id
  return params
}

// songLibrary: the token that adds the song (add) or removes it, and the
// song's artists; null when the row has not got the token.
function songLibraryParams(track, add) {
  var tokens = songTokens(track)
  var token = tokens ? (add ? tokens.add : tokens.remove) : null
  if (!token) return null
  var params = { videoId: String(track.videoId), token: String(token) }
  var artistIds = editArtistIds(track)
  if (artistIds.length > 0) params.artistIds = artistIds
  return params
}

// ---- state: known, optimistic, rolled back ------------------------------------------------

// true, false, or null when `map` does not know `key`.
function stateOf(map, key) {
  var value = map && key !== undefined && key !== null ? map[key] : undefined
  return typeof value === "boolean" ? value : null
}

function firstState(map, keys) {
  for (var i = 0; i < (keys || []).length; i++) {
    var known = stateOf(map, keys[i])
    if (known !== null) return known
  }
  return null
}

function _keyList(keys) {
  return keys === undefined || keys === null ? [] : (typeof keys === "object" && typeof keys.length === "number" ? keys : [keys])
}

// A copy of `map` with every key of `keys` set to `value` (a boolean), or
// left out (anything else: unknown again).
function stateWith(map, keys, value) {
  var next = {}
  for (var k in map || {}) next[k] = map[k]
  var list = _keyList(keys)
  for (var i = 0; i < list.length; i++) {
    if (typeof value === "boolean") next[list[i]] = value
    else delete next[list[i]]
  }
  return next
}

// An edit shown before the worker answers: { map, previous }, previous
// being what each key held (null: nothing), for restoreStates.
function optimistic(map, keys, value) {
  var list = _keyList(keys)
  var previous = {}
  for (var i = 0; i < list.length; i++) previous[list[i]] = stateOf(map, list[i])
  return { map: stateWith(map, list, value), previous: previous }
}

// The rollback of `optimistic`: every key back to what it held.
function restoreStates(map, previous) {
  var next = stateWith(map, [], null)
  for (var key in previous || {}) {
    if (typeof previous[key] === "boolean") next[key] = previous[key]
    else delete next[key]
  }
  return next
}

function _listHas(list, keys, fields) {
  var items = list && typeof list.length === "number" ? list : []
  for (var i = 0; i < items.length; i++) {
    for (var f = 0; items[i] && f < fields.length; f++) {
      var value = items[i][fields[f]]
      if (value && keys.indexOf(_stripVl(String(value))) >= 0) return true
    }
  }
  return false
}

// Whether an album, playlist or mix is in the library, as far as the panel
// knows. sources: { saved (the Store's edits), fallback (the page header's
// likeStatus as a boolean), playlists (the library's, read whole when
// playlistsLoaded), albums (the library's read so far; albumsComplete when
// all) }. An edit made here wins, then the page itself, then the lists; a
// playlist missing from the whole playlist list is not saved. null:
// unknown (menus then offer Save).
function savedState(item, sources) {
  var s = sources || {}
  var keys = saveKeys(item)
  if (keys.length === 0) return null
  var known = firstState(s.saved, keys)
  if (known !== null) return known
  if (typeof s.fallback === "boolean") return s.fallback
  if (_listHas(s.playlists, keys, ["playlistId"]) || _listHas(s.albums, keys, ["playlistId", "browseId"])) return true
  if (menuKind(item) === "album") return s.albumsComplete === true ? false : null
  return s.playlistsLoaded === true ? false : null
}

// Whether a channel is subscribed to. sources: { subscribed (edits),
// fallback (an artist page's header.subscribed), subscriptions (the
// Subscriptions page's items, when held; subscriptionsComplete when all) }.
function subscribedState(channelId, sources) {
  var s = sources || {}
  if (!channelId) return null
  var known = stateOf(s.subscribed, channelId)
  if (known !== null) return known
  if (typeof s.fallback === "boolean") return s.fallback
  if (_listHas(s.subscriptions, [String(channelId)], ["channelId"])) return true
  return s.subscriptionsComplete === true ? false : null
}

// Whether a song is in the library: an edit made here, else what its row
// said (S3's inLibrary), else null.
function inLibraryState(track, sources) {
  var s = sources || {}
  if (!track || !track.videoId) return null
  var known = stateOf(s.inLibrary, track.videoId)
  if (known !== null) return known
  return typeof track.inLibrary === "boolean" ? track.inLibrary : null
}

// Whether a song is disliked: the Store's likes first; a song disliked
// here this session stays disliked when a later check reports it
// INDIFFERENT (YouTube reports disliked songs so); a like undoes it.
// `fallback` is the row's own likeStatus.
function dislikedState(videoId, likes, disliked, fallback) {
  var known = likes ? likes[videoId] : undefined
  if (known === "DISLIKE") return true
  if (known === "LIKE") return false
  if (disliked && disliked[videoId] === true) return true
  if (known !== undefined && known !== null) return false
  return fallback === "DISLIKE"
}

// ---- after an edit ---------------------------------------------------------------------

// A songLibrary token YouTube would not take: the page it came from is
// out of date (the shell reloads that page once). The worker marks it
// (error.stale); any other UPSTREAM error is no stale token.
function staleToken(reply) {
  return !!reply && reply.ok === false && !!reply.error && reply.error.stale === true && reply.error.unknown !== true
}

// The owner's callback for an edit's reply: CollectionView's and
// LibraryView's editCallback() reload the page or chip shown when the
// edit's outcome is unknown or a token was stale (they return true when
// they did). Null for owners without one (Search, Home).
function editCallbackOf(owner) {
  return owner && typeof owner.editCallback === "function" ? owner.editCallback() : null
}

// ---- menu entries and keys -------------------------------------------------------------

// A page menu's library entry is the header button's twin (the view's
// toggleSave() knows the page: an album's header, an artist's subscribe
// channel; it says so when it has nothing to do).
function _pageToggle(env) {
  if (!env.ctx || env.ctx.page !== true || !env.owner || typeof env.owner.toggleSave !== "function") return false
  env.owner.toggleSave()
  return true
}

// On a page menu whose view says what its header offers (ctx.canSavePage,
// ctx.canSubscribePage), the entry shows only with the button: none while
// the page loads or after it failed.
function _headerOffers(ctx, key) {
  return ctx.page !== true || ctx[key] === undefined || ctx[key] === true
}

var _LIBRARY_ACTIONS = [
  { id: "save", title: function(item, ctx) { return ctx.saved === true ? "Remove from library" : "Save to library" },
    icon: "󰃅", key: "save",
    applies: function(item, ctx) { return ctx.signedIn === true && canSave(item, ctx) && _headerOffers(ctx, "canSavePage") },
    run: function(env) {
      if (!_pageToggle(env)) env.service.saveItem(env.item, env.ctx.saved !== true, editCallbackOf(env.owner))
    } },
  { id: "subscribe", title: function(item, ctx) { return ctx.subscribed === true ? "Unsubscribe" : "Subscribe" },
    icon: "󰀔", key: "save",
    applies: function(item, ctx) {
      return ctx.signedIn === true && canSubscribe(item) && _headerOffers(ctx, "canSubscribePage")
    },
    run: function(env) {
      if (!_pageToggle(env)) env.service.subscribe(env.item, env.ctx.subscribed !== true, editCallbackOf(env.owner))
    } },
  { id: "saveSong", title: function(item, ctx) { return ctx.inLibrary === true ? "Remove from library" : "Save to library" },
    icon: "󰃅", key: "save",
    applies: function(item, ctx) { return ctx.signedIn === true && canSaveSong(item, ctx.inLibrary !== true) },
    run: function(env) { env.service.setSongLibrary(env.item, env.ctx.inLibrary !== true, editCallbackOf(env.owner)) } },
  // Liked Music's own rows have Remove from Liked Music already.
  { id: "like", title: function(item, ctx) { return ctx.liked === true ? "Remove from Liked Music" : "Add to Liked Music" },
    icon: "󰋕", key: "like",
    applies: function(item, ctx) { return ctx.signedIn === true && menuKind(item) === "track" && ctx.pageKind !== "liked" },
    run: function(env) { env.service.setLike(env.item, env.ctx.liked !== true) } },
  { id: "dislike", title: function(item, ctx) { return ctx.disliked === true ? "Remove dislike" : "Dislike" },
    icon: "󰔒", key: "dislike",
    applies: function(item, ctx) { return ctx.signedIn === true && menuKind(item) === "track" },
    run: function(env) { env.service.dislike(env.item, env.ctx.disliked !== true) } }
]

// After Start radio, as in the web's menus; S and D after L in the keys.
function _insertAfter(list, id, entries) {
  var at = list.length
  for (var i = 0; i < list.length; i++) if (list[i].id === id) at = i + 1
  Array.prototype.splice.apply(list, [at, 0].concat(entries))
}

_insertAfter(MENU_ACTIONS, "radio", _LIBRARY_ACTIONS)
_insertAfter(KEYS, "like", [
  { id: "save", group: "Song", keys: ["S"], title: "save to library or subscribe (the row, else the page)" },
  { id: "dislike", group: "Song", keys: ["D"], title: "dislike the row's song (again to undo) or the one playing, which skips" }
])

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    SAVE_KINDS: SAVE_KINDS,
    SUBSCRIBE_TYPES: SUBSCRIBE_TYPES,
    savePlaylistId: savePlaylistId,
    saveKeys: saveKeys,
    canSave: canSave,
    canSubscribe: canSubscribe,
    songTokens: songTokens,
    canSaveSong: canSaveSong,
    saveAction: saveAction,
    saveTarget: saveTarget,
    rateCollectionParams: rateCollectionParams,
    subscribeParams: subscribeParams,
    songLibraryParams: songLibraryParams,
    editArtistIds: editArtistIds,
    stateOf: stateOf,
    firstState: firstState,
    stateWith: stateWith,
    optimistic: optimistic,
    restoreStates: restoreStates,
    savedState: savedState,
    subscribedState: subscribedState,
    inLibraryState: inLibraryState,
    dislikedState: dislikedState,
    staleToken: staleToken,
    editCallbackOf: editCallbackOf
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
// quality and location (accountSettings, in its section).
function workerSettings(prefs) {
  return accountSettings(prefs, { normalize: normalizeMode(prefs && prefs.normalize) })  // S16
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

// ==== home (S5) ============================================================================
// Home is the whole feed (the worker's homeFeed): every shelf the web shows,
// read a page of shelves at a time (Store.loadHomeMore when the cursor nears
// the end, or the Load more row). A shelf ends in a selectable See all row
// when it has one, routed like any See all (routeEndpoint: VL to a
// playlist, MPAD to an artist's releases, UC to an artist, a mood category
// to its mood page (S6); anything else, such as Listen again, Mixed for
// you, New releases, the library landing or a channel with params like
// Recaps, to a browse page),
// and in a Play all row when it has a Play all (Service.playShelf: the list
// YouTube generates for it). The mood chips above it, All first, switch
// with h / l; each chip is its served params ("" for All).

var HOME_PLAY_ALL = "home:playAll"
var HOME_ALL_CHIP = { title: "All", params: "" }

// Recaps' See all is a user channel with params (live: your own channel's
// page, Songs, Artists, Playlists and Videos on repeat; the recap playlists
// themselves are only on the shelf). It reads as a browse page, as the web
// opens it; this keeps it there when a profile route for user channels
// comes (S9), which would otherwise outrank the UC default.
ROUTES.push({ pageType: "MUSIC_PAGE_TYPE_USER_CHANNEL", prefix: "UC", when: function(ep) { return !!ep.params },
  target: function(ep, id) { return { type: "browse", browseId: id, params: ep.params, title: ep.title || "" } } })

// A podcast on a Home shelf (the Podcasts chip has whole shelves of them)
// opens as its browse page, which reads an MPSP page's episodes, until S8
// gives podcasts a page of their own.
if (!PAGE_KINDS.podcast) {
  PAGE_KINDS.podcast = {
    method: "browsePage", title: "Podcast", round: false,
    params: function(t) { return t.browseId ? { browseId: t.browseId } : null },
    id: function(t) { return t.browseId }
  }
}

// All, then the served chips ({ title, params }; one without params is left out).
function homeChipList(served) {
  var out = [HOME_ALL_CHIP]
  var list = served && typeof served.length === "number" ? served : []
  for (var i = 0; i < list.length; i++) {
    var chip = list[i]
    if (chip && chip.params && chip.title) out.push({ title: String(chip.title), params: String(chip.params) })
  }
  return out
}

// The params of the chip `dx` steps from `current` (wrapping; an unknown
// current counts as All).
function nextHomeChip(chips, current, dx) {
  var list = chips && chips.length ? chips : [HOME_ALL_CHIP]
  var at = 0
  for (var i = 0; i < list.length; i++) if (list[i].params === (current || "")) at = i
  var n = list.length
  return list[((at + (dx < 0 ? -1 : 1)) % n + n) % n].params
}

function isPlayAllRow(item) {
  return !!item && item.type === "action" && item.playAll === true
}

// A header per titled shelf (its strapline first when it has one: "Similar
// to" before an artist's name), its items (shelfIndex on every row; tracks
// numbered among the shelf's tracks, so Enter plays the shelf from there),
// See all when the shelf has a more endpoint, Play all when it has a play
// endpoint, and a trailing Load more when `opts.more` (more shelves to
// read). A shelf with nothing to show is skipped.
function homeRows(shelves, opts) {
  var rows = []
  var list = shelves || []
  for (var s = 0; s < list.length; s++) {
    var shelf = list[s] || {}
    var items = _validItems(shelf.items)
    if (!items.length && !shelf.more && !shelf.play) continue
    var title = shelf.title ? String(shelf.title) : ""
    if (title) rows.push({ header: true, title: shelf.strapline ? String(shelf.strapline) + " · " + title : title })
    var trackIndex = 0
    for (var i = 0; i < items.length; i++) {
      var row = { item: items[i], shelfIndex: s }
      if (items[i].type === "track") row.trackIndex = trackIndex++
      rows.push(row)
    }
    if (shelf.more) {
      rows.push({ item: { type: "more", title: title ? "See all " + title : "See all", endpoint: shelf.more,
                          sectionTitle: title }, shelfIndex: s })
    }
    var queue = shelfQueueParams(shelf)
    if (queue) {
      // The id names what the shelf plays, so the cursor keeps to this
      // shelf's row. Not its params: YouTube serves new ones on every load
      // of the same shelf (Service.playShelf reads them from the shelf).
      var plays = { videoId: queue.videoId || null, playlistId: queue.playlistId || null }
      rows.push({ item: { type: "action", id: HOME_PLAY_ALL + ":" + canonicalJson(plays), playAll: true,
                          title: title ? "Play all " + title : "Play all", icon: "󰐊" }, shelfIndex: s })
    }
  }
  if (opts && opts.more) rows.push({ item: { type: "loadMore", title: "Load more" } })
  return rows
}

function shelfTracks(shelves, index) {
  var shelf = shelves && shelves[index]
  return shelf ? _validItems(shelf.items).filter(function(item) { return item.type === "track" }) : []
}

// The worker's shelfQueue params of a shelf's Play all, or null.
function shelfQueueParams(shelf) {
  var play = shelf && shelf.play
  if (!play || !(play.videoId || play.playlistId)) return null
  var out = {}
  if (play.videoId) out.videoId = String(play.videoId)
  if (play.playlistId) out.playlistId = String(play.playlistId)
  if (play.params) out.params = String(play.params)
  return out
}

// `shelves` with the next page's after them, at most `max` in all:
// { shelves, capped } (capped: some were left out, the feed ends there).
function appendShelves(shelves, next, max) {
  var limit = max > 0 ? max : Infinity
  var out = (shelves || []).slice()
  var incoming = next || []
  var capped = false
  for (var i = 0; i < incoming.length; i++) {
    if (out.length >= limit) {
      capped = true
      break
    }
    if (incoming[i] && typeof incoming[i] === "object") out.push(incoming[i])
  }
  return { shelves: out, capped: capped || out.length >= limit }
}

// What playing a shelf's songs from a row reports as playing ("Playing from").
function shelfContext(shelf) {
  return { playlistId: null, title: shelf && shelf.title ? String(shelf.title) : "", radio: false }
}

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    HOME_PLAY_ALL: HOME_PLAY_ALL,
    HOME_ALL_CHIP: HOME_ALL_CHIP,
    homeChipList: homeChipList,
    nextHomeChip: nextHomeChip,
    isPlayAllRow: isPlayAllRow,
    homeRows: homeRows,
    shelfTracks: shelfTracks,
    shelfQueueParams: shelfQueueParams,
    appendShelves: appendShelves,
    shelfContext: shelfContext
  })
}

// ==== explore (S6) =========================================================================
// The Explore tab (key 5): the worker's `explore` page as rows. Its entry
// rows open New releases (a browse page), Charts and Moods & genres; then
// New albums & singles, Top songs (Premium), Moods & genres (eight
// categories, and a row that shows the rest: some are on no other page, not
// even its See all), Trending, New music videos and Popular episodes when
// served, each ending in See all (Top songs and Trending open their
// playlist, VL stripped, on the playlist page). A chart row (Top songs,
// Trending, Top artists) leads its subtitle with its rank and trend
// (itemSubtitle); a song keeps them only there, not in the queue
// (withoutRank, Service.remember). Charts is a page of kind `charts`
// in one country: O, its Country row or its M menu choose another from the
// countries the page serves, kept in prefs.chartsCountry; a Charts item
// without a country gets that one when it opens (Panel.push). Moods &
// genres (`moods`, kept a day, seen or not) lists every category, and a category
// (`mood`, a `mood` item: { type, id, title, params }) its playlists in
// titled shelves, each with a See all of a narrower category.

var EXPLORE_TTL_MS = 30 * 60 * 1000
var MOODS_TTL_MS = 24 * 60 * 60 * 1000
var GLOBAL_COUNTRY = "ZZ"
var CHARTS_COUNTRY_ID = "charts:country"
// Moods & genres on Explore: this many categories, then a row that shows all.
var MOOD_PREVIEW = 8
var EXPLORE_MOODS_ID = "explore:moods"
var MOOD_ICON = "󰠱"
var EXPLORE_ENTRY_ICONS = { FEmusic_new_releases: "󰎕", FEmusic_charts: "󰔵", FEmusic_moods_and_genres: MOOD_ICON }
var COUNTRY_ICON = "󰇧"
var TREND_ICONS = { up: "󰍠", down: "󰍝", neutral: "󰧟" }

PAGE_KINDS.charts = {
  method: "charts", title: "Charts", round: false,
  params: function(t) { return { country: chartsCountry(t.country) } },
  id: function(t) { return chartsCountry(t.country) },
  adapt: function(result) { return chartsPage(result) }
}
PAGE_KINDS.moods = {
  method: "moods", title: "Moods & genres", round: false, ttlMs: MOODS_TTL_MS,
  params: function() { return {} },
  id: function() { return "moods" }
}
// Only params: a category's title is not part of what the page is.
PAGE_KINDS.mood = {
  method: "mood", title: "", round: false,
  params: function(t) { return t.params ? { params: t.params } : null },
  id: function(t) { return t.params }
}

ROUTES.push({ browseId: "FEmusic_charts", target: function(ep) { return { type: "charts", title: ep.title || "" } } })
ROUTES.push({ browseId: "FEmusic_moods_and_genres", target: function(ep) { return { type: "moods", title: ep.title || "" } } })
ROUTES.push({ browseId: "FEmusic_moods_and_genres_category",
  target: function(ep) { return ep.params ? { type: "mood", id: ep.params, params: ep.params, title: ep.title || "" } : null } })

// ---- charts ---------------------------------------------------------------------------

// A chart country as prefs keep it: ZZ (Global) or two capitals.
function chartsCountry(value) {
  return normalizePref("chartsCountry", typeof value === "string" ? value.toUpperCase() : value)
}

function chartsItem(country, title) {
  return { type: "charts", country: chartsCountry(country), title: title || "Charts" }
}

// A Charts item opened without a country (Explore's entry row, a See all
// that leads to Charts) opens in the one chosen last.
function withChartsCountry(item, country) {
  if (!item || item.type !== "charts" || item.country) return item
  var out = {}
  for (var k in item) out[k] = item[k]
  out.country = chartsCountry(country)
  if (!out.title) out.title = "Charts"
  return out
}

// { code, label } the page was served in (Global while unknown).
function chartsCountryOf(page) {
  var c = page && page.country
  return c && c.code ? { code: String(c.code), label: String(c.label || c.code) } : { code: GLOBAL_COUNTRY, label: "Global" }
}

function chartsCountryTitle(page) {
  return "Country: " + chartsCountryOf(page).label
}

// The Charts page as shown: its Country row first (Enter opens the chooser).
function chartsPage(result) {
  if (!result || result.type !== "page") return result
  var out = {}
  for (var k in result) out[k] = result[k]
  var row = { type: "action", id: CHARTS_COUNTRY_ID, title: chartsCountryTitle(result), icon: COUNTRY_ICON, hint: keyHint("order") }
  out.sections = [{ type: "section", title: null, items: [row], more: null, play: null, layout: null, key: "country" }]
    .concat(result.sections || [])
  return out
}

// ItemMenu chooser rows of the countries the page serves, in their order
// (the suggested ones first); the one shown is ticked.
function countryChoices(page) {
  var shown = chartsCountryOf(page).code
  var list = page && Array.isArray(page.countries) ? page.countries : []
  return list.filter(function(c) { return c && /^[A-Z]{2}$/.test(String(c.code)) }).map(function(c) {
    var code = String(c.code)
    return { id: "country:" + code, title: String(c.label || code), icon: code === shown ? "󰄬" : COUNTRY_ICON, value: code }
  })
}

// "#1 󰍠": a chart row's rank and trend ("" for any other row).
function rankText(item) {
  if (!item || item.rank === undefined || item.rank === null || String(item.rank) === "") return ""
  var icon = TREND_ICONS[item.trend] || ""
  return "#" + String(item.rank) + (icon ? " " + icon : "")
}

// A song as the queue keeps it (Service.remember): a chart position means
// nothing there, so its rank and trend stay on the chart's rows. The same
// object when it has neither.
function withoutRank(track) {
  if (!track || typeof track !== "object" || (track.rank === undefined && track.trend === undefined)) return track
  var out = {}
  for (var key in track) if (key !== "rank" && key !== "trend") out[key] = track[key]
  return out
}

// ---- the Explore page -------------------------------------------------------------------

// An entry row (the page's navigation buttons): an action row that opens
// what its endpoint routes to.
function exploreEntry(more) {
  var ep = more && more.endpoint ? more.endpoint : {}
  var title = String((more && more.title) || "")
  return { type: "action", id: "explore:" + String(ep.browseId || title), title: title,
           icon: EXPLORE_ENTRY_ICONS[ep.browseId] || "󰅂",
           target: routeEndpoint({ browseId: ep.browseId, params: ep.params, pageType: ep.pageType, title: title }) }
}

function _isMood(item) {
  return !!item && item.type === "mood"
}

// The row that shows every mood category, or only the first MOOD_PREVIEW.
function moodsToggle(total, open) {
  return { type: "action", id: EXPLORE_MOODS_ID, title: open ? "Fewer moods and genres" : "All " + total + " moods and genres",
           icon: open ? "󰅃" : "󰅀" }
}

// pageRows, with the entry rows as action rows and the Moods & genres
// section cut to MOOD_PREVIEW categories unless opts.moodsOpen; the toggle
// row follows its last category shown.
function exploreRows(page, opts) {
  var sections = page && page.sections ? page.sections : []
  var open = !!(opts && opts.moodsOpen)
  var rows = pageRows(page)
  var moods = {}
  var out = []
  for (var i = 0; i < rows.length; i++) {
    var row = rows[i]
    var section = !row.header && row.sectionIndex !== undefined ? sections[row.sectionIndex] : null
    var key = section ? section.key : null
    if (key === "entries" && row.item.type === "more") {
      out.push({ item: exploreEntry(row.item), sectionIndex: row.sectionIndex })
      continue
    }
    if (key === "moods" && _isMood(row.item)) {
      var state = moods[row.sectionIndex]
      if (!state) state = moods[row.sectionIndex] = { total: _validItems(section.items).filter(_isMood).length, seen: 0 }
      state.seen++
      if (open || state.seen <= MOOD_PREVIEW) out.push(row)
      var last = state.seen === (open ? state.total : Math.min(MOOD_PREVIEW, state.total))
      if (last && state.total > MOOD_PREVIEW) out.push({ item: moodsToggle(state.total, open), sectionIndex: row.sectionIndex })
      continue
    }
    out.push(row)
  }
  return out
}

// Where the cursor goes once the moods toggle changed the rows: opened, onto
// the first category it showed (it takes the toggle's old place); closed,
// onto the toggle again. -1 without a toggle.
function exploreMoodsCursor(rows, open) {
  var list = rows || []
  var toggle = -1
  for (var i = 0; i < list.length && toggle < 0; i++)
    if (!list[i].header && list[i].item && list[i].item.id === EXPLORE_MOODS_ID) toggle = i
  if (!open || toggle < 0) return toggle
  var seen = 0
  for (var j = 0; j < toggle; j++) {
    var row = list[j]
    if (!row.header && row.sectionIndex === list[toggle].sectionIndex && _isMood(row.item) && ++seen > MOOD_PREVIEW) return j
  }
  return toggle
}

// What a section's songs play as ("Playing from" Top songs, with the chart
// playlist as autoplay's context when the section has one).
function exploreContext(page, index) {
  var section = page && page.sections ? page.sections[index] : null
  var id = section && section.more && section.more.browseId ? String(section.more.browseId) : ""
  return { playlistId: id.indexOf("VL") === 0 ? id.slice(2) : null, title: section && section.title ? String(section.title) : "Explore",
           radio: false }
}

// The status line above Explore's rows.
function exploreStatus(explore) {
  var e = explore || {}
  if (e.loading && !e.page) return "Loading Explore…"
  if (e.error) return String(e.error)
  return "New releases, charts, moods and genres"
}

// Charts (M): the Country row's twin. Explore (M): Refresh.
MENU_ACTIONS.push({ id: "chartsCountry", title: function(item, ctx) { return "Country: " + (ctx.countryLabel || "Global") },
  icon: COUNTRY_ICON, key: "order",
  applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "charts" },
  run: function(env) { if (env.owner && typeof env.owner.chooseCountry === "function") env.owner.chooseCountry() } })
MENU_ACTIONS.push({ id: "exploreRefresh", title: "Refresh", icon: "󰑐",
  applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "explore" },
  run: function(env) { env.owner.refresh(true) } })

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    EXPLORE_TTL_MS: EXPLORE_TTL_MS,
    MOODS_TTL_MS: MOODS_TTL_MS,
    GLOBAL_COUNTRY: GLOBAL_COUNTRY,
    CHARTS_COUNTRY_ID: CHARTS_COUNTRY_ID,
    MOOD_PREVIEW: MOOD_PREVIEW,
    EXPLORE_MOODS_ID: EXPLORE_MOODS_ID,
    MOOD_ICON: MOOD_ICON,
    TREND_ICONS: TREND_ICONS,
    chartsCountry: chartsCountry,
    chartsItem: chartsItem,
    withChartsCountry: withChartsCountry,
    chartsCountryOf: chartsCountryOf,
    chartsCountryTitle: chartsCountryTitle,
    chartsPage: chartsPage,
    countryChoices: countryChoices,
    rankText: rankText,
    withoutRank: withoutRank,
    exploreEntry: exploreEntry,
    moodsToggle: moodsToggle,
    exploreRows: exploreRows,
    exploreMoodsCursor: exploreMoodsCursor,
    exploreContext: exploreContext,
    exploreStatus: exploreStatus
  })
}

// ==== search (S7) ============================================================================
// Search is the web's search. All groups every kind of result in the order
// YouTube served them, under the top result card, and each group ends in a
// Show all row that switches to its filter. Songs, Albums, Artists and
// Playlists are chips; the other filters sit in a More chooser (the chip
// then shows the chosen one), and h / l cycle through all of them. One
// scope chip cycles YouTube Music, your library and your uploads (signed
// in). A filtered list loads more as the cursor nears its end. When YouTube
// corrected the spelling, a row searches the words exactly as typed (or
// offers its correction). An empty field lists your recent searches merged
// with your YouTube searches; while typing, completions come with up to
// five rich rows (artists, songs, albums) that open or play.

var SEARCH_GROUPS = [
  { key: "songs", title: "Songs" },
  { key: "videos", title: "Videos" },
  { key: "albums", title: "Albums" },
  { key: "artists", title: "Artists" },
  { key: "playlists", title: "Playlists" },
  { key: "podcasts", title: "Podcasts" },
  { key: "episodes", title: "Episodes" },
  { key: "profiles", title: "Profiles" }
]

// Every filter in h / l order ("" is All); `more` ones are in the More chooser.
var SEARCH_FILTERS = [
  { key: "", title: "All" },
  { key: "songs", title: "Songs" },
  { key: "albums", title: "Albums" },
  { key: "artists", title: "Artists" },
  { key: "playlists", title: "Playlists" },
  { key: "videos", title: "Videos", more: true },
  { key: "featured_playlists", title: "Featured playlists", more: true },
  { key: "community_playlists", title: "Community playlists", more: true },
  { key: "podcasts", title: "Podcasts", more: true },
  { key: "episodes", title: "Episodes", more: true },
  { key: "profiles", title: "Profiles", more: true }
]

var SEARCH_SCOPES = [
  { key: "", title: "YouTube Music", menuTitle: "Search YouTube Music" },
  { key: "library", title: "Library", menuTitle: "Search your library" },
  { key: "uploads", title: "Uploads", menuTitle: "Search your uploads" }
]

var SEARCH_SHOW_ALL = "search:showAll:"
var SEARCH_EXACT = "search:exact"
var SEARCH_CORRECTED = "search:corrected"
var SEARCH_MORE_CHIP = "more"

function _searchFilter(key) {
  for (var i = 0; i < SEARCH_FILTERS.length; i++) if (SEARCH_FILTERS[i].key === (key || "")) return SEARCH_FILTERS[i]
  return null
}

function _listOf(value) {
  var out = []
  for (var i = 0; value && typeof value.length === "number" && i < value.length; i++) {
    if (value[i] && typeof value[i] === "object") out.push(value[i])
  }
  return out
}

function searchFilterTitle(key) {
  var spec = _searchFilter(key)
  return spec ? spec.title : "Results"
}

function searchScopeTitle(scope) {
  for (var i = 0; i < SEARCH_SCOPES.length; i++) if (SEARCH_SCOPES[i].key === (scope || "")) return SEARCH_SCOPES[i].title
  return SEARCH_SCOPES[0].title
}

// Uploads take no filter; your library has no featured or community playlists.
function searchFilterAllowed(filter, scope) {
  if (!filter) return true
  if (!_searchFilter(filter) || scope === "uploads") return false
  return scope !== "library" || (filter !== "featured_playlists" && filter !== "community_playlists")
}

// The filter `dx` steps from `filter` among those the scope allows (wrapping).
function nextSearchFilter(filter, scope, dx) {
  var keys = SEARCH_FILTERS.filter(function(f) { return searchFilterAllowed(f.key, scope) }).map(function(f) { return f.key })
  var at = Math.max(0, keys.indexOf(filter || ""))
  var n = keys.length
  return keys[((at + (dx < 0 ? -1 : 1)) % n + n) % n]
}

function nextSearchScope(scope) {
  var keys = SEARCH_SCOPES.map(function(s) { return s.key })
  var at = Math.max(0, keys.indexOf(scope || ""))
  return keys[(at + 1) % keys.length]
}

// The chip row: All, Songs, Albums, Artists, Playlists, then More, which
// shows the chosen More filter. A chip the scope does not allow is disabled.
function searchChips(filter, scope) {
  var out = []
  for (var i = 0; i < SEARCH_FILTERS.length; i++) {
    var f = SEARCH_FILTERS[i]
    if (f.more) continue
    out.push({ key: f.key, title: f.title, selected: (filter || "") === f.key, enabled: searchFilterAllowed(f.key, scope), more: false })
  }
  var chosen = _searchFilter(filter)
  var isMore = !!chosen && chosen.more === true
  out.push({ key: SEARCH_MORE_CHIP, title: isMore ? chosen.title : "More", selected: isMore, enabled: scope !== "uploads", more: true })
  return out
}

// The More chooser's choices (ItemMenu.openChooser): the current one ticked.
function moreFilterChoices(filter, scope) {
  return SEARCH_FILTERS.filter(function(f) { return f.more && searchFilterAllowed(f.key, scope) }).map(function(f) {
    return { id: f.key, title: f.title, icon: f.key === filter ? "󰄬" : "󰈲" }
  })
}

function searchResultCount(result) {
  if (!result) return 0
  if (result.items && typeof result.items.length === "number") return result.items.length
  var count = _listOf(result.top).length
  for (var i = 0; i < SEARCH_GROUPS.length; i++) count += _listOf(result[SEARCH_GROUPS[i].key]).length
  return count
}

// "Showing results for radiohead" when YouTube corrected the query, else "".
function spellingStatus(result) {
  var sp = result ? result.spelling : null
  return sp && sp.kind === "corrected" && sp.corrected ? "Showing results for " + sp.corrected : ""
}

function _spellingRows(result) {
  var sp = result ? result.spelling : null
  if (!sp || !sp.corrected) return []
  if (sp.kind === "corrected" && sp.original)
    return [{ item: { type: "action", id: SEARCH_EXACT, title: "Search instead for \"" + sp.original + "\"", icon: "󰓆", query: String(sp.original) } }]
  if (sp.kind === "didYouMean")
    return [{ item: { type: "action", id: SEARCH_CORRECTED, title: "Did you mean \"" + sp.corrected + "\"?", icon: "󰓆", query: String(sp.corrected) } }]
  return []
}

// A search result as rows. A list (a filter, or the uploads scope): its
// items, then Load more while it has a cursor. All: the top result card's
// rows, then each group in `order` (YouTube's; the old fixed order when the
// result has none) ending in a Show all row for its filter. A spelling row
// comes first. opts: { scope }.
function searchRows(result, filter, opts) {
  var rows = []
  if (!result) return rows
  var o = opts || {}
  rows = rows.concat(_spellingRows(result))
  var i
  if (filter || (result.items && typeof result.items.length === "number")) {
    var items = _listOf(result.items)
    if (items.length) rows.push({ header: true, title: filter ? searchFilterTitle(filter) : o.scope === "uploads" ? "Uploads" : "Results" })
    for (i = 0; i < items.length; i++) rows.push({ item: items[i] })
    if ((result.cursor || result.hasMore) && result.capped !== true) rows.push({ item: { type: "loadMore", title: "Load more" } })
    return rows
  }
  var top = _listOf(result.top)
  if (top.length) rows.push({ header: true, title: "Top result" })
  for (i = 0; i < top.length; i++) rows.push({ item: top[i] })
  var order = result.order && typeof result.order.length === "number" ? result.order
    : ["songs", "albums", "artists", "playlists", "videos", "podcasts", "episodes", "profiles"]
  for (var g = 0; g < order.length; g++) {
    var group = null
    for (var k = 0; k < SEARCH_GROUPS.length; k++) if (SEARCH_GROUPS[k].key === order[g]) group = SEARCH_GROUPS[k]
    var list = group ? _listOf(result[group.key]) : []
    if (!list.length) continue
    rows.push({ header: true, title: group.title })
    for (i = 0; i < list.length; i++) rows.push({ item: list[i] })
    if (searchFilterAllowed(group.key, o.scope))
      rows.push({ item: { type: "action", id: SEARCH_SHOW_ALL + group.key, title: "Show all " + group.title.toLowerCase(), icon: "󰁔", filter: group.key } })
  }
  return rows
}

// The filter a Show all row sets ("" for any other row).
function showAllFilter(item) {
  return item && item.type === "action" && String(item.id || "").indexOf(SEARCH_SHOW_ALL) === 0 ? String(item.filter || "") : ""
}

// The empty field: your recent searches on this computer, then your other
// YouTube searches (signed in), each once. All can be removed with x; one
// that is also a YouTube search is `remote` (removed there too).
function pastSearchRows(local, remote, max) {
  var limit = max || 10
  var out = []
  var seen = {}
  function add(text, isRemote) {
    var value = String(text || "").trim()
    var k = value.toLowerCase()
    if (value === "") return
    if (seen[k]) {
      if (isRemote) seen[k].remote = true
      return
    }
    if (out.length >= limit) return
    var item = { type: "query", text: value, recent: true, removable: true }
    if (isRemote) item.remote = true
    seen[k] = item
    out.push(item)
  }
  var i
  for (i = 0; local && i < local.length; i++) add(local[i], false)
  for (i = 0; remote && i < remote.length; i++) add(remote[i] && typeof remote[i] === "object" ? remote[i].text : remote[i], true)
  return out.length ? [{ header: true, title: "Recent searches" }].concat(out.map(function(item) { return { item: item } })) : []
}

// While typing: the completions (suggestionRows), then up to five rich rows
// (songs play, artists and albums open), under one Suggestions header.
function typingRows(suggestions, selected, entities) {
  var rows = suggestionRows(suggestions, selected)
  var items = _listOf(entities).slice(0, 5)
  if (items.length && rows.length === 0) rows.push({ header: true, title: "Suggestions" })
  for (var i = 0; i < items.length; i++) rows.push({ item: items[i], suggestion: true })
  return rows
}

// searchMore's page added to a filtered result (appendPage), without the
// rows already listed: YouTube's next page can repeat a few results of the
// one before (the worker drops repeats within one reply).
function _searchRepeatKey(item) {
  if (!item || typeof item !== "object") return ""
  var id = item.videoId || item.browseId || item.playlistId || item.channelId
  return id ? String(item.type) + ":" + id : ""
}

function appendSearchPage(result, next, max) {
  if (!result || !next || !Array.isArray(next.items)) return appendPage(result, next, max)
  var seen = {}
  var listed = _listOf(result.items)
  for (var i = 0; i < listed.length; i++) seen[_searchRepeatKey(listed[i])] = true
  var fresh = []
  for (var j = 0; j < next.items.length; j++) {
    var key = _searchRepeatKey(next.items[j])
    if (key && seen[key]) continue
    if (key) seen[key] = true
    fresh.push(next.items[j])
  }
  var page = {}
  for (var k in next) page[k] = next[k]
  page.items = fresh
  return appendPage(result, page, max)
}

// Search's page menu (M): the scopes (signed in) and the More filters.
MENU_ACTIONS.push({ id: "searchMoreFilters", title: "More filters", icon: "󰈲",
  applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "search" && ctx.scope !== "uploads" },
  run: function(env) { env.owner.openMoreFilters() } })
SEARCH_SCOPES.forEach(function(scope) {
  MENU_ACTIONS.push({ id: "searchScope:" + scope.key, title: scope.menuTitle, icon: "󰍉",
    applies: function(item, ctx) {
      return ctx.page === true && ctx.pageKind === "search" && ctx.signedIn === true && (ctx.scope || "") !== scope.key
    },
    run: function(env) { env.owner.setScope(scope.key) } })
})

// An uploaded album or artist (the uploads scope) opens as its browse page
// until S10 gives uploads pages of their own (the item's `upload` says which).
// Its items are `type: "browse"`, so PAGE_KINDS.browse already reads them.

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    SEARCH_GROUPS: SEARCH_GROUPS,
    SEARCH_FILTERS: SEARCH_FILTERS,
    SEARCH_SCOPES: SEARCH_SCOPES,
    SEARCH_SHOW_ALL: SEARCH_SHOW_ALL,
    SEARCH_EXACT: SEARCH_EXACT,
    SEARCH_CORRECTED: SEARCH_CORRECTED,
    SEARCH_MORE_CHIP: SEARCH_MORE_CHIP,
    searchFilterTitle: searchFilterTitle,
    searchScopeTitle: searchScopeTitle,
    searchFilterAllowed: searchFilterAllowed,
    nextSearchFilter: nextSearchFilter,
    nextSearchScope: nextSearchScope,
    searchChips: searchChips,
    moreFilterChoices: moreFilterChoices,
    searchResultCount: searchResultCount,
    spellingStatus: spellingStatus,
    searchRows: searchRows,
    showAllFilter: showAllFilter,
    pastSearchRows: pastSearchRows,
    typingRows: typingRows,
    appendSearchPage: appendSearchPage
  })
}

// ==== playlist edit (S13) ==================================================================
// Everything the web lets you do to your own playlist, plus saving albums,
// playlists and the queue into playlists (the worker's ytm_edit). On your
// playlist's page: e opens the editor (title, description, privacy;
// Advanced: order, where new songs go, voting, collaborators), J / K move
// the song under the cursor (optimistic, put back when YouTube refuses),
// and Suggestions follow the songs (i adds the one under the cursor). The
// picker saves a whole album or playlist (Save to playlist) or the queue,
// and a song YouTube says is already there asks again (Enter again adds
// it anyway). Your playlists show each song's net votes when voting is on.

var PRIVACY_OPTIONS = [
  { value: "PRIVATE", title: "Private", icon: "󰌾" },
  { value: "UNLISTED", title: "Unlisted", icon: "󰌷" },
  { value: "PUBLIC", title: "Public", icon: "󰖟" }
]
// The orders when YouTube serves none (NEWEST_LAST is "Oldest first");
// served ones (settings.sortOptions) carry YouTube's own titles.
var SORT_OPTIONS = [
  { value: "MANUAL", title: "Manual" },
  { value: "NEWEST_FIRST", title: "Newest first" },
  { value: "NEWEST_LAST", title: "Oldest first" },
  { value: "TOP_VOTED", title: "Top voted" }
]
var VOTE_OPTIONS = [
  { value: "EVERYONE_CAN_VOTE", title: "Everyone can vote" },
  { value: "COLLABORATORS_ONLY", title: "Collaborators only" },
  { value: "OFF", title: "Off" }
]
var ADD_TO_TOP_OPTIONS = [
  { value: false, title: "At the end" },
  { value: true, title: "At the top" }
]
var NOT_REPORTED = "not reported by YouTube"
var DESCRIPTION_NOTE = "YouTube Music cannot clear a description: write a new one, or leave it as it was"
var TITLE_MAX = 150
var SUGGESTIONS_REFRESH_ID = "suggestions:refresh"
var EDITOR_IDS = { save: "editor:save", privacy: "editor:privacy", order: "editor:order", addToTop: "editor:addToTop",
                   vote: "editor:vote", invite: "editor:invite", close: "editor:close" }
// The editor's option rows and the setting each one cycles.
var EDITOR_OPTIONS = { "editor:privacy": "privacy", "editor:order": "sortOrder", "editor:addToTop": "addToTop",
                       "editor:vote": "voteOption" }

// The options a setting cycles through. With `initial` (editorInitial),
// the ones YouTube serves: the orders of its sort menu under their served
// titles, and the voting options it offers (Collaborators only once
// collaboration is on). Unknown: every option.
function editOptions(key, initial) {
  var a = initial || {}
  if (key === "sortOrder") {
    var served = (a.sortOptions || []).filter(function(o) { return o && typeof o.value === "string" && o.title })
    return served.length > 0 ? served.map(function(o) { return { value: o.value, title: String(o.title) } })
      : SORT_OPTIONS
  }
  if (key === "voteOption") {
    if (!Array.isArray(a.voteOptions) || a.voteOptions.length === 0) return VOTE_OPTIONS
    return VOTE_OPTIONS.filter(function(o) { return a.voteOptions.indexOf(o.value) >= 0 || o.value === a.voteOption })
  }
  return key === "privacy" ? PRIVACY_OPTIONS : key === "addToTop" ? ADD_TO_TOP_OPTIONS : []
}

// The option's title; NOT_REPORTED for null (YouTube did not say) or a
// value the list does not know.
function optionTitle(options, value) {
  for (var i = 0; i < (options || []).length; i++) if (options[i].value === value) return options[i].title
  return NOT_REPORTED
}

// The value dx places on (cycling); from an unknown value, the first (dx > 0)
// or the last one.
function nextOption(options, value, dx) {
  var list = options || []
  if (list.length === 0) return value
  var at = -1
  for (var i = 0; i < list.length; i++) if (list[i].value === value) at = i
  if (at < 0) return list[dx < 0 ? list.length - 1 : 0].value
  return list[(at + (dx < 0 ? -1 : 1) + list.length) % list.length].value
}

// "" when YouTube takes the title, else why not (the worker's own rules).
function playlistTitleProblem(title) {
  var text = String(title || "").trim()
  if (text === "") return "A playlist needs a title"
  if (text.indexOf("<") >= 0 || text.indexOf(">") >= 0) return "A title cannot contain < or >"
  if (text.length > TITLE_MAX) return "A title can have at most " + TITLE_MAX + " characters"
  return ""
}

// ---- the editor ----------------------------------------------------------------------------

// What the editor starts from: the page's header, then the owner settings
// read from YouTube (playlistSettings) where it serves them. null means
// "not reported" (the editor says so and sends nothing for it).
function editorInitial(target, settings) {
  var t = target || {}
  var s = settings || {}
  var pick = function(key, fallback) { return s[key] !== null && s[key] !== undefined ? s[key] : fallback }
  return {
    title: String(pick("title", t.title || "")),
    description: String(pick("description", t.description || "")),
    privacy: pick("privacy", t.privacy || null),
    sortOrder: pick("sortOrder", null),
    sortTitle: pick("sortTitle", null),
    sortOptions: pick("sortOptions", null),
    addToTop: pick("addToTop", null),
    voteOption: pick("voteOption", null),
    voteOptions: pick("voteOptions", null),
    collaboration: pick("collaboration", null),
    collaborators: pick("collaborators", t.collaborators || null)
  }
}

// editPlaylist's params for what changed between `initial` and `current`
// ({ title, description, privacy, sortOrder, addToTop, voteOption }), with
// `problem` ("" when it can be sent) and `count` (fields changed). An
// emptied description is a problem: YouTube keeps the old one.
function editorChanges(initial, current) {
  var a = initial || {}
  var b = current || {}
  var params = {}
  var problem = ""
  var title = String(b.title || "").trim()
  if (title !== String(a.title || "").trim()) {
    problem = playlistTitleProblem(title)
    params.title = title
  }
  var description = String(b.description || "").trim()
  if (description !== String(a.description || "").trim()) {
    if (description === "") problem = problem || DESCRIPTION_NOTE
    else params.description = description
  }
  var keys = ["privacy", "sortOrder", "addToTop", "voteOption"]
  for (var i = 0; i < keys.length; i++) {
    var value = b[keys[i]]
    if (value !== null && value !== undefined && value !== a[keys[i]]) params[keys[i]] = value
  }
  return { params: params, problem: problem, count: Object.keys(params).length }
}

// The editor's list (keyboard: j / k, Enter, h / l cycle an option row):
// Save, Privacy, then Advanced: Order, New songs, Voting, Get invite link,
// Close collaboration (only while YouTube says collaboration is on: closing
// it otherwise is refused) and who collaborates. `current` as in
// editorChanges; opts: { busy }.
function editorRows(initial, current, opts) {
  var a = initial || {}
  var b = current || {}
  var o = opts || {}
  var changes = editorChanges(a, b)
  var option = function(id, key, label) {
    var options = editOptions(key, a)
    var value = b[key]
    var title = label + ": " + (key === "sortOrder" && value === null && a.sortTitle ? a.sortTitle
                                 : optionTitle(options, value))
    if (value !== null && value !== undefined && value !== a[key]) title += " (changed)"
    var icon = key === "privacy" && value ? optionIcon(options, value) : "󰒿"
    if (key === "addToTop") icon = "󰖃"
    if (key === "voteOption") icon = "󰨟"
    return { item: { type: "action", id: id, title: title, icon: icon, hint: "h / l" } }
  }
  var save = o.busy ? "Saving…" : changes.count > 0 ? "Save " + (changes.count === 1 ? "1 change" : changes.count + " changes")
    : "No changes to save"
  var rows = [
    { item: { type: "action", id: EDITOR_IDS.save, title: save, icon: "󰆓", hint: "Enter" } },
    option(EDITOR_IDS.privacy, "privacy", "Privacy"),
    { header: true, title: "Advanced" },
    option(EDITOR_IDS.order, "sortOrder", "Order"),
    option(EDITOR_IDS.addToTop, "addToTop", "New songs go"),
    option(EDITOR_IDS.vote, "voteOption", "Voting"),
    { item: { type: "action", id: EDITOR_IDS.invite, title: "Get an invite link for collaborators (copied)", icon: "󰀐" } }
  ]
  if (a.collaboration === true)
    rows.push({ item: { type: "action", id: EDITOR_IDS.close, title: "Close collaboration", icon: "󰅖" } })
  if (a.collaborators) rows.push({ item: { type: "message", title: "Collaborators: " + a.collaborators } })
  return rows
}

function optionIcon(options, value) {
  for (var i = 0; i < (options || []).length; i++) if (options[i].value === value) return options[i].icon || ""
  return ""
}

// Why the invite link cannot be had now ("" when it can): a private
// playlist cannot have collaborators (YouTube answers 400), and a new
// privacy has to be saved first.
function inviteProblem(initial, current) {
  var saved = initial ? initial.privacy : null
  if (current && current.privacy && current.privacy !== saved) return "Save the new privacy first"
  if (saved === "PRIVATE") return "A private playlist cannot have collaborators: make it Unlisted or Public and save"
  return ""
}

// The editor's `initial` once collaboration was turned on or off: Close
// collaboration follows it, and so does Collaborators only voting, which
// YouTube offers only while collaboration is on.
function withCollaboration(initial, on) {
  var next = {}
  for (var key in initial || {}) next[key] = initial[key]
  next.collaboration = on === true
  if (Array.isArray(next.voteOptions)) {
    var offered = next.voteOptions.filter(function(v) { return v !== "COLLABORATORS_ONLY" })
    if (on === true) offered.push("COLLABORATORS_ONLY")
    next.voteOptions = offered
  }
  return next
}

// ---- invite links ----------------------------------------------------------------------------

// { playlistId, token } of a playlist invite link
// (https://music.youtube.com/playlist?list=<id>&jct=<token>), else null.
// The worker's parse_invite takes the same links.
function parseInviteUrl(text) {
  var s = String(text || "").trim()
  var m = /^https?:\/\/([A-Za-z0-9.-]+)\/playlist\/?\?([^#]*)/i.exec(s)
  if (!m) return null
  var host = m[1].toLowerCase()
  if (host !== "youtube.com" && !/\.youtube\.com$/.test(host)) return null
  var params = {}
  var pairs = m[2].split("&")
  for (var i = 0; i < pairs.length; i++) {
    var eq = pairs[i].indexOf("=")
    var key = eq < 0 ? pairs[i] : pairs[i].slice(0, eq)
    var value = eq < 0 ? "" : pairs[i].slice(eq + 1)
    try {
      value = decodeURIComponent(value.replace(/\+/g, " "))
    } catch (e) {
      value = ""
    }
    if (value !== "" && !Object.prototype.hasOwnProperty.call(params, key)) params[key] = value
  }
  var id = _stripVl(params.list || "")
  var token = params.jct || ""
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(id) || !/^[A-Za-z0-9_-]{1,512}$/.test(token)) return null
  return { playlistId: id, token: token }
}

// ---- votes -----------------------------------------------------------------------------------

// A song's net votes on a playlist with voting on ("+3", "-1"); "" when the
// playlist has none, and for 0 (voting is on for every song of a playlist
// that has it, mostly at 0: a column of zeros says nothing).
function voteLabel(track) {
  var vote = track && track.voteStatus
  if (!vote || typeof vote.votes !== "number" || !isFinite(vote.votes) || vote.votes === 0) return ""
  return vote.votes > 0 ? "+" + vote.votes : String(vote.votes)
}

// ---- moving songs ----------------------------------------------------------------------------

// The order that keeps J / K from moving songs, by its served title (""
// when none): YouTube answers a move in a sorted playlist with success and
// keeps its order. Unknown (null settings) does not block.
function orderBlocksMoves(settings) {
  if (!settings) return ""
  if (settings.sortOrder && settings.sortOrder !== "MANUAL")
    return settings.sortTitle ? String(settings.sortTitle) : optionTitle(editOptions("sortOrder", settings), settings.sortOrder)
  if (!settings.sortOrder && settings.sortTitle) return String(settings.sortTitle)
  return ""
}

// The song at page.tracks[index] one place down (delta > 0) or up: { page,
// index, params } with the page in the new order (optimistic), the song's
// new index and movePlaylistItem's params (it goes before its new
// successor, or to the end); or { problem } when it cannot move ("" for
// nothing to say). A page that shows part of its list cannot send its
// last song further down: the successor is not loaded.
function moveTrack(page, index, delta) {
  var tracks = page && page.tracks ? page.tracks : []
  var i = Number(index)
  var d = delta > 0 ? 1 : -1
  if (!(i >= 0 && i < tracks.length)) return { problem: "" }
  var j = i + d
  if (j < 0) return { problem: "It is the first song already" }
  if (j >= tracks.length) return { problem: page.truncated ? "Load all songs to move it further down" : "It is the last song already" }
  var track = tracks[i]
  var successor = d < 0 ? tracks[j] : tracks[i + 2]
  if (d > 0 && !successor && page.truncated) return { problem: "Load all songs to move it further down" }
  if (!track.setVideoId || (successor && !successor.setVideoId)) return { problem: "This song cannot be moved; refresh the page" }
  var next = tracks.slice()
  next.splice(i, 1)
  next.splice(j, 0, track)
  var moved = {}
  for (var key in page) moved[key] = page[key]
  moved.tracks = next
  return { page: moved, index: j,
           params: { playlistId: _stripVl(page.playlistId || page.id), setVideoId: track.setVideoId,
                     beforeSetVideoId: successor ? successor.setVideoId : null } }
}

// ---- suggestions -----------------------------------------------------------------------------

// The Suggestions section of your playlist's page: a header, the songs
// (rows with suggestion: true; Enter plays one, i adds it), a message while
// loading or after a failure, and Refresh suggestions. state: { items,
// loading, error }.
function playlistSuggestionRows(state) {
  var s = state || {}
  var items = s.items || []
  var rows = [{ header: true, title: "Suggestions" }]
  for (var i = 0; i < items.length; i++) rows.push({ item: items[i], suggestion: true })
  if (s.loading && items.length === 0) rows.push({ item: { type: "message", title: "Loading suggestions…" } })
  else if (s.error) rows.push({ item: { type: "message", title: "Couldn't load suggestions: " + s.error } })
  else if (items.length === 0 && s.loaded) rows.push({ item: { type: "message", title: "YouTube has no suggestions for this playlist" } })
  rows.push({ item: { type: "action", id: SUGGESTIONS_REFRESH_ID, title: s.loading ? "Refreshing suggestions…" : "Refresh suggestions",
                      icon: "󰑐" } })
  return rows
}

// A collection page's rows with the Suggestions section after the songs
// (and Load all), before the page's shelves (which start with a header).
function withSuggestionRows(rows, state) {
  var list = rows || []
  var at = list.length
  for (var i = 0; i < list.length; i++) {
    if (list[i].header) {
      at = i
      break
    }
  }
  return list.slice(0, at).concat(playlistSuggestionRows(state), list.slice(at))
}

// The suggestions left after one was added (by videoId).
function withoutSuggestion(items, track) {
  return (items || []).filter(function(t) { return !track || t.videoId !== track.videoId })
}

// ---- the picker: a whole album or playlist, duplicates --------------------------------------

// The playlist whose songs Save to playlist (or Copy to new playlist)
// adds: an album's audio playlist, a playlist's id (VL stripped); "" for
// anything else (a song, a mix, Liked Music, Episodes for later).
function sourcePlaylistOf(item) {
  var kind = menuKind(item)
  if (kind === "album") return item.playlistId ? _stripVl(item.playlistId) : ""
  if (kind !== "playlist") return ""
  var id = _stripVl(item.playlistId)
  return isSpecialPlaylist({ playlistId: id }) ? "" : id
}

// What the picker's next add sends for `playlistId`: duplicates once
// YouTube said the songs are there and Enter was pressed again on it.
function pickerAddOptions(state, playlistId) {
  return { duplicates: !!state && !!playlistId && state.duplicateOf === playlistId }
}

// total: the songs being added; 0 for a whole album or playlist (YouTube
// does not say which of its songs are there).
function duplicatePrompt(count, total) {
  if (total === 0) return "Some of its songs are already there: Enter again to add them anyway"
  if (count > 0 && total > 1) return (count === total ? "All " + total + " songs are" : count + " of " + total + " songs are")
    + " already there: Enter again to add anyway"
  return "Already there: Enter again to add anyway"
}

// The picker after an add's reply: { duplicateOf, status, done }. A
// DUPLICATE arms that playlist for "add anyway"; anything else disarms.
function pickerAfterReply(state, playlistId, reply, total) {
  if (reply && reply.ok) return { duplicateOf: "", status: "", done: true }
  var error = reply && reply.error ? reply.error : null
  if (error && error.code === "DUPLICATE" && !error.unknown) {
    var ids = Array.isArray(error.videoIds) ? error.videoIds.length : 0
    return { duplicateOf: playlistId, status: duplicatePrompt(ids, total === undefined ? 1 : total), done: false }
  }
  return { duplicateOf: "", status: error ? String(error.message || "") : "", done: false }
}

// ---- menu entries and keys -------------------------------------------------------------------

var _PLAYLIST_EDIT_ACTIONS = [
  { id: "saveToPlaylist", title: "Save to playlist", icon: "󰐒", key: "addToPlaylist",
    applies: function(item, ctx) { return ctx.signedIn === true && sourcePlaylistOf(item) !== "" },
    run: function(env) { env.panel.openSourcePicker(env.item, false) } },
  { id: "copyToNewPlaylist", title: "Copy to new playlist", icon: "󰆏",
    applies: function(item, ctx) { return ctx.signedIn === true && sourcePlaylistOf(item) !== "" },
    run: function(env) { env.panel.openSourcePicker(env.item, true) } },
  { id: "addSuggestion", title: "Add to this playlist", icon: "󰐕", key: "addSuggestion",
    applies: function(item, ctx) { return ctx.suggestion === true && menuKind(item) === "track" },
    run: function(env) { if (env.index >= 0 && env.owner && typeof env.owner.addSuggestion === "function") env.owner.addSuggestion(env.index) } },
  { id: "editPlaylist", title: "Edit playlist", icon: "󰏫", key: "editPlaylist",
    applies: function(item, ctx) { return ctx.page === true && ctx.owned === true && menuKind(item) === "playlist" },
    run: function(env) { env.panel.openEditor() } }
]

_insertAfter(MENU_ACTIONS, "addToPlaylist", _PLAYLIST_EDIT_ACTIONS)
// Now (M): the queue header's Save twin.
MENU_ACTIONS.push(
  { id: "saveQueue", title: "Save the queue to a playlist", icon: "󰐒",
    applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "now" && ctx.queueLength > 0 && ctx.signedIn === true },
    run: function(env) { env.panel.saveQueue() } })

_insertAfter(KEYS, "queueMove", [
  { id: "addSuggestion", group: "Song", keys: ["i"], title: "add the suggestion under the cursor to your playlist" }
])
_insertAfter(KEYS, "find", [
  { id: "editPlaylist", group: "Menus", keys: ["e"], title: "edit your playlist: details, order, voting, collaborators" }
])
_keyEntry("queueMove").title = "move a queued song, or a song of your own playlist, down / up"
_keyEntry("addToPlaylist").title = "add to playlist (the song, or a whole album or playlist row)"
_keyEntry("back").title = "leave the field → close the picker, editor or menu → clear the filter → back → close"

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    PRIVACY_OPTIONS: PRIVACY_OPTIONS,
    SORT_OPTIONS: SORT_OPTIONS,
    VOTE_OPTIONS: VOTE_OPTIONS,
    ADD_TO_TOP_OPTIONS: ADD_TO_TOP_OPTIONS,
    NOT_REPORTED: NOT_REPORTED,
    DESCRIPTION_NOTE: DESCRIPTION_NOTE,
    SUGGESTIONS_REFRESH_ID: SUGGESTIONS_REFRESH_ID,
    EDITOR_IDS: EDITOR_IDS,
    EDITOR_OPTIONS: EDITOR_OPTIONS,
    optionTitle: optionTitle,
    optionIcon: optionIcon,
    nextOption: nextOption,
    editOptions: editOptions,
    playlistTitleProblem: playlistTitleProblem,
    editorInitial: editorInitial,
    editorChanges: editorChanges,
    editorRows: editorRows,
    inviteProblem: inviteProblem,
    withCollaboration: withCollaboration,
    parseInviteUrl: parseInviteUrl,
    voteLabel: voteLabel,
    orderBlocksMoves: orderBlocksMoves,
    moveTrack: moveTrack,
    playlistSuggestionRows: playlistSuggestionRows,
    withSuggestionRows: withSuggestionRows,
    withoutSuggestion: withoutSuggestion,
    sourcePlaylistOf: sourcePlaylistOf,
    pickerAddOptions: pickerAddOptions,
    duplicatePrompt: duplicatePrompt,
    pickerAfterReply: pickerAfterReply
  })
}

// ==== recommendations (S14) ================================================================
// The web's recommendation controls, through the worker's feedback and
// setTasteProfile:
// - Pin to / Unpin from Listen again on songs (S3's listenAgainTokens),
//   Home's cards (albums, playlists, artists, podcasts: the tokens their
//   menu came with) and on the page menu (M) of an album, playlist, Liked
//   Music or artist (the header's pin, withPagePin). YouTube may ignore an
//   unpin; the notice says so.
// - Not interested and Don't recommend artist (or channel) on Home's cards:
//   a second press within 4 s, then the card leaves Home at once and comes
//   back when YouTube says no (withoutHomeItem / withHomeItems). Like the
//   web's "Removed from Home" toast, Home's menu (M) then offers Undo for
//   a while (HOME_UNDO_MS): the card's own Undo token puts it back.
// - The taste builder: Library's account menu (M) opens "Add artists you
//   like", a page of YouTube's suggestions. f finds, Enter marks or
//   unmarks an artist here, and the "Add N artists" row sends the marked
//   ones after a second press. Adding is all the API does: the page says
//   so, as YouTube does not say which artists were picked before.
// What is pinned lives in the Store ("recommendations (S14)"), keyed by
// pinKey; Service.fillMenuState puts it in a menu's ctx as `pinned`.

// The shell keeps a Home card's feedback tokens out of what it saves, as
// it does a song's (withoutTokens).
TOKEN_KEYS.push("feedback")

var PIN_ICON = "󰐃"
var LISTEN_AGAIN_ID = "FEmusic_listen_again"
var TASTE_ADD_ID = "taste:add"
var TASTE_ARTIST_PREFIX = "taste:artist:"
var TASTE_TITLE = "Add artists you like"
var TASTE_NOTE = "Marked artists are added to what YouTube recommends from. Adding is all this can do:"
  + " YouTube does not say which artists you picked before, and taking one out is done on the web."
// The most one add sends (the worker's TASTE_MAX), above every suggestion
// list seen (889 names).
var TASTE_ADD_MAX = 1000
// How long Home's menu offers Undo after a Not interested or Don't recommend.
var HOME_UNDO_MS = 120000

// ---- Listen again pins ------------------------------------------------------------------

// Where the Store keeps an item's pin: its kind and id ("" when it has none).
function pinKey(item) {
  if (!item || typeof item !== "object") return ""
  var kind = menuKind(item)
  if (kind === "track") return "track:" + item.videoId
  if (kind === "album") return "album:" + (item.browseId || _stripVl(item.playlistId))
  if (kind === "playlist" || kind === "mix") return "playlist:" + _stripVl(item.playlistId)
  if (kind === "liked") return "playlist:LM"
  if (kind === "artist") return "artist:" + item.channelId
  var id = item.browseId || item.channelId || item.videoId
  return item.type && id ? String(item.type) + ":" + String(id) : ""
}

// The item's Listen again tokens ({ pin, unpin }), or null. Rows from a
// disk copy and songs in the queue have none.
function pinTokens(item) {
  var tokens = item && typeof item === "object" ? item.listenAgainTokens : null
  return tokens && typeof tokens === "object" && (tokens.pin || tokens.unpin) ? tokens : null
}

// Pinned now: an edit made here (`map`, by pinKey), else what the item
// says (its row, or its page header's pin).
function pinnedState(item, map) {
  var key = pinKey(item)
  var known = key ? stateOf(map, key) : null
  return known !== null ? known : !!item && typeof item === "object" && item.pinned === true
}

// A menu's ctx.pinned when filled (Service.fillMenuState), else the item's own.
function _pinnedIn(item, ctx) {
  return ctx && ctx.pinned !== undefined && ctx.pinned !== null ? ctx.pinned === true : pinnedState(item, null)
}

// The menu offers Pin when the item is not pinned and has the pin token,
// Unpin when it is and has the unpin token.
function canPin(item, pinned) {
  var tokens = pinTokens(item)
  if (!tokens) return false
  return pinned === true ? !!tokens.unpin : !!tokens.pin
}

// A page header's pin (album, playlist, Liked Music, artist): { pinned,
// listenAgainTokens } (a disk copy has no tokens), or null.
function pagePin(page) {
  var pin = page && page.header && typeof page.header === "object" ? page.header.pin : null
  return pin && typeof pin === "object" ? pin : null
}

// The page item (CollectionView.pageItem, a copy) with its header's pin,
// so the page menu's entry pins the page itself. A disk copy's header has
// no tokens: the item keeps the ones it was opened with (a Home card's).
function withPagePin(item, page) {
  var pin = pagePin(page)
  if (!item || !pin) return item
  item.pinned = pin.pinned === true
  if (pinTokens(pin)) item.listenAgainTokens = pin.listenAgainTokens
  return item
}

// The page a row's or a page menu's token came from, which the worker
// then drops from its cache: an album, a playlist (Liked Music is LM), an
// artist; null for anything else (Home's cards, Search, the queue).
function pinSource(ref) {
  var kind = ref ? ref.kind : ""
  var params = ref && ref.params ? ref.params : {}
  if (kind === "album" && params.browseId) return { browseId: String(params.browseId) }
  if (kind === "playlist" && params.playlistId) return { playlistId: _stripVl(params.playlistId) }
  if (kind === "liked") return { playlistId: "LM" }
  if (kind === "artist" && params.channelId) return { channelId: String(params.channelId) }
  return null
}

// The worker's feedback params: one token, and the page it came from.
function feedbackParams(token, source) {
  var params = { tokens: [String(token)] }
  var from = source || {}
  for (var i = 0; i < 3; i++) {
    var key = ["browseId", "playlistId", "channelId"][i]
    if (from[key]) params[key] = String(from[key])
  }
  return params
}

// ---- Home: Not interested, Don't recommend ----------------------------------------------

// A Home card's { notInterested, dontRecommend } tokens, or null.
function homeFeedback(item) {
  var feedback = item && typeof item === "object" ? item.feedback : null
  return feedback && typeof feedback === "object" && (feedback.notInterested || feedback.dontRecommend) ? feedback : null
}

// which: "notInterested" | "dontRecommend"; "" when the card has none.
function homeFeedbackToken(item, which) {
  var feedback = homeFeedback(item)
  return feedback && feedback[which] ? String(feedback[which]) : ""
}

// The token that takes `which` back (the toast's Undo); "" when none.
function homeUndoToken(item, which) {
  return which === "notInterested" || which === "dontRecommend" ? homeFeedbackToken(item, which + "Undo") : ""
}

// Home's menu entry title for the Undo the Store holds (its card's title).
function homeUndoTitle(title) {
  return title ? "Undo: put " + title + " back on Home" : "Undo: put it back on Home"
}

// As the web titles it: a podcast's or an episode's is the channel's.
function dontRecommendTitle(item) {
  var type = item && typeof item === "object" ? item.type : ""
  return type === "podcast" || type === "episode" ? "Don't recommend channel" : "Don't recommend artist"
}

// Home's shelves without every card that is `item` (by itemKey):
// { shelves, removed: [{ shelf, index, item }] }; the same shelves array
// when nothing matched.
function withoutHomeItem(shelves, item) {
  var list = shelves || []
  var key = itemKey(item)
  var removed = []
  if (!key) return { shelves: list, removed: removed }
  var out = list.map(function(shelf, s) {
    var items = shelf && shelf.items && typeof shelf.items.length === "number" ? shelf.items : null
    if (!items) return shelf
    var kept = []
    for (var i = 0; i < items.length; i++) {
      if (itemKey(items[i]) === key) removed.push({ shelf: s, index: i, item: items[i] })
      else kept.push(items[i])
    }
    if (kept.length === items.length) return shelf
    var copy = {}
    for (var k in shelf) copy[k] = shelf[k]
    copy.items = kept
    return copy
  })
  return { shelves: removed.length ? out : list, removed: removed }
}

// The cards `removed` took out, back where they were (on a shelf that is
// still there and does not hold them again).
function withHomeItems(shelves, removed) {
  var out = (shelves || []).slice()
  var back = (removed || []).slice().sort(function(a, b) { return a.shelf - b.shelf || a.index - b.index })
  for (var i = 0; i < back.length; i++) {
    var shelf = out[back[i].shelf]
    if (!shelf || !shelf.items) continue
    var items = Array.prototype.slice.call(shelf.items)
    var key = itemKey(back[i].item)
    if (items.some(function(it) { return itemKey(it) === key && it !== back[i].item })) continue
    if (items.indexOf(back[i].item) >= 0) continue
    items.splice(Math.min(back[i].index, items.length), 0, back[i].item)
    var copy = {}
    for (var k in shelf) copy[k] = shelf[k]
    copy.items = items
    out[back[i].shelf] = copy
  }
  return out
}

// ---- the taste builder ------------------------------------------------------------------

// What Library's account menu opens (a new object each time).
function tasteItem() {
  return { type: "taste", title: TASTE_TITLE }
}

PAGE_KINDS.taste = {
  method: "tasteProfile", title: TASTE_TITLE, round: false,
  params: function() { return {} },
  id: function() { return "taste" },
  adapt: function(result) { return tastePage(result) }
}

// The worker's { artists: [{ name }] } as the page CollectionView shows:
// the names in served order, once each.
function tastePage(result) {
  var names = []
  var seen = {}
  var list = result && result.artists && typeof result.artists.length === "number" ? result.artists : []
  for (var i = 0; i < list.length; i++) {
    var name = list[i] && list[i].name ? String(list[i].name) : ""
    if (name === "" || seen["a:" + name]) continue
    seen["a:" + name] = true
    names.push(name)
  }
  return { type: "taste", kind: "taste", id: "taste", title: TASTE_TITLE,
           subtitle: names.length === 1 ? "1 suggested artist" : groupedCount(names.length) + " suggested artists",
           artists: names }
}

// Marks are a map of "a:" + name (a name may be any text, "constructor" too).
function isTasteMarked(marked, name) {
  return !!marked && marked["a:" + name] === true
}

// Marking `name` is allowed: an unmark always, a mark while fewer than
// TASTE_ADD_MAX of the page's names are marked.
function canTasteMark(page, marked, name) {
  return isTasteMarked(marked, name) || tasteMarked(page, marked).length < TASTE_ADD_MAX
}

function toggleTasteMark(marked, name) {
  var next = {}
  for (var k in marked || {}) next[k] = marked[k]
  if (next["a:" + name] === true) delete next["a:" + name]
  else next["a:" + name] = true
  return next
}

// The marked names still on the page, in its order.
function tasteMarked(page, marked) {
  var names = page && page.artists ? page.artists : []
  return names.filter(function(name) { return isTasteMarked(marked, name) })
}

// The name an artist row's action id names ("" for any other id).
function tasteNameOf(id) {
  var text = String(id || "")
  return text.indexOf(TASTE_ARTIST_PREFIX) === 0 ? text.slice(TASTE_ARTIST_PREFIX.length) : ""
}

function isTasteAction(id) {
  return id === TASTE_ADD_ID || tasteNameOf(id) !== ""
}

// Where Down or Enter in the find field goes on the taste page: the first
// artist shown (never the Add row above them), or -1.
function firstTasteArtist(rows) {
  var list = rows || []
  for (var i = 0; i < list.length; i++) if (list[i] && list[i].item && tasteNameOf(list[i].item.id) !== "") return i
  return -1
}

function _artistsText(n) {
  return n === 1 ? "1 artist" : groupedCount(n) + " artists"
}

// opts: { armed (the first press was made), sending }.
function tasteAddTitle(count, opts) {
  var o = opts || {}
  if (o.sending) return "Adding " + _artistsText(count) + "…"
  if (!(count > 0)) return "Add artists: mark the ones you like with Enter first"
  if (o.armed) return "Really add " + _artistsText(count) + "? They can't be taken out here. Enter again"
  return "Add " + _artistsText(count)
}

// The taste page's rows: the Add row, the note, then the suggestions (the
// ones every word of opts.find matches), each an action row that Enter
// marks or unmarks. opts: { marked, find, armed, sending }.
function tasteRows(page, opts) {
  var o = opts || {}
  var names = page && page.artists ? page.artists : []
  var count = tasteMarked(page, o.marked).length
  var rows = [
    { item: { type: "action", id: TASTE_ADD_ID, title: tasteAddTitle(count, o), icon: "󰐕",
              hint: count > 0 && !o.sending ? groupedCount(count) + " marked" : "" } },
    { item: { type: "message", title: TASTE_NOTE } }
  ]
  var words = foldText(o.find || "").split(/\s+/).filter(function(w) { return w !== "" })
  var shown = 0
  for (var i = 0; i < names.length; i++) {
    var folded = foldText(names[i])
    if (words.length && !words.every(function(w) { return folded.indexOf(w) >= 0 })) continue
    var on = isTasteMarked(o.marked, names[i])
    rows.push({ item: { type: "action", id: TASTE_ARTIST_PREFIX + names[i], title: names[i], icon: on ? "󰄲" : "󰄱",
                        hint: on ? "marked" : "" } })
    shown++
  }
  if (names.length === 0) rows.push({ item: { type: "message", title: "YouTube has no suggestions right now" } })
  else if (shown === 0) rows.push({ item: { type: "message", title: "No suggested artist matches" } })
  return rows
}

// ---- menu entries -------------------------------------------------------------------------

var _RECOMMENDATION_ACTIONS = [
  { id: "pin", title: function(item, ctx) { return _pinnedIn(item, ctx) ? "Unpin from Listen again" : "Pin to Listen again" },
    icon: PIN_ICON,
    applies: function(item, ctx) { return ctx.signedIn === true && canPin(item, _pinnedIn(item, ctx)) },
    run: function(env) {
      env.service.setPinned(env.item, !_pinnedIn(env.item, env.ctx), editCallbackOf(env.owner), env.ctx.pinSource || null)
    } },
  // Home's cards only (their tokens are Home's); Home's menu has the Undo.
  { id: "notInterested", title: "Not interested", icon: "󰜺", confirm: "Really not interested? Choose again",
    applies: function(item, ctx) {
      return ctx.signedIn === true && ctx.pageKind === "home" && ctx.page !== true && homeFeedbackToken(item, "notInterested") !== ""
    },
    run: function(env) { env.service.homeFeedback(env.item, "notInterested") } },
  { id: "dontRecommend", title: function(item) { return dontRecommendTitle(item) }, icon: "󰍶",
    confirm: "Really stop recommending it? Choose again",
    applies: function(item, ctx) {
      return ctx.signedIn === true && ctx.pageKind === "home" && ctx.page !== true && homeFeedbackToken(item, "dontRecommend") !== ""
    },
    run: function(env) { env.service.homeFeedback(env.item, "dontRecommend") } }
]

// After Copy link, as in the web's menus.
_insertAfter(MENU_ACTIONS, "share", _RECOMMENDATION_ACTIONS)

// Home (M): the last Not interested or Don't recommend's Undo (ctx.homeUndo
// is its card's title, from Store.homeUndoTitle), first: before Refresh.
MENU_ACTIONS.splice(Math.max(0, MENU_ACTIONS.indexOf(menuAction("refresh"))), 0,
  { id: "homeUndo", title: function(item, ctx) { return homeUndoTitle(ctx.homeUndo) }, icon: "󰕌",
    applies: function(item, ctx) {
      return ctx.page === true && ctx.pageKind === "home" && ctx.signedIn === true && typeof ctx.homeUndo === "string"
    },
    run: function(env) { env.service.undoHomeFeedback() } })

// Library (M): the account row's taste builder, before Sign out.
_insertAfter(MENU_ACTIONS, "refresh", [
  { id: "tasteProfile", title: TASTE_TITLE, icon: "󰢙",
    applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "library" && ctx.signedIn === true },
    run: function(env) { env.panel.push(tasteItem()) } }
])

// The taste page (M): the Add row's twin and unmarking (f is S3's find).
MENU_ACTIONS.push(
  { id: "tasteAdd", title: function(item, ctx) { return tasteAddTitle(ctx.tasteMarked, {}) }, icon: "󰐕",
    confirm: "Really add them? They can't be taken out here. Choose again",
    applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "taste" && ctx.tasteMarked > 0 },
    run: function(env) { if (env.owner && typeof env.owner.addTaste === "function") env.owner.addTaste(true) } },
  { id: "tasteClear", title: "Unmark every artist", icon: "󰄱",
    applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "taste" && ctx.tasteMarked > 0 },
    run: function(env) { if (env.owner && typeof env.owner.clearTaste === "function") env.owner.clearTaste() } })

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    PIN_ICON: PIN_ICON,
    LISTEN_AGAIN_ID: LISTEN_AGAIN_ID,
    TASTE_ADD_ID: TASTE_ADD_ID,
    TASTE_ARTIST_PREFIX: TASTE_ARTIST_PREFIX,
    TASTE_TITLE: TASTE_TITLE,
    TASTE_NOTE: TASTE_NOTE,
    TASTE_ADD_MAX: TASTE_ADD_MAX,
    HOME_UNDO_MS: HOME_UNDO_MS,
    pinKey: pinKey,
    pinTokens: pinTokens,
    pinnedState: pinnedState,
    canPin: canPin,
    pagePin: pagePin,
    withPagePin: withPagePin,
    pinSource: pinSource,
    feedbackParams: feedbackParams,
    homeFeedback: homeFeedback,
    homeFeedbackToken: homeFeedbackToken,
    homeUndoToken: homeUndoToken,
    homeUndoTitle: homeUndoTitle,
    dontRecommendTitle: dontRecommendTitle,
    withoutHomeItem: withoutHomeItem,
    withHomeItems: withHomeItems,
    tasteItem: tasteItem,
    tastePage: tastePage,
    isTasteMarked: isTasteMarked,
    canTasteMark: canTasteMark,
    toggleTasteMark: toggleTasteMark,
    tasteMarked: tasteMarked,
    tasteNameOf: tasteNameOf,
    isTasteAction: isTasteAction,
    firstTasteArtist: firstTasteArtist,
    tasteAddTitle: tasteAddTitle,
    tasteRows: tasteRows
  })
}

// ==== history (S15) =========================================================================
// Plays recorded in YouTube Music history, as the web records them, and
// entries removed from it. Recording is prefs.recordHistory, off until you
// turn it on (Library's account row or its M menu); while it is off, the
// first play of REPORT_AFTER_SEC shows a one-time notice saying how. Each
// play of a queue entry (a start-file, or a repeat-one loop: loopRestart)
// counts once it has played REPORT_AFTER_SEC by mpv's time-pos
// (shouldReport), and joins the pending plays. They go to the worker's
// reportPlays in one call when the worker is up for other work anyway,
// when REPORT_COUNT are waiting or the oldest has waited REPORT_WAIT_MS
// (flushDue), and when a panel opens, so the worker is never started for
// one song. They are kept in pending-plays.json (PENDING_MAX, none older
// than PENDING_TTL_MS). An unknown outcome drops the batch: a missing play
// beats one counted twice (afterReport). History rows carry S3's
// historyToken: x pressed twice, or the row menu, removes one at once and
// puts it back when YouTube says no.

var REPORT_AFTER_SEC = 10
var REPORT_COUNT = 5
var REPORT_WAIT_MS = 15 * 60 * 1000
// After a passing failure (offline, rate limited) a play waits this long.
var REPORT_RETRY_MS = 15 * 60 * 1000
// Plays per reportPlays call (two requests each).
var REPORT_CALL_MAX = 20
var PENDING_MAX = 200
var PENDING_TTL_MS = 24 * 3600 * 1000
// A repeat-one loop is a restart near the start this long after the play began.
var LOOP_START_SEC = 1.5
var LOOP_MIN_MS = 2000
var PLAY_STATE_VERSION = 1
var _VIDEO_ID = /^[A-Za-z0-9_-]{11}$/

// One play of a queue entry: mpv's entry id and the shell's count of plays
// started (every start-file and every loop counts one up), so a song played
// again, or looped, is a new play.
function playKey(entryId, serial) {
  return String(entryId) + ":" + String(serial || 0)
}

function _reportedHas(reported, key) {
  if (!reported) return false
  if (Array.isArray(reported)) return reported.indexOf(key) >= 0
  return reported[key] === true
}

// This play goes to history now: `on` (recording, signed in), the entry
// ({ key, videoId }) is a YouTube video that has played REPORT_AFTER_SEC,
// and `reported` (keys, as a map or a list) does not hold this play yet.
function shouldReport(entry, timePos, reported, on) {
  if (on !== true || !entry || !entry.key || !_VIDEO_ID.test(String(entry.videoId || ""))) return false
  return typeof timePos === "number" && isFinite(timePos) && timePos >= REPORT_AFTER_SEC && !_reportedHas(reported, entry.key)
}

// A playback-restart that is a repeat-one loop: loop-file on, back near the
// start, and the play began at least LOOP_MIN_MS ago (the restart that
// follows a file's own start comes sooner). A seek to the very start while
// repeating one song counts as a loop too.
function loopRestart(repeatMode, timePos, playedMs) {
  return repeatMode === "one" && typeof timePos === "number" && isFinite(timePos) && timePos < LOOP_START_SEC
    && Number(playedMs) >= LOOP_MIN_MS
}

// When to read time-pos next (ms): once REPORT_AFTER_SEC will have played
// at `speed`, plus a moment; between 1 and 15 s.
function playCheckMs(timePos, speed) {
  var pos = typeof timePos === "number" && isFinite(timePos) && timePos > 0 ? timePos : 0
  var rate = typeof speed === "number" && isFinite(speed) && speed > 0 ? Math.max(0.25, speed) : 1
  var ms = Math.round(Math.max(0, REPORT_AFTER_SEC - pos) / rate * 1000) + 300
  return Math.max(1000, Math.min(15000, ms))
}

// ---- pending plays ------------------------------------------------------------------------
// A pending play is { videoId, at (ms since the epoch), retryAt?, sent? },
// oldest first. `sent` marks the batch a reportPlays call has out (written
// to the file before the call): never in another batch, and dropped when
// the file is read again, since a reload or crash mid-call left its outcome
// unknown.

function _pendingPlay(play, now) {
  if (!play || typeof play !== "object" || !_VIDEO_ID.test(String(play.videoId || ""))) return null
  if (typeof play.at !== "number" || !isFinite(play.at) || now - play.at >= PENDING_TTL_MS || play.at > now + 60000) return null
  var out = { videoId: String(play.videoId), at: play.at }
  if (typeof play.retryAt === "number" && isFinite(play.retryAt) && play.retryAt > now) out.retryAt = play.retryAt
  if (play.sent === true) out.sent = true
  return out
}

// Valid plays younger than PENDING_TTL_MS, at most PENDING_MAX (the newest).
function prunePending(pending, now) {
  var out = []
  var list = Array.isArray(pending) ? pending : []
  for (var i = 0; i < list.length; i++) {
    var play = _pendingPlay(list[i], now)
    if (play) out.push(play)
  }
  return out.length > PENDING_MAX ? out.slice(out.length - PENDING_MAX) : out
}

function withPendingPlay(pending, videoId, now) {
  return prunePending((Array.isArray(pending) ? pending : []).concat([{ videoId: videoId, at: now }]), now)
}

// A pending play's identity (the same song played twice is two plays).
function pendingKey(play) {
  return play ? String(play.videoId) + "@" + String(play.at) : ""
}

// The plays neither out in a call nor waiting after a passing failure.
function readyPlays(pending, now) {
  return _unsent(pending).filter(function(play) {
    return !(typeof play.retryAt === "number" && play.retryAt > now)
  })
}

function _unsent(pending) {
  return (Array.isArray(pending) ? pending : []).filter(function(play) { return !!play && play.sent !== true })
}

// The pending plays with `batch` marked as out in a call.
function markSent(pending, batch) {
  var keys = {}
  for (var i = 0; i < (batch || []).length; i++) keys[pendingKey(batch[i])] = true
  return (Array.isArray(pending) ? pending : []).map(function(play) {
    if (!keys[pendingKey(play)]) return play
    var out = {}
    for (var key in play) out[key] = play[key]
    out.sent = true
    return out
  })
}

// Time to send the pending plays: some are ready, and the worker is up
// anyway (`workerUp`), or REPORT_COUNT are ready, or the oldest ready one
// has waited REPORT_WAIT_MS. (Opening a panel sends them regardless.)
function flushDue(pending, now, workerUp) {
  var ready = readyPlays(prunePending(pending, now), now)
  if (ready.length === 0) return false
  if (workerUp === true || ready.length >= REPORT_COUNT) return true
  var oldest = ready[0].at
  for (var i = 1; i < ready.length; i++) oldest = Math.min(oldest, ready[i].at)
  return now - oldest >= REPORT_WAIT_MS
}

// When time alone makes a flush due (ms since the epoch), 0 with nothing pending.
function nextFlushAt(pending, now) {
  var list = _unsent(prunePending(pending, now))
  var next = 0
  for (var i = 0; i < list.length; i++) {
    var due = Math.max(list[i].at + REPORT_WAIT_MS, list[i].retryAt || 0)
    if (next === 0 || due < next) next = due
  }
  return next
}

// What one reportPlays call sends: the oldest ready plays (`force`, as when
// a panel opens: the waiting ones too), at most REPORT_CALL_MAX.
function reportBatch(pending, now, force) {
  var list = prunePending(pending, now)
  return (force === true ? _unsent(list) : readyPlays(list, now)).slice(0, REPORT_CALL_MAX)
}

// The shell's timeout for a call of `count` plays (a player request and a ping each).
function reportTimeoutMs(count) {
  return 15000 + 4000 * Math.max(1, Number(count) || 1)
}

var _PASSING = ["NETWORK", "RATE_LIMITED", "TIMEOUT", "WORKER_EXITED"]

// The pending plays after the reportPlays reply for `batch` (matched by
// pendingKey: plays added meanwhile stay). A play YouTube took goes, and so
// does one that failed for good; a passing failure (a result's retry, or a
// call that failed offline before anything was sent) waits REPORT_RETRY_MS.
// An unknown outcome (the worker stopped or timed out mid-call) drops the
// batch, as does any other failed call. Signed out or expired: every
// pending play goes (they were made under that session). `waiting` counts
// the batch's plays that wait after a passing failure: the shell sends no
// next batch then (it would meet the same throttle or outage).
// { pending, reported, dropped, waiting }
function afterReport(pending, batch, reply, now) {
  var list = Array.isArray(pending) ? pending : []
  var sent = Array.isArray(batch) ? batch : []
  var outcome = {}
  var reported = 0
  var dropped = 0
  var waiting = 0
  var i
  if (reply && reply.ok) {
    var results = reply.result && Array.isArray(reply.result.results) ? reply.result.results : []
    for (i = 0; i < sent.length; i++) {
      var result = results[i]
      var same = !!result && result.videoId === sent[i].videoId
      if (same && result.ok === true) reported++
      else if (same && result.retry === true) waiting++
      else dropped++
      outcome[pendingKey(sent[i])] = same && result.ok !== true && result.retry === true ? "retry" : "done"
    }
  } else {
    var error = reply && reply.error ? reply.error : {}
    var unknown = !!reply && (reply.unknown === true || error.unknown === true)
    if (!unknown && (error.code === "AUTH_REQUIRED" || error.code === "AUTH_EXPIRED"))
      return { pending: [], reported: 0, dropped: list.length, waiting: 0 }
    var later = !unknown && _PASSING.indexOf(error.code) >= 0
    for (i = 0; i < sent.length; i++) outcome[pendingKey(sent[i])] = later ? "retry" : "done"
    if (later) waiting = sent.length
    else dropped = sent.length
  }
  var next = []
  for (i = 0; i < list.length; i++) {
    var what = outcome[pendingKey(list[i])]
    if (what === "done") continue
    if (what === "retry") next.push({ videoId: list[i].videoId, at: list[i].at, retryAt: now + REPORT_RETRY_MS })
    else next.push(list[i])
  }
  return { pending: prunePending(next, now), reported: reported, dropped: dropped, waiting: waiting }
}

// pending-plays.json: { version, plays, notice } (notice: the one-time
// notice about recording was shown).
function parsePlayState(text, now) {
  var saved = null
  try {
    saved = JSON.parse(text)
  } catch (e) {
    saved = null
  }
  var ok = !!saved && typeof saved === "object" && saved.version === PLAY_STATE_VERSION
  // A batch the last run had out: its outcome is unknown, so it goes.
  return { plays: ok ? _unsent(prunePending(saved.plays, now)) : [], notice: ok && saved.notice === true }
}

function playStateText(state, now) {
  var s = state || {}
  return JSON.stringify({ version: PLAY_STATE_VERSION, plays: prunePending(s.plays, now), notice: s.notice === true }) + "\n"
}

// ---- notices --------------------------------------------------------------------------------

function historyNotice() {
  return { title: "Plays are not added to your YouTube Music history",
           body: "To add songs played for 10 s or more, as the web does: Library, " + keyHint("pageMenu")
             + ", Record plays in history." }
}

function recordHistoryNotice(on) {
  return on
    ? { title: "Recording plays in your history",
        body: "Songs played for 10 s or more go to YouTube Music history. Library, " + keyHint("pageMenu")
          + " turns it off." }
    : { title: "Plays are no longer recorded", body: "Songs you play stay out of YouTube Music history." }
}

// The first press's prompt (x, or a click on the row's remove button).
function removeHistoryPrompt(click) {
  return click ? "Click again to remove from history" : "Press " + keyHint("remove") + " again to remove from history"
}

// ---- History rows ----------------------------------------------------------------------------

// A History row's token (S3; fresh pages only, a disk copy has none), or "".
function historyTokenOf(track) {
  var token = track && track.type === "track" ? track.historyToken : null
  return typeof token === "string" && token !== "" ? token : ""
}

function removeHistoryParams(track) {
  var token = historyTokenOf(track)
  return token ? { tokens: [token] } : null
}

function _sameHistoryEntry(a, b) {
  var ta = historyTokenOf(a)
  var tb = historyTokenOf(b)
  if (ta !== "" || tb !== "") return ta === tb
  return !!a && !!b && a.videoId === b.videoId && String(a.played || "") === String(b.played || "")
}

// Where the History page holds `track` (by its token), or -1.
function historyEntryIndex(page, track) {
  var tracks = page && Array.isArray(page.tracks) ? page.tracks : []
  for (var i = 0; i < tracks.length; i++) if (_sameHistoryEntry(tracks[i], track)) return i
  return -1
}

function _historyWithTracks(page, tracks) {
  var next = {}
  for (var key in page) next[key] = page[key]
  next.tracks = tracks
  next.trackCount = tracks.length
  next.subtitle = tracks.length + " songs"
  return next
}

// The History page without one entry; the same page when it is not there.
function withoutHistoryEntry(page, track) {
  var at = historyEntryIndex(page, track)
  if (at < 0) return page
  return _historyWithTracks(page, page.tracks.slice(0, at).concat(page.tracks.slice(at + 1)))
}

// The entry back at `index` (YouTube said no); the same page when it holds
// it already (a reload brought it back).
function withHistoryEntry(page, track, index) {
  if (!page || !track || historyEntryIndex(page, track) >= 0) return page
  var tracks = Array.isArray(page.tracks) ? page.tracks : []
  var at = Math.max(0, Math.min(tracks.length, Number(index) >= 0 ? Number(index) : 0))
  return _historyWithTracks(page, tracks.slice(0, at).concat([track], tracks.slice(at)))
}

// ---- menu entries and keys -------------------------------------------------------------------

// History rows: Remove from history (choose again within 4 s), the x twin.
// S16 sets ctx.canRemoveHistory false for a brand account (YouTube does
// not remove history entries there).
_insertAfter(MENU_ACTIONS, "remove", [
  { id: "removeHistory", title: "Remove from history", icon: "󰅖", key: "remove",
    confirm: "Really remove from history? Choose again",
    applies: function(item, ctx) {
      return ctx.signedIn === true && ctx.page !== true && ctx.pageKind === "history" && historyTokenOf(item) !== ""
        && ctx.canRemoveHistory !== false
    },
    run: function(env) {
      if (env.index >= 0 && env.owner && typeof env.owner.removeFromHistory === "function") env.owner.removeFromHistory(env.index, true)
    } }
])

// Library (M), before Sign out: Record plays in history, on and off
// (ctx.recordHistory from LibraryView.pageMenu).
;(function() {
  var at = MENU_ACTIONS.length
  for (var i = 0; i < MENU_ACTIONS.length; i++) if (MENU_ACTIONS[i].id === "signOut") at = i
  MENU_ACTIONS.splice(at, 0, {
    id: "recordHistory",
    title: function(item, ctx) { return ctx.recordHistory === true ? "Stop recording plays in history" : "Record plays in history" },
    icon: "󰋚",
    applies: function(item, ctx) { return ctx.page === true && ctx.pageKind === "library" && ctx.signedIn === true },
    run: function(env) { env.service.setRecordHistory(env.ctx.recordHistory !== true) } })
})()

_keyEntry("remove").title = "remove (queue, own playlist, Liked Music, recent search; History: press twice)"

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    REPORT_AFTER_SEC: REPORT_AFTER_SEC,
    REPORT_COUNT: REPORT_COUNT,
    REPORT_WAIT_MS: REPORT_WAIT_MS,
    REPORT_RETRY_MS: REPORT_RETRY_MS,
    REPORT_CALL_MAX: REPORT_CALL_MAX,
    PENDING_MAX: PENDING_MAX,
    PENDING_TTL_MS: PENDING_TTL_MS,
    LOOP_START_SEC: LOOP_START_SEC,
    LOOP_MIN_MS: LOOP_MIN_MS,
    playKey: playKey,
    shouldReport: shouldReport,
    loopRestart: loopRestart,
    playCheckMs: playCheckMs,
    prunePending: prunePending,
    withPendingPlay: withPendingPlay,
    pendingKey: pendingKey,
    readyPlays: readyPlays,
    markSent: markSent,
    flushDue: flushDue,
    nextFlushAt: nextFlushAt,
    reportBatch: reportBatch,
    reportTimeoutMs: reportTimeoutMs,
    afterReport: afterReport,
    parsePlayState: parsePlayState,
    playStateText: playStateText,
    historyNotice: historyNotice,
    recordHistoryNotice: recordHistoryNotice,
    removeHistoryPrompt: removeHistoryPrompt,
    historyTokenOf: historyTokenOf,
    removeHistoryParams: removeHistoryParams,
    historyEntryIndex: historyEntryIndex,
    withoutHistoryEntry: withoutHistoryEntry,
    withHistoryEntry: withHistoryEntry
  })
}

// ==== account (S16) =========================================================================
// Library's account menu (M) gains Switch account, Audio quality and Content
// location, before S15's Record plays and Sign out.
// - Switch account lists the session's identities from the worker's
//   `accounts` (your own account, its brand accounts and, when YouTube lists
//   them, other Google logins); the worker's `switchAccount` acts as the one
//   chosen from then on (saved by the worker without names). Identities of
//   another Google login switch by signing in again as that login
//   (`bin/ytm auth --authuser N`): rewriting X-Goog-AuthUser could not be
//   checked live (one login), so AUTHUSER_SWITCH stays false. Names stay in
//   the shell's memory: never in prefs, files or logs.
// - Audio quality is prefs.quality: the worker's resolver format (the
//   setting `quality`) and mpv's ytdl-format (QUALITY_FORMATS, the same
//   strings as backend/ytm_streams.py and bin/ytm-mpv).
// - Content location is prefs.location: one of ytmusicapi's
//   SUPPORTED_LOCATIONS (LOCATIONS, not the chart countries) or "" for
//   YouTube's own guess, sent as the worker's client setting `location`;
//   choosing one Charts has (CHART_COUNTRIES) also opens Charts there next.
// - Until the worker says which identity it acts as (and while it is
//   stopped), identity.json tells (savedIdentity, shownIdentity). When
//   YouTube stops answering as a brand account or login in use, the session
//   reads as expired; Library then offers going back to your own account.

var QUALITIES = ["low", "normal", "high"]
var QUALITY_FORMATS = {
  low: "bestaudio[abr<=70]/worstaudio",
  normal: "bestaudio[abr<=130]/bestaudio",
  high: "bestaudio[acodec=opus]/bestaudio/best"
}
var ACCOUNT_ICON = "󰀄"
var BRAND_ICON = "󰇗"
var SWITCH_ICON = "󰀙"
var ADD_ACCOUNT_ICON = "󰀔"
var QUALITY_ICON = "󰐵"
var LOCATION_ICON = "󰍎"
var CHOSEN_ICON = "󰄬"
// The account list is asked again after this long (names and photos change rarely).
var ACCOUNTS_TTL_MS = 10 * 60 * 1000
// Another Google login: switch in place (true) or sign in again as it (false).
var AUTHUSER_SWITCH = false
var ANOTHER_LOGIN_ID = "account:another"
// The countries Charts serves besides Global (ZZ), as its country menu lists
// them (tests/backend/test_account.py keeps them in step with the recorded
// Charts page). A location outside them leaves prefs.chartsCountry alone:
// Charts would answer Global.
var CHART_COUNTRIES = [
  "AE", "AR", "AT", "AU", "BE", "BO", "BR", "CA", "CH", "CL", "CO", "CR", "CZ", "DE", "DK", "DO", "EC", "EE",
  "EG", "ES", "FI", "FR", "GB", "GT", "HK", "HN", "HU", "ID", "IE", "IL", "IN", "IS", "IT", "JP", "KE", "KR",
  "LU", "MX", "MY", "NG", "NI", "NL", "NO", "NZ", "PA", "PE", "PH", "PL", "PT", "PY", "RO", "RS", "RU", "SA",
  "SE", "SG", "SV", "TH", "TR", "TW", "TZ", "UA", "UG", "US", "UY", "VN", "ZA", "ZW"
]

// ytmusicapi 1.12.2 SUPPORTED_LOCATIONS with English names, by name
// (tests/backend/test_account.py keeps the codes in step with ytmusicapi).
var LOCATIONS = [
  ["DZ", "Algeria"], ["AR", "Argentina"], ["AU", "Australia"], ["AT", "Austria"], ["AZ", "Azerbaijan"],
  ["BH", "Bahrain"], ["BD", "Bangladesh"], ["BY", "Belarus"], ["BE", "Belgium"], ["BO", "Bolivia"],
  ["BA", "Bosnia and Herzegovina"], ["BR", "Brazil"], ["BG", "Bulgaria"], ["KH", "Cambodia"], ["CA", "Canada"],
  ["CL", "Chile"], ["CO", "Colombia"], ["CR", "Costa Rica"], ["HR", "Croatia"], ["CY", "Cyprus"],
  ["CZ", "Czechia"], ["DK", "Denmark"], ["DO", "Dominican Republic"], ["EC", "Ecuador"], ["EG", "Egypt"],
  ["SV", "El Salvador"], ["EE", "Estonia"], ["FI", "Finland"], ["FR", "France"], ["GE", "Georgia"],
  ["DE", "Germany"], ["GH", "Ghana"], ["GR", "Greece"], ["GT", "Guatemala"], ["HN", "Honduras"],
  ["HK", "Hong Kong"], ["HU", "Hungary"], ["IS", "Iceland"], ["IN", "India"], ["ID", "Indonesia"],
  ["IQ", "Iraq"], ["IE", "Ireland"], ["IL", "Israel"], ["IT", "Italy"], ["JM", "Jamaica"], ["JP", "Japan"],
  ["JO", "Jordan"], ["KZ", "Kazakhstan"], ["KE", "Kenya"], ["KW", "Kuwait"], ["LA", "Laos"],
  ["LV", "Latvia"], ["LB", "Lebanon"], ["LY", "Libya"], ["LI", "Liechtenstein"], ["LT", "Lithuania"],
  ["LU", "Luxembourg"], ["MY", "Malaysia"], ["MT", "Malta"], ["MX", "Mexico"], ["ME", "Montenegro"],
  ["MA", "Morocco"], ["NP", "Nepal"], ["NL", "Netherlands"], ["NZ", "New Zealand"], ["NI", "Nicaragua"],
  ["NG", "Nigeria"], ["MK", "North Macedonia"], ["NO", "Norway"], ["OM", "Oman"], ["PK", "Pakistan"],
  ["PA", "Panama"], ["PG", "Papua New Guinea"], ["PY", "Paraguay"], ["PE", "Peru"], ["PH", "Philippines"],
  ["PL", "Poland"], ["PT", "Portugal"], ["PR", "Puerto Rico"], ["QA", "Qatar"], ["RO", "Romania"],
  ["RU", "Russia"], ["SA", "Saudi Arabia"], ["SN", "Senegal"], ["RS", "Serbia"], ["SG", "Singapore"],
  ["SK", "Slovakia"], ["SI", "Slovenia"], ["ZA", "South Africa"], ["KR", "South Korea"], ["ES", "Spain"],
  ["LK", "Sri Lanka"], ["SE", "Sweden"], ["CH", "Switzerland"], ["TW", "Taiwan"], ["TZ", "Tanzania"],
  ["TH", "Thailand"], ["TN", "Tunisia"], ["TR", "Türkiye"], ["UG", "Uganda"], ["UA", "Ukraine"],
  ["AE", "United Arab Emirates"], ["GB", "United Kingdom"], ["US", "United States"], ["UY", "Uruguay"],
  ["VE", "Venezuela"], ["VN", "Vietnam"], ["YE", "Yemen"], ["ZW", "Zimbabwe"]
]

// ---- audio quality ----------------------------------------------------------------------

function qualityMode(value) {
  return QUALITIES.indexOf(value) >= 0 ? value : "high"
}

function qualityTitle(quality) {
  var q = qualityMode(quality)
  return q === "low" ? "Low" : q === "normal" ? "Normal" : "High"
}

// yt-dlp's format for a quality (mpv's ytdl-format).
function qualityFormat(quality) {
  return QUALITY_FORMATS[qualityMode(quality)]
}

// ItemMenu chooser rows for the qualities; the one set is ticked.
function qualityChoices(quality) {
  var on = qualityMode(quality)
  var about = { low: "Low (about 70 kbps, saves data)", normal: "Normal (about 128 kbps)",
                high: "High (the best stream, about 130 to 160 kbps)" }
  return QUALITIES.map(function(q) {
    return { id: "quality-" + q, title: about[q], icon: q === on ? CHOSEN_ICON : QUALITY_ICON, value: q }
  })
}

// ---- content location --------------------------------------------------------------------

// "" (YouTube's own guess) or a code YouTube Music supports.
function locationCode(value) {
  var code = typeof value === "string" ? value.toUpperCase() : ""
  for (var i = 0; i < LOCATIONS.length; i++) if (LOCATIONS[i][0] === code) return code
  return ""
}

function locationTitle(code) {
  var c = locationCode(code)
  for (var i = 0; i < LOCATIONS.length; i++) if (LOCATIONS[i][0] === c) return LOCATIONS[i][1]
  return "Default"
}

// ItemMenu chooser rows: Default, then every supported location by name;
// the one set is ticked.
function locationChoices(code) {
  var on = locationCode(code)
  var rows = [{ id: "location:", title: "Default (YouTube decides from your connection)",
                icon: on === "" ? CHOSEN_ICON : LOCATION_ICON, value: "" }]
  for (var i = 0; i < LOCATIONS.length; i++) {
    var c = LOCATIONS[i][0]
    rows.push({ id: "location:" + c, title: LOCATIONS[i][1], hint: c, icon: c === on ? CHOSEN_ICON : LOCATION_ICON, value: c })
  }
  return rows
}

// The Charts country a content location opens Charts in next: the
// location when Charts has it, else "" (Charts stays where it was).
function chartsCountryForLocation(code) {
  var c = locationCode(code)
  return c !== "" && CHART_COUNTRIES.indexOf(c) >= 0 ? c : ""
}

// The worker settings this section owns, added to `base` (workerSettings):
// quality always, location only when one is set (none is YouTube's guess).
function accountSettings(prefs, base) {
  var out = base || {}
  out.quality = qualityMode(prefs && prefs.quality)
  var location = locationCode(prefs && prefs.location)
  if (location !== "") out.location = location
  return out
}

// ---- identities --------------------------------------------------------------------------

// Whether the worker acts as an identity other than the session's own
// account (identity: the worker's { key, brand, own, authUser }).
function otherIdentity(identity) {
  return !!identity && identity.own === false
}

// identity.json's text as { brand, own, authUser } (no id kept), checked as
// the worker's ytm_auth.read_identity() checks it: missing, damaged or
// odd means the session's own account.
function savedIdentity(text) {
  var own = { brand: false, own: true, authUser: null }
  var saved = null
  try { saved = text ? JSON.parse(text) : null } catch (e) { return own }
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) return own
  var pageId = saved.pageId === undefined ? null : saved.pageId
  var authUser = saved.authUser === undefined ? null : saved.authUser
  if (pageId !== null && !(typeof pageId === "string" && /^[0-9]{1,40}$/.test(pageId))) return own
  if (authUser !== null && !(typeof authUser === "number" && Math.floor(authUser) === authUser && authUser >= 0 && authUser <= 9))
    return own
  if (pageId === null && authUser === null) return own
  return { brand: pageId !== null, own: false, authUser: authUser }
}

// The identity in use as the shell knows it: the worker's while it runs
// and has said (reported); else identity.json's (saved: savedIdentity(),
// null before it was read), which the worker reads when it starts. null
// without a session.
function shownIdentity(hasSession, ready, reported, saved) {
  if (!hasSession) return null
  if (ready && reported) return reported
  return saved || reported || null
}

// The notice when the session reads as expired: acting as a brand account
// or another login, YouTube may only have stopped answering as that one.
function expiryNotice(identity) {
  if (otherIdentity(identity))
    return { title: "YouTube Music did not answer as the account in use",
             body: "Go back to your own account in the Library tab, or click to sign in again." }
  return { title: "YouTube Music sign-in expired", body: "Click to sign in again (or use the Library tab)." }
}

// What the account row shows: account.json's name for the session's own
// account; the identity's (info: { name, handle, photo } from the worker)
// for a brand account or another login, with a note saying which. null
// when signed out.
function activeAccount(account, identity, info) {
  if (!account) return null
  if (!otherIdentity(identity)) return account
  var brand = identity.brand === true
  var note = brand ? "Brand account" : "Google account " + identity.authUser
  // Until the worker named it, the name says which kind it is.
  if (!info) return { name: note, handle: "", photo: "", note: "" }
  return { name: info.name || (brand ? "Brand account" : "Another Google account"), handle: info.handle || "",
           photo: info.photo || "", note: note }
}

// The account row's second line: the handle, and which kind of identity.
function accountCaption(shown) {
  if (!shown) return ""
  return [shown.handle || "", shown.note || ""].filter(function(s) { return s !== "" }).join(" · ")
}

// The identity selected in an `accounts` reply's items, or null.
function selectedAccount(items) {
  var list = Array.isArray(items) ? items : []
  for (var i = 0; i < list.length; i++) if (list[i] && list[i].selected === true) return list[i]
  return null
}

function accountsFresh(accounts, now) {
  return !!accounts && Array.isArray(accounts.items) && now - (accounts.at || 0) < ACCOUNTS_TTL_MS
}

// switchAccount's params for an identity of `accounts`.
function switchParams(item) {
  var params = { pageId: item && item.brand === true && item.pageId ? String(item.pageId) : null }
  if (item && typeof item.authUser === "number") params.authUser = item.authUser
  return params
}

// "switch" (switchAccount), "signIn" (sign in again as its login), "current"
// (nothing to do) or "" (no way to reach it).
function accountChoiceKind(item) {
  if (!item) return ""
  if (item.selected === true) return "current"
  if (item.otherLogin !== true) return "switch"
  if (typeof item.authUser !== "number") return ""
  return AUTHUSER_SWITCH ? "switch" : "signIn"
}

function authUserCommand(authUser) {
  return "bin/ytm auth --authuser " + (typeof authUser === "number" ? authUser : "N")
}

// ItemMenu chooser rows for `accounts` items: the one in use ticked, brand
// accounts with their own icon, another login's identities with the command
// that signs in as it (its brand accounts: then switch, as signing in starts
// as the login's own account); then a row that says how to add another
// Google account. Each row's value is { kind, item }.
function accountChoices(items) {
  var rows = []
  var list = Array.isArray(items) ? items : []
  for (var i = 0; i < list.length; i++) {
    var item = list[i]
    var kind = accountChoiceKind(item)
    if (kind === "") continue
    var hint = kind === "signIn" ? authUserCommand(item.authUser) + (item.brand === true ? ", then switch" : "")
      : (item.handle || (item.brand ? "Brand account" : ""))
    rows.push({ id: "account:" + (item.index !== undefined ? item.index : i), title: item.name || "Unnamed account",
                hint: hint, icon: kind === "current" ? CHOSEN_ICON : item.brand ? BRAND_ICON : ACCOUNT_ICON,
                value: { kind: kind, item: item } })
  }
  rows.push({ id: ANOTHER_LOGIN_ID, title: "Another Google account", hint: authUserCommand(null), icon: ADD_ACCOUNT_ICON,
              value: { kind: "another", item: null } })
  return rows
}

function switchedNotice(item, info) {
  var name = (info && info.name) || (item && item.name) || "your account"
  var brand = item ? item.brand === true : false
  return { title: "Switched to " + name,
           body: brand ? "Likes, playlists, subscriptions and history now go to this brand account" : "" }
}

// Choosing another login's brand account signs in as that login, which
// starts as its own account: the brand is one more switch away.
function signInFirstNotice(item) {
  var name = (item && item.name) || "that brand account"
  var login = item && typeof item.authUser === "number" ? item.authUser : "N"
  return { title: "Signing in as Google account " + login + " first",
           body: "You start as its own account; then choose " + name + " under Switch account" }
}

function anotherLoginNotice() {
  return { title: "Another Google account",
           body: "Sign in to it in Chromium, then run " + authUserCommand(null) + " (N: its place in Chromium's account list, from 0)" }
}

// ---- menu entries ------------------------------------------------------------------------

// Library (M), before Record plays in history and Sign out. Audio quality
// and Content location apply signed out too. ctx.quality and ctx.location
// come from LibraryView.pageMenu.
;(function() {
  var at = MENU_ACTIONS.length
  for (var i = MENU_ACTIONS.length - 1; i >= 0; i--) {
    if (MENU_ACTIONS[i].id === "recordHistory" || MENU_ACTIONS[i].id === "signOut" || MENU_ACTIONS[i].id === "signIn") at = i
  }
  var library = function(ctx) { return ctx.page === true && ctx.pageKind === "library" }
  MENU_ACTIONS.splice(at, 0, {
    id: "switchAccount", title: "Switch account", icon: SWITCH_ICON,
    applies: function(item, ctx) { return library(ctx) && ctx.signedIn === true },
    run: function(env) { env.service.openAccountChooser(env.panel) }
  }, {
    id: "audioQuality", title: function(item, ctx) { return "Audio quality (" + qualityTitle(ctx.quality) + ")" },
    icon: QUALITY_ICON,
    applies: function(item, ctx) { return library(ctx) },
    run: function(env) {
      env.panel.openChooser({ title: "Audio quality", subtitle: "From the next song on",
        choices: qualityChoices(env.ctx.quality), onChoice: function(choice) { env.service.setQuality(choice.value) } })
    }
  }, {
    id: "contentLocation", title: function(item, ctx) { return "Content location (" + locationTitle(ctx.location) + ")" },
    icon: LOCATION_ICON,
    applies: function(item, ctx) { return library(ctx) },
    run: function(env) {
      env.panel.openChooser({ title: "Content location", subtitle: "For Home, Explore and search; Charts too, where it has the country",
        choices: locationChoices(env.ctx.location), onChoice: function(choice) { env.service.setLocation(choice.value) } })
    }
  }, {
    // The session reads as expired while acting as a brand account or another
    // login: going back checks the session itself (ctx.canUseOwnAccount from
    // LibraryView.pageMenu).
    id: "useOwnAccount", title: "Use your own account", icon: ACCOUNT_ICON,
    applies: function(item, ctx) { return library(ctx) && ctx.signedIn !== true && ctx.canUseOwnAccount === true },
    run: function(env) { env.service.useOwnAccount() }
  })
})()

if (typeof module !== "undefined") {
  Object.assign(module.exports, {
    QUALITIES: QUALITIES,
    QUALITY_FORMATS: QUALITY_FORMATS,
    LOCATIONS: LOCATIONS,
    ACCOUNTS_TTL_MS: ACCOUNTS_TTL_MS,
    AUTHUSER_SWITCH: AUTHUSER_SWITCH,
    ANOTHER_LOGIN_ID: ANOTHER_LOGIN_ID,
    CHART_COUNTRIES: CHART_COUNTRIES,
    CHOSEN_ICON: CHOSEN_ICON,
    BRAND_ICON: BRAND_ICON,
    ACCOUNT_ICON: ACCOUNT_ICON,
    qualityMode: qualityMode,
    qualityTitle: qualityTitle,
    qualityFormat: qualityFormat,
    qualityChoices: qualityChoices,
    locationCode: locationCode,
    locationTitle: locationTitle,
    locationChoices: locationChoices,
    chartsCountryForLocation: chartsCountryForLocation,
    accountSettings: accountSettings,
    otherIdentity: otherIdentity,
    savedIdentity: savedIdentity,
    shownIdentity: shownIdentity,
    expiryNotice: expiryNotice,
    activeAccount: activeAccount,
    accountCaption: accountCaption,
    selectedAccount: selectedAccount,
    accountsFresh: accountsFresh,
    switchParams: switchParams,
    accountChoiceKind: accountChoiceKind,
    authUserCommand: authUserCommand,
    accountChoices: accountChoices,
    switchedNotice: switchedNotice,
    signInFirstNotice: signInFirstNotice,
    anotherLoginNotice: anotherLoginNotice
  })
}
