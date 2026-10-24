"""The final cut's transcript: subtitles (.srt), readable text (.txt) and the full edit data (.json)."""
from __future__ import annotations

import json


def lines(edit: dict, view: str = "original") -> list:
    t = edit["transcript"]
    if view == "hinglish" and t["hinglish"]:
        return t["hinglish"]
    if view == "english" and t["english"]:
        return t["english"]
    return t["lines"]


def plain_text(edit: dict, view: str = "original") -> str:
    """Paragraphs for copying into a description or a script; a long pause starts a new paragraph."""
    out, prev = [], None
    for ln in lines(edit, view):
        if prev is not None:
            out.append("\n\n" if ln["start"] - prev >= 1.2 else " ")
        out.append(ln["text"])
        prev = ln["end"]
    return "".join(out).strip()


def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(edit: dict, path: str, view: str = "original") -> str:
    blocks = [f"{i}\n{_srt_time(ln['start'])} --> {_srt_time(ln['end'])}\n{ln['text']}\n"
              for i, ln in enumerate(lines(edit, view), start=1)]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(blocks))
    return path


def write_txt(edit: dict, path: str, view: str = "original") -> str:
    seconds = edit["duration"] / edit["fps"]
    rows = [edit["name"], f"Final cut: {int(seconds // 60)}m {int(seconds % 60):02d}s", ""]
    rows += [f"[{int(ln['start'] // 60):02d}:{int(ln['start'] % 60):02d}] {ln['text']}" for ln in lines(edit, view)]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")
    return path


def write_json(edit: dict, path: str, view: str = "original") -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(edit, f, ensure_ascii=False, indent=1)
    return path
