"""
Turns decisions into the final clip list (source seconds), then into frames.

Rules that matter to an editor:
  * Removed takes are cut out completely - head/tail padding of the kept
    clips is fenced so it can never leak the first syllable of a dropped take.
  * Natural pauses up to `max_pause` stay; longer silences are cut down to
    head/tail padding.
  * A "review" (brown) phrase is always its own clip, so it can be coloured,
    marked and deleted with one click.
  * Cut points land on the quietest 30 ms near the boundary.
"""
from __future__ import annotations

import math

DEFAULTS = {
    "max_pause": 0.45,   # pauses shorter than this are kept as-is
    "head_pad": 0.10,
    "tail_pad": 0.16,
    "min_clip": 0.25,
}


def _subtract(interval: tuple, holes: list) -> list:
    a, b = interval
    out = []
    cur = a
    for h0, h1 in sorted(holes):
        if h1 <= cur or h0 >= b:
            continue
        if h0 > cur:
            out.append((cur, h0))
        cur = max(cur, h1)
    if cur < b:
        out.append((cur, b))
    return out


def build_clips(decisions: list, regions: list, env, settings: dict = None,
                source_range: tuple = None, frozen: list = None, screen_mode: bool = False) -> dict:
    s = dict(DEFAULTS)
    s.update(settings or {})
    lo, hi = source_range or (0.0, env.duration)

    units, removed = [], []

    # phrase-level items
    for d in decisions:
        if d["kind"] not in ("phrase", "sound"):
            continue
        if d["end"] <= lo or d["start"] >= hi:
            continue
        span = (max(lo, d["start"]), min(hi, d["end"]))
        if d["action"] == "remove":
            removed.append({"start": span[0], "end": span[1], "id": d["id"], "reason": d["reason"],
                            "text": d.get("text", ""), "confidence": d["confidence"]})
            continue
        pieces = [span]
        review_pieces = []
        for x in decisions:
            if x.get("parent") != d["id"] or x["action"] == "keep":
                continue
            if x["action"] == "remove":
                pieces = [p for piece in pieces for p in _subtract(piece, [(x["start"], x["end"])])]
                removed.append({"start": x["start"], "end": x["end"], "id": x["id"], "reason": x["reason"],
                                "text": x.get("text", ""), "confidence": x["confidence"]})
            else:
                review_pieces.append(x)
        for a, b in pieces:
            inner = [x for x in review_pieces if x["start"] < b and x["end"] > a]
            if not inner:
                units.append({"start": a, "end": b, "status": "review" if d["action"] == "review" else "keep",
                              "ids": [d["id"]], "reasons": [d["reason"]] if d["action"] == "review" else [],
                              "text": d.get("text", "")})
                continue
            cur = a
            for x in sorted(inner, key=lambda x: x["start"]):
                if x["start"] > cur:
                    units.append({"start": cur, "end": x["start"], "status": "keep", "ids": [d["id"]],
                                  "reasons": [], "text": d.get("text", "")})
                units.append({"start": max(cur, x["start"]), "end": min(b, x["end"]), "status": "review",
                              "ids": [x["id"]], "reasons": [x["reason"]], "text": x.get("text", "")})
                cur = min(b, x["end"])
            if cur < b:
                units.append({"start": cur, "end": b, "status": "keep", "ids": [d["id"]], "reasons": [],
                              "text": d.get("text", "")})

    units = [u for u in units if u["end"] - u["start"] > 0.04]
    units.sort(key=lambda u: u["start"])
    removed.sort(key=lambda r: r["start"])

    # grow unit edges to cover the speech region they sit in (keeps word tails
    # that Whisper's timestamps clipped), but never into a removed item
    fences = [(r["start"], r["end"]) for r in removed]
    for u in units:
        for a, b in regions:
            if a <= u["start"] + 0.02 < b:
                new = max(a, max([f1 for f0, f1 in fences if f1 <= u["start"] + 0.01] or [lo]), lo)
                u["start"] = min(u["start"], new)
            if a < u["end"] - 0.02 <= b:
                new = min(b, min([f0 for f0, f1 in fences if f0 >= u["end"] - 0.01] or [hi]), hi)
                u["end"] = max(u["end"], new)
    for i in range(1, len(units)):
        if units[i]["start"] < units[i - 1]["end"]:
            mid = env.quietest(units[i]["start"], units[i - 1]["end"])
            units[i - 1]["end"] = units[i]["start"] = mid

    # merge across short pauses
    clips = []
    for u in units:
        if clips:
            c = clips[-1]
            gap = u["start"] - c["end"]
            removed_between = any(r["start"] < u["start"] and r["end"] > c["end"] for r in removed)
            if gap <= s["max_pause"] and u["status"] == c["status"] and not removed_between:
                c["end"] = u["end"]
                c["ids"] += u["ids"]
                c["reasons"] += [r for r in u["reasons"] if r not in c["reasons"]]
                c["text"] = (c["text"] + " " + u["text"]).strip() if u["text"] not in c["text"] else c["text"]
                continue
        clips.append(dict(u, ids=list(u["ids"]), reasons=list(u["reasons"])))

    # silent stretches in screen recordings: keep them for review when the picture changes
    if screen_mode and frozen is not None:
        extra = []
        for a, b in zip(clips, clips[1:]):
            g0, g1 = a["end"] + s["tail_pad"], b["start"] - s["head_pad"]
            if g1 - g0 < 1.0 or any(r["start"] < g1 and r["end"] > g0 for r in removed):
                continue
            frozen_sec = sum(max(0.0, min(g1, f1) - max(g0, f0)) for f0, f1 in frozen)
            moving = (g1 - g0) - frozen_sec
            if moving >= 0.6:
                extra.append({"start": g0, "end": g1, "status": "review", "ids": [], "text": "",
                              "reasons": [f"No talking for {g1 - g0:.1f}s, but the screen changes - keep, trim or speed up"],
                              "kind": "visual"})
        clips = sorted(clips + extra, key=lambda c: c["start"])

    # padding, fenced by neighbours and removed takes
    occupied = sorted([(c["start"], c["end"]) for c in clips] + fences)
    for i, c in enumerate(clips):
        if c.get("kind") == "visual":
            continue
        before = max([e for st, e in occupied if e <= c["start"] + 1e-6 and (st, e) != (c["start"], c["end"])] or [lo])
        after = min([st for st, e in occupied if st >= c["end"] - 1e-6 and (st, e) != (c["start"], c["end"])] or [hi])
        room_l, room_r = c["start"] - before, after - c["end"]
        pad_l = min(s["head_pad"], room_l / 2.0 if room_l < 2 * s["head_pad"] else s["head_pad"])
        pad_r = min(s["tail_pad"], room_r / 2.0 if room_r < 2 * s["tail_pad"] else s["tail_pad"])
        c["start"] = max(lo, c["start"] - max(0.0, pad_l))
        c["end"] = min(hi, c["end"] + max(0.0, pad_r))
        # move the cut to the quietest point within the padding window
        if pad_l > 0.03:
            c["start"] = env.quietest(c["start"], c["start"] + pad_l * 0.8)
        if pad_r > 0.03:
            c["end"] = env.quietest(c["end"] - pad_r * 0.8, c["end"])

    clips = [c for c in clips if c["end"] - c["start"] >= s["min_clip"] or c["status"] == "review"]
    for c in clips:
        c["start"], c["end"] = round(c["start"], 3), round(c["end"], 3)

    kept = sum(c["end"] - c["start"] for c in clips)
    return {
        "clips": clips,
        "removed": removed,
        "stats": {
            "source_seconds": round(hi - lo, 2),
            "kept_seconds": round(kept, 2),
            "review_seconds": round(sum(c["end"] - c["start"] for c in clips if c["status"] == "review"), 2),
            "removed_takes": sum(1 for r in removed if r["id"].startswith("p")),
            "review_clips": sum(1 for c in clips if c["status"] == "review"),
            "clips": len(clips),
        },
    }


