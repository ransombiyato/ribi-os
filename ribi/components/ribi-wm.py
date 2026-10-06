#!/usr/bin/python3
"""ribi-wm - the Ribi OS session window-manager front end.

The Ribi desktop runs a real ICCCM/EWMH window manager (Openbox) for
reliability, optionally with the picom compositor for shadows and transparency,
and a Ribi-branded theme/environment. This module is the single entry point the
live session and init scripts start, so the desktop behaviour lives in one
place instead of being duplicated in shell.

If no supported window manager is present the process exits non-zero and the
caller falls back to its own compatibility path.
"""

import os
import shutil
import signal
import subprocess
import sys

LOG = "/tmp/ribi-wm.log"


def log(message: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(f"[ribi-wm] {message}\n")
    except OSError:
        pass


def session_env() -> dict:
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XAUTHORITY", "/run/ribi/server.auth")
    env.setdefault("XDG_CURRENT_DESKTOP", "Ribi")
    env.setdefault("XDG_SESSION_DESKTOP", "ribi")
    env.setdefault("XDG_SESSION_TYPE", "x11")
    env.setdefault("GDK_BACKEND", "x11")
    env.setdefault("GTK_ICON_THEME", "RibiShapes")
    env.setdefault("GIO_USE_VFS", "local")
    env.setdefault("GSETTINGS_BACKEND", "memory")
    env.setdefault("NO_AT_BRIDGE", "1")
    return env


def start_compositor(env: dict) -> subprocess.Popen | None:
    if os.environ.get("RIBI_NO_COMPOSITOR") == "1":
        return None
    config = "/etc/xdg/ribi/picom.conf"
    # Capture the compositor's own diagnostics instead of discarding them: a
    # rejected option (picom exits at startup) is the difference between "no
    # compositor" and "windows stop being painted", and the log is the only way
    # to tell those apart after the fact.
    try:
        compositor_log = open("/tmp/ribi-compositor.log", "ab")
    except OSError:
        compositor_log = subprocess.DEVNULL
    for name, args in (
        ("picom", ["picom", "--config", config]),
        ("compton", ["compton", "--config", config]),
    ):
        if shutil.which(name):
            try:
                proc = subprocess.Popen(
                    args, env=env, stdout=compositor_log, stderr=compositor_log,
                    start_new_session=True,
                )
                log(f"compositor started: {name} pid={proc.pid}")
                return proc
            except OSError as exc:
                log(f"compositor failed: {name}: {exc}")
    return None


def load_xresources(env: dict) -> None:
    """Merge the Ribi terminal palette into the X server's resource database."""
    if not shutil.which("xrdb"):
        return
    for path in ("/etc/X11/Xresources/ribi", os.path.expanduser("~/.Xresources")):
        if os.path.isfile(path):
            try:
                with open(path, "rb") as handle:
                    subprocess.run(
                        ["xrdb", "-merge", "-"], env=env, stdin=handle,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                    )
                log(f"xresources loaded: {path}")
            except OSError as exc:
                log(f"xresources failed: {path}: {exc}")


def choose_wm() -> list | None:
    for name in ("openbox", "openbox-session", "xfwm4", "metacity", "marco", "mutter"):
        if shutil.which(name):
            return [name]
    return None


def main() -> int:
    if not os.environ.get("DISPLAY"):
        sys.stderr.write("ribi-wm: no DISPLAY; nothing to do\n")
        return 1
    env = session_env()
    log("starting session window manager")
    load_xresources(env)
    compositor = start_compositor(env)

    wm = choose_wm()
    if wm is None:
        log("no supported window manager found")
        if compositor is not None:
            compositor.terminate()
        return 1

    def shutdown(_signum, _frame):
        if compositor is not None and compositor.poll() is None:
            compositor.terminate()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log(f"exec window manager: {' '.join(wm)}")
    try:
        return subprocess.call(wm, env=env)
    finally:
        if compositor is not None and compositor.poll() is None:
            compositor.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
