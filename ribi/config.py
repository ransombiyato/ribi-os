"""Static configuration: OS identity, kernel spec, mirrors, workspace paths,
package seed sets, and host tool requirements.

This module is part of the Ribi OS ISO builder package. Split out of the
original monolithic builder for readability; behaviour is unchanged.
"""

import os
from pathlib import Path
from typing import List


OS_NAME = "Ribi OS"
OS_CODENAME = "bulbQT"
OS_VERSION = "1.0.0-PROD"
OS_ARCH = "x86_64"
OS_IDENTIFIER = "ribios"
DEFAULT_HOSTNAME = "ribios-pc"
DEFAULT_USER = "ribi"
RELEASE_PROFILE = "xfce-desktop"

# Kernel Specification
KERNEL_VERSION = "6.1.90"
KERNEL_MAJOR = "v6.x"
KERNEL_TARBALL = f"linux-{KERNEL_VERSION}.tar.xz"
KERNEL_URL = f"https://cdn.kernel.org/pub/linux/kernel/{KERNEL_MAJOR}/{KERNEL_TARBALL}"
KERNEL_FALLBACK_URLS = [
    f"https://mirrors.edge.kernel.org/pub/linux/kernel/{KERNEL_MAJOR}/{KERNEL_TARBALL}",
    f"https://kernel.googlesource.com/pub/scm/linux/kernel/git/stable/linux/+archive/refs/tags/v{KERNEL_VERSION}.tar.gz",
]

# Upstream Verified x86_64 Userspace Bootstrap & Package Repositories
ALPINE_MIRROR_BASE = "https://dl-cdn.alpinelinux.org/alpine/v3.24"
ALPINE_FALLBACK_BASE_URLS = [
    "https://uk.alpinelinux.org/alpine/v3.24",
    "https://mirrors.edge.kernel.org/alpine/v3.24",
    "https://mirror.yandex.ru/mirrors/alpine/v3.24",
]

ALPINE_MIRROR = f"{ALPINE_MIRROR_BASE}/main/x86_64"
ALPINE_MIRROR_COMMUNITY = f"{ALPINE_MIRROR_BASE}/community/x86_64"
BOOTSTRAP_X86_64_URL = f"{ALPINE_MIRROR_BASE}/releases/x86_64/alpine-minirootfs-3.24.1-x86_64.tar.gz"
BOOTSTRAP_X86_64_TARBALL = "x86_64-bootstrap-rootfs.tar.gz"
APKINDEX_URL = f"{ALPINE_MIRROR}/APKINDEX.tar.gz"
APKINDEX_COMMUNITY_URL = f"{ALPINE_MIRROR_COMMUNITY}/APKINDEX.tar.gz"
ZEN_BROWSER_URL = "https://github.com/zen-browser/desktop/releases/latest/download/zen.linux-x86_64.tar.xz"
ZEN_BROWSER_TARBALL = "zen.linux-x86_64.tar.xz"

# Storage Configuration
WORK_DIR = Path.cwd().resolve()
TERMUX_DOWNLOADS_PATH = Path("/data/data/com.termux/files/home/storage/downloads")

# Crucial: Workspace must reside on native Linux ext4 filesystem, NEVER on Android FUSE
STORAGE_ROOT = WORK_DIR / "ribi-build-workspace"
DIR_BUILD = STORAGE_ROOT / "build"
DIR_SRC = STORAGE_ROOT / "src"
DIR_CACHE = STORAGE_ROOT / "cache"
DIR_ROOTFS = STORAGE_ROOT / "rootfs"
DIR_INITRAMFS = STORAGE_ROOT / "initramfs"
DIR_X86_SYSROOT = DIR_BUILD / "x86_64_sysroot"
DIR_ISO = STORAGE_ROOT / "iso"
DIR_PKGS = STORAGE_ROOT / "packages"
DIR_APPS = STORAGE_ROOT / "apps"
DIR_LOGS = STORAGE_ROOT / "logs"

PERSISTENT_BUILD = os.environ.get("RIBI_PERSISTENT_BUILD", "0") == "1"
ISO_OUTPUT = WORK_DIR / (f"ribi-os-{OS_CODENAME}-{OS_ARCH}" + ("-persistent" if PERSISTENT_BUILD else "") + ".iso")
BUILD_LOG = DIR_LOGS / "builder.log"
_ASSET_DIR = Path(__file__).resolve().parent / "assets"
_BUNDLED_WALLPAPER = _ASSET_DIR / "wallpaper.png"
WALLPAPER_SOURCE = _BUNDLED_WALLPAPER if _BUNDLED_WALLPAPER.is_file() else Path("/home/ubuntu/upload/kite-wallpaper.png")
WALLPAPER_TARGET = Path("usr/share/backgrounds/ribi-wallpaper.png")

