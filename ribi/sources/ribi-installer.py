#!/usr/bin/env python3
"""ribi-installer - the single Ribi OS setup and installation wizard.

The questions and their wording follow Alpine's `setup-alpine` family so the
flow feels familiar to anyone who has installed Alpine. One guided pass covers
everything a first boot or a disk install needs:

  1. keyboard, hostname, network, DNS   (setup-keymap/hostname/interfaces/dns)
  2. root password, timezone, NTP, SSH  (setup-alpine/sshd/ntp)
  3. storage                            (setup-disk: sys / data / none)

Safety is unchanged from the original disk installer: a target disk must be
unmounted, the running root is protected, and a destructive action requires an
explicit yes (y/n).
"""

import json
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

# Alpine-style keyboard layouts shipped by the kbd package. The value is the
# console keymap (loadkmap) and the XKB layout name for X.
KEYMAP_LAYOUTS = {
    "us": ("us", "us"),
    "gb": ("gb", "gb"),
    "de": ("de", "de"),
    "fr": ("fr", "fr"),
    "es": ("es", "es"),
    "it": ("it", "it"),
    "pt": ("pt", "pt"),
    "br": ("br", "br"),
    "ru": ("ru", "ru"),
    "pl": ("pl", "pl"),
    "nl": ("nl", "nl"),
    "se": ("se", "se"),
    "no": ("no", "no"),
    "dk": ("dk", "dk"),
    "fi": ("fi-classic", "fi"),
    "tr": ("tr", "tr"),
    "cz": ("cz", "cz"),
    "hu": ("hu", "hu"),
    "ch": ("ch", "ch"),
    "ca": ("ca", "ca"),
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def sh(cmd):
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def run(cmd):
    return subprocess.run(cmd, check=True)


def ask(question, default=""):
    """Alpine `ask`: show the default in brackets only when there is one."""
    suffix = f" [{default}]" if default else ""
    value = input(f"{question}{suffix}: ").strip()
    return value or default


def ask_yesno(question, default="n"):
    """Alpine `ask_yesno`: accept y/yes or n/no, default on blank."""
    while True:
        value = input(f"{question} (y/n) ").strip().lower()
        if not value:
            value = default
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        print("Please answer 'y' or 'n'.")


def ask_pass(prompt, confirm_prompt="Retype password: "):
    """Prompt for a password twice (like `passwd`), returning None if skipped."""
    import getpass

    while True:
        first = getpass.getpass(prompt)
        if not first:
            return None
        second = getpass.getpass(confirm_prompt)
        if first != second:
            print("Passwords do not match. Please retry.")
            continue
        return first


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


def whole_disks():
    """Just the whole disks, like setup-disk offers (not partitions)."""
    return [row for row in storage_rows() if row[1] == "disk"]


def _disk_info(device):
    """Alpine's show_disk_info: size, label/model for the erase warning."""
    row = sh(["lsblk", "-dnro", "SIZE,LABEL,MODEL", device]).stdout.strip()
    return [part for part in row.split("  ") if part.strip()] or [row]


# --------------------------------------------------------------------------
# networking
# --------------------------------------------------------------------------

def configure_interfaces():
    """setup-interfaces: pick an interface and an addressing method."""
    print("\nAvailable interfaces are: " + (" ".join(interfaces()) or "none"))
    interface = ask("Which one do you want to initialize? (or 'done')",
                    interfaces()[0] if interfaces() else "eth0")
    ipv4 = "dhcp"
    ipv4addr = netmask = gateway = ""
    if interface and interface != "done":
        answer = ask(f"IPv4 address for {interface}? (or 'dhcp', 'none')", "dhcp").lower()
        if answer == "dhcp":
            ipv4 = "dhcp"
        elif answer == "none":
            ipv4 = "none"
        else:
            ipv4 = "manual"
            if "/" in answer:
                ipv4addr, netmask = answer.split("/", 1)
            else:
                ipv4addr = answer
                netmask = ask("Netmask?", "24")
            gateway = ask("Gateway? (or 'none')", "")
            if gateway == "none":
                gateway = ""
        answer = ask(f"IPv6 address for {interface}? (or 'dhcp', 'none')", "none").lower()
        ipv6 = "auto" if answer in ("dhcp", "auto") else "none"
    else:
        interface, ipv6 = "", "none"

    if interface and ipv4 == "dhcp":
        try:
            if shutil.which("dhcpcd"):
                run(["dhcpcd", "-q", interface])
            elif shutil.which("udhcpc"):
                run(["udhcpc", "-i", interface, "-n", "-q"])
            print(f"[+] Network configured on {interface}.")
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"[!] Could not apply the network settings now: {exc}")
    elif interface and ipv4 == "manual":
        try:
            address = f"{ipv4addr}/{netmask}"
            run(["ip", "addr", "add", address, "dev", interface])
            if gateway:
                run(["ip", "route", "add", "default", "via", gateway])
            print(f"[+] Network configured on {interface}.")
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"[!] Could not apply the network settings now: {exc}")
    return {"interface": interface, "ipv4": ipv4, "ipv4addr": ipv4addr,
            "netmask": netmask, "gateway": gateway, "ipv6": ipv6}


