"""
Script mode: the creator pastes the script they recorded from (Hindi, Hinglish
or English; mixed is fine). Every line is looked up in the recording, the last
good take of each line is kept and its earlier takes are cut.

How a line is found
  The line and the spoken words are compared as Hinglish tokens (translit.py),
  so a Devanagari script matches Hinglish speech and the other way round, and
  by sound (sound_key), so Whisper's spellings match: nekst / next, stoori /
  story, accha / achha. A whole-line alignment (takes()) finds every stretch of
  speech where most of the line's words were said in order; creators rarely
  read word for word, so a take is judged by how much of the line it covers.
  An English script for Hindi speech is compared with Whisper's English
  translation instead.

What happens to each piece of speech
  * the last take that is about as complete as the best one: kept
  * earlier and unfinished takes: cut ("Earlier take of script line 4")
  * a later, weaker take, or a take far earlier (> 5 min): brown, the creator decides
  * speech in no take: cut when the normal retake analysis cuts it (said again
    later) or when it is a scrap; a longer new stretch stays brown (an ad-lib?)
  * a false start inside a kept take: still cut (word-level restarts, restarts.py)

Safety: script mode never cuts a later take, never cuts on a line that is too
short to be sure of (< 4 words) or appears twice in the script, and leaves a
file alone when its words could not be read.

apply() replaces each file's retake decisions with script decisions and returns
a summary for the page: how many lines were found, and which were not.
"""
from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from difflib import SequenceMatcher

from .text import STOPWORDS, restart_cue, tokens
from .translit import to_hinglish

# alignment scores: a wrong word costs more than skipping one, so a take ends on matching words
MATCH, SOUND, NEAR, MISS = 2.0, 1.8, 1.0, -1.5
SKIP_SCRIPT, SKIP_SPOKEN = -1.0, -1.0  # a script word not said / an extra spoken word inside a take
FULL_TAKE = 0.45     # share of the line's words said, in order: a take of the line
PARTIAL_TAKE = 0.3   # less than a full take: an unfinished take
LAST_WINS = 0.75     # the last full take is kept unless it covers less than this share of the best one
MIN_LINE = 4         # shorter lines match too easily to cut anything on their own
LONG_AGO = 300.0     # an "earlier take" this many seconds before the kept one may be on purpose: brown
ANCHOR_MAX = 60      # a word said more often than this is too common to anchor a search
RUN_GAP = 0.5        # words closer than this belong to the same breath
EDGE = 0.12          # a cut ends this long before the next kept word
CUT, BROWN = 0.92, 0.6
# words that carry no meaning of their own: English ones from text.py plus everyday Hindi / Hinglish
# ones. A take must contain most of a line's other ("meaningful") words, not just these.
SMALL_WORDS = STOPWORDS | set("""
agar aapko aapka aapki aapke mujhe mera meri mere tera teri tere uska uski uske iska iski iske unka unki unke
isko usko inko unko jo jab tab kab kya kaise kyun kyunki lekin par pe tak liye saath wala wali wale kar karo
karna karte karti karta kiya kiye ho hoga hogi honge raha rahi rahe tha thi the gaya gayi gaye sakte sakta
sakti chahiye ek koi kuch sab bahut abhi ab phir fir isi usi yahan wahan aise waise jaise
""".split())
KEY_RULES = [("x", "ks"), ("chh", "c"), ("ch", "c"), ("kh", "k"), ("gh", "g"), ("th", "t"), ("dh", "d"),
             ("bh", "b"), ("ph", "f"), ("sh", "s"), ("w", "v"), ("z", "j"), ("q", "k"), ("ck", "k")]
SPLIT = re.compile(r"(?<=[.!?।॥])\s+|\n+")  # a script line ends at a sentence mark or a line break


