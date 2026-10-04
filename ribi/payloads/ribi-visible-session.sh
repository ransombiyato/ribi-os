#!/bin/sh
set -eu
LOG=/tmp/ribi-visible.log
export DISPLAY=${DISPLAY:-:0}
export XAUTHORITY=${XAUTHORITY:-/run/ribi/server.auth}
export HOME=/home/ribi USER=ribi LOGNAME=ribi
export XDG_RUNTIME_DIR=/run/user/1000 XDG_CURRENT_DESKTOP=Ribi XDG_SESSION_DESKTOP=ribi XDG_SESSION_TYPE=x11
export XDG_CONFIG_HOME=/home/ribi/.config XDG_CONFIG_DIRS=/etc/xdg
printf '[RIBI-VISIBLE] entered display=%s uid=%s\n' "$DISPLAY" "$(id -u)" >>"$LOG" 2>&1
printf '[RIBI-VISIBLE] launching dbus-run-session openbox-session\n' >/dev/ttyS0 2>/dev/null || true
if command -v xrandr >/dev/null 2>&1; then
    _ribi_xrandr_ok=1
    _ribi_output=none
    printf '[RIBI-VISIBLE] xrandr query begin
' >/dev/ttyS0 2>/dev/null || true
    xrandr --query >/dev/ttyS0 2>&1 || true
    # The direct Xorg live path can expose no connected RandR output even
    # though its virtual framebuffer supports the requested desktop size.
    # Set the framebuffer first so root-window geometry is truly 1280x800.
    xrandr --fb 1280x800 >>"$LOG" 2>&1 || true
    xrandr --fb 1280x800 >/dev/ttyS0 2>&1 || true
    for _ribi_try in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        _ribi_output=$(xrandr --query 2>>"$LOG" | awk '$2 == "connected" {print $1; exit}')
        if [ -n "$_ribi_output" ] && xrandr --output "$_ribi_output" --mode 1280x800 >>"$LOG" 2>&1; then
            xrandr --fb 1280x800 >>"$LOG" 2>&1 || true
            _ribi_xrandr_ok=0
            break
        fi
        sleep 1
    done
    printf '[RIBI-VISIBLE] xrandr mode attempt output=%s
' "$_ribi_output" >/dev/ttyS0 2>/dev/null || true
    xrandr --query >/dev/ttyS0 2>&1 || true
    printf '[RIBI-VISIBLE] xrandr output=%s mode=1280x800 rc=%s
' "$_ribi_output" "$_ribi_xrandr_ok" >>"$LOG" 2>&1
fi
# Phase 9: Ribi WM is the default. RIBI_USE_WM=0 or ribi.wm=0 is an
# emergency compatibility escape hatch; any startup failure falls back to
# Openbox instead of leaving a black screen.
_ribi_use_wm=${RIBI_USE_WM:-1}
grep -qw 'ribi.wm=0' /proc/cmdline 2>/dev/null && _ribi_use_wm=0 || true
grep -qw 'ribi.wm=1' /proc/cmdline 2>/dev/null && _ribi_use_wm=1 || true
if [ "$_ribi_use_wm" = 1 ]; then
    printf '[RIBI-VISIBLE] default Ribi WM requested
' >>"$LOG" 2>&1
    /usr/local/bin/ribi-wm.py >>"$LOG" 2>&1 & _ribi_wm_pid=$!
    sleep 1
    if kill -0 "$_ribi_wm_pid" 2>/dev/null; then
        printf '[RIBI-VISIBLE] Ribi WM active pid=%s
' "$_ribi_wm_pid" >>"$LOG" 2>&1
        /usr/local/bin/ribi-wallpaper-viewer >>"$HOME/.cache/ribi-wallpaper.log" 2>&1 &
        /usr/local/bin/ribi-dock >>"$HOME/.cache/ribi-dock.log" 2>&1 &
        wait "$_ribi_wm_pid"
        exit $?
    fi
    printf '[RIBI-VISIBLE] Ribi WM failed; falling back to Openbox
' >>"$LOG" 2>&1
fi
exec /usr/bin/dbus-run-session -- /usr/bin/openbox-session >>"$LOG" 2>&1
