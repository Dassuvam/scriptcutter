"""
Local speech-to-text with whisper.cpp (whisper-cli).

The audio is split at real pauses into chunks of at most 24 s which are
transcribed independently in one whisper-cli process (the model loads once).
That keeps repetition loops and timestamp drift from spreading through long
recordings. Non-English speech can be analysed through Whisper's English
translation: repeated takes become near-identical sentences, which is what
retake detection needs.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import wave
from difflib import SequenceMatcher

from . import paths
from .energy import clip_to_speech, speech_overlap
from .text import collapse_loop, has_loop, is_nonspeech_annotation, is_stock_hallucination, stock_share, tokens

# best first; "turbo" cannot translate and ".en" models only understand English
MODEL_ORDER = [
    "large-v3-turbo", "large-v3-turbo-q8_0", "large-v3-turbo-q5_0", "large-v3", "large-v3-q5_0",
    "medium.en", "medium", "medium-q5_0", "small.en", "small", "small-q5_1", "base.en", "base",
]
DTW_PRESETS = {
    "large-v3-turbo": "large.v3.turbo", "large-v3": "large.v3", "medium": "medium", "medium.en": "medium.en",
    "small": "small", "small.en": "small.en", "base": "base", "base.en": "base.en",
}
VERBATIM_PROMPT_EN = "Um, so, uh, I was like, you know... I mean, let me, let me start that again. Okay."

CHUNK_MAX = 24.0       # seconds of audio per Whisper window
CHUNK_SPLIT_GAP = 3.0  # a silence this long always starts a new chunk
CHUNK_PAD = 0.25


def find_whisper_cli() -> str:
    """whisper.cpp's command-line program (called whisper-cpp in some older builds)."""
    return paths.find_program("whisper-cli") or paths.find_program("whisper-cpp")


def installed_models() -> list:
    """Model files in the model folder, by name. Tiny broken downloads (< 30 MB) are ignored."""
    d = paths.model_dir()
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.startswith("ggml-") and f.endswith(".bin") and "silero" not in f
                  and os.path.getsize(os.path.join(d, f)) > 30_000_000)


def _core_name(filename: str) -> str:
    """'ggml-large-v3-turbo.bin' -> 'large-v3-turbo'."""
    return re.sub(r"^ggml-|\.bin$", "", filename)


def can_translate(model_path: str) -> bool:
    """Turbo models were trained without translation, and '.en' models only know English."""
    core = _core_name(os.path.basename(model_path))
    return "turbo" not in core and not core.endswith(".en")


def pick_model(requested: str, translate: bool, language: str) -> str:
    """
    'auto' or a model file name -> absolute path. 'auto' takes the best installed
    model (MODEL_ORDER) that can do the job. A chosen model that has since been
    deleted also falls back to 'auto', so an old saved setting never blocks an analysis.
    """
    d = paths.model_dir()
    if requested and requested != "auto":
        p = requested if os.path.isabs(requested) else os.path.join(d, requested)
        if os.path.isfile(p):
            return p
    have = {_core_name(f): f for f in installed_models()}
    for core in MODEL_ORDER:
        if core not in have:
            continue
        if translate and not can_translate(core):
            continue
        if core.endswith(".en") and (translate or language != "en"):
            continue
        return os.path.join(d, have[core])
    raise RuntimeError("No Whisper model found. Run the ScriptCutter installer to download one.")


def dtw_preset(model_path: str) -> str:
    """whisper.cpp's name for the model's attention layout, needed for exact word times (DTW)."""
    core = re.sub(r"-q\d_\d$", "", _core_name(os.path.basename(model_path)))
    return DTW_PRESETS.get(core, "")


