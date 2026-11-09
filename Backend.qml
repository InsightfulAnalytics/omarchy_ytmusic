import QtQuick
import Quickshell
import Quickshell.Io

// The ytmusicapi worker (backend/ytm.py serve) as a request/callback API.
// Started on the first request; the worker exits by itself after
// `idleSeconds` without requests or when its stdin closes (this object going
// away), so it costs nothing while you are not browsing. A request that dies
// with a running worker is retried once on a fresh one, except edits
// (opts.retry === false, or a method the worker lists as mutating): those
// fail with error.unknown = true, as does an edit that times out, because it
// may have been applied. Callers then refresh what it touched.
Item {
  id: root

  // bin/ytm: runs backend/ytm.py in its uv environment without a uv parent.
  property string launcher: ""
  property int idleSeconds: 180
  // First start may build the uv environment; later starts take ~0.2 s.
  property int startTimeoutMs: 60000

  // stopped | starting | ready | stopping (said it will exit: new requests
  // wait for the next start instead of going to a worker that won't read them)
  property string workerState: "stopped"
  // unknown | none | ok | invalid | expired (as reported by the worker)
  property string auth: "unknown"
  // When a request last failed with NETWORK (ms since the epoch; 0 = never).
  property real lastNetworkErrorAt: 0
  // Settings for the worker's `configure` method, written before anything
  // else after every start (and again whenever they change). null = none.
  property var configureParams: null

  // Unsolicited worker lines other than ready/idle-exit (e.g. "thumbs").
  signal eventReceived(var message)

  property int _nextId: 1
  property var _pending: ({})
  property var _queue: []
  property real _startDeadline: 0
  property real _stoppingSince: 0
  // Methods the worker reported as mutating in its ready event.
  property var _mutates: []

  // opts.retry === false: never re-send after a worker exit (edits).
  function request(method, params, callback, timeoutMs, opts) {
    releaseTimer.stop()
    var id = _nextId++
    _pending[id] = {
      method: method, params: params || {}, callback: callback || null,
      timeoutMs: timeoutMs || 20000, deadline: 0, retried: false,
      noRetry: !!opts && opts.retry === false
    }
    _dispatch(id)
    return id
  }

  function _noRetry(pending) {
    return pending.noRetry || _mutates.indexOf(pending.method) !== -1
  }

  function _configure() {
    if (!configureParams || workerState !== "ready") return
    var id = _nextId++
    _pending[id] = {
      method: "configure", params: configureParams, timeoutMs: 10000, deadline: Date.now() + 10000,
      retried: false, noRetry: false, internal: true,
      callback: function(reply) {
        if (!reply.ok) console.warn("ytmusic: configure failed: " + reply.error.message)
      }
    }
    proc.write(JSON.stringify({ id: id, method: "configure", params: configureParams }) + "\n")
    sweep.start()
  }

  onConfigureParamsChanged: _configure()

  function _dispatch(id) {
    var pending = _pending[id]
    if (!pending) return
    if (workerState === "ready") {
      pending.deadline = Date.now() + pending.timeoutMs
      proc.write(JSON.stringify({ id: id, method: pending.method, params: pending.params }) + "\n")
      sweep.start()
    } else {
      _queue.push(id)
      _start()
      sweep.start()
    }
  }

  function _start() {
    if (workerState !== "stopped") return
    workerState = "starting"
    _startDeadline = Date.now() + startTimeoutMs
    proc.running = true
    sweep.start()
  }

  function _finish(id, reply) {
    var pending = _pending[id]
    if (!pending) return
    delete _pending[id]
    if (!pending.callback) return
    try {
      pending.callback(reply)
    } catch (e) {
      console.warn("ytmusic: callback for " + pending.method + " failed: " + e)
    }
  }

  // unknown: the request reached the worker and may have been applied.
  function _fail(id, code, message, unknown) {
    var error = { code: code, message: message, retryable: code === "TIMEOUT" && !unknown }
    if (unknown) error.unknown = true
    _finish(id, { id: id, ok: false, error: error, unknown: !!unknown })
  }

  function _failQueued(code, message) {
    var queued = _queue
    _queue = []
    for (var i = 0; i < queued.length; i++) _fail(queued[i], code, message)
  }

  function _handleLine(line) {
    var message
    try {
      message = JSON.parse(line)
    } catch (e) {
      console.warn("ytmusic worker: unexpected output: " + line)
      return
    }
    if (message.event === "ready") {
      workerState = "ready"
      auth = message.auth || "none"
      _mutates = message.mutates || []
      _configure()
      var queued = _queue
      _queue = []
      for (var i = 0; i < queued.length; i++) _dispatch(queued[i])
      return
    }
    if (message.event) {
      if (message.event === "idle-exit") _stopping()
      else eventReceived(message)
      return
    }
    if (message.ok && message.result && typeof message.result.auth === "string") auth = message.result.auth
    else if (!message.ok && message.error && message.error.code === "AUTH_EXPIRED") auth = "expired"
    if (!message.ok && message.error && message.error.code === "NETWORK") lastNetworkErrorAt = Date.now()
    _finish(message.id, message)
  }

  function _stopping() {
    workerState = "stopping"
    _stoppingSince = Date.now()
  }

  function _onExited(code) {
    var wasStarting = workerState === "starting"
    workerState = "stopped"
    if (wasStarting) {
      // Never came up (uv missing, broken environment): don't loop.
      console.warn("ytmusic worker failed to start (exit " + code + ")")
      _failQueued("WORKER_EXITED", "The YouTube Music helper could not start")
      return
    }
    for (var key in _pending) {
      var id = Number(key)
      if (_queue.indexOf(id) !== -1) continue
      if (_pending[key].internal) {
        delete _pending[key]  // configure: sent again on the next start
      } else if (_noRetry(_pending[key])) {
        _fail(id, "WORKER_EXITED", "The YouTube Music helper stopped before confirming the change", true)
      } else if (_pending[key].retried) {
        _fail(id, "WORKER_EXITED", "The YouTube Music helper stopped unexpectedly")
      } else {
        _pending[key].retried = true
        _pending[key].deadline = 0
        _queue.push(id)
      }
    }
    if (_queue.length > 0) _start()
  }

  Process {
    id: proc
    command: [root.launcher, "serve", "--idle", String(root.idleSeconds)]
    // Nothing may be written into the watched plugin directory.
    environment: ({ PYTHONDONTWRITEBYTECODE: "1", PYTHONUNBUFFERED: "1" })
    stdinEnabled: true

    stdout: SplitParser {
      onRead: data => root._handleLine(data)
    }

    stderr: SplitParser {
      onRead: data => console.warn("ytmusic worker: " + data)
    }

    onExited: (exitCode, exitStatus) => root._onExited(exitCode)
  }

  // After background-only work, let the worker go after `ms` instead of its
  // full idle timeout. Any new request cancels this.
  function release(ms) {
    releaseTimer.interval = ms
    releaseTimer.restart()
  }

  Timer {
    id: releaseTimer
    onTriggered: {
      if (root.workerState !== "ready" || root._queue.length > 0 || Object.keys(root._pending).length > 0) return
      proc.write(JSON.stringify({ id: 0, method: "shutdown" }) + "\n")
      root._stopping()
    }
  }

  Timer {
    id: sweep
    interval: 1000
    repeat: true
    onTriggered: {
      var now = Date.now()
      if (root.workerState === "starting" && now > root._startDeadline) {
        console.warn("ytmusic worker did not become ready in time")
        root._failQueued("TIMEOUT", "The YouTube Music helper took too long to start")
        proc.running = false
      }
      // It said it would exit; if it hasn't, end it so queued requests can start a new one.
      if (root.workerState === "stopping" && now - root._stoppingSince > 5000) proc.running = false
      var any = root._queue.length > 0
      for (var key in root._pending) {
        var pending = root._pending[key]
        if (pending.deadline > 0 && pending.deadline < now)
          root._fail(Number(key), "TIMEOUT", pending.method + " timed out", root._noRetry(pending))
        else any = true
      }
      if (!any && root.workerState !== "starting") sweep.stop()
    }
  }
}
