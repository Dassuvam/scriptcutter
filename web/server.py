"""
Local web app for ScriptCutter v2 (http://127.0.0.1:7870).

v1 used a single-threaded HTTPServer and ran the whole job inside the request:
the page could not poll status, long videos looked frozen, and errors were
printed to a log that the launcher discarded. Here every analysis runs in a
background thread with progress + a visible log, and the review edits re-cut
instantly from the cached analysis.
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cutter import learn, llm, pipeline, resolve_api  # noqa: E402
from cutter.analyze import POLICIES  # noqa: E402
from cutter.export import export_edl, export_report  # noqa: E402
from cutter.media import cache_dir_for, find_bin  # noqa: E402
from cutter.transcribe import find_whisper_cli, installed_models  # noqa: E402

HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
JOBS = {}
JOBS_LOCK = threading.Lock()
RESOLVE_LOCK = threading.Lock()  # the Resolve API is not thread-safe


class Job:
    def __init__(self, source: str, path: str, track: int, settings: dict):
        self.id = uuid.uuid4().hex[:10]
        self.source, self.path, self.track = source, path, track
        self.settings = dict(pipeline.DEFAULT_SETTINGS)
        self.settings.update(settings or {})
        self.state, self.error = "running", ""
        self.rep = pipeline.Reporter()
        self.sources, self.analyses, self.result = [], {}, None
        self.timeline_name = ""
        self.builds = []
        self.recorded = {}
        self.created = time.time()

    def public(self) -> dict:
        return {
            "id": self.id, "state": self.state, "error": self.error,
            "progress": round(self.rep.overall(), 4), "stage": self.rep.stage,
            "logs": self.rep.logs[-60:], "stats": self.result["stats"] if self.result else None,
            "builds": self.builds,
        }

    def run(self):
        try:
            if self.source == "resolve":
                with RESOLVE_LOCK:
                    st = resolve_api.status()
                    items = resolve_api.timeline_sources(track=self.track)
                if not items:
                    raise RuntimeError(f"No clips with media on video track {self.track} of the current timeline.")
                self.timeline_name = st.get("timeline") or "Timeline"
                self.rep.log(f"Timeline '{self.timeline_name}': {len(items)} clip(s) on V{self.track}")
                self.sources = items
            else:
                p = os.path.abspath(os.path.expanduser(self.path.strip().strip("'\"")))
                if not os.path.isfile(p):
                    raise RuntimeError(f"File not found: {p}")
                self.timeline_name = os.path.splitext(os.path.basename(p))[0]
                self.sources = [{"path": p, "fps": 0.0, "start_sec": 0.0, "end_sec": None, "name": os.path.basename(p)}]
            uniq = []
            for s in self.sources:
                if s["path"] not in uniq:
                    uniq.append(s["path"])
            self.rep.file_count = len(uniq)
            for i, p in enumerate(uniq):
                self.rep.file_index = i
                self.analyses[p] = pipeline.analyze_file(p, self.settings, self.rep)
            for s in self.sources:
                if not s.get("fps"):
                    s["fps"] = self.analyses[s["path"]]["info"]["fps"] or 25.0
            self.replan()
            self.rep.file_index = self.rep.file_count
            st = self.result["stats"]
            self.rep.log(f"Done: {st['source_seconds'] / 60:.1f} min -> {st['kept_seconds'] / 60:.1f} min, "
                         f"{st['removed_takes']} takes removed, {st['review_clips']} clips to review")
            self.state = "done"
        except Exception as e:  # surfaced in the UI
            self.error = str(e)
            self.rep.log("ERROR: " + str(e))
            traceback.print_exc()
            self.state = "error"

    def replan(self):
        self.result = pipeline.plan_for_sources(self.sources, self.analyses, self.settings)

    def review_payload(self) -> dict:
        files = []
        for path, a in self.analyses.items():
            ranges = [(s.get("start_sec", 0.0), s.get("end_sec") or a["info"]["duration"])
                      for s in self.sources if s["path"] == path]
            decs = []
            for d in a["decisions"]:
                if d["kind"] in ("filler", "restart") and d["action"] == "keep" and not d.get("user_action"):
                    continue
                if not any(d["end"] > r0 and d["start"] < r1 for r0, r1 in ranges):
                    continue
                decs.append({k: d.get(k) for k in ("id", "start", "end", "kind", "text", "confidence", "reason",
                                                   "ref", "action", "user_action", "parent", "llm_only", "learned")})
            decs.sort(key=lambda d: (d["start"], d["kind"] != "phrase"))
            files.append({"path": path, "name": a["name"], "language": a["language"], "translated": a["translated"],
                          "has_video": a["info"]["has_video"],
                          "model": a["model"], "levels": a["levels"], "llm": a.get("llm"), "decisions": decs,
                          "duration": a["info"]["duration"]})
        return {"job": self.public(), "files": files, "timeline_name": self.timeline_name, "learned": learn.summary(),
                "settings": {k: v for k, v in self.settings.items() if k != "llm_api_key"}}


def _models_info():
    return {"whisper": bool(find_whisper_cli()), "ffmpeg": bool(find_bin("ffmpeg")), "models": installed_models(),
            "providers": {k: {"label": v["label"], "model": v["model"]} for k, v in llm.PROVIDERS.items()}}


def _wav_slice(path: str, start: float, end: float) -> bytes:
    wav = os.path.join(cache_dir_for(path), "audio16k.wav")
    with wave.open(wav, "rb") as src:
        sr = src.getframerate()
        a = max(0, int(start * sr))
        b = min(src.getnframes(), int(end * sr))
        src.setpos(a)
        frames = src.readframes(max(0, b - a))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(frames)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "ScriptCutterV2"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _stream(self, path: str):
        """Serve the source video with HTTP Range so the browser can seek."""
        size = os.path.getsize(path)
        ctype = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime"}.get(
            os.path.splitext(path)[1].lower(), "application/octet-stream")
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
        self.send_header("Content-Type", ctype)
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

    def _job(self, jid):
        with JOBS_LOCK:
            return JOBS.get(jid)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        parts = [p for p in u.path.split("/") if p]
        try:
            if u.path in ("/", "/index.html"):
                with open(HTML, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/api/status":
                with RESOLVE_LOCK:
                    st = resolve_api.status()
                st.update(_models_info())
                return self._json(st)
            if len(parts) == 3 and parts[:2] == ["api", "job"]:
                j = self._job(parts[2])
                return self._json(j.public()) if j else self._json({"error": "no such job"}, 404)
            if len(parts) == 4 and parts[:2] == ["api", "job"] and parts[3] == "review":
                j = self._job(parts[2])
                if not j or j.state != "done":
                    return self._json({"error": "analysis not finished"}, 409)
                return self._json(j.review_payload())
            if u.path == "/api/video":
                j = self._job(q.get("job", [""])[0])
                path = q.get("path", [""])[0]
                if not j or path not in j.analyses:
                    return self._json({"error": "unknown source"}, 404)
                return self._stream(path)
            if u.path == "/api/audio":
                j = self._job(q.get("job", [""])[0])
                path = q.get("path", [""])[0]
                if not j or path not in j.analyses:
                    return self._json({"error": "unknown source"}, 404)
                start, end = float(q.get("start", ["0"])[0]), float(q.get("end", ["0"])[0])
                if end - start > 120:
                    end = start + 120
                return self._send(200, _wav_slice(path, start, end), "audio/wav")
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
                j = self._job(parts[2])
                if not j or j.state != "done":
                    return self._json({"error": "analysis not finished"}, 409)
                action = parts[3]
                if action == "decision":
                    a = j.analyses.get(body.get("path"))
                    if not a:
                        return self._json({"error": "unknown source"}, 404)
                    want = body.get("action")
                    for d in a["decisions"]:
                        if d["id"] == body.get("id"):
                            d["user_action"] = want if want in ("keep", "review", "remove") else None
                    j.replan()
                    return self._json(j.review_payload())
                if action == "bulk":
                    # e.g. {"from": "review", "action": "remove"}: every brown line at once
                    src, want = body.get("from"), body.get("action")
                    for a in j.analyses.values():
                        for d in a["decisions"]:
                            if d.get("action") == src and not d.get("user_action"):
                                d["user_action"] = want if want in ("keep", "review", "remove") else None
                    j.replan()
                    return self._json(j.review_payload())
                if action == "settings":
                    for k in ("policy", "max_pause", "head_pad", "tail_pad"):
                        if k in body:
                            j.settings[k] = body[k] if k == "policy" else float(body[k])
                    if body.get("reset_user"):
                        for a in j.analyses.values():
                            for d in a["decisions"]:
                                d["user_action"] = None
                    j.replan()
                    return self._json(j.review_payload())
                if action == "build":
                    name = (body.get("name") or f"{j.timeline_name} - AI Cut v2").strip()
                    plan = [c for c in j.result["plan"]]
                    if j.source == "file":
                        for c in plan:
                            c["media_pool_item"] = None
                    with RESOLVE_LOCK:
                        if j.source == "resolve":
                            fresh = resolve_api.timeline_sources(track=j.track)
                            mpis = {s["path"]: s["media_pool_item"] for s in fresh}
                            for c in plan:
                                c["media_pool_item"] = mpis.get(c["path"])
                        res = resolve_api.build_timeline(
                            plan, name, checkerboard=bool(body.get("checkerboard")),
                            overlap_frames=int(body.get("overlap_frames", 3)),
                            voice_isolation=bool(body.get("voice_isolation")),
                            markers=bool(body.get("markers", True)), log=j.rep.log)
                    j.builds.append(res)
                    # remember your overrides so future analyses match your taste
                    remove_at, review_at = POLICIES.get(j.settings["policy"], POLICIES["balanced"])

                    def tool_action(d):
                        c = d["confidence"]
                        a = "remove" if c >= remove_at else ("review" if c >= review_at else "keep")
                        return "review" if a == "remove" and d.get("llm_only") else a
                    fresh_choices = [d for a in j.analyses.values() for d in a["decisions"]
                                     if d.get("user_action") and j.recorded.get(d["id"]) != d["user_action"]]
                    taught = learn.record(fresh_choices, tool_action)
                    for d in fresh_choices:
                        j.recorded[d["id"]] = d["user_action"]
                    if taught:
                        j.rep.log(f"Learned from {taught} of your choices (used on the next analysis)")
                    res["learned_from"] = taught
                    j.rep.log(f"Created timeline '{res['timeline']}' ({res['items']} items, "
                              f"{res['review_markers']} review markers)")
                    return self._json(res)
                if action == "export":
                    first = next(iter(j.analyses))
                    out_dir = os.path.dirname(first)
                    base = os.path.join(out_dir, f"{j.timeline_name} - AI Cut v2")
                    tl_fps = float(body.get("timeline_fps") or 0) or 24.0
                    if j.source == "resolve":
                        with RESOLVE_LOCK:
                            tl_fps = resolve_api.status().get("timeline_fps", tl_fps)
                    edl = export_edl(j.result["plan"], base + ".edl", j.timeline_name, tl_fps,
                                     {p: a["info"]["start_timecode"] for p, a in j.analyses.items()})
                    rep = export_report(j.result, j.analyses, base + ".json")
                    return self._json({"edl": edl, "report": rep})
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)


def run(port: int = 7870):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"ScriptCutter v2 running at http://127.0.0.1:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 7870)
