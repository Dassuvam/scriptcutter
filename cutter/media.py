"""
ffmpeg / ffprobe helpers and the on-disk analysis cache.

The cache key is (absolute path, size, mtime), so re-running the analysis with
different settings on the same footage skips audio extraction and Whisper.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from fractions import Fraction

CACHE_ROOT = os.path.expanduser("~/Library/Caches/ScriptCutterV2")

_BIN_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]


def find_bin(name: str) -> str:
    p = shutil.which(name)
    if p:
        return p
    for d in _BIN_DIRS:
        c = os.path.join(d, name)
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return ""


def require_bin(name: str, hint: str) -> str:
    p = find_bin(name)
    if not p:
        raise RuntimeError(f"'{name}' was not found. {hint}")
    return p


def file_signature(path: str) -> str:
    st = os.stat(path)
    raw = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def cache_dir_for(path: str) -> str:
    d = os.path.join(CACHE_ROOT, file_signature(path))
    os.makedirs(d, exist_ok=True)
    meta = os.path.join(d, "source.txt")
    if not os.path.isfile(meta):
        with open(meta, "w", encoding="utf-8") as f:
            f.write(os.path.abspath(path))
    return d


def _parse_rate(s: str) -> float:
    try:
        fr = Fraction(s)
        return float(fr) if fr > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: str) -> dict:
    ffprobe = require_bin("ffprobe", "Install it with: brew install ffmpeg")
    res = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe could not read '{os.path.basename(path)}': {res.stderr.strip()[:300]}")
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    tc = (fmt.get("tags", {}) or {}).get("timecode")
    for s in streams:
        tc = tc or (s.get("tags", {}) or {}).get("timecode")
    fps = 0.0
    if video:
        fps = _parse_rate(video.get("r_frame_rate", "0")) or _parse_rate(video.get("avg_frame_rate", "0"))
    return {
        "path": os.path.abspath(path),
        "duration": float(fmt.get("duration") or 0.0),
        "fps": fps,
        "has_video": video is not None,
        "audio_streams": len(audios),
        "audio_channels": int(audios[0].get("channels", 0)) if audios else 0,
        "start_timecode": tc or "00:00:00:00",
    }


def extract_audio(path: str, out_wav: str, audio_stream: int = 0) -> str:
    """16 kHz mono PCM, all channels of the chosen stream mixed down."""
    if os.path.isfile(out_wav) and os.path.getsize(out_wav) > 1000:
        return out_wav
    ffmpeg = require_bin("ffmpeg", "Install it with: brew install ffmpeg")
    tmp = out_wav + ".part.wav"
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", path, "-map", f"0:a:{audio_stream}",
           "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tmp]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    if res.returncode != 0 or not os.path.isfile(tmp):
        if os.path.isfile(tmp):
            os.unlink(tmp)
        err = res.stderr.strip()
        if "matches no streams" in err or "does not contain any stream" in err:
            raise RuntimeError(f"'{os.path.basename(path)}' has no audio track to analyse.")
        raise RuntimeError(f"Audio extraction failed: {err[:400]}")
    os.replace(tmp, out_wav)
    return out_wav


def detect_frozen_video(path: str, duration: float, min_freeze: float = 1.0, progress=None) -> list:
    """
    Returns [(start, end), ...] where the picture does not change (screen
    recordings: nothing happening on screen). Decoded at 5 fps / 320 px so a
    one-hour 1080p recording takes about a minute on Apple Silicon.
    """
    ffmpeg = require_bin("ffmpeg", "Install it with: brew install ffmpeg")
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-hwaccel", "videotoolbox", "-i", path, "-an",
           "-vf", f"fps=5,scale=320:-2,freezedetect=n=0.002:d={min_freeze}", "-f", "null", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace")
    frozen, start = [], None
    for line in proc.stderr:
        if "freeze_start:" in line:
            start = float(line.rsplit("freeze_start:", 1)[1].strip().split()[0])
        elif "freeze_end:" in line and start is not None:
            end = float(line.rsplit("freeze_end:", 1)[1].strip().split()[0])
            frozen.append((start, end))
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
