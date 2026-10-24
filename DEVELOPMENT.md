# ScriptCutter: developer guide

Everything is standard-library Python 3.9+ plus two external programs (ffmpeg, whisper.cpp) and a Lua file that runs inside DaVinci Resolve. There are no pip dependencies. Supported platforms: macOS and Windows.

## Layout

```
cutter/                 the engine
  project.py              Project + Steps: cut -> script (background) -> outputs; each step can raise a flag
  edit.py                 the edit document every output reads (clips, markers, transcript)
  exporters/              one module per output format: video (MP4 via ffmpeg), fcp7 (Premiere XML), fcpxml
                          (Final Cut), edl, transcript (srt, txt, json); the transcript PDF is printed by the browser
  restarts.py             restarts in the timed spoken-language words (also inside Whisper's long sentences)
  scriptmatch.py          script mode: the creator's script decides which take of every line is kept
  translit.py             Hindi (Devanagari) -> Hinglish, offline
  paths.py                every OS-specific folder and program lookup (start here when porting)
  media.py                ffprobe / ffmpeg: probe, extract 16 kHz audio, screen-motion scan; per-file cache
  energy.py               10 ms loudness envelope, adaptive speech detection
  vad.py                  Silero voice detection (whisper.cpp's whisper-vad-speech-segments)
  transcribe.py           whisper-cli in <=24 s chunks; loop / hallucination cleaning
  text.py                 Unicode normalisation, tokens, similarity measures
  analyze.py              retake detection -> decisions with a confidence
  learn.py                adjusts confidences from the user's past overrides
  llm.py                  optional AI reviewer (Gemini, Groq, OpenRouter, Ollama, OpenAI-compatible)
  cuts.py                 decisions -> clips in source seconds/frames (padding, pauses, review clips)
  pipeline.py             per-file analysis, caching, progress; original-language words
  resolve_bridge.py       Python side of the Resolve bridge
resolve/
  scriptcutter.lua        all Resolve work: read the open timeline, build the new one
  *.lua.template          the Workspace > Scripts > ScriptCutter menu entry (filled in by install.py)
web/server.py           local web app + JSON API (http://127.0.0.1:7870)
web/static/             index.html, css/theme.css (all colours/fonts), css/app.css, js/app.js
install.py              cross-platform installer: programs, models, Resolve menu, self-test
install.sh / .ps1       macOS / Windows wrappers that install ffmpeg + whisper.cpp first
tools/build_release.py  builds dist/ScriptCutter.zip
tests/                  python3 tests/test_scriptcutter.py
```

## How a cut is made

1. **Audio:** ffmpeg extracts 16 kHz mono audio (cached per file in the cache folder, see `paths.cache_dir()`).
2. **Where is speech:** `energy.py` sets a threshold from the file's own noise floor; `vad.py` (Silero) removes typing, music and other non-voice sound.
3. **Words:** `transcribe.py` sends speech to whisper-cli in chunks of at most 24 s, each decoded independently (no text context between chunks). That stops repetition loops and timestamp drift on long files. Non-English speech is analysed through Whisper's English translation: repeated takes translate to near-identical sentences, and it is far more stable than decoding Devanagari with small models. Hallucinations ("Thank you for watching" over typing, machine-regular repeats) are filtered in `clean_segments`.
4. **Decisions:** `analyze.py` compares each phrase with the next ~2 minutes. The last complete take wins; re-recorded blocks, restart cues, fillers and mid-line restarts are detected. Each decision gets a removal confidence. `apply_policy` maps it to remove / review (Brown) / keep (Blue) using the Cautious / Balanced / Aggressive thresholds. The AI reviewer may only confirm or dispute; AI-only flags are capped at review.
5. **Clips:** `cuts.py` turns decisions into clips: padding is fenced so it never leaks into a removed take, pauses up to `max_pause` are kept, and review items are always separate clips.
6. **Spoken-language words:** for non-English speech, retakes are matched on the English translation, which has no word timings and often glues two takes into one sentence. So the file is transcribed once more in the spoken language (turbo model, cached). `restarts.py` splits those timed words into attempts at pauses and compares them in Hinglish letters (`translit.py`), so वीडियो and "video" match: a dropped attempt, the end of a line that the next one starts again, a restart without a pause, a scrap before a restart, and (brown only) a line begun again up to 25 s later. A first attempt that says something the redo doesn't repeat is not a restart. The same words feed the review's हिंदी / Hinglish view and the final transcript. (If this pass is switched off with `use_words`, the `script` step writes the words in the background instead.)
6b. **Script mode** (when the creator pastes a script, `settings["script"]`): after the analysis, `scriptmatch.py` aligns every script line with the spoken words of the timeline's ranges, in timeline order (a whole-line, local alignment in Hinglish tokens; for an English script of Hindi speech, against the English translation). The last take within `CLOSE` of the best one is kept, other takes and unfinished takes are cut. Speech in no take falls back to the retake analysis (cut if it was said again later), otherwise short bits are cut and longer new stretches stay brown. The script decisions replace the retake decisions (`d["script"] = True`; they are not used for learning).
7. **Outputs:** `Project.edit()` builds the edit document (`edit.py`): clips in timeline and source frames, markers for brown clips, and the transcript of the final cut on the timeline clock. Every format in `exporters/` reads only this document.
8. **Resolve:** `resolve_bridge.py` writes the plan as a Lua table (`bridge/plan.lua`); `resolve/scriptcutter.lua` builds the timeline, places one clip per call, colours clips Blue/Brown and adds Cocoa markers.

