import QtQuick
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// Any page opened from a row: album, playlist, Liked Music, History, and
// sectioned pages (the worker's `page` shape: an artist, a See all list).
// Header with Play, Shuffle and Queue all (an artist adds Radio and About),
// sort chips when the page has orders, then its rows. One instance shows
// every entry of the panel's stack in turn; `entry` is the stack wrapper
// { item, view }, and `view` is what viewState() saved when another page
// was pushed on top.
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
  readonly property bool editable: !!page && page.editable === true
  readonly property bool round: Model.pageRound(ref, page)
  readonly property var tracks: !page ? [] : isPage ? Model.pageTracks(page) : (page.tracks || [])
  readonly property var rows: !page ? [] : isPage ? Model.pageRows(page)
    : isHistory ? Model.historyRows(tracks) : Model.trackRows(tracks)
  // The page shows part of its list (Liked Music past 200 songs, a page with
  // more to load, an artist's top songs): Play, Shuffle and Queue all then
  // read the whole list (Service.playItem), at most 500 queued.
  readonly property bool incomplete: !!page && (page.truncated === true || Model.pagePartial(ref ? ref.kind : "")
    || (isPage && (!!page.cursor || page.hasMore === true)))
  readonly property var orders: page && Array.isArray(page.orders) ? page.orders : []
  readonly property var currentOrder: order !== null ? order : (page && page.order !== undefined ? page.order : null)
  readonly property alias results: resultList

  readonly property string title: (page && (page.title || page.name)) || (target && (target.title || target.name))
    || Model.pageKindTitle(ref)
  readonly property string subtitle: !page ? Model.itemSubtitle(target)
    : isPage ? Model.pageSubtitle(ref, page)
    : String(page.subtitle || "") + (page.truncated ? " • showing " + tracks.length : "")
  // Artist (S1): YouTube's shuffle and radio mixes, and the About page.
  readonly property var shuffleItem: isArtist ? Model.artistShuffleItem(page) : null
  readonly property var radioItem: isArtist ? Model.artistRadioItem(page) : null
  readonly property bool hasAbout: isArtist && Model.hasAbout(page)

  signal openRequested(var item)
  // The page itself went away (playlist deleted).
  signal closeRequested()
  // A row's menu, or the page's (M): Panel opens it.
  signal itemMenuRequested(var request)

  ConfirmGate {
    id: gate
  }

  // ---- opening and loading ----------------------------------------------------------

  function open() {
    if (entry === _opened) return
    _opened = entry
    var view = entry ? entry.view : null
    order = view && view.order !== undefined ? view.order : null
    ref = entry ? Model.pageRef(entry.item, order !== null ? { order: order } : null) : null
    page = null
    error = ""
    loading = false
    loadingMore = false
    moreError = ""
    _moreWanted = false
    gate.reset()
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

  // For Back: the sort order and the list's place.
  function viewState() {
    return { order: order, list: resultList.viewState() }
  }

  function restore(view) {
    if (!view) return
    if (view.order !== undefined && view.order !== order && entry) {
      order = view.order
      ref = Model.pageRef(entry.item, order !== null ? { order: order } : null)
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
    ref = Model.pageRef(entry.item, { order: index })
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
    if (!row || row.item.type !== "track" || !service) return
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
    // About (S1): a description link row copies its URL.
    else if (item.url && service) service.copyLink({ type: "link", url: item.url })
  }

  function activate(index) {
    var row = resultList.rowAt(index)
    if (!row || !service) return
    var item = row.item
    if (item.type === "more") root.openRequested(Model.moreTarget(item))
    else if (item.type === "loadMore") loadMore(true)
    else if (item.type === "action") runAction(item)
    else if (item.type === "message" || item.type === "text") return
    else if (row.trackIndex !== undefined)
      service.playCollection(isPage ? Model.sectionTracks(page, row.sectionIndex) : tracks, row.trackIndex, false)
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
    var removable = row.item.type === "track" && (editable || isLiked)
    root.itemMenuRequested({ item: row.item, owner: root, index: index,
      ctx: { pageKind: ref ? ref.kind : "", owned: editable, removable: removable,
             removeTitle: isLiked ? "Remove from Liked Music" : "Remove from playlist" } })
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
    return item
  }

  // M: the header buttons' twins, then what the item offers (radio, share,
  // go to artist) and Delete on your own playlist.
  function pageMenu() {
    return { item: pageItem(), owner: root, index: -1, title: title, subtitle: subtitle,
             ctx: { page: true, pageKind: ref ? ref.kind : "", owned: editable, hasTracks: tracks.length > 0,
                    about: hasAbout } }
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

      Flow {
        width: parent.width
        spacing: Style.space(4)
        visible: root.tracks.length > 0 || root.isArtist

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

  // The page's sort orders as served (O cycles them).
  Flow {
    id: orderRow
    anchors.top: header.bottom
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
    listKey: root.refKey
    removable: root.editable || root.isLiked
    removeTooltip: root.isLiked ? "Remove from Liked Music" : "Remove from playlist"
    moreBusy: root.loadingMore || root._moreWanted
    moreError: root.moreError
    // A new sort order is loading over the old one.
    opacity: root.loading && root.page ? 0.5 : 1

    onActivated: function(index) { root.activate(index) }
    onPlayNextRequested: function(index) { root.playNext(index) }
    onAddToQueueRequested: function(index) { root.addToQueue(index) }
    onRemoveRequested: function(index) { root.remove(index) }
    onRowsUpdated: root._applyPending()
    // After a failure only an explicit Load more tries again.
    onNearEnd: if (root.page && root.page.cursor && !root.loadingMore && !root.loading && root.moreError === "")
      root.loadMore(false)
  }
}
