"""
Every operating-system specific location in one place: app folders, the
DaVinci Resolve script folder and fuscript, and the external programs
(ffmpeg, whisper.cpp). Supported: macOS and Windows.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

APP_NAME = "ScriptCutter"
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

IS_WINDOWS = os.name == "nt"


def _home(*parts: str) -> str:
    return os.path.join(os.path.expanduser("~"), *parts)


def data_dir() -> str:
    """Settings, learned choices and the Resolve hand-off files."""
    if IS_WINDOWS:
        base = os.path.join(os.environ.get("APPDATA", _home("AppData", "Roaming")), APP_NAME)
    else:
        base = _home("Library", "Application Support", APP_NAME)
    os.makedirs(base, exist_ok=True)
    return base


def cache_dir() -> str:
    """Extracted audio and transcripts. Safe to delete."""
    if IS_WINDOWS:
        base = os.path.join(os.environ.get("LOCALAPPDATA", _home("AppData", "Local")), APP_NAME, "Cache")
    else:
        base = _home("Library", "Caches", APP_NAME)
    os.makedirs(base, exist_ok=True)
    return base


def bridge_dir() -> str:
    """Files exchanged with the Lua scripts running inside Resolve."""
    d = os.path.join(data_dir(), "bridge")
    os.makedirs(d, exist_ok=True)
    return d


def log_file() -> str:
    if IS_WINDOWS:
        return os.path.join(data_dir(), f"{APP_NAME}.log")
    d = _home("Library", "Logs")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{APP_NAME}.log")


def model_dir() -> str:
    """Whisper models; the same folder the whisper.cpp tools use."""
    return _home(".cache", "whisper-models")


def resolve_scripts_dir() -> str:
    """Resolve's per-user Workspace > Scripts > (Utility) folder."""
    if IS_WINDOWS:
        return os.path.join(os.environ.get("APPDATA", _home("AppData", "Roaming")), "Blackmagic Design",
                            "DaVinci Resolve", "Support", "Fusion", "Scripts", "Utility")
    return _home("Library", "Application Support", "Blackmagic Design", "DaVinci Resolve", "Fusion", "Scripts",
                 "Utility")


def fuscript_candidates() -> list:
    """Resolve's own script runner, used to talk to Resolve Studio from outside."""
    if IS_WINDOWS:
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        return [os.path.join(pf, "Blackmagic Design", "DaVinci Resolve", "fuscript.exe")]
    return [f"/Applications/{folder}/{app}.app/Contents/Libraries/Fusion/fuscript"
            for folder in ("DaVinci Resolve", "DaVinci Resolve Studio")
            for app in ("DaVinci Resolve", "DaVinci Resolve Studio")]


# --------------------------------------------------------------------------- programs
def _config() -> dict:
    try:
        with open(os.path.join(data_dir(), "config.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(values: dict):
    cfg = _config()
    cfg.update(values)
    with open(os.path.join(data_dir(), "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)


def _extra_bin_dirs() -> list:
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", _home("AppData", "Local"))
        return [os.path.join(local, APP_NAME, "tools", "whisper"), os.path.join(local, "Microsoft", "WinGet", "Links")]
    return ["/opt/homebrew/bin", "/usr/local/bin"]  # Homebrew (Apple Silicon, Intel)


def find_program(name: str) -> str:
    """
    Absolute path of an external program, or "". Checks the paths recorded by
    the installer first (apps launched from Resolve often get a minimal PATH),
    then PATH, then the usual install folders.
    """
    recorded = _config().get("programs", {}).get(name)
    if recorded and os.path.isfile(recorded):
        return recorded
    if name == "fuscript":
        return next((p for p in fuscript_candidates() if os.path.isfile(p)), "")
    found = shutil.which(name)
    if found:
        return found
    exe = name + (".exe" if IS_WINDOWS else "")
    for d in _extra_bin_dirs():
        p = os.path.join(d, exe)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return ""


def reveal(path: str):
    """Show a file in Finder / Explorer."""
    if IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        subprocess.Popen(["open", "-R", path])
