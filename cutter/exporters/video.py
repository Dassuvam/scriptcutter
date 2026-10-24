"""
The final cut as an MP4 file, made with ffmpeg.

  1. Every clip is cut from its source into its own piece, frame-accurately
     (re-encoded, not copied, because copying can only cut at keyframes).
     Each piece is exactly as many frames as the clip, with sound of exactly
     the same length, so picture and sound never drift apart over many cuts.
     The sound fades in and out over 8 ms so a cut never clicks.
  2. The pieces are joined (ffmpeg's concat demuxer, picture copied) and the
     sound is encoded to AAC once, so the joins are seamless.

Straight cuts only: A/B overlap and Voice Isolation are DaVinci Resolve
features. Brown clips are kept, like on a timeline. Phone videos recorded
upright come out upright: ffmpeg turns the picture while decoding, and the
edit's width/height are already the upright size (media.describe()).
"""
from __future__ import annotations

import os
import subprocess
import tempfile

from ..media import require_program

FADE = 0.008  # seconds of sound fade at each cut
_encoder = None  # the H.264 encoder found on this computer, looked up once


def video_encoder(ffmpeg: str) -> list:
    """Arguments for the best H.264 encoder this ffmpeg has (x264, else Apple's hardware one, else MPEG-4)."""
    global _encoder
    if _encoder is None:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True,
                             errors="replace").stdout
        if " libx264 " in out:
            _encoder = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
        elif "h264_videotoolbox" in out:
            _encoder = ["-c:v", "h264_videotoolbox", "-b:v", "12M"]
        else:
            _encoder = ["-c:v", "mpeg4", "-q:v", "3"]
    return _encoder


def _run(cmd: list):
    res = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if res.returncode != 0:
        if "No space left" in res.stderr:
            raise RuntimeError("The disk is full. Free some space and make the video again.")
        raise RuntimeError("ffmpeg could not make the video: " + res.stderr.strip()[-400:])


def write(edit: dict, path: str, view: str = "original", progress=None) -> str:
    ffmpeg = require_program("ffmpeg")
    if not edit["clips"]:
        raise RuntimeError("Nothing to put in the video: every part is cut. Keep some parts and try again.")
    if not all(s["has_video"] for s in edit["sources"].values()):
        raise RuntimeError("Some clips have no picture, so they can't go into an MP4. "
                           "Use DaVinci Resolve or an XML export for this cut.")
    r = edit["rate"]
    rate = f"{r['den']}/{r['num']}"  # e.g. 30000/1001
    w, h = edit["width"] // 2 * 2, edit["height"] // 2 * 2  # H.264 needs even sizes
    # fit every source into the cut's frame size and rate; tpad repeats the last picture if a
    # clip ends right at the end of its file, so every piece really has all its frames
    picture = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
               f"setsar=1,fps={rate},tpad=stop_mode=clone:stop_duration=1")
    total = sum(c["rec_out"] - c["rec_in"] for c in edit["clips"])
    done = 0
    partial = path + ".part.mp4"
    try:
        with tempfile.TemporaryDirectory(prefix="scriptcutter_mp4_") as tmp:
            pieces = []
            for i, c in enumerate(edit["clips"]):
                src = edit["sources"][c["source"]]
                start = c["src_in"] / src["fps"]
                frames = c["rec_out"] - c["rec_in"]
                length = frames * r["num"] / r["den"]
                piece = os.path.join(tmp, f"p{i:05d}.mkv")  # MKV holds plain PCM sound until the final encode
                cmd = [ffmpeg, "-v", "error", "-y", "-ss", f"{start:.4f}", "-t", f"{length + 0.2:.4f}", "-i", src["path"]]
                if src["audio_channels"]:
                    fade = min(FADE, length / 4)
                    sound = ["-af", f"afade=t=in:d={fade:.4f},afade=t=out:st={max(0.0, length - fade):.4f}:d={fade:.4f},"
                                    "aresample=48000,apad", "-ac", "2", "-c:a", "pcm_s16le", "-map", "0:v:0", "-map", "0:a:0"]
                else:  # a silent source: add silence, so every piece has a sound track to join
                    cmd += ["-f", "lavfi", "-t", f"{length:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
                    sound = ["-c:a", "pcm_s16le", "-map", "0:v:0", "-map", "1:a:0"]
                # -frames:v and -t make the piece exactly this long (apad / tpad fill a short ending)
                _run(cmd + ["-vf", picture, *video_encoder(ffmpeg), "-pix_fmt", "yuv420p", *sound,
                            "-frames:v", str(frames), "-t", f"{length:.6f}", "-map_metadata", "-1", "-dn", piece])
                pieces.append(piece)
                done += frames
                if progress:
                    progress(0.95 * done / total)
            listing = os.path.join(tmp, "pieces.txt")
            with open(listing, "w", encoding="utf-8") as f:
                # forward slashes and single quotes: safe for ffmpeg on Windows and with spaces in names
                f.writelines("file '" + p.replace("\\", "/").replace("'", "'\\''") + "'\n" for p in pieces)
            _run([ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing, "-c:v", "copy",
                  "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-write_tmcd", "0", partial])
        os.replace(partial, path)
    finally:
        if os.path.exists(partial):  # a failed run must not leave a half video behind
            os.unlink(partial)
    if progress:
        progress(1.0)
    return path
