#!/bin/sh
# Ribi terminal launcher: prefer lxterminal (nicer tabs/font rendering) and
# fall back to xterm. Extra arguments are forwarded, so `ribi-terminal -e prog`
# works the same for either terminal.
if [ -x /usr/bin/lxterminal ]; then
    exec /usr/bin/lxterminal --title "Ribi Terminal" "$@"
fi
exec /usr/bin/xterm -title "Ribi Terminal" "$@"
