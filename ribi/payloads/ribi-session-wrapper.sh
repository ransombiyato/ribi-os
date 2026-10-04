#!/bin/sh
LOG=/tmp/ribi-session-wrapper.log
printf '[RIBI-WRAPPER] entered uid=%s args=%s display=%s home=%s
' "$(id -u 2>/dev/null)" "$*" "${DISPLAY:-}" "${HOME:-}" >>"$LOG" 2>&1
printf '[RIBI-WRAPPER] entered
' >/dev/ttyS0 2>/dev/null || true
if [ "$#" -gt 0 ]; then
    exec "$@" >>"$LOG" 2>&1
fi
exec /usr/local/bin/ribi-xfce-session >>"$LOG" 2>&1
