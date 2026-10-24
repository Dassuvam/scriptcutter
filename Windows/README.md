# ScriptCutter for Windows

Download: https://github.com/Dassuvam/scriptcutter/releases/latest/download/ScriptCutter-Windows.zip

ScriptCutter cuts the retakes out of your recordings and keeps your last good take of every line.
It works with DaVinci Resolve (Free and Studio) and on its own with any video file.

## Install (one time, about 10 minutes)

1. Right-click the downloaded ScriptCutter-Windows.zip, choose **Extract All**, then open the new
   ScriptCutter folder.
   Tip: first right-click the zip > Properties > tick **Unblock** > OK, and Windows won't ask in step 2.
2. Double-click **Install ScriptCutter**.
   If Windows says "Windows protected your PC": click **More info**, then **Run anyway**.
   If it says "Open File - Security Warning": click **Run**.
3. A window installs what ScriptCutter needs, for your user only (no administrator password):
   Python, ffmpeg and whisper.cpp. With an NVIDIA graphics card it offers the GPU speech
   engine: answer **Y**, it is many times faster.
4. When it is done, ScriptCutter opens in your browser. You can delete the downloaded folder.

**Install without any warning:** instead of steps 1 and 2, open PowerShell (Start menu, type
PowerShell), paste this line and press Enter:

    irm https://github.com/Dassuvam/scriptcutter/releases/latest/download/install-windows.ps1 | iex

## Open ScriptCutter

- **Without DaVinci Resolve:** **ScriptCutter** in the Start menu or on the desktop.
- **In DaVinci Resolve:** restart Resolve once, then choose Workspace > Scripts > ScriptCutter.
  Resolve Studio: also set Preferences > System > General > External scripting using = Local.

## Update or uninstall

- **Update:** download the new ScriptCutter-Windows.zip and install it the same way. Your settings stay.
- **Uninstall:** Settings > Apps > ScriptCutter > Uninstall (or **Uninstall ScriptCutter** in the
  downloaded folder).

## Good to know

- Needs Windows 10 or 11 (64-bit or ARM) and about 1 GB of disk.
- Without an NVIDIA graphics card, speech recognition runs on the processor: the small model is the
  best start, and pasting the script you recorded from (Step 4 of the app) gives the best cut.
- Problems? Send %APPDATA%\ScriptCutter\ScriptCutter.log to dassuvam2005@gmail.com
