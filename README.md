# ScriptCutter

**Automatic retake removal for video creators.** Record the way you always do: stumble, repeat lines, say "let me do that again". ScriptCutter finds the retakes and long pauses and builds a clean new timeline that keeps your last good take, in DaVinci Resolve, Premiere Pro, Final Cut Pro or any editor that reads an EDL.

- **Free and private.** Speech recognition runs on your computer. Nothing is uploaded.
- **Never cuts blindly.** Parts it is sure about are **Blue**. Parts it is unsure about stay in the timeline as **Brown**, with a marker that says why.
- **Hindi, Hinglish, English** and the other languages Whisper understands. Read Hindi videos in Hindi letters or in Hinglish with one switch.
- **Works with DaVinci Resolve Free and Studio**, and saves files for **Premiere Pro**, **Final Cut Pro** and other editors.
- **Transcript of the final cut**, ready to copy, plus subtitles (.srt).
- **Have a script? Paste it.** ScriptCutter keeps the last good take of every line of your script and cuts the rest, which is more accurate than working from the audio alone.
- **Learns your taste.** Your Keep / Cut corrections improve the next video.

---

## Download

| Mac | Windows |
|---|---|
| **[Download ScriptCutter for Mac](https://github.com/Dassuvam/scriptcutter/releases/latest/download/ScriptCutter-Mac.zip)** | **[Download ScriptCutter for Windows](https://github.com/Dassuvam/scriptcutter/releases/latest/download/ScriptCutter-Windows.zip)** |
| macOS 13 or newer, Apple Silicon (M1 or newer) recommended | Windows 10 or 11 (64-bit or ARM) |
| Speech recognition runs on the Mac's graphics chip | Runs on the processor, or on an NVIDIA graphics card |
| Step-by-step: [Mac/README.md](Mac/README.md) | Step-by-step: [Windows/README.md](Windows/README.md) |

Works with DaVinci Resolve 18.5+ (Free or Studio), Premiere Pro, Final Cut Pro (Mac) or any app that imports an EDL, and on its own with any video file. About 1 GB of disk (3 to 5 GB with the large models). Internet only while installing.

---

## Install (one time)

1. **Mac:** open the downloaded zip and double-click **Install ScriptCutter**. **Windows:** right-click the zip, choose **Extract All**, and double-click **Install ScriptCutter** in the new folder.
2. The installer sets up everything ScriptCutter needs (ffmpeg, Whisper, and Python on Windows), downloads the speech models, adds ScriptCutter to DaVinci Resolve, and opens it in your browser. Say **y** to the bigger models for the most accurate cut.
3. **Restart DaVinci Resolve** once. **Studio only:** Preferences → System → General → **External scripting using: Local** → Save.

You can delete the downloaded folder afterwards: ScriptCutter is installed in its own place.

**First time only, a security question.** ScriptCutter is free and not yet signed with a paid Apple or Microsoft certificate, so the system asks once:
- **Mac:** if macOS says it could not check "Install ScriptCutter", open System Settings → Privacy & Security and click **Open Anyway**.
- **Windows:** if Windows says "Windows protected your PC", click **More info → Run anyway**.

**Or install with one line, without that question:**
- Mac (Terminal): `curl -fsSL https://github.com/Dassuvam/scriptcutter/releases/latest/download/install-mac.sh | bash`
- Windows (PowerShell): `irm https://github.com/Dassuvam/scriptcutter/releases/latest/download/install-windows.ps1 | iex`

---

## Use

1. In Resolve, put your raw recording on **video track 1** of a timeline.
2. Choose **Workspace → Scripts → ScriptCutter**. ScriptCutter opens in your browser.
3. Choose **Talking head** or **Screen recording** and press **Analyse**.
4. Check the list. Click **Brown** to see only the parts that need you, press ▶ to listen, and click **Keep** or **Cut**.
5. At the bottom, choose **Export as: DaVinci Resolve** and press **Create in Resolve**.
   - **Studio:** the new timeline appears by itself.
   - **Free version:** go back to Resolve and choose **Workspace → Scripts → ScriptCutter** once more. It builds the timeline.

Your original timeline is never changed. Click **Help** (or press `?`) inside ScriptCutter for a simple guide.

**Premiere Pro, Final Cut Pro or another app**
1. Open **ScriptCutter** without Resolve: from Launchpad or Spotlight on a Mac, from the Start menu or the desktop on Windows. It opens in your browser.
2. Choose **Video file**, drag your raw recording into the box, and press **Analyse**.
3. Check the list as above. In the bottom bar, under **Export as**, pick **MP4 video** (the finished cut) or your editing app, and press the button next to it. The file is saved next to your video.
4. **Premiere Pro:** File → Import, pick the `.xml` file. **Final Cut Pro:** File → Import → XML, pick the `.fcpxml` file. **Other apps:** import the `.edl` file.

The cut arrives on one video track; brown parts have a marker with the reason. Overlapping A/B cuts and Voice Isolation are Resolve-only.

**Script (optional):** if you recorded from a script, paste it in **Step 4** before pressing Analyse (Hindi, Hinglish or English). ScriptCutter keeps your last good take of every line and cuts the other takes and anything not in the script; longer unscripted parts stay brown.

**Transcript:** only when you want it, press **Transcript** in the bottom bar to read what is said in the final cut, line by line with times. Copy it, or download it as subtitles (`.srt`), text (`.txt`) or PDF. For Hindi videos, the **हिंदी / Hinglish / English** switch above the list picks the letters.

**In the new timeline**
- **Blue clips:** kept, sure.
- **Brown clips:** kept, but please check. Each has a marker with the reason; press **Shift + ↓** to jump to the next one.
- Removed takes are not in the new timeline. They are still in your original.

**Optional: AI second opinion.** Under *AI second opinion*, pick Google Gemini (free key from [aistudio.google.com](https://aistudio.google.com)), Groq, OpenRouter or a local Ollama. The AI only reviews. Anything only the AI dislikes becomes Brown; it never cuts on its own.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| ScriptCutter is not in Workspace → Scripts | Run the installer again, then restart Resolve. |
| "Can't see your Resolve timeline" | Open ScriptCutter from **Workspace → Scripts → ScriptCutter** in Resolve. In the free version, do this again after switching timelines. |
| Studio: nothing happens after "Create timeline" | Check *External scripting using = Local* (Install, step 4) and restart Resolve. |
| The page doesn't open | Open **ScriptCutter** from Launchpad / Spotlight (Mac) or the Start menu (Windows), or visit http://127.0.0.1:7870 |
| Mac: analysis stops at "Reading audio" | macOS may be asking for permission to read the folder with your video. Look for the dialog, or allow it in System Settings → Privacy & Security → Files and Folders. |
| The video preview shows an error | Click **▶ Audio only**. Some camera formats can't play in a browser. |
| Too many brown clips | Choose **Aggressive**, or run the installer again and add a large model. |
| Anything else | Send the log. Mac: `~/Library/Logs/ScriptCutter.log`. Windows: `%APPDATA%\ScriptCutter\ScriptCutter.log` |

**Update:** download and install the new version the same way; settings and learned choices stay.
**Uninstall:** Mac: double-click **Uninstall ScriptCutter** in the downloaded folder. Windows: Settings → Apps → ScriptCutter → Uninstall.

---

## Authors

- **Suvam Das** - dassuvam2005@gmail.com
- **Aswini Prasad Rout** - aswiniprasadrout@gmail.com

Questions or problems? Email dassuvam2005@gmail.com

Developers: see [DEVELOPMENT.md](DEVELOPMENT.md). License: MIT.