# --------------------------------------------------------------------------- chunks
def build_chunks(regions: list, duration: float, env=None) -> list:
    """Group speech regions into windows of at most CHUNK_MAX seconds, split at pauses."""
    chunks, cur = [], None
    for a, b in regions:
        pieces = [(a, b)]
        while pieces[-1][1] - pieces[-1][0] > CHUNK_MAX:  # one very long region: split at its quietest point
            pa, pb = pieces.pop()
            cut = env.quietest(pa + CHUNK_MAX * 0.6, pa + CHUNK_MAX) if env else pa + CHUNK_MAX
            pieces += [(pa, cut), (cut, pb)]
        for pa, pb in pieces:
            if cur is None:
                cur = [pa, pb]
            elif pa - cur[1] >= CHUNK_SPLIT_GAP or pb - cur[0] > CHUNK_MAX:
                chunks.append(cur)
                cur = [pa, pb]
            else:
                cur[1] = pb
    if cur:
        chunks.append(cur)
    out = []
    for i, (a, b) in enumerate(chunks):
        lo = out[-1][1] if out else 0.0
        hi = chunks[i + 1][0] if i + 1 < len(chunks) else duration
        out.append((round(max(lo, a - CHUNK_PAD), 3), round(min(hi, b + CHUNK_PAD), 3)))
    return out


def _write_wav(path: str, src: wave.Wave_read, spans: list):
    sr = src.getframerate()
    with wave.open(path, "wb") as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(sr)
        for a, b in spans:
            src.setpos(int(a * sr))
            dst.writeframes(src.readframes(max(0, int((b - a) * sr))))


# --------------------------------------------------------------------------- whisper-cli
def detect_language(wav_path: str, regions: list, model_path: str) -> tuple:
    """
    (language code, probability). Three ~15 s samples of speech (20/50/80% into
    the recording) vote, so one sample landing on music or typing can't decide.
    """
    exe = find_whisper_cli()
    if not exe or not regions:
        return "en", 0.0
    if _core_name(os.path.basename(model_path)).endswith(".en"):
        return "en", 1.0  # English-only model
    pool = [r for r in regions if r[1] - r[0] >= 0.8] or list(regions)
    total = sum(b - a for a, b in pool)
    samples = []
    for frac in ((0.2, 0.5, 0.8) if total > 45 else (0.0,)):
        target, seen, picked, acc = total * frac, 0.0, [], 0.0
        for a, b in pool:
            if seen + (b - a) < target:
                seen += b - a
                continue
            picked.append((a, b))
            acc += b - a
            if acc >= 15:
                break
        if picked:
            samples.append(picked)
    votes = {}
    with tempfile.TemporaryDirectory(prefix="scriptcutter_lang_") as tmp, wave.open(wav_path, "rb") as src:
        for k, picked in enumerate(samples):
            sample = os.path.join(tmp, f"s{k}.wav")
            _write_wav(sample, src, picked)
            try:
                res = subprocess.run([exe, "-m", model_path, "-l", "auto", "-dl", "-f", sample],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     errors="replace", timeout=300)
            except (subprocess.SubprocessError, OSError):
                continue
            m = re.search(r"auto-detected language:\s*([a-z]{2,3})\s*\(p\s*=\s*([0-9.]+)\)", res.stdout)
            if m:
                votes.setdefault(m.group(1), []).append(float(m.group(2)))
    if not votes:
        return "en", 0.0
    best = max(votes, key=lambda lang: sum(votes[lang]))
    return best, round(sum(votes[best]) / len(samples), 3)


def _bytes(s: str) -> bytes:
    # whisper.cpp may split one UTF-8 character across tokens; the JSON is read
    # with surrogateescape so the raw bytes can be glued back together
    return s.encode("utf-8", "surrogateescape")


