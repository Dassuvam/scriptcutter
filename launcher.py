#!/usr/bin/env python3
"""
Open ScriptCutter in the browser, starting its local server first if needed.
Started by Resolve's Workspace > Scripts > ScriptCutter entry, by ScriptCutter.app
(macOS) and the Start menu / desktop shortcut (Windows), and at the end of the install.

  * A ScriptCutter that is already running from this folder and version is reused.
  * An older or different copy still running (from before an update) is asked
    to stop, so the menu never opens an old version.
  * If port 7870 is used by another program, the next free port up to 7879 is used.
  * If the server does not start, the log file opens, so the problem is visible
    even when there is no terminal (started from Resolve).
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from cutter import __version__, paths  # noqa: E402

PORTS = range(7870, 7880)


def running_on(port: int, timeout: float = 3.0) -> dict:
    """The /api/status of a ScriptCutter on this port, or {} when there is none (or another program)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=timeout) as r:
            status = json.loads(r.read().decode("utf-8"))
            return status if "app_version" in status else {}
    except (OSError, ValueError):
        return {}


def port_free(port: int) -> bool:
    """
    True when a server could listen on this port: tried by claiming it for a moment
    (connecting is not enough: a busy program may hold the port without answering).
    Like the server, connections that are just closing down don't count (macOS).
    """
    with socket.socket() as s:
        if not paths.IS_WINDOWS:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _listening_pid(port: int):
    """The process id listening on this port, or None."""
    try:
        if paths.IS_WINDOWS:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[1] == f"127.0.0.1:{port}" and parts[3] == "LISTENING":
                    return int(parts[4])
            return None
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True,
                             text=True).stdout.split()
        return int(out[0]) if out else None
    except (OSError, ValueError):
        return None


def _wait_free(port: int, seconds: float) -> bool:
    for _ in range(int(seconds / 0.25)):
        if port_free(port):
            return True
        time.sleep(0.25)
    return False


def stop(port: int):
    """
    Stop an old ScriptCutter on this port (only called after its /api/status said it is
    ScriptCutter). Asked politely first; versions before 3.2.0 don't know that request,
    so then its process is ended.
    """
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "X-ScriptCutter": "1"})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except OSError:
        pass
    if _wait_free(port, 3):
        return
    pid = _listening_pid(port)
    if pid:
        if paths.IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    _wait_free(port, 5)


def start_server(port: int) -> bool:
    """Start web/server.py in the background (no window), detached from Resolve; True once it answers."""
    python = sys.executable
    options = {}
    if paths.IS_WINDOWS:
        pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")  # no console window
        python = pythonw if os.path.isfile(pythonw) else python
        options["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True  # keeps running when Resolve's script ends
    with open(paths.log_file(), "a") as log:
        proc = subprocess.Popen([python, os.path.join(ROOT, "web", "server.py"), str(port)], cwd=ROOT,
                                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **options)
    for _ in range(120):  # up to 30 s (a cold start of Python on a slow PC)
        time.sleep(0.25)
        if proc.poll() is not None:  # it stopped: the reason is in the log
            return False
        if running_on(port, timeout=1.0):
            return True
    return False


def show_log():
    """Open the log file in the system's text viewer."""
    log = paths.log_file()
    if paths.IS_WINDOWS:
        os.startfile(log)  # noqa: S606 (Windows only)
    else:
        subprocess.Popen(["open", log])


def main():
    for port in PORTS:
        status = running_on(port)
        if not status:
            continue
        if status["app_version"] == __version__ and os.path.realpath(status.get("app_root", "")) == ROOT:
            webbrowser.open(f"http://127.0.0.1:{port}")
            return
        stop(port)  # an older or different copy: replace it
    port = next((p for p in PORTS if port_free(p)), None)
    if port is None:
        sys.exit(f"Ports {PORTS.start}-{PORTS.stop - 1} are all in use by other programs.")
    if not start_server(port):
        show_log()
        sys.exit(f"ScriptCutter did not start. See the log: {paths.log_file()}")
    webbrowser.open(f"http://127.0.0.1:{port}")


if __name__ == "__main__":
    main()
