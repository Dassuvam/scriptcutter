# ScriptCutter

**Automatic retake removal for video creators.** Record the way you always do: stumble, repeat lines, say "let me do that again". ScriptCutter finds the retakes and long pauses and builds a clean new timeline that keeps your last good take, in DaVinci Resolve, Premiere Pro, Final Cut Pro or any editor that reads an EDL.

- **Free and private.** Speech recognition runs on your computer. Nothing is uploaded.
- **Never cuts blindly.** Parts it is sure about are **Blue**. Parts it is unsure about stay in the timeline as **Brown**, with a marker that says why.
- **Hindi, Hinglish, English** and the other languages Whisper understands. Read Hindi videos in Hindi letters or in Hinglish with one switch.
- **Works with DaVinci Resolve Free and Studio**, and saves files for **Premiere Pro**, **Final Cut Pro** and other editors.
- **Transcript of the final cut**, ready to copy, plus subtitles (.srt).
- **Learns your taste.** Your Keep / Cut corrections improve the next video.

---

## What you need

| | macOS | Windows |
|---|---|---|
| System | macOS 12 or newer. Apple Silicon (M1 or newer) recommended | Windows 10 or 11, 64-bit |
| Editing app | DaVinci Resolve 18.5+ (Free or Studio), Premiere Pro, Final Cut Pro, or any app that imports an EDL | DaVinci Resolve 18.5+, Premiere Pro, or any app that imports an EDL |
| Disk space | about 1 GB (plus 2–3 GB for the optional large models) | same |
| Internet | only while installing | only while installing |

On Windows, speech recognition runs on the processor, so use the default model. The large models are slow there.

---

## Install (one time)

1. **Download** `ScriptCutter.zip`, unzip it, and move the `ScriptCutter` folder somewhere permanent, such as **Documents**. If you move it later, run the installer again.
2. **Run the installer:**
   - **Mac:** right-click **`Install ScriptCutter.command`**, choose **Open**, then **Open** again. (macOS asks this the first time for files from the internet. If it still refuses: System Settings → Privacy & Security → **Open Anyway**.)
   - **Windows:** double-click **`Install ScriptCutter.bat`**. If Windows shows "Windows protected your PC", click **More info → Run anyway**.

   The installer sets up everything ScriptCutter needs (ffmpeg, Whisper, and Python on Windows), downloads the speech models, and adds ScriptCutter to Resolve's menu. It asks whether you want the bigger models. Say **y** to **large-v3** if you speak Hindi or another non-English language, or **large-v3-turbo** for English.
3. **Restart DaVinci Resolve.**
4. **Studio only:** in Resolve open **Preferences → System → General**, set **External scripting using** to **Local**, and click **Save**. (The free version doesn't have or need this.)

---

## Use

1. In Resolve, put your raw recording on **video track 1** of a timeline.
2. Choose **Workspace → Scripts → ScriptCutter**. ScriptCutter opens in your browser.
3. Choose **Talking head** or **Screen recording** and press **Analyse**.
4. Check the list. Click **Brown** to see only the parts that need you, press ▶ to listen, and click **Keep** or **Cut**.
5. Press **Create timeline in Resolve**.
   - **Studio:** the new timeline appears by itself.
   - **Free version:** go back to Resolve and choose **Workspace → Scripts → ScriptCutter - Build Timeline**.

Your original timeline is never changed. Click **Help** (or press `?`) inside ScriptCutter for a simple guide.

**Premiere Pro, Final Cut Pro or another app**
1. Double-click `Open ScriptCutter` (`.command` on Mac, `.bat` on Windows). ScriptCutter opens in your browser.
2. Choose **Video file**, drag your raw recording into the box, and press **Analyse**.
3. Check the list as above. In the bottom bar, pick your app and press **Save**. The file is saved next to your video.
4. **Premiere Pro:** File → Import, pick the `.xml` file. **Final Cut Pro:** File → Import → XML, pick the `.fcpxml` file. **Other apps:** import the `.edl` file.

The cut arrives on one video track; brown parts have a marker with the reason. Overlapping A/B cuts and Voice Isolation are Resolve-only.

**Transcript:** press **Transcript** in the bottom bar to read and copy what is said in the final cut, or save it as text and subtitles (`.srt`). For Hindi videos, the **हिंदी / Hinglish / English** switch above the list picks the letters.

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
| The page doesn't open | Double-click `Open ScriptCutter` (`.command` on Mac, `.bat` on Windows), or visit http://127.0.0.1:7870 |
| Mac: analysis stops at "Reading audio" | macOS may be asking for permission to read the folder with your video. Look for the dialog, or allow it in System Settings → Privacy & Security → Files and Folders. |
| The video preview shows an error | Click **▶ Audio only**. Some camera formats can't play in a browser. |
| Too many brown clips | Choose **Aggressive**, or run the installer again and add a large model. |
| Anything else | Send the log. Mac: `~/Library/Logs/ScriptCutter.log`. Windows: `%APPDATA%\ScriptCutter\ScriptCutter.log` |

**Uninstall:** run `python3 uninstall.py` (Windows: `py uninstall.py`) in the ScriptCutter folder, then delete the folder.

---

## Authors

- **Suvam Das** - dassuvam2005@gmail.com
- **Aswini Prasad Rout** - aswiniprasadrout@gmail.com

Questions or problems? Email dassuvam2005@gmail.com

Developers: see [DEVELOPMENT.md](DEVELOPMENT.md). License: MIT.
