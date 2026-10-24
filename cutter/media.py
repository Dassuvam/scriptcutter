"""
Everything ScriptCutter needs from ffmpeg / ffprobe:

  probe()               what is in a file: length, frame rate, picture size, sound, timecode
  extract_audio()       16 kHz mono WAV of the voice, which every later step reads
  detect_frozen_video() where the picture stands still (screen recordings)
  cache_dir_for()       one cache folder per source file

ffprobe's answer is turned into ScriptCutter's own small dict in describe(), a
pure function, so the tricky cases (phone videos recorded sideways, odd frame
rates, missing durations) can be unit-tested without real files.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from fractions import Fraction

from . import paths

INSTALL_HINT = "Run the ScriptCutter installer again."


def require_program(name: str) -> str:
    """Absolute path of ffmpeg / ffprobe, or a clear error telling the user how to fix it."""
    p = paths.find_program(name)
    if not p:
        raise RuntimeError(f"'{name}' was not found. {INSTALL_HINT}")
    return p


def cache_dir_for(path: str) -> str:
    """
    One cache folder per source file. The key includes size and modification time,
    so a re-exported file with the same name never reuses an old transcript.
    """
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8")).hexdigest()[:16]
    d = os.path.join(paths.cache_dir(), key)
    os.makedirs(d, exist_ok=True)
    return d


# --------------------------------------------------------------------------- probe
def _rate(text: str) -> float:
    """'30000/1001' -> 29.97; anything unreadable -> 0."""
    try:
        value = Fraction(text)
        return float(value) if value > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _frame_rate(video: dict) -> float:
    """
    The frame rate editors use for this clip. ffprobe gives two numbers:
    r_frame_rate (the nominal rate) and avg_frame_rate (frames / duration).
    Phones record with a variable rate and sometimes report a nonsense nominal
    rate (90000/1), so a nominal rate outside 1..240 fps falls back to the average.
    """
    nominal, average = _rate(video.get("r_frame_rate", "0")), _rate(video.get("avg_frame_rate", "0"))
    if 1.0 <= nominal <= 240.0:
        return nominal
    if 1.0 <= average <= 240.0:
        return average
    return 25.0


def _rotation(video: dict) -> int:
    """Degrees a player turns the picture (phone videos recorded upright are stored sideways)."""
    for item in video.get("side_data_list") or []:  # ffmpeg 5+
        if "rotation" in item:
            return int(round(float(item["rotation"]))) % 360
    try:  # older ffmpeg kept it in a tag
        return int((video.get("tags") or {}).get("rotate", 0)) % 360
    except ValueError:
        return 0


def describe(data: dict, path: str) -> dict:
    """ffprobe's JSON -> the facts ScriptCutter uses. Width and height are as the viewer sees them."""
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    # cover art inside an audio file is a "video" stream too; it is not a picture we can cut
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    timecode = (fmt.get("tags") or {}).get("timecode")
    for s in streams:  # .mov files keep it on a timecode track, .mp4 on the video stream
        timecode = timecode or (s.get("tags") or {}).get("timecode")
    # some files (raw streams, broken headers) have no container duration: use the longest stream
    duration = float(fmt.get("duration") or 0.0) or max([float(s.get("duration") or 0.0) for s in streams] or [0.0])
    width = int(video.get("width") or 0) if video else 0
    height = int(video.get("height") or 0) if video else 0
    if video and _rotation(video) in (90, 270):
        width, height = height, width
    return {
        "path": os.path.abspath(path),
        "duration": duration,
        "fps": _frame_rate(video) if video else 0.0,
        "has_video": video is not None,
        "audio_streams": len(audios),
        "start_timecode": timecode or "00:00:00:00",  # ';' before the frames means drop-frame timecode
        "width": width,
        "height": height,
        # only the first audio stream is analysed (cameras and OBS put the main mix there)
        "audio_channels": int(audios[0].get("channels") or 2) if audios else 0,
        "sample_rate": int(audios[0].get("sample_rate") or 48000) if audios else 48000,
    }


def probe(path: str) -> dict:
    """Run ffprobe on a file and describe() it. Raises a readable error for files ffmpeg can't open."""
    res = subprocess.run(
        [require_program("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"Could not read '{os.path.basename(path)}': {res.stderr.strip()[:300]}")
    return describe(json.loads(res.stdout or "{}"), path)


# --------------------------------------------------------------------------- audio
def extract_audio(path: str, out_wav: str, audio_stream: int = 0) -> str:
    """
    16 kHz mono 16-bit WAV of one audio stream (its channels mixed down): the
    format Whisper and the loudness envelope expect. Written to a .part file
    first, so an interrupted run never leaves a half file that looks finished.
    """
    if os.path.isfile(out_wav) and os.path.getsize(out_wav) > 1000:
        return out_wav  # cached
    tmp = out_wav + ".part.wav"
    res = subprocess.run(
        [require_program("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", path, "-map", f"0:a:{audio_stream}",
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tmp],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    if res.returncode != 0 or not os.path.isfile(tmp):
        if os.path.isfile(tmp):
            os.unlink(tmp)
        if "No space left" in res.stderr:
            raise RuntimeError("The disk is full. Free some space and try again.")
        raise RuntimeError(f"Could not read the sound of '{os.path.basename(path)}': {res.stderr.strip()[:400]}")
    os.replace(tmp, out_wav)
    return out_wav


# --------------------------------------------------------------------------- picture
def detect_frozen_video(path: str, duration: float, min_freeze: float = 1.0, progress=None) -> list:
    """
    [(start, end), ...] where the picture does not change. Used for screen
    recordings, to tell "silent, but something happens on screen" apart from
    dead time. Decoded at 5 fps and 320 px wide to stay fast.
    """
    cmd = [require_program("ffmpeg"), "-nostdin", "-hide_banner", "-hwaccel", "auto", "-i", path, "-an",
           "-vf", f"fps=5,scale=320:-2,freezedetect=n=0.002:d={min_freeze}", "-f", "null", "-"]
    # text mode reads ffmpeg's carriage-return progress lines as separate lines
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
                pass  # "time=N/A" at the very start
    proc.wait()
    if start is not None:  # still frozen at the end of the file
        frozen.append((start, duration))
    return frozen
