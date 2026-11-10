import QtQuick
import Quickshell.Io
import "YtmModel.js" as Model

// Data the panel shows, kept in the service so it outlives panel unloads and
// is shared by every monitor: searches, opened collections, the library,
// likes, cover thumbnails on disk, and tab requests coming in over IPC.
//
// Collections and the library are loaded stale-while-revalidate: the worker's
// disk cache answers first (instantly), then a fresh copy replaces it.
Item {
  id: root

  property var worker: null
  // Only ask the worker for thumbnails while a panel shows them, so this
  // never starts the worker by itself.
  property bool thumbsEnabled: false

  // ---- search ---------------------------------------------------------------
  property string query: ""
  // "" = everything, grouped; otherwise a ytmusicapi search filter.
  property string filter: ""
  property var result: null
  property bool searching: false
  property string searchError: ""
  property int lastSearchMs: 0
  // The search field has keyboard focus (panel shortcuts are off then).
  property bool inputFocused: false
  property int _seq: 0
  // Recent searches: key -> { result, at }.
  readonly property int searchTtlMs: 10 * 60 * 1000
  readonly property int maxSearches: 20
  property var _searches: ({})
  readonly property int searchCacheSize: Object.keys(_searches).length

  // ---- likely picks ----------------------------------------------------------
  // A track you're likely to play next (a search's first song, the row under
  // the cursor or pointer); the service looks its stream up in advance.
  signal trackHinted(var track)

  function hint(track) {
    if (track && track.type === "track" && track.videoId) trackHinted(track)
  }

  function search(text, searchFilter) {
    var q = String(text || "").trim()
    var f = searchFilter || ""
    if (q === "") {
      _seq++
      query = ""
      result = null
      searching = false
      searchError = ""
      return
    }
    if (q === query && f === filter && (result || searching) && searchError === "") return
    var key = Model.searchKey(q, f)
    var hit = _searches[key]
    if (hit && Date.now() - hit.at < searchTtlMs) {
      _seq++
      query = q
      filter = f
      searching = false
      searchError = ""
      lastSearchMs = 0
      result = hit.result
      hint(Model.firstTrack(Model.flattenSearch(hit.result, f)))
      return
    }
    var seq = ++_seq
    var started = Date.now()
    query = q
    filter = f
    searching = true
    worker.request("search", { query: q, filter: f || null, limit: f ? 40 : 20 }, function(reply) {
      // A newer search superseded this one while it was in flight.
      if (seq !== root._seq) return
      root.searching = false
      root.lastSearchMs = Date.now() - started
      if (!reply.ok) {
        root.searchError = reply.error.message
        root.result = null
        return
      }
      root.searchError = ""
      root.result = reply.result
      root._rememberSearch(key, reply.result)
      root.requestThumbs(Model.collectThumbs(reply.result))
      root.hint(Model.firstTrack(Model.flattenSearch(reply.result, f)))
    })
  }

  // ---- recent searches -----------------------------------------------------------
  // Searches that led somewhere, newest first, in <stateDir>/searches.json.
  property string stateDir: ""
  property var recent: []
  readonly property int maxRecent: 20

  function rememberQuery(text) {
    var next = Model.rememberQuery(recent, text, maxRecent)
    if (JSON.stringify(next) === JSON.stringify(recent)) return
    recent = next
    _saveRecent()
  }

  function forgetQuery(text) {
    recent = Model.forgetQuery(recent, text)
    _saveRecent()
  }

  function _saveRecent() {
    if (stateDir !== "") recentFile.setText(JSON.stringify({ version: 1, queries: recent }) + "\n")
  }

  FileView {
    id: recentFile
    path: root.stateDir !== "" ? root.stateDir + "/searches.json" : ""
    atomicWrites: true
    watchChanges: false
    printErrors: false
    onLoaded: root.recent = Model.parseRecent(text())
  }

  // ---- suggestions while typing ------------------------------------------------------
  // [{ text, recent }] for the text being typed: matching recent searches,
  // then YouTube Music's. One request at a time (the latest text wins);
  // answers are kept for the session, so backspacing asks nothing.
  property var suggestions: []
  property string _suggestFor: ""
  // The text `suggestions` belong to (the field may hold something else,
  // e.g. after an IPC search).
  readonly property string suggestionsFor: _suggestFor
  property bool _suggesting: false
  property var _suggestCache: ({})

  function suggest(text) {
    var q = String(text || "").trim()
    _suggestFor = q
    if (q === "") {
      suggestions = []
      return
    }
    var cached = _suggestCache[q.toLowerCase()]
    suggestions = Model.suggestionList(recent, cached || [], q, 5)
    if (cached || _suggesting || !worker) return
    _suggesting = true
    worker.request("suggestions", { query: q }, function(reply) {
      root._suggesting = false
      if (reply.ok) root._cacheSuggestions(q, reply.result.items || [])
      if (root._suggestFor !== q) root.suggest(root._suggestFor)
      else if (reply.ok) root.suggestions = Model.suggestionList(root.recent, reply.result.items || [], q, 5)
    }, 5000)
  }

  function _cacheSuggestions(q, items) {
    var keys = Object.keys(_suggestCache)
    var next = ({})
    for (var i = Math.max(0, keys.length - 49); i < keys.length; i++) next[keys[i]] = _suggestCache[keys[i]]
    next[q.toLowerCase()] = items
    _suggestCache = next
  }

  function _rememberSearch(key, value) {
    var keys = Object.keys(_searches).filter(function(k) { return k !== key })
    keys.sort(function(a, b) { return root._searches[b].at - root._searches[a].at })
    var next = ({})
    for (var i = 0; i < keys.length && i < maxSearches - 1; i++) next[keys[i]] = _searches[keys[i]]
    next[key] = { result: value, at: Date.now() }
    _searches = next
  }

  // ---- collections: every page opened from a row (F2) ----------------------------
  // Keyed by Model.pageRef(item).key, method + canonical params, so an
  // artist's albums and singles or two sort orders never share an entry.
  // An entry is { ref, data, at, seen, seq }: `at` is when its first page
  // was fetched, `seen` when it was last handed out, and `seq` a number no
  // other chain ever had. Load more appends to `data` under the same seq; a
  // refresh replaces the whole chain under a new one, so a Load more reply
  // that started before the refresh is dropped. Copies from the disk cache
  // carry no cursor (F1), so Load more only runs from a fresh page.
  readonly property int collectionTtlMs: 5 * 60 * 1000
  readonly property int unseenMs: 10 * 60 * 1000
  readonly property int maxCollections: 8
  readonly property int maxCollectionItems: 3000
  property var _collections: ({})
  property int _chainSeq: 0
  // key -> true while a Load more for it is in flight (the sweep spares it).
  property var _appending: ({})
  // key -> true from a disk copy until its background refresh answers.
  // Replaced on each change, so bindings on refreshPending() follow.
  property var _refreshing: ({})
  readonly property int collectionCount: Object.keys(_collections).length

  // A refresh of ref's disk copy is on its way (it brings the cursor), so
  // another one would only repeat it.
  function refreshPending(ref) {
    return !!ref && _refreshing[ref.key] === true
  }

  function _setRefreshing(key, on) {
    var next = _copy(_refreshing)
    if (on) next[key] = true
    else delete next[key]
    _refreshing = next
  }

  function _ttl(ref) {
    var kind = Model.pageKind(ref)
    return kind && kind.ttlMs > 0 ? kind.ttlMs : collectionTtlMs
  }

  function _copy(params) {
    var out = ({})
    for (var k in params) out[k] = params[k]
    return out
  }

  // ref: Model.pageRef(item). `callback` may run twice: with the disk-cached
  // copy, then the fresh one. A kind no slice registered answers
  // BAD_REQUEST "Cannot open this item yet" (never a guessed method).
  // fresh: ask YouTube only (after an edit whose outcome is unknown, when the
  // disk copy may predate it), so a failure is reported instead of the copy.
  function loadCollection(ref, callback, fresh) {
    var kind = Model.pageKind(ref)
    if (!kind) {
      callback(Model.cannotOpen())
      return
    }
    var entry = _collections[ref.key]
    var now = Date.now()
    if (!fresh && entry && now - entry.at < _ttl(ref)) {
      entry.seen = now  // bookkeeping only; nothing binds to it
      callback({ ok: true, result: entry.data })
      return
    }
    var params = _copy(ref.params)
    var handle = function(reply) {
      if (reply.ok) {
        var data = kind.adapt ? kind.adapt(reply.result, ref) : reply.result
        reply = { ok: true, cached: reply.cached, result: data }
        root._remember(ref, data)
        root.requestThumbs(Model.collectThumbs(data))
      }
      callback(reply)
    }
    if (fresh) {
      params.cache = "refresh"
      worker.request(ref.method, params, handle, 30000)
    } else {
      _cachedThenFresh(ref.method, params, handle, function(on) { root._setRefreshing(ref.key, on) })
    }
  }

  // The next cursor page of ref's chain, appended (Model.appendPage, at most
  // maxCollectionItems). `shown` is the page the caller shows, adopted when
  // the sweep dropped the entry meanwhile. The callback gets the whole
  // chain, or { ok: false, stale: true } when a refresh replaced it first.
  function loadMore(ref, callback, shown) {
    var kind = Model.pageKind(ref)
    if (!kind) {
      callback(Model.cannotOpen())
      return
    }
    var key = ref.key
    // Adopted as stale (at 0): it is shown, but how old it is is unknown.
    if (!_collections[key] && shown) _remember(ref, shown, 0)
    var entry = _collections[key]
    var cursor = entry && entry.data ? entry.data.cursor : null
    if (!cursor) {
      callback({ ok: false, error: { code: "BAD_REQUEST", message: "Nothing more to load" } })
      return
    }
    var seq = entry.seq
    entry.seen = Date.now()
    var params = _copy(ref.params)
    params.cursor = cursor
    params.cache = "bypass"
    _appending[key] = true
    worker.request(ref.method, params, function(reply) {
      delete root._appending[key]
      var current = root._collections[key]
      if (!current || current.seq !== seq || current.data.cursor !== cursor) {
        callback({ ok: false, stale: true, error: { code: "STALE", message: "The page was refreshed" } })
        return
      }
      if (!reply.ok) {
        callback(reply)
        return
      }
      var next = kind.adapt ? kind.adapt(reply.result, ref) : reply.result
      var merged = Model.appendPage(current.data, next, root.maxCollectionItems)
      root._extend(key, merged)
      root.requestThumbs(Model.collectThumbs(next))
      callback({ ok: true, result: merged })
    }, 60000)
  }

  // ---- menu (F3) ------------------------------------------------------------------
  // The collection held for ref, whatever its age (null when none): a whole
  // album or playlist plays from a row without asking the worker again
  // (Service.itemTracks, when Model.completeTracks says it is all there).
  function cachedCollection(ref) {
    var entry = ref ? _collections[ref.key] : null
    return entry ? entry.data : null
  }

  // ---- home ---------------------------------------------------------------------
  readonly property int homeTtlMs: 10 * 60 * 1000
  property var shelves: []
  property bool homeLoading: false
  property string homeError: ""
  property real homeAt: 0

  function loadHome(force) {
    if (homeLoading) return
    if (!force && homeAt > 0 && Date.now() - homeAt < homeTtlMs) return
    homeLoading = true
    homeError = ""
    var handle = function(reply) {
      root.homeLoading = false
      if (!reply.ok) {
        if (root.shelves.length === 0) root.homeError = reply.error.message
        return
      }
      root.homeError = ""
      root.shelves = reply.result.shelves || []
      root.homeAt = Date.now()
      root.requestThumbs(Model.collectThumbs(root.shelves))
    }
    if (force) worker.request("home", { limit: 6, cache: "refresh" }, handle, 30000)
    else _cachedThenFresh("home", { limit: 6 }, handle)
  }

  // Ask with cache "prefer"; if that came from disk, follow up with a refresh.
  // `pending(true)` runs before the disk copy is handled when that refresh
  // follows, `pending(false)` once it has answered (after handling it, if
  // it worked).
  function _cachedThenFresh(method, params, handle, pending) {
    var first = ({})
    for (var k in params) first[k] = params[k]
    first.cache = "prefer"
    worker.request(method, first, function(reply) {
      var follow = reply.ok && reply.cached
      if (follow && pending) pending(true)
      handle(reply)
      if (!follow) return
      var fresh = ({})
      for (var j in params) fresh[j] = params[j]
      fresh.cache = "refresh"
      worker.request(method, fresh, function(update) {
        if (update.ok) handle(update)
        if (pending) pending(false)
      }, 30000)
    }, 30000)
  }

  // Drops every cached page of that kind and id (all its sort orders), as
  // PAGE_KINDS[kind].id names it: ("playlist", playlistId), ("liked", "LM").
  function forgetCollection(kind, id) {
    _dropCollections(function(entry) { return entry.ref.kind === kind && (id === undefined || entry.ref.id === id) })
  }

  function forgetPage(ref) {
    if (ref) _dropCollections(function(entry) { return entry.ref.key === ref.key })
  }

  function _dropCollections(match) {
    var next = null
    for (var key in _collections) {
      if (!match(_collections[key])) continue
      if (!next) next = _copy(_collections)
      delete next[key]
    }
    if (next) _collections = next
  }

  // A new chain for ref (least recently seen entries make room). `at`
  // defaults to now.
  function _remember(ref, data, at) {
    var key = ref.key
    var now = Date.now()
    var others = Object.keys(_collections).filter(function(k) { return k !== key })
    others.sort(function(a, b) { return root._collections[b].seen - root._collections[a].seen })
    var next = ({})
    for (var i = 0; i < others.length && i < maxCollections - 1; i++) next[others[i]] = _collections[others[i]]
    next[key] = { ref: ref, data: data, at: at === undefined ? now : at, seen: now, seq: ++_chainSeq }
    _collections = next
  }

  // The same chain, one page longer.
  function _extend(key, data) {
    var entry = _collections[key]
    if (!entry) return
    var next = _copy(_collections)
    next[key] = { ref: entry.ref, data: data, at: entry.at, seen: Date.now(), seq: entry.seq }
    _collections = next
  }

  // ---- sweep (F2) -------------------------------------------------------------------
  // Every minute while anything is cached: drop collections past their TTL
  // or unseen for 10 min, and searches past theirs. A slice with its own
  // keyed cache adds it here and to sweepWanted.
  readonly property bool sweepWanted: collectionCount > 0 || searchCacheSize > 0

  function sweep(now) {
    var t = now || Date.now()
    var collections = ({})
    var droppedCollections = false
    for (var key in _collections) {
      var entry = _collections[key]
      var expired = t - entry.at >= _ttl(entry.ref) || t - entry.seen >= unseenMs
      if (expired && !_appending[key]) droppedCollections = true
      else collections[key] = entry
    }
    if (droppedCollections) _collections = collections
    var searches = ({})
    var droppedSearches = false
    for (var q in _searches) {
      if (t - _searches[q].at >= searchTtlMs) droppedSearches = true
      else searches[q] = _searches[q]
    }
    if (droppedSearches) _searches = searches
  }

  Timer {
    interval: 60 * 1000
    repeat: true
    running: root.sweepWanted
    onTriggered: root.sweep()
  }

  // ---- library ------------------------------------------------------------------
  readonly property int libraryTtlMs: 5 * 60 * 1000
  property var playlists: []
  property bool libraryLoading: false
  property string libraryError: ""
  property real libraryAt: 0

  // force: skip both the memory and the disk cache (after an edit).
  function loadLibrary(force) {
    if (libraryLoading) return
    if (!force && libraryAt > 0 && Date.now() - libraryAt < libraryTtlMs) return
    libraryLoading = true
    libraryError = ""
    var handle = function(reply) {
      root.libraryLoading = false
      if (!reply.ok) {
        if (root.playlists.length === 0) root.libraryError = reply.error.message
        return
      }
      root.libraryError = ""
      root.playlists = reply.result.items || []
      root.libraryAt = Date.now()
      root.requestThumbs(Model.collectThumbs(root.playlists))
    }
    if (force) worker.request("libraryPlaylists", { limit: 200, cache: "refresh" }, handle, 30000)
    else _cachedThenFresh("libraryPlaylists", { limit: 200 }, handle)
  }

  function invalidateLibrary() {
    libraryAt = 0
  }

  // ---- likes (videoId -> "LIKE" | "INDIFFERENT" | "DISLIKE") -----------------------
  property var likes: ({})

  function likeStatus(videoId, fallback) {
    var known = likes[videoId]
    return known !== undefined ? known : (fallback === undefined ? null : fallback)
  }

  function setLike(videoId, status) {
    var next = ({})
    for (var key in likes) next[key] = likes[key]
    next[videoId] = status
    likes = next
  }

  // ---- cover thumbnails on disk (url -> local path) ------------------------------
  readonly property int maxThumbs: 2000
  property var thumbs: ({})
  property var _thumbRequested: ({})
  readonly property int thumbCount: Object.keys(thumbs).length

  function thumbSource(url) {
    if (!url) return ""
    var path = thumbs[url]
    return path ? "file://" + path : url
  }

  function requestThumbs(urls) {
    if (!thumbsEnabled || !worker) return
    var wanted = []
    for (var i = 0; i < (urls || []).length; i++) {
      var url = urls[i]
      if (url && !thumbs[url] && !_thumbRequested[url]) {
        _thumbRequested[url] = true
        wanted.push(url)
      }
    }
    for (var start = 0; start < wanted.length; start += 150) {
      worker.request("cacheThumbs", { urls: wanted.slice(start, start + 150) }, function(reply) {
        if (reply.ok) root.mergeThumbs(reply.result.paths || {})
      })
    }
  }

  function mergeThumbs(paths) {
    var next = null
    for (var url in paths) {
      if (thumbs[url] === paths[url]) continue
      if (!next) {
        next = ({})
        var keys = Object.keys(thumbs)
        // Keep the map bounded; the files themselves are pruned by the worker.
        for (var i = Math.max(0, keys.length - maxThumbs + 200); i < keys.length; i++) next[keys[i]] = thumbs[keys[i]]
      }
      next[url] = paths[url]
    }
    if (next) thumbs = next
  }

  Connections {
    target: root.worker
    function onEventReceived(message) {
      if (message.event === "thumbs") root.mergeThumbs(message.paths || {})
    }
  }

  // Signing in or out: nothing cached belongs to the new session.
  function reset() {
    _collections = ({})
    _appending = ({})
    _searches = ({})
    playlists = []
    libraryAt = 0
    libraryError = ""
    shelves = []
    homeAt = 0
    homeError = ""
    likes = ({})
  }

  // ---- tab requests (IPC "search" opens the panel on the search tab) -----------
  property string requestedTab: ""
  property int tabRequestSeq: 0

  function requestTab(tab) {
    requestedTab = tab
    tabRequestSeq++
  }
}
