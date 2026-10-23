"""
Neural voice detection (Silero VAD, bundled with whisper.cpp >= 1.8).

The loudness envelope finds *sound*; in screen recordings that includes
typing, mouse clicks and background music, which Whisper then "transcribes"
as "Thank you." / "Thanks for watching!". Silero answers a different
question - is a human voice present? - so only real speech reaches Whisper.

Optional: used when ggml-silero-*.bin is in ~/.cache/whisper-models
(install.sh downloads it, 885 KB). Without it the pipeline still works with
the loudness-based regions plus text-level hallucination filters.
"""
from __future__ import annotations

import os
import re
import subprocess

from .media import find_bin
from .transcribe import MODEL_DIR

SILERO_FILES = ["ggml-silero-v6.2.0.bin", "ggml-silero-v5.1.2.bin"]
SILERO_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin"


def silero_model() -> str:
    for f in SILERO_FILES:
        p = os.path.join(MODEL_DIR, f)
        if os.path.isfile(p) and os.path.getsize(p) > 100_000:
            return p
    return ""


def vad_binary() -> str:
    p = find_bin("whisper-vad-speech-segments")
    if p:
        return p
    # Homebrew keeps it next to whisper-cli
    cli = find_bin("whisper-cli")
    if cli:
        c = os.path.join(os.path.dirname(os.path.realpath(cli)), "whisper-vad-speech-segments")
        if os.path.isfile(c):
            return c
    return ""


def available() -> bool:
    return bool(silero_model() and vad_binary())


def voice_segments(wav_path: str, threshold: float = 0.5) -> list:
    """[(start, end), ...] in seconds where Silero hears a voice."""
    exe, model = vad_binary(), silero_model()
    if not exe or not model:
        return []
    res = subprocess.run([exe, "-vm", model, "-f", wav_path, "-vt", str(threshold), "-vspd", "150", "-vsd", "200",
                          "-vp", "60", "-np"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", timeout=1800)
    if res.returncode != 0:
        raise RuntimeError(f"Silero VAD failed: {res.stderr.strip()[-300:]}")
    segs = []
    for line in res.stdout.splitlines():
        low = line.lower()
        if "start" in low and "end" in low:
            nums = re.findall(r"[-+]?\d+(?:\.\d+)?", line.split(":", 1)[-1])
            if len(nums) >= 2:
                a, b = float(nums[-2]), float(nums[-1])
                segs.append((a / 100.0, b / 100.0))  # centiseconds
    return sorted(segs)


def intersect(regions: list, voice: list, pad: float = 0.12) -> tuple:
    """Split loudness regions into (voiced parts, non-voice sound parts)."""
    voiced, other = [], []
    vi = 0
    for a, b in regions:
        cur = a
        while vi < len(voice) and voice[vi][1] + pad < a:
            vi += 1
        k = vi
        while k < len(voice) and voice[k][0] - pad < b:
            va, vb = max(a, voice[k][0] - pad), min(b, voice[k][1] + pad)
            if va > cur + 0.05:
                other.append((cur, va))
            if vb > va:
                voiced.append((round(va, 3), round(vb, 3)))
            cur = max(cur, vb)
            k += 1
        if b > cur + 0.05:
            other.append((round(cur, 3), round(b, 3)))
    return voiced, other
