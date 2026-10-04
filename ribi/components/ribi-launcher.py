#!/usr/bin/python3
"""ribi-launcher - the Ribi OS application launcher.

A modern, searchable launcher (the Windows/Ubuntu "type to find apps" surface)
that replaces the plain dock menu. It opens as a centred overlay, focuses a
search box, filters the application catalog live, and launches the highlighted
entry on Enter or click.

Icons are drawn with Cairo (see ribi_theme) so the launcher never depends on
the target's minimal image/icon stack.
"""

import os
import shutil
import subprocess
import sys

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk
except Exception as exc:  # pragma: no cover - depends on target GTK stack
    sys.stderr.write(f"[ribi-launcher] GTK unavailable: {exc}\n")
    raise SystemExit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/usr/local/bin")
import ribi_theme  # noqa: E402

APP_ID = "ribi-launcher"

# (name, exec, glyph, keywords) - the curated catalog the launcher searches.
CATALOG = [
    ("Files", "ribi-file-explorer", "files", "explorer home folders browse"),
    ("Terminal", "ribi-terminal", "terminal", "shell console command"),
    ("Zen Browser", "zen-browser", "zen", "web internet browser"),
    ("Code Editor", "ribi-edit", "editor", "text code write"),
    ("Screenshot", "ribi-screenshot", "screenshot", "capture screen image"),
    ("Control Center", "ribi-control-center.py", "control", "settings preferences system"),
    ("OBS Studio", "obs --disable-shutdown-check", "obs", "record stream video"),
    ("Snake", "ribi-terminal -e ribi-snake", "games", "game play"),
    ("2048", "ribi-terminal -e ribi-2048", "games", "game play puzzle"),
    ("Install Ribi OS", "sudo -n /usr/local/bin/ribi-installer", "install", "installer disk setup"),
    ("Ribi Setup", "ribi-terminal -e /usr/local/bin/ribi-setup", "control", "first boot welcome"),
]

LAUNCHER_CSS = """
#ribi-launcher-window { background-color: rgba(8, 10, 15, 0.86); }
#ribi-launcher-panel {
    background-color: rgba(22, 27, 38, 0.98);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 16px;
    padding: 18px;
}
#ribi-launcher-search { font-size: 16px; padding: 10px 14px; }
#ribi-launcher-hint { color: #9fb0c8; font-size: 12px; }
.ribi-launcher-tile {
    background-color: rgba(29, 36, 49, 0.9);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 12px;
    padding: 12px 8px;
}
.ribi-launcher-tile:hover {
    background-color: rgba(57, 197, 255, 0.18);
    border-color: #39c5ff;
}
"""


def run(command: str) -> None:
    try:
        subprocess.Popen(command, shell=True, start_new_session=True)
    except OSError as exc:
        sys.stderr.write(f"[ribi-launcher] launch failed: {command}: {exc}\n")


def _matches(entry, query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(entry[:1] + entry[2:]).lower()
    return all(token in haystack for token in query.lower().split())


def build_launcher() -> Gtk.Window:
    window = Gtk.Window(title=APP_ID)
    window.set_name("ribi-launcher-window")
    window.set_decorated(False)
    window.set_skip_taskbar_hint(True)
    window.set_keep_above(True)
    window.set_position(Gtk.WindowPosition.CENTER)
    window.set_default_size(680, 460)
    window.connect("destroy", Gtk.main_quit)
    ribi_theme.prefer_dark()
    ribi_theme.apply_theme(window)
    ribi_theme.apply_css(window, LAUNCHER_CSS)

    overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    overlay.set_valign(Gtk.Align.CENTER)
    overlay.set_halign(Gtk.Align.CENTER)

    panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    panel.set_name("ribi-launcher-panel")
    panel.set_size_request(640, -1)

    search = Gtk.Entry()
    search.set_name("ribi-launcher-search")
    search.set_placeholder_text("Search applications…")
    panel.pack_start(search, False, False, 0)

    grid = Gtk.FlowBox()
    grid.set_selection_mode(Gtk.SelectionMode.NONE)
    grid.set_column_spacing(10)
    grid.set_row_spacing(10)
    grid.set_max_children_per_line(4)
    grid.set_min_children_per_line(4)
    panel.pack_start(grid, True, True, 0)

    hint = Gtk.Label(label="Type to filter  ·  Enter to launch  ·  Esc to close")
    hint.set_name("ribi-launcher-hint")
    panel.pack_start(hint, False, False, 0)

    overlay.pack_start(panel, False, False, 0)
    window.add(overlay)

    shown_state = {"entries": [], "launched": False}

    def activate(command: str) -> None:
        # Return is seen twice (the entry emits "activate" and the key also
        # reaches the window handler); guard so a launch happens only once.
        if shown_state["launched"]:
            return
        shown_state["launched"] = True
        run(command)
        Gtk.main_quit()

    def make_tile(name, command, glyph):
        button = Gtk.Button()
        button.get_style_context().add_class("ribi-launcher-tile")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_halign(Gtk.Align.CENTER)
        content.pack_start(ribi_theme.icon_widget(glyph, size=40), False, False, 0)
        label = Gtk.Label(label=name)
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        content.pack_start(label, False, False, 0)
        button.add(content)
        button.connect("clicked", lambda _b, c=command: activate(c))
        return button

    def refresh(*_a):
        for child in grid.get_children():
            grid.remove(child)
        query = search.get_text().strip()
        shown = [e for e in CATALOG if _matches(e, query)]
        shown_state["entries"] = shown
        for name, command, glyph, _kw in shown:
            grid.add(make_tile(name, command, glyph))
        grid.show_all()

    def launch_first(*_a):
        entries = shown_state["entries"]
        if entries:
            activate(entries[0][1])

    search.connect("changed", refresh)
    # The search entry consumes Return and emits "activate", so the window-level
    # key handler never sees Enter. Wire launch to the entry as well as the
    # window so Enter works while typing and when focus is elsewhere.
    search.connect("activate", launch_first)
    window.connect("key-press-event", lambda _w, e: _on_key(_w, e, launch_first))

    refresh()
    window.show_all()
    # show_all() alone does not always hand the new window focus under a
    # window manager; present() asks for it, and the search entry must be
    # focused after mapping (grab_focus before map is dropped).
    window.present()
    GLib.idle_add(lambda: (search.grab_focus(), False)[1])
    return window


def _on_key(_widget, event, launch_first):
    if event.keyval == Gdk.KEY_Escape:
        Gtk.main_quit()
    elif event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
        launch_first()
    return False


def main() -> int:
    if not os.environ.get("DISPLAY"):
        sys.stderr.write("ribi-launcher: no DISPLAY\n")
        return 1
    build_launcher()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
