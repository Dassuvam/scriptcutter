#!/usr/bin/env python3
"""Start the ScriptCutter server in the background (if it isn't running) and open it in the browser."""
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from cutter import paths  # noqa: E402

PORT = 7870
URL = f"http://127.0.0.1:{PORT}"


def server_running() -> bool:
    try:
        with urllib.request.urlopen(URL + "/api/status", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def port_taken() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def start_server():
    python = sys.executable
    options = {}
    if paths.IS_WINDOWS:
        pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
        python = pythonw if os.path.isfile(pythonw) else python
        options["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    with open(paths.log_file(), "a") as log:
        subprocess.Popen([python, os.path.join(ROOT, "web", "server.py"), str(PORT)], cwd=ROOT,
                         stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **options)
    for _ in range(60):
        time.sleep(0.25)
        if server_running():
            return
    sys.exit(f"ScriptCutter did not start. See the log: {paths.log_file()}")


def main():
    if not server_running():
        if port_taken():
            sys.exit(f"Port {PORT} is used by another program. Close it and try again.")
        start_server()
    webbrowser.open(URL)


if __name__ == "__main__":
    main()