def configure_dns():
    """setup-dns: optional search domain and nameservers."""
    domain = ask("DNS domain name? (e.g 'bar.com')", "")
    nameservers = ask("DNS nameserver(s)?", "")
    if domain or nameservers:
        lines = []
        if domain:
            lines.append(f"search {domain}")
        for server in nameservers.split():
            lines.append(f"nameserver {server}")
        try:
            Path("/etc/resolv.conf").write_text("\n".join(lines) + "\n")
        except OSError as exc:
            print(f"[!] Could not write /etc/resolv.conf: {exc}")
    return {"dns_domain": domain, "dns_nameservers": nameservers}


# --------------------------------------------------------------------------
# persistence (keep changes on a live USB)
# --------------------------------------------------------------------------

def prepare_persistence(device, conf):
    """'data' mode: use the disk(s) for storage, not for the operating system.

    The system still runs from RAM; this only lays down the persistent overlay
    the live init discovers via the .ribi-persistence marker.
    """
    fstype = sh(["blkid", "-s", "TYPE", "-o", "value", device]).stdout.strip()
    if fstype != "ext4":
        print("Persistence needs an ext4 filesystem on the selected device.")
        if ask_yesno(f"Format {device} as ext4? ALL DATA WILL BE LOST", "n"):
            run(["mkfs.ext4", "-F", "-L", "RibiPersistence", device])
        else:
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
    print("\nWARNING: The following disk(s) will be erased:")
    print(f"  {device}  " + " | ".join(_disk_info(device)))
    if not ask_yesno("WARNING: Erase the above disk(s) and continue?", "n"):
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
        apply_identity(MOUNT_DIR, conf)
        apply_passwords(MOUNT_DIR, conf)
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
    keys = ("hostname", "interface", "ipv4", "ipv4addr", "netmask", "gateway", "ipv6",
            "dns_domain", "dns_nameservers", "timezone", "keymap", "xkb_layout",
            "ntp", "ssh", "root_ssh", "username")
    return "".join(f"{key}={conf.get(key, '')}\n" for key in keys)


def _save_conf(conf):
    Path("/etc/ribi").mkdir(exist_ok=True)
    Path(SETUP_CONF).write_text(_conf_text(conf))


def choose_keymap():
    """setup-keymap: console keymap and X layout from one layout name."""
    layouts = " ".join(sorted(KEYMAP_LAYOUTS))
    print("\nAvailable keyboard layouts are: " + layouts)
    layout = ask("Select keyboard layout:", "us").lower()
    while layout not in KEYMAP_LAYOUTS:
        print(f"'{layout}' is not a supported layout.")
        layout = ask("Select keyboard layout:", "us").lower()
    console, xkb = KEYMAP_LAYOUTS[layout]
    return {"keymap": console, "xkb_layout": xkb, "layout_name": layout}


def configure_ntp():
    """setup-ntp: busybox ntpd or none."""
    while True:
        client = ask("Which NTP client to run? ('busybox' or 'none')", "busybox").lower()
        if client in ("busybox", "none"):
            return client
        print(f"'{client}' is not a supported NTP client")


