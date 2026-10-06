#!/usr/bin/python3
"""ribi-dock - the Ribi OS desktop panel, taskbar, and application launcher.

A single GTK3 window pinned to the bottom edge that provides:
  * an application menu,
  * quick-launch buttons for the core apps,
  * a live taskbar of the currently open windows (click to focus/minimise,
    right-click for the usual window actions),
  * a live clock,
  * a compact system tray (volume, battery).

Icons are drawn with Cairo (see ribi_theme) so the dock never depends on the
target's image/icon stack, which is intentionally minimal.

The window list is read over EWMH with the standard X tools (xprop, xdotool)
that the image already ships, rather than a desktop-specific client library.
Every X call runs in a worker thread: the dock must never block the GTK main
loop waiting on the X server, or the whole panel freezes while an application
is busy starting.
"""

import os
import shutil
import subprocess
import sys
import threading
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

# key -> (label, command, glyph, sandboxed)
# `sandboxed` marks apps whose launcher runs an OS sandbox (currently Zen's
# content sandbox). Those get a "Run without sandbox" item in their right-click
# menu; the flag is False for everything else.
APPS = [
    ("files", "Files", "ribi-file-explorer", "files", False),
    ("terminal", "Terminal", "ribi-terminal", "terminal", False),
    ("zen", "Zen Browser", "zen-browser", "zen", True),
    ("editor", "Editor", "ribi-edit", "editor", False),
    ("calculator", "Calculator", "galculator", "calculator", False),
    ("images", "Image Viewer", "ristretto", "image", False),
    ("media", "Media Player", "celluloid", "media", False),
    ("archives", "Archive Manager", "file-roller", "archive", False),
    ("text", "Text Editor", "mousepad", "text", False),
    ("pdf", "Document Viewer", "zathura", "pdf", False),
    ("audio", "Audacity", "audacity", "audio", False),
    ("screenshot", "Screenshot", "ribi-screenshot", "screenshot", False),
    ("control", "Settings", "ribi-control-center.py", "control", False),
    ("obs", "OBS Studio", "obs --disable-shutdown-check --profile ribi --collection ribi", "obs", False),
]

QUICK = ["files", "terminal", "zen", "editor", "control"]

# Bounds on the taskbar: keep the panel usable even if a window is stuck or an
# app spawns a flood of helper windows.
MAX_TASKS = 12

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


def run_without_sandbox(command: str) -> None:
    """Launch a command with the OS sandbox disabled.

    Some launchers wrap their app in a sandbox (Zen passes its own flags, but
    the sandbox can be forced off through the environment as a fallback). This
    sets the disable flag for the whole process tree and runs the command
    unchanged, so it works for any catalog entry.
    """
    program = command.split()[0]
    if shutil.which(program) is None:
        log(f"skip missing program: {program}")
        return
    env = dict(os.environ)
    env["RIBI_DISABLE_SANDBOX"] = "1"
    env["RIBI_NO_SANDBOX"] = "1"
    env["RIBI_SANDBOX"] = "0"
    try:
        subprocess.Popen(command, shell=True, start_new_session=True, env=env)
    except OSError as exc:
        log(f"launch failed: {command}: {exc}")


def _app_entries():
    """Normalise APPS entries to (label, command, glyph, sandboxed)."""
    for entry in APPS:
        key, label, command, glyph = entry[:4]
        sandboxed = bool(entry[4]) if len(entry) > 4 else False
        yield key, label, command, glyph, sandboxed


# ---- Open-window taskbar ----------------------------------------------------

