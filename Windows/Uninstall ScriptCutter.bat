@echo off
rem Double-click to remove ScriptCutter from this PC (Settings > Apps > ScriptCutter does the same).
set "APP=%LOCALAPPDATA%\ScriptCutter\App"
if not exist "%APP%\uninstall.py" (
  echo ScriptCutter is not installed.
  goto done
)
cd /d "%USERPROFILE%"
where py >nul 2>nul
if errorlevel 1 (
  python "%APP%\uninstall.py"
) else (
  py -3 "%APP%\uninstall.py"
)
:done
pause
