"""
ScriptCutter's local web app: http://127.0.0.1:7870 (launcher.py picks the next
free port if that one is taken).

The page (web/static) talks to this server over a small JSON API:

    GET  /api/status                      Resolve connection, installed models, export choices
    POST /api/analyze                     start an analysis -> {"job_id"}
    GET  /api/job/<id>                    progress and log while it runs
    GET  /api/job/<id>/review             every decision, for the review list
    POST /api/job/<id>/decision|bulk|settings   the creator's choices (re-cut instantly)
    POST /api/job/<id>/build              create the timeline in Resolve
    GET  /api/job/<id>/build              free version of Resolve: has the menu entry built it yet?
    POST /api/job/<id>/export             MP4 / XML / EDL next to the video
    GET  /api/job/<id>/render             progress of the MP4
    GET  /api/job/<id>/transcript         the final cut's words, for the Transcript window
    GET  /api/job/<id>/download           the transcript as .srt or .txt (a browser download)
    POST /api/job/<id>/reveal             show an exported file in Finder / Explorer
    GET  /api/audio, /api/video           play a line in the review
    POST /api/choose-file                 the system's file picker -> {"path"}
    POST /api/shutdown                    stop this server (launcher.py, when a newer version starts)

Each analysis is a cutter.project.Project running in a background thread.

Safety: the server only listens on this computer. It also refuses requests
addressed to any other host name (so a web page can't reach it by pointing its
own domain at 127.0.0.1), and every POST must carry the X-ScriptCutter header,
which only ScriptCutter's own page sends (a different website can't add it).
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import traceback
import urllib.parse
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cutter import __version__, exporters, llm, paths, resolve_bridge  # noqa: E402
from cutter.exporters import transcript as transcript_out  # noqa: E402
from cutter.media import cache_dir_for  # noqa: E402
from cutter.project import Project  # noqa: E402
from cutter.transcribe import find_whisper_cli, installed_models  # noqa: E402

PORT = 7870
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
MAX_JOBS = 4                  # finished analyses kept in memory (each holds a file's analysis)
MAX_BODY = 10 * 1024 * 1024   # largest request body accepted (a pasted script is far smaller)
EXPORT_CHOICES = [{"id": "mp4", "label": "MP4 video"}, {"id": "resolve", "label": "DaVinci Resolve"}] + \
                 [{"id": k, "label": exporters.FORMATS[k]["label"]} for k in exporters.EDITORS]

JOBS = {}                     # job id -> Project, oldest first
JOBS_LOCK = threading.Lock()
BUILD_LOCK = threading.Lock()  # one Resolve build at a time


def add_job(job: Project):
    """Remember a new analysis; forget the oldest finished ones beyond MAX_JOBS."""
    with JOBS_LOCK:
        JOBS[job.id] = job
        done = [j for j in JOBS.values() if j.state != "running" and j.render.get("state") != "running"]
        for old in done[:max(0, len(JOBS) - MAX_JOBS)]:
            del JOBS[old.id]


def wav_slice(path: str, start: float, end: float) -> bytes:
    """A short WAV from the cached analysis audio, for the play buttons."""
    with wave.open(os.path.join(cache_dir_for(path), "audio16k.wav"), "rb") as src:
        sr = src.getframerate()
        a = min(src.getnframes(), max(0, int(start * sr)))
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
        pass  # one line per request would bury the useful messages in the log file

    # ----------------------------------------------------------------- responses
    def _send(self, code: int, body: bytes, ctype: str, extra: dict = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")  # the page always gets the latest app files
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _attachment(self, body: bytes, filename: str, ctype: str):
        """A file the browser saves to Downloads, under its real (possibly Hindi) name."""
        ascii_name = filename.encode("ascii", "replace").decode().replace("?", "_").replace('"', "")
        self._send(200, body, ctype, {"Content-Disposition": f'attachment; filename="{ascii_name}"; '
                                                             f"filename*=UTF-8''{urllib.parse.quote(filename)}"})

    def _static(self, rel: str):
        """A file from web/static (never anything outside it)."""
        full = os.path.realpath(os.path.join(STATIC, urllib.parse.unquote(rel)))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        with open(full, "rb") as f:
            self._send(200, f.read(), CONTENT_TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))

    def _stream(self, path: str):
        """The source video with HTTP Range support, so the preview player can jump around."""
        size = os.path.getsize(path)
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            try:
                a, _, b = rng[6:].split(",")[0].strip().partition("-")
                if a:
                    start = int(a)
                    end = int(b) if b else min(size - 1, start + 8 * 1024 * 1024)  # 8 MB at a time
                elif b:
                    start = max(0, size - int(b))  # "the last N bytes"
            except ValueError:
                start, end = 0, size - 1
        end = min(end, size - 1)
        if start >= size or start > end:  # asking past the end of the file
            return self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
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
                pass  # the player jumped elsewhere and closed this request

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > MAX_BODY:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except ValueError:
            return {}

    def _job(self, jid: str):
        with JOBS_LOCK:
            return JOBS.get(jid)

    def _allowed(self, post: bool) -> bool:
        """Only requests addressed to this computer, and (for POST) only from ScriptCutter's own page."""
        port = self.server.server_address[1]
        if self.headers.get("Host", "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        return not post or self.headers.get("X-ScriptCutter") == "1"

    # ----------------------------------------------------------------- routing
    def do_GET(self):
        if not self._allowed(post=False):
            return self._json({"error": "forbidden"}, 403)
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
                if not job or path not in job.analyses:  # only files this analysis read
                    return self._json({"error": "unknown source"}, 404)
                if u.path == "/api/video":
                    return self._stream(path)
                start = float(q.get("start", 0))
                end = min(float(q.get("end", 0)), start + 120)
                return self._send(200, wav_slice(path, start, end), "audio/wav")
            if len(parts) >= 3 and parts[:2] == ["api", "job"]:
                job = self._job(parts[2])
                if not job:
                    return self._json({"error": "This analysis is no longer open. Press Analyse again."}, 404)
                if len(parts) == 3:
                    return self._json(job.public())
                if job.state != "done":
                    return self._json({"error": "analysis not finished"}, 409)
                if parts[3] == "review":
                    return self._json(job.review_payload())
                if parts[3] == "build":
                    return self._json(self.build_status(job))
                if parts[3] == "transcript":
                    return self._json(self.transcript(job, q))
                if parts[3] == "download":
                    return self.download(job, q)
                if parts[3] == "render":
                    return self._json(job.render)
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        if not self._allowed(post=True):
            return self._json({"error": "forbidden"}, 403)
        u = urllib.parse.urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        body = self._body()
        try:
            if u.path == "/api/analyze":
                job = Project(body.get("source", "resolve"), body.get("path", ""), int(body.get("track", 1) or 1),
                              body.get("settings", {}))
                add_job(job)
                threading.Thread(target=job.run, daemon=True).start()
                return self._json({"job_id": job.id})
            if u.path == "/api/choose-file":
                return self._json({"path": paths.choose_file("Choose your raw recording")})
            if u.path == "/api/shutdown":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
            if len(parts) == 4 and parts[:2] == ["api", "job"]:
                job = self._job(parts[2])
                if not job or job.state != "done":
                    return self._json({"error": "This analysis is no longer open. Press Analyse again."}, 409)
                handler = {"decision": self.set_decision, "bulk": self.bulk_decision, "split": self.split_line,
                           "undo": self.undo, "settings": self.change_settings,
                           "build": self.build, "export": self.export, "reveal": self.reveal}.get(parts[3])
                if handler:
                    return self._json(handler(job, body))
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    # ----------------------------------------------------------------- answers
    @staticmethod
    def status() -> dict:
        st = dict(resolve_bridge.status())
        st.update(app_version=__version__, app_root=ROOT, whisper=bool(find_whisper_cli()),
                  ffmpeg=bool(paths.find_program("ffmpeg")), models=installed_models(), editors=EXPORT_CHOICES,
                  providers={k: {"label": v["label"], "model": v["model"]} for k, v in llm.PROVIDERS.items()})
        return st

    @staticmethod
    def transcript(job: Project, q: dict) -> dict:
        view = q.get("view", "original")
        edit = job.edit()
        return {"text": transcript_out.plain_text(edit, view), "lines": transcript_out.lines(edit, view),
                "seconds": edit["duration"] / edit["fps"],
                "pending": job.step("words").state in ("waiting", "running")}

    def download(self, job: Project, q: dict):
        """?format=srt|txt&view=...&name=... -> the final cut's subtitles or text, as a browser download."""
        fmt = q.get("format", "srt")
        if fmt not in ("srt", "txt"):
            return self._json({"error": "unknown format"}, 404)
        edit = job.edit(q.get("name", "").strip())
        with tempfile.TemporaryDirectory() as tmp:
            path = exporters.export(edit, fmt, os.path.join(tmp, "transcript"), q.get("view", "original"))
            with open(path, "rb") as f:
                body = f.read()
        name = job.file_name(edit["name"]) + exporters.FORMATS[fmt]["ext"]  # the browser numbers duplicates itself
        return self._attachment(body, name, "application/x-subrip; charset=utf-8" if fmt == "srt"
                                else "text/plain; charset=utf-8")

    @staticmethod
    def set_decision(job: Project, body: dict) -> dict:
        job.set_decision(body.get("path"), body.get("id"), body.get("action"))
        return job.review_payload()

    @staticmethod
    def bulk_decision(job: Project, body: dict) -> dict:
        job.bulk(body.get("from"), body.get("action"))
        return job.review_payload()

    @staticmethod
    def split_line(job: Project, body: dict) -> dict:
        """{"path", "id", "word"}: split the line before that word (the review's text cursor)."""
        job.split(body.get("path"), body.get("id"), int(body.get("word") or 0))
        return job.review_payload()

    @staticmethod
    def undo(job: Project, body: dict) -> dict:
        job.undo()
        return job.review_payload()

    @staticmethod
    def change_settings(job: Project, body: dict) -> dict:
        job.change_settings(body)
        return job.review_payload()

    @staticmethod
    def build(job: Project, body: dict) -> dict:
        """Create the timeline in Resolve (Studio: now; free version: after the menu entry runs)."""
        job.require_clips()
        name = (body.get("name") or f"{job.timeline_name} - AI Cut").strip()
        with BUILD_LOCK:
            res = resolve_bridge.build(job.result["plan"], name, checkerboard=bool(body.get("checkerboard")),
                                       overlap_frames=int(body.get("overlap_frames", 3)),
                                       voice_isolation=bool(body.get("voice_isolation")),
                                       markers=bool(body.get("markers", True)), timeline_fps=job.timeline_fps())
        res["learned_from"] = job.learn_from_choices()
        job.approved = True
        if res.get("pending"):
            job.pending_plan = res["plan_id"]
            job.rep.log("Timeline plan saved. " + res["message"])
        else:
            Handler._finish_build(job, res)
        return res

    @staticmethod
    def build_status(job: Project) -> dict:
        if not job.pending_plan:
            return {"pending": False}
        res = resolve_bridge.build_result(job.pending_plan)
        if not res:
            return {"pending": True}
        job.pending_plan = None
        Handler._finish_build(job, res)
        return res

    @staticmethod
    def _finish_build(job: Project, res: dict):
        job.builds.append(res)
        if res.get("ok"):
            job.rep.log(f"Created timeline '{res['timeline']}' ({res['items']} clips, "
                        f"{res['review_markers']} review markers)")
        else:
            job.rep.log("Timeline was not created: " + str(res.get("error")))

    @staticmethod
    def export(job: Project, body: dict) -> dict:
        """{"format": "mp4" | "premiere" | "fcp" | "edl", "name", "view"} -> a new file next to the video."""
        fmt, view = body.get("format", "mp4"), body.get("view", "original")
        if fmt != "mp4" and fmt not in exporters.EDITORS:
            raise RuntimeError(f"unknown format {fmt}")
        job.require_clips()
        edit = job.edit(body.get("name", "").strip())
        path = job.output_path(edit["name"], exporters.FORMATS[fmt]["ext"])
        job.approved = True
        learned = job.learn_from_choices()
        how = exporters.FORMATS[fmt]["how"]
        if os.path.dirname(path) == paths.output_dir(create=False):
            how = f"Your video's folder can't be written to, so it went to {os.path.dirname(path)}. " + how
        if fmt == "mp4":  # takes a while: made in the background, the page asks /render for progress
            if job.render.get("state") == "running":
                raise RuntimeError("A video is already being made for this cut.")
            job.render = {"state": "running", "progress": 0.0, "file": path, "error": ""}

            def make():
                try:
                    exporters.video.write(edit, path, view, progress=lambda f: job.render.update(progress=round(f, 3)))
                    job.outputs.append(path)
                    job.render.update(state="done", progress=1.0)
                    job.rep.log("Saved " + os.path.basename(path))
                except Exception as e:
                    traceback.print_exc()
                    job.render.update(state="error", error=str(e))
            threading.Thread(target=make, daemon=True).start()
            return {"rendering": True, "file": path, "how": how, "learned_from": learned}
        exporters.FORMATS[fmt]["write"](edit, path, view)
        job.outputs.append(path)
        job.rep.log("Saved " + os.path.basename(path))
        return {"files": [path], "how": how, "learned_from": learned}

    @staticmethod
    def reveal(job: Project, body: dict) -> dict:
        """Show one of this analysis' exported files in Finder / Explorer (nothing else)."""
        if body.get("path") not in job.outputs:
            raise RuntimeError("unknown file")
        paths.reveal(body["path"])
        return {"ok": True}


class Server(ThreadingHTTPServer):
    # macOS: reuse the port right after an old ScriptCutter stopped. Windows: never, because
    # there it would let two programs share one port
    allow_reuse_address = not paths.IS_WINDOWS
    daemon_threads = True  # an open request never keeps a stopping server alive


def run(port: int = PORT):
    server = Server(("127.0.0.1", port), Handler)
    print(f"ScriptCutter {__version__} running at http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else PORT)
