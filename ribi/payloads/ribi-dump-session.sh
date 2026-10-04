#!/bin/sh
printf '
[RIBI-DUMP] session stopped
' >/dev/ttyS0 2>/dev/null || true
for f in /tmp/ribi-xsession.log /tmp/ribi-xfce-session.log /tmp/ribi-session-wrapper.log; do
  if [ -f "$f" ]; then
    printf '[RIBI-DUMP] %s
' "$f" >/dev/ttyS0 2>/dev/null || true
    cat "$f" >/dev/ttyS0 2>/dev/null || true
  fi
done
exit 0
