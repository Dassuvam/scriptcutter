"""
Script mode: the creator pastes the script they recorded from (Hindi, Hinglish
or English; mixed is fine). Every line is looked up in the recording, the last
good take of each line is kept and its other takes are cut. Speech that is not
in the script is cut when it is short (false starts, "let me do that again") or
when the normal retake analysis already cuts it (it was said again later), and
stays brown when it is a longer new stretch, which may be a good ad-lib.

Lines are compared with the spoken words in Hinglish letters (translit.py), so a
Devanagari script matches Hinglish speech and the other way round. An English
script for Hindi speech is compared with Whisper's English translation instead.

apply() replaces each file's retake decisions with script decisions and returns
a summary: how many lines were found, and which were not.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from difflib import SequenceMatcher

from .text import STOPWORDS, restart_cue, tokens
from .translit import to_hinglish

MATCH, NEAR, MISS = 2.0, 1.0, -1.0
SKIP_SCRIPT, SKIP_SPOKEN = -1.0, -0.5  # a script word not said / an extra spoken word inside a take
FULL_TAKE = 0.45       # share of a perfect match for a take of the line
PARTIAL_TAKE = 0.25    # weaker matches are unfinished takes (cut)
CLOSE = 0.12           # the last take wins unless an earlier one is this much better
WEAK = 0.6             # a kept take below this stays brown
AD_LIB = 6             # unscripted speech of this many words stays brown
EDGE = 0.12
SPLIT = re.compile(r"(?<=[.!?।॥])\s+|\n+")


def split_lines(script: str) -> list:
    out = []
    for part in SPLIT.split(script or ""):
        text = part.strip(" \t-•*")
        toks = tokens(to_hinglish(text))
        if toks and not all(t.isdigit() for t in toks):
            out.append({"n": len(out) + 1, "text": text, "toks": toks})
    return out


class _Similar:
    """Token similarity, cached: equal, close in spelling (vidio / video), or different."""

    def __init__(self):
        self.cache = {}

    def __call__(self, a: str, b: str) -> float:
        if a == b:
            return MATCH
        key = (a, b)
        if key not in self.cache:
            close = min(len(a), len(b)) >= 3 and SequenceMatcher(None, a, b).ratio() >= 0.75
            self.cache[key] = NEAR if close else MISS
        return self.cache[key]


def takes(line: list, stream: list, sim=None) -> list:
    """
    Every place the line was said: [(i0, i1, score)] over stream[i0:i1], score 0..1
    (1 = every word, in order, nothing extra). The whole line is aligned against any
    stretch of the stream (Smith-Waterman style, with the line matched end to end).
    """
    sim = sim or _Similar()
    m, n = len(line), len(stream)
    if not m or not n:
        return []
    prev, prev_start = [0.0] * (n + 1), list(range(n + 1))
    for a in range(1, m + 1):
        cur, cur_start = [prev[0] + SKIP_SCRIPT] + [0.0] * n, [0] * (n + 1)
        word = line[a - 1]
        for b in range(1, n + 1):
            best, st = prev[b - 1] + sim(word, stream[b - 1]), prev_start[b - 1]
            up = prev[b] + SKIP_SCRIPT
            if up > best:
                best, st = up, prev_start[b]
            left = cur[b - 1] + SKIP_SPOKEN
            if left > best:
                best, st = left, cur_start[b - 1]
            cur[b], cur_start[b] = best, st
        prev, prev_start = cur, cur_start
    top = MATCH * m
    found = []
    for b in range(1, n + 1):
        s = prev[b] / top
        if s >= PARTIAL_TAKE and s >= prev[b - 1] / top and (b == n or s > prev[b + 1] / top):
            found.append((prev_start[b], b, round(s, 3)))
    chosen = []
    for t in sorted(found, key=lambda t: -t[2]):  # best first, no overlaps
        if t[1] - t[0] >= 2 and all(t[1] <= c[0] or t[0] >= c[1] for c in chosen):
            chosen.append(t)
    return sorted(chosen)


def _streams(sources: list, analyses: dict) -> tuple:
    """Hinglish word tokens (timed) and English translation tokens (times spread over each phrase)."""
    words, english = [], []
    for src in sources:
        a = analyses[src["path"]]
        lo, hi = src.get("start_sec", 0.0), src.get("end_sec") or a["info"]["duration"]
        for w in a.get("_words") or []:
            if lo <= (w["start"] + w["end"]) / 2 < hi:
                for t in tokens(to_hinglish(w["w"])):
                    words.append({"tok": t, "path": src["path"], "t0": w["start"], "t1": max(w["end"], w["start"] + 0.2)})
        if a["translated"]:
            for p in a["phrases"]:
                toks = tokens(p["text"])
                if toks and lo <= (p["start"] + p["end"]) / 2 < hi:
                    step = (p["end"] - p["start"]) / len(toks)
                    english += [{"tok": t, "path": src["path"], "t0": p["start"] + i * step,
                                 "t1": p["start"] + (i + 1) * step} for i, t in enumerate(toks)]
    return words, english


def _intervals(lines: list, words: list, english: list) -> tuple:
    """Keep / cut time intervals from each line's takes; lines with no full take."""
    sim, out, missing, found = _Similar(), [], [], 0
    streams = [s for s in (words, english) if s]
    for line in lines:
        best_stream, best_takes = None, []
        for stream in streams:  # spoken words first; the translation must do clearly better
            tk = takes(line["toks"], [u["tok"] for u in stream], sim)
            if tk and (not best_takes or max(t[2] for t in tk) > max(t[2] for t in best_takes) + 0.05):
                best_stream, best_takes = stream, tk
        full = [t for t in best_takes if t[2] >= FULL_TAKE]
        if not full:
            missing.append({"n": line["n"], "text": line["text"]})
        else:
            found += 1
        top = max([t[2] for t in full] or [0])
        keep = next((t for t in reversed(full) if t[2] >= top - CLOSE), None)
        label = _label(line)
        for t in best_takes:
            u0, u1 = best_stream[t[0]], best_stream[t[1] - 1]
            if t is keep:
                weak = t[2] < WEAK
                out.append({"path": u0["path"], "t0": u0["t0"], "t1": u1["t1"], "keep": True, "score": t[2],
                            "confidence": 0.6 if weak else 0.0,
                            "reason": f"Unsure match to script {label} - check it" if weak else ""})
            else:
                kind = "Unfinished take" if t[2] < FULL_TAKE else ("Earlier take" if t[0] < keep[0] else "Weaker take")
                out.append({"path": u0["path"], "t0": u0["t0"], "t1": u1["t1"], "keep": False, "score": t[2],
                            "confidence": 0.92 if t[2] >= FULL_TAKE else 0.85,
                            "reason": f"{kind} of script {label} - keeping the best take"})
    return out, missing, found


