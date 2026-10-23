@echo off
rem Double-click to open ScriptCutter on Windows.
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 launcher.py) || (start "" pythonw launcher.py)
