#!/bin/sh
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/root
umask 022
if [ -x /usr/sbin/setfont ] && [ -d /usr/share/consolefonts ]; then
    _ribi_font=/usr/share/consolefonts/Lat2-Terminus16.psfu.gz
    [ -f "$_ribi_font" ] || _ribi_font=$(find /usr/share/consolefonts -maxdepth 1 -type f -name 'ter-*.psf*' | sort | head -1)
    [ -n "$_ribi_font" ] && /usr/sbin/setfont "$_ribi_font" >/dev/null 2>&1 || true
fi
_C='\033[1;36m'; _G='\033[1;32m'; _Y='\033[1;33m'; _R='\033[0m'

# Long-lived services are supervised by ribisvc. Do not install a SIGCHLD trap
# here: BusyBox ash may re-enter a wait loop during the early PID 1 handoff on
# UEFI guests before the interactive console is available.

is_mounted() { grep -q " $1 " /proc/mounts 2>/dev/null; }
require_mount() {
    if ! is_mounted "$1"; then
        echo "[FATAL] Required mount $1 is unavailable"; return 1
    fi
}

mkdir -p /proc /sys /dev /run /tmp /dev/pts /dev/shm
# The live initramfs moves /proc, /sys, and /dev into the target before
# switch_root. Avoid re-running mount/grep probes in PID 1 when that handoff is
# already complete; this is both faster and safer on UEFI firmware guests.
if [ ! -r /proc/mounts ]; then
    is_mounted /proc || mount -t proc proc /proc -o nosuid,noexec,nodev || exit 1
    is_mounted /sys || mount -t sysfs sys /sys -o nosuid,noexec,nodev || exit 1
    is_mounted /dev || mount -t devtmpfs devtmpfs /dev -o mode=0755,nosuid || exit 1
    is_mounted /dev/pts || mount -t devpts devpts /dev/pts -o mode=0620,gid=5,nosuid,noexec || exit 1
    is_mounted /dev/shm || mount -t tmpfs tmpfs /dev/shm -o mode=1777,nosuid,nodev || exit 1
    is_mounted /run || mount -t tmpfs tmpfs /run -o mode=0755,nosuid,nodev || exit 1
    is_mounted /tmp || mount -t tmpfs tmpfs /tmp -o mode=1777,nosuid,nodev || exit 1
fi

require_mount /proc || exit 1
require_mount /sys || exit 1
require_mount /dev || exit 1

if grep -qw 'ribi.nogui=1' /proc/cmdline 2>/dev/null; then
    echo "[DIAGNOSTICS] Running safe guest commands"
    echo '+ uname -a'; uname -a
    echo '+ id'; id
    echo '+ mount'; mount
    echo '+ ls /'; ls /
    echo '+ cat /etc/os-release'; cat /etc/os-release
    echo "[DIAGNOSTICS] Complete"
fi

if [ -f /etc/hostname ]; then hostname "$(cat /etc/hostname | tr -d '\r\n')" || true; fi
ip link set lo up 2>/dev/null || true

# Apply the keyboard layout chosen during setup. The kbd keymaps are gzipped and
# loadkmap wants the raw table, so decompress to a temp file first.
if [ -x /sbin/loadkmap ] && [ -f /etc/conf.d/keymaps ]; then
    _ribi_keymap=$(sed -n 's/^keymap="\{0,1\}\([^"]*\)"\{0,1\}.*/\1/p' /etc/conf.d/keymaps | head -1)
    if [ -n "$_ribi_keymap" ] && [ -f "/usr/share/keymaps/xkb/$_ribi_keymap.map.gz" ]; then
        if gzip -dc "/usr/share/keymaps/xkb/$_ribi_keymap.map.gz" > /tmp/ribi-console.keymap 2>/dev/null; then
            /sbin/loadkmap < /tmp/ribi-console.keymap >/dev/null 2>&1 || true
        fi
    fi
fi

