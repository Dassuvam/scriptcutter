@echo off
rem Double-click to install ScriptCutter on this PC. It opens by itself when it is ready.
cd /d "%~dp0"
if exist "app\install.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "app\install.ps1" %*
) else if exist "install.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "install.ps1" %*
) else (
  echo Please unzip the whole ScriptCutter folder first:
  echo right-click ScriptCutter-Windows.zip, choose Extract All, then open this file from the new folder.
)
pause
