"""
Restarts found in the timed words of the spoken language (Hindi words for a
Hindi video): attempts at a line that were dropped and started again.

Whisper often glues two takes into one long "sentence", so this works on the
words, not on Whisper's sentences. The words are split into attempts ("runs")
at pauses; Hindi is compared in Hinglish letters, so वीडियो and "video" match.

  1. a run that the next run starts again      "mere ko jo sabse badi" / "aur mere ko jo poochh..."
  2. the end of a run that the next run starts  "... and also aapko description mein hai na" /
                                                "and also aapko description mein ye jo video..."
  3. a line restarted inside a run, no pause    "so today we, so today we are going to"
  4. a scrap left just before a restart         "and main ..." / "and mere ko jo..."
  5. a run that begins a line already begun up to LOOKBACK seconds earlier: everything in between
     is a dropped attempt. Long stretches stay brown (kept, please check).

find() returns [{"start", "end", "confidence", "reason"}] in source seconds.
"""
from __future__ import annotations

from .text import STOPWORDS, find_internal_restart, tokens
from .translit import to_hinglish

RUN_GAP = 0.5          # a pause this long ends an attempt
MIN_WORD = 0.2         # word end times are rough; assume a word lasts at least this long
NEXT_RUN_GAP = 6.0     # rules 1, 2, 4: the redo follows within this many seconds
LOOKBACK = 25.0        # rule 5
EDGE = 0.12            # a cut ends this long before the redo starts
CONNECTORS = {"and", "aur", "or", "ya", "to", "toh", "so", "but", "lekin", "par", "phir", "fir", "then", "matlab",
              "basically", "actually", "also", "bhi", "na", "haan", "okay", "ok", "um", "uh"}
RESTART = "Started this line, stopped and said it again"


def _tokens(words: list) -> tuple:
    """Hinglish tokens and, for each, the index of the word it came from."""
    toks, owner = [], []
    for i, w in enumerate(words):
        for t in tokens(to_hinglish(w["w"])):
            toks.append(t)
            owner.append(i)
    return toks, owner


def _runs(words: list) -> list:
    """Word index ranges [i0, i1) between pauses."""
    runs, start = [], 0
    for i in range(1, len(words)):
        prev = words[i - 1]
        if words[i]["start"] - max(prev["end"], prev["start"] + MIN_WORD) >= RUN_GAP:
            runs.append((start, i))
            start = i
    if words:
        runs.append((start, len(words)))
    return runs


def _content(toks: list) -> bool:
    return any(t not in STOPWORDS and t not in CONNECTORS for t in toks)


def _strip(toks: list) -> int:
    """Number of leading connector tokens ("and", "aur", "so"...)."""
    n = 0
    while n < len(toks) and toks[n] in CONNECTORS:
        n += 1
    return n


def _shared_start(a: list, b: list) -> int:
    k = 0
    while k < len(a) and k < len(b) and a[k] == b[k]:
        k += 1
    return k


def _strong_enough(prefix: list) -> bool:
    return len(prefix) >= 3 or (len(prefix) == 2 and _content(prefix))


def _redo_cap(rest: list, redo: list, finished: bool) -> float:
    """
    How sure a restart can be, judged by what the first attempt said after the shared start.
    "agar aapko ye video pasand aaya to like kar dena" / "agar aapko ye video pasand nahi aaya to comment
    karke batana" share a start but say different things: 0 = not a restart.
    """
    rest = [t for t in rest if t not in STOPWORDS and t not in CONNECTORS]
    again = sum(1 for t in rest if t in set(redo))
    if len(rest) >= 2 and again < len(rest) / 2:
        return 0.0
    if finished and again < len(rest):
        return 0.62  # a finished sentence that the next one changes: kept, please check
    return 1.0


def _finished(word: dict) -> bool:
    return word["w"].rstrip().endswith((".", "?", "!", "।"))


