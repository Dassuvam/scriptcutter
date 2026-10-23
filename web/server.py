"""
ScriptCutter's local web app: http://127.0.0.1:7870

Analyses run in background threads; the page polls /api/job/<id> for progress
and then edits decisions, which re-cuts instantly from the cached analysis.
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import traceback
import urllib.parse
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cutter import __version__, learn, llm, paths, pipeline, resolve_bridge  # noqa: E402
from cutter.analyze import POLICIES  # noqa: E402
from cutter.export import export_edl, export_report  # noqa: E402
from cutter.media import cache_dir_for  # noqa: E402
from cutter.transcribe import find_whisper_cli, installed_models  # noqa: E402

PORT = 7870
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
ACTIONS = ("keep", "review", "remove")

JOBS = {}
JOBS_LOCK = threading.Lock()
BUILD_LOCK = threading.Lock()


class Job:
    """One analysis: a Resolve timeline or a single file, plus the user's edits."""

    def __init__(self, source: str, path: str, track: int, settings: dict):
        self.id = uuid.uuid4().hex[:10]
        self.source, self.path, self.track = source, path, track
        self.settings = dict(pipeline.DEFAULT_SETTINGS)
        self.settings.update(settings or {})
        self.state, self.error = "running", ""
        self.rep = pipeline.Reporter()
        self.context = {}
        self.sources, self.analyses, self.result = [], {}, None
        self.timeline_name = ""
        self.builds, self.pending_plan, self.recorded = [], None, {}

    def public(self) -> dict:
        return {"id": self.id, "state": self.state, "error": self.error, "progress": round(self.rep.overall(), 4),
                "stage": self.rep.stage, "logs": self.rep.logs[-60:],
                "stats": self.result["stats"] if self.result else None, "builds": self.builds}

    def run(self):
        try:
            if self.source == "resolve":
                self.context, items = resolve_bridge.timeline_sources(self.track)
                if not items:
                    raise RuntimeError(f"No clips with media on video track {self.track} of the open timeline.")
                self.timeline_name = self.context.get("timeline") or "Timeline"
                self.rep.log(f"Timeline '{self.timeline_name}': {len(items)} clip(s) on V{self.track}")
                self.sources = items
            else:
                p = os.path.abspath(os.path.expanduser(self.path.strip().strip("'\"")))
                if not os.path.isfile(p):
                    raise RuntimeError(f"File not found: {p}")
                self.timeline_name = os.path.splitext(os.path.basename(p))[0]
                self.sources = [{"path": p, "fps": 0.0, "start_sec": 0.0, "end_sec": None}]
            files = list(dict.fromkeys(s["path"] for s in self.sources))
            self.rep.file_count = len(files)
            for i, p in enumerate(files):
                self.rep.file_index = i
                self.analyses[p] = pipeline.analyze_file(p, self.settings, self.rep)
            for s in self.sources:
                s["fps"] = s.get("fps") or self.analyses[s["path"]]["info"]["fps"] or 25.0
            self.replan()
            self.rep.file_index = self.rep.file_count
            st = self.result["stats"]
            self.rep.log(f"Done: {st['source_seconds'] / 60:.1f} min -> {st['kept_seconds'] / 60:.1f} min, "
                         f"{st['removed_takes']} takes removed, {st['review_clips']} clips to review")
            self.state = "done"
        except Exception as e:  # shown in the page
            self.error = str(e)
            self.rep.log("ERROR: " + str(e))
            traceback.print_exc()
            self.state = "error"

    def replan(self):
        self.result = pipeline.plan_for_sources(self.sources, self.analyses, self.settings)

    def decisions(self):
        return [d for a in self.analyses.values() for d in a["decisions"]]

    def learn_from_choices(self) -> int:
        """Count the user's overrides so the next analysis matches their taste."""
        remove_at, review_at = POLICIES.get(self.settings["policy"], POLICIES["balanced"])

        def tool_action(d):
            c = d["confidence"]
            action = "remove" if c >= remove_at else ("review" if c >= review_at else "keep")
            return "review" if action == "remove" and d.get("llm_only") else action

        fresh = [d for d in self.decisions() if d.get("user_action") and self.recorded.get(d["id"]) != d["user_action"]]
        taught = learn.record(fresh, tool_action)
        self.recorded.update({d["id"]: d["user_action"] for d in fresh})
        return taught

    def review_payload(self) -> dict:
        files = []
        for path, a in self.analyses.items():
            ranges = [(s.get("start_sec", 0.0), s.get("end_sec") or a["info"]["duration"])
                      for s in self.sources if s["path"] == path]
            decisions = [{k: d.get(k) for k in ("id", "start", "end", "kind", "text", "confidence", "reason", "ref",
                                                "action", "user_action", "llm_only", "learned")}
                         for d in a["decisions"]
                         if not (d["kind"] in ("filler", "restart") and d["action"] == "keep" and not d.get("user_action"))
                         and any(d["end"] > r0 and d["start"] < r1 for r0, r1 in ranges)]
            decisions.sort(key=lambda d: (d["start"], d["kind"] != "phrase"))
            files.append({"path": path, "name": a["name"], "language": a["language"], "translated": a["translated"],
                          "has_video": a["info"]["has_video"], "model": a["model"], "llm": a.get("llm"),
                          "decisions": decisions})
        return {"job": self.public(), "files": files, "timeline_name": self.timeline_name, "learned": learn.summary()}


