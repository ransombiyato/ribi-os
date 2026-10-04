#!/bin/sh
set -eu
LOG="$HOME/.cache/ribi-autostart.log"
export DISPLAY=${DISPLAY:-:0}
export XAUTHORITY=${XAUTHORITY:-/run/ribi/server.auth}
export GDK_BACKEND=x11
export GTK_USE_PORTAL=0
export NO_AT_BRIDGE=1
export GTK_ICON_THEME=RibiShapes
export GTK_MODULES=
export XCURSOR_THEME=Adwaita
export GIO_USE_VFS=local
export GSETTINGS_BACKEND=memory
export XDG_CURRENT_DESKTOP=Ribi
export XDG_SESSION_DESKTOP=ribi
mkdir -p "$HOME/.cache" "$HOME/Pictures/Screenshots" "$XDG_RUNTIME_DIR"
printf '[RIBI-AUTOSTART] uid=%s display=%s
' "$(id -u)" "$DISPLAY" >>"$LOG" 2>&1
# Paint only the X root; never create a foreground wallpaper/probe window.
/usr/local/bin/ribi-wallpaper-viewer >>"$HOME/.cache/ribi-wallpaper.log" 2>&1
# The dock is the sole graphical autostart client. It remains independent of app exits.
exec /usr/local/bin/ribi-dock >>"$HOME/.cache/ribi-dock.log" 2>&1
