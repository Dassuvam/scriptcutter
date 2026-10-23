# Changelog

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
