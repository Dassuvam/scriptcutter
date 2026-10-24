"""
Retake and stumble detection with a confidence for every decision.

Every phrase (one Whisper segment, snapped to real speech) starts as KEEP and
only loses that status with evidence:
  * the same words are said again shortly after (a retake): the LAST take wins
  * the phrase belongs to a block that was re-recorded
  * explicit restart cues ("let me do that again", "phir se")
  * filler-only phrases, tiny isolated noises, unexplained audio
  * restarts in the timed words, also across Whisper's sentences (restarts.py)

`confidence` is how sure we are that an item should be REMOVED;
apply_policy() turns it into remove | review (kept, brown) | keep (blue).
"""
from __future__ import annotations

from . import restarts
from .energy import speech_overlap
from .text import FILLERS, content_tokens, find_internal_restart, is_filler_only, match_stats, restart_cue, tokens

WINDOW_SEC = 120.0     # how far ahead to look for a redo
WEAK_BLOCK_REASONS = ("After a failed take", "Sits inside a section", "Introduces a section", "This phrase contains")
MAX_AHEAD = 18         # phrases

POLICIES = {
    # name: (remove_at, review_at)
    "cautious": (0.88, 0.40),
    "balanced": (0.75, 0.45),
    "aggressive": (0.62, 0.42),
}


def build_phrases(segments: list) -> list:
    phrases = []
    for i, s in enumerate(segments):
        toks = tokens(s["text"])
        phrases.append({
            "id": f"p{i + 1:04d}",
            "start": s["start"],
            "end": s["end"],
            "text": s["text"],
            "toks": toks,
            "ctoks": content_tokens(toks),
            "p": s.get("p", 0.0),
            "words": s.get("words", []),
            "flags": list(s.get("flags", [])),
        })
    return phrases


def _retake_strength(st: dict, len_a: int, len_b: int, adjacent: bool) -> float:
    """0..1 evidence that phrase A is an earlier attempt of the text in B."""
    cov, matched, content, prefix = st["coverage_a"], st["matched"], st["content"], st["prefix"]
    ccov, n_content = st["content_cov"], st["n_content_a"]
    if len_a <= 3:
        # very short phrases: only an adjacent false start ("So the-" / "So the first tool is")
        if adjacent and prefix >= min(len_a, 2) and len_b > len_a and (content >= 1 or prefix >= 2):
            return 0.80 if prefix >= 2 else 0.6
        if adjacent and cov == 1.0 and content >= 1 and len_b <= len_a + 2:
            return 0.70  # "Let's get started." twice in a row
        return 0.0
    if n_content == 0:
        # all function words ("so what you have to do is, as I am...") - only a long exact opening counts
        if prefix >= 5 and (adjacent or cov >= 0.9):
            return 0.86
        return 0.0
    # alternative evidence: same meaningful words in another order, or same
    # sounds with drifting spelling (both common in translated transcripts)
    alt = 0.0
    # (the redo may not bring in new key words - "Claude will work better..." is a new statement)
    if st["bag"] >= 0.8 and st["bag_rev"] >= 0.9 and st["bag_n"] >= 3:
        alt = 0.62 + 0.25 * (st["bag"] - 0.8) / 0.2
    if st["char_cov"] >= 0.8 and st["len_chars_a"] >= 15 and (n_content == 0 or ccov >= 0.34):
        alt = max(alt, st["char_cov"] - 0.06)
    if matched < 3 or content < 1:
        return alt
    # the words that carry meaning must re-appear, not just the sentence shape
    # ("the second tool is Claude" vs "the third tool is Google Flow")
    if n_content >= 2 and ccov < 0.5:
        if cov >= 0.7 and matched >= 8:
            alt = max(alt, min(0.62, cov - 0.1))  # long shared frame: worth a look, not a cut
        return alt
    s = max(alt, min(cov, 0.5 + 0.5 * ccov))
    if matched >= 6:
        s += 0.05
    if content >= 4:
        s += 0.05
    if prefix >= 3 and (st["prefix_content"] >= 1 or prefix >= 5):
        s = max(s, 0.66 + 0.04 * min(prefix, 6))
    # the later take must not be a tiny fragment of this one
    if len_b < 0.5 * len_a and cov < 0.7:
        s -= 0.25
    return max(0.0, min(1.0, s))


