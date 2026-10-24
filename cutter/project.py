"""
The editing engine. A Project carries one video (or Resolve timeline) through
a list of steps. Each step adds its result to the project; when the creator
should look at that result before going on, the step raises a flag. Nothing
here depends on the web page, so the CLI or a future automation drives the
same steps.

    cut      find retakes and long pauses      flag: brown parts to check
             (with a pasted script: keep the last good take of every script line)
    words    words in the spoken language      runs in the background when the cut did not read them
    (later)  captions, B-roll images, generated shots, music: one Step each

All outputs (Resolve timeline, Premiere / Final Cut XML, EDL, transcript) read
the edit document from Project.edit().
"""
from __future__ import annotations

import os
import traceback
import uuid
from bisect import bisect_left

from . import edit as edit_doc
from . import learn, pipeline, resolve_bridge, scriptmatch
from .analyze import POLICIES
from .translit import has_devanagari, to_hinglish

ACTIONS = ("keep", "review", "remove")


class Step:
    """One stage of the engine. A subclass sets id/title and implements run()."""

    id = ""
    title = ""
    background = False  # True: the review is usable while this step still runs

    def __init__(self, project: "Project"):
        self.project = project
        self.state = "waiting"  # waiting | running | done | failed | skipped
        self.progress = 0.0
        self.error = ""

    def run(self):
        raise NotImplementedError

    def flag(self) -> str:
        """Short note when the creator should check this step's result ("" = nothing to check)."""
        return ""

    def public(self) -> dict:
        return {"id": self.id, "title": self.title, "state": self.state, "progress": round(self.progress, 3),
                "flag": self.flag() if self.state == "done" else "", "error": self.error}


class CutStep(Step):
    id, title = "cut", "Cut retakes"

    def run(self):
        p = self.project
        if p.source == "resolve":
            p.context, items = resolve_bridge.timeline_sources(p.track)
            if not items:
                raise RuntimeError(f"No clips with media on video track {p.track} of the open timeline.")
            p.timeline_name = p.context.get("timeline") or "Timeline"
            p.rep.log(f"Timeline '{p.timeline_name}': {len(items)} clip(s) on V{p.track}")
            p.sources = items
        else:
            path = os.path.abspath(os.path.expanduser(p.path.strip().strip("'\"")))
            if not os.path.isfile(path):
                raise RuntimeError(f"File not found: {path}")
            p.timeline_name = os.path.splitext(os.path.basename(path))[0]
            p.sources = [{"path": path, "fps": 0.0, "start_sec": 0.0, "end_sec": None}]
        files = list(dict.fromkeys(s["path"] for s in p.sources))
        p.rep.file_count = len(files)
        for i, path in enumerate(files):
            p.rep.file_index = i
            p.analyses[path] = pipeline.analyze_file(path, p.settings, p.rep, use_cache=p.use_cache)
        for s in p.sources:
            s["fps"] = s.get("fps") or p.analyses[s["path"]]["info"]["fps"] or 25.0
        if p.settings.get("script", "").strip():
            p.script = scriptmatch.apply(p.sources, p.analyses, p.settings["script"])
            p.rep.log(f"Script: {p.script['found']} of {p.script['lines']} lines found in the recording")
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
    """Words in the spoken language (Hindi letters / Hinglish in the review, the final transcript),
    when the cut step did not already read them."""

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


