#!/usr/bin/python3
"""ribi-edit - a lightweight GTK3 text and code editor for Ribi OS.

Uses GtkSourceView when available (syntax highlighting, line numbers) and
falls back to a plain monospace Gtk.TextView otherwise, so it always runs on a
minimal GTK3 stack. Supports open/save/save-as, find, and a live status bar.
"""

import os
import sys

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, Gtk

    try:
        gi.require_version("GtkSource", "3.0")
        from gi.repository import GtkSource

        HAS_SOURCE = True
    except Exception:
        HAS_SOURCE = False
except Exception as exc:  # pragma: no cover
    sys.stderr.write(f"[ribi-edit] GTK unavailable: {exc}\n")
    raise SystemExit(1)

CSS = """
#ribi-edit-status { font-size: 11px; color: #9fb0c8; padding: 2px 6px; }
"""


class Editor(Gtk.Window):
    def __init__(self, path: str = ""):
        super().__init__(title="Ribi Editor")
        self.set_default_size(900, 620)
        self.connect("destroy", Gtk.main_quit)
        self.path = ""

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("Ribi Editor")
        self.set_titlebar(header)

        open_button = Gtk.Button.new_from_icon_name("document-open", Gtk.IconSize.BUTTON)
        save_button = Gtk.Button.new_from_icon_name("document-save", Gtk.IconSize.BUTTON)
        save_as_button = Gtk.Button.new_from_icon_name("document-save-as", Gtk.IconSize.BUTTON)
        find_button = Gtk.Button.new_from_icon_name("edit-find", Gtk.IconSize.BUTTON)
        open_button.connect("clicked", lambda _b: self.open_dialog())
        save_button.connect("clicked", lambda _b: self.save())
        save_as_button.connect("clicked", lambda _b: self.save_as())
        find_button.connect("clicked", lambda _b: self.find())
        for widget in (open_button, save_button, save_as_button, find_button):
            header.pack_start(widget)

        if HAS_SOURCE:
            self.buffer = GtkSource.Buffer()
            manager = GtkSource.LanguageManager.get_default()
            self.buffer.set_language(manager.guess_language(None, None))
            self.view = GtkSource.View.new_with_buffer(self.buffer)
            self.view.set_show_line_numbers(True)
            self.view.set_highlight_current_line(True)
            self.view.set_auto_indent(True)
            self.view.set_tab_width(4)
            self.view.set_insert_spaces_instead_of_tabs(True)
        else:
            self.buffer = Gtk.TextBuffer()
            self.view = Gtk.TextView.new_with_buffer(self.buffer)
            self.view.set_monospace(True)

        self.buffer.connect("changed", lambda _b: self.update_status())

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.add(self.view)

        self.status = Gtk.Label(label="")
        self.status.set_name("ribi-edit-status")
        self.status.set_xalign(0)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(scroll, True, True, 0)
        box.pack_start(self.status, False, False, 0)
        self.add(box)

        self.show_all()
        if path:
            self.load(path)
        self.update_status()

    def load(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as exc:
            self.error(str(exc))
            return
        self.buffer.set_text(text)
        self.path = path
        self.set_title(os.path.basename(path))
        if HAS_SOURCE:
            language = GtkSource.LanguageManager.get_default().guess_language(
                path, None
            )
            if language is not None:
                self.buffer.set_language(language)
        self.update_status()

    def open_dialog(self) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Open File", transient_for=self, action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.OK)
        if dialog.run() == Gtk.ResponseType.OK:
            self.load(dialog.get_filename())
        dialog.destroy()

    def save(self) -> None:
        if not self.path:
            self.save_as()
            return
        self.write(self.path)

    def save_as(self) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Save As", transient_for=self, action=Gtk.FileChooserAction.SAVE
        )
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        dialog.set_do_overwrite_confirmation(True)
        if self.path:
            dialog.set_filename(self.path)
        if dialog.run() == Gtk.ResponseType.OK:
            self.write(dialog.get_filename())
        dialog.destroy()

    def write(self, path: str) -> None:
        text = self.buffer.get_text(
            self.buffer.get_start_iter(), self.buffer.get_end_iter(), True
        )
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            self.error(str(exc))
            return
        self.path = path
        self.set_title(os.path.basename(path))
        self.update_status()

    def find(self) -> None:
        dialog = Gtk.Dialog(title="Find", transient_for=self, flags=0)
        dialog.add_buttons("Close", Gtk.ResponseType.CLOSE)
        entry = Gtk.Entry()
        dialog.get_content_area().add(entry)
        dialog.show_all()
        dialog.run()
        needle = entry.get_text()
        dialog.destroy()
        if not needle:
            return
        text = self.buffer.get_text(
            self.buffer.get_start_iter(), self.buffer.get_end_iter(), True
        )
        index = text.find(needle)
        if index < 0:
            self.status.set_text(f"Not found: {needle}")
            return
        start = self.buffer.get_iter_at_offset(index)
        end = self.buffer.get_iter_at_offset(index + len(needle))
        self.buffer.select_range(start, end)
        self.view.scroll_to_iter(start, 0.1, False, 0, 0)

    def update_status(self) -> None:
        text = self.buffer.get_text(
            self.buffer.get_start_iter(), self.buffer.get_end_iter(), True
        )
        lines = text.count("\n") + 1
        name = self.path or "untitled"
        self.status.set_text(f"{name}   |   {lines} lines   |   {len(text)} chars")

    def error(self, message: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=message,
        )
        dialog.run()
        dialog.destroy()


def main() -> int:
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    Editor(sys.argv[1] if len(sys.argv) > 1 else "")
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
