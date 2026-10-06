#!/bin/sh
# =============================================================================
# RIBI OS - Live Media Resilient Boot Script (/init)
# =============================================================================
export PATH=/sbin:/bin:/usr/sbin:/usr/bin
umask 022

mkdir -p /proc /sys /dev /run /run/media /run/rootfs /run/overlay /sysroot

mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
mkdir -p /dev/pts /dev/shm
mount -t devpts devpts /dev/pts -o gid=5,mode=620,ptmxmode=666 2>/dev/null || true
/bin/busybox ln -sf pts/ptmx /dev/ptmx
printf '[RIBI-INITRAMFS] devpts='; /bin/busybox mount | /bin/busybox grep devpts || echo absent
printf '[RIBI-INITRAMFS] ptmx='; ls -l /dev/ptmx /dev/pts 2>/dev/null || true
mount -t tmpfs tmpfs /dev/shm -o mode=1777 2>/dev/null || true
# Ensure vital device nodes exist
modprobe loop 2>/dev/null || true
[ -e /dev/console ] || mknod -m 600 /dev/console c 5 1
[ -e /dev/null ]    || mknod -m 666 /dev/null c 1 3
[ -e /dev/zero ]    || mknod -m 666 /dev/zero c 1 5
[ -e /dev/tty ]     || mknod -m 666 /dev/tty c 5 0

# Resolve module dependencies (modules.dep/modules.alias) for whatever was staged
# into THIS initramfs. This must run here, at boot, against the target's own
# /lib/modules tree -- a modules.dep computed on the host at build time would not
# necessarily match and is not shipped. Safe no-op if no modules were staged.
depmod -a 2>/dev/null || true

# Load early filesystem and block drivers if modular kernel (most are built-in on
# Ribi's bespoke kernel config, so these are best-effort and never fatal).
for mod in scsi_mod sd_mod libahci ahci virtio virtio_pci virtio_blk loop cdrom sr_mod isofs squashfs overlay; do
    if modprobe "$mod" 2>/dev/null; then
        continue
    fi
    modfile=$(find /lib/modules -name "${mod}.ko" 2>/dev/null | head -n 1)
    [ -n "$modfile" ] && insmod "$modfile" 2>/dev/null
done

# Device discovery loop. Normal boots use a visible smooth gradient loader;
# detailed diagnostics remain available through the explicit Debug Mode entry.
# Force the splash onto the VGA virtual terminal instead of whichever console
# happens to be last on the kernel command line.
if [ -c /dev/tty0 ] && [ -w /dev/tty0 ]; then
    exec > /dev/tty0 2>&1
    command -v chvt >/dev/null 2>&1 && chvt 1 >/dev/null 2>&1 || true
fi
FOUND=""
_TITLE='[1;37m'; _DIM='[90m'; _R='[0m'
draw_loader() {
    label="$1"; filled="$2"; total=32
    printf '[2J[H'
    printf "${_TITLE}RIBI OS${_R}  ${_DIM}x86_64${_R}

"
    printf "${_DIM}%s${_R}

" "$label"
    i=1
    while [ "$i" -le "$total" ]; do
        if [ "$i" -le "$filled" ]; then
            case "$i" in
              1|2|3|4|5|6|7|8) color='[38;5;153m' ;;
              9|10|11|12|13|14|15|16) color='[38;5;117m' ;;
              17|18|19|20|21|22|23|24) color='[38;5;132m' ;;
              25|26|27|28|29|30) color='[38;5;124m' ;;
              *) color='[38;5;88m' ;;
            esac
            printf "%b█%b" "$color" "$_R"
        else
            printf "${_DIM}░${_R}"
        fi
        i=$((i + 1))
    done
    printf "  ${_TITLE}%3s%%${_R}
" "$((filled * 100 / total))"
}
mount_with_timeout() {
    _mt_seconds="$1"; shift
    "$@" >/dev/null 2>&1 &
    _mt_pid=$!
    _mt_elapsed=0
    while kill -0 "$_mt_pid" 2>/dev/null; do
        if [ "$_mt_elapsed" -ge "$_mt_seconds" ]; then
            kill "$_mt_pid" 2>/dev/null || true
            wait "$_mt_pid" 2>/dev/null || true
            return 124
        fi
        sleep 1
        _mt_elapsed=$((_mt_elapsed + 1))
    done
    wait "$_mt_pid"
}

