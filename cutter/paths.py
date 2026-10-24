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


def app_dir() -> str:
    """Where the installer puts ScriptCutter itself, so the downloaded folder can be deleted."""
    if IS_WINDOWS:
        return os.path.join(os.environ.get("LOCALAPPDATA", _home("AppData", "Local")), APP_NAME, "App")
    return _home("Library", "Application Support", APP_NAME, "App")


def tools_dir() -> str:
    """Windows: ffmpeg and whisper.cpp downloaded by the installer (macOS uses Homebrew)."""
    return os.path.join(os.environ.get("LOCALAPPDATA", _home("AppData", "Local")), APP_NAME, "tools")


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
    """The server's log: what to ask a user for when something goes wrong."""
    if IS_WINDOWS:
        return os.path.join(data_dir(), f"{APP_NAME}.log")
    d = _home("Library", "Logs")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{APP_NAME}.log")


def output_dir(create: bool = True) -> str:
    """Where exports go when the video's own folder can't be written to: Movies (Mac) / Videos (Windows)."""
    d = _home("Videos" if IS_WINDOWS else "Movies", APP_NAME)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


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
    """config.json in the data folder: where the installer found each program."""
    try:
        with open(os.path.join(data_dir(), "config.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def config_value(key: str, default=None):
    """One value from config.json, such as "whisper_gpu" (set by the installer)."""
    return _config().get(key, default)


def save_config(values: dict):
    """Merge values into config.json."""
    cfg = _config()
    cfg.update(values)
    with open(os.path.join(data_dir(), "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)


def _extra_bin_dirs() -> list:
    """Install folders that are not always on PATH when Resolve starts the app."""
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", _home("AppData", "Local"))
        return [os.path.join(tools_dir(), "whisper"), os.path.join(tools_dir(), "ffmpeg", "bin"),
                os.path.join(local, "Microsoft", "WinGet", "Links")]
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


def choose_file(prompt: str) -> str:
    """
    The system's own "open file" window, so nobody has to type a path. Returns the
    chosen path, or "" when the window was cancelled. Runs on this computer, so it
    also works when the page was opened from Resolve's Scripts menu.
    """
    if IS_WINDOWS:
        # a hidden top-most window as owner keeps the picker in front of the browser
        script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8;"
                  "Add-Type -AssemblyName System.Windows.Forms;"
                  "$owner = New-Object System.Windows.Forms.Form; $owner.TopMost = $true;"
                  "$d = New-Object System.Windows.Forms.OpenFileDialog; $d.Title = '" + prompt.replace("'", "") + "';"
                  "$d.Filter = 'Videos and sound|*.mp4;*.mov;*.m4v;*.mkv;*.avi;*.mxf;*.mts;*.webm;*.wav;*.mp3;*.m4a|"
                  "All files|*.*';"
                  "if ($d.ShowDialog($owner) -eq 'OK') { $d.FileName }")
        res = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=900)
        return res.stdout.strip()
    # "activate" brings the picker in front of the browser
    res = subprocess.run(["osascript", "-e", "activate", "-e",
                          f'POSIX path of (choose file with prompt "{prompt.replace(chr(34), "")}")'],
                         capture_output=True, text=True, errors="replace", timeout=900)
    return res.stdout.strip() if res.returncode == 0 else ""  # cancelling the picker is an error in AppleScript


def reveal(path: str):
    """Show a file in Finder / Explorer."""
    if IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        subprocess.Popen(["open", "-R", path])
