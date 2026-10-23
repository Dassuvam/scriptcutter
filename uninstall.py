#!/usr/bin/env python3
"""Remove ScriptCutter's Resolve menu entries and caches. Asks before deleting learned choices and models."""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cutter import paths  # noqa: E402

MENU_SCRIPTS = ["ScriptCutter.lua", "ScriptCutter - Build Timeline.lua", "ScriptCutterV2.lua"]


def ask(question: str) -> bool:
    return input(question + " [y/N] ").strip().lower().startswith("y")


def main():
    for name in MENU_SCRIPTS:
        p = os.path.join(paths.resolve_scripts_dir(), name)
        if os.path.isfile(p):
            os.unlink(p)
            print(f"Removed the Resolve menu entry {name}")
    shutil.rmtree(paths.cache_dir(), ignore_errors=True)
    print("Removed the analysis cache.")
    if ask("Also delete settings and what ScriptCutter learned from your choices?"):
        shutil.rmtree(paths.data_dir(), ignore_errors=True)
    if ask(f"Also delete the speech models in {paths.model_dir()} (several GB)?"):
        shutil.rmtree(paths.model_dir(), ignore_errors=True)
    print("Done. You can now delete the ScriptCutter folder.")


if __name__ == "__main__":
    main()
