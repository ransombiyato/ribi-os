#!/usr/bin/python3
"""ribi-control-center - a GTK3 settings hub for Ribi OS.

Notebook tabs for the settings people actually touch on a live/installed
system: Appearance, Display, Sound, Network, Users, and About. Each tab reads
live state with small standard tools (xrandr, amixer, ip) and degrades to an
explanatory message when a tool or device is missing.
"""

import os
import platform
import shutil
import subprocess
import sys

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except Exception as exc:  # pragma: no cover
    sys.stderr.write(f"[ribi-control-center] GTK unavailable: {exc}\n")
    raise SystemExit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/usr/local/bin")
import ribi_theme  # noqa: E402

CSS = """
#ribi-cc-title { font-size: 16px; font-weight: bold; color: #e6ebf5; }
#ribi-cc-mono { font-family: monospace; font-size: 12px; color: #c8d4e6; }
#ribi-cc-note { color: #9fb0c8; }
"""


def capture(command: list) -> str:
    if shutil.which(command[0]) is None:
        return f"{command[0]} is not installed."
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=5, check=False
        )
        return (result.stdout or result.stderr or "").strip() or "(no output)"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Failed to run {command[0]}: {exc}"


def run(command: list) -> None:
    try:
        subprocess.Popen(command, start_new_session=True)
    except OSError:
        pass


def titled(text: str) -> Gtk.Widget:
    label = Gtk.Label(label=text, xalign=0)
    label.set_name("ribi-cc-title")
    return label


def scrolled_text(text: str) -> Gtk.Widget:
    view = Gtk.TextView()
    view.set_editable(False)
    view.set_cursor_visible(False)
    view.set_monospace(True)
    view.set_name("ribi-cc-mono")
    view.get_buffer().set_text(text)
    scroll = Gtk.ScrolledWindow()
    scroll.set_vexpand(True)
    scroll.add(view)
    return scroll


class ControlCenter(Gtk.Window):
    def __init__(self):
        super().__init__(title="Ribi Settings")
        self.set_default_size(720, 560)
        self.connect("destroy", Gtk.main_quit)
        ribi_theme.prefer_dark()
        ribi_theme.apply_css(self, ribi_theme.base_css() + CSS)

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("Ribi Settings")
        self.set_titlebar(header)

        notebook = Gtk.Notebook()
        self.add(notebook)
        for name, builder in (
            ("Appearance", self.tab_appearance),
            ("Display", self.tab_display),
            ("Sound", self.tab_sound),
            ("Network", self.tab_network),
            ("Users", self.tab_users),
            ("About", self.tab_about),
        ):
            notebook.append_page(builder(), Gtk.Label(label=name))
        self.show_all()

    def tab_appearance(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
        box.pack_start(titled("Appearance"), False, False, 0)
        theme = os.environ.get("GTK_THEME", "Ribi")
        icon = os.environ.get("GTK_ICON_THEME", "RibiShapes")
        info = Gtk.Label(
            label=f"Theme: {theme}\nIcon theme: {icon}\nSession: Ribi (X11)",
            xalign=0,
        )
        info.set_name("ribi-cc-note")
        box.pack_start(info, False, False, 0)

        swatches = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for name in ("files", "terminal", "zen", "editor", "screenshot", "control"):
            swatches.pack_start(ribi_theme.icon_widget(name, size=36), False, False, 0)
        box.pack_start(swatches, False, False, 0)
        return box

    def tab_display(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
        box.pack_start(titled("Display"), False, False, 0)
        box.pack_start(scrolled_text(capture(["xrandr", "--query"])), True, True, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for size in ("1024x768", "1280x800", "1920x1080"):
            button = Gtk.Button(label=size)
            button.connect("clicked", lambda _b, s=size: self.set_resolution(s))
            row.pack_start(button, False, False, 0)
        box.pack_start(row, False, False, 0)
        return box

    def set_resolution(self, size: str) -> None:
        output = ""
        try:
            out = subprocess.run(
                ["xrandr", "--query"], capture_output=True, text=True, timeout=5, check=False
            ).stdout
            for line in out.splitlines():
                if " connected" in line:
                    output = line.split()[0]
                    break
        except (OSError, subprocess.SubprocessError):
            return
        if output:
            run(["xrandr", "--output", output, "--mode", size])

    def tab_sound(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
        box.pack_start(titled("Sound"), False, False, 0)
        box.pack_start(scrolled_text(capture(["amixer", "get", "Master"])), True, True, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for label, args in (
            ("Volume -", ["amixer", "-q", "set", "Master", "5%-"]),
            ("Mute", ["amixer", "-q", "set", "Master", "toggle"]),
            ("Volume +", ["amixer", "-q", "set", "Master", "5%+"]),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _b, a=args: run(a))
            row.pack_start(button, False, False, 0)
        box.pack_start(row, False, False, 0)
        return box

    def tab_network(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
        box.pack_start(titled("Network"), False, False, 0)
        self.network_view = scrolled_text(capture(["ip", "addr"]))
        box.pack_start(self.network_view, True, True, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        connect = Gtk.Button(label="Connect (ribi-netup)")
        connect.connect("clicked", lambda _b: run(["xterm", "-hold", "-e", "sudo", "/usr/local/sbin/ribi-netup"]))
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", lambda _b: self.refresh())
        row.pack_start(connect, False, False, 0)
        row.pack_start(refresh, False, False, 0)
        box.pack_start(row, False, False, 0)
        return box

    def tab_users(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
        box.pack_start(titled("Users"), False, False, 0)
        info = Gtk.Label(label=f"Current user: {os.environ.get('USER', 'ribi')}", xalign=0)
        info.set_name("ribi-cc-note")
        box.pack_start(info, False, False, 0)
        change = Gtk.Button(label="Change password")
        change.connect("clicked", lambda _b: run(["xterm", "-hold", "-e", "passwd"]))
        box.pack_start(change, False, False, 0)
        return box

    def tab_about(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
        box.pack_start(titled("About Ribi OS"), False, False, 0)
        text = (
            "Ribi OS\n"
            f"Kernel: {platform.release()}\n"
            f"Machine: {platform.machine()}\n"
            f"Python: {platform.python_version()}\n"
            "\nA from-scratch x86_64 desktop OS with its own init,\n"
            "service manager, package engine, and installer."
        )
        label = Gtk.Label(label=text, xalign=0)
        label.set_name("ribi-cc-note")
        box.pack_start(label, False, False, 0)
        return box

    def refresh(self) -> None:
        view = getattr(self, "network_view", None)
        if view is not None:
            view.get_buffer().set_text(capture(["ip", "addr"]))


def main() -> int:
    ControlCenter()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
