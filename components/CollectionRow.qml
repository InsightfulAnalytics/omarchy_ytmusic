import QtQuick
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// An album, playlist or artist in a list; clicking opens it, a right-click
// (or the menu button shown on hover) asks for its menu.
Item {
  id: root

  property var item: null
  property bool cursor: false
  property QtObject bar: null
  property var store: null
  property bool showMenu: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool isArtist: item && item.type === "artist"

  signal activated()
  signal menuRequested()

  implicitHeight: Style.space(50)

  Rectangle {
    anchors.fill: parent
    radius: Style.spacing.labelGap
    color: hover.hovered || root.cursor ? Style.hoverFillFor(root.foreground, Color.accent) : "transparent"
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

    CoverArt {
      id: cover
      anchors.verticalCenter: parent.verticalCenter
      size: Style.space(38)
      radius: root.isArtist ? size / 2 : Math.max(2, Math.round(size * 0.1))
      store: root.store
      source: root.item && root.item.thumb ? root.item.thumb : ""
      foreground: root.foreground
      fontFamily: root.fontFamily
    }

    Column {
      anchors.verticalCenter: parent.verticalCenter
      width: parent.width - cover.width - trailing.width - parent.spacing * 2
      spacing: Style.space(1)

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: root.item ? (root.item.title || root.item.name || "") : ""
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        elide: Text.ElideRight
      }

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: Model.itemSubtitle(root.item)
        visible: text !== ""
        color: Qt.darker(root.foreground, 1.4)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
      }
    }

    Item {
      id: trailing
      anchors.verticalCenter: parent.verticalCenter
      width: Math.max(chevron.implicitWidth, menuButton.implicitWidth)
      height: parent.height

      Text {
        id: chevron
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        visible: !menuButton.visible
        textFormat: Text.PlainText
        text: "󰅂"
        color: Qt.darker(root.foreground, 1.6)
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }

      PanelActionButton {
        id: menuButton
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        visible: root.showMenu && (hover.hovered || root.cursor)
        iconText: "󰇙"
        tooltipText: Model.withKey("More", "menu")
        foreground: root.foreground
        onClicked: root.menuRequested()
      }
    }
  }
}
