"""
Script mode: the creator pastes the script they recorded from (Hindi, Hinglish
or English; mixed is fine). Every line is looked up in the recording, the last
good take of each line is kept and its other takes are cut. Speech that is not
in the script is cut when it is short (false starts, "let me do that again") or
when the normal retake analysis already cuts it (it was said again later), and
stays brown when it is a longer new stretch, which may be a good ad-lib.

Lines are compared with the spoken words in Hinglish letters (translit.py), so a
Devanagari script matches Hinglish speech and the other way round, and by sound
(sound_key), so Whisper's spellings match: nekst / next, stoori / story,
accha / achha. An English script for Hindi speech is compared with Whisper's
English translation instead. Creators rarely read word for word, so a take is
judged by how much of the line it covers, and anything unclear stays brown:
script mode never cuts a later take, only earlier and unfinished ones.

apply() replaces each file's retake decisions with script decisions and returns
a summary: how many lines were found, and which were not.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from difflib import SequenceMatcher

from .text import STOPWORDS, restart_cue, tokens
from .translit import to_hinglish

MATCH, SOUND, NEAR, MISS = 2.0, 1.8, 1.0, -1.5  # a wrong word costs more than skipping one
SKIP_SCRIPT, SKIP_SPOKEN = -1.0, -1.0  # a script word not said / an extra spoken word inside a take
FULL_TAKE = 0.45       # share of the line's words said: a take of the line
PARTIAL_TAKE = 0.3     # less: an unfinished take
LAST_WINS = 0.75       # the last full take is kept unless it covers less than this share of the best one
RUN_GAP = 0.5          # words closer than this belong to the same attempt
EDGE = 0.12
KEY_RULES = [("x", "ks"), ("chh", "c"), ("ch", "c"), ("kh", "k"), ("gh", "g"), ("th", "t"), ("dh", "d"),
             ("bh", "b"), ("ph", "f"), ("sh", "s"), ("w", "v"), ("z", "j"), ("q", "k"), ("ck", "k")]
SPLIT = re.compile(r"(?<=[.!?।॥])\s+|\n+")


def split_lines(script: str) -> list:
    out = []
    for part in SPLIT.split(script or ""):
        text = part.strip(" \t-•*")
        toks = tokens(to_hinglish(text))
        if toks and not all(t.isdigit() for t in toks):
            out.append({"n": len(out) + 1, "text": text, "toks": toks})
    return out


def sound_key(tok: str) -> str:
    """How a Hinglish word sounds, roughly: consonants without doubling, vowels dropped after the first letter."""
    t = tok.lower()
    for a, b in KEY_RULES:
        t = t.replace(a, b)
    t = re.sub(r"(.)\1+", r"\1", t)
    return t[:1] + re.sub(r"[aeiouy]", "", t[1:])


class _Similar:
    """Token similarity, cached: equal, same sound (nekst / next), close (vidio / videos), or different."""

    def __init__(self):
        self.cache, self.keys = {}, {}

    def key(self, t: str) -> str:
        if t not in self.keys:
            self.keys[t] = sound_key(t)
        return self.keys[t]

    def __call__(self, a: str, b: str) -> float:
        if a == b:
            return MATCH
        pair = (a, b)
        if pair not in self.cache:
            ka, kb = self.key(a), self.key(b)
            if len(ka) >= 2 and ka == kb:
                self.cache[pair] = SOUND
            elif min(len(ka), len(kb)) >= 3 and SequenceMatcher(None, ka, kb).ratio() >= 0.75:
                self.cache[pair] = NEAR
            else:
                self.cache[pair] = MISS
        return self.cache[pair]


def _best_take(line: list, stream: list, sim) -> tuple:
    """The strongest place the line was said: (i0, i1, coverage) or None. Masked words (None) can't be used."""
    m, n = len(line), len(stream)
    prev, prev_start, prev_hit = [0.0] * (n + 1), list(range(n + 1)), [0.0] * (n + 1)
    for a in range(1, m + 1):
        cur, cur_start, cur_hit = [prev[0] + SKIP_SCRIPT] + [0.0] * n, [0] * (n + 1), [0.0] * (n + 1)
        word = line[a - 1]
        for b in range(1, n + 1):
            spoken = stream[b - 1]
            s = sim(word, spoken) if spoken is not None else -10.0
            best, st, hit = prev[b - 1] + s, prev_start[b - 1], prev_hit[b - 1] + (1.0 if s >= SOUND else 0.5 if s > 0 else 0)
            up = prev[b] + SKIP_SCRIPT
            if up > best:
                best, st, hit = up, prev_start[b], prev_hit[b]
            left = cur[b - 1] + (SKIP_SPOKEN if spoken is not None else -10.0)
            if left > best:
                best, st, hit = left, cur_start[b - 1], cur_hit[b - 1]
            cur[b], cur_start[b], cur_hit[b] = best, st, hit
        prev, prev_start, prev_hit = cur, cur_start, cur_hit
    b = max(range(1, n + 1), key=lambda b: (prev[b], -b))  # the strongest end; the earliest on a tie
    if prev[b] <= 0 or prev_hit[b] < 2 or prev_hit[b] / m < PARTIAL_TAKE or b - prev_start[b] < 2:
        return None
    return prev_start[b], b, round(prev_hit[b] / m, 3)


