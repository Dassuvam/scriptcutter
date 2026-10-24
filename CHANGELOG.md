# Changelog

## 3.2.1
- **Split a line where you want, like in a text editor:** in the review, click a word to put a text cursor there, then press **Enter** (or *Split at the cursor*). The line becomes two lines, and each can be kept, sent to review or cut on its own, so a retake glued to a good sentence ("So that's it for this video, so that's it for this video and if you like…") can be cut exactly at the word. The arrow keys move the cursor, Esc hides it.
- **Undo:** **⌘Z** (Mac) or **Ctrl+Z** (Windows) takes back the last split or Keep / Review / Cut choice, one step at a time.

## 3.2.0

**Notes**
- **Works best on a Mac** with Apple Silicon (M1 or newer). Speech recognition runs on the Mac's graphics chip, so the large, most accurate models are fast. On Windows it runs on the processor unless there is an NVIDIA graphics card: it works, but it is several times slower, and the default (small) model is less accurate.
- **For the best cut, give it your script.** Paste the script you recorded from in Step 4, complete and as close as possible to what you said. With a script, ScriptCutter knows which lines belong in the video and keeps your last good take of each.

- **Separate downloads for Mac and Windows:** *ScriptCutter-Mac.zip* and *ScriptCutter-Windows.zip*, each with only its own files. Their links never change: `releases/latest/download/ScriptCutter-Mac.zip` and `.../ScriptCutter-Windows.zip`.
- **One double-click installs and opens it:** *Install ScriptCutter* sets everything up, puts ScriptCutter in its own place (the download can be deleted), adds it to DaVinci Resolve, and opens it. To open it without Resolve: **ScriptCutter** in Launchpad and Spotlight (Mac), or in the Start menu and on the desktop (Windows). Uninstall from Settings > Apps on Windows.
- **Windows:** nothing needs administrator rights any more (Python, ffmpeg and whisper.cpp are installed for your user only, without winget); with an NVIDIA graphics card the much faster GPU speech engine is offered and used once it is proven to work; Windows on ARM is supported. Fixed: new whisper.cpp versions publish no files on their release page, which stopped the Windows installer.
- **No security question with the one-line install** (`curl ... | bash` on Mac, `irm ... | iex` on Windows). Fixed: after unzipping on a Mac, *Install ScriptCutter* could lose its permission to run.
- **Script mode:** paste the script you recorded from (Hindi letters, Hinglish or English, mixed is fine) in Step 4. ScriptCutter finds every line in the recording, keeps the last good take of each line, and cuts the other takes, unfinished takes and short off-script bits. Longer new parts that are not in the script stay brown, in case they are a good ad-lib. The review shows how many lines were found and which were not. Command line: `--script script.txt`. Words are matched by sound, so Whisper's spellings (nekst / next, stoori / story) and lines said in your own words still count; a later take is never cut on the script alone. On a real Hinglish recording with the creator's own script, 95% of words were handled safely (kept or cut like the hand edit, or left brown), against 77% without the script.
- **MP4 video of the final cut:** choose *Export as: MP4 video* and press *Make the video*. The finished cut (straight cuts, brown parts kept) is saved next to your video, ready to watch or upload; picture and sound stay in sync over any number of cuts.
- **Simpler bottom bar:** *Transcript* and one *Export as* choice: MP4 video, DaVinci Resolve, Premiere Pro (XML), Final Cut Pro (XML) or another editor (EDL).
- **Transcript only when you want it:** the Transcript window shows the final cut line by line with times. Copy the text, or download subtitles (.srt), text (.txt) or a PDF.
- **One menu entry in Resolve:** *Workspace > Scripts > ScriptCutter* opens the app; in the free version of Resolve, choosing it again builds the prepared timeline. The separate *ScriptCutter - Build Timeline* entry is removed by the installer.
- **Overlapping A/B cuts and AI Voice Isolation are now on by default** when the timeline is created in DaVinci Resolve (Voice Isolation needs Studio). Clips alternate between V1/A1 and V2/A2 with a 3-frame overlap. Earlier saved settings are updated to the new defaults once.
- **Fixes for real-world files:** phone videos recorded upright come out upright in the MP4; drop-frame timecode (common on Sony cameras) is placed correctly in Premiere, Final Cut and EDL exports; clips without sound or without speech (title cards, B-roll) stay in the cut instead of disappearing; disabled and offline clips on a Resolve timeline are skipped with a note; the new Resolve timeline gets the frame rate of the timeline that was cut.
- **Safer script mode:** a different sentence that only shares small words ("agar aapko… to… karo") no longer counts as a take; lines the script repeats, very short lines and takes far apart are left brown instead of cut; long videos are matched in seconds.
- **More robust app:** *Choose video…* opens the system's file window; exports never overwrite an earlier file and fall back to Movies / Videos > ScriptCutter when the video's folder is read-only; an old ScriptCutter still running after an update is replaced automatically; another program on port 7870 no longer blocks it; model downloads resume after a dropped connection; Whisper retries on the processor if the Mac's graphics chip fails; the local server refuses commands from other websites.