# Hand the initramfs over to /sysroot, then exec the target init.
# A bare `chroot` sets a non-standard root, and create_user_ns() rejects any
# process whose fs root is not the mount-namespace root, so unshare(NEWUSER)
# returns EPERM and sandboxed browsers cannot start. The kernel's own root
# handoff (init/do_mounts.c prepare_namespace) instead moves the new root onto
# / and chroots into it; replaying that sequence -- cd into the target,
# `mount --move . /`, then `chroot .` -- makes the target the mount-namespace
# root again and keeps user namespaces working. Unlike pivot_root it needs no
# mount above the target, so it works from the initramfs rootfs and on the live
# overlay root as well as the installed ext4 root. It must run in PID 1 (no
# subshell) so the target init really becomes PID 1; a plain chroot remains as a
# fallback only if the move fails. The caller must have /dev, /proc and /sys
# already bound into /sysroot.
ribi_root_handoff() {
    _init="$1"
    if cd /sysroot && mount --move . /; then
        exec chroot . "$_init"
    fi
    cd /
    exec chroot /sysroot "$_init"
}

# Installed entries mount their UUID directly; only live entries scan for squashfs.
if grep -qw 'ribi.installed=1' /proc/cmdline 2>/dev/null; then
    ROOT_UUID=''
    for _arg in $(cat /proc/cmdline 2>/dev/null); do
        case "$_arg" in root=UUID=*) ROOT_UUID=${_arg#root=UUID=} ;; esac
    done
    ROOT_UUID=$(printf '%s' "$ROOT_UUID" | tr 'A-F' 'a-f')
    ROOT_DEV=''
    if [ -n "$ROOT_UUID" ]; then
        for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
            for dev in /dev/mmcblk*p[0-9] /dev/nvme*n*p[0-9] /dev/sd*[0-9] /dev/vd*[0-9] /dev/xvd*[0-9]; do
                [ -b "$dev" ] || continue
                _uuid=$(/sbin/blkid -s UUID -o value "$dev" 2>/dev/null | tr 'A-F' 'a-f')
                if [ "$_uuid" = "$ROOT_UUID" ]; then ROOT_DEV="$dev"; break 2; fi
            done
            draw_loader "Finding installed root" "$attempt"
            sleep 1
        done
    fi
    if [ -z "$ROOT_DEV" ]; then
        echo "[RIBI-INITRAMFS] installed root UUID not found: $ROOT_UUID" >/dev/ttyS0 2>/dev/null || true
        exec /bin/sh
    fi
    echo "[RIBI-INITRAMFS] installed root found: $ROOT_DEV UUID=$ROOT_UUID" >/dev/ttyS0 2>/dev/null || true
    if ! mount_with_timeout 20 mount -t ext4 -o rw "$ROOT_DEV" /sysroot; then
        echo "[RIBI-INITRAMFS] failed to mount installed root $ROOT_DEV" >/dev/ttyS0 2>/dev/null || true
        exec /bin/sh
    fi
    if [ ! -x /sysroot/sbin/ribi-init ] || [ ! -x /sysroot/bin/sh ]; then
        echo "[RIBI-INITRAMFS] installed root is missing ribi-init or /bin/sh" >/dev/ttyS0 2>/dev/null || true
        exec /bin/sh
    fi
    mkdir -p /sysroot/run /sysroot/dev/pts /sysroot/dev/shm /sysroot/proc /sysroot/sys /sysroot/tmp
    if ! mount_with_timeout 10 mount -t tmpfs -o mode=0755 tmpfs /sysroot/run; then exec /bin/sh; fi
    if ! mount_with_timeout 10 mount -t tmpfs -o mode=1777 tmpfs /sysroot/tmp; then exec /bin/sh; fi
    if ! mount_with_timeout 10 mount --bind /dev /sysroot/dev; then exec /bin/sh; fi
    if ! mount_with_timeout 10 mount --bind /dev/pts /sysroot/dev/pts; then exec /bin/sh; fi
    /bin/busybox rm -f /sysroot/dev/ptmx 2>/dev/null || true
    /bin/busybox ln -sf pts/ptmx /sysroot/dev/ptmx 2>/dev/null || true
    if ! mount_with_timeout 10 mount --bind /proc /sysroot/proc; then exec /bin/sh; fi
    if ! mount_with_timeout 10 mount --rbind /sys /sysroot/sys; then exec /bin/sh; fi
    echo "[RIBI-INITRAMFS] installed-root handoff" >/dev/ttyS0 2>/dev/null || true
    ribi_root_handoff /sbin/ribi-init
fi

draw_loader "Preparing live system" 1
sleep 2
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    for dev in /dev/sr* /dev/sd* /dev/nvme* /dev/vd* /dev/mmcblk* /dev/loop*; do
        [ -e "$dev" ] || continue
        mount -r -t iso9660 "$dev" /run/media 2>/dev/null ||         mount -r -t squashfs "$dev" /run/media 2>/dev/null ||         mount -r -t ext4 "$dev" /run/media 2>/dev/null ||         mount -r -t vfat "$dev" /run/media 2>/dev/null

        if [ -f /run/media/live/filesystem.squashfs ]; then
            FOUND="$dev"
            break 2
        fi
        umount /run/media 2>/dev/null
    done
    draw_loader "Searching for live media" 8
    sleep 2
done

if [ -z "$FOUND" ]; then
    echo "[!] CRITICAL: Could not locate live installation media (/live/filesystem.squashfs)."
    echo "[!] Spawning rescue recovery shell..."
    exec /bin/sh
fi

draw_loader "Loading Ribi OS" 18
sleep 2
if ! mount -t squashfs -o loop,ro /run/media/live/filesystem.squashfs /run/rootfs; then
    echo "[!] CRITICAL: Failed to mount live SquashFS root."
    exec /bin/sh
fi

# Establish Target Root: OverlayFS with Guaranteed Read-Only Fallback
draw_loader "Preparing target root" 20
sleep 2
BOOT_ROOT="/sysroot"
OVERLAY_MOUNTED=0

mkdir -p /run/overlay
PERSIST=0
case " $(cat /proc/cmdline 2>/dev/null) " in *" ribi.persistence=1 "*) PERSIST=1;; esac
# Persistence is hardware-neutral: ignore filenames, labels, bus type, and VM vendor.
# Try filesystem partitions first, then whole-disk filesystems (such as a direct
# ext4 QCOW2 attached by Vectras). Only ext4 is accepted because OverlayFS needs
# Linux whiteouts, xattrs, and reliable chmod/rename semantics.
for pd in /dev/mmcblk*p[0-9] /dev/nvme*n*p[0-9] /dev/sd*[0-9] /dev/vd*[0-9] /dev/xvd*[0-9] /dev/mmcblk[0-9] /dev/nvme[0-9]n[0-9] /dev/sd[a-z] /dev/vd[a-z] /dev/xvd[a-z]; do
    [ -b "$pd" ] || continue
    [ "$pd" = "$FOUND" ] && continue
    mkdir -p /run/persist
    # Persistent ISO flag enables scanning; the RAM ISO enables it after Setup
    # writes .ribi-persistence to the selected filesystem.
    if [ "$PERSIST" -eq 1 ]; then
        mount -t ext4 -o rw "$pd" /run/persist 2>/dev/null || continue
    else
        mount -t ext4 -o ro "$pd" /run/persist 2>/dev/null || continue
        [ -f /run/persist/.ribi-persistence ] || { umount /run/persist 2>/dev/null || true; continue; }
        umount /run/persist 2>/dev/null || true
        mount -t ext4 -o rw "$pd" /run/persist 2>/dev/null || continue
    fi
    mkdir -p /run/persist/upper /run/persist/work
    if mount -t overlay overlay -o lowerdir=/run/rootfs,upperdir=/run/persist/upper,workdir=/run/persist/work /sysroot 2>/dev/null; then
        OVERLAY_MOUNTED=1
        printf '[RIBI-PERSIST] using writable disk %s
