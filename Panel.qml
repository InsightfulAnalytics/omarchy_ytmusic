import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "components"
import "YtmModel.js" as Model

// The YouTube Music dropdown. BarWidget.qml loads this and injects the bar,
// the anchor button, the widget itself (bar identity) and the service.
//
// Tabs: Now (playing, then the queue, lyrics or related), Search, Library and
// Home, with a page stack on top for albums, playlists, Liked Music, History,
// artists, credits and See all pages, the item menu (row menu, chooser, key
// help) and the playlist picker.
// Keys (Model.KEYS; ? lists them): 1-4 switch tab: Now, Search, Library, Home
// · / search · j / k / ↑ / ↓ move · g first row · G last row (loads more) ·
// Enter play or open · h / l / ← / → seek 10 s on Now, or the page's chips ·
// Esc leave the field → close the picker or menu → back → close · Space
// play/pause · n play next (a song, album or playlist) · a add to queue · R
// start radio from the row or the current song · s shuffle · r repeat · L
// like · p add to playlist · A go to artist (of the row or the current song)
// · o go to album · J / K move a queued song down / up · x remove (queue, own
// playlist, Liked Music, recent search) · m / . the row's menu (or
// right-click) · M the page's menu (Library: account) · O next sort order on
// a page that has one · f find, on pages that offer it · ? these keys · y
// cycle the pane under the song: Queue, Lyrics, Related · t cycle the queue
// tuner chip: All, Discover, Popular and the rest.
// Text keys go through Model.keyId to runKey(); Ctrl and Alt chords are
// ignored. Views offer optional hooks, checked with typeof: horizontal(dx),
// cycleOrder(), find(), playNext(i) / addToQueue(i), menu(i), pageMenu(),
// menuChosen(id) and placeAt(i) / indexOfPlace(place).
Panel {
  id: root
  moduleName: "damir.ytmusic"
  ipcTarget: "damir.ytmusic"
  manageIpc: false

  property var anchorItem: null
  property var service: null

  // The bar tracks the widget mounted in its slot, not this nested panel, so
  // the popout coordinator must compare against that widget.
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  readonly property var store: service ? service.store : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool sessionProblem: !!service && (service.authState === "expired" || service.authState === "invalid")

  property string tab: "now"
  // Opened pages, innermost last, as wrappers { item, view }: the item that
  // was opened (never changed) and, once another page is pushed on top, the
  // place it was left at (CollectionView.viewState()), restored on Back.
  property var stack: []
  readonly property var topEntry: stack.length > 0 ? stack[stack.length - 1] : null
  readonly property var page: topEntry ? topEntry.item : null
  property bool returnPressed: false
  // Now (S11): the pane under the song (prefs.nowPane), and whether the
  // queue is what the keys move in.
  readonly property string nowPane: service && service.prefs ? Model.nowPane(service.prefs.nowPane) : "queue"
  readonly property bool queueShown: tab === "now" && !page && nowPane === "queue"

  readonly property var tabs: [
    { key: "now", title: "Now" },
    { key: "search", title: "Search" },
    { key: "library", title: "Library" },
    { key: "home", title: "Home" }
  ]

  function setTab(key) {
    picker.close()
    itemMenu.close()
    tab = key
    stack = []
    if (key === "search") Qt.callLater(focusSearch)
    else Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function focusSearch() {
    searchView.field.forceActiveFocus()
    searchView.field.selectAll()
  }

  function push(item) {
    if (!item) return
    picker.close()
    itemMenu.close()
    var next = stack.slice()
    var shown = collectionLoader.item
    if (next.length > 0 && shown) next[next.length - 1] = { item: next[next.length - 1].item, view: shown.viewState() }
    next.push({ item: item, view: null })
    stack = next
    keyCatcher.forceActiveFocus()
  }

  // The page underneath comes back with its saved view (CollectionView
  // restores it from the wrapper). The overlays close first: the header's
  // Back button stays clickable over them, and a menu's rows belong to the
  // page that goes.
  function back() {
    if (stack.length === 0) return false
    picker.close()
    itemMenu.close()
    stack = stack.slice(0, -1)
    keyCatcher.forceActiveFocus()
    return true
  }

  // (Not "escape": QML rejects methods named after JS globals.)
  function handleEscape() {
    if (picker.open) picker.close()
    else if (itemMenu.open) itemMenu.close()
    else if (!back()) close()
  }

  // The view whose list the keyboard cursor currently drives.
  function activeListOwner() {
    if (picker.open) return picker
    if (itemMenu.open) return itemMenu
    if (page) return collectionLoader.item
    if (tab === "now" && nowPane === "related") return relatedView
    if (tab === "search") return searchView
    if (tab === "library") return libraryView
    if (tab === "home") return homeView
    return null
  }

  function activeList() {
    var owner = activeListOwner()
    return owner ? owner.results : null
  }

  // Optional hooks of the active owner (horizontal, cycleOrder, find): a
  // view offers one by defining the function; otherwise the key does nothing.
  function ownerHook(name, arg) {
    var owner = activeListOwner()
    if (!owner || typeof owner[name] !== "function") return false
    owner[name](arg)
    return true
  }

  // Views and overlays with a text field, each listed once. While one
  // reports inputFocused, keys go to its field instead of the shortcuts.
  function inputOwners() {
    return [libraryView, picker, itemMenu, collectionLoader.item]
  }

  function anyInputFocused() {
    var owners = inputOwners()
    for (var i = 0; i < owners.length; i++) {
      if (owners[i] && owners[i].inputFocused === true) return true
    }
    return false
  }

  // g / G: the first or last row (G also lets the list load more).
  function jumpCursor(toEnd) {
    var list = activeList()
    if (list) {
      if (toEnd) list.toBottom()
      else list.toTop()
    } else if (tab === "now" && nowPane === "lyrics") {
      lyricsView.jump(toEnd)
    } else if (queueShown && service && service.queue.length > 0) {
      queueView.cursorIndex = toEnd ? service.queue.length - 1 : 0
      queueView.ensureVisible(queueView.cursorIndex)
    }
  }

  function moveCursor(delta) {
    var list = activeList()
    if (list) list.moveCursor(delta)
    else if (tab === "now" && nowPane === "lyrics") lyricsView.scroll(delta)
    else if (queueShown) queueView.moveCursor(delta)
  }

  function activateCursor() {
    var owner = activeListOwner()
    var list = activeList()
    if (owner && list) owner.activate(list.cursorIndex)
    else if (queueShown) queueView.activateCursor()
  }

  function listAction(action) {
    var owner = activeListOwner()
    var list = activeList()
    if (!owner || !list || list.cursorIndex < 0) return
    if (action === "next") ownerHook("playNext", list.cursorIndex)
    else if (action === "queue") ownerHook("addToQueue", list.cursorIndex)
  }

  // The track the keyboard is pointing at: a selected row, else what plays.
  function cursorTrack() {
    var list = activeList()
    var row = list ? list.rowAt(list.cursorIndex) : null
    if (row && row.item.type === "track") return row.item
    if (!list && queueShown && queueView.cursorIndex >= 0 && service) {
      var entry = service.queue[queueView.cursorIndex]
      if (entry) return entry.track
    }
    return service ? service.currentTrack : null
  }

  // The item the keyboard is pointing at: a selected row of any kind, else
  // the queue entry under the cursor on Now; null when neither.
  function cursorItem() {
    var list = activeList()
    var row = list ? list.rowAt(list.cursorIndex) : null
    if (row) return row.item
    if (!list && queueShown && queueView.cursorIndex >= 0 && service) {
      var entry = service.queue[queueView.cursorIndex]
      if (entry) return entry.track
    }
    return null
  }

  // The artist / album page of a track (from Now Playing, or `A` / `o` on
  // the selected row or the current track).
  function openArtist(artist) {
    if (artist && artist.id) push({ type: "artist", channelId: artist.id, name: artist.name, thumb: null })
  }

  function openArtistOf(track) {
    var artists = Model.linkedArtists(track)
    if (artists.length > 0) openArtist(artists[0])
  }

  // Go to artist from a menu: one with a page opens, several (a duet, a
  // feature) give a chooser.
  function goArtist(item) {
    var artists = Model.linkedArtists(item)
    if (artists.length === 1) {
      openArtist(artists[0])
    } else if (artists.length > 1) {
      itemMenu.openChooser("Go to artist", Model.itemTitle(item), artists.map(function(artist) {
        return { id: artist.id, title: artist.name, icon: "󰠃", value: artist }
      }), function(choice) { root.openArtist(choice.value) })
      keyCatcher.forceActiveFocus()
    }
  }

  // Go to album: its page when the album id is known, else (an album known
  // only by its audio playlist) the worker looks the page up, cached.
  function goAlbum(item) {
    var album = Model.albumOf(item)
    if (album) {
      push(album)
      return
    }
    var playlistId = Model.albumPlaylistOf(item)
    if (playlistId === "" || !service) return
    service.albumBrowseIdOf(playlistId, function(reply) {
      if (!reply.ok) {
        root.service.failAction("Couldn't open the album", reply.error.message)
        return
      }
      if (!root.opened) return
      root.push({ type: "album", browseId: reply.result.browseId,
                  title: item.album && item.album.name ? item.album.name : Model.itemTitle(item),
                  thumb: item.thumb || null, thumbLarge: item.thumbLarge || null })
    })
  }

  // R: radio from the row (a song, album, playlist, artist or mix), else
  // from the song playing. It replaces the queue, as on the web.
  function startRadio() {
    if (!service) return
    var item = cursorItem()
    if (!Model.radioParams(item)) item = service.currentTrack
    if (item) service.startRadio(item)
  }

  // ---- the item menu ------------------------------------------------------------------
  // A view's menu(i) emits itemMenuRequested(request) and its pageMenu()
  // returns one: { item, ctx, owner, index, title?, subtitle? }; ctx is the
  // view's (pageKind, owned, removable, ...) and signedIn is added here.
  // Model.menuActions picks the entries and ItemMenu shows them; the chosen
  // one runs with this panel and the service, on the row the menu was
  // opened for, found again by its place (rows can change while the menu is
  // open: a refresh, a new like, the queue moving on).

  // What keeps the places of an owner's rows: QueueView itself, or a view's
  // ResultList.
  function rowPlaces(owner) {
    if (owner && typeof owner.placeAt === "function") return owner
    if (owner && owner.results && typeof owner.results.placeAt === "function") return owner.results
    return null
  }

  function openItemMenu(request) {
    if (!request || !service) return false
    var item = request.item || null
    var ctx = {}
    for (var key in request.ctx || {}) ctx[key] = request.ctx[key]
    if (ctx.signedIn === undefined) ctx.signedIn = service.signedIn
    var index = request.index >= 0 ? request.index : -1
    var places = rowPlaces(request.owner)
    var full = { item: item, ctx: ctx, owner: request.owner || null, index: index,
                 place: index >= 0 && places ? places.placeAt(index) : null }
    picker.close()
    var opened = itemMenu.openMenu(full, Model.menuActions(item, ctx),
      request.title || Model.itemTitle(item) || "Menu",
      request.subtitle !== undefined ? request.subtitle : Model.itemSubtitle(item))
    // A right-click while the search field had the keys.
    if (opened) keyCatcher.forceActiveFocus()
    return opened
  }

  // The row's index now: -1 when it is gone (Remove then does nothing, and
  // Play plays the song alone).
  function menuRowIndex(request) {
    var places = rowPlaces(request.owner)
    if (request.index < 0 || !request.place || !places) return request.index
    return places.indexOfPlace(request.place)
  }

  function runMenuAction(action, request) {
    if (!action || typeof action.run !== "function" || !request || !service) return
    var owner = request.owner
    // Search remembers a query that led somewhere.
    if (owner && typeof owner.menuChosen === "function") owner.menuChosen(action.id)
    action.run({ item: request.item, ctx: request.ctx, index: menuRowIndex(request), owner: owner,
                 panel: root, service: service })
  }

  // The song playing: its own menu (the cover's menu button), or the Now
  // tab's page menu (M), which adds the queue header's Autoplay and Clear.
  function nowMenu(pageMenu) {
    var track = service ? service.currentTrack : null
    var ctx = { pageKind: pageMenu ? "now" : "nowPlaying", isCurrent: !!track }
    if (pageMenu) {
      ctx.page = true
      ctx.autoplay = service.autoplay
      ctx.queueLength = service.queue.length
      // S12: the tuner chooser (Tune the queue).
      ctx.tunerChips = service.tunerChips.length
      ctx.tunerTitle = service.tunerTitle
    }
    ctx.counterpart = track ? service.counterpartOf(track) : null  // S12: song / video version
    return { item: track, ctx: ctx, owner: queueView, index: -1,
             title: track ? Model.itemTitle(track) : "Queue", subtitle: track ? Model.itemSubtitle(track) : "" }
  }

  // ---- up next (S12) -----------------------------------------------------------------
  // The Now page menu's Tune the queue: every chip of the song playing in a
  // chooser; the one picked replaces the songs autoplay added.
  function openTuner() {
    if (!service) return
    var chips = service.tunerChips
    if (chips.length === 0) return
    itemMenu.openChooser("Tune the queue", "Replaces the songs autoplay added; yours stay",
      Model.tunerChoices(chips, service.tuner), function(choice) { root.service.chooseTuner(choice.value) })
    keyCatcher.forceActiveFocus()
  }

  // m / . : the cursor row's menu; with no row, the page's.
  function openRowMenu() {
    var list = activeList()
    if (list) {
      if (list.cursorIndex >= 0 && ownerHook("menu", list.cursorIndex)) return
      openPageMenu()
    } else if (tab === "now" && !page) {
      if (queueShown && queueView.cursorIndex >= 0) queueView.menu(queueView.cursorIndex)
      else openItemMenu(nowMenu(false))
    }
  }

  // M: the page's own menu (on Library the account; on Now the queue).
  function openPageMenu() {
    if (!service) return
    if (!page && tab === "now") {
      openItemMenu(nowMenu(true))
      return
    }
    var owner = activeListOwner()
    if (owner && typeof owner.pageMenu === "function") openItemMenu(owner.pageMenu())
  }

  // ---- keys -----------------------------------------------------------------------------
  // What a key does (Model.KEYS ids); `text` tells the tabs and J / K apart.
  // While the item menu is open only tabs, g / G and ? work, and under the
  // playlist picker only tabs and g / G: other keys would act on the rows
  // underneath, or on the picker's own playlists (R), or open something the
  // picker hides (?, M). The help (Enter on a key's row) also passes Enter,
  // Esc and Space, which the catcher handles itself; x is left out there, so
  // browsing the help never removes anything.
  function runKey(id, text) {
    if (id === "") return
    if (picker.open && ["tab", "search", "top", "bottom"].indexOf(id) < 0) return
    if (itemMenu.open && ["tab", "search", "top", "bottom", "help"].indexOf(id) < 0) return
    if (id === "tab") {
      var chosen = tabs[Number(text) - 1]
      if (chosen) setTab(chosen.key)
    }
    else if (id === "search") setTab("search")
    else if (id === "playNext") listAction("next")
    else if (id === "addToQueue") listAction("queue")
    else if (id === "radio") startRadio()
    else if (id === "like") { if (service) service.toggleLike(cursorTrack()) }
    else if (id === "addToPlaylist") openPicker([cursorTrack()])
    else if (id === "artist") openArtistOf(cursorTrack())
    else if (id === "album") goAlbum(cursorTrack())
    else if (id === "shuffle") { if (service) service.toggleShuffle() }
    else if (id === "repeat") { if (service) service.cycleRepeat() }
    else if (id === "queueMove") { if (root.queueShown && (text === "J" || text === "K")) queueView.moveSelected(text === "J" ? 1 : -1) }
    else if (id === "nowPane") { if (root.tab === "now" && !root.page) queueView.cyclePane() }
    else if (id === "tune") { if (root.queueShown) queueView.cycleTuner() }
    else if (id === "top") jumpCursor(false)
    else if (id === "bottom") jumpCursor(true)
    else if (id === "order") ownerHook("cycleOrder")
    else if (id === "find") ownerHook("find")
    else if (id === "menu") openRowMenu()
    else if (id === "pageMenu") openPageMenu()
    else if (id === "help") { itemMenu.openHelp(); keyCatcher.forceActiveFocus() }
    else if (id === "open") activateCursor()
    else if (id === "back") handleEscape()
    else if (id === "playPause") { if (service) service.playPause() }
  }

  function removeCursor() {
    // x acts on the rows of a view, never on what an overlay covers.
    if (picker.open || itemMenu.open) return
    if (page && collectionLoader.item) {
      collectionLoader.item.remove(collectionLoader.item.results.cursorIndex)
    } else if (queueShown) {
      queueView.removeCursor()
    } else if (tab === "search" && !page) {
      searchView.remove(searchView.results.cursorIndex)
    }
  }

  function openPicker(tracks) {
    var list = (tracks || []).filter(function(t) { return t && t.videoId })
    if (list.length === 0 || !service || !service.signedIn) return
    picker.tracks = list
    keyCatcher.forceActiveFocus()
  }

  // Tab requests (IPC "search") can arrive before this panel exists, so they
  // are also picked up when it opens.
  property int handledTabRequest: 0

  function applyTabRequest() {
    if (!store || store.tabRequestSeq === handledTabRequest) return
    handledTabRequest = store.tabRequestSeq
    // Show the requested query in the field, not whatever was typed last.
    if (store.requestedTab === "search") searchView.setText(store.query)
    setTab(store.requestedTab)
  }

  onOpenedChanged: {
    if (!opened) {
      picker.close()
      itemMenu.close()
      return
    }
    queueView.cursorIndex = -1
    applyTabRequest()
    Qt.callLater(function() { queueView.ensureVisible(root.service ? root.service.playlistPos : -1) })
  }

  Connections {
    target: root.store
    function onTabRequestSeqChanged() { if (root.opened) root.applyTabRequest() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: root.tab === "search" && !root.page ? searchView.field : keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(440))
    contentHeight: panel.fittedContentHeight(Style.space(600))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: searchView.field.activeFocus || root.anyInputFocused()

      onCloseRequested: root.handleEscape()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        if (dy !== 0) root.moveCursor(dy)
        else if (dx !== 0 && root.tab === "now" && !root.page) { if (root.service) root.service.seekRelative(dx * 10) }
        else if (dx !== 0) root.ownerHook("horizontal", dx)
      }
      onReturnRequested: {
        root.returnPressed = true
        root.activateCursor()
      }
      onActivateRequested: {
        // Enter also emits activate; only Space means play/pause.
        if (root.returnPressed) {
          root.returnPressed = false
          return
        }
        if (root.service) root.service.playPause()
      }
      onDeleteRequested: root.removeCursor()
      onTextKey: function(text, modifiers) {
        if (Model.chordAllowed(modifiers)) root.runKey(Model.keyId(text), text)
      }

      Item {
        id: header
        width: parent.width
        height: tabRow.implicitHeight

        Row {
          id: tabRow
          spacing: Style.space(4)

          Button {
            visible: !!root.page
            iconText: "󰁍"
            tooltipText: Model.withKey("Back", "back")
            foreground: root.foreground
            onClicked: root.back()
          }

          Repeater {
            model: root.tabs

            Button {
              required property var modelData
              required property int index
              text: modelData.title
              tooltipText: modelData.title + " (" + (index + 1) + ")"
              selected: root.tab === modelData.key && !root.page
              foreground: root.foreground
              onClicked: root.setTab(modelData.key)
            }
          }
        }
      }

      // uv / deno / mpv-mpris missing (a fresh Omarchy lacks uv and deno).
      Rectangle {
        id: toolsBanner
        anchors.top: header.bottom
        anchors.topMargin: visible ? Style.space(8) : 0
        width: parent.width
        height: visible ? toolsRow.implicitHeight + Style.space(12) : 0
        visible: !!root.service && root.service.missingTools.length > 0
        radius: Style.spacing.labelGap
        color: Qt.rgba(Color.urgent.r, Color.urgent.g, Color.urgent.b, 0.15)

        Row {
          id: toolsRow
          anchors.verticalCenter: parent.verticalCenter
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.leftMargin: Style.space(10)
          anchors.rightMargin: Style.space(6)
          spacing: Style.space(8)

          Text {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - toolsButton.width - parent.spacing
            textFormat: Text.PlainText
            text: root.service ? "YouTube Music needs " + root.service.missingTools.join(", ")
              + " (" + root.service.installCommand + ")." : ""
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.Wrap
          }

          Button {
            id: toolsButton
            anchors.verticalCenter: parent.verticalCenter
            text: "Install"
            tooltipText: "Opens a terminal running " + (root.service ? root.service.installCommand : "")
            foreground: root.foreground
            fontSize: Style.font.caption
            onClicked: root.service.installTools()
          }
        }
      }

      Rectangle {
        id: banner
        anchors.top: toolsBanner.bottom
        anchors.topMargin: visible ? Style.space(8) : 0
        width: parent.width
        height: visible ? bannerRow.implicitHeight + Style.space(12) : 0
        visible: root.sessionProblem
        radius: Style.spacing.labelGap
        color: Qt.rgba(Color.urgent.r, Color.urgent.g, Color.urgent.b, 0.15)

        Row {
          id: bannerRow
          anchors.verticalCenter: parent.verticalCenter
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.leftMargin: Style.space(10)
          anchors.rightMargin: Style.space(6)
          spacing: Style.space(8)

          Text {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - bannerButton.width - parent.spacing
            textFormat: Text.PlainText
            text: "Your YouTube Music sign-in expired. Search and playback still work."
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.Wrap
          }

          Button {
            id: bannerButton
            anchors.verticalCenter: parent.verticalCenter
            text: "Sign in"
            foreground: root.foreground
            fontSize: Style.font.caption
            onClicked: root.service.signIn()
          }
        }
      }

      Item {
        id: content
        anchors.top: banner.bottom
        anchors.topMargin: Style.space(12)
        anchors.bottom: parent.bottom
        width: parent.width

        Column {
          id: nowTab
          anchors.fill: parent
          visible: root.tab === "now" && !root.page
          spacing: Style.space(14)

          NowPlayingView {
            id: nowPlaying
            width: parent.width
            service: root.service
            bar: root.bar
            onAddToPlaylistRequested: root.openPicker([root.service.currentTrack])
            onArtistRequested: function(artist) { root.openArtist(artist) }
            onAlbumRequested: function(track) { root.goAlbum(track) }
            onMenuRequested: root.openItemMenu(root.nowMenu(false))
          }

          PanelSeparator {
            id: separator
            width: parent.width
            foreground: root.foreground
          }

          QueueView {
            id: queueView
            width: parent.width
            height: nowTab.height - nowPlaying.height - separator.height - nowTab.spacing * 2
            service: root.service
            bar: root.bar
            pane: root.nowPane
            onItemMenuRequested: function(request) { root.openItemMenu(request) }

            // The other panes (S11), under the queue header's pane chip.
            LyricsView {
              id: lyricsView
              anchors.fill: parent
              visible: queueView.pane === "lyrics"
              service: root.service
              bar: root.bar
              active: root.opened && root.tab === "now" && !root.page && queueView.pane === "lyrics"
            }

            RelatedView {
              id: relatedView
              anchors.fill: parent
              visible: queueView.pane === "related"
              service: root.service
              bar: root.bar
              active: root.opened && root.tab === "now" && !root.page && queueView.pane === "related"
              onOpenRequested: function(item) { root.push(item) }
              onItemMenuRequested: function(request) { root.openItemMenu(request) }
            }
          }
        }

        SearchView {
          id: searchView
          anchors.fill: parent
          visible: root.tab === "search" && !root.page
          service: root.service
          bar: root.bar
          onOpenRequested: function(item) { root.push(item) }
          onItemMenuRequested: function(request) { root.openItemMenu(request) }
          onLeftField: keyCatcher.forceActiveFocus()
        }

        LibraryView {
          id: libraryView
          anchors.fill: parent
          visible: root.tab === "library" && !root.page
          service: root.service
          bar: root.bar
          onOpenRequested: function(item) { root.push(item) }
          onItemMenuRequested: function(request) { root.openItemMenu(request) }
          onLeftField: keyCatcher.forceActiveFocus()
        }

        HomeView {
          id: homeView
          anchors.fill: parent
          visible: root.tab === "home" && !root.page
          service: root.service
          bar: root.bar
          onOpenRequested: function(item) { root.push(item) }
          onItemMenuRequested: function(request) { root.openItemMenu(request) }
        }

        Loader {
          id: collectionLoader
          anchors.fill: parent
          active: !!root.page
          sourceComponent: CollectionView {
            service: root.service
            bar: root.bar
            entry: root.topEntry
            onOpenRequested: function(item) { root.push(item) }
            onItemMenuRequested: function(request) { root.openItemMenu(request) }
            onCloseRequested: root.back()
          }
        }

        // Under the picker: Add to playlist from the menu opens over it.
        ItemMenu {
          id: itemMenu
          anchors.fill: parent
          service: root.service
          bar: root.bar
          onClosed: keyCatcher.forceActiveFocus()
          onChosen: function(action, request) { root.runMenuAction(action, request) }
          onKeyChosen: function(keyId) { root.runKey(keyId, "") }
        }

        PlaylistPicker {
          id: picker
          anchors.fill: parent
          service: root.service
          bar: root.bar
          onClosed: keyCatcher.forceActiveFocus()
        }
      }
    }
  }
}
