"""
Source file -> analysis -> clip plan.

Audio, transcript, voice and screen-motion results are cached per file, so
changing the policy, padding or your own choices re-cuts instantly.

Two transcripts can exist per file: the one the cut is analysed from (for
non-English speech usually Whisper's English translation) and the words in the
spoken language (`original_words`), which the review and the final transcript
show. When the analysis already ran in the spoken language they are the same.
"""
from __future__ import annotations

import json
import os
import threading
import time

from . import learn, llm, vad
from .analyze import analyze, apply_policy, build_phrases, find_untranscribed, POLICIES
from .cuts import build_clips, to_frames
from .energy import compute_envelope, detect_speech, thresholds
from .media import cache_dir_for, detect_frozen_video, extract_audio, probe
from .transcribe import can_translate, clean_segments, detect_language, pick_model, transcribe

DEFAULT_SETTINGS = {
    "language": "auto",         # auto | en | hi | ... (Whisper codes)
    "text_mode": "auto",        # auto | original | english
    "model": "auto",            # auto | ggml-xxx.bin
    "sensitivity": 0.0,         # dB; + catches quieter speech
    "policy": "balanced",       # cautious | balanced | aggressive
    "footage": "talking_head",  # talking_head | screen
    "max_pause": 0.45,
    "head_pad": 0.10,
    "tail_pad": 0.16,
    "llm_provider": "",         # "" = local only
    "llm_model": "",
    "llm_api_key": "",
    "llm_base_url": "",
    "topic": "",
    "use_vad": True,            # Silero voice detection when installed
    "learn": True,              # apply what was learned from your past choices
}

# one Whisper at a time: two large models at once can run a laptop out of memory
WHISPER_LOCK = threading.Lock()


