"""
Talks to DaVinci Resolve through resolve/scriptcutter.lua.

Two routes, chosen automatically:
  * live  - Resolve Studio allows outside tools: the Lua file is run with
            Resolve's own `fuscript`, so reading and building are one click.
  * menu  - the free version only runs scripts started from its menu. The
            "ScriptCutter" menu entry saves the open timeline to
            bridge/context.json; choosing "ScriptCutter" again builds from
            bridge/plan.lua and writes bridge/result.json.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
import uuid

from . import paths

LUA = os.path.join(paths.APP_ROOT, "resolve", "scriptcutter.lua")
SNAPSHOT_MAX_AGE = 12 * 3600  # a menu snapshot older than this is not trusted
_status_cache = {"at": 0.0, "value": None}
_status_lock = threading.Lock()  # one fuscript at a time; others get the last answer


# --------------------------------------------------------------------------- files
def _bridge(name: str) -> str:
    """A file in the bridge folder shared with the Lua scripts."""
    return os.path.join(paths.bridge_dir(), name)


def _read_json(path: str):
    """A JSON file written by the Lua side, or None (missing, half written, or not JSON)."""
    try:
        with open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None


def _lua_string(s: str) -> str:
    """A Python string as a safe Lua string literal (quotes, backslashes and control characters escaped)."""
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append("\\%03d" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def to_lua(value) -> str:
    """Python value -> Lua literal (the plan file is loaded with dofile)."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if math.isfinite(value) else "0"
    if isinstance(value, str):
        return _lua_string(value)
    if isinstance(value, dict):
        return "{" + ",".join(f"[{_lua_string(str(k))}]={to_lua(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "{" + ",".join(to_lua(v) for v in value) + "}"
    raise TypeError(f"Cannot convert {type(value).__name__} to Lua")


# --------------------------------------------------------------------------- live route
def _run_lua(mode: str, *args: str, timeout: float = 15.0):
    """
    Run scriptcutter.lua with Resolve's fuscript (Resolve Studio). The last argument
    is the JSON file the script writes its answer to. None when fuscript is missing,
    Resolve doesn't answer in time, or the answer is unreadable.
    """
    exe = paths.find_program("fuscript")
    if not exe:
        return None
    out = args[-1]
    if os.path.exists(out):
        os.unlink(out)
    try:
        subprocess.run([exe, "-l", "lua", LUA, mode, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=timeout, cwd=paths.bridge_dir())
    except (OSError, subprocess.SubprocessError):
        return None
    return _read_json(out)


def live_context():
    """Context dict from a live Resolve, or None when Resolve can't be reached from outside."""
    res = _run_lua("context", _bridge("live_context.json"))
    if not res or res.get("code") == "not_connected":
        return None
    return res


def snapshot_context():
    """Context saved by the 'ScriptCutter' menu entry (free version), if recent."""
    snap = _read_json(_bridge("context.json"))
    if not snap or time.time() - float(snap.get("created") or 0) > SNAPSHOT_MAX_AGE:
        return None
    return snap


def status(max_age: float = 10.0) -> dict:
    """
    How ScriptCutter can reach Resolve right now, for the status line in the page:
    mode "live" (Studio), "menu" (a recent snapshot from the Scripts menu) or "none".
    Cached for max_age seconds. While one check is talking to Resolve, other calls
    get the last answer at once instead of piling up behind a slow Resolve.
    """
    if _status_cache["value"] and time.time() - _status_cache["at"] < max_age:
        return _status_cache["value"]
    if not _status_lock.acquire(blocking=False):
        return _status_cache["value"] or {"connected": False, "mode": "none", "studio": False}
    try:
        return _check_status()
    finally:
        _status_lock.release()


def _check_status() -> dict:
    live = live_context()
    if live:
        value = {"connected": True, "mode": "live", "product": live.get("product"), "project": live.get("project"),
                 "timeline": live.get("timeline"), "timeline_fps": live.get("timeline_fps"),
                 "message": live.get("error") if not live.get("ok") else ""}
    else:
        snap = snapshot_context()
        if snap and snap.get("ok"):
            value = {"connected": False, "mode": "menu", "product": snap.get("product"),
                     "project": snap.get("project"), "timeline": snap.get("timeline"),
                     "timeline_fps": snap.get("timeline_fps"),
                     "snapshot_age": int(time.time() - float(snap.get("created") or 0))}
        else:
            value = {"connected": False, "mode": "none"}
    value["studio"] = "Studio" in (value.get("product") or "")
    _status_cache.update(at=time.time(), value=value)
    return value


def timeline_sources(track: int = 1) -> tuple:
    """
    (context, the clips on one video track) from live Resolve or the menu snapshot.
    Each clip: its file and the part of the file the timeline uses, in seconds.
    Clips whose media is offline are left out; the context records how many.
    """
    ctx = live_context() or snapshot_context()
    if not ctx:
        raise RuntimeError("Can't see your Resolve timeline. In DaVinci Resolve choose Workspace > Scripts > "
                           "ScriptCutter (needed once per timeline in the free version), then try again.")
    if not ctx.get("ok"):
        raise RuntimeError(ctx.get("error") or "Resolve did not return the timeline.")
    items, offline = [], 0
    for it in ctx.get("items", []):
        fps = float(it.get("fps") or 0)
        if int(it.get("track", 1)) != track or not fps:
            continue
        if not os.path.isfile(it.get("path", "")):
            offline += 1
            continue
        items.append({"name": it.get("name"), "path": it["path"], "fps": fps,
                      "start_sec": it["src_in"] / fps, "end_sec": it["src_out"] / fps})
    ctx["offline"] = offline
    return ctx, items


# --------------------------------------------------------------------------- building
def build(plan_clips: list, name: str, checkerboard: bool = True, overlap_frames: int = 3,
          voice_isolation: bool = True, markers: bool = True, timeline_fps: float = 0.0) -> dict:
    """
    Build the new timeline. Resolve Studio: right away, through fuscript. Free
    version: the plan is saved as bridge/plan.lua and {"pending": True, ...} is
    returned until the creator chooses Workspace > Scripts > ScriptCutter again.
    timeline_fps: the rate of the timeline being cut, so the new one matches it.
    """
    plan_id = uuid.uuid4().hex[:12]
    plan = {
        "plan_id": plan_id, "created": int(time.time()), "name": name, "timeline_fps": float(timeline_fps or 0),
        "checkerboard": bool(checkerboard), "overlap": int(overlap_frames),
        "voice_isolation": bool(voice_isolation), "voice_amount": 100, "markers": bool(markers),
        "clips": [{"path": c["path"], "fps": float(c["src_fps"]), "src_in": int(c["src_in"]),
                   "src_out": int(c["src_out"]), "status": c["status"],
                   "reason": "; ".join(r for r in c.get("reasons", []) if r), "text": c.get("text", "")[:400],
                   "ids": ",".join(c.get("ids", []))[:200]} for c in plan_clips],
    }
    plan_path, result_path = _bridge("plan.lua"), _bridge("result.json")
    for p in (plan_path, result_path):
        if os.path.exists(p):
            os.unlink(p)
    with open(plan_path + ".tmp", "w", encoding="utf-8") as f:
        f.write("return " + to_lua(plan) + "\n")
    os.replace(plan_path + ".tmp", plan_path)

    if live_context() is not None:
        res = _run_lua("build", plan_path, result_path, timeout=900) or {
            "ok": False, "error": "Resolve did not answer while building the timeline."}
        if os.path.exists(plan_path):
            os.unlink(plan_path)
        res["mode"] = "live"
        return res
    return {"pending": True, "plan_id": plan_id, "mode": "menu",
            "message": "Now in DaVinci Resolve choose Workspace > Scripts > ScriptCutter again to build it."}


def build_result(plan_id: str):
    """Result of a menu build for this plan, or None while it is still waiting."""
    res = _read_json(_bridge("result.json"))
    if res and res.get("plan_id") == plan_id:
        res["mode"] = "menu"
        return res
    return None