# Installed images are cloned from the same live root; never keep the generic
# build-time D-Bus ID. Create one on first boot and preserve it thereafter.
_ribi_machine_id=$(cat /etc/machine-id 2>/dev/null | tr -d '[:space:]' | tr 'A-F' 'a-f')
case "$_ribi_machine_id" in *[!0123456789abcdef]*|'') _ribi_machine_id='' ;; esac
if [ "${#_ribi_machine_id}" -ne 32 ] || [ "$_ribi_machine_id" = "8de277067b3544d4b65c267d0edab928" ]; then
    command -v dbus-uuidgen >/dev/null 2>&1 && dbus-uuidgen --ensure=/etc/machine-id >/dev/null 2>&1 || true
    _ribi_machine_id=$(cat /etc/machine-id 2>/dev/null | tr -d '[:space:]' | tr 'A-F' 'a-f')
    case "$_ribi_machine_id" in *[!0123456789abcdef]*|'') _ribi_machine_id='' ;; esac
    if [ "${#_ribi_machine_id}" -ne 32 ] || [ "$_ribi_machine_id" = "8de277067b3544d4b65c267d0edab928" ]; then
        _ribi_machine_id=$(od -An -N16 -tx1 /dev/urandom | tr -d '[:space:]')
        [ "${#_ribi_machine_id}" -eq 32 ] && printf '%s\n' "$_ribi_machine_id" > /etc/machine-id
    fi
    chmod 0444 /etc/machine-id 2>/dev/null || true
    printf '[RIBI-INIT] machine-id initialized\n' >/dev/ttyS0 2>/dev/null || true
fi

# The initramfs already resolves its own module metadata before the root handoff.
# Do not run depmod again in PID 1: on some UEFI firmware/QEMU combinations this
# can block while probing the read-only live module tree and delay the console.

# First-boot package setup that normally happens through Alpine package hooks.
# It is executed with the target's own x86_64 binaries after the real root exists.
if [ ! -e /var/lib/ribi/.firstboot-done ]; then
    mkdir -p /var/lib/ribi
    command -v fc-cache >/dev/null 2>&1 && fc-cache -f >/dev/null 2>&1 || true
    command -v glib-compile-schemas >/dev/null 2>&1 && [ -d /usr/share/glib-2.0/schemas ] && glib-compile-schemas /usr/share/glib-2.0/schemas >/dev/null 2>&1 || true
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
    command -v udevadm >/dev/null 2>&1 && udevadm hwdb --update >/dev/null 2>&1 || true
    : > /var/lib/ribi/.firstboot-done
fi

# Bring up device management before the console. devtmpfs provides nodes immediately;
# eudev then supplies hotplug rules and stable device metadata.
printf '[RIBI-INIT] before-udev
' >/dev/ttyS0 2>/dev/null || true
if [ -x /sbin/udevd ]; then
    /sbin/udevd --daemon >/dev/null 2>&1 || /sbin/udevd >/dev/null 2>&1 &
    [ -x /sbin/udevadm ] && /sbin/udevadm trigger --action=add >/dev/null 2>&1 || true
    [ -x /sbin/udevadm ] && /sbin/udevadm settle --timeout=5 >/dev/null 2>&1 || true
fi
printf '[RIBI-INIT] after-udev
' >/dev/ttyS0 2>/dev/null || true

# Handle the physical/QEMU ACPI power button in this non-systemd PID-1 setup.
if command -v acpid >/dev/null 2>&1; then
    acpid -f -n >/tmp/ribi-acpid.log 2>&1 &
    _ribi_acpid_pid=$!
    sleep 0.2
    if kill -0 "$_ribi_acpid_pid" 2>/dev/null; then
        printf '[RIBI-INIT] ACPI power-button handler active
' >/dev/ttyS0 2>/dev/null || true
    else
        printf '[RIBI-INIT] ACPI handler failed to start
' >/dev/ttyS0 2>/dev/null || true
        cat /tmp/ribi-acpid.log >/dev/ttyS0 2>/dev/null || true
    fi
fi

# Start native services without blocking the graphical session on DHCP or other
# optional services. The supervisor remains alive and writes its own diagnostics.
mkdir -p /run/dbus
if [ -x /usr/local/bin/ribisvc ]; then
    /usr/local/bin/ribisvc --start-all >/tmp/ribisvc-boot.log 2>&1 &
fi
printf '[RIBI-INIT] after-services
' >/dev/ttyS0 2>/dev/null || true

if grep -qw 'ribi.nogui=1' /proc/cmdline 2>/dev/null; then
    echo "[DIAGNOSTICS] Running safe guest commands"
    echo '+ uname -a'; /bin/busybox uname -a
    echo '+ id'; /bin/busybox id
    echo '+ mount'; /bin/busybox mount
    echo '+ ls /'; ls /
    echo '+ cat /etc/os-release'; cat /etc/os-release
    echo "[DIAGNOSTICS] Complete"