' "$pd" >/dev/ttyS0 2>/dev/null || true
        break
    fi
    umount /run/persist 2>/dev/null || true
done
if ! mount -t tmpfs -o mode=0755 tmpfs /run/overlay; then
    echo "[!] FATAL: Cannot mount overlay working tmpfs."
    exec /bin/sh
fi
mkdir -p /run/overlay/upper /run/overlay/work

draw_loader "Preparing writable session" 22
sleep 2
if [ "$OVERLAY_MOUNTED" -eq 1 ]; then
    :
elif mount -t overlay overlay -o lowerdir=/run/rootfs,upperdir=/run/overlay/upper,workdir=/run/overlay/work /sysroot 2>/dev/null; then
    OVERLAY_MOUNTED=1
else
    if ! mount --bind /run/rootfs /sysroot; then
        echo "[!] FATAL: OverlayFS and read-only bind fallback both failed."
        exec /bin/sh
    fi
    fi
draw_loader "Configuring devices" 25
sleep 2

# Ensure /sysroot/run exists and mount tmpfs before binding rootfs and live media
mkdir -p /sysroot/run
mount -t tmpfs -o mode=0755 tmpfs /sysroot/run 2>/dev/null || true
mkdir -p /sysroot/run/rootfs
mount --bind /run/rootfs /sysroot/run/rootfs 2>/dev/null || true
mkdir -p /sysroot/run/media
mount --bind /run/media /sysroot/run/media 2>/dev/null || true

# Validate target rootfs BEFORE moving /dev/console away from initramfs
FAIL=0
if [ ! -d /sysroot ]; then
    echo "[!] FATAL: /sysroot directory missing"
    FAIL=1
fi
if ! grep -q " /sysroot " /proc/mounts 2>/dev/null; then
    echo "[!] FATAL: /sysroot is not an active mountpoint"
    FAIL=1
