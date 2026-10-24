# ScriptCutter for Mac

Download: https://github.com/Dassuvam/scriptcutter/releases/latest/download/ScriptCutter-Mac.zip

ScriptCutter cuts the retakes out of your recordings and keeps your last good take of every line.
It works with DaVinci Resolve (Free and Studio) and on its own with any video file.

## Install (one time, about 10 minutes)

1. Open the downloaded ScriptCutter-Mac.zip (it unzips by itself) and open the ScriptCutter folder.
2. Double-click **Install ScriptCutter**.
   macOS checks every file from the internet. If it says it could not check "Install ScriptCutter":
   click Done, open System Settings > Privacy & Security, scroll down, click **Open Anyway**
   next to "Install ScriptCutter" and then **Open**. You only do this once.
3. A Terminal window installs what ScriptCutter needs: Apple's command line tools, Homebrew,
   ffmpeg and whisper.cpp. It may ask for your Mac password (typing it shows nothing).
   Answer **y** to the bigger speech models for the most accurate cut.
4. When it is done, ScriptCutter opens in your browser. You can delete the downloaded folder.

**Install without any warning:** instead of steps 1 and 2, open Terminal
(Applications > Utilities > Terminal), paste this line and press Return:

    curl -fsSL https://github.com/Dassuvam/scriptcutter/releases/latest/download/install-mac.sh | bash

## Open ScriptCutter

- **Without DaVinci Resolve:** open **ScriptCutter** from Launchpad or Spotlight (Command + Space, type
  ScriptCutter). Drag it to the Dock to keep it there.
- **In DaVinci Resolve:** restart Resolve once, then choose Workspace > Scripts > ScriptCutter.
  Resolve Studio: also set Preferences > System > General > External scripting using = Local.

## Update or uninstall

- **Update:** download the new ScriptCutter-Mac.zip and install it the same way. Your settings stay.
- **Uninstall:** double-click **Uninstall ScriptCutter** in the downloaded folder.

## Good to know

- Works best on Apple Silicon (M1 or newer): speech recognition runs on the graphics chip.
- Needs macOS 13 or newer and about 1 GB of disk (3 to 5 GB with the large models).
- For the best cut, paste the script you recorded from in Step 4 of the app.
- Problems? Send ~/Library/Logs/ScriptCutter.log to dassuvam2005@gmail.com
