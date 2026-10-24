#!/usr/bin/env python3
"""
Build the downloads in dist/, one per operating system:

  ScriptCutter-Mac.zip       ScriptCutter/Install ScriptCutter.command, Uninstall ..., Read me.txt, app/
  ScriptCutter-Windows.zip   ScriptCutter/Install ScriptCutter.bat, Uninstall ..., Read me.txt, app/
  install-mac.sh             the one-line install (curl ... | bash): fetches ScriptCutter-Mac.zip itself
  install-windows.ps1        the one-line install (irm ... | iex): fetches ScriptCutter-Windows.zip itself
  *.sha256, RELEASE_NOTES.md

The installer files come from the Mac/ and Windows/ folders; app/ holds what
install.py copies into place (install.APP_FILES, without the other system's icon)
plus that system's install script.

    python3 tools/build_release.py
"""
import hashlib
import os
import re
import shutil
import stat
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from install import APP_FILES  # noqa: E402

DIST = os.path.join(ROOT, "dist")
REPO = "Dassuvam/scriptcutter"
SYSTEMS = {
    "Mac": {"zip": "ScriptCutter-Mac.zip", "launchers": ["Install ScriptCutter.command", "Uninstall ScriptCutter.command"],
            "script": "install.sh", "online": "install-mac.sh", "crlf": False, "skip": (".ico", ".png")},
    "Windows": {"zip": "ScriptCutter-Windows.zip", "launchers": ["Install ScriptCutter.bat", "Uninstall ScriptCutter.bat"],
                "script": "install.ps1", "online": "install-windows.ps1", "crlf": True, "skip": (".icns", ".png")},
}
EXECUTABLE = (".command", ".sh", ".py")
TEXT = (".bat", ".ps1", ".txt", ".md")


def app_files():
    """(path in the repository, path inside app/) for everything install.py copies into place."""
    for item in APP_FILES:
        full = os.path.join(ROOT, item)
        if os.path.isfile(full):
            yield item, item
            continue
        for d, dirs, names in os.walk(full):
            dirs[:] = sorted(x for x in dirs if x != "__pycache__")
            for n in sorted(names):
                if not n.endswith(".pyc") and n != ".DS_Store":
                    rel = os.path.relpath(os.path.join(d, n), ROOT)
                    yield rel, rel


def _read(rel: str, crlf: bool) -> bytes:
    with open(os.path.join(ROOT, rel), "rb") as f:
        data = f.read()
    if crlf and rel.endswith(TEXT):  # Windows tools (cmd, Notepad) expect CRLF line ends
        data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return data


def build_zip(system: str, out_dir: str) -> str:
    spec = SYSTEMS[system]
    entries = [(f"{system}/{name}", name) for name in spec["launchers"]]
    entries.append((f"{system}/README.md", "Read me.txt"))
    entries.append((f"{system}/{spec['script']}", f"app/{spec['script']}"))
    entries += [(src, "app/" + dest) for src, dest in app_files() if not dest.endswith(spec["skip"])]  # other OS's icon
    path = os.path.join(out_dir, spec["zip"])
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for src, dest in entries:
            info = zipfile.ZipInfo("ScriptCutter/" + dest.replace(os.sep, "/"))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix, so macOS keeps the "can run" permission of the .command files
            # a regular file with its permissions: without the file type, macOS's unzip drops the "can run" bit
            info.external_attr = (stat.S_IFREG | (0o755 if dest.endswith(EXECUTABLE) else 0o644)) << 16
            z.writestr(info, _read(src.replace("/", os.sep), spec["crlf"]))
    return path


def release_notes(version: str) -> str:
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(rf"^## {re.escape(version)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    latest = f"https://github.com/{REPO}/releases/latest/download"
    return ((m.group(1).strip() if m else "") + "\n\n## Download\n\n"
            f"- **Mac:** [ScriptCutter-Mac.zip]({latest}/ScriptCutter-Mac.zip), then double-click "
            "*Install ScriptCutter*. Or, with no download warning, paste in Terminal:\n"
            f"  `curl -fsSL {latest}/install-mac.sh | bash`\n"
            f"- **Windows:** [ScriptCutter-Windows.zip]({latest}/ScriptCutter-Windows.zip), extract it, then "
            "double-click *Install ScriptCutter*. Or, with no download warning, paste in PowerShell:\n"
            f"  `irm {latest}/install-windows.ps1 | iex`\n")


def build(out_dir: str = DIST) -> list:
    with open(os.path.join(ROOT, "VERSION")) as f:
        version = f.read().strip()
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    built = []
    for system, spec in SYSTEMS.items():
        built.append(build_zip(system, out_dir))
        online = os.path.join(out_dir, spec["online"])
        with open(online, "wb") as f:
            f.write(_read(os.path.join(system, spec["script"]), spec["crlf"]))
        built.append(online)
    for path in built:
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        with open(path + ".sha256", "w") as f:
            f.write(f"{digest}  {os.path.basename(path)}\n")
    with open(os.path.join(out_dir, "RELEASE_NOTES.md"), "w", encoding="utf-8") as f:
        f.write(release_notes(version))
    for path in built:
        print(f"Built {os.path.relpath(path, ROOT)} ({os.path.getsize(path) // 1024} KB), version {version}")
    return built


if __name__ == "__main__":
    build()