fi
if [ ! -x /sysroot/sbin/ribi-init ]; then
    echo "[!] FATAL: /sysroot/sbin/ribi-init missing or not executable"
    FAIL=1
fi
if [ ! -x /sysroot/bin/sh ]; then
    echo "[!] FATAL: /sysroot/bin/sh missing or not executable"
    FAIL=1
fi

if [ "$FAIL" -ne 0 ]; then
    echo "[!] Boot prerequisites unsatisfied. Spawning rescue shell in initramfs..."
    exec /bin/sh
fi

# Leave /dev, /proc, and /sys mounted in the initramfs until switch_root runs.
# BusyBox switch_root needs /proc/mounts to identify and detach the old root;
# moving /proc away first makes switch_root fail with "mount: no /proc/mounts".
mkdir -p /sysroot/dev /sysroot/proc /sysroot/sys /sysroot/tmp
mount -t tmpfs -o mode=1777 tmpfs /sysroot/tmp 2>/dev/null || true

draw_loader "Finalizing boot" 30
sleep 2
draw_loader "Starting console" 32
sleep 3
if [ -e /sysroot/etc/ribi/nogui ]; then
    echo "[DIAGNOSTICS] Running safe guest commands"
    echo '+ uname -a'; uname -a
    echo '+ id'; id
    echo '+ mount'; mount
    echo '+ ls /'; ls /sysroot
    echo '+ cat /etc/os-release'; cat /sysroot/etc/os-release
    echo "[DIAGNOSTICS] Complete"
fi
    printf '[RIBI-INITRAMFS] handoff sysroot=%s init=%s switch_root=%s
' "$(grep -q ' /sysroot ' /proc/mounts; echo $?)" "$(test -x /sysroot/sbin/ribi-init; echo $?)" "$(command -v switch_root 2>/dev/null || echo missing)" >/dev/ttyS0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] handoff begin
'
    # Bind the live kernel interfaces into the target, then use BusyBox chroot.
    # This keeps the target usable when switch_root rejects an overlay mount
    # while preserving the same /dev, /proc, /sys, and /run contract.
    mount_with_timeout() {
        _mt_seconds="$1"; shift
        "$@" >/dev/null 2>&1 &
        _mt_pid=$!
        _mt_elapsed=0
        while kill -0 "$_mt_pid" 2>/dev/null; do
            if [ "$_mt_elapsed" -ge "$_mt_seconds" ]; then
                kill "$_mt_pid" 2>/dev/null || true
                wait "$_mt_pid" 2>/dev/null || true
                return 124
            fi
            sleep 1
            _mt_elapsed=$((_mt_elapsed + 1))
        done
        wait "$_mt_pid"
    }
    printf '[RIBI-INITRAMFS] bind-dev-before
' >/dev/ttyS0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] bind-dev-before
' >/dev/tty0 2>/dev/null || true
    mount_with_timeout 5 mount --bind /dev /sysroot/dev 2>/dev/null; _rc=$?; printf '[RIBI-INITRAMFS] bind-dev-after rc=%s
' "$_rc" >/dev/ttyS0 2>/dev/null || true; printf '[RIBI-INITRAMFS] bind-dev-after rc=%s
' "$_rc" >/dev/tty0 2>/dev/null || true
    mkdir -p /sysroot/dev/pts
    mount_with_timeout 5 mount --bind /dev/pts /sysroot/dev/pts 2>/dev/null || true
    /bin/busybox rm -f /sysroot/dev/ptmx 2>/dev/null || true
    /bin/busybox ln -sf pts/ptmx /sysroot/dev/ptmx 2>/dev/null || true
    printf '[RIBI-INITRAMFS] bind-devpts
' >/dev/ttyS0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] bind-proc-before
' >/dev/ttyS0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] bind-proc-before
' >/dev/tty0 2>/dev/null || true
    mount_with_timeout 5 mount --bind /proc /sysroot/proc 2>/dev/null; _rc=$?; printf '[RIBI-INITRAMFS] bind-proc-after rc=%s
' "$_rc" >/dev/ttyS0 2>/dev/null || true; printf '[RIBI-INITRAMFS] bind-proc-after rc=%s
' "$_rc" >/dev/tty0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] bind-sys-before
' >/dev/ttyS0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] bind-sys-before
' >/dev/tty0 2>/dev/null || true
    mount_with_timeout 5 mount --rbind /sys /sysroot/sys 2>/dev/null; _rc=$?; printf '[RIBI-INITRAMFS] bind-sys-after rc=%s
' "$_rc" >/dev/ttyS0 2>/dev/null || true; printf '[RIBI-INITRAMFS] bind-sys-after rc=%s
' "$_rc" >/dev/tty0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] chroot handoff
' >/dev/ttyS0 2>/dev/null || true
    ribi_root_handoff /sbin/ribi-init