def wav_slice(path: str, start: float, end: float) -> bytes:
    """A short WAV from the cached analysis audio, for the play buttons."""
    with wave.open(os.path.join(cache_dir_for(path), "audio16k.wav"), "rb") as src:
        sr = src.getframerate()
        a = max(0, int(start * sr))
        src.setpos(a)
        frames = src.readframes(max(0, min(src.getnframes(), int(end * sr)) - a))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(frames)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "ScriptCutter"

    def log_message(self, fmt, *args):
        pass

    # ----------------------------------------------------------------- responses
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _static(self, rel: str):
        full = os.path.realpath(os.path.join(STATIC, urllib.parse.unquote(rel)))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        with open(full, "rb") as f:
            self._send(200, f.read(), CONTENT_TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))

    def _stream(self, path: str):
        """The source video with HTTP Range support, so the preview can seek."""
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            if a:
                start = int(a)
                end = int(b) if b else min(size - 1, start + 8 * 1024 * 1024)
            elif b:
                start = max(0, size - int(b))
        end = min(end, size - 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", VIDEO_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream"))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except ValueError:
            return {}

    def _job(self, jid: str):
        with JOBS_LOCK:
            return JOBS.get(jid)

    # ----------------------------------------------------------------- routing
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        parts = [p for p in u.path.split("/") if p]
        try:
            if u.path in ("/", "/index.html"):
                return self._static("index.html")
            if u.path.startswith("/static/"):
                return self._static(u.path[len("/static/"):])
            if u.path == "/api/status":
                return self._json(self.status())
            if u.path in ("/api/audio", "/api/video"):
                job = self._job(q.get("job", ""))
                path = q.get("path", "")
                if not job or path not in job.analyses:
                    return self._json({"error": "unknown source"}, 404)
                if u.path == "/api/video":
                    return self._stream(path)
                start = float(q.get("start", 0))
                end = min(float(q.get("end", 0)), start + 120)
                return self._send(200, wav_slice(path, start, end), "audio/wav")
            if len(parts) >= 3 and parts[:2] == ["api", "job"]:
                job = self._job(parts[2])
                if not job:
                    return self._json({"error": "no such job"}, 404)
                if len(parts) == 3:
                    return self._json(job.public())
                if job.state != "done":
                    return self._json({"error": "analysis not finished"}, 409)
                if parts[3] == "review":
                    return self._json(job.review_payload())
                if parts[3] == "build":
                    return self._json(self.build_status(job))
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        body = self._body()
        try:
            if u.path == "/api/analyze":
                job = Job(body.get("source", "resolve"), body.get("path", ""), int(body.get("track", 1) or 1),
                          body.get("settings", {}))
                with JOBS_LOCK:
                    JOBS[job.id] = job
                threading.Thread(target=job.run, daemon=True).start()
                return self._json({"job_id": job.id})
            if len(parts) == 4 and parts[:2] == ["api", "job"]:
                job = self._job(parts[2])
                if not job or job.state != "done":
                    return self._json({"error": "analysis not finished"}, 409)
                handler = {"decision": self.set_decision, "bulk": self.bulk_decision, "settings": self.change_settings,
                           "build": self.build, "export": self.export}.get(parts[3])
                if handler:
                    return self._json(handler(job, body))
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    # ----------------------------------------------------------------- actions
    @staticmethod
    def status() -> dict:
        st = dict(resolve_bridge.status())
        st.update(app_version=__version__, whisper=bool(find_whisper_cli()), ffmpeg=bool(paths.find_program("ffmpeg")),
                  models=installed_models(),
                  providers={k: {"label": v["label"], "model": v["model"]} for k, v in llm.PROVIDERS.items()})
        return st

    @staticmethod
    def set_decision(job: Job, body: dict) -> dict:
        analysis = job.analyses.get(body.get("path"))
        if not analysis:
            raise RuntimeError("unknown source")
        for d in analysis["decisions"]:
            if d["id"] == body.get("id"):
                d["user_action"] = body.get("action") if body.get("action") in ACTIONS else None
        job.replan()
        return job.review_payload()

    @staticmethod
    def bulk_decision(job: Job, body: dict) -> dict:
        """e.g. {"from": "review", "action": "remove"}: every brown line at once."""
        want = body.get("action") if body.get("action") in ACTIONS else None
        for d in job.decisions():
            if d.get("action") == body.get("from") and not d.get("user_action"):
                d["user_action"] = want
        job.replan()
        return job.review_payload()

    @staticmethod
    def change_settings(job: Job, body: dict) -> dict:
        if body.get("policy") in POLICIES:
            job.settings["policy"] = body["policy"]
        for key in ("max_pause", "head_pad", "tail_pad"):
            if key in body:
                job.settings[key] = float(body[key])
        job.replan()
        return job.review_payload()

    @staticmethod
    def build(job: Job, body: dict) -> dict:
        name = (body.get("name") or f"{job.timeline_name} - AI Cut").strip()
        with BUILD_LOCK:
            res = resolve_bridge.build(job.result["plan"], name, checkerboard=bool(body.get("checkerboard")),
                                       overlap_frames=int(body.get("overlap_frames", 3)),
                                       voice_isolation=bool(body.get("voice_isolation")),
                                       markers=bool(body.get("markers", True)))
        res["learned_from"] = job.learn_from_choices()
        if res.get("pending"):
            job.pending_plan = res["plan_id"]
            job.rep.log("Timeline plan saved. " + res["message"])
        else:
            Handler._finish_build(job, res)
        return res

    @staticmethod
    def build_status(job: Job) -> dict:
        if not job.pending_plan:
            return {"pending": False}
        res = resolve_bridge.build_result(job.pending_plan)
        if not res:
            return {"pending": True}
        job.pending_plan = None
        Handler._finish_build(job, res)
        return res

    @staticmethod
    def _finish_build(job: Job, res: dict):
        job.builds.append(res)
        if res.get("ok"):
            job.rep.log(f"Created timeline '{res['timeline']}' ({res['items']} clips, "
                        f"{res['review_markers']} review markers)")
        else:
            job.rep.log("Timeline was not created: " + str(res.get("error")))

    @staticmethod
    def export(job: Job, body: dict) -> dict:
        base = os.path.join(os.path.dirname(next(iter(job.analyses))), f"{job.timeline_name} - AI Cut")
        tl_fps = float(job.context.get("timeline_fps") or resolve_bridge.status().get("timeline_fps") or 24.0)
        edl = export_edl(job.result["plan"], base + ".edl", job.timeline_name, tl_fps,
                         {p: a["info"]["start_timecode"] for p, a in job.analyses.items()})
        return {"edl": edl, "report": export_report(job.result, job.analyses, base + ".json")}


def run(port: int = PORT):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"ScriptCutter {__version__} running at http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else PORT)