def configure_sshd():
    """setup-sshd: openssh (installed on demand) or none."""
    while True:
        server = ask("Which ssh server? ('openssh' or 'none')", "openssh").lower()
        if server in ("openssh", "none"):
            return server
        print(f"'{server}' is not a supported ssh server")


def configure_user():
    """setup-user: set a password for the desktop login name."""
    username = ask("Setup a user? (enter a lower-case loginname, or 'no')", "ribi").lower()
    if username in ("no", "none", ""):
        return {"username": "", "user_password": ""}
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", username):
        print("Invalid login name; skipping user setup.")
        return {"username": "", "user_password": ""}
    password = ask_pass(f"New password for {username}: ")
    return {"username": username, "user_password": password}


def configure_root_ssh(ssh):
    """setup-sshd: whether root may log in over ssh."""
    if ssh != "openssh":
        return "no"
    while True:
        answer = ask("Allow root ssh login? ('yes' or 'no')", "no").lower()
        if answer in ("yes", "no"):
            return answer
        print("Please answer 'yes' or 'no'.")


def _apply_xkb_layout(xorg_conf, layout):
    """Put XkbLayout into the keyboard InputClass of an existing xorg.conf.

    ribi-init starts X with `-config /etc/X11/xorg.conf`, which replaces the
    whole config search, so an xorg.conf.d snippet would be ignored.
    """
    conf = Path(xorg_conf)
    if not conf.is_file():
        return
    option = f'    Option "XkbLayout" "{layout}"\n'
    lines = conf.read_text(encoding="utf-8").splitlines(keepends=True)
    out, in_keyboard, inserted = [], False, False
    for line in lines:
        if line.lstrip().startswith('Section "InputClass"'):
            in_keyboard = False
        if "MatchIsKeyboard" in line:
            in_keyboard = True
        if in_keyboard and line.lstrip().startswith("EndSection") and not inserted:
            out.append(option)
            inserted = True
            in_keyboard = False
        out.append(line)
    if not inserted:
        out.append('\nSection "InputClass"\n    Identifier "system-keyboard"\n'
                   '    MatchIsKeyboard "on"\n' + option + 'EndSection\n')
    conf.write_text("".join(out), encoding="utf-8")


def apply_identity(target_root, conf):
    """Write identity, timezone, keymap and NTP settings into the target tree."""
    target = Path(target_root)
    (target / "etc").mkdir(parents=True, exist_ok=True)
    (target / "etc/hostname").write_text(conf["hostname"] + "\n")

    zone = target / "usr/share/zoneinfo" / conf["timezone"]
    if zone.is_file():
        localtime = target / "etc/localtime"
        if localtime.exists() or localtime.is_symlink():
            localtime.unlink()
        try:
            os.symlink(f"/usr/share/zoneinfo/{conf['timezone']}", localtime)
        except OSError:
            shutil.copy2(zone, localtime)
    (target / "etc/timezone").write_text(conf["timezone"] + "\n")

    (target / "etc/conf.d").mkdir(parents=True, exist_ok=True)
    (target / "etc/conf.d/keymaps").write_text(
        f'keymap="{conf["keymap"]}"\nwindowkeys="NO"\n')
    (target / "etc/X11/xorg.conf.d").mkdir(parents=True, exist_ok=True)
    (target / "etc/X11/xorg.conf.d/00-keyboard.conf").write_text(
        'Section "InputClass"\n'
        '    Identifier "system-keyboard"\n'
        '    MatchIsKeyboard "on"\n'
        f'    Option "XkbLayout" "{conf["xkb_layout"]}"\n'
        'EndSection\n')
    _apply_xkb_layout(target / "etc/X11/xorg.conf", conf["xkb_layout"])

    (target / "etc/ribi").mkdir(parents=True, exist_ok=True)
    (target / SETUP_CONF.lstrip("/")).write_text(_conf_text(conf))

    services = target / "etc/ribi/services"
    services.mkdir(parents=True, exist_ok=True)
    ntp_file = services / "ntp.json"
    if conf.get("ntp") == "busybox":
        ntp_file.write_text(json.dumps({
            "name": "ntp",
            "description": "BusyBox NTP client",
            "start_command": ["/usr/sbin/ntpd", "-n", "-p", "/run/ntpd.pid"],
            "enabled": True,
        }, indent=2))
    elif ntp_file.exists():
        ntp_file.unlink()

    sshd_file = services / "sshd.json"
    ssh_request = target / "etc/ribi/ssh.requested"
    if conf.get("ssh") == "openssh":
        # openssh is installed on first boot (apk is available in the image);
        # the service entry makes ribisvc start it once it exists.
        ssh_request.write_text("openssh\n")
        sshd_file.write_text(json.dumps({
            "name": "sshd",
            "description": "OpenSSH server",
            "start_command": ["/usr/sbin/sshd", "-D"],
            "enabled": True,
        }, indent=2))
        if conf.get("root_ssh") == "yes":
            sshd_config = target / "etc/ssh/sshd_config"
            if sshd_config.is_file():
                text = sshd_config.read_text(encoding="utf-8")
                if "PermitRootLogin" not in text:
                    sshd_config.write_text(text + "\nPermitRootLogin yes\n")
    else:
        for stale in (sshd_file, ssh_request):
            if stale.exists():
                stale.unlink()


