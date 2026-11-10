import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// The Lyrics pane on Now (S11). Time-synced lyrics show every line, the one
// being sung highlighted and kept in the middle (a click on a line seeks
// there); others show as plain text. The source is credited below. Lyrics
// load when the pane is shown, by the song's lyricsId from nowContext, and
// the time is read from mpv every 250 ms only while the pane is shown in an
// open panel and the song plays.
Item {
  id: root

  property var service: null
  property QtObject bar: null
  // The pane is shown in an open panel: only then does it load or follow
  // the time.
  property bool active: false

  readonly property var store: service ? service.store : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property var track: service ? service.currentTrack : null
  readonly property string videoId: track && track.videoId ? String(track.videoId) : ""
  readonly property var context: service ? service.nowContext : null
  // The memo may still hold the previous song's context only.
  readonly property bool contextReady: !!context && videoId !== "" && context.videoId === videoId
  readonly property string lyricsId: contextReady && context.lyricsId ? String(context.lyricsId) : ""
  readonly property var lyrics: store && videoId !== "" ? store.lyricsOf(videoId) : null
  readonly property string loadError: {
    if (!store || videoId === "") return ""
    var failure = service ? service.nowContextFailure : null
    if (!contextReady && failure && failure.videoId === videoId) return failure.message
    return store.lyricsError(videoId)
  }
  readonly property var lines: lyrics && lyrics.lines ? lyrics.lines : []
  readonly property bool synced: lines.length > 0
  readonly property bool hasText: !!lyrics && typeof lyrics.text === "string" && lyrics.text !== ""
  readonly property int currentLine: synced && service ? Model.currentLineIndex(lines, service.position) : -1
  // The source line's room under the lyrics.
  readonly property real sourceRoom: sourceLine.visible ? sourceLine.implicitHeight + Style.space(6) : 0
  // When the lines were last scrolled by hand (a flick, the wheel, the
  // scroll bar, j / k / g / G): the current line is not pulled back to the
  // middle for a few seconds after, nor while the scroll bar is held.
  property real _manualAt: 0

  readonly property string status: {
    if (!track) return "Nothing playing"
    if (loadError !== "") return loadError
    if (!contextReady) return "Loading…"
    if (lyricsId === "") return "No lyrics for this song"
    if (!lyrics) return "Loading lyrics…"
    if (!hasText) return "No lyrics for this song"
    return ""
  }

  // Lyrics already held are only marked as used (the Store's LRU).
  function load() {
    if (active && store && videoId !== "" && lyricsId !== "") store.loadLyrics(videoId, lyricsId)
  }

  // j / k: three lines up or down.
  function scroll(delta) {
    var view = synced ? lineList : plainView
    if (!view.visible) return
    _manualAt = Date.now()
    var step = Style.space(60) * (delta < 0 ? -1 : 1)
    var last = view.originY + Math.max(0, view.contentHeight - view.height)
    view.contentY = Math.max(view.originY, Math.min(view.contentY + step, last))
  }

  // g / G: the first or the last line.
  function jump(toEnd) {
    var view = synced ? lineList : plainView
    if (!view.visible) return
    _manualAt = Date.now()
    view.contentY = toEnd ? view.originY + Math.max(0, view.contentHeight - view.height) : view.originY
  }

  // The current line to the middle (smoothly when it is near).
  function centerLine(animated) {
    if (!synced || !active || lineBar.pressed || Date.now() - _manualAt < 4000) return
    var from = lineList.contentY
    if (currentLine < 0) lineList.positionViewAtBeginning()
    else lineList.positionViewAtIndex(currentLine, ListView.Center)
    var to = lineList.contentY
    if (!animated || Math.abs(to - from) > lineList.height * 2) return
    lineList.contentY = from
    scrollAnimation.to = to
    scrollAnimation.restart()
  }

  onActiveChanged: {
    load()
    if (active) Qt.callLater(root.centerLine, false)
  }
  onLyricsIdChanged: load()
  onVideoIdChanged: {
    _manualAt = 0
    load()
  }
  onCurrentLineChanged: centerLine(true)
  onLinesChanged: Qt.callLater(root.centerLine, false)

  NumberAnimation {
    id: scrollAnimation
    target: lineList
    property: "contentY"
    duration: 250
    easing.type: Easing.OutCubic
  }

  // mpv's time-pos, four times a second, while the highlight can move.
  Timer {
    interval: 250
    repeat: true
    running: root.active && root.synced && !!root.service && root.service.playing
    onTriggered: root.service.fetchPosition()
  }

  Text {
    anchors.centerIn: parent
    width: parent.width
    visible: root.status !== ""
    horizontalAlignment: Text.AlignHCenter
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    text: root.status
    color: Qt.darker(root.foreground, 1.6)
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  ListView {
    id: lineList
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    anchors.bottomMargin: root.sourceRoom
    width: parent.width
    visible: root.status === "" && root.synced
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    spacing: Style.space(6)
    model: root.synced ? root.lines : []
    // Room above the first line and below the last, so either can sit in
    // the middle.
    header: Item {
      width: lineList.width
      height: lineList.height / 2
    }
    footer: Item {
      width: lineList.width
      height: lineList.height / 2
    }

    onMovementStarted: root._manualAt = Date.now()

    // A drag on the bar moves contentY without a movement signal: its press
    // and its release count as scrolling by hand.
    ScrollBar.vertical: ScrollBar {
      id: lineBar
      policy: ScrollBar.AsNeeded
      onPressedChanged: root._manualAt = Date.now()
    }

    delegate: Text {
      id: lineText
      required property var modelData
      required property int index
      readonly property bool current: index === root.currentLine

      width: lineList.width
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      text: String(modelData.text || "")
      color: current ? root.foreground : Qt.darker(root.foreground, index < root.currentLine ? 1.8 : 1.4)
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
      font.bold: current

      MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: {
          root._manualAt = 0
          if (root.service) root.service.seek(Number(lineText.modelData.start) || 0)
        }
      }
    }
  }

  Flickable {
    id: plainView
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    anchors.bottomMargin: root.sourceRoom
    width: parent.width
    visible: root.status === "" && !root.synced
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    contentWidth: width
    contentHeight: plainText.implicitHeight

    ScrollBar.vertical: ScrollBar {
      policy: ScrollBar.AsNeeded
    }

    Text {
      id: plainText
      width: plainView.width
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      text: root.hasText && !root.synced ? root.lyrics.text : ""
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
      lineHeight: 1.15
    }
  }

  // "Source: Musixmatch", as on the web.
  Text {
    id: sourceLine
    anchors.bottom: parent.bottom
    width: parent.width
    visible: root.status === "" && text !== ""
    textFormat: Text.PlainText
    elide: Text.ElideRight
    text: root.lyrics && root.lyrics.source ? String(root.lyrics.source) : ""
    color: Qt.darker(root.foreground, 1.6)
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
  }
}
