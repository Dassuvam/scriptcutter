-- DaVinci Resolve menu entry: Workspace > Scripts > ScriptCutterV2
-- install.sh writes the real project path into the line below.
local launcher = "__LAUNCHER_PATH__"

local f = io.open(launcher, "r")
if not f then
    print("[ScriptCutterV2] launcher.py not found at " .. launcher .. " - run install.sh again.")
    return
end
f:close()
os.execute('/usr/bin/python3 "' .. launcher .. '" > /dev/null 2>&1 &')
