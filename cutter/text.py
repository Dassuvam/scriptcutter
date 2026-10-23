"""
Unicode-aware text helpers: normalisation, tokens, fillers, restart cues,
loop detection and the similarity measures used to find retakes. Works for
any script (Latin, Devanagari, ...).
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    "first": "1st", "third": "3rd", "fourth": "4th", "fifth": "5th",
}

STOPWORDS = set("""
a an the and or but so to of in on at for with from by as is are was were be been being am
i you he she it we they me him her us them my your his its our their this that these those
there here then than now just also very really too only not no do does did have has had will
would can could should shall may might must okay ok yeah yes well like right um uh oh
what which who whom how when where why if because about into over up down out
toh to hai hain ki ka ke ko se mein me main aur ye yeh wo woh bhi ho hum aap tum na nahi
""".split())

FILLERS = {
    "um", "umm", "ummm", "uh", "uhh", "uhm", "erm", "er", "ah", "ahh", "hmm", "hm", "mm", "mmm",
    "eh", "uhhh", "अं", "अ", "हम्म", "हम", "उम", "आ",
}

# phrases that mean "that take was bad, I'm going again"
RESTART_CUES = [
    "let me start again", "let me start over", "let me redo", "let me do that again", "let me say that again",
    "let me try that again", "let me try again", "lets do that again", "let's do that again", "one more time",
    "start over", "from the top", "scratch that", "cut that", "sorry let me", "wait let me", "no wait",
    "take two", "take 2", "again from", "i'll say that again", "i will say that again", "one second",
    "phir se", "fir se", "dobara", "ek baar aur", "ek aur baar", "ek second", "ruko", "cut cut", "sorry sorry",
    "फिर से", "दोबारा", "एक बार और", "रुको", "सॉरी",
]

NONSPEECH_RE = re.compile(r"^\s*[\[\(\*♪♫].*[\]\)\*♪♫]\s*$|^\s*[♪♫\s.]+\s*$")
STOCK_HALLUCINATIONS = [
    "thank you for watching", "thanks for watching", "please subscribe", "subscribe to my channel",
    "like and subscribe", "see you in the next video", "subtitles by", "amara.org", "transcribed by",
    "thank you.", "you", "bye.",
]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().replace("’", "'")
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat[0] in ("L", "M", "N") or ch == "'":
            out.append(ch)
        else:
            out.append(" ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


CONTRACTIONS = {
    "it's": "it is", "that's": "that is", "what's": "what is", "there's": "there is", "here's": "here is",
    "let's": "let us", "don't": "do not", "doesn't": "does not", "didn't": "did not", "can't": "can not",
    "won't": "will not", "isn't": "is not", "aren't": "are not", "i'm": "i am", "you're": "you are",
    "we're": "we are", "they're": "they are", "i've": "i have", "you've": "you have", "we've": "we have",
    "i'll": "i will", "you'll": "you will", "we'll": "we will", "i'd": "i would", "you'd": "you would",
    "gonna": "going to", "wanna": "want to",
}


def tokens(text: str) -> list:
    toks = []
    for w in normalize(text).split():
        w = w.strip("'")
        if not w:
            continue
        if w in CONTRACTIONS:
            toks.extend(CONTRACTIONS[w].split())
            continue
        toks.append(NUMBER_WORDS.get(w, w))
    return toks


def content_tokens(toks: list) -> list:
    return [t for t in toks if t not in STOPWORDS and t not in FILLERS]


def is_filler_only(text: str) -> bool:
    t = tokens(text)
    return bool(t) and all(w in FILLERS for w in t)


def restart_cue(text: str) -> str:
    n = " " + normalize(text) + " "
    for cue in RESTART_CUES:
        if " " + normalize(cue) + " " in n:
            return cue
    return ""


def is_nonspeech_annotation(text: str) -> bool:
    return bool(NONSPEECH_RE.match(text.strip()))


STOCK_PHRASES = [
    "thank you for watching", "thanks for watching", "please subscribe to our channel", "please subscribe",
    "subscribe to our channel", "subscribe to my channel", "hit the like button", "like and subscribe",
    "see you in the next video", "thank you", "thanks",
]


def stock_share(text: str) -> float:
    """Share of the words that belong to YouTube-outro boilerplate Whisper invents over noise."""
    n = " " + normalize(text) + " "
    total = len(n.split())
    if not total:
        return 0.0
    covered = 0
    for ph in STOCK_PHRASES:
        p = " " + ph + " "
        while p in n:
            covered += len(ph.split())
            n = n.replace(p, " ", 1)
    return covered / total


def is_stock_hallucination(text: str) -> bool:
    n = normalize(text)
    return any(n == normalize(h) or (len(h) > 12 and normalize(h) in n) for h in STOCK_HALLUCINATIONS)


# ---------------------------------------------------------------------------
# loops
# ---------------------------------------------------------------------------
def _find_repeat(toks: list, min_reps: int = 4):
    """(start, unit_len, reps) of the longest run of a repeated n-gram, or None."""
    n = len(toks)
    best = None
    for unit in range(1, 9):
        i = 0
        while i + unit * min_reps <= n:
            reps = 1
            while i + unit * (reps + 1) <= n and toks[i + unit * reps:i + unit * (reps + 1)] == toks[i:i + unit]:
                reps += 1
            if reps >= min_reps and (best is None or reps * unit > best[2] * best[1]):
                best = (i, unit, reps)
            i += max(1, reps * unit if reps >= min_reps else 1)
    return best


def has_loop(text: str) -> bool:
    t = text.split()
    if len(t) < 8:
        return False
    if _find_repeat(t):
        return True
    uniq = len(set(t)) / len(t)
    return len(t) >= 20 and uniq < 0.25


def collapse_loop(text: str) -> str:
    t = text.split()
    for _ in range(5):
        r = _find_repeat(t)
        if not r:
            break
        i, unit, reps = r
        t = t[:i + unit] + ["…"] + t[i + unit * reps:]
    return " ".join(t)


# ---------------------------------------------------------------------------
# similarity
# ---------------------------------------------------------------------------
def match_stats(a: list, b: list) -> dict:
    """
    How much of token list `a` re-appears (in order) inside `b`.
      coverage_a: share of a's tokens found in b
      matched:    number of matched tokens
      content:    matched tokens that are not stopwords
      prefix:     length of the common opening (1 typo allowed)
      a_pos/b_pos: where the first matching block sits
    """
    empty = {"coverage_a": 0.0, "coverage_b": 0.0, "matched": 0, "content": 0, "content_cov": 0.0,
             "prefix": 0, "prefix_content": 0, "ratio": 0.0, "first_b": -1}
    if not a or not b:
        return empty
    sm = SequenceMatcher(None, a, b, autojunk=False)
    blocks = [blk for blk in sm.get_matching_blocks() if blk.size > 0]
    # ignore isolated single-token matches of stopwords: they are noise
    useful = [blk for blk in blocks if blk.size >= 2 or a[blk.a] not in STOPWORDS]
    matched = sum(blk.size for blk in useful)
    content = sum(1 for blk in useful for k in range(blk.size) if a[blk.a + k] not in STOPWORDS)
    n_content_a = sum(1 for t in a if t not in STOPWORDS and t not in FILLERS)
    # common opening; one mismatch is tolerated only between two filler/stop words
    prefix, prefix_content, miss = 0, 0, 0
    for x, y in zip(a, b):
        if x == y:
            prefix += 1
            if x not in STOPWORDS:
                prefix_content += 1
        elif miss == 0 and prefix >= 2 and x in STOPWORDS and y in STOPWORDS:
            miss = 1
        else:
            break
    # order-free overlap of meaningful words (translations reorder clauses),
    # measured against the opening of b that is about as long as a
    ca = {t for t in a if t not in STOPWORDS and t not in FILLERS}
    cb = {t for t in b[:len(a) + 2] if t not in STOPWORDS and t not in FILLERS}
    bag = len(ca & cb) / len(ca) if ca else 0.0
    bag_rev = len(ca & cb) / len(cb) if cb else 0.0
    # character-level coverage (spelling drift: "chaat gpt" / "chargipiti")
    sa, sb = " ".join(a), " ".join(b)
    csm = SequenceMatcher(None, sa, sb, autojunk=False)
    char_cov = sum(blk.size for blk in csm.get_matching_blocks() if blk.size >= 3) / max(1, len(sa))
    return {
        "coverage_a": matched / len(a),
        "coverage_b": matched / len(b),
        "matched": matched,
        "content": content,
        "content_cov": content / n_content_a if n_content_a else 1.0,
        "n_content_a": n_content_a,
        "bag": bag,
        "bag_rev": bag_rev,
        "bag_n": len(ca & cb),
        "char_cov": char_cov,
        "len_chars_a": len(sa),
        "prefix": prefix,
        "prefix_content": prefix_content,
        "ratio": sm.ratio(),
        "first_b": useful[0].b if useful else -1,
    }


def find_internal_restart(toks: list, min_len: int = 3, max_dist: int = 18):
    """
    A restart inside one phrase: "so today we are, so today we are going to..."
    Returns (i, j) token indices: the earlier attempt starts at i, the redo at j.
    """
    n = len(toks)
    best = None
    for i in range(n):
        for j in range(i + min_len, min(n, i + max_dist + 1)):
            k = 0
            while j + k < n and i + k < j and toks[i + k] == toks[j + k]:
                k += 1
            if k >= min_len and any(t not in STOPWORDS for t in toks[i:i + k]):
                if best is None or k > best[2]:
                    best = (i, j, k)
    return (best[0], best[1]) if best else None
