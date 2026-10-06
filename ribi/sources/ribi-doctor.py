#!/usr/bin/env python3
"""ribi-doctor - check the Ribi OS desktop for the problems that bite at runtime.

This is the "open every app and read its logs" pass, made repeatable. It does
not replace a real boot test; it catches the failure classes that are cheap to
detect on a running system:

  * a required binary or runtime database is missing,
  * a GLib/GTK app aborts on startup (missing schemas, mime cache, icon theme),
  * a GUI app prints errors or crashes within a short launch window.

Usage:
    ribi-doctor              # files + databases + a quick app smoke test
    ribi-doctor --files      # only the file/database checks (no launching)
    ribi-doctor --app NAME   # launch one app and show its output

Exit status is 0 when everything checked out, 1 when a problem was found.
"""

import os
import shutil
import subprocess
import sys

# (label, argv, needs_display)
APPS = [
    ("Terminal", ["ribi-terminal", "-e", "true"], True),
    ("File Explorer", ["ribi-file-explorer"], True),
    ("Code Editor", ["ribi-edit"], True),
    ("Calculator", ["galculator"], True),
    ("Image Viewer", ["ristretto"], True),
    ("Media Player", ["celluloid"], True),
    ("Archive Manager", ["file-roller"], True),
    ("Text Editor", ["mousepad"], True),
    ("Document Viewer", ["zathura"], True),
    ("Audacity", ["audacity"], True),
    ("OBS Studio", ["obs", "--disable-shutdown-check", "--profile", "ribi",
                    "--collection", "ribi"], True),
    ("Settings", ["ribi-control-center.py"], True),
]

REQUIRED_FILES = [
    "/sbin/ribi-init",
    "/usr/local/bin/ribisvc",
    "/usr/local/bin/ribi-pkg",
    "/usr/local/bin/ribi",
    "/usr/local/bin/ribi-installer",
    "/usr/local/bin/ribi-dock",
    "/usr/local/bin/ribi-launcher.py",
    "/usr/local/bin/ribi-wm.py",
    "/usr/local/bin/ribi-shell.py",
    "/usr/local/bin/ribi_theme.py",
    "/etc/xdg/ribi/picom.conf",
    "/etc/X11/Xresources/ribi",
    "/usr/share/backgrounds/ribi-wallpaper.png",
]

REQUIRED_DATABASES = [
    "/usr/share/glib-2.0/schemas/gschemas.compiled",
    "/usr/share/mime/mime.cache",
]

# Substrings that mean "this app did not start cleanly". Keep these specific:
# a bare "error" also matches benign warnings every GTK app emits (missing
# AT-SPI bus, DRI3 unavailable, an optional icon), which would flag a healthy
# app as broken.
ERROR_MARKERS = (
    "segmentation fault", "segfault", "core dumped", "abort",
    "assertion", "traceback (most recent call last)",
    "cannot open display", "no gsettings schemas",
    "symbol lookup error", "undefined symbol", "failed to execute",
)

ok = 0
problems = 0


def report(status: str, message: str) -> None:
    global ok, problems
    if status == "ok":
        ok += 1
        print(f"  [ ok ] {message}")
    else:
        problems += 1
        print(f"  [FAIL] {message}")


def check_files() -> None:
    print("Files and runtime databases:")
    for path in REQUIRED_FILES + REQUIRED_DATABASES:
        report("ok" if os.path.exists(path) else "fail", path)


def check_apps(only: str = "") -> None:
    if not os.environ.get("DISPLAY"):
        print("Applications: skipped (no DISPLAY; run inside the desktop session).")
        return
    print("Applications (short launch smoke test):")
    for label, argv, needs_display in APPS:
        if only and only.lower() not in label.lower():
            continue
        program = argv[0]
        if shutil.which(program) is None:
            report("fail", f"{label}: '{program}' is not installed")
            continue
        if needs_display and not os.environ.get("DISPLAY"):
            report("fail", f"{label}: no DISPLAY")
            continue
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=8, check=False,
                env={**os.environ, "GSETTINGS_BACKEND": os.environ.get("GSETTINGS_BACKEND", "memory")},
            )
            output = (result.stdout or "") + (result.stderr or "")
        except subprocess.TimeoutExpired as exc:
            # A GUI app that is still running after the timeout is healthy; it
            # simply does not exit on its own. TimeoutExpired can hand back a mix
            # of str and bytes (or None) for stdout/stderr, so normalise each
            # stream before joining them.
            def _text(stream):
                if stream is None:
                    return ""
                if isinstance(stream, bytes):
                    return stream.decode("utf-8", "replace")
                return stream

            output = _text(exc.stdout) + _text(exc.stderr)
            lowered = output.lower()
            if any(marker in lowered for marker in ERROR_MARKERS):
                report("fail", f"{label}: errors while running:\n{output.strip()[:800]}")
            else:
                report("ok", f"{label}: started cleanly")
            continue
        except OSError as exc:
            report("fail", f"{label}: could not launch: {exc}")
            continue
        lowered = output.lower()
        if result.returncode != 0 or any(marker in lowered for marker in ERROR_MARKERS):
            detail = output.strip()[:800] or f"exit status {result.returncode}"
            report("fail", f"{label}: {detail}")
        else:
            report("ok", label)


def main() -> int:
    args = sys.argv[1:]
    only = ""
    if "--app" in args:
        index = args.index("--app")
        if index + 1 < len(args):
            only = args[index + 1]

    print("=== Ribi OS doctor ===\n")
    check_files()
    if "--files" not in args:
        print()
        check_apps(only)
    print(f"\n{ok} ok, {problems} problem(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
