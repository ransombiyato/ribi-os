#!/bin/sh
set -u
failed=0
for ifc in $(ls /sys/class/net 2>/dev/null | grep -v '^lo$'); do
    ip link set "$ifc" up 2>/dev/null || { failed=1; continue; }
    if [ -f /etc/wpa_supplicant/wpa_supplicant.conf ] &&
       command -v wpa_supplicant >/dev/null 2>&1 &&
       [ -e "/sys/class/net/$ifc/wireless" ]; then
        wpa_supplicant -B -i "$ifc" -c /etc/wpa_supplicant/wpa_supplicant.conf 2>/dev/null || failed=1
        sleep 2
    fi
    if command -v dhcpcd >/dev/null 2>&1; then
        dhcpcd -q "$ifc" 2>/dev/null || failed=1
    elif command -v udhcpc >/dev/null 2>&1; then
        udhcpc -i "$ifc" -n -q 2>/dev/null || failed=1
    else
        failed=1
    fi
done
# Require an IPv4 address on at least one non-loopback interface when one exists.
if ls /sys/class/net 2>/dev/null | grep -qv '^lo$' && ! ip -4 addr show scope global 2>/dev/null | grep -q 'inet '; then
    exit 1
fi
exit "$failed"
