import QtQuick
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// The Related pane on Now (S11): the web's Related tab for the song
// playing (you might also like, recommended playlists, other performances,
// similar artists, the artist's albums, about the artist) as page rows.
// Rows play and open as on any page; it loads when the pane is shown, by
// the song's relatedId from nowContext, through the Store like a page.
Item {
  id: root

  property var service: null
  property QtObject bar: null
  // The pane is shown in an open panel: only then does it load.
  property bool active: false

  readonly property var store: service ? service.store : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property var track: service ? service.currentTrack : null
  readonly property string videoId: track && track.videoId ? String(track.videoId) : ""
  readonly property var context: service ? service.nowContext : null
  readonly property bool contextReady: !!context && videoId !== "" && context.videoId === videoId
  readonly property string relatedId: contextReady && context.relatedId ? String(context.relatedId) : ""
  readonly property var ref: relatedId !== "" ? Model.pageRef(Model.relatedTarget(relatedId)) : null
  readonly property string refKey: ref ? ref.key : ""

  property var page: null
  // The ref key `page` belongs to.
  property string _pageKey: ""
  property bool loading: false
  property string error: ""

  readonly property var rows: page && _pageKey === refKey ? Model.pageRows(page) : []
  readonly property alias results: resultList

  readonly property string status: {
    if (!track) return "Nothing playing"
    var failure = service ? service.nowContextFailure : null
    if (!contextReady && failure && failure.videoId === videoId) return failure.message
    if (!contextReady) return "Loading…"
    if (relatedId === "") return "Nothing related to this song"
    if (error !== "") return error
    if (rows.length === 0) return loading || !page ? "Loading…" : "Nothing related to this song"
    return ""
  }

  signal openRequested(var item)
  // A row's menu: Panel opens it.
  signal itemMenuRequested(var request)

  function load() {
    if (!active || !store || !ref || loading || (page && _pageKey === refKey)) return
    var key = refKey
    loading = true
    error = ""
    store.loadCollection(ref, function(reply) {
      if (root.refKey !== key) return
      root.loading = false
      if (reply.ok) {
        root.page = reply.result
        root._pageKey = key
        root.error = ""
      } else if (!root.page || root._pageKey !== key) {
        root.error = reply.error.message
      }
    })
  }

  onActiveChanged: load()
  onRefKeyChanged: {
    loading = false
    error = ""
    if (_pageKey !== refKey) page = null
    load()
  }

  // Enter: a song plays its section from there, See all and other rows open.
  function activate(index) {
    var row = resultList.rowAt(index)
    if (!row || !service) return
    var item = row.item
    if (item.type === "more") root.openRequested(Model.moreTarget(item))
    else if (item.type === "message" || item.type === "text") return
    else if (row.trackIndex !== undefined) service.playCollection(Model.sectionTracks(page, row.sectionIndex), row.trackIndex, false)
    else root.openRequested(item)
  }

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
    root.itemMenuRequested({ item: row.item, owner: root, index: index, ctx: { pageKind: "related" } })
  }

  Text {
    anchors.centerIn: parent
    width: parent.width
    visible: root.status !== ""
    horizontalAlignment: Text.AlignHCenter
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    text: root.status
    color: root.error !== "" ? Color.urgent : Qt.darker(root.foreground, 1.6)
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  ResultList {
    id: resultList
    anchors.fill: parent
    visible: root.status === ""
    bar: root.bar
    store: root.store
    rows: root.rows
    owner: root
    listKey: root.refKey

    onActivated: function(index) { root.activate(index) }
    onPlayNextRequested: function(index) { root.playNext(index) }
    onAddToQueueRequested: function(index) { root.addToQueue(index) }
  }
}
