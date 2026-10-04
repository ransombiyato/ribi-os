#!/usr/bin/env python3
"""ribi-installer - the single Ribi OS setup and installation wizard.

One guided flow covers everything a first boot or an install needs, the way a
mainstream OS presents a single "Install / Set up" program instead of a setup
tool and a separate installer:

  1. system identity   - hostname and timezone
  2. networking        - interface, DHCP or manual, applied immediately
  3. storage           - erase and install to disk, prepare live persistence,
                         or keep the RAM-only live session

Safety is unchanged from the original disk installer: a target disk must be
unmounted, the running root is protected, the target must be re-typed, and a
destructive action requires an explicit YES.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

MOUNT_DIR = "/tmp/ribi_target"
LIVE_ROOT = "/run/rootfs"
SETUP_CONF = "/etc/ribi/setup.conf"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def sh(cmd):
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def run(cmd):
    return subprocess.run(cmd, check=True)


def ask(question, default=""):
    value = input(f"{question} [{default}]: ").strip()
    return value or default


def yes(question):
    return input(question + " Type YES to continue: ").strip() == "YES"


def choices(question, values, default):
    value = ask(question, default).lower()
    if value not in values:
        raise SystemExit(f"Invalid choice '{value}'. Choose one of: {', '.join(values)}")
    return value


def interfaces():
    try:
        return sorted(p.name for p in Path("/sys/class/net").iterdir() if p.name != "lo")
    except OSError:
        return []


def storage_rows():
    """Whole disks and partitions, as lsblk name/type/size/fstype/label/model."""
    try:
        out = subprocess.check_output(
            ["lsblk", "-rpo", "NAME,TYPE,SIZE,FSTYPE,LABEL,MODEL"], text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    rows = []
    for line in out.splitlines():
        fields = line.split(None, 5)
        if len(fields) >= 3 and fields[1] in ("disk", "part"):
            rows.append(fields)
    return rows


# --------------------------------------------------------------------------
# networking
# --------------------------------------------------------------------------

def configure_network():
    print("\n=== Networking ===")
    detected = interfaces()
    interface = ask(
        "Network interface (detected: " + (", ".join(detected) or "none") + ")",
        detected[0] if detected else "eth0",
    )
    ipv4 = choices("IPv4 mode (dhcp/manual)", {"dhcp", "manual"}, "dhcp")
    ipv4addr = gateway = dns = ""
    if ipv4 == "manual":
        ipv4addr = ask("IPv4 address/CIDR", "192.168.1.100/24")
        gateway = ask("Gateway", "192.168.1.1")
        dns = ask("DNS", "1.1.1.1")
    ipv6 = choices("IPv6 mode (auto/disabled/manual)", {"auto", "disabled", "manual"}, "auto")

    try:
        if ipv4 == "manual":
            run(["ip", "addr", "add", ipv4addr, "dev", interface])
            if gateway:
                run(["ip", "route", "add", "default", "via", gateway])
            if dns:
                Path("/etc/resolv.conf").write_text(f"nameserver {dns}\n")
        else:
            if shutil.which("dhcpcd"):
                run(["dhcpcd", "-q", interface])
            elif shutil.which("udhcpc"):
                run(["udhcpc", "-i", interface, "-n", "-q"])
        print(f"[+] Network configured on {interface}.")
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[!] Could not apply the network settings now: {exc}")
        print("    The settings are still saved and will be used on the next boot.")
    return {"interface": interface, "ipv4": ipv4, "ipv6": ipv6}


# --------------------------------------------------------------------------
# persistence (keep changes on a live USB)
# --------------------------------------------------------------------------

def prepare_persistence(device, conf):
    fstype = sh(["blkid", "-s", "TYPE", "-o", "value", device]).stdout.strip()
    if fstype != "ext4":
        print("Persistence requires an ext4 filesystem on the selected device.")
        print("Choose 'erase' to create one, or pick another device.")
        return 1
    if not yes(f"Prepare {device} for Ribi persistent storage?"):
        print("Persistence cancelled.")
        return 1
    mount = "/run/ribi-persistence-storage"
    Path(mount).mkdir(exist_ok=True)
    try:
        run(["mount", device, mount])
        Path(mount, "upper").mkdir(exist_ok=True)
        Path(mount, "work").mkdir(exist_ok=True)
        Path(mount, ".ribi-persistence").write_text("Ribi OS persistent live overlay\n")
        Path(mount, "setup.conf").write_text(_conf_text(conf))
    finally:
        subprocess.run(["umount", mount], check=False)
    print("\n[+] Persistence prepared. Reboot and choose Ribi OS normally;")
    print("    the next boot will discover this disk automatically.")
    return 0


# --------------------------------------------------------------------------
# disk installation
# --------------------------------------------------------------------------

def parts(device):
    out = subprocess.check_output(["lsblk", "-lnpo", "NAME,TYPE", device], text=True)
    return [line.split()[0] for line in out.splitlines()
            if len(line.split()) >= 2 and line.split()[1] == "part"]


def mountpoints_under(path):
    base = os.path.abspath(path).rstrip("/")
    try:
        with open("/proc/mounts", "r") as mounts_file:
            found = []
            for line in mounts_file:
                fields = line.split()
                if len(fields) >= 2 and (fields[1] == base or fields[1].startswith(base + "/")):
                    found.append(fields[1])
            return sorted(set(found), key=len, reverse=True)
    except OSError:
        return [base]


def cleanup(paths):
    for path in reversed(paths):
        for mountpoint in mountpoints_under(path):
            subprocess.run(["umount", mountpoint], check=False)


def has_mounts_below(path):
    return bool(mountpoints_under(path))


def preflight_target(device):
    rows = subprocess.run(
        ["lsblk", "-nrpo", "NAME,MOUNTPOINTS", device],
        capture_output=True, text=True, check=False,
    ).stdout.splitlines()
    mounted = []
    for row in rows:
        fields = row.split(None, 1)
        if len(fields) == 2 and fields[1].strip() not in ("", "-"):
            mounted.append(f"{fields[0]} -> {fields[1].strip()}")
    if mounted:
        raise SystemExit("Error: target disk or one of its partitions is mounted: " + ", ".join(mounted))
    try:
        with open("/proc/mounts", "r") as mounts_file:
            root_source = ""
            for line in mounts_file:
                fields = line.split()
                if len(fields) >= 2 and fields[1] == "/":
                    root_source = fields[0].split("[", 1)[0]
                    break
    except OSError as exc:
        raise SystemExit("Error: cannot read active root source; refusing to install.") from exc
    if not root_source:
        raise SystemExit("Error: cannot determine active root source; refusing to install.")
    if root_source.startswith("/dev/"):
        root_source = os.path.realpath(root_source)
    if root_source and (root_source == device or root_source.startswith(device + "p") or root_source.startswith(device)):
        raise SystemExit(f"Error: refusing to overwrite the active root device ({root_source}).")
    print("[safety] target and child partitions are not mounted; active root is protected.")


def dry_run_plan(device):
    if not device.startswith("/dev/") or device == "/dev/":
        raise SystemExit("Error: dry-run target must look like a device path under /dev/.")
    if os.path.exists(device):
        typ = sh(["lsblk", "-dn", "-o", "TYPE", device]).stdout.strip()
        if typ != "disk":
            raise SystemExit("Error: dry-run target exists but is not a whole disk.")
        preflight_target(device)
        print(f"Target detected: whole disk {device}; no mounts found")
    else:
        print("Target is not present in this environment; showing a static plan only.")
    uefi = os.path.isdir("/sys/firmware/efi")
    print("RIBI INSTALLER DRY-RUN - NO DISK WILL BE MODIFIED")
    print(f"Target placeholder: {device}")
    print(f"Firmware plan: {'GPT + UEFI' if uefi else 'MBR + BIOS'}")
    if uefi:
        print(f"  parted -s {device} mklabel gpt")
        print("  create 513 MiB FAT32 ESP and mark esp")
        print("  create remaining ext4 RibiRoot partition")
    else:
        print(f"  parted -s {device} mklabel msdos")
        print("  create full-disk ext4 RibiRoot partition and mark boot")
    print("  format filesystems only after interactive YES confirmation")
    print("  mount root, synchronize /run/rootfs, write fstab and machine-id")
    print("  install GRUB without NVRAM changes, write /boot/grub/grub.cfg")
    print("DRY-RUN COMPLETE - rerun without --dry-run only after reviewing the target disk.")
    return 0


def require_installer_tools():
    required = ("lsblk", "parted", "partprobe", "blockdev", "mkfs.ext4", "mkfs.vfat",
                "mount", "umount", "rsync", "blkid", "grub-install", "chroot", "udevadm")
    missing = [tool for tool in required if shutil.which(tool) is None]
    if missing:
        raise SystemExit("Error: installer tools missing: " + ", ".join(missing) + ". No disks were changed.")


def install_to_disk(device, conf):
    if os.geteuid() != 0:
        raise SystemExit("Error: Installation must be run as root.")
    if not os.path.isfile(LIVE_ROOT + "/sbin/ribi-init"):
        raise SystemExit("Error: Live rootfs not found at /run/rootfs")
    require_installer_tools()
    kernel = "/run/media/live/vmlinuz" if os.path.isfile("/run/media/live/vmlinuz") else "/live/vmlinuz"
    initrd = "/run/media/live/initrd.img" if os.path.isfile("/run/media/live/initrd.img") else "/live/initrd.img"
    if not os.path.isfile(kernel):
        raise SystemExit("FATAL: live kernel missing; no disks were changed.")
    if not device.startswith("/dev/") or not os.path.exists(device):
        raise SystemExit("Error: Device not found.")
    if sh(["lsblk", "-dn", "-o", "TYPE", device]).stdout.strip() != "disk":
        raise SystemExit("Error: Target must be a whole disk.")
    preflight_target(device)
    if input(f"Re-enter target device exactly ({device}) to confirm: ").strip() != device:
        raise SystemExit("Installation aborted: target confirmation did not match.")
    if input(f"WARNING: ALL DATA ON {device} WILL BE ERASED! Type 'YES': ").strip() != "YES":
        raise SystemExit("Installation aborted.")

    uefi = os.path.isdir("/sys/firmware/efi")
    print(f"[*] Partitioning {device} ({'GPT / UEFI' if uefi else 'MBR / BIOS'})...")
    if uefi:
        run(["parted", "-s", device, "mklabel", "gpt"])
        run(["parted", "-s", device, "mkpart", "ESP", "fat32", "1MiB", "513MiB"])
        run(["parted", "-s", device, "set", "1", "esp", "on"])
        run(["parted", "-s", device, "mkpart", "RibiRoot", "ext4", "513MiB", "100%"])
    else:
        run(["parted", "-s", device, "mklabel", "msdos"])
        run(["parted", "-s", device, "mkpart", "primary", "ext4", "1MiB", "100%"])
        run(["parted", "-s", device, "set", "1", "boot", "on"])
    probe = subprocess.run(["partprobe", device], capture_output=True, text=True)
    if probe.returncode != 0:
        reread = subprocess.run(["blockdev", "--rereadpt", device], capture_output=True, text=True)
        if reread.returncode != 0:
            print(f"WARNING: kernel did not immediately reread {device}; waiting for udev.")
    disk_parts = []
    for _ in range(20):
        disk_parts = parts(device)
        if (len(disk_parts) >= 2 if uefi else len(disk_parts) >= 1):
            break
        subprocess.run(["udevadm", "settle"], check=False)
        time.sleep(0.5)
    if (len(disk_parts) < 2 if uefi else len(disk_parts) < 1):
        raise SystemExit(f"Error: partition table did not appear for {device}")
    if uefi:
        esp, root = disk_parts[0], disk_parts[1]
    else:
        root = disk_parts[0]
        esp = None

    mounted = []
    try:
        if esp:
            run(["mkfs.vfat", "-F32", esp])
        run(["mkfs.ext4", "-F", "-L", "RibiRoot", root])
        os.makedirs(MOUNT_DIR, exist_ok=True)
        run(["mount", root, MOUNT_DIR])
        mounted.append(MOUNT_DIR)
        print("[*] Synchronizing clean Ribi OS rootfs...")
        run(["rsync", "-aH", "--delete", "--exclude=/run/*", "--exclude=/tmp/*",
             "--exclude=/proc/*", "--exclude=/sys/*", "--exclude=/dev/*",
             LIVE_ROOT + "/", MOUNT_DIR + "/"])
        os.makedirs(MOUNT_DIR + "/boot", exist_ok=True)
        if esp:
            os.makedirs(MOUNT_DIR + "/boot/efi", exist_ok=True)
            run(["mount", esp, MOUNT_DIR + "/boot/efi"])
            mounted.append(MOUNT_DIR + "/boot/efi")
        # The installed system gets its own fstab, machine-id, and the settings
        # collected earlier instead of live-media assumptions.
        os.makedirs(MOUNT_DIR + "/etc", exist_ok=True)
        Path(MOUNT_DIR + "/etc/hostname").write_text(conf["hostname"] + "\n")
        Path(MOUNT_DIR + "/etc/ribi").mkdir(parents=True, exist_ok=True)
        Path(MOUNT_DIR + SETUP_CONF).write_text(_conf_text(conf))
        uuid_root = sh(["blkid", "-s", "UUID", "-o", "value", root]).stdout.strip()
        lines = [f"UUID={uuid_root} / ext4 defaults 0 1"]
        if esp:
            uuid_esp = sh(["blkid", "-s", "UUID", "-o", "value", esp]).stdout.strip()
            lines.append(f"UUID={uuid_esp} /boot/efi vfat umask=0077 0 2")
        Path(MOUNT_DIR + "/etc/fstab").write_text("\n".join(lines) + "\n")
        Path(MOUNT_DIR + "/etc/machine-id").write_text("")
        shutil.copy2(kernel, MOUNT_DIR + "/boot/vmlinuz")
        os.chmod(MOUNT_DIR + "/boot/vmlinuz", 0o755)
        if os.path.isfile(initrd):
            shutil.copy2(initrd, MOUNT_DIR + "/boot/initrd.img")
        vfs = []
        try:
            for v in ("dev", "proc", "sys"):
                target = f"{MOUNT_DIR}/{v}"
                os.makedirs(target, exist_ok=True)
                run(["mount", "--rbind", "/" + v, target])
                vfs.append(target)
            if uefi:
                run(["chroot", MOUNT_DIR, "grub-install", "--target=x86_64-efi",
                     "--efi-directory=/boot/efi", "--bootloader-id=RibiOS",
                     "--no-nvram", "--removable", "--recheck"])
            else:
                run(["grub-install", f"--boot-directory={MOUNT_DIR}/boot",
                     "--target=i386-pc", "--recheck", device])
        finally:
            cleanup(vfs)
        os.makedirs(MOUNT_DIR + "/boot/grub", exist_ok=True)
        initrd_line = " ribi.installed=1 console=tty0 console=ttyS0,115200" + (
            "\n    initrd /boot/initrd.img" if os.path.isfile(MOUNT_DIR + "/boot/initrd.img") else ""
        )
        Path(MOUNT_DIR + "/boot/grub/grub.cfg").write_text(
            f'set default=0\nset timeout=5\nmenuentry "Ribi OS 1.0 (bulbQT)" {{\n'
            f"    linux /boot/vmlinuz root=UUID={uuid_root} rw init=/sbin/ribi-init{initrd_line}\n}}\n"
        )
        print("[+] Ribi OS installation successfully completed!")
        return 0
    finally:
        cleanup(mounted)
        if has_mounts_below(MOUNT_DIR):
            print("WARNING: target mounts remain; preserving the mounted tree to protect live devices.")
        else:
            shutil.rmtree(MOUNT_DIR, ignore_errors=True)


# --------------------------------------------------------------------------
# wizard
# --------------------------------------------------------------------------

def _conf_text(conf):
    return (
        f"hostname={conf['hostname']}\n"
        f"interface={conf['interface']}\n"
        f"ipv4={conf['ipv4']}\n"
        f"ipv6={conf['ipv6']}\n"
        f"timezone={conf['timezone']}\n"
    )


def _save_conf(conf):
    Path("/etc/ribi").mkdir(exist_ok=True)
    Path(SETUP_CONF).write_text(_conf_text(conf))


def wizard():
    if os.geteuid() != 0:
        raise SystemExit("Ribi OS setup must run as administrator.")
    print("\n=== Ribi OS Setup & Installer ===\n")
    print("One wizard sets up this system: identity, networking, and storage.")
    print("Nothing is erased unless you explicitly type YES.\n")

    hostname = ask("Hostname", "ribi")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}", hostname):
        raise SystemExit("Invalid hostname. Use letters, numbers, dots, and hyphens.")
    timezone = ask("Timezone", "UTC")

    net = configure_network()
    conf = {
        "hostname": hostname,
        "timezone": timezone,
        "interface": net["interface"],
        "ipv4": net["ipv4"],
        "ipv6": net["ipv6"],
    }
    _save_conf(conf)

    print("\n=== Storage ===")
    rows = storage_rows()
    for index, row in enumerate(rows):
        print(f"  {index}: " + " | ".join(row))
    selected = ""
    if rows:
        raw = ask("Enter device path or number (blank = keep RAM-only live session)", "")
        if raw.isdigit() and int(raw) < len(rows):
            selected = rows[int(raw)][0]
        else:
            selected = raw
    mode = choices(
        "Disk use (erase-install/persistence/ram-only)",
        {"erase-install", "persistence", "ram-only"},
        "erase-install" if selected else "ram-only",
    )

    if mode == "ram-only" or not selected:
        print("\n[+] Keeping RAM-only mode. Your settings were saved for this session.")
        return 0
    if not selected.startswith("/dev/") or not Path(selected).exists():
        raise SystemExit("Selected device does not exist.")
    typ = sh(["lsblk", "-dnpo", "TYPE", selected]).stdout.strip()
    if typ not in ("part", "disk"):
        raise SystemExit("Selected path is not a disk or partition.")
    print(f"\nSelected: {selected} ({typ})")

    if mode == "persistence":
        fstype = sh(["blkid", "-s", "TYPE", "-o", "value", selected]).stdout.strip()
        if fstype != "ext4":
            print("1) use an existing ext4 filesystem\n2) erase and create ext4 filesystem")
            action = ask("Storage action", "1")
            if action in ("2", "erase", "format"):
                if not yes(f"PERMANENTLY ERASE ALL DATA on {selected}?"):
                    raise SystemExit("Formatting cancelled.")
                subprocess.run(["mkfs.ext4", "-F", "-L", "RibiPersistence", selected], check=True)
        return prepare_persistence(selected, conf)

    # erase-install
    if typ != "disk":
        raise SystemExit("A full install needs a whole disk (not a partition).")
    return install_to_disk(selected, conf)


def main():
    if "--dry-run" in sys.argv:
        targets = [x for x in sys.argv[1:] if x != "--dry-run"]
        if len(targets) != 1:
            raise SystemExit("Usage: ribi-installer --dry-run /dev/<target-disk>")
        return dry_run_plan(targets[0])
    return wizard()


if __name__ == "__main__":
    raise SystemExit(main())
