import QtQuick
import Quickshell
import Quickshell.Io
import "YtmModel.js" as Model

// Single service instance for damir.ytmusic. Playback state is mirrored from
// the detached mpv player (which owns the queue and per-track metadata, so
// both survive plugin reloads); library/search calls go to the ytmusicapi
// worker. Bar widgets, one per monitor, reach this via
// bar.shell.serviceFor("damir.ytmusic").
Item {
  id: root

  property var shell: null
  property var manifest: null

  readonly property string pluginId: Model.PLUGIN_ID
  readonly property string version: manifest && manifest.version ? String(manifest.version) : "0.0.0"

  // The shell strips manifest.__sourceDir for third-party plugins, so the
  // plugin locates its own files relative to this QML file instead.
  readonly property string rootDir: localPath(Qt.resolvedUrl("."))

  // When this instance was created; tells hot-reloaded instances apart.
  readonly property real startedAt: Date.now()

  // ---- playback state (mirrored from mpv) -------------------------------------
  readonly property string mpvState: mpv.mpvState
  property bool paused: false
  property bool idle: true
  property bool buffering: false
  property int playlistPos: -1
  property int playlistCount: 0
  property string mediaTitle: ""
  property real duration: 0
  property real position: 0
  property int volume: 100
  property var playlist: []
  // videoId -> Track; mirrored into mpv's user-data/ytm/meta.
  property var meta: ({})

  // ---- player modes -------------------------------------------------------------
  // Saved in ~/.cache/omarchy-ytmusic/player.json. Repeat lives in mpv
  // (loop-file / loop-playlist, so MPRIS sees it too) and is mirrored here;
  // the saved value is applied whenever a fresh player starts.
  property bool autoplay: true
  property bool shuffle: false
  property string repeatPref: "off"
  property bool prefsLoaded: false
  // Every saved preference (Model.parsePrefs v2: player modes plus the keys
  // later features read, such as libraryChip, chartsCountry, nowPane). A new
  // object on each change, so bindings on prefs.<key> follow; change keys
  // with setPref(key, value).
  property var prefs: Model.parsePrefs("")
  property var _loopFile: false
  property var _loopPlaylist: false
  readonly property string repeatMode: mpv.up ? Model.repeatMode(_loopFile, _loopPlaylist) : repeatPref
  // mpv entry ids in their order before shuffling; mirrored into mpv's
  // user-data/ytm/order so turning shuffle off works after a plugin reload.
  property var shuffleOrder: []
  property bool _reordering: false

  // videoId -> { url, expires }: streams resolved ahead of time by the worker,
  // mirrored into mpv's user-data/ytm/streams where mpv/ytm-streams.lua plays
  // them (~0.3 s to audio instead of ~2 s through yt-dlp).
  property var streams: ({})
  property var _resolving: ({})
  property int resolvingCount: 0
  // A "play now" waiting for its stream: { token, title, run }.
  property var _pendingPlay: null
  property int _playToken: 0
  readonly property string pendingTitle: _pendingPlay ? _pendingPlay.title : ""
  property string lastError: ""

  // Each entry with its origin, "user" or "auto" (S12: queueOrigins).
  readonly property var queue: Model.withOrigins(Model.buildQueue(playlist, meta), queueOrigins)
  // S12: a trim holds it while it updates the queue and position (_trimHeld).
  readonly property var currentEntry: _trimHeld ? _trimHeld
    : playlistPos >= 0 && playlistPos < queue.length ? queue[playlistPos] : null
  readonly property var currentTrack: currentEntry ? currentEntry.track : null
  readonly property bool hasMedia: mpv.up && !idle && currentEntry !== null
  readonly property bool playing: hasMedia && !paused

  // Open panels (one per monitor at most). The position is only polled while
  // one shows it; otherwise time-pos would wake the shell every second.
  property int panelsOpen: 0
  readonly property bool positionWanted: panelsOpen > 0

  readonly property string workerState: backend.workerState

  // ---- account ----------------------------------------------------------------
  // The shell only ever reads account.json (display name/handle/photo). The
  // session itself stays in files only the worker and mpv read.
  readonly property string configDir: (Quickshell.env("XDG_CONFIG_HOME") || (Quickshell.env("HOME") + "/.config")) + "/omarchy-ytmusic"
  property var account: null
  // signedOut | ok | expired | invalid
  readonly property string authState: account === null ? "signedOut"
    : backend.auth === "expired" ? "expired"
    : backend.auth === "invalid" ? "invalid"
    : "ok"
  readonly property bool signedIn: authState === "ok"
  property bool expiryNotified: false
  readonly property string currentLike: currentTrack ? storeItem.likeStatus(currentTrack.videoId, currentTrack.likeStatus) : ""

  property var _lookedUp: ({})

  function localPath(url) {
    var path = String(url || "")
    if (path.indexOf("file://") === 0) path = decodeURIComponent(path.slice(7))
    return path.replace(/\/+$/, "")
  }

  function fail(message) {
    lastError = message
    console.warn("ytmusic: " + message)
  }

  // ---- queue actions ---------------------------------------------------------------

  function remember(tracks) {
    var next = ({})
    for (var key in meta) next[key] = meta[key]
    var changed = false
    for (var i = 0; i < tracks.length; i++) {
      var track = tracks[i]
      if (track && track.videoId) {
        next[track.videoId] = track
        changed = true
      }
    }
    if (!changed) return
    meta = next
    _noteRemembered(tracks)  // S12
    metaSync.restart()
    storeItem.requestThumbs(Model.collectThumbs(tracks))
  }

  function playNow(track) {
    if (!track || !track.videoId) return false
    // The latest thing started wins over a Play or radio still loading.
    _replaceToken++
    // S12: into an empty queue it starts a new one, from nothing in particular.
    if (playlistCount === 0) _newQueue(null)
    remember([track])
    withStream(track, function() {
      mpv.commands(Model.playNowCommands(track, { count: root.playlistCount, pos: root.playlistPos }))
    })
    return true
  }

  // ---- streams -------------------------------------------------------------------

  function nowSec() {
    return Date.now() / 1000
  }

  function streamFor(videoId) {
    var stream = streams[videoId]
    return Model.streamFresh(stream, nowSec()) ? stream : null
  }

  function resolveStream(videoId, callback) {
    if (!videoId) return
    if (streamFor(videoId)) {
      if (callback) callback(true)
      return
    }
    if (_resolving[videoId]) {
      if (callback) _resolving[videoId].push(callback)
      return
    }
    _resolving[videoId] = callback ? [callback] : []
    resolvingCount++
    backend.request("resolve", { videoId: videoId }, function(reply) {
      var waiting = root._resolving[videoId] || []
      delete root._resolving[videoId]
      root.resolvingCount = Math.max(0, root.resolvingCount - 1)
      if (reply.ok) root.addStream(videoId, reply.result)
      else console.warn("ytmusic: no stream for " + videoId + ": " + reply.error.message)
      for (var i = 0; i < waiting.length; i++) waiting[i](reply.ok)
      root.releaseWorkerIfIdle()
    }, 20000)
  }

  // ---- likely picks -------------------------------------------------------------------
  // While a panel is open, the stream of what you'll probably play next (a
  // search's first song, the row under the cursor or pointer) is looked up in
  // the background, so Enter or a click starts in ~0.3 s instead of ~1.5 s.
  // One lookup at a time (the latest hint wins), at most 30 per opening.
  property string _speculating: ""
  property var _speculateNext: null
  property int speculated: 0

  function speculate(track) {
    if (panelsOpen === 0 || !track || !track.videoId || track.available === false) return
    if (streamFor(track.videoId) || _resolving[track.videoId]) return
    if (_speculating !== "") {
      _speculateNext = track
      return
    }
    if (speculated >= 30) return
    speculated++
    _speculating = track.videoId
    resolveStream(track.videoId, function() {
      root._speculating = ""
      var next = root._speculateNext
      root._speculateNext = null
      if (next) root.speculate(next)
    })
  }

  // Background pre-resolving shouldn't keep the worker (~80 MiB with yt-dlp
  // loaded) around for its full idle timeout when nobody is browsing.
  function releaseWorkerIfIdle() {
    if (panelsOpen === 0 && !_pendingPlay && resolvingCount === 0) backend.release(20000)
  }

  function addStream(videoId, stream) {
    var next = Model.pruneStreams(streams, nowSec(), 50)
    next[videoId] = { url: stream.url, expires: stream.expires }
    streams = next
    pushStreams()
  }

  function dropStreams(ids) {
    var next = ({})
    for (var key in streams) if (ids.indexOf(key) === -1) next[key] = streams[key]
    streams = next
  }

  // Sent before any loadfile that needs them: commands on the socket run in order.
  function pushStreams() {
    if (mpv.up) mpv.command(["set_property", "user-data/ytm/streams", Model.pruneStreams(streams, nowSec(), 50)])
  }

  // Run `start` once the track's stream is resolved (or after a failure or
  // 10 s, when mpv resolves it through yt-dlp itself). Only the latest runs.
  function withStream(track, start) {
    if (streamFor(track.videoId)) {
      pushStreams()
      start()
      return
    }
    var token = ++_playToken
    _pendingPlay = { token: token, title: track.title || "", run: start }
    pendingFallback.restart()
    resolveStream(track.videoId, function() { root.runPendingPlay(token) })
  }

  function runPendingPlay(token) {
    if (!_pendingPlay || _pendingPlay.token !== token) return
    var run = _pendingPlay.run
    _pendingPlay = null
    pendingFallback.stop()
    run()
  }

  function preResolveUpcoming() {
    if (repeatMode === "one") return
    var ids = Model.streamsToResolve(queue, playlistPos, streams, nowSec(), 1, repeatMode === "all")
    for (var i = 0; i < ids.length; i++) resolveStream(ids[i])
  }

  // ---- shuffle and repeat ------------------------------------------------------------

  // player.json version 2 (version 1 files read the same). Nothing is
  // written before the file has been read, so an early toggle never wipes
  // the keys saved there.
  function savePrefs() {
    var next = Model.withPref(prefs, "autoplay", autoplay)
    next = Model.withPref(next, "shuffle", shuffle)
    prefs = Model.withPref(next, "repeat", repeatPref)
    if (prefsLoaded) prefsFile.setText(Model.serializePrefs(prefs))
  }

  // Copy on write; invalid values become the key's default. The player
  // modes go through their own setters (they also drive mpv).
  function setPref(key, value) {
    if (!Model.isPrefKey(key)) return false
    if (key === "autoplay") return setAutoplay(value)
    if (key === "shuffle") return setShuffle(value)
    if (key === "repeat") return setRepeat(value)
    prefs = Model.withPref(prefs, key, value)
    savePrefs()
    return true
  }

  function applyPrefs(text) {
    var saved = Model.parsePrefs(text)
    prefs = saved
    autoplay = saved.autoplay
    shuffle = saved.shuffle
    repeatPref = saved.repeat
    prefsLoaded = true
    applyRepeat()
  }

  // A player that just started has mpv's defaults (no looping).
  function applyRepeat() {
    if (prefsLoaded && mpv.up) mpv.commands(Model.repeatCommands(repeatPref))
  }

  function setRepeat(mode) {
    repeatPref = Model.normalizeRepeat(mode)
    savePrefs()
    applyRepeat()
    return true
  }

  // off → all → one → off
  function cycleRepeat() {
    return setRepeat(Model.nextRepeat(repeatMode))
  }

  function setShuffleOrder(ids) {
    shuffleOrder = ids
    if (mpv.up) mpv.command(["set_property", "user-data/ytm/order", ids])
  }

  // On: the songs after the current one play in random order. Off: they go
  // back to the order they had (songs added meanwhile keep their places).
  function setShuffle(on) {
    if (_reordering) return false
    shuffle = !!on
    savePrefs()
    if (!mpv.up || playlistCount < 2) {
      setShuffleOrder([])
      return true
    }
    _reordering = true
    mpv.command(["get_property", "playlist"], function(reply) {
      root._reordering = false
      if (!reply || reply.error !== "success" || !Array.isArray(reply.data)) return
      var ids = reply.data.map(function(entry) { return entry.id })
      var pos = -1
      for (var i = 0; i < reply.data.length; i++) if (reply.data[i].current) pos = i
      if (root.shuffle) {
        root.setShuffleOrder(ids)
        mpv.commands(Model.reorderCommands(ids, Model.shuffleOrder(ids, pos, Date.now())))
      } else {
        mpv.commands(Model.reorderCommands(ids, Model.unshuffleOrder(ids, root.shuffleOrder)))
        root.setShuffleOrder([])
      }
      // Moves change neither playlist-count nor playlist-pos, so nothing
      // observed would announce them.
      root.refreshQueue()
    })
    return true
  }

  function toggleShuffle() {
    return setShuffle(!shuffle)
  }

  // ---- autoplay ------------------------------------------------------------------------
  // S12: a few seconds into a song with Model.AUTOPLAY_LEFT (3) or fewer
  // songs after it, up to 10 new ones are appended as radio for whatever
  // plays (refillAutoplay, Model.autoplaySources), so the music keeps going
  // as on music.youtube.com: songs before videos, nothing queued or played
  // already. Not while repeating.
  readonly property bool autoplayWanted: autoplay && repeatMode === "off" && hasMedia
    && Model.topUpNeeded(playlistCount, playlistPos, Model.AUTOPLAY_LEFT)
  // The song and queue length the last top-up started from.
  property string _autoplaySeed: ""

  onAutoplayWantedChanged: {
    // A trim passes through states with no current song (S12): no new song.
    if (_trimShift) return
    if (autoplayWanted) autoplayTimer.restart()
    else autoplayTimer.stop()
  }

  function setAutoplay(on) {
    autoplay = !!on
    autoplayNote = ""  // S12
    savePrefs()
    return true
  }

  function toggleAutoplay() {
    return setAutoplay(!autoplay)
  }

  // One top-up per song and queue length: the chosen tuner chip's queue,
  // else the song's nowContext items (no request) and upNext for the
  // context; if every pick of those is a duplicate, the song's own radio
  // once; then "Autoplay found nothing new".
  function refillAutoplay() {
    var seed = currentTrack
    if (!autoplayWanted || !seed || !seed.videoId) return
    var key = seed.videoId + "|" + playlistCount
    if (key === _autoplaySeed) return
    _autoplaySeed = key
    var memo = storeItem.nowContextOf(seed.videoId, false)
    var sources = Model.autoplaySources(seed.videoId, tuner, memo, playContext ? playContext.playlistId : null)
    _topUp(seed, sources, _replaceToken, memo, false)
  }

  onPlaylistPosChanged: {
    preResolve.restart()
    sessionSave.restart()
    // S12: each song with few left tries a top-up; played ones are trimmed
    // (a trim's own shift of the index is no new song).
    if (_trimShift) return
    if (autoplayWanted) autoplayTimer.restart()
    trimTimer.restart()
  }
  onPlaylistChanged: {
    preResolve.restart()
    sessionSave.restart()
  }
  onPausedChanged: if (paused) saveSession()

  // ---- resume ----------------------------------------------------------------------
  // The queue, position and volume are saved to ~/.cache/omarchy-ytmusic/
  // session.json, so play/pause can bring everything back after mpv quit
  // (idle timeout, logout, reboot).
  readonly property string cacheDir: (Quickshell.env("XDG_CACHE_HOME") || (Quickshell.env("HOME") + "/.cache")) + "/omarchy-ytmusic"
  property var session: null
  readonly property bool canResume: !mpv.up && !!session && session.videoIds.length > 0
  readonly property string resumeTitle: {
    if (!session || !session.meta) return ""
    var track = session.meta[session.videoIds[session.index]]
    return track ? Model.mediaTitle(track) : ""
  }

  function saveSession() {
    if (!mpv.up || playlistPos < 0 || queue.length === 0) return
    mpv.command(["get_property", "time-pos"], function(reply) {
      var pos = reply && typeof reply.data === "number" ? reply.data : root.position
      var snapshot = Model.sessionSnapshot(root.queue, root.playlistPos, pos, root.volume, Date.now())
      root.session = snapshot
      sessionFile.setText(JSON.stringify(snapshot) + "\n")
    })
  }

  function resume() {
    if (!canResume) return false
    var plan = Model.resumeCommands(session)
    if (plan.tracks.length === 0) return false
    _replaceToken++
    if (typeof session.volume === "number") volume = session.volume
    remember(plan.tracks)
    // The snapshot keeps the queue as it was (shuffled or not), so turning
    // shuffle off afterwards keeps that order.
    withStream(plan.tracks[plan.index], function() {
      mpv.commands(plan.commands)
      root.setShuffleOrder([])
    })
    return true
  }

  // ---- playback error notices --------------------------------------------------------
  // Delayed a few seconds: mpv/ytm-streams.lua silently retries an expired
  // pre-resolved stream, and a retry that works is not worth a notification.
  property int consecutiveErrors: 0
  property var _notifiedAt: ({})
  property var _pendingNotice: null

  function notifyOnce(key, title, body, windowMs) {
    var now = Date.now()
    var last = _notifiedAt[key]
    if (last && now - last < (windowMs || 600000)) return
    var next = ({})
    for (var k in _notifiedAt) next[k] = _notifiedAt[k]
    next[key] = now
    _notifiedAt = next
    Quickshell.execDetached(["omarchy-notification-send", "--app-name", "YouTube Music", title, body || ""])
  }

  // Where play/pause continues after failures stopped playback: the first
  // track of the current run of failures.
  property int _retryIndex: -1

  function queueErrorNotice(title, detail) {
    consecutiveErrors++
    var offline = backend.lastNetworkErrorAt > 0 && Date.now() - backend.lastNetworkErrorAt < 120000
    _pendingNotice = Model.playbackNotice(consecutiveErrors, title, detail, offline)
    errorNotice.restart()
  }

  function playNext(track) {
    if (!track || !track.videoId) return false
    remember([track])
    mpv.commands(Model.playNextCommands(track))
    return true
  }

  function addToQueue(track) {
    if (!track || !track.videoId) return false
    remember([track])
    // S12: before autoplay's upcoming picks, as on the web.
    var at = _queueAt()
    mpv.commands(at >= 0 ? Model.queueAtCommands([track], at).commands : Model.addToQueueCommands(track))
    return true
  }

  // Replace the queue with `tracks`, starting at `start`. With shuffle mode
  // on, that song plays first and the rest is shuffled. `shuffleAll` (the
  // Shuffle button) turns shuffle mode on and starts anywhere. At most
  // Model.QUEUE_CAP songs go in (from `start` on; a random pick when
  // shuffling all), with a notice. `context` is what plays now
  // ({ playlistId, title, radio }); null when unknown. `partial`
  // ({ total }) says `tracks` is only the start of a longer list.
  // S12: `autoFrom` (Start radio) marks the tracks from that index on as
  // autoplay entries (a tuner chip may replace them).
  function playCollection(tracks, start, shuffleAll, context, partial, autoFrom) {
    // The latest thing started wins over a Play or radio still loading.
    _replaceToken++
    if (shuffleAll && !shuffle) {
      shuffle = true
      savePrefs()
    }
    var random = shuffle
    var list = tracks || []
    var autoIds = autoFrom >= 0 ? _idsOf(list.slice(autoFrom)) : null  // S12
    if (shuffleAll && list.length > Model.QUEUE_CAP) list = Model.shuffled(list.filter(Model.playable), Date.now())
    var cut = Model.capWindow(list, start || 0, Model.QUEUE_CAP)
    var plan = Model.playCollectionCommands(cut.tracks, cut.start, random, Date.now(), !shuffleAll)
    if (plan.tracks.length === 0) return false
    noticeCut(plan.tracks.length, cut, partial)
    _newQueue(context || null)  // S12
    remember(plan.tracks)
    withStream(plan.tracks[plan.index], function() {
      root._sendQueue(plan.commands, autoIds)  // S12
      if (!random) {
        root.setShuffleOrder([])
        return
      }
      mpv.command(["get_property", "playlist"], function(reply) {
        if (reply && reply.error === "success" && Array.isArray(reply.data))
          root.setShuffleOrder(Model.originalOrder(reply.data.map(function(entry) { return entry.id }), plan.originalIndex))
      })
    })
    return true
  }

  // At the end of the queue, at most Model.QUEUE_CAP. A `title` (a whole
  // album or playlist) is named in a notification. `partial` as in
  // playCollection.
  function queueAll(tracks, title, partial) {
    var cut = Model.capWindow(tracks || [], 0, Model.QUEUE_CAP)
    var plan = Model.queueAtCommands(cut.tracks, _queueAt())  // S12: before autoplay's picks
    if (plan.tracks.length === 0) return false
    remember(plan.tracks)
    mpv.commands(plan.commands)
    if (!noticeCut(plan.tracks.length, cut, partial) && title)
      notify("Added to queue", Model.joinParts([title, songCount(plan.tracks.length)]))
    return true
  }

  // ---- play anything (F3) ------------------------------------------------------------
  // Rows of every kind play, queue, shuffle and start radio here: a song
  // directly, an album, playlist, mix, Liked Music or artist once its
  // tracks are read (itemTracks).

  // What plays now, as the web's "Playing from": { playlistId, title, radio }
  // or null. Set whenever the queue is replaced (playCollection, startRadio).
  property var playContext: null
  // Only the latest start replaces the queue: a Play, Shuffle or Start radio
  // still loading gives way to any later one, and to a song or list started
  // directly (playNow, playCollection, resume all count this up).
  property int _replaceToken: 0
  property string _copying: ""

  function songCount(n) {
    return n === 1 ? "1 song" : n + " songs"
  }

  function noticeCap(kept, total) {
    var notice = Model.queueCapNotice(kept, total)
    notify(notice.title, notice.body)
  }

  // The cap notice when not all of a list went in (Model.capNotice); true
  // when it was shown.
  function noticeCut(kept, cut, partial) {
    var notice = Model.capNotice(kept, cut, partial)
    if (notice) notify(notice.title, notice.body)
    return !!notice
  }

  // A menu action that could not run: the Now tab shows it, and so does a
  // notification (the action was chosen on another tab).
  function failAction(title, message) {
    fail(title + ": " + message)
    notify(title, message)
  }

  // callback({ ok, tracks, playlistId, total, complete }) or an error reply.
  // A collection the Store holds in full is used as it is; anything else is
  // read by the worker (tracksFor, at most `limit` songs, else 5000; up to
  // 120 s). `complete` is false when the list has more.
  function itemTracks(item, callback, limit) {
    var kind = Model.menuKind(item)
    if (kind === "track") {
      callback({ ok: true, tracks: [item], playlistId: null, total: 1, complete: true })
      return
    }
    var ref = Model.pageRef(item)
    var held = ref ? storeItem.cachedCollection(ref) : null
    var tracks = Model.completeTracks(held)
    if (tracks && tracks.length > 0) {
      callback({ ok: true, tracks: tracks, playlistId: held.playlistId || null, total: tracks.length, complete: true })
      return
    }
    var params = Model.tracksForParams(item)
    if (!params) {
      callback({ ok: false, error: { code: "BAD_REQUEST", message: "Nothing to play here" } })
      return
    }
    if (limit > 0) params.limit = limit
    backend.request("tracksFor", params, function(reply) {
      if (!reply.ok) {
        callback(reply)
        return
      }
      var result = reply.result
      callback({ ok: true, tracks: result.items || [], playlistId: result.playlistId || null,
                 total: result.total, complete: result.complete === true })
    }, 120000)
  }

  // mode: "now" | "shuffle" replace the queue; "next" | "queue" insert.
  // `partial` ({ total }): `tracks` is only the start of the list.
  function playTracks(tracks, mode, context, title, partial) {
    if (mode === "next") return playNextAll(tracks, title, partial)
    if (mode === "queue") return queueAll(tracks, title, partial)
    return playCollection(tracks, 0, mode === "shuffle", context, partial)
  }

  // n / a / Play / Shuffle on any row, and every header Play on a page that
  // holds only part of its list. Play, Play next and Add to queue read only
  // the songs the queue takes (Model.QUEUE_CAP); Shuffle picks from all.
  function playItem(item, mode) {
    var kind = Model.menuKind(item)
    if (kind === "") return false
    if (kind === "track") {
      if (mode === "next") return playNext(item)
      if (mode === "queue") return addToQueue(item)
      return playNow(item)
    }
    var replaces = mode !== "next" && mode !== "queue"
    var token = replaces ? ++_replaceToken : _replaceToken
    var title = Model.itemTitle(item)
    itemTracks(item, function(result) {
      if (replaces && token !== root._replaceToken) return
      if (!result.ok) {
        root.failAction("Couldn't play " + (title || "this"), result.error.message)
        return
      }
      if (result.tracks.length === 0) {
        root.failAction("Couldn't play " + (title || "this"), "It has no songs")
        return
      }
      root.playTracks(result.tracks, mode, { playlistId: result.playlistId, title: title, radio: false }, title,
                      result.complete ? null : { total: result.total })
    }, mode === "shuffle" ? 0 : Model.QUEUE_CAP)
    return true
  }

  // Many songs right after the current one, in their order (at most
  // Model.QUEUE_CAP). `partial` as in playCollection.
  function playNextAll(tracks, title, partial) {
    var cut = Model.capWindow(tracks || [], 0, Model.QUEUE_CAP)
    var plan = Model.playNextAllCommands(cut.tracks, { count: playlistCount, pos: playlistPos })
    if (plan.tracks.length === 0) return false
    remember(plan.tracks)
    mpv.commands(plan.commands)
    if (!noticeCut(plan.tracks.length, cut, partial) && title)
      notify("Playing next", Model.joinParts([title, songCount(plan.tracks.length)]))
    return true
  }

  // Start radio, like the web: the queue is replaced by the item's radio and
  // it plays. From the song that is playing, that song plays on and only
  // what follows it is replaced.
  function startRadio(item) {
    var params = Model.radioParams(item)
    if (!params) return false
    var token = ++_replaceToken
    var seed = Model.menuKind(item) === "track" ? item : null
    var title = Model.itemTitle(item)
    backend.request("radio", params, function(reply) {
      if (token !== root._replaceToken) return
      if (!reply.ok) {
        root.failAction("Couldn't start radio", reply.error.message)
        return
      }
      var items = reply.result.items || []
      // S12: named as YouTube names it ("Karma Police Mix"); its songs are
      // autoplay entries (a tuner chip replaces them, as on the web).
      var context = { playlistId: reply.result.playlistId || null, title: reply.result.playingFrom || title, radio: true }
      var current = root.currentTrack
      if (seed && root.hasMedia && current && current.videoId === seed.videoId) {
        var plan = Model.queueAllCommands(Model.capWindow(items, 0, Model.QUEUE_CAP).tracks)
        root.remember(plan.tracks)
        root._sendQueue([["playlist-clear"]].concat(plan.commands), root._idsOf(plan.tracks))
        root.setShuffleOrder([])
        root._newQueue(context)
        return
      }
      var list = seed ? [seed].concat(items.filter(function(t) { return t.videoId !== seed.videoId })) : items
      if (!root.playCollection(list, 0, false, context, undefined, seed ? 1 : 0))
        root.failAction("Couldn't start radio", "No songs came back")
    }, 30000)
    return true
  }

  // Copy link: the item's music.youtube.com URL on the clipboard (wl-copy).
  function copyLink(item) {
    var url = Model.shareUrl(item)
    if (url === "") return false
    if (missingTools.indexOf("wl-copy") >= 0) {
      failAction("Couldn't copy the link", "wl-copy is missing (" + installCommand + ")")
      return false
    }
    _copying = url
    copier.running = false
    copier.command = ["wl-copy", url]
    copier.running = true
    return true
  }

  // callback(reply) with result { playlistId, browseId }: the album page of
  // an audio playlist (OLAK5uy_...), cached by the worker.
  function albumBrowseIdOf(playlistId, callback) {
    backend.request("albumBrowseId", { playlistId: playlistId, cache: "prefer" }, callback, 30000)
  }

  Process {
    id: copier
    onExited: function(exitCode) {
      if (exitCode === 0) root.notify("Link copied", root._copying)
      else root.failAction("Couldn't copy the link", "wl-copy failed")
    }
  }

  // ---- account actions ------------------------------------------------------------

  function signInCommand() {
    return "'" + rootDir + "/bin/ytm' auth"
  }

  function signIn() {
    Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", signInCommand()])
    return true
  }

  // `ytm logout` deletes the session files and asks this service to reload.
  function signOut() {
    Quickshell.execDetached([rootDir + "/bin/ytm", "logout"])
    return true
  }

  function setAccount(text) {
    var parsed = null
    try {
      parsed = text ? JSON.parse(text) : null
    } catch (e) {
      parsed = null
    }
    var changed = JSON.stringify(parsed) !== JSON.stringify(account)
    account = parsed
    if (changed) applyAuthChange()
  }

  function applyAuthChange() {
    storeItem.reset()
    expiryNotified = false
    if (backend.workerState === "stopped") backend.auth = account ? "unknown" : "none"
    else backend.request("reloadAuth", ({}))
  }

  // Called by `ytm auth` / `ytm logout` over IPC after they change the files.
  function reloadAuth() {
    accountFile.reload()
    applyAuthChange()
    return true
  }

  function notify(title, body) {
    Quickshell.execDetached(["omarchy-notification-send", "--app-name", "YouTube Music", "-t", "2500", title, body || ""])
  }

  // ---- edits -------------------------------------------------------------------------
  // Edits go to the worker with retry: false. When one fails with
  // error.unknown (the worker exited or timed out after it was sent), it may
  // have been applied: refresh what it touched instead of rolling back.
  readonly property var noRetry: ({ retry: false })

  function unsure(reply) {
    return !!reply && !reply.ok && !!reply.error && reply.error.unknown === true
  }

  // touched: [{ kind: "playlist" | "liked" | "library" | "like", id }]
  function refreshUnsure(touched, body) {
    for (var i = 0; i < touched.length; i++) {
      var t = touched[i]
      if (t.kind === "library") storeItem.loadLibrary(true)
      else if (t.kind === "like") refreshLikeOf(t.id)
      else storeItem.forgetCollection(t.kind, t.id)
    }
    notify("Not sure the change applied; refreshed", body || "")
  }

  onAuthStateChanged: {
    if ((authState === "expired" || authState === "invalid") && !expiryNotified) {
      expiryNotified = true
      Quickshell.execDetached(["omarchy-notification-send", "--app-name", "YouTube Music",
        "YouTube Music sign-in expired", "Click to sign in again (or use the Library tab).",
        "--exec", "omarchy-launch-floating-terminal-with-presentation", signInCommand()])
    }
  }

  // ---- likes -------------------------------------------------------------------------

  function likeStatusOf(track) {
    return track ? storeItem.likeStatus(track.videoId, track.likeStatus) : null
  }

  // callback(reply) is optional; it runs after the store has been updated.
  function setLike(track, like, callback) {
    if (!track || !track.videoId) return false
    if (!signedIn) {
      fail("Sign in to like songs")
      return false
    }
    var previous = likeStatusOf(track)
    var rating = like ? "LIKE" : "INDIFFERENT"
    storeItem.setLike(track.videoId, rating)
    root._rateSent(track.videoId)  // S11
    backend.request("rate", { videoId: track.videoId, rating: rating }, function(reply) {
      root._rateAnswered(track.videoId)  // S11
      if (root.unsure(reply)) {
        root.refreshUnsure([{ kind: "liked", id: "LM" }, { kind: "like", id: track.videoId }], Model.mediaTitle(track))
      } else if (!reply.ok) {
        storeItem.setLike(track.videoId, previous)
        root.fail(reply.error.message)
      } else {
        storeItem.forgetCollection("liked", "LM")
        root.notify(like ? "Liked" : "Removed from Liked Music", Model.mediaTitle(track))
      }
      if (callback) callback(reply)
    }, undefined, noRetry)
    return true
  }

  function toggleLike(track) {
    return setLike(track, likeStatusOf(track) !== "LIKE")
  }

  // After an unsure rating (refreshUnsure).
  function refreshLikeOf(videoId) {
    backend.request("likeStatus", { videoId: videoId }, function(reply) {
      if (reply.ok && reply.result.likeStatus) storeItem.setLike(videoId, reply.result.likeStatus)
    })
  }

  // ---- now context (S11) ------------------------------------------------------------
  // One nowContext request per song while a panel is open (a moment after
  // it starts, so skipping through the queue asks nothing): its like status
  // goes to the Store's likes, and the whole reply (lyrics and Related ids,
  // the song/video twin, the queue's chips, "Playing from", YouTube's up-next
  // list) to the Store's memo for 10 min, reused when the song comes back.
  // S12 reads the memo for autoplay.
  readonly property var nowContext: currentTrack ? storeItem.nowContextOf(currentTrack.videoId, false) : null
  // { videoId, message } when the last nowContext of that song failed.
  property var nowContextFailure: null
  property string _nowFetching: ""
  // videoId -> ratings sent and not answered yet (setLike counts them). A
  // nowContext runs on the worker's pages lane, beside a rating in flight
  // on its main thread, so it may read the status from before that rating.
  property var _ratesOut: ({})

  function _rateSent(videoId) {
    var next = ({})
    for (var k in _ratesOut) next[k] = _ratesOut[k]
    next[videoId] = (next[videoId] || 0) + 1
    _ratesOut = next
  }

  function _rateAnswered(videoId) {
    var next = ({})
    for (var k in _ratesOut) if (k !== videoId) next[k] = _ratesOut[k]
    if (_ratesOut[videoId] > 1) next[videoId] = _ratesOut[videoId] - 1
    _ratesOut = next
  }

  function refreshNowContext() {
    var track = currentTrack
    if (!positionWanted || !track || !track.videoId) return
    var videoId = track.videoId
    if (storeItem.nowContextOf(videoId, true) || _nowFetching === videoId) return
    _nowFetching = videoId
    nowContextFailure = null
    // A rating wins over the status this brings back: one still out when
    // it is sent, one sent while it is out (still out, or already applied
    // to the Store's likes) or a rating's rollback.
    var likeBefore = storeItem.likeStatus(videoId)
    var ratingOut = !!_ratesOut[videoId]
    backend.request("nowContext", { videoId: videoId }, function(reply) {
      if (root._nowFetching === videoId) root._nowFetching = ""
      if (!reply.ok) {
        root.nowContextFailure = { videoId: videoId, message: reply.error.message }
        return
      }
      storeItem.rememberNowContext(videoId, reply.result)
      var like = reply.result.likeStatus
      if (root.signedIn && like && !ratingOut && !root._ratesOut[videoId] && storeItem.likeStatus(videoId) === likeBefore)
        storeItem.setLike(videoId, like)
    }, 15000)
  }

  onCurrentTrackChanged: if (positionWanted) nowCheck.restart()
  // Signing in or out empties the memo: ask again for the song playing.
  onNowContextChanged: if (!nowContext && positionWanted) nowCheck.restart()

  // ---- up next (S12) ----------------------------------------------------------------
  // The queue as the web's Up next (Model's "up next" section). Every mpv
  // entry autoplay, Start radio or a tuner chip added is "auto" in
  // queueOrigins (by mpv playlist entry id, read from loadfile's reply);
  // the rest are the user's. "Playing from" is the playContext's title, else
  // the song's nowContext name; the one tuner chip ("Tune: All", t) cycles
  // the song's nowContext chips and replaces only the upcoming auto entries.
  // Played entries more than 50 behind the current one leave mpv (their ids
  // stay in `_seen` for autoplay's dedupe until a new queue starts), and
  // `meta` keeps only what is queued. Origins, context, tuner and seen ids
  // are kept in queue.json for the player they belong to (mpv's pid), so a
  // hot reload keeps them.
  readonly property string queueStatePath: cacheDir + "/queue.json"
  // "<entry id>" -> { videoId, origin: "auto" }
  property var queueOrigins: ({})
  property var _originsPending: []
  // The chosen tuner chip { title, playlistId, params }; null is YouTube's own (All).
  property var tuner: null
  readonly property var tunerChips: Model.tunerChips(nowContext)
  readonly property string tunerTitle: Model.tunerTitle(tuner, tunerChips)
  readonly property string playingFrom: Model.playingFrom(playContext, nowContext)
  // "Autoplay found nothing new" (the queue view shows it), else "".
  property string autoplayNote: ""
  // Songs trimmed out of this queue, oldest first (Model.SEEN_MAX); a new
  // queue starts over, as on the web.
  property var _seen: []
  property var _tuneChip: null
  property int _tuneToken: 0
  property bool _tuning: false
  property bool _trimShift: false
  // The current entry while a trim updates playlist, count and position one
  // at a time (currentEntry): no other song is current in between.
  property var _trimHeld: null
  property var _trimTarget: null
  // videoId -> when remember() last stored it (its loadfile may still be on the way).
  property var _rememberedAt: ({})
  property real _mpvPid: 0
  property var _savedQueueState: null
  property bool _queueStateRead: false

  onPlayContextChanged: _queueStateChanged()

  // A trim took the first `count` entries out: every row index after them
  // is `count` less (QueueView keeps its cursor on the same song).
  signal trimmed(int count)

  // videoId -> true for `tracks`.
  function _idsOf(tracks) {
    var ids = ({})
    for (var i = 0; i < (tracks || []).length; i++) if (tracks[i] && tracks[i].videoId) ids[tracks[i].videoId] = true
    return ids
  }

  // A new queue: what it plays from, no tuner, no note, nothing seen.
  function _newQueue(context) {
    playContext = context
    _seen = []
    tuner = null
    _tuneChip = null
    autoplayNote = ""
    _autoplaySeed = ""
    _queueStateChanged()
  }

  // Where added songs go: before the first upcoming auto entry, else the end (-1).
  function _queueAt() {
    return hasMedia ? Model.queueInsertAt(queue, playlistPos) : -1
  }

  // Sends `commands`; a loadfile of a song in `autoIds` records its new
  // entry (mpv's reply names its id) as an autoplay entry.
  function _sendQueue(commands, autoIds) {
    for (var i = 0; i < commands.length; i++) {
      var command = commands[i]
      var videoId = command[0] === "loadfile" ? Model.videoIdFromUrl(command[1]) : ""
      if (videoId && autoIds && autoIds[videoId]) mpv.command(command, _originReply(videoId))
      else mpv.command(command)
    }
  }

  function _originReply(videoId) {
    return function(reply) {
      var id = reply && reply.error === "success" && reply.data ? reply.data.playlist_entry_id : undefined
      if (typeof id !== "number") return
      root._originsPending = root._originsPending.concat([{ entryId: id, videoId: videoId, origin: "auto" }])
      Qt.callLater(root._flushOrigins)
    }
  }

  function _flushOrigins() {
    if (_originsPending.length === 0) return
    queueOrigins = Model.addOrigins(queueOrigins, _originsPending)
    _originsPending = []
    _queueStateChanged()
  }

  function _noteRemembered(tracks) {
    var now = Date.now()
    var next = ({})
    for (var key in _rememberedAt) next[key] = _rememberedAt[key]
    for (var i = 0; i < tracks.length; i++) if (tracks[i] && tracks[i].videoId) next[tracks[i].videoId] = now
    _rememberedAt = next
  }

  // After every playlist refresh: origins of entries that left go, and so
  // does the metadata of songs neither queued nor remembered in the last 2 min.
  function _queueRefreshed() {
    _flushOrigins()
    var origins = Model.pruneOrigins(queueOrigins, playlist)
    if (origins !== queueOrigins) {
      queueOrigins = origins
      _queueStateChanged()
    }
    var now = Date.now()
    var recent = ({})
    for (var id in _rememberedAt) if (now - _rememberedAt[id] < 120000) recent[id] = _rememberedAt[id]
    _rememberedAt = recent
    var kept = Model.trimMeta(meta, playlist, recent)
    if (kept !== meta) meta = kept
  }

  // mpv's playlist as it is now, with its current index, for edits by
  // index (a trim or a move may have shifted what the queue binding shows).
  // fn(null, -1) when mpv did not answer.
  function _withSnapshot(fn) {
    mpv.command(["get_property", "playlist"], function(reply) {
      if (!reply || reply.error !== "success" || !Array.isArray(reply.data)) {
        fn(null, -1)
        return
      }
      root._flushOrigins()
      var pos = -1
      for (var i = 0; i < reply.data.length; i++) if (reply.data[i].current) pos = i
      fn(Model.withOrigins(Model.buildQueue(reply.data, root.meta), root.queueOrigins), pos)
    })
  }

  function _queueEnded() {
    return autoplay && repeatMode === "off" && idle && playlistCount > 0
  }

  // Walks the top-up's sources (Model.autoplaySources) until one gives new
  // songs. `failed`: a source failed on the way (then no "found nothing").
  function _topUp(seed, sources, token, memo, failed) {
    if (token !== _replaceToken || (!autoplayWanted && !_queueEnded())) return
    if (sources.length === 0) {
      if (failed) _autoplaySeed = ""  // the next song tries again
      else _autoplayFoundNothing()
      return
    }
    var step = sources[0]
    var rest = sources.slice(1)
    if (step.items) {
      if (!_appendAuto(step.items, memo)) _topUp(seed, rest, token, memo, failed)
      return
    }
    backend.request(step.method, step.params, function(reply) {
      root.releaseWorkerIfIdle()
      if (token !== root._replaceToken || (!root.autoplayWanted && !root._queueEnded())) return
      if (!reply.ok) {
        console.warn("ytmusic: autoplay: " + reply.error.message)
        root._topUp(seed, rest, token, memo, true)
        return
      }
      if (!root._appendAuto(reply.result.items || [], step.method === "upNext" ? reply.result : null))
        root._topUp(seed, rest, token, memo, failed)
    }, 20000)
  }

  // Appends autoplay's picks of `items` (false when none is new). A queue
  // started from nothing in particular then plays from the radio they came
  // from (`source`: { playlistId, playingFrom }), like the web.
  function _appendAuto(items, source) {
    var picks = Model.autoplayPicks(queue, items, Model.AUTOPLAY_BATCH, _seen)
    if (picks.length === 0) return false
    remember(picks)
    _sendQueue(Model.queueAllCommands(picks).commands, _idsOf(picks))
    autoplayNote = ""
    if (!playContext && source && source.playlistId && source.playingFrom)
      playContext = { playlistId: source.playlistId, title: source.playingFrom, radio: true }
    return true
  }

  function _autoplayFoundNothing() {
    autoplayNote = "Autoplay found nothing new"
    notifyOnce("autoplay-nothing", "Autoplay found nothing new", "The queue ends after the songs in it.")
  }

  // t or the tune chip: the next chip (`delta` -1: the one before), applied
  // a moment later so a few quick presses send one request.
  function cycleTuner(delta) {
    var chip = Model.nextTuner(tunerChips, tuner, delta)
    if (!chip || !hasMedia) return false
    _setTuner(chip)
    tuneApply.restart()
    return true
  }

  // A chip from the Now page menu's chooser.
  function chooseTuner(chip) {
    if (!chip || !chip.playlistId || !hasMedia) return false
    _setTuner(chip)
    tuneApply.stop()
    _applyTuner()
    return true
  }

  // YouTube's own chip (All) is no tuner: autoplay goes back to its usual sources.
  function _setTuner(chip) {
    _tuneChip = Model.tunerOf(chip)
    tuner = chip.selected === true ? null : _tuneChip
    _queueStateChanged()
  }

  // The chip's queue in place of the upcoming auto entries (user entries
  // stay); nothing changes when it brings nothing new.
  function _applyTuner() {
    var chip = _tuneChip
    var seed = currentTrack
    if (!chip || !seed || !seed.videoId || !hasMedia) return
    var token = _replaceToken
    var mine = ++_tuneToken
    var params = { videoId: seed.videoId, playlistId: chip.playlistId }
    if (chip.params) params.params = chip.params
    _tuning = true
    backend.request("queueFrom", params, function(reply) {
      root.releaseWorkerIfIdle()
      if (mine !== root._tuneToken) return
      if (token !== root._replaceToken) {
        root._tuning = false
        return
      }
      if (!reply.ok) {
        root._tuning = false
        root.failAction("Couldn't tune the queue", reply.error.message)
        return
      }
      var items = reply.result.items || []
      root._withSnapshot(function(queue, pos) {
        root._tuning = false
        if (!queue || mine !== root._tuneToken || token !== root._replaceToken) return
        var plan = Model.replaceUpcomingAuto(queue, pos, items, Model.AUTOPLAY_BATCH, root._seen)
        if (plan.tracks.length === 0) {
          root.notify("Nothing new for " + chip.title, "The queue stays as it is.")
          return
        }
        root.remember(plan.tracks)
        root._sendQueue(plan.commands, root._idsOf(plan.tracks))
        root.autoplayNote = ""
      })
    }, 20000)
  }

  // The current song's other version (Model.counterpartOf), for its menu.
  function counterpartOf(track) {
    return Model.counterpartOf(track, nowContext)
  }

  // Play the song playing's twin (song or video version) from its start in
  // place of its entry, which keeps its origin. `item` is the song the menu
  // was opened for: once another song plays, nothing happens.
  function switchVersion(counterpart, item) {
    var entry = currentEntry
    if (!entry || !item || entry.videoId !== item.videoId) return false
    var next = Model.counterpartTrack(entry.track, counterpart)
    if (!next || !hasMedia) return false
    remember([next])
    var entryId = entry.entryId
    var videoId = entry.videoId
    var autoIds = entry.origin === "auto" ? _idsOf([next]) : null
    withStream(next, function() {
      root._withSnapshot(function(queue, pos) {
        // Moved on to another song meanwhile: leave it.
        if (!queue || pos < 0 || queue[pos].entryId !== entryId || queue[pos].videoId !== videoId) return
        root._sendQueue(Model.switchVersionCommands(next, pos), autoIds)
      })
    })
    return true
  }

  // Played entries more than Model.TRIM_KEEP behind the current one leave
  // mpv's playlist (not while repeating the queue: they come round again).
  // Not while a Play waits for its stream either: a queue row's jump sends
  // the index it was clicked at (the next song change trims instead).
  function trimPlayed() {
    if (!mpv.up || repeatMode !== "off" || _reordering || _tuning || _pendingPlay) return false
    var count = Model.trimPlayed(playlistPos, Model.TRIM_KEEP)
    if (count <= 0 || count > playlist.length || playlist.length !== playlistCount) return false
    var gone = queue.slice(0, count)
    _seen = Model.withSeen(_seen, gone.map(function(entry) { return entry.videoId }).filter(Boolean), Model.SEEN_MAX)
    if (shuffleOrder.length > 0) {
      var ids = gone.map(function(entry) { return entry.entryId })
      setShuffleOrder(shuffleOrder.filter(function(id) { return ids.indexOf(id) < 0 }))
    }
    if (_retryIndex >= 0) _retryIndex = Math.max(0, _retryIndex - count)
    mpv.commands(Model.trimCommands(count))
    // Shown trimmed at once, and mpv's index changes on the way there (one
    // per removed entry) are skipped; the current entry is held while the
    // three change, so no other song is ever current.
    _trimHeld = currentEntry
    _trimShift = true
    playlist = playlist.slice(count)
    playlistCount = playlist.length
    playlistPos = playlistPos - count
    _trimShift = false
    _trimHeld = null
    _trimTarget = { pos: playlistPos, count: playlistCount, posSeen: false, countSeen: false }
    trimGuard.restart()
    _queueStateChanged()
    trimmed(count)
    return true
  }

  // True for a playlist-pos / playlist-count change a trim is still making.
  function _trimEvent(name, value) {
    var target = _trimTarget
    if (!target) return false
    if (name === "playlist-pos" ? value !== target.pos : value !== target.count) return true
    if (name === "playlist-pos") target.posSeen = true
    else target.countSeen = true
    if (target.posSeen && target.countSeen) {
      _trimTarget = null
      trimGuard.stop()
    }
    return false
  }

  // Something else moved the queue meanwhile: read where mpv is.
  function _trimSettle() {
    if (!_trimTarget) return
    _trimTarget = null
    mpv.command(["get_property", "playlist-pos"], function(reply) {
      if (reply && reply.error === "success") root.onMpvProperty("playlist-pos", reply.data)
    })
    mpv.command(["get_property", "playlist-count"], function(reply) {
      if (reply && reply.error === "success") root.onMpvProperty("playlist-count", reply.data)
    })
  }

  // ---- queue.json
  // Nothing is written before the file was read and merged (a restore
  // waiting for mpv's pid would otherwise lose what it holds).
  function _queueStateChanged() {
    if (_queueStateRead) queueStateSave.restart()
  }

  function saveQueueState() {
    if (!_queueStateRead || _savedQueueState !== null) return
    queueStateFile.setText(Model.queueStateText({ pid: _mpvPid > 0 ? _mpvPid : null, context: playContext, tuner: tuner,
                                                  origins: queueOrigins, seen: _seen }))
  }

  function _readMpvPid() {
    mpv.command(["get_property", "pid"], function(reply) {
      root._mpvPid = reply && reply.error === "success" && typeof reply.data === "number" ? reply.data : 0
      root._restoreQueueState()
    })
  }

  // Once both the file and mpv's pid are known: the saved origins, context,
  // tuner and seen ids belong to this player's queue only when the pid
  // matches (a new player starts its entry ids over).
  function _restoreQueueState() {
    var saved = _savedQueueState
    if (!saved || !(_mpvPid > 0)) return
    _savedQueueState = null
    if (saved.pid === _mpvPid) {
      _seen = Model.withSeen(saved.seen, _seen, Model.SEEN_MAX)
      var origins = ({})
      for (var key in saved.origins) origins[key] = saved.origins[key]
      for (var mine in queueOrigins) origins[mine] = queueOrigins[mine]
      // Before the first playlist read the next refresh prunes them.
      queueOrigins = playlist.length > 0 ? Model.pruneOrigins(origins, playlist) : origins
      if (!playContext) playContext = saved.context
      if (!tuner) tuner = saved.tuner
    }
    _queueStateChanged()
  }

  function _applyQueueStateText(text) {
    _savedQueueState = Model.parseQueueState(text)
    _queueStateRead = true
    _restoreQueueState()
  }

  // mpv quit: its entry ids mean nothing to the next player.
  function _playerGone() {
    _mpvPid = 0
    _trimTarget = null
    _originsPending = []
    if (Object.keys(queueOrigins).length > 0) queueOrigins = ({})
    _queueStateChanged()
  }

  FileView {
    id: queueStateFile
    path: root.queueStatePath
    atomicWrites: true
    watchChanges: false
    printErrors: false
    onLoaded: root._applyQueueStateText(text())
    onLoadFailed: root._applyQueueStateText("")
  }

  Timer {
    id: queueStateSave
    interval: 1000
    onTriggered: root.saveQueueState()
  }

  Timer {
    id: tuneApply
    interval: 400
    onTriggered: root._applyTuner()
  }

  // A few seconds after a track change, so skipping through the queue
  // trims nothing until it settles.
  Timer {
    id: trimTimer
    interval: 3000
    onTriggered: root.trimPlayed()
  }

  Timer {
    id: trimGuard
    interval: 1500
    onTriggered: root._trimSettle()
  }

  // ---- playlists ---------------------------------------------------------------------

  function createPlaylist(title, tracks, callback) {
    var name = String(title || "").trim()
    if (!signedIn || name === "") return false
    var ids = (tracks || []).filter(function(t) { return t && t.videoId }).map(function(t) { return t.videoId })
    backend.request("createPlaylist", { title: name, videoIds: ids.length ? ids : null }, function(reply) {
      if (reply.ok) {
        storeItem.loadLibrary(true)
        root.notify("Playlist created", ids.length ? name + " — " + ids.length + (ids.length === 1 ? " song" : " songs") : name)
      } else if (root.unsure(reply)) {
        root.refreshUnsure([{ kind: "library" }], name)
      } else {
        root.fail(reply.error.message)
      }
      if (callback) callback(reply)
    }, 30000, noRetry)
    return true
  }

  function addToPlaylist(playlist, tracks, callback) {
    var list = (tracks || []).filter(function(t) { return t && t.videoId })
    if (!signedIn || !playlist || list.length === 0) return false
    backend.request("addToPlaylist", { playlistId: playlist.playlistId, videoIds: list.map(function(t) { return t.videoId }) }, function(reply) {
      if (reply.ok) {
        storeItem.forgetCollection("playlist", playlist.playlistId)
        root.notify("Added to " + playlist.title, list.length === 1 ? Model.mediaTitle(list[0]) : list.length + " songs")
      } else if (root.unsure(reply)) {
        root.refreshUnsure([{ kind: "playlist", id: playlist.playlistId }, { kind: "library" }], playlist.title)
      } else {
        root.fail(reply.error.message)
      }
      if (callback) callback(reply)
    }, 30000, noRetry)
    return true
  }

  function removeFromPlaylist(playlistId, tracks, callback) {
    var items = (tracks || []).filter(function(t) { return t && t.setVideoId })
      .map(function(t) { return { videoId: t.videoId, setVideoId: t.setVideoId } })
    if (!signedIn || !playlistId || items.length === 0) return false
    backend.request("removeFromPlaylist", { playlistId: playlistId, items: items }, function(reply) {
      if (reply.ok) storeItem.forgetCollection("playlist", playlistId)
      else if (root.unsure(reply)) root.refreshUnsure([{ kind: "playlist", id: playlistId }, { kind: "library" }])
      else root.fail(reply.error.message)
      if (callback) callback(reply)
    }, 30000, noRetry)
    return true
  }

  function deletePlaylist(playlist, callback) {
    if (!signedIn || !playlist || !playlist.playlistId) return false
    backend.request("deletePlaylist", { playlistId: playlist.playlistId }, function(reply) {
      if (reply.ok) {
        storeItem.forgetCollection("playlist", playlist.playlistId)
        storeItem.loadLibrary(true)
        root.notify("Playlist deleted", playlist.title || "")
      } else if (root.unsure(reply)) {
        root.refreshUnsure([{ kind: "playlist", id: playlist.playlistId }, { kind: "library" }], playlist.title)
      } else {
        root.fail(reply.error.message)
      }
      if (callback) callback(reply)
    }, 30000, noRetry)
    return true
  }

  // Open the panel on the search tab with results for `query`.
  function showSearch(query) {
    var text = String(query || "").trim()
    if (text !== "") store.search(text, "")
    store.requestTab("search")
    if (shell && typeof shell.summon === "function") shell.summon(pluginId)
    return true
  }

  // ---- transport ------------------------------------------------------------

  function playPause() {
    if (!mpv.up) return canResume ? resume() : false
    if (idle && playlistCount > 0) {
      var start = _retryIndex >= 0 && _retryIndex < playlistCount ? _retryIndex : 0
      mpv.commands([["playlist-play-index", start], ["set_property", "pause", false]])
    }
    else if (!idle) mpv.command(["cycle", "pause"])
    else return false
    return true
  }

  function next() {
    if (!hasMedia) return false
    mpv.command(["playlist-next", "weak"])
    return true
  }

  function previous() {
    if (!hasMedia) return false
    mpv.command(["get_property", "time-pos"], function(reply) {
      mpv.commands(Model.previousCommands(reply && reply.data))
    })
    return true
  }

  function seek(seconds) {
    if (!hasMedia) return false
    position = Math.max(0, Number(seconds) || 0)
    mpv.command(["seek", position, "absolute"])
    return true
  }

  function seekRelative(delta) {
    if (!hasMedia) return false
    mpv.command(["seek", Number(delta) || 0, "relative"], function() { root.fetchPosition() })
    return true
  }

  function fetchPosition() {
    if (!mpv.up) return
    mpv.command(["get_property", "time-pos"], function(reply) {
      if (reply && typeof reply.data === "number") root.position = reply.data
    })
  }

  function panelOpenChanged(isOpen) {
    panelsOpen = Math.max(0, panelsOpen + (isOpen ? 1 : -1))
    // Start the worker and load yt-dlp while you look around, so the first
    // search, library load and play from the panel are warm.
    if (isOpen) {
      speculated = 0
      checkTools()
      backend.request("warmStreams", ({}))
    }
  }

  onPositionWantedChanged: {
    if (!positionWanted) return
    fetchPosition()
    nowCheck.restart()
  }

  function setVolume(percent) {
    volume = Math.max(0, Math.min(130, Math.round(Number(percent) || 0)))
    if (mpv.up) mpv.command(["set_property", "volume", volume])
    return true
  }

  function stop() {
    if (!mpv.up) return false
    mpv.command(["stop"])
    return true
  }

  function jumpTo(index) {
    if (!mpv.up || index < 0 || index >= playlistCount) return false
    var entry = queue[index]
    var jump = function() { mpv.commands([["playlist-play-index", index], ["set_property", "pause", false]]) }
    if (entry && entry.videoId) withStream(entry.track, jump)
    else jump()
    return true
  }

  function removeAt(index) {
    if (!mpv.up || index < 0 || index >= playlistCount) return false
    mpv.command(["playlist-remove", index])
    return true
  }

  function move(from, to) {
    if (!mpv.up || from === to || from < 0 || to < 0 || from >= playlistCount || to >= playlistCount) return false
    mpv.command(Model.moveArgs(from, to))
    // A move changes neither playlist-count nor playlist-pos (unless it
    // moves the current entry), so fetch the new order.
    refreshQueue()
    return true
  }

  // Keeps the current track, drops everything else.
  function clearUpcoming() {
    if (!mpv.up) return false
    mpv.command(["playlist-clear"])
    return true
  }

  // Search songs and play the best match now, next, or at the end of the
  // queue (mode "now" | "next" | "queue"). Used by IPC.
  function playQuery(query, mode) {
    var text = String(query || "").trim()
    if (text === "") return false
    backend.request("search", { query: text, filter: "songs", limit: 5 }, function(reply) {
      if (!reply.ok) {
        root.fail(reply.error.message)
        return
      }
      var items = reply.result.items || []
      var track = null
      for (var i = 0; i < items.length && !track; i++) if (items[i].available !== false) track = items[i]
      if (!track) {
        root.fail("No songs found for \"" + text + "\"")
        return
      }
      if (mode === "queue") root.addToQueue(track)
      else if (mode === "next") root.playNext(track)
      else root.playNow(track)
    })
    return true
  }

  // ---- mirroring mpv -----------------------------------------------------------

  function onMpvProperty(name, value) {
    if (name === "pause") paused = value === true
    else if (name === "idle-active") idle = value === true
    else if (name === "playlist-pos") { if (_trimEvent(name, value)) return; playlistPos = typeof value === "number" ? value : -1; queueRefresh.restart() }
    else if (name === "playlist-count") { if (_trimEvent(name, value)) return; playlistCount = typeof value === "number" ? value : 0; queueRefresh.restart() }
    else if (name === "media-title") mediaTitle = value || ""
    else if (name === "duration") duration = typeof value === "number" ? value : 0
    else if (name === "volume") { if (typeof value === "number") volume = Math.round(value) }
    else if (name === "paused-for-cache") buffering = value === true
    else if (name === "loop-file") _loopFile = value
    else if (name === "loop-playlist") _loopPlaylist = value
    else if (name === "user-data/ytm/stale" && Array.isArray(value) && value.length > 0) {
      // mpv/ytm-streams.lua dropped streams that failed to play.
      dropStreams(value)
      mpv.command(["set_property", "user-data/ytm/stale", []])
    }
  }

  function onMpvEvent(message) {
    if (message.event === "start-file") {
      buffering = true
      position = 0
    } else if (message.event === "file-loaded") {
      buffering = false
      lastError = ""
      consecutiveErrors = 0
      _retryIndex = -1
      _pendingNotice = null
      errorNotice.stop()
    } else if (message.event === "end-file" && message.reason === "error") {
      buffering = false
      // By now mpv may already be loading the next entry, so name the one
      // the event is about.
      var entry = Model.entryById(queue, message.playlist_entry_id)
      var failed = entry ? entry.track : currentTrack
      var title = failed ? "\"" + failed.title + "\"" : "the track"
      if (consecutiveErrors === 0) _retryIndex = entry ? entry.index : playlistPos
      fail("Could not play " + title + ": " + (message.file_error || "unknown error"))
      queueErrorNotice(title, message.file_error || "unknown error")
      // Offline (or yt-dlp broken), every entry fails in ~0.5 s: stop after
      // three instead of running through the whole queue. The queue stays;
      // play/pause continues from the first failure.
      if (consecutiveErrors >= 3) mpv.command(["stop", "keep-playlist"])
    }
  }

  function onMpvReady() {
    applyRepeat()
    _readMpvPid()  // S12
    mpv.command(["get_property", "user-data/ytm/order"], function(reply) {
      root.shuffleOrder = reply && reply.error === "success" && Array.isArray(reply.data) ? reply.data : []
    })
    // Streams resolved before a plugin reload or shell restart live in mpv.
    mpv.command(["get_property", "user-data/ytm/streams"], function(reply) {
      if (!reply || reply.error !== "success" || !reply.data || typeof reply.data !== "object") {
        root.pushStreams()
        return
      }
      var merged = ({})
      for (var key in reply.data) merged[key] = reply.data[key]
      for (var local in root.streams) merged[local] = root.streams[local]
      root.streams = Model.pruneStreams(merged, root.nowSec(), 50)
      root.pushStreams()
    })
    mpv.command(["get_property", "user-data/ytm/meta"], function(reply) {
      if (reply && reply.error === "success" && reply.data && typeof reply.data === "object") {
        var merged = ({})
        for (var key in reply.data) merged[key] = reply.data[key]
        for (var local in root.meta) merged[local] = root.meta[local]
        root.meta = merged
      }
      root.refreshQueue()
    })
  }

  function refreshQueue() {
    if (!mpv.up) return
    mpv.command(["get_property", "playlist"], function(reply) {
      if (!reply || reply.error !== "success") return
      root.playlist = reply.data || []
      root._queueRefreshed()  // S12
      metaSync.restart()
      root.lookUpUnknownTracks()
    })
  }

  // Entries queued by someone else (or before a restart lost the metadata).
  function lookUpUnknownTracks() {
    var ids = Model.unknownVideoIds(queue, 10)
    for (var i = 0; i < ids.length; i++) {
      var videoId = ids[i]
      if (_lookedUp[videoId]) continue
      _lookedUp[videoId] = true
      backend.request("song", { videoId: videoId }, function(reply) {
        if (reply.ok) root.remember([reply.result])
      })
    }
  }

  function pushMeta() {
    if (!mpv.up) return
    mpv.command(["set_property", "user-data/ytm/meta", Model.pruneMeta(meta, playlist)])
  }

  function resetPlayback() {
    paused = false
    idle = true
    buffering = false
    playlistPos = -1
    playlistCount = 0
    mediaTitle = ""
    duration = 0
    position = 0
    playlist = []
    // Entry ids start over in the next player.
    shuffleOrder = []
    _loopFile = false
    _loopPlaylist = false
    _playerGone()  // S12
  }

  function statusJson() {
    return JSON.stringify({
      plugin: pluginId,
      version: version,
      startedAt: startedAt,
      mpv: mpvState,
      worker: workerState,
      missingTools: missingTools,
      auth: authState,
      signedIn: signedIn,
      expiryNotified: expiryNotified,
      like: currentLike,
      libraryPlaylists: storeItem.playlists.length,
      playing: playing,
      paused: paused,
      idle: idle,
      buffering: buffering,
      volume: volume,
      position: position,
      duration: duration,
      current: currentTrack ? { videoId: currentTrack.videoId, title: currentTrack.title, artists: currentTrack.artistText } : null,
      queueIndex: playlistPos,
      queue: queue.map(function(entry) { return entry.videoId }),
      player: {
        autoplay: autoplay,
        autoplayWanted: autoplayWanted,
        autoplaySeed: _autoplaySeed,
        shuffle: shuffle,
        repeat: repeatMode,
        shuffleOrder: shuffleOrder.length,
        context: playContext,
        // S12
        playingFrom: playingFrom,
        tuner: tuner ? tuner.title : null,
        autoEntries: queue.filter(function(entry) { return entry.origin === "auto" }).length,
        seen: _seen.length,
        autoplayNote: autoplayNote
      },
      lastError: lastError,
      streams: {
        fresh: Object.keys(Model.pruneStreams(streams, nowSec(), 50)),
        resolving: resolvingCount,
        pending: pendingTitle,
        speculated: speculated
      },
      cache: {
        thumbs: storeItem.thumbCount,
        searches: storeItem.searchCacheSize
      },
      session: {
        canResume: canResume,
        index: session ? session.index : -1,
        timePos: session ? session.timePos : 0,
        tracks: session ? session.videoIds.length : 0
      },
      search: {
        query: storeItem.query,
        filter: storeItem.filter,
        busy: storeItem.searching,
        ms: storeItem.lastSearchMs,
        rows: Model.flattenSearch(storeItem.result, storeItem.filter).length,
        error: storeItem.searchError,
        inputFocused: storeItem.inputFocused,
        recent: storeItem.recent.length,
        suggestions: storeItem.suggestions.map(function(s) { return s.text })
      }
    })
  }

  // ---- required tools ------------------------------------------------------------
  // Omarchy ships mpv, mpv-mpris, yt-dlp and wl-clipboard (wl-copy, for
  // Copy link); uv (runs the ytmusicapi helper) and deno (yt-dlp's solver for
  // YouTube's JavaScript challenge) usually have to be added. Checked at
  // start and whenever a panel opens.
  readonly property var requiredTools: ["uv", "mpv", "yt-dlp", "deno", "wl-copy"]
  // Tools whose package has another name.
  readonly property var toolPackages: ({ "wl-copy": "wl-clipboard" })
  property var missingTools: []
  property var _missingFound: []
  readonly property string installCommand: "omarchy-pkg-add "
    + missingTools.map(function(tool) { return root.toolPackages[tool] || tool }).join(" ")

  function checkTools() {
    if (toolCheck.running) return
    _missingFound = []
    toolCheck.running = true
  }

  function installTools() {
    if (missingTools.length === 0) return false
    Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", installCommand])
    return true
  }

  Process {
    id: toolCheck
    command: ["sh", "-c",
      "for t in \"$@\"; do command -v \"$t\" >/dev/null 2>&1 || echo \"$t\"; done; "
        + "[ -e /etc/mpv/scripts/mpris.so ] || [ -e /usr/lib/mpv-mpris/mpris.so ] || echo mpv-mpris",
      "sh"].concat(root.requiredTools)
    stdout: SplitParser {
      onRead: data => {
        if (data.trim() !== "") root._missingFound = root._missingFound.concat([data.trim()])
      }
    }
    onExited: root.missingTools = root._missingFound
  }

  function togglePanel() {
    if (!shell || typeof shell.toggle !== "function") return false
    shell.toggle(pluginId)
    return true
  }

  MpvClient {
    id: mpv
    socketPath: Quickshell.env("XDG_RUNTIME_DIR") + "/omarchy-ytmusic/mpv.sock"
    controlScript: root.rootDir + "/bin/ytm-mpv"
    startVolume: root.volume
    observedProperties: ["pause", "idle-active", "playlist-pos", "playlist-count", "media-title", "duration", "volume", "paused-for-cache", "loop-file", "loop-playlist", "user-data/ytm/stale"]

    onPropertyUpdated: (name, value) => root.onMpvProperty(name, value)
    onEvent: message => root.onMpvEvent(message)
    onReady: root.onMpvReady()
    onMpvStateChanged: if (mpvState === "down") root.resetPlayback()
  }

  Backend {
    id: backend
    launcher: root.rootDir + "/bin/ytm"
  }

  // Search results and opened collections, shared by every panel.
  readonly property var store: storeItem

  Store {
    id: storeItem
    worker: backend
    thumbsEnabled: root.panelsOpen > 0
    stateDir: root.cacheDir
    nowKeep: root.currentTrack ? root.currentTrack.videoId : ""
  }

  Connections {
    target: storeItem
    function onTrackHinted(track) { root.speculate(track) }
  }

  FileView {
    id: accountFile
    path: root.configDir + "/account.json"
    watchChanges: true
    printErrors: false
    onLoaded: root.setAccount(text())
    onLoadFailed: root.setAccount("")
    // text() is stale inside the change signal itself, so re-read first.
    onFileChanged: reload()
  }

  Timer {
    id: nowCheck
    interval: 600
    onTriggered: root.refreshNowContext()
  }

  FileView {
    id: sessionFile
    path: root.cacheDir + "/session.json"
    atomicWrites: true
    watchChanges: false
    printErrors: false
    onLoaded: root.session = Model.parseSession(text())
    onLoadFailed: root.session = null
  }

  FileView {
    id: prefsFile
    path: root.cacheDir + "/player.json"
    atomicWrites: true
    watchChanges: false
    printErrors: false
    onLoaded: root.applyPrefs(text())
    onLoadFailed: root.applyPrefs("")
  }

  Timer {
    id: sessionSave
    interval: 2000
    onTriggered: root.saveSession()
  }

  Timer {
    interval: 30000
    repeat: true
    running: root.playing
    onTriggered: root.saveSession()
  }

  Timer {
    id: errorNotice
    interval: 4000
    onTriggered: {
      var notice = root._pendingNotice
      root._pendingNotice = null
      if (notice) root.notifyOnce(notice.key, notice.title, notice.body)
    }
  }

  // The session file's directory may not exist before the first save, and it
  // must be private whoever created it first.
  Component.onCompleted: {
    Quickshell.execDetached(["sh", "-c", "mkdir -p \"$1\" && chmod 700 \"$1\"", "sh", root.cacheDir])
    checkTools()
  }

  // Waits a few seconds into a song with few left (S12: 3 or fewer), so
  // skipping through the queue doesn't fetch anything.
  Timer {
    id: autoplayTimer
    interval: 5000
    onTriggered: root.refillAutoplay()
  }

  Timer {
    id: queueRefresh
    interval: 50
    onTriggered: root.refreshQueue()
  }

  // Resolve the next track a moment after the current one starts (a fast
  // skip through the queue doesn't resolve every track it passes).
  Timer {
    id: preResolve
    interval: 1500
    onTriggered: root.preResolveUpcoming()
  }

  Timer {
    id: pendingFallback
    // A cold worker (start, first search, loading yt-dlp) can take ~4 s; give
    // up only well after that. A failed resolve falls back right away.
    interval: 10000
    onTriggered: if (root._pendingPlay) root.runPendingPlay(root._pendingPlay.token)
  }

  Timer {
    id: metaSync
    interval: 300
    onTriggered: root.pushMeta()
  }

  Timer {
    interval: 1000
    repeat: true
    running: root.positionWanted && root.playing
    onTriggered: root.fetchPosition()
  }

  // On a hot reload the shell creates this instance before the previous one
  // is actually deleted (destroy() is deferred), and Quickshell ignores a
  // second handler for a target that is still taken. Registering a moment
  // later lets the fresh instance own "ytmusic" after every reload.
  property bool ipcReady: false

  Timer {
    interval: 250
    running: true
    onTriggered: root.ipcReady = true
  }

  IpcHandler {
    enabled: root.ipcReady
    target: "ytmusic"

    function ping(): string { return "ok" }
    function status(): string { return root.statusJson() }
    function toggle(): string { return root.togglePanel() ? "ok" : "unavailable" }
    function playPause(): string { return root.playPause() ? "ok" : "unhandled" }
    function next(): string { return root.next() ? "ok" : "unhandled" }
    function previous(): string { return root.previous() ? "ok" : "unhandled" }
    function stop(): string { return root.stop() ? "ok" : "unhandled" }
    function resume(): string { return root.resume() ? "ok" : "unhandled" }
    function seek(seconds: real): string { return root.seek(seconds) ? "ok" : "unhandled" }
    function volume(percent: int): string { return root.setVolume(percent) ? "ok" : "unhandled" }
    function play(query: string): string { return root.playQuery(query, "now") ? "ok" : "unhandled" }
    function playNext(query: string): string { return root.playQuery(query, "next") ? "ok" : "unhandled" }
    function queue(query: string): string { return root.playQuery(query, "queue") ? "ok" : "unhandled" }
    function shuffle(): string { return root.toggleShuffle() ? (root.shuffle ? "on" : "off") : "busy" }
    function repeat(): string { root.cycleRepeat(); return root.repeatPref }
    function autoplay(): string { root.toggleAutoplay(); return root.autoplay ? "on" : "off" }
    function search(query: string): string { return root.showSearch(query) ? "ok" : "unhandled" }
    function like(): string { return root.setLike(root.currentTrack, true) ? "ok" : "unhandled" }
    function unlike(): string { return root.setLike(root.currentTrack, false) ? "ok" : "unhandled" }
    function toggleLike(): string { return root.toggleLike(root.currentTrack) ? "ok" : "unhandled" }
    function reloadAuth(): string { return root.reloadAuth() ? "ok" : "unhandled" }
    function signIn(): string { return root.signIn() ? "ok" : "unhandled" }
    function signOut(): string { return root.signOut() ? "ok" : "unhandled" }
  }
}
