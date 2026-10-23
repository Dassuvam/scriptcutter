# ScriptCutter v2

Retake-aware auto cutting for DaVinci Resolve. 100% free and local (Whisper + ffmpeg); an AI second opinion from free tiers (Gemini, Groq, OpenRouter, Ollama) is optional.

**Colours on the new timeline**
- **Blue** – kept, sure.
- **Brown** – kept, but the tool is unsure (possible retake, odd sound, silent screen action). Each brown clip gets a Cocoa marker with the reason; jump between them with Shift+↑/↓.
- Cut – only when the evidence is strong. Anything uncertain stays in the timeline as brown.

Your original timeline is never modified or deleted. A new one is created.

## Setup
```bash
./install.sh
```
Installs ffmpeg + whisper-cpp, downloads `ggml-small.bin` and the 885 KB Silero voice detector, and adds **Workspace > Scripts > ScriptCutterV2** to Resolve. In Resolve, set Preferences > System > General > External scripting = Local.

## Use
1. Put the raw clip(s) on V1 of a timeline, then run Workspace > Scripts > ScriptCutterV2 (or `./start.sh`).
2. Choose **Talking head** or **Screen recording**, then press **Analyse**.
3. Review: every line shows Keep / Review / Cut with a reason. ▶ plays the line, and "hear the later take" plays the take it was compared against. Keys: ↑↓, Space, 1/2/3.
4. Press **Create timeline in Resolve**, or export an EDL + JSON report.

CLI: `python3 cli.py analyze --video raw.mp4 --edl cut.edl` · `python3 cli.py resolve`

## Why v1 struggled (and what changed)
| v1 problem | v2 fix |
|---|---|
| Whisper ran on the whole file: repetition loops ("ले ले ले…"), hallucinations in silence, timestamps drifting to 30 s boundaries | Audio is split at pauses into ≤24 s chunks, transcribed independently (one process, model loaded once), loops and stock hallucinations filtered |
| Hindi text was stripped by an ASCII-only regex, so takes could not be compared | Unicode text handling. Non-English speech is analysed via Whisper's English translation (stable, about 10× faster, and retakes translate to near-identical sentences) |
| Decisions were made on silence chunks, not sentences | Decisions are made per phrase; the last complete take wins; re-recorded blocks are detected as a whole |
| LLM returned a keep-list with an 18 s timeout; failures silently fell back | The local engine decides and the AI only reviews (chunked, retried, errors shown). AI-only suggestions become brown, never cut |
| Fixed −32 dB silence threshold clipped soft word endings | Threshold set from each file's own noise floor and speech level |
| EDL used the timeline fps for 29.97/30 fps sources (cuts off by up to 0.2 s) and dropped the last ~20% of each clip on a 24 fps timeline | Clips are placed with exact source frames via the Resolve API, trimmed timeline items respected |
| Single-threaded server, no progress, logs sent to /dev/null | Background jobs, progress bar, visible log, results cached so changing settings re-cuts instantly |

Optional bigger models (`install.sh` asks): `medium` (better translation), `large-v3-turbo` (best for English or original-language transcripts; cannot translate).

Tests: `python3 tests/test_v2.py` (includes a synthetic end-to-end recording built with macOS `say`).