class Reporter:
    """Progress sink. Stages have weights so the bar moves sensibly on long files."""

    STAGES = [("audio", 4), ("levels", 2), ("language", 4), ("transcribe", 60), ("analyse", 4),
              ("video", 10), ("ai", 16)]

    def __init__(self, on_update=None):
        self.on_update = on_update
        self.stage = ""
        self.frac = 0.0
        self.logs = []
        self.file_index, self.file_count = 0, 1

    def log(self, msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.logs.append(line)
        print(line, flush=True)
        self._emit()

    def set(self, stage: str, frac: float = 0.0):
        self.stage, self.frac = stage, max(0.0, min(1.0, frac))
        self._emit()

    def overall(self) -> float:
        total = sum(w for _, w in self.STAGES)
        done = 0.0
        for name, w in self.STAGES:
            if name == self.stage:
                done += w * self.frac
                break
            done += w
        per_file = done / total
        return (self.file_index + per_file) / max(1, self.file_count)

    def _emit(self):
        if self.on_update:
            self.on_update(self)


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _transcript_key(model: str, lang: str, translate: bool, sens: float, voice_used: bool) -> str:
    return (f"transcript_{os.path.basename(model)}_{lang}_{'en' if translate else 'orig'}_{sens:+.0f}"
            f"{'_vad' if voice_used else ''}.json")


def _transcribe(log, *args, **kwargs) -> dict:
    if not WHISPER_LOCK.acquire(blocking=False):
        log("Waiting for the speech engine to finish the previous script...")
        WHISPER_LOCK.acquire()
    try:
        t0 = time.time()
        tr = transcribe(*args, log=log, **kwargs)
        tr["seconds"] = round(time.time() - t0, 1)
        return tr
    finally:
        WHISPER_LOCK.release()


def words_from_segments(segments: list) -> list:
    """Timed words; a segment without word timings (e.g. a collapsed loop) counts as one word."""
    out = []
    for seg in segments:
        words = [w for w in seg.get("words", []) if w.get("w")]
        if words:
            out += [{"w": w["w"], "start": w["start"], "end": w["end"]} for w in words]
        else:
            out.append({"w": seg["text"], "start": seg["start"], "end": seg["end"]})
    return out


def analyze_file(path: str, settings: dict, rep: Reporter = None, use_cache: bool = True) -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update({k: v for k, v in (settings or {}).items() if v is not None})
    rep = rep or Reporter()
    name = os.path.basename(path)

    rep.set("audio")
    info = probe(path)
    cdir = cache_dir_for(path)
    rep.log(f"{name}: {info['duration'] / 60:.1f} min, {info['fps']:.3f} fps, "
            f"{info['audio_streams']} audio stream(s)")
    wav = extract_audio(path, os.path.join(cdir, "audio16k.wav"))

    rep.set("levels")
    env = compute_envelope(wav)
    sens = float(s["sensitivity"])
    regions = detect_speech(env, sensitivity_db=sens)
    loose = detect_speech(env, sensitivity_db=sens + 4.0)  # inclusive set for transcription
    on, _ = thresholds(env, sens)
    rep.log(f"Noise floor {env.noise_floor:.0f} dB, speech {env.speech_level:.0f} dB -> threshold {on:.0f} dB; "
            f"{sum(b - a for a, b in regions) / 60:.1f} min of sound in {len(regions)} regions")

    # screen motion runs on the CPU while Whisper uses the GPU
    frozen_box = {}
    frozen_thread = None
    if s["footage"] == "screen" and info["has_video"]:
        fpath = os.path.join(cdir, "frozen.json")
        cached_frozen = _load_json(fpath) if use_cache else None
        if cached_frozen is not None:
            frozen_box["v"] = cached_frozen
        else:
            def _scan():
                try:
                    frozen_box["v"] = detect_frozen_video(path, info["duration"],
                                                          progress=lambda f: frozen_box.__setitem__("p", f))
                    _save_json(fpath, frozen_box["v"])
                except Exception as e:  # never fatal
                    frozen_box["err"] = str(e)
            frozen_thread = threading.Thread(target=_scan, daemon=True)
            frozen_thread.start()

    # neural voice detection: keeps typing / clicks / music away from Whisper
    voice_used = False
    nonvoice = []
    if s["use_vad"] and vad.available():
        vpath = os.path.join(cdir, "voice.json")
        voice = _load_json(vpath) if use_cache else None
        if voice is None:
            voice = vad.voice_segments(wav)
            _save_json(vpath, voice)
        sound_total = sum(b - a for a, b in loose) or 1.0
        if sum(b - a for a, b in voice) > 0.15 * sound_total:
            loose, _ = vad.intersect(loose, voice)
            regions, nonvoice = vad.intersect(regions, voice)
            voice_used = True
            rep.log(f"Voice detector: {sum(b - a for a, b in regions) / 60:.1f} min of actual speech, "
                    f"{sum(b - a for a, b in nonvoice) / 60:.1f} min of other sounds (typing, music, noise)")
        else:
            rep.log("Voice detector found almost no speech - falling back to loudness only")

    # language + text mode
    rep.set("language")
    lang = s["language"]
    lang_p = 1.0
    if lang == "auto":
        try:
            probe_model = pick_model(s["model"], translate=True, language="xx")
        except RuntimeError:
            probe_model = pick_model(s["model"], translate=False, language="xx")
        cached = _load_json(os.path.join(cdir, "language.json")) if use_cache else None
        if cached:
            lang, lang_p = cached["language"], cached["p"]
        else:
            lang, lang_p = detect_language(wav, loose, probe_model)
            _save_json(os.path.join(cdir, "language.json"), {"language": lang, "p": lang_p})
        rep.log(f"Detected language: {lang} (p={lang_p:.2f})")
    mode = s["text_mode"]
    translate = (lang != "en") if mode == "auto" else (mode == "english" and lang != "en")
    model = pick_model(s["model"], translate=translate, language=lang)
    if translate and not can_translate(model):
        translate = False
    rep.log(f"Whisper model {os.path.basename(model)}; "
            + ("analysing via English translation (stable for non-English speech)" if translate
               else "transcribing in the original language"))

    # transcript (cached)
    rep.set("transcribe")
    tpath = os.path.join(cdir, _transcript_key(model, lang, translate, sens, voice_used))
    tr = _load_json(tpath) if use_cache else None
    if tr:
        rep.log("Using cached transcript (delete it from the cache folder to redo)")
    else:
        tr = _transcribe(rep.log, wav, loose, env.duration, env, model, lang, translate,
                         progress=lambda f: rep.set("transcribe", f))
        _save_json(tpath, tr)
    segs = clean_segments(tr["raw"], regions)
    rep.log(f"{len(segs)} phrases transcribed"
            + (f" ({sum(1 for x in segs if 'loop' in x.get('flags', []))} unreliable)" if segs else ""))

    # analysis
    rep.set("analyse")
    phrases = build_phrases(segs)
    if s["footage"] == "screen":
        untranscribed = []
    else:
        untranscribed = find_untranscribed(regions, phrases)
        # non-voice sounds (claps, coughs, bumps) are noise, never words
        untranscribed += [(a, b) for a, b in nonvoice if b - a >= 0.15 and
                          not any(p["start"] - 0.2 < b and p["end"] + 0.2 > a for p in phrases)]
        untranscribed.sort()
    decisions = analyze(phrases, untranscribed, translate_mode=translate, regions=regions)
    if voice_used:
        for d in decisions:
            if d["kind"] == "sound" and any(a <= d["start"] + 0.01 and b >= d["end"] - 0.01 for a, b in nonvoice):
                d["confidence"] = max(d["confidence"], 0.85)
                d["reason"] = "Non-speech sound (no voice detected)"

    learned = learn.apply(decisions) if s["learn"] else {}
    if learned:
        rep.log("Adjusted by your past choices: " + ", ".join(
            f"{learn.LABELS.get(k, k)} {'+' if v > 0 else ''}{v:.2f}" for k, v in learned.items()))

    if frozen_thread is not None:
        rep.set("video", frozen_box.get("p", 0.0))
        if frozen_thread.is_alive():
            rep.log("Waiting for the screen-motion scan to finish...")
        while frozen_thread.is_alive():
            frozen_thread.join(0.5)
            rep.set("video", frozen_box.get("p", 0.0))
        if "err" in frozen_box:
            rep.log(f"Screen-motion scan failed ({frozen_box['err']}); silent gaps will simply be cut")
    frozen = frozen_box.get("v")

    llm_summary = None
    if s["llm_provider"]:
        rep.set("ai")
        rep.log(f"Asking {llm.PROVIDERS.get(s['llm_provider'], {}).get('label', s['llm_provider'])} "
                "for a second opinion...")
        res = llm.review(phrases, decisions, s, log=rep.log, progress=lambda f: rep.set("ai", f))
        review_at = POLICIES.get(s["policy"], POLICIES["balanced"])[1]
        changed = llm.merge(decisions, res["verdicts"], review_at)
        llm_summary = {"provider": s["llm_provider"], "model": res.get("model"), "windows": res["windows"],
                       "notes": len(res["verdicts"]), "changed": changed, "errors": res["errors"]}
        if res["errors"]:
            rep.log(f"AI reviewer had problems on {len(res['errors'])} window(s); local analysis used there")
        rep.log(f"AI reviewer: {len(res['verdicts'])} notes, {changed} decisions adjusted")

    apply_policy(decisions, s["policy"])
    rep.set("ai", 1.0)
    return {
        "path": info["path"],
        "name": name,
        "info": info,
        "language": lang,
        "language_p": lang_p,
        "translated": translate,
        "model": os.path.basename(model),
        "levels": {"noise_floor": round(env.noise_floor, 1), "speech": round(env.speech_level, 1),
                   "threshold": round(on, 1)},
        "regions": regions,
        "phrases": [{k: p[k] for k in ("id", "start", "end", "text", "p", "flags")} for p in phrases],
        "decisions": decisions,
        "frozen": frozen,
        "llm": llm_summary,
        "learned": learned,
        "transcribe_seconds": tr.get("seconds"),
        "_env": env,
        "_words": None if translate else words_from_segments(segs),
        "_speech": {"wav": wav, "loose": loose, "voice_used": voice_used, "sens": sens, "model": s["model"]},
    }


def original_words(analysis: dict, progress=None, log=print, use_cache: bool = True) -> list:
    """Words in the spoken language with source times (Hindi script for Hindi). Cached per file."""
    if analysis.get("_words") is not None:
        return analysis["_words"]
    sp, lang = analysis["_speech"], analysis["language"]
    model = pick_model(sp["model"], translate=False, language=lang)
    tpath = os.path.join(cache_dir_for(analysis["path"]),
                         _transcript_key(model, lang, False, sp["sens"], sp["voice_used"]))
    tr = _load_json(tpath) if use_cache else None
    if not tr:
        log(f"{analysis['name']}: writing the {lang} script with {os.path.basename(model)}")
        env = analysis["_env"]
        tr = _transcribe(log, sp["wav"], sp["loose"], env.duration, env, model, lang, False, progress=progress)
        _save_json(tpath, tr)
    analysis["_words"] = words_from_segments(clean_segments(tr["raw"], analysis["regions"]))
    return analysis["_words"]


def plan_for_sources(sources: list, analyses: dict, settings: dict) -> dict:
    """
    sources: ordered timeline items [{"path", "fps", "start_sec", "end_sec"}]
             (for a plain file: one item covering the whole file)
    analyses: path -> analysis from analyze_file()
    """
    s = dict(DEFAULT_SETTINGS)
    s.update(settings or {})
    plan, removed = [], []
    totals = {"source_seconds": 0.0, "kept_seconds": 0.0, "review_seconds": 0.0, "removed_takes": 0,
              "review_clips": 0, "clips": 0}
    for src in sources:
        a = analyses[src["path"]]
        apply_policy(a["decisions"], s["policy"])
        env = a["_env"]
        rng = (max(0.0, src.get("start_sec", 0.0)), min(env.duration, src.get("end_sec") or env.duration))
        res = build_clips(a["decisions"], a["regions"], env,
                          {k: s[k] for k in ("max_pause", "head_pad", "tail_pad")},
                          source_range=rng, frozen=a.get("frozen"), screen_mode=s["footage"] == "screen")
        fps = src.get("fps") or a["info"]["fps"] or 25.0
        for c in to_frames(res["clips"], fps):
            c.update(path=src["path"], src_fps=fps, source_name=os.path.basename(src["path"]))
            plan.append(c)
        for r in res["removed"]:
            r["source_name"] = os.path.basename(src["path"])
            removed.append(r)
        for k in totals:
            totals[k] += res["stats"][k]
    totals = {k: round(v, 2) if isinstance(v, float) else v for k, v in totals.items()}
    return {"plan": plan, "removed": removed, "stats": totals}
