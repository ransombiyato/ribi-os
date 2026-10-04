#!/bin/sh
set -eu
mkdir -p /run/user/1000
chown 1000:1000 /run/user/1000
chmod 700 /run/user/1000
chown -R 1000:1000 /home/ribi
export DISPLAY=${DISPLAY:-:0}
export HOME=/home/ribi
export USER=ribi
export LOGNAME=ribi
export XDG_RUNTIME_DIR=/run/user/1000
export XAUTHORITY=${XAUTHORITY:-/home/ribi/.Xauthority}
printf '[RIBI-SETUP] session display=%s uid=%s
' "$DISPLAY" "$(id -u)" >/dev/ttyS0 2>/dev/null || true
exec /usr/local/bin/ribi-xfce-session