class Project:
    """One analysis: a Resolve timeline or a single file, plus the creator's choices."""

    def __init__(self, source: str, path: str = "", track: int = 1, settings: dict = None, use_cache: bool = True):
        self.id = uuid.uuid4().hex[:10]
        self.source, self.path, self.track, self.use_cache = source, path, track, use_cache
        self.settings = dict(pipeline.DEFAULT_SETTINGS)
        self.settings.update({k: v for k, v in (settings or {}).items() if v is not None})
        self.state, self.error = "running", ""
        self.rep = pipeline.Reporter()
        self.context, self.timeline_name = {}, ""
        self.sources, self.analyses, self.result = [], {}, None
        self.script = {}  # summary of script mode: lines found / missing
        self.steps = [cls(self) for cls in STEPS]
        self.approved = False  # the creator sent the cut somewhere: the cut step's flag is cleared
        self.builds, self.pending_plan, self.recorded, self.outputs = [], None, {}, []

    def step(self, step_id: str) -> Step:
        return next(s for s in self.steps if s.id == step_id)

    def public(self) -> dict:
        return {"id": self.id, "state": self.state, "error": self.error, "progress": round(self.rep.overall(), 4),
                "stage": self.rep.stage, "logs": self.rep.logs[-60:], "steps": [s.public() for s in self.steps],
                "stats": self.result["stats"] if self.result else None, "builds": self.builds,
                "approved": self.approved}

    # ----------------------------------------------------------------- running
    def run(self, only: tuple = None):
        """Run the steps in order. state becomes "done" once the foreground steps finish."""
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
        self.result = pipeline.plan_for_sources(self.sources, self.analyses, self.settings)

    def decisions(self):
        return [d for a in self.analyses.values() for d in a["decisions"]]

    # ----------------------------------------------------------------- choices
    def set_decision(self, path: str, decision_id: str, action):
        analysis = self.analyses.get(path)
        if not analysis:
            raise RuntimeError("unknown source")
        for d in analysis["decisions"]:
            if d["id"] == decision_id:
                d["user_action"] = action if action in ACTIONS else None
        self.replan()

    def bulk(self, from_action: str, action):
        """e.g. bulk("review", "remove"): every brown line at once."""
        for d in self.decisions():
            if d.get("action") == from_action and not d.get("user_action"):
                d["user_action"] = action if action in ACTIONS else None
        self.replan()

    def change_settings(self, values: dict):
        if values.get("policy") in POLICIES:
            self.settings["policy"] = values["policy"]
        for key in ("max_pause", "head_pad", "tail_pad"):
            if key in values:
                self.settings[key] = float(values[key])
        self.replan()

    def learn_from_choices(self) -> int:
        """Count the creator's overrides so the next analysis matches their taste."""
        remove_at, review_at = POLICIES.get(self.settings["policy"], POLICIES["balanced"])

        def tool_action(d):
            c = d["confidence"]
            action = "remove" if c >= remove_at else ("review" if c >= review_at else "keep")
            return "review" if action == "remove" and d.get("llm_only") else action

        fresh = [d for d in self.decisions() if d.get("user_action") and not d.get("script")
                 and self.recorded.get(d["id"]) != d["user_action"]]
        taught = learn.record(fresh, tool_action)
        self.recorded.update({d["id"]: d["user_action"] for d in fresh})
        return taught

    # ----------------------------------------------------------------- outputs
    def timeline_fps(self) -> float:
        if self.source == "resolve":
            return float(self.context.get("timeline_fps") or 0.0)
        return 0.0  # a single file: the file's own rate

    def edit(self, name: str = "") -> dict:
        words = {p: a.get("_words") for p, a in self.analyses.items()}
        return edit_doc.build(name or f"{self.timeline_name} - AI Cut", self.result["plan"], self.analyses,
                              self.timeline_fps(), words)

    def output_base(self, name: str = "") -> str:
        """Where files go: next to the (first) source video."""
        safe = "".join(ch for ch in (name or f"{self.timeline_name} - AI Cut") if ch not in '<>:"/\\|?*').strip()
        return os.path.join(os.path.dirname(next(iter(self.analyses))), safe or "AI Cut")

    # ----------------------------------------------------------------- review page
    def _texts(self, analysis: dict) -> dict:
        """Decision id -> text in the spoken language (+ Hinglish), from the original-language words."""
        words = analysis.get("_words")
        if words is None:
            return {}
        if analysis.get("_texts_for") is not words:
            starts = [w["start"] for w in words]
            out = {}
            for d in analysis["decisions"]:
                picked = []
                for w in words[max(0, bisect_left(starts, d["start"] - 5.0)):]:
                    mid = (w["start"] + w["end"]) / 2
                    if mid >= d["end"]:
                        break
                    if mid >= d["start"]:
                        picked.append(w["w"])
                text = " ".join(picked) if analysis["translated"] else d.get("text", "")
                out[d["id"]] = {"orig": text, "hinglish": to_hinglish(text)}
            analysis["_texts"], analysis["_texts_for"] = out, words
        return analysis["_texts"]

    def review_payload(self) -> dict:
        files = []
        for path, a in self.analyses.items():
            ranges = [(s.get("start_sec", 0.0), s.get("end_sec") or a["info"]["duration"])
                      for s in self.sources if s["path"] == path]
            texts = self._texts(a)
            decisions = []
            for d in a["decisions"]:
                if d["kind"] in ("filler", "restart", "script") and d["action"] == "keep" and not d.get("user_action"):
                    continue
                if not any(d["end"] > r0 and d["start"] < r1 for r0, r1 in ranges):
                    continue
                row = {k: d.get(k) for k in ("id", "start", "end", "kind", "text", "confidence", "reason", "ref",
                                             "action", "user_action", "llm_only", "learned")}
                row.update(texts.get(d["id"], {}))
                decisions.append(row)
            decisions.sort(key=lambda d: (d["start"], d["kind"] != "phrase"))
            views = []
            if any(has_devanagari(t["orig"]) for t in texts.values()):
                views += ["original", "hinglish"]
            elif texts and a["translated"]:
                views += ["original"]
            if a["translated"]:
                views.append("english")
            files.append({"path": path, "name": a["name"], "language": a["language"], "translated": a["translated"],
                          "has_video": a["info"]["has_video"], "model": a["model"], "llm": a.get("llm"),
                          "views": views, "decisions": decisions})
        return {"job": self.public(), "files": files, "timeline_name": self.timeline_name, "learned": learn.summary(),
                "script": self.script}
