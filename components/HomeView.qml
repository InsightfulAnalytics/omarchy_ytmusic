import QtQuick
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// YouTube Music's home shelves (personal when signed in). Songs play their
// shelf from that song; albums, playlists and artists open as pages.
Item {
  id: root

  property var service: null
  property QtObject bar: null

  readonly property var store: service ? service.store : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property var rows: store ? Model.homeRows(store.shelves) : []
  readonly property alias results: resultList

  signal openRequested(var item)
  // A row's menu, or the page's (Refresh): Panel opens it.
  signal itemMenuRequested(var request)

  function refresh(force) {
    if (store) store.loadHome(force)
  }

  function activate(index) {
    var row = resultList.rowAt(index)
    if (!row || !service) return
    if (row.item.type === "track") service.playCollection(Model.shelfTracks(store.shelves, row.shelfIndex), row.trackIndex, false)
    else root.openRequested(row.item)
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

  onVisibleChanged: if (visible) refresh(false)

  Item {
    id: header
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
        : root.service && !root.service.signedIn ? "General picks — sign in for yours."
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

  ResultList {
    id: resultList
    anchors.top: header.bottom
    anchors.topMargin: Style.space(8)
    anchors.bottom: parent.bottom
    width: parent.width
    bar: root.bar
    store: root.store
    rows: root.rows
    owner: root

    onActivated: function(index) { root.activate(index) }
    onPlayNextRequested: function(index) { root.playNext(index) }
    onAddToQueueRequested: function(index) { root.addToQueue(index) }
  }
}
