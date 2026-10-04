#!/usr/bin/python3
"""ribi-shell - the persistent Ribi OS desktop session.

Started by the live init and by the display manager. It owns the user session:
it paints the wallpaper, runs the dock, and keeps the session alive, restarting
the dock if it ever exits so the desktop never ends up without a panel. It
shuts its children down cleanly on logout or signal.
"""

import os
import signal
import subprocess
import sys
import time

LOG = "/tmp/ribi-shell.log"


def log(message: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(f"[ribi-shell] {message}\n")
    except OSError:
        pass


def session_env() -> dict:
    home = os.path.expanduser("~") or "/home/ribi"
    env = dict(os.environ)
    env.update(
        HOME=home,
        USER=env.get("USER", "ribi"),
        LOGNAME=env.get("LOGNAME", "ribi"),
        DISPLAY=env.get("DISPLAY", ":0"),
        XAUTHORITY=env.get("XAUTHORITY", "/run/ribi/server.auth"),
        XDG_RUNTIME_DIR=env.get("XDG_RUNTIME_DIR", "/run/user/1000"),
        XDG_CURRENT_DESKTOP="Ribi",
        XDG_SESSION_DESKTOP="ribi",
        XDG_SESSION_TYPE="x11",
        GDK_BACKEND="x11",
        GTK_ICON_THEME="RibiShapes",
        GIO_USE_VFS="local",
        GSETTINGS_BACKEND="memory",
        NO_AT_BRIDGE="1",
    )
    return env


def spawn(command: str, env: dict) -> subprocess.Popen | None:
    if not command:
        return None
    try:
        return subprocess.Popen(
            command, shell=True, env=env, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log(f"spawn failed: {command}: {exc}")
        return None


def main() -> int:
    if not os.environ.get("DISPLAY"):
        sys.stderr.write("ribi-shell: no DISPLAY; nothing to do\n")
        return 1
    env = session_env()
    home = env["HOME"]
    for sub in (".cache", ".config", "Desktop", "Pictures/Screenshots"):
        try:
            os.makedirs(os.path.join(home, sub), exist_ok=True)
        except OSError:
            pass
    try:
        os.makedirs(env["XDG_RUNTIME_DIR"], exist_ok=True)
    except OSError:
        pass

    log(f"session start home={home} display={env['DISPLAY']}")

    wallpaper = spawn("/usr/local/bin/ribi-wallpaper-viewer", env)
    dock = spawn("/usr/local/bin/ribi-dock", env)

    stopping = {"flag": False}

    def stop(_signum, _frame):
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGHUP, stop)

    while not stopping["flag"]:
        time.sleep(3)
        if dock is not None and dock.poll() is not None:
            log("dock exited; restarting")
            dock = spawn("/usr/local/bin/ribi-dock", env)

    log("session stopping")
    for proc in (dock, wallpaper):
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
