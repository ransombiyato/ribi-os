#!/bin/sh
LOG=/tmp/ribi-desktop.log
printf '[RIBI-DESKTOP] entered
' >>"$LOG" 2>&1
printf '[RIBI-DESKTOP] entered uid=%s display=%s
' "$(id -u)" "${DISPLAY:-}" >/dev/ttyS0 2>/dev/null || true
export DISPLAY=${DISPLAY:-:0}
export HOME=/home/ribi
export USER=ribi
export LOGNAME=ribi
export XDG_RUNTIME_DIR=/run/user/1000
export GTK_ICON_THEME=RibiShapes
mkdir -p "$XDG_RUNTIME_DIR" "$HOME/Desktop" >>"$LOG" 2>&1
chown -R 1000:1000 "$XDG_RUNTIME_DIR" "$HOME" >>"$LOG" 2>&1 || true
printf '[RIBI-DESKTOP] launching terminal
' >>"$LOG" 2>&1
printf '[RIBI-DESKTOP] launching terminal
' >/dev/ttyS0 2>/dev/null || true
if command -v dbus-run-session >/dev/null 2>&1; then
    exec dbus-run-session -- xfce4-terminal --disable-server --geometry=90x28+80+80 --title='Ribi OS Desktop' >>"$LOG" 2>&1
else
    exec xfce4-terminal --disable-server --geometry=90x28+80+80 --title='Ribi OS Desktop' >>"$LOG" 2>&1
fi