def to_frames(clips: list, fps: float, src_frame_offset: int = 0, min_frames: int = 3) -> list:
    """Seconds -> source frames (end exclusive). Adjacent clips never overlap in source."""
    out = []
    for c in clips:
        f_in = int(math.floor(c["start"] * fps + 0.5)) + src_frame_offset
        f_out = int(math.floor(c["end"] * fps + 0.5)) + src_frame_offset
        if out and f_in < out[-1]["src_out"] and out[-1]["src_in"] < f_in:
            f_in = out[-1]["src_out"]
        if f_out - f_in < min_frames:
            if c["status"] != "review":
                continue
            f_out = f_in + min_frames
        out.append(dict(c, src_in=f_in, src_out=f_out))
    return out


def layout(frame_clips: list, src_fps: float, tl_fps: float, overlap_frames: int = 0,
           checkerboard: bool = False) -> list:
    """
    Record positions on the new timeline (timeline frames, relative to timeline start).
    With checkerboard, clips alternate V1/V2 (A1/A2) and each overlaps the
    previous one by `overlap_frames`.
    """
    rec = 0
    out = []
    for i, c in enumerate(frame_clips):
        dur_tl = int(round((c["src_out"] - c["src_in"]) * tl_fps / src_fps))
        ov = min(overlap_frames, max(0, dur_tl // 2)) if checkerboard and i > 0 else 0
        start = max(0, rec - ov)
        out.append(dict(c, rec_in=start, rec_out=start + dur_tl, track=(i % 2) + 1 if checkerboard else 1))
        rec = start + dur_tl
    return out