def _parse_chunk(path: str, offset: float, chunk_end: float, want_words: bool) -> list:
    with open(path, "rb") as f:
        data = json.loads(f.read().decode("utf-8", "surrogateescape"))
    segments = []
    for seg in data.get("transcription", []):
        off = seg.get("offsets", {})
        t0 = offset + off.get("from", 0) / 1000.0
        t1 = offset + off.get("to", 0) / 1000.0
        toks = [t for t in seg.get("tokens", []) if not t.get("text", "").startswith("[_")]
        probs = [float(t.get("p", 0.0)) for t in toks]
        words = []
        if want_words:
            cur = None
            for t in toks:
                raw = _bytes(t.get("text", ""))
                to = t.get("offsets", {})
                ts, te = offset + to.get("from", 0) / 1000.0, offset + to.get("to", 0) / 1000.0
                dtw = t.get("t_dtw", -1)
                if isinstance(dtw, (int, float)) and dtw >= 0:
                    ts = offset + dtw / 100.0
                if cur is None or raw.startswith(b" "):
                    if cur:
                        words.append(cur)
                    cur = {"b": raw.lstrip(b" "), "start": ts, "end": te, "p": [float(t.get("p", 0))]}
                else:
                    cur["b"] += raw
                    cur["end"] = te
                    cur["p"].append(float(t.get("p", 0)))
            if cur:
                words.append(cur)
            words = [{"w": w["b"].decode("utf-8", "replace").strip(),
                      "start": round(min(w["start"], chunk_end), 3),
                      "end": round(min(max(w["end"], w["start"]), chunk_end), 3),
                      "p": round(sum(w["p"]) / len(w["p"]), 3)} for w in words]
            words = [w for w in words if w["w"]]
        text = _bytes(seg.get("text", "")).decode("utf-8", "replace").strip()
        if text:
            segments.append({
                "start": round(min(t0, chunk_end), 3),
                "end": round(min(max(t1, t0 + 0.05), chunk_end), 3),
                "text": text,
                "p": round(sum(probs) / len(probs), 3) if probs else 0.0,
                "words": words,
            })
    return segments