def takes(line: list, stream: list, sim=None) -> list:
    """
    Every place the line was said: [(i0, i1, coverage)] over stream[i0:i1]; coverage is the
    share of the line's words found there, in order (1 = all of them). The whole line is
    aligned against any stretch of the stream (Smith-Waterman style, line matched end to end);
    the strongest take is found, blanked out, and the search repeats (Waterman-Eggert).
    """
    sim = sim or _Similar()
    if not line or not stream:
        return []
    stream, found = list(stream), []
    for _ in range(12):
        t = _best_take(line, stream, sim)
        if not t:
            break
        found.append(t)
        stream[t[0]:t[1]] = [None] * (t[1] - t[0])
    return sorted(found)


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
        keep = next((t for t in reversed(full) if t[2] >= LAST_WINS * top), None)
        label = _label(line)
        for t in best_takes:
            u0, u1 = best_stream[t[0]], best_stream[t[1] - 1]
            iv = {"path": u0["path"], "t0": u0["t0"], "t1": u1["t1"], "score": t[2]}
            if t is keep:  # said in your own words is fine: keeping is never the risky choice
                iv.update(keep=True, confidence=0.0, reason="")
            elif keep is not None and t[0] < keep[0]:
                kind = "Earlier take" if t[2] >= FULL_TAKE else "Unfinished take"
                iv.update(keep=False, confidence=0.92 if t[2] >= FULL_TAKE else 0.85,
                          reason=f"{kind} of script {label} - keeping the later take")
            else:  # after the kept take, or a line never said in full: never cut on the script alone
                iv.update(keep=False, confidence=0.6,
                          reason=f"Another take of script {label} - check which one you want")
            out.append(iv)
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
    def joined(w1, w2):
        return w2["start"] - max(w1["end"], w1["start"] + 0.2) < RUN_GAP

    for k in [k for k in range(len(out)) if out[k][1] is not None and out[k][1] < 0.75]:  # takes as found, once
        for step in (-1, 1):
            n, j = 0, k + step
            while 0 <= j < len(out) and out[j][1] is None and n < 4 and \
                    joined(*((out[j][0], out[j - step][0]) if step < 0 else (out[j - step][0], out[j][0]))):
                out[j][1], out[j][2] = out[k][1], out[k][2]
                n, j = n + 1, j + step

    # a false start inside a kept take is still cut ("content creation video editing ya AI |
    # content creation video editing AI for creators"): the word-level restarts of the analysis
    restarts_found = [d for d in a["decisions"] if d["kind"] == "restart" and d["confidence"] >= 0.75]
    for x in out:
        if x[1] == 0.0:
            mid = (x[0]["start"] + x[0]["end"]) / 2
            hit = next((d for d in restarts_found if d["start"] <= mid < d["end"]), None)
            if hit:
                x[1], x[2] = hit["confidence"], hit["reason"]

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
        elif restart_cue(text) or not content:
            conf, reason = 0.9, "Not in your script"
        else:  # when unsure, keep it brown
            conf, reason = 0.6, "Not in your script - keep it if it is a good ad-lib"
        for x in out[i:j]:
            x[1], x[2] = conf, reason
            del x[3:]
        i = j
    return out


def _spans(labelled: list, ranges: list) -> list:
    """
    Runs of words with the same decision -> time spans. Spans of one breath touch (no gap
    for a word to fall into); across a pause, a cut takes the pause with it.
    """
    spans = []
    for k, (w, conf, reason) in enumerate(labelled):
        nxt = labelled[k + 1][0] if k + 1 < len(labelled) else None
        same_range = nxt is not None and any(lo <= w["start"] < hi and lo <= nxt["start"] < hi for lo, hi in ranges)
        if same_range and nxt["start"] - max(w["end"], w["start"] + 0.2) < RUN_GAP:
            end = nxt["start"]
        elif same_range and conf >= 0.75:
            end = nxt["start"] - EDGE
        else:
            end = max(w["end"], w["start"] + 0.2)
        if spans and spans[-1]["confidence"] == conf and spans[-1]["reason"] == reason and spans[-1]["joined"]:
            spans[-1].update(end=max(spans[-1]["end"], end), joined=same_range)
            spans[-1]["words"].append(w["w"])
        else:
            # right after a cut that took the pause, start where the cut ends: no sliver between them
            start = spans[-1]["end"] if spans and spans[-1]["joined"] and spans[-1]["confidence"] >= 0.75 \
                and spans[-1]["end"] < w["start"] else w["start"]
            spans.append({"start": start, "end": max(w["start"] + 0.05, end), "confidence": conf,
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
    return {"lines": len(lines), "found": found, "missing": missing}