def _weak_similarity(st: dict) -> float:
    """Loose 'same topic' signal used only for block reasoning."""
    if st["matched"] < 2 or st["content"] < 1:
        return 0.0
    return 0.5 * st["coverage_a"] + 0.5 * st["content_cov"]


def _conf_from_strength(s: float) -> float:
    if s >= 0.82:
        return 0.92
    if s >= 0.70:
        return 0.82
    if s >= 0.58:
        return 0.66
    if s >= 0.45:
        return 0.50
    return 0.0


def find_retakes(phrases: list) -> tuple:
    """
    For each phrase: the best later phrase (or pair of phrases) that redoes it.
    Returns (strong candidates, weak matches {i: [(j, sim), ...]}).
    """
    n = len(phrases)
    cands, weak = {}, {}
    for i in range(n):
        a = phrases[i]
        if not a["toks"] or "loop" in a["flags"]:
            continue
        best = None
        for j in range(i + 1, min(n, i + 1 + MAX_AHEAD)):
            b = phrases[j]
            if b["start"] - a["end"] > WINDOW_SEC:
                break
            adjacent = all(not phrases[k]["ctoks"] for k in range(i + 1, j))
            for span in (1, 2):
                if j + span > n:
                    break
                toks_b = [t for k in range(j, j + span) for t in phrases[k]["toks"]]
                st = match_stats(a["toks"], toks_b)
                if span == 2 and st["first_b"] >= len(phrases[j]["toks"]):
                    continue  # match lies entirely in the next phrase; that one is checked on its own
                if span == 1:
                    w = _weak_similarity(st)
                    if w >= 0.3:
                        weak.setdefault(i, []).append((j, w))
                s = _retake_strength(st, len(a["toks"]), len(toks_b), adjacent and span == 1)
                if s <= 0:
                    continue
                if best is None or s > best["strength"] + 0.02:
                    best = {"j": j, "span": span, "strength": s, "stats": st}
        if best:
            cands[i] = best
    return cands, weak


