--[[
ScriptCutter <-> DaVinci Resolve.

All timeline work happens here, inside Resolve's own script engine, so it
works in both the free and the Studio version:

  * Studio: the ScriptCutter app runs this file through Resolve's `fuscript`:
      fuscript -l lua scriptcutter.lua context <out.json>
      fuscript -l lua scriptcutter.lua build   <plan.lua> <result.json>
  * Free: the Workspace > Scripts > ScriptCutter menu entry loads this file (it
    sets SCRIPTCUTTER_EMBEDDED) and calls run() directly, because the free
    version only allows scripts started from inside Resolve.

M.context() reads the open timeline (which files, which parts, frame rates).
M.build(plan) creates a new timeline from the plan the app wrote: one clip per
call, Blue / Brown clip colours, markers on brown clips, optional A/B overlap
(alternating V1/A1 and V2/A2) and AI Voice Isolation on the audio tracks.
]]

local M = {}

local REVIEW_COLORS = { "Brown", "Chocolate", "Tan", "Orange" }  -- first one Resolve accepts wins
local SURE_COLOR = "Blue"
local MARKER_COLOR = "Cocoa"

-- ------------------------------------------------------------------ helpers
local function json(v)
  local t = type(v)
  if t == "nil" then return "null" end
  if t == "boolean" then return v and "true" or "false" end
  if t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    return string.format("%.14g", v)
  end
  if t == "string" then
    local escapes = { ['"'] = '\\"', ["\\"] = "\\\\", ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t" }
    return '"' .. (v:gsub('[%c"\\]', function(c) return escapes[c] or string.format("\\u%04x", c:byte()) end)) .. '"'
  end
  if t == "table" then
    local parts = {}
    if #v > 0 or next(v) == nil then
      for i = 1, #v do parts[i] = json(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    for k, val in pairs(v) do parts[#parts + 1] = json(tostring(k)) .. ":" .. json(val) end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return "null"
end
M.json = json

function M.write_file(path, text)
  local f = assert(io.open(path, "wb"))
  f:write(text)
  f:close()
end

-- Resolve's API tables carry an extra numeric entry next to the objects; keep only the objects
local function objects(t)
  local out = {}
  for _, v in ipairs(t or {}) do out[#out + 1] = v end
  if #out == 0 then
    for _, v in pairs(t or {}) do
      if type(v) ~= "number" then out[#out + 1] = v end
    end
  end
  return out
end

local function round(x) return math.floor(x + 0.5) end

local function normalize_path(p)
  return (tostring(p or ""):gsub("\\", "/"):lower())
end

local function get_resolve()
  local ok, r = pcall(function() return Resolve() end)
  if ok and r then return r end
  if bmd and bmd.scriptapp then return bmd.scriptapp("Resolve") end
  return nil
end

local function open_project()
  local r = get_resolve()
  if not r then
    return nil, nil, { ok = false, code = "not_connected", error = "DaVinci Resolve is not reachable." }
  end
  local proj = r:GetProjectManager():GetCurrentProject()
  if not proj then
    return r, nil, { ok = false, code = "no_project", error = "Open a project in DaVinci Resolve first." }
  end
  return r, proj, nil
end

local function wait(seconds)
  if bmd and bmd.wait then bmd.wait(seconds) end
end

-- source range of a timeline item, in source frames (end exclusive)
-- (Resolve reports NTSC sources in their rounded rate: 29.97 counts like 30)
local function source_range(item)
  local ok1, s = pcall(function() return item:GetSourceStartFrame() end)
  local ok2, e = pcall(function() return item:GetSourceEndFrame() end)
  if ok1 and ok2 and s and e then return s, e + 1 end
  local left = item:GetLeftOffset() or 0  -- Resolve before 18.5: approximate
  return left, left + item:GetDuration()
end

-- ------------------------------------------------------------------ read the open timeline
function M.context()
  local r, proj, err = open_project()
  if err then return err end
  local tl = proj:GetCurrentTimeline()
  if not tl then
    return { ok = false, code = "no_timeline", error = "Open a timeline in DaVinci Resolve first.",
             product = r:GetProductName(), project = proj:GetName() }
  end
  local items = {}
  for track = 1, tl:GetTrackCount("video") do
    local list = objects(tl:GetItemListInTrack("video", track))
    table.sort(list, function(a, b) return a:GetStart() < b:GetStart() end)
    for _, it in ipairs(list) do
      local mpi = it:GetMediaPoolItem()
      local path = mpi and mpi:GetClipProperty("File Path") or ""
      -- a clip the editor disabled on the timeline is not meant to be in the video
      local ok, enabled = pcall(function() return it:GetClipEnabled() end)
      if path ~= "" and not (ok and enabled == false) then
        local s, e = source_range(it)
        items[#items + 1] = { track = track, name = it:GetName(), path = path,
                              fps = tonumber(mpi:GetClipProperty("FPS")) or 0, src_in = s, src_out = e }
      end
    end
  end
  -- a timeline can have its own frame rate; the project's is the default for new timelines
  local tl_fps = tonumber(tl:GetSetting("timelineFrameRate")) or tonumber(proj:GetSetting("timelineFrameRate")) or 24
  return {
    ok = true, product = r:GetProductName(), version = r:GetVersionString(), project = proj:GetName(),
    timeline = tl:GetName(), timeline_fps = tl_fps, items = items, created = os.time(),
  }
end

-- ------------------------------------------------------------------ build a new timeline
local function unique_name(proj, base)
  local taken = {}
  for i = 1, proj:GetTimelineCount() do taken[proj:GetTimelineByIndex(i):GetName()] = true end
  if not taken[base] then return base end
  local k = 2
  while taken[base .. " " .. k] do k = k + 1 end
  return base .. " " .. k
end

local function find_clip(folder, wanted)
  for _, clip in ipairs(objects(folder:GetClipList())) do
    if normalize_path(clip:GetClipProperty("File Path")) == wanted then return clip end
  end
  for _, sub in ipairs(objects(folder:GetSubFolderList())) do
    local found = find_clip(sub, wanted)
    if found then return found end
  end
  return nil
end

local function media_item(pool, path)
  local found = find_clip(pool:GetRootFolder(), normalize_path(path))
  if found then return found end
  return objects(pool:ImportMedia({ path }))[1]
end

local function review_color_for(item)
  for _, c in ipairs(REVIEW_COLORS) do
    if item:SetClipColor(c) then return c end
  end
  return nil
end

function M.build(plan)
  local _, proj, err = open_project()
  if err then return err end
  local pool = proj:GetMediaPool()
  local name = unique_name(proj, plan.name or "ScriptCutter")
  local tl = pool:CreateEmptyTimeline(name)
  if not tl then return { ok = false, error = "Resolve could not create the timeline '" .. name .. "'." } end
  proj:SetCurrentTimeline(tl)
  -- give the new timeline the rate of the one that was cut (it can differ from the project's)
  local wanted = tonumber(plan.timeline_fps) or 0
  if wanted > 0 and math.abs((tonumber(tl:GetSetting("timelineFrameRate")) or 0) - wanted) > 0.001 then
    pcall(function()
      tl:SetSetting("useCustomSettings", "1")
      tl:SetSetting("timelineFrameRate", tostring(wanted))
    end)
  end
  local fps = tonumber(tl:GetSetting("timelineFrameRate")) or tonumber(proj:GetSetting("timelineFrameRate")) or 24
  local start = tl:GetStartFrame()

  if plan.checkerboard then
    while tl:GetTrackCount("video") < 2 do tl:AddTrack("video") end
    local audio_type = tl:GetTrackSubType("audio", 1) or "stereo"
    while tl:GetTrackCount("audio") < 2 do tl:AddTrack("audio", audio_type) end
  end

  -- media pool handles fetched before the timeline existed are rejected, so look them up now
  pool = proj:GetMediaPool()
  local media, missing = {}, 0
  local rec, placed, marked, review_color = 0, 0, 0, nil

  for i, clip in ipairs(plan.clips or {}) do
    if media[clip.path] == nil then media[clip.path] = media_item(pool, clip.path) or false end
    local item = media[clip.path]
    if not item then
      missing = missing + 1
    else
      -- Resolve conforms NTSC rates frame-for-frame (29.97 on a 24 timeline uses 24/30)
      local duration = math.max(1, round((clip.src_out - clip.src_in) * round(fps) / round(clip.fps)))
      local overlap = (plan.checkerboard and i > 1) and math.min(plan.overlap or 3, math.floor(duration / 2)) or 0
      local at = math.max(0, rec - overlap)
      rec = at + duration
      local info = {
        mediaPoolItem = item, startFrame = clip.src_in, endFrame = clip.src_out - 1,
        trackIndex = plan.checkerboard and ((i - 1) % 2 + 1) or 1, recordFrame = start + at,
      }
      -- one clip per call: multi-clip calls can hang Resolve, and a brand-new
      -- timeline may reject the very first append for a moment
      local got = objects(pool:AppendToTimeline({ info }))
      local tries = 0
      while #got == 0 and placed == 0 and tries < 10 do
        wait(0.5)
        proj:SetCurrentTimeline(tl)
        got = objects(pool:AppendToTimeline({ info }))
        tries = tries + 1
      end
      if #got > 0 then
        placed = placed + 1
        local color = SURE_COLOR
        if clip.status == "review" then
          review_color = review_color or review_color_for(got[1])
          color = review_color or SURE_COLOR
        end
        for _, ti in ipairs(got) do ti:SetClipColor(color) end
        if clip.status == "review" and plan.markers then
          local reason = clip.reason ~= "" and clip.reason or "Needs a human look"
          local note = reason .. ((clip.text or "") ~= "" and ('\n\n"' .. clip.text .. '"') or "")
          if tl:AddMarker(at, MARKER_COLOR, "REVIEW: " .. reason:sub(1, 60), note, duration, clip.ids or "") then
            marked = marked + 1
          end
        end
      end
    end
  end

  local voice = nil
  if plan.voice_isolation then
    voice = true
    for track = 1, tl:GetTrackCount("audio") do
      voice = (tl:SetVoiceIsolationState(track, { isEnabled = true, amount = plan.voice_amount or 100 }) and voice)
    end
  end

  return {
    ok = placed > 0, timeline = name, items = placed, review_markers = marked, review_color = review_color,
    missing_media = missing, voice_isolation = voice, timeline_fps = fps,
    error = placed == 0 and "Resolve did not accept the clips. Is the media online?" or nil,
  }
end

-- ------------------------------------------------------------------ entry points
function M.run(mode, plan_path)
  local plan
  local ok, result = pcall(function()
    if mode == "context" then return M.context() end
    if mode == "build" then
      plan = dofile(plan_path)
      return M.build(plan)
    end
    return { ok = false, error = "Unknown mode: " .. tostring(mode) }
  end)
  if not ok then result = { ok = false, error = tostring(result) } end
  if plan then result.plan_id = plan.plan_id end
  return result
end

-- start the ScriptCutter app without blocking Resolve
function M.launch(python, script)
  if package.config:sub(1, 1) == "\\" then
    os.execute('start "" "' .. python .. '" "' .. script .. '"')
  else
    os.execute('"' .. python .. '" "' .. script .. '" >/dev/null 2>&1 &')
  end
end

if not SCRIPTCUTTER_EMBEDDED and arg and arg[1] then
  local mode = arg[1]
  local out = mode == "build" and arg[3] or arg[2]
  M.write_file(out, json(M.run(mode, arg[2])))
end

return M
