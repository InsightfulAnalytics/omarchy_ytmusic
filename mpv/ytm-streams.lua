-- Play the stream URLs the shell resolved through the worker
-- (user-data/ytm/streams: videoId -> { url, expires }). The playlist keeps
-- the youtube.com URL, so `path`, cover art over MPRIS and the shell's
-- videoId mapping stay intact.
--
-- mpv never runs yt-dlp itself (bin/ytm-mpv passes --ytdl=no): its ytdl
-- hook would put the video id on yt-dlp's command line, which every local
-- account can read, and would need a copy of the session's cookies. When a
-- track loads without a fresh stream, this script holds the load (a deferred
-- on_load hook), asks the shell for one (user-data/ytm/want: { id, n }) and
-- goes on once the stream arrives, or fails the load when the shell answers
-- that there is none (user-data/ytm/unavailable), after WAIT_SECS, or when
-- playback is stopped meanwhile (a skip).
--
-- A pre-resolved URL can go bad (expired after a suspend, network changed):
-- the stream is dropped, reported in user-data/ytm/stale for the shell, and
-- the same entry is loaded once more, which asks for a fresh one.
local WAIT_SECS = 25
-- What a load with no stream opens instead: fails at once, opens nothing.
local NO_STREAM = "/dev/null/ytm-no-stream"

local redirected = {} -- stream URL -> videoId
local retried = {}    -- videoId -> true: each gets one retry
-- { pos, entry }: the entry whose stream just failed. Only that entry's own
-- end-file may use it; anything else (the shell stopped playback, you skipped)
-- drops it, or a later, unrelated failure would restart an old position.
local retry = nil
-- { id, hook, timer }: the load held while the shell resolves its stream.
local waiting = nil
local asked = 0

local function video_id(url)
  if type(url) ~= "string" or not url:match("^https://www%.youtube%.com/watch%?") then return nil end
  return url:match("[?&]v=([%w_%-]+)")
end

-- Only an https URL on YouTube's video servers (googlevideo.com; uploads
-- stream from *.c.youtube.com): mpv opens any protocol it is given
-- (file://, lavfi, edl, ...).
local function stream_url_ok(url)
  if type(url) ~= "string" then return false end
  return url:match("^https://[%w%-%.]+%.googlevideo%.com/") ~= nil
    or url:match("^https://[%w%-%.]+%.c%.youtube%.com/") ~= nil
end

local function streams()
  local all = mp.get_property_native("user-data/ytm/streams")
  return type(all) == "table" and all or {}
end

local function fresh(id)
  local stream = streams()[id]
  if type(stream) ~= "table" or not stream_url_ok(stream.url) then return nil end
  if (tonumber(stream.expires) or 0) < os.time() + 60 then return nil end
  return stream.url
end

local function mark_stale(id)
  local all = streams()
  all[id] = nil
  mp.set_property_native("user-data/ytm/streams", all)
  local stale = mp.get_property_native("user-data/ytm/stale")
  if type(stale) ~= "table" then stale = {} end
  stale[#stale + 1] = id
  mp.set_property_native("user-data/ytm/stale", stale)
end

local function play(id, url)
  redirected[url] = id
  mp.set_property("stream-open-filename", url)
end

-- Ends the held load: with the stream's URL, or (nil) failing it.
local function finish(url)
  local held = waiting
  if not held then return end
  waiting = nil
  held.timer:kill()
  if url then
    play(held.id, url)
  else
    mp.set_property("stream-open-filename", NO_STREAM)
  end
  held.hook:cont()
end

mp.add_hook("on_load", 5, function(hook)
  local id = video_id(mp.get_property("stream-open-filename", ""))
  if not id then return end
  local url = fresh(id)
  if url then
    play(id, url)
    return
  end
  finish(nil) -- a load still held (cannot happen; never leave one hanging)
  hook:defer()
  asked = asked + 1
  waiting = { id = id, hook = hook, timer = mp.add_timeout(WAIT_SECS, function() finish(nil) end) }
  mp.set_property_native("user-data/ytm/want", { id = id, n = asked })
end)

mp.observe_property("user-data/ytm/streams", "native", function()
  if waiting then
    local url = fresh(waiting.id)
    if url then finish(url) end
  end
end)

mp.observe_property("user-data/ytm/unavailable", "native", function(_, value)
  if waiting and type(value) == "table" and value.id == waiting.id then finish(nil) end
end)

-- A skip or stop while a load is held: mpv waits for the hook, so end it now.
mp.observe_property("playback-abort", "bool", function(_, value)
  if value and waiting then finish(nil) end
end)

mp.add_hook("on_load_fail", 5, function()
  local url = mp.get_property("stream-open-filename", "")
  local id = redirected[url]
  if not id then return end
  redirected[url] = nil
  mp.msg.warn("pre-resolved stream for " .. id .. " failed; asking for a fresh one")
  mark_stale(id)
  if not retried[id] then
    retried[id] = true
    local pos = mp.get_property_number("playlist-playing-pos")
    if pos and pos >= 0 then
      retry = { pos = pos, entry = mp.get_property_number("playlist/" .. pos .. "/id") }
    end
  end
end)

mp.register_event("end-file", function(event)
  local pending = retry
  retry = nil
  if not pending or event.reason ~= "error" or event.playlist_entry_id ~= pending.entry then return end
  -- Replays the same playlist entry: its on_load finds no stream (it was
  -- dropped) and asks the shell for a fresh one.
  mp.commandv("playlist-play-index", tostring(pending.pos))
end)
