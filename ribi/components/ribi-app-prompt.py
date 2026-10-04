#!/usr/bin/python3
"""ribi-app-prompt - a tiny GTK3 dialog helper for shell scripts.

Usage:
  ribi-app-prompt --entry  "Title" "Prompt" [--default TEXT]
  ribi-app-prompt --info   "Title" "Message"
  ribi-app-prompt --error  "Title" "Message"
  ribi-app-prompt --yesno  "Title" "Question"

Exit status: 0 for OK/Yes/Info, 1 for Cancel/No or on usage error. For --entry
the entered text is printed to stdout.
"""

import sys


def usage() -> int:
    sys.stderr.write(__doc__)
    return 1


def gtk_dialog(mode: str, title: str, message: str, default: str) -> int:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
    except Exception as exc:
        # Headless fallback: emit defaults so callers never hang.
        sys.stderr.write(f"[ribi-app-prompt] GTK unavailable: {exc}\n")
        if mode == "entry":
            print(default)
            return 0
        return 0

    if mode == "entry":
        dialog = Gtk.Dialog(title=title, flags=0)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "OK", Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text(default)
        entry.set_activates_default(True)
        label = Gtk.Label(label=message, xalign=0)
        box = dialog.get_content_area()
        box.add(label)
        box.add(entry)
        dialog.show_all()
        response = dialog.run()
        text = entry.get_text()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            print(text)
            return 0
        return 1

    if mode == "info":
        dialog = Gtk.MessageDialog(
            flags=0, message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE, text=title, secondary_text=message,
        )
        dialog.run()
        dialog.destroy()
        return 0

    if mode == "error":
        dialog = Gtk.MessageDialog(
            flags=0, message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE, text=title, secondary_text=message,
        )
        dialog.run()
        dialog.destroy()
        return 0

    if mode == "yesno":
        dialog = Gtk.MessageDialog(
            flags=0, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO, text=title, secondary_text=message,
        )
        response = dialog.run()
        dialog.destroy()
        return 0 if response == Gtk.ResponseType.YES else 1

    return usage()


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 3:
        return usage()
    mode = args[0].lstrip("-")
    title = args[1]
    message = args[2]
    default = ""
    if "--default" in args:
        default = args[args.index("--default") + 1]
    if mode not in ("entry", "info", "error", "yesno"):
        return usage()
    return gtk_dialog(mode, title, message, default)


if __name__ == "__main__":
    raise SystemExit(main())
