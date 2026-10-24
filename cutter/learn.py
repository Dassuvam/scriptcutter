"""
Learns your taste from the Keep / Review / Cut choices you make in the review page.

When you create a timeline, every decision you changed is counted per kind of
flag ("said again", "inside a re-recorded block", "short noise", ...). After a
few examples the confidence for that kind is nudged: if you keep cutting
brown "block" items, they start getting cut automatically; if you keep
rescuing "short noise" items, they stop being flagged. Stored locally, no AI.
"""
from __future__ import annotations

import json
import os

from . import paths

PATH = None  # tests point this elsewhere; default is <app data>/feedback.json
MIN_EXAMPLES = 4
MAX_SHIFT = 0.2

CATEGORIES = [
    ("Said again", "retake"), ("Sits inside a section", "block"), ("After a failed take", "block"),
    ("Introduces a section", "block"), ("Restart cue", "cue"),
    ("Contains a restart cue", "cue"), ("Filler", "filler"), ("Very short isolated", "stray"),
    ("Sound with no recognisable", "sound"), ("Short noise", "noise"), ("Non-speech sound", "noise"),
    ("unreliable", "loop"), ("talking but only", "sparse"), ("repeated line", "repeat_line"),
    ("Started this line", "restart_inline"), ("Dropped attempt", "dropped"), ("AI reviewer", "ai"),
]
LABELS = {"retake": "repeated lines", "block": "re-recorded sections", "cue": "restart cues", "filler": "fillers",
          "stray": "tiny stray sounds", "sound": "wordless sounds", "noise": "noises", "loop": "garbled transcript",
          "sparse": "mumbled stretches", "repeat_line": "repeats inside a line", "restart_inline": "mid-line restarts",
          "dropped": "dropped attempts",
          "ai": "AI-only flags"}


def category(reason: str) -> str:
    for key, cat in CATEGORIES:
        if key.lower() in (reason or "").lower():
            return cat
    return ""


def _path() -> str:
    return PATH or os.path.join(paths.data_dir(), "feedback.json")


def load() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(data: dict):
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, _path())


def shifts(data: dict = None) -> dict:
    """category -> confidence shift (+ = cut more readily, - = flag less)."""
    data = load() if data is None else data
    out = {}
    for cat, c in data.items():
        n = c.get("kept", 0) + c.get("cut", 0)
        if n >= MIN_EXAMPLES:
            out[cat] = round(max(-MAX_SHIFT, min(MAX_SHIFT, (c.get("cut", 0) / n - 0.5) * 0.4)), 3)
    return out


def apply(decisions: list) -> dict:
    """Nudge confidences by what you taught it. Returns the shifts used."""
    sh = shifts()
    if not sh:
        return {}
    for d in decisions:
        cat = category(d.get("reason", ""))
        if cat in sh and d["confidence"] > 0:
            d["confidence"] = round(max(0.0, min(0.97, d["confidence"] + sh[cat])), 3)
            d["learned"] = sh[cat]
    return sh


def record(decisions: list, tool_action) -> int:
    """Count your overrides. `tool_action(d)` = what the tool would do without you."""
    data = load()
    n = 0
    for d in decisions:
        ua = d.get("user_action")
        if not ua:
            continue
        cat = category(d.get("reason", ""))
        if not cat:
            continue
        ta = tool_action(d)
        if ta == ua:
            continue
        c = data.setdefault(cat, {"kept": 0, "cut": 0})
        if ua == "keep":
            c["kept"] += 1
        elif ua == "remove":
            c["cut"] += 1
        else:
            continue
        n += 1
    if n:
        save(data)
    return n


def summary() -> list:
    data = load()
    sh = shifts(data)
    rows = []
    for cat, c in sorted(data.items()):
        rows.append({"category": cat, "label": LABELS.get(cat, cat), "kept": c.get("kept", 0),
                     "cut": c.get("cut", 0), "shift": sh.get(cat, 0.0)})
    return rows
