import QtQuick
import qs.Commons
// Qt 6.12's own Color singleton hides an unqualified Color: write Commons.Color.
import qs.Commons as Commons
import qs.Ui
import "../YtmModel.js" as Model

// Search field, filter chips and grouped results. Typing searches after a
// short pause; Enter searches right away and moves to the results. P1
// (acct-3): signed in, a pause in typing searches signed out (a signed-in
// search joins the account's search history, as the web's submitted ones
// do), and Enter or a picked suggestion searches with the account; your
// library and uploads are searched on Enter only (a scope or filter picked
// for words only typed waits for Enter too). A typed list's songs get
// their library and Listen again tokens from the song's own menu (m, S).
// S7: All lists every kind of result in YouTube's order under the top
// result, each group ending in Show all (its filter). More holds the other
// filters (a chooser; h / l cycle through all of them), and the scope chip
// on the status line (signed in; also in the M menu) searches your library
// or your uploads. A filtered list loads more as the cursor nears its end.
// An empty field lists your recent and YouTube searches (x removes one,
// from YouTube too); while typing, completions come with rich rows that
// play or open.
Item {
  id: root

  property var service: null
  property QtObject bar: null

  readonly property var store: service ? service.store : null
  readonly property bool signedIn: !!service && service.signedIn
  readonly property color foreground: bar ? bar.foreground : Commons.Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string scope: store ? store.scope : ""
  readonly property var resultRows: store ? Model.flattenSearch(store.result, store.filter, { scope: store.scope }) : []
  // Suggestions show above the results while you type; Tab / Shift+Tab put
  // them in the field. An empty field lists your recent searches.
  // S7: Down from the field keeps them (holdSuggestions) so k reaches the
  // rich rows; typing, Enter, Esc or a search picked lets them go.
  readonly property bool typing: (searchField.activeFocus || holdSuggestions) && searchField.text.trim() !== ""
  property bool holdSuggestions: false
  property int suggestionIndex: -1
  property bool _completing: false
  readonly property var rows: !store ? []
    : searchField.text.trim() === "" ? Model.pastSearchRows(store.recent, signedIn ? store.pastSearches : [], 10)
    : (typing && (suggestionIndex >= 0 || store.suggestionsFor === searchField.text.trim())
        ? Model.typingRows(store.suggestions, suggestionIndex, store.suggestionEntities) : []).concat(resultRows)
  readonly property alias field: searchField
  readonly property alias results: resultList
  readonly property var chips: Model.searchChips(store ? store.filter : "", scope)
  readonly property bool hasMore: !!store && !!store.result && !!store.result.cursor
  // P1: a library or uploads search waits for Enter: the words in the field
  // were typed since the last one (or a scope or filter was picked for them).
  readonly property bool waitsForEnter: !!store && scope !== "" && searchField.text.trim() !== ""
    && searchField.text.trim() !== store.query

  // An album, playlist or artist was chosen: Panel.qml opens it.
  signal openRequested(var item)
  // The field gave up focus (Down, Enter, Esc): the panel takes keys again.
  signal leftField()
  // A row's menu (m, '.', right-click): Panel opens it.
  signal itemMenuRequested(var request)
  // S7: the More filters chooser { title, subtitle, choices, onChoice }: Panel opens it.
  signal chooserRequested(var request)

  // The words in the field, submitted (Enter): searched with the account.
  function runSearch() {
    if (store) store.search(searchField.text, store.filter)
  }

  // A pause in typing (P1): the words are not submitted (Store.search
  // reads them signed out, or waits for Enter in a library or uploads scope).
  function typedSearch() {
    if (store) store.search(searchField.text, store.filter, undefined, false, true)
  }

  // P1: the field holds words nobody submitted: not what the results show,
  // or what they show was read while typing.
  function _wordsTyped() {
    return !store || store.typed || searchField.text.trim() !== store.query
  }

  // A filter for the words in the field; an exact search stays exact. P1:
  // words only typed stay unsubmitted: searched signed out, or (your
  // library or uploads) the filter is set and the search waits for Enter.
  function setFilter(key) {
    if (!store) return
    var same = searchField.text.trim() === store.query
    store.search(searchField.text, key, store.scope, same && store.exact, _wordsTyped())
  }

  // The scope chip and the M menu's scope entries ("", "library", "uploads").
  // P1: words only typed stay unsubmitted: back to YouTube Music they are
  // searched signed out; your library or uploads, chosen, waits for Enter.
  // Submitted words search the scope chosen with the account at once.
  function setScope(key) {
    if (!store) return
    var s = key || ""
    store.search(searchField.text, Model.searchFilterAllowed(store.filter, s) ? store.filter : "", s, false,
                 _wordsTyped())
  }

  function cycleScope() {
    setScope(Model.nextSearchScope(scope))
  }

  // h / l: the filter before or after (All, the chips, then the More ones).
  function horizontal(dx) {
    if (store && scope !== "uploads") setFilter(Model.nextSearchFilter(store.filter, scope, dx))
  }

  // More: Videos, Featured and Community playlists, Podcasts, Episodes, Profiles.
  function openMoreFilters() {
    if (!store || scope === "uploads") return
    root.chooserRequested({ title: "More filters", subtitle: Model.searchScopeTitle(scope),
                            choices: Model.moreFilterChoices(store.filter, scope),
                            onChoice: function(choice) { root.setFilter(choice.id) } })
  }

  function chipClicked(chip) {
    if (chip.more) openMoreFilters()
    else setFilter(chip.key)
  }

  // Set the field without asking for suggestions (IPC search, completions).
  function setText(text) {
    _completing = true
    searchField.text = text
    _completing = false
  }

  // A search that led somewhere (Enter, or something from its results was
  // played or opened) joins the recent searches; half-typed ones don't.
  function rememberSearch() {
    if (store) store.rememberQuery(store.query)
  }

  // A recent search or suggestion was picked: search it right away.
  function searchFor(text) {
    setText(text)
    debounce.stop()
    suggestionIndex = -1
    if (store) {
      store.search(text, store.filter)
      store.rememberQuery(text)
    }
    root.leftField()
  }

  // Search instead for: the words as typed, not YouTube's correction.
  function searchExactly(text) {
    setText(text)
    debounce.stop()
    if (store) store.search(text, store.filter, store.scope, true)
  }

  // Your YouTube searches for the empty field (at most once a minute), asked
  // for only while the field has focus: nothing is fetched for a Search tab
  // that is not in front (the panel opens on Now).
  function refreshPastSearches() {
    if (store && signedIn && searchField.activeFocus && searchField.text.trim() === "") store.loadPastSearches(false)
  }

  function cycleSuggestion(delta) {
    var list = store ? store.suggestions : []
    if (list.length === 0) return
    var i = suggestionIndex + delta
    if (i < 0) i = list.length - 1
    else if (i >= list.length) i = 0
    suggestionIndex = i
    setText(list[i].text)
    searchField.cursorPosition = searchField.text.length
  }

  function activate(index) {
    var row = resultList.rowAt(index)
    if (!row || !service) return
    var item = row.item
    if (item.type === "query") {
      searchFor(item.text)
      return
    }
    if (item.type === "loadMore") {
      if (store) store.searchMore(true)
      return
    }
    if (item.type === "action") {
      if (Model.showAllFilter(item) !== "") setFilter(Model.showAllFilter(item))
      else if (item.id === Model.SEARCH_EXACT) searchExactly(item.query)
      else if (item.id === Model.SEARCH_CORRECTED) searchFor(item.query)
      return
    }
    if (item.type === "message" || item.type === "text") return
    rememberSearch()
    if (item.type === "track") service.playNow(item)
    else root.openRequested(item)
  }

  // n / a: a song, or a whole album or playlist (Service.playItem).
  function playNext(index) {
    var row = resultList.rowAt(index)
    if (!row || !Model.canQueue(row.item) || !service) return
    rememberSearch()
    service.playItem(row.item, "next")
  }

  function addToQueue(index) {
    var row = resultList.rowAt(index)
    if (!row || !Model.canQueue(row.item) || !service) return
    rememberSearch()
    service.playItem(row.item, "queue")
  }

  function menu(index) {
    var row = resultList.rowAt(index)
    _menuWait = null
    menuWait.stop()
    if (!row || Model.menuKind(row.item) === "") return
    var item = liveItem(index)
    // P1: a typed song with no tokens held: its menu opens once its own
    // menu was read (_menuWaited).
    if (_typedSong(index) && !Model.liveTokensOf(item) && _readsMenu(item.videoId)) {
      _menuWait = { videoId: String(item.videoId), place: resultList.placeAt(index), at: Date.now() }
      menuWait.start()
      return
    }
    _openMenu(item, index, false)
  }

  // `late`: the menu waited for the song's own menu; Panel opens it only
  // when nothing else took the panel meanwhile.
  function _openMenu(item, index, late) {
    root.itemMenuRequested({ item: item, ctx: { pageKind: "search" }, owner: root, index: index, late: late })
  }

  // ---- a typed list's songs (P1) ------------------------------------------------------
  // A typed list is read signed out (acct-3), so its songs carry no library
  // or Listen again tokens and never say they are in your library. Signed
  // in, m and S on such a row put back the tokens held for the song
  // (Service.withLiveTokens) and, when none are held, read its own menu
  // (one nowContext, Service.readSongTokens, as for a queue row): m opens
  // the menu once it answered, and S says it is loading. Nothing is
  // searched with the account, so the words stay out of its search history.

  // A song row of a typed list (not a suggestion row above it), signed in.
  function _typedSong(index) {
    var row = resultList.rowAt(index)
    return !!row && !!service && signedIn && !!store && store.typed && index >= rows.length - resultRows.length
      && row.item.type === "track" && !!row.item.videoId && !Model.isUploadTrack(row.item)
  }

  // The row's item; a typed song's with the tokens held for it (Panel's S
  // reads it through this hook too). Null when the index is no row.
  function liveItem(index) {
    var row = resultList.rowAt(index)
    if (!row) return null
    return _typedSong(index) ? service.withLiveTokens(row.item) : row.item
  }

  // True when the song's own menu is being read now (asked here when it was
  // never read, or asked again after a failure).
  function _readsMenu(videoId) {
    var state = service.songMenuState(videoId).state
    return state === "loading" || state === "failed"
  }

  property var _menuWait: null

  // The menu waiting for its song's own menu (menu): it opens on the same
  // row once the read answered, or failed, or 16 s passed (then without
  // Save and Pin). The cursor leaving the row, the field taking the keys,
  // another tab or the row going drops it.
  function _menuWaited() {
    var wait = _menuWait
    var index = wait ? resultList.indexOfPlace(wait.place) : -1
    if (index < 0 || !service || !root.visible || searchField.activeFocus || resultList.cursorIndex !== index) {
      _menuWait = null
      menuWait.stop()
      return
    }
    if (service.songMenuPending(wait.videoId) && Date.now() - wait.at < 16000) return
    _menuWait = null
    menuWait.stop()
    _openMenu(liveItem(index), index, true)
  }

  // S on a typed song with no library control held (Panel.saveCursor): why,
  // and its own menu is read now (Service.songMenuState). "" for other rows.
  function noSaveNotice(index) {
    if (!_typedSong(index)) return ""
    var track = liveItem(index)
    return Model.nowSaveNotice(track, { signedIn: signedIn, menu: service.songMenuState(track.videoId) })
  }

  // After a Save or Pin from a typed song whose outcome is unknown or whose
  // token was stale, the song's own menu is read again (as QueueView's), so
  // the next menu has what YouTube has. Other rows carry their own tokens:
  // no callback, as before.
  function editCallback(item) {
    var videoId = item && item.type === "track" && item.videoId && store && store.typed ? String(item.videoId) : ""
    if (videoId === "") return null
    return function(reply) {
      if (!root.service || !(root.service.unsure(reply) || Model.staleToken(reply))) return false
      return root.service.rereadSongTokens(videoId)
    }
  }

  // M: the scopes (signed in) and More filters (Panel adds signedIn).
  function pageMenu() {
    return { item: null, ctx: { page: true, pageKind: "search", scope: scope, filter: store ? store.filter : "" },
             owner: root, index: -1, title: "Search", subtitle: Model.searchScopeTitle(scope) }
  }

  // An entry of a result's menu ran (Panel calls this): the search led
  // somewhere. A menu closed with Esc remembers nothing, and so does the
  // page menu's scope or More filters (S7).
  function menuChosen(actionId) {
    if (String(actionId).indexOf("search") !== 0) rememberSearch()
  }

  // x on a recent search forgets it; one of your YouTube searches is
  // removed from YouTube too (S7).
  function remove(index) {
    var row = resultList.rowAt(index)
    if (!row || row.item.type !== "query" || !row.item.removable || !store) return
    var item = row.item
    if (Model.forgetQuery(store.recent, item.text).length !== store.recent.length) store.forgetQuery(item.text)
    if (!item.remote || !signedIn) return
    // The rows belong to the text they were suggested for, not to what Tab
    // put in the field since ("" for the empty field's past searches).
    var typed = searchField.text.trim() === "" ? "" : store.suggestionsFor
    store.removeSuggestion(item.text, typed, function(reply) {
      if (reply.ok || !root.service) return
      if (root.service.unsure(reply)) root.service.notify("Not sure the search was removed; refreshed", item.text)
      else root.service.failAction("Couldn't remove that search", reply.error.message)
    })
  }

  // Down and Enter put the cursor on the first result; held suggestions
  // stay above it (S7).
  function leaveField(moveToResults) {
    root.leftField()
    if (!moveToResults || resultList.cursorIndex >= 0) return
    var start = rows.length - resultRows.length - 1
    var at = Model.nextSelectable(rows, start, 1)
    if (at > start && start >= 0) {
      resultList.cursorIndex = at
      resultList.positionViewAtIndex(at, ListView.Contain)
    } else {
      resultList.moveCursor(1)
    }
  }

  // The session ending (signed out, expired, invalid) ends a library or
  // uploads search (Store.leaveScope drops it): the words in the field
  // search YouTube Music again.
  function _accountChanged() {
    refreshPastSearches()
    if (!signedIn && store && store.query === "" && !store.searching && searchField.text.trim() !== "") typedSearch()
  }

  onSignedInChanged: Qt.callLater(_accountChanged)

  TextField {
    id: searchField
    width: parent.width
    placeholderText: root.scope === "library" ? "Search your library"
      : root.scope === "uploads" ? "Search your uploads" : "Search YouTube Music"
    foreground: root.foreground
    font.family: root.fontFamily
    activeFocusOnTab: false  // P1: Tab cycles the suggestions here, never Qt's focus chain

    Component.onCompleted: if (root.store) root.setText(root.store.query)
    onTextChanged: {
      if (!activeFocus) root.holdSuggestions = false
      debounce.restart()
      if (text.trim() === "") root.refreshPastSearches()
      if (root._completing) return
      root.suggestionIndex = -1
      suggestTimer.restart()
    }
    onActiveFocusChanged: {
      if (root.store) root.store.inputFocused = activeFocus
      if (activeFocus) {
        root.holdSuggestions = false
        root.refreshPastSearches()
      }
    }

    Keys.onPressed: function(event) {
      if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
        debounce.stop()
        root.holdSuggestions = false
        root.runSearch()
        root.rememberSearch()
        root.leaveField(true)
        event.accepted = true
      } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
        root.cycleSuggestion(event.key === Qt.Key_Backtab || (event.modifiers & Qt.ShiftModifier) ? -1 : 1)
        event.accepted = true
      } else if (event.key === Qt.Key_Down) {
        root.holdSuggestions = true
        root.leaveField(true)
        event.accepted = true
      } else if (event.key === Qt.Key_Escape) {
        root.holdSuggestions = false
        root.leaveField(false)
        event.accepted = true
      }
    }
  }

  Timer {
    id: debounce
    interval: 350
    onTriggered: root.typedSearch()
  }

  Timer {
    id: suggestTimer
    interval: 120
    onTriggered: if (root.store) root.store.suggest(searchField.text)
  }

  // P1: a typed song's menu waiting for its own menu (_menuWaited).
  Timer {
    id: menuWait
    interval: 100
    repeat: true
    onTriggered: root._menuWaited()
  }

  // All, Songs, Albums, Artists, Playlists, and More (the chosen More filter).
  Flow {
    id: chips
    anchors.top: searchField.bottom
    anchors.topMargin: Style.space(8)
    width: parent.width
    spacing: Style.space(4)

    Repeater {
      model: root.chips

      Button {
        required property var modelData
        text: modelData.title
        iconText: modelData.more ? "󰅀" : ""
        selected: modelData.selected
        enabled: modelData.enabled
        opacity: enabled ? 1 : 0.4
        tooltipText: modelData.more ? "Videos, featured and community playlists, podcasts, episodes, profiles"
          : Model.withKey(modelData.title, "horizontal")
        foreground: root.foreground
        fontSize: Style.font.caption
        onClicked: root.chipClicked(modelData)
      }
    }
  }

  // The status, and the scope chip at its end (signed in).
  Item {
    id: statusRow
    anchors.top: chips.bottom
    anchors.topMargin: Style.space(8)
    width: parent.width
    // root.signedIn, not scopeButton.visible: a child is never visible in a hidden parent.
    visible: statusText.text !== "" || root.signedIn
    height: visible ? Math.max(statusText.implicitHeight, root.signedIn ? scopeButton.implicitHeight : 0) : 0

    Text {
      id: statusText
      anchors.left: parent.left
      anchors.right: root.signedIn ? scopeButton.left : parent.right
      anchors.rightMargin: root.signedIn ? Style.space(8) : 0
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      // P1: a library or uploads search waits for Enter, and says so; an
      // error belongs to the words searched, not to the ones typed since. A
      // typed song's menu waiting for its own menu says so too.
      readonly property bool failed: !!root.store && !root.store.searching && !root._menuWait && !root.waitsForEnter
        && root.store.searchError !== ""
      text: !root.store ? ""
        : root.store.searching ? "Searching…"
        : root._menuWait ? "Loading the song's menu…"
        : root.waitsForEnter ? "Press Enter to search your " + (root.scope === "uploads" ? "uploads" : "library")
        : failed ? root.store.searchError
        : root.store.query === "" ? (root.rows.length > 0 ? ""
            : root.scope === "library" ? "Search the songs, albums and artists in your library."
            : root.scope === "uploads" ? "Search the songs you uploaded."
            : "Search songs, albums, artists, playlists, podcasts and profiles.")
        : Model.searchResultCount(root.store.result) === 0 ? "No results for \"" + root.store.query + "\""
        : Model.spellingStatus(root.store.result)
      color: failed ? Commons.Color.urgent : Qt.darker(root.foreground, 1.5)
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Button {
      id: scopeButton
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      visible: root.signedIn
      iconText: root.scope === "uploads" ? "󰕒" : root.scope === "library" ? "󰉋" : "󰍉"
      text: Model.searchScopeTitle(root.scope)
      selected: root.scope !== ""
      tooltipText: "Searching " + Model.searchScopeTitle(root.scope) + "; click for "
        + Model.searchScopeTitle(Model.nextSearchScope(root.scope)) + " (also in the " + Model.keyHint("pageMenu") + " menu)"
      foreground: root.foreground
      fontSize: Style.font.caption
      onClicked: root.cycleScope()
    }
  }

  ResultList {
    id: resultList
    anchors.top: statusRow.visible ? statusRow.bottom : chips.bottom
    anchors.topMargin: Style.space(8)
    anchors.bottom: parent.bottom
    width: parent.width
    bar: root.bar
    store: root.store
    rows: root.rows
    owner: root
    // Typing or a new search starts a fresh list, as before pages kept
    // their place; Load more keeps it.
    listKey: root.store ? [root.store.filter, root.store.scope, root.store.exact, root.store.query, searchField.text].join("|") : ""
    moreBusy: !!root.store && root.store.searchingMore
    moreError: root.store ? root.store.searchMoreError : ""
    opacity: root.store && root.store.searching ? 0.5 : 1

    onActivated: function(index) { root.activate(index) }
    onPlayNextRequested: function(index) { root.playNext(index) }
    onAddToQueueRequested: function(index) { root.addToQueue(index) }
    onRemoveRequested: function(index) { root.remove(index) }
    // After a failure only an explicit Load more (Enter, a click) tries again.
    onNearEnd: if (root.hasMore && !root.store.searchingMore && root.store.searchMoreError === "") root.store.searchMore(false)
  }
}
