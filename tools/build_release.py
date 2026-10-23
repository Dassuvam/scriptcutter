#!/usr/bin/env python3
"""
Build dist/ScriptCutter.zip (the download for users) and dist/RELEASE_NOTES.md.

    python3 tools/build_release.py
"""
import hashlib
import os
import re
import shutil
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
INCLUDE = ["cutter", "web", "resolve", "tests", "cli.py", "launcher.py", "install.py", "install.sh", "install.ps1",
           "uninstall.py", "Install ScriptCutter.command", "Open ScriptCutter.command", "Install ScriptCutter.bat",
           "Open ScriptCutter.bat", "README.md", "CHANGELOG.md", "LICENSE", "VERSION"]
EXECUTABLE = (".command", ".sh", ".py")


def files():
    for item in INCLUDE:
        full = os.path.join(ROOT, item)
        if os.path.isfile(full):
            yield item
            continue
        for d, dirs, names in os.walk(full):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            for n in sorted(names):
                if not n.endswith(".pyc") and n != ".DS_Store":
                    yield os.path.relpath(os.path.join(d, n), ROOT)


def release_notes(version: str) -> str:
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(rf"^## {re.escape(version)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return (m.group(1).strip() if m else "") + "\n\nDownload **ScriptCutter.zip** and follow README.md.\n"


def main():
    with open(os.path.join(ROOT, "VERSION")) as f:
        version = f.read().strip()
    shutil.rmtree(DIST, ignore_errors=True)
    os.makedirs(DIST)
    zip_path = os.path.join(DIST, "ScriptCutter.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files():
            info = zipfile.ZipInfo("ScriptCutter/" + rel.replace(os.sep, "/"))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if rel.endswith(EXECUTABLE) else 0o644) << 16
            with open(os.path.join(ROOT, rel), "rb") as f:
                z.writestr(info, f.read())
    with open(zip_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(zip_path + ".sha256", "w") as f:
        f.write(f"{digest}  ScriptCutter.zip\n")
    with open(os.path.join(DIST, "RELEASE_NOTES.md"), "w", encoding="utf-8") as f:
        f.write(release_notes(version))
    print(f"Built {zip_path} ({os.path.getsize(zip_path) // 1024} KB), version {version}")


if __name__ == "__main__":
    main()
