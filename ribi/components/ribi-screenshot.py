#!/usr/bin/python3
"""ribi-screenshot - capture the full screen or a region.

Tries the best available backend in order:
  1. xfce4-screenshooter (full or region),
  2. ImageMagick ``import``,
  3. a GTK full-screen grab as a last resort.
Saves to ~/Pictures/Screenshots and prints the output path.
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime

OUT_DIR = os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")


def out_path(prefix: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(OUT_DIR, f"{prefix}-{stamp}.png")


def region_requested() -> bool:
    return any(arg in ("-r", "--region", "--selection", "select") for arg in sys.argv[1:])


def try_xfce(dest: str, region: bool) -> bool:
    exe = shutil.which("xfce4-screenshooter")
    if not exe:
        return False
    flag = "-r" if region else "-f"
    return subprocess.call([exe, flag, "-s", dest]) == 0


def try_import(dest: str, region: bool) -> bool:
    exe = shutil.which("import")
    if not exe:
        return False
    cmd = [exe, "-window", "root", dest]
    if region:
        cmd = [exe, dest]
    return subprocess.call(cmd) == 0


def try_gtk(dest: str) -> bool:
    try:
        import gi

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
    except Exception:
        return False
    root = Gdk.get_default_root_window()
    width = root.get_width()
    height = root.get_height()
    pixbuf = Gdk.pixbuf_get_from_window(root, 0, 0, width, height)
    if pixbuf is None:
        return False
    pixbuf.savev(dest, "png", [], [])
    return True


def main() -> int:
    region = region_requested()
    dest = out_path("Selection" if region else "Screenshot")
    for attempt in (
        lambda: try_xfce(dest, region),
        lambda: try_import(dest, region),
        lambda: try_gtk(dest),
    ):
        try:
            if attempt() and os.path.exists(dest):
                print(dest)
                return 0
        except Exception:
            continue
    sys.stderr.write("ribi-screenshot: no usable capture backend\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
