import QtQuick
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// The Explore tab (S6): the web's Explore page. Its first rows open New
// releases, Charts and Moods & genres; then New albums & singles, Top songs (Premium),
// Moods & genres (eight, and a row that shows them all), Trending, New
// music videos and Popular episodes (when served), each ending in See all.
// Songs play their section from there;
// albums, playlists, artists and moods open as pages. Asked when the tab is
// first shown and kept 30 min (Store.loadExplore).
Item {
  id: root

  property var service: null
  property QtObject bar: null

  readonly property var store: service ? service.store : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property var explore: store ? store.explore : null
  readonly property var page: explore ? explore.page : null
  // Every Moods & genres category is shown, not only the first eight.
  property bool moodsOpen: false
  // A press of the moods toggle, until the rows it changed are shown:
  // { open, offset } (the toggle row's distance from the top edge, null
  // when it was not on screen).
  property var _moodsPending: null
  readonly property var rows: Model.exploreRows(page, { moodsOpen: moodsOpen })
  readonly property alias results: resultList

  signal openRequested(var item)
  // A row's menu, or the page's (Refresh): Panel opens it.
  signal itemMenuRequested(var request)

  function refresh(force) {
    if (store) store.loadExplore(force)
  }

  // Enter: a song plays its section from there; an entry row, a See all or
  // any other row opens its page.
  function activate(index) {
    var row = resultList.rowAt(index)
    if (!row || !service) return
    var item = row.item
    if (item.type === "more") root.openRequested(Model.moreTarget(item))
    else if (item.type === "action" && item.id === Model.EXPLORE_MOODS_ID) toggleMoods(index)
    else if (item.type === "action") { if (item.target) root.openRequested(item.target) }
    else if (item.type === "message" || item.type === "text") return
    else if (row.trackIndex !== undefined)
      service.playCollection(Model.sectionTracks(page, row.sectionIndex), row.trackIndex, false,
                             Model.exploreContext(page, row.sectionIndex))
    else root.openRequested(item)
  }

  // The moods toggle (Enter on its row). The rows change at once, 43 of
  // them with the live 51 categories: the cursor goes onto the first
  // category shown (or the toggle again, closing), on screen where the
  // toggle was (_afterMoodsToggle).
  function toggleMoods(index) {
    var shown = resultList.itemAtIndex(index)
    _moodsPending = { open: !moodsOpen, offset: shown ? shown.y - resultList.contentY : null }
    moodsOpen = !moodsOpen
  }

  function _afterMoodsToggle() {
    var pending = _moodsPending
    if (!pending) return
    _moodsPending = null
    var i = Model.exploreMoodsCursor(rows, pending.open)
    if (i < 0) return
    var place = Model.rowPlace(rows, i)
    if (pending.offset !== null) resultList.restoreView({ cursor: place, anchor: { place: place, offset: pending.offset } })
    else resultList.cursorIndex = i
    resultList.positionViewAtIndex(i, ListView.Contain)
  }

  // n / a: a song, or a whole album or playlist (Service.playItem).
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
    root.itemMenuRequested({ item: row.item, ctx: { pageKind: "explore" }, owner: root, index: index })
  }

  // M: the Refresh button's twin.
  function pageMenu() {
    return { item: null, ctx: { page: true, pageKind: "explore" }, owner: root, index: -1, title: "Explore", subtitle: "" }
  }

  onVisibleChanged: if (visible) refresh(false)

  // Signing in or out empties the Store's copy: shown, the tab asks again.
  Connections {
    target: root.store
    function onExploreChanged() {
      var e = root.store.explore
      if (root.visible && !e.page && !e.loading && e.at === 0 && e.error === "") root.refresh(false)
    }
  }

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
      text: Model.exploreStatus(root.explore)
      color: root.explore && root.explore.error ? Color.urgent : Qt.darker(root.foreground, 1.5)
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
    onRowsUpdated: root._afterMoodsToggle()
  }
}
