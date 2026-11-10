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
// Explore (S6): Charts (its first row, O and the M menu choose the country,
// and the page then shows that one in its place), Moods & genres and a
// mood's playlists are sectioned pages too.
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
    : Model.collectionRows(page, { history: isHistory, find: findText, about: descriptionText.truncated, aboutOpen: aboutOpen })
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

  signal openRequested(var item)
  // The page itself went away (playlist deleted).
  signal closeRequested()
  // The find field gave the keys back (Esc, Down, Enter).
  signal leftField()
  // A row's menu, or the page's (M): Panel opens it.
  signal itemMenuRequested(var request)
  // S6: a chooser for Panel to open ({ title, subtitle, choices, onChoice }),
  // and this page shown another way (Charts in another country), which
  // takes the stack entry's place.
  signal chooserRequested(var request)
  signal replaceRequested(var item)

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
    return { order: order, full: full, aboutOpen: aboutOpen, list: resultList.viewState() }
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
    if (ref && ref.kind === "charts") return chooseCountry()  // S6: O picks the chart country
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
    // Collections (S3): the About row shows or hides the description.
    else if (item.id === Model.COLLECTION_ABOUT_ID) toggleAbout()
    // Explore (S6): Charts' Country row opens the country chooser.
    else if (item.id === Model.CHARTS_COUNTRY_ID) chooseCountry()
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
    // S3: an album opened from a link knows its artists from the page (Go to artist).
    if (ref && ref.kind === "album" && Model.linkedArtists(item).length === 0)
      item.artists = Model.collectionHeader(page).artists || []
    return item
  }

  // M: the header buttons' twins, then what the item offers (radio, share,
  // go to artist) and Delete on your own playlist.
  function pageMenu() {
    return { item: pageItem(), owner: root, index: -1, title: title, subtitle: subtitle,
             ctx: { page: true, pageKind: ref ? ref.kind : "", owned: editable, hasTracks: tracks.length > 0,
                    about: hasAbout, canLoadAll: canLoadAll && !loading, loadAllTitle: Model.loadAllTitle(page),
                    canFind: Model.canFind(page), aboutOpen: aboutOpen,
                    countryLabel: ref && ref.kind === "charts" ? Model.chartsCountryOf(page).label : "",  // S6
                    // The About row is there to show or hide.
                    description: Model.showsAbout(page, { find: findText, about: descriptionText.truncated }) } }
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
    if (!Model.canFind(page)) return false
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

  // ---- explore (S6) -----------------------------------------------------------------

  // Charts' country chooser (O, the Country row, the page menu): the
  // countries the page serves, the one shown ticked.
  function chooseCountry() {
    if (!ref || ref.kind !== "charts" || !page) return false
    var choices = Model.countryChoices(page)
    if (choices.length === 0) return false
    root.chooserRequested({ title: "Charts country", subtitle: Model.chartsCountryTitle(page), choices: choices,
                            onChoice: function(choice) { root.setCountry(choice.value) } })
    return true
  }

  // The pick is remembered (prefs.chartsCountry, where later Charts pages
  // open) and this page shows it in its place.
  function setCountry(code) {
    var country = Model.chartsCountry(code)
    if (service) service.setPref("chartsCountry", country)
    if (target && target.country !== country) root.replaceRequested(Model.chartsItem(country, target.title))
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
      placeholderText: "Find songs by title, artist or album"
      foreground: root.foreground
      font.family: root.fontFamily

      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Escape) {
          root.closeFind()
          event.accepted = true
        } else if (event.key === Qt.Key_Down || event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
          root.leftField()
          resultList.toTop()
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
    onRowsUpdated: root._applyPending()
    // After a failure only an explicit Load more tries again.
    onNearEnd: if (root.page && root.page.cursor && !root.loadingMore && !root.loading && root.moreError === "")
      root.loadMore(false)
  }
}
