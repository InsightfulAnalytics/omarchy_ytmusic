import QtQuick
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// Your account and "new playlist", then the library's chips (S2):
// Playlists (Liked Music, History, Recent activity, your and saved
// playlists), Songs (Shuffle all, then the songs), Albums and Artists
// (Subscriptions, then the artists). h / l switch chips (kept in
// prefs.libraryChip); O cycles the chip's sort order (prefs.libraryOrders),
// named in its first heading. A chip's list loads only once it is shown,
// about 50 at a time: Load more, or the cursor nearing the end.
//
// New playlist (S13): a privacy chip (a click, or Alt+Left / Alt+Right in
// the fields) and an optional description under the name; a pasted
// playlist invite link joins that playlist instead.
Item {
  id: root

  property var service: null
  property QtObject bar: null

  readonly property var store: service ? service.store : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool signedIn: !!service && service.signedIn
  // S16: the identity in use (a brand account's own name and photo).
  readonly property var account: service ? service.activeAccount : null
  readonly property string chip: Model.libraryChip(service && service.prefs ? service.prefs.libraryChip : "")
  readonly property bool isPlaylists: chip === "playlists"
  // The chip's list: libraryList's shape (Playlists: its orders and order).
  readonly property var list: !store ? null
    : isPlaylists ? { orders: store.playlistOrders,
                      current: store.playlistsRequested !== null ? store.playlistsRequested : store.playlistOrder }
    : (store.libraryLists[chip] || null)
  readonly property var orders: list && list.orders ? list.orders : []
  readonly property string orderLabel: Model.orderTitle(orders, list ? list.current : null)
  readonly property bool listLoading: !store ? false : isPlaylists ? store.libraryLoading : !!list && list.loading === true
  readonly property string listError: !store ? "" : isPlaylists ? store.libraryError : list && list.error ? list.error : ""
  readonly property int itemCount: !store ? 0 : isPlaylists ? store.playlists.length : list && list.items ? list.items.length : 0
  // Playlists in a new order are loading over the old one (the other chips
  // start a new order empty).
  readonly property bool reordering: isPlaylists && !!store && store.libraryLoading && store.playlistsRequested !== null
    && !Model.sameOrder(store.playlistsRequested, store.playlistOrder)
  readonly property var rows: store ? Model.libraryRows(chip, list, store.playlists) : []
  readonly property alias results: resultList
  readonly property bool inputFocused: nameField.activeFocus || descriptionField.activeFocus
  // S13: the new playlist's privacy, and whether the name field holds an invite link.
  property string newPrivacy: "PRIVATE"
  readonly property bool joining: !!Model.parseInviteUrl(nameField.text)
  // S15: plays go to YouTube Music history (the account row's button and
  // the M menu turn it on and off).
  readonly property bool recordHistory: !!service && service.recordHistory === true

  signal openRequested(var item)
  signal leftField()
  // A row's or the account's menu: Panel opens it.
  signal itemMenuRequested(var request)

  // The shown chip's list (a list held less than 5 min is reused).
  function refresh(force) {
    if (signedIn && store) store.loadLibraryList(chip, force)
  }

  // h / l: the chip before or after.
  function horizontal(dx) {
    if (signedIn) setChip(Model.nextLibraryChip(chip, dx))
  }

  function setChip(key) {
    if (!service || key === chip) return
    service.setPref("libraryChip", key)
  }

  // O: the chip's next sort order (saved per chip; YouTube saves it too).
  function cycleOrder() {
    if (!signedIn || !store) return false
    var next = Model.nextOrder(orders, list ? list.current : null)
    if (next === null) return false
    store.setLibraryOrder(chip, next)
    return true
  }

  // Enter plays a song (the loaded songs from there), Shuffle all plays
  // YouTube's shuffle of the whole library, Load more loads the next page;
  // anything else opens as a page.
  function activate(index) {
    var row = resultList.rowAt(index)
    if (!row || !service) return
    var item = row.item
    if (item.type === "loadMore") {
      if (store) store.loadLibraryMore(chip, true)
    } else if (item.type === "action") {
      if (item.id === Model.LIBRARY_SHUFFLE && Model.libraryShuffleItem(list))
        service.playItem(Model.libraryShuffleItem(list), "now")
    } else if (item.type === "message") {
      return
    } else if (row.trackIndex !== undefined) {
      service.playCollection(Model.libraryTracks(list), row.trackIndex, false,
                             { playlistId: null, title: Model.LIBRARY_SONGS_TITLE, radio: false })
    } else {
      root.openRequested(item)
    }
  }

  // n / a: Liked Music or a whole playlist (Service.playItem).
  function playNext(index) {
    var row = resultList.rowAt(index)
    if (row && Model.canQueue(row.item) && service) service.playItem(row.item, "next")
  }

  function addToQueue(index) {
    var row = resultList.rowAt(index)
    if (row && Model.canQueue(row.item) && service) service.playItem(row.item, "queue")
  }

  function menu(index) {
    var row = resultList.rowAt(index)
    if (!row || Model.menuKind(row.item) === "") return
    root.itemMenuRequested({ item: row.item, ctx: { pageKind: "library", owned: row.item.owned === true },
                             owner: root, index: index })
  }

  // M: the account row's menu (S14: Add artists you like; S16: Switch
  // account, Audio quality, Content location; S15: Record plays in history;
  // Sign out; a later slice adds Your channel).
  function pageMenu() {
    return { item: null, ctx: { page: true, pageKind: "library", recordHistory: root.recordHistory,
                                quality: service ? service.quality : "", location: service ? service.location : "",  // S16
                                canUseOwnAccount: !!service && service.canUseOwnAccount },  // S16
             owner: root, index: -1,
             title: root.signedIn ? (root.account && root.account.name ? root.account.name : "Signed in") : "Not signed in",
             subtitle: root.signedIn ? Model.accountCaption(root.account) : "" }
  }

  // ---- library actions (S4) ---------------------------------------------------------
  // The callback of an edit made from a chip's rows (Model.editCallbackOf):
  // when its outcome is unknown or a song's token was stale, the chip it
  // was made on is read again from YouTube, if it is still the one shown
  // (true then; Service then leaves that list alone). The view may be gone
  // when the reply comes: nothing then.
  function editCallback() {
    var requested = chip
    return function(reply) { return !!root && root.reloadAfterEdit(reply, requested) }
  }

  function reloadAfterEdit(reply, requested) {
    if (!service || !store || !visible || !signedIn || chip !== requested) return false
    if (!(service.unsure(reply) || Model.staleToken(reply))) return false
    store.loadLibraryList(requested, true)
    return true
  }

  // S13: with the privacy chip's privacy and the description; an invite
  // link joins its playlist.
  function createPlaylist() {
    var name = nameField.text.trim()
    if (name === "" || !service) return
    var done = function(reply) {
      if (!reply.ok) return
      nameField.text = ""
      descriptionField.text = ""
    }
    if (joining) service.joinPlaylist(name, done)
    else service.createPlaylist(name, [], done, { privacy: newPrivacy, description: descriptionField.text.trim() })
  }

  function cyclePrivacy(dx) {
    newPrivacy = Model.nextOption(Model.PRIVACY_OPTIONS, newPrivacy, dx)
  }

  // Keys both fields share: Enter creates (or joins), Alt+Left / Alt+Right
  // cycle the privacy, Esc and Down give the keys back to the list.
  function fieldKey(event) {
    if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
      root.createPlaylist()
    } else if ((event.modifiers & Qt.AltModifier) && (event.key === Qt.Key_Left || event.key === Qt.Key_Right)) {
      root.cyclePrivacy(event.key === Qt.Key_Left ? -1 : 1)
    } else if (event.key === Qt.Key_Escape || event.key === Qt.Key_Down) {
      root.leftField()
      if (event.key === Qt.Key_Down) resultList.moveCursor(1)
    } else {
      return false
    }
    event.accepted = true
    return true
  }

  // The list shown is kept from the Store's sweep; a list stays only while
  // it is shown or seen in the last 10 minutes.
  function markShown() {
    if (store) store.libraryShown = visible && signedIn ? chip : ""
  }

  // Once the bindings settle: new prefs change the chip and the Store's
  // orders together.
  function refreshShown() {
    markShown()
    if (visible) refresh(false)
  }

  onVisibleChanged: Qt.callLater(refreshShown)
  onSignedInChanged: Qt.callLater(refreshShown)
  // S16: another identity or location emptied the Store: the chip shown reads again.
  Connections {
    target: root.service
    function onAccountEpochChanged() { Qt.callLater(root.refreshShown) }
  }
  onChipChanged: Qt.callLater(refreshShown)
  onStoreChanged: Qt.callLater(refreshShown)
  Component.onDestruction: if (root.store && root.store.libraryShown === root.chip) root.store.libraryShown = ""

  SignInCard {
    width: parent.width
    visible: !root.signedIn
    service: root.service
    bar: root.bar
    expired: !!root.service && (root.service.authState === "expired" || root.service.authState === "invalid")
    canUseOwnAccount: !!root.service && root.service.canUseOwnAccount  // S16
  }

  Item {
    id: content
    anchors.fill: parent
    visible: root.signedIn

    Row {
      id: accountRow
      width: parent.width
      spacing: Style.space(10)

      CoverArt {
        id: photo
        anchors.verticalCenter: parent.verticalCenter
        size: Style.space(34)
        radius: size / 2
        store: root.store
        source: root.account && root.account.photo ? root.account.photo : ""
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      Column {
        id: nameColumn
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width - photo.width - recordPlays.width - signOut.width - parent.spacing * 3
        spacing: Style.space(1)

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: root.account && root.account.name ? root.account.name : "Signed in"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: true
          elide: Text.ElideRight
        }

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: Model.accountCaption(root.account)  // S16: the handle, and Brand account
          visible: text !== ""
          color: Qt.darker(root.foreground, 1.4)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      // S15: whether plays go to YouTube Music history, in sight; a click
      // turns it on or off.
      Button {
        id: recordPlays
        anchors.verticalCenter: parent.verticalCenter
        iconText: "󰋚"
        text: root.recordHistory ? "Recording plays" : "Record plays"
        selected: root.recordHistory
        tooltipText: (root.recordHistory
          ? "Songs played for 10 s or more go to your YouTube Music history; click to stop"
          : "Add songs played for 10 s or more to your YouTube Music history, as the web does")
          + " (also in the " + Model.keyHint("pageMenu") + " menu)"
        foreground: root.foreground
        fontSize: Style.font.caption
        onClicked: root.service.setRecordHistory(!root.recordHistory)
      }

      Button {
        id: signOut
        anchors.verticalCenter: parent.verticalCenter
        text: "Sign out"
        tooltipText: "Delete the saved session from this computer (also in the " + Model.keyHint("pageMenu") + " menu)"
        foreground: root.foreground
        fontSize: Style.font.caption
        onClicked: root.service.signOut()
      }
    }

    // S16: a click on the account (its photo or name) opens its menu, M's twin.
    MouseArea {
      x: accountRow.x
      y: accountRow.y
      width: nameColumn.x + nameColumn.width
      height: accountRow.height
      cursorShape: Qt.PointingHandCursor
      onClicked: root.itemMenuRequested(root.pageMenu())
    }

    Row {
      id: createRow
      anchors.top: accountRow.bottom
      anchors.topMargin: Style.space(12)
      width: parent.width
      spacing: Style.space(6)

      TextField {
        id: nameField
        width: parent.width - privacyChip.width - createButton.width - parent.spacing * (privacyChip.visible ? 2 : 1)
        placeholderText: "New playlist name (or an invite link)"
        foreground: root.foreground
        font.family: root.fontFamily

        Keys.onPressed: function(event) {
          if (root.fieldKey(event)) return
          if (event.key === Qt.Key_Tab && descriptionRow.visible) {
            descriptionField.forceActiveFocus()
            event.accepted = true
          }
        }
      }

      // S13: the new playlist's privacy (one cycling chip).
      Button {
        id: privacyChip
        anchors.verticalCenter: parent.verticalCenter
        visible: !root.joining
        width: visible ? implicitWidth : 0
        iconText: Model.optionIcon(Model.PRIVACY_OPTIONS, root.newPrivacy)
        text: Model.optionTitle(Model.PRIVACY_OPTIONS, root.newPrivacy)
        tooltipText: "The new playlist's privacy (Alt+Left / Alt+Right while typing)"
        foreground: root.foreground
        fontSize: Style.font.caption
        onClicked: root.cyclePrivacy(1)
      }

      Button {
        id: createButton
        anchors.verticalCenter: parent.verticalCenter
        iconText: root.joining ? "󰀐" : "󰐕"
        text: root.joining ? "Join" : "Create"
        tooltipText: root.joining ? "Join this collaborative playlist" : "Create a " + root.newPrivacy.toLowerCase() + " playlist"
        enabled: nameField.text.trim() !== ""
        opacity: enabled ? 1 : 0.4
        foreground: root.foreground
        fontSize: Style.font.caption
        onClicked: root.createPlaylist()
      }
    }

    // S13: an optional description, once a name is typed.
    Row {
      id: descriptionRow
      anchors.top: createRow.bottom
      anchors.topMargin: visible ? Style.space(6) : 0
      width: parent.width
      height: visible ? implicitHeight : 0
      visible: !root.joining && (nameField.text.trim() !== "" || descriptionField.text !== "" || descriptionField.activeFocus)

      TextField {
        id: descriptionField
        width: parent.width
        placeholderText: "Description (optional)"
        foreground: root.foreground
        font.family: root.fontFamily

        Keys.onPressed: function(event) {
          if (root.fieldKey(event)) return
          if (event.key === Qt.Key_Backtab || (event.key === Qt.Key_Tab && (event.modifiers & Qt.ShiftModifier))) {
            nameField.forceActiveFocus()
            event.accepted = true
          }
        }
      }
    }

    // The chips (h / l), and the chip's sort order (O) when it has some.
    Flow {
      id: chipRow
      anchors.top: descriptionRow.bottom
      anchors.topMargin: Style.space(8)
      width: parent.width
      spacing: Style.space(4)

      Repeater {
        model: Model.LIBRARY_CHIPS

        Button {
          required property var modelData
          text: modelData.title
          selected: root.chip === modelData.key
          tooltipText: Model.withKey(modelData.title, "horizontal")
          foreground: root.foreground
          fontSize: Style.font.caption
          onClicked: root.setChip(modelData.key)
        }
      }

      Button {
        visible: root.orders.length > 1
        iconText: "󰒺"
        text: root.orderLabel !== "" ? root.orderLabel : "Sort"
        tooltipText: Model.withKey("Next sort order", "order")
        foreground: root.foreground
        fontSize: Style.font.caption
        onClicked: root.cycleOrder()
      }
    }

    Text {
      id: statusText
      anchors.top: chipRow.bottom
      anchors.topMargin: Style.space(8)
      width: parent.width
      visible: text !== ""
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      text: !root.store ? "" : root.listLoading && root.itemCount === 0 ? "Loading your library…"
        : root.listError !== "" ? root.listError
        : !root.isPlaylists && !!root.list && !root.listLoading && root.itemCount === 0 && !root.list.message
          && root.list.at > 0 ? "Nothing here yet" : ""
      color: root.listError !== "" ? Color.urgent : Qt.darker(root.foreground, 1.5)
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    ResultList {
      id: resultList
      anchors.top: statusText.visible ? statusText.bottom : chipRow.bottom
      anchors.topMargin: Style.space(8)
      anchors.bottom: parent.bottom
      width: parent.width
      bar: root.bar
      store: root.store
      rows: root.rows
      owner: root
      // Another chip or order starts a fresh list; a refresh or Load more keeps the place.
      listKey: root.chip + "|" + Model.canonicalJson(root.store ? root.store.requestedOrder(root.chip) : null)
      moreBusy: !!root.list && (root.list.loadingMore === true || root.list.moreWanted === true)
      moreError: root.list && root.list.moreError ? root.list.moreError : ""
      opacity: root.reordering ? 0.5 : 1

      onActivated: function(index) { root.activate(index) }
      onPlayNextRequested: function(index) { root.playNext(index) }
      onAddToQueueRequested: function(index) { root.addToQueue(index) }
      // After a failure only an explicit Load more tries again.
      onNearEnd: if (root.store && root.list && root.list.cursor && !root.list.loadingMore && !root.list.loading
                     && !root.list.moreError) root.store.loadLibraryMore(root.chip, false)
    }
  }
}