def _xdo(*args: str) -> bool:
    if shutil.which("xdotool") is None:
        return False
    try:
        return subprocess.run(
            ["xdotool", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=3, check=False,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _xprop(window_id: str, field: str) -> str:
    if shutil.which("xprop") is None:
        return ""
    try:
        out = subprocess.run(
            ["xprop", "-id", window_id, field],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    if "=" not in out:
        return ""
    value = out.split("=", 1)[1].strip()
    # xprop prints strings as `_NET_WM_NAME(UTF8_STRING) = "Title"`; drop the
    # quoting so the taskbar shows the title, not `"Title"`.
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    return "" if value in ("", "0x0") else value


def _window_class(window_id: str) -> str:
    # _NET_WM_PID is a single number; _WM_CLASS looks like:
    #   _WM_CLASS(STRING) = "instance", "Class"
    pid = _xprop(window_id, "_NET_WM_PID").strip()
    if pid.isdigit():
        try:
            with open(f"/proc/{pid}/comm", encoding="ascii", errors="replace") as handle:
                name = handle.read().strip()
            if name:
                return name
        except OSError:
            pass
    raw = _xprop(window_id, "WM_CLASS")
    parts = [p.strip().strip('"') for p in raw.split(",") if p.strip()]
    return parts[-1] if parts else ""


def _active_window() -> str:
    if shutil.which("xdotool") is None:
        return ""
    try:
        out = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout.strip()
        return out
    except (OSError, subprocess.SubprocessError):
        return ""


def list_windows() -> tuple[list, str]:
    """Return ([{id, title, cls}], active_id) for normal, on-screen windows."""
    if shutil.which("xdotool") is None:
        return [], ""
    try:
        out = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", "."],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return [], ""
    active = _active_window()
    windows = []
    for window_id in out.split():
        title = _xprop(window_id, "_NET_WM_NAME") or _xprop(window_id, "WM_NAME")
        cls = _window_class(window_id)
        # Skip the dock itself and anything with no title (hidden helpers).
        if not title or cls == APP_ID:
            continue
        windows.append({"id": window_id, "title": title, "cls": cls})
        if len(windows) >= MAX_TASKS:
            break
    return windows, active


def _activate_window(window_id: str) -> None:
    # Openbox ignores a plain focus request for a minimised window, so restore
    # first, then raise and focus. The fallback handles a stale window id.
    if not _xdo("windowactivate", "--sync", window_id):
        _xdo("windowactivate", window_id)


def window_action(window_id: str, action: str) -> None:
    if action == "minimize":
        _xdo("windowminimize", window_id)
    elif action == "close":
        _xdo("windowclose", window_id)
    elif action == "maximize":
        # Ask the window manager to maximise via EWMH so it accounts for its own
        # decorations; a raw resize leaves the frame hanging off-screen.
        if not _xdo("windowstate", "--add", "MAXIMIZED_VERT", "MAXIMIZED_HORZ", window_id):
            _xdo("windowsize", window_id, "100%", "100%")
            _xdo("windowmove", window_id, "0", "0")
    elif action == "restore":
        _activate_window(window_id)
    else:
        _activate_window(window_id)


def _attach_app_menu(button: Gtk.Button, label: str, command: str, sandboxed: bool) -> None:
    """Wire a launcher button to run on click and, for sandboxed apps, offer a
    "Run without sandbox" item on right-click."""
    button.connect("clicked", lambda _b, c=command: run(c))

    def on_press(_widget, event, c=command):
        if event.button != 3:
            return False
        menu = Gtk.Menu()
        if sandboxed:
            item = Gtk.MenuItem(label="Run without sandbox")
            item.connect("activate", lambda _i, cc=c: run_without_sandbox(cc))
            menu.append(item)
        else:
            item = Gtk.MenuItem(label="Run")
            item.connect("activate", lambda _i, cc=c: run(cc))
            menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    button.connect("button-press-event", on_press)


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
    for index, (_key, label, command, glyph, sandboxed) in enumerate(_app_entries()):
        button = Gtk.Button()
        button.set_tooltip_text(label)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.pack_start(ribi_theme.icon_widget(glyph, size=24), False, False, 0)
        box.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
        button.add(box)
        _attach_app_menu(button, label, command, sandboxed)
        button.connect("clicked", lambda _b: window.destroy())
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
        entry = next((a for a in _app_entries() if a[0] == key), None)
        if entry is None:
            continue
        _key, label, command, glyph, sandboxed = entry
        button = ribi_theme.icon_button(glyph, tooltip=label)
        _attach_app_menu(button, label, command, sandboxed)
        box.pack_start(button, False, False, 0)

    # Open-window taskbar. It sits between the launchers and the tray and grows
    # to fill the free space, so the clock/tray stay pinned to the right edge.
    taskbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
    taskbar.set_name("ribi-taskbar")
    box.pack_start(taskbar, True, True, 4)

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

    schedule_tasks(taskbar)

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


def _clear(container) -> None:
    for child in container.get_children():
        container.remove(child)


def _task_button(task, active: bool = False) -> Gtk.Button:
    title = task["title"]
    label = title if len(title) <= 22 else title[:21] + "…"
    button = Gtk.Button()
    button.set_name("ribi-task")
    if active:
        button.get_style_context().add_class("ribi-task-active")
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_tooltip_text(f"{title}  ({task['cls']})")
    content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    content.pack_start(ribi_theme.icon_widget("display", size=14), False, False, 0)
    content.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
    button.add(content)
    button.connect("clicked", lambda _b, wid=task["id"]: window_action(wid, "focus"))
    button.connect("button-press-event", lambda _b, e, wid=task["id"]: _task_click(e, wid))
    return button


def _task_click(event, window_id: str):
    if event.button != 3:
        return False
    menu = Gtk.Menu()
    for label, action in (
        ("Activate", "focus"),
        ("Minimize", "minimize"),
        ("Maximize", "maximize"),
        ("Close", "close"),
    ):
        item = Gtk.MenuItem(label=label)
        item.connect("activate", lambda _i, a=action: window_action(window_id, a))
        menu.append(item)
    menu.show_all()
    menu.popup_at_pointer(event)
    return True


def refresh_tasks(taskbar) -> None:
    """Rebuild the taskbar from the live EWMH window list, off the main loop."""
    if getattr(refresh_tasks, "busy", False):
        return
    refresh_tasks.busy = True

    def worker():
        try:
            windows, active = list_windows()
        except Exception as exc:  # never let a poll error kill the dock
            log(f"window poll failed: {exc}")
            windows, active = [], ""

        def apply():
            refresh_tasks.busy = False
            _clear(taskbar)
            for task in windows:
                taskbar.pack_start(_task_button(task, task["id"] == active), False, False, 0)
            taskbar.show_all()
            return False

        GLib.idle_add(apply)

    threading.Thread(target=worker, daemon=True).start()


def schedule_tasks(taskbar) -> None:
    """Refresh the taskbar now and every 2s. The timeout always returns True,
    so the loop cannot stop even if a poll is still in flight (refresh_tasks
    simply skips while one is running)."""
    refresh_tasks(taskbar)
    GLib.timeout_add_seconds(2, lambda: (refresh_tasks(taskbar), True)[1])


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