## Measuring accuracy

Test changes to the analysis on real footage with a hand edit to compare against: cut the raw timeline with ScriptCutter only (no AI provider, Balanced), export both timelines as FCP7 XML from Resolve, and score every spoken word: kept or cut the way the editor did, brown (kept, flagged), wrongly cut, or missed. Wrongly cut words must stay at zero; brown is the safe answer when unsure. A hand edit also contains editorial choices (dropping a line that is not a retake), so separate those when reading the score.

## Growing the engine

ScriptCutter is built to become a full editing engine (raw clip → cut → images → generated shots → final video) while the page stays simple for creators.

- **A new step** (captions, B-roll images, generated video, music): subclass `Step` in `cutter/project.py`, set `id` and `title`, implement `run()`, and add it to `STEPS`. Write the result into the project (and later into the edit document as a new list next to `clips`, e.g. `overlays`). Return a short note from `flag()` when the creator should check the result ("5 images to approve"); the page shows it as ⚑ automatically. Set `background = True` when the review can open before the step finishes. Steps that call outside services (image or video generation) should read their keys from the settings like `llm.py` does and fail softly: a failed background step is shown, it never breaks the cut.
- **A new output format:** add a module to `cutter/exporters/` with `write(edit, path, view)` and one entry in `FORMATS` (and `EDITORS` if it is an editing app). The bottom bar lists editing apps from `/api/status`.
- **The edit document** has a `version` field. Add fields rather than changing existing ones, and bump the version when a change would break a reader.
- Keep the page creator-friendly: one main button, plain words, and each new step as one chip in the step strip.

## The Resolve bridge (Free and Studio)

The free version of Resolve only runs scripts started from its own Workspace > Scripts menu; Studio also allows outside tools. Both routes run the same Lua file:

- **Studio (live):** the app runs `fuscript -l lua resolve/scriptcutter.lua context|build ...`. Requires *Preferences → System → General → External scripting using = Local*.
- **Free (menu):** "ScriptCutter" writes `bridge/context.json` and opens the app; choosing "ScriptCutter" again (within 30 minutes) builds from `bridge/plan.lua` and writes `bridge/result.json`, which the app polls.

The bridge folder is `<app data>/bridge` (`paths.bridge_dir()`). Things learned the hard way in Resolve 21:
- Resolve's Lua API tables contain an extra numeric entry; iterate them through `objects()`.
- Appending many clips in one `AppendToTimeline` call can hang Resolve, so place one clip per call.
- Media-pool handles fetched before `CreateEmptyTimeline` are rejected afterwards; look them up again.
- For NTSC sources (29.97) Resolve reports source frames in the conformed integer rate.

## Run and test

```bash
python3 web/server.py                    # http://127.0.0.1:7870
python3 cli.py analyze --video clip.mp4  # decisions in the terminal
python3 cli.py analyze --video clip.mp4 --xml cut.xml --srt cut.srt --view hinglish
python3 tests/test_scriptcutter.py       # unit tests (+ an end-to-end test on macOS using `say`)
```

GitHub Actions runs the tests on macOS and Windows for every push (`.github/workflows/tests.yml`).

Conventions: standard library only; colours only in `theme.css`; OS-specific paths only in `paths.py`; Resolve calls only in `scriptcutter.lua`.

## Release

1. Update `VERSION` and add a `## <version>` section to `CHANGELOG.md`.
2. Commit, then tag and push: `git tag v3.1.0 && git push origin main v3.1.0`. (Push one tag at a time: GitHub skips tag events when more than three tags are pushed at once.)
3. The `release` workflow runs the tests, builds `ScriptCutter.zip` and publishes a GitHub release with the changelog section as notes.

To build locally: `python3 tools/build_release.py` (creates `dist/`).

**Permanent download link** (the repository must be public):
```
https://github.com/<owner>/<repo>/releases/latest/download/ScriptCutter.zip
```

**Website button:**
```html
<a href="https://github.com/<owner>/<repo>/releases/latest/download/ScriptCutter.zip"
   style="display:inline-block;padding:14px 28px;border-radius:999px;background:#d0402c;color:#fffaf2;
          font:600 16px/1 'Avenir Next',sans-serif;text-decoration:none">Download ScriptCutter</a>
```