def find(words: list) -> list:
    if len(words) < 3:
        return []
    toks, owner = _tokens(words)
    first_tok = {}
    for ti, wi in enumerate(owner):
        first_tok.setdefault(wi, ti)
    runs = [(a, b) for a, b in _runs(words) if a in first_tok]

    def run_toks(run):
        a, b = run
        t0 = first_tok[a]
        t1 = first_tok.get(b, len(toks)) if b < len(words) else len(toks)
        return t0, toks[t0:t1]

    found, cut_runs = [], set()

    def add(start, end, conf, reason):
        if end - start > 0.05:
            found.append({"start": round(start, 3), "end": round(end, 3), "confidence": conf, "reason": reason})

    for r in range(len(runs) - 1):
        A, B = runs[r], runs[r + 1]
        wa0, wa1 = A
        wb0 = B[0]
        if words[wb0]["start"] - words[wa1 - 1]["start"] > NEXT_RUN_GAP:
            continue
        ta0, ta = run_toks(A)
        _, tb = run_toks(B)
        tb = tb[_strip(tb):]
        if len(tb) < 2:
            continue
        redo_at = words[wb0]["start"] - EDGE
        # 1 + 2: the next attempt starts with words found in this one (at its start or later)
        best = None
        for p in range(len(ta)):
            k = _shared_start(ta[p:], tb)
            if _strong_enough(tb[:k]) and (best is None or k >= best[1]):
                best = (p, k)
        if best:
            p, k = best
            cap = _redo_cap(ta[p + k:], tb, _finished(words[wa1 - 1]))
            if not cap:
                continue
            lead = _strip(ta)
            if p <= lead:  # rule 1: the whole run was a try
                tail = len(ta) - lead - k
                if tail <= 12:
                    conf = 0.84 if k >= 4 or (k == 3 and _content(tb[:3])) else 0.72
                    add(words[wa0]["start"], max(words[wa1 - 1]["end"], redo_at),
                        min(cap, conf - (0.15 if tail > 6 else 0)), RESTART)
                    cut_runs.add(r)
                    continue
            else:  # rule 2: the end of the run was a try; take the connectors before it too
                ti = p
                while ti > 0 and ta[ti - 1] in CONNECTORS:
                    ti -= 1
                pause = words[wb0]["start"] - max(words[wa1 - 1]["end"], words[wa1 - 1]["start"] + MIN_WORD)
                conf = 0.82 if k >= 4 else (0.78 if pause >= 0.8 else 0.72)
                add(words[owner[ta0 + ti]]["start"], max(words[wa1 - 1]["end"], redo_at), min(cap, conf), RESTART)

    for r, (a, b) in enumerate(runs):
        # 3: restart inside one run
        t0, rt = run_toks((a, b))
        hit = find_internal_restart(rt)
        if hit:
            i, j = owner[t0 + hit[0]], owner[t0 + hit[1]]
            pause = words[j]["start"] - max(words[j - 1]["end"], words[j - 1]["start"] + MIN_WORD)
            add(words[i]["start"], words[j]["start"] - 0.02, 0.8 if pause >= 0.1 else 0.66, RESTART)
        # 4: a scrap of one or two words, a pause, then a restart
        if r + 1 < len(runs) and len(rt) <= 2 and words[b - 1]["start"] - words[a]["start"] <= 1.2:
            nxt = runs[r + 1]
            _, nt = run_toks(nxt)
            gap = words[nxt[0]]["start"] - words[b - 1]["start"]
            same = bool(nt) and nt[0] == rt[0]
            if 0.8 <= gap <= NEXT_RUN_GAP and (r + 1 in cut_runs or (same and rt[0] in CONNECTORS | STOPWORDS)):
                conf = 0.8 if r + 1 in cut_runs and same else (0.7 if same else 0.62)
                add(words[a]["start"], words[nxt[0]]["start"] - EDGE, conf, RESTART)

    # 5: a line begun again after a longer stretch
    for r in range(1, len(runs)):
        wb0 = runs[r][0]
        tb0, tb = run_toks(runs[r])
        lead = _strip(tb)
        tb = tb[lead:]
        start_b = words[wb0]["start"]
        if len(tb) < 3 or not _content(tb[:3]):
            continue
        prev_run_start = first_tok[runs[r - 1][0]]
        best = None
        for ti in range(prev_run_start - 1, -1, -1):  # before the previous run (that one is rules 1-2)
            if start_b - words[owner[ti]]["start"] > LOOKBACK:
                break
            k = _shared_start(toks[ti:tb0], tb)
            if k >= 3 and _content(tb[:k]):
                best = (ti, k)
                break
        if best:
            ti, k = best
            occ = words[owner[ti]]["start"]
            span = start_b - occ
            conf = 0.8 if span <= 6 else (0.62 if span <= 12 else 0.55)
            conf = min(conf, _redo_cap(toks[ti + k:tb0], tb, False) or 0.55)
            add(occ, start_b - EDGE, conf, f"Dropped attempt: this line starts again {span:.0f}s later")
    return found
