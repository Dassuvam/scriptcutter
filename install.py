#!/usr/bin/env python3
"""
ScriptCutter installer, shared by macOS and Windows.

Mac/install.sh and Windows/install.ps1 first install the system programs
(ffmpeg, whisper.cpp and, on Windows, Python) and then run this file, which:
  1. copies ScriptCutter to its own folder (paths.app_dir()), so the download can be deleted,
  2. finds ffmpeg, whisper-cli and Resolve's fuscript and remembers where they are,
  3. downloads the speech models,
  4. adds ScriptCutter to DaVinci Resolve's Workspace > Scripts menu,
  5. adds a way to open it without Resolve: ScriptCutter.app in ~/Applications (macOS),
     Start menu and desktop shortcuts and an entry in Settings > Apps (Windows),
  6. checks that speech recognition really runs here (and on Windows picks the NVIDIA
     build of whisper.cpp when it works), then opens ScriptCutter.

  python3 install.py                        asks which optional models to download
  python3 install.py --yes                  no questions (small model only)
  python3 install.py --models small,large-v3
  python3 install.py --here                 developers: register this folder instead of copying it
  python3 install.py --no-open              don't open ScriptCutter at the end
"""
import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from cutter import __version__, paths  # noqa: E402
from cutter.vad import SILERO_FILES, SILERO_URL  # noqa: E402

# what an installed ScriptCutter contains (tools/build_release.py ships the same list)
APP_FILES = ["cutter", "web", "resolve", "assets", "launcher.py", "install.py", "uninstall.py", "cli.py",
             "VERSION", "CHANGELOG.md", "LICENSE", "AUTHORS.md"]
WHISPER_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
MODELS = {  # name: (file, size, what it is good for)
    "small": ("ggml-small.bin", "466 MB", "good all-rounder, fast (installed by default)"),
    "large-v3": ("ggml-large-v3.bin", "2.9 GB", "best for Hindi, Hinglish and other non-English speech"),
    "large-v3-turbo": ("ggml-large-v3-turbo.bin", "1.5 GB", "best for English"),
}
MENU_TEMPLATES = ["ScriptCutter.lua.template"]
OLD_MENU_SCRIPTS = ["ScriptCutterV2.lua", "ScriptCutter - Build Timeline.lua"]  # earlier versions' menu entries
MAC_APP = os.path.expanduser("~/Applications/ScriptCutter.app")
SHORTCUT = "ScriptCutter.lnk"
SHORTCUT_FOLDERS = "@([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))"  # PowerShell
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ScriptCutter"  # Settings > Apps


def step(text: str):
    print(f"\n== {text}", flush=True)


def stop_running():
    """An older ScriptCutter still running keeps its folder in use (Windows): stop it first."""
    import launcher
    for port in launcher.PORTS:
        if launcher.running_on(port, timeout=1.0):
            launcher.stop(port)


def install_app(here: bool) -> str:
    """Copy ScriptCutter to its own folder and return that folder (this one with --here)."""
    target = paths.app_dir()
    if here or os.path.realpath(ROOT) == os.path.realpath(target):
        print(f"  {ROOT}")
        return ROOT
    stop_running()
    new = target + ".new"  # copied completely first, so a failed copy never leaves half an app
    shutil.rmtree(new, ignore_errors=True)
    os.makedirs(new)
    skip = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    for item in APP_FILES:
        src = os.path.join(ROOT, item)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(new, item), ignore=skip)
        elif os.path.isfile(src):
            shutil.copy2(src, os.path.join(new, item))
    shutil.rmtree(target, ignore_errors=True)
    try:
        os.replace(new, target)
    except OSError:
        sys.exit(f"\nThe old ScriptCutter in {target} is in use. Close ScriptCutter and DaVinci Resolve, "
                 "then run the installer again.")
    print(f"  {target}")
    return target


def find_programs() -> dict:
    found = {name: paths.find_program(name)
             for name in ("ffmpeg", "ffprobe", "whisper-cli", "whisper-vad-speech-segments", "fuscript")}
    found["whisper-cli"] = found["whisper-cli"] or paths.find_program("whisper-cpp")
    for name, where in found.items():
        print(f"  {name:28s} {where or '-'}")
    missing = [n for n in ("ffmpeg", "ffprobe", "whisper-cli") if not found[n]]
    if missing:
        sys.exit(f"\nMissing: {', '.join(missing)}. Run 'Install ScriptCutter' again "
                 "(it installs them before this step).")
    if not found["fuscript"]:
        print("  (DaVinci Resolve was not found. ScriptCutter still works with video files.)")
    paths.save_config({"programs": {k: v for k, v in found.items() if v}})
    return found


