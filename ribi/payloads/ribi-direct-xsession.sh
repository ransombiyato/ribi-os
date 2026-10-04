#!/bin/sh
LOG=/tmp/ribi-xsession.log
printf '[RIBI-XSESSION] direct visible session args=%s uid=%s display=%s
' "$*" "$(id -u 2>/dev/null)" "${DISPLAY:-}" >>"$LOG" 2>&1
printf '[RIBI-XSESSION] launching persistent visible session
' >/dev/ttyS0 2>/dev/null || true
exec /usr/local/bin/ribi-visible-session >>"$LOG" 2>&1
