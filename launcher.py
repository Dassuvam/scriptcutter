#!/usr/bin/env python3
"""Starts the ScriptCutter v2 server (if needed) and opens the browser. Logs go to ~/Library/Logs."""
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

PORT = 7870
ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.expanduser("~/Library/Logs/ScriptCutterV2.log")


def running() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def port_busy() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def main():
    if not running():
        if port_busy():
            sys.exit(f"Port {PORT} is used by another program.")
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as log:
            subprocess.Popen([sys.executable, os.path.join(ROOT, "web", "server.py"), str(PORT)],
                             cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(40):
            time.sleep(0.25)
            if running():
                break
    webbrowser.open(f"http://127.0.0.1:{PORT}")


if __name__ == "__main__":
    main()
