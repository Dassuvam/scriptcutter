"""
CMX 3600 EDL: the oldest edit-list format, understood by nearly every editor.
One video track, no colours; a review (brown) clip gets a "* REVIEW:" comment.

Each event line reads: number, reel, tracks (AA/V = two audio + video), cut,
source in/out (in the source's own timecode, drop-frame where the camera used
it) and record in/out (on the new timeline, starting at 01:00:00:00).
"""
from __future__ import annotations

from ..edit import frames_to_tc


def write(edit: dict, path: str, view: str = "original") -> str:
    fps = edit["fps"]
    lines = [f"TITLE: {edit['name'][:60]}", "FCM: NON-DROP FRAME", ""]
    rec0 = int(round(fps)) * 3600  # the new timeline starts at 01:00:00:00
    for i, c in enumerate(edit["clips"], start=1):
        src = edit["sources"][c["source"]]
        off, df = src["start_frame"], src["drop_frame"]
        lines.append(f"{i:03d}  AX       AA/V  C        "
                     f"{frames_to_tc(c['src_in'] + off, src['fps'], df)} {frames_to_tc(c['src_out'] + off, src['fps'], df)} "
                     f"{frames_to_tc(rec0 + c['rec_in'], fps)} {frames_to_tc(rec0 + c['rec_out'], fps)}")
        lines.append(f"* FROM CLIP NAME: {src['name']}")
        if c["status"] == "review":
            lines.append(f"* REVIEW: {'; '.join(c['reasons'])[:200]}")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
