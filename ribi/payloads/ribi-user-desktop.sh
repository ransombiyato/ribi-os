#!/bin/sh
export DISPLAY=:0 HOME=/home/ribi USER=ribi LOGNAME=ribi XDG_RUNTIME_DIR=/run/user/1000
mkdir -p "$HOME/.config" "$XDG_RUNTIME_DIR" "$HOME/Pictures/Screenshots"
xfwm4 --replace >"$HOME/xfwm4.log" 2>&1 &
sleep 2
printf '[RIBI-WM] xfwm4 started
' >/dev/ttyS0 2>/dev/null || true
xfce4-panel --disable-wm-check >"$HOME/panel.log" 2>&1 &
printf '[RIBI-PANEL] launch submitted
' >/dev/ttyS0 2>/dev/null || true
# The dock is the application entry point. Do not open a carousel of apps at login.
printf '[RIBI-APPS] dock ready; applications are user-launched
' >/dev/ttyS0 2>/dev/null || true
sleep 600
