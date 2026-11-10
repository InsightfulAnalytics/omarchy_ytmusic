import QtQuick
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// One track: cover (or its number on an album page), title, artists (after
// a boxed E when explicit) and duration. Hovering (or the keyboard cursor)
// swaps the duration for row actions; a right-click (or the menu action)
// asks for the row's menu.
Item {
  id: root

  property var track: null
  property bool current: false
  property bool cursor: false
  property bool showMove: false
  property bool showRemove: false
  property bool showQueueActions: false
  property bool showMenu: false
  property string removeTooltip: "Remove"
  // S3: the track's number on its album, shown in place of the cover (0: the cover).
  property int number: 0
  // S13: show the net votes a playlist's song carries (pages; the queue does not).
  property bool showVotes: false
  property QtObject bar: null
  property var store: null

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool showActions: (hover.hovered || cursor) && (showMove || showRemove || showQueueActions || showMenu)
  readonly property bool hovered: hover.hovered

  signal activated()
  signal removeRequested()
  signal moveRequested(int delta)
  signal playNextRequested()
  signal addToQueueRequested()
  signal menuRequested()

  implicitHeight: Style.space(46)

  Rectangle {
    anchors.fill: parent
    radius: Style.spacing.labelGap
    color: root.current ? Style.selectedFillFor(root.foreground, Color.accent)
      : (hover.hovered || root.cursor ? Style.hoverFillFor(root.foreground, Color.accent) : "transparent")
  }

  HoverHandler {
    id: hover
  }

  MouseArea {
    anchors.fill: parent
    acceptedButtons: Qt.LeftButton | Qt.RightButton
    cursorShape: Qt.PointingHandCursor
    onClicked: function(mouse) {
      if (mouse.button === Qt.RightButton) root.menuRequested()
      else root.activated()
    }
  }

  Row {
    anchors.fill: parent
    anchors.leftMargin: Style.space(6)
    anchors.rightMargin: Style.space(8)
    spacing: Style.space(10)

    Item {
      id: leading
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(34)
      height: Style.space(34)

      CoverArt {
        id: cover
        anchors.fill: parent
        visible: root.number <= 0
        size: Style.space(34)
        store: root.store
        source: root.track && root.track.thumb && root.number <= 0 ? root.track.thumb : ""
        foreground: root.foreground
        fontFamily: root.fontFamily
      }

      Text {
        anchors.centerIn: parent
        visible: root.number > 0
        textFormat: Text.PlainText
        text: String(root.number)
        color: Qt.darker(root.foreground, 1.4)
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }
    }

    Column {
      anchors.verticalCenter: parent.verticalCenter
      width: parent.width - leading.width - trailing.width - parent.spacing * 2
      spacing: Style.space(1)

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: root.track ? root.track.title : ""
        color: root.foreground
        opacity: root.track && root.track.available === false ? 0.5 : 1
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: root.current
        elide: Text.ElideRight
      }

      Row {
        width: parent.width
        spacing: Style.space(4)
        visible: subtitle.text !== "" || explicitBadge.visible || votes.visible

        // S13: the song's net votes on a playlist with voting on ("+3").
        Text {
          id: votes
          anchors.verticalCenter: parent.verticalCenter
          visible: text !== ""
          textFormat: Text.PlainText
          text: root.showVotes ? Model.voteLabel(root.track) : ""
          color: Color.accent
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          font.bold: true
        }

        ExplicitBadge {
          id: explicitBadge
          anchors.verticalCenter: parent.verticalCenter
          visible: !!root.track && root.track.explicit === true
          foreground: root.foreground
          fontFamily: root.fontFamily
        }

        Text {
          id: subtitle
          width: parent.width - (explicitBadge.visible ? explicitBadge.width + parent.spacing : 0)
            - (votes.visible ? votes.width + parent.spacing : 0)
          textFormat: Text.PlainText
          text: Model.itemSubtitle(root.track)
          color: Qt.darker(root.foreground, 1.4)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }
    }

    Item {
      id: trailing
      anchors.verticalCenter: parent.verticalCenter
      width: Math.max(duration.implicitWidth, actions.implicitWidth)
      height: parent.height

      Text {
        id: duration
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        visible: !root.showActions
        textFormat: Text.PlainText
        text: root.track && root.track.durationSec ? Model.formatDuration(root.track.durationSec) : ""
        color: Qt.darker(root.foreground, 1.4)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }

      Row {
        id: actions
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        visible: root.showActions
        spacing: Style.space(2)

        PanelActionButton {
          visible: root.showQueueActions
          iconText: "󰒭"
          tooltipText: Model.withKey("Play next", "playNext")
          foreground: root.foreground
          onClicked: root.playNextRequested()
        }

        PanelActionButton {
          visible: root.showQueueActions
          iconText: "󰐕"
          tooltipText: Model.withKey("Add to queue", "addToQueue")
          foreground: root.foreground
          onClicked: root.addToQueueRequested()
        }

        PanelActionButton {
          visible: root.showMove
          iconText: "󰁝"
          tooltipText: "Move up"
          foreground: root.foreground
          onClicked: root.moveRequested(-1)
        }

        PanelActionButton {
          visible: root.showMove
          iconText: "󰁅"
          tooltipText: "Move down"
          foreground: root.foreground
          onClicked: root.moveRequested(1)
        }

        PanelActionButton {
          visible: root.showRemove
          iconText: "󰅖"
          tooltipText: root.removeTooltip
          foreground: root.foreground
          onClicked: root.removeRequested()
        }

        PanelActionButton {
          visible: root.showMenu
          iconText: "󰇙"
          tooltipText: Model.withKey("More", "menu")
          foreground: root.foreground
          onClicked: root.menuRequested()
        }
      }
    }
  }
}
