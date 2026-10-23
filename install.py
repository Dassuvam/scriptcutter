#!/usr/bin/env python3
"""
ScriptCutter installer, shared by macOS and Windows.

install.sh (macOS) and install.ps1 (Windows) first install the system
programs (ffmpeg and whisper.cpp) and then run this file, which:
  1. finds ffmpeg, ffprobe, whisper-cli and Resolve's fuscript and remembers where they are,
  2. downloads the speech models,
  3. adds ScriptCutter to DaVinci Resolve's Workspace > Scripts menu,
  4. runs the self-test.

  python3 install.py                        asks which optional models to download
  python3 install.py --yes                  no questions (small model only)
  python3 install.py --models small,large-v3
"""
import argparse
import os
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from cutter import __version__, paths  # noqa: E402
from cutter.vad import SILERO_FILES, SILERO_URL  # noqa: E402

WHISPER_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
MODELS = {  # name: (file, size, what it is good for)
    "small": ("ggml-small.bin", "466 MB", "good all-rounder, fast (installed by default)"),
    "large-v3": ("ggml-large-v3.bin", "2.9 GB", "best for Hindi, Hinglish and other non-English speech"),
    "large-v3-turbo": ("ggml-large-v3-turbo.bin", "1.5 GB", "best for English"),
}
MENU_TEMPLATES = ["ScriptCutter.lua.template", "ScriptCutter - Build Timeline.lua.template"]
OLD_MENU_SCRIPTS = ["ScriptCutterV2.lua"]


def step(text: str):
    print(f"\n== {text}", flush=True)


def find_programs() -> dict:
    found = {name: paths.find_program(name)
             for name in ("ffmpeg", "ffprobe", "whisper-cli", "whisper-vad-speech-segments", "fuscript")}
    found["whisper-cli"] = found["whisper-cli"] or paths.find_program("whisper-cpp")
    for name, where in found.items():
        print(f"  {name:28s} {where or '-'}")
    missing = [n for n in ("ffmpeg", "ffprobe", "whisper-cli") if not found[n]]
    if missing:
        sys.exit(f"\nMissing: {', '.join(missing)}. Run install.sh (macOS) or "
                 "'Install ScriptCutter.bat' (Windows), which install them first.")
    if not found["fuscript"]:
        print("  (DaVinci Resolve was not found. ScriptCutter still works with video files.)")
    paths.save_config({"programs": {k: v for k, v in found.items() if v}})
    return found


def download(url: str, dest: str):
    if os.path.isfile(dest) and os.path.getsize(dest) > 100_000:
        print(f"  {os.path.basename(dest)} already installed")
        return
    print(f"  downloading {os.path.basename(dest)} ...", flush=True)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    curl = shutil.which("curl")
    if curl:
        subprocess.run([curl, "-L", "--fail", "--progress-bar", "-o", part, url], check=True)
    else:
        with urllib.request.urlopen(url) as resp, open(part, "wb") as out:
            shutil.copyfileobj(resp, out, 1 << 20)
    os.replace(part, dest)


def choose_models(args) -> list:
    if args.models:
        names = [m.strip() for m in args.models.split(",") if m.strip()]
        unknown = [m for m in names if m not in MODELS]
        if unknown:
            sys.exit(f"Unknown model(s): {', '.join(unknown)}. Choose from: {', '.join(MODELS)}")
        return names
    names = ["small"]
    if args.yes or not sys.stdin.isatty():
        return names
    print("  Bigger models give better results but are slower (and big downloads):")
    for name in ("large-v3", "large-v3-turbo"):
        file, size, use = MODELS[name]
        if input(f"  Download {name} ({size}, {use})? [y/N] ").strip().lower().startswith("y"):
            names.append(name)
    return names


def install_models(names: list):
    for name in names:
        file = MODELS[name][0]
        download(WHISPER_URL + file, os.path.join(paths.model_dir(), file))
    download(SILERO_URL, os.path.join(paths.model_dir(), SILERO_FILES[0]))


def python_for_resolve() -> str:
    """The Python Resolve should use to start the app (windowless on Windows)."""
    if paths.IS_WINDOWS:
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if os.path.isfile(pythonw):
            return pythonw
    return sys.executable


def install_resolve_menu():
    target = paths.resolve_scripts_dir()
    os.makedirs(target, exist_ok=True)
    values = {
        "@APP_DIR@": ROOT.replace("\\", "/"),
        "@BRIDGE_DIR@": paths.bridge_dir().replace("\\", "/"),
        "@PYTHON@": python_for_resolve(),
    }
    for template in MENU_TEMPLATES:
        with open(os.path.join(ROOT, "resolve", template), "r", encoding="utf-8") as f:
            text = f.read()
        for key, value in values.items():
            text = text.replace(key, value)
        with open(os.path.join(target, template.replace(".template", "")), "w", encoding="utf-8") as f:
            f.write(text)
    for old in OLD_MENU_SCRIPTS:
        if os.path.isfile(os.path.join(target, old)):
            os.unlink(os.path.join(target, old))
    print(f"  Added to {target}")
    print("  In Resolve: Workspace > Scripts > ScriptCutter (restart Resolve once).")
    print("  Resolve Studio: also set Preferences > System > General > External scripting using = Local.")


def self_test() -> bool:
    res = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "test_scriptcutter.py")],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    print("  all tests passed" if res.returncode == 0 else res.stdout[-1500:])
    return res.returncode == 0


def main():
    ap = argparse.ArgumentParser(description="Install ScriptCutter")
    ap.add_argument("--yes", action="store_true", help="don't ask questions")
    ap.add_argument("--models", help=f"comma separated, from: {', '.join(MODELS)}")
    ap.add_argument("--skip-tests", action="store_true")
    args = ap.parse_args()
    if sys.version_info < (3, 9):
        sys.exit("ScriptCutter needs Python 3.9 or newer.")

    print(f"ScriptCutter {__version__} setup")
    step("1/4  Programs")
    find_programs()
    step("2/4  Speech models")
    install_models(choose_models(args))
    step("3/4  DaVinci Resolve menu")
    install_resolve_menu()
    step("4/4  Self-test")
    ok = args.skip_tests or self_test()
    print("\nDone." if ok else "\nInstalled, but some tests failed. See above.")
    print("Open ScriptCutter from Resolve (Workspace > Scripts > ScriptCutter) or with the 'Open ScriptCutter' file.")


if __name__ == "__main__":
    main()