def download(url: str, dest: str):
    """
    Download a model next to the others. A .part file is renamed only when complete,
    so an interrupted download never looks installed; running the installer again
    continues it where it stopped (curl -C -) instead of starting over.
    """
    if os.path.isfile(dest) and os.path.getsize(dest) > 100_000:
        print(f"  {os.path.basename(dest)} already installed")
        return
    print(f"  downloading {os.path.basename(dest)} ...", flush=True)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    curl = shutil.which("curl")  # built into macOS and Windows 10+
    if curl:
        res = subprocess.run([curl, "-L", "--fail", "--progress-bar", "--retry", "5", "--retry-delay", "3",
                              "-C", "-", "-o", part, url])
        if res.returncode != 0:
            sys.exit(f"\nThe download of {os.path.basename(dest)} stopped (check the internet connection). "
                     "Run the installer again to continue it.")
    else:
        with urllib.request.urlopen(url) as resp, open(part, "wb") as out:
            shutil.copyfileobj(resp, out, 1 << 20)
    if os.path.getsize(part) <= 100_000:  # an error page instead of a model
        os.unlink(part)
        sys.exit(f"\nThe download of {os.path.basename(dest)} failed. Run the installer again.")
    os.replace(part, dest)


def cuda_whisper() -> str:
    """Windows: the NVIDIA build of whisper-cli that install.ps1 downloaded, or ""."""
    exe = os.path.join(paths.tools_dir(), "whisper-cuda", "whisper-cli.exe")
    return exe if paths.IS_WINDOWS and os.path.isfile(exe) else ""


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
    # Apple Silicon and NVIDIA cards run the large models at a usable speed: suggest them there
    fast = (not paths.IS_WINDOWS and platform.machine() == "arm64") or bool(cuda_whisper())
    print("  Bigger models give better results but are slower and big downloads.")
    if not fast:
        print("  On this computer they run on the processor, so they are slow: the small model is the best start.")
    for name in ("large-v3", "large-v3-turbo"):
        file, size, use = MODELS[name]
        answer = input(f"  Download {name} ({size}, {use})? {'[Y/n]' if fast else '[y/N]'} ").strip().lower()
        if answer.startswith("y") or (fast and not answer):
            names.append(name)
    return names


def install_models(names: list) -> str:
    """Download the models; returns the smallest one, for the check."""
    for name in names:
        file = MODELS[name][0]
        download(WHISPER_URL + file, os.path.join(paths.model_dir(), file))
    download(SILERO_URL, os.path.join(paths.model_dir(), SILERO_FILES[0]))
    installed = [os.path.join(paths.model_dir(), MODELS[n][0]) for n in MODELS]
    return next((p for p in installed if os.path.isfile(p)), "")


def python_for_app() -> str:
    """The Python that starts the app from Resolve and the shortcuts (windowless on Windows)."""
    if paths.IS_WINDOWS:
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        return pythonw if os.path.isfile(pythonw) else sys.executable
    if os.path.realpath(sys.executable).startswith(("/Library/Developer/", "/Applications/Xcode")):
        return "/usr/bin/python3"  # Apple's Python: this path stays the same when the tools update
    return sys.executable


def install_resolve_menu(app: str):
    target = paths.resolve_scripts_dir()
    os.makedirs(target, exist_ok=True)
    values = {
        "@APP_DIR@": app.replace("\\", "/"),
        "@BRIDGE_DIR@": paths.bridge_dir().replace("\\", "/"),
        "@PYTHON@": python_for_app(),
    }
    for template in MENU_TEMPLATES:
        with open(os.path.join(app, "resolve", template), "r", encoding="utf-8") as f:
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


def _applescript_text(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def add_mac_app(app: str):
    """~/Applications/ScriptCutter.app opens ScriptCutter from Launchpad, Spotlight or the Dock."""
    command = f"quoted form of {_applescript_text(python_for_app())} & \" \" & " \
              f"quoted form of {_applescript_text(os.path.join(app, 'launcher.py'))}"
    script = (f"try\n  do shell script {command}\n"
              "on error message\n"
              "  display dialog message buttons {\"OK\"} default button 1 with title \"ScriptCutter\" with icon caution\n"
              "end try")
    os.makedirs(os.path.dirname(MAC_APP), exist_ok=True)
    shutil.rmtree(MAC_APP, ignore_errors=True)
    res = subprocess.run(["osacompile", "-o", MAC_APP, "-e", script], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"  ScriptCutter.app could not be made: {res.stderr.strip()}")
        return
    icon = os.path.join(app, "assets", "ScriptCutter.icns")
    if os.path.isfile(icon):
        shutil.copy(icon, os.path.join(MAC_APP, "Contents", "Resources", "applet.icns"))
        # a changed bundle needs a fresh (local) signature to keep opening on Apple Silicon
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", MAC_APP], capture_output=True)
    print(f"  {MAC_APP}  (Launchpad, Spotlight: ScriptCutter)")


def add_windows_entries(app: str):
    """Start menu and desktop shortcuts, and ScriptCutter in Settings > Apps (this user only, no admin)."""
    icon = os.path.join(app, "assets", "ScriptCutter.ico")
    env = dict(os.environ, SC_TARGET=python_for_app(), SC_ARGS=f'"{os.path.join(app, "launcher.py")}"',
               SC_DIR=app, SC_ICON=icon)
    script = ("$ws = New-Object -ComObject WScript.Shell; "
              f"foreach ($folder in {SHORTCUT_FOLDERS}) {{ "
              f"$s = $ws.CreateShortcut((Join-Path $folder '{SHORTCUT}')); "
              "$s.TargetPath = $env:SC_TARGET; $s.Arguments = $env:SC_ARGS; $s.WorkingDirectory = $env:SC_DIR; "
              "$s.IconLocation = $env:SC_ICON; $s.Description = 'Cut retakes from your recordings'; $s.Save() }")
    res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], env=env,
                         capture_output=True, text=True, errors="replace")
    print("  Start menu and desktop: ScriptCutter" if res.returncode == 0
          else f"  The shortcuts could not be made: {res.stderr.strip()[-300:]}")

    import winreg
    python = os.path.join(os.path.dirname(sys.executable), "python.exe")  # a console, for uninstall's questions
    size = sum(os.path.getsize(os.path.join(d, f)) for top in (app, paths.tools_dir())
               for d, _, files in os.walk(top) for f in files)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in (("DisplayName", "ScriptCutter"), ("DisplayVersion", __version__),
                            ("Publisher", "Suvam Das"), ("InstallLocation", app), ("DisplayIcon", icon),
                            ("UninstallString", f'"{python}" "{os.path.join(app, "uninstall.py")}"'),
                            ("URLInfoAbout", "https://github.com/Dassuvam/scriptcutter")):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", size // 1024)):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
    print("  Settings > Apps: ScriptCutter (to uninstall)")


