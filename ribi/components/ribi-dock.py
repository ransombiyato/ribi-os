#!/usr/bin/python3
"""ribi-dock - the Ribi OS desktop panel and application launcher.

A single GTK3 window pinned to the bottom edge that provides:
  * an application menu (also available as a standalone launcher),
  * quick-launch buttons for the core apps,
  * a live clock,
  * a compact system tray (volume, battery).

The dock is intentionally self-contained: it degrades gracefully if GTK or the
individual apps are unavailable, so a missing optional app never takes down
the whole desktop session.
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

APP_ID = "ribi-dock"
MENU_MODE = "--menu" in sys.argv
LOG = os.path.join(os.path.expanduser("~"), ".cache", "ribi-dock.log")

# name -> (label, command, icon name)
APPS = [
    ("files", "Files", "ribi-file-explorer", "system-file-manager"),
    ("terminal", "Terminal", "xterm -title Ribi\\ Terminal", "utilities-terminal"),
    ("zen", "Zen Browser", "zen-browser", "zen-browser"),
    ("editor", "Editor", "ribi-edit", "accessories-text-editor"),
    ("screenshot", "Screenshot", "ribi-screenshot", "camera-photo"),
    ("control", "Settings", "ribi-control-center.py", "preferences-system"),
    ("obs", "OBS Studio", "obs --disable-shutdown-check", "obs"),
]

QUICK = ["files", "terminal", "zen", "editor", "control"]

CSS = """
#ribi-dock {
    background-color: rgba(18, 21, 32, 0.92);
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    padding: 4px 10px;
}
#ribi-dock button {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 4px 8px;
    color: #e6ebf5;
}
#ribi-dock button:hover {
    background-color: rgba(57, 197, 255, 0.20);
}
#ribi-dock button:active {
    background-color: rgba(57, 197, 255, 0.35);
}
#ribi-clock {
    color: #e6ebf5;
    font-size: 12px;
}
#ribi-tray {
    color: #9fb0c8;
    font-size: 12px;
}
#ribi-menu {
    background-color: rgba(18, 21, 32, 0.98);
}
#ribi-menu button {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 8px 12px;
    color: #e6ebf5;
}
#ribi-menu button:hover {
    background-color: rgba(57, 197, 255, 0.20);
}
"""


def log(message: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(f"[ribi-dock] {message}\n")
    except OSError:
        pass


def run(command: str) -> None:
    """Launch an app detached, tolerating a missing binary."""
    program = command.split()[0]
    if shutil.which(program) is None:
        log(f"skip missing program: {program}")
        return
    try:
        subprocess.Popen(command, shell=True, start_new_session=True)
    except OSError as exc:
        log(f"launch failed: {command}: {exc}")


def icon_for(name: str) -> Gtk.Image:
    theme = Gtk.IconTheme.get_default()
    try:
        if theme.has_icon(name):
            return Gtk.Image.new_from_icon_name(name, Gtk.IconSize.DND)
    except Exception:
        pass
    return Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DND)


def build_menu() -> Gtk.Window:
    window = Gtk.Window(title="Ribi Applications")
    window.set_name("ribi-menu")
    window.set_type_hint(Gdk.WindowTypeHint.DIALOG)
    window.set_position(Gtk.WindowPosition.CENTER)
    window.set_border_width(10)
    window.set_default_size(360, -1)
    window.connect("destroy", Gtk.main_quit)

    grid = Gtk.Grid(column_spacing=6, row_spacing=6)
    for index, (key, label, command, icon_name) in enumerate(APPS):
        button = Gtk.Button()
        button.set_tooltip_text(label)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.pack_start(icon_for(icon_name), False, False, 0)
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

    screen = window.get_screen()
    monitor = screen.get_primary_monitor() or 0
    geometry = screen.get_monitor_geometry(monitor)

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

    menu_button = Gtk.Button()
    menu_button.set_tooltip_text("Applications")
    menu_button.add(icon_for("view-app-grid"))
    menu_button.connect("clicked", lambda _b: show_menu())
    box.pack_start(menu_button, False, False, 0)

    for key in QUICK:
        entry = next((a for a in APPS if a[0] == key), None)
        if entry is None:
            continue
        _key, label, command, icon_name = entry
        button = Gtk.Button()
        button.set_tooltip_text(label)
        button.add(icon_for(icon_name))
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
    window.show_all()
    window.move(geometry.x, geometry.y + geometry.height - window.get_size()[1])
    return window


def show_menu() -> None:
    menu = build_menu()
    menu.show_all()


def main() -> int:
    log(f"start mode={'menu' if MENU_MODE else 'dock'}")
    try:
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
    except Exception:
        pass
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    if MENU_MODE:
        show_menu()
    else:
        build_dock()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
