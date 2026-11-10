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
        // S3: no feedback tokens in the queue (status, session.json, mpv).
        // S6: nor a chart song's rank and trend (Explore's rows only).
        next[track.videoId] = Model.withoutRank(Model.withoutTokens(track))
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
    savePosition()  // S8: the episode playing keeps its place
    // S12: into an empty queue it starts a new one, from nothing in particular.
    if (playlistCount === 0) _newQueue(null)
    remember([track])
    withStream(track, function() {
      var commands = Model.playNowCommands(track, { count: root.playlistCount, pos: root.playlistPos })
      commands[0] = Model.withStartOption(commands[0], root._episodeStart(track))  // S8: where it was left
      mpv.commands(commands)
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

  // `track` (optional): the song, when it is not queued yet (its meta says
  // whether it is an upload).
  function resolveStream(videoId, callback, track) {
    if (!videoId) return
    if (streamFor(videoId)) {
      if (callback) callback(true)
      return
    }
    // S10: an uploaded song streams with the account; once the worker could
    // not, mpv's yt-dlp (the loadfile's cookies option) plays them.
    var upload = Model.isUploadTrack(track || meta[videoId])
    if (upload && _accountStreamsFailed) {
      if (callback) callback(false)
      return
    }
    if (_resolving[videoId]) {
      if (callback) _resolving[videoId].push(callback)
      return
    }
    _resolving[videoId] = callback ? [callback] : []
    resolvingCount++
    var epoch = _qualityEpoch  // S16
    backend.request("resolve", upload ? { videoId: videoId, account: true } : { videoId: videoId }, function(reply) {
      var waiting = root._resolving[videoId] || []
      delete root._resolving[videoId]
      root.resolvingCount = Math.max(0, root.resolvingCount - 1)
      if (reply.ok && epoch !== root._qualityEpoch) return root._resolveAgain(videoId, waiting)  // S16: old quality
      if (reply.ok) root.addStream(videoId, reply.result)
      else console.warn("ytmusic: no stream for " + videoId + ": " + reply.error.message)
      if (!reply.ok && upload && !reply.error.retryable && !reply.error.unknown) root._accountStreamsFailed = true  // S10
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
    }, track)
  }

  // Background pre-resolving shouldn't keep the worker (~80 MiB with yt-dlp
  // loaded) around for its full idle timeout when nobody is browsing.
  function releaseWorkerIfIdle() {
    if (panelsOpen === 0 && !_pendingPlay && resolvingCount === 0) backend.release(20000)
  }

  function addStream(videoId, stream) {
    var next = Model.pruneStreams(streams, nowSec(), 50)
    next[videoId] = Model.streamEntry(stream)  // S17: with its loudness gain when asked for
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
    resolveStream(track.videoId, function() { root.runPendingPlay(token) }, track)
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
  onPausedChanged: if (paused) {
    saveSession()
    savePosition()  // S8: a paused episode keeps its place
  }

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
  // S8: the song started, when an episode, starts at `episodeStart`
  // ({ seconds }, { percent }, false for its beginning), by default where
  // it was left (_episodeStart).
  function playCollection(tracks, start, shuffleAll, context, partial, autoFrom, episodeStart) {
    // The latest thing started wins over a Play or radio still loading.
    _replaceToken++
    savePosition()  // S8: the episode playing keeps its place
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
    plan.commands[1 + plan.index] = Model.withStartOption(plan.commands[1 + plan.index],
                                                          _episodeStart(plan.tracks[plan.index], episodeStart))  // S8
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

  // ---- home (S5) ----------------------------------------------------------------------
  // A Home shelf's Play all: the list YouTube generates for the shelf (the
  // worker's shelfQueue, from its play endpoint) replaces the queue. When
  // that fails, the shelf's own songs play instead.
  function playShelf(shelf) {
    if (!shelf) return false
    var title = shelf.title ? String(shelf.title) : ""
    var own = Model.shelfTracks([shelf], 0)
    var fallback = function(message) {
      if (own.length > 0 && root.playCollection(own, 0, false, Model.shelfContext(shelf))) return
      root.failAction("Couldn't play " + (title || "this shelf"), message)
    }
    var params = Model.shelfQueueParams(shelf)
    if (!params) {
      fallback("Nothing to play here")
      return own.length > 0
    }
    // The latest thing started wins over this one while it loads.
    var token = ++_replaceToken
    backend.request("shelfQueue", params, function(reply) {
      if (token !== root._replaceToken) return
      var items = reply.ok ? (reply.result.items || []) : []
      if (items.length === 0) {
        fallback(reply.ok ? "No songs came back" : reply.error.message)
        return
      }
      if (!root.playCollection(items, 0, false, { playlistId: reply.result.playlistId || null, title: title, radio: false }))
        fallback("No songs came back")
    }, 30000)
    return true
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

  // S7: a library or uploads search ends with the session (expired, invalid
  // or signed out), not only on an account change; signed out, nothing in
  // the panel could take the scope back.
  onSignedInChanged: {
    if (!signedIn) storeItem.leaveScope()
    Qt.callLater(root._syncMpvCookies)  // S10
  }

  function applyAuthChange() {
    storeItem.reset()
    expiryNotified = false
    _accountStreamsFailed = false  // S10: the new session may stream uploads
    Qt.callLater(root._syncMpvCookies)  // S10: and its cookies go to mpv's copy
    if (backend.workerState === "stopped") backend.auth = account ? "unknown" : "none"
    else backend.request("reloadAuth", ({}))
  }

  // Called by `ytm auth` / `ytm logout` over IPC after they change the files.
  function reloadAuth() {
    accountFile.reload()
    identityFile.reload()  // S16: a switch by `bin/ytm call switchAccount`
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
      var notice = Model.expiryNotice(authState === "expired" ? identity : null)  // S16: perhaps only the identity in use
      Quickshell.execDetached(["omarchy-notification-send", "--app-name", "YouTube Music",
        notice.title, notice.body,
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
    storeItem.setDisliked(track.videoId, null)  // S4: a like or unlike ends a dislike
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

  // ---- playback (S17) ------------------------------------------------------------------
  // Speed is mpv's `speed`, 0.5x to 2x in steps of 0.25 ([ and ], the Now
  // page menu), remembered per kind of entry in prefs.speed { music,
  // episode } and applied whenever the kind of the entry playing (or its
  // saved speed) changes, so an episode keeps its own speed after music.
  // Loudness is prefs.normalize: "youtube" has the worker answer each
  // resolved song's loudness (its `normalize` setting), kept with the
  // stream as its gain, which mpv/ytm-levels.lua sets as that file's own
  // volume-gain when it loads; "dynamic" runs mpv's dynaudnorm filter (the
  // same script); "off" neither. mpv learns the mode from
  // user-data/ytm/normalize.
  // mpv's speed (mirrored; 1 for a new player).
  property real speed: 1
  readonly property string speedKind: currentTrack ? Model.speedKind(currentTrack) : ""
  readonly property string normalize: Model.normalizeMode(prefs.normalize)
  // "<kind>:<speed>" the song playing wants; "" without a song or before the
  // prefs are read. Applied once per change, so a speed set elsewhere (MPRIS)
  // stays until the kind changes.
  readonly property string speedWanted: prefsLoaded && speedKind !== ""
    ? speedKind + ":" + Model.speedFor(prefs.speed, speedKind) : ""
  property string _speedApplied: ""
  // The kind [ / ] and the speed chooser set: the entry playing's, music
  // when nothing plays (mpv may still hold the speed of an episode that ended).
  readonly property string speedEditKind: speedKind !== "" ? speedKind : "music"
  // That kind's speed as its next play gets it: mpv's while mpv holds what
  // was applied for it (a speed set elsewhere included), else the saved one,
  // which a play applies. [ / ], the Now page menu and the chooser's tick go
  // by it, so they agree with what is saved; the badge shows mpv's `speed`.
  readonly property real speedShown: mpv.up
    && _speedApplied === speedEditKind + ":" + Model.speedFor(prefs.speed, speedEditKind)
    ? speed : Model.speedFor(prefs.speed, speedEditKind)
  // The mode mpv gets; "" before the prefs are read.
  readonly property string levelsMode: prefsLoaded ? normalize : ""
  // A string, so the worker is configured again only when a setting changed.
  readonly property string workerSettingsText: JSON.stringify(Model.workerSettings(prefs))

  onSpeedWantedChanged: _applySpeed()
  onLevelsModeChanged: _applyLevels()

  function _applySpeed() {
    if (speedWanted === "" || speedWanted === _speedApplied || !mpv.up) return
    _speedApplied = speedWanted
    _setMpvSpeed(Model.speedFor(prefs.speed, speedKind))
  }

  function _setMpvSpeed(value) {
    speed = value
    mpv.command(["set_property", "speed", value])
  }

  // [ / ] (delta 1 / -1): one step, kept for the kind of entry playing.
  function stepSpeed(delta) {
    return setSpeed(Model.speedStep(speedShown, delta))
  }

  // Kept for the kind playing (music when nothing plays) and applied now;
  // a player that is down is not started for it.
  function setSpeed(value) {
    var next = Model.clampSpeed(value)
    setPref("speed", Model.withSpeed(prefs.speed, speedEditKind, next))
    if (speedKind === "") {
      // Nothing playing: mpv keeps the last entry's speed. A song's takes
      // the new one now, so the next song keeps it; an episode's stays for
      // the next episode, and the next song applies the saved one.
      if (!mpv.up || _speedApplied.indexOf("music:") !== 0) return true
      _speedApplied = "music:" + Model.speedFor(prefs.speed, "music")
    }
    // The pref may have held it already while mpv's speed differs (MPRIS).
    if (mpv.up && Math.abs(speed - next) > 0.001) _setMpvSpeed(next)
    return true
  }

  function setNormalize(mode) {
    var next = Model.normalizeMode(mode)
    setPref("normalize", next)
    // After the bindings above have sent the worker its new setting.
    if (next === "youtube") Qt.callLater(root._levelStreams)
    return true
  }

  function _applyLevels() {
    if (levelsMode !== "" && mpv.up) mpv.command(["set_property", "user-data/ytm/normalize", levelsMode])
  }

  // YouTube levelling just turned on: streams resolved without their
  // loudness are forgotten, and the song playing and the next one are
  // resolved again (with it), so they are levelled too.
  function _levelStreams() {
    if (normalize !== "youtube") return
    var kept = Model.streamsWithLoudness(streams)
    if (kept !== streams) {
      streams = kept
      pushStreams()
    }
    var track = currentTrack
    if (hasMedia && track && track.videoId && !Model.streamHasLoudness(streams[track.videoId])) resolveStream(track.videoId)
    preResolveUpcoming()
  }

  function _playbackReady() {
    _applyLevels()
    _applySpeed()
    levelsCheck.restart()
  }

  // A new player starts at 1x.
  function _playbackGone() {
    speed = 1
    _speedApplied = ""
    levelsCheck.stop()
  }

  // mpv outlives plugin updates: a player started before mpv/ytm-levels.lua
  // existed gets it loaded. The script marks itself in user-data/ytm/levels;
  // asked a moment after connecting, so a player that is starting has run it.
  function _loadLevelsScript() {
    if (!mpv.up) return
    mpv.command(["get_property", "user-data/ytm/levels"], function(reply) {
      if (!mpv.up || (reply && reply.error === "success" && reply.data === true)) return
      mpv.command(["load-script", root.rootDir + "/mpv/ytm-levels.lua"])
    })
  }

  Timer {
    id: levelsCheck
    interval: 2000
    onTriggered: root._loadLevelsScript()
  }

  // ---- history (S15) -------------------------------------------------------------------
  // Plays go to YouTube Music history while prefs.recordHistory is on. It is
  // off until you turn it on (Library's account row or its M menu); while it
  // is off, the first play of 10 s, signed in, shows a one-time notice saying
  // how. Each play of an entry (a start-file, or a repeat-one loop) is
  // checked against mpv's time-pos once Model.REPORT_AFTER_SEC should have
  // played, by one timer that runs only while that play is uncounted and
  // playing (a time-pos observer would wake the shell every second). A
  // counted play joins pendingPlays (pending-plays.json), sent with the
  // worker's reportPlays on its report lane when Model.flushDue says so (the
  // worker is up for other work, 5 wait, or the oldest waited 15 min) and
  // whenever a panel opens, so no worker starts for one song. An unknown
  // result drops the batch: a missing play beats a double count. Turning
  // recording off, signing out or another account drops what waits.
  readonly property bool recordHistory: prefs.recordHistory === true
  readonly property string playStatePath: cacheDir + "/pending-plays.json"
  property var pendingPlays: []
  property bool historyNoticeShown: false
  property bool _playStateRead: false
  // The play being watched: { key, entryId, startedAt }, or null.
  property var _play: null
  property int _playSerial: 0
  // The plays already counted (the current one at most): key -> true.
  property var _counted: ({})
  property int _playCheckMs: 10300
  property bool _reporting: false
  property string _historyAccount: ""
  // Plays are watched while recording, and until the one-time notice was shown.
  readonly property bool _watchPlays: prefsLoaded && _playStateRead && signedIn && (recordHistory || !historyNoticeShown)

  function setRecordHistory(on) {
    var value = on === true
    if (!prefsLoaded || value === recordHistory) return false
    setPref("recordHistory", value)
    _setHistoryNoticeShown()
    if (!value) _setPendingPlays([])
    var notice = Model.recordHistoryNotice(value)
    notify(notice.title, notice.body)
    return true
  }

  // mpv's events: a file starts (a new play), or playback restarts (after a
  // seek, or a repeat-one loop).
  function _historyEvent(message) {
    if (message.event === "start-file") _startPlay(message.playlist_entry_id)
    else if (message.event === "playback-restart") _playRestarted()
  }

  function _startPlay(entryId) {
    if (entryId === undefined || entryId === null) {
      _play = null
      return
    }
    _playSerial++
    _play = { key: Model.playKey(entryId, _playSerial), entryId: entryId, startedAt: Date.now() }
    _counted = ({})
    _playCheckMs = Model.playCheckMs(0, speed)
    if (playCheck.running) playCheck.restart()
  }

  // Repeat one loops the file without a new start-file: a restart near 0 s
  // is a new play (Model.loopRestart).
  function _playRestarted() {
    if (repeatMode !== "one" || !mpv.up) return
    var play = _play
    mpv.command(["get_property", "time-pos"], function(reply) {
      if (root._play !== play || !reply || reply.error !== "success") return
      var entryId = play ? play.entryId : (root.currentEntry ? root.currentEntry.entryId : null)
      var played = play ? Date.now() - play.startedAt : Model.LOOP_MIN_MS
      if (entryId !== null && entryId !== undefined && Model.loopRestart(root.repeatMode, reply.data, played))
        root._startPlay(entryId)
    })
  }

  // The timer's look at time-pos: the play is counted (recording), or the
  // one-time notice shows (not recording); else look again when 10 s will
  // have played.
  function _checkPlay() {
    var play = _play
    if (!play || !mpv.up) return
    mpv.command(["get_property", "time-pos"], function(reply) {
      if (root._play !== play || root._counted[play.key] === true) return
      var pos = reply && reply.error === "success" ? reply.data : null
      var entry = Model.entryById(root.queue, play.entryId)
      var candidate = { key: play.key, videoId: entry ? entry.videoId : "" }
      var on = root.recordHistory && root.signedIn
      if (Model.shouldReport(candidate, pos, root._counted, on)) root._countPlay(play, candidate.videoId)
      else if (!root.recordHistory && root.signedIn && Model.shouldReport(candidate, pos, root._counted, true)) root._noticeOnce(play)
      // Not a YouTube entry (a file mpv was given elsewhere): never counted.
      else if (entry && !candidate.videoId && typeof pos === "number" && pos >= Model.REPORT_AFTER_SEC) root._markCounted(play)
      else root._playCheckMs = Model.playCheckMs(pos, root.speed)
    })
  }

  function _markCounted(play) {
    var next = ({})
    next[play.key] = true
    _counted = next
  }

  function _countPlay(play, videoId) {
    _markCounted(play)
    _setPendingPlays(Model.withPendingPlay(pendingPlays, videoId, Date.now()))
    flushPlays(false)
  }

  function _noticeOnce(play) {
    _markCounted(play)
    if (historyNoticeShown) return
    _setHistoryNoticeShown()
    var notice = Model.historyNotice()
    notifyOnce("history-notice", notice.title, notice.body)
  }

  function _historyGone() {
    _play = null
    _counted = ({})
  }

  // Sends what waits when Model.flushDue says so; `force` (a panel opened)
  // also sends plays waiting after a passing failure. One call at a time, at
  // most Model.REPORT_CALL_MAX plays; the rest follow while the worker is up.
  function flushPlays(force) {
    if (_reporting || !_playStateRead || !prefsLoaded) return false
    // Off (however it was turned off): nothing waits to be sent.
    if (!recordHistory) {
      if (pendingPlays.length > 0) _setPendingPlays([])
      return false
    }
    if (!signedIn) return false
    var now = Date.now()
    var pending = Model.prunePending(pendingPlays, now)
    if (pending.length !== pendingPlays.length) _setPendingPlays(pending)
    var due = force === true || Model.flushDue(pending, now, workerState === "ready")
    var batch = due ? Model.reportBatch(pending, now, force === true) : []
    if (batch.length === 0) {
      _armReportTimer()
      return false
    }
    _reporting = true
    reportTimer.stop()
    // Marked as out before it goes, at once on disk: a reload mid-call
    // drops the batch rather than sending it again.
    _setPendingPlays(Model.markSent(pending, batch))
    _writePlayState()
    backend.request("reportPlays", { videoIds: batch.map(function(play) { return play.videoId }) }, function(reply) {
      root._reporting = false
      var after = Model.afterReport(root.pendingPlays, batch, reply, Date.now())
      root._setPendingPlays(after.pending)
      if (after.reported > 0) storeItem.forgetCollection("history")
      // The next batch follows, unless YouTube throttled or the network
      // failed: then what waits goes with the next trigger.
      if (reply.ok && after.waiting === 0) Qt.callLater(root.flushPlays)
      // After a report the panel did not ask for, the worker goes soon.
      root.releaseWorkerIfIdle()
    }, Model.reportTimeoutMs(batch.length), noRetry)
    return true
  }

  // Armed only while a flush could send (Connections below re-arm it when
  // that changes: prefs read late, or the session back).
  function _armReportTimer() {
    var at = recordHistory && prefsLoaded && signedIn ? Model.nextFlushAt(pendingPlays, Date.now()) : 0
    if (at === 0) {
      reportTimer.stop()
      return
    }
    reportTimer.interval = Math.max(1000, at - Date.now())
    reportTimer.restart()
  }

  function _setPendingPlays(list) {
    pendingPlays = list
    _savePlayState()
    _armReportTimer()
  }

  function _setHistoryNoticeShown() {
    if (historyNoticeShown) return
    historyNoticeShown = true
    _savePlayState()
  }

  // Nothing is written before the file was read (a write would lose what it holds).
  function _savePlayState() {
    if (_playStateRead) playStateSave.restart()
  }

  function _writePlayState() {
    if (_playStateRead) playStateFile.setText(Model.playStateText({ plays: pendingPlays, notice: historyNoticeShown }, Date.now()))
  }

  function _applyPlayState(text) {
    var saved = Model.parsePlayState(text, Date.now())
    pendingPlays = Model.prunePending(saved.plays.concat(pendingPlays), Date.now())
    historyNoticeShown = saved.notice || historyNoticeShown
    _playStateRead = true
    _armReportTimer()
  }

  // A History row's Remove from history (CollectionView puts the row back
  // on a definite failure; callback(reply) is its).
  function removeFromHistory(track, callback) {
    var params = Model.removeHistoryParams(track)
    if (!signedIn || !params) return false
    backend.request("removeHistory", params, function(reply) {
      if (reply.ok) {
        storeItem.forgetCollection("history")
        root.notify("Removed from history", Model.mediaTitle(track))
      } else if (root.unsure(reply)) {
        root.refreshUnsure([{ kind: "history", id: "history" }], Model.mediaTitle(track))
      } else {
        root.failAction("Not removed from history", reply.error.message)
      }
      if (callback) callback(reply)
    }, 30000, noRetry)
    return true
  }

  // What waits belongs to the session it was played under: S16's account
  // switch calls this (an account.json change does it below).
  function dropPendingPlays() {
    _setPendingPlays([])
  }

  // The worker came up for other work: what waits goes with it.
  onWorkerStateChanged: if (workerState === "ready") Qt.callLater(root.flushPlays)

  onAccountChanged: {
    var who = account ? String(account.handle || account.name || "signed in") : ""
    // Signed out, or another account: what waits was played under the old one.
    if (_historyAccount !== "" && who !== _historyAccount) dropPendingPlays()
    _historyAccount = who
  }

  FileView {
    id: playStateFile
    path: root.playStatePath
    atomicWrites: true
    watchChanges: false
    printErrors: false
    onLoaded: root._applyPlayState(text())
    onLoadFailed: root._applyPlayState("")
  }

  Timer {
    id: playStateSave
    interval: 500
    onTriggered: root._writePlayState()
  }

  // Runs only while the play is uncounted and playing (see _checkPlay).
  Timer {
    id: playCheck
    interval: root._playCheckMs
    repeat: true
    running: root._watchPlays && root.hasMedia && !root.paused && !!root._play && root._counted[root._play.key] !== true
    onTriggered: root._checkPlay()
  }

  // When the oldest play waiting comes due by time alone (15 min).
  Timer {
    id: reportTimer
    onTriggered: root.flushPlays(false)
  }

  // player.json read after pending-plays.json, recording turned on, or the
  // session back: the timer starts again (and stops while a flush could not
  // send). Recording turned off however (setPref, player.json edited): what
  // waits goes now.
  Connections {
    target: root
    function onRecordHistoryChanged() {
      if (!root.recordHistory && root.prefsLoaded && root.pendingPlays.length > 0) root.dropPendingPlays()
      else root._armReportTimer()
    }
    function onPrefsLoadedChanged() { root._armReportTimer() }
    function onSignedInChanged() { root._armReportTimer() }
  }

  // ---- account (S16) -------------------------------------------------------------------
  // Library's account menu: Switch account, Audio quality, Content location.
  // - The identity the worker acts as is Backend.identity ({ key, brand, own,
  //   authUser }, from its ready event, hello, accounts and switchAccount).
  //   Before the worker said (and while it is stopped) identity.json tells
  //   which kind it is, without names. The session's identities
  //   (`accounts`) and the name of a brand account in use live in memory
  //   only: names never reach a file or the log. Another identity (switched
  //   here, or by `bin/ytm call switchAccount` and reloadAuth) empties the
  //   Store and drops plays waiting for history: they belong to the one
  //   before.
  // - When YouTube stops answering as the brand account or login in use, the
  //   session reads as expired: Library offers your own account
  //   (useOwnAccount), which checks the session itself.
  // - The audio quality (prefs.quality) is the worker's resolver format and
  //   mpv's ytdl-format, set over IPC because the player unit's
  //   --ytdl-format is fixed when it starts (bin/ytm-mpv reads player.json).
  // - The content location (prefs.location) is the worker's client setting;
  //   a change empties the Store too (the worker clears its disk cache).

  // identity.json as Model.savedIdentity() reads it; null before it was read.
  property var _savedIdentity: null
  property string _identityText: ""
  readonly property var identity: Model.shownIdentity(account !== null, backend.workerState === "ready",
                                                      backend.identity, _savedIdentity)
  readonly property bool identityBrand: !!identity && identity.brand === true
  // Expired while acting as another identity: your own account may still work.
  readonly property bool canUseOwnAccount: authState === "expired" && Model.otherIdentity(identity)
  // { name, handle, photo } of the brand account or other login in use, or null.
  property var identityInfo: null
  // What the account row shows (account.json's for the session's own account).
  readonly property var activeAccount: Model.activeAccount(account, identity, identityInfo)
  // The last `accounts` reply as { items, at }, or null.
  property var accounts: null
  property bool accountsLoading: false
  property var _accountsWaiting: []
  property bool switching: false
  // Counted up when the identity or the location changes: a list on show
  // reads itself again (LibraryView).
  property int accountEpoch: 0
  property string _identityKey: ""
  property string _accountSeen: ""
  // Counted up by setQuality: a stream asked for before is at the old quality.
  property int _qualityEpoch: 0
  readonly property string quality: Model.qualityMode(prefs.quality)
  readonly property string location: Model.locationCode(prefs.location)
  // mpv's ytdl-format (its yt-dlp fallback); "" before the prefs are read.
  readonly property string ytdlFormat: prefsLoaded ? Model.qualityFormat(quality) : ""

  onYtdlFormatChanged: _applyYtdlFormat()

  function _applyYtdlFormat() {
    if (ytdlFormat !== "" && mpv.up) mpv.command(["set_property", "ytdl-format", ytdlFormat])
  }

  // From the next song on: streams resolved at the old quality are
  // forgotten (the song playing keeps its own) and the next one is
  // resolved again, after the binding has sent the worker the new setting.
  function setQuality(value) {
    var next = Model.qualityMode(value)
    if (next === quality) return true
    _qualityEpoch++
    setPref("quality", next)
    Qt.callLater(root._requalifyStreams)
    return true
  }

  // A lookup sent before a quality change answered (resolveStream): its
  // stream is not kept; the song is looked up again at the new quality and
  // `waiting` (resolveStream's callbacks) runs when that answers.
  function _resolveAgain(videoId, waiting) {
    resolveStream(videoId, function(ok) {
      for (var i = 0; i < waiting.length; i++) waiting[i](ok)
    })
  }

  function _requalifyStreams() {
    var keep = currentTrack && currentTrack.videoId && streams[currentTrack.videoId] ? currentTrack.videoId : ""
    var next = ({})
    if (keep !== "") next[keep] = streams[keep]
    streams = next
    pushStreams()
    preResolveUpcoming()
  }

  // A location Charts has also opens Charts there next (S6's chartsCountry);
  // the default and the locations Charts lacks leave Charts where it was.
  function setLocation(code) {
    var next = Model.locationCode(code)
    if (next === location) return true
    setPref("location", next)
    var country = Model.chartsCountryForLocation(next)
    if (country !== "") setPref("chartsCountry", country)
    _contentChanged()
    return true
  }

  function _contentChanged() {
    storeItem.reset()
    accountEpoch++
  }

  // The worker reported its identity: another one than before (both signed
  // in) means what shows and what waits belong to the old one.
  function _identitySeen() {
    var reported = backend.identity
    var key = reported && typeof reported.key === "string" ? reported.key : ""
    if (key === _identityKey) return
    identityFile.reload()  // the worker may have written it
    var before = _identityKey
    _identityKey = key
    accounts = null
    identityInfo = null
    if (before !== "" && key !== "") {
      dropPendingPlays()  // S15
      _contentChanged()
    }
    // After the reply that brought it: switchAccount's names the identity itself.
    Qt.callLater(root._nameIdentity)
  }

  // A brand account or another login in use: ask the worker its name, once,
  // while a panel is open to show it.
  function _nameIdentity() {
    if (!Model.otherIdentity(identity) || identityInfo || !signedIn || panelsOpen === 0 || accountsLoading) return
    loadAccounts(null)
  }

  // callback(reply) with reply.result.items (a list younger than
  // Model.ACCOUNTS_TTL_MS is used again). One request at a time.
  function loadAccounts(callback) {
    if (Model.accountsFresh(accounts, Date.now())) {
      _nameFrom(accounts.items)
      if (callback) callback({ ok: true, result: { items: accounts.items } })
      return
    }
    if (callback) _accountsWaiting = _accountsWaiting.concat([callback])
    if (accountsLoading) return
    accountsLoading = true
    backend.request("accounts", ({}), function(reply) {
      root.accountsLoading = false
      var waiting = root._accountsWaiting
      root._accountsWaiting = []
      if (reply.ok) {
        var items = reply.result && Array.isArray(reply.result.items) ? reply.result.items : []
        root.accounts = { items: items, at: Date.now() }
        root._nameFrom(items)
      }
      for (var i = 0; i < waiting.length; i++) waiting[i](reply)
    })
  }

  // The name of the brand account or login in use, from the list's selected one.
  function _nameFrom(items) {
    var chosen = Model.selectedAccount(items)
    if (chosen && Model.otherIdentity(identity))
      identityInfo = { name: chosen.name || "", handle: chosen.handle || "", photo: chosen.photo || "" }
  }

  // Library's Switch account: the chooser opens once the list is there (on
  // the panel it was asked from, if it is still open).
  function openAccountChooser(panel) {
    if (!signedIn) return false
    loadAccounts(function(reply) {
      if (!reply.ok) {
        root.failAction("Accounts not loaded", reply.error ? reply.error.message : "")
        return
      }
      if (!panel || panel.opened === false || typeof panel.openChooser !== "function") return
      panel.openChooser({ title: "Switch account", subtitle: "Likes, playlists and history go to the account in use",
                          choices: Model.accountChoices(reply.result.items),
                          onChoice: function(choice) { root.chooseAccount(choice.value) } })
    })
    return true
  }

  // A chooser row's value ({ kind, item }, Model.accountChoices).
  function chooseAccount(value) {
    if (!value) return false
    if (value.kind === "switch") return switchAccount(value.item)
    if (value.kind === "signIn") {
      if (value.item.brand === true) {  // the login's own account first, then a switch
        var first = Model.signInFirstNotice(value.item)
        notify(first.title, first.body)
      }
      return signInAs(value.item.authUser)
    }
    if (value.kind === "another") {
      var notice = Model.anotherLoginNotice()
      notify(notice.title, notice.body)
      return true
    }
    return false  // the one in use
  }

  // The worker acts as `item` (an `accounts` item) from now on; the identity
  // watcher above empties the Store when its reply arrives.
  function switchAccount(item) {
    if (!item || !signedIn) return false
    return _sendSwitch(Model.switchParams(item), item)
  }

  // Back to the session's own account while the session reads as expired
  // as another identity (Library's card and M). The worker checks the
  // session as that account; its reply's auth says whether it answered.
  function useOwnAccount() {
    if (!canUseOwnAccount) return false
    return _sendSwitch({ pageId: null }, null)
  }

  function _sendSwitch(params, item) {
    if (switching) return false
    switching = true
    backend.request("switchAccount", params, function(reply) {
      root.switching = false
      if (!reply.ok) {
        root.failAction("Account not switched", reply.error ? reply.error.message : "")
        return
      }
      var result = reply.result || {}
      if (result.auth === "ok") root.expiryNotified = false  // a later expiry tells again
      if (result.account && Model.otherIdentity(root.identity)) root.identityInfo = result.account
      var notice = Model.switchedNotice(item, result.account)
      root.notify(notice.title, notice.body)
    }, 30000)
    return true
  }

  // Another Google login of the browser: sign in again as it.
  function signInAs(authUser) {
    if (typeof authUser !== "number") return false
    Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation",
                             signInCommand() + " --authuser " + Math.floor(authUser)])
    return true
  }

  Connections {
    target: backend
    function onIdentityChanged() { root._identitySeen() }
  }

  // Read at start and again when the worker reports another identity or
  // `ytm` asks for reloadAuth; the worker writes it (0600, ids only).
  FileView {
    id: identityFile
    path: root.configDir + "/identity.json"
    watchChanges: true
    printErrors: false
    onLoaded: root._identityFileRead(text())
    onLoadFailed: root._identityFileRead("")
    onFileChanged: reload()
  }

  function _identityFileRead(text) {
    var changed = text !== _identityText
    _identityText = text
    _savedIdentity = Model.savedIdentity(text)
    // Switched while the worker is stopped (`bin/ytm call switchAccount`):
    // the name and the list were the identity's before.
    if (changed && backend.workerState !== "ready") {
      identityInfo = null
      accounts = null
    }
    Qt.callLater(root._nameIdentity)
  }

  Connections {
    target: mpv
    function onReady() { root._applyYtdlFormat() }
  }

  Connections {
    target: root
    function onPanelsOpenChanged() { root._nameIdentity() }
    function onSignedInChanged() { root._nameIdentity() }
    // Signed out or in again: the list and the name were the old session's.
    function onAccountChanged() {
      var seen = JSON.stringify(root.account)
      if (seen === root._accountSeen) return
      root._accountSeen = seen
      root.accounts = null
      root.identityInfo = null
      root._nameIdentity()
    }
  }

  // ---- playlists ---------------------------------------------------------------------

  // S13: opts { description, privacy, sourcePlaylistId (a copy of that
  // playlist or album), sourceTitle }.
  function createPlaylist(title, tracks, callback, opts) {
    var name = String(title || "").trim()
    if (!signedIn || name === "") return false
    var o = opts || {}
    var ids = (tracks || []).filter(function(t) { return t && t.videoId }).map(function(t) { return t.videoId })
    var params = { title: name, videoIds: ids.length ? ids : null }
    if (o.description) params.description = String(o.description)
    if (o.privacy) params.privacy = String(o.privacy)
    if (o.sourcePlaylistId) params.sourcePlaylistId = String(o.sourcePlaylistId)
    backend.request("createPlaylist", params, function(reply) {
      if (reply.ok) {
        storeItem.loadLibrary(true)
        // A copy fills in on YouTube's side a few seconds later.
        if (o.sourcePlaylistId) copyRefresh.restart()
        root.notify("Playlist created", ids.length ? name + ": " + root.songCount(ids.length)
          : o.sourceTitle ? name + ": the songs of " + o.sourceTitle + " follow in a few seconds" : name)
      } else if (root.unsure(reply)) {
        root.refreshUnsure([{ kind: "library" }], name)
      } else {
        root.fail(reply.error.message)
      }
      if (callback) callback(reply)
    }, ids.length > 100 || o.sourcePlaylistId ? 60000 : 30000, noRetry)
    return true
  }

  // S13: opts { duplicates (add anyway), sourcePlaylistId (a whole album or
  // playlist, with sourceTitle) }. A song already there (DUPLICATE) is the
  // callback's to ask about: it returns true when it did (the picker),
  // else a notice says nothing was added.
  function addToPlaylist(playlist, tracks, callback, opts) {
    var o = opts || {}
    var list = (tracks || []).filter(function(t) { return t && t.videoId })
    if (!signedIn || !playlist || (list.length === 0 && !o.sourcePlaylistId)) return false
    var params = { playlistId: playlist.playlistId }
    if (list.length > 0) params.videoIds = list.map(function(t) { return t.videoId })
    if (o.sourcePlaylistId) params.sourcePlaylistId = String(o.sourcePlaylistId)
    if (o.duplicates === true) params.duplicates = true
    backend.request("addToPlaylist", params, function(reply) {
      if (!reply.ok && reply.error.code === "DUPLICATE" && !root.unsure(reply)) {
        if (!(callback && callback(reply) === true)) root.notify("Already in " + playlist.title, "Nothing was added")
        return
      }
      if (reply.ok) {
        storeItem.forgetCollection("playlist", playlist.playlistId)
        storeItem.forgetCollection("libraryLanding")  // S2: Recent activity lists playlists too
        root.notify("Added to " + playlist.title, o.sourceTitle ? String(o.sourceTitle)
          : list.length === 1 ? Model.mediaTitle(list[0]) : list.length + " songs")
      } else if (root.unsure(reply)) {
        root.refreshUnsure([{ kind: "playlist", id: playlist.playlistId }, { kind: "library" }], playlist.title)
      } else {
        root.fail(reply.error.message)
      }
      if (callback) callback(reply)
    }, list.length > 100 || o.sourcePlaylistId ? 60000 : 30000, noRetry)
    return true
  }

  function removeFromPlaylist(playlistId, tracks, callback) {
    var items = (tracks || []).filter(function(t) { return t && t.setVideoId })
      .map(function(t) { return { videoId: t.videoId, setVideoId: t.setVideoId } })
    if (!signedIn || !playlistId || items.length === 0) return false
    backend.request("removeFromPlaylist", { playlistId: playlistId, items: items }, function(reply) {
      if (reply.ok) {
        storeItem.forgetCollection("playlist", playlistId)
        storeItem.forgetCollection("libraryLanding")  // S2: Recent activity lists playlists too
      } else if (root.unsure(reply)) {
        root.refreshUnsure([{ kind: "playlist", id: playlistId }, { kind: "library" }])
      } else {
        root.fail(reply.error.message)
      }
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

  // ---- playlist edits (S13) -----------------------------------------------------------
  // The editor's Save (editPlaylist with only what changed), the invite link
  // (copied with wl-copy; never shown, notified or logged), J / K moves, a
  // playlist's owner settings and suggestions (reads), and joining from an
  // invite link (IPC join, the Library's new playlist field). Edits go with
  // noRetry. A definite error is the caller's to show, also once its view
  // has moved on (the editor keeps its fields or, closed, sends a notice; a
  // move is put back); an unknown outcome forgets the playlist and reads
  // the library again.

  property string _inviteTitle: ""
  // copyInvite's copied(ok), for the copy on its way.
  property var _inviteCopied: null

  // A copy (createPlaylist with sourcePlaylistId) gets its songs a few
  // seconds after it exists: the library is read again then, for its count.
  Timer {
    id: copyRefresh
    interval: 8000
    onTriggered: storeItem.loadLibrary(true)
  }

  // changes: editPlaylist's params besides playlistId. callback(reply) runs
  // after the Store dropped the playlist's pages.
  function editPlaylist(playlist, changes, callback) {
    if (!playlist || !playlist.playlistId || !_signedInFor("Couldn't change the playlist")) return false
    var params = { playlistId: playlist.playlistId }
    for (var key in changes || {}) params[key] = changes[key]
    backend.request("editPlaylist", params, function(reply) {
      if (reply.ok) {
        storeItem.forgetCollection("playlist", playlist.playlistId)
        // A new title or privacy shows in the library's list (and Recent activity).
        if (params.title !== undefined || params.privacy !== undefined) storeItem.loadLibrary(true)
        else storeItem.forgetCollection("libraryLanding")
      } else if (root.unsure(reply)) {
        root.refreshUnsure([{ kind: "playlist", id: playlist.playlistId }, { kind: "library" }], playlist.title || "")
      }
      if (callback) callback(reply)
    }, 30000, noRetry)
    return true
  }

  // Why the invite link cannot be copied ("" when it can).
  function inviteCopyProblem() {
    return missingTools.indexOf("wl-copy") >= 0 ? "Getting an invite link needs wl-copy (" + installCommand + ")" : ""
  }

  // Opens the playlist to collaborators and copies its invite link:
  // callback(reply) as editPlaylist's, then copied(ok) once wl-copy is done
  // (false: the link was not copied). Without wl-copy nothing is sent: the
  // link would be lost.
  function copyInvite(playlist, callback, copied) {
    var problem = inviteCopyProblem()
    if (problem !== "") {
      failAction("Couldn't copy the invite link", problem)
      return false
    }
    return editPlaylist(playlist, { collaboration: true }, function(reply) {
      if (callback) callback(reply)
      if (!reply.ok) return
      var started = !!reply.result.invite && root._copyInvite(String(reply.result.invite), playlist.title || "", copied)
      if (!started && copied) copied(false)
    })
  }

  function _copyInvite(url, title, copied) {
    var problem = inviteCopyProblem()
    if (problem !== "") {
      failAction("Couldn't copy the invite link", problem)
      return false
    }
    _inviteTitle = title
    _inviteCopied = copied || null
    inviteCopier.running = false
    inviteCopier.command = ["wl-copy", url]
    inviteCopier.running = true
    return true
  }

  Process {
    id: inviteCopier
    // The link is a secret: the notice names the playlist only.
    onExited: function(exitCode) {
      inviteCopier.command = []
      var copied = root._inviteCopied
      root._inviteCopied = null
      if (exitCode === 0) root.notify("Invite link copied", root._inviteTitle)
      else root.failAction("Couldn't copy the invite link", "wl-copy failed")
      if (typeof copied === "function") copied(exitCode === 0)
    }
  }

  // J / K on your playlist's page: params from Model.moveTrack.
  function movePlaylistItem(params, callback) {
    if (!signedIn || !params || !params.playlistId || !params.setVideoId) return false
    backend.request("movePlaylistItem", params, function(reply) {
      if (reply.ok || root.unsure(reply)) storeItem.forgetCollection("playlist", params.playlistId)
      if (callback) callback(reply)
    }, 30000, noRetry)
    return true
  }

  function playlistSettings(playlistId, callback) {
    if (!signedIn || !playlistId) return false
    backend.request("playlistSettings", { playlistId: playlistId }, callback, 30000)
    return true
  }

  function playlistSuggestions(playlistId, callback) {
    if (!signedIn || !playlistId) return false
    backend.request("playlistSuggestions", { playlistId: playlistId }, callback, 60000)
    return true
  }

  // An invite link (Model.parseInviteUrl): join that playlist; false when
  // the text is no invite link or nobody is signed in.
  function joinPlaylist(text, callback) {
    var invite = Model.parseInviteUrl(text)
    if (!invite) {
      failAction("Couldn't join the playlist", "That is not a playlist invite link")
      return false
    }
    if (!_signedInFor("Couldn't join the playlist")) return false
    backend.request("joinPlaylist", { playlistId: invite.playlistId, token: invite.token }, function(reply) {
      if (reply.ok) {
        storeItem.loadLibrary(true)
        root.notify("Joined the playlist", "It is in your library now")
      } else if (root.unsure(reply)) {
        root.refreshUnsure([{ kind: "library" }], "")
      } else {
        root.failAction("Couldn't join the playlist", reply.error.message)
      }
      if (callback) callback(reply)
    }, 30000, noRetry)
    return true
  }

  // ---- library actions (S4) ---------------------------------------------------------
  // Save to library (albums, playlists, mixes), Subscribe (artists), a
  // song's Save to library (through its row's library tokens) and Dislike,
  // done as setLike does: the Store shows the change at once, a definite
  // error puts it back, and an unknown outcome (the worker stopped or timed
  // out after the edit went out) forgets it and refreshes what it touched
  // ("Not sure the change applied; refreshed"). `callback(reply)` is the
  // owner's (Model.editCallbackOf): it reloads the page or Library list
  // shown when the outcome is unknown or a song's token was stale, and
  // returns true then (_ownerCallback). The Library list shown is read
  // again after every edit that changed it (_refreshLists).

  readonly property bool currentDisliked: !!currentTrack && storeItem.isDisliked(currentTrack.videoId, currentTrack.likeStatus)

  // fallback: what the item's page says (album or playlist header,
  // artist header), when it is shown.
  function savedOf(item, fallback) {
    return storeItem.savedState(item, fallback)
  }

  function subscribedOf(item, fallback) {
    return item ? storeItem.subscribedState(item.channelId, fallback) : null
  }

  function inLibraryOf(track) {
    return storeItem.inLibraryState(track)
  }

  function dislikedOf(track) {
    return !!track && !!track.videoId && storeItem.isDisliked(track.videoId, track.likeStatus)
  }

  // What saveAction and the menus need to know beyond the item: whether it
  // is a playlist of yours.
  function saveContext(item) {
    var kind = Model.menuKind(item)
    return { ownItem: (kind === "playlist" || kind === "mix") && storeItem.ownsPlaylist(Model.savePlaylistId(item)) }
  }

  // Panel.openItemMenu: the states the library entries' titles follow,
  // for the keys the view left unset.
  function fillMenuState(item, ctx) {
    if (!item || !ctx) return
    var kind = Model.menuKind(item)
    if (ctx.ownItem === undefined) ctx.ownItem = saveContext(item).ownItem
    if (ctx.saved === undefined && Model.SAVE_KINDS.indexOf(kind) >= 0) ctx.saved = savedOf(item)
    if (ctx.subscribed === undefined && Model.canSubscribe(item)) ctx.subscribed = subscribedOf(item)
    _fillPodcastState(item, ctx)  // S8
    if (ctx.pinned === undefined && Model.pinTokens(item)) ctx.pinned = pinnedOf(item)  // S14: Listen again
    if (kind !== "track") return
    if (ctx.inLibrary === undefined) ctx.inLibrary = inLibraryOf(item)
    if (ctx.liked === undefined) ctx.liked = likeStatusOf(item) === "LIKE"
    if (ctx.disliked === undefined) ctx.disliked = dislikedOf(item)
  }

  function _signedInFor(title) {
    if (signedIn) return true
    failAction(title, "Sign in to use your library")
    return false
  }

  // S: whatever the item offers (Model.saveAction), the other way round.
  function toggleSaveItem(item, callback) {
    var action = Model.saveAction(item, saveContext(item))
    if (action === "save") return saveItem(item, savedOf(item) !== true, callback)
    if (action === "subscribe") return subscribe(item, subscribedOf(item) !== true, callback)
    if (action === "saveSong") return setSongLibrary(item, inLibraryOf(item) !== true, callback)
    if (action === "savePodcast") return savePodcast(item, podcastSavedOf(item) !== true, callback)  // S8
    if (action === "saveEpisode") return saveEpisode(item, episodeSavedOf(item) !== true, callback)  // S8
    return false
  }

  // An album, playlist or mix into the library (save) or out of it.
  function saveItem(item, save, callback) {
    var params = Model.rateCollectionParams(item, save === true)
    var failTitle = save ? "Couldn't save to your library" : "Couldn't remove from your library"
    if (!params || !_signedInFor(failTitle)) return false
    var title = Model.itemTitle(item)
    var keys = Model.saveKeys(item)
    var previous = storeItem.setSaved(keys, save === true)
    var lists = params.kind === "album" ? ["albums", "songs", "artists"] : ["playlists"]
    backend.request("rateCollection", params, function(reply) {
      if (root.unsure(reply)) {
        storeItem.setSaved(keys, null)
        root._forgetSaved(params)
        var reloaded = root._ownerCallback(callback, reply)
        root._editUnsure(root._refreshLists(lists, true, reloaded) || reloaded, title)
        return
      }
      if (!reply.ok) {
        storeItem.restore("saved", previous)
        root.failAction(failTitle, reply.error.message)
        root._ownerCallback(callback, reply)
        return
      }
      root._forgetSaved(params)
      root._refreshLists(lists, false, false)
      root.notify(save ? "Saved to library" : "Removed from library", title)
      root._ownerCallback(callback, reply)
    }, 30000, noRetry)
    return true
  }

  // The worker dropped the same (its INVALIDATES). An album brings its
  // songs and artists into the library too: its artists' pages change
  // (From your library).
  function _forgetSaved(params) {
    if (params.kind === "album") storeItem.forgetCollection("album", params.browseId)
    if (params.playlistId) {
      storeItem.forgetCollection("playlist", params.playlistId)
      storeItem.forgetCollection("mix", params.playlistId)
    }
    storeItem.forgetCollection("libraryLanding")
    if (params.kind === "album") _forgetArtists(params.artistIds)
  }

  // The artist pages an album save or a song's library edit changed; every
  // one when the edit named none (the worker drops the same).
  function _forgetArtists(ids) {
    if (!ids || ids.length === 0) storeItem.forgetCollection("artist")
    else for (var i = 0; i < ids.length; i++) storeItem.forgetCollection("artist", ids[i])
  }

  // The Library lists ("playlists", "songs", "albums", "artists") an edit
  // changed. The one shown is read again now, the others when next shown;
  // after an unknown outcome (unsure) every one held is read again too, as
  // the saved states come from them. shownDone: the owner's callback has
  // already read the one shown again. True when this read the one shown.
  function _refreshLists(chips, unsure, shownDone) {
    var readShown = false
    for (var i = 0; i < chips.length; i++) {
      var chip = chips[i]
      var shown = storeItem.libraryShown === chip
      var held = chip === "playlists" ? storeItem.playlists.length > 0 : !!storeItem.libraryLists[chip]
      if (shown && shownDone) continue
      if (shown || (unsure && held)) {
        if (chip === "playlists") storeItem.loadLibrary(true)
        else storeItem.loadLibraryList(chip, true)
        readShown = readShown || shown
      } else if (chip === "playlists") {
        storeItem.invalidateLibrary()
      } else {
        storeItem.staleLibraryList(chip)
      }
    }
    return readShown
  }

  // The owner's callback (Model.editCallbackOf), if any: true when it read
  // its page again. Its view may be gone by now (Back while the edit was
  // on its way), and a throw never stops the reply's own handling.
  function _ownerCallback(callback, reply) {
    if (!callback) return false
    try {
      return callback(reply) === true
    } catch (e) {
      console.warn("ytmusic: edit callback failed: " + e)
      return false
    }
  }

  // The notice after an unknown outcome: "refreshed" only when what is
  // shown was read again (a page, a Library list). Search and Home rows
  // are not searched or read again: their item is to be opened again.
  function _editUnsure(reloaded, title) {
    if (reloaded) notify("Not sure the change applied; refreshed", title)
    else notify("Not sure the change applied; open it again to check", title)
  }

  // Subscribe to an artist, a podcast channel (S8) or a profile (S9), or unsubscribe.
  function subscribe(item, on, callback) {
    var params = Model.subscribeParams(item, on === true)
    var failTitle = on ? "Couldn't subscribe" : "Couldn't unsubscribe"
    if (!params || !_signedInFor(failTitle)) return false
    var title = Model.itemTitle(item)
    var channelId = item.channelId
    var previous = storeItem.setSubscribed(channelId, on === true)
    backend.request("subscribe", params, function(reply) {
      if (root.unsure(reply)) {
        storeItem.setSubscribed(channelId, null)
        root._forgetSubscribed(channelId)
        var reloaded = root._ownerCallback(callback, reply)
        root._editUnsure(root._refreshLists(["artists"], true, reloaded) || reloaded, title)
        return
      }
      if (!reply.ok) {
        storeItem.restore("subscribed", previous)
        root.failAction(failTitle, reply.error.message)
        root._ownerCallback(callback, reply)
        return
      }
      root._forgetSubscribed(channelId)
      root._refreshLists(["artists"], false, false)
      root.notify(on ? "Subscribed" : "Unsubscribed", title)
      root._ownerCallback(callback, reply)
    }, 30000, noRetry)
    return true
  }

  function _forgetSubscribed(channelId) {
    storeItem.forgetCollection("artist", channelId)
    storeItem.forgetCollection("librarySubscriptions")
    storeItem.forgetCollection("libraryLanding")
    // S8: a podcast channel's page and the library's channels.
    storeItem.forgetCollection("channel", channelId)
    storeItem.forgetCollection("libraryChannels")
    // S9: a profile's page and the library's profiles.
    storeItem.forgetCollection("user", channelId)
    storeItem.forgetCollection("libraryProfiles")
  }

  // A song into the library (add) or out of it, with the token its row
  // came with. YouTube refuses a stale one: the owner reloads its page
  // (once; the next press uses the new tokens).
  function setSongLibrary(track, add, callback) {
    var failTitle = add ? "Couldn't save the song" : "Couldn't remove the song"
    if (!track || !track.videoId || !_signedInFor(failTitle)) return false
    var params = Model.songLibraryParams(track, add === true)
    if (!params) {
      failAction(failTitle, "This list has no library control for it; open its album")
      return false
    }
    var title = Model.mediaTitle(track)
    var videoId = track.videoId
    var previous = storeItem.setInLibrary(videoId, add === true)
    var lists = ["songs", "albums", "artists"]
    backend.request("songLibrary", params, function(reply) {
      if (root.unsure(reply)) {
        storeItem.setInLibrary(videoId, null)
        root._forgetArtists(params.artistIds)
        var reloaded = root._ownerCallback(callback, reply)
        root._editUnsure(root._refreshLists(lists, true, reloaded) || reloaded, title)
        return
      }
      if (!reply.ok) {
        storeItem.restore("inLibrary", previous)
        if (!Model.staleToken(reply)) {
          root.failAction(failTitle, reply.error.message)
          root._ownerCallback(callback, reply)
        } else if (root._ownerCallback(callback, reply)) {
          root.notify("This page is out of date; refreshed", title)
        } else {
          root.failAction("This list is out of date", "Open it again to change " + title)
        }
        return
      }
      root._forgetArtists(params.artistIds)
      root._refreshLists(lists, false, false)
      root.notify(add ? "Saved to library" : "Removed from library", title)
      root._ownerCallback(callback, reply)
    }, 30000, noRetry)
    return true
  }

  // Dislike a song (on) or remove the dislike. The song playing skips to
  // the next one at once, as on the web.
  function dislike(track, on, callback) {
    var failTitle = on ? "Couldn't dislike the song" : "Couldn't remove the dislike"
    if (!track || !track.videoId || !_signedInFor(failTitle)) return false
    var videoId = track.videoId
    var title = Model.mediaTitle(track)
    var previousLike = storeItem.likes[videoId]
    var previous = storeItem.setDisliked(videoId, on === true)
    storeItem.setLike(videoId, on ? "DISLIKE" : "INDIFFERENT")
    var current = currentTrack
    if (on && hasMedia && current && current.videoId === videoId) root.next()
    root._rateSent(videoId)  // S11, as in setLike
    backend.request("rate", { videoId: videoId, rating: on ? "DISLIKE" : "INDIFFERENT" }, function(reply) {
      root._rateAnswered(videoId)  // S11
      if (root.unsure(reply)) {
        // A dislike cannot be read back: what YouTube reports stands.
        storeItem.setDisliked(videoId, null)
        root.refreshUnsure([{ kind: "liked", id: "LM" }, { kind: "like", id: videoId }], title)
      } else if (!reply.ok) {
        storeItem.restore("disliked", previous)
        storeItem.setLike(videoId, previousLike)
        root.failAction(failTitle, reply.error.message)
      } else {
        storeItem.forgetCollection("liked", "LM")
        root.notify(on ? "Disliked" : "Dislike removed", title)
      }
      if (callback) callback(reply)
    }, undefined, noRetry)
    return true
  }

  function toggleDislike(track) {
    return dislike(track, !dislikedOf(track))
  }

  // ---- recommendations (S14) -------------------------------------------------------
  // Pin to / Unpin from Listen again (any item whose row or page header
  // came with the tokens), Home's Not interested and Don't recommend (the
  // card leaves Home at once; Home's menu then has Undo), and adding
  // artists to the taste profile.
  // Sent with retry: false like every edit; a definite error puts the
  // change back, an unknown outcome reads again what it touched. A pin
  // edit's `callback` is the owner's (Model.editCallbackOf): it reloads its
  // page when the outcome is unknown or the token was stale.

  function pinnedOf(item) {
    return storeItem.pinnedState(item)
  }

  // pin: true pins, false unpins. source: Model.pinSource of the page the
  // token came from (its cached copy is dropped).
  function setPinned(item, pin, callback, source) {
    var failTitle = pin ? "Couldn't pin to Listen again" : "Couldn't unpin from Listen again"
    if (!item || !_signedInFor(failTitle)) return false
    var tokens = Model.pinTokens(item)
    var token = tokens ? (pin ? tokens.pin : tokens.unpin) : null
    if (!token) {
      failAction(failTitle, "This list has no Listen again control for it; open it again")
      return false
    }
    var title = Model.menuKind(item) === "track" ? Model.mediaTitle(item) : Model.itemTitle(item)
    var key = Model.pinKey(item)
    var previous = storeItem.setPinned(key, pin === true)
    var params = Model.feedbackParams(token, source)
    backend.request("feedback", params, function(reply) {
      if (root.unsure(reply)) {
        storeItem.setPinned(key, null)
        root._forgetPinned(params)
        root._editUnsure(root._ownerCallback(callback, reply), title)
        return
      }
      if (!reply.ok) {
        storeItem.restorePinned(previous)
        if (!Model.staleToken(reply)) {
          root.failAction(failTitle, reply.error.message)
          root._ownerCallback(callback, reply)
        } else if (root._ownerCallback(callback, reply)) {
          root.notify("This page is out of date; refreshed", title)
        } else {
          root.failAction("This list is out of date", "Open it again to change " + title)
        }
        return
      }
      root._forgetPinned(params)
      // YouTube is known to keep some unpinned items in Listen again.
      root.notify(pin ? "Pinned to Listen again" : "Unpinned from Listen again",
                  pin ? title : title + ". YouTube may keep showing it there for a while")
      root._ownerCallback(callback, reply)
    }, 30000, noRetry)
    return true
  }

  // What the worker dropped as well: Home (Listen again) and its See all,
  // and the page the token came from.
  function _forgetPinned(params) {
    storeItem.staleHome()
    storeItem.forgetCollection("browse", Model.LISTEN_AGAIN_ID)
    if (params.browseId) storeItem.forgetCollection("album", params.browseId)
    if (params.playlistId === "LM") storeItem.forgetCollection("liked")
    else if (params.playlistId) storeItem.forgetCollection("playlist", params.playlistId)
    if (params.channelId) storeItem.forgetCollection("artist", params.channelId)
  }

  // which: "notInterested" | "dontRecommend", on a Home card (its tokens
  // are Home's). The menu asked twice. Once YouTube took it, Home's menu
  // offers Undo (the card's Undo token, as the web's toast does).
  function homeFeedback(item, which) {
    var failTitle = which === "notInterested" ? "Couldn't mark it Not interested" : "Couldn't stop recommending it"
    if (!item || !_signedInFor(failTitle)) return false
    var token = Model.homeFeedbackToken(item, which)
    if (token === "") {
      failAction(failTitle, "Home is out of date; refresh it")
      return false
    }
    var title = Model.itemTitle(item)
    var undo = Model.homeUndoToken(item, which)
    var dropped = storeItem.dropHomeItem(item)
    backend.request("feedback", Model.feedbackParams(token, null), function(reply) {
      if (root.unsure(reply)) {
        // The card's fate is YouTube's: Home shows what it has now.
        storeItem.loadHome(true)
        root.notify("Not sure the change applied; refreshed", title)
        return
      }
      if (!reply.ok) {
        storeItem.restoreHomeItems(dropped)
        if (Model.staleToken(reply)) {
          storeItem.loadHome(true)
          root.failAction("Home is out of date; refreshed", "Choose it again on " + title)
        } else {
          root.failAction(failTitle, reply.error.message)
        }
        return
      }
      storeItem.staleHome(dropped ? dropped.chip : null)
      if (undo === "") {
        storeItem.homeUndo = null
        root.notify("Removed from Home", title)
        return
      }
      storeItem.homeUndo = { title: title, token: undo, dropped: dropped, at: Date.now() }
      root.notify("Removed from Home", title + ". To undo: Home's menu (" + Model.keyHint("pageMenu") + ")")
    }, 30000, noRetry)
    return true
  }

  // Home's menu (M): the last Not interested or Don't recommend taken back
  // with its Undo token. The card goes back where it was, or Home is read
  // again when that feed was read since.
  function undoHomeFeedback() {
    var failTitle = "Couldn't undo"
    if (!_signedInFor(failTitle)) return false
    var undo = storeItem.takeHomeUndo()
    if (!undo) {
      failAction(failTitle, "Nothing to undo on Home now")
      return false
    }
    var dropped = undo.dropped
    backend.request("feedback", Model.feedbackParams(undo.token, null), function(reply) {
      if (root.unsure(reply)) {
        storeItem.loadHome(true)
        root.notify("Not sure the undo applied; refreshed", undo.title)
        return
      }
      if (!reply.ok) {
        if (Model.staleToken(reply)) storeItem.loadHome(true)
        root.failAction(failTitle, Model.staleToken(reply) ? "YouTube no longer takes it back; Home refreshed"
                                                           : reply.error.message)
        return
      }
      if (storeItem.restoreHomeItems(dropped)) storeItem.staleHome(dropped.chip)
      else {
        storeItem.staleHome()
        storeItem.loadHome(true)
      }
      root.notify("Back on Home", undo.title)
    }, 30000, noRetry)
    return true
  }

  // names: as tasteProfile served them. callback(reply) is the taste
  // page's; it returns true when it read the list again.
  function setTasteProfile(names, callback) {
    var failTitle = "Couldn't add the artists"
    if (!names || names.length === 0 || !_signedInFor(failTitle)) return false
    var what = names.length === 1 ? names[0] : names.length + " artists"
    backend.request("setTasteProfile", { artists: names }, function(reply) {
      if (root.unsure(reply)) {
        storeItem.staleHome()
        storeItem.forgetCollection("taste")
        root._editUnsure(root._ownerCallback(callback, reply), what)
        return
      }
      if (!reply.ok) {
        // A name no longer suggested (BAD_REQUEST): the list is read again.
        if (reply.error.code === "BAD_REQUEST") storeItem.forgetCollection("taste")
        root.failAction(failTitle, reply.error.message)
        root._ownerCallback(callback, reply)
        return
      }
      storeItem.staleHome()
      storeItem.forgetCollection("taste")
      root.notify("Added to your taste", what + ". Home will recommend more like them")
      root._ownerCallback(callback, reply)
    }, 30000, noRetry)
    return true
  }

  // Open the panel on the search tab with results for `query`.
  function showSearch(query) {
    var text = String(query || "").trim()
    // S7: YouTube Music, whichever scope the panel's Search was left in.
    if (text !== "") store.search(text, "", "")
    store.requestTab("search")
    if (shell && typeof shell.summon === "function") shell.summon(pluginId)
    return true
  }

  // ---- uploads (S10) -------------------------------------------------------------------
  // Upload a file (the Uploads chip's path field, IPC upload): one request
  // with the whole file on the worker's upload lane, never sent again (10
  // min before the shell gives up on the reply); YouTube lists the song
  // once it has processed it, minutes later. Delete upload (the menu, after
  // a second press): the row leaves the Uploads chip at once and comes back
  // when the worker says no. Either drops every uploads page held.
  // `uploading`: the file names on their way. `_accountStreamsFailed`: the
  // worker could not resolve an upload with the account (its yt-dlp lacks
  // what uploads need), so later uploads go straight to mpv's yt-dlp.
  // `mpvCookiesPath`: what an upload's loadfile hands mpv's yt-dlp while
  // signed in: bin/ytm-mpv's copy of the session's cookies.txt in mpv's
  // runtime directory, never the session file (yt-dlp writes its cookie
  // jar back into the file it is given, and would bring a signed-out
  // session's file back). The copy is made again whenever the sign-in
  // changes and goes on sign-out; signed out, loadfiles carry no cookies.
  readonly property string mpvCookiesPath: Quickshell.env("XDG_RUNTIME_DIR") + "/omarchy-ytmusic/cookies-mpv.txt"
  property var uploading: []
  property bool _accountStreamsFailed: false

  function _syncMpvCookies() {
    Model.setUploadCookies(signedIn ? mpvCookiesPath : "")
    Quickshell.execDetached([rootDir + "/bin/ytm-mpv", "cookies"])
  }

  // false when nothing was sent (signed out, a path that cannot be a music
  // file: the notice says why).
  function uploadFile(path, callback) {
    var text = String(path || "").trim()
    if (!signedIn) {
      notify("Sign in to upload", "")
      return false
    }
    var problem = Model.uploadPathProblem(text)
    if (problem !== "") {
      failAction("Cannot upload that file", problem)
      return false
    }
    var name = Model.uploadFileName(text)
    uploading = uploading.concat([name])
    notify("Uploading", name)
    backend.request("uploadSong", { path: text }, function(reply) {
      var left = root.uploading.slice()
      var at = left.indexOf(name)
      if (at >= 0) left.splice(at, 1)
      root.uploading = left
      if (reply.ok) {
        storeItem.forgetUploads()
        root.notify(Model.UPLOAD_PROCESSING, reply.result && reply.result.name ? reply.result.name : name)
      } else if (root.unsure(reply)) {
        storeItem.forgetUploads()
        root.notify("Not sure the upload went through", "Look in Uploads in a few minutes before you try again")
      } else {
        root.failAction("Upload failed", reply.error.message)
      }
      if (callback) callback(reply)
    }, Model.UPLOAD_TIMEOUT_MS, noRetry)
    return true
  }

  function deleteUpload(item, callback) {
    var entityId = Model.uploadEntityId(item)
    if (!signedIn || entityId === "") return false
    var taken = storeItem.takeUpload(item)
    backend.request("deleteUpload", { entityId: entityId }, function(reply) {
      if (reply.ok) {
        storeItem.forgetUploads()
        root.notify("Deleted from your uploads", Model.itemTitle(item))
      } else if (root.unsure(reply)) {
        storeItem.forgetUploads()
        root.notify("Not sure the change applied; refreshed", Model.itemTitle(item))
      } else {
        storeItem.putBackUpload(taken)
        root.failAction("Could not delete the upload", reply.error.message)
      }
      if (callback) callback(reply)
    }, 30000, noRetry)
    return true
  }

  // ---- podcasts (S8) -----------------------------------------------------------------
  // Where each played episode was left (Model.withPosition), kept in
  // ~/.cache/omarchy-ytmusic/positions.json (at most Model.POSITIONS_MAX):
  // saved every 15 s while an episode plays, when it pauses, and before a
  // stop, a skip or a new queue takes it away; one played to its end is
  // kept as played. An episode started from a list or its page goes on
  // from there, else from YouTube's progress (Model.episodeResume, mpv's
  // start=<pct>%); nothing goes back to YouTube. A chapter row seeks in
  // the episode playing, or plays its episode from there. Saving a podcast
  // (savePodcast) and Episodes for Later (saveEpisode, unsaveEpisode) work
  // as S4's edits: shown at once, put back on a definite error, forgotten
  // and read again when the outcome is unknown.

  property var positions: ({})
  property bool positionsLoaded: false
  readonly property bool episodePlaying: playing && Model.isEpisode(currentTrack)

  function episodeResumeOf(track) {
    return Model.episodeResume(track, positions)
  }

  // A loadfile's start for `track`: `start` when given ({ seconds },
  // { percent }; false for the beginning), else where it was left.
  function _episodeStart(track, start) {
    if (start === false) return null
    if (start && typeof start === "object") return start
    return Model.isEpisode(track) ? episodeResumeOf(track) : null
  }

  // The playing episode's place. mpv answers before any command sent after
  // this one, so a stop or skip right after still finds it playing.
  function savePosition() {
    var track = currentTrack
    if (!mpv.up || !positionsLoaded || !Model.isEpisode(track)) return
    var videoId = track.videoId
    mpv.command(["get_property", "time-pos"], function(reply) {
      if (!reply || typeof reply.data !== "number") return
      if (!root.currentTrack || root.currentTrack.videoId !== videoId) return
      var length = root.duration > 0 ? root.duration : Number(track.durationSec) || 0
      root._setPositions(Model.withPosition(root.positions, videoId, reply.data, length, Date.now()))
    })
  }

  function _setPositions(next) {
    positions = next
    positionsFile.setText(Model.positionsText(next) + "\n")
  }

  // mpv's end-file for an entry played to its end: an episode is played.
  function _episodeEnded(message) {
    var entry = Model.entryById(queue, message.playlist_entry_id)
    if (!positionsLoaded || !entry || !Model.isEpisode(entry.track)) return
    _setPositions(Model.withFinished(positions, entry.track.videoId, entry.track.durationSec, Date.now()))
  }

  // An episode on its own (its page's Play, Resume or Play from start).
  // The episode playing stays where it is, with its queue: it goes on if
  // paused, or back to its start (start false) or to { seconds }.
  function playEpisode(track, start) {
    if (!Model.isEpisode(track)) return false
    if (isCurrentEpisode(track)) {
      if (start === false) seek(0)
      else if (start && typeof start === "object" && start.seconds !== undefined) seek(start.seconds)
      if (paused) mpv.command(["set_property", "pause", false])
      return true
    }
    return playCollection([track], 0, false, { playlistId: null, title: track.title || "", radio: false }, null, -1, start)
  }

  function isCurrentEpisode(track) {
    return hasMedia && Model.isEpisode(track) && !!currentTrack && currentTrack.videoId === track.videoId
  }

  // A chapter row (Model.chapterAction): a seek in the episode playing,
  // else `episode` (the page's) from the chapter's second.
  function playChapter(chapter, episode) {
    var action = Model.chapterAction(chapter, currentTrack ? currentTrack.videoId : "")
    if (action.action === "seek") {
      seek(action.seconds)
      if (paused) mpv.command(["set_property", "pause", false])
      return true
    }
    if (!Model.isEpisode(episode) || episode.videoId !== action.videoId) return false
    return playEpisode(episode, { seconds: action.seconds })
  }

  function podcastSavedOf(item, fallback) {
    return storeItem.podcastSavedState(Model.podcastIdOf(item), fallback)
  }

  function episodeSavedOf(track, fallback) {
    return track && track.videoId ? storeItem.episodeSavedState(track.videoId, fallback) : null
  }

  // fillMenuState: the podcast's and the episode's saved states.
  function _fillPodcastState(item, ctx) {
    if (ctx.podcastSaved === undefined && Model.menuKind(item) === "podcast") ctx.podcastSaved = podcastSavedOf(item)
    if (ctx.episodeSaved === undefined && Model.isEpisode(item)) ctx.episodeSaved = episodeSavedOf(item)
  }

  // A podcast into the library (save) or out of it.
  function savePodcast(item, save, callback) {
    var params = Model.savePodcastParams(item, save === true)
    var failTitle = save ? "Couldn't save the podcast" : "Couldn't remove the podcast"
    if (!params || !_signedInFor(failTitle)) return false
    var title = Model.itemTitle(item)
    var podcastId = params.podcastId
    var previous = storeItem.setPodcastSaved(podcastId, save === true)
    backend.request("savePodcast", params, function(reply) {
      if (root.unsure(reply)) {
        storeItem.setPodcastSaved(podcastId, null)
        root._forgetPodcast(podcastId)
        var reloaded = root._ownerCallback(callback, reply)
        root._editUnsure(root._refreshLists(["podcasts"], true, reloaded) || reloaded, title)
        return
      }
      if (!reply.ok) {
        storeItem.restoreSaves("podcastSaves", previous)
        root.failAction(failTitle, reply.error.message)
        root._ownerCallback(callback, reply)
        return
      }
      root._forgetPodcast(podcastId)
      root._refreshLists(["podcasts"], false, false)
      root.notify(save ? "Saved to library" : "Removed from library", title)
      root._ownerCallback(callback, reply)
    }, 30000, noRetry)
    return true
  }

  // The worker dropped the same (its INVALIDATES).
  function _forgetPodcast(podcastId) {
    storeItem.forgetCollection("podcast", "MPSP" + podcastId)
    storeItem.forgetCollection("newEpisodes")
    storeItem.forgetCollection("libraryLanding")
  }

  // An episode into Episodes for Later (save) or out of it. Removing names
  // its entry there: the one a save made here named (newer than any page),
  // else `setVideoId` (the Episodes for Later page's row), else the held
  // Episodes for Later's, else Episodes for Later is read first.
  function saveEpisode(track, save, callback, setVideoId) {
    var failTitle = save ? "Couldn't save the episode" : "Couldn't remove the episode"
    if (!Model.isEpisode(track) || !_signedInFor(failTitle)) return false
    var known = save ? "" : (storeItem.episodeSetIds[track.videoId] || setVideoId || storeItem.savedEpisodeSetId(track.videoId))
    if (save || known) {
      _sendEpisodeEdit(track, save === true, known, callback)
      return true
    }
    storeItem.loadCollection(Model.pageRef({ type: "savedEpisodes" }), function(reply) {
      var found = reply.ok ? Model.savedEpisodeSetId(reply.result, track.videoId) : ""
      if (found) {
        root._sendEpisodeEdit(track, false, found, callback)
        return
      }
      if (reply.ok) storeItem.setEpisodeSaved(track.videoId, false)
      root.failAction(failTitle, reply.ok ? "It is not in Episodes for Later" : reply.error.message)
    }, true)
    return true
  }

  function _sendEpisodeEdit(track, save, setVideoId, callback) {
    var failTitle = save ? "Couldn't save the episode" : "Couldn't remove the episode"
    var title = Model.itemTitle(track)
    var videoId = track.videoId
    var previous = storeItem.setEpisodeSaved(videoId, save)
    var params = save ? { videoId: videoId } : { videoId: videoId, setVideoId: setVideoId }
    backend.request(save ? "saveEpisode" : "unsaveEpisode", params, function(reply) {
      if (root.unsure(reply)) {
        storeItem.setEpisodeSaved(videoId, null)
        storeItem.rememberSetVideoId(videoId, "")
        root._forgetEpisode(videoId)
        var reloaded = root._ownerCallback(callback, reply)
        root._editUnsure(root._refreshLists(["podcasts"], true, reloaded) || reloaded, title)
        return
      }
      if (!reply.ok) {
        storeItem.restoreSaves("episodeSaves", previous)
        root.failAction(failTitle, reply.error.message)
        root._ownerCallback(callback, reply)
        return
      }
      storeItem.rememberSetVideoId(videoId, save && reply.result ? reply.result.setVideoId : "")
      root._forgetEpisode(videoId)
      root._refreshLists(["podcasts"], false, false)
      root.notify(save ? "Saved to Episodes for Later" : "Removed from Episodes for Later", title)
      root._ownerCallback(callback, reply)
    }, 30000, noRetry)
  }

  // Episodes for Later and the episode's page (the worker drops the same;
  // podcast pages hold no saved state of their episodes).
  function _forgetEpisode(videoId) {
    storeItem.forgetCollection("savedEpisodes")
    storeItem.forgetCollection("episode", videoId)
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
    savePosition()  // S8
    mpv.command(["playlist-next", "weak"])
    return true
  }

  function previous() {
    if (!hasMedia) return false
    savePosition()  // S8
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
      flushPlays(true)  // S15: plays waiting for history go now
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
    savePosition()  // S8: answered before the stop
    mpv.command(["stop"])
    return true
  }

  function jumpTo(index) {
    if (!mpv.up || index < 0 || index >= playlistCount) return false
    if (index !== playlistPos) savePosition()  // S8
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
    else if (name === "speed") { if (typeof value === "number") speed = value }  // S17
    else if (name === "user-data/ytm/stale" && Array.isArray(value) && value.length > 0) {
      // mpv/ytm-streams.lua dropped streams that failed to play.
      dropStreams(value)
      mpv.command(["set_property", "user-data/ytm/stale", []])
    }
  }

  function onMpvEvent(message) {
    _historyEvent(message)  // S15
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
    } else if (message.event === "end-file" && message.reason === "eof") {
      _episodeEnded(message)  // S8
    }
  }

  function onMpvReady() {
    applyRepeat()
    _readMpvPid()  // S12
    _playbackReady()  // S17
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
    _playbackGone()  // S17
    _historyGone()  // S15
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
        autoplayNote: autoplayNote,
        // S17
        speed: speed,
        speedKind: speedKind,
        normalize: normalize,
        // The song playing's YouTube gain in dB (mpv applies it while normalize is youtube).
        gain: currentTrack && streams[currentTrack.videoId] && typeof streams[currentTrack.videoId].gain === "number"
          ? streams[currentTrack.videoId].gain : null
      },
      lastError: lastError,
      // S15: plays recorded in YouTube Music history.
      history: { record: recordHistory, pending: pendingPlays.length, reporting: _reporting, noticeShown: historyNoticeShown },
      // S16: the identity in use (no names), the audio quality and the content location.
      account: { identity: identity ? { brand: identity.brand === true, own: identity.own !== false, authUser: identity.authUser } : null,
                 quality: quality, ytdlFormat: ytdlFormat, location: location, accounts: accounts ? accounts.items.length : null },
      streams: {
        fresh: Object.keys(Model.pruneStreams(streams, nowSec(), 50)),
        resolving: resolvingCount,
        pending: pendingTitle,
        speculated: speculated
      },
      cache: {
        thumbs: storeItem.thumbCount,
        searches: storeItem.searchCacheSize,
        // Library (S2): items held per chip list.
        libraryLists: storeItem.libraryListSizes()
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
    observedProperties: ["pause", "idle-active", "playlist-pos", "playlist-count", "media-title", "duration", "volume", "paused-for-cache", "loop-file", "loop-playlist", "user-data/ytm/stale",
      "speed"]  // S17

    onPropertyUpdated: (name, value) => root.onMpvProperty(name, value)
    onEvent: message => root.onMpvEvent(message)
    onReady: root.onMpvReady()
    onMpvStateChanged: if (mpvState === "down") root.resetPlayback()
  }

  Backend {
    id: backend
    launcher: root.rootDir + "/bin/ytm"
    // S17: the worker's settings (Model.workerSettings; S16 adds its keys
    // there), once the prefs are read.
    configureParams: root.prefsLoaded ? JSON.parse(root.workerSettingsText) : null
  }

  // Search results and opened collections, shared by every panel.
  readonly property var store: storeItem

  Store {
    id: storeItem
    worker: backend
    thumbsEnabled: root.panelsOpen > 0
    stateDir: root.cacheDir
    nowKeep: root.currentTrack ? root.currentTrack.videoId : ""
    // Library (S2): each chip's sort order, saved in player.json.
    libraryOrders: root.prefs.libraryOrders
  }

  Connections {
    target: storeItem
    function onTrackHinted(track) { root.speculate(track) }
    // Library (S2): O on a chip.
    function onLibraryOrderChosen(chip, order) {
      root.setPref("libraryOrders", Model.withLibraryOrder(root.prefs.libraryOrders, chip, order))
    }
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

  // S8: where each played episode was left.
  FileView {
    id: positionsFile
    path: root.cacheDir + "/positions.json"
    atomicWrites: true
    watchChanges: false
    printErrors: false
    onLoaded: {
      root.positions = Model.parsePositions(text())
      root.positionsLoaded = true
    }
    onLoadFailed: root.positionsLoaded = true
  }

  Timer {
    interval: 15000
    repeat: true
    running: root.episodePlaying
    onTriggered: root.savePosition()
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
    _syncMpvCookies()  // S10: mpv's yt-dlp plays uploads as the account
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
    function recordPlays(): string { root.setRecordHistory(!root.recordHistory); return root.recordHistory ? "on" : "off" }  // S15
    function search(query: string): string { return root.showSearch(query) ? "ok" : "unhandled" }
    function like(): string { return root.setLike(root.currentTrack, true) ? "ok" : "unhandled" }
    function unlike(): string { return root.setLike(root.currentTrack, false) ? "ok" : "unhandled" }
    function toggleLike(): string { return root.toggleLike(root.currentTrack) ? "ok" : "unhandled" }
    function reloadAuth(): string { return root.reloadAuth() ? "ok" : "unhandled" }
    function signIn(): string { return root.signIn() ? "ok" : "unhandled" }
    function signOut(): string { return root.signOut() ? "ok" : "unhandled" }
    function join(url: string): string {  // S13: a playlist invite link
      return !Model.parseInviteUrl(url) ? "invalid" : root.joinPlaylist(url) ? "ok" : "unhandled"
    }
    // S10: a music file (its full path, or one from ~/) to your uploads.
    function upload(path: string): string {
      var problem = Model.uploadPathProblem(path)
      return problem !== "" ? problem : root.uploadFile(path) ? "ok" : "unhandled"
    }
  }
}
