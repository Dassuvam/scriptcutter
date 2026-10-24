"""
The edit document: one plain-JSON description of the finished cut. Every output
reads it (Premiere / Final Cut XML, EDL, transcript, JSON) and later engine
steps extend it (captions, B-roll images, generated shots, music) by adding
their own lists next to "clips".

Times are frames: "rec" on the new timeline (at edit["rate"]), "src" in each
source (at that source's own rate). The transcript uses timeline seconds.
"""
from __future__ import annotations

import os
from bisect import bisect_left

from .translit import has_devanagari, to_hinglish

VERSION = 1
LINE_CHARS = 42      # subtitle line length
LINE_SECONDS = 5.0
LINE_GAP = 0.7       # a pause this long starts a new subtitle line


def rate(fps: float) -> dict:
    """Frame rate as editors store it: integer timebase + NTSC flag, frame duration num/den seconds."""
    fps = float(fps or 25.0)
    base = int(round(fps))
    if abs(fps - base) > 0.005 and abs(fps * 1.001 - round(fps * 1.001)) < 0.01:
        base = int(round(fps * 1.001))
        return {"fps": base / 1.001, "timebase": base, "ntsc": True, "num": 1001, "den": base * 1000}
    return {"fps": float(base), "timebase": base, "ntsc": False, "num": 1, "den": base}


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
    s = frames // base
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}:{frames % base:02d}"


def _lines(words: list, key: str) -> list:
    lines, cur = [], []

    def close():
        if cur:
            lines.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": " ".join(w[key] for w in cur)})
            cur.clear()

    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > LINE_GAP or w["end"] - cur[0]["start"] > LINE_SECONDS
                    or len(" ".join(x[key] for x in cur + [w])) > LINE_CHARS):
            close()
        cur.append(w)
        if w[key].endswith((".", "?", "!", "।")):
            close()
    close()
    for a, b in zip(lines, lines[1:]):  # hold short lines on screen a little longer
        a["end"] = round(min(max(a["end"], a["start"] + 1.0), b["start"]), 3)
    if lines:
        lines[-1]["end"] = round(max(lines[-1]["end"], lines[-1]["start"] + 1.0), 3)
    return lines


def transcript(clips: list, sources: dict, words_by_path: dict, fps: float) -> dict:
    """What is said in the final cut, on the timeline clock, in the spoken language (+ Hinglish)."""
    words, english = [], []
    starts = {p: [w["start"] for w in ws] for p, ws in words_by_path.items() if ws is not None}
    for i, c in enumerate(clips):
        src = sources[c["source"]]
        s0, s1 = c["src_in"] / src["fps"], c["src_out"] / src["fps"]
        t0 = c["rec_in"] / fps
        if src.get("translated") and c["text"]:
            english.append({"start": round(t0, 3), "end": round(c["rec_out"] / fps, 3), "text": c["text"]})
        found = words_by_path.get(src["path"])
        if found is None:  # no word timings: the clip's own text, as one line
            if c["text"]:
                words.append({"text": c["text"], "start": round(t0, 3), "end": round(c["rec_out"] / fps, 3), "clip": i})
            continue
        for w in found[max(0, bisect_left(starts[src["path"]], s0 - 5.0)):]:
            mid = (w["start"] + w["end"]) / 2
            if mid >= s1:
                break
            if mid >= s0:
                words.append({"text": w["w"], "start": round(t0 + max(0.0, w["start"] - s0), 3),
                              "end": round(t0 + min(s1, w["end"]) - s0, 3), "clip": i})
    deva = any(has_devanagari(w["text"]) for w in words)
    for w in words:
        w["hinglish"] = to_hinglish(w["text"]) if deva else w["text"]
    return {
        "script": "devanagari" if deva else "latin",
        "words": words,
        "lines": _lines(words, "text"),
        "hinglish": _lines(words, "hinglish") if deva else [],
        "english": english,
    }


def build(name: str, plan: list, analyses: dict, timeline_fps: float = 0.0, words_by_path: dict = None) -> dict:
    """plan: clips from pipeline.plan_for_sources(); analyses: path -> pipeline.analyze_file() result."""
    words_by_path = words_by_path or {}
    first = analyses[plan[0]["path"]]["info"] if plan else next(iter(analyses.values()))["info"]
    r = rate(timeline_fps or first["fps"] or 25.0)
    sources, ids = {}, {}
    for path, a in analyses.items():
        info = a["info"]
        sr = rate(info["fps"] or r["fps"])
        sid = ids[path] = f"s{len(ids) + 1}"
        sources[sid] = {
            "path": path, "name": os.path.basename(path), "rate": sr, "fps": sr["fps"],
            "frames": int(round(info["duration"] * sr["fps"])), "start_tc": info["start_timecode"],
            "start_frame": tc_to_frames(info["start_timecode"], sr["fps"]), "has_video": info["has_video"],
            "width": info.get("width") or 1920, "height": info.get("height") or 1080,
            "audio_channels": info.get("audio_channels", 2), "sample_rate": info.get("sample_rate", 48000),
            "language": a["language"], "translated": a["translated"],
        }
    clips, markers, rec = [], [], 0
    for c in plan:
        src = sources[ids[c["path"]]]
        dur = max(1, int(round((c["src_out"] - c["src_in"]) * r["fps"] / src["fps"])))
        clips.append({"source": ids[c["path"]], "src_in": c["src_in"], "src_out": c["src_out"],
                      "rec_in": rec, "rec_out": rec + dur, "status": c["status"],
                      "reasons": list(c.get("reasons", [])), "text": c.get("text", "")})
        if c["status"] == "review":
            markers.append({"frame": rec, "duration": dur, "name": "Check this",
                            "note": "; ".join(c.get("reasons", []))[:240], "color": "brown"})
        rec += dur
    main = sources[ids[plan[0]["path"]]] if plan else next(iter(sources.values()))
    return {
        "format": "scriptcutter.edit", "version": VERSION, "name": name, "rate": r, "fps": r["fps"],
        "width": main["width"], "height": main["height"], "sample_rate": main["sample_rate"],
        "audio_channels": max(1, min(2, max([s["audio_channels"] for s in sources.values()] or [2]))),
        "duration": rec, "sources": sources, "clips": clips, "markers": markers,
        "transcript": transcript(clips, sources, words_by_path, r["fps"]),
    }
