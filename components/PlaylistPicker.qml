import QtQuick
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// "Add to playlist": your own playlists, or a new one created with the songs.
// Covers the panel content while it is open: with `tracks` (a song, or the
// queue's songs), or (S13) with `source`, a whole album or playlist ("Save
// to playlist"; Copy to new playlist puts its title in the name field).
// A song YouTube says is already in the playlist chosen asks again: Enter
// on the same playlist adds it anyway. G4: when some of several songs (or
// of a whole album or playlist) are there, a Skip duplicates row under
// that playlist adds only the others; a mix is saved by its songs (the 50
// it plays, read when the picker opens).
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
  // G4: Skip duplicates is offered (for duplicateOf); a mix's songs are being read.
  property bool skipOffered: false
  property bool loadingSongs: false
  property int _generation: 0

  readonly property bool open: tracks.length > 0 || !!source
  readonly property string sourceId: source ? Model.sourcePlaylistOf(source) : ""
  // G4: a mix has no playlist to add by: its songs are added.
  readonly property bool sourceIsMix: !!source && Model.menuKind(source) === "mix"
  readonly property var store: service ? service.store : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  // Your playlists, without the one being saved (a playlist is never added to itself).
  // G4: with the Skip duplicates row under the playlist YouTube refused.
  readonly property var rows: store ? Model.withSkipRow(Model.ownedPlaylists(store.playlists)
    .filter(function(p) { return p.playlistId !== root.sourceId })
    .map(function(p) { return { item: p } }), duplicateOf, skipOffered) : []
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
    skipOffered = false  // G4
    loadingSongs = false
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
    if (sourceIsMix) _readMixSongs()  // G4
  }

  // G4: a mix's songs (tracksFor: the 50 the panel plays), which are what
  // goes into the playlist.
  function _readMixSongs() {
    if (!service) return
    var generation = _generation
    loadingSongs = true
    status = "Reading the mix's songs…"
    service.itemTracks(source, function(reply) {
      if (!root || generation !== root._generation) return
      root.loadingSongs = false
      var list = reply.ok ? (reply.tracks || []).filter(function(t) { return t && t.videoId }) : []
      root.tracks = list
      root.status = list.length > 0 ? "" : reply.ok ? "The mix sent no songs to save" : "Couldn't read the mix: " + reply.error.message
    })
  }

  // Adds to the playlist chosen; the picker stays until YouTube answers, so
  // a duplicate can be asked about (Enter again sends duplicates: true).
  // G4: Enter on the Skip duplicates row skips them instead.
  function activate(index) {
    var row = resultList.rowAt(index)
    if (!row || !service || busy) return
    if (row.item.type === "action") {
      if (row.item.id === Model.SKIP_DUPLICATES_ID) skipDuplicates()
      return
    }
    _add(row.item, false)
  }

  // G4: the playlist YouTube refused, again, adding only what is not there.
  function skipDuplicates() {
    if (!skipOffered || duplicateOf === "" || busy) return
    var list = rows
    for (var i = 0; i < list.length; i++) {
      if (list[i].item.type !== "action" && list[i].item.playlistId === duplicateOf) {
        _add(list[i].item, true)
        return
      }
    }
  }

  function _add(playlist, skip) {
    if (loadingSongs || (sourceIsMix && tracks.length === 0)) return  // G4: the mix's songs first
    var generation = _generation
    var options = Model.pickerAddOptions({ duplicateOf: duplicateOf }, playlist.playlistId, skip)
    if (source) {
      if (sourceId !== "") options.sourcePlaylistId = sourceId  // G4: a mix goes as its songs
      options.sourceTitle = Model.itemTitle(source)
    }
    var total = tracks.length
    busy = true
    status = (skip ? "Adding the songs not there yet to " : "Adding to ") + playlist.title + "…"
    var sent = service.addToPlaylist(playlist, tracks, function(reply) {
      // Closed or opened again meanwhile: Service tells what happened.
      if (!root || generation !== root._generation) return false
      root.busy = false
      var next = Model.pickerAfterReply({ duplicateOf: root.duplicateOf }, playlist.playlistId, reply, total)
      root.duplicateOf = next.duplicateOf
      root.status = next.status
      root.skipOffered = next.skip === true  // G4
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
    if (sourceIsMix && tracks.length === 0) return  // G4: the mix's songs first
    if (source && sourceId !== "")
      service.createPlaylist(name, [], null, { sourcePlaylistId: sourceId, sourceTitle: Model.itemTitle(source) })
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
        text: root.source ? Model.pickerSourceText(root.source)  // G4: a mix too
                            + (root.sourceIsMix && root.tracks.length > 0 ? " (" + Model.songsText(root.tracks.length) + ")" : "")
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
      placeholderText: root.source ? Model.pickerSourcePlaceholder(root.source)  // G4: a mix too
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
    color: root.status !== "" ? (root.duplicateOf !== "" ? Color.accent
                                 : root.busy || root.loadingSongs ? Qt.darker(root.foreground, 1.5) : Color.urgent)  // G4
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
    opacity: root.busy || root.loadingSongs ? 0.5 : 1  // G4

    onActivated: function(index) { root.activate(index) }
  }
}
