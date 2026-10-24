"""
The editing engine. A Project carries one video (or Resolve timeline) through
a list of steps. Each step adds its result to the project; when the creator
should look at that result before going on, the step raises a flag ("3 to
check"), which the page shows as ⚑. Nothing here depends on the web page, so
the command line or a future automation drives the same steps.

    cut      find retakes and long pauses      flag: brown parts to check
             (with a pasted script: keep the last good take of every script line)
    words    words in the spoken language      runs in the background, only when the cut did not read them
    (later)  captions, B-roll images, generated shots, music: one Step subclass each

Every output (Resolve timeline, MP4, Premiere / Final Cut XML, EDL,
transcript) reads the edit document from Project.edit().
"""
from __future__ import annotations

import copy
import os
import traceback
import uuid
from bisect import bisect_left

from . import edit as edit_doc
from . import learn, paths, pipeline, resolve_bridge, scriptmatch
from .analyze import POLICIES
from .translit import has_devanagari, to_hinglish

ACTIONS = ("keep", "review", "remove")  # what a creator can choose for a line ("review" = brown)


class Step:
    """One stage of the engine. A subclass sets id / title and implements run()."""

    id = ""
    title = ""
    background = False  # True: the review page opens while this step still runs

    def __init__(self, project: "Project"):
        self.project = project
        self.state = "waiting"  # waiting | running | done | failed | skipped
        self.progress = 0.0
        self.error = ""

    def run(self):
        raise NotImplementedError

    def flag(self) -> str:
        """A short note when the creator should check this step's result ("" = nothing to check)."""
        return ""

    def public(self) -> dict:
        """What the page shows for this step."""
        return {"id": self.id, "title": self.title, "state": self.state, "progress": round(self.progress, 3),
                "flag": self.flag() if self.state == "done" else "", "error": self.error}


class CutStep(Step):
    """Read the sources, analyse every file, and plan the cut."""

    id, title = "cut", "Cut retakes"

    def run(self):
        p = self.project
        if p.source == "resolve":
            p.context, items = resolve_bridge.timeline_sources(p.track)
            if not items:
                raise RuntimeError(f"No clips with media on video track {p.track} of the open timeline. "
                                   "Is the media offline, or are the clips on another track?")
            p.timeline_name = p.context.get("timeline") or "Timeline"
            p.rep.log(f"Timeline '{p.timeline_name}': {len(items)} clip(s) on V{p.track}")
            if p.context.get("offline"):
                p.rep.log(f"{p.context['offline']} clip(s) left out: their media is offline")
            p.sources = items
        else:
            path = os.path.abspath(os.path.expanduser(p.path.strip().strip("'\"")))
            if not os.path.isfile(path):
                raise RuntimeError(f"Can't find the video at {path}. Check the path, or use Choose video…")
            p.timeline_name = os.path.splitext(os.path.basename(path))[0]
            p.sources = [{"path": path, "fps": 0.0, "start_sec": 0.0, "end_sec": None}]

        files = list(dict.fromkeys(s["path"] for s in p.sources))  # each file once, in timeline order
        p.rep.file_count = len(files)
        for i, path in enumerate(files):
            p.rep.file_index = i
            p.analyses[path] = pipeline.analyze_file(path, p.settings, p.rep, use_cache=p.use_cache)
        if all(a.get("silent") for a in p.analyses.values()):
            raise RuntimeError("This video has no sound, so there is nothing to listen to and cut.")
        for a in p.analyses.values():
            if a.get("silent"):
                p.rep.log(f"{a['name']}: nothing to listen to, so it stays in the cut as it is")
            elif not a.get("background_only") and not a["phrases"]:
                p.rep.log(f"{a['name']}: no speech found, so it stays in the cut as it is")
        for s in p.sources:
            s["fps"] = s.get("fps") or p.analyses[s["path"]]["info"]["fps"] or 25.0

        if p.settings.get("script", "").strip():
            p.script = scriptmatch.apply(p.sources, p.analyses, p.settings["script"])
            if p.script:
                p.rep.log(f"Script: {p.script['found']} of {p.script['lines']} lines found in the recording")
            for name in p.script.get("unread", []):
                p.rep.log(f"{name}: its words could not be read, so the script was not used for it")
        p.replan()
        p.rep.file_index = p.rep.file_count
        st = p.result["stats"]
        p.rep.log(f"Done: {st['source_seconds'] / 60:.1f} min -> {st['kept_seconds'] / 60:.1f} min, "
                  f"{st['removed_takes']} takes removed, {st['review_clips']} clips to review")

    def flag(self) -> str:
        p = self.project
        n = p.result["stats"]["review_clips"] if p.result else 0
        return f"{n} to check" if n and not p.approved else ""


