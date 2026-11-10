import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// The play queue (mpv's playlist): click to jump, hover to move or remove,
// right-click (or m) for a song's menu. Owns its keyboard cursor; Panel.qml
// forwards keys. Its header's pane chip (or y) switches the area below to
// Lyrics or Related (S11): Panel puts those views in it as children.
Item {
  id: root

  property var service: null
  property QtObject bar: null
  property int cursorIndex: -1
  // "queue", "lyrics" or "related" (prefs.nowPane).
  property string pane: "queue"
  // The other panes, declared inside this view by Panel: they fill the area
  // under the header while their pane is shown.
  default property alias paneContent: paneArea.data

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property var queue: service ? service.queue : []
  readonly property int currentIndex: service ? service.playlistPos : -1

  // A song's menu: Panel opens it.
  signal itemMenuRequested(var request)
  // S13: the header's Save: every queued song into a playlist (Panel's picker).
  signal saveRequested()

  function ensureVisible(index) {
    if (index >= 0 && index < list.count) list.positionViewAtIndex(index, ListView.Contain)
  }

  function moveCursor(delta) {
    if (queue.length === 0) {
      cursorIndex = -1
      return
    }
    if (cursorIndex < 0) cursorIndex = Math.max(0, currentIndex)
    else cursorIndex = Math.max(0, Math.min(queue.length - 1, cursorIndex + delta))
    ensureVisible(cursorIndex)
  }

  function activateCursor() {
    if (cursorIndex < 0 || !service) return false
    service.jumpTo(cursorIndex)
    return true
  }

  // The menu's Play jumps to the entry; its Remove takes it out.
  function activate(index) {
    if (index < 0 || !service) return false
    return service.jumpTo(index)
  }

  function remove(index) {
    if (index < 0 || !service) return
    service.removeAt(index)
  }

  // A menu's entry, found again when it runs (the queue may have changed):
  // mpv's playlist entry id, which stays with the entry when others move.
  function placeAt(index) {
    var entry = index >= 0 && index < queue.length ? queue[index] : null
    return entry ? { entryId: entry.entryId } : null
  }

  function indexOfPlace(place) {
    if (!place) return -1
    for (var i = 0; i < queue.length; i++) if (queue[i].entryId === place.entryId) return i
    return -1
  }

  function menu(index) {
    var entry = index >= 0 && index < queue.length ? queue[index] : null
    if (!entry || Model.menuKind(entry.track) === "") return
    cursorIndex = index
    var current = index === currentIndex
    // G2: the song with its library and Listen again tokens (Save, Pin).
    var track = service ? service.withLiveTokens(entry.track) : entry.track
    root.itemMenuRequested({ item: track, owner: root, index: index,
      ctx: { pageKind: "queue", removable: true, removeTitle: "Remove from queue", isCurrent: current,
             counterpart: current && service ? service.counterpartOf(entry.track) : null } })  // S12
  }

  // G2 (Model.editCallbackOf): after a library or pin edit made here or on
  // the song playing (Panel.nowMenu, S on Now) whose outcome is unknown or
  // whose token was stale, that song's own menu is read again; true then
  // ("refreshed").
  function editCallback(item) {
    var videoId = item && item.videoId ? String(item.videoId) : ""
    return function(reply) {
      if (!root.service || videoId === "" || !(root.service.unsure(reply) || Model.staleToken(reply))) return false
      return root.service.rereadSongTokens(videoId)
    }
  }

  function removeCursor() {
    if (cursorIndex < 0 || !service) return
    service.removeAt(cursorIndex)
    if (cursorIndex >= queue.length - 1) cursorIndex = queue.length - 2
  }

  // y or the pane chip: Queue, Lyrics, Related, round again (remembered).
  function cyclePane() {
    if (service) service.setPref("nowPane", Model.nextNowPane(pane, 1))
  }

  onPaneChanged: cursorIndex = -1

  // ---- up next (S12) ------------------------------------------------------------------
  // Under the header on the Queue pane: "Playing from <title>" and the one
  // tuner chip ("Tune: All"; a click or t shows the next chip, the Now page
  // menu lists them all). Choosing a chip replaces only the songs autoplay
  // added. The chip hides when the song has none (signed out, a video).
  readonly property string playingFrom: service ? service.playingFrom : ""
  readonly property int tunerCount: service && service.tunerChips ? service.tunerChips.length : 0
  readonly property bool contextShown: pane === "queue" && (playingFrom !== "" || tunerCount > 0)

  function cycleTuner() {
    if (service && tunerCount > 0) service.cycleTuner(1)
  }

  // A trim took played rows off the top: the cursor stays on its song (and
  // goes when its row went).
  Connections {
    target: root.service
    function onTrimmed(count) {
      if (root.cursorIndex >= 0) root.cursorIndex = root.cursorIndex >= count ? root.cursorIndex - count : -1
    }
  }

  function moveSelected(delta) {
    if (cursorIndex < 0 || !service) return
    var target = cursorIndex + delta
    if (service.move(cursorIndex, target)) {
      cursorIndex = target
      ensureVisible(cursorIndex)
    }
  }

  Item {
    id: header
    width: parent.width
    height: Math.max(heading.implicitHeight, autoplayButton.implicitHeight, paneChip.implicitHeight)

    // One chip for the three panes: it names the one shown, a click (or y)
    // shows the next.
    Button {
      id: paneChip
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      iconText: Model.nowPaneIcon(root.pane)
      text: Model.nowPaneTitle(root.pane)
      tooltipText: Model.withKey("Show " + Model.nowPaneTitle(Model.nextNowPane(root.pane, 1)).toLowerCase(), "nowPane")
      foreground: root.foreground
      fontSize: Style.font.caption
      onClicked: root.cyclePane()
    }

    Text {
      id: heading
      anchors.left: paneChip.right
      anchors.leftMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      visible: root.pane === "queue"
      textFormat: Text.PlainText
      text: root.queue.length === 0 ? ""
        : (root.currentIndex >= 0 ? (root.currentIndex + 1) + " / " : "") + root.queue.length
      color: Qt.darker(root.foreground, 1.4)
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.letterSpacing: 1
    }

    Row {
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      visible: root.pane === "queue"
      spacing: Style.space(4)

      // S13: Save the queue to a playlist (the Now page menu's twin).
      Button {
        visible: root.queue.length > 0 && !!root.service && root.service.signedIn
        iconText: "󰐒"
        text: "Save"
        tooltipText: "Save the queue to a playlist (" + Model.keyHint("pageMenu") + " has it too)"
        foreground: root.foreground
        fontSize: Style.font.caption
        onClicked: root.saveRequested()
      }

      Button {
        id: autoplayButton
        readonly property bool autoplayOn: !!root.service && root.service.autoplay
        text: "Autoplay"
        selected: autoplayOn
        tooltipText: autoplayOn ? "Similar songs follow when the queue ends (click to turn off)"
          : "Keep playing similar songs when the queue ends"
        foreground: root.foreground
        fontSize: Style.font.caption
        onClicked: root.service.toggleAutoplay()
      }

      Button {
        visible: root.queue.length > 1
        text: "Clear"
        tooltipText: "Remove everything except the current track"
        foreground: root.foreground
        fontSize: Style.font.caption
        onClicked: root.service.clearUpcoming()
      }
    }
  }

  // S12: "Playing from" and the tuner chip (Queue pane only).
  Item {
    id: contextRow
    anchors.top: header.bottom
    anchors.topMargin: visible ? Style.space(4) : 0
    width: parent.width
    height: visible ? Math.max(playingFromText.implicitHeight, tuneChip.visible ? tuneChip.implicitHeight : 0) : 0
    visible: root.contextShown

    Text {
      id: playingFromText
      anchors.left: parent.left
      anchors.right: tuneChip.visible ? tuneChip.left : parent.right
      anchors.rightMargin: tuneChip.visible ? Style.space(8) : 0
      anchors.verticalCenter: parent.verticalCenter
      visible: root.playingFrom !== ""
      textFormat: Text.PlainText
      text: "Playing from " + root.playingFrom
      color: Qt.darker(root.foreground, 1.4)
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      elide: Text.ElideRight
    }

    Button {
      id: tuneChip
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      visible: root.tunerCount > 0
      iconText: "󰘮"
      text: "Tune: " + (root.service ? root.service.tunerTitle : "All")
      tooltipText: Model.withKey("Next tuner chip (M lists them all)", "tune")
      selected: !!root.service && !!root.service.tuner
      foreground: root.foreground
      fontSize: Style.font.caption
      onClicked: root.cycleTuner()
    }
  }

  // Lyrics and Related (Panel's children of this view) go here.
  Item {
    id: paneArea
    anchors.top: header.bottom
    anchors.topMargin: Style.space(6)
    anchors.bottom: parent.bottom
    width: parent.width
  }

  ListView {
    id: list
    anchors.top: contextRow.bottom  // S12
    anchors.topMargin: Style.space(6)
    anchors.bottom: parent.bottom
    width: parent.width
    visible: root.pane === "queue"
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    spacing: Style.space(2)
    model: root.queue
    reuseItems: true

    ScrollBar.vertical: ScrollBar {
      policy: ScrollBar.AsNeeded
    }

    delegate: TrackRow {
      required property var modelData
      required property int index

      width: list.width
      bar: root.bar
      store: root.service ? root.service.store : null
      track: modelData.track
      current: modelData.current
      cursor: index === root.cursorIndex
      showMove: true
      showRemove: true
      showMenu: true

      onActivated: root.service.jumpTo(index)
      onRemoveRequested: root.service.removeAt(index)
      onMenuRequested: root.menu(index)
      onMoveRequested: function(delta) { root.service.move(index, index + delta) }
    }

    // S12: where autoplay would have added songs, why it did not.
    footer: Item {
      readonly property string text: note.text
      width: list.width
      height: note.text !== "" ? note.implicitHeight + Style.space(8) : 0
      visible: note.text !== ""

      Text {
        id: note
        anchors.bottom: parent.bottom
        width: parent.width
        textFormat: Text.PlainText
        text: root.service && root.service.autoplay ? root.service.autoplayNote : ""
        color: Qt.darker(root.foreground, 1.6)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        horizontalAlignment: Text.AlignHCenter
      }
    }

    Text {
      anchors.centerIn: parent
      visible: list.count === 0
      textFormat: Text.PlainText
      text: "The queue is empty"
      color: Qt.darker(root.foreground, 1.6)
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
  }
}