REQUIRED_HOST_COMMANDS = [
    "make",
    "mksquashfs",
    "xorriso",
    "rsync",
    "tar",
    "xz",
    "curl",
    "cpio",
    "mformat",
    "mcopy",
    "depmod",
    "grub-mkimage",
    "grub-mkrescue",
    "grub-mkstandalone",
    "openssl",
    "mkfs.vfat",
    "mmd",
    "mdir",
    "partprobe",
    "clang",
    "ld.lld",
    "llvm-ar",
    "llvm-nm",
    "strings",
    "convert",
]

# Checked separately (advisory only, not a hard preflight requirement): these are
# provided by the same 'grub-common'/'squashfs-tools' packages already installed
# above on apt/dnf/pacman/apk hosts, but Termux has no equivalent package, so a
# bare-Termux invocation must not be hard-blocked on them (stages 9-11 already
# raise clear, specific errors at the point of use if one is genuinely absent).
ADVISORY_HOST_COMMANDS: List[str] = []

ESSENTIAL_TARGET_BINARIES = [
    "busybox", "sh", "ash", "bash", "ls", "cp", "mv", "rm", "mkdir", "rmdir", "cat",
    "chmod", "chown", "grep", "sed", "awk", "find", "tar", "rsync", "gzip", "xz", "mount",
    "umount", "ps", "kill", "ip", "python3", "parted", "mkfs.ext4", "mkfs.vfat",
    "lsblk", "blkid", "sync", "reboot", "poweroff", "dmesg", "uname", "mknod",
    "chroot", "sleep", "dd", "echo", "tr", "true", "false", "hostname", "env",
    # depmod is required at boot (ribi-init and the initramfs /init both call it) to
    # build modules.dep/modules.alias from whatever the target kernel actually shipped
    # as loadable modules; without it modprobe can never resolve dependencies.
    "depmod",
    "acpid",
]

# Seed packages only. The real install set is the full transitive dependency
# closure of this list, computed at build time from the live Alpine APKINDEX.
# Ribi OS v1 is intentionally console-first: it has no X11, Wayland, desktop
# environment, display manager, or graphical browser. Usability comes from a
# TTY/serial shell, networking, Python, the native Ribi tools, and text games.
TARGET_APK_PACKAGES_BASE = [
    "python3",
    "libbz2",
    "libexpat",
    "libffi",
    "gdbm",
    "xz-libs",
    "mpdecimal",
    "parted",
    "rsync",
    "e2fsprogs",
    "e2fsprogs-libs",
    "util-linux",
    "lsblk",
    "blkid",
    "dosfstools",
    "grub",
    "grub-bios",
    "grub-efi",
    "device-mapper-libs",
    "eudev",
    "acpid",
    "dbus",
    "kmod",
    "sudo",
    "bash",
    "nano",
    "neovim",
    "ncurses-terminfo-base",
    "ncurses-terminfo",
    "kbd",
    "font-terminus",
]

# Networking
TARGET_APK_PACKAGES_NET = [
    "iproute2",
    "dhcpcd",
    "wpa_supplicant",
    "openresolv",
]

# Audio
TARGET_APK_PACKAGES_AUDIO = [
    "alsa-utils",
    "alsa-lib",
]

# Ribi desktop profile: Xorg/Mesa are borrowed foundations; the shell,
# session controller, wallpaper path, and core utilities are Ribi-owned.
TARGET_APK_PACKAGES_DESKTOP = [
    "openbox", "xterm", "xmessage", "feh", "xdotool", "xorg-server", "xauth", "dbus", "dbus-x11", "xinit", "xf86-video-vesa", "xf86-input-libinput", "xf86-input-evdev",
    "mesa-dri-gallium", "font-dejavu", "libxft", "python3", "py3-gobject3", "py3-cairo", "gtk+3.0", "gdk-pixbuf-loaders", "glycin-loaders-all", "glycin-image-rs", "xrandr",
    "picom", "lxterminal",
]
TARGET_APK_PACKAGES_APPS = ["gcompat", "obs-studio"]

# Full seed list used to compute the transitive install closure.
TARGET_APK_PACKAGES = (
    TARGET_APK_PACKAGES_BASE
    + TARGET_APK_PACKAGES_NET
    + TARGET_APK_PACKAGES_AUDIO
    + TARGET_APK_PACKAGES_DESKTOP
    + TARGET_APK_PACKAGES_APPS
)

FORBIDDEN_NO_DESKTOP_PACKAGES = {
    "xfce4", "xfce4-terminal", "xfce4-session", "xfce4-panel", "xfce4-settings",
    "xfdesktop", "lightdm", "lightdm-gtk-greeter", "xorg-server", "xwayland",
    "mesa-dri-gallium", "mesa-gl", "firefox", "firefox-esr", "geany",
}