class WordsStep(Step):
    """
    Words in the spoken language (Hindi letters / Hinglish in the review, the
    transcript), for files whose analysis did not read them already. Normally
    skipped: analyze_file() reads them itself unless "use_words" is off.
    """

    id, title, background = "words", "Read the words", True

    def run(self):
        p = self.project
        todo = [a for a in p.analyses.values() if a.get("_words") is None]
        if not todo:
            self.state = "skipped"
            return
        for i, a in enumerate(todo):
            pipeline.original_words(a, progress=lambda f, i=i: setattr(self, "progress", (i + f) / len(todo)),
                                    log=p.rep.log, use_cache=p.use_cache)
        p.rep.log("Words ready")


STEPS = [CutStep, WordsStep]


def _words_in(words: list, start: float, end: float, starts: list = None) -> list:
    """The timed words whose middle lies inside start..end (starts: the words' start times, if known)."""
    starts = starts if starts is not None else [w["start"] for w in words]
    picked = []
    for w in words[max(0, bisect_left(starts, start - 5.0)):]:
        mid = (w["start"] + w["end"]) / 2
        if mid >= end:
            break
        if mid >= start:
            picked.append(w)
    return picked


class Project:
    """One analysis: a Resolve timeline or a single file, plus the creator's choices."""

    def __init__(self, source: str, path: str = "", track: int = 1, settings: dict = None, use_cache: bool = True):
        self.id = uuid.uuid4().hex[:10]
        self.source, self.path, self.track, self.use_cache = source, path, track, use_cache  # source: resolve | file
        self.settings = dict(pipeline.DEFAULT_SETTINGS)
        self.settings.update({k: v for k, v in (settings or {}).items() if v is not None})
        self.state, self.error = "running", ""  # running | done | error
        self.rep = pipeline.Reporter()
        self.context, self.timeline_name = {}, ""  # context: what Resolve told us about the open timeline
        self.sources, self.analyses, self.result = [], {}, None
        self.script = {}  # script mode summary: lines, found, missing
        self.steps = [cls(self) for cls in STEPS]
        self.approved = False  # the creator exported the cut, so the cut step's flag is cleared
        self.builds, self.pending_plan, self.recorded, self.outputs = [], None, {}, []
        self.render = {"state": "idle"}  # the MP4 being made: state, progress, file, error
        self.history = []  # the creator's edits (choices, splits), newest last: what undo takes back

    def step(self, step_id: str) -> Step:
        return next(s for s in self.steps if s.id == step_id)

    def public(self) -> dict:
        """Progress and results for the page (polled while the analysis runs)."""
        return {"id": self.id, "state": self.state, "error": self.error, "progress": round(self.rep.overall(), 4),
                "stage": self.rep.stage, "logs": self.rep.logs[-60:], "steps": [s.public() for s in self.steps],
                "stats": self.result["stats"] if self.result else None, "builds": self.builds,
                "approved": self.approved}

    # ----------------------------------------------------------------- running
    def run(self, only: tuple = None):
        """
        Run the steps in order (only: the step ids to run). state becomes "done"
        when the first background step starts, so the review opens right away.
        A failing background step is reported but never breaks the cut.
        """
        for s in self.steps:
            if only and s.id not in only:
                continue
            if s.background and self.state == "running":
                self.state = "done"
            s.state = "running"
            try:
                s.run()
                if s.state == "running":
                    s.state, s.progress = "done", 1.0
            except Exception as e:  # shown in the page
                s.state, s.error = "failed", str(e)
                traceback.print_exc()
                if not s.background:
                    self.error = str(e)
                    self.rep.log("ERROR: " + str(e))
                    self.state = "error"
                    return
                self.rep.log(f"{s.title} failed: {e}")
        if self.state == "running":
            self.state = "done"

    def replan(self):
        """Re-cut from the cached analysis (instant): after a choice, a policy or a padding change."""
        self.result = pipeline.plan_for_sources(self.sources, self.analyses, self.settings)

    def decisions(self) -> list:
        return [d for a in self.analyses.values() for d in a["decisions"]]

    # ----------------------------------------------------------------- the creator's choices
    def set_decision(self, path: str, decision_id: str, action):
        """Keep / review / cut one line; None goes back to ScriptCutter's own choice."""
        analysis = self.analyses.get(path)
        if not analysis:
            raise RuntimeError("unknown source")
        self._remember([path])
        for d in analysis["decisions"]:
            if d["id"] == decision_id:
                d["user_action"] = action if action in ACTIONS else None
        self.replan()

    def bulk(self, from_action: str, action):
        """E.g. bulk("review", "remove"): every brown line at once (lines set by hand are left alone)."""
        self._remember(list(self.analyses))
        for d in self.decisions():
            if d.get("action") == from_action and not d.get("user_action"):
                d["user_action"] = action if action in ACTIONS else None
        self.replan()

    def split(self, path: str, decision_id: str, word: int) -> str:
        """
        Split a line in two before its word number `word` (counted in the review's words of
        the line), like pressing Enter in a text, so each half can be kept or cut on its own.
        Both halves start with the line's choice; the split point stays exactly where it was
        put (cuts.py does not pad or move it). Returns the second half's id.
        """
        analysis = self.analyses.get(path)
        d = next((x for x in analysis["decisions"] if x["id"] == decision_id), None) if analysis else None
        if not d:
            raise RuntimeError("This line is no longer in the list.")
        words = _words_in(analysis.get("_words") or [], d["start"], d["end"])
        if not 0 < word < len(words):
            raise RuntimeError("Put the cursor between two words of the line to split it there.")
        before, after = words[word - 1], words[word]
        at = (before["end"] + after["start"]) / 2 if before["end"] <= after["start"] else after["start"]
        at = min(max(at, d["start"] + 0.02), d["end"] - 0.02)
        self._remember([path])
        first = dict(d, id=f"{d['id']}-1", end=at, text=" ".join(w["w"] for w in words[:word]), fixed_end=True)
        second = dict(d, id=f"{d['id']}-2", start=at, text=" ".join(w["w"] for w in words[word:]), fixed_start=True)
        decisions = analysis["decisions"]
        i = decisions.index(d)
        decisions[i:i + 1] = [first, second]
        for x in decisions:  # lines inside it and links to it follow the half they belong to
            if x.get("parent") == d["id"]:
                x["parent"] = first["id"] if (x["start"] + x["end"]) / 2 < at else second["id"]
            if x.get("ref") == d["id"]:
                x["ref"] = first["id"]
        analysis["_texts_for"] = None
        self.replan()
        return second["id"]

    def undo(self):
        """Take back the creator's last edit: a Keep / Review / Cut choice, a bulk change or a split."""
        if not self.history:
            raise RuntimeError("Nothing to undo.")
        for path, decisions in self.history.pop().items():
            self.analyses[path]["decisions"] = decisions
            self.analyses[path]["_texts_for"] = None
        self.replan()

    def _remember(self, files: list):
        """Save the lines of these files as they are now, for undo (the last 100 edits)."""
        self.history.append({p: copy.deepcopy(self.analyses[p]["decisions"]) for p in files if p in self.analyses})
        del self.history[:-100]

    def change_settings(self, values: dict):
        """Policy and padding from the review page; anything else needs a new analysis."""
        if values.get("policy") in POLICIES:
            self.settings["policy"] = values["policy"]
        for key in ("max_pause", "head_pad", "tail_pad"):
            if key in values:
                self.settings[key] = float(values[key])
        self.replan()

    def learn_from_choices(self) -> int:
        """Count the creator's overrides (once each) so the next analysis matches their taste (learn.py)."""
        remove_at, review_at = POLICIES.get(self.settings["policy"], POLICIES["balanced"])

        def tool_action(d):  # what ScriptCutter would have done without the creator
            c = d["confidence"]
            action = "remove" if c >= remove_at else ("review" if c >= review_at else "keep")
            return "review" if action == "remove" and d.get("llm_only") else action

        # script-mode lines follow the creator's script, not ScriptCutter's taste: nothing to learn from them
        fresh = [d for d in self.decisions() if d.get("user_action") and not d.get("script")
                 and self.recorded.get(d["id"]) != d["user_action"]]
        taught = learn.record(fresh, tool_action)
        self.recorded.update({d["id"]: d["user_action"] for d in fresh})
        return taught

    # ----------------------------------------------------------------- outputs
    def timeline_fps(self) -> float:
        """Rate of the new timeline: the Resolve timeline's own rate, or (0) the video file's rate."""
        if self.source == "resolve":
            return float(self.context.get("timeline_fps") or 0.0)
        return 0.0

    def edit(self, name: str = "") -> dict:
        """The edit document of the current cut (cutter/edit.py)."""
        words = {p: a.get("_words") for p, a in self.analyses.items()}
        return edit_doc.build(name or f"{self.timeline_name} - AI Cut", self.result["plan"], self.analyses,
                              self.timeline_fps(), words)

    def require_clips(self):
        """Exports need something to export."""
        if not self.result or not self.result["plan"]:
            raise RuntimeError("Nothing to export: every part is cut. Keep some parts first.")

    def file_name(self, name: str) -> str:
        """The cut's name as a file name every system accepts (Windows refuses <>:"/\\|?* and a final dot)."""
        safe = "".join(ch for ch in (name or f"{self.timeline_name} - AI Cut") if ch not in '<>:"/\\|?*' and ch >= " ")
        return safe.strip().rstrip(". ") or "AI Cut"

    def output_path(self, name: str, ending: str) -> str:
        """
        A new file next to the (first) source video, or in Movies / Videos > ScriptCutter
        when that folder can't be written to (a camera card, a read-only drive).
        Never overwrites: a second export of the same cut becomes "... 2", "... 3".
        """
        safe = self.file_name(name)
        folder = os.path.dirname(next(iter(self.analyses)))
        if not os.access(folder, os.W_OK):
            folder = paths.output_dir()
        path, n = os.path.join(folder, safe + ending), 2
        while os.path.exists(path):
            path, n = os.path.join(folder, f"{safe} {n}{ending}"), n + 1
        return path

    # ----------------------------------------------------------------- review page
    def _texts(self, analysis: dict) -> dict:
        """
        Decision id -> its words in the spoken language (+ Hinglish), from the timed
        words: every word whose middle lies inside the decision. Cached until the
        words change (they arrive later when the words step runs in the background).
        """
        words = analysis.get("_words")
        if words is None:
            return {}
        if analysis.get("_texts_for") is not words:
            starts = [w["start"] for w in words]
            out = {}
            for d in analysis["decisions"]:
                picked = _words_in(words, d["start"], d["end"], starts)
                text = " ".join(w["w"] for w in picked) if analysis["translated"] else d.get("text", "")
                out[d["id"]] = {"orig": text, "hinglish": to_hinglish(text),
                                # each word with its times: the review's text cursor splits a line between two
                                "words": [[round(w["start"], 3), round(w["end"], 3), w["w"], to_hinglish(w["w"])]
                                          for w in picked]}
            analysis["_texts"], analysis["_texts_for"] = out, words
        return analysis["_texts"]

    def review_payload(self) -> dict:
        """Everything the review list needs: every decision in the timeline's ranges, per file."""
        files = []
        for path, a in self.analyses.items():
            ranges = [(s.get("start_sec", 0.0), s.get("end_sec") or a["info"]["duration"])
                      for s in self.sources if s["path"] == path]
            texts = self._texts(a)
            decisions = []
            for d in a["decisions"]:
                # word-level items ScriptCutter keeps anyway would only clutter the list
                if d["kind"] in ("filler", "restart", "script") and d["action"] == "keep" and not d.get("user_action"):
                    continue
                if not any(d["end"] > r0 and d["start"] < r1 for r0, r1 in ranges):
                    continue
                row = {k: d.get(k) for k in ("id", "start", "end", "kind", "text", "confidence", "reason", "ref",
                                             "action", "user_action", "llm_only", "learned")}
                row.update(texts.get(d["id"], {}))
                decisions.append(row)
            decisions.sort(key=lambda d: (d["start"], d["kind"] != "phrase"))
            # which word views the हिंदी / Hinglish / English switch offers for this file
            views = []
            if any(has_devanagari(t["orig"]) for t in texts.values()):
                views += ["original", "hinglish"]
            elif texts and a["translated"]:
                views += ["original"]
            if a["translated"]:
                views.append("english")
            files.append({"path": path, "name": a["name"], "language": a["language"], "translated": a["translated"],
                          "has_video": a["info"]["has_video"], "model": a["model"], "llm": a.get("llm"),
                          "silent": bool(a.get("silent")), "speechless": not a.get("silent") and not a["phrases"],
                          "views": views, "decisions": decisions})
        return {"job": self.public(), "files": files, "timeline_name": self.timeline_name, "learned": learn.summary(),
                "script": self.script}
