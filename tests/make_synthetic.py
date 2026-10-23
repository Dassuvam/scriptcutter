"""
Builds a synthetic "raw recording" with known retakes using macOS `say`, so the
whole pipeline can be checked against ground truth without real footage.

    python3 tests/make_synthetic.py /tmp/synth.wav
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

# (text, pause_after_seconds, expected) ; expected: keep | remove
SCRIPT = [
    ("Hey everyone, welcome back to the channel.", 1.2, "keep"),
    ("Today I am going to show you how to", 1.6, "remove"),
    ("Today I am going to show you how to edit videos faster with DaVinci Resolve.", 1.0, "keep"),
    ("The first thing you need is a good microphone.", 1.4, "keep"),
    ("The second thing you need is good lighting from a window.", 0.9, "remove"),
    ("Let me say that again.", 1.1, "remove"),
    ("The second thing you need is soft and even lighting from a window.", 1.0, "keep"),
    ("The third thing is a quiet room with a rug on the floor.", 2.5, "keep"),
    ("Now open the color page and add a serial node.", 1.0, "keep"),
    ("And that is it for today.", 0.8, "keep"),
    ("Thanks for watching, see you in the next one.", 1.5, "keep"),
]


def build(out_wav: str, voice: str = "Samantha") -> list:
    say, ffmpeg = shutil.which("say"), shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not say:
        raise RuntimeError("macOS 'say' not available")
    tmp = tempfile.mkdtemp(prefix="sc2_synth_")
    parts, truth, t = [], [], 1.0
    silence = os.path.join(tmp, "lead.wav")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "1.0",
                    silence], check=True)
    parts.append(silence)
    for i, (text, pause, exp) in enumerate(SCRIPT):
        aiff = os.path.join(tmp, f"p{i}.aiff")
        wav = os.path.join(tmp, f"p{i}.wav")
        subprocess.run([say, "-v", voice, "-r", "175", "-o", aiff, text], check=True)
        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", aiff, "-ac", "1", "-ar", "16000", wav], check=True)
        dur = float(subprocess.run([shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe", "-v", "error",
                                    "-show_entries", "format=duration", "-of", "csv=p=0", wav],
                                   capture_output=True, text=True).stdout.strip())
        truth.append({"text": text, "start": t, "end": t + dur, "expected": exp})
        t += dur
        sil = os.path.join(tmp, f"s{i}.wav")
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t",
                        str(pause), sil], check=True)
        parts += [wav, sil]
        t += pause
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w") as f:
        f.writelines(f"file '{p}'\n" for p in parts)
    # light room noise so the adaptive threshold has a floor to find
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                    "-f", "lavfi", "-i", "anoisesrc=r=16000:a=0.002:c=pink", "-filter_complex",
                    "[0:a][1:a]amix=inputs=2:duration=first:normalize=0", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", out_wav], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return truth


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "synth.wav"
    for row in build(out):
        print(f"{row['start']:6.2f}-{row['end']:6.2f} {row['expected']:6s} {row['text']}")
