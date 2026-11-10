import QtQuick
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// "Add to playlist": your own playlists, or a new one created with the songs.
// Covers the panel content while it is open: with `tracks` (a song, or the
// queue's songs), or (S13) with `source`, a whole album or playlist ("Save
// to playlist"; Copy to new playlist puts its title in the name field).
// A song YouTube says is already in the playlist chosen asks again: Enter
// on the same playlist adds it anyway.
Rectangle {
  id: root

  property var service: null
  property QtObject bar: null
  property var tracks: []
  // S13: the album or playlist saved whole (null: the songs in `tracks`).
  property var source: null
  // S13: what the songs are, when not one song ("The queue").
  property string label: ""
  // S13: the duplicate prompt (Model.pickerAfterReply), the add on its way.
  property string duplicateOf: ""
  property string status: ""
  property bool busy: false
  property int _generation: 0

  readonly property bool open: tracks.length > 0 || !!source
  readonly property string sourceId: source ? Model.sourcePlaylistOf(source) : ""
  readonly property var store: service ? service.store : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  // Your playlists, without the one being saved (a playlist is never added to itself).
  readonly property var rows: store ? Model.ownedPlaylists(store.playlists)
    .filter(function(p) { return p.playlistId !== root.sourceId })
    .map(function(p) { return { item: p } }) : []
  readonly property alias results: resultList
  readonly property bool inputFocused: nameField.activeFocus

  signal closed()

  function close() {
    if (!open) return
    tracks = []
    source = null
    label = ""
    duplicateOf = ""
    status = ""
    busy = false
    _generation++
    nameField.text = ""
    closed()
  }

  // S13: open with songs (label: what they are, e.g. "The queue").
  function openTracks(list, what) {
    close()
    label = what || ""
    tracks = list || []
  }

  // S13: open with a whole album or playlist; `copy` puts its title in the
  // name field, ready for Enter (Copy to new playlist).
  function openSource(item, copy) {
    close()
    source = item
    if (copy) {
      nameField.text = Model.itemTitle(item)
      nameField.forceActiveFocus()
      nameField.selectAll()
    }
  }

  // Adds to the playlist chosen; the picker stays until YouTube answers, so
  // a duplicate can be asked about (Enter again sends duplicates: true).
  function activate(index) {
    var row = resultList.rowAt(index)
    if (!row || !service || busy) return
    var playlist = row.item
    var generation = _generation
    var options = Model.pickerAddOptions({ duplicateOf: duplicateOf }, playlist.playlistId)
    if (source) {
      options.sourcePlaylistId = sourceId
      options.sourceTitle = Model.itemTitle(source)
    }
    var total = tracks.length
    busy = true
    status = "Adding to " + playlist.title + "…"
    var sent = service.addToPlaylist(playlist, tracks, function(reply) {
      // Closed or opened again meanwhile: Service tells what happened.
      if (!root || generation !== root._generation) return false
      root.busy = false
      var next = Model.pickerAfterReply({ duplicateOf: root.duplicateOf }, playlist.playlistId, reply, total)
      root.duplicateOf = next.duplicateOf
      root.status = next.status
      if (next.done || root.service.unsure(reply)) root.close()
      return true
    }, options)
    if (!sent) {
      busy = false
      status = ""
    }
  }

  function playNext(index) {}
  function addToQueue(index) {}

  function createWithTracks() {
    var name = nameField.text.trim()
    if (name === "" || !service) return
    if (source) service.createPlaylist(name, [], null, { sourcePlaylistId: sourceId, sourceTitle: Model.itemTitle(source) })
    else service.createPlaylist(name, tracks)
    close()
  }

  visible: open
  color: Color.popups.background

  onOpenChanged: if (open && store) store.loadLibrary(false)

  // Keep clicks from reaching the views underneath.
  MouseArea {
    anchors.fill: parent
    acceptedButtons: Qt.AllButtons
  }

  Item {
    id: header
    width: parent.width
    height: Math.max(titleColumn.implicitHeight, closeButton.implicitHeight)

    Column {
      id: titleColumn
      width: parent.width - closeButton.width
      spacing: Style.space(2)

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: root.source ? "Save to playlist" : "Add to playlist"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.subtitle
        font.bold: true
      }

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: root.source ? (Model.menuKind(root.source) === "album" ? "The whole album: " : "The whole playlist: ")
                            + Model.itemTitle(root.source)
          : root.label !== "" ? root.label + ": " + root.tracks.length + (root.tracks.length === 1 ? " song" : " songs")
          : root.tracks.length === 1 ? Model.mediaTitle(root.tracks[0]) : root.tracks.length + " songs"
        color: Qt.darker(root.foreground, 1.4)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
      }
    }

    PanelActionButton {
      id: closeButton
      anchors.right: parent.right
      iconText: "󰅖"
      tooltipText: Model.withKey("Cancel", "back")
      foreground: root.foreground
      onClicked: root.close()
    }
  }

  Row {
    id: createRow
    anchors.top: header.bottom
    anchors.topMargin: Style.space(12)
    width: parent.width
    spacing: Style.space(6)

    TextField {
      id: nameField
      width: parent.width - createButton.width - parent.spacing
      placeholderText: root.source ? (Model.menuKind(root.source) === "album" ? "New playlist from this album"
                                                                             : "New playlist from this playlist")
        : root.tracks.length > 1 ? "New playlist with these " + root.tracks.length + " songs" : "New playlist with this song"
      foreground: root.foreground
      font.family: root.fontFamily

      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
          root.createWithTracks()
          event.accepted = true
        } else if (event.key === Qt.Key_Escape) {
          root.close()
          event.accepted = true
        }
      }
    }

    Button {
      id: createButton
      anchors.verticalCenter: parent.verticalCenter
      iconText: "󰐕"
      text: "Create"
      tooltipText: "Create a private playlist"
      enabled: nameField.text.trim() !== ""
      opacity: enabled ? 1 : 0.4
      foreground: root.foreground
      fontSize: Style.font.caption
      onClicked: root.createWithTracks()
    }
  }

  Text {
    id: statusText
    anchors.top: createRow.bottom
    anchors.topMargin: Style.space(8)
    width: parent.width
    visible: text !== ""
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    // S13: the add on its way, the duplicate prompt or why it failed come first.
    text: root.status !== "" ? root.status
      : !root.store ? "" : root.store.libraryLoading && root.rows.length === 0 ? "Loading your playlists…"
      : root.store.libraryError !== "" ? root.store.libraryError
      : root.rows.length === 0 ? "You have no playlists yet: create one above." : ""
    color: root.status !== "" ? (root.duplicateOf !== "" ? Color.accent : root.busy ? Qt.darker(root.foreground, 1.5) : Color.urgent)
      : root.store && root.store.libraryError !== "" ? Color.urgent : Qt.darker(root.foreground, 1.5)
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  ResultList {
    id: resultList
    anchors.top: statusText.visible ? statusText.bottom : createRow.bottom
    anchors.topMargin: Style.space(8)
    anchors.bottom: parent.bottom
    width: parent.width
    bar: root.bar
    store: root.store
    rows: root.rows
    opacity: root.busy ? 0.5 : 1

    onActivated: function(index) { root.activate(index) }
  }
}