def analyze(phrases: list, untranscribed: list = None, translate_mode: bool = False,
            regions: list = None, words: list = None) -> list:
    """
    words: timed words in the spoken language for the whole file (phrases carry
    the ones inside them as p["words"]).
    Returns decisions: one per phrase (same order) plus extra items for
    untranscribed sounds, in-phrase restarts and filler words:
      {"id", "start", "end", "kind", "text", "confidence", "reason", "ref"}
    """
    n = len(phrases)
    cands, weak = find_retakes(phrases)
    dec = [{"id": p["id"], "start": p["start"], "end": p["end"], "kind": "phrase", "text": p["text"],
            "confidence": 0.0, "reason": "", "ref": None} for p in phrases]

    def set_dec(k, conf, reason, ref=None):
        conf = round(max(0.0, min(0.97, conf)), 3)
        if conf > dec[k]["confidence"]:
            dec[k].update(confidence=conf, reason=reason, ref=ref)

    base = {i: _conf_from_strength(c["strength"]) for i, c in cands.items()}
    redo_targets = {c["j"] for c in cands.values()}  # lines that ARE the redo of something earlier
    flagged = set()

    for _round in range(3):
        for k in range(n):
            dec[k].update(confidence=0.0, reason="", ref=None)

        # 1) direct retakes. Phrases between the take and its redo that are
        #    themselves failed attempts don't count as "real content in between".
        for i, c in cands.items():
            conf = base[i]
            if conf <= 0:
                continue
            j = c["j"]
            between = sum(phrases[k]["end"] - phrases[k]["start"] for k in range(i + 1, j)
                          if k not in flagged and k not in cands and k not in redo_targets
                          and phrases[k]["ctoks"])
            conf -= min(0.45, between * 0.025)
            if phrases[j]["start"] - phrases[i]["end"] > 45:
                conf -= 0.12
            if any(restart_cue(phrases[k]["text"]) for k in range(i, j)):
                conf += 0.08
            if translate_mode:
                conf -= 0.03  # translated text is a paraphrase; be a bit more careful
            pct = int(round(c["stats"]["coverage_a"] * 100))
            set_dec(i, conf, f"Said again at {fmt_t(phrases[j]['start'])} ({pct}% same words) - keeping the later take",
                    phrases[j]["id"])

        # 1b) a block redone in the same order (A1 B1 ... A2 B2) backs up each member
        for _ in range(2):
            for i in sorted(cands):
                for nb in (i - 1, i + 1):
                    if nb in cands and dec[nb]["confidence"] >= 0.6 and 0 < dec[i]["confidence"] < 0.9:
                        d_ref = cands[i]["j"] - cands[nb]["j"]
                        if (nb < i and 0 <= d_ref <= 2) or (nb > i and -2 <= d_ref <= 0):
                            dec[i]["confidence"] = round(min(0.92, dec[i]["confidence"] + 0.12), 3)
                            if "block" not in dec[i]["reason"]:
                                dec[i]["reason"] += " (part of a re-recorded block)"

        strong = {i for i in range(n) if dec[i]["confidence"] >= 0.6 and dec[i]["ref"]}

        # 2) phrases trapped inside a re-recorded block: there is a failed take
        #    before them, more failed material after them, and the redo starts later
        for k in range(n):
            if dec[k]["confidence"] >= 0.45 or not phrases[k]["toks"]:
                continue
            prev = [i for i in strong if i < k and k - i <= 4]
            if not prev:
                continue
            i0 = max(prev)
            redo = cands[i0]["j"] if i0 in cands else None
            if redo is None or k >= redo:
                continue
            p = phrases[k]
            after = [i for i in strong if k < i < redo and i - k <= 3]
            # where does the redo of this failed block begin? (earliest redo of its members)
            block = [i0]
            while block[-1] - 1 in strong:
                block.append(block[-1] - 1)
            block_redo = min(cands[i]["j"] for i in block if i in cands)
            if after:
                set_dec(k, 0.62 if len(p["toks"]) <= 8 else 0.55,
                        f"Sits inside a section that was re-recorded at {fmt_t(phrases[redo]['start'])}",
                        phrases[redo]["id"])
            elif k < block_redo and block_redo - k <= 4 and \
                    phrases[block_redo]["start"] - phrases[i0]["end"] <= 25:
                set_dec(k, 0.55, "After a failed take, before its redo at "
                                 f"{fmt_t(phrases[block_redo]['start'])}", phrases[block_redo]["id"])

        # 2b) lead-in line of a re-recorded section: kept on its own, but the
        #     section it introduces was redone, and it loosely matches the redo's intro
        for k in range(n):
            if dec[k]["confidence"] >= 0.45 or k + 1 >= n:
                continue
            nxt = next((m for m in range(k + 1, min(n, k + 3)) if phrases[m]["ctoks"]), None)
            if nxt is None or nxt not in strong or nxt not in cands:
                continue
            redo = cands[nxt]["j"]
            for j, w in weak.get(k, []):
                # the loose match must sit just before the redo, after the failed block
                if nxt < j < redo and j >= redo - 3 and j not in strong and w >= 0.4:
                    set_dec(k, 0.52, f"Introduces a section that was re-recorded at {fmt_t(phrases[j]['start'])}",
                            phrases[j]["id"])
                    break

        new_flagged = {k for k in range(n) if dec[k]["confidence"] >= 0.45}
        if new_flagged == flagged:
            break
        flagged = new_flagged

    # 3) cues, fillers, short noises, loops, unexplained audio
    for k, p in enumerate(phrases):
        d = dec[k]
        cue = restart_cue(p["text"])
        if cue:
            extra = [t for t in p["ctoks"] if t not in tokens(cue)]
            if len(extra) <= 3:
                set_dec(k, 0.9, f'Restart cue ("{cue}")')
            else:
                set_dec(k, 0.5, f'Contains a restart cue ("{cue}") - check the take')
        if is_filler_only(p["text"]):
            set_dec(k, 0.9, "Filler sound only (um / uh)")
        dur = p["end"] - p["start"]
        if d["confidence"] == 0 and dur < 0.6 and len(p["text"].split()) <= 2 and p["p"] < 0.6:
            prev_gap = p["start"] - phrases[k - 1]["end"] if k > 0 else 9
            next_gap = phrases[k + 1]["start"] - p["end"] if k + 1 < n else 9
            if prev_gap > 0.5 and next_gap > 0.5:
                set_dec(k, 0.55, "Very short isolated sound - probably noise or a stray word")
        if "loop" in p["flags"]:
            set_dec(k, 0.5, "Transcription was unreliable here (repeated words) - check by ear")
        if regions and d["confidence"] < 0.45:
            speech = speech_overlap(regions, p["start"], p["end"])
            if speech > 5.0 and len(p["toks"]) / speech < (0.45 if translate_mode else 0.7):
                set_dec(k, 0.5, f"{speech:.0f}s of talking but only {len(p['toks'])} words transcribed "
                                "- may contain a false start or mumbling")

    # 4) restarts in the timed words: a whole phrase, or a part of it (a child item)
    extra = []
    for r_i, r in enumerate(restarts.find(words or [])):
        for k, p in enumerate(phrases):
            lo, hi = max(p["start"], r["start"]), min(p["end"], r["end"])
            if hi - lo <= 0.05 or dec[k]["confidence"] >= r["confidence"]:
                continue
            mids = [((w["start"] + w["end"]) / 2, w) for w in p["words"]]
            inside = [w for m, w in mids if lo <= m < hi]
            if not inside:
                continue
            before, after = any(m < lo for m, _ in mids), any(m >= hi for m, _ in mids)
            if not before and not after:
                set_dec(k, r["confidence"], r["reason"])
                continue
            extra.append({"id": f"{p['id']}r{r_i}", "start": lo if before else p["start"],
                          "end": hi if after else p["end"], "kind": "restart", "parent": p["id"],
                          "text": " ".join(w["w"] for w in inside), "confidence": r["confidence"], "ref": None,
                          "reason": r["reason"]})
            # the failed opening is now cut precisely, so a loose "part of a failed block" flag on the
            # phrase no longer applies to the rest of it (the rest is the redo)
            if not before and r["confidence"] >= 0.75 and dec[k]["confidence"] < 0.6 and \
                    dec[k]["reason"].startswith(WEAK_BLOCK_REASONS):
                dec[k].update(confidence=0.0, reason="", ref=None)
    for k, p in enumerate(phrases):
        if dec[k]["confidence"] >= 0.6 or "loop" in p["flags"]:
            continue
        words_k = p["words"]
        if not words_k and find_internal_restart(p["toks"], min_len=4):
            set_dec(k, 0.5, "This phrase contains a repeated line - trim the first attempt")
        for w_i, w in enumerate(words_k):
            wt = tokens(w["w"])
            if wt and all(t in FILLERS for t in wt):
                gap_l = w["start"] - words_k[w_i - 1]["end"] if w_i > 0 else 1.0
                gap_r = words_k[w_i + 1]["start"] - w["end"] if w_i + 1 < len(words_k) else 1.0
                if gap_l >= 0.12 and gap_r >= 0.12:
                    extra.append({"id": f"{p['id']}f{w_i}", "start": w["start"], "end": w["end"],
                                  "kind": "filler", "parent": p["id"], "text": w["w"], "confidence": 0.8,
                                  "ref": None, "reason": "Filler word between pauses"})

    # 5) sounds with no words
    for u_i, (a, b) in enumerate(untranscribed or []):
        if b - a >= 0.7:
            extra.append({"id": f"u{u_i + 1:04d}", "start": a, "end": b, "kind": "sound", "text": "",
                          "confidence": 0.5, "ref": None,
                          "reason": "Sound with no recognisable words (mumble, laugh, cough?)"})
        else:
            extra.append({"id": f"u{u_i + 1:04d}", "start": a, "end": b, "kind": "sound", "text": "",
                          "confidence": 0.8, "ref": None, "reason": "Short noise between phrases"})
    return dec + extra


def apply_policy(decisions: list, policy: str = "balanced") -> list:
    remove_at, review_at = POLICIES.get(policy, POLICIES["balanced"])
    for d in decisions:
        if d.get("user_action"):
            d["action"] = d["user_action"]
            continue
        c = d["confidence"]
        d["action"] = "remove" if c >= remove_at else ("review" if c >= review_at else "keep")
        if d["action"] == "remove" and d.get("llm_only"):
            d["action"] = "review"  # the AI alone never cuts anything
    return decisions


def find_untranscribed(regions: list, phrases: list, min_dist: float = 0.35) -> list:
    """Speech-energy regions that no phrase covers and that are not glued to a phrase."""
    out = []
    spans = sorted((p["start"], p["end"]) for p in phrases)
    k = 0
    for a, b in regions:
        while k < len(spans) and spans[k][1] < a - min_dist:
            k += 1
        near = any(s - min_dist <= b and e + min_dist >= a for s, e in spans[k:k + 3])
        if not near:
            out.append((a, b))
    return out


def fmt_t(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}"