def run_whisper(exe: str, model: str, ffmpeg: str, *extra: str) -> tuple:
    """Two seconds of generated sound through whisper-cli: (it worked, everything it printed)."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "check.wav")
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-ar", "16000", "-ac", "1", wav], capture_output=True)
        try:
            res = subprocess.run([exe, "-m", model, "-f", wav, "-nt", *extra], capture_output=True, text=True,
                                 errors="replace", timeout=600)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, str(e)
    return res.returncode == 0, res.stdout + res.stderr


def use_nvidia(found: dict, model: str):
    """Windows: switch to the NVIDIA build of whisper-cli only when it really runs on the card here."""
    cuda = cuda_whisper()
    if not cuda:
        return
    ok, out = run_whisper(cuda, model, found["ffmpeg"])
    if ok and re.search(r"found [1-9]\d* CUDA devices", out):
        found["whisper-cli"] = cuda
        paths.save_config({"programs": {k: v for k, v in found.items() if v}, "whisper_gpu": True})
        print("  the NVIDIA graphics card does the speech recognition")
    else:
        paths.save_config({"whisper_gpu": False})
        print("  the NVIDIA build did not start on this card (an NVIDIA driver update may help);"
              " the processor does the speech recognition")


def check(found: dict, model: str) -> bool:
    """Speech recognition end to end, the same way the app runs it."""
    if not model:
        print("  no speech model installed")
        return False
    ok, out = run_whisper(found["whisper-cli"], model, found["ffmpeg"])
    if not ok and (not paths.IS_WINDOWS or paths.config_value("whisper_gpu")):
        ok, out = run_whisper(found["whisper-cli"], model, found["ffmpeg"], "-ng")  # graphics chip failed
    print("  speech recognition works" if ok else f"  speech recognition failed:\n{out.strip()[-800:]}")
    return ok


def open_app(app: str):
    subprocess.run([sys.executable, os.path.join(app, "launcher.py")])


def main():
    ap = argparse.ArgumentParser(description="Install ScriptCutter")
    ap.add_argument("--yes", action="store_true", help="don't ask questions")
    ap.add_argument("--models", help=f"comma separated, from: {', '.join(MODELS)}")
    ap.add_argument("--here", action="store_true", help="register this folder instead of copying it")
    ap.add_argument("--no-open", action="store_true", help="don't open ScriptCutter at the end")
    args = ap.parse_args()
    if sys.version_info < (3, 9):
        sys.exit("ScriptCutter needs Python 3.9 or newer.")

    print(f"ScriptCutter {__version__} setup")
    step("1/6  ScriptCutter")
    app = install_app(args.here)
    step("2/6  Programs")
    found = find_programs()
    step("3/6  Speech models")
    model = install_models(choose_models(args))
    step("4/6  DaVinci Resolve menu")
    install_resolve_menu(app)
    step("5/6  Open without DaVinci Resolve")
    if paths.IS_WINDOWS:
        add_windows_entries(app)
    else:
        add_mac_app(app)
    step("6/6  Check")
    use_nvidia(found, model)
    ok = check(found, model)

    where = ("the Start menu or the desktop" if paths.IS_WINDOWS else "Launchpad or Spotlight")
    print("\nDone." if ok else "\nInstalled, but the check failed (see above). Send this window to "
                               "dassuvam2005@gmail.com if it keeps failing.")
    print(f"Open ScriptCutter from {where} (ScriptCutter), or in DaVinci Resolve from "
          "Workspace > Scripts > ScriptCutter. You can delete the downloaded folder.")
    if not args.no_open:
        open_app(app)


if __name__ == "__main__":
    main()
