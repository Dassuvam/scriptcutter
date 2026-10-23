"""
File exports: CMX3600 EDL (manual import fallback) and a JSON report.

EDL source timecodes are written in the SOURCE clip's frame rate and offset by
its embedded start timecode - v1 used the timeline rate and assumed 00:00:00:00.
"""
from __future__ import annotations

import json
import os


def tc_to_frames(tc: str, fps: float) -> int:
    base = int(round(fps))
    try:
        hh, mm, ss, ff = [int(x) for x in tc.replace(";", ":").split(":")]
    except ValueError:
        return 0
    return ((hh * 60 + mm) * 60 + ss) * base + ff


def frames_to_tc(frames: int, fps: float) -> str:
    base = int(round(fps)) or 25
    frames = max(0, int(frames))
    ff = frames % base
    s = frames // base
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}:{ff:02d}"


def export_edl(plan: list, out_path: str, title: str, tl_fps: float, start_tcs: dict = None) -> str:
    start_tcs = start_tcs or {}
    lines = [f"TITLE: {title[:60]}", "FCM: NON-DROP FRAME", ""]
    rec = int(round(tl_fps)) * 3600  # 01:00:00:00
    for i, c in enumerate(plan, start=1):
        fps = c["src_fps"]
        off = tc_to_frames(start_tcs.get(c["path"], "00:00:00:00"), fps)
        dur_tl = int(round((c["src_out"] - c["src_in"]) * round(tl_fps) / round(fps)))
        reel = "AX"
        lines.append(f"{i:03d}  {reel:<8} AA/V  C        "
                     f"{frames_to_tc(c['src_in'] + off, fps)} {frames_to_tc(c['src_out'] + off, fps)} "
                     f"{frames_to_tc(rec, tl_fps)} {frames_to_tc(rec + dur_tl, tl_fps)}")
        lines.append(f"* FROM CLIP NAME: {os.path.basename(c['path'])}")
        if c["status"] == "review":
            lines.append(f"* REVIEW: {'; '.join(c.get('reasons', []))[:200]}")
        lines.append("")
        rec += dur_tl
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path


def export_report(result: dict, analyses: dict, out_path: str) -> str:
    data = {
        "stats": result["stats"],
        "clips": [{k: v for k, v in c.items() if k != "media_pool_item"} for c in result["plan"]],
        "removed": result["removed"],
        "sources": [{k: v for k, v in a.items() if not k.startswith("_") and k not in ("regions",)}
                    for a in analyses.values()],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return out_path
