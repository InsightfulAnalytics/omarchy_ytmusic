import QtQuick
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// The row menu (the web's three-dot menu), a chooser (which artist of a
// song; later slices: country, quality, tuner) and the key help. Covers the
// panel content while open, like the playlist picker. The menu lists
// Model.menuActions for an item; Panel runs the one chosen. An action with
// `confirm` runs on a second press within 4 s.
Rectangle {
  id: root

  property var service: null
  property QtObject bar: null

  // "" (closed) | "menu" | "chooser" | "help"
  property string mode: ""
  property string title: ""
  property string subtitle: ""
  // menu: the resolved actions, and what they act on: { item, ctx, owner, index }
  property var actions: []
  property var request: null
  // chooser: [{ id, title, icon?, hint?, value }], and what gets the pick
  property var choices: []
  property var _onChoice: null

  readonly property bool open: mode !== ""
  readonly property var store: service ? service.store : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property var rows: mode === "menu" ? Model.actionRows(actions, gate.armedKey)
    : mode === "chooser" ? choices.map(function(c) {
        return { item: { type: "action", id: String(c.id), title: String(c.title || ""), icon: c.icon || "", hint: c.hint || "" } }
      })
    : mode === "help" ? Model.keyHelp()
    : []
  readonly property alias results: resultList
  // No text field: the panel's keys keep working while it is open.
  readonly property bool inputFocused: false

  signal closed()
  // A menu entry was chosen (after the menu closed): Panel runs it.
  signal chosen(var action, var request)
  // A key's row in the help was chosen: Panel runs that key.
  signal keyChosen(string keyId)

  // Opens the menu of `request.item`; false (and nothing opens) when no
  // entry applies.
  function openMenu(request, actions, title, subtitle) {
    if (!actions || actions.length === 0) return false
    gate.reset()
    root.request = request
    root.actions = actions
    root.title = title || ""
    root.subtitle = subtitle || ""
    root.mode = "menu"
    resultList.cursorIndex = -1
    resultList.moveCursor(1)
    return true
  }

  // `onChoice(choice)` runs after the chooser closed.
  function openChooser(title, subtitle, choices, onChoice) {
    if (!choices || choices.length === 0) return false
    gate.reset()
    root.choices = choices
    root._onChoice = onChoice
    root.title = title || ""
    root.subtitle = subtitle || ""
    root.mode = "chooser"
    resultList.cursorIndex = -1
    resultList.moveCursor(1)
    return true
  }

  function openHelp() {
    gate.reset()
    root.title = "Keys"
    root.subtitle = "Enter runs the key under the cursor"
    root.mode = "help"
    resultList.cursorIndex = -1
    resultList.positionViewAtBeginning()
    return true
  }

  function close() {
    if (!open) return
    mode = ""
    actions = []
    choices = []
    request = null
    _onChoice = null
    gate.reset()
    closed()
  }

  function activate(index) {
    var row = resultList.rowAt(index)
    if (!row || row.item.type !== "action") return
    var id = row.item.id
    if (mode === "menu") {
      var action = null
      for (var i = 0; i < actions.length; i++) if (actions[i].id === id) action = actions[i]
      if (!action) return
      // A first press only asks again (the row's title changes).
      if (action.confirm && !gate.arm(id)) return
      var request = root.request
      close()
      chosen(action, request)
    } else if (mode === "chooser") {
      var choice = null
      for (var j = 0; j < choices.length; j++) if (String(choices[j].id) === id) choice = choices[j]
      var onChoice = _onChoice
      close()
      if (choice && onChoice) onChoice(choice)
    } else if (mode === "help") {
      close()
      keyChosen(id.slice(4))
    }
  }

  visible: open
  color: Color.popups.background

  ConfirmGate {
    id: gate
  }

  // Keep clicks from reaching the views underneath.
  MouseArea {
    anchors.fill: parent
    acceptedButtons: Qt.AllButtons
  }

  Item {
    id: menuHeader
    width: parent.width
    height: Math.max(titleColumn.implicitHeight, closeButton.implicitHeight)

    Column {
      id: titleColumn
      width: parent.width - closeButton.width
      spacing: Style.space(2)

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: root.title
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.subtitle
        font.bold: true
        elide: Text.ElideRight
      }

      Text {
        width: parent.width
        visible: text !== ""
        textFormat: Text.PlainText
        text: root.subtitle
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
      tooltipText: Model.withKey("Close", "back")
      foreground: root.foreground
      onClicked: root.close()
    }
  }

  ResultList {
    id: resultList
    anchors.top: menuHeader.bottom
    anchors.topMargin: Style.space(10)
    anchors.bottom: parent.bottom
    width: parent.width
    bar: root.bar
    store: root.store
    rows: root.rows
    // A new menu starts at its top; a confirm press keeps the place.
    listKey: root.mode + "|" + root.title

    onActivated: function(index) { root.activate(index) }
  }
}