## 3.1.0
- **More accurate on Hindi and Hinglish:** ScriptCutter now also reads the timed words in the spoken language and finds restarts inside Whisper's long sentences ("mere ko jo sabse badi… aur mere ko jo poochh sabse accha lagega"), including the end of a line that the next line starts again and short scraps before a restart. Sentences that only start the same way ("agar aapko ye video pasand aaya…" / "…pasand nahi aaya…") are left alone. On a real 2.6-minute Hindi recording, agreement with a hand edit rose from 66% to 75% of the time (77% of words handled safely), with no wrongly cut lines.
- Fixed: a brown part inside a sentence no longer turns the whole sentence brown.
- **Premiere Pro, Final Cut Pro and other editors:** pick your app in the bottom bar and press Save. ScriptCutter writes a Premiere Pro XML, a Final Cut Pro FCPXML or an EDL next to your video. Brown parts get a marker with the reason (and an orange label in Premiere). One video track; A/B overlap and Voice Isolation stay Resolve-only.
- **Transcript of the final cut:** the new Transcript button shows what is said in the finished cut, ready to copy, and saves a text file, subtitles (.srt) and an edit-data file (.json) for automation.
- **हिंदी / Hinglish / English switch:** for Hindi videos the review shows the words in Hindi letters, in Hinglish (Hindi in English letters, like a chat) or as the English translation.
- **Steps with flags:** the top of the review shows each step of the work, with ✓ when done and ⚑ when it needs your eyes. The engine behind it (`cutter/project.py`) is ready for new steps such as captions, B-roll images and generated shots.
- "Show file" opens Finder / Explorer at a saved file. A double-click no longer closes the Help window right after opening it.
- Developers: one edit document (`cutter/edit.py`) feeds every output; formats live in `cutter/exporters/`. The command line gained `--xml`, `--fcpxml`, `--srt`, `--transcript` and `--view`.

## 3.0.0
- Works with DaVinci Resolve Free as well as Studio: all Resolve work moved into `resolve/scriptcutter.lua`, run through `fuscript` (Studio) or from the Workspace > Scripts menu (Free).
- Windows support: `Install ScriptCutter.bat` installs Python, ffmpeg and whisper.cpp; one cross-platform `install.py` handles models, the Resolve menu and the self-test.
- All OS-specific locations live in `cutter/paths.py`; program locations are recorded at install time.
- New check: a false start at the end of one line that is redone in the next line is cut word-accurately.
- Code clean-up (dead code removed), developer guide (`DEVELOPMENT.md`), GitHub Actions for tests and releases.
- The menu entry is now **ScriptCutter** (replaces ScriptCutterV2).

## 2.2.0
- Calmer palette inspired by the editorial reference: warm charcoal (no pure black), cream and vermilion.
- Removed the scenery artwork for a cleaner interface.
- New Help panel (top-right **Help** button or the `?` key) with simple steps for creators.

## 2.1.0
- New look: ink, paper and sun-red editorial theme with a night mode. All colours and fonts live in `web/static/css/theme.css`.
- Web app split into `index.html`, `css/theme.css`, `css/app.css`, `js/app.js`.
- Learns from your Keep/Cut overrides (stored locally).
- Video preview in the review list, plus "All brown → Keep / Cut".
- Silero voice detection keeps typing and music away from Whisper.
- Double-click installer and launcher, an uninstaller, and a release build script.
- Resolve: clips are placed one at a time and media handles re-fetched (fixes hangs and rejected appends in Resolve 21).

## 2.0.0
- Rewrite of v1: chunked local Whisper, retake detection with confidence, Blue/Brown review timeline, frame-accurate Resolve placement.