fi
while :; do
    # Prefer the serial console when one is provided (for headless BIOS/UEFI
    # systems and diagnostics); otherwise retain the normal VGA console.
    if grep -qw 'ribi.serial=1' /proc/cmdline 2>/dev/null && [ -c /dev/ttyS0 ] && [ -w /dev/ttyS0 ]; then
        # Debug Mode: serial is explicitly requested by the kernel command line.
        printf '[BOOT-1] Entered ribi-init
' >/dev/ttyS0
        setsid -c /bin/sh -l </dev/ttyS0 >/dev/ttyS0 2>&1
    elif [ -x /usr/local/bin/ribi-xorg ] && [ -x /usr/local/bin/ribi-visible-session ]; then
        # Own the VT and DRM device with exactly one Xorg process. LightDM's
        # repeated greeter/session restart loop can leave the virtio DRM device
        # busy, so the live release uses a deterministic direct session here.
        printf '[RIBI-GUI] init pid=%s uid=%s tty1=%s ttyS0=%s
' "$$" "$(id -u 2>/dev/null)" "$(test -c /dev/tty1; echo $?)" "$(test -c /dev/ttyS0; echo $?)" >/dev/ttyS0 2>/dev/null || true
        printf '[RIBI-GUI] PATH=%s DISPLAY=%s HOME=%s
' "${PATH:-}" "${DISPLAY:-}" "${HOME:-}" >/dev/ttyS0 2>/dev/null || true
        command -v chvt >/dev/null 2>&1 && chvt 1 >/dev/null 2>&1 || true
        printf '[RIBI-GUI] starting direct Xorg
