import QtQuick
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// S13: edit your playlist (e on its page, or the page menu's Edit
// playlist), over the panel content like the playlist picker. The title
// and description fields, the privacy chips, then a list: Save, Privacy
// and, under Advanced, Order, where new songs go, Voting, the invite link
// for collaborators (copied, never shown) and Close collaboration. A
// setting YouTube does not report reads "not reported by YouTube" and is
// sent only once chosen; Save sends only what changed. Keys: Tab moves
// title, description, list; Enter in a field saves; in the list j / k,
// Enter, and h / l cycle an option row; Esc leaves a field, then closes.
Rectangle {
  id: root

  property var service: null
  property QtObject bar: null
  // { playlistId, title, description, privacy, collaborators, settings,
  //   done(reply), onSettings(settings) }: CollectionView.editTarget().
  property var target: null
  // What the editor started from (Model.editorInitial), and the options
  // chosen since (privacy, sortOrder, addToTop, voteOption).
  property var initial: null
  property var choices: ({})
  property bool busy: false
  property bool settingsLoading: false
  property string status: ""
  property bool statusError: false
  property int _generation: 0

  readonly property bool open: !!target
  readonly property var store: service ? service.store : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property var current: ({ title: titleField.text, description: descriptionField.text,
                                    privacy: value("privacy"), sortOrder: value("sortOrder"),
                                    addToTop: value("addToTop"), voteOption: value("voteOption") })
  readonly property var rows: open && initial ? Model.editorRows(initial, current, { busy: busy }) : []
  readonly property alias results: resultList
  readonly property bool inputFocused: titleField.activeFocus || descriptionField.activeFocus

  signal closed()
  // A field gave the keys back to the list (Tab, Down, Esc).
  signal leftField()

  // An option's value: the one chosen, else the one it started with.
  function value(key) {
    if (choices[key] !== undefined) return choices[key]
    return initial && initial[key] !== undefined ? initial[key] : null
  }

  function openFor(t) {
    close()
    _generation++
    target = t
    initial = Model.editorInitial(t, t.settings || null)
    choices = ({})
    titleField.text = initial.title
    descriptionField.text = initial.description
    titleField.forceActiveFocus()
    titleField.selectAll()
    loadSettings()
  }

  function close() {
    if (!open) return
    target = null
    initial = null
    choices = ({})
    busy = false
    settingsLoading = false
    status = ""
    statusError = false
    _generation++
    titleField.text = ""
    descriptionField.text = ""
    closed()
  }

  function setStatus(text, error) {
    status = text
    statusError = error === true
  }

  // The owner settings YouTube serves (order, voting, collaboration): the
  // fields and options not touched yet follow them.
  function loadSettings() {
    if (!service || !target) return
    var generation = _generation
    settingsLoading = true
    var sent = service.playlistSettings(target.playlistId, function(reply) {
      if (!root || generation !== root._generation) return
      root.settingsLoading = false
      if (!reply.ok) {
        root.setStatus("Couldn't read the playlist's settings: " + reply.error.message, true)
        return
      }
      var before = root.initial
      var next = Model.editorInitial(root.target, reply.result)
      if (titleField.text === before.title) titleField.text = next.title
      if (descriptionField.text === before.description) descriptionField.text = next.description
      root.initial = next
      if (typeof root.target.onSettings === "function") root.target.onSettings(reply.result)
    })
    if (!sent) settingsLoading = false
  }

  function cycle(key, dx) {
    if (!open || busy || !key) return
    choose(key, Model.nextOption(Model.editOptions(key, initial), value(key), dx))
  }

  function choose(key, choice) {
    if (!open || busy) return
    var next = ({})
    for (var k in choices) next[k] = choices[k]
    next[key] = choice
    choices = next
    setStatus("", false)
  }

  // Save: one editPlaylist with what changed. The page under the editor
  // reads itself again (target.done); an error keeps the editor open, or
  // is a notice when the editor was closed meanwhile (Service reports an
  // unknown outcome itself).
  function save() {
    if (!open || busy || !service || !initial) return
    var changes = Model.editorChanges(initial, current)
    if (changes.problem !== "") {
      setStatus(changes.problem, true)
      return
    }
    if (changes.count === 0) {
      close()
      return
    }
    var t = target
    var svc = service
    var generation = _generation
    busy = true
    setStatus("Saving…", false)
    var sent = service.editPlaylist(t, changes.params, function(reply) {
      if (typeof t.done === "function") t.done(reply)
      if (!root || generation !== root._generation) {
        if (!reply.ok && !svc.unsure(reply)) svc.failAction("Couldn't change the playlist", reply.error.message)
        return
      }
      root.busy = false
      if (reply.ok) {
        root.service.notify("Playlist updated", changes.params.title || t.title || "")
        root.close()
      } else if (root.service.unsure(reply)) {
        root.close()
      } else {
        root.setStatus(reply.error.message, true)
      }
    })
    if (!sent) {
      busy = false
      setStatus("", false)
    }
  }

  // Open the playlist to collaborators and copy the invite link (Service
  // copies it; the editor never shows it). Not without wl-copy: the link
  // would be lost.
  function invite() {
    if (!open || busy || !service) return
    var problem = Model.inviteProblem(initial, current) || service.inviteCopyProblem()
    if (problem !== "") {
      setStatus(problem, true)
      return
    }
    _collaboration(true)
  }

  function closeCollaboration() {
    if (!open || busy || !service) return
    _collaboration(false)
  }

  function _collaboration(on) {
    var t = target
    var svc = service
    var generation = _generation
    busy = true
    setStatus(on ? "Opening the playlist to collaborators…" : "Closing collaboration…", false)
    var handle = function(reply) {
      if (typeof t.done === "function" && (reply.ok || svc.unsure(reply))) t.done(reply)
      if (!root || generation !== root._generation) {
        if (!reply.ok && !svc.unsure(reply))
          svc.failAction(on ? "Couldn't open the playlist to collaborators" : "Couldn't close collaboration",
                         reply.error.message)
        return
      }
      root.busy = false
      if (reply.ok) {
        root.initial = Model.withCollaboration(root.initial, on)
        root.setStatus(on ? "Collaboration on; copying the invite link…" : "Collaboration closed", false)
      } else if (svc.unsure(reply)) {
        root.close()
      } else {
        root.setStatus(reply.error.message, true)
      }
    }
    // Once wl-copy is done (Service's notice says so as well).
    var copied = function(ok) {
      if (!root || generation !== root._generation) return
      if (ok) root.setStatus("Invite link copied: whoever opens it can add songs", false)
      else root.setStatus("Collaboration is on, but the invite link was not copied: get an invite link again", true)
    }
    var sent = on ? service.copyInvite(t, handle, copied) : service.editPlaylist(t, { collaboration: false }, handle)
    if (!sent) {
      busy = false
      setStatus("", false)
    }
  }

  // Enter on a row (and a click).
  function activate(index) {
    var row = resultList.rowAt(index)
    if (!row || !row.item) return
    var id = row.item.id
    if (id === Model.EDITOR_IDS.save) save()
    else if (Model.EDITOR_OPTIONS[id]) cycle(Model.EDITOR_OPTIONS[id], 1)
    else if (id === Model.EDITOR_IDS.invite) invite()
    else if (id === Model.EDITOR_IDS.close) closeCollaboration()
  }

  // h / l (Panel's horizontal hook): cycle the option row under the cursor.
  function horizontal(dx) {
    var row = resultList.rowAt(resultList.cursorIndex)
    if (row && row.item) cycle(Model.EDITOR_OPTIONS[row.item.id], dx)
  }

  // Tab with the keys on the list: into the fields again.
  function tabFocus(direction) {
    if (!open) return
    if (direction < 0) descriptionField.forceActiveFocus()
    else titleField.forceActiveFocus()
  }

  function leaveField() {
    root.leftField()
    if (resultList.cursorIndex < 0) resultList.toTop()
  }

  visible: open
  color: Color.popups.background

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
        text: "Edit playlist"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.subtitle
        font.bold: true
      }

      Text {
        width: parent.width
        textFormat: Text.PlainText
        text: root.target ? String(root.target.title || "") : ""
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
      tooltipText: Model.withKey("Close without saving", "back")
      foreground: root.foreground
      onClicked: root.close()
    }
  }

  Column {
    id: form
    anchors.top: header.bottom
    anchors.topMargin: Style.space(12)
    width: parent.width
    spacing: Style.space(6)

    TextField {
      id: titleField
      width: parent.width
      placeholderText: "Title"
      foreground: root.foreground
      font.family: root.fontFamily

      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
          root.save()
          event.accepted = true
        } else if (event.key === Qt.Key_Tab && !(event.modifiers & Qt.ShiftModifier)) {
          descriptionField.forceActiveFocus()
          event.accepted = true
        } else if (event.key === Qt.Key_Backtab || event.key === Qt.Key_Tab || event.key === Qt.Key_Escape
                   || event.key === Qt.Key_Down) {
          root.leaveField()
          event.accepted = true
        }
      }
    }

    TextField {
      id: descriptionField
      width: parent.width
      placeholderText: "Description"
      foreground: root.foreground
      font.family: root.fontFamily

      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
          root.save()
          event.accepted = true
        } else if (event.key === Qt.Key_Backtab || event.key === Qt.Key_Up
                   || (event.key === Qt.Key_Tab && (event.modifiers & Qt.ShiftModifier))) {
          titleField.forceActiveFocus()
          event.accepted = true
        } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Escape || event.key === Qt.Key_Down) {
          root.leaveField()
          event.accepted = true
        }
      }
    }

    Text {
      width: parent.width
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      text: "YouTube Music cannot clear a description: an empty one keeps the old text."
      color: Qt.darker(root.foreground, 1.6)
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }

    // The privacy chips (the list's Privacy row and h / l on it are their twins).
    Flow {
      width: parent.width
      spacing: Style.space(4)

      Repeater {
        model: Model.PRIVACY_OPTIONS

        Button {
          required property var modelData
          iconText: modelData.icon
          text: modelData.title
          selected: root.value("privacy") === modelData.value
          tooltipText: "Privacy (h / l on the Privacy row)"
          foreground: root.foreground
          fontSize: Style.font.caption
          onClicked: root.choose("privacy", modelData.value)
        }
      }
    }
  }

  Text {
    id: statusText
    anchors.top: form.bottom
    anchors.topMargin: Style.space(8)
    width: parent.width
    visible: text !== ""
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    text: root.status !== "" ? root.status : root.settingsLoading ? "Reading the playlist's settings…" : ""
    color: root.status !== "" && root.statusError ? Color.urgent : Qt.darker(root.foreground, 1.5)
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  ResultList {
    id: resultList
    anchors.top: statusText.visible ? statusText.bottom : form.bottom
    anchors.topMargin: Style.space(8)
    anchors.bottom: parent.bottom
    width: parent.width
    bar: root.bar
    store: root.store
    rows: root.rows
    // The editor's own list: Save and the options stay the same rows.
    listKey: root.target ? "editor:" + root.target.playlistId : ""
    opacity: root.busy ? 0.5 : 1

    onActivated: function(index) { root.activate(index) }
  }
}
