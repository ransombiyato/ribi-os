#!/bin/sh
LOG=/tmp/ribi-xfce-session.log
printf '[RIBI-XFCE] entered
' >/dev/ttyS0 2>/dev/null || true
printf '[RIBI-XFCE] entered uid=%s display=%s home=%s
' "$(id -u 2>/dev/null)" "${DISPLAY:-}" "${HOME:-}" >>"$LOG" 2>&1
export DISPLAY=${DISPLAY:-:0}
export XDG_RUNTIME_DIR=/run/user/1000
export XDG_CURRENT_DESKTOP=XFCE
export XDG_SESSION_DESKTOP=xfce
export XDG_SESSION_TYPE=x11
export GTK_ICON_THEME=RibiShapes
export GDK_BACKEND=x11
export MOZ_ENABLE_WAYLAND=0
mkdir -p /run/user/1000 "$HOME/.config" >>"$LOG" 2>&1 || true
chown 1000:1000 /run/user/1000 >>"$LOG" 2>&1 || true
printf '[RIBI-XFCE] launching dbus-run-session
' >>"$LOG" 2>&1
printf '[RIBI-XFCE] exec dbus-run-session
' >/dev/ttyS0 2>/dev/null || true
exec /usr/bin/dbus-run-session -- /usr/bin/xfce4-session >>"$LOG" 2>&1