' >/dev/ttyS0 2>/dev/null || true
        mkdir -p /run/ribi /run/user/1000
        chown 1000:1000 /run/user/1000 2>/dev/null || true
        chmod 700 /run/user/1000
        AUTH=/run/ribi/server.auth
        rm -f "$AUTH"
        COOKIE=$(od -An -N16 -tx1 /dev/urandom | tr -d ' 
')
        /usr/bin/xauth -f "$AUTH" add :0 MIT-MAGIC-COOKIE-1 "$COOKIE" >/dev/null 2>&1 || true
        chmod 644 "$AUTH"
        /usr/local/bin/ribi-xorg :0 -auth "$AUTH" -config /etc/X11/xorg.conf -nolisten tcp -noreset -keeptty vt1 -novtswitch </dev/tty1 >/tmp/ribi-xorg-direct.log 2>&1 &
        _x_pid=$!
        _x_real=0
        for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
            _x_real=$(pgrep -xo Xorg 2>/dev/null || true)
            if [ -n "$_x_real" ] && [ -S /tmp/.X11-unix/X0 ] && DISPLAY=:0 XAUTHORITY="$AUTH" /usr/bin/xprop -root >/dev/null 2>&1; then break; fi
            sleep 1
        done
        printf '[RIBI-GUI] xorg_parent=%s real=%s socket=%s
' "$_x_pid" "$_x_real" "$(test -S /tmp/.X11-unix/X0; echo $?)" >/dev/ttyS0 2>/dev/null || true
        printf '[RIBI-GUI] Xorg log tail begin
' >/dev/ttyS0 2>/dev/null || true
        tail -n 140 /tmp/ribi-xorg-direct.log >/dev/ttyS0 2>&1 || true
        printf '[RIBI-GUI] Xorg log tail end
' >/dev/ttyS0 2>/dev/null || true
        if command -v xrandr >/dev/null 2>&1; then
            DISPLAY=:0 XAUTHORITY="$AUTH" xrandr --fb 1280x800 >/dev/ttyS0 2>&1 || true
            DISPLAY=:0 XAUTHORITY="$AUTH" xrandr --query >/dev/ttyS0 2>&1 || true
        fi
        printf '[RIBI-GUI] starting persistent ribi session
' >/dev/ttyS0 2>/dev/null || true
        DISPLAY=:0 XAUTHORITY="$AUTH" HOME=/home/ribi USER=ribi LOGNAME=ribi XDG_RUNTIME_DIR=/run/user/1000 /usr/bin/feh --bg-fill --no-fehbg /usr/share/backgrounds/ribi-wallpaper.png >/tmp/ribi-wallpaper-direct.log 2>&1 || true
        printf '[RIBI-GUI] wallpaper painted
' >/dev/ttyS0 2>/dev/null || true
        # Keep the live critical path deterministic: the X server and clients
        # share one PID-1-owned session. The target BusyBox su works in chroot
        # smoke tests but blocks in this live PID-1/VT context before clients
        # start, so do not let a privilege wrapper hide the desktop.
        mkdir -p /home/ribi/.cache /home/ribi/.config /home/ribi/Desktop /home/ribi/Pictures/Screenshots
        chown 1000:1000 /home/ribi /home/ribi/.cache /home/ribi/.config /home/ribi/Desktop /home/ribi/Pictures /home/ribi/Pictures/Screenshots 2>/dev/null || true
        _ribi_wm_pid=''
        _ribi_use_wm=${RIBI_USE_WM:-1}
        grep -qw 'ribi.wm=0' /proc/cmdline 2>/dev/null && _ribi_use_wm=0 || true
        if [ "$_ribi_use_wm" = 1 ] && [ -x /usr/local/bin/ribi-wm.py ]; then
            DISPLAY=:0 XAUTHORITY="$AUTH" HOME=/home/ribi USER=ribi LOGNAME=ribi                 XDG_RUNTIME_DIR=/run/user/1000 XDG_CURRENT_DESKTOP=Ribi XDG_SESSION_DESKTOP=ribi                 /usr/local/bin/ribi-wm.py >>/tmp/ribi-wm-direct.log 2>&1 &
            _ribi_wm_pid=$!
            sleep 1
            if kill -0 "$_ribi_wm_pid" 2>/dev/null; then
                printf '[RIBI-GUI] default Ribi WM active pid=%s
' "$_ribi_wm_pid" >/dev/ttyS0 2>/dev/null || true
            else
                printf '[RIBI-GUI] Ribi WM failed; continuing with compatibility fallback
' >/dev/ttyS0 2>/dev/null || true
                _ribi_wm_pid=''
            fi
        fi
        printf '[RIBI-GUI] starting persistent Ribi shell
' >/dev/ttyS0 2>/dev/null || true
        DISPLAY=:0 XAUTHORITY="$AUTH" HOME=/home/ribi USER=ribi LOGNAME=ribi XDG_RUNTIME_DIR=/run/user/1000 XDG_CURRENT_DESKTOP=Ribi XDG_SESSION_DESKTOP=ribi /usr/local/bin/ribi-drop-session /usr/local/bin/ribi-shell.py >/tmp/ribi-session-direct.log 2>&1
        _session_rc=$?
        printf '[RIBI-GUI] ribi session exit=%s\n' "$_session_rc" >/dev/ttyS0 2>/dev/null || true
        [ "$_session_rc" -eq 0 ] || cat /tmp/ribi-session-direct.log >/dev/ttyS0 2>/dev/null || true
        printf '[RIBI-GUI] direct session ended
' >/dev/ttyS0 2>/dev/null || true
        [ -n "$_ribi_wm_pid" ] && kill "$_ribi_wm_pid" 2>/dev/null || true
        kill "$_x_pid" 2>/dev/null || true
    elif [ -c /dev/tty1 ] && [ -w /dev/tty1 ]; then
        # Normal Vectras/PC mode: make tty1 the visible controlling VT before
        # attaching the login shell, since firmware may leave another VT active.
        command -v chvt >/dev/null 2>&1 && chvt 1 >/dev/null 2>&1 || true
        printf '[BOOT-1] Entered ribi-init
' >/dev/tty1
        setsid -c /bin/sh -l </dev/tty1 >/dev/tty1 2>&1
    elif [ -c /dev/tty1 ] && [ -w /dev/tty1 ]; then
        su -s /bin/sh - ribi -c "exec /bin/sh -l" </dev/tty1 >/dev/tty1 2>&1
    elif [ -e /dev/console ]; then
        su -s /bin/sh - ribi -c "exec /bin/sh -l" </dev/console >/dev/console 2>&1
    else
        su -s /bin/sh - ribi -c "exec /bin/sh -l"
    fi
    echo "[WARN] Console shell exited; respawning in 1s."
    sleep 1
done