def transcribe(wav_path: str, regions: list, duration: float, env, model_path: str, language: str,
               translate: bool, progress=None, log=None) -> dict:
    """Raw Whisper segments for all speech chunks: {"raw": [...], "chunks": [...]}."""
    exe = find_whisper_cli()
    if not exe:
        raise RuntimeError("whisper-cli was not found. Run the ScriptCutter installer again.")
    chunks = build_chunks(regions, duration, env)
    if not chunks:
        return {"raw": [], "chunks": []}

    with tempfile.TemporaryDirectory(prefix="scriptcutter_chunks_") as tmp:
        files = []
        with wave.open(wav_path, "rb") as src:
            for i, span in enumerate(chunks):
                files.append(os.path.join(tmp, f"c{i:05d}.wav"))
                _write_wav(files[-1], src, [span])

        # on a graphics chip (every Mac; Windows with the installer's NVIDIA build) half the cores feed it;
        # on the processor alone every core but one does the work
        gpu = not paths.IS_WINDOWS or bool(paths.config_value("whisper_gpu"))
        cores = os.cpu_count() or 4
        threads = max(2, min(8, cores // 2)) if gpu else min(8, max(2, cores - 1))
        cmd = [exe, "-m", model_path, "-l", language, "-mc", "0", "-ojf", "-np", "-sns", "-t", str(threads)]
        if translate:
            cmd.append("-tr")
        else:
            preset = dtw_preset(model_path)
            if preset:
                cmd += ["-nfa", "-dtw", preset]
            if language == "en":
                cmd += ["--prompt", VERBATIM_PROMPT_EN]
        started = time.time()
        log_path = os.path.join(tmp, "whisper.log")
        returncode = _run_whisper(cmd + files, files, log_path, progress)
        if returncode != 0 and gpu:
            # the graphics chip can fail (older Macs, memory pressure, an old NVIDIA driver):
            # run once more on the processor before giving up
            if log:
                log("Speech recognition failed on the graphics chip; trying again on the processor (slower)...")
            returncode = _run_whisper(cmd + ["-ng"] + files, files, log_path, progress)
        if returncode != 0:
            with open(log_path, "r", errors="replace") as lf:
                raise RuntimeError(f"whisper-cli failed (exit {returncode}): {lf.read()[-600:]}")
        if log:
            log(f"Whisper finished {len(files)} chunks in {time.time() - started:.1f}s")

        segments = []
        for ci, ((a, b), f) in enumerate(zip(chunks, files)):
            if os.path.isfile(f + ".json"):
                for seg in _parse_chunk(f + ".json", a, b, want_words=not translate):
                    seg["chunk"] = ci
                    segments.append(seg)
    return {"raw": segments, "chunks": chunks}


def _run_whisper(cmd: list, files: list, log_path: str, progress=None) -> int:
    """Run whisper-cli over all chunk files; progress = share of chunks with a finished .json."""
    for f in files:  # a retry must not count the first run's results as progress
        if os.path.isfile(f + ".json"):
            os.unlink(f + ".json")
    with open(log_path, "w") as lf:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=lf)
        while proc.poll() is None:
            time.sleep(0.4)
            if progress:
                progress(sum(1 for f in files if os.path.isfile(f + ".json")) / len(files))
    return proc.returncode


# --------------------------------------------------------------------------- cleaning
def _regular_loops(segments: list) -> set:
    """
    Indices of machine-like repeats: 3+ near-identical sentences in one Whisper
    window at perfectly regular spacing (e.g. every 2.00 s over music). Real
    retakes come at irregular intervals, so they are left alone.
    """
    drop = set()
    i = 0
    while i < len(segments):
        j = i + 1
        while (j < len(segments) and segments[j].get("chunk") == segments[i].get("chunk")
               and SequenceMatcher(None, tokens(segments[i]["text"]), tokens(segments[j]["text"])).ratio() >= 0.7):
            j += 1
        if j - i >= 3:
            starts = [segments[k]["start"] for k in range(i, j)]
            deltas = [b - a for a, b in zip(starts, starts[1:])]
            if max(deltas) - min(deltas) <= 0.15:
                drop.update(range(i + 1, j))
                segments[i].setdefault("flags", []).append("loop")
        i = max(j, i + 1)
    return drop


def clean_segments(segments: list, regions: list) -> list:
    """Drop hallucinations and annotations, collapse loops, snap to real speech. Returns copies."""
    segments = [dict(s, words=list(s.get("words", [])), flags=list(s.get("flags", []))) for s in segments]
    total_end = max([s["end"] for s in segments] or [0.0])
    loops = _regular_loops(segments)
    out = []
    for idx, s in enumerate(segments):
        text = s["text"]
        dur = max(0.01, s["end"] - s["start"])
        speech = speech_overlap(regions, s["start"], s["end"])
        n_words = len(text.split())
        if idx in loops or is_nonspeech_annotation(text):
            continue
        if speech < 0.15 or (speech / dur < 0.2 and dur > 2.0):
            continue  # words where there is no sound
        if is_stock_hallucination(text) and (speech / dur < 0.6 or speech > 1.5 * max(1, n_words)):
            continue  # "Thank you." over seconds of typing or music
        if stock_share(text) >= 0.6 and (n_words / max(0.5, speech) < 1.2 or s["start"] < total_end * 0.9):
            continue  # "Thanks for watching, please subscribe" in the middle of a video
        if has_loop(text):
            s["text"] = collapse_loop(text)
            s["flags"].append("loop")
            s["words"] = []  # word timings inside a loop are meaningless
        clipped = clip_to_speech(regions, s["start"], s["end"], slack=0.05)
        if clipped:
            s["start"], s["end"] = round(clipped[0], 3), round(clipped[1], 3)
        out.append(s)
    out.sort(key=lambda x: x["start"])
    for i in range(1, len(out)):  # snapping can create small overlaps
        if out[i]["start"] < out[i - 1]["end"]:
            mid = round((out[i]["start"] + out[i - 1]["end"]) / 2, 3)
            out[i - 1]["end"] = max(out[i - 1]["start"] + 0.05, mid)
            out[i]["start"] = min(out[i]["end"] - 0.05, mid)
    return out