def _started_line(toks: list, lines: list):
    """The script line a short scrap of speech starts ("sabse pehle camera" -> line 2), if any."""
    for line in lines:
        k = 0
        while k < len(toks) and k < len(line["toks"]) and toks[k] == line["toks"][k]:
            k += 1
        if k >= 2 and k >= 0.6 * len(toks):
            return line
    return None


def _label(line: dict) -> str:
    return f'line {line["n"]} ("{line["text"][:40]}{"…" if len(line["text"]) > 40 else ""}")'


def _retake_view(decisions: list, t: float) -> tuple:
    """What the normal retake analysis decided at time t: (confidence, reason) of the strongest item."""
    hits = [d for d in decisions if d["kind"] in ("phrase", "restart") and d["start"] <= t < d["end"]]
    best = max(hits, key=lambda d: d["confidence"], default=None)
    return (best["confidence"], best["reason"]) if best else (0.0, "")


def _label_words(path: str, a: dict, ranges: list, intervals: list, lines: list) -> list:
    """Each spoken word in range: [word, confidence, reason]."""
    mine = [iv for iv in intervals if iv["path"] == path]
    out = []
    for w in a.get("_words") or []:
        mid = (w["start"] + w["end"]) / 2
        if not any(lo <= mid < hi for lo, hi in ranges):
            continue
        keep = [iv for iv in mine if iv["keep"] and iv["t0"] - 0.05 <= mid <= iv["t1"] + 0.05]
        cut = [iv for iv in mine if not iv["keep"] and iv["t0"] - 0.05 <= mid <= iv["t1"] + 0.05]
        if keep:
            iv = max(keep, key=lambda x: x["score"])
            out.append([w, iv["confidence"], iv["reason"]])
        elif cut:
            iv = max(cut, key=lambda x: x["score"])
            out.append([w, iv["confidence"], iv["reason"]])
        else:
            out.append([w, None, ""])
    # speech that is in no take: what the retake analysis cuts (said again later) is cut; of the rest,
    # short bits are cut and longer new stretches may be a good ad-lib
    for x in out:
        if x[1] is None:
            x.append(_retake_view(a["decisions"], (x[0]["start"] + x[0]["end"]) / 2))
    i = 0
    while i < len(out):
        if out[i][1] is not None:
            i += 1
            continue
        retaken = out[i][3][0] >= 0.75
        j = i
        while j < len(out) and out[j][1] is None and (out[j][3][0] >= 0.75) == retaken:
            j += 1
        text = " ".join(x[0]["w"] for x in out[i:j])
        toks = tokens(to_hinglish(text))
        content = [t for t in toks if t not in STOPWORDS]
        started = _started_line(toks, lines)
        if started:
            conf, reason = 0.9, f"Unfinished take of script {_label(started)}"
        elif retaken:
            why = max((x[3] for x in out[i:j]), key=lambda v: v[0])[1]
            conf, reason = 0.9, f"Not in your script, and {why[0].lower()}{why[1:]}"
        elif len(toks) >= AD_LIB and len(content) >= 2 and not restart_cue(text):
            conf, reason = 0.6, "Not in your script - keep it if it is a good ad-lib"
        else:
            conf, reason = 0.9, "Not in your script"
        for x in out[i:j]:
            x[1], x[2] = conf, reason
            del x[3:]
        i = j
    return out


