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
  // S7: "" (YouTube Music), "library" or "uploads" (signed in); `exact`
  // searches the words as typed (no spelling fix). A filtered result
  // (or uploads) grows by searchMore() while it has a cursor.
  property string scope: ""
  property bool exact: false
  property bool searchingMore: false
  property string searchMoreError: ""
  readonly property int maxSearchItems: 500
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

  // S7: `searchScope` undefined keeps the current scope; `exactly` is for
  // this search only (a new one is corrected again).
  function search(text, searchFilter, searchScope, exactly) {
    var q = String(text || "").trim()
    var f = searchFilter || ""
    var s = searchScope === undefined ? scope : (searchScope || "")
    var x = exactly === true
    if (!Model.searchFilterAllowed(f, s)) f = ""
    if (q === "") {
      _seq++
      query = ""
      filter = f
      scope = s
      exact = false
      result = null
      searching = false
      searchingMore = false
      searchError = ""
      searchMoreError = ""
      return
    }
    if (q === query && f === filter && s === scope && x === exact && (result || searching) && searchError === "") return
    var key = Model.searchKey(q, f, s, x)
    var hit = _searches[key]
    searchingMore = false
    searchMoreError = ""
    if (hit && Date.now() - hit.at < searchTtlMs) {
      _seq++
      query = q
      filter = f
      scope = s
      exact = x
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
    scope = s
    exact = x
    searching = true
    var params = { query: q, filter: f || null, scope: s || null, exact: x }
    worker.request("search", params, function(reply) {
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
      // A signed-in search joins your YouTube searches.
      root.pastSearchesAt = 0
    })
  }

  // ---- search: more of a filtered list (S7) -----------------------------------
  // The next page by the result's cursor (never cached). A reply for a
  // search that has been replaced meanwhile is dropped. After a failure
  // only an explicit Load more (Enter, a click) tries again.
  function searchMore(explicit) {
    var current = result
    if (!worker || !current || !current.cursor || searching || searchingMore) return false
    if (searchMoreError !== "" && explicit !== true) return false
    var seq = _seq
    var key = Model.searchKey(query, filter, scope, exact)
    var params = { query: query, filter: filter || null, scope: scope || null, exact: exact, cursor: current.cursor }
    searchingMore = true
    searchMoreError = ""
    worker.request("search", params, function(reply) {
      if (seq !== root._seq || root.result !== current) return
      root.searchingMore = false
      if (!reply.ok) {
        root.searchMoreError = reply.error.message
        return
      }
      var next = Model.appendSearchPage(current, reply.result, root.maxSearchItems)
      root.result = next
      root._updateSearch(key, next)
      root.requestThumbs(Model.collectThumbs(reply.result))
    }, 60000)
    return true
  }

  // A held search grown by searchMore keeps its age.
  function _updateSearch(key, value) {
    var hit = _searches[key]
    if (!hit) return
    var next = ({})
    for (var k in _searches) next[k] = _searches[k]
    next[key] = { result: value, at: hit.at }
    _searches = next
  }

  // ---- search: the session ended (S7) ------------------------------------------
  // A library or uploads search belongs to the session. Service calls this
  // when signedIn goes false (expired, invalid, signed out) and reset() on
  // an account change: the scoped search is dropped, so the next search
  // (SearchView runs the field's words again) goes to YouTube Music.
  function leaveScope() {
    if (scope === "") return
    _seq++
    query = ""
    result = null
    searching = false
    searchingMore = false
    searchError = ""
    searchMoreError = ""
    exact = false
    scope = ""
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
    // S7: a recent search can be removed from the completions too.
    var key = String(text || "").trim().toLowerCase()
    suggestions = suggestions.filter(function(s) { return s.remote || s.text.toLowerCase() !== key })
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
  // [{ text, recent, remote? }] for the text being typed: matching recent searches,
  // then YouTube Music's. One request at a time (the latest text wins);
  // answers are kept for the session, so backspacing asks nothing.
  // S7: suggestionEntities are the rich rows (artists, songs, albums) for
  // the same text.
  property var suggestions: []
  property var suggestionEntities: []
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
      suggestionEntities = []
      return
    }
    var cached = _suggestCache[q.toLowerCase()]
    suggestions = Model.suggestionList(recent, cached ? cached.items : [], q, 5)
    suggestionEntities = cached ? cached.entities : []
    if (cached || _suggesting || !worker) return
    _suggesting = true
    worker.request("suggestions", { query: q }, function(reply) {
      root._suggesting = false
      if (reply.ok) root._cacheSuggestions(q, reply.result.items || [], reply.result.entities || [])
      if (root._suggestFor !== q) root.suggest(root._suggestFor)
      else if (reply.ok) {
        root.suggestions = Model.suggestionList(root.recent, reply.result.items || [], q, 5)
        root.suggestionEntities = reply.result.entities || []
        root.requestThumbs(Model.collectThumbs(root.suggestionEntities))
      }
    }, 5000)
  }

  function _cacheSuggestions(q, items, entities) {
    var keys = Object.keys(_suggestCache)
    var next = ({})
    for (var i = Math.max(0, keys.length - 49); i < keys.length; i++) next[keys[i]] = _suggestCache[keys[i]]
    next[q.toLowerCase()] = { items: items, entities: entities || [] }
    _suggestCache = next
  }

  // ---- your YouTube searches (S7) ----------------------------------------------------
  // The empty field lists them (signed in) with the recent searches above.
  // Asked for at most once a minute while the field is shown empty; a
  // search made since makes the next ask go to YouTube.
  property var pastSearches: []
  property real pastSearchesAt: 0
  readonly property int pastSearchesTtlMs: 60 * 1000
  property bool _pastLoading: false
  property int _pastSeq: 0

  function loadPastSearches(force) {
    if (!worker || _pastLoading) return
    if (force !== true && pastSearchesAt > 0 && Date.now() - pastSearchesAt < pastSearchesTtlMs) return
    var seq = _pastSeq
    _pastLoading = true
    worker.request("suggestions", { query: "" }, function(reply) {
      root._pastLoading = false
      if (seq !== root._pastSeq || !reply.ok) return
      root.pastSearches = (reply.result.items || []).filter(function(item) { return item.fromHistory })
        .map(function(item) { return item.text })
      root.pastSearchesAt = Date.now()
    }, 5000)
  }

  // x on one of your YouTube searches: gone from the lists at once, back
  // when YouTube refuses for sure; an unknown outcome (the worker went away)
  // reloads the list. `typed` is what the field held ("" for the empty
  // field's list). callback(reply) after the Store is updated.
  function removeSuggestion(text, typed, callback) {
    var value = String(text || "").trim()
    if (!worker || value === "") return false
    var key = value.toLowerCase()
    var before = pastSearches
    var beforeSuggestions = suggestions
    pastSearches = before.filter(function(t) { return String(t).toLowerCase() !== key })
    suggestions = suggestions.filter(function(s) { return !(s.remote && s.text.toLowerCase() === key) })
    // Cached completions still list it; an answer under way would bring it back.
    _suggestCache = ({})
    _pastSeq++
    var seq = _pastSeq
    worker.request("removeSuggestion", { query: String(typed || "").trim(), text: value }, function(reply) {
      var unknown = !reply.ok && !!reply.error && reply.error.unknown === true
      if (!reply.ok && !unknown && seq === root._pastSeq) {
        root.pastSearches = before
        if (root.suggestions !== beforeSuggestions && root._suggestFor === String(typed || "").trim())
          root.suggestions = beforeSuggestions
      }
      if (unknown) root.loadPastSearches(true)
      if (callback) callback(reply)
    }, 15000, { retry: false })
    return true
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
      worker.request(ref.method, params, handle, loadTimeout(ref))
    } else {
      _cachedThenFresh(ref.method, params, handle, function(on) { root._setRefreshing(ref.key, on) }, loadTimeout(ref))
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

  // ---- collections (S3) ------------------------------------------------------------
  // A whole read (limit "all": every song of a playlist or Liked Music, up
  // to 5000, about 50 requests on the worker's pages lane) gets 120 s; any
  // other page 30 s. (History asks for 200: Model's PAGE_KINDS.history.)
  readonly property int wholeTimeoutMs: 120000

  function loadTimeout(ref) {
    return ref && ref.params && ref.params.limit === "all" ? wholeTimeoutMs : 30000
  }

  // ---- menu (F3) ------------------------------------------------------------------
  // The collection held for ref, whatever its age (null when none): a whole
  // album or playlist plays from a row without asking the worker again
  // (Service.itemTracks, when Model.completeTracks says it is all there).
  function cachedCollection(ref) {
    var entry = ref ? _collections[ref.key] : null
    return entry ? entry.data : null
  }

  // ---- home (S5) --------------------------------------------------------------------
  // The Home feed of each chip (the worker's homeFeed). homeChip is the
  // chosen chip's served params, "" for All. A chip's feed is { shelves,
  // chips, cursor, hasMore, capped, loading, loadingMore, moreError,
  // moreWanted, refreshing, error, at, seenAt, seq }: its first page (about
  // nine shelves; the worker's disk copy first, then fresh), then a page of
  // shelves at a time (loadHomeMore: the cursor nearing the end, or Load
  // more), at most maxHomeShelves. A chip's feed is reused for homeTtlMs;
  // up to maxHomeFeeds chips are held, and one unseen for 10 min is swept
  // (never the chosen chip's). A refresh replaces the whole feed; a Load
  // more reply from before it is dropped (seq).
  readonly property int homeTtlMs: 10 * 60 * 1000
  readonly property int maxHomeShelves: 40
  readonly property int maxHomeFeeds: 4
  property var homeFeeds: ({})
  property string homeChip: ""
  // The chips as last served ([{ title, params, selected }]); every chip's
  // first page serves them all, so they stay while another chip loads.
  property var homeChips: []
  property int _homeSeq: 0
  readonly property int homeFeedCount: Object.keys(homeFeeds).length
  readonly property var homeFeed: homeFeeds[homeChip] || null
  readonly property var _noShelves: []
  readonly property var shelves: homeFeed ? homeFeed.shelves : _noShelves
  readonly property var homeCursor: homeFeed ? homeFeed.cursor : null
  readonly property bool homeLoading: !!homeFeed && homeFeed.loading === true
  readonly property string homeError: homeFeed ? homeFeed.error : ""
  readonly property real homeAt: homeFeed ? homeFeed.at : 0

  function _setHomeFeed(chip, entry) {
    var next = _copy(homeFeeds)
    next[chip] = entry
    homeFeeds = next
  }

  // The chip's feed with `fields` changed (a new object, so bindings follow).
  function _patchHome(chip, fields) {
    var entry = homeFeeds[chip]
    if (!entry) return
    var next = _copy(entry)
    for (var key in fields) next[key] = fields[key]
    _setHomeFeed(chip, next)
  }

  // force: skip the memory and disk copies (Refresh, Load more on a disk
  // copy). chip: choose that chip first (its served params, "" for All);
  // left out, the chosen one loads.
  function loadHome(force, chip) {
    if (chip !== undefined && chip !== null) homeChip = String(chip)
    var key = homeChip
    var entry = homeFeeds[key] || null
    var now = Date.now()
    if (!worker) return
    // Choosing a chip always patches its feed (seenAt), so Home's rows follow
    // the switch even when nothing new is asked for.
    if (!force && entry && entry.loading) {
      _patchHome(key, { seenAt: now })
      return
    }
    if (!force && entry && entry.at > 0 && now - entry.at < homeTtlMs && entry.error === "") {
      _patchHome(key, { seenAt: now })
      return
    }
    var seq = ++_homeSeq
    var keep = !!entry && entry.shelves.length > 0
    _setHomeFeed(key, {
      shelves: keep ? entry.shelves : [], chips: keep ? entry.chips : [], cursor: keep ? entry.cursor : null,
      hasMore: keep ? entry.hasMore : false, capped: keep ? entry.capped : false, loading: true,
      loadingMore: false, moreError: "", moreWanted: !!entry && entry.moreWanted === true, refreshing: false,
      error: "", at: keep ? entry.at : 0, seenAt: now, seq: seq
    })
    _trimHomeFeeds()
    var params = key !== "" ? { params: key } : {}
    var handle = function(reply) {
      var current = root.homeFeeds[key]
      if (!current || current.seq !== seq) return
      if (!reply.ok) {
        // A Load more asked for meanwhile goes on from the held cursor.
        var more = current.moreWanted === true && !!current.cursor
        root._patchHome(key, { loading: false, moreWanted: false,
                               error: current.shelves.length === 0 ? reply.error.message : "" })
        if (more) root.loadHomeMore(false)
        return
      }
      var result = reply.result || {}
      var shelves = Model.appendShelves([], result.shelves, root.maxHomeShelves)
      var chips = result.chips || []
      var wanted = current.moreWanted === true && !reply.cached
      var fields = { shelves: shelves.shelves, cursor: shelves.capped ? null : (result.cursor || null),
                     hasMore: !shelves.capped && (!!result.cursor || result.hasMore === true), capped: shelves.capped,
                     loading: false, error: "", moreError: "", at: Date.now(), seenAt: Date.now() }
      if (chips.length > 0) {
        fields.chips = chips
        // The same chips come with every page (one selected): keep the list
        // as it is unless they changed, so the chip row is not rebuilt.
        if (JSON.stringify(Model.homeChipList(chips)) !== JSON.stringify(Model.homeChipList(root.homeChips)))
          root.homeChips = chips
      }
      if (wanted) fields.moreWanted = false
      root._patchHome(key, fields)
      root.requestThumbs(Model.collectThumbs(shelves.shelves))
      if (wanted) root.loadHomeMore(false)
    }
    // A feed already shown is newer than its disk copy: straight to YouTube.
    if (force || keep) {
      params.cache = "refresh"
      worker.request("homeFeed", params, handle, 30000)
    } else {
      _cachedThenFresh("homeFeed", params, handle, function(on) {
        var current = root.homeFeeds[key]
        if (!current || current.seq !== seq) return
        // The refresh ended without a page for us (it failed): a Load more
        // waiting for its cursor is spent.
        root._patchHome(key, on ? { refreshing: true } : { refreshing: false, moreWanted: false })
      })
    }
  }

  // The chosen chip's next page of shelves. explicit: Load more was pressed
  // (on a disk copy, which has no cursor, that refreshes the feed and then
  // loads more). The cursor nearing the end passes false: it waits for a
  // load or refresh already on its way (which brings the cursor), and never
  // starts one.
  function loadHomeMore(explicit) {
    var key = homeChip
    var entry = homeFeeds[key]
    if (!entry || entry.loadingMore || entry.capped || !worker) return
    if (entry.loading) {
      if (entry.cursor || entry.hasMore) _patchHome(key, { moreWanted: true })
      return
    }
    if (!entry.cursor) {
      if (!entry.hasMore) return
      if (entry.refreshing) {
        _patchHome(key, { moreWanted: true })
        return
      }
      if (!explicit) return
      loadHome(true)
      _patchHome(key, { moreWanted: true })
      return
    }
    var seq = entry.seq
    var cursor = entry.cursor
    _patchHome(key, { loadingMore: true, moreError: "", seenAt: Date.now() })
    var params = key !== "" ? { params: key } : {}
    params.cursor = cursor
    params.cache = "bypass"
    worker.request("homeFeed", params, function(reply) {
      var current = root.homeFeeds[key]
      // A refresh replaced the feed meanwhile: its own state stands.
      if (!current || current.seq !== seq || current.cursor !== cursor) return
      if (!reply.ok) {
        root._patchHome(key, { loadingMore: false, moreError: reply.error.message })
        return
      }
      var next = reply.result.shelves || []
      var merged = Model.appendShelves(current.shelves, next, root.maxHomeShelves)
      var nextCursor = merged.capped ? null : (reply.result.cursor || null)
      root._patchHome(key, { shelves: merged.shelves, cursor: nextCursor, hasMore: !!nextCursor,
                             capped: merged.capped, loadingMore: false, seenAt: Date.now() })
      root.requestThumbs(Model.collectThumbs(next))
    }, 60000)
  }

  // At most maxHomeFeeds chips held: the least recently seen go first,
  // never the chosen one.
  function _trimHomeFeeds() {
    var others = Object.keys(homeFeeds).filter(function(chip) { return chip !== root.homeChip })
    if (others.length < maxHomeFeeds) return
    others.sort(function(a, b) { return root.homeFeeds[b].seenAt - root.homeFeeds[a].seenAt })
    var next = ({})
    next[homeChip] = homeFeeds[homeChip]
    for (var i = 0; i < maxHomeFeeds - 1; i++) next[others[i]] = homeFeeds[others[i]]
    homeFeeds = next
  }

  // From the shared sweep (every minute while another chip's feed is held):
  // drop the ones unseen for 10 min (not the chosen chip's, nor one still
  // loading).
  function sweepHome(now) {
    var t = now || Date.now()
    var next = null
    for (var chip in homeFeeds) {
      var entry = homeFeeds[chip]
      if (chip === homeChip || entry.loading || entry.loadingMore || t - entry.seenAt < unseenMs) continue
      if (!next) next = _copy(homeFeeds)
      delete next[chip]
    }
    if (next) homeFeeds = next
  }

  function _resetHome() {
    _homeSeq++
    homeFeeds = ({})
    homeChip = ""
    homeChips = []
  }

  // Ask with cache "prefer"; if that came from disk, follow up with a refresh.
  // `pending(true)` runs before the disk copy is handled when that refresh
  // follows, `pending(false)` once it has answered (after handling it, if
  // it worked). `timeoutMs` (default 30 s) applies to each request.
  function _cachedThenFresh(method, params, handle, pending, timeoutMs) {
    var wait = timeoutMs > 0 ? timeoutMs : 30000
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
      }, wait)
    }, wait)
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
  // or unseen for 10 min, searches past theirs, library lists (S2) unseen
  // for 10 min, the now contexts and lyrics (S11) of songs other than the
  // one playing, and Home's feeds (S5) of chips other than the chosen one
  // unseen for 10 min. A slice with its own keyed cache adds it here and to
  // sweepWanted.
  readonly property bool sweepWanted: collectionCount > 0 || searchCacheSize > 0 || libraryListCount > 0
    || nowSweepWanted || homeFeedCount > 1

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
    _sweepLibraryLists(t)
    _sweepNow(t)
    sweepHome(t)  // S5
  }

  Timer {
    interval: 60 * 1000
    repeat: true
    running: root.sweepWanted
    onTriggered: root.sweep()
  }

  // ---- library ------------------------------------------------------------------
  // Every playlist in the library (the Playlists chip and the playlist
  // picker), in the Playlists chip's order (S2: playlistOrders are the
  // served ones, playlistOrder the one they came in).
  readonly property int libraryTtlMs: 5 * 60 * 1000
  property var playlists: []
  property bool libraryLoading: false
  property string libraryError: ""
  property real libraryAt: 0
  property var playlistOrders: []
  property var playlistOrder: null
  // The order asked for (null: the one YouTube has saved).
  property var playlistsRequested: null
  // The order the held playlists were asked in.
  property var _playlistsAsked: null
  property int _librarySeq: 0

  // force: skip both the memory and the disk cache (after an edit).
  function loadLibrary(force) {
    // S2: Recent activity lists playlists too (the worker drops its copy
    // with the same edits).
    if (force) forgetCollection("libraryLanding")
    _loadPlaylists(force, libraryOrder("playlists"))
  }

  function _loadPlaylists(force, order) {
    var same = Model.sameOrder(order, playlistsRequested)
    if (!force && same && libraryLoading) return
    if (!force && same && libraryAt > 0 && Date.now() - libraryAt < libraryTtlMs) return
    var seq = ++_librarySeq
    playlistsRequested = order
    libraryLoading = true
    libraryError = ""
    var params = order !== null ? { order: order } : {}
    var handle = function(reply) {
      // A newer load (another order, an edit's refresh) replaced this one.
      if (seq !== root._librarySeq) return
      root.libraryLoading = false
      if (!reply.ok) {
        if (root.playlists.length === 0) {
          root.libraryError = reply.error.message
        } else if (!Model.sameOrder(order, root._playlistsAsked)) {
          // A new order failed over playlists held in another: name theirs
          // again and say why. prefs keep the new order, so the next load
          // asks for it again.
          root.playlistsRequested = root._playlistsAsked
          root.libraryError = reply.error.message
        }
        return
      }
      root.libraryError = ""
      root._playlistsAsked = order
      root.playlists = reply.result.items || []
      root.playlistOrders = reply.result.orders || []
      root.playlistOrder = reply.result.order !== undefined ? reply.result.order : null
      root.libraryAt = Date.now()
      root.requestThumbs(Model.collectThumbs(root.playlists))
    }
    if (force) {
      params.cache = "refresh"
      worker.request("libraryPlaylists", params, handle, 30000)
    } else {
      _cachedThenFresh("libraryPlaylists", params, handle)
    }
  }

  function invalidateLibrary() {
    libraryAt = 0
  }

  // ---- library (S2) ----------------------------------------------------------------
  // The Library tab's chip lists other than Playlists (that one is
  // `playlists` above): chip -> { items, cursor, hasMore, capped, order,
  // orders, current, header, message, loading, error, at, seenAt, seq,
  // loadingMore, moreError, moreWanted, refreshing }. `order` is the order
  // asked for (null: the one YouTube has saved), `current` the one the
  // items came in. A chip's list is fetched when it is shown
  // (loadLibraryList: the first page from disk, then fresh; within 5 min
  // the held list is reused) and grows a cursor page at a time
  // (loadLibraryMore), at most maxLibraryItems. A refresh replaces the whole
  // list; a Load more reply from before it is dropped. Orders come from
  // prefs.libraryOrders (Service binds libraryOrders); setLibraryOrder asks
  // Service to save the new one (libraryOrderChosen) and reloads in it. The
  // sweep drops a list unseen for 10 min, except the one shown
  // (libraryShown, set by LibraryView) and one still loading.
  readonly property int libraryListTtlMs: 5 * 60 * 1000
  readonly property int maxLibraryItems: 2000
  property var libraryLists: ({})
  property var libraryOrders: ({})
  property string libraryShown: ""
  property int _listSeq: 0
  readonly property int libraryListCount: Object.keys(libraryLists).length

  signal libraryOrderChosen(string chip, var order)

  // The order a chip's list is asked in (prefs), or null.
  function libraryOrder(chip) {
    return Model.libraryOrderOf(libraryOrders, chip)
  }

  // The order a chip's list was asked in last (what the view shows).
  function requestedOrder(chip) {
    if (chip === "playlists") return playlistsRequested
    var entry = libraryLists[chip]
    return entry ? entry.order : libraryOrder(chip)
  }

  function _setList(chip, entry) {
    var next = _copy(libraryLists)
    next[chip] = entry
    libraryLists = next
  }

  // The chip's list with `fields` changed (a new object, so bindings follow).
  function _patchList(chip, fields) {
    var entry = libraryLists[chip]
    if (!entry) return
    var next = _copy(entry)
    for (var key in fields) next[key] = fields[key]
    _setList(chip, next)
  }

  // force: skip the memory and disk copies (Load more on a disk copy, a
  // retry). A list held in another order starts over, empty.
  function loadLibraryList(chip, force) {
    _loadList(chip, libraryOrder(chip), force)
  }

  function _loadList(chip, order, force) {
    if (chip === "playlists") {
      _loadPlaylists(force, order)
      return
    }
    var spec = Model.libraryChipSpec(chip)
    if (!spec || !worker) return
    var entry = libraryLists[chip] || null
    var now = Date.now()
    var same = !!entry && Model.sameOrder(entry.order, order)
    if (!force && same && entry.loading) return
    if (!force && same && entry.at > 0 && now - entry.at < libraryListTtlMs && entry.error === "") {
      _patchList(chip, { seenAt: now })
      return
    }
    var seq = ++_listSeq
    var keep = same && entry.items.length > 0
    _setList(chip, {
      items: keep ? entry.items : [], cursor: keep ? entry.cursor : null, hasMore: keep ? entry.hasMore : false,
      capped: keep ? entry.capped : false, order: order, orders: entry ? entry.orders : [],
      current: same ? entry.current : order, header: keep ? entry.header : {}, message: keep ? entry.message : null,
      loading: true, error: "", at: keep ? entry.at : 0, seenAt: now, seq: seq, loadingMore: false, moreError: "",
      moreWanted: same && entry.moreWanted === true, refreshing: false
    })
    var params = order !== null ? { order: order } : {}
    var handle = function(reply) {
      var current = root.libraryLists[chip]
      if (!current || current.seq !== seq) return
      if (!reply.ok) {
        // A Load more pressed meanwhile goes on from the held cursor.
        var more = current.moreWanted === true && !!current.cursor
        root._patchList(chip, { loading: false, moreWanted: false,
                                error: current.items.length === 0 ? reply.error.message : "" })
        if (more) root.loadLibraryMore(chip, false)
        return
      }
      var list = Model.libraryList(reply.result)
      var wanted = current.moreWanted === true && !reply.cached
      var fields = { loading: false, error: "", moreError: "", at: Date.now(), seenAt: Date.now() }
      for (var key in list) fields[key] = list[key]
      if (wanted) fields.moreWanted = false
      root._patchList(chip, fields)
      root.requestThumbs(Model.collectThumbs(list.items))
      if (wanted) root.loadLibraryMore(chip, false)
    }
    // A list already shown is newer than its disk copy: straight to YouTube.
    if (force || keep) {
      params.cache = "refresh"
      worker.request(spec.method, params, handle, 30000)
    } else {
      _cachedThenFresh(spec.method, params, handle, function(on) {
        var current = root.libraryLists[chip]
        if (!current || current.seq !== seq) return
        // The refresh ended without a page for us (it failed): a Load more
        // pressed on the disk copy is spent.
        root._patchList(chip, on ? { refreshing: true } : { refreshing: false, moreWanted: false })
      })
    }
  }

  // The next cursor page of a chip's list. explicit: Load more was pressed
  // (on a disk copy, which has no cursor, that refreshes the list, or waits
  // for the refresh on its way, and then loads more); nearEnd and G pass
  // false and never refresh. While a refresh of a held list is on its way,
  // Load more waits for it too: it brings a new cursor (YouTube hands out a
  // new one with every first page), so a page read from the old one would
  // be dropped.
  function loadLibraryMore(chip, explicit) {
    var entry = libraryLists[chip]
    var spec = Model.libraryChipSpec(chip)
    if (!entry || !spec || chip === "playlists" || entry.loadingMore || entry.capped || !worker) return
    if (entry.loading) {
      if (explicit && (entry.cursor || entry.hasMore)) _patchList(chip, { moreWanted: true })
      return
    }
    if (!entry.cursor) {
      if (!explicit || !entry.hasMore) return
      if (!entry.refreshing) _loadList(chip, entry.order, true)
      _patchList(chip, { moreWanted: true })
      return
    }
    var seq = entry.seq
    var cursor = entry.cursor
    _patchList(chip, { loadingMore: true, moreError: "", seenAt: Date.now() })
    var params = entry.order !== null ? { order: entry.order } : {}
    params.cursor = cursor
    params.cache = "bypass"
    worker.request(spec.method, params, function(reply) {
      var current = root.libraryLists[chip]
      // A refresh replaced the list meanwhile: its own state stands. Under
      // the same seq (its fresh page landed with a new cursor) this page is
      // not the next one, but Load more is free again.
      if (!current || current.seq !== seq || current.cursor !== cursor) {
        if (current && current.seq === seq && current.loadingMore) root._patchList(chip, { loadingMore: false })
        return
      }
      if (!reply.ok) {
        root._patchList(chip, { loadingMore: false, moreError: reply.error.message })
        return
      }
      var next = Model.libraryList(reply.result)
      var merged = Model.appendLibraryList(current, next, root.maxLibraryItems)
      root._patchList(chip, { items: merged.items, cursor: merged.cursor, hasMore: merged.hasMore,
                              capped: merged.capped, loadingMore: false, seenAt: Date.now() })
      root.requestThumbs(Model.collectThumbs(next.items))
    }, 60000)
  }

  // O on a chip: Service saves the order (prefs.libraryOrders), the list
  // reloads in it. null goes back to the order YouTube has saved.
  function setLibraryOrder(chip, order) {
    var value = order === undefined ? null : order
    libraryOrderChosen(chip, value)
    _loadList(chip, value, false)
  }

  // chip -> items held (status).
  function libraryListSizes() {
    var out = ({})
    for (var chip in libraryLists) out[chip] = (libraryLists[chip].items || []).length
    return out
  }

  function _sweepLibraryLists(t) {
    var next = null
    for (var chip in libraryLists) {
      var entry = libraryLists[chip]
      if (chip === libraryShown || entry.loading || entry.loadingMore || t - entry.seenAt < unseenMs) continue
      if (!next) next = _copy(libraryLists)
      delete next[chip]
    }
    if (next) libraryLists = next
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

  // ---- actions (S4) ------------------------------------------------------------------
  // What the panel knows of the library beyond the lists it read: albums,
  // playlists and mixes saved (by playlistId, and an album's browseId too),
  // channels subscribed to (by the channelId rows and pages use), songs in
  // the library (by videoId), each a boolean set by an edit made here (at
  // once, put back when the worker says no) or forgotten again (null:
  // unknown) when its outcome is unknown; and the songs disliked this
  // session (YouTube reports a disliked song as INDIFFERENT). The states
  // themselves (savedState and the others) also read the library's
  // playlists, albums and subscriptions held here and the page's own
  // header (fallback). reset() clears them.
  property var saved: ({})
  property var subscribed: ({})
  property var inLibrary: ({})
  property var disliked: ({})

  // item: an album, playlist or mix; fallback: what its page's header says
  // (true / false), or undefined.
  function savedState(item, fallback) {
    var albums = libraryLists.albums || null
    return Model.savedState(item, {
      saved: saved, fallback: fallback, playlists: playlists, playlistsLoaded: libraryAt > 0,
      albums: albums ? albums.items : [], albumsComplete: !!albums && albums.at > 0 && !albums.cursor && !albums.hasMore
    })
  }

  // channelId: the page id rows and pages use; fallback: an artist page's
  // header.subscribed.
  function subscribedState(channelId, fallback) {
    var page = cachedCollection(Model.pageRef({ type: "librarySubscriptions" }))
    var list = page ? Model.libraryList(page) : null
    return Model.subscribedState(channelId, {
      subscribed: subscribed, fallback: fallback, subscriptions: list ? list.items : [],
      subscriptionsComplete: !!list && !list.cursor && !list.hasMore
    })
  }

  function inLibraryState(track) {
    return Model.inLibraryState(track, { inLibrary: inLibrary })
  }

  function isDisliked(videoId, fallback) {
    return Model.dislikedState(videoId, likes, disliked, fallback)
  }

  // A playlist of the library's that is yours.
  function ownsPlaylist(playlistId) {
    var id = String(playlistId || "")
    for (var i = 0; id !== "" && i < playlists.length; i++) if (playlists[i].playlistId === id) return playlists[i].owned === true
    return false
  }

  // The edits: each returns what the keys held before (for restore).
  function setSaved(keys, value) {
    var edit = Model.optimistic(saved, keys, value)
    saved = edit.map
    return edit.previous
  }

  function setSubscribed(channelId, value) {
    var edit = Model.optimistic(subscribed, channelId, value)
    subscribed = edit.map
    return edit.previous
  }

  function setInLibrary(videoId, value) {
    var edit = Model.optimistic(inLibrary, videoId, value)
    inLibrary = edit.map
    return edit.previous
  }

  // true while disliked here; false or null forgets it.
  function setDisliked(videoId, value) {
    var edit = Model.optimistic(disliked, videoId, value === true ? true : null)
    disliked = edit.map
    return edit.previous
  }

  // map: "saved" | "subscribed" | "inLibrary" | "disliked".
  function restore(map, previous) {
    if (map === "saved") saved = Model.restoreStates(saved, previous)
    else if (map === "subscribed") subscribed = Model.restoreStates(subscribed, previous)
    else if (map === "inLibrary") inLibrary = Model.restoreStates(inLibrary, previous)
    else if (map === "disliked") disliked = Model.restoreStates(disliked, previous)
  }

  // A chip's list is out of date (an edit changed it): the next show reads
  // it again, while what it holds stays shown.
  function staleLibraryList(chip) {
    if (libraryLists[chip]) _patchList(chip, { at: 0 })
  }

  function _resetActions() {
    saved = ({})
    subscribed = ({})
    inLibrary = ({})
    disliked = ({})
  }

  // ---- now (S11) ----------------------------------------------------------------------
  // The song playing's nowContext (Service reads one per track while a panel
  // is open; S12 reuses it for autoplay) and lyrics, both keyed by videoId:
  // a memo of 20 for 10 min, and an LRU of 20 lyrics (by when the Lyrics
  // pane last showed them) for 30 min. The sweep spares the song playing
  // (`nowKeep`, set by the service), so that song alone keeps no timer going.
  readonly property int nowContextTtlMs: Model.NOW_MEMO_TTL_MS
  readonly property int maxNowContexts: Model.NOW_MEMO_MAX
  readonly property int lyricsTtlMs: 30 * 60 * 1000
  readonly property int maxLyrics: 20
  property string nowKeep: ""
  property var _nowContexts: ({})
  property var _lyrics: ({})
  // videoId -> true while its lyrics load; videoId -> message when they failed.
  property var lyricsPending: ({})
  property var lyricsFailed: ({})
  readonly property int nowContextCount: Object.keys(_nowContexts).length
  readonly property int lyricsCount: Object.keys(_lyrics).length
  // Something the sweep may drop: an entry of a song other than nowKeep.
  readonly property bool nowSweepWanted: _holdsOther(_nowContexts, nowKeep) || _holdsOther(_lyrics, nowKeep)
    || _holdsOther(lyricsFailed, nowKeep)

  // The memo's context for videoId: only when younger than its TTL with
  // `fresh` (reuse it instead of asking again), else whatever its age.
  function nowContextOf(videoId, fresh) {
    return Model.nowMemoGet(_nowContexts, videoId, Date.now(), fresh ? nowContextTtlMs : 0)
  }

  function rememberNowContext(videoId, context) {
    _nowContexts = Model.nowMemoPut(_nowContexts, videoId, context, Date.now(), maxNowContexts)
  }

  // { text, source, lines } of videoId's lyrics, or null until loaded.
  function lyricsOf(videoId) {
    return Model.nowMemoGet(_lyrics, videoId, Date.now(), 0)
  }

  function lyricsError(videoId) {
    return lyricsFailed[videoId] || ""
  }

  // browseId is the song's lyricsId: time-synced lyrics when there are any
  // (the worker falls back to plain ones). Answers land in lyricsOf();
  // lyrics already held count as used now (the LRU's order).
  function loadLyrics(videoId, browseId) {
    if (!worker || !videoId || !browseId || lyricsPending[videoId]) return
    if (lyricsOf(videoId)) {
      _lyrics = Model.nowMemoTouch(_lyrics, videoId, Date.now())
      return
    }
    lyricsPending = _with(lyricsPending, videoId, true)
    lyricsFailed = _with(lyricsFailed, videoId, undefined)
    worker.request("lyrics", { browseId: browseId, timed: true, cache: "prefer" }, function(reply) {
      root.lyricsPending = root._with(root.lyricsPending, videoId, undefined)
      if (reply.ok) root._lyrics = Model.nowMemoPut(root._lyrics, videoId, reply.result, Date.now(), root.maxLyrics)
      // Only the song shown needs its failure (asking again clears it): keep
      // nowKeep's and this one, the sweep drops all but nowKeep's.
      else root.lyricsFailed = root._with(root._onlyKey(root.lyricsFailed, root.nowKeep), videoId, reply.error.message)
    }, 30000)
  }

  // A copy of map with key set (undefined: removed).
  function _with(map, key, value) {
    var next = ({})
    for (var k in map) if (k !== key) next[k] = map[k]
    if (value !== undefined) next[key] = value
    return next
  }

  // map with key only (an empty map when it lacks key).
  function _onlyKey(map, key) {
    var next = ({})
    if (key && map[key] !== undefined) next[key] = map[key]
    return next
  }

  function _holdsOther(map, key) {
    for (var k in map) if (k !== key) return true
    return false
  }

  function _sweepNow(now) {
    var contexts = Model.nowMemoSweep(_nowContexts, now, nowContextTtlMs, nowKeep)
    if (contexts !== _nowContexts) _nowContexts = contexts
    var lyrics = Model.nowMemoSweep(_lyrics, now, lyricsTtlMs, nowKeep)
    if (lyrics !== _lyrics) _lyrics = lyrics
    if (_holdsOther(lyricsFailed, nowKeep)) lyricsFailed = _onlyKey(lyricsFailed, nowKeep)
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
    // search (S7): your searches and the library scopes belong to the account.
    leaveScope()
    pastSearches = []
    pastSearchesAt = 0
    _pastSeq++
    _suggestCache = ({})
    playlists = []
    libraryAt = 0
    libraryError = ""
    libraryLoading = false
    playlistOrders = []
    playlistOrder = null
    playlistsRequested = null
    _playlistsAsked = null
    _librarySeq++
    libraryLists = ({})
    _listSeq++
    _resetHome()  // home (S5)
    likes = ({})
    _resetActions()  // S4
    _nowContexts = ({})
    // A failure may have come from the old session. Pending loads clear
    // themselves (every request is answered, at the latest by its timeout).
    lyricsFailed = ({})
  }

  // ---- tab requests (IPC "search" opens the panel on the search tab) -----------
  property string requestedTab: ""
  property int tabRequestSeq: 0

  function requestTab(tab) {
    requestedTab = tab
    tabRequestSeq++
  }
}
