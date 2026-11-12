import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "YtmModel.js" as Model

// Bar entry for damir.ytmusic: a music icon only, no text. Left or right
// click opens the panel, middle click plays/pauses, the wheel skips tracks.
// One instance per monitor; all state lives in the service.
BarWidget {
  id: root
  moduleName: Model.PLUGIN_ID

  // serviceFor() is a plain call, so resolve again until the service exists.
  property var service: null

  readonly property var track: service ? service.currentTrack : null
  readonly property bool hasMedia: service ? service.hasMedia : false
  readonly property bool playing: service ? service.playing : false
  readonly property string tooltip: track ? Model.mediaTitle(track) : "YouTube Music"
  readonly property Item anchorButton: iconButton

  // Shape contract for shell.summon/hide/toggle routing: Bar.findPanelWidget
  // requires open/close/opened on the bar-widget root.
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false

  // Forwarded so this widget can stand in for the panel as the bar's popout
  // identity: Bar.requestPopout prefers closeForPopoutSwitch over close, and
  // KeyboardPanel reads popoutSwitchClosing back off its owner.
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  // The panel is created on first open and destroyed a while after closing,
  // so its rows and cover images don't sit in the shell's memory.
  property bool panelWanted: false
  property bool openWhenLoaded: false
  property bool reportedOpen: false
  property real wheelRemainder: 0
  property real lastWheelAt: 0

  function resolveService() {
    var found = bar && bar.shell && typeof bar.shell.serviceFor === "function"
      ? bar.shell.serviceFor(Model.PLUGIN_ID) : null
    if (found !== service) service = found
  }

  function open() {
    panelWanted = true
    if (panelLoader.item) panelLoader.item.open()
    else openWhenLoaded = true
  }

  function close() {
    openWhenLoaded = false
    if (panelLoader.item) panelLoader.item.close()
  }

  function togglePanel() {
    if (opened) close()
    else open()
  }

  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    target.bar = root.bar
    target.settings = root.settings
    target.anchorItem = root.anchorButton
    target.hostWidget = root
    target.service = root.service
  }

  function handlePress(button) {
    var action = Model.clickAction(button)
    if (action === "playPause") {
      if (!service || !service.playPause()) togglePanel()
    } else {
      togglePanel()
    }
  }

  // One notch, one track; touchpads accumulate, and a short cooldown keeps a
  // fast flick from skipping half the queue.
  function wheel(delta) {
    var result = Util.wheelSteps(wheelRemainder, delta)
    wheelRemainder = result.remainder
    if (result.steps === 0 || !service) return
    var now = Date.now()
    if (now - lastWheelAt < 250) return
    lastWheelAt = now
    if (result.steps > 0) service.previous()
    else service.next()
  }

  function syncOpenReport() {
    if (!service || opened === reportedOpen) return
    reportedOpen = opened
    service.panelOpenChanged(opened)
  }

  implicitWidth: iconButton.implicitWidth
  implicitHeight: iconButton.implicitHeight

  onBarChanged: {
    resolveService()
    injectPanel()
  }
  onSettingsChanged: injectPanel()
  onServiceChanged: {
    injectPanel()
    syncOpenReport()
  }
  onAnchorButtonChanged: injectPanel()
  onOpenedChanged: {
    if (opened) unloadTimer.stop()
    else unloadTimer.restart()
    syncOpenReport()
  }
  Component.onDestruction: if (reportedOpen && service) service.panelOpenChanged(false)

  Timer {
    interval: 250
    repeat: true
    running: root.service === null && root.bar !== null
    onTriggered: root.resolveService()
  }

  Timer {
    id: unloadTimer
    interval: 45000
    onTriggered: {
      if (root.opened) return
      root.panelWanted = false
      // Reclaim the destroyed panel's JS objects now rather than whenever the
      // engine next feels like collecting.
      Qt.callLater(gc)
    }
  }

  Loader {
    id: panelLoader
    active: root.panelWanted
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      if (root.openWhenLoaded) {
        root.openWhenLoaded = false
        item.open()
      }
    }
  }

  BarIconButton {
    id: iconButton
    anchors.fill: parent
    bar: root.bar
    text: "󰝚"
    dimmed: root.hasMedia && !root.playing
    tooltipText: root.tooltip

    onPressed: function(b) { root.handlePress(b) }
    onWheelMoved: function(delta) { root.wheel(delta) }
  }
}