def _spans(labelled: list, ranges: list) -> list:
    """Runs of words with the same decision -> time spans; a cut takes the pause after it."""
    spans = []
    for k, (w, conf, reason) in enumerate(labelled):
        nxt = labelled[k + 1][0] if k + 1 < len(labelled) else None
        same_range = nxt is not None and any(lo <= w["start"] < hi and lo <= nxt["start"] < hi for lo, hi in ranges)
        end = nxt["start"] - EDGE if same_range and conf else max(w["end"], w["start"] + 0.2)
        if spans and spans[-1]["confidence"] == conf and spans[-1]["reason"] == reason and spans[-1]["joined"]:
            spans[-1].update(end=max(spans[-1]["end"], end), joined=same_range)
            spans[-1]["words"].append(w["w"])
        else:
            spans.append({"start": w["start"], "end": max(w["start"] + 0.05, end), "confidence": conf,
                          "reason": reason, "words": [w["w"]], "joined": same_range})
    return spans


def apply(sources: list, analyses: dict, script: str) -> dict:
    lines = split_lines(script)
    if not lines:
        return {}
    words, english = _streams(sources, analyses)
    intervals, missing, found = _intervals(lines, words, english)
    for path, a in analyses.items():
        ranges = [(s.get("start_sec", 0.0), s.get("end_sec") or a["info"]["duration"])
                  for s in sources if s["path"] == path]
        spans = _spans(_label_words(path, a, ranges, intervals, lines), ranges)
        starts = [s["start"] for s in spans]
        decisions = [d for d in a["decisions"] if d["kind"] in ("sound", "filler")]
        for p in a["phrases"]:
            mine = []  # spans that really lie in this phrase, not ones that only touch its edge
            for s in spans[max(0, bisect_right(starts, p["start"]) - 1):]:
                if s["start"] >= p["end"]:
                    break
                overlap = min(s["end"], p["end"]) - max(s["start"], p["start"])
                if overlap > min(0.15, 0.5 * (s["end"] - s["start"])):
                    mine.append(s)
            base = {"id": p["id"], "start": p["start"], "end": p["end"], "kind": "phrase", "text": p["text"],
                    "confidence": 0.0, "reason": "", "ref": None, "script": True}
            if len({(s["confidence"], s["reason"]) for s in mine}) == 1:
                base.update(confidence=mine[0]["confidence"], reason=mine[0]["reason"])
                decisions.append(base)
                continue
            decisions.append(base)
            for j, s in enumerate(mine):
                if s["confidence"]:
                    decisions.append({"id": f"{p['id']}s{j}", "start": max(p["start"], s["start"]),
                                      "end": min(p["end"], s["end"]), "kind": "script", "parent": p["id"],
                                      "text": " ".join(s["words"]), "confidence": s["confidence"],
                                      "reason": s["reason"], "ref": None, "script": True})
        a["decisions"] = decisions
    return {"lines": len(lines), "found": found, "missing": missing,
            "weak": sum(1 for iv in intervals if iv["keep"] and iv["confidence"])}
