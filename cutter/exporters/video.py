"""
The final cut as an MP4 file, made with ffmpeg. Every clip is cut frame-accurately
from its source (re-encoded, with a few milliseconds of sound fade so a cut never
clicks), the pieces are joined, and the sound is encoded once at the end.
Straight cuts only: A/B overlap and Voice Isolation are DaVinci Resolve features.
Brown clips are kept, like on a timeline.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

from ..media import require_program

FADE = 0.008  # seconds of sound fade at each cut
_encoder = None


def video_encoder(ffmpeg: str) -> list:
    """The best H.264 encoder this ffmpeg has."""
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
        raise RuntimeError("ffmpeg could not make the video: " + res.stderr.strip()[-400:])


def write(edit: dict, path: str, view: str = "original", progress=None) -> str:
    ffmpeg = require_program("ffmpeg")
    if not any(s["has_video"] for s in edit["sources"].values()):
        raise RuntimeError("This cut has no picture, so there is no video to make.")
    if not all(s["has_video"] for s in edit["sources"].values()):
        raise RuntimeError("Some clips have no picture. Use a DaVinci Resolve or XML export for this cut.")
    r = edit["rate"]
    rate = f"{r['den']}/{r['num']}"
    w, h = edit["width"] // 2 * 2, edit["height"] // 2 * 2
    picture = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={rate}"
    total = sum(c["rec_out"] - c["rec_in"] for c in edit["clips"]) or 1
    done = 0
    with tempfile.TemporaryDirectory(prefix="scriptcutter_mp4_") as tmp:
        pieces = []
        for i, c in enumerate(edit["clips"]):
            src = edit["sources"][c["source"]]
            start = c["src_in"] / src["fps"]
            frames = c["rec_out"] - c["rec_in"]  # exactly this many frames, and sound of the same length,
            length = frames * r["num"] / r["den"]  # so picture and sound never drift apart over many cuts
            piece = os.path.join(tmp, f"p{i:05d}.mkv")
            cmd = [ffmpeg, "-v", "error", "-y", "-ss", f"{start:.4f}", "-t", f"{length + 0.2:.4f}", "-i", src["path"]]
            if src["audio_channels"]:
                fade = min(FADE, length / 4)
                sound = [f"-af", f"afade=t=in:d={fade:.4f},afade=t=out:st={max(0.0, length - fade):.4f}:d={fade:.4f},"
                                 "aresample=48000", "-ac", "2", "-c:a", "pcm_s16le", "-map", "0:v:0", "-map", "0:a:0"]
            else:  # silent source: add silence so every piece has sound
                cmd += ["-f", "lavfi", "-t", f"{length:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
                sound = ["-c:a", "pcm_s16le", "-map", "0:v:0", "-map", "1:a:0"]
            _run(cmd + ["-vf", picture, *video_encoder(ffmpeg), "-pix_fmt", "yuv420p", *sound,
                        "-frames:v", str(frames), "-t", f"{length:.6f}", "-map_metadata", "-1", "-dn", piece])
            pieces.append(piece)
            done += c["rec_out"] - c["rec_in"]
            if progress:
                progress(0.95 * done / total)
        listing = os.path.join(tmp, "pieces.txt")
        with open(listing, "w", encoding="utf-8") as f:
            f.writelines("file '" + p.replace("'", "'\\''") + "'\n" for p in pieces)
        partial = path + ".part.mp4"
        _run([ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing, "-c:v", "copy",
              "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-write_tmcd", "0", partial])
        os.replace(partial, path)
    if progress:
        progress(1.0)
    return path
