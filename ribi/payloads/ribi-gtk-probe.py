#!/usr/bin/python3
import sys, faulthandler
faulthandler.enable()
faulthandler.dump_traceback_later(5, repeat=True, file=sys.stderr)
def mark(s):
    try: open('/dev/ttyS0','a').write('[RIBI-GTK-PROBE] '+s+'\n')
    except Exception: pass
mark('import-start')
import gi
mark('gi-imported')
gi.require_version('Gtk', '3.0')
mark('gtk-version-required')
from gi.repository import Gtk
mark('gtk-imported')
win = Gtk.Window(title='Ribi GTK probe')
win.set_default_size(520, 260)
win.connect('destroy', Gtk.main_quit)
label = Gtk.Label(label='Ribi GTK window probe')
win.add(label)
mark('window-created')
win.show_all()
mark('window-shown')
Gtk.main()