# --------------------------------------------------------------------------- the script
def split_lines(script: str) -> list:
    """The script as numbered lines with their Hinglish tokens. Bullets and bare numbers are dropped."""
    out = []
    for part in SPLIT.split(script or ""):
        text = part.strip(" \t-•*")
        toks = tokens(to_hinglish(text))
        if toks and not all(t.isdigit() for t in toks):
            out.append({"n": len(out) + 1, "text": text, "toks": toks})
    return out


def _mark_repeats(lines: list):
    """Lines the script says twice (or nearly): their takes can't be told apart, so they are never cut."""
    for i, a in enumerate(lines):
        a["repeated"] = any(SequenceMatcher(None, a["toks"], b["toks"]).ratio() >= 0.85
                            for j, b in enumerate(lines) if j != i)


def _label(line: dict) -> str:
    return f'line {line["n"]} ("{line["text"][:40]}{"…" if len(line["text"]) > 40 else ""}")'


# --------------------------------------------------------------------------- comparing words
def sound_key(tok: str) -> str:
    """How a Hinglish word sounds, roughly: consonant groups simplified, no doubling, vowels dropped after the first letter."""
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


# --------------------------------------------------------------------------- finding takes
def _best_take(line: list, stream: list, sim) -> tuple:
    """
    The strongest place the whole line was said in stream: (i0, i1, coverage) or None.

    Dynamic programming over (line word a, spoken word b): every cell holds the best
    score of an alignment of line[:a] that ends at spoken word b, where it started,
    and how many of the line's words (and of its meaningful words) it matched. The
    line must be aligned end to end; the spoken stretch can start and end anywhere
    (row 0 is free). Masked words (None) belong to a take found earlier.

    coverage is the smaller of "share of all words" and "share of meaningful words"
    (not SMALL_WORDS): small words (hai, ko, aapko, to) are everywhere in Hinglish, so
    a different sentence can share half of them; it can't share the meaningful ones.
    """
    m, n = len(line), len(stream)
    meaningful = [t not in SMALL_WORDS for t in line]
    m_meaningful = sum(meaningful)
    prev, prev_start = [0.0] * (n + 1), list(range(n + 1))
    prev_hit, prev_mhit = [0.0] * (n + 1), [0.0] * (n + 1)
    for a in range(1, m + 1):
        cur, cur_start = [prev[0] + SKIP_SCRIPT] + [0.0] * n, [0] * (n + 1)
        cur_hit, cur_mhit = [0.0] * (n + 1), [0.0] * (n + 1)
        word, counts = line[a - 1], meaningful[a - 1]
        for b in range(1, n + 1):
            spoken = stream[b - 1]
            s = sim(word, spoken) if spoken is not None else -10.0
            # match / mismatch: line word a against spoken word b
            gain = 1.0 if s >= SOUND else 0.5 if s > 0 else 0.0
            best, st = prev[b - 1] + s, prev_start[b - 1]
            hit, mhit = prev_hit[b - 1] + gain, prev_mhit[b - 1] + (gain if counts else 0.0)
            up = prev[b] + SKIP_SCRIPT  # line word a was not said
            if up > best:
                best, st, hit, mhit = up, prev_start[b], prev_hit[b], prev_mhit[b]
            # spoken word b is extra; not in the last row, where the end of the take is free anyway
            left = cur[b - 1] + (SKIP_SPOKEN if spoken is not None else -10.0)
            if a < m and left > best:
                best, st, hit, mhit = left, cur_start[b - 1], cur_hit[b - 1], cur_mhit[b - 1]
            cur[b], cur_start[b], cur_hit[b], cur_mhit[b] = best, st, hit, mhit
        prev, prev_start, prev_hit, prev_mhit = cur, cur_start, cur_hit, cur_mhit
    b = max(range(1, n + 1), key=lambda b: (prev[b], -b))  # the strongest end; the earliest on a tie
    coverage = prev_hit[b] / m
    if m_meaningful >= 2:
        coverage = min(coverage, prev_mhit[b] / m_meaningful)
    if prev[b] <= 0 or prev_hit[b] < 2 or coverage < PARTIAL_TAKE or b - prev_start[b] < 2:
        return None
    return prev_start[b], b, round(coverage, 3)