def apply_passwords(target_root, conf):
    target = Path(target_root)
    if not (target / "etc/shadow").is_file():
        return
    entries = []
    if conf.get("root_password"):
        entries.append(f"root:{conf['root_password']}")
    user = conf.get("username")
    if user and conf.get("user_password"):
        entries.append(f"{user}:{conf['user_password']}")
    if not entries:
        return
    result = subprocess.run(
        ["chpasswd", "-R", str(target)],
        input="\n".join(entries) + "\n", text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        print(f"[!] Could not set the account password(s): {result.stderr.strip()}")


def apply_live_passwords(conf):
    """Set the root (and desktop user) password in the running live session."""
    if conf.get("root_password"):
        subprocess.run(["chpasswd"], input=f"root:{conf['root_password']}\n",
                       text=True, capture_output=True, check=False)
    if conf.get("username") and conf.get("user_password"):
        subprocess.run(["chpasswd"],
                       input=f"{conf['username']}:{conf['user_password']}\n",
                       text=True, capture_output=True, check=False)


def apply_live_session(conf):
    """Apply the identity settings to the running live session (best effort)."""
    subprocess.run(["hostname", conf["hostname"]], check=False)
    Path("/etc/hostname").write_text(conf["hostname"] + "\n")
    Path("/etc/timezone").write_text(conf["timezone"] + "\n")
    zone = Path("/usr/share/zoneinfo") / conf["timezone"]
    if zone.is_file():
        localtime = Path("/etc/localtime")
        if localtime.exists() or localtime.is_symlink():
            localtime.unlink()
        try:
            os.symlink(f"/usr/share/zoneinfo/{conf['timezone']}", localtime)
        except OSError:
            shutil.copy2(zone, localtime)
    Path("/etc/conf.d").mkdir(parents=True, exist_ok=True)
    Path("/etc/conf.d/keymaps").write_text(f'keymap="{conf["keymap"]}"\nwindowkeys="NO"\n')
    keymap = Path("/usr/share/keymaps") / conf["keymap"]
    if not keymap.exists():
        keymap = Path("/usr/share/keymaps/xkb") / f"{conf['keymap']}.map.gz"
    if keymap.exists() and shutil.which("loadkmap"):
        with open(keymap, "rb") as handle:
            subprocess.run(["loadkmap"], stdin=handle, check=False)
    _apply_xkb_layout("/etc/X11/xorg.conf", conf["xkb_layout"])


def _choose_disk(disks, prompt):
    """Alpine ask_disk: list disks, accept a number or /dev path."""
    while True:
        for index, row in enumerate(disks):
            print(f"  {index}: " + " | ".join(row))
        print()
        answer = ask(prompt, disks[0][0] if disks else "")
        if answer in ("none", "abort"):
            return ""
        if answer.isdigit() and int(answer) < len(disks):
            return disks[int(answer)][0]
        if answer.startswith("/dev/") and Path(answer).exists():
            return answer
        print(f"'{answer}' is not a listed disk.")


def wizard():
    if os.geteuid() != 0:
        raise SystemExit("Ribi OS setup must run as administrator.")
    print("\n=== Ribi OS Setup & Installer ===\n")
    print("One wizard sets up this system: keyboard, hostname, network, identity,")
    print("and storage. Questions follow the Alpine installer.\n")

    keymap = choose_keymap()
    hostname = ask("Enter system hostname (fully qualified form, e.g. 'foo.example.org')", "ribi")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}", hostname):
        raise SystemExit("Invalid hostname. Use letters, numbers, dots, and hyphens.")

    net = configure_interfaces()
    dns = configure_dns()

    print("\n=== Root Password ===")
    print("(leave blank to keep the account passwordless)")
    root_password = ask_pass("New password: ")

    user = configure_user()

    timezone = ask("Which timezone are you in? (or '?' or 'none')", "UTC")
    if timezone in ("none", "?"):
        timezone = "UTC"
    while not (Path("/usr/share/zoneinfo") / timezone).is_file():
        print(f"'{timezone}' is not a valid timezone on this system")
        timezone = ask("Which timezone are you in? (or '?' or 'none')", "UTC")
        if timezone in ("none", "?"):
            timezone = "UTC"
            break

    ntp = configure_ntp()
    ssh = configure_sshd()
    root_ssh = configure_root_ssh(ssh)

    conf = {
        "hostname": hostname,
        "timezone": timezone,
        "keymap": keymap["keymap"],
        "xkb_layout": keymap["xkb_layout"],
        "ntp": ntp,
        "ssh": ssh,
        "root_ssh": root_ssh,
        "root_password": root_password,
        **user,
        **{k: net[k] for k in ("interface", "ipv4", "ipv4addr", "netmask", "gateway", "ipv6")},
        **dns,
    }
    _save_conf(conf)
    apply_live_session(conf)
    apply_live_passwords(conf)

    print("\n=== Storage ===")
    disks = whole_disks()
    if not disks:
        print("\nNo disks available. Your settings were saved for this session.")
        return 0
    device = _choose_disk(disks, "Which disk(s) would you like to use? (or '?' for help or 'none')")
    if not device:
        print("\n[+] Running diskless. Your settings were saved for this session.")
        return 0

    print(f"\nThe following disk is selected:")
    print("  " + " | ".join([device] + _disk_info(device)))
    print()
    mode = ""
    while mode not in ("sys", "data", "none"):
        mode = ask("How would you like to use it? ('sys', 'data' or '?')", "?").lower()
        if mode == "?":
            print(
                "\n  sys:\n"
                "    This mode is a traditional disk install. On UEFI a FAT32 ESP and an\n"
                "    ext4 root partition are created; on BIOS one bootable ext4 partition.\n"
                "    This mode may be used for development boxes, desktops, virtual servers, etc.\n"
                "\n"
                "  data:\n"
                "    This mode uses your disk for data storage, not for the operating system.\n"
                "    The system itself will run from tmpfs (RAM).\n"
                "    Use this mode if you only want the disk for persistent live changes,\n"
                "    a mailspool, databases, logs, etc.\n")
            mode = ""
        elif mode not in ("sys", "data", "none"):
            print(f"'{mode}' is not a valid mode")

    if mode == "none":
        print("\n[+] Running diskless. Your settings were saved for this session.")
        return 0

    if not device.startswith("/dev/") or not Path(device).exists():
        raise SystemExit("Selected device does not exist.")

    if mode == "data":
        return prepare_persistence(device, conf)

    if sh(["lsblk", "-dn", "-o", "TYPE", device]).stdout.strip() != "disk":
        raise SystemExit("A full install needs a whole disk (not a partition).")
    return install_to_disk(device, conf)


def main():
    if "--dry-run" in sys.argv:
        targets = [x for x in sys.argv[1:] if x != "--dry-run"]
        if len(targets) != 1:
            raise SystemExit("Usage: ribi-installer --dry-run /dev/<target-disk>")
        return dry_run_plan(targets[0])
    return wizard()


if __name__ == "__main__":
    raise SystemExit(main())
