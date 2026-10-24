#!/bin/bash
# Double-click to remove ScriptCutter from this Mac.
APP="$HOME/Library/Application Support/ScriptCutter/App"
if [ ! -f "$APP/uninstall.py" ]; then
  echo "ScriptCutter is not installed."
  exit 0
fi
cd "$HOME" && /usr/bin/python3 "$APP/uninstall.py"
