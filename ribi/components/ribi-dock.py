#!/usr/bin/python3
"""ribi-dock - the Ribi OS desktop panel and application launcher.

A single GTK3 window pinned to the bottom edge that provides:
  * an application menu,
  * quick-launch buttons for the core apps,
  * a live clock,
  * a compact system tray (volume, battery).

Icons are drawn with Cairo (see ribi_theme) so the dock never depends on the
target's image/icon stack, which is intentionally minimal.
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, GLib, Gtk
except Exception as exc:  # pragma: no cover - depends on target GTK stack
    sys.stderr.write(f"[ribi-dock] GTK unavailable: {exc}\n")
    raise SystemExit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/usr/local/bin")
import ribi_theme  # noqa: E402

APP_ID = "ribi-dock"
MENU_MODE = "--menu" in sys.argv
LOG = os.path.join(os.path.expanduser("~"), ".cache", "ribi-dock.log")

# key -> (label, command, glyph)
APPS = [
    ("files", "Files", "ribi-file-explorer", "files"),
    ("terminal", "Terminal", "xterm -title Ribi\\ Terminal", "terminal"),
    ("zen", "Zen Browser", "zen-browser", "zen"),
    ("editor", "Editor", "ribi-edit", "editor"),
    ("screenshot", "Screenshot", "ribi-screenshot", "screenshot"),
    ("control", "Settings", "ribi-control-center.py", "control"),
    ("obs", "OBS Studio", "obs --disable-shutdown-check", "obs"),
]

QUICK = ["files", "terminal", "zen", "editor", "control"]

MENU_CSS = """
#ribi-menu { background-color: rgba(16, 19, 29, 0.98); }
#ribi-menu button { background: transparent; border: none; padding: 8px 12px; color: #e6ebf5; }
#ribi-menu button:hover { background-color: rgba(57, 197, 255, 0.18); border-radius: 8px; }
"""


def log(message: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(f"[ribi-dock] {message}\n")
    except OSError:
        pass


def run(command: str) -> None:
    program = command.split()[0]
    if shutil.which(program) is None:
        log(f"skip missing program: {program}")
        return
    try:
        subprocess.Popen(command, shell=True, start_new_session=True)
    except OSError as exc:
        log(f"launch failed: {command}: {exc}")


def build_menu() -> Gtk.Window:
    window = Gtk.Window(title="Ribi Applications")
    window.set_name("ribi-menu")
    window.set_type_hint(Gdk.WindowTypeHint.DIALOG)
    window.set_position(Gtk.WindowPosition.CENTER)
    window.set_border_width(10)
    window.set_default_size(360, -1)
    window.connect("destroy", Gtk.main_quit)
    ribi_theme.apply_css(window, ribi_theme.base_css() + MENU_CSS)

    grid = Gtk.Grid(column_spacing=6, row_spacing=6)
    for index, (_key, label, command, glyph) in enumerate(APPS):
        button = Gtk.Button()
        button.set_tooltip_text(label)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.pack_start(ribi_theme.icon_widget(glyph, size=24), False, False, 0)
        box.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
        button.add(box)
        button.connect("clicked", lambda _b, c=command: (run(c), window.destroy()))
        grid.attach(button, index % 2, index // 2, 1, 1)

    window.add(grid)
    return window


def battery_text() -> str:
    for base in ("/sys/class/power_supply/BAT0", "/sys/class/power_supply/BAT1"):
        try:
            with open(os.path.join(base, "capacity"), encoding="ascii") as handle:
                return f"{handle.read().strip()}%"
        except OSError:
            continue
    return ""


def volume_text() -> str:
    if shutil.which("amixer") is None:
        return ""
    try:
        out = subprocess.run(
            ["amixer", "get", "Master"],
            capture_output=True, text=True, timeout=2, check=False,
        ).stdout
        for line in out.splitlines():
            if "[" in line and "%" in line:
                return line.split("[")[1].split("]")[0]
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def build_dock() -> Gtk.Window:
    window = Gtk.Window(title=APP_ID)
    window.set_name("ribi-dock")
    window.set_type_hint(Gdk.WindowTypeHint.DOCK)
    window.set_decorated(False)
    window.set_resizable(False)
    window.set_skip_taskbar_hint(True)
    window.set_keep_above(True)
    window.connect("destroy", Gtk.main_quit)
    ribi_theme.apply_css(window, ribi_theme.base_css() + ribi_theme.DOCK_CSS)

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)

    menu_button = ribi_theme.icon_button("menu", tooltip="Applications")
    menu_button.connect("clicked", lambda _b: open_launcher())
    box.pack_start(menu_button, False, False, 0)

    for key in QUICK:
        entry = next((a for a in APPS if a[0] == key), None)
        if entry is None:
            continue
        _key, label, command, glyph = entry
        button = ribi_theme.icon_button(glyph, tooltip=label)
        button.connect("clicked", lambda _b, c=command: run(c))
        box.pack_start(button, False, False, 0)

    spacer = Gtk.Box()
    box.pack_start(spacer, True, True, 0)

    tray = Gtk.Label(label="")
    tray.set_name("ribi-tray")
    box.pack_end(tray, False, False, 6)

    clock = Gtk.Label(label="")
    clock.set_name("ribi-clock")
    box.pack_end(clock, False, False, 6)

    def tick():
        clock.set_text(datetime.now().strftime("%a %H:%M"))
        parts = [p for p in (volume_text(), battery_text()) if p]
        tray.set_text("  ".join(parts))
        return True

    tick()
    GLib.timeout_add_seconds(5, tick)

    window.add(box)

    screen = window.get_screen()
    monitor = screen.get_primary_monitor() or 0
    geometry = screen.get_monitor_geometry(monitor)
    # Span the full monitor width; let the height follow the content. Position
    # from the real allocation (size-allocate) because get_size() before the
    # window is mapped reports a placeholder and misplaces the panel.
    window.set_size_request(geometry.width, -1)
    placed = {"done": False}

    def place(_widget, allocation):
        if not placed["done"] and allocation.height > 1:
            placed["done"] = True
            window.move(geometry.x, geometry.y + geometry.height - allocation.height)

    window.connect("size-allocate", place)
    window.show_all()
    return window


def show_menu() -> None:
    build_menu().show_all()


_launcher_proc = {"proc": None}


def open_launcher() -> None:
    """Open the searchable application launcher overlay.

    The launcher runs as its own process (it owns focus and Esc-to-close
    handling). Re-clicking while one is already running does nothing rather
    than stacking duplicate overlays.
    """
    proc = _launcher_proc["proc"]
    if proc is not None and proc.poll() is None:
        return
    command = "/usr/local/bin/ribi-launcher"
    if not os.path.exists(command):
        show_menu()
        return
    try:
        _launcher_proc["proc"] = subprocess.Popen(command, start_new_session=True)
    except OSError as exc:
        log(f"launcher failed: {exc}")
        show_menu()


def main() -> int:
    log(f"start mode={'menu' if MENU_MODE else 'dock'}")
    try:
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
    except Exception:
        pass

    if MENU_MODE:
        show_menu()
    else:
        build_dock()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
