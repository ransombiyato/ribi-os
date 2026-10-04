#!/usr/bin/python3
"""ribi-file-explorer - a small GTK3 file manager for Ribi OS.

Sidebar of common places, a path bar with back/forward/up, a list view with
human-readable sizes and dates, open on double-click, and context actions
(open, rename, delete, new folder). All icons are Cairo-drawn so no icon theme
or image loader is required.
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gio, GLib, Gtk
except Exception as exc:  # pragma: no cover
    sys.stderr.write(f"[ribi-file-explorer] GTK unavailable: {exc}\n")
    raise SystemExit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/usr/local/bin")
import ribi_theme  # noqa: E402

PLACES = [
    ("Home", os.path.expanduser("~")),
    ("Desktop", os.path.join(os.path.expanduser("~"), "Desktop")),
    ("Documents", os.path.join(os.path.expanduser("~"), "Documents")),
    ("Downloads", os.path.join(os.path.expanduser("~"), "Downloads")),
    ("Pictures", os.path.join(os.path.expanduser("~"), "Pictures")),
    ("Root", "/"),
    ("/etc", "/etc"),
    ("/usr", "/usr"),
    ("/var", "/var"),
]

CSS = """
#ribi-fx-path { font-size: 12px; color: #9fb0c8; }
#ribi-fx-side { background-color: rgba(16,19,29,0.5); }
#ribi-fx-side row { padding: 2px; }
"""


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def open_path(path: str) -> None:
    try:
        Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(path), None)
    except Exception:
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", path], start_new_session=True)


class Explorer(Gtk.Window):
    def __init__(self, start: str):
        super().__init__(title="Ribi Files")
        self.set_default_size(900, 560)
        self.set_name("ribi-fx")
        self.connect("destroy", Gtk.main_quit)
        ribi_theme.prefer_dark()
        ribi_theme.apply_css(self, ribi_theme.base_css() + CSS)
        self.history = []
        self.index = -1

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("Ribi Files")
        self.set_titlebar(header)

        self.back = ribi_theme.icon_button("back", tooltip="Back", size=18)
        self.forward = ribi_theme.icon_button("forward", tooltip="Forward", size=18)
        self.up = ribi_theme.icon_button("up", tooltip="Up", size=18)
        new_folder = ribi_theme.icon_button("new", tooltip="New Folder", size=18)
        refresh = ribi_theme.icon_button("refresh", tooltip="Refresh", size=18)
        self.back.connect("clicked", lambda _b: self.go_back())
        self.forward.connect("clicked", lambda _b: self.go_forward())
        self.up.connect("clicked", lambda _b: self.go_up())
        new_folder.connect("clicked", lambda _b: self.new_folder())
        refresh.connect("clicked", lambda _b: self.reload())
        for widget in (self.back, self.forward, self.up, new_folder, refresh):
            header.pack_start(widget)

        self.path_label = Gtk.Label(label="")
        self.path_label.set_name("ribi-fx-path")
        self.path_label.set_ellipsize(3)
        header.set_custom_title(self.path_label)

        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(split)

        sidebar = Gtk.ListBox()
        sidebar.set_name("ribi-fx-side")
        sidebar.set_size_request(170, -1)
        for label, path in PLACES:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.pack_start(ribi_theme.icon_widget("folder", size=16), False, False, 0)
            box.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
            row.add(box)
            row.path = path
            sidebar.add(row)
        sidebar.connect("row-activated", self.on_place)
        split.pack1(sidebar, False, False)

        self.store = Gtk.ListStore(str, str, str)
        view = Gtk.TreeView(model=self.store)
        view.connect("row-activated", self.on_open)
        view.connect("button-press-event", self.on_button)
        for title, column in (("Name", 0), ("Size", 1), ("Modified", 2)):
            renderer = Gtk.CellRendererText()
            tree_column = Gtk.TreeViewColumn(title, renderer)
            tree_column.add_attribute(renderer, "text", column)
            tree_column.set_resizable(True)
            view.append_column(tree_column)
        self.view = view

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.add(view)
        split.pack2(scroll, True, False)

        self.show_all()
        self.navigate(start)

    # -- navigation -------------------------------------------------------
    def navigate(self, path: str, record: bool = True) -> None:
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            return
        if record:
            self.history = self.history[: self.index + 1]
            self.history.append(path)
            self.index = len(self.history) - 1
        self.current = path
        self.path_label.set_text(path)
        self.back.set_sensitive(self.index > 0)
        self.forward.set_sensitive(self.index < len(self.history) - 1)
        self.up.set_sensitive(path != "/")
        self.populate()

    def go_back(self) -> None:
        if self.index > 0:
            self.index -= 1
            self.navigate(self.history[self.index], record=False)

    def go_forward(self) -> None:
        if self.index < len(self.history) - 1:
            self.index += 1
            self.navigate(self.history[self.index], record=False)

    def go_up(self) -> None:
        self.navigate(os.path.dirname(self.current))

    def reload(self) -> None:
        self.populate()

    def on_place(self, _listbox, row) -> None:
        self.navigate(row.path)

    def populate(self) -> None:
        self.store.clear()
        try:
            entries = sorted(
                os.scandir(self.current),
                key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()),
            )
        except PermissionError:
            self.store.append(["(permission denied)", "", ""])
            return
        for entry in entries:
            try:
                stat = entry.stat()
                size = "<dir>" if entry.is_dir() else human_size(stat.st_size)
                modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                size, modified = "", ""
            self.store.append([entry.name, size, modified])

    # -- actions ----------------------------------------------------------
    def selected_path(self) -> str:
        model, tree_iter = self.view.get_selection().get_selected()
        if tree_iter is None:
            return ""
        return os.path.join(self.current, model[tree_iter][0])

    def on_open(self, _view, _path, _column) -> None:
        target = self.selected_path()
        if not target:
            return
        if os.path.isdir(target):
            self.navigate(target)
        else:
            open_path(target)

    def on_button(self, _view, event) -> None:
        if event.button != 3:
            return
        target = self.selected_path()
        if not target:
            return
        menu = Gtk.Menu()
        for label, callback in (
            ("Open", lambda: self.on_open(None, None, None)),
            ("Rename", lambda: self.rename(target)),
            ("Delete", lambda: self.delete(target)),
        ):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _i, cb=callback: cb())
            menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)

    def rename(self, target: str) -> None:
        dialog = Gtk.Dialog(title="Rename", transient_for=self, flags=0)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Rename", Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text(os.path.basename(target))
        dialog.get_content_area().add(entry)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            new_name = entry.get_text().strip()
            if new_name and new_name != os.path.basename(target):
                try:
                    os.rename(target, os.path.join(self.current, new_name))
                except OSError as exc:
                    self.error(str(exc))
        dialog.destroy()
        self.reload()

    def delete(self, target: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self, flags=0, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Move '{os.path.basename(target)}' to trash?",
        )
        if dialog.run() == Gtk.ResponseType.YES:
            try:
                Gio.File.new_for_path(target).trash(None)
            except Exception:
                try:
                    if os.path.isdir(target):
                        shutil.rmtree(target)
                    else:
                        os.remove(target)
                except OSError as exc:
                    self.error(str(exc))
        dialog.destroy()
        self.reload()

    def new_folder(self) -> None:
        dialog = Gtk.Dialog(title="New Folder", transient_for=self, flags=0)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Create", Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text("New Folder")
        dialog.get_content_area().add(entry)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            name = entry.get_text().strip()
            if name:
                try:
                    os.mkdir(os.path.join(self.current, name))
                except OSError as exc:
                    self.error(str(exc))
        dialog.destroy()
        self.reload()

    def error(self, message: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self, flags=0, message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE, text=message,
        )
        dialog.run()
        dialog.destroy()


def main() -> int:
    start = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~")
    Explorer(start)
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
