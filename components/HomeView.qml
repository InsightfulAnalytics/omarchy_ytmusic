import QtQuick
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// YouTube Music's Home (personal when signed in): every shelf of the web's
// feed, a page of shelves at a time (the cursor nearing the end, G, or the
// Load more row reads the next). Songs play their shelf from that song;
// albums, playlists, mixes, artists and the rest open as pages. A shelf ends
// in See all when the web has one and in Play all when it plays as a list
// (the list YouTube generates for it). The mood chips above (All first)
// switch with h / l or a click and reload Home for that mood.
Item {
  id: root

  property var service: null
  property QtObject bar: null

  readonly property var store: service ? service.store : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool signedIn: !!service && service.signedIn
  // The chosen chip's feed (Store's shape), and the chips (All first).
  readonly property var feed: store ? store.homeFeed : null
  readonly property string chip: store ? store.homeChip : ""
  readonly property var chips: store ? Model.homeChipList(store.homeChips) : [Model.HOME_ALL_CHIP]
  readonly property bool hasMore: !!feed && feed.capped !== true && (!!feed.cursor || feed.hasMore === true)
  // The rows follow the feed object, so a chip's feed held from before (its
  // seenAt patch) still rebuilds them once listKey names that chip.
  readonly property var rows: store ? Model.homeRows(feed ? feed.shelves : [], { more: hasMore }) : []
  readonly property alias results: resultList

  signal openRequested(var item)
  // A row's menu, or the page's (Refresh): Panel opens it.
  signal itemMenuRequested(var request)

  function refresh(force) {
    if (store) store.loadHome(force)
  }

  // A chip's served params ("" for All): Home reloads for it.
  function setChip(params) {
    if (store && params !== chip) store.loadHome(false, params)
  }

  // h / l: the chip before or after (wrapping).
  function horizontal(dx) {
    if (chips.length > 1) setChip(Model.nextHomeChip(chips, chip, dx))
  }

  function shelfAt(index) {
    return store && index >= 0 && index < store.shelves.length ? store.shelves[index] : null
  }

  function activate(index) {
    var row = resultList.rowAt(index)
    if (!row || !service) return
    var item = row.item
    if (item.type === "more") {
      root.openRequested(Model.moreTarget(item))
    } else if (item.type === "loadMore") {
      if (store) store.loadHomeMore(true)
    } else if (Model.isPlayAllRow(item)) {
      service.playShelf(shelfAt(row.shelfIndex))
    } else if (item.type === "message" || item.type === "text" || item.type === "action") {
      return
    } else if (row.trackIndex !== undefined) {
      service.playCollection(Model.shelfTracks(store.shelves, row.shelfIndex), row.trackIndex, false,
                             Model.shelfContext(shelfAt(row.shelfIndex)))
    } else {
      root.openRequested(item)
    }
  }

  // n / a: a song, or a whole album, playlist or mix (Service.playItem).
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
    root.itemMenuRequested({ item: row.item, ctx: { pageKind: "home" }, owner: root, index: index })
  }

  // M: the Refresh button's twin.
  function pageMenu() {
    return { item: null, ctx: { page: true, pageKind: "home" }, owner: root, index: -1, title: "Home", subtitle: "" }
  }

  // Once the bindings settle (a sign-in or out empties the Store first).
  function refreshShown() {
    if (visible) refresh(false)
  }

  onVisibleChanged: if (visible) refresh(false)
  onSignedInChanged: Qt.callLater(refreshShown)

  Item {
    id: statusRow
    width: parent.width
    height: Math.max(statusText.implicitHeight, refreshButton.implicitHeight)

    Text {
      id: statusText
      anchors.verticalCenter: parent.verticalCenter
      width: parent.width - refreshButton.width - Style.space(8)
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      text: !root.store ? ""
        : root.store.homeLoading && root.rows.length === 0 ? "Loading your recommendations…"
        : root.store.homeError !== "" ? root.store.homeError
        : root.service && !root.service.signedIn ? "General picks. Sign in for yours."
        : "Picked for you"
      color: root.store && root.store.homeError !== "" ? Color.urgent : Qt.darker(root.foreground, 1.5)
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Button {
      id: refreshButton
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      iconText: "󰑐"
      tooltipText: "Refresh (also in the " + Model.keyHint("pageMenu") + " menu)"
      foreground: root.foreground
      fontSize: Style.font.caption
      onClicked: root.refresh(true)
    }
  }

  // The mood chips (h / l), shown once YouTube has served them.
  Flow {
    id: chipRow
    anchors.top: statusRow.bottom
    anchors.topMargin: Style.space(8)
    width: parent.width
    spacing: Style.space(4)
    visible: root.chips.length > 1

    Repeater {
      model: root.chips

      Button {
        required property var modelData
        text: modelData.title
        selected: root.chip === modelData.params
        tooltipText: Model.withKey(modelData.title, "horizontal")
        foreground: root.foreground
        fontSize: Style.font.caption
        onClicked: root.setChip(modelData.params)
      }
    }
  }

  ResultList {
    id: resultList
    anchors.top: chipRow.visible ? chipRow.bottom : statusRow.bottom
    anchors.topMargin: Style.space(8)
    anchors.bottom: parent.bottom
    width: parent.width
    bar: root.bar
    store: root.store
    rows: root.rows
    owner: root
    // Another chip starts at the top; a refresh or the next page keeps the place.
    listKey: root.chip
    moreBusy: !!root.feed && (root.feed.loadingMore === true || root.feed.moreWanted === true)
    moreError: root.feed && root.feed.moreError ? root.feed.moreError : ""

    onActivated: function(index) { root.activate(index) }
    onPlayNextRequested: function(index) { root.playNext(index) }
    onAddToQueueRequested: function(index) { root.addToQueue(index) }
    // After a failure only an explicit Load more tries again.
    onNearEnd: if (root.store && root.hasMore && !(root.feed && root.feed.moreError)) root.store.loadHomeMore(false)
  }
}
