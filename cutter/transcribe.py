"""
Local Whisper (whisper.cpp / whisper-cli) transcription, done defensively.

Why v1's transcripts were unusable on real footage:
  * The whole file went through Whisper in one pass. On long recordings with
    silences, whisper.cpp falls into repetition loops ("ले ले ले ले ...") and
    its timestamps drift onto 30 s window boundaries.
  * Silent stretches produced hallucinated text ("[Birds chirping]", "Thank you
    for watching").
  * Hindi in Devanagari costs ~1 token per character, so a 30 s window easily
    overflows Whisper's 224-token budget -> loops and truncation.

What this module does instead:
  * Splits audio into <= 24 s chunks at real pauses (from the loudness
    envelope) and transcribes each chunk independently in ONE whisper-cli
    process (model loaded once). A loop can no longer spread, and every
    timestamp is anchored to its chunk offset.
  * Never carries text context between chunks (-mc 0).
  * For non-English speech it can transcribe "via English" (Whisper's translate
    task). That is not verbatim, but repeated takes translate to near-identical
    sentences, which is exactly what retake detection needs, and it is ~10x
    faster and far more stable than Devanagari decoding with small models.
  * Drops non-speech annotations, collapses loops and flags them.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import wave

from .media import find_bin
from .text import has_loop, collapse_loop, is_nonspeech_annotation, is_stock_hallucination

MODEL_DIR = os.path.expanduser("~/.cache/whisper-models")
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{name}"

# Best first. Turbo is the best transcriber but cannot translate.
TRANSCRIBE_PREFERENCE = [
    "ggml-large-v3-turbo.bin", "ggml-large-v3-turbo-q8_0.bin", "ggml-large-v3-turbo-q5_0.bin",
    "ggml-large-v3.bin", "ggml-large-v3-q5_0.bin", "ggml-medium.bin", "ggml-medium-q5_0.bin",
    "ggml-small.bin", "ggml-small-q5_1.bin", "ggml-base.bin",
]
TRANSLATE_PREFERENCE = [
    "ggml-large-v3.bin", "ggml-large-v3-q5_0.bin", "ggml-medium.bin", "ggml-medium-q5_0.bin",
    "ggml-small.bin", "ggml-small-q5_1.bin", "ggml-base.bin",
]
ENGLISH_ONLY = {"ggml-medium.en.bin": "medium.en", "ggml-small.en.bin": "small.en", "ggml-base.en.bin": "base.en"}

DTW_PRESETS = {
    "large-v3-turbo": "large.v3.turbo", "large-v3": "large.v3", "medium": "medium",
    "small": "small", "base": "base", "tiny": "tiny",
}

VERBATIM_PROMPT_EN = "Um, so, uh, I was like, you know... I mean, let me, let me start that again. Okay."

CHUNK_MAX = 24.0      # seconds of audio per Whisper call
CHUNK_SPLIT_GAP = 3.0  # a silence this long always starts a new chunk
CHUNK_PAD = 0.25


def find_whisper_cli() -> str:
    for name in ("whisper-cli", "whisper-cpp"):
        p = find_bin(name)
        if p:
            return p
    return ""


def installed_models() -> list:
    if not os.path.isdir(MODEL_DIR):
        return []
    return sorted(f for f in os.listdir(MODEL_DIR)
                  if f.startswith("ggml-") and f.endswith(".bin") and "silero" not in f
                  and os.path.getsize(os.path.join(MODEL_DIR, f)) > 30_000_000)


def pick_model(requested: str, translate: bool, language: str) -> str:
    """Resolve 'auto' or a filename to an absolute model path."""
    have = set(installed_models())
    if requested and requested != "auto":
        p = requested if os.path.isabs(requested) else os.path.join(MODEL_DIR, requested)
        if os.path.isfile(p):
            return p
        raise RuntimeError(f"Whisper model not found: {p}")
    if language == "en" and not translate:
        for m in TRANSCRIBE_PREFERENCE[:7] + ["ggml-medium.en.bin", "ggml-small.en.bin", "ggml-small.bin",
                                               "ggml-base.en.bin", "ggml-base.bin"]:
            if m in have:
                return os.path.join(MODEL_DIR, m)
    for m in (TRANSLATE_PREFERENCE if translate else TRANSCRIBE_PREFERENCE):
        if m in have:
            return os.path.join(MODEL_DIR, m)
    raise RuntimeError(
        "No usable Whisper model found in ~/.cache/whisper-models. Run ./install.sh "
        "(it downloads one), or download ggml-small.bin from " + MODEL_URL.format(name="ggml-small.bin"))


def can_translate(model_path: str) -> bool:
    n = os.path.basename(model_path)
    return "turbo" not in n and ".en." not in n


def dtw_preset(model_path: str) -> str:
    n = os.path.basename(model_path)
    if n in ENGLISH_ONLY:
        return ENGLISH_ONLY[n]
    core = re.sub(r"^ggml-|(-q\d_\d)?\.bin$", "", n)
    return DTW_PRESETS.get(core, "")


# ---------------------------------------------------------------------------
# chunking
# ---------------------------------------------------------------------------
def build_chunks(regions: list, duration: float, env=None) -> list:
    """Group speech regions into <= CHUNK_MAX second windows split at pauses."""
    chunks = []
    cur = None
    for a, b in regions:
        # a single very long region: split it at its quietest points
        pieces = [(a, b)]
        while pieces[-1][1] - pieces[-1][0] > CHUNK_MAX:
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


def _write_chunk(src: wave.Wave_read, start: float, end: float, path: str):
    sr = src.getframerate()
    src.setpos(int(start * sr))
    frames = src.readframes(max(0, int((end - start) * sr)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(frames)


# ---------------------------------------------------------------------------
# whisper-cli runs
# ---------------------------------------------------------------------------
def detect_language(wav_path: str, regions: list, model_path: str) -> tuple:
    """
    Detect the spoken language. Three ~15 s samples of the longest-running
    sound (at 20/50/80% of the recording) vote, weighted by Whisper's own
    probability - a single sample can land on typing or music.
    Returns (code, prob).
    """
    exe = find_whisper_cli()
    if not exe or not regions:
        return "en", 0.0
    if ".en." in os.path.basename(model_path):
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
    tmpdir = tempfile.mkdtemp(prefix="sc2_lang_")
    try:
        with wave.open(wav_path, "rb") as src:
            sr = src.getframerate()
            for k, picked in enumerate(samples):
                sample = os.path.join(tmpdir, f"s{k}.wav")
                with wave.open(sample, "wb") as dst:
                    dst.setnchannels(1)
                    dst.setsampwidth(2)
                    dst.setframerate(sr)
                    for a, b in picked:
                        src.setpos(int(a * sr))
                        dst.writeframes(src.readframes(int((b - a) * sr)))
                try:
                    res = subprocess.run([exe, "-m", model_path, "-l", "auto", "-dl", "-f", sample],
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         errors="replace", timeout=300)
                except (subprocess.SubprocessError, OSError):
                    continue
                m = re.search(r"auto-detected language:\s*([a-z]{2,3})\s*\(p\s*=\s*([0-9.]+)\)", res.stdout)
                if m:
                    votes.setdefault(m.group(1), []).append(float(m.group(2)))
    finally:
        for f in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)
    if not votes:
        return "en", 0.0
    best = max(votes, key=lambda l: sum(votes[l]))
    return best, round(sum(votes[best]) / max(1, len(samples)), 3)


def _decode(s: str) -> bytes:
    return s.encode("utf-8", "surrogateescape")


def _parse_chunk_json(path: str, offset: float, chunk_end: float, want_words: bool) -> list:
    with open(path, "rb") as f:
        data = json.loads(f.read().decode("utf-8", "surrogateescape"))
    segs = []
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
                raw = _decode(t.get("text", ""))
                to = t.get("offsets", {})
                ts = offset + to.get("from", 0) / 1000.0
                te = offset + to.get("to", 0) / 1000.0
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
        text = _decode(seg.get("text", "")).decode("utf-8", "replace").strip()
        if not text:
            continue
        segs.append({
            "start": round(min(t0, chunk_end), 3),
            "end": round(min(max(t1, t0 + 0.05), chunk_end), 3),
            "text": text,
            "p": round(sum(probs) / len(probs), 3) if probs else 0.0,
            "words": words,
        })
    return segs


def transcribe(
    wav_path: str,
    regions: list,
    duration: float,
    env,
    model_path: str,
    language: str,
    translate: bool,
    progress=None,
    log=None,
) -> dict:
    exe = find_whisper_cli()
    if not exe:
        raise RuntimeError("whisper-cli not found. Install it with: brew install whisper-cpp")
    chunks = build_chunks(regions, duration, env)
    if not chunks:
        return {"raw": [], "chunks": []}

    want_words = not translate
    tmpdir = tempfile.mkdtemp(prefix="sc2_chunks_")
    files = []
    try:
        with wave.open(wav_path, "rb") as src:
            for i, (a, b) in enumerate(chunks):
                p = os.path.join(tmpdir, f"c{i:05d}.wav")
                _write_chunk(src, a, b, p)
                files.append(p)

        cmd = [exe, "-m", model_path, "-l", language, "-mc", "0", "-ojf", "-np", "-sns",
               "-t", str(max(2, min(8, (os.cpu_count() or 4) // 2)))]
        if translate:
            cmd.append("-tr")
        else:
            preset = dtw_preset(model_path)
            if preset:
                cmd += ["-nfa", "-dtw", preset]
            if language == "en":
                cmd += ["--prompt", VERBATIM_PROMPT_EN]
        log_path = os.path.join(tmpdir, "whisper.log")
        t_start = time.time()
        with open(log_path, "w") as lf:
            proc = subprocess.Popen(cmd + files, stdout=subprocess.DEVNULL, stderr=lf)
            while proc.poll() is None:
                time.sleep(0.4)
                if progress:
                    done = sum(1 for f in files if os.path.isfile(f + ".json"))
                    progress(done / len(files))
        if proc.returncode != 0:
            with open(log_path, "r", errors="replace") as lf:
                tail = lf.read()[-600:]
            raise RuntimeError(f"whisper-cli failed (exit {proc.returncode}): {tail}")
        if log:
            log(f"Whisper finished {len(files)} chunks in {time.time() - t_start:.1f}s")

        segments = []
        for ci, ((a, b), f) in enumerate(zip(chunks, files)):
            jp = f + ".json"
            if not os.path.isfile(jp):
                continue
            for seg in _parse_chunk_json(jp, a, b, want_words):
                seg["chunk"] = ci
                segments.append(seg)
    finally:
        for f in os.listdir(tmpdir):
            try:
                os.unlink(os.path.join(tmpdir, f))
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass

    return {"raw": segments, "chunks": chunks}


def _regular_loops(segments: list) -> set:
    """
    Indices of segments that are machine-like repeats: >= 3 near-identical
    sentences in one Whisper window at perfectly regular spacing (e.g. every
    2.00 s over music). Real retakes come at irregular intervals.
    """
    from difflib import SequenceMatcher
    from .text import tokens

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
    """Drop annotations / hallucinations, collapse loops, snap to speech. Works on copies."""
    from .energy import speech_overlap, clip_to_speech
    from .text import stock_share

    segments = [dict(s, words=list(s.get("words", [])), flags=list(s.get("flags", []))) for s in segments]
    total_end = max([s["end"] for s in segments] or [0.0])
    loops = _regular_loops(segments)
    out = []
    for idx, s in enumerate(segments):
        if idx in loops:
            continue
        text = s["text"]
        dur = max(0.01, s["end"] - s["start"])
        speech = speech_overlap(regions, s["start"], s["end"])
        if is_nonspeech_annotation(text):
            continue
        if speech < 0.15 or (speech / dur < 0.2 and dur > 2.0):
            continue  # text where there is no sound = hallucination
        n_words = len(text.split())
        if is_stock_hallucination(text) and (speech / dur < 0.6 or speech > 2.5 * max(1, n_words) * 0.6):
            continue  # "Thank you." over seconds of typing/music
        if stock_share(text) >= 0.6:
            near_end = s["start"] > total_end * 0.9
            if n_words / max(0.5, speech) < 1.2 or not near_end:
                continue  # "Thank you for watching, please subscribe" mid-video over typing
        flags = list(s.get("flags", []))
        if has_loop(text):
            text = collapse_loop(text)
            flags.append("loop")
            s["words"] = []  # word timings inside a loop are meaningless
        clipped = clip_to_speech(regions, s["start"], s["end"], slack=0.05)
        if clipped:
            s["start"], s["end"] = round(clipped[0], 3), round(clipped[1], 3)
        s["text"] = text
        s["flags"] = flags
        out.append(s)
    out.sort(key=lambda x: x["start"])
    # resolve small overlaps created by snapping
    for i in range(1, len(out)):
        if out[i]["start"] < out[i - 1]["end"]:
            mid = round((out[i]["start"] + out[i - 1]["end"]) / 2, 3)
            out[i - 1]["end"] = max(out[i - 1]["start"] + 0.05, mid)
            out[i]["start"] = min(out[i]["end"] - 0.05, mid)
    return out
