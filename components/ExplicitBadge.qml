import QtQuick
import qs.Commons

// The small boxed "E" YouTube Music puts on explicit songs and albums (S3).
Rectangle {
  id: root

  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  readonly property color tint: Qt.darker(foreground, 1.4)

  implicitWidth: label.implicitWidth + Style.space(6)
  implicitHeight: label.implicitHeight + Style.space(2)
  radius: Style.space(2)
  color: "transparent"
  border.width: 1
  border.color: tint

  Text {
    id: label
    anchors.centerIn: parent
    textFormat: Text.PlainText
    text: "E"
    color: root.tint
    font.family: root.fontFamily
    font.pixelSize: Math.round(Style.font.caption * 0.8)
    font.bold: true
  }
}
