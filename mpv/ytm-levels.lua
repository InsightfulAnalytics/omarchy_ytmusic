-- Loudness levelling for damir.ytmusic, set by the shell in
-- user-data/ytm/normalize (its prefs.normalize):
--   "youtube"  each song is turned down by what YouTube measured above its
--              loudness target: the stream's `gain` (dB, never above 0) in
--              user-data/ytm/streams, set as the file's own volume-gain when
--              it loads, so it ends with the file. A song without a resolved
--              stream (played through yt-dlp) is left as it is.
--   "dynamic"  mpv's dynaudnorm filter, labelled @ytmlevels in `af`.
--   "off"      neither.
-- A new mode, or a stream with a gain arriving for the song playing, applies
-- at once.
local LABEL = "ytmlevels"
local FILTER = "@" .. LABEL .. ":lavfi=[dynaudnorm]"
local MAX_GAINS = 500

-- The shell loads this script into a player started before it existed (mpv
-- outlives plugin updates) when this mark is missing; a copy that finds it
-- leaves the work to the first.
if mp.get_property_native("user-data/ytm/levels") == true then return end
mp.set_property_native("user-data/ytm/levels", true)

local mode = "off"

-- videoId -> gain, as last seen in the streams map: a stream that failed
-- leaves the map (mpv/ytm-streams.lua), and its retry through yt-dlp keeps
-- the song's gain.
local gains = {}
local gain_count = 0

local function video_id(url)
  return url and url:match("[?&]v=([%w_%-]+)")
end

local function gain_of(id)
  if not id then return nil end
  local all = mp.get_property_native("user-data/ytm/streams")
  local stream = type(all) == "table" and all[id] or nil
  if type(stream) == "table" and type(stream.gain) == "number" then
    if gains[id] == nil then
      if gain_count >= MAX_GAINS then
        gains = {}
        gain_count = 0
      end
      gain_count = gain_count + 1
    end
    gains[id] = stream.gain
  end
  return gains[id]
end

-- The volume-gain of the file being loaded or played.
local function apply_gain()
  local id = video_id(mp.get_property("path"))
  local wanted = 0
  if mode == "youtube" then wanted = gain_of(id) or 0 end
  local now = mp.get_property_number("volume-gain", 0)
  if math.abs(now - wanted) > 0.005 then
    mp.set_property_number("file-local-options/volume-gain", wanted)
  end
end

local function has_filter()
  local filters = mp.get_property_native("af")
  if type(filters) ~= "table" then return false end
  for _, filter in ipairs(filters) do
    if filter.label == LABEL then return true end
  end
  return false
end

local function apply_filter()
  local present = has_filter()
  if mode == "dynamic" and not present then
    mp.commandv("af", "add", FILTER)
  elseif mode ~= "dynamic" and present then
    mp.commandv("af", "remove", "@" .. LABEL)
  end
end

local function loaded()
  return mp.get_property("path") ~= nil and not mp.get_property_bool("idle-active", true)
end

mp.add_hook("on_load", 5, apply_gain)

mp.observe_property("user-data/ytm/normalize", "native", function(_, value)
  mode = (value == "youtube" or value == "dynamic") and value or "off"
  apply_filter()
  if loaded() then apply_gain() end
end)

mp.observe_property("user-data/ytm/streams", "native", function()
  if mode == "youtube" and loaded() then apply_gain() end
end)
