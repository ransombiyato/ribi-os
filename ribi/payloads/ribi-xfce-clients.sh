#!/bin/sh
set -eu
export DISPLAY=${DISPLAY:-:0}
export HOME=/home/ribi
export USER=ribi
export LOGNAME=ribi
export XDG_CURRENT_DESKTOP=XFCE
export XDG_SESSION_DESKTOP=xfce
mkdir -p "$HOME/.config" /run/user/1000
printf 'RIBI_XFCE_CLIENTS_START uid=%s display=%s
' "$(id -u)" "$DISPLAY" | tee "$HOME/xfce-session.log" >/dev/ttyS0 2>/dev/null || true
unset DBUS_SESSION_BUS_ADDRESS
xfwm4 --replace >>"$HOME/xfwm4.log" 2>&1 & wm_pid=$!

printf 'RIBI_XFCE_PIDS wm=%s
' "$wm_pid" >/dev/ttyS0 2>/dev/null || true
sleep 8
printf 'RIBI_XFCE_STATUS wm=%s desktop=%s panel=%s
' "$(kill -0 "$wm_pid" 2>/dev/null; echo $?)" "$(kill -0 "$desktop_pid" 2>/dev/null; echo $?)" "$(kill -0 "$panel_pid" 2>/dev/null; echo $?)" >/dev/ttyS0 2>/dev/null || true
zen-browser --no-sandbox --disable-gpu --no-first-run --user-data-dir="$HOME/.zen-browser" >/home/ribi/zen-browser.log 2>&1 &
sleep 3
if ! kill -0 "$wm_pid" 2>/dev/null || ! kill -0 "$desktop_pid" 2>/dev/null || ! kill -0 "$panel_pid" 2>/dev/null; then
    printf '[1;31mXFCE startup failed for ribi[0m
' >/dev/tty1 2>/dev/null || true
    cat "$HOME/xfwm4.log" "$HOME/xfdesktop.log" "$HOME/xfce4-panel.log" >/dev/tty1 2>/dev/null || true
    exit 1
fi
wait
