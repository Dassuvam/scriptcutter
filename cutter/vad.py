"""
Neural voice detection with Silero VAD (shipped with whisper.cpp).

The loudness envelope finds any sound, including typing, clicks and music;
Silero tells whether a human voice is present, so only real speech reaches
Whisper. Optional: used when ggml-silero-*.bin is in the model folder.
"""
from __future__ import annotations

import os
import re
import subprocess

from . import paths

SILERO_FILES = ["ggml-silero-v6.2.0.bin", "ggml-silero-v5.1.2.bin"]
SILERO_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin"


def silero_model() -> str:
    for f in SILERO_FILES:
        p = os.path.join(paths.model_dir(), f)
        if os.path.isfile(p) and os.path.getsize(p) > 100_000:
            return p
    return ""


def vad_program() -> str:
    found = paths.find_program("whisper-vad-speech-segments")
    if found:
        return found
    cli = paths.find_program("whisper-cli")  # Homebrew keeps it next to whisper-cli
    if cli:
        exe = "whisper-vad-speech-segments" + (".exe" if paths.IS_WINDOWS else "")
        candidate = os.path.join(os.path.dirname(os.path.realpath(cli)), exe)
        if os.path.isfile(candidate):
            return candidate
    return ""


def available() -> bool:
    return bool(silero_model() and vad_program())


def voice_segments(wav_path: str, threshold: float = 0.5) -> list:
    """[(start, end), ...] in seconds where a voice is heard."""
    res = subprocess.run([vad_program(), "-vm", silero_model(), "-f", wav_path, "-vt", str(threshold),
                          "-vspd", "150", "-vsd", "200", "-vp", "60", "-np"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", timeout=1800)
    if res.returncode != 0:
        raise RuntimeError(f"Voice detection failed: {res.stderr.strip()[-300:]}")
    segments = []
    for line in res.stdout.splitlines():
        if "start" in line.lower() and "end" in line.lower():
            nums = re.findall(r"[-+]?\d+(?:\.\d+)?", line.split(":", 1)[-1])
            if len(nums) >= 2:
                segments.append((float(nums[-2]) / 100.0, float(nums[-1]) / 100.0))  # centiseconds
    return sorted(segments)


def intersect(regions: list, voice: list, pad: float = 0.12) -> tuple:
    """Split loudness regions into (voiced parts, other sounds)."""
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
