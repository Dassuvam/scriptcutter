"""
Source file -> analysis -> clip plan. The heart of the engine.

analyze_file() runs once per source file, in this order:

    probe            what is in the file (media.py)
    audio            16 kHz mono WAV (cached)
    levels           loudness envelope -> where any sound is (energy.py)
    voice            Silero: which of that sound is a human voice (vad.py, optional)
    language         Whisper's guess, three samples vote (cached)
    transcribe       Whisper in <= 24 s chunks (cached)
    words            the spoken-language words, when the main transcript is a translation (cached)
    analyse          retakes, restarts, fillers -> decisions with a confidence (analyze.py, restarts.py)
    video            screen recordings: where the picture moves (in a thread, while Whisper runs)
    ai               optional second opinion (llm.py)

plan_for_sources() then turns the decisions into clips for each piece of the
timeline. It is cheap, so changing the policy, padding or a Keep / Cut choice
re-cuts instantly from the cached analysis.

Two transcripts can exist per file: the one retakes are matched on (for
non-English speech usually Whisper's English translation) and the timed words
in the spoken language. Those words find restarts inside Whisper's sentences
(restarts.py) and are what the review and the final transcript show. When the
analysis runs in the spoken language they are the same.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from bisect import bisect_left

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
    "use_words": True,          # translated analysis: also read the spoken-language words (restarts, Hindi text)
    "script": "",               # the creator's script: keep the last good take of every line (scriptmatch.py)
}

# one Whisper at a time: two large models at once can run a laptop out of memory
WHISPER_LOCK = threading.Lock()
STEADY_DB = 12.0  # loudness range below this, with no voice found, means background sound only


class Reporter:
    """
    Collects log lines and the current stage for the progress bar. Stage weights
    are rough shares of the total time, so the bar moves evenly on long files.
    """

    STAGES = [("audio", 4), ("levels", 2), ("language", 4), ("transcribe", 45), ("words", 25), ("analyse", 4),
              ("video", 6), ("ai", 10)]

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
    """A cached result, or None when it is missing or unreadable (then it is simply computed again)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_json(path, data):
    """Write through a temporary file, so an interrupted run never leaves a broken cache file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _transcript_key(model: str, lang: str, translate: bool, sens: float, voice_used: bool) -> str:
    """Cache file name: a transcript depends on the model, language, mode, sensitivity and voice detection."""
    return (f"transcript_{os.path.basename(model)}_{lang}_{'en' if translate else 'orig'}_{sens:+.0f}"
            f"{'_vad' if voice_used else ''}.json")


def _transcribe(log, *args, **kwargs) -> dict:
    """transcribe() under WHISPER_LOCK, timed."""
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


def _spoken_words(path: str, lang: str, model_request: str, sens: float, voice_used: bool, wav: str, loose: list,
                  env, regions: list, progress=None, log=print, use_cache: bool = True) -> list:
    """Transcribe in the spoken language (no translation) and return the timed words. Cached."""
    model = pick_model(model_request, translate=False, language=lang)
    tpath = os.path.join(cache_dir_for(path), _transcript_key(model, lang, False, sens, voice_used))
    tr = _load_json(tpath) if use_cache else None
    if not tr:
        log(f"{os.path.basename(path)}: reading the {lang} words with {os.path.basename(model)}")
        tr = _transcribe(log, wav, loose, env.duration, env, model, lang, False, progress=progress)
        _save_json(tpath, tr)
    return words_from_segments(clean_segments(tr["raw"], regions))


def silent_analysis(info: dict) -> dict:
    """
    The analysis of a file without a sound track (a title card, a screen recording
    without a mic): nothing to listen to, so plan_for_sources() keeps it whole.
    """
    return {"path": info["path"], "name": os.path.basename(info["path"]), "info": info, "silent": True,
            "language": "", "language_p": 0.0, "translated": False, "model": "", "levels": None, "regions": [],
            "phrases": [], "decisions": [], "frozen": None, "llm": None, "learned": {}, "transcribe_seconds": None,
            "_env": None, "_words": [], "_speech": None}


def analyze_file(path: str, settings: dict, rep: Reporter = None, use_cache: bool = True) -> dict:
    """Analyse one source file (see the module docstring for the stages). Settings: DEFAULT_SETTINGS."""
    s = dict(DEFAULT_SETTINGS)
    s.update({k: v for k, v in (settings or {}).items() if v is not None})
    rep = rep or Reporter()
    name = os.path.basename(path)

    rep.set("audio")
    info = probe(path)
    if not info["audio_streams"]:
        rep.log(f"{name}: no sound track")
        return silent_analysis(info)
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

    # neural voice detection: keeps typing / clicks / music away from Whisper. Optional: a failure
    # only means the loudness envelope alone decides what is speech
    voice_used = False
    nonvoice = []
    voice = None
    if s["use_vad"] and vad.available():
        vpath = os.path.join(cdir, "voice.json")
        voice = _load_json(vpath) if use_cache else None
        if voice is None:
            try:
                voice = vad.voice_segments(wav)
                _save_json(vpath, voice)
            except (RuntimeError, OSError, subprocess.SubprocessError) as e:
                rep.log(f"Voice detector did not work ({e}); using loudness only")
    if voice is not None:
        sound_total = sum(b - a for a, b in loose) or 1.0
        if sum(b - a for a, b in voice) > 0.15 * sound_total:
            loose, _ = vad.intersect(loose, voice)
            regions, nonvoice = vad.intersect(regions, voice)
            voice_used = True
            rep.log(f"Voice detector: {sum(b - a for a, b in regions) / 60:.1f} min of actual speech, "
                    f"{sum(b - a for a, b in nonvoice) / 60:.1f} min of other sounds (typing, music, noise)")
        elif env.speech_level - env.noise_floor < STEADY_DB:
            # no voice, and the sound hardly changes (room tone, fan, music bed): Whisper would only
            # invent words here ("Thank you.", counting), and invented repeats could cut B-roll
            rep.log(f"{name}: only background sound, no speech, so it stays in the cut as it is")
            quiet = silent_analysis(info)
            quiet.update(silent=False, background_only=True, regions=regions, _env=env, levels={
                "noise_floor": round(env.noise_floor, 1), "speech": round(env.speech_level, 1), "threshold": round(on, 1)})
            return quiet
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

    # timed words in the spoken language: restarts inside Whisper's sentences, the review, the transcript
    words = None if translate else words_from_segments(segs)
    if translate and s["use_words"]:
        rep.set("words")
        try:
            words = _spoken_words(path, lang, s["model"], sens, voice_used, wav, loose, env, regions,
                                  progress=lambda f: rep.set("words", f), log=rep.log, use_cache=use_cache)
        except Exception as e:  # never fatal: the cut still works from the translation
            rep.log(f"Could not read the {lang} words ({e}); restarts inside sentences are not checked")

    # analysis
    rep.set("analyse")
    phrases = build_phrases(segs)
    if translate and words:
        starts = [(w["start"] + w["end"]) / 2 for w in words]
        for p in phrases:
            p["words"] = words[bisect_left(starts, p["start"]):bisect_left(starts, p["end"])]
    if s["footage"] == "screen":
        untranscribed = []
    else:
        untranscribed = find_untranscribed(regions, phrases)
        # non-voice sounds (claps, coughs, bumps) are noise, never words
        untranscribed += [(a, b) for a, b in nonvoice if b - a >= 0.15 and
                          not any(p["start"] - 0.2 < b and p["end"] + 0.2 > a for p in phrases)]
        untranscribed.sort()
    decisions = analyze(phrases, untranscribed, translate_mode=translate, regions=regions, words=words)
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
        "_words": words,
        "_speech": {"wav": wav, "loose": loose, "voice_used": voice_used, "sens": sens, "model": s["model"]},
    }


def original_words(analysis: dict, progress=None, log=print, use_cache: bool = True) -> list:
    """
    Words in the spoken language with source times (Hindi letters for Hindi), for an
    analysis that did not read them yet (use_words switched off). Cached per file.
    """
    if analysis.get("_words") is not None:
        return analysis["_words"]
    sp = analysis["_speech"]
    analysis["_words"] = _spoken_words(analysis["path"], analysis["language"], sp["model"], sp["sens"],
                                       sp["voice_used"], sp["wav"], sp["loose"], analysis["_env"], analysis["regions"],
                                       progress=progress, log=log, use_cache=use_cache)
    return analysis["_words"]


def plan_for_sources(sources: list, analyses: dict, settings: dict) -> dict:
    """
    The cut: every kept or brown clip, in timeline order, in source frames.

    sources:  the pieces of the timeline in order [{"path", "fps", "start_sec", "end_sec"}]
              (a plain file is one piece covering the whole file)
    analyses: path -> analysis from analyze_file()
    Returns {"plan": [clip...], "removed": [cut item...], "stats": {...}} for the page and the exporters.
    """
    s = dict(DEFAULT_SETTINGS)
    s.update(settings or {})
    plan, removed = [], []
    totals = {"source_seconds": 0.0, "kept_seconds": 0.0, "review_seconds": 0.0, "removed_takes": 0,
              "review_clips": 0, "clips": 0}
    for src in sources:
        a = analyses[src["path"]]
        fps = src.get("fps") or a["info"]["fps"] or 25.0
        length = a["_env"].duration if a["_env"] else a["info"]["duration"]
        rng = (max(0.0, src.get("start_sec", 0.0)), min(length, src.get("end_sec") or length))
        if a.get("silent") or not a["phrases"]:
            # no sound, or sound without speech (B-roll, music): nothing to judge, so it stays whole
            whole = {"start": rng[0], "end": rng[1], "status": "keep", "ids": [], "reasons": [], "text": ""}
            res = {"clips": [whole], "removed": [], "stats": {
                "source_seconds": rng[1] - rng[0], "kept_seconds": rng[1] - rng[0], "review_seconds": 0.0,
                "removed_takes": 0, "review_clips": 0, "clips": 1}}
        else:
            apply_policy(a["decisions"], s["policy"])
            res = build_clips(a["decisions"], a["regions"], a["_env"],
                              {k: s[k] for k in ("max_pause", "head_pad", "tail_pad")},
                              source_range=rng, frozen=a.get("frozen"), screen_mode=s["footage"] == "screen")
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
