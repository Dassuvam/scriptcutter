"""ffmpeg / ffprobe helpers and the per-file analysis cache."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from fractions import Fraction

from . import paths

INSTALL_HINT = "Run the ScriptCutter installer again."


def require_program(name: str) -> str:
    p = paths.find_program(name)
    if not p:
        raise RuntimeError(f"'{name}' was not found. {INSTALL_HINT}")
    return p


def cache_dir_for(path: str) -> str:
    """One cache folder per source file, keyed by path, size and modification time."""
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8")).hexdigest()[:16]
    d = os.path.join(paths.cache_dir(), key)
    os.makedirs(d, exist_ok=True)
    return d


def _rate(s: str) -> float:
    try:
        fr = Fraction(s)
        return float(fr) if fr > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: str) -> dict:
    res = subprocess.run(
        [require_program("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"Could not read '{os.path.basename(path)}': {res.stderr.strip()[:300]}")
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    timecode = (fmt.get("tags") or {}).get("timecode")
    for s in streams:
        timecode = timecode or (s.get("tags") or {}).get("timecode")
    fps = (_rate(video.get("r_frame_rate", "0")) or _rate(video.get("avg_frame_rate", "0"))) if video else 0.0
    return {
        "path": os.path.abspath(path),
        "duration": float(fmt.get("duration") or 0.0),
        "fps": fps,
        "has_video": video is not None,
        "audio_streams": len(audios),
        "start_timecode": timecode or "00:00:00:00",
    }


def extract_audio(path: str, out_wav: str, audio_stream: int = 0) -> str:
    """16 kHz mono 16-bit WAV of one audio stream (all its channels mixed down)."""
    if os.path.isfile(out_wav) and os.path.getsize(out_wav) > 1000:
        return out_wav
    tmp = out_wav + ".part.wav"
    res = subprocess.run(
        [require_program("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", path, "-map", f"0:a:{audio_stream}",
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tmp],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    if res.returncode != 0 or not os.path.isfile(tmp):
        if os.path.isfile(tmp):
            os.unlink(tmp)
        if "matches no streams" in res.stderr:
            raise RuntimeError(f"'{os.path.basename(path)}' has no audio track.")
        raise RuntimeError(f"Audio extraction failed: {res.stderr.strip()[:400]}")
    os.replace(tmp, out_wav)
    return out_wav


def detect_frozen_video(path: str, duration: float, min_freeze: float = 1.0, progress=None) -> list:
    """
    [(start, end), ...] where the picture does not change. Used for screen
    recordings to tell "silent but something happens on screen" apart from
    dead time. Decoded at 5 fps / 320 px to stay fast.
    """
    cmd = [require_program("ffmpeg"), "-nostdin", "-hide_banner", "-hwaccel", "auto", "-i", path, "-an",
           "-vf", f"fps=5,scale=320:-2,freezedetect=n=0.002:d={min_freeze}", "-f", "null", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
    frozen, start = [], None
    for line in proc.stderr:
        if "freeze_start:" in line:
            start = float(line.rsplit("freeze_start:", 1)[1].split()[0])
        elif "freeze_end:" in line and start is not None:
            frozen.append((start, float(line.rsplit("freeze_end:", 1)[1].split()[0])))
            start = None
        elif progress and "time=" in line and duration > 0:
            try:
                hh, mm, ss = line.split("time=", 1)[1].split()[0].split(":")
                progress(min(1.0, (int(hh) * 3600 + int(mm) * 60 + float(ss)) / duration))
            except (ValueError, IndexError):
                pass
    proc.wait()
    if start is not None:
        frozen.append((start, duration))
    return frozen
