import QtQuick

// A second press confirms: arm(key) returns true only when the same key was
// armed less than `windowMs` ago (delete a playlist, remove from history,
// Not interested). armed(key) tells a label to ask "Really ...?"; a binding
// that calls it follows the gate.
QtObject {
  id: root

  property int windowMs: 4000
  property string armedKey: ""

  function arm(key) {
    var k = String(key)
    if (armedKey === k && _timer.running) {
      reset()
      return true
    }
    armedKey = k
    _timer.restart()
    return false
  }

  function armed(key) {
    return armedKey !== "" && armedKey === String(key)
  }

  function reset() {
    _timer.stop()
    armedKey = ""
  }

  property Timer _timer: Timer {
    interval: root.windowMs
    onTriggered: root.armedKey = ""
  }
}
