# Changelog

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
