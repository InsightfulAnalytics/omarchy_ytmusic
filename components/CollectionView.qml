import QtQuick
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// Any page opened from a row: album, playlist, mix, Liked Music, History,
// and sectioned pages (the worker's `page` shape: an artist, a See all
// list). Header with Play, Shuffle and Queue all (an artist adds Radio and
// About), sort chips when the page has orders, then its rows. One instance
// shows every entry of the panel's stack in turn; `entry` is the stack
// wrapper { item, view }, and `view` is what viewState() saved when another
// page was pushed on top.
//
// Collections (S3): the header adds the byline (album artists and a
// playlist's author, as links), details and two lines of the description
// (an About row shows the rest); a list with more songs than shown ends in
// Load all, which reloads it whole (limit "all") with the cursor kept; f
// opens a filter over the songs; the page's shelves follow the songs.
//
// Playlist edits (S13), on your own playlist: Edit (e) opens Panel's
// editor on editTarget(); J / K (moveTrack) move the song under the cursor
// one place (optimistic, put back when YouTube refuses; a playlist sorted
// by date or votes asks for Manual order first); Suggestions follow the
// songs (i adds the one under the cursor, Enter plays it, a Refresh row
// asks again). Rows show net votes when voting is on.
//
// Recommendations (S14): the page menu (M) of an album, playlist, Liked
// Music or artist pins it to Listen again (its header's pin); the taste
// page ("Add artists you like", from Library's account menu) lists
// YouTube's suggested artists: f finds, Enter marks or unmarks one here,
// and the Add row sends the marked ones after a second press.
Item {
  id: root

  property var service: null
  property QtObject bar: null
  property var entry: null
  readonly property var target: entry ? entry.item : null

  // Model.pageRef(target, { order }): set by open() and setOrder() only, so
  // a load always pairs the target with its order.
  property var ref: null
  // The sort order asked for (an `index` of page.orders: a position, or an
  // artistAlbums option title), null for the page's own.
  property var order: null
  property var page: null
  property bool loading: false
  property string error: ""
  property bool loadingMore: false
  property string moreError: ""
  // Load more was pressed on a disk copy: the refresh that brings its
  // cursor loads more when it lands.
  property bool _moreWanted: false
  property var _opened: null
  // The list place to restore once the page's rows are shown.
  property var _pendingView: null
  // ---- collections (S3)
  // Load all was chosen: the page is asked with limit "all".
  property bool full: false
  // The About row shows the whole description.
  property bool aboutOpen: false
  // f: the filter field is shown (its text filters the songs).
  property bool findOpen: false
  // ---- playlist edits (S13)
  // Your playlist's suggestions (playlistSuggestions), for _suggestFor.
  property var suggestions: []
  property bool suggestionsLoading: false
  property bool suggestionsLoaded: false
  property string suggestionsError: ""
  property string _suggestFor: ""
  property int _suggestSeq: 0
  // The entry the settings, adds and moves on their way were sent for.
  property int _editSeq: 0
  // Its owner settings (playlistSettings, or the suggestions' copy); known
  // once asked, even when YouTube reported nothing.
  property var settings: null
  property bool settingsKnown: false
  property bool settingsLoading: false
  // J / K: the move waiting for the settings, and the moves on their way.
  property var _pendingMove: null
  property int _movesPending: 0
  property bool _moveFailed: false
  // ---- recommendations (S14)
  // The taste page's marked artists (Model.toggleTasteMark's map), and the
  // add on its way.
  property var tasteMarks: ({})
  property bool tasteSending: false
  property int _tasteSeq: 0

  readonly property var store: service ? service.store : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string refKey: ref ? ref.key : ""
  // The disk copy shown is being refreshed in the background.
  readonly property bool refreshing: !!store && store.refreshPending(ref)
  readonly property bool isPage: !!page && page.type === "page"
  readonly property bool isArtist: !!ref && ref.kind === "artist"
  readonly property bool isLiked: !!ref && ref.kind === "liked"
  readonly property bool isHistory: !!ref && ref.kind === "history"
  readonly property bool isTaste: !!ref && ref.kind === "taste"  // S14
  readonly property bool editable: !!page && page.editable === true
  readonly property bool round: Model.pageRound(ref, page)
  readonly property var tracks: !page ? [] : isPage ? Model.pageTracks(page) : (page.tracks || [])
  // One binding (S13's Suggestions included), so Back's place is looked up
  // in the whole list, never in a half-updated one.
  readonly property var rows: !page ? [] : isPage ? Model.pageRows(page)
    // S14: the taste page's Add row and suggested artists.
    : isTaste ? Model.tasteRows(page, { marked: tasteMarks, find: findText, armed: gate.armed(Model.TASTE_ADD_ID),
                                        sending: tasteSending })
    : _withSuggestions(Model.collectionRows(page, { history: isHistory, find: findText, about: descriptionText.truncated,
                                                    aboutOpen: aboutOpen }))
  // The page shows part of its list (Liked Music past 200 songs, a page with
  // more to load, an artist's top songs): Play, Shuffle and Queue all then
  // read the whole list (Service.playItem), at most 500 queued.
  readonly property bool incomplete: !!page && (page.truncated === true || Model.pagePartial(ref ? ref.kind : "")
    || (isPage && (!!page.cursor || page.hasMore === true)))
  readonly property var orders: page && Array.isArray(page.orders) ? page.orders : []
  readonly property var currentOrder: order !== null ? order : (page && page.order !== undefined ? page.order : null)
  readonly property alias results: resultList

  // A mix keeps the name of the row it was opened from (S3: the queue
  // YouTube serves for it may be titled "Your Queue").
  readonly property string title: (ref && ref.kind === "mix" && target && target.title)
    || (page && (page.title || page.name)) || (target && (target.title || target.name))
    || Model.pageKindTitle(ref)
  readonly property string subtitle: !page ? Model.itemSubtitle(target)
    : isPage ? Model.pageSubtitle(ref, page)
    : Model.isCollection(page) ? Model.collectionMeta(page, tracks.length)
    : String(page.subtitle || "") + (page.truncated ? " • showing " + tracks.length : "")
  // Artist (S1): YouTube's shuffle and radio mixes, and the About page.
  readonly property var shuffleItem: isArtist ? Model.artistShuffleItem(page) : null
  readonly property var radioItem: isArtist ? Model.artistRadioItem(page) : null
  readonly property bool hasAbout: isArtist && Model.hasAbout(page)
  // Collections (S3).
  readonly property bool isCollection: Model.isCollection(page)
  readonly property var byline: Model.collectionByline(page)
  readonly property string description: Model.collectionDescription(page)
  readonly property bool explicitAlbum: isCollection && page.kind === "album" && Model.collectionHeader(page).explicit === true
  readonly property bool canLoadAll: !full && Model.canLoadAll(page)
  // The whole list is loading over the first read.
  readonly property bool loadingAll: full && loading
  readonly property string findText: findOpen ? findField.text : ""
  readonly property bool inputFocused: findField.activeFocus
  // The list's identity: the page and its order, not how much of it is
  // read, so Load all keeps the cursor and the scroll.
  readonly property string listId: entry ? (Model.pageRef(entry.item, Model.pageExtra(order, false)) || { key: "" }).key : ""
  // Library actions (S4): Save on an album, a playlist that is not yours or
  // a mix; Subscribe on an artist. The state shown is the Store's (an edit
  // made here), else the page header's, else the library's lists.
  readonly property bool signedIn: !!service && service.signedIn
  readonly property var pageSaveItem: isCollection ? pageItem() : null
  readonly property bool canSavePage: signedIn && !!pageSaveItem
    && Model.canSave(pageSaveItem, { page: true, owned: page.owned === true })
  readonly property bool pageSaved: canSavePage && !!store && store.savedState(pageSaveItem, headerSaved()) === true
  readonly property bool canSubscribePage: signedIn && isArtist && !!Model.artistHeader(page)
  readonly property bool pageSubscribed: canSubscribePage && !!store
    && store.subscribedState(subscribeItem().channelId, Model.artistHeader(page).subscribed === true) === true

  signal openRequested(var item)
  // The page itself went away (playlist deleted).
  signal closeRequested()
  // The find field gave the keys back (Esc, Down, Enter).
  signal leftField()
  // A row's menu, or the page's (M): Panel opens it.
  signal itemMenuRequested(var request)
  // S13: the header's Edit (Panel opens its editor on editTarget()).
  signal editRequested()

  ConfirmGate {
    id: gate
  }

  // ---- opening and loading ----------------------------------------------------------

  function open() {
    if (entry === _opened) return
    _opened = entry
    var view = entry ? entry.view : null
    order = view && view.order !== undefined ? view.order : null
    full = !!view && view.full === true
    aboutOpen = !!view && view.aboutOpen === true
    closeFind()
    ref = entry ? Model.pageRef(entry.item, Model.pageExtra(order, full)) : null
    page = null
    error = ""
    loading = false
    loadingMore = false
    moreError = ""
    _moreWanted = false
    gate.reset()
    _resetEdits(view ? view.edits : null)  // S13
    _resetTaste()  // S14
    _pendingView = view ? (view.list || null) : null
    if (entry) load(false)
  }

  // fresh: skip the memory and disk caches (see Store.loadCollection). The
  // page shown stays until the reply replaces it, keeping the cursor.
  function load(fresh) {
    // Loading before store is read: the first read evaluates its binding,
    // and onStoreChanged would load the page a second time.
    loading = true
    if (!store) {
      loading = false
      return
    }
    var key = refKey
    store.loadCollection(ref, function(reply) {
      if (root.refKey !== key) return
      root.loading = false
      // A disk copy (cached) is followed by its refresh.
      var wanted = root._moreWanted && !reply.cached
      if (wanted) root._moreWanted = false
      if (reply.ok) {
        root.error = ""
        // A new first page starts a new chain: an old failure is moot.
        root.moreError = ""
        root.page = reply.result
        if (wanted) root.loadMore(true)
        root._pageLoaded()  // S13
      } else if (root.full && Model.canLoadAll(root.page)) {
        // Load all failed over the first read: that stays, and its Load
        // all row says why (Enter tries again).
        root.full = false
        root.ref = Model.pageRef(root.entry.item, Model.pageExtra(root.order, false))
        root.moreError = reply.error.message
      } else {
        root.error = reply.error.message
      }
    }, fresh === true)
  }

  // The next cursor page. A copy from the disk cache has no cursor: its
  // background refresh brings one, so only an explicit Load more refreshes,
  // or waits for the refresh already on its way, and then loads more.
  function loadMore(explicit) {
    if (!store || !ref || !page || loadingMore) return
    if (!page.cursor) {
      if (!explicit || !page.hasMore) return
      _moreWanted = true
      if (!loading && !refreshing) load(true)
      return
    }
    var key = refKey
    loadingMore = true
    moreError = ""
    store.loadMore(ref, function(reply) {
      if (root.refKey !== key) return
      root.loadingMore = false
      if (reply.ok) root.page = reply.result
      else if (!reply.stale) root.moreError = reply.error.message
    }, page)
  }

  // For Back: the sort order, Load all, the About row and the list's place.
  function viewState() {
    return { order: order, full: full, aboutOpen: aboutOpen, list: resultList.viewState(), edits: _editsState() }
  }

  function restore(view) {
    if (!view) return
    aboutOpen = view.aboutOpen === true
    var wantsAll = view.full === true
    if (((view.order !== undefined && view.order !== order) || wantsAll !== full) && entry) {
      if (view.order !== undefined) order = view.order
      full = wantsAll
      ref = Model.pageRef(entry.item, Model.pageExtra(order, full))
      _pendingView = view.list || null
      load(false)
      return
    }
    _pendingView = view.list || null
    _applyPending()
  }

  function _applyPending() {
    if (!_pendingView || rows.length === 0) return
    var view = _pendingView
    _pendingView = null
    resultList.restoreView(view)
  }

  // O: the next of the page's sort orders (nothing when it has none).
  function cycleOrder() {
    if (orders.length < 2) return false
    var at = -1
    for (var i = 0; i < orders.length; i++) if (orders[i].index === currentOrder) at = i
    setOrder(orders[(at + 1) % orders.length].index)
    return true
  }

  function setOrder(index) {
    if (!entry || loading || index === currentOrder) return
    order = index
    ref = Model.pageRef(entry.item, Model.pageExtra(index, full))
    error = ""
    loadingMore = false
    moreError = ""
    load(false)
  }

  onEntryChanged: open()
  onRefKeyChanged: _moreWanted = false
  // Ended without a page for us (it failed): the press is spent.
  onRefreshingChanged: if (!refreshing) _moreWanted = false
  onStoreChanged: if (store && entry && !page && !loading) load(false)
  Component.onCompleted: open()

  // ---- rows -------------------------------------------------------------------------

  // Playlist pages remove from the playlist; Liked Music un-likes. When the
  // outcome is unknown (the worker stopped mid-edit), reload the page.
  // Replies only touch the page if it is still the one edited: this view
  // shows every page of the panel's stack in turn.
  function remove(index) {
    var row = resultList.rowAt(index)
    if (!row || row.item.type !== "track" || row.suggestion === true || !service) return
    var track = row.item
    var requested = ref
    if (isLiked) {
      if (service.setLike(track, false, function(reply) { root.reloadIfUnsure(reply, requested) }))
        page = Model.withoutTrack(page, track)
    } else if (editable) {
      service.removeFromPlaylist(page.id, [track], function(reply) {
        if (reply.ok) {
          if (root.shows(requested)) root.page = Model.withoutTrack(root.page, track)
        } else {
          root.reloadIfUnsure(reply, requested)
        }
      })
    }
  }

  function shows(requested) {
    return !!ref && !!requested && ref.key === requested.key
  }

  // Straight from YouTube: the disk copy predates the edit. (If another page
  // is shown now, Service.refreshUnsure has already forgotten this one.)
  function reloadIfUnsure(reply, requested) {
    if (service && service.unsure(reply) && shows(requested)) load(true)
  }

  // A second press within 4 s deletes; `confirmed` when the page menu
  // already asked twice.
  function deletePlaylist(confirmed) {
    if (!editable || !service) return
    if (confirmed !== true && !gate.arm("delete:" + page.id)) return
    var requested = ref
    service.deletePlaylist({ playlistId: page.id, title: page.title }, function(reply) {
      if (reply.ok) {
        if (root.shows(requested)) root.closeRequested()
      } else {
        root.reloadIfUnsure(reply, requested)
      }
    })
  }

  // Action rows: one that names a target opens it; slices handle their ids here.
  function runAction(item) {
    if (item.target) root.openRequested(item.target)
    // Collections (S3): the About row shows or hides the description.
    else if (item.id === Model.COLLECTION_ABOUT_ID) toggleAbout()
    // S13: Refresh suggestions.
    else if (item.id === Model.SUGGESTIONS_REFRESH_ID) loadSuggestions()
    // S14: the taste page's Add row, or an artist to mark or unmark.
    else if (Model.isTasteAction(item.id)) tasteAction(item.id)
    // About (S1): a description link row copies its URL.
    else if (item.url && service) service.copyLink({ type: "link", url: item.url })
  }

  function activate(index) {
    var row = resultList.rowAt(index)
    if (!row || !service) return
    var item = row.item
    if (item.type === "more") root.openRequested(Model.moreTarget(item))
    else if (item.type === "loadMore" && item.id === Model.LOAD_ALL_ID) loadAll()
    else if (item.type === "loadMore") loadMore(true)
    else if (item.type === "action") runAction(item)
    else if (item.type === "message" || item.type === "text") return
    else if (row.suggestion === true) service.playItem(item, "now")  // S13: a suggestion plays alone
    else if (row.trackIndex !== undefined)  // S12: the page is what plays ("Playing from", autoplay)
      service.playCollection(isPage ? Model.sectionTracks(page, row.sectionIndex) : tracks, row.trackIndex, false,
                             { playlistId: page ? page.playlistId || null : null, title: title, radio: false })
    else root.openRequested(item)
  }

  // n / a: a song, or a whole album or playlist row (an artist's albums).
  function playNext(index) {
    var row = resultList.rowAt(index)
    if (row && Model.canQueue(row.item) && service) service.playItem(row.item, "next")
  }

  function addToQueue(index) {
    var row = resultList.rowAt(index)
    if (row && Model.canQueue(row.item) && service) service.playItem(row.item, "queue")
  }

  // ---- menus (F3) -------------------------------------------------------------------

  function menu(index) {
    var row = resultList.rowAt(index)
    if (!row || Model.menuKind(row.item) === "") return
    var suggestion = row.suggestion === true  // S13: offers Add to this playlist, not Remove
    var removable = row.item.type === "track" && (editable || isLiked) && !suggestion
    root.itemMenuRequested({ item: row.item, owner: root, index: index,
      ctx: { pageKind: ref ? ref.kind : "", owned: editable, removable: removable, suggestion: suggestion,
             removeTitle: isLiked ? "Remove from Liked Music" : "Remove from playlist",
             pinSource: Model.pinSource(ref) } })  // S14: the page a song's pin token came from
  }

  // The page as an item for its own menu: the opened item, completed from
  // the page (an album's audio playlist feeds Start radio).
  function pageItem() {
    if (!target) return null
    var item = ({})
    for (var key in target) item[key] = target[key]
    if (page && !item.playlistId && page.playlistId && ref && (ref.kind === "album" || ref.kind === "playlist"))
      item.playlistId = page.playlistId
    if (page && !item.title && !item.name && (page.title || page.name)) item.title = page.title || page.name
    // S3: an album opened from a link knows its artists from the page (Go to artist).
    if (ref && ref.kind === "album" && Model.linkedArtists(item).length === 0)
      item.artists = Model.collectionHeader(page).artists || []
    // S14: the header's Listen again pin (the page menu's Pin / Unpin).
    return Model.withPagePin(item, page)
  }

  // M: the header buttons' twins, then what the item offers (radio, share,
  // go to artist) and Delete on your own playlist.
  function pageMenu() {
    return { item: pageItem(), owner: root, index: -1, title: title, subtitle: subtitle,
             ctx: { page: true, pageKind: ref ? ref.kind : "", owned: editable, hasTracks: tracks.length > 0,
                    about: hasAbout, canLoadAll: canLoadAll && !loading, loadAllTitle: Model.loadAllTitle(page),
                    canFind: Model.canFind(page) || isTaste, aboutOpen: aboutOpen,
                    // The About row is there to show or hide.
                    description: Model.showsAbout(page, { find: findText, about: descriptionText.truncated }),
                    // S4: the header's Save and Subscribe (none until the page has loaded).
                    saved: pageSaved, subscribed: pageSubscribed,
                    canSavePage: canSavePage, canSubscribePage: canSubscribePage,
                    // S14: the page's pin token's source, and the taste page's marks.
                    pinSource: Model.pinSource(ref), tasteMarked: isTaste ? Model.tasteMarked(page, tasteMarks).length : 0 } }
  }

  // Play, Shuffle, Queue all (mode now, shuffle, queue; next from the menu).
  // An artist's Shuffle plays YouTube's shuffle mix, as on the web.
  function playAll(mode) {
    if (!service) return
    var item = pageItem()
    if (mode === "shuffle" && shuffleItem) service.playItem(shuffleItem, "now")
    else if (incomplete && Model.tracksForParams(item)) service.playItem(item, mode)
    else service.playTracks(tracks, mode, { playlistId: page ? page.playlistId || null : null, title: title, radio: false }, title)
  }

  // ---- artist (S1) -------------------------------------------------------------------

  // Radio: the artist's radio mix (the page item's when the page has none).
  function startArtistRadio() {
    if (service) service.startRadio(radioItem || pageItem())
  }

  // Panel's R when the cursor row has no radio (no row, a See all row): the
  // artist's radio on an artist page. False elsewhere, so R falls back to
  // the song playing.
  function pageRadio() {
    if (!isArtist) return false
    startArtistRadio()
    return true
  }

  function openAbout() {
    var item = Model.aboutItem(page)
    if (item) root.openRequested(item)
  }

  // ---- collections (S3) --------------------------------------------------------------

  // Every song (limit "all", up to 5000): the first read stays shown until
  // the whole list replaces it, and the cursor and scroll stay on the same
  // rows (the list's identity, listId, leaves the limit out).
  function loadAll() {
    if (!entry || !canLoadAll || loading) return false
    full = true
    moreError = ""
    ref = Model.pageRef(entry.item, Model.pageExtra(order, true))
    load(false)
    return true
  }

  function toggleAbout() {
    aboutOpen = !aboutOpen
  }

  // f (Panel's find hook): the filter field over the page's songs. A page
  // that shows part of its list loads all of it first.
  function find() {
    if (!Model.canFind(page) && !isTaste) return false  // S14: the taste page finds artists
    if (canLoadAll) loadAll()
    findOpen = true
    findField.forceActiveFocus()
    return true
  }

  // Esc in the field: the filter goes and the keys go back to the list.
  function closeFind() {
    var focused = findField.activeFocus
    findField.text = ""
    findOpen = false
    if (focused) root.leftField()
  }

  // Esc in the list (Panel's clearFind hook): an open filter goes before
  // the page does.
  function clearFind() {
    if (!findOpen) return false
    closeFind()
    return true
  }

  // A with no row selected (Panel's pageArtist hook): an album's first artist.
  function pageArtist() {
    var item = Model.pageArtistItem(page)
    if (!item) return false
    root.openRequested(item)
    return true
  }

  // ---- playlist edits (S13) -----------------------------------------------------------

  // A new entry starts without suggestions or settings; one coming back
  // (Back) has them from its saved view, so nothing is asked again.
  function _resetEdits(saved) {
    _suggestSeq++
    _editSeq++
    var s = saved || {}
    suggestions = s.items || []
    suggestionsLoaded = s.loaded === true
    suggestionsLoading = false
    suggestionsError = ""
    _suggestFor = s.forId || ""
    settings = s.settings || null
    settingsKnown = s.known === true
    settingsLoading = false
    _pendingMove = null
    _movesPending = 0
    _moveFailed = false
  }

  // Your playlist's rows with the Suggestions section after the songs (not
  // while finding). Read from the page itself, not editable / isCollection:
  // rows may be evaluated before those follow a new page.
  function _withSuggestions(list) {
    if (!(page && page.editable === true && Model.isCollection(page) && signedIn && findText.trim() === "")) return list
    return Model.withSuggestionRows(list, { items: suggestions, loading: suggestionsLoading, error: suggestionsError,
                                            loaded: suggestionsLoaded })
  }

  // Back asks again for suggestions that had not come (or had failed) when
  // the page was left: only a finished load counts as had.
  function _editsState() {
    var had = suggestionsLoaded && !suggestionsLoading && suggestionsError === ""
    return { forId: had ? _suggestFor : "", items: suggestions, loaded: had, settings: settings, known: settingsKnown }
  }

  function _playlistId() {
    return page ? String(page.playlistId || page.id || "") : ""
  }

  // Once your playlist's page is shown: its suggestions (once per entry).
  function _pageLoaded() {
    if (editable && signedIn && _suggestFor !== _playlistId()) loadSuggestions()
  }

  // The suggestions YouTube has for your playlist now (Refresh asks again;
  // they bring the owner settings along).
  function loadSuggestions() {
    if (!service || !editable || suggestionsLoading) return false
    var id = _playlistId()
    var seq = ++_suggestSeq
    _suggestFor = id
    suggestionsLoading = true
    suggestionsError = ""
    var sent = service.playlistSuggestions(id, function(reply) {
      if (!root || root._suggestSeq !== seq) return
      root.suggestionsLoading = false
      root.suggestionsLoaded = true
      if (!reply.ok) {
        root.suggestionsError = reply.error.message
        return
      }
      root.suggestions = reply.result.items || []
      if (reply.result.settings) root._takeSettings(reply.result.settings)
    })
    if (!sent) suggestionsLoading = false
    return sent
  }

  function _takeSettings(value) {
    settings = value
    settingsKnown = true
  }

  function loadSettings() {
    if (!service || !editable || settingsLoading) return
    var id = _playlistId()
    var seq = _editSeq
    settingsLoading = true
    var sent = service.playlistSettings(id, function(reply) {
      if (!root || root._editSeq !== seq) return
      root.settingsLoading = false
      // Unknown settings never block a move: YouTube is asked then.
      if (reply.ok) root._takeSettings(reply.result)
      else root.settingsKnown = true
      var pending = root._pendingMove
      root._pendingMove = null
      if (pending) root._moveBySetId(pending.setVideoId, pending.delta)
    })
    if (!sent) settingsLoading = false
  }

  // i (Panel's addSuggestion hook) or the row menu's Add to this playlist:
  // the suggestion leaves the list at once and the page is read again to
  // show it among the songs; a failure puts it back.
  function addSuggestion(index) {
    if (!service) return false
    var row = resultList.rowAt(index === undefined ? resultList.cursorIndex : index)
    if (!row || row.suggestion !== true) {
      service.failAction("Nothing to add", "Put the cursor on one of the suggestions under the songs")
      return false
    }
    var track = row.item
    var before = suggestions
    var seq = _editSeq
    var requested = ref
    suggestions = Model.withoutSuggestion(suggestions, track)
    var sent = service.addToPlaylist({ playlistId: _playlistId(), title: title }, [track], function(reply) {
      if (!root || root._editSeq !== seq) return false
      if (reply.ok || root.service.unsure(reply)) {
        if (root.shows(requested)) root.load(true)
      } else if (reply.error.code !== "DUPLICATE") {
        root.suggestions = before
      }
      return false  // Service notifies (Already in, or why it failed)
    })
    if (!sent) suggestions = before
    return sent
  }

  // J / K (Panel's moveTrack hook) on the cursor's song, and the row's move
  // buttons. False when the row is no song of your playlist.
  function moveTrack(delta) {
    return moveTrackAt(resultList.cursorIndex, delta)
  }

  function moveTrackAt(index, delta) {
    var row = resultList.rowAt(index)
    if (!service || !editable || !row || row.trackIndex === undefined || row.suggestion === true) return false
    if (findText.trim() !== "") {
      service.failAction("Can't move songs while finding", "Clear the filter first (Esc)")
      return true
    }
    if (loading || refreshing) {
      service.failAction("Can't move the song yet", "The playlist is still loading")
      return true
    }
    return _moveBySetId(row.item.setVideoId, delta)
  }

  function _moveBySetId(setVideoId, delta) {
    if (!page || !setVideoId) return false
    // The settings say whether YouTube keeps its own order: asked once, first.
    if (!settingsKnown) {
      _pendingMove = { setVideoId: setVideoId, delta: delta }
      loadSettings()
      return true
    }
    var blocked = Model.orderBlocksMoves(settings)
    if (blocked !== "") {
      service.failAction("Set order to Manual first", "This playlist is sorted " + blocked + "; change it with e (Advanced, Order)")
      return true
    }
    var at = -1
    var list = page.tracks || []
    for (var i = 0; i < list.length; i++) if (list[i].setVideoId === setVideoId) at = i
    var move = Model.moveTrack(page, at, delta)
    if (!move.page) {
      if (move.problem !== "") service.failAction("Can't move the song", move.problem)
      return true
    }
    _sendMove(move)
    return true
  }

  // Shown at once; sent in order. A refusal puts this move back when no
  // later one is on its way (they were made on top of it: the page is read
  // again once they are done); an unknown outcome reads it again. A failure
  // is said even when the page was left meanwhile (Service has dropped the
  // playlist's pages then).
  function _sendMove(move) {
    var before = page
    var seq = _editSeq
    var requested = ref
    var svc = service
    var name = title
    _movesPending++
    page = move.page
    var sent = service.movePlaylistItem(move.params, function(reply) {
      var unsure = !reply.ok && svc.unsure(reply)
      if (unsure) svc.notify("Not sure the song moved; refreshed", name)
      else if (!reply.ok) svc.failAction("Couldn't move the song", reply.error.message)
      if (!root || root._editSeq !== seq) return
      root._movesPending = Math.max(0, root._movesPending - 1)
      if (!root.shows(requested)) return  // Load all or a new order replaced the page meanwhile
      if (unsure) {
        root._moveFailed = true
      } else if (!reply.ok) {
        if (root._movesPending === 0 && !root._moveFailed) root.page = before
        else root._moveFailed = true
      }
      if (root._movesPending === 0 && root._moveFailed) {
        root._moveFailed = false
        root.load(true)
      }
    })
    if (!sent) {
      _movesPending--
      page = before
    }
  }

  // Panel's editor (e, the header's Edit, the page menu): your playlist as
  // the editor needs it, or null on any other page. done(reply) reads the
  // page again after a change (if it is still shown).
  function editTarget() {
    if (!editable || !isCollection || !page) return null
    var requested = ref
    var header = Model.collectionHeader(page)
    return { playlistId: _playlistId(), title: page.title || title, description: header.description || "",
             privacy: header.privacy || null, collaborators: header.collaborators || null, settings: settings,
             done: function(reply) { if (root) root.afterEdit(reply, requested) },
             onSettings: function(value) { if (root && root.shows(requested)) root._takeSettings(value) } }
  }

  function afterEdit(reply, requested) {
    if (!service || !shows(requested)) return
    if (!reply.ok && !service.unsure(reply)) return
    settings = null
    settingsKnown = false
    load(true)
  }

  // ---- library actions (S4) ---------------------------------------------------------

  // What the header says: an album's or playlist's likeStatus (LIKE: in the
  // library), undefined when it says nothing (your own playlist, a mix).
  function headerSaved() {
    var status = Model.collectionHeader(page).likeStatus
    return status === "LIKE" ? true : status === "INDIFFERENT" ? false : undefined
  }

  // The artist page as the item Subscribe acts on, with the channel its
  // subscribe button names (S1's header.subscribeId).
  function subscribeItem() {
    var header = Model.artistHeader(page) || {}
    return { type: "artist", channelId: header.channelId || (target ? target.channelId : ""),
             subscribeId: header.subscribeId || null, title: title }
  }

  // S with no row (Panel's toggleSave hook), the header's Save or
  // Subscribe, and the page menu's twin. A page with neither (still
  // loading, failed, your own playlist) says so.
  function toggleSave() {
    if (!service) return false
    if (canSubscribePage) return service.subscribe(subscribeItem(), !pageSubscribed, editCallback())
    if (canSavePage) return service.saveItem(pageSaveItem, !pageSaved, editCallback())
    service.failAction("Nothing to save", !signedIn ? "Sign in to use your library"
      : (loading && !page) || error !== "" ? "The page has not loaded" : title)
    return false
  }

  // The callback of an edit made from this page (Model.editCallbackOf):
  // when its outcome is unknown or a song's token was stale, the page is
  // read again from YouTube, if it is still the one shown (true then).
  // The view may be gone when the reply comes (Back): nothing then.
  function editCallback() {
    var requested = ref
    return function(reply) { return !!root && root.reloadAfterEdit(reply, requested) }
  }

  function reloadAfterEdit(reply, requested) {
    if (!service || !shows(requested) || !(service.unsure(reply) || Model.staleToken(reply))) return false
    load(true)
    return true
  }

  // ---- recommendations (S14) -------------------------------------------------------

  function _resetTaste() {
    _tasteSeq++
    tasteMarks = ({})
    tasteSending = false
  }

  function tasteAction(id) {
    if (id === Model.TASTE_ADD_ID) addTaste(false)
    else toggleTaste(Model.tasteNameOf(id))
  }

  // Enter on an artist: marked here only, until the Add row sends them
  // (at most Model.TASTE_ADD_MAX at once). A change of marks asks the Add
  // row's question again.
  function toggleTaste(name) {
    if (!isTaste || tasteSending || name === "") return
    if (!Model.canTasteMark(page, tasteMarks, name)) {
      if (service) service.failAction("At most " + Model.groupedCount(Model.TASTE_ADD_MAX) + " artists at a time",
                                      "Add the marked ones first, then mark more")
      return
    }
    gate.reset()
    tasteMarks = Model.toggleTasteMark(tasteMarks, name)
  }

  // Down or Enter in the find field: the first artist shown, not the Add
  // row above them (no cursor when none matches).
  function _toFirstTasteArtist() {
    var index = Model.firstTasteArtist(rows)
    resultList.cursorIndex = index
    if (index >= 0) resultList.positionViewAtIndex(index, ListView.Contain)
    else resultList.positionViewAtBeginning()
  }

  function clearTaste() {
    gate.reset()
    tasteMarks = ({})
  }

  // The Add row (a second press within 4 s sends), and the page menu's
  // twin (`confirmed`: it asked twice already). Taste picks cannot be
  // taken out through the API. The marks go once YouTube took them, and
  // the list is read again (its suggestions change); a name it no longer
  // suggests, or an unknown outcome, reads it again too.
  function addTaste(confirmed) {
    if (!service || !isTaste || tasteSending) return false
    var names = Model.tasteMarked(page, tasteMarks)
    if (names.length === 0) {
      service.failAction("No artists marked", "Press Enter on the artists you like first")
      return false
    }
    if (confirmed !== true && !gate.arm(Model.TASTE_ADD_ID)) return true
    gate.reset()
    var seq = _tasteSeq
    var requested = ref
    tasteSending = true
    var sent = service.setTasteProfile(names, function(reply) {
      if (!root || root._tasteSeq !== seq) return false
      root.tasteSending = false
      if (!root.shows(requested)) return false
      if (reply.ok) root.tasteMarks = ({})
      if (!reply.ok && !root.service.unsure(reply) && reply.error.code !== "BAD_REQUEST") return false
      root.load(true)
      return true
    })
    if (!sent) tasteSending = false
    return sent
  }

  Row {
    id: header
    width: parent.width
    spacing: Style.space(14)

    CoverArt {
      id: cover
      size: Style.space(88)
      radius: root.round ? size / 2 : Math.max(2, Math.round(size * 0.1))
      store: root.store
      source: root.page && root.page.thumbLarge ? root.page.thumbLarge
        : root.target ? (root.target.thumbLarge || root.target.thumb || "") : ""
      foreground: root.foreground
      fontFamily: root.fontFamily
    }

    Column {
      anchors.verticalCenter: parent.verticalCenter
      width: parent.width - cover.width - parent.spacing
      spacing: Style.space(4)

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: root.title
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.subtitle
        font.bold: true
        wrapMode: Text.Wrap
        maximumLineCount: 2
        elide: Text.ElideRight
      }

      // S3: an album's type, artists (links) and year; a playlist's author.
      Flow {
        id: bylineFlow
        width: parent.width
        visible: root.byline.length > 0
        spacing: 0

        Item {
          visible: root.explicitAlbum
          width: albumBadge.width + Style.space(4)
          height: albumBadge.height

          ExplicitBadge {
            id: albumBadge
            foreground: root.foreground
            fontFamily: root.fontFamily
          }
        }

        Repeater {
          model: root.byline

          Text {
            id: part
            required property var modelData
            readonly property bool linked: !!modelData.item
            width: Math.min(part.implicitWidth, bylineFlow.width)
            textFormat: Text.PlainText
            text: String(part.modelData.text)
            color: Qt.darker(root.foreground, part.linked ? 1.3 : 1.4)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.underline: part.linked && partHover.hovered
            elide: Text.ElideRight

            HoverHandler {
              id: partHover
              enabled: part.linked
              cursorShape: Qt.PointingHandCursor
            }

            TapHandler {
              enabled: part.linked
              onTapped: root.openRequested(part.modelData.item)
            }
          }
        }
      }

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: root.subtitle
        visible: text !== ""
        color: Qt.darker(root.foreground, 1.4)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
      }

      // Two lines of the description; the About row shows all of it.
      Text {
        id: descriptionText
        width: parent.width
        visible: text !== ""
        textFormat: Text.PlainText
        text: root.description
        color: Qt.darker(root.foreground, 1.5)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.Wrap
        maximumLineCount: 2
        elide: Text.ElideRight
      }

      Flow {
        width: parent.width
        spacing: Style.space(4)
        // S13: your playlist's Edit and Delete show on an empty one too.
        visible: root.tracks.length > 0 || root.isArtist || root.canSavePage || root.editable

        Button {
          visible: root.tracks.length > 0
          iconText: "󰐊"
          text: "Play"
          tooltipText: root.incomplete ? "Plays all of it, not only what is shown (at most 500 songs)" : ""
          foreground: root.foreground
          fontSize: Style.font.caption
          onClicked: root.playAll("now")
        }

        Button {
          visible: root.tracks.length > 0 || !!root.shuffleItem
          text: "Shuffle"
          tooltipText: root.shuffleItem ? "Play the artist's shuffle mix" : "Turn shuffle on and play in random order"
          foreground: root.foreground
          fontSize: Style.font.caption
          onClicked: root.playAll("shuffle")
        }

        Button {
          visible: root.isArtist
          text: "Radio"
          tooltipText: "Start the artist's radio"
          foreground: root.foreground
          fontSize: Style.font.caption
          onClicked: root.startArtistRadio()
        }

        Button {
          visible: root.tracks.length > 0
          text: "Queue all"
          tooltipText: "Add to the queue (" + Model.keyHint("pageMenu") + " has more)"
          foreground: root.foreground
          fontSize: Style.font.caption
          onClicked: root.playAll("queue")
        }

        // S4: Save to library, and Subscribe on an artist page.
        Button {
          visible: root.canSavePage
          iconText: root.pageSaved ? "󰃀" : "󰃃"
          text: root.pageSaved ? "Saved" : "Save"
          tooltipText: Model.withKey(root.pageSaved ? "Remove from library" : "Save to library", "save")
          foreground: root.pageSaved ? Color.accent : root.foreground
          fontSize: Style.font.caption
          onClicked: root.toggleSave()
        }

        Button {
          visible: root.canSubscribePage
          text: root.pageSubscribed ? "Subscribed" : "Subscribe"
          tooltipText: Model.withKey(root.pageSubscribed ? "Unsubscribe" : "Subscribe", "save")
          foreground: root.pageSubscribed ? Color.accent : root.foreground
          fontSize: Style.font.caption
          onClicked: root.toggleSave()
        }

        // S13: the editor (e; the page menu's Edit playlist).
        Button {
          visible: root.editable
          iconText: "󰏫"
          text: "Edit"
          tooltipText: Model.withKey("Title, description, privacy, order, voting, collaborators", "editPlaylist")
          foreground: root.foreground
          fontSize: Style.font.caption
          onClicked: root.editRequested()
        }

        Button {
          readonly property bool confirming: !!root.page && gate.armed("delete:" + root.page.id)
          visible: root.editable
          text: confirming ? "Really delete?" : "Delete"
          tooltipText: "Delete this playlist from your account"
          foreground: confirming ? Color.urgent : root.foreground
          fontSize: Style.font.caption
          onClicked: root.deletePlaylist()
        }

        Button {
          visible: root.hasAbout
          text: "About"
          tooltipText: "Description and links (" + Model.keyHint("pageMenu") + " has every action)"
          foreground: root.foreground
          fontSize: Style.font.caption
          onClicked: root.openAbout()
        }
      }
    }
  }

  // f: the filter over the page's songs (S3). Esc clears it and leaves;
  // Down or Enter goes to the first match.
  Row {
    id: findRow
    anchors.top: header.bottom
    anchors.topMargin: visible ? Style.space(8) : 0
    width: parent.width
    height: visible ? implicitHeight : 0
    spacing: Style.space(6)
    visible: root.findOpen

    TextField {
      id: findField
      width: parent.width - stopFind.width - parent.spacing
      placeholderText: root.isTaste ? "Find artists" : "Find songs by title, artist or album"
      foreground: root.foreground
      font.family: root.fontFamily

      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Escape) {
          root.closeFind()
          event.accepted = true
        } else if (event.key === Qt.Key_Down || event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
          root.leftField()
          if (root.isTaste) root._toFirstTasteArtist()  // S14
          else resultList.toTop()
          event.accepted = true
        }
      }
    }

    PanelActionButton {
      id: stopFind
      anchors.verticalCenter: parent.verticalCenter
      iconText: "󰅖"
      tooltipText: "Stop finding (Esc)"
      foreground: root.foreground
      onClicked: root.closeFind()
    }
  }

  // The page's sort orders as served (O cycles them).
  Flow {
    id: orderRow
    anchors.top: findRow.bottom
    anchors.topMargin: visible ? Style.space(8) : 0
    width: parent.width
    height: visible ? implicitHeight : 0
    spacing: Style.space(4)
    visible: root.orders.length > 1

    Repeater {
      model: root.orders

      Button {
        required property var modelData
        text: String(modelData.title || "")
        selected: modelData.index === root.currentOrder
        tooltipText: Model.withKey("Sort order", "order")
        foreground: root.foreground
        fontSize: Style.font.caption
        onClicked: root.setOrder(modelData.index)
      }
    }
  }

  Text {
    id: statusText
    anchors.top: orderRow.bottom
    anchors.topMargin: Style.space(10)
    width: parent.width
    visible: text !== ""
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    text: root.loading && !root.page ? "Loading…" : root.error !== "" ? root.error
      : root.page && root.rows.length === 0 ? "Nothing here" : ""
    color: root.error !== "" ? Color.urgent : Qt.darker(root.foreground, 1.5)
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  ResultList {
    id: resultList
    anchors.top: statusText.visible ? statusText.bottom : orderRow.bottom
    anchors.topMargin: Style.space(10)
    anchors.bottom: parent.bottom
    width: parent.width
    bar: root.bar
    store: root.store
    rows: root.rows
    owner: root
    // Not refKey: Load all reloads the same list (S3).
    listKey: root.listId
    removable: root.editable || root.isLiked
    removeTooltip: root.isLiked ? "Remove from Liked Music" : "Remove from playlist"
    // S13: your playlist's songs move (J / K's twins), not while finding.
    movable: root.editable && root.findText.trim() === ""
    numbered: !!root.ref && root.ref.kind === "album"
    // G stops on Load all: the page's shelves come after it.
    endIndex: Model.loadAllIndex(root.rows)
    moreBusy: root.loadingMore || root._moreWanted || root.loadingAll
    moreError: root.moreError
    // A new sort order is loading over the old one (Load all shows on its row).
    opacity: root.loading && root.page && !root.loadingAll ? 0.5 : 1

    onActivated: function(index) { root.activate(index) }
    onPlayNextRequested: function(index) { root.playNext(index) }
    onAddToQueueRequested: function(index) { root.addToQueue(index) }
    onRemoveRequested: function(index) { root.remove(index) }
    onMoveRequested: function(index, delta) { root.moveTrackAt(index, delta) }
    onRowsUpdated: root._applyPending()
    // After a failure only an explicit Load more tries again.
    onNearEnd: if (root.page && root.page.cursor && !root.loadingMore && !root.loading && root.moreError === "")
      root.loadMore(false)
  }
}
