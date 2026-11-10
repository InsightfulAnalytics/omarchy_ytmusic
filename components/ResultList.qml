import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "../YtmModel.js" as Model

// A flat list of result rows ({ header, title } or { item, trackIndex }):
// section headers, tracks, collections, and the line rows of pages (See all,
// Load more, action, message, and text: page content shown in full). Owns its keyboard cursor, and keeps it and
// the scroll position on the same rows by key when the rows change (an
// append, a background refresh, a removal), unless `listKey` changed.
ListView {
  id: root

  property var rows: []
  // What the list shows (a page's ref key, a search). When it changes, the
  // next rows start a fresh list: cursor off, scrolled to the top.
  property string listKey: ""
  property QtObject bar: null
  property var store: null
  property int cursorIndex: -1
  // Track rows get a remove action (playlist pages, Liked Music).
  property bool removable: false
  // S3: track rows show their album track number instead of the cover (album pages).
  property bool numbered: false
  // S3: the row G lands on instead of the last one (a collection's Load
  // all row, which its shelves follow); -1 for the last row.
  property int endIndex: -1
  property string removeTooltip: "Remove"
  // S13: the page's own songs get move buttons (your playlist; J / K's twins).
  property bool movable: false
  // The Load more row's state, set by the owner.
  property bool moreBusy: false
  property string moreError: ""
  // The view the rows belong to: a right-click (or a row's menu button)
  // opens owner.menu(index) when the owner has one.
  property var owner: null
  readonly property bool hasMenu: !!owner && typeof owner.menu === "function"

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // index = row index in `rows`
  signal activated(int index)
  signal playNextRequested(int index)
  signal addToQueueRequested(int index)
  signal removeRequested(int index)
  signal moveRequested(int index, int delta)
  signal menuRequested(int index)
  // The cursor came within 5 rows of the end, or the list was scrolled to
  // the bottom (debounced), or G was pressed: time to load more.
  signal nearEnd()
  // The shown rows were replaced (after the cursor and scroll were kept).
  signal rowsUpdated()

  // The rows the view shows. `model` follows this, not `rows`, so the place
  // can be read off the old rows before the model resets.
  property var _shown: []
  property string _shownKey: ""

  function moveCursor(delta) {
    cursorIndex = Model.nextSelectable(_shown, cursorIndex, delta)
    if (cursorIndex >= 0) positionViewAtIndex(cursorIndex, ListView.Contain)
  }

  function toTop() {
    cursorIndex = Model.nextSelectable(_shown, -1, 1)
    positionViewAtBeginning()
  }

  function toBottom() {
    var end = endIndex >= 0 && endIndex < _shown.length && Model.selectableRow(_shown[endIndex]) ? endIndex : -1
    cursorIndex = end >= 0 ? end : Model.nextSelectable(_shown, -1, -1)
    if (end >= 0) positionViewAtIndex(end, ListView.Contain)
    else positionViewAtEnd()
    if (_shown.length > 0) root.nearEnd()
  }

  function rowAt(index) {
    return index >= 0 && index < _shown.length && !_shown[index].header ? _shown[index] : null
  }

  // A row's place (its key, and how many rows before it share it), to find
  // it again after the rows changed: indexOfPlace gives its index now, or
  // -1 when it is gone. (A menu acts on the row it was opened for.)
  function placeAt(index) {
    return Model.rowPlace(_shown, index)
  }

  function indexOfPlace(place) {
    return Model.findPlace(_shown, place)
  }

  // The row's menu: the cursor moves onto it first, so the menu and the
  // keys act on the same row.
  function requestMenu(index) {
    if (!Model.selectableRow(_shown[index])) return
    cursorIndex = index
    menuRequested(index)
    if (hasMenu) owner.menu(index)
  }

  // ---- keeping the place --------------------------------------------------------
  // A place is a row's key plus how many rows before it share that key.

  // The row at the top edge and how far it sits from it; null at the top.
  function _anchor() {
    if (_shown.length === 0 || atYBeginning) return null
    var i = indexAt(width / 2, contentY + 1)
    if (i < 0) i = indexAt(width / 2, contentY + spacing + 1)
    var item = i >= 0 ? itemAtIndex(i) : null
    if (!item) return null
    return { place: Model.rowPlace(_shown, i), offset: item.y - contentY }
  }

  function _restoreAnchor(anchor) {
    var i = anchor ? Model.findPlace(_shown, anchor.place) : -1
    if (i < 0) {
      positionViewAtBeginning()
      return
    }
    positionViewAtIndex(i, ListView.Beginning)
    var y = contentY - anchor.offset
    var last = originY + Math.max(0, contentHeight - height)
    contentY = Math.max(originY, Math.min(y, last))
  }

  function _update() {
    var next = rows || []
    if (next === _shown) return
    var old = _shown
    var same = listKey === _shownKey
    var anchor = same ? _anchor() : null
    var cursor = same ? Model.cursorAfterRows(old, next, cursorIndex) : -1
    _shownKey = listKey
    _shown = next
    cursorIndex = cursor
    if (same) _restoreAnchor(anchor)
    else positionViewAtBeginning()
    rowsUpdated()
  }

  onRowsChanged: _update()
  Component.onCompleted: _update()

  // For Back: where the cursor and the top edge were, by place.
  function viewState() {
    return { cursor: cursorIndex >= 0 ? Model.rowPlace(_shown, cursorIndex) : null, anchor: _anchor() }
  }

  function restoreView(state) {
    if (!state) return
    var i = state.cursor ? Model.findPlace(_shown, state.cursor) : -1
    cursorIndex = i >= 0 && Model.selectableRow(_shown[i]) ? i : -1
    _restoreAnchor(state.anchor)
  }

  // The row under the keyboard cursor or the pointer, once it rests there a
  // moment, is a likely pick: its stream gets looked up in advance.
  property int _hintIndex: -1

  function hintRow(index) {
    _hintIndex = index
    hintTimer.restart()
  }

  onCursorIndexChanged: {
    if (cursorIndex >= 0) hintRow(cursorIndex)
    endTimer.restart()
  }
  onContentYChanged: endTimer.restart()

  Timer {
    id: hintTimer
    interval: 350
    onTriggered: {
      var row = root.rowAt(root._hintIndex)
      if (row && root.store) root.store.hint(row.item)
    }
  }

  Timer {
    id: endTimer
    interval: 150
    onTriggered: {
      var count = root._shown.length
      if (count === 0) return
      var nearCursor = root.cursorIndex >= 0 && root.cursorIndex >= count - 5
      var atBottom = root.atYEnd && root.contentHeight > root.height
      if (nearCursor || atBottom) root.nearEnd()
    }
  }

  // Glyphs of the line rows.
  function lineIcon(item) {
    if (item.icon) return item.icon
    if (item.type === "more") return "󰁔"
    if (item.type === "loadMore") return root.moreBusy ? "󰔟" : "󰅀"
    if (item.type === "message") return "󰋽"
    return "󰅂"
  }

  function lineText(item) {
    if (item.type === "loadMore") {
      if (root.moreBusy) return item.busyTitle ? String(item.busyTitle) : "Loading more…"
      if (root.moreError !== "") return "Couldn't load more: " + root.moreError
    }
    return String(item.title || "")
  }

  clip: true
  boundsBehavior: Flickable.StopAtBounds
  spacing: Style.space(2)
  model: root._shown

  ScrollBar.vertical: ScrollBar {
    policy: ScrollBar.AsNeeded
  }

  delegate: Loader {
    id: row

    required property var modelData
    required property int index

    readonly property string kind: modelData.header ? "header" : modelData.item ? String(modelData.item.type || "") : ""

    width: root.width
    sourceComponent: kind === "header" ? headerComponent
      : kind === "track" ? trackComponent
      : kind === "query" ? queryComponent
      : kind === "more" || kind === "loadMore" || kind === "action" || kind === "message" || kind === "text" ? lineComponent
      : collectionComponent

    Component {
      id: headerComponent

      Text {
        topPadding: row.index === 0 ? 0 : Style.space(10)
        bottomPadding: Style.space(4)
        textFormat: Text.PlainText
        text: String(row.modelData.title).toUpperCase()
        color: Qt.darker(root.foreground, 1.4)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        font.letterSpacing: 1
      }
    }

    Component {
      id: trackComponent

      TrackRow {
        bar: root.bar
        store: root.store
        track: row.modelData.item
        cursor: row.index === root.cursorIndex
        showQueueActions: true
        // S13: a suggestion is no entry of the playlist: neither removed nor moved.
        showRemove: root.removable && row.modelData.suggestion !== true
        showMove: root.movable && row.modelData.trackIndex !== undefined && row.modelData.suggestion !== true
        showVotes: true
        showMenu: root.hasMenu
        removeTooltip: root.removeTooltip
        number: root.numbered && row.modelData.item.trackNumber > 0 ? row.modelData.item.trackNumber : 0
        onActivated: root.activated(row.index)
        onPlayNextRequested: root.playNextRequested(row.index)
        onAddToQueueRequested: root.addToQueueRequested(row.index)
        onRemoveRequested: root.removeRequested(row.index)
        onMoveRequested: function(delta) { root.moveRequested(row.index, delta) }
        onMenuRequested: root.requestMenu(row.index)
        onHoveredChanged: if (hovered) root.hintRow(row.index)
      }
    }

    // A recent search or suggestion: history or search icon, text, and a
    // remove action on recent searches.
    Component {
      id: queryComponent

      Item {
        id: queryRow
        readonly property var query: row.modelData.item
        readonly property bool highlighted: row.index === root.cursorIndex || query.selected === true

        implicitHeight: Style.space(32)

        Rectangle {
          anchors.fill: parent
          radius: Style.spacing.labelGap
          color: queryHover.hovered || queryRow.highlighted ? Style.hoverFillFor(root.foreground, Color.accent) : "transparent"
        }

        HoverHandler {
          id: queryHover
        }

        MouseArea {
          anchors.fill: parent
          cursorShape: Qt.PointingHandCursor
          onClicked: root.activated(row.index)
        }

        Row {
          anchors.fill: parent
          anchors.leftMargin: Style.space(10)
          anchors.rightMargin: Style.space(8)
          spacing: Style.space(10)

          Text {
            id: queryIcon
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: queryRow.query.recent ? "󰋚" : "󰍉"
            color: Qt.darker(root.foreground, 1.4)
            font.family: root.fontFamily
            font.pixelSize: Style.font.icon
          }

          Text {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - queryIcon.width - removeQuery.width - parent.spacing * 2
            textFormat: Text.PlainText
            text: queryRow.query.text
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            elide: Text.ElideRight
          }

          PanelActionButton {
            id: removeQuery
            anchors.verticalCenter: parent.verticalCenter
            opacity: queryRow.query.removable && (queryHover.hovered || row.index === root.cursorIndex) ? 1 : 0
            enabled: opacity > 0
            iconText: "󰅖"
            tooltipText: Model.withKey("Forget this search", "remove")
            foreground: root.foreground
            onClicked: root.removeRequested(row.index)
          }
        }
      }
    }

    // See all, Load more and action rows (selectable, in the query-row
    // layout; an action row may show its key as a hint on the right),
    // message rows (plain text, never selected) and text rows (page content
    // shown in full, without a line cap: a credited name or About the artist
    // with its icon, never selected, so the list scrolls over it and G shows
    // its end; or a description's paragraph, without an icon, which the
    // cursor rests on so the keyboard can scroll through, but which does
    // nothing).
    Component {
      id: lineComponent

      Item {
        id: lineRow
        readonly property var line: row.modelData.item
        readonly property bool isText: line.type === "text"
        readonly property bool selectable: line.type !== "message" && (!isText || line.paragraph === true)
        readonly property bool highlighted: selectable && row.index === root.cursorIndex

        implicitHeight: Math.max(Style.space(32), lineLabel.implicitHeight + Style.space(12))

        Rectangle {
          anchors.fill: parent
          radius: Style.spacing.labelGap
          color: lineRow.selectable && (lineHover.hovered || lineRow.highlighted)
            ? Style.hoverFillFor(root.foreground, Color.accent) : "transparent"
        }

        HoverHandler {
          id: lineHover
          enabled: lineRow.selectable && !lineRow.isText
        }

        MouseArea {
          anchors.fill: parent
          enabled: lineRow.selectable && !lineRow.isText
          cursorShape: Qt.PointingHandCursor
          onClicked: root.activated(row.index)
        }

        Text {
          id: lineIcon
          anchors.left: parent.left
          anchors.leftMargin: Style.space(10)
          anchors.verticalCenter: parent.verticalCenter
          visible: !lineRow.isText || !!lineRow.line.icon
          textFormat: Text.PlainText
          text: root.lineIcon(lineRow.line)
          color: Qt.darker(root.foreground, 1.4)
          font.family: root.fontFamily
          font.pixelSize: Style.font.icon
        }

        Text {
          id: lineHint
          anchors.right: parent.right
          anchors.rightMargin: Style.space(10)
          anchors.verticalCenter: parent.verticalCenter
          visible: text !== ""
          textFormat: Text.PlainText
          text: lineRow.line.hint ? String(lineRow.line.hint) : ""
          color: Qt.darker(root.foreground, 1.6)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        Text {
          id: lineLabel
          anchors.left: lineIcon.visible ? lineIcon.right : parent.left
          anchors.leftMargin: Style.space(10)
          anchors.right: lineHint.visible ? lineHint.left : parent.right
          anchors.rightMargin: Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: root.lineText(lineRow.line)
          color: lineRow.selectable || lineRow.isText ? root.foreground : Qt.darker(root.foreground, 1.4)
          font.family: root.fontFamily
          font.pixelSize: lineRow.selectable || lineRow.isText ? Style.font.body : Style.font.bodySmall
          wrapMode: lineRow.selectable && !lineRow.isText ? Text.NoWrap : Text.Wrap
          // undefined resets the cap: a text row has none.
          maximumLineCount: lineRow.isText ? undefined : lineRow.selectable ? 1 : 4
          elide: lineRow.isText ? Text.ElideNone : Text.ElideRight
        }
      }
    }

    Component {
      id: collectionComponent

      CollectionRow {
        bar: root.bar
        store: root.store
        item: row.modelData.item
        cursor: row.index === root.cursorIndex
        showMenu: root.hasMenu
        onActivated: root.activated(row.index)
        onMenuRequested: root.requestMenu(row.index)
      }
    }
  }
}
