"""
Hindi (Devanagari) -> Hinglish: Hindi words in English letters, the way people
type them in chats ("kya hai", "accha", "nahi"). Offline and rule based:
letters map to sounds, the silent "a" is dropped (Ohala's rule), and the most
common words use their usual chat spelling. Latin text passes through.
"""
from __future__ import annotations

import re
import unicodedata

CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "ळ": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
}
NUKTA_FORMS = {"क": "k", "ख": "kh", "ग": "g", "ज": "z", "ड": "d", "ढ": "dh", "फ": "f", "य": "y", "र": "r"}
VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai",
          "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e", "ॲ": "a"}
MATRAS = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o",
          "ौ": "au", "ॉ": "o", "ॅ": "e", "ॆ": "e", "ॊ": "o"}
HALANT, NUKTA, ANUSVARA, CANDRABINDU, VISARGA = "्", "़", "ं", "ँ", "ः"
LITERALS = {"ॐ": "om", **{chr(0x0966 + i): str(i) for i in range(10)}}
LABIALS = ("p", "ph", "b", "bh", "m")

# usual chat spellings; also covers English words Whisper writes in Devanagari
COMMON = {
    "है": "hai", "हैं": "hain", "मैं": "main", "में": "mein", "नहीं": "nahi", "नही": "nahi", "क्या": "kya",
    "यह": "yeh", "ये": "ye", "वह": "woh", "वो": "wo", "और": "aur", "तो": "to", "भी": "bhi", "कि": "ki",
    "ही": "hi", "हम": "hum", "तुम": "tum", "कुछ": "kuch", "आप": "aap", "हाँ": "haan", "हां": "haan", "माँ": "maa", "मां": "maa",
    "मुझे": "mujhe", "हमें": "humein", "तुम्हें": "tumhe", "उन्हें": "unhe", "इन्हें": "inhe", "एक": "ek",
    "कैसे": "kaise", "ऐसे": "aise", "वैसे": "waise", "जैसे": "jaise", "यहाँ": "yahan", "यहां": "yahan",
    "वहाँ": "wahan", "वहां": "wahan", "कहाँ": "kahan", "कहां": "kahan", "अच्छा": "accha", "अच्छी": "acchi",
    "अच्छे": "acche", "ठीक": "theek", "लेकिन": "lekin", "क्योंकि": "kyunki", "फिर": "phir", "बहुत": "bahut",
    "थोड़ा": "thoda", "थोड़ी": "thodi", "ज़्यादा": "zyada", "ज्यादा": "zyada", "गई": "gayi", "गए": "gaye",
    "हुआ": "hua", "हुई": "hui", "हुए": "hue", "दोस्तों": "doston", "दोस्तो": "dosto", "पहले": "pehle",
    "ज़रूर": "zaroor", "जरूर": "zaroor", "शुरू": "shuru", "ख़त्म": "khatam", "खत्म": "khatam", "मतलब": "matlab",
    "लिए": "liye", "इसलिए": "isliye", "साथ": "saath", "वाला": "wala", "वाली": "wali", "वाले": "wale",
    "वीडियो": "video", "वीदियो": "video", "विडियो": "video", "विडिओ": "video", "वीडियोज़": "videos", "नमस्ति": "namaste", "चैनल": "channel", "सब्सक्राइब": "subscribe", "लाइक": "like",
    "कमेंट": "comment", "शेयर": "share", "यूट्यूब": "YouTube", "इंस्टाग्राम": "Instagram", "फ़ोन": "phone",
    "फोन": "phone", "ऐप": "app", "एडिट": "edit", "एडिटिंग": "editing", "ओके": "okay", "बेसिकली": "basically",
    "एक्चुअली": "actually", "टाइम": "time", "टिप्स": "tips", "ट्यूटोरियल": "tutorial", "कैमरा": "camera",
    "लाइट": "light", "चाहिए": "chahiye", "चाहिये": "chahiye", "आवाज़": "awaaz", "आवाज": "awaaz",
}
COMMON = {unicodedata.normalize("NFC", k): v for k, v in COMMON.items()}

_DEVANAGARI_RUN = re.compile("[\u0900-\u0963\u0966-\u097f]+")  # letters and digits, not the danda


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI_RUN.search(text or ""))