def takes(line: list, stream: list, sim=None) -> list:
    """
    Every place the line was said: [(i0, i1, coverage)] over stream[i0:i1], sorted by
    time; coverage is the share of the line's words found there, in order (1 = all).
    The strongest take is found, blanked out, and the search repeats (Waterman-Eggert).
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


def _windows(line: list, index: dict, n: int, sim) -> list:
    """
    Stretches of the stream worth aligning the line against: around every place where
    one of its anchor words was said. Anchors are the two rarest meaningful words of
    the line (by sound), plus the rarest one of each third of the line, so even a
    take that stopped halfway contains an anchor. A word said more than ANCHOR_MAX
    times ("video" in a video about videos) is no use as an extra anchor. Keeps long
    videos fast: a line is compared with a few hundred words instead of all of them.
    """
    def count(key: str) -> int:
        return len(index.get(key, ()))

    def rarest(part: list, k: int) -> list:
        return sorted({sim.key(t) for t in part if t not in SMALL_WORDS}, key=count)[:k]

    anchors = set(rarest(line, 2) or sorted({sim.key(t) for t in line}, key=count)[:2])
    third = max(1, len(line) // 3)
    for part in (line[:third], line[third:2 * third], line[2 * third:]):
        anchors.update(k for k in rarest(part, 1) if count(k) <= ANCHOR_MAX)
    hits = sorted({p for k in anchors for p in index.get(k, ())})
    reach = 2 * len(line) + 10
    out = []
    for p in hits:
        lo, hi = max(0, p - reach), min(n, p + reach)
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def _find(line: list, stream: list, index: dict, sim) -> list:
    """takes() of the line, searched only inside its windows; positions are stream positions."""
    toks = [u["tok"] for u in stream]
    found = []
    for lo, hi in _windows(line, index, len(toks), sim):
        found += [(lo + a, lo + b, cov) for a, b, cov in takes(line, toks[lo:hi], sim)]
    return sorted(found)


# --------------------------------------------------------------------------- the recording
def _streams(sources: list, analyses: dict) -> tuple:
    """
    The recording as tokens in timeline order: the spoken words (timed, Hinglish)
    and, for translated analyses, Whisper's English sentences (times spread evenly
    over each sentence). Only the parts of each file the timeline uses.
    """
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
    """
    Every take of every line as a time interval: keep (the chosen take) or not,
    with the confidence and reason the decision will carry. Also returns the
    lines with no full take, and how many lines were found.
    """
    sim, out, missing, found = _Similar(), [], [], 0
    streams = []
    for stream in (words, english):
        if stream:
            index = {}  # sound key -> stream positions, for _windows()
            for i, u in enumerate(stream):
                index.setdefault(sim.key(u["tok"]), []).append(i)
            streams.append((stream, index))
    for line in lines:
        best_stream, best_takes = None, []
        for stream, index in streams:  # spoken words first; the translation must do clearly better
            tk = _find(line["toks"], stream, index, sim)
            if tk and (not best_takes or max(t[2] for t in tk) > max(t[2] for t in best_takes) + 0.05):
                best_stream, best_takes = stream, tk
        full = [t for t in best_takes if t[2] >= FULL_TAKE]
        if full:
            found += 1
        else:
            missing.append({"n": line["n"], "text": line["text"]})
        top = max([t[2] for t in full] or [0])
        keep = next((t for t in reversed(full) if t[2] >= LAST_WINS * top), None)
        sure = not line["repeated"] and len(line["toks"]) >= MIN_LINE  # may this line cut other takes?
        label = _label(line)
        for t in best_takes:
            u0, u1 = best_stream[t[0]], best_stream[t[1] - 1]
            iv = {"path": u0["path"], "t0": u0["t0"], "t1": u1["t1"], "score": t[2], "keep": False}
            if t is keep or (line["repeated"] and t[2] >= FULL_TAKE):
                iv.update(keep=True, confidence=0.0, reason="")  # said in your own words is fine
            elif not sure:
                continue  # unsure line: this take is decided like speech outside the script
            elif keep is not None and t[0] < keep[0]:
                k0 = best_stream[keep[0]]
                long_ago = k0["path"] == u0["path"] and k0["t0"] - u1["t1"] > LONG_AGO
                kind = "Earlier take" if t[2] >= FULL_TAKE else "Unfinished take"
                iv.update(confidence=BROWN if long_ago else CUT if t[2] >= FULL_TAKE else 0.85,
                          reason=f"{kind} of script {label}" + (" - said again much later, check it" if long_ago
                                                                 else " - keeping the later take"))
            else:  # after the kept take, or a line never said in full: never cut on the script alone
                iv.update(confidence=BROWN, reason=f"Another take of script {label} - check which one you want")
            out.append(iv)
    return out, missing, found


# --------------------------------------------------------------------------- word by word
def _started_line(toks: list, lines: list):
    """The script line a scrap of speech starts ("sabse pehle camera" -> line 2), if any."""
    for line in lines:
        k = 0
        while k < len(toks) and k < len(line["toks"]) and toks[k] == line["toks"][k]:
            k += 1
        if k >= 2 and k >= 0.6 * len(toks):
            return line
    return None


class _Intervals:
    """Time intervals looked up by a point in time, fast: sorted by start, the longest one bounds the search."""

    def __init__(self, items: list, start: str, end: str):
        self.items = sorted(items, key=lambda x: x[start])
        self.starts = [x[start] for x in self.items]
        self.longest = max([x[end] - x[start] for x in self.items] or [0.0])
        self.start, self.end = start, end

    def at(self, t: float, slack: float = 0.0) -> list:
        lo = bisect_left(self.starts, t - self.longest - slack)
        hi = bisect_right(self.starts, t + slack)
        return [x for x in self.items[lo:hi] if x[self.start] - slack <= t <= x[self.end] + slack]


def _label_words(path: str, a: dict, ranges: list, intervals: list, lines: list) -> list:
    """Each spoken word in the timeline's ranges: [word, confidence, reason] (confidence 0 = keep)."""
    takes_here = _Intervals([iv for iv in intervals if iv["path"] == path], "t0", "t1")
    analysed = _Intervals([d for d in a["decisions"] if d["kind"] in ("phrase", "restart")], "start", "end")
    out = []
    for w in a["_words"]:
        mid = (w["start"] + w["end"]) / 2
        if not any(lo <= mid < hi for lo, hi in ranges):
            continue
        hits = takes_here.at(mid, slack=0.05)
        kept = [iv for iv in hits if iv["keep"]]
        best = max(kept or hits, key=lambda iv: iv["score"], default=None)
        out.append([w, best["confidence"], best["reason"]] if best else [w, None, ""])

    # a kept take also takes the few loose words of the same breath around it ("aur | mere ko jo ...")
    def same_breath(w1, w2):
        return w2["start"] - max(w1["end"], w1["start"] + 0.2) < RUN_GAP

    for k in [k for k, x in enumerate(out) if x[1] is not None and x[1] < 0.75]:
        for step in (-1, 1):
            n, j = 0, k + step
            while 0 <= j < len(out) and out[j][1] is None and n < 4 and \
                    same_breath(*((out[j][0], out[j - step][0]) if step < 0 else (out[j - step][0], out[j][0]))):
                out[j][1], out[j][2] = out[k][1], out[k][2]
                n, j = n + 1, j + step

    def strongest(t: float, kinds: tuple) -> tuple:
        """(confidence, reason) of the strongest decision of the retake analysis at time t."""
        best = max((d for d in analysed.at(t) if d["kind"] in kinds), key=lambda d: d["confidence"], default=None)
        return (best["confidence"], best["reason"]) if best else (0.0, "")

    # a false start inside a kept take is still cut ("content creation video editing ya AI |
    # content creation video editing AI for creators"): the word-level restarts of the analysis
    for x in out:
        if x[1] == 0.0:
            conf, reason = strongest((x[0]["start"] + x[0]["end"]) / 2, ("restart",))
            if conf >= 0.75:
                x[1], x[2] = conf, reason

    # speech in no take: what the retake analysis cuts (said again later) is cut; of the rest,
    # scraps are cut and longer new stretches may be a good ad-lib (brown)
    view = [strongest((x[0]["start"] + x[0]["end"]) / 2, ("phrase", "restart")) if x[1] is None else None for x in out]
    i = 0
    while i < len(out):
        if out[i][1] is not None:
            i += 1
            continue
        retaken = view[i][0] >= 0.75
        j = i
        while j < len(out) and out[j][1] is None and (view[j][0] >= 0.75) == retaken:
            j += 1
        text = " ".join(x[0]["w"] for x in out[i:j])
        toks = tokens(to_hinglish(text))
        started = _started_line(toks, lines)
        # attached without a pause to speech that stays: part of that sentence, so never cut on its own
        attached = (i > 0 and out[i - 1][1] < 0.75 and same_breath(out[i - 1][0], out[i][0])) or \
                   (j < len(out) and out[j][1] is not None and out[j][1] < 0.75 and same_breath(out[j - 1][0], out[j][0]))
        if started and len(started["toks"]) >= MIN_LINE and not started["repeated"]:
            conf, reason = 0.9, f"Unfinished take of script {_label(started)}"
        elif retaken:
            why = max(view[i:j], key=lambda v: v[0])[1]
            conf, reason = 0.9, f"Not in your script, and {why[0].lower()}{why[1:]}"
        elif not attached and (restart_cue(text) or not [t for t in toks if t not in SMALL_WORDS]):
            conf, reason = 0.9, "Not in your script"  # a scrap between pauses: "okay", "let me do that again"
        else:  # when unsure, keep it brown
            conf, reason = BROWN, "Not in your script - keep it if it is a good ad-lib"
        for x in out[i:j]:
            x[1], x[2] = conf, reason
        i = j
    return out


