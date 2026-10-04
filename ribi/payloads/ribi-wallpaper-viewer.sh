#!/bin/sh
set -eu
path=/usr/share/backgrounds/ribi-wallpaper.png
printf '[RIBI-WALLPAPER] painting X root %s\n' "$path" >/dev/ttyS0 2>/dev/null || true
# Paint the X root window instead of creating a foreground image window. This
# keeps the wallpaper visible while leaving the panel, launcher, and app windows
# above it in the normal stacking order.
exec /usr/bin/feh --bg-fill --no-fehbg "$path"
