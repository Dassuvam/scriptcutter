# ScriptCutter

**Automatic retake removal for DaVinci Resolve.** Record your video the way you always do: stumble, repeat lines, say "let me do that again". ScriptCutter finds the retakes and silences and builds a clean timeline that keeps only the last good take.

- **Free and private.** Speech recognition runs on your Mac. Nothing is uploaded.
- **Never cuts blindly.** Clips it is sure about are **Blue**. Anything it is unsure about stays in the timeline as **Brown**, with a marker that tells you why.
- **Works with Hindi, Hinglish, English** and the other languages Whisper supports.
- **Learns your taste.** Your Keep/Cut corrections make the next video better.

---

## Requirements

| | |
|---|---|
| Computer | Mac with Apple Silicon (M1 or newer) recommended; Intel Macs work but are slower |
| macOS | 12 Monterey or newer |
| DaVinci Resolve | 18, 19, 20 or 21 (free or Studio; AI Voice Isolation needs Studio) |
| Disk space | about 1 GB, plus 3–5 GB if you add the large speech models |
| Internet | only during installation |

---

## Install (one time, about 10 minutes)

**1. Download and unzip.** Unzip `ScriptCutter-macOS.zip` and move the `ScriptCutter` folder somewhere permanent, for example your **Documents** folder. (If you move it later, run the installer again.)

**2. Run the installer.** Right-click **`Install ScriptCutter.command`** → **Open** → **Open**.
> macOS blocks double-clicking scripts downloaded from the internet the first time; right-click → Open allows it. If macOS still refuses, open **System Settings → Privacy & Security** and click **Open Anyway**.

A Terminal window opens and:
- asks you to install Apple's developer tools if they are missing (click **Install**, then run the installer again);
- installs Homebrew, ffmpeg and whisper-cpp (you may be asked for your Mac password);
- downloads the speech model (466 MB) and the voice detector (1 MB);
- offers the bigger models. Say **y** to **large-v3** if you speak Hindi or another non-English language, and **large-v3-turbo** if you speak English;
- adds ScriptCutter to DaVinci Resolve's menu and runs a self-test.

**3. Allow Resolve to talk to ScriptCutter.** In DaVinci Resolve: **DaVinci Resolve → Settings (Preferences) → System → General → External scripting using: Local** → Save. Then **restart Resolve**.

---

## Use

1. In Resolve, put your raw recording on **video track 1** of a timeline.
2. Open **Workspace → Scripts → ScriptCutterV2**. ScriptCutter opens in your browser.
   (Or double-click `Open ScriptCutter.command`.)
3. Choose **Talking head** or **Screen recording** and press **Analyse**.
   A 5-minute video takes about 10–40 seconds, depending on the model.
Click **Help** (or press `?`) in the app any time for a simple guide.

4. **Review.** Click **Brown** to see only the lines it is unsure about. ▶ plays the line, and *hear the later take* plays the take it was compared with. Click **Keep** or **Cut**, or use the keys `1` keep, `2` review, `3` cut, `Space` play, `↑ ↓` move.
5. Press **Create timeline in Resolve**. A new timeline appears; your original is never touched.

**In the new timeline:**
- **Blue clips:** kept, ScriptCutter is sure.
- **Brown clips:** kept, but worth a look. Each has a marker with the reason. Jump between markers with **Shift + ↑ / ↓**.
- Removed retakes are simply not there. You can always find them in the original timeline.

**Tips**
- *Cautious / Balanced / Aggressive* changes how sure it must be before cutting. You can switch after the analysis and the cut updates instantly.
- *Pace & padding* controls how much breathing room is left around words.
- The ☾ button switches to night mode.

### Optional: AI second opinion (free)
Under **AI second opinion**, choose Google Gemini (free key from [aistudio.google.com](https://aistudio.google.com)), Groq, OpenRouter, or a local Ollama. The AI only reviews. It cannot cut anything on its own: lines only the AI dislikes become Brown.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Resolve not reachable" | Resolve must be open with a project, and *External scripting using* must be **Local**. Restart Resolve after changing it. |
| ScriptCutter isn't in the Workspace → Scripts menu | Run the installer again, then restart Resolve. |
| The page doesn't open | Double-click `Open ScriptCutter.command`, or visit http://127.0.0.1:7870 |
| Video preview shows an error | Switch to **▶ Audio only**. Some camera codecs can't play in the browser. |
| Too many brown clips | Use a bigger model (run the installer again and say **y** to large-v3), or set *Aggressive*. |
| Something else | The log is at `~/Library/Logs/ScriptCutterV2.log` |

**Uninstall:** run `./uninstall.sh` in the folder, then delete the folder.

---

## For developers

```
cutter/            analysis engine (pure Python 3.9+, standard library only)
  energy.py          loudness envelope, adaptive speech detection
  vad.py             Silero voice detection (whisper.cpp)
  transcribe.py      chunked whisper-cli transcription, loop/hallucination filters
  text.py            Unicode text normalisation and similarity
  analyze.py         retake detection with confidence
  learn.py           learns from the user's overrides
  llm.py             optional AI reviewer (Gemini, Groq, OpenRouter, Ollama)
  cuts.py            decisions → frame-accurate clips
  resolve_api.py     builds the Resolve timeline (colours, markers)
  pipeline.py        orchestration and on-disk cache
  export.py          EDL and JSON report
web/server.py      local web server and JSON API
web/static/        index.html, css/theme.css (all colours and fonts), css/app.css, js/app.js
resolve/           Resolve menu script
tests/             python3 tests/test_v2.py
tools/             build_release.sh
```

- Run without installing: `python3 web/server.py` → http://127.0.0.1:7870
- Command line: `python3 cli.py analyze --video raw.mp4 --edl cut.edl`
- Tests: `python3 tests/test_v2.py`
- To change the look, edit `web/static/css/theme.css`. It holds every colour, font and radius.

### Publishing a release

1. Bump `VERSION` and add a `CHANGELOG.md` entry.
2. Run `tools/build_release.sh`. It creates `dist/ScriptCutter-macOS.zip`.
3. On GitHub: **Releases → Draft a new release**, tag it (e.g. `v2.1.0`), and attach `dist/ScriptCutter-macOS.zip`.

Because the file name never changes, this link always serves the newest release:
```
https://github.com/<your-user>/<your-repo>/releases/latest/download/ScriptCutter-macOS.zip
```

### Download button for your website
```html
<a href="https://github.com/<your-user>/<your-repo>/releases/latest/download/ScriptCutter-macOS.zip"
   style="display:inline-block;padding:14px 28px;border-radius:999px;background:#d0402c;color:#fffaf2;
          font:600 16px/1 'Avenir Next',sans-serif;text-decoration:none">
  Download ScriptCutter for Mac
</a>
```

## License
MIT. See `LICENSE`.
