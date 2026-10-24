#!/usr/bin/env python3
"""
Remove ScriptCutter: the Resolve menu entry, the ScriptCutter app (macOS) or the shortcuts and
the Settings > Apps entry (Windows), the installed app, its tools and the analysis cache.
Asks before deleting learned choices and the speech models.
Started by "Uninstall ScriptCutter" and by Settings > Apps > ScriptCutter > Uninstall.
"""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import install  # noqa: E402  (the names of everything the installer adds)
from cutter import paths  # noqa: E402

MENU_SCRIPTS = ["ScriptCutter.lua"] + install.OLD_MENU_SCRIPTS


def ask(question: str) -> bool:
    try:
        return input(question + " [y/N] ").strip().lower().startswith("y")
    except EOFError:
        return False


def main():
    install.stop_running()
    os.chdir(os.path.expanduser("~"))  # Windows can't delete the folder a program is working in
    for name in MENU_SCRIPTS:
        p = os.path.join(paths.resolve_scripts_dir(), name)
        if os.path.isfile(p):
            os.unlink(p)
            print(f"Removed the Resolve menu entry {name}")
    if paths.IS_WINDOWS:
        script = (f"foreach ($folder in {install.SHORTCUT_FOLDERS}) "
                  f"{{ Remove-Item (Join-Path $folder '{install.SHORTCUT}') -ErrorAction SilentlyContinue }}")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True)
        import winreg
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, install.UNINSTALL_KEY)
        except OSError:
            pass
        shutil.rmtree(paths.tools_dir(), ignore_errors=True)
        print("Removed the shortcuts, ffmpeg and whisper.cpp")
    else:
        shutil.rmtree(install.MAC_APP, ignore_errors=True)
        print("Removed ScriptCutter from Applications")
    shutil.rmtree(paths.app_dir(), ignore_errors=True)
    shutil.rmtree(paths.cache_dir(), ignore_errors=True)
    print("Removed ScriptCutter and its analysis cache.")
    if ask("Also delete settings and what ScriptCutter learned from your choices?"):
        shutil.rmtree(paths.data_dir(), ignore_errors=True)
    if ask(f"Also delete the speech models in {paths.model_dir()} (several GB)?"):
        shutil.rmtree(paths.model_dir(), ignore_errors=True)
    if paths.IS_WINDOWS:
        print("Done. Python stays installed for other programs (remove it in Settings > Apps if you like).")
    else:
        print("Done. ffmpeg and whisper.cpp stay installed (remove them with: brew uninstall whisper-cpp ffmpeg).")


if __name__ == "__main__":
    main()