def _units(word: str) -> list:
    """Letters -> [{"c": consonant or None, "v": vowel ("" = none), "schwa": inherent a, "nasal", "h"}]."""
    units, i = [], 0
    while i < len(word):
        ch = word[i]
        if ch in CONSONANTS:
            c = CONSONANTS[ch]
            if i + 1 < len(word) and word[i + 1] == NUKTA:
                c = NUKTA_FORMS.get(ch, c)
                i += 1
            nxt = word[i + 1] if i + 1 < len(word) else ""
            if nxt in MATRAS:
                units.append({"c": c, "v": MATRAS[nxt], "schwa": False})
                i += 1
            elif nxt == HALANT:
                units.append({"c": c, "v": "", "schwa": False})
                i += 1
            else:
                units.append({"c": c, "v": "a", "schwa": True})
        elif ch in VOWELS:
            units.append({"c": None, "v": VOWELS[ch], "schwa": False})
        elif ch in (ANUSVARA, CANDRABINDU):
            if units:
                units[-1]["nasal"] = True
            else:
                units.append({"c": None, "v": "n", "schwa": False})
        elif ch == VISARGA and units:
            units[-1]["h"] = True
        elif ch in LITERALS:
            units.append({"c": None, "v": LITERALS[ch], "schwa": False, "literal": True})
        i += 1
    return units


def _drop_schwas(units: list):
    """Silent 'a': at the end of a word and in V C a C V, right to left. मित्र/प्रिय keep it, दोस्त drops it."""
    n = len(units)
    last = n - 1
    if n > 1 and units[last]["schwa"] and not units[last].get("nasal"):
        after_cluster = units[last - 1]["v"] == ""
        keeps = units[last]["c"] in ("r", "y", "v") and (after_cluster or units[last - 1]["v"] in ("i", "ee"))
        if not keeps:
            units[last]["v"] = ""
    for k in range(last - 1, 0, -1):
        u, prev, nxt = units[k], units[k - 1], units[k + 1]
        if (u["schwa"] and u["v"] and not u.get("nasal") and prev["v"] and not prev.get("literal")
                and nxt["c"] and nxt["v"]):
            u["v"] = ""


def _word(word: str) -> str:
    if word in COMMON:
        return COMMON[word]
    if "ज्ञ" in word:
        word = word.replace("ज्ञ", "ग्य")
    units = _units(word)
    _drop_schwas(units)
    out = []
    for k, u in enumerate(units):
        c, v = u["c"] or "", u["v"]
        if c == "v" and k == 0 and v in ("a", "aa", "o", "e", "ai"):
            c = "w"
        if u["c"] is None and v == "e" and k > 0 and units[k - 1]["v"] not in ("", "a"):
            v = "ye"  # गए -> gaye, किए -> kiye
        nasal = ""
        if u.get("nasal"):
            nxt = units[k + 1]["c"] if k + 1 < len(units) else None
            nasal = "m" if nxt in LABIALS else "n"
            if k == len(units) - 1 and v == "e":
                v, nasal = "ei", "n"  # में -> mein, करें -> karein
        out.append(c + v + nasal + ("h" if u.get("h") else ""))
    text = "".join(out)
    text = text.replace("chchh", "cch").replace("chch", "cch")
    head = len(out[0]) if out else 0
    text = text[:head] + text[head:].replace("aa", "a")  # long a only in the first syllable: aap, banate
    if len(text) > 2:  # chat spelling of word endings: tha, bhi, tu, saaf
        text = re.sub(r"ph$", "f", text)
        text = re.sub(r"aa$", "a", text)
        text = re.sub(r"ee$", "i", text)
        text = re.sub(r"een$", "in", text)
        text = re.sub(r"oo$", "u", text)
    return text


def to_hinglish(text: str) -> str:
    """Devanagari words -> chat-style Latin letters; everything else is left as it is."""
    if not has_devanagari(text):
        return text or ""
    text = unicodedata.normalize("NFC", text).replace("॥", ".").replace("।", ".")
    return _DEVANAGARI_RUN.sub(lambda m: _word(m.group(0)), text)
