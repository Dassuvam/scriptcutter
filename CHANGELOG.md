# Changelog

## 3.2.0 (not released yet)
- **Script mode:** paste the script you recorded from (Hindi letters, Hinglish or English, mixed is fine) in Step 4. ScriptCutter finds every line in the recording, keeps the last good take of each line, and cuts the other takes, unfinished takes and short off-script bits. Longer new parts that are not in the script stay brown, in case they are a good ad-lib. The review shows how many lines were found and which were not. Command line: `--script script.txt`.

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