def _spans(labelled: list, ranges: list) -> list:
    """
    Runs of words with the same decision -> time spans. Spans of one breath touch
    (no gap for a word to fall into); across a pause, a cut takes the pause with it.
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


# --------------------------------------------------------------------------- decisions
def _decisions(a: dict, spans: list) -> list:
    """
    Spans -> decisions on the analysis' phrases: a phrase covered by one span takes
    that span's decision; a mixed phrase stays kept, with child items ("script")
    for its cut / brown parts. Noises and fillers of the analysis are kept as they are.
    """
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
    return decisions


def apply(sources: list, analyses: dict, script: str) -> dict:
    """
    Replace the retake decisions of every analysed file with script decisions.
    Files without timed words (the words could not be read) keep their retake
    decisions. Returns {"lines", "found", "missing", "unread"} ({} for an empty script).
    """
    lines = split_lines(script)
    if not lines:
        return {}
    _mark_repeats(lines)
    usable = {p: a for p, a in analyses.items() if a.get("_words")}
    words, english = _streams([s for s in sources if s["path"] in usable], analyses)
    intervals, missing, found = _intervals(lines, words, english)
    for path, a in usable.items():
        ranges = [(s.get("start_sec", 0.0), s.get("end_sec") or a["info"]["duration"])
                  for s in sources if s["path"] == path]
        a["decisions"] = _decisions(a, _spans(_label_words(path, a, ranges, intervals, lines), ranges))
    unread = [a["name"] for p, a in analyses.items() if p not in usable and not a.get("silent") and a["phrases"]]
    return {"lines": len(lines), "found": found, "missing": missing, "unread": unread}
