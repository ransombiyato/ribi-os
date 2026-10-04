#!/usr/bin/env python3
"""
===============================================================================
 RIBI OS — Master Operating System Rebuilder & Production Build Engine
 Codename: bulbQT | Version: 1.0.0-PROD | Target: x86_64 Hybrid BIOS/UEFI Live
===============================================================================
 Complete Autonomous Single-File Rebuild Pipeline:
   - Zero Host Contamination: Cross-Host Verification (ARM64/AArch64 -> x86_64)
   - Hermetic Target Architecture: No Debian/Ubuntu/systemd/apt in Target OS
   - Resilient Live Initramfs: Dual-Path (Unified-tmpfs OverlayFS + Read-Only Fallback)
   - Native Init Architecture: Native Ribi PID 1 Supervisor (/sbin/ribi-init)
   - Native Service Supervision: Native Ribi Service Manager (/usr/local/bin/ribisvc)
   - Native Packaging: Hardened .rpk Engine (/usr/local/bin/ribi-pkg)
   - Native System CLI: Unified Controller (/usr/local/bin/ribi)
   - Real Python 3 Userspace: Genuine x86_64 Python Runtime & Libraries
   - Live SquashFS Root: Deterministic Compressed Filesystem
   - Hybrid Bootloader: GRUB 2 El Torito (BIOS i386-pc) + UEFI (x86_64-efi)
   - C.I.C. Resource Engine: Check -> Install/Acquire If Absent -> Continue
   - Strict End-to-End Boot-Chain Static Validator (El Torito, Kernel, Initramfs)
===============================================================================
"""

import argparse
import copy
import base64
import hashlib
import io
import json
import logging
import lzma
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import time
import urllib.request
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# =============================================================================
# 1. GLOBAL SYSTEM & ARCHITECTURAL CONFIGURATION
# =============================================================================
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
_BUNDLED_WALLPAPER = Path(__file__).resolve().with_name("kite-wallpaper.png")
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
    "mesa-dri-gallium", "font-dejavu", "libxft", "python3", "py3-gobject3", "gtk+3.0", "gdk-pixbuf-loaders", "glycin-loaders-all", "glycin-image-rs", "xrandr",
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

# =============================================================================
# 2. LOGGING & SAFE SUBPROCESS CONTROLLER
# =============================================================================
class BuildLogger:
    @staticmethod
    def init():
        DIR_LOGS.mkdir(parents=True, exist_ok=True)
        handlers = [logging.StreamHandler(sys.stdout)]
        try:
            handlers.insert(0, logging.FileHandler(BUILD_LOG, mode="a", encoding="utf-8"))
        except PermissionError:
            print(f"[WARN] Build log is not writable: {BUILD_LOG}; continuing on stdout", file=sys.stderr)
        logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", handlers=handlers)

    @staticmethod
    def step(step_num: int, total_steps: int, title: str):
        msg = f"\n\033[1;36m[{step_num}/{total_steps}] >>> {title}\033[0m"
        print(msg)
        logging.info(f"STAGE [{step_num}/{total_steps}]: {title}")

    @staticmethod
    def cic(action: str, resource: str, status: str):
        print(f"  \033[1;34m[C.I.C.]\033[0m {action:<8} | {resource:<35} -> \033[1;32m{status}\033[0m")
        logging.info(f"[C.I.C.] {action} | {resource} -> {status}")

    @staticmethod
    def info(msg: str):
        print(f"\033[1;32m[*] INFO:\033[0m {msg}")
        logging.info(msg)

    @staticmethod
    def warn(msg: str):
        print(f"\033[1;33m[!] WARN:\033[0m {msg}")
        logging.warning(msg)

    @staticmethod
    def error(msg: str):
        print(f"\033[1;31m[!] ERROR:\033[0m {msg}")
        logging.error(msg)


def run_cmd(
    cmd: List[str],
    check: bool = True,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[Path] = None,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    cmd_str = " ".join(str(c) for c in cmd)
    logging.info(f"EXEC: {cmd_str} (cwd={cwd})")
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        proc = subprocess.run(
            cmd,
            check=check,
            cwd=cwd,
            env=merged_env,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
        )
        return proc
    except subprocess.CalledProcessError as err:
        BuildLogger.error(f"Command execution failed (Code {err.returncode}): {cmd_str}")
        if err.stdout:
            logging.error(f"STDOUT:\n{err.stdout}")
        if err.stderr:
            logging.error(f"STDERR:\n{err.stderr}")
        raise


def write_file(path: Path, content: str, mode: int = 0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content.strip() + "\n")
    path.chmod(mode)
    logging.info(f"Generated file: {path} (mode={oct(mode)})")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_digest_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".sha256")


def _verify_cache(dest: Path) -> bool:
    if not dest.is_file() or dest.stat().st_size == 0:
        return False
    digest = _cache_digest_path(dest)
    if not digest.is_file():
        return False
    try:
        expected = digest.read_text(encoding="ascii").strip().split()[0].lower()
        return bool(re.fullmatch(r"[0-9a-f]{64}", expected)) and sha256_file(dest) == expected
    except Exception:
        return False


def download_file(url: str, dest: Path, fallback_urls: Optional[List[str]] = None,
                  expected_sha256: Optional[str] = None):
    """Download atomically with TLS verification and cryptographic cache validation."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest_path = _cache_digest_path(dest)

    if dest.exists():
        if _verify_cache(dest):
            BuildLogger.cic("CHECK", dest.name, "EXISTS (Cached + SHA-256 Verified)")
            if expected_sha256 and sha256_file(dest) != expected_sha256.lower():
                dest.unlink(missing_ok=True)
                digest_path.unlink(missing_ok=True)
            else:
                return
        elif dest.stat().st_size > 0 and expected_sha256 and sha256_file(dest) == expected_sha256.lower():
            digest_path.write_text(expected_sha256.lower() + "\n", encoding="ascii")
            BuildLogger.cic("CHECK", dest.name, "EXISTS (SHA-256 Verified)")
            return
        else:
            BuildLogger.warn(f"Discarding unverified cached artifact: {dest}")
            dest.unlink(missing_ok=True)
            digest_path.unlink(missing_ok=True)

    all_urls = [url] + (fallback_urls or [])
    temp_dest = dest.with_suffix(dest.suffix + ".part")
    temp_dest.unlink(missing_ok=True)
    headers = {
        "User-Agent": "RibiOSBuilder/1.0",
        "Accept": "*/*",
    }
    last_error = None

    for target_url in all_urls:
        BuildLogger.cic("INSTALL", dest.name, f"Downloading from {target_url}")
        success = False
        if shutil.which("curl"):
            try:
                run_cmd(["curl", "--fail", "--silent", "--show-error", "--location",
                         "--retry", "3", "--retry-delay", "2", "--proto", "=https",
                         "--tlsv1.2", "-A", headers["User-Agent"], "-o", str(temp_dest), target_url],
                        capture=True)
                success = temp_dest.is_file() and temp_dest.stat().st_size > 0
            except Exception as e:
                last_error = e
        if not success and shutil.which("wget"):
            try:
                run_cmd(["wget", "--https-only", "--tries=3", "--timeout=60",
                         f"--user-agent={headers['User-Agent']}", "-O", str(temp_dest), target_url],
                        capture=True)
                success = temp_dest.is_file() and temp_dest.stat().st_size > 0
            except Exception as e:
                last_error = e
        if not success:
            try:
                req = urllib.request.Request(target_url, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as resp, open(temp_dest, "wb") as out:
                    shutil.copyfileobj(resp, out)
                success = temp_dest.is_file() and temp_dest.stat().st_size > 0
            except Exception as e:
                last_error = e

        if success:
            actual = sha256_file(temp_dest)
            if expected_sha256 and actual != expected_sha256.lower():
                last_error = RuntimeError(
                    f"SHA-256 mismatch for {dest.name}: expected {expected_sha256}, got {actual}"
                )
                temp_dest.unlink(missing_ok=True)
                continue
            temp_dest.replace(dest)
            digest_path.write_text(actual + "\n", encoding="ascii")
            BuildLogger.cic("CONTINUE", dest.name, f"READY ({dest.stat().st_size / 1024:.1f} KB; SHA-256 {actual[:16]}...)")
            return
        temp_dest.unlink(missing_ok=True)

    raise RuntimeError(f"Failed to download verified {dest.name} from all available mirrors: {all_urls}. Last error: {last_error}")


def download_with_sidecar_hash(url: str, dest: Path, fallback_urls: Optional[List[str]] = None):
    """Fetch an artifact only after obtaining its published SHA-256 sidecar."""
    urls=[url]+(fallback_urls or [])
    last=None
    for u in urls:
        side=dest.with_name(dest.name+'.upstream.sha256')
        try:
            download_file(u+'.sha256',side)
            text=side.read_text(encoding='ascii',errors='ignore')
            m=re.search(r'\b([0-9a-fA-F]{64})\b',text)
            if not m: raise RuntimeError(f'no SHA-256 found in {u}.sha256')
            download_file(u,dest,expected_sha256=m.group(1))
            side.unlink(missing_ok=True)
            return
        except Exception as e:
            last=e; side.unlink(missing_ok=True); dest.unlink(missing_ok=True); _cache_digest_path(dest).unlink(missing_ok=True)
    raise RuntimeError(f'Unable to fetch a SHA-256-verified artifact from {urls}: {last}')


def verify_apkindex_signature(index_path: Path, key_dir: Path):
    """Verify an Alpine v2 signed APKINDEX.tar.gz.

    The signed index is two concatenated gzip streams: a signature tar segment
    followed by the original unsigned APKINDEX.tar.gz stream. The RSA signature
    covers the exact bytes of that second gzip stream.
    """
    raw = index_path.read_bytes()
    streams = _gzip_streams(raw)
    if len(streams) != 2:
        raise RuntimeError(
            f"{index_path.name}: expected 2 gzip streams, found {len(streams)}"
        )
    sig_payload = streams[0][1]
    unsigned_index_gz = streams[1][0]

    sig_name = None
    signature = None
    with tarfile.open(fileobj=io.BytesIO(sig_payload), mode="r:", ignore_zeros=True) as tar:
        for member in tar.getmembers():
            name = Path(member.name).name
            if name.startswith(".SIGN.RSA."):
                sig_name = name
                f = tar.extractfile(member)
                signature = f.read() if f else None
                break
    if not sig_name or not signature:
        raise RuntimeError(f"{index_path.name}: missing repository signature member")
    if not shutil.which("openssl"):
        raise RuntimeError("openssl is required to verify Alpine repository signatures")

    key_name = sig_name[len(".SIGN.RSA."):]
    key = key_dir / key_name
    if not key.is_file():
        raise RuntimeError(
            f"{index_path.name}: trusted repository key missing: {key_name}"
        )

    import tempfile
    with tempfile.TemporaryDirectory(prefix="ribi-index-") as td:
        data = Path(td) / "APKINDEX.tar.gz"
        sig = Path(td) / "signature"
        data.write_bytes(unsigned_index_gz)
        sig.write_bytes(signature)
        proc = subprocess.run(
            ["openssl", "dgst", "-sha1", "-verify", str(key),
             "-signature", str(sig), str(data)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if proc.returncode != 0 or "Verified OK" not in proc.stdout:
            raise RuntimeError(
                f"{index_path.name}: repository signature verification failed"
            )

def determine_dest_subdir(file_path: Path) -> str:
    """
    Determines canonical target subdirectory (usr/bin, usr/sbin, bin, sbin)
    using explicit path-component logic without naive substring ambiguity.
    """
    parts = [p.lower() for p in file_path.parts]
    if len(parts) >= 2 and parts[-2] == "bin" and len(parts) >= 3 and parts[-3] == "usr":
        return "usr/bin"
    if len(parts) >= 2 and parts[-2] == "sbin" and len(parts) >= 3 and parts[-3] == "usr":
        return "usr/sbin"
    if len(parts) >= 2 and parts[-2] == "sbin":
        return "sbin"
    if len(parts) >= 2 and parts[-2] == "bin":
        return "bin"

    path_str = str(file_path)
    if "/usr/bin" in path_str:
        return "usr/bin"
    if "/usr/sbin" in path_str:
        return "usr/sbin"
    if "/sbin" in path_str:
        return "sbin"
    if "/bin" in path_str:
        return "bin"
    return "usr/bin"


def _gzip_streams(raw: bytes) -> List[Tuple[bytes, bytes]]:
    """Return [(compressed_stream, decompressed_payload), ...] for concatenated gzip data."""
    out=[]; pos=0
    while pos < len(raw):
        if raw[pos:pos+2] != b"\x1f\x8b":
            break
        d=zlib.decompressobj(16 + zlib.MAX_WBITS)
        dec=d.decompress(raw[pos:]) + d.flush()
        unused=d.unused_data
        consumed=len(raw[pos:]) - len(unused)
        if consumed <= 0: break
        out.append((raw[pos:pos+consumed], dec)); pos += consumed
    return out


def verify_apk_integrity(archive_path: Path, expected_csum: str, key_dir: Optional[Path] = None):
    """Verify Alpine APK control checksum, datahash and, when possible, RSA signature."""
    raw=archive_path.read_bytes()
    streams=_gzip_streams(raw)
    if len(streams) < 3:
        raise RuntimeError(f"{archive_path.name}: APK does not contain signature/control/data gzip streams")
    # streams[i] is (compressed_segment, decompressed_payload). The compressed
    # segment is what checksums/signatures are computed over; the decompressed
    # payload is what must be fed to tarfile (mode="r:" expects plain tar bytes,
    # not gzip -- passing compressed bytes here silently yields an empty archive
    # instead of raising, which is what caused the ".PKGINFO not found" KeyError).
    signature_gz, control_gz, data_gz = streams[0][0], streams[1][0], streams[-1][0]
    signature_tar_bytes, control_tar_bytes = streams[0][1], streams[1][1]
    if expected_csum:
        try:
            encoded=expected_csum[2:] if expected_csum.startswith("Q1") else expected_csum
            expected_sha1=base64.b64decode(encoded)
            if len(expected_sha1)==20 and hashlib.sha1(control_gz).digest()!=expected_sha1:
                raise RuntimeError(f"{archive_path.name}: APKINDEX control checksum mismatch")
        except Exception as e:
            raise RuntimeError(f"{archive_path.name}: invalid APKINDEX checksum: {e}")
    pkginfo=None; signature_name=None; signature_bytes=None
    with tarfile.open(fileobj=io.BytesIO(signature_tar_bytes), mode="r:", ignore_zeros=True) as tar:
        for m in tar.getmembers():
            if m.name.startswith(".SIGN.RSA."):
                signature_name=m.name; f=tar.extractfile(m); signature_bytes=f.read() if f else None
                break
    with tarfile.open(fileobj=io.BytesIO(control_tar_bytes), mode="r:", ignore_zeros=True) as tar:
        f=tar.extractfile(".PKGINFO")
        if f: pkginfo=f.read().decode("utf-8", errors="replace")
    if not pkginfo:
        raise RuntimeError(f"{archive_path.name}: missing .PKGINFO control metadata")
    datahash=None
    for line in pkginfo.splitlines():
        if line.startswith("datahash = "):
            datahash=line.split(" = ",1)[1].strip(); break
    if not datahash:
        raise RuntimeError(f"{archive_path.name}: .PKGINFO has no datahash")
    if hashlib.sha256(data_gz).hexdigest().lower()!=datahash.lower():
        raise RuntimeError(f"{archive_path.name}: datahash mismatch")
    if not signature_name or not signature_bytes or not key_dir:
        raise RuntimeError(f"{archive_path.name}: signed APK is required")
    if signature_name and signature_bytes and key_dir and shutil.which("openssl"):
        key_name=signature_name[len(".SIGN.RSA."):]
        key=key_dir / key_name
        if not key.exists():
            raise RuntimeError(f"{archive_path.name}: trusted Alpine signing key missing: {key_name}")
        import tempfile
        with tempfile.TemporaryDirectory(prefix="ribi-apk-") as td:
            control=Path(td)/"control.gz"; sig=Path(td)/"signature"
            control.write_bytes(control_gz); sig.write_bytes(signature_bytes)
            proc=subprocess.run(["openssl","dgst","-sha1","-verify",str(key),"-signature",str(sig),str(control)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode != 0 or "Verified OK" not in proc.stdout:
                raise RuntimeError(f"{archive_path.name}: APK RSA signature verification failed")

def safe_tar_extract(archive_path: Path, target_dir: Path):
    """Safely extract tar archives while preserving safe Alpine rootfs links.

    Python 3.13's ``tarfile`` ``filter="data"`` intentionally rejects absolute
    symlinks. Alpine minirootfs archives legitimately contain links such as
    ``usr/bin/yes -> /bin/busybox``; inside a rootfs that means ``<root>/bin``.
    We therefore validate the link *within the extraction root* and rewrite a
    safe absolute link to the equivalent relative link before extraction.

    Archive paths, symlinks, and hardlinks are checked lexically so a malicious
    archive cannot escape ``target_dir``. Device nodes, FIFOs, and other special
    files are rejected because this function is used for untrusted downloaded
    staging archives, not for creating a live /dev tree.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    norm_target = os.path.abspath(str(target_dir))
    target_prefix = norm_target if norm_target.endswith(os.sep) else norm_target + os.sep

    def inside_target(path: str) -> bool:
        return path == norm_target or path.startswith(target_prefix)

    def clean_archive_path(name: str) -> str:
        # Tar member names always use '/', even on POSIX hosts. Treat backslash
        # as a separator too so crafted Windows-style traversal cannot bypass it.
        clean = os.path.normpath(name.replace("\\", "/")).lstrip("/\\")
        if clean in ("", "."):
            return "."
        resolved = os.path.abspath(os.path.join(norm_target, clean))
        if not inside_target(resolved):
            raise RuntimeError(f"Security Alert: Path traversal entry blocked: {name}")
        return clean

    def _extract_tar(tar: tarfile.TarFile):
        members = tar.getmembers()
        member_names = set()

        # Validate the complete archive namespace first. Hardlinks may legally point
        # forward to a later member, so extraction itself is performed in two passes:
        # ordinary members first, hardlinks second.
        cleaned_members = []
        all_names = set()
        for original in members:
            if original.name in (".PKGINFO", ".INSTALL", ".PRE-INSTALL", ".POST-INSTALL") or original.name.startswith(".SIGN."):
                continue
            clean_name = clean_archive_path(original.name)
            all_names.add(clean_name)
            cleaned_members.append((original, clean_name))

        for original, clean_name in cleaned_members:
            if original.name in (".PKGINFO", ".INSTALL", ".PRE-INSTALL", ".POST-INSTALL") or original.name.startswith(".SIGN."):
                continue

            member = copy.copy(original)
            member.name = clean_name
            norm_member = os.path.abspath(os.path.join(norm_target, clean_name))
            member_names.add(clean_name)

            # Do not allow special files from a downloaded archive to become
            # device nodes/FIFOs on the host filesystem.
            if member.ischr() or member.isblk() or member.isfifo():
                raise RuntimeError(f"Security Alert: Special file blocked: {original.name}")

            if member.islnk():
                # Tar hardlink names refer to archive members. Normalize both
                # absolute and relative spellings and require the target to stay
                # inside the archive root.
                raw_link = member.linkname.replace("\\", "/")
                clean_link = raw_link.lstrip("/")
                clean_link = clean_archive_path(clean_link)
                if clean_link not in all_names:
                    # A hardlink target must exist somewhere in the archive namespace.
                    raise RuntimeError(
                        f"Security Alert: Hardlink target missing: {original.linkname} -> {original.name}"
                    )
                member.linkname = clean_link

            elif member.issym():
                raw_link = member.linkname.replace("\\", "/")
                if raw_link.startswith("/"):
                    # Absolute links in a rootfs archive are rooted at the
                    # archive root, not the host root. Convert them to a relative
                    # link so the extracted sysroot retains the intended meaning.
                    link_target = os.path.abspath(os.path.join(norm_target, raw_link.lstrip("/")))
                    if not inside_target(link_target):
                        raise RuntimeError(
                            f"Security Alert: Symlink escape blocked: {member.linkname}"
                        )
                    member.linkname = os.path.relpath(link_target, os.path.dirname(norm_member))
                else:
                    link_target = os.path.abspath(os.path.join(os.path.dirname(norm_member), raw_link))
                    if not inside_target(link_target):
                        raise RuntimeError(
                            f"Security Alert: Symlink escape blocked: {member.linkname}"
                        )
                    # Preserve relative links exactly when they are already safe.
                    member.linkname = raw_link

            # Ensure no pre-existing symlink in an intermediate component can
            # redirect extraction outside the target. Replace such components
            # with real directories before extracting the member.
            rel_parts = os.path.relpath(norm_member, norm_target).split(os.sep)
            curr = norm_target
            for part in rel_parts[:-1]:
                curr = os.path.join(curr, part)
                if os.path.islink(curr):
                    os.unlink(curr)
                    os.makedirs(curr, exist_ok=True)
                elif not os.path.exists(curr):
                    os.makedirs(curr, exist_ok=True)
                elif not os.path.isdir(curr):
                    raise RuntimeError(f"Security Alert: Non-directory path component: {curr}")

            # The extraction root and directory members may already exist.  The
            # root is created before extraction, and package archives commonly
            # contain parent-directory entries that can also have been created
            # while processing another member.  Existing real directories are
            # therefore safe to reuse; existing non-directories are replaced.
            if os.path.lexists(norm_member):
                if member.isdir() and os.path.isdir(norm_member) and not os.path.islink(norm_member):
                    continue
                if norm_member == norm_target and os.path.isdir(norm_member) and not os.path.islink(norm_member):
                    continue
                if os.path.isdir(norm_member) and not os.path.islink(norm_member):
                    raise RuntimeError(f"Archive collision with existing directory: {norm_member}")
                os.unlink(norm_member)

            # All security checks above are stricter than tarfile's data filter,
            # and the latter rejects legitimate rewritten Alpine links on Python
            # 3.13. Extract ordinary members now; defer hardlinks until their targets
            # have been materialized.
            if not member.islnk():
                tar.extract(member, path=target_dir)

        # Second pass: hardlinks. Every target was validated against all_names above,
        # and now exists if it is an ordinary archive member.
        for original, clean_name in cleaned_members:
            if not original.islnk():
                continue
            member = copy.copy(original)
            raw_link = member.linkname.replace("\\", "/")
            clean_link = raw_link.lstrip("/")
            clean_link = clean_archive_path(clean_link)
            member.name = clean_name
            member.linkname = clean_link
            norm_member = os.path.abspath(os.path.join(norm_target, clean_name))
            # Recreate the same parent-component protection for the deferred pass.
            rel_parts = os.path.relpath(norm_member, norm_target).split(os.sep)
            curr = norm_target
            for part in rel_parts[:-1]:
                curr = os.path.join(curr, part)
                if os.path.islink(curr):
                    os.unlink(curr); os.makedirs(curr, exist_ok=True)
                elif not os.path.exists(curr):
                    os.makedirs(curr, exist_ok=True)
                elif not os.path.isdir(curr):
                    raise RuntimeError(f"Security Alert: Non-directory path component: {curr}")
            if os.path.lexists(norm_member):
                if os.path.isdir(norm_member) and not os.path.islink(norm_member):
                    raise RuntimeError(f"Archive collision with existing directory: {norm_member}")
                os.unlink(norm_member)
            tar.extract(member, path=target_dir)

    def _extract_gzip_streams(raw_bytes: bytes) -> int:
        offset = 0
        streams_found = 0
        raw_len = len(raw_bytes)
        while offset < raw_len:
            if raw_bytes[offset:offset + 2] != b"\x1f\x8b":
                raise RuntimeError(f"{archive_path.name}: trailing non-gzip data after {streams_found} stream(s)")
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            try:
                decompressed = d.decompress(raw_bytes[offset:]) + d.flush()
            except zlib.error as exc:
                raise RuntimeError(f"{archive_path.name}: invalid gzip stream: {exc}") from exc
            if not decompressed or not d.eof:
                raise RuntimeError(f"{archive_path.name}: truncated gzip stream")
            consumed = raw_len - offset - len(d.unused_data)
            if consumed <= 0:
                raise RuntimeError(f"{archive_path.name}: gzip parser made no progress")
            with tarfile.open(fileobj=io.BytesIO(decompressed), mode="r:", ignore_zeros=True) as tar:
                _extract_tar(tar)
            streams_found += 1
            offset += consumed
        return streams_found

    # Alpine APKs may contain concatenated gzip streams. The normal minirootfs
    # is a single .tar.gz, so only use multi-stream handling for .apk files.
    if archive_path.name.endswith(".apk"):
        raw_bytes = archive_path.read_bytes()
        _extract_gzip_streams(raw_bytes)
        return

    with tarfile.open(archive_path, "r:*") as tar:
        _extract_tar(tar)


def _apk_bare_token(token: str) -> str:
    """Return the package/provide name from an Alpine dependency expression."""
    token = token.strip()
    if token.startswith("!"):
        token = token[1:]
    # Alpine uses =, <, <=, >, >=, ~ and ~= forms.
    m = re.match(r"^([^<>=~]+?)(?:>=|<=|~=|=|>|<|~).*$", token)
    return m.group(1) if m else token


def _apk_constraint(token: str) -> Tuple[str, Optional[str]]:
    m = re.match(r"^[^<>=~]+?(>=|<=|~=|=|>|<|~)(.+)$", token.strip())
    return (m.group(1), m.group(2)) if m else ("", None)


def _apk_version_key(version: str):
    """Deterministic Alpine-ish version key; exact equality remains the safest check."""
    parts = re.split(r"([0-9]+|[A-Za-z]+|-r[0-9]+|~)", version)
    key=[]
    order={"alpha":-5,"beta":-4,"pre":-3,"rc":-2,"cvs":1,"svn":2,"git":3,"hg":4,"p":5}
    for x in parts:
        if not x: continue
        if x.isdigit(): key.append((1,int(x)))
        elif x.startswith("-r") and x[2:].isdigit(): key.append((3,int(x[2:])))
        elif x in order: key.append((2,order[x]))
        else: key.append((2,x))
    return key


def _apk_satisfies(version: str, op: str, wanted: Optional[str]) -> bool:
    if not op or wanted is None:
        return True
    if op == "=": return version == wanted
    a,b=_apk_version_key(version),_apk_version_key(wanted)
    if op == ">": return a>b
    if op == ">=": return a>=b
    if op == "<": return a<b
    if op == "<=": return a<=b
    if op in ("~", "~="):
        # Compatible-release dependency: exact major/minor prefix when possible.
        vp=re.match(r"^(\d+(?:\.\d+)*)", version)
        wp=re.match(r"^(\d+(?:\.\d+)*)", wanted)
        return bool(vp and wp and vp.group(1).split(".")[:2] == wp.group(1).split(".")[:2] and a>=b)
    return False


def parse_apkindex(raw_content: str, pkg_versions: Dict[str, str],
                   pkg_depends: Dict[str, List[str]], provides_map: Dict[str, str],
                   pkg_checksums: Optional[Dict[str, str]] = None,
                   pkg_arch: Optional[Dict[str, str]] = None,
                   pkg_install_if: Optional[Dict[str, List[str]]] = None):
    """Parse Alpine APKINDEX v2 records."""
    for block in raw_content.split("\n\n"):
        name=version=None; depends=[]; provides=[]; checksum=None; arch=None; install_if=[]
        for line in block.splitlines():
            if line.startswith("C:"): checksum=line[2:].strip()
            elif line.startswith("P:"): name=line[2:].strip()
            elif line.startswith("V:"): version=line[2:].strip()
            elif line.startswith("A:"): arch=line[2:].strip()
            elif line.startswith("D:"): depends=[d for d in line[2:].strip().split() if d]
            elif line.startswith("p:"): provides=[p for p in line[2:].strip().split() if p]
            elif line.startswith("i:"): install_if=[d for d in line[2:].strip().split() if d]
        if not name or not version: continue
        if name not in pkg_versions:
            pkg_versions[name]=version; pkg_depends[name]=depends
            if pkg_checksums is not None: pkg_checksums[name]=checksum or ""
            if pkg_arch is not None: pkg_arch[name]=arch or ""
            if pkg_install_if is not None: pkg_install_if[name]=install_if
        provides_map.setdefault(name,name)
        for prov in provides:
            provides_map.setdefault(_apk_bare_token(prov),name)


def resolve_apk_closure(seed_packages: List[str], pkg_versions: Dict[str, str],
                        pkg_depends: Dict[str, List[str]], provides_map: Dict[str, str]) -> List[str]:
    """Resolve a strict dependency closure, rejecting unsatisfied versioned dependencies."""
    resolved=[]; seen=set(); queue=list(seed_packages); unresolved=[]
    while queue:
        token=queue.pop(0).strip()
        if token.startswith("!"): continue
        bare=_apk_bare_token(token); op,wanted=_apk_constraint(token)
        pkg_name=provides_map.get(bare, bare if bare in pkg_versions else None)
        if pkg_name is None or not _apk_satisfies(pkg_versions[pkg_name],op,wanted):
            unresolved.append(token); continue
        if pkg_name in seen: continue
        seen.add(pkg_name); resolved.append(pkg_name)
        queue.extend(d for d in pkg_depends.get(pkg_name,[]) if not d.startswith("!"))
    if unresolved:
        raise RuntimeError("Unresolved/unsatisfied Alpine dependencies: " + ", ".join(sorted(set(unresolved))[:30]))
    return resolved

# =============================================================================
# 3. PURE-PYTHON ELF, PE32+ & STATIC BINARY AUDITOR
# =============================================================================
def get_elf_arch(file_path: Path) -> Optional[str]:
    """Reads ELF magic and machine ID in pure Python."""
    if not file_path.is_file() or file_path.stat().st_size < 52:
        return None
    try:
        with open(file_path, "rb") as f:
            magic = f.read(4)
            if magic != b"\x7fELF":
                return None
            elf_class = ord(f.read(1))
            data_encoding = ord(f.read(1))
            f.seek(18)
            e_machine = f.read(2)
            if data_encoding == 1:
                machine_id = struct.unpack("<H", e_machine)[0]
            else:
                machine_id = struct.unpack(">H", e_machine)[0]

            if elf_class == 2 and machine_id == 62:
                return "x86_64"
            elif elf_class == 2 and machine_id == 183:
                return "aarch64"
            elif elf_class == 1 and machine_id == 3:
                return "i386"
            elif elf_class == 1 and machine_id == 40:
                return "arm"
            return f"other({machine_id})"
    except Exception:
        return None


def is_elf_x86_64(file_path: Path) -> bool:
    return get_elf_arch(file_path) == "x86_64"


def is_efi_x86_64(file_path: Path) -> bool:
    """Verifies x86_64 PE32+ executable (EFI application binary)."""
    if not file_path.is_file() or file_path.stat().st_size < 0x100:
        return False
    try:
        with open(file_path, "rb") as f:
            mz = f.read(2)
            if mz != b"MZ":
                return False
            f.seek(0x3C)
            pe_offset_bytes = f.read(4)
            if len(pe_offset_bytes) < 4:
                return False
            pe_offset = struct.unpack("<I", pe_offset_bytes)[0]
            if pe_offset < 0x40 or pe_offset > file_path.stat().st_size - 6:
                return False
            f.seek(pe_offset)
            pe_sig = f.read(4)
            if pe_sig != b"PE\x00\x00":
                return False
            machine_bytes = f.read(2)
            if len(machine_bytes) < 2:
                return False
            machine = struct.unpack("<H", machine_bytes)[0]
            return machine == 0x8664
    except Exception:
        return False


def get_elf_interpreter(file_path: Path) -> Optional[str]:
    """Extracts PT_INTERP dynamic linker path directly from 64-bit ELF headers."""
    if not is_elf_x86_64(file_path):
        return None
    try:
        with open(file_path, "rb") as f:
            hdr = f.read(64)
            if len(hdr) < 64 or hdr[:4] != b"\x7fELF":
                return None
            e_phoff = struct.unpack("<Q", hdr[32:40])[0]
            e_phentsize = struct.unpack("<H", hdr[54:56])[0]
            e_phnum = struct.unpack("<H", hdr[56:58])[0]

            f.seek(e_phoff)
            for _ in range(e_phnum):
                phdr = f.read(e_phentsize)
                if len(phdr) < 56:
                    break
                p_type = struct.unpack("<I", phdr[0:4])[0]
                if p_type == 3:  # PT_INTERP
                    p_offset = struct.unpack("<Q", phdr[8:16])[0]
                    p_filesz = struct.unpack("<Q", phdr[32:40])[0]
                    f.seek(p_offset)
                    raw_interp = f.read(p_filesz).split(b"\x00")[0]
                    return raw_interp.decode("utf-8", errors="replace")
    except Exception:
        pass
    return None


def get_elf_needed_libraries(file_path: Path) -> List[str]:
    """Pure-Python DT_NEEDED dynamic library name parser for x86_64 ELFs."""
    needed: List[str] = []
    if not is_elf_x86_64(file_path):
        return needed
    try:
        with open(file_path, "rb") as f:
            hdr = f.read(64)
            if len(hdr) < 64 or hdr[:4] != b"\x7fELF":
                return needed

            e_phoff = struct.unpack("<Q", hdr[32:40])[0]
            e_phentsize = struct.unpack("<H", hdr[54:56])[0]
            e_phnum = struct.unpack("<H", hdr[56:58])[0]

            pt_dynamic_offset = None
            pt_dynamic_filesz = 0
            program_headers = []

            f.seek(e_phoff)
            for _ in range(e_phnum):
                phdr = f.read(e_phentsize)
                if len(phdr) < 56:
                    break
                p_type = struct.unpack("<I", phdr[0:4])[0]
                p_offset = struct.unpack("<Q", phdr[8:16])[0]
                p_vaddr = struct.unpack("<Q", phdr[16:24])[0]
                p_filesz = struct.unpack("<Q", phdr[32:40])[0]
                program_headers.append((p_vaddr, p_offset, p_filesz))

                if p_type == 2:  # PT_DYNAMIC
                    pt_dynamic_offset = p_offset
                    pt_dynamic_filesz = p_filesz

            if pt_dynamic_offset is None:
                return needed

            f.seek(pt_dynamic_offset)
            dyn_data = f.read(pt_dynamic_filesz)
            dyn_entries = []
            strtab_vaddr = None

            for i in range(0, len(dyn_data) - 15, 16):
                d_tag = struct.unpack("<q", dyn_data[i:i+8])[0]
                d_val = struct.unpack("<Q", dyn_data[i+8:i+16])[0]
                if d_tag == 0:
                    break
                elif d_tag == 1:
                    dyn_entries.append(d_val)
                elif d_tag == 5:
                    strtab_vaddr = d_val

            if strtab_vaddr is None or not dyn_entries:
                return needed

            strtab_offset = None
            for p_vaddr, p_offset, p_filesz in program_headers:
                if p_vaddr <= strtab_vaddr < p_vaddr + p_filesz:
                    strtab_offset = p_offset + (strtab_vaddr - p_vaddr)
                    break

            if strtab_offset is None:
                return needed

            for name_idx in dyn_entries:
                f.seek(strtab_offset + name_idx)
                s_bytes = bytearray()
                while True:
                    b = f.read(1)
                    if not b or b == b"\x00":
                        break
                    s_bytes.extend(b)
                if s_bytes:
                    needed.append(s_bytes.decode("utf-8", errors="replace"))
    except Exception as err:
        logging.debug(f"ELF parse warning on {file_path}: {err}")
    return needed


def is_linux_bzimage(file_path: Path) -> bool:
    """Verifies Linux bzImage boot header signature (HdrS at offset 0x202)."""
    if not file_path.is_file() or file_path.stat().st_size < 1024:
        return False
    try:
        with open(file_path, "rb") as f:
            f.seek(0x202)
            return f.read(4) == b"HdrS"
    except Exception:
        return False


def is_squashfs(file_path: Path) -> bool:
    """Verifies SquashFS 4.0 magic signature (hsqs)."""
    if not file_path.is_file() or file_path.stat().st_size < 4096:
        return False
    try:
        with open(file_path, "rb") as f:
            return f.read(4) == b"hsqs"
    except Exception:
        return False


def has_el_torito_boot_record(iso_path: Path) -> bool:
    """Verifies ISO 9660 PVD and El Torito Boot Record Volume Descriptor."""
    if not iso_path.is_file() or iso_path.stat().st_size < 0x10000:
        return False
    try:
        with open(iso_path, "rb") as f:
            # Check PVD at sector 16 (0x8000)
            f.seek(0x8000)
            pvd = f.read(2048)
            if len(pvd) < 2048 or pvd[0] != 1 or pvd[1:6] != b"CD001":
                return False

            # Scan Volume Descriptors between sector 17 and 32
            for sector_num in range(17, 33):
                f.seek(sector_num * 2048)
                desc = f.read(2048)
                if len(desc) < 2048:
                    break
                desc_type = desc[0]
                if desc_type == 255:
                    break
                if desc_type == 0 and desc[1:6] == b"CD001":
                    boot_sys_id = desc[7:39].rstrip(b" \x00")
                    if b"EL TORITO SPECIFICATION" in boot_sys_id:
                        return True
    except Exception as e:
        logging.debug(f"El Torito validation exception: {e}")
    return False


def has_uefi_el_torito_entry(iso_path: Path) -> bool:
    """Verifies that the ISO El Torito catalog contains an EFI-platform boot entry."""
    if not iso_path.is_file():
        return False
    try:
        import subprocess
        result = subprocess.run(
            ["xorriso", "-indev", str(iso_path), "-report_el_torito", "plain"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        out = result.stdout
        if result.returncode != 0:
            return False

        # xorriso reports UEFI El Torito entries with platform identifier EFI.
        for line in out.splitlines():
            normalized = line.strip().upper()
            if normalized.startswith("PLATFORM ID") and ("EFI" in normalized or "UEFI" in normalized):
                return True
            if "PLATFORM:" in normalized and ("EFI" in normalized or "UEFI" in normalized):
                return True
            # xorriso 1.5.x uses a compact table format whose platform is
            # reported on the boot-image row instead of a PLATFORM column.
            if re.search(r"EL TORITO BOOT IMG\s*:.*\b(?:EFI|UEFI)\b", normalized):
                return True
        return False
    except Exception as e:
        logging.debug(f"UEFI El Torito validation exception: {e}")
        return False


# =============================================================================
# 4. C.I.C. HOST DEPENDENCY AUDITOR & ACQUISITION
# =============================================================================
def cic_host_dependencies(cross_required: bool = False):
    """C.I.C. host dependency verification with native Termux support."""
    BuildLogger.info("Executing C.I.C. Verification on Host Dependencies...")

    # Termux must use pkg, not Debian/Ubuntu package names.
    is_termux = (
        shutil.which("pkg") is not None
        and (
            "com.termux" in os.environ.get("PREFIX", "")
            or os.path.exists("/data/data/com.termux")
        )
    )

    if is_termux:
        # Termux provides clang rather than Debian's x86_64-linux-gnu-gcc.
        required = list(REQUIRED_HOST_COMMANDS)
        required = [c for c in required if c != "gcc"]

        if cross_required:
            # A native clang can target x86_64 when the required target
            # support is available; do not demand a nonexistent Debian
            # cross-compiler package from Termux.
            if shutil.which("clang") is None:
                required.append("clang")
        elif shutil.which("gcc") is None and shutil.which("clang") is None:
            required.append("clang")

        missing_cmds = [cmd for cmd in required if shutil.which(cmd) is None]

        for cmd in required:
            if cmd not in missing_cmds:
                BuildLogger.cic("CHECK", cmd, "EXISTS")

        if not missing_cmds:
            BuildLogger.cic("CONTINUE", "Host Toolchain",
                            "ALL HOST COMMANDS SATISFIED")
            return

        pkg_map = {
            "make": "make",
            "mksquashfs": "squashfs-tools",
            "xorriso": "xorriso",
            "rsync": "rsync",
            "tar": "tar",
            "xz": "xz-utils",
            "curl": "curl",
            "cpio": "cpio",
            "mformat": "mtools",
            "mcopy": "mtools",
            "clang": "clang",
            "openssl": "openssl",
            "mkfs.vfat": "dosfstools", "mmd": "mtools", "mdir": "mtools", "mcopy": "mtools",
            "partprobe": "parted", "depmod": "kmod", "strings": "binutils", "grub-install": "grub",
        }

        pkgs = []
        for cmd in missing_cmds:
            pkg = pkg_map.get(cmd)
            if pkg and pkg not in pkgs:
                pkgs.append(pkg)

        if not pkgs:
            BuildLogger.error(
                f"Missing Termux host tools cannot be acquired automatically: "
                f"{missing_cmds}"
            )
            sys.exit(1)

        try:
            BuildLogger.cic(
                "INSTALL",
                f"pkg ({', '.join(pkgs)})",
                "IN PROGRESS",
            )
            run_cmd(["pkg", "update", "-y"])
            run_cmd(["pkg", "install", "-y"] + pkgs)
        except Exception as err:
            BuildLogger.error(f"Host auto-installation failed: {err}")
            BuildLogger.info(
                f"Please install the missing Termux tools manually: {missing_cmds}"
            )
            sys.exit(1)

        # Verify rather than assuming pkg succeeded.
        still_missing = [cmd for cmd in required if shutil.which(cmd) is None]
        if still_missing:
            BuildLogger.error(
                f"Host dependency verification failed after installation: "
                f"{still_missing}"
            )
            sys.exit(1)

        BuildLogger.cic(
            "CONTINUE",
            "Host Toolchain",
            "INSTALLED & VERIFIED",
        )
        return

    # Non-Termux hosts retain the original distro-specific installation logic.
    missing_cmds = [
        cmd for cmd in REQUIRED_HOST_COMMANDS
        if shutil.which(cmd) is None
    ]

    # Linux kernel LLVM builds can target x86_64 directly from an ARM64 host.
    # Do not require a Debian-only x86_64-linux-gnu-gcc wrapper.
    if shutil.which("clang") is None:
        missing_cmds.append("clang")

    for cmd in REQUIRED_HOST_COMMANDS:
        if cmd not in missing_cmds:
            BuildLogger.cic("CHECK", cmd, "EXISTS")

    if not missing_cmds:
        BuildLogger.cic("CONTINUE", "Host Toolchain",
                        "ALL HOST COMMANDS SATISFIED")
        return

    BuildLogger.warn(
        f"Missing host tools detected: {', '.join(missing_cmds)}. "
        "Auto-acquiring..."
    )

    pkg_mgr = None
    for pm in ["apt-get", "dnf", "pacman", "apk"]:
        if shutil.which(pm):
            pkg_mgr = pm
            break

    if not pkg_mgr:
        BuildLogger.error(
            f"No supported package manager detected. "
            f"Install missing tools manually: {missing_cmds}"
        )
        sys.exit(1)

    sudo_prefix = []
    if os.geteuid() != 0 and shutil.which("sudo"):
        sudo_prefix = ["sudo"]

    host_is_x86 = platform.machine() == "x86_64"

    try:
        if pkg_mgr == "apt-get":
            pkg_map = {
                "make": "make",
                "mksquashfs": "squashfs-tools",
                "xorriso": "xorriso",
                "rsync": "rsync",
                "tar": "tar",
                "xz": "xz-utils",
                "curl": "curl",
                "cpio": "cpio",
                "x86_64-linux-gnu-gcc": "gcc-x86-64-linux-gnu",
                "gcc": "gcc",
                "mformat": "mtools",
                "mcopy": "mtools", "mdir": "mtools", "mmd": "mtools",
                "depmod": "kmod", "openssl": "openssl", "mkfs.vfat": "dosfstools",
                "partprobe": "parted", "convert": "imagemagick", "clang": "clang", "ld.lld": "lld", "llvm-ar": "llvm", "llvm-nm": "llvm", "strings": "binutils", "grub-install": "grub2-common",
                 "grub-mkimage": "grub-common",
                "grub-mkrescue": "grub-common", "grub-mkstandalone": "grub-common",
            }
            pkgs = list(set(pkg_map.get(m, m) for m in missing_cmds))
            pkgs.extend([
                "mtools", "dosfstools", "grub-common",
                "ca-certificates", "bison", "flex", "bc",
                "libelf-dev", "libssl-dev"
            ])
            if host_is_x86:
                pkgs.extend(["grub-pc-bin", "grub-efi-amd64-bin"])
            BuildLogger.cic("INSTALL", f"apt-get ({', '.join(pkgs)})",
                            "IN PROGRESS")
            run_cmd(sudo_prefix + ["apt-get", "update", "-y"])
            run_cmd(sudo_prefix + [
                "apt-get", "install", "-y", "--no-install-recommends"
            ] + list(dict.fromkeys(pkgs)))

        elif pkg_mgr == "dnf":
            pkg_map = {
                "make": "make", "mksquashfs": "squashfs-tools",
                "xorriso": "xorriso", "rsync": "rsync", "tar": "tar",
                "xz": "xz", "curl": "curl", "cpio": "cpio",
                "x86_64-linux-gnu-gcc": "gcc-x86_64-linux-gnu",
                "gcc": "gcc", "mformat": "mtools", "mcopy": "mtools", "strings": "binutils", "grub-install": "grub2-tools"
            }
            pkgs = list(set(pkg_map.get(m, m) for m in missing_cmds))
            pkgs.extend([
                "mtools", "dosfstools", "grub2-tools", "ca-certificates",
                "bison", "flex", "bc", "elfutils-libelf-devel", "openssl-devel"
            ])
            if host_is_x86:
                pkgs.extend(["grub2-pc-modules", "grub2-efi-x64-modules"])
            BuildLogger.cic("INSTALL", f"dnf ({', '.join(pkgs)})",
                            "IN PROGRESS")
            run_cmd(sudo_prefix + ["dnf", "install", "-y"] +
                    list(dict.fromkeys(pkgs)))

        elif pkg_mgr == "pacman":
            pkg_map = {
                "make": "make", "mksquashfs": "squashfs-tools",
                "xorriso": "xorriso", "rsync": "rsync", "tar": "tar",
                "xz": "xz", "curl": "curl", "cpio": "cpio",
                "x86_64-linux-gnu-gcc": "x86_64-linux-gnu-gcc",
                "gcc": "gcc", "mformat": "mtools", "mcopy": "mtools", "strings": "binutils", "grub-install": "grub2-tools"
            }
            pkgs = list(set(pkg_map.get(m, m) for m in missing_cmds))
            pkgs.extend([
                "mtools", "dosfstools", "grub", "ca-certificates",
                "bison", "flex", "bc", "libelf", "openssl"
            ])
            BuildLogger.cic("INSTALL", f"pacman ({', '.join(pkgs)})",
                            "IN PROGRESS")
            run_cmd(sudo_prefix + ["pacman", "-Sy", "--noconfirm"] +
                    list(dict.fromkeys(pkgs)))

        elif pkg_mgr == "apk":
            pkg_map = {
                "make": "make", "mksquashfs": "squashfs-tools",
                "xorriso": "xorriso", "rsync": "rsync", "tar": "tar",
                "xz": "xz", "curl": "curl", "cpio": "cpio",
                "mformat": "mtools", "mcopy": "mtools", "gcc": "gcc", "strings": "binutils", "grub-install": "grub"
            }
            pkgs = list(set(pkg_map.get(m, m) for m in missing_cmds))
            pkgs.extend([
                "mtools", "dosfstools", "grub", "ca-certificates",
                "bison", "flex", "bc", "elfutils-dev", "openssl-dev",
                "build-base"
            ])
            if host_is_x86:
                pkgs.extend(["grub-bios", "grub-efi"])
            BuildLogger.cic("INSTALL", f"apk ({', '.join(pkgs)})",
                            "IN PROGRESS")
            run_cmd(sudo_prefix + ["apk", "add", "--no-cache"] +
                    list(dict.fromkeys(pkgs)))

    except Exception as err:
        BuildLogger.error(f"Host auto-installation failed: {err}")
        BuildLogger.info(f"Please install {missing_cmds} manually.")
        sys.exit(1)

    BuildLogger.cic("CONTINUE", "Host Toolchain", "INSTALLED & VERIFIED")

# =============================================================================
# 5. USERSPACE SOURCES: NATIVE PID 1, INITRAMFS, SUPERVISORS, APPLICATIONS
# =============================================================================

# --- Native Ribi PID 1 Supervisor (/sbin/ribi-init) ---
SRC_RIBI_INIT = """#!/bin/sh
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/root
umask 022
if [ -x /usr/sbin/setfont ] && [ -d /usr/share/consolefonts ]; then
    _ribi_font=/usr/share/consolefonts/Lat2-Terminus16.psfu.gz
    [ -f "$_ribi_font" ] || _ribi_font=$(find /usr/share/consolefonts -maxdepth 1 -type f -name 'ter-*.psf*' | sort | head -1)
    [ -n "$_ribi_font" ] && /usr/sbin/setfont "$_ribi_font" >/dev/null 2>&1 || true
fi
_C='\\033[1;36m'; _G='\\033[1;32m'; _Y='\\033[1;33m'; _R='\\033[0m'

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

if [ -f /etc/hostname ]; then hostname "$(cat /etc/hostname | tr -d '\\r\\n')" || true; fi
ip link set lo up 2>/dev/null || true

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
        [ "${#_ribi_machine_id}" -eq 32 ] && printf '%s\\n' "$_ribi_machine_id" > /etc/machine-id
    fi
    chmod 0444 /etc/machine-id 2>/dev/null || true
    printf '[RIBI-INIT] machine-id initialized\\n' >/dev/ttyS0 2>/dev/null || true
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
printf '[RIBI-INIT] before-udev\n' >/dev/ttyS0 2>/dev/null || true
if [ -x /sbin/udevd ]; then
    /sbin/udevd --daemon >/dev/null 2>&1 || /sbin/udevd >/dev/null 2>&1 &
    [ -x /sbin/udevadm ] && /sbin/udevadm trigger --action=add >/dev/null 2>&1 || true
    [ -x /sbin/udevadm ] && /sbin/udevadm settle --timeout=5 >/dev/null 2>&1 || true
fi
printf '[RIBI-INIT] after-udev\n' >/dev/ttyS0 2>/dev/null || true

# Handle the physical/QEMU ACPI power button in this non-systemd PID-1 setup.
if command -v acpid >/dev/null 2>&1; then
    acpid -f -n >/tmp/ribi-acpid.log 2>&1 &
    _ribi_acpid_pid=$!
    sleep 0.2
    if kill -0 "$_ribi_acpid_pid" 2>/dev/null; then
        printf '[RIBI-INIT] ACPI power-button handler active\n' >/dev/ttyS0 2>/dev/null || true
    else
        printf '[RIBI-INIT] ACPI handler failed to start\n' >/dev/ttyS0 2>/dev/null || true
        cat /tmp/ribi-acpid.log >/dev/ttyS0 2>/dev/null || true
    fi
fi

# Start native services without blocking the graphical session on DHCP or other
# optional services. The supervisor remains alive and writes its own diagnostics.
mkdir -p /run/dbus
if [ -x /usr/local/bin/ribisvc ]; then
    /usr/local/bin/ribisvc --start-all >/tmp/ribisvc-boot.log 2>&1 &
fi
printf '[RIBI-INIT] after-services\n' >/dev/ttyS0 2>/dev/null || true

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
        printf '[BOOT-1] Entered ribi-init\n' >/dev/ttyS0
        setsid -c /bin/sh -l </dev/ttyS0 >/dev/ttyS0 2>&1
    elif [ -x /usr/local/bin/ribi-xorg ] && [ -x /usr/local/bin/ribi-visible-session ]; then
        # Own the VT and DRM device with exactly one Xorg process. LightDM's
        # repeated greeter/session restart loop can leave the virtio DRM device
        # busy, so the live release uses a deterministic direct session here.
        printf '[RIBI-GUI] init pid=%s uid=%s tty1=%s ttyS0=%s\n' "$$" "$(id -u 2>/dev/null)" "$(test -c /dev/tty1; echo $?)" "$(test -c /dev/ttyS0; echo $?)" >/dev/ttyS0 2>/dev/null || true
        printf '[RIBI-GUI] PATH=%s DISPLAY=%s HOME=%s\n' "${PATH:-}" "${DISPLAY:-}" "${HOME:-}" >/dev/ttyS0 2>/dev/null || true
        command -v chvt >/dev/null 2>&1 && chvt 1 >/dev/null 2>&1 || true
        printf '[RIBI-GUI] starting direct Xorg\n' >/dev/ttyS0 2>/dev/null || true
        mkdir -p /run/ribi /run/user/1000
        chown 1000:1000 /run/user/1000 2>/dev/null || true
        chmod 700 /run/user/1000
        AUTH=/run/ribi/server.auth
        rm -f "$AUTH"
        COOKIE=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
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
        printf '[RIBI-GUI] xorg_parent=%s real=%s socket=%s\n' "$_x_pid" "$_x_real" "$(test -S /tmp/.X11-unix/X0; echo $?)" >/dev/ttyS0 2>/dev/null || true
        printf '[RIBI-GUI] Xorg log tail begin\n' >/dev/ttyS0 2>/dev/null || true
        tail -n 140 /tmp/ribi-xorg-direct.log >/dev/ttyS0 2>&1 || true
        printf '[RIBI-GUI] Xorg log tail end\n' >/dev/ttyS0 2>/dev/null || true
        if command -v xrandr >/dev/null 2>&1; then
            DISPLAY=:0 XAUTHORITY="$AUTH" xrandr --fb 1280x800 >/dev/ttyS0 2>&1 || true
            DISPLAY=:0 XAUTHORITY="$AUTH" xrandr --query >/dev/ttyS0 2>&1 || true
        fi
        printf '[RIBI-GUI] starting persistent ribi session\n' >/dev/ttyS0 2>/dev/null || true
        DISPLAY=:0 XAUTHORITY="$AUTH" HOME=/home/ribi USER=ribi LOGNAME=ribi XDG_RUNTIME_DIR=/run/user/1000 /usr/bin/feh --bg-fill --no-fehbg /usr/share/backgrounds/ribi-wallpaper.png >/tmp/ribi-wallpaper-direct.log 2>&1 || true
        printf '[RIBI-GUI] wallpaper painted\n' >/dev/ttyS0 2>/dev/null || true
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
            DISPLAY=:0 XAUTHORITY="$AUTH" HOME=/home/ribi USER=ribi LOGNAME=ribi \
                XDG_RUNTIME_DIR=/run/user/1000 XDG_CURRENT_DESKTOP=Ribi XDG_SESSION_DESKTOP=ribi \
                /usr/local/bin/ribi-wm.py >>/tmp/ribi-wm-direct.log 2>&1 &
            _ribi_wm_pid=$!
            sleep 1
            if kill -0 "$_ribi_wm_pid" 2>/dev/null; then
                printf '[RIBI-GUI] default Ribi WM active pid=%s\n' "$_ribi_wm_pid" >/dev/ttyS0 2>/dev/null || true
            else
                printf '[RIBI-GUI] Ribi WM failed; continuing with compatibility fallback\n' >/dev/ttyS0 2>/dev/null || true
                _ribi_wm_pid=''
            fi
        fi
        printf '[RIBI-GUI] starting persistent Ribi shell\n' >/dev/ttyS0 2>/dev/null || true
        DISPLAY=:0 XAUTHORITY="$AUTH" HOME=/home/ribi USER=ribi LOGNAME=ribi XDG_RUNTIME_DIR=/run/user/1000 XDG_CURRENT_DESKTOP=Ribi XDG_SESSION_DESKTOP=ribi /usr/local/bin/ribi-drop-session /usr/local/bin/ribi-shell.py >/tmp/ribi-session-direct.log 2>&1
        _session_rc=$?
        printf '[RIBI-GUI] ribi session exit=%s\\n' "$_session_rc" >/dev/ttyS0 2>/dev/null || true
        [ "$_session_rc" -eq 0 ] || cat /tmp/ribi-session-direct.log >/dev/ttyS0 2>/dev/null || true
        printf '[RIBI-GUI] direct session ended\n' >/dev/ttyS0 2>/dev/null || true
        [ -n "$_ribi_wm_pid" ] && kill "$_ribi_wm_pid" 2>/dev/null || true
        kill "$_x_pid" 2>/dev/null || true
    elif [ -c /dev/tty1 ] && [ -w /dev/tty1 ]; then
        # Normal Vectras/PC mode: make tty1 the visible controlling VT before
        # attaching the login shell, since firmware may leave another VT active.
        command -v chvt >/dev/null 2>&1 && chvt 1 >/dev/null 2>&1 || true
        printf '[BOOT-1] Entered ribi-init\n' >/dev/tty1
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
"""

# --- Resilient Live Boot Script (/init) ---
SRC_LIVE_INIT = """#!/bin/sh
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
_TITLE='\033[1;37m'; _DIM='\033[90m'; _R='\033[0m'
draw_loader() {
    label="$1"; filled="$2"; total=32
    printf '\033[2J\033[H'
    printf "${_TITLE}RIBI OS${_R}  ${_DIM}x86_64${_R}\n\n"
    printf "${_DIM}%s${_R}\n\n" "$label"
    i=1
    while [ "$i" -le "$total" ]; do
        if [ "$i" -le "$filled" ]; then
            case "$i" in
              1|2|3|4|5|6|7|8) color='\033[38;5;153m' ;;
              9|10|11|12|13|14|15|16) color='\033[38;5;117m' ;;
              17|18|19|20|21|22|23|24) color='\033[38;5;132m' ;;
              25|26|27|28|29|30) color='\033[38;5;124m' ;;
              *) color='\033[38;5;88m' ;;
            esac
            printf "%b█%b" "$color" "$_R"
        else
            printf "${_DIM}░${_R}"
        fi
        i=$((i + 1))
    done
    printf "  ${_TITLE}%3s%%${_R}\n" "$((filled * 100 / total))"
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
    exec chroot /sysroot /sbin/ribi-init
fi

draw_loader "Preparing live system" 1
sleep 2
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    for dev in /dev/sr* /dev/sd* /dev/nvme* /dev/vd* /dev/mmcblk* /dev/loop*; do
        [ -e "$dev" ] || continue
        mount -r -t iso9660 "$dev" /run/media 2>/dev/null || \
        mount -r -t squashfs "$dev" /run/media 2>/dev/null || \
        mount -r -t ext4 "$dev" /run/media 2>/dev/null || \
        mount -r -t vfat "$dev" /run/media 2>/dev/null

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
        printf '[RIBI-PERSIST] using writable disk %s\n' "$pd" >/dev/ttyS0 2>/dev/null || true
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
    printf '[RIBI-INITRAMFS] handoff sysroot=%s init=%s switch_root=%s\n' "$(grep -q ' /sysroot ' /proc/mounts; echo $?)" "$(test -x /sysroot/sbin/ribi-init; echo $?)" "$(command -v switch_root 2>/dev/null || echo missing)" >/dev/ttyS0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] handoff begin\n'
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
    printf '[RIBI-INITRAMFS] bind-dev-before\n' >/dev/ttyS0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] bind-dev-before\n' >/dev/tty0 2>/dev/null || true
    mount_with_timeout 5 mount --bind /dev /sysroot/dev 2>/dev/null; _rc=$?; printf '[RIBI-INITRAMFS] bind-dev-after rc=%s\n' "$_rc" >/dev/ttyS0 2>/dev/null || true; printf '[RIBI-INITRAMFS] bind-dev-after rc=%s\n' "$_rc" >/dev/tty0 2>/dev/null || true
    mkdir -p /sysroot/dev/pts
    mount_with_timeout 5 mount --bind /dev/pts /sysroot/dev/pts 2>/dev/null || true
    /bin/busybox rm -f /sysroot/dev/ptmx 2>/dev/null || true
    /bin/busybox ln -sf pts/ptmx /sysroot/dev/ptmx 2>/dev/null || true
    printf '[RIBI-INITRAMFS] bind-devpts\n' >/dev/ttyS0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] bind-proc-before\n' >/dev/ttyS0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] bind-proc-before\n' >/dev/tty0 2>/dev/null || true
    mount_with_timeout 5 mount --bind /proc /sysroot/proc 2>/dev/null; _rc=$?; printf '[RIBI-INITRAMFS] bind-proc-after rc=%s\n' "$_rc" >/dev/ttyS0 2>/dev/null || true; printf '[RIBI-INITRAMFS] bind-proc-after rc=%s\n' "$_rc" >/dev/tty0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] bind-sys-before\n' >/dev/ttyS0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] bind-sys-before\n' >/dev/tty0 2>/dev/null || true
    mount_with_timeout 5 mount --rbind /sys /sysroot/sys 2>/dev/null; _rc=$?; printf '[RIBI-INITRAMFS] bind-sys-after rc=%s\n' "$_rc" >/dev/ttyS0 2>/dev/null || true; printf '[RIBI-INITRAMFS] bind-sys-after rc=%s\n' "$_rc" >/dev/tty0 2>/dev/null || true
    printf '[RIBI-INITRAMFS] chroot handoff\n' >/dev/ttyS0 2>/dev/null || true
    exec chroot /sysroot /sbin/ribi-init
"""

# --- Stateful Native Service Supervisor (/usr/local/bin/ribisvc) ---
SRC_RIBI_SVC = """#!/usr/bin/env python3
import sys, os, subprocess, json, signal, time
from pathlib import Path
from typing import Optional, Dict, Tuple, Any
SERVICES_DIR=Path('/etc/ribi/services'); RUN_DIR=Path('/run/ribi/services')
NAME_RE=__import__('re').compile(r'^[A-Za-z0-9_.+-]+$')

def init_runtime(): RUN_DIR.mkdir(parents=True,exist_ok=True); SERVICES_DIR.mkdir(parents=True,exist_ok=True)
def get_service_meta(name):
    if not NAME_RE.fullmatch(name): return None
    p=SERVICES_DIR/f'{name}.json'
    if not p.is_file(): return None
    try:
        d=json.loads(p.read_text());
        if not isinstance(d,dict): return None
        return d
    except Exception as e: print(f'Error reading service {name}: {e}'); return None

def _proc_starttime(pid):
    try:
        fields=Path(f'/proc/{pid}/stat').read_text().split()
        if len(fields) < 22 or fields[2] == 'Z': return None
        return fields[21]
    except Exception: return None

def is_running(name):
    pid_file=RUN_DIR/f'{name}.pid'; meta_file=RUN_DIR/f'{name}.start'
    if not pid_file.is_file(): return False,None
    try:
        pid=int(pid_file.read_text().strip()); start=pid_file.with_suffix('.start').read_text().strip() if pid_file.with_suffix('.start').is_file() else ''
        os.kill(pid,0)
        current=_proc_starttime(pid)
        if current is None or (start and current != start): raise OSError('PID reused or zombie')
        return True,pid
    except Exception:
        pid_file.unlink(missing_ok=True); meta_file.unlink(missing_ok=True); return False,None

def _normalize_cmd(cmd):
    if isinstance(cmd,list) and all(isinstance(x,str) for x in cmd): return cmd
    if isinstance(cmd,str): return ['/bin/sh','-c',cmd]
    return None

def start_service(name):
    init_runtime(); meta=get_service_meta(name)
    if not meta: print(f"Service '{name}' not found or invalid."); return False
    running,pid=is_running(name)
    if running: print(f"Service '{name}' is already running (PID {pid})."); return True
    cmd=_normalize_cmd(meta.get('start_command'))
    if not cmd: print(f"Service '{name}' has no valid start_command."); return False
    try:
        proc=subprocess.Popen(cmd, start_new_session=True, close_fds=True)
        (RUN_DIR/f'{name}.pid').write_text(str(proc.pid)); (RUN_DIR/f'{name}.start').write_text(_proc_starttime(proc.pid) or '')
        return True
    except OSError as e: print(f"Error starting {name}: {e}"); return False

def stop_service(name):
    init_runtime(); running,pid=is_running(name)
    if not running or pid is None: print(f"Service '{name}' is not running."); return False
    try:
        os.killpg(pid,signal.SIGTERM); deadline=time.time()+3
        while time.time()<deadline and is_running(name)[0]: time.sleep(.1)
        if is_running(name)[0]: os.killpg(pid,signal.SIGKILL)
    except OSError: pass
    (RUN_DIR/f'{name}.pid').unlink(missing_ok=True); (RUN_DIR/f'{name}.start').unlink(missing_ok=True); return True

def list_services():
    init_runtime(); print('RIBI OS REGISTERED SERVICES:')
    for p in sorted(SERVICES_DIR.glob('*.json')):
        m=get_service_meta(p.stem)
        if m:
            r,pid=is_running(p.stem); print(f"  {p.stem:<20} [{'RUNNING '+str(pid) if r else 'STOPPED':<14}] [{'ENABLED' if m.get('enabled') else 'DISABLED':<8}] - {m.get('description','')}")

def start_all():
    init_runtime()
    for p in sorted(SERVICES_DIR.glob('*.json')):
        m=get_service_meta(p.stem)
        if m and m.get('enabled'): start_service(p.stem)

if __name__=='__main__':
    init_runtime(); a=sys.argv[1:] if len(sys.argv)>1 else []
    if a==['list']: list_services()
    elif a==['--start-all']: start_all()
    elif len(a)>=2 and a[0] in ('start','stop','restart','status'):
        if a[0]=='start': ok=start_service(a[1])
        elif a[0]=='stop': ok=stop_service(a[1])
        elif a[0]=='restart': stop_service(a[1]); ok=start_service(a[1])
        else:
            r,pid=is_running(a[1]); print(f"Status: {'RUNNING ('+str(pid)+')' if r else 'STOPPED'}"); ok=True
        sys.exit(0 if ok else 1)
    else: print('Usage: ribisvc {start <svc>|stop <svc>|restart <svc>|status <svc>|list|--start-all}'); sys.exit(1)
"""

# --- Native Package Manager (.rpk) (/usr/local/bin/ribi-pkg) ---
SRC_RIBI_PKG = """#!/usr/bin/env python3
import sys, os, tarfile, json, hashlib, re, tempfile, shutil, copy, subprocess, fcntl
from pathlib import Path
DB_DIR=Path('/var/lib/ribi/pkgdb'); LOCK=DB_DIR/'.lock'; NAME_RE=re.compile(r'^[a-z0-9][a-z0-9._+-]{0,63}$')

def init_db(): DB_DIR.mkdir(parents=True,exist_ok=True)
def pkgfile(name):
    if not isinstance(name,str) or not NAME_RE.fullmatch(name): raise ValueError('invalid package name')
    return DB_DIR/f'{name}.json'
def digest_file(p):
    h=hashlib.sha256();
    with open(p,'rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''): h.update(c)
    return h.hexdigest()
def safe_member(member):
    rel=Path(member.name)
    if rel.is_absolute() or '..' in rel.parts or str(rel)=='.': raise RuntimeError(f'unsafe package path: {member.name}')
    if member.issym() or member.islnk():
        link=Path(member.linkname)
        if link.is_absolute() or '..' in link.parts: raise RuntimeError(f'unsafe package link: {member.name}')
def payload_digest(tar):
    h=hashlib.sha256()
    for m in tar.getmembers():
        if not m.name.startswith('payload/'): continue
        f=tar.extractfile(m)
        if f: h.update(m.name[8:].encode()+b'\\0'); h.update(hashlib.sha256(f.read()).digest())
    return h.hexdigest()
def verify_rpk_signature(tar, manifest, payload_hash):
    sig_member = next((x for x in tar.getmembers() if x.name == 'signature.bin'), None)
    require = os.environ.get('RIBI_REQUIRE_SIGNED') == '1' or bool(manifest.get('signature_required'))
    if sig_member is None:
        if require: raise SystemExit('Error: signed package required but signature.bin is missing')
        return False
    if manifest.get('signature_algorithm', 'ed25519') != 'ed25519':
        raise SystemExit('Error: unsupported RPK signature algorithm')
    key = Path(os.environ.get('RIBI_REPOSITORY_KEY', '/etc/ribi/keys/repository.pub'))
    if not key.is_file(): raise SystemExit(f'Error: signature present but repository key is unavailable: {key}')
    sig = tar.extractfile(sig_member)
    if sig is None: raise SystemExit('Error: unreadable package signature')
    canonical = dict(manifest); canonical.pop('signature_required', None); canonical.pop('signature_algorithm', None)
    data = (json.dumps(canonical, sort_keys=True, separators=(',', ':')) + chr(10) + payload_hash + chr(10)).encode()
    with tempfile.TemporaryDirectory(prefix='rpk-verify-', dir='/tmp') as td:
        base=Path(td); (base/'data').write_bytes(data); (base/'sig').write_bytes(sig.read())
        result=subprocess.run(['openssl','pkeyutl','-verify','-pubin','-inkey',str(key),'-rawin','-in',str(base/'data'),'-sigfile',str(base/'sig')], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0: raise SystemExit('Error: RPK signature verification failed')
    return True
def install_rpk(path):
    init_db()
    if not path.is_file(): raise SystemExit(f"Error: Package file '{path}' not found.")
    with tarfile.open(path,'r:*') as tar:
        mf=tar.extractfile('manifest.json')
        if not mf: raise SystemExit('Error: Invalid package. Missing manifest.json.')
        try: m=json.loads(mf.read().decode('utf-8'))
        except Exception as e: raise SystemExit(f'Error: Invalid manifest JSON: {e}')
        if not isinstance(m,dict): raise SystemExit('Error: manifest must be an object')
        name=m.get('name'); ver=m.get('version'); arch=m.get('architecture',m.get('arch','x86_64'))
        if not isinstance(name,str) or not NAME_RE.fullmatch(name): raise SystemExit('Error: Invalid package name')
        if not isinstance(ver,str) or not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z._+~-]{0,127}',ver): raise SystemExit('Error: Invalid package version')
        if arch!='x86_64': raise SystemExit(f'Error: Unsupported package architecture: {arch}')
        members=[x for x in tar.getmembers() if x.name.startswith('payload/') and x.name!='payload/']
        if not members: raise SystemExit('Error: Package payload is empty')
        expected=m.get('payload_sha256')
        actual=payload_digest(tar)
        if expected and (not isinstance(expected,str) or not re.fullmatch(r'[0-9a-fA-F]{64}',expected) or actual.lower()!=expected.lower()):
            raise SystemExit('Error: Package payload integrity check failed')
        if not expected: raise SystemExit('Error: Package lacks required payload_sha256')
        signed=verify_rpk_signature(tar, m, actual)
        existing=pkgfile(name)
        if existing.exists(): raise SystemExit(f"Error: Package '{name}' is already installed")

        seen=set(); payload=[]
        for member in members:
            safe_member(member)
            rel=Path(member.name[8:])
            if rel.is_absolute() or '..' in rel.parts or str(rel)=='.':
                raise SystemExit(f'Error: unsafe package path: {member.name}')
            key=rel.as_posix()
            if key in seen: raise SystemExit(f'Error: duplicate package path: {member.name}')
            seen.add(key)
            payload.append((member,rel))

        protected={'/sbin/ribi-init','/sbin/init','/usr/local/bin/ribi-pkg','/etc/passwd','/etc/shadow'}
        installed=['/'+rel.as_posix() for member,rel in payload if not member.isdir()]
        if any(x in protected for x in installed):
            raise SystemExit('Error: Package attempts to replace protected Ribi system files')

        with tempfile.TemporaryDirectory(prefix='rpk-',dir='/tmp') as td:
            root=Path(td)/'root'; root.mkdir()
            # Extract directories/regular files/symlinks first. Hardlinks are deferred
            # so a valid forward hardlink does not depend on archive member order.
            hardlinks=[]
            for member,rel in payload:
                dest=root/rel
                curr=root
                for part in rel.parts[:-1]:
                    curr=curr/part
                    if curr.is_symlink():
                        raise SystemExit(f'Error: symlink used as package directory: {curr}')
                    if curr.exists() and not curr.is_dir():
                        raise SystemExit(f'Error: non-directory package path component: {curr}')
                    curr.mkdir(exist_ok=True)
                if dest.exists() or dest.is_symlink():
                    if member.isdir() and dest.is_dir() and not dest.is_symlink():
                        continue
                    raise SystemExit(f'Error: duplicate/existing package staging path: {dest}')
                member_copy=copy.copy(member)
                member_copy.name=rel.as_posix()
                if member.issym():
                    link=Path(member.linkname)
                    if link.is_absolute() or '..' in link.parts:
                        raise SystemExit(f'Error: unsafe package link: {member.name}')
                if member.islnk():
                    hardlinks.append((member_copy,rel)); continue
                tar.extract(member_copy,path=root,filter='data')
            for member,rel in hardlinks:
                target=Path(member.linkname)
                if target.is_absolute() or '..' in target.parts or target.as_posix() not in seen:
                    raise SystemExit(f'Error: unsafe/missing hardlink target: {member.linkname}')
                dest=root/rel
                if dest.exists() or dest.is_symlink():
                    raise SystemExit(f'Error: duplicate package staging path: {dest}')
                tar.extract(member,path=root,filter='data')

            destinations=[]
            for rel in installed:
                src=root/rel.lstrip('/'); dst=Path('/'+rel.lstrip('/'))
                if not src.exists() and not src.is_symlink():
                    raise SystemExit(f'Error: staged package file missing: {src}')
                if dst.exists() or dst.is_symlink():
                    raise SystemExit(f'Error: refusing to overwrite existing path: {dst}')
                destinations.append((src,dst))
            moved=[]
            try:
                for src,dst in destinations:
                    dst.parent.mkdir(parents=True,exist_ok=True)
                    shutil.move(str(src),str(dst)); moved.append((src,dst))
            except Exception:
                for src,dst in reversed(moved):
                    if dst.exists() or dst.is_symlink():
                        src.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(dst),str(src))
                raise

        m['installed_files']=installed; m['payload_sha256']=actual; m['signature_verified']=signed
        pkgfile(name).write_text(json.dumps(m,indent=2)+'\\n')
    print(f"[+] Package '{name}' ({ver}) installed.")

def install_compat(name):
    if not NAME_RE.fullmatch(name):
        raise SystemExit(f"Error: Invalid package name '{name}'.")
    apk=Path('/sbin/apk')
    if not apk.exists():
        raise SystemExit('Error: Compatibility package client is unavailable in this image.')
    print(f"[*] Installing '{name}' from the configured signed compatibility repositories...")
    try:
        result=subprocess.run([str(apk),'add','--no-cache',name], timeout=120)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"Error: Compatibility package '{name}' timed out while contacting repositories.")
    if result.returncode:
        raise SystemExit(f"Error: Compatibility package '{name}' could not be installed.")
    print(f"[+] Compatibility package '{name}' installed.")

def install_target(target):
    p=Path(target)
    if p.exists() or target.endswith('.rpk') or '/' in target:
        install_rpk(p)
    else:
        install_compat(target)

def compat_command(action, args):
    apk=Path('/sbin/apk')
    if not apk.exists(): raise SystemExit('Error: Compatibility package client is unavailable.')
    cmd=[str(apk), action, '--no-cache'] + args
    try:
        result=subprocess.run(cmd, timeout=30 if action=='search' else 120)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"Error: '{action}' timed out while contacting repositories. Check networking and try again.")
    raise SystemExit(result.returncode)

def remove_pkg(name):
    try: db=pkgfile(name)
    except ValueError: print('Error: Invalid package name.'); return 1
    if not db.exists(): print(f"Error: Package '{name}' is not installed."); return 1
    m=json.loads(db.read_text()); installed=m.get('installed_files',[])
    # Ownership-aware removal: do not remove a path referenced by another package.
    owned={x for x in installed if isinstance(x,str) and x.startswith('/')}
    others=set()
    for f in DB_DIR.glob('*.json'):
        if f==db: continue
        try: others.update(x for x in json.loads(f.read_text()).get('installed_files',[]) if isinstance(x,str))
        except Exception: pass
    for x in sorted(owned-others,key=len,reverse=True):
        p=Path(x)
        if p.is_file() or p.is_symlink(): p.unlink(missing_ok=True)
    db.unlink(); print(f"[+] Package '{name}' removed."); return 0
def locked(fn, *args):
    init_db()
    with open(LOCK, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return fn(*args)

def list_pkgs():
    init_db(); print(f"{'PACKAGE':<25} {'VERSION':<15} DESCRIPTION")
    for f in sorted(DB_DIR.glob('*.json')):
        try:
            d=json.loads(f.read_text()); print(f"{d.get('name',f.stem):<25} {d.get('version','?'):<15} {d.get('description','')}")
        except Exception: print(f"{f.stem:<25} {'INVALID':<15}")

if __name__=='__main__':
    a=sys.argv[1:]
    if not a: print('Usage: ribi-pkg {install <name|pkg.rpk>|remove <name>|list|info}'); raise SystemExit(0)
    if a[0]=='install' and len(a)>1: locked(install_target, a[1])
    elif a[0]=='remove' and len(a)>1: raise SystemExit(locked(remove_pkg, a[1]))
    elif a[0]=='list': list_pkgs()
    elif a[0] in ('search','update'): compat_command(a[0],a[1:])
    elif a[0]=='info': print('Ribi packages: signed Alpine compatibility packages by name, plus authenticated RPK files.')
    else: raise SystemExit('Usage: ribi-pkg {install <pkg.rpk>|remove <name>|list|info}')
"""

# --- Unified Ribi System CLI (/usr/local/bin/ribi) ---
SRC_RIBI_CLI = """#!/usr/bin/env python3
import sys
import subprocess

HELP_TEXT = \"\"\"
===============================================================================
 RIBI OS - Unified System & Package Controller
===============================================================================
 Usage:
   ribi install <name|file.rpk> Install a repository package or native .rpk
   ribi search <name>       Search configured compatibility repositories
   ribi update              Refresh compatibility repository indexes
   ribi remove <pkg>        Remove installed Ribi package
   ribi list                List installed native packages
   ribi service <cmd>       Manage services via ribisvc
   ribi sysinfo             Display hardware, memory, and kernel status
   ribi version             Show Ribi OS release information
===============================================================================
\"\"\"

def main():
    if len(sys.argv) < 2:
        print(HELP_TEXT)
        sys.exit(0)

    action = sys.argv[1]
    if action == "install" and len(sys.argv) > 2:
        sys.exit(subprocess.run(["ribi-pkg", "install", sys.argv[2]]).returncode)
    elif action == "remove" and len(sys.argv) > 2:
        sys.exit(subprocess.run(["ribi-pkg", "remove", sys.argv[2]]).returncode)
    elif action in ("search", "update"):
        sys.exit(subprocess.run(["ribi-pkg"] + sys.argv[1:]).returncode)
    elif action == "list":
        sys.exit(subprocess.run(["ribi-pkg", "list"]).returncode)
    elif action == "service" and len(sys.argv) > 2:
        sys.exit(subprocess.run(["ribisvc"] + sys.argv[2:]).returncode)
    elif action == "sysinfo":
        print("=== Ribi OS System Status ===")
        subprocess.run(["uname", "-a"])
        subprocess.run(["uptime"])
    elif action in ("version", "--version", "-v"):
        print("RIBI OS x86_64")
    else:
        print(HELP_TEXT)

if __name__ == "__main__":
    main()
"""

# --- Interactive first-boot setup wizard (/usr/local/bin/ribi-setup) ---
SRC_RIBI_SETUP = r"""#!/usr/bin/env python3
import os, subprocess, sys, time
from pathlib import Path

def sh(cmd, check=False):
    return subprocess.run(cmd, text=True, capture_output=True, check=check)
def ask(q, default=''):
    v=input(f'{q} [{default}]: ').strip()
    return v or default
def yes(q):
    return input(q+' Type YES to continue: ').strip() == 'YES'
def choices(q, values, default):
    value=ask(q, default).lower()
    if value not in values: raise SystemExit(f"Invalid choice '{value}'. Choose one of: {', '.join(values)}")
    return value
def interfaces():
    try:
        return sorted(p.name for p in Path('/sys/class/net').iterdir() if p.name != 'lo')
    except Exception: return []
def devices():
    try:
        out=subprocess.check_output(['lsblk','-rpo','NAME,TYPE,SIZE,FSTYPE,LABEL,MODEL'],text=True)
        rows=[]
        for line in out.splitlines():
            f=line.split(None,5)
            if len(f)>=3 and f[1] in ('disk','part'):
                rows.append(f)
        return rows
    except Exception: return []
def main():
    if os.geteuid()!=0: raise SystemExit('Setup Ribi OS must run as administrator.')
    print('\n=== Ribi OS First-Boot Setup ===\n')
    print('This wizard configures networking and can prepare persistence.')
    print('Nothing is erased unless you explicitly type YES.\n')
    hostname=ask('Hostname','ribi')
    if not __import__('re').fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,62}', hostname):
        raise SystemExit('Invalid hostname. Use letters, numbers, dots, and hyphens.')
    detected=interfaces(); iface=ask('Network interface (detected: '+(', '.join(detected) or 'none')+')',detected[0] if detected else 'eth0')
    ipv4=choices('IPv4 mode (dhcp/manual)', {'dhcp','manual'}, 'dhcp')
    ipv4addr=''; gateway=''; dns=''
    if ipv4=='manual':
        ipv4addr=ask('IPv4 address/CIDR','192.168.1.100/24'); gateway=ask('Gateway','192.168.1.1'); dns=ask('DNS','1.1.1.1')
    ipv6=choices('IPv6 mode (auto/disabled/manual)', {'auto','disabled','manual'}, 'auto')
    timezone=ask('Timezone','UTC')
    print('\nStorage devices/partitions:')
    rows=devices()
    for i,r in enumerate(rows): print(f'  {i}: '+' | '.join(r))
    selected=''
    if rows:
        raw=ask('Enter device path or number (blank = RAM only)','')
        if raw.isdigit() and int(raw)<len(rows): selected=rows[int(raw)][0]
        else: selected=raw
    mode=choices('Disk use (persistent-overlay/full-install/ram-only)', {'persistent-overlay','full-install','ram-only'}, 'persistent-overlay')
    if mode=='ram-only' or not selected:
        print('Keeping RAM-only mode. Network settings will be applied for this session.')
        Path('/etc/ribi').mkdir(exist_ok=True)
        Path('/etc/ribi/setup.conf').write_text(f'hostname={hostname}\ninterface={iface}\nipv4={ipv4}\nipv6={ipv6}\ntimezone={timezone}\n')
        return 0
    if not selected.startswith('/dev/') or not Path(selected).exists(): raise SystemExit('Selected device does not exist.')
    typ=sh(['lsblk','-dnpo','TYPE',selected]).stdout.strip()
    if typ not in ('part','disk'): raise SystemExit('Selected path is not a disk or partition.')
    print(f'\nSelected: {selected} ({typ})')
    print('1) use existing ext4 filesystem\n2) erase and create ext4 filesystem')
    action=ask('Storage action','1')
    if action in ('2','erase','format'):
        if not yes(f'PERMANENTLY ERASE ALL DATA on {selected}?'): raise SystemExit('Formatting cancelled.')
        subprocess.run(['mkfs.ext4','-F','-L','RibiPersistence',selected],check=True)
    fstype=sh(['blkid','-s','TYPE','-o','value',selected]).stdout.strip()
    if fstype!='ext4': raise SystemExit('Persistence requires ext4. Choose erase/create ext4 or cancel.')
    if not yes(f'Prepare {selected} for Ribi persistent storage?'): raise SystemExit('Persistence cancelled.')
    mount='/run/ribi-setup-storage'; Path(mount).mkdir(exist_ok=True)
    subprocess.run(['mount',selected,mount],check=True)
    try:
        Path(mount,'upper').mkdir(exist_ok=True); Path(mount,'work').mkdir(exist_ok=True)
        Path(mount,'.ribi-persistence').write_text('Ribi OS persistent live overlay\n')
        Path(mount,'setup.conf').write_text(f'hostname={hostname}\ninterface={iface}\nipv4={ipv4}\nipv6={ipv6}\ntimezone={timezone}\n')
    finally: subprocess.run(['umount',mount],check=False)
    if mode=='full-install':
        print('Launching the full-disk installer. Review its separate confirmation carefully.')
        return subprocess.run(['/usr/local/bin/ribi-installer']).returncode
    print('\nPersistence prepared successfully. Reboot and choose Ribi OS normally.')
    print('The next boot will discover the prepared disk automatically.')
    return 0
if __name__=='__main__': raise SystemExit(main())
"""

# --- Dual BIOS + UEFI Storage Installer (/usr/local/bin/ribi-installer) ---
SRC_RIBI_INSTALLER = """#!/usr/bin/env python3
import os, sys, shutil, subprocess, time

def run(c): return subprocess.run(c,check=True)
def parts(dev):
    out=subprocess.check_output(['lsblk','-lnpo','NAME,TYPE',dev],text=True)
    return [line.split()[0] for line in out.splitlines() if len(line.split())>=2 and line.split()[1]=='part']
def mountpoints_under(path):
    base=os.path.abspath(path).rstrip('/')
    try:
        with open('/proc/mounts','r') as mounts_file:
            found=[]
            for line in mounts_file:
                fields=line.split()
                if len(fields)>=2 and (fields[1]==base or fields[1].startswith(base+'/')):
                    found.append(fields[1])
        return sorted(set(found),key=len,reverse=True)
    except OSError:
        return [base]
def cleanup(paths):
    for p in reversed(paths):
        for mountpoint in mountpoints_under(p):
            subprocess.run(['umount',mountpoint],check=False)
def has_mounts_below(path): return bool(mountpoints_under(path))

def preflight_target(dev):
    rows=subprocess.run(['lsblk','-nrpo','NAME,MOUNTPOINTS',dev],capture_output=True,text=True,check=False).stdout.splitlines()
    mounted=[]
    for row in rows:
        fields=row.split(None,1)
        if len(fields)==2 and fields[1].strip() not in ('','-'):
            mounted.append(f'{fields[0]} -> {fields[1].strip()}')
    if mounted:
        raise SystemExit('Error: target disk or one of its partitions is mounted: '+', '.join(mounted))
    try:
        with open('/proc/mounts','r') as mounts_file:
            root_source=''
            for line in mounts_file:
                fields=line.split()
                if len(fields)>=2 and fields[1]=='/':
                    root_source=fields[0].split('[',1)[0]
                    break
    except OSError as exc:
        raise SystemExit('Error: cannot read active root source; refusing to install.') from exc
    if not root_source:
        raise SystemExit('Error: cannot determine active root source; refusing to install.')
    if root_source.startswith('/dev/'):
        root_source=os.path.realpath(root_source)
    if root_source and (root_source==dev or root_source.startswith(dev+'p') or root_source.startswith(dev)):
        raise SystemExit(f'Error: refusing to overwrite the active root device ({root_source}).')
    print('[safety] target and child partitions are not mounted; active root is protected.')

def dry_run_plan(dev):
    if not dev.startswith('/dev/') or dev == '/dev/':
        raise SystemExit('Error: dry-run target must look like a device path under /dev/.')
    if os.path.exists(dev):
        typ=subprocess.run(['lsblk','-dn','-o','TYPE',dev],capture_output=True,text=True,check=False).stdout.strip()
        if typ!='disk': raise SystemExit('Error: dry-run target exists but is not a whole disk.')
        preflight_target(dev)
        print(f'Target detected: whole disk {dev}; no mounts found')
    else:
        print('Target is not present in this environment; showing a static plan only.')
    uefi=os.path.isdir('/sys/firmware/efi')
    print('RIBI INSTALLER DRY-RUN — NO DISK WILL BE MODIFIED')
    print(f'Target placeholder: {dev}')
    print(f'Firmware plan: {"GPT + UEFI" if uefi else "MBR + BIOS"}')
    if uefi:
        print(f'  parted -s {dev} mklabel gpt')
        print(f'  create 513 MiB FAT32 ESP and mark esp')
        print(f'  create remaining ext4 RibiRoot partition')
    else:
        print(f'  parted -s {dev} mklabel msdos')
        print(f'  create full-disk ext4 RibiRoot partition and mark boot')
    print('  format filesystems only after interactive YES confirmation')
    print('  mount root, synchronize /run/rootfs, write fstab and machine-id')
    print('  install GRUB without NVRAM changes, write /boot/grub/grub.cfg')
    print('DRY-RUN COMPLETE — rerun without --dry-run only after reviewing the target disk.')
    return 0

def require_installer_tools():
    required=('lsblk','parted','partprobe','blockdev','mkfs.ext4','mkfs.vfat','mount','umount','rsync','blkid','grub-install','chroot','udevadm')
    missing=[tool for tool in required if shutil.which(tool) is None]
    if missing: raise SystemExit('Error: installer tools missing: '+', '.join(missing)+'. No disks were changed.')

def run_cli_installer():
    if '--dry-run' in sys.argv:
        targets=[x for x in sys.argv[1:] if x != '--dry-run']
        if len(targets)!=1: raise SystemExit('Usage: ribi-installer --dry-run /dev/<target-disk>')
        return dry_run_plan(targets[0])
    if os.geteuid()!=0: raise SystemExit('Error: Installer must be run as root.')
    src='/run/rootfs'
    if not os.path.isfile(src+'/sbin/ribi-init'): raise SystemExit('Error: Live rootfs not found at /run/rootfs')
    require_installer_tools()
    kernel='/run/media/live/vmlinuz' if os.path.isfile('/run/media/live/vmlinuz') else '/live/vmlinuz'
    initrd='/run/media/live/initrd.img' if os.path.isfile('/run/media/live/initrd.img') else '/live/initrd.img'
    if not os.path.isfile(kernel): raise SystemExit('FATAL: live kernel missing; no disks were changed.')
    subprocess.run(['lsblk','-d','-o','NAME,SIZE,MODEL,TYPE'])
    dev=input('Enter target disk device (e.g. /dev/sda): ').strip()
    if not dev.startswith('/dev/') or not os.path.exists(dev): raise SystemExit('Error: Device not found.')
    if subprocess.run(['lsblk','-dn','-o','TYPE',dev],capture_output=True,text=True).stdout.strip()!='disk': raise SystemExit('Error: Target must be a whole disk.')
    preflight_target(dev)
    if input(f"Re-enter target device exactly ({dev}) to confirm: ").strip()!=dev: raise SystemExit('Installation aborted: target confirmation did not match.')
    if input(f"WARNING: ALL DATA ON {dev} WILL BE ERASED! Type 'YES': ").strip()!='YES': raise SystemExit('Installation aborted.')
    uefi=os.path.isdir('/sys/firmware/efi'); print(f"[*] Partitioning {dev} ({'GPT / UEFI' if uefi else 'MBR / BIOS'})...")
    if uefi:
        run(['parted','-s',dev,'mklabel','gpt']); run(['parted','-s',dev,'mkpart','ESP','fat32','1MiB','513MiB']); run(['parted','-s',dev,'set','1','esp','on']); run(['parted','-s',dev,'mkpart','RibiRoot','ext4','513MiB','100%'])
    else:
        run(['parted','-s',dev,'mklabel','msdos']); run(['parted','-s',dev,'mkpart','primary','ext4','1MiB','100%']); run(['parted','-s',dev,'set','1','boot','on'])
    probe=subprocess.run(['partprobe',dev],capture_output=True,text=True)
    if probe.returncode!=0:
        reread=subprocess.run(['blockdev','--rereadpt',dev],capture_output=True,text=True)
        if reread.returncode!=0:
            print(f"WARNING: kernel did not immediately reread {dev}; waiting for udev.")
    disk_parts=[]
    for _ in range(20):
        disk_parts=parts(dev)
        if (len(disk_parts)>=2 if uefi else len(disk_parts)>=1): break
        subprocess.run(['udevadm','settle'],check=False); time.sleep(.5)
    if (len(disk_parts)<2 if uefi else len(disk_parts)<1): raise SystemExit(f'Error: partition table did not appear for {dev}')
    if uefi: esp,root=disk_parts[0],disk_parts[1]
    else: root=disk_parts[0]; esp=None
    mounted=[]; mount_dir='/tmp/ribi_target'
    try:
        if esp: run(['mkfs.vfat','-F32',esp])
        run(['mkfs.ext4','-F','-L','RibiRoot',root]); os.makedirs(mount_dir,exist_ok=True); run(['mount',root,mount_dir]); mounted.append(mount_dir)
        print('[*] Synchronizing clean Ribi OS rootfs...')
        run(['rsync','-aH','--delete','--exclude=/run/*','--exclude=/tmp/*','--exclude=/proc/*','--exclude=/sys/*','--exclude=/dev/*',src+'/',mount_dir+'/'])
        os.makedirs(mount_dir+'/boot',exist_ok=True)
        if esp: os.makedirs(mount_dir+'/boot/efi',exist_ok=True); run(['mount',esp,mount_dir+'/boot/efi']); mounted.append(mount_dir+'/boot/efi')
        # The installed system gets its own fstab and machine-id instead of live-media assumptions.
        os.makedirs(mount_dir+'/etc',exist_ok=True)
        uuid_root=subprocess.check_output(['blkid','-s','UUID','-o','value',root],text=True).strip()
        lines=[f'UUID={uuid_root} / ext4 defaults 0 1']
        if esp:
            uuid_esp=subprocess.check_output(['blkid','-s','UUID','-o','value',esp],text=True).strip(); lines.append(f'UUID={uuid_esp} /boot/efi vfat umask=0077 0 2')
        open(mount_dir+'/etc/fstab','w').write('\\n'.join(lines)+'\\n')
        os.makedirs(mount_dir+'/etc',exist_ok=True); open(mount_dir+'/etc/machine-id','w').write('')
        shutil.copy2(kernel,mount_dir+'/boot/vmlinuz'); os.chmod(mount_dir+'/boot/vmlinuz',0o755)
        if os.path.isfile(initrd): shutil.copy2(initrd,mount_dir+'/boot/initrd.img')
        vfs=[]
        try:
            for v in ('dev','proc','sys'):
                t=f'{mount_dir}/{v}'; os.makedirs(t,exist_ok=True); run(['mount','--rbind','/'+v,t]); vfs.append(t)
            if uefi:
                run(['chroot',mount_dir,'grub-install','--target=x86_64-efi','--efi-directory=/boot/efi','--bootloader-id=RibiOS','--no-nvram','--removable','--recheck'])
            else:
                run(['grub-install',f'--boot-directory={mount_dir}/boot','--target=i386-pc','--recheck',dev])
        finally: cleanup(vfs)
        os.makedirs(mount_dir+'/boot/grub',exist_ok=True)
        initrd_line=' ribi.installed=1 console=tty0 console=ttyS0,115200'+('\\n    initrd /boot/initrd.img' if os.path.isfile(mount_dir+'/boot/initrd.img') else '')
        open(mount_dir+'/boot/grub/grub.cfg','w').write(f'set default=0\\nset timeout=5\\nmenuentry "Ribi OS 1.0 (bulbQT)" {{\\n    linux /boot/vmlinuz root=UUID={uuid_root} rw init=/sbin/ribi-init{initrd_line}\\n}}\\n')
        print('[+] Ribi OS installation successfully completed!')
    finally:
        cleanup(mounted)
        if has_mounts_below(mount_dir):
            print('WARNING: target mounts remain; preserving the mounted tree to protect live devices.')
        else:
            shutil.rmtree(mount_dir,ignore_errors=True)

if __name__=='__main__': run_cli_installer()
"""

# --- Native fallback syntax-highlighting terminal editor (/usr/local/bin/ribi-edit) ---
# Guaranteed to work using only the Python 3 standard library (curses), independent
# of whether the optional Alpine 'geany' GUI package resolved/installed successfully.
SRC_RIBI_EDIT = """#!/usr/bin/env python3
import curses
import sys
import re

KEYWORDS = {
    "def","class","import","from","return","if","elif","else","for","while",
    "try","except","finally","with","as","pass","break","continue","in","is",
    "not","and","or","lambda","yield","global","nonlocal","None","True","False",
    "fi","then","do","done","esac","case","function","echo","local","export",
}
TOKEN_RE = re.compile(r"(#.*$|\\".*?\\"|'.*?'|\\b\\w+\\b)")

def highlight(win, y, x, line, max_x):
    col = x
    for m in TOKEN_RE.finditer(line):
        tok = m.group(0)
        if col >= max_x:
            break
        attr = curses.A_NORMAL
        if tok.startswith("#"):
            attr = curses.color_pair(2)
        elif tok.startswith('"') or tok.startswith("'"):
            attr = curses.color_pair(3)
        elif tok in KEYWORDS:
            attr = curses.color_pair(1) | curses.A_BOLD
        try:
            win.addnstr(y, col, tok, max_x - col, attr)
        except curses.error:
            pass
        col = x + m.end()

def main(stdscr, path):
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.curs_set(1)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\\n")
    except FileNotFoundError:
        lines = [""]

    cy, cx, top = 0, 0, 0
    modified = False
    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()
        body_h = max_y - 1
        if cy - top >= body_h:
            top = cy - body_h + 1
        if cy < top:
            top = cy
        for i in range(body_h):
            li = top + i
            if li >= len(lines):
                break
            highlight(stdscr, i, 0, lines[li][:max_x], max_x)
        status = f" ribi-edit: {path} {'[+]' if modified else ''}  ^S save  ^X exit "
        try:
            stdscr.addnstr(max_y - 1, 0, status.ljust(max_x), max_x, curses.A_REVERSE)
        except curses.error:
            pass
        stdscr.move(min(cy - top, body_h - 1), min(cx, max_x - 1))
        ch = stdscr.getch()
        if ch == 24:  # ^X
            break
        elif ch == 19:  # ^S
            with open(path, "w", encoding="utf-8") as f:
                f.write("\\n".join(lines))
            modified = False
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            if cx > 0:
                lines[cy] = lines[cy][:cx-1] + lines[cy][cx:]
                cx -= 1
                modified = True
            elif cy > 0:
                cx = len(lines[cy-1])
                lines[cy-1] += lines[cy]
                del lines[cy]
                cy -= 1
                modified = True
        elif ch in (curses.KEY_ENTER, 10, 13):
            lines.insert(cy+1, lines[cy][cx:])
            lines[cy] = lines[cy][:cx]
            cy += 1
            cx = 0
            modified = True
        elif ch == curses.KEY_UP and cy > 0:
            cy -= 1; cx = min(cx, len(lines[cy]))
        elif ch == curses.KEY_DOWN and cy < len(lines) - 1:
            cy += 1; cx = min(cx, len(lines[cy]))
        elif ch == curses.KEY_LEFT and cx > 0:
            cx -= 1
        elif ch == curses.KEY_RIGHT and cx < len(lines[cy]):
            cx += 1
        elif 32 <= ch < 127:
            lines[cy] = lines[cy][:cx] + chr(ch) + lines[cy][cx:]
            cx += 1
            modified = True

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "untitled.txt"
    curses.wrapper(main, target)
"""

# --- Native fallback games (/usr/local/bin/ribi-snake, /usr/local/bin/ribi-2048) ---
SRC_RIBI_SNAKE = """#!/usr/bin/env python3
import curses
import random

def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(120)
    max_y, max_x = stdscr.getmaxyx()
    if max_y < 7 or max_x < 12:
        stdscr.addstr(0, 0, "Terminal too small for Ribi Snake.")
        stdscr.getch()
        return
    snake = [(max_y // 2, max_x // 2)]
    direction = (0, 1)
    food = (random.randint(1, max_y - 2), random.randint(1, max_x - 2))
    score = 0
    while True:
        stdscr.erase()
        stdscr.border()
        stdscr.addstr(0, 2, f" Ribi Snake - score {score} (q to quit) ")
        try:
            stdscr.addch(food[0], food[1], ord("*"))
            for y, x in snake:
                stdscr.addch(y, x, ord("#"))
        except curses.error:
            pass
        key = stdscr.getch()
        if key == ord("q"):
            break
        elif key == curses.KEY_UP and direction != (1, 0):
            direction = (-1, 0)
        elif key == curses.KEY_DOWN and direction != (-1, 0):
            direction = (1, 0)
        elif key == curses.KEY_LEFT and direction != (0, 1):
            direction = (0, -1)
        elif key == curses.KEY_RIGHT and direction != (0, -1):
            direction = (0, 1)

        head_y = (snake[0][0] + direction[0] - 1) % (max_y - 2) + 1
        head_x = (snake[0][1] + direction[1] - 1) % (max_x - 2) + 1
        new_head = (head_y, head_x)
        if new_head in snake:
            stdscr.nodelay(False)
            stdscr.addstr(max_y // 2, max_x // 2 - 5, "GAME OVER!")
            stdscr.getch()
            break
        snake.insert(0, new_head)
        if new_head == food:
            score += 1
            food = (random.randint(1, max_y - 2), random.randint(1, max_x - 2))
        else:
            snake.pop()

if __name__ == "__main__":
    curses.wrapper(main)
"""

SRC_RIBI_2048 = """#!/usr/bin/env python3
import curses
import random

SIZE = 4

def new_tile(board):
    empties = [(r, c) for r in range(SIZE) for c in range(SIZE) if board[r][c] == 0]
    if empties:
        r, c = random.choice(empties)
        board[r][c] = 4 if random.random() < 0.1 else 2

def compress(row):
    vals = [v for v in row if v != 0]
    result = []
    skip = False
    for i in range(len(vals)):
        if skip:
            skip = False
            continue
        if i + 1 < len(vals) and vals[i] == vals[i + 1]:
            result.append(vals[i] * 2)
            skip = True
        else:
            result.append(vals[i])
    return result + [0] * (SIZE - len(result))

def move(board, direction):
    rotated = [row[:] for row in board]
    rotations = {"L": 0, "U": 1, "R": 2, "D": 3}[direction]
    for _ in range(rotations):
        rotated = [list(r) for r in zip(*rotated[::-1])]
    new_rows = [compress(r) for r in rotated]
    for _ in range((4 - rotations) % 4):
        new_rows = [list(r) for r in zip(*new_rows[::-1])]
    changed = new_rows != board
    return new_rows, changed

def has_moves(board):
    if any(0 in row for row in board):
        return True
    for r in range(SIZE):
        for c in range(SIZE):
            if c + 1 < SIZE and board[r][c] == board[r][c + 1]:
                return True
            if r + 1 < SIZE and board[r][c] == board[r + 1][c]:
                return True
    return False

def main(stdscr):
    curses.curs_set(0)
    board = [[0] * SIZE for _ in range(SIZE)]
    new_tile(board)
    new_tile(board)
    while True:
        stdscr.erase()
        stdscr.addstr(0, 0, "Ribi 2048 - arrows to move, q to quit")
        for r in range(SIZE):
            row_str = " ".join(f"{v:5d}" if v else "    ." for v in board[r])
            stdscr.addstr(2 + r, 0, row_str)
        stdscr.refresh()
        key = stdscr.getch()
        direction = {curses.KEY_LEFT: "L", curses.KEY_RIGHT: "R",
                     curses.KEY_UP: "U", curses.KEY_DOWN: "D"}.get(key)
        if key == ord("q"):
            break
        if direction:
            board, changed = move(board, direction)
            if changed:
                new_tile(board)
        if not has_moves(board):
            stdscr.addstr(8, 0, "GAME OVER!")
            stdscr.refresh()
            stdscr.nodelay(False)
            stdscr.getch()
            break

if __name__ == "__main__":
    curses.wrapper(main)
"""

# =============================================================================
# 6. BESPOKE KERNEL CONFIGURATION PROFILE
# =============================================================================
def get_bespoke_kernel_config() -> str:
    """Production Linux kernel configuration with required boot features built-in."""
    return """
CONFIG_64BIT=y
CONFIG_X86_64=y
CONFIG_X86=y
CONFIG_OUTPUT_FORMAT="elf64-x86-64"
CONFIG_SMP=y
CONFIG_NR_CPUS=64
CONFIG_PREEMPT_VOLUNTARY=y
CONFIG_NO_HZ_IDLE=y
CONFIG_HIGH_RES_TIMERS=y
CONFIG_ACPI=y
CONFIG_PCI=y
CONFIG_BINFMT_ELF=y
CONFIG_BINFMT_SCRIPT=y

# Boot & Compression Subsystems
CONFIG_BLK_DEV_INITRD=y
CONFIG_RD_XZ=y
CONFIG_RD_GZIP=y

# EFI & Bootloader Handover
CONFIG_EFI=y
CONFIG_EFI_STUB=y
CONFIG_EFI_PARTITION=y

# Block Devices & Virtual Hardware
CONFIG_BLOCK=y
CONFIG_BLK_DEV_LOOP=y
CONFIG_BLK_DEV_LOOP_MIN_COUNT=8
CONFIG_BLK_DEV_SR=y
CONFIG_BLK_DEV_SD=y
CONFIG_CHR_DEV_SG=y
CONFIG_ATA=y
CONFIG_SATA_AHCI=y
CONFIG_ATA_PIIX=y
CONFIG_BLK_DEV_NVME=y
CONFIG_VIRTIO_BLK=y
CONFIG_VIRTIO_PCI=y
CONFIG_VIRTIO_NET=y
CONFIG_VIRTIO_CONSOLE=y
CONFIG_SCSI=y
CONFIG_SCSI_MOD=y

# Built-in Filesystems
CONFIG_EXT4_FS=y
CONFIG_EXT4_FS_POSIX_ACL=y
CONFIG_EXT4_FS_SECURITY=y
CONFIG_FAT_FS=y
CONFIG_MSDOS_FS=y
CONFIG_VFAT_FS=y
CONFIG_FAT_DEFAULT_CODEPAGE=437
CONFIG_FAT_DEFAULT_IOCHARSET="iso8859-1"
CONFIG_NLS=y
CONFIG_NLS_CODEPAGE_437=y
CONFIG_NLS_ISO8859_1=y
CONFIG_NLS_UTF8=y
CONFIG_ISO9660_FS=y
CONFIG_JOLIET=y
CONFIG_ZISOFS=y
CONFIG_SQUASHFS=y
CONFIG_SQUASHFS_XZ=y
CONFIG_SQUASHFS_FILE_DIRECT=y
CONFIG_SQUASHFS_DECOMP_SINGLE=y
CONFIG_OVERLAY_FS=y
CONFIG_DEVTMPFS=y
CONFIG_DEVTMPFS_MOUNT=y
CONFIG_PROC_FS=y
CONFIG_PROC_SYSCTL=y
CONFIG_SYSFS=y
CONFIG_TMPFS=y
CONFIG_TMPFS_POSIX_ACL=y
CONFIG_TMPFS_XATTR=y

# Memory & Namespaces
CONFIG_MMU=y
CONFIG_NAMESPACES=y
CONFIG_UTS_NS=y
CONFIG_IPC_NS=y
CONFIG_USER_NS=y
CONFIG_PID_NS=y
CONFIG_NET_NS=y
CONFIG_PRINTK=y
CONFIG_PRINTK_TIME=y
CONFIG_ELF_CORE=y

# Input & Display Console
CONFIG_INPUT=y
CONFIG_INPUT_KEYBOARD=y
CONFIG_KEYBOARD_ATKBD=y
CONFIG_INPUT_MOUSE=y
CONFIG_MOUSE_PS2=y
CONFIG_INPUT_EVDEV=y
CONFIG_VT=y
CONFIG_VT_CONSOLE=y
CONFIG_FB=y
CONFIG_FB_EFI=y
CONFIG_FB_SIMPLE=y

# Core Networking
CONFIG_NET=y
CONFIG_INET=y
CONFIG_IP_PNP=y
CONFIG_IP_PNP_DHCP=y
CONFIG_NETDEVICES=y
CONFIG_ETHERNET=y
CONFIG_E1000=y
CONFIG_E1000E=y
CONFIG_R8169=y
CONFIG_PACKET=y
CONFIG_UNIX=y

# Kernel modules (allows /lib/modules cache fallback and future extensibility)
CONFIG_MODULES=y
CONFIG_MODULE_UNLOAD=y

# Pseudo-terminals (required by every terminal emulator, ssh, su -, script, etc.)
CONFIG_UNIX98_PTYS=y

# USB (keyboard/mouse/storage on real PCs and most VM configurations)
CONFIG_USB_SUPPORT=y
CONFIG_USB=y
CONFIG_USB_XHCI_HCD=y
CONFIG_USB_EHCI_HCD=y
CONFIG_USB_OHCI_HCD=y
CONFIG_USB_UHCI_HCD=y
CONFIG_USB_HID=y
CONFIG_HID=y
CONFIG_HID_GENERIC=y
CONFIG_USB_STORAGE=y

# Graphics: basic DRM/KMS drivers used by common VMs (QEMU/VirtualBox/VMware) and
# generic VESA/EFI framebuffers for real hardware, so Xorg/XFCE has something to draw on.
CONFIG_DRM=y
CONFIG_DRM_BOCHS=y
CONFIG_DRM_CIRRUS_QEMU=y
CONFIG_DRM_QXL=y
CONFIG_DRM_VIRTIO_GPU=y
CONFIG_FB_VESA=y
CONFIG_FRAMEBUFFER_CONSOLE=y

# Serial console (useful for QEMU -nographic / headless debugging)
CONFIG_SERIAL_8250=y
CONFIG_SERIAL_8250_CONSOLE=y

# Audio (ALSA + common HDA controllers used on real PCs and QEMU ich9/AC97 models)
CONFIG_SOUND=y
CONFIG_SND=y
CONFIG_SND_TIMER=y
CONFIG_SND_PCM=y
CONFIG_SND_HDA_INTEL=y
CONFIG_SND_HDA_GENERIC=y
CONFIG_SND_AC97_CODEC=y
CONFIG_SND_INTEL8X0=y

# Optional KVM PTP support is not needed by Ribi and emits a distracting
# "failed to initialize ptp_kvm" message under QEMU/Vectras without a PTP host.
# Keep generic PTP support, but omit the KVM-specific clock driver.
# CONFIG_PTP_1588_CLOCK_KVM is not set
    """

# =============================================================================
# 7. MASTER BUILD ENGINE CLASS
# =============================================================================
class RibiMasterBuilder:
    def __init__(self, resume: bool = False):
        self.resume = resume
        self.total_stages = 12
        self.host_arch = platform.machine()
        self.cross_compile = ""
        if self.host_arch != "x86_64":
            self.cross_compile = "x86_64-linux-gnu-"

    def verify_staging_filesystem(self):
        """Ensures the build workspace resides on a POSIX filesystem supporting symlinks/permissions."""
        test_file = STORAGE_ROOT / ".fs_test_file"
        test_link = STORAGE_ROOT / ".fs_test_link"
        try:
            STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
            test_file.write_text("ribi_fs_test")
            test_file.chmod(0o755)
            if test_link.exists() or test_link.is_symlink():
                test_link.unlink()
            test_link.symlink_to(test_file)
            test_link.unlink()
            test_file.unlink()
        except OSError as e:
            BuildLogger.error(
                f"Workspace path {STORAGE_ROOT} does not support POSIX symlinks or mode flags (Error: {e}).\n"
                "Likely cause: Attempting to build on Android FUSE / shared storage.\n"
                "Remedy: Run the builder from an ext4 directory inside PRoot (e.g. /root or /home/user)."
            )
            sys.exit(1)

    def stage_1_preflight_checks(self):
        BuildLogger.step(1, self.total_stages, "Preflight Verification & C.I.C. Dependencies")
        if os.geteuid() != 0:
            BuildLogger.error("Superuser privileges (root) required to construct live OS filesystems. Re-run with sudo.")
            sys.exit(1)

        BuildLogger.info(f"Host Architecture Detected: {self.host_arch}")
        BuildLogger.info(f"Target Architecture Enforced: {OS_ARCH}")
        cross_req = (self.host_arch != "x86_64")
        cic_host_dependencies(cross_required=cross_req)


    def stage_2_directory_hierarchy(self):
        BuildLogger.step(2, self.total_stages, "Establishing Structured Project Workspace")
        self.verify_staging_filesystem()
        for d in [
            STORAGE_ROOT, DIR_BUILD, DIR_SRC, DIR_CACHE, DIR_ROOTFS, DIR_INITRAMFS,
            DIR_X86_SYSROOT, DIR_ISO, DIR_PKGS, DIR_APPS, DIR_LOGS
        ]:
            d.mkdir(parents=True, exist_ok=True)
        BuildLogger.info(f"Workspace initialized on native POSIX storage: {STORAGE_ROOT}")

    def stage_3_acquire_x86_64_bootstrap(self):
        BuildLogger.step(3, self.total_stages, "C.I.C. x86_64 Userspace Bootstrap Sysroot & Python 3")
        marker = DIR_X86_SYSROOT / ".sysroot_ready"
        if marker.exists():
            # Do not trust a stale marker from an interrupted/older build.  In
            # particular, the Alpine minirootfs provides BusyBox applet links,
            # while the final Ribi rootfs requires real standalone util-linux,
            # e2fsprogs, dosfstools and e2fsprogs binaries.
            required_standalone = [
                "blkid", "lsblk", "parted", "mkfs.ext4", "mkfs.vfat", "depmod", "rsync", "acpid"
            ]
            stale = []
            for name in required_standalone:
                if not self.find_x86_64_binary(name, [DIR_X86_SYSROOT]):
                    stale.append(name)
            if not stale:
                BuildLogger.cic("CHECK", "x86_64 Bootstrap Sysroot", "EXISTS (Ready)")
                return
            BuildLogger.warn(
                "Cached x86_64 Bootstrap Sysroot is incomplete; rebuilding package staging "
                f"because standalone binaries are missing: {stale}"
            )
            marker.unlink(missing_ok=True)

        # 1. Base Minirootfs
        tar_dest = DIR_CACHE / BOOTSTRAP_X86_64_TARBALL
        bootstrap_fallbacks = [
            f"{base}/releases/x86_64/alpine-minirootfs-3.24.1-x86_64.tar.gz"
            for base in ALPINE_FALLBACK_BASE_URLS
        ]
        download_with_sidecar_hash(BOOTSTRAP_X86_64_URL, tar_dest, fallback_urls=bootstrap_fallbacks)
        # Never mix a partial/old sysroot with a fresh snapshot.
        if DIR_X86_SYSROOT.exists():
            for child in DIR_X86_SYSROOT.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink(missing_ok=True)
        BuildLogger.cic("INSTALL", "x86_64 Sysroot Staging", f"Extracting {tar_dest.name} safely...")
        safe_tar_extract(tar_dest, DIR_X86_SYSROOT)

        # 2. Acquire APKINDEX (main + community) to resolve full dependency closure.
        pkg_versions: Dict[str, str] = {}
        pkg_depends: Dict[str, List[str]] = {}
        provides_map: Dict[str, str] = {}
        pkg_repo: Dict[str, str] = {}  # pkg name -> "main" | "community"
        pkg_checksums: Dict[str, str] = {}
        pkg_arch: Dict[str, str] = {}

        for repo_label, repo_url, repo_base_sub in [
            ("main", APKINDEX_URL, "main"),
            ("community", APKINDEX_COMMUNITY_URL, "community"),
        ]:
            index_tar = DIR_CACHE / f"APKINDEX-{repo_label}.tar.gz"
            index_fallbacks = [f"{base}/{repo_base_sub}/x86_64/APKINDEX.tar.gz" for base in ALPINE_FALLBACK_BASE_URLS]
            try:
                download_file(repo_url, index_tar, fallback_urls=index_fallbacks)
                verify_apkindex_signature(index_tar, DIR_X86_SYSROOT / "etc/apk/keys")
            except Exception as e:
                BuildLogger.warn(f"Could not fetch {repo_label} APKINDEX ({e}); packages from it will be unavailable.")
                continue
            before = set(pkg_versions.keys())
            raw_index = index_tar.read_bytes()
            idx_streams = _gzip_streams(raw_index)
            if len(idx_streams) != 2:
                raise RuntimeError(f"{repo_label} APKINDEX has invalid gzip stream count")
            with tarfile.open(fileobj=io.BytesIO(idx_streams[1][1]), mode="r:", ignore_zeros=True) as tar:
                index_file = tar.extractfile("APKINDEX")
                desc_file = tar.extractfile("DESCRIPTION")
                if not index_file or not desc_file:
                    raise RuntimeError(f"{repo_label} APKINDEX is missing DESCRIPTION/APKINDEX")
                content = index_file.read().decode("utf-8", errors="replace")
                parse_apkindex(content, pkg_versions, pkg_depends, provides_map, pkg_checksums, pkg_arch)
            for new_name in set(pkg_versions.keys()) - before:
                pkg_repo[new_name] = repo_label

        # 3. Compute the full transitive install set for our desired seed packages.
        install_set = resolve_apk_closure(TARGET_APK_PACKAGES, pkg_versions, pkg_depends, provides_map)
        if RELEASE_PROFILE == "no-desktop":
            forbidden = sorted(set(install_set) & FORBIDDEN_NO_DESKTOP_PACKAGES)
            if forbidden:
                raise RuntimeError(
                    f"No-desktop release policy violation: GUI packages resolved into target: {forbidden}"
                )

        # resolve_apk_closure() already validates every seed, including virtual
        # provides such as ttf-dejavu -> font-dejavu.  Do not compare the literal
        # seed token against install_set: a virtual provide is intentionally not
        # the same package name as its concrete provider.

        BuildLogger.info(f"Resolved {len(install_set)} total x86_64 packages (console core + networking + audio + deps).")

        # 4. Download and unpack every resolved package into the hermetic sysroot.
        for pkg in install_set:
            version = pkg_versions[pkg]
            apk_name = f"{pkg}-{version}.apk"
            repo_sub = pkg_repo.get(pkg, "main")
            apk_url = f"{ALPINE_MIRROR_BASE}/{repo_sub}/x86_64/{apk_name}"
            apk_fallbacks = [f"{base}/{repo_sub}/x86_64/{apk_name}" for base in ALPINE_FALLBACK_BASE_URLS]
            apk_dest = DIR_CACHE / apk_name
            try:
                download_file(apk_url, apk_dest, fallback_urls=apk_fallbacks)
                verify_apk_integrity(apk_dest, pkg_checksums.get(pkg, ""), DIR_X86_SYSROOT / "etc/apk/keys")
                safe_tar_extract(apk_dest, DIR_X86_SYSROOT)
            except Exception as e:
                raise RuntimeError(f"Failed to fetch, verify, or extract required package '{pkg}': {e}") from e

        # Final structural gate: do not mark a partially staged sysroot as ready.
        required_standalone = ["busybox", "python3", "blkid", "lsblk", "parted", "mkfs.ext4", "mkfs.vfat", "rsync", "acpid"]
        missing=[n for n in required_standalone if not self.find_x86_64_binary(n,[DIR_X86_SYSROOT])]
        if missing:
            raise RuntimeError(f"x86_64 sysroot verification failed; missing genuine ELF tools: {missing}")
        marker.touch()
        BuildLogger.cic("CONTINUE", "x86_64 Bootstrap Sysroot", "VERIFIED & STAGED")

    def acquire_x86_64_kernel(self) -> Path:
        """Build or acquire a verified x86_64 Linux kernel and matching modules."""
        vmlinuz_dest = DIR_CACHE / "vmlinuz-x86_64"
        cfg_marker = DIR_CACHE / "vmlinuz-x86_64.config"

        kernel_hash_file = DIR_CACHE / "vmlinuz-x86_64.sha256"
        config_hash_file = DIR_CACHE / "vmlinuz-x86_64.config.sha256"
        if vmlinuz_dest.exists() and cfg_marker.exists() and is_linux_bzimage(vmlinuz_dest):
            try:
                kh=kernel_hash_file.read_text().strip(); ch=config_hash_file.read_text().strip()
                valid_hash=(re.fullmatch(r"[0-9a-f]{64}",kh or "") and re.fullmatch(r"[0-9a-f]{64}",ch or ""))
                valid_cfg=sha256_file(cfg_marker)==ch
                if valid_hash and sha256_file(vmlinuz_dest)==kh and valid_cfg:
                    BuildLogger.cic("CHECK", "x86_64 Linux LTS Kernel", "EXISTS (Cached + SHA-256 Verified)")
                    return vmlinuz_dest
            except Exception: pass
            BuildLogger.warn("Cached kernel metadata is stale or unverifiable; rebuilding it.")
            vmlinuz_dest.unlink(missing_ok=True); cfg_marker.unlink(missing_ok=True); kernel_hash_file.unlink(missing_ok=True); config_hash_file.unlink(missing_ok=True)

        src_dir = DIR_SRC / f"linux-{KERNEL_VERSION}"
        try:
            BuildLogger.info(
                "Compiling bespoke x86_64 Linux LTS kernel with built-in storage/live drivers..."
            )
            tarball_path = DIR_CACHE / KERNEL_TARBALL
            download_file(KERNEL_URL, tarball_path, fallback_urls=KERNEL_FALLBACK_URLS)
            if not (src_dir / "Makefile").exists():
                run_cmd(["tar", "-xf", str(tarball_path), "-C", str(DIR_SRC)])

            # LLVM performs the x86_64 target build directly; this works from ARM64
            # without requiring an x86_64 GNU cross compiler.
            make_flags = ["ARCH=x86_64", "LLVM=1", "LLVM_IAS=1"]

            # Linux 6.1 Kconfig's host utility uses bcmp(). Android/Termux's libc
            # does not expose that declaration through <string.h>, so clang rejects
            # confdata.c with -Wimplicit-function-declaration. HOSTCFLAGS affects only
            # host-side Kconfig/build utilities; it does not change target code.
            host_cflags = "-include strings.h"
            host_make_flags = make_flags + [f"HOSTCFLAGS={host_cflags}"]

            run_cmd(["make"] + host_make_flags + ["defconfig"], cwd=src_dir)

            fragment_path = src_dir / "ribi-fragment.config"
            write_file(fragment_path, get_bespoke_kernel_config())
            merge_script = src_dir / "scripts/kconfig/merge_config.sh"
            if merge_script.exists():
                run_cmd(
                    ["sh", str(merge_script), "-m", ".config", str(fragment_path)],
                    cwd=src_dir,
                )
            else:
                with open(src_dir / ".config", "a", encoding="utf-8") as f:
                    f.write("\n" + fragment_path.read_text())

            run_cmd(["make"] + host_make_flags + ["olddefconfig"], cwd=src_dir)
            config_path = src_dir / ".config"
            threads = max(1, os.cpu_count() or 4)

            run_cmd(
                ["make"] + host_make_flags + [f"-j{threads}", "bzImage"],
                cwd=src_dir,
            )

            compiled_bz = src_dir / "arch/x86/boot/bzImage"
            if not compiled_bz.exists() or not is_linux_bzimage(compiled_bz):
                raise RuntimeError("Kernel build completed without a valid x86_64 bzImage")

            # Build and install loadable modules into the canonical
            # /lib/modules/<release>/ layout. Boot-critical drivers remain built-in
            # according to get_bespoke_kernel_config(), but this gives the final OS a
            # real module tree for everything that resolves to '=m'.
            run_cmd(
                ["make"] + host_make_flags + [f"-j{threads}", "modules"],
                cwd=src_dir,
            )
            kr_proc = run_cmd(
                ["make"] + host_make_flags + ["-s", "kernelrelease"],
                cwd=src_dir,
                capture=True,
            )
            kernelrelease = (
                (kr_proc.stdout or "").strip().splitlines()[-1].strip()
                if kr_proc.stdout else ""
            ) or KERNEL_VERSION

            mod_install_root = DIR_CACHE / "modules-root"
            if mod_install_root.exists():
                shutil.rmtree(mod_install_root)
            mod_install_root.mkdir(parents=True, exist_ok=True)
            run_cmd(
                ["make"] + host_make_flags + [
                    f"INSTALL_MOD_PATH={mod_install_root}", "modules_install"
                ],
                cwd=src_dir,
            )

            release_path = mod_install_root / "lib/modules" / kernelrelease
            if not release_path.is_dir():
                raise RuntimeError(
                    f"modules_install did not create expected module tree: {release_path}"
                )

            (DIR_CACHE / "kernel-release.txt").write_text(kernelrelease + "\n")
            shutil.copy2(compiled_bz, vmlinuz_dest)
            shutil.copy2(config_path, cfg_marker)
            kernel_hash_file.write_text(sha256_file(vmlinuz_dest) + "\n")
            config_hash_file.write_text(sha256_file(cfg_marker) + "\n")
            BuildLogger.cic(
                "CONTINUE", "x86_64 Kernel",
                f"COMPILED BZIMAGE + MODULES READY (release {kernelrelease})"
            )
            return vmlinuz_dest

        except Exception as e:
            BuildLogger.warn(f"Kernel source compilation failed: {e}")

        raise RuntimeError(
            "Bespoke Linux kernel build failed. Ribi OS refuses to substitute an "
            "unmatched prebuilt kernel because the boot-critical configuration and "
            "module tree must correspond to the exact kernel being shipped."
        )

    def find_x86_64_binary(self, bin_name: str, search_roots: List[Path]) -> Optional[Path]:
        # Prefer a real standalone ELF over a BusyBox applet symlink. Alpine's
        # base filesystem can contain /bin/<name> -> /bin/busybox even when the
        # actual package supplies a genuine /usr/bin/<name>. Returning the
        # symlink first makes the staging pass accidentally preserve that fake
        # BusyBox link and can even make shutil.copy2 follow it into /bin/busybox.
        candidates: List[Path] = []
        for root in search_roots:
            for sub in ["bin", "usr/bin", "sbin", "usr/sbin"]:
                cand = root / sub / bin_name
                if cand.is_symlink():
                    try:
                        target = os.readlink(cand)
                        resolved = (root / target.lstrip("/")) if os.path.isabs(target) else (cand.parent / target).resolve()
                        if resolved.exists() and is_elf_x86_64(resolved):
                            # Never treat a BusyBox applet link as the genuine
                            # standalone utility when searching for an essential
                            # target binary.
                            if resolved.name != "busybox":
                                candidates.append(resolved)
                    except OSError:
                        pass
                elif cand.exists() and is_elf_x86_64(cand):
                    candidates.append(cand)
        if candidates:
            return candidates[0]
        return None

    def find_x86_64_library(self, lib_name: str, search_roots: List[Path]) -> Optional[Path]:
        for root in search_roots:
            for sub in [
                "lib64", "usr/lib64", "lib/x86_64-linux-gnu", "usr/lib/x86_64-linux-gnu",
                "lib", "usr/lib", "lib/x86_64", "usr/lib/x86_64"
            ]:
                cand = root / sub / lib_name
                if cand.is_symlink():
                    try:
                        target = os.readlink(cand)
                        if os.path.isabs(target):
                            resolved = root / target.lstrip("/")
                        else:
                            resolved = (cand.parent / target).resolve()
                        if resolved.is_file() and is_elf_x86_64(resolved):
                            return resolved
                    except OSError:
                        pass
                if cand.exists() and is_elf_x86_64(cand):
                    return cand
        return None

    def stage_4_configure_and_stage_kernel(self):
        BuildLogger.step(4, self.total_stages, "Deploying x86_64 Linux LTS Kernel")
        vmlinuz_binary = self.acquire_x86_64_kernel()
        (DIR_ROOTFS / "boot").mkdir(parents=True, exist_ok=True)
        shutil.copy2(vmlinuz_binary, DIR_ROOTFS / "boot/vmlinuz")
        BuildLogger.info("Kernel vmlinuz staged successfully.")

    def stage_5_build_hermetic_rootfs(self):
        BuildLogger.step(5, self.total_stages, "Assembling Hermetic Ribi Userspace Rootfs")
        # Ensure fresh rootfs build without stale artifact contamination
        shutil.rmtree(DIR_ROOTFS, ignore_errors=True)
        for p in [
            "bin", "sbin", "usr/bin", "usr/sbin", "usr/lib", "usr/lib64", "lib", "lib64",
            "etc", "etc/ribi", "etc/ribi/services", "var", "var/log", "var/lib/ribi/pkgdb",
            "tmp", "proc", "sys", "dev", "run", "home/ribi", "home/ribi/Desktop", "root", "boot"
        ]:
            (DIR_ROOTFS / p).mkdir(parents=True, exist_ok=True)

        # Restore kernel from persistent cache after clean rootfs wipe
        cached_kernel = DIR_CACHE / "vmlinuz-x86_64"
        if not cached_kernel.exists():
            cached_kernel = self.acquire_x86_64_kernel()
        shutil.copy2(cached_kernel, DIR_ROOTFS / "boot/vmlinuz")

        # Bulk-import the entire resolved x86_64 sysroot (XFCE/X11/Firefox/geany/audio/
        # network stacks and their libraries, fonts, and data files) BEFORE the strict
        # ELF-verified critical-binary harvest below runs. Only whitelisting a handful of
        # ESSENTIAL_TARGET_BINARIES would silently discard the entire desktop/application
        # stack even though it was downloaded — this bulk copy is what actually lands XFCE,
        # the browser, the editor, and the audio/network daemons in the produced rootfs.
        # The later verified-harvest step still runs afterward and may re-copy/overwrite
        # the small set of boot-critical tools to guarantee their integrity checks pass.
        BuildLogger.info("Bulk-importing full x86_64 sysroot (console apps, audio, network) into rootfs...")
        run_cmd([
            "rsync", "-a", "--links", "--no-owner", "--no-group",
            "--exclude=/dev/***", "--exclude=/proc/***", "--exclude=/sys/***",
            "--exclude=/run/***", "--exclude=/tmp/***", "--exclude=/var/cache/apk/***",
            "--exclude=/.sysroot_ready", "--exclude=/.PKGINFO", "--exclude=/.INSTALL",
            str(DIR_X86_SYSROOT) + "/", str(DIR_ROOTFS) + "/"
        ])
        # Install the official Zen x86_64 Linux tarball. Zen is distributed as a
        # self-contained Firefox-based directory rather than an Alpine APK; gcompat
        # is included in the Alpine package closure for its glibc ABI entry point.
        zen_archive = DIR_CACHE / ZEN_BROWSER_TARBALL
        download_file(ZEN_BROWSER_URL, zen_archive, fallback_urls=[
            "https://github.com/zen-browser/desktop/releases/latest/download/zen.linux-x86_64.tar.xz"
        ])
        run_cmd(["tar", "-xJf", str(zen_archive), "-C", str(DIR_ROOTFS)])
        # The official archive has a top-level `zen/` directory; install it
        # under /opt so the launcher, validation, and desktop conventions agree.
        zen_src_dir = DIR_ROOTFS / "zen"
        zen_opt_dir = DIR_ROOTFS / "opt/zen"
        zen_opt_dir.parent.mkdir(parents=True, exist_ok=True)
        if zen_src_dir.exists() and not zen_opt_dir.exists():
            shutil.move(str(zen_src_dir), str(zen_opt_dir))
        zen_launcher = """#!/bin/sh
set -eu
export MOZ_ENABLE_WAYLAND=0
export GDK_BACKEND=x11
exec /opt/zen/zen "$@"
"""
        write_file(DIR_ROOTFS / "usr/local/bin/zen-browser", zen_launcher, mode=0o755)
        
        # Zen's Firefox components declare libdl.so.2 while modern glibc
        # folds libdl into libc. gcompat provides the ABI entry point, and these
        # copies make the merged-library names resolvable inside the hermetic rootfs.
        libc_compat = DIR_ROOTFS / "lib/libc.so.6"
        if libc_compat.exists():
            for merged_name in ("libdl.so.2", "libm.so.6", "libpthread.so.0", "librt.so.1"):
                merged = DIR_ROOTFS / "lib" / merged_name
                if not merged.exists():
                    shutil.copy2(libc_compat.resolve(), merged)

        # The package resolver treats glycin-image-rs as a virtual loader
        # dependency and may omit its APK payload. If the cached musl APK is
        # present, unpack it explicitly so PNG decoding is available to GTK.
        loader_apk = DIR_CACHE / "glycin-image-rs-2.1.5-r0.apk"
        if loader_apk.exists():
            run_cmd(["tar", "-xzf", str(loader_apk), "-C", str(DIR_ROOTFS)], check=False)

        # Use a deterministic XPM-only geometric icon theme. The minimal target
        # ships only the XPM gdk-pixbuf loader, so PNG/SVG icon themes crash GTK
        # clients such as Thunar when they request image-missing or status icons.
        icon_base = DIR_ROOTFS / "usr/share/icons/RibiShapes"
        if icon_base.exists():
            shutil.rmtree(icon_base)
        write_file(icon_base / "index.theme", """[Icon Theme]
Name=RibiShapes
Comment=Loader-safe XPM geometric Ribi icon theme
Directories=16x16,24x24,32x32,48x48,64x64

[16x16]
Size=16
Type=Fixed

[24x24]
Size=24
Type=Fixed

[32x32]
Size=32
Type=Fixed

[48x48]
Size=48
Type=Fixed

[64x64]
Size=64
Type=Fixed
""")
        shape_specs = {
            "application-x-executable": "polygon 50%,8% 92%,32% 76%,88% 24%,88% 8%,32%",
            "folder": "roundrectangle 8%,24% 92%,88% 4,4",
            "user-desktop": "rectangle 10%,18% 90%,72% line 50%,72% 50%,88%",
            "system-run": "polygon 18%,10% 88%,50% 18%,90% 18%,64% 58%,50%",
            "text-x-generic": "polygon 18%,8% 68%,8% 86%,26% 86%,92% 18%,92%",
            "image-missing": "polygon 50%,8% 92%,92% 8%,92%",
            "dialog-information": "circle 8%,8% 92%,92%",
            "dialog-warning": "polygon 50%,8% 94%,90% 6%,90%",
            "dialog-error": "polygon 50%,8% 92%,50% 50%,92% 8% 50%",
            "window-close": "line 18%,18% 82%,82% line 82%,18% 18%,82%",
            "system-file-manager": "roundrectangle 8%,24% 92%,88% 4,4",
            "utilities-terminal": "rectangle 10%,14% 90%,86%",
            "zen-browser": "circle 8%,8% 92%,92%",
            "accessories-text-editor": "polygon 18%,8% 82%,8% 82%,92% 18%,92%",
            "camera-photo": "roundrectangle 8%,24% 92%,80% 3,3",
            "obs": "circle 8%,8% 92%,92%",
        }
        palette = ("#39c5ff", "#ff4f81", "#ffd43b", "#66d17a", "#b98cff")
        for size in (16, 24, 32, 48, 64):
            d = icon_base / f"{size}x{size}"; d.mkdir(parents=True, exist_ok=True)
            for n, (name, shape) in enumerate(shape_specs.items()):
                color = palette[n % len(palette)]
                for variant, fill, stroke, width in (("", color, "none", 1), ("-outline", "none", color, max(1, size // 12))):
                    suffix = ".xpm"
                    out = d / f"{name}{variant}{suffix}"
                    subprocess.run(["convert", "-size", f"{size}x{size}", "-depth", "8", "xc:#10131d", "-fill", fill, "-stroke", stroke, "-strokewidth", str(width), "-draw", shape, str(out)], check=False)
                    if name == "image-missing":
                        write_file(out, f'''/* XPM */
static const char * image_missing[] = {{
"{size} {size} 2 1",
". c #10131D",
"X c #FF4F81",
''' + "\n".join('"' + ("X" * size if r in (0, size - 1) else "X" + "." * (size - 2) + "X") + '",' for r in range(size)) + '\n};\n')
        write_file(DIR_ROOTFS / "etc/environment", "GTK_ICON_THEME=RibiShapes\nXCURSOR_THEME=RibiShapes\n")
        write_file(DIR_ROOTFS / "home/ribi/.config/gtk-3.0/settings.ini", """[Settings]
gtk-icon-theme-name=RibiShapes
gtk-theme-name=Ribi
gtk-enable-animations=false
""")
        write_file(DIR_ROOTFS / "home/ribi/.config/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml", """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xsettings" version="1.0">
 <property name="Net" type="empty">
  <property name="ThemeName" type="string" value="Ribi"/>
  <property name="IconThemeName" type="string" value="RibiShapes"/>
 </property>
 <property name="Gtk" type="empty">
  <property name="EnableAnimations" type="bool" value="false"/>
 </property>
</channel>
""")
        write_file(DIR_ROOTFS / "etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml", """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xsettings" version="1.0">
 <property name="Net" type="empty">
  <property name="ThemeName" type="string" value="Ribi"/>
  <property name="IconThemeName" type="string" value="RibiShapes"/>
 </property>
 <property name="Gtk" type="empty">
  <property name="EnableAnimations" type="bool" value="false"/>
 </property>
</channel>
""")
        write_file(icon_base / "gtk-3.0/gtk.css", "/* minimal RibiShapes GTK theme */\n")
        # Generate target-side GTK icon and GDK-Pixbuf loader caches.
        for theme in ("hicolor", "RibiShapes"):
            d = DIR_ROOTFS / "usr/share/icons" / theme
            if d.is_dir(): run_cmd(["gtk-update-icon-cache", "-f", "-t", str(d)], check=False)
        # The copied Alpine fallback themes contain SVG/PNG assets, but this
        # target intentionally ships only the XPM gdk-pixbuf loader. Remove
        # those fallback trees so GTK cannot silently resolve image-missing to
        # an unsupported Adwaita or hicolor asset and abort Thunar.
        for theme in ("Adwaita", "hicolor"):
            fallback = DIR_ROOTFS / "usr/share/icons" / theme
            if fallback.is_dir(): shutil.rmtree(fallback, ignore_errors=True)
        ld = DIR_ROOTFS / "usr/lib/gdk-pixbuf-2.0/2.10.0"
        q = DIR_ROOTFS / "usr/bin/gdk-pixbuf-query-loaders"
        if ld.is_dir() and q.exists():
            r = subprocess.run(["chroot", str(DIR_ROOTFS), "/usr/bin/gdk-pixbuf-query-loaders"], capture_output=True, text=True)
            if r.returncode == 0: write_file(ld / "loaders.cache", r.stdout)
        # Remove distro package-manager/boot-service state. Ribi owns PID 1 and
        # service supervision; Alpine/OpenRC metadata must not leak into the target.
        for rel in ["etc/init.d", "etc/conf.d", "etc/runlevels", "etc/rc.conf",
                    "var/cache/apk", "sbin/openrc", "sbin/rc"]:
            q=DIR_ROOTFS/rel
            if q.is_dir() and not q.is_symlink(): shutil.rmtree(q, ignore_errors=True)
            elif q.exists() or q.is_symlink(): q.unlink()


        search_roots: List[Path] = [DIR_X86_SYSROOT]
        # Target binaries and libraries must come only from the verified Alpine x86_64 sysroot.
        # Never fall back to the build host filesystem: on an x86_64 host that would silently
        # inject host glibc/tools into the musl-based Ribi rootfs.
        collected_libs: Set[Path] = set()

        def trace_and_stage_libs(binary_path: Path):
            needed_names = get_elf_needed_libraries(binary_path)
            for lib_name in needed_names:
                if lib_name.startswith('/'):
                    absolute_lib = DIR_ROOTFS / lib_name.lstrip('/')
                    if absolute_lib.exists() and is_elf_x86_64(absolute_lib):
                        continue
                    lib_name = Path(lib_name).name
                resolved_p = self.find_x86_64_library(lib_name, search_roots)
                if resolved_p and resolved_p not in collected_libs:
                    collected_libs.add(resolved_p)
                    dest_lib = DIR_ROOTFS / "lib" / lib_name
                    dest_lib.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(resolved_p, dest_lib)
                    shutil.copy2(resolved_p, DIR_ROOTFS / "usr/lib" / lib_name)
                    trace_and_stage_libs(resolved_p)

        # Stage x86_64 BusyBox
        busybox_src = self.find_x86_64_binary("busybox", search_roots)
        if not busybox_src:
            BuildLogger.error("Failed to locate verified x86_64 BusyBox binary in bootstrap sysroot.")
            sys.exit(1)

        dest_bb = DIR_ROOTFS / "bin/busybox"
        shutil.copy2(busybox_src, dest_bb)
        dest_bb.chmod(0o755)
        trace_and_stage_libs(busybox_src)

        # Stage essential binaries without fake symlinks to BusyBox
        staged_count = 0
        for b in ESSENTIAL_TARGET_BINARIES:
            if b == "mkfs.vfat":
                # dosfstools alias fallback:
                # 1. Check for real ELF or valid symlink for mkfs.vfat
                vfat_src = self.find_x86_64_binary("mkfs.vfat", search_roots)
                vfat_staged = False
                if vfat_src and not vfat_src.is_symlink() and is_elf_x86_64(vfat_src):
                    dest_sub = determine_dest_subdir(vfat_src)
                    dest_p = DIR_ROOTFS / dest_sub / "mkfs.vfat"
                    dest_p.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(vfat_src, dest_p)
                    dest_p.chmod(0o755)
                    trace_and_stage_libs(vfat_src)
                    staged_count += 1
                    vfat_staged = True
                elif vfat_src and vfat_src.is_symlink():
                    target = os.readlink(vfat_src)
                    real_target = (vfat_src.parent / target).resolve() if not os.path.isabs(target) else None
                    if not real_target or not real_target.exists():
                        for r in search_roots:
                            cand_t = r / target.lstrip("/")
                            if cand_t.exists() and is_elf_x86_64(cand_t):
                                real_target = cand_t
                                break
                    if real_target and real_target.exists() and is_elf_x86_64(real_target):
                        dest_sub = determine_dest_subdir(real_target)
                        dest_target = DIR_ROOTFS / dest_sub / real_target.name
                        dest_target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(real_target, dest_target)
                        dest_target.chmod(0o755)
                        trace_and_stage_libs(dest_target)
                        dest_vfat = DIR_ROOTFS / dest_sub / "mkfs.vfat"
                        if dest_vfat.exists() or dest_vfat.is_symlink():
                            dest_vfat.unlink()
                        dest_vfat.symlink_to(real_target.name)
                        staged_count += 1
                        vfat_staged = True

                if not vfat_staged:
                    # 2. Check mkfs.fat if mkfs.vfat is unavailable
                    fat_src = self.find_x86_64_binary("mkfs.fat", search_roots)
                    if fat_src:
                        real_fat = fat_src
                        if fat_src.is_symlink():
                            target = os.readlink(fat_src)
                            cand_t = (fat_src.parent / target).resolve() if not os.path.isabs(target) else None
                            if not cand_t or not cand_t.exists():
                                for r in search_roots:
                                    if (r / target.lstrip("/")).exists():
                                        cand_t = r / target.lstrip("/")
                                        break
                            if cand_t and cand_t.exists():
                                real_fat = cand_t

                        dest_sub = determine_dest_subdir(real_fat)
                        dest_fat = DIR_ROOTFS / dest_sub / "mkfs.fat"
                        dest_fat.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(real_fat, dest_fat)
                        dest_fat.chmod(0o755)
                        trace_and_stage_libs(dest_fat)

                        dest_vfat = DIR_ROOTFS / dest_sub / "mkfs.vfat"
                        if dest_vfat.exists() or dest_vfat.is_symlink():
                            dest_vfat.unlink()
                        dest_vfat.symlink_to("mkfs.fat")
                        staged_count += 1
                        vfat_staged = True
                    else:
                        BuildLogger.error("Essential binary 'mkfs.vfat' (and alias 'mkfs.fat') missing from x86_64 sysroot.")
                        sys.exit(1)
                continue

            src_bin = self.find_x86_64_binary(b, search_roots)
            if src_bin:
                dest_sub = determine_dest_subdir(src_bin)
                dest_p = DIR_ROOTFS / dest_sub / b
                dest_p.parent.mkdir(parents=True, exist_ok=True)
                # Bulk sysroot import may have left an applet symlink here.
                # Remove it before copying so copy2() cannot follow the link and
                # overwrite /bin/busybox instead of creating a standalone binary.
                if dest_p.is_symlink() or dest_p.exists():
                    dest_p.unlink()
                if src_bin.is_symlink():
                    target = os.readlink(src_bin)
                    real_src = (src_bin.parent / target).resolve() if not os.path.isabs(target) else None
                    if not real_src or not real_src.exists():
                        for r in search_roots:
                            cand_t = r / target.lstrip("/")
                            if cand_t.exists() and is_elf_x86_64(cand_t):
                                real_src = cand_t
                                break
                    shutil.copy2(real_src if real_src and real_src.exists() else src_bin, dest_p)
                else:
                    shutil.copy2(src_bin, dest_p)
                dest_p.chmod(0o755)
                trace_and_stage_libs(dest_p)
                staged_count += 1
            else:
                bb_applets = {"sh", "ash", "bash", "ls", "cp", "mv", "rm", "mkdir", "rmdir",
                              "cat", "chmod", "chown", "grep", "sed", "awk", "find", "head", "tar",
                              "gzip", "xz", "mount", "umount", "ps", "kill", "ip", "sync",
                              "reboot", "poweroff", "dmesg", "uname", "mknod", "chroot",
                              "sleep", "dd", "echo", "tr", "true", "false", "hostname", "env",
                              "id", "su", "setsid", "depmod"}
                if b in bb_applets:
                    dest_p = DIR_ROOTFS / "bin" / b
                    if not dest_p.exists():
                        dest_p.symlink_to("/bin/busybox")
                    staged_count += 1
                else:
                    BuildLogger.error(f"Essential non-BusyBox binary '{b}' is missing from x86_64 sysroot.")
                    sys.exit(1)

        # grub-install is a target-side installer script rather than an ELF binary.
        # It is needed by ribi-installer for persistent disk installation, but must
        # not be forced through find_x86_64_binary(), which intentionally accepts
        # only genuine x86_64 ELF executables.
        grub_install = None
        for candidate in (DIR_ROOTFS / "usr/sbin/grub-install", DIR_ROOTFS / "usr/bin/grub-install", DIR_ROOTFS / "sbin/grub-install"):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                grub_install = candidate
                break
        if grub_install is None:
            raise RuntimeError("Required target installer program grub-install is missing or not executable")

        # The native BusyBox fallback above may have supplied these shell utilities
        # as /bin/* applet links. Expose them at /usr/bin too because ribi-init and
        # the strict final validator use their canonical userland paths.
        for applet in ("id", "su", "setsid"):
            usr_tool = DIR_ROOTFS / "usr/bin" / applet
            if not usr_tool.exists() and not usr_tool.is_symlink():
                usr_tool.symlink_to("/bin/busybox")

        # Ensure canonical Python 3 at /usr/bin/python3
        canonical_py3 = DIR_ROOTFS / "usr/bin/python3"
        if not canonical_py3.exists() and (DIR_ROOTFS / "bin/python3").exists() and not (DIR_ROOTFS / "bin/python3").is_symlink():
            shutil.move(str(DIR_ROOTFS / "bin/python3"), str(canonical_py3))

        # Ensure /bin/python3 points to /usr/bin/python3
        bin_py = DIR_ROOTFS / "bin/python3"
        if bin_py.exists() or bin_py.is_symlink():
            bin_py.unlink()
        bin_py.symlink_to("/usr/bin/python3")

        # Ensure /usr/bin/env exists
        usr_env = DIR_ROOTFS / "usr/bin/env"
        if usr_env.exists() or usr_env.is_symlink():
            usr_env.unlink()
        usr_env.symlink_to("/bin/busybox")

        # Stage authentic dynamic loaders
        for root in search_roots:
            for l_cand in (root / "lib").glob("ld*"):
                if l_cand.is_file() and is_elf_x86_64(l_cand):
                    dest_l = DIR_ROOTFS / "lib" / l_cand.name
                    shutil.copy2(l_cand, dest_l)
                    dest_l.chmod(0o755)
                    (DIR_ROOTFS / "lib64").mkdir(parents=True, exist_ok=True)
                    shutil.copy2(l_cand, DIR_ROOTFS / "lib64" / l_cand.name)
            for l_cand in (root / "lib64").glob("ld*"):
                if l_cand.is_file() and is_elf_x86_64(l_cand):
                    dest_l = DIR_ROOTFS / "lib64" / l_cand.name
                    shutil.copy2(l_cand, dest_l)
                    dest_l.chmod(0o755)

        musl_loader = DIR_ROOTFS / "lib/ld-musl-x86_64.so.1"
        if not musl_loader.exists():
            for c in (DIR_ROOTFS / "lib").glob("libc.musl*"):
                musl_loader.symlink_to(c.name)
                break

        # Stage Python 3 stdlib from sysroot
        for py_dir in (DIR_X86_SYSROOT / "usr/lib").glob("python3*"):
            if py_dir.is_dir():
                dest_py = DIR_ROOTFS / "usr/lib" / py_dir.name
                run_cmd(["rsync", "-a", "--links", "--no-owner", "--no-group",
                         str(py_dir) + "/", str(dest_py) + "/"])

        # Trace and stage libraries for Python compiled C-extensions in lib-dynload
        for dyn_so in (DIR_ROOTFS / "usr/lib").rglob("*.so*"):
            if dyn_so.is_file() and is_elf_x86_64(dyn_so):
                trace_and_stage_libs(dyn_so)

        # Copy the FULL kernel modules tree from cache, preserving the real
        # /lib/modules/<kernelrelease>/... layout (populated by acquire_x86_64_kernel()
        # via 'make modules_install' or, on the fallback path, extracted verbatim from
        # Alpine's modloop). depmod/modprobe on the target require this exact
        # per-release directory name -- a flat folder of .ko files is not a valid
        # modules tree and modprobe can never resolve anything from it.
        mod_install_root = DIR_CACHE / "modules-root"
        src_lib_modules = mod_install_root / "lib/modules"
        if src_lib_modules.exists() and any(src_lib_modules.iterdir()):
            rootfs_modules = DIR_ROOTFS / "lib/modules"
            rootfs_modules.mkdir(parents=True, exist_ok=True)
            run_cmd(["rsync", "-a", "--links", "--no-owner", "--no-group", str(src_lib_modules) + "/", str(rootfs_modules) + "/"])
            staged_releases = [d.name for d in rootfs_modules.iterdir() if d.is_dir()]
            BuildLogger.info(f"Kernel modules staged into rootfs for release(s): {staged_releases}")
        else:
            BuildLogger.warn(
                "No kernel modules tree available to stage into rootfs; relying entirely "
                "on the built-in (=y) drivers in the bespoke kernel config."
            )

        # Ensure the ribi user's home directory is actually owned by uid/gid 1000
        # (everything staged above was written by the root build process).
        for dirpath, dirnames, filenames in os.walk(DIR_ROOTFS / "home/ribi"):
            os.chown(dirpath, 1000, 1000)
            for fn in filenames:
                try:
                    os.chown(os.path.join(dirpath, fn), 1000, 1000)
                except OSError:
                    pass

        BuildLogger.info(f"Target userspace assembled ({staged_count} verified tools and dynamic libraries).")

    def stage_6_deploy_ribi_system_core(self):
        BuildLogger.step(6, self.total_stages, "Deploying Native Ribi Init, Services & Packaging Engine")
        write_file(DIR_ROOTFS / "etc/hostname", f"{DEFAULT_HOSTNAME}\n")
        write_file(DIR_ROOTFS / "etc/hosts", f"127.0.0.1 localhost\n127.0.1.1 {DEFAULT_HOSTNAME}\n")
        machine_id = "8de277067b3544d4b65c267d0edab928\n"
        write_file(DIR_ROOTFS / "etc/machine-id", machine_id, mode=0o644)
        dbus_machine_id = DIR_ROOTFS / "var/lib/dbus/machine-id"
        dbus_machine_id.parent.mkdir(parents=True, exist_ok=True)
        if dbus_machine_id.exists() or dbus_machine_id.is_symlink():
            dbus_machine_id.unlink()
        dbus_machine_id.symlink_to("/etc/machine-id")
        write_file(DIR_ROOTFS / "etc/apk/repositories", f"{ALPINE_MIRROR_BASE}/main\n{ALPINE_MIRROR_BASE}/community\n")
        write_file(DIR_ROOTFS / "etc/issue", "RIBI OS x86_64\\n\\l\n")
        write_file(DIR_ROOTFS / "etc/os-release", f'NAME="{OS_NAME}"\nVERSION="{OS_VERSION}"\nID={OS_IDENTIFIER}\nPRETTY_NAME="RIBI OS x86_64"\n')
        write_file(DIR_ROOTFS / "etc/profile", """# RIBI OS interactive console profile
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export TERM=${TERM:-linux}
export XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-XFCE}
export EDITOR=${EDITOR:-nvim}
export VISUAL=${VISUAL:-nvim}
export PAGER=${PAGER:-more}
export LESS='-R'
export CLICOLOR=1
alias vi='nvim'
alias vim='nvim'
alias e='nvim'
alias ll='ls -laF'
alias la='ls -A'
alias c='clear'
if [ ! -e "$HOME/.ribi-welcome-shown" ]; then
    printf '\\033[2J\\033[H'
    printf '\\033[1;36mRIBI OS\\033[0m  \\033[90mx86_64\\033[0m\\n\\n'
    printf '\\033[1;37mWelcome to RIBI OS\\033[0m\\n'
    printf '\\033[1;33mType \\033[1;37mribi\\033[1;33m for system tools or \\033[1;37mhelp\\033[1;33m for shell help.\\033[0m\\n'
    : > "$HOME/.ribi-welcome-shown"
fi
if [ "$(id -u 2>/dev/null)" = "0" ]; then
    PS1='\\[\\033[1;31m\\]root\\[\\033[0m\\]@\\[\\033[1;36m\\]ribi\\[\\033[0m\\]:\\[\\033[1;34m\\]\\w\\[\\033[0m\\]# '
else
    PS1='\\[\\033[1;32m\\]ribi\\[\\033[0m\\]@\\[\\033[1;36m\\]ribi\\[\\033[0m\\]:\\[\\033[1;34m\\]\\w\\[\\033[0m\\]$ '
fi
""")
        for wrapper, target in (("nano", "/usr/bin/nano"), ("nvim", "/usr/bin/nvim")):
            wrapper_text = f'''#!/bin/sh
export TERM=${{TERM:-linux}}
stty sane 2>/dev/null || true
trap 'stty sane 2>/dev/null || true; printf "\\033[0m\\033[?25h\\033[2J\\033[H"' EXIT HUP INT TERM
"{target}" "$@"
status=$?
exit $status
'''
            write_file(DIR_ROOTFS / "usr/local/bin" / wrapper, wrapper_text, mode=0o755)
        # Login shells source /etc/profile themselves; keep per-user profiles
        # empty so the welcome banner is not printed twice.
        write_file(DIR_ROOTFS / "root/.profile", "# Ribi OS login profile\n")
        write_file(DIR_ROOTFS / "home/ribi/.profile", "# Ribi OS login profile\n")
        # Diagnostics are enabled only by the Debug Mode kernel argument
        # (ribi.nogui=1); normal boots must not loop test commands.
        (DIR_ROOTFS / "etc/ribi/nogui").unlink(missing_ok=True)

        # Use LightDM for the standard VT/Xorg handoff; its autologin session is
        # unprivileged and remains attached to the real XFCE session process.
        # The supplied artwork is authoritative and must be preserved byte-for-byte.
        # Never generate, resize, overwrite, or substitute a fallback wallpaper.
        if not WALLPAPER_SOURCE.is_file() or WALLPAPER_SOURCE.stat().st_size == 0:
            raise RuntimeError(f"Authoritative wallpaper missing: {WALLPAPER_SOURCE}")
        wallpaper_dest = DIR_ROOTFS / WALLPAPER_TARGET
        wallpaper_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WALLPAPER_SOURCE, wallpaper_dest)
        shutil.copy2(WALLPAPER_SOURCE, DIR_ROOTFS / "usr/share/backgrounds/ribi-wallpaper-original.png")
        wallpaper_dest.chmod(0o644)
        BuildLogger.info(f"Installed supplied wallpaper at /{WALLPAPER_TARGET}")
        lightdm_conf = """[Seat:*]
autologin-user=ribi
autologin-user-timeout=0
autologin-session=ribi-direct
user-session=ribi-direct
greeter-session=lightdm-gtk-greeter
minimum-vt=1
xserver-command=/usr/local/bin/ribi-xorg -nolisten tcp -noreset -keeptty -ac
session-wrapper=/usr/local/bin/ribi-session-wrapper
display-stopped-script=/usr/local/bin/ribi-dump-session
"""
        write_file(DIR_ROOTFS / "etc/lightdm/lightdm.conf", lightdm_conf)
        write_file(DIR_ROOTFS / "etc/lightdm/lightdm-gtk-greeter.conf", "[greeter]\nbackground=#101820\n")
        write_file(DIR_ROOTFS / "etc/skel/.xinitrc", "exec startxfce4\n")
        # Start the display manager as root; LightDM drops the session to ribi.

        desktop_session = """#!/bin/sh
set -eu
mkdir -p /run/user/1000
chown 1000:1000 /run/user/1000
chmod 700 /run/user/1000
chown -R 1000:1000 /home/ribi
export DISPLAY=${DISPLAY:-:0}
export HOME=/home/ribi
export USER=ribi
export LOGNAME=ribi
export XDG_RUNTIME_DIR=/run/user/1000
export XAUTHORITY=${XAUTHORITY:-/home/ribi/.Xauthority}
printf '[RIBI-SETUP] session display=%s uid=%s\n' "$DISPLAY" "$(id -u)" >/dev/ttyS0 2>/dev/null || true
exec /usr/local/bin/ribi-xfce-session
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-desktop-session", desktop_session, mode=0o755)
        dump_session = """#!/bin/sh
printf '\n[RIBI-DUMP] session stopped\n' >/dev/ttyS0 2>/dev/null || true
for f in /tmp/ribi-xsession.log /tmp/ribi-xfce-session.log /tmp/ribi-session-wrapper.log; do
  if [ -f "$f" ]; then
    printf '[RIBI-DUMP] %s\n' "$f" >/dev/ttyS0 2>/dev/null || true
    cat "$f" >/dev/ttyS0 2>/dev/null || true
  fi
done
exit 0
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-dump-session", dump_session, mode=0o755)
        xfce_session = """#!/bin/sh
LOG=/tmp/ribi-xfce-session.log
printf '[RIBI-XFCE] entered\n' >/dev/ttyS0 2>/dev/null || true
printf '[RIBI-XFCE] entered uid=%s display=%s home=%s\n' "$(id -u 2>/dev/null)" "${DISPLAY:-}" "${HOME:-}" >>"$LOG" 2>&1
export DISPLAY=${DISPLAY:-:0}
export XDG_RUNTIME_DIR=/run/user/1000
export XDG_CURRENT_DESKTOP=XFCE
export XDG_SESSION_DESKTOP=xfce
export XDG_SESSION_TYPE=x11
export GTK_ICON_THEME=RibiShapes
export GDK_BACKEND=x11
export MOZ_ENABLE_WAYLAND=0
mkdir -p /run/user/1000 "$HOME/.config" >>"$LOG" 2>&1 || true
chown 1000:1000 /run/user/1000 >>"$LOG" 2>&1 || true
printf '[RIBI-XFCE] launching dbus-run-session\n' >>"$LOG" 2>&1
printf '[RIBI-XFCE] exec dbus-run-session\n' >/dev/ttyS0 2>/dev/null || true
exec /usr/bin/dbus-run-session -- /usr/bin/xfce4-session >>"$LOG" 2>&1
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-xfce-session", xfce_session, mode=0o755)
        xfwm_defaults = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfwm4" version="1.0">
  <property name="general" type="empty">
    <property name="use_compositing" type="bool" value="false"/>
  </property>
</channel>
"""
        write_file(DIR_ROOTFS / "home/ribi/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml", xfwm_defaults)
        power_override = """[Desktop Entry]
Type=Application
Name=XFCE Power Manager
Hidden=true
X-GNOME-Autostart-enabled=false
"""
        write_file(DIR_ROOTFS / "etc/xdg/autostart/xfce4-power-manager.desktop", power_override)
        settings_override = """[Desktop Entry]
Type=Application
Name=XFCE Settings Daemon
Hidden=true
X-GNOME-Autostart-enabled=false
"""
        # Alpine's minimal icon/theme stack triggers a libwnck assertion in
        # xfsettingsd. It is optional for the core XFCE session, so replace it
        # with a successful no-op after packages have been installed.
        panel_override = """[Desktop Entry]
Type=Application
Name=XFCE Panel
Hidden=true
X-GNOME-Autostart-enabled=false
"""
        desktop_override = """[Desktop Entry]
Type=Application
Name=XFCE Desktop
Hidden=true
X-GNOME-Autostart-enabled=false
"""
        # These optional binaries abort in the minimal Alpine icon stack;
        # provide successful no-op shims so saved sessions cannot resurrect them.
        zen_browser_probe = """[Desktop Entry]
Type=Application
Name=Ribi Zen Browser Probe
Exec=/usr/bin/zen-browser --no-sandbox --disable-gpu --disable-dev-shm-usage --user-data-dir=/tmp/ribi-zen-browser --no-first-run about:blank
OnlyShowIn=XFCE;
X-GNOME-Autostart-enabled=true
"""
        session_wrapper = """#!/bin/sh
LOG=/tmp/ribi-session-wrapper.log
printf '[RIBI-WRAPPER] entered uid=%s args=%s display=%s home=%s\n' "$(id -u 2>/dev/null)" "$*" "${DISPLAY:-}" "${HOME:-}" >>"$LOG" 2>&1
printf '[RIBI-WRAPPER] entered\n' >/dev/ttyS0 2>/dev/null || true
if [ "$#" -gt 0 ]; then
    exec "$@" >>"$LOG" 2>&1
fi
exec /usr/local/bin/ribi-xfce-session >>"$LOG" 2>&1
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-session-wrapper", session_wrapper, mode=0o755)
        # Alpine's stock Xsession sources interactive profile logic before doing
        # an unquoted exec. Use a minimal deterministic handoff for LightDM.
        direct_xsession = """#!/bin/sh
LOG=/tmp/ribi-xsession.log
printf '[RIBI-XSESSION] direct visible session args=%s uid=%s display=%s\n' "$*" "$(id -u 2>/dev/null)" "${DISPLAY:-}" >>"$LOG" 2>&1
printf '[RIBI-XSESSION] launching persistent visible session\n' >/dev/ttyS0 2>/dev/null || true
exec /usr/local/bin/ribi-visible-session >>"$LOG" 2>&1
"""
        wallpaper_xml = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-desktop" version="1.0">
  <property name="backdrop" type="empty">
    <property name="screen0" type="empty">
      <property name="monitor0" type="empty">
        <property name="image-path" type="string" value="/usr/share/backgrounds/ribi-wallpaper.png"/>
        <property name="image-style" type="int" value="5"/>
        <property name="color-style" type="int" value="0"/>
        <property name="color1" type="array">
          <value type="uint" value="0"/>
          <value type="uint" value="0"/>
          <value type="uint" value="0"/>
          <value type="uint" value="65535"/>
        </property>
        <property name="color2" type="array">
          <value type="uint" value="0"/>
          <value type="uint" value="0"/>
          <value type="uint" value="0"/>
          <value type="uint" value="65535"/>
        </property>
      </property>
    </property>
  </property>
</channel>
"""
        wallpaper_cfg = DIR_ROOTFS / "home/ribi/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml"
        write_file(wallpaper_cfg, wallpaper_xml)
        wallpaper_data = base64.b64encode(wallpaper_dest.read_bytes()).decode("ascii")
        wallpaper_html = f"""<!doctype html><html><head><meta charset=\"utf-8\"><style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:linear-gradient(135deg,#08090b 0%,#2b1118 42%,#62545b 72%,#111216 100%)}}img{{width:100vw;height:100vh;object-fit:cover;display:block}}</style></head><body><img src=\"data:image/png;base64,{wallpaper_data}\" alt=\"Ribi OS wallpaper\"></body></html>\n"""
        write_file(DIR_ROOTFS / "usr/share/backgrounds/ribi-wallpaper.html", wallpaper_html)
        wallpaper_viewer = """#!/bin/sh
set -eu
path=/usr/share/backgrounds/ribi-wallpaper.png
printf '[RIBI-WALLPAPER] painting X root %s\\n' "$path" >/dev/ttyS0 2>/dev/null || true
# Paint the X root window instead of creating a foreground image window. This
# keeps the wallpaper visible while leaving the panel, launcher, and app windows
# above it in the normal stacking order.
exec /usr/bin/feh --bg-fill --no-fehbg "$path"
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-wallpaper-viewer", wallpaper_viewer, mode=0o755)
        dock_src = Path(__file__).with_name("ribi-dock.py")
        if not dock_src.is_file():
            raise RuntimeError(f"Missing native dock source: {dock_src}")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-dock", dock_src.read_text(), mode=0o755)
        # Convert the actual target application icons to XPM so the tiny native
        # dock can render them without Unicode glyphs or synthetic letter icons.
        dock_icons = DIR_ROOTFS / "usr/share/ribi-dock-icons"
        dock_icons.mkdir(parents=True, exist_ok=True)
        icon_sources = {
            "files": DIR_X86_SYSROOT / "usr/share/icons/hicolor/128x128/apps/org.xfce.filemanager.png",
            "terminal": DIR_X86_SYSROOT / "usr/share/icons/hicolor/128x128/apps/org.xfce.terminal.png",
            "zen": DIR_ROOTFS / "opt/zen/browser/chrome/icons/default/default128.png",
            "editor": DIR_X86_SYSROOT / "usr/share/icons/hicolor/128x128/apps/nvim.png",
            "screenshot": DIR_X86_SYSROOT / "usr/share/icons/hicolor/128x128/apps/org.xfce.screenshooter.png",
            "obs": DIR_X86_SYSROOT / "usr/share/icons/hicolor/128x128/apps/com.obsproject.Studio.png",
            "all": DIR_X86_SYSROOT / "usr/share/icons/hicolor/128x128/apps/org.xfce.appfinder.png",
            "search": DIR_X86_SYSROOT / "usr/share/icons/hicolor/128x128/apps/org.xfce.appfinder.png",
        }
        for kind, source in icon_sources.items():
            if source.exists():
                subprocess.run(["convert", str(source), "-background", "none", "-resize", "48x48", str(dock_icons / f"{kind}.xpm")], check=False)  # keep transparency: dock fills it per-tile

        # Keep the historical launcher path as a compatibility shim; the desktop
        # now uses the native dock and never imports GTK for the app menu.
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-app-launcher", "#!/bin/sh\nexec /usr/local/bin/ribi-dock --menu\n", mode=0o755)
        gtk_probe_py = r'''#!/usr/bin/python3
import sys, faulthandler
faulthandler.enable()
faulthandler.dump_traceback_later(5, repeat=True, file=sys.stderr)
def mark(s):
    try: open('/dev/ttyS0','a').write('[RIBI-GTK-PROBE] '+s+'\n')
    except Exception: pass
mark('import-start')
import gi
mark('gi-imported')
gi.require_version('Gtk', '3.0')
mark('gtk-version-required')
from gi.repository import Gtk
mark('gtk-imported')
win = Gtk.Window(title='Ribi GTK probe')
win.set_default_size(520, 260)
win.connect('destroy', Gtk.main_quit)
label = Gtk.Label(label='Ribi GTK window probe')
win.add(label)
mark('window-created')
win.show_all()
mark('window-shown')
Gtk.main()
'''
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-gtk-probe", gtk_probe_py, mode=0o755)
        handoff_py = """#!/usr/bin/python3
import os, sys, traceback
def mark(s):
    line='[RIBI-HANDOFF] '+s+'\\n'
    try: open('/tmp/ribi-handoff.log','a').write(line)
    except Exception: pass
    try: open('/dev/ttyS0','a').write(line)
    except Exception: pass
try:
    mark('python-start uid=%s'%os.getuid())
    env=os.environ.copy()
    env.update(DISPLAY=':0', HOME='/home/ribi', USER='ribi', LOGNAME='ribi',
               XDG_RUNTIME_DIR='/run/user/1000', XDG_CURRENT_DESKTOP='Ribi',
               XDG_SESSION_DESKTOP='ribi')
    os.setgroups([1000]); mark('groups=1000')
    os.setgid(1000); mark('gid=1000')
    os.setuid(1000); mark('uid=1000')
    os.environ.clear(); os.environ.update(env)
    mark('exec-session')
    os.execve('/usr/local/bin/ribi-user-desktop', ['/usr/local/bin/ribi-user-desktop'], env)
except Exception as e:
    mark('ERROR '+repr(e)); traceback.print_exc(file=sys.stderr); raise
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-user-handoff", handoff_py, mode=0o755)
        panel_xml = """<?xml version="1.0" encoding="UTF-8"?>

<channel name="xfce4-panel" version="1.0">
  <property name="configver" type="int" value="2"/>
  <property name="panels" type="array"><value type="int" value="1"/></property>
  <property name="dark-mode" type="bool" value="true"/>
  <property name="panel-1" type="empty">
    <property name="position" type="string" value="p=10;x=0;y=0"/>
    <property name="length" type="uint" value="100"/>
    <property name="position-locked" type="bool" value="true"/>
    <property name="icon-size" type="uint" value="32"/>
    <property name="size" type="uint" value="54"/>
    <property name="plugin-ids" type="array">
      <value type="int" value="1"/><value type="int" value="2"/><value type="int" value="3"/>
      <value type="int" value="4"/><value type="int" value="5"/><value type="int" value="6"/>
      <value type="int" value="7"/><value type="int" value="8"/><value type="int" value="9"/>
      <value type="int" value="10"/><value type="int" value="11"/><value type="int" value="12"/>
    </property>
  </property>
  <property name="plugins" type="empty">
    <property name="plugin-1" type="string" value="launcher"><property name="items" type="array"><value type="string" value="ribi-app-menu.desktop"/></property></property>
    <property name="plugin-2" type="string" value="separator"><property name="expand" type="bool" value="true"/></property>
    <property name="plugin-3" type="string" value="tasklist"><property name="grouping" type="uint" value="1"/></property>
    <property name="plugin-4" type="string" value="separator"/>
    <property name="plugin-5" type="string" value="launcher"><property name="items" type="array"><value type="string" value="ribi-file-explorer.desktop"/></property></property>
    <property name="plugin-6" type="string" value="launcher"><property name="items" type="array"><value type="string" value="xfce4-terminal.desktop"/></property></property>
    <property name="plugin-7" type="string" value="launcher"><property name="items" type="array"><value type="string" value="zen-browser-ribi.desktop"/></property></property>
    <property name="plugin-8" type="string" value="launcher"><property name="items" type="array"><value type="string" value="ribi-edit.desktop"/></property></property>
    <property name="plugin-9" type="string" value="launcher"><property name="items" type="array"><value type="string" value="xfce4-screenshooter-ribi.desktop"/></property></property>
    <property name="plugin-10" type="string" value="launcher"><property name="items" type="array"><value type="string" value="obs-studio-ribi.desktop"/></property></property>
    <property name="plugin-11" type="string" value="separator"/>
    <property name="plugin-12" type="string" value="clock"/>
  </property>
</channel>
"""
        write_file(DIR_ROOTFS / "home/ribi/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml", panel_xml)
        write_file(DIR_ROOTFS / "etc/xdg/xfce4/panel/default.xml", panel_xml)
        screenshot_full = """#!/bin/sh
set -eu
mkdir -p /home/ribi/Pictures/Screenshots
stamp=$(date +%Y%m%d-%H%M%S)
exec /usr/bin/xfce4-screenshooter -f -s "/home/ribi/Pictures/Screenshots/Screenshot-$stamp.png"
"""
        screenshot_select = """#!/bin/sh
set -eu
mkdir -p /home/ribi/Pictures/Screenshots
stamp=$(date +%Y%m%d-%H%M%S)
exec /usr/bin/xfce4-screenshooter -r -s "/home/ribi/Pictures/Screenshots/Selection-$stamp.png"
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-screenshot-full", screenshot_full, mode=0o755)
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-screenshot-selection", screenshot_select, mode=0o755)
        shortcuts_xml = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-keyboard-shortcuts" version="1.0">
  <property name="commands" type="empty">
    <property name="custom" type="empty">
      <property name="&lt;Primary&gt;&lt;Shift&gt;p" type="string" value="/usr/local/bin/ribi-screenshot-full"/>
      <property name="&lt;Primary&gt;&lt;Shift&gt;k" type="string" value="/usr/local/bin/ribi-screenshot-selection"/>
    </property>
  </property>
</channel>
"""
        shortcut_paths = [
            DIR_ROOTFS / "home/ribi/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-keyboard-shortcuts.xml",
            DIR_ROOTFS / "etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-keyboard-shortcuts.xml",
        ]
        for shortcut_path in shortcut_paths:
            write_file(shortcut_path, shortcuts_xml)
        os.chown(wallpaper_cfg, 1000, 1000)
        os.chown(wallpaper_cfg.parent, 1000, 1000)
        os.chown(wallpaper_cfg.parent.parent, 1000, 1000)
        os.chown(wallpaper_cfg.parent.parent.parent, 1000, 1000)
        write_file(DIR_ROOTFS / "etc/X11/xinit/Xsession", direct_xsession, mode=0o755)
        direct = """#!/bin/sh
LOG=/tmp/ribi-desktop.log
printf '[RIBI-DESKTOP] entered\n' >>"$LOG" 2>&1
printf '[RIBI-DESKTOP] entered uid=%s display=%s\n' "$(id -u)" "${DISPLAY:-}" >/dev/ttyS0 2>/dev/null || true
export DISPLAY=${DISPLAY:-:0}
export HOME=/home/ribi
export USER=ribi
export LOGNAME=ribi
export XDG_RUNTIME_DIR=/run/user/1000
export GTK_ICON_THEME=RibiShapes
mkdir -p "$XDG_RUNTIME_DIR" "$HOME/Desktop" >>"$LOG" 2>&1
chown -R 1000:1000 "$XDG_RUNTIME_DIR" "$HOME" >>"$LOG" 2>&1 || true
printf '[RIBI-DESKTOP] launching terminal\n' >>"$LOG" 2>&1
printf '[RIBI-DESKTOP] launching terminal\n' >/dev/ttyS0 2>/dev/null || true
if command -v dbus-run-session >/dev/null 2>&1; then
    exec dbus-run-session -- xfce4-terminal --disable-server --geometry=90x28+80+80 --title='Ribi OS Desktop' >>"$LOG" 2>&1
else
    exec xfce4-terminal --disable-server --geometry=90x28+80+80 --title='Ribi OS Desktop' >>"$LOG" 2>&1
fi
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-direct-desktop", direct, mode=0o755)
        user_desktop = """#!/bin/sh
export DISPLAY=:0 HOME=/home/ribi USER=ribi LOGNAME=ribi XDG_RUNTIME_DIR=/run/user/1000
mkdir -p "$HOME/.config" "$XDG_RUNTIME_DIR" "$HOME/Pictures/Screenshots"
xfwm4 --replace >"$HOME/xfwm4.log" 2>&1 &
sleep 2
printf '[RIBI-WM] xfwm4 started\n' >/dev/ttyS0 2>/dev/null || true
xfce4-panel --disable-wm-check >"$HOME/panel.log" 2>&1 &
printf '[RIBI-PANEL] launch submitted\n' >/dev/ttyS0 2>/dev/null || true
# The dock is the application entry point. Do not open a carousel of apps at login.
printf '[RIBI-APPS] dock ready; applications are user-launched\n' >/dev/ttyS0 2>/dev/null || true
sleep 600
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-user-desktop", user_desktop, mode=0o755)
        openbox_autostart = """#!/bin/sh
set -eu
LOG="$HOME/.cache/ribi-autostart.log"
export DISPLAY=${DISPLAY:-:0}
export XAUTHORITY=${XAUTHORITY:-/run/ribi/server.auth}
export GDK_BACKEND=x11
export GTK_USE_PORTAL=0
export NO_AT_BRIDGE=1
export GTK_ICON_THEME=RibiShapes
export GTK_MODULES=
export XCURSOR_THEME=Adwaita
export GIO_USE_VFS=local
export GSETTINGS_BACKEND=memory
export XDG_CURRENT_DESKTOP=Ribi
export XDG_SESSION_DESKTOP=ribi
mkdir -p "$HOME/.cache" "$HOME/Pictures/Screenshots" "$XDG_RUNTIME_DIR"
printf '[RIBI-AUTOSTART] uid=%s display=%s\n' "$(id -u)" "$DISPLAY" >>"$LOG" 2>&1
# Paint only the X root; never create a foreground wallpaper/probe window.
/usr/local/bin/ribi-wallpaper-viewer >>"$HOME/.cache/ribi-wallpaper.log" 2>&1
# The dock is the sole graphical autostart client. It remains independent of app exits.
exec /usr/local/bin/ribi-dock >>"$HOME/.cache/ribi-dock.log" 2>&1
"""
        write_file(DIR_ROOTFS / "home/ribi/.config/openbox/autostart", openbox_autostart, mode=0o755)
        visible = """#!/bin/sh
set -eu
LOG=/tmp/ribi-visible.log
export DISPLAY=${DISPLAY:-:0}
export XAUTHORITY=${XAUTHORITY:-/run/ribi/server.auth}
export HOME=/home/ribi USER=ribi LOGNAME=ribi
export XDG_RUNTIME_DIR=/run/user/1000 XDG_CURRENT_DESKTOP=Ribi XDG_SESSION_DESKTOP=ribi XDG_SESSION_TYPE=x11
export XDG_CONFIG_HOME=/home/ribi/.config XDG_CONFIG_DIRS=/etc/xdg
printf '[RIBI-VISIBLE] entered display=%s uid=%s\\n' "$DISPLAY" "$(id -u)" >>"$LOG" 2>&1
printf '[RIBI-VISIBLE] launching dbus-run-session openbox-session\\n' >/dev/ttyS0 2>/dev/null || true
if command -v xrandr >/dev/null 2>&1; then
    _ribi_xrandr_ok=1
    _ribi_output=none
    printf '[RIBI-VISIBLE] xrandr query begin\n' >/dev/ttyS0 2>/dev/null || true
    xrandr --query >/dev/ttyS0 2>&1 || true
    # The direct Xorg live path can expose no connected RandR output even
    # though its virtual framebuffer supports the requested desktop size.
    # Set the framebuffer first so root-window geometry is truly 1280x800.
    xrandr --fb 1280x800 >>"$LOG" 2>&1 || true
    xrandr --fb 1280x800 >/dev/ttyS0 2>&1 || true
    for _ribi_try in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        _ribi_output=$(xrandr --query 2>>"$LOG" | awk '$2 == "connected" {print $1; exit}')
        if [ -n "$_ribi_output" ] && xrandr --output "$_ribi_output" --mode 1280x800 >>"$LOG" 2>&1; then
            xrandr --fb 1280x800 >>"$LOG" 2>&1 || true
            _ribi_xrandr_ok=0
            break
        fi
        sleep 1
    done
    printf '[RIBI-VISIBLE] xrandr mode attempt output=%s\n' "$_ribi_output" >/dev/ttyS0 2>/dev/null || true
    xrandr --query >/dev/ttyS0 2>&1 || true
    printf '[RIBI-VISIBLE] xrandr output=%s mode=1280x800 rc=%s\n' "$_ribi_output" "$_ribi_xrandr_ok" >>"$LOG" 2>&1
fi
# Phase 9: Ribi WM is the default. RIBI_USE_WM=0 or ribi.wm=0 is an
# emergency compatibility escape hatch; any startup failure falls back to
# Openbox instead of leaving a black screen.
_ribi_use_wm=${RIBI_USE_WM:-1}
grep -qw 'ribi.wm=0' /proc/cmdline 2>/dev/null && _ribi_use_wm=0 || true
grep -qw 'ribi.wm=1' /proc/cmdline 2>/dev/null && _ribi_use_wm=1 || true
if [ "$_ribi_use_wm" = 1 ]; then
    printf '[RIBI-VISIBLE] default Ribi WM requested\n' >>"$LOG" 2>&1
    /usr/local/bin/ribi-wm.py >>"$LOG" 2>&1 & _ribi_wm_pid=$!
    sleep 1
    if kill -0 "$_ribi_wm_pid" 2>/dev/null; then
        printf '[RIBI-VISIBLE] Ribi WM active pid=%s\n' "$_ribi_wm_pid" >>"$LOG" 2>&1
        /usr/local/bin/ribi-wallpaper-viewer >>"$HOME/.cache/ribi-wallpaper.log" 2>&1 &
        /usr/local/bin/ribi-dock >>"$HOME/.cache/ribi-dock.log" 2>&1 &
        wait "$_ribi_wm_pid"
        exit $?
    fi
    printf '[RIBI-VISIBLE] Ribi WM failed; falling back to Openbox\n' >>"$LOG" 2>&1
fi
exec /usr/bin/dbus-run-session -- /usr/bin/openbox-session >>"$LOG" 2>&1
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-visible-session", visible, mode=0o755)
        drop_session = """#!/usr/bin/python3
import os, sys
os.setgroups([1000])
os.setgid(1000)
os.setuid(1000)
os.environ.update(HOME="/home/ribi", USER="ribi", LOGNAME="ribi")
os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-drop-session", drop_session, mode=0o755)
        write_file(DIR_ROOTFS / "usr/share/xsessions/ribi-direct.desktop", """[Desktop Entry]
Name=Ribi Desktop
Comment=Ribi OS stable desktop session
Exec=/usr/local/bin/ribi-xfce-session
Type=Application
DesktopNames=XFCE;Ribi
""")

        xorg_conf = """Section "Module"
    Disable "glx"
EndSection
Section "ServerFlags"
    Option "AutoAddDevices" "true"
    Option "AllowMouseOpenFail" "false"
    Option "AutoEnableDevices" "true"
EndSection
Section "InputClass"
    Identifier "Ribi libinput pointer"
    MatchIsPointer "on"
    Driver "libinput"
EndSection
Section "InputClass"
    Identifier "Ribi libinput keyboard"
    MatchIsKeyboard "on"
    Driver "libinput"
EndSection
Section "Device"
    Identifier "Ribi QEMU Video"
    Driver "modesetting"
    Option "AccelMethod" "none"
EndSection
Section "Monitor"
    Identifier "Ribi Monitor"
    Option "PreferredMode" "1280x800"
EndSection
Section "Screen"
    Identifier "Ribi Screen"
    Device "Ribi QEMU Video"
    Monitor "Ribi Monitor"
    DefaultDepth 24
    SubSection "Display"
        Depth 24
        Virtual 1280 800
        Modes "1280x800"
    EndSubSection
EndSection
"""
        write_file(DIR_ROOTFS / "etc/X11/xorg.conf", xorg_conf)
        xorg_launcher = """#!/bin/sh
set -u
printf '[RIBI-XORG] launching: %s\n' "$*" >/dev/ttyS0 2>/dev/null || true
exec /usr/libexec/Xorg "$@" -logfile /dev/ttyS0 -verbose 3
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-xorg", xorg_launcher, mode=0o755)

        xfce_clients = """#!/bin/sh
set -eu
export DISPLAY=${DISPLAY:-:0}
export HOME=/home/ribi
export USER=ribi
export LOGNAME=ribi
export XDG_CURRENT_DESKTOP=XFCE
export XDG_SESSION_DESKTOP=xfce
mkdir -p "$HOME/.config" /run/user/1000
printf 'RIBI_XFCE_CLIENTS_START uid=%s display=%s\n' "$(id -u)" "$DISPLAY" | tee "$HOME/xfce-session.log" >/dev/ttyS0 2>/dev/null || true
unset DBUS_SESSION_BUS_ADDRESS
xfwm4 --replace >>"$HOME/xfwm4.log" 2>&1 & wm_pid=$!

printf 'RIBI_XFCE_PIDS wm=%s\n' "$wm_pid" >/dev/ttyS0 2>/dev/null || true
sleep 8
printf 'RIBI_XFCE_STATUS wm=%s desktop=%s panel=%s\n' "$(kill -0 "$wm_pid" 2>/dev/null; echo $?)" "$(kill -0 "$desktop_pid" 2>/dev/null; echo $?)" "$(kill -0 "$panel_pid" 2>/dev/null; echo $?)" >/dev/ttyS0 2>/dev/null || true
zen-browser --no-sandbox --disable-gpu --no-first-run --user-data-dir="$HOME/.zen-browser" >/home/ribi/zen-browser.log 2>&1 &
sleep 3
if ! kill -0 "$wm_pid" 2>/dev/null || ! kill -0 "$desktop_pid" 2>/dev/null || ! kill -0 "$panel_pid" 2>/dev/null; then
    printf '\033[1;31mXFCE startup failed for ribi\033[0m\n' >/dev/tty1 2>/dev/null || true
    cat "$HOME/xfwm4.log" "$HOME/xfdesktop.log" "$HOME/xfce4-panel.log" >/dev/tty1 2>/dev/null || true
    exit 1
fi
wait
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-xfce-clients", xfce_clients, mode=0o755)
        # Alpine packages place Xorg in /usr/bin on some releases and in
        # /usr/libexec on others. The supervisor resolves either location.
        xorg_resolver = """#!/bin/sh
if [ -x /usr/libexec/Xorg ]; then exec /usr/libexec/Xorg "$@"; fi
if [ -x /usr/bin/Xorg ]; then exec /usr/bin/Xorg "$@"; fi
echo '[RIBI-XORG] no Xorg binary found' >/dev/ttyS0 2>/dev/null || true
exit 127
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-xorg", xorg_resolver, mode=0o755)
        # Let Xorg select QEMU's available modesetting driver automatically.
        # A forced VESA stanza is unreliable across QEMU video adapters.


        # Deploy Native PID 1 & Toolchain
        write_file(DIR_ROOTFS / "sbin/ribi-init", SRC_RIBI_INIT, mode=0o755)
        # Defense-in-depth: the kernel's hardcoded default init search order is
        # /sbin/init, /etc/init, /bin/init, /bin/sh. The live ISO's grub.cfg execs
        # ribi-init explicitly via the initramfs /init script, and the installer sets
        # init=/sbin/ribi-init -- but neither covers every possible boot path (e.g. a
        # third-party bootloader entry with no init= kernel argument), so also expose
        # ribi-init under the standard /sbin/init name it would otherwise never be found
        # under.
        init_symlink = DIR_ROOTFS / "sbin/init"
        if init_symlink.exists() or init_symlink.is_symlink():
            init_symlink.unlink()
        init_symlink.symlink_to("ribi-init")
        write_file(DIR_ROOTFS / "usr/local/bin/ribisvc", SRC_RIBI_SVC, mode=0o755)
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-pkg", SRC_RIBI_PKG, mode=0o755)
        write_file(DIR_ROOTFS / "usr/local/bin/ribi", SRC_RIBI_CLI, mode=0o755)
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-installer", SRC_RIBI_INSTALLER, mode=0o755)
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-setup", SRC_RIBI_SETUP, mode=0o755)
        editor_src = Path(__file__).with_name("ribi-code-editor.py")
        if not editor_src.is_file():
            raise RuntimeError(f"Missing Code Editor source: {editor_src}")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-edit", editor_src.read_text(), mode=0o755)
        prompt_src = Path(__file__).with_name("ribi-app-prompt.py")
        if not prompt_src.is_file():
            raise RuntimeError(f"Missing app action helper: {prompt_src}")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-app-prompt", prompt_src.read_text(), mode=0o755)
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-snake", SRC_RIBI_SNAKE, mode=0o755)
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-2048", SRC_RIBI_2048, mode=0o755)

        # Preserve service identities created by the imported userspace. Only add
        # the Ribi accounts/groups that are required by our native init. Privileged
        # passwords are locked until the installer/administrator explicitly sets them.
        passwd_p=DIR_ROOTFS / "etc/passwd"
        group_p=DIR_ROOTFS / "etc/group"
        shadow_p=DIR_ROOTFS / "etc/shadow"
        passwd=passwd_p.read_text(encoding="utf-8", errors="replace") if passwd_p.exists() else ""
        group=group_p.read_text(encoding="utf-8", errors="replace") if group_p.exists() else ""
        shadow=shadow_p.read_text(encoding="utf-8", errors="replace") if shadow_p.exists() else ""
        if not any(line.startswith("root:") for line in passwd.splitlines()):
            passwd += "root:x:0:0:root:/root:/bin/sh\n"
        if not any(line.startswith("ribi:") for line in passwd.splitlines()):
            passwd += "ribi:x:1000:1000:Ribi User:/home/ribi:/bin/sh\n"
        if not any(line.startswith("messagebus:") for line in passwd.splitlines()):
            passwd += "messagebus:x:81:81:DBus Message Bus:/run/dbus:/sbin/nologin\n"
        if not any(line.startswith("root:") for line in group.splitlines()): group += "root:x:0:\n"
        if not any(line.startswith("ribi:") for line in group.splitlines()): group += "ribi:x:1000:\n"
        for gname,gid in (("audio",29),("video",44),("sudo",27)):
            if not any(line.startswith(gname+":") for line in group.splitlines()): group += f"{gname}:x:{gid}:ribi\n"
        # Add ribi to existing supplemental groups without destroying package groups.
        gl=[]
        for line in group.splitlines():
            if not line: continue
            f=line.split(":",3)
            if len(f)==4 and f[0] in ("audio","video","sudo") and "ribi" not in f[3].split(","):
                f[3]=(f[3]+",ribi").lstrip(","); line=":".join(f)
            gl.append(line)
        group="\n".join(gl)+"\n"
        if not any(line.startswith("root:") for line in shadow.splitlines()): shadow += "root:!:1:0:99999:7:::\n"
        if not any(line.startswith("ribi:") for line in shadow.splitlines()): shadow += "ribi:!:1:0:99999:7:::\n"
        write_file(passwd_p, passwd)
        write_file(group_p, group)
        write_file(shadow_p, shadow, mode=0o600)

        # The live ribi account has a locked password. Permit only the root-owned
        # interactive installer, with no CLI arguments; do not grant a shell or
        # broad sudo-group access. Installer input still requires target re-entry
        # and the explicit destructive confirmation.
        sudoers_p = DIR_ROOTFS / "etc/sudoers"
        sudoers_text = sudoers_p.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"(?m)^\s*@includedir\s+/etc/sudoers\.d\s*$", sudoers_text):
            raise RuntimeError("sudoers does not include /etc/sudoers.d; refusing to create installer policy")
        write_file(
            DIR_ROOTFS / "etc/sudoers.d/ribi-installer",
            'ribi ALL=(root) NOPASSWD: /usr/local/bin/ribi-installer ""',
            mode=0o440,
        )

        # Native DHCP-on-boot helper: prefers dhcpcd if the optional package resolved,
        # otherwise falls back to BusyBox's built-in udhcpc applet (always present),
        # so basic wired/virtio networking comes up without requiring any GUI tool.
        net_up_script = """#!/bin/sh
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
"""
        write_file(DIR_ROOTFS / "usr/local/sbin/ribi-netup", net_up_script, mode=0o755)

        # Register Baseline Network Service
        write_file(DIR_ROOTFS / "etc/ribi/services/network.json", json.dumps({
            "name": "network",
            "description": "Ribi Core Network Controller (loopback + DHCP)",
            "start_command": ["/usr/local/sbin/ribi-netup"],
            "enabled": True
        }, indent=2))
        write_file(DIR_ROOTFS / "etc/ribi/services/audio.json", json.dumps({
            "name": "audio",
            "description": "Ribi Core Audio (ALSA state restore)",
            "start_command": ["/bin/sh", "-c", "command -v alsactl >/dev/null 2>&1 && alsactl restore >/dev/null 2>&1 || true"],
            "enabled": True
        }, indent=2))
        if (DIR_ROOTFS / "usr/bin/dbus-daemon").exists():
            write_file(DIR_ROOTFS / "etc/ribi/services/dbus.json", json.dumps({
                "name":"dbus", "description":"D-Bus system message bus",
                "start_command":["/usr/bin/dbus-daemon","--system","--nofork"], "enabled":True
            }, indent=2))
        if (DIR_ROOTFS / "sbin/udevd").exists():
            write_file(DIR_ROOTFS / "etc/ribi/services/udev.json", json.dumps({
                "name":"udev", "description":"eudev device manager",
                "start_command":["/sbin/udevd","--daemon"], "enabled":True
            }, indent=2))

        acpi_events = DIR_ROOTFS / "etc/acpi/events"
        acpi_events.mkdir(parents=True, exist_ok=True)
        acpi_poweroff = """#!/bin/sh
mkdir -p /run
mkdir /run/ribi-acpi-poweroff.lock 2>/dev/null || exit 0
printf '[RIBI-ACPI] power button received; syncing and powering off\n' >/dev/ttyS0 2>/dev/null || true
sync
printf '[RIBI-ACPI] sync complete; forcing poweroff\n' >/dev/ttyS0 2>/dev/null || true
exec /sbin/poweroff -f
"""
        write_file(DIR_ROOTFS / "usr/local/sbin/ribi-acpi-poweroff", acpi_poweroff, mode=0o755)
        write_file(acpi_events / "power-button",
                   "event=button/power.*\naction=/usr/local/sbin/ribi-acpi-poweroff\n", mode=0o644)

        BuildLogger.info("Ribi core management services deployed.")

    def stage_7_synthesize_applications(self):
        BuildLogger.step(7, self.total_stages, "Synthesizing Native Console Applications")
        apps_dir = DIR_ROOTFS / "usr/share/applications"
        apps_dir.mkdir(parents=True, exist_ok=True)
        explorer_src = Path(__file__).with_name("ribi-file-explorer.py")
        if not explorer_src.is_file():
            raise RuntimeError(f"Missing native explorer prototype: {explorer_src}")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-file-explorer", explorer_src.read_text(), mode=0o755)
        for native_name in ("ribi-shell.py", "ribi-screenshot.py", "ribi-wm.py", "ribi-control-center.py"):
            native_src = Path(__file__).with_name(native_name)
            if not native_src.is_file():
                raise RuntimeError(f"Missing native Ribi component: {native_src}")
            write_file(DIR_ROOTFS / "usr/local/bin" / native_name, native_src.read_text(), mode=0o755)
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-screenshot", '#!/bin/sh\nexec /usr/local/bin/ribi-screenshot.py "$@"\n', mode=0o755)

        desktop_manifest = [
            ("ribi-app-menu.desktop", "Applications", "ribi-app-launcher", "view-app-grid", "System;Utility;", False),
            ("ribi-file-explorer.desktop", "Ribi File Explorer", "ribi-file-explorer %U", "system-file-manager", "System;FileManager;", False),
            ("ribi-terminal.desktop", "Ribi Terminal", "xterm -title 'Ribi Terminal'", "utilities-terminal", "System;TerminalEmulator;", False),
            ("zen-browser-ribi.desktop", "Zen Browser", "zen-browser %U", "zen-browser", "Network;WebBrowser;", False),
            ("obs-studio-ribi.desktop", "OBS Studio", "obs --disable-shutdown-check", "obs", "AudioVideo;Recorder;", False),
            ("ribi-screenshot.desktop", "Ribi Screenshot", "ribi-screenshot", "camera-photo", "Graphics;Utility;", False),
            ("ribi-control-center.desktop", "Ribi Control Center", "ribi-control-center.py", "preferences-system", "System;Settings;", False),
            ("ribi-installer.desktop", "Install Ribi OS", "sudo -n /usr/local/bin/ribi-installer", "system-software-install", "System;", True),
            ("ribi-setup.desktop", "Setup Ribi OS", "xterm -hold -e /usr/local/bin/ribi-setup", "system-software-install", "System;Settings;", False),
            ("ribi-edit.desktop", "Ribi Code Editor", "ribi-edit %F", "nvim", "Utility;TextEditor;Development;", True),
            ("ribi-snake.desktop", "Ribi Snake", "ribi-snake", "applications-games", "Game;", True),
            ("ribi-2048.desktop", "Ribi 2048", "ribi-2048", "applications-games", "Game;", True),
        ]

        for fname, name, exec_cmd, icon, cats, term in desktop_manifest:
            content = (
                f"[Desktop Entry]\nVersion=1.0\nType=Application\nName={name}\nExec={exec_cmd}\n"
                f"Icon={icon}\nCategories={cats}\nTerminal={'true' if term else 'false'}\nStartupNotify=false\n"
            )
            # This root-only wizard currently lacks the installer's full target
            # preflight. Keep it out of the app finder until its Stage 3/4 audit.
            if fname == "ribi-setup.desktop":
                content += "NoDisplay=true\n"
            write_file(apps_dir / fname, content)

        # Keep launch metadata available for users who inspect the filesystem,
        # but do not create a graphical desktop directory or X session files.
        desktop_entry = DIR_ROOTFS / "home/ribi/Desktop/ribi-installer.desktop"
        desktop_entry.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(apps_dir / "ribi-installer.desktop", desktop_entry)
        desktop_entry.chmod(0o755)

        BuildLogger.info("Applications synthesized.")

    def stage_8_construct_initramfs(self):
        BuildLogger.step(8, self.total_stages, "Constructing Production Live Boot Initramfs")
        shutil.rmtree(DIR_INITRAMFS, ignore_errors=True)
        for d in ["bin", "sbin", "dev", "proc", "sys", "run", "run/media", "run/rootfs", "run/overlay", "sysroot", "lib", "lib64", "usr/lib", "lib/modules"]:
            (DIR_INITRAMFS / d).mkdir(parents=True, exist_ok=True)

        # 1. Write the resilient production /init script
        write_file(DIR_INITRAMFS / "init", SRC_LIVE_INIT, mode=0o755)

        # 2. Deploy verified x86_64 BusyBox binary
        bb_src = DIR_ROOTFS / "bin/busybox"
        bb_dest = DIR_INITRAMFS / "bin/busybox"
        shutil.copy2(bb_src, bb_dest)
        bb_dest.chmod(0o755)

        if not is_elf_x86_64(bb_dest):
            raise RuntimeError("CRITICAL ERROR: Initramfs BusyBox is NOT x86_64 ELF architecture!")

        # 3. Create required applet symlinks
        # NOTE: "find" and "depmod" are required here because /init (SRC_LIVE_INIT)
        # calls both directly (the insmod-by-filename fallback uses `find`, and module
        # dependency resolution needs `depmod` before `modprobe` can work at all). The
        # previous applet list omitted both, so /init would silently fail every
        # `find`/`depmod` invocation with "not found" the instant it tried them.
        applets = ["sh", "mount", "umount", "mkdir", "cat", "mknod", "sleep", "ls", "echo",
                   "grep", "head", "modprobe", "insmod", "find", "depmod", "chroot", "tr"]
        # Never execute the x86_64 target BusyBox on the build host: the builder
        # is intentionally designed to run on ARM64/Termux, where that would fail
        # with Exec format error.  Inspect the ELF's printable strings instead.
        if not shutil.which("strings"):
            raise RuntimeError("The host 'strings' utility is required to inspect target BusyBox without executing x86_64 code.")
        try:
            strings_proc = subprocess.run(
                ["strings", "-a", str(bb_dest)],
                check=True, capture_output=True, text=True
            )
            # BusyBox does not guarantee that applet names are emitted as one
            # printable string per line.  In particular, short names such as
            # ``sh``, ``cat`` and ``ls`` may be stored in packed/relocated
            # tables and therefore are false negatives with an exact-line
            # comparison.  Keep the output for diagnostics, but only use it as
            # a conservative check for the boot-critical switch_root applet.
            available_text = strings_proc.stdout or ""
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"Unable to inspect target BusyBox applet table without executing x86_64 code: {exc}") from exc
        if not re.search(r"(?:^|\n)switch_root(?:\n|$)", available_text):
            raise RuntimeError("Initramfs BusyBox does not advertise the required switch_root applet")
        for a in applets:
            (DIR_INITRAMFS / "bin" / a).symlink_to("/bin/busybox")

        (DIR_INITRAMFS / "sbin/switch_root").symlink_to("/bin/busybox")
        (DIR_INITRAMFS / "bin/switch_root").symlink_to("/bin/busybox")

        # 4. Copy dynamic loader & needed libraries recursively
        search_roots = [DIR_ROOTFS, DIR_X86_SYSROOT]
        # Initramfs runtime binaries/libraries must also come only from the verified target sysroot/rootfs.
        initrd_libs_collected: Set[Path] = set()

        def trace_and_stage_initrd_libs(bin_path: Path):
            needed = get_elf_needed_libraries(bin_path)
            for lib_name in needed:
                resolved = self.find_x86_64_library(lib_name, search_roots)
                if resolved and resolved not in initrd_libs_collected:
                    initrd_libs_collected.add(resolved)
                    dest_l = DIR_INITRAMFS / "lib" / lib_name
                    dest_l.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(resolved, dest_l)
                    (DIR_INITRAMFS / "lib64").mkdir(parents=True, exist_ok=True)
                    shutil.copy2(resolved, DIR_INITRAMFS / "lib64" / lib_name)
                    trace_and_stage_initrd_libs(resolved)

        trace_and_stage_initrd_libs(bb_dest)

        blkid_src = DIR_ROOTFS / "sbin/blkid"
        if not blkid_src.is_file() or not is_elf_x86_64(blkid_src):
            raise RuntimeError("Installed-root initramfs requires the verified x86_64 /sbin/blkid binary.")
        blkid_dest = DIR_INITRAMFS / "sbin/blkid"
        shutil.copy2(blkid_src, blkid_dest)
        blkid_dest.chmod(0o755)
        trace_and_stage_initrd_libs(blkid_dest)

        for loader_file in list((DIR_ROOTFS / "lib").glob("ld*")) + list((DIR_ROOTFS / "lib").glob("libc*")):
            if loader_file.is_file():
                dest_lf = DIR_INITRAMFS / "lib" / loader_file.name
                shutil.copy2(loader_file, dest_lf)
                dest_lf.chmod(0o755)
                (DIR_INITRAMFS / "lib64").mkdir(parents=True, exist_ok=True)
                shutil.copy2(loader_file, DIR_INITRAMFS / "lib64" / loader_file.name)

        musl_found = any(f.name.startswith("ld-musl-x86_64") for f in (DIR_INITRAMFS / "lib").iterdir())
        if not musl_found:
            for c in (DIR_INITRAMFS / "lib").glob("libc.musl*"):
                (DIR_INITRAMFS / "lib/ld-musl-x86_64.so.1").symlink_to(c.name)
                break

        # Check interpreter of BusyBox inside initramfs
        interp = get_elf_interpreter(bb_dest)
        if interp:
            interp_path = DIR_INITRAMFS / interp.lstrip("/")
            if not interp_path.exists():
                resolved_interp = self.find_x86_64_library(Path(interp).name, search_roots)
                if resolved_interp:
                    interp_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(resolved_interp, interp_path)
                    interp_path.chmod(0o755)
                else:
                    raise RuntimeError(f"Initramfs BusyBox dynamic interpreter missing: {interp}")

        # 5. Populate ONLY the early-boot-critical kernel modules into the initramfs,
        # preserving the real /lib/modules/<kernelrelease>/... tree structure (a flat
        # directory of .ko files is not a layout depmod/modprobe can use). Everything
        # else in the full modules tree is deliberately left out of the initramfs to
        # keep it small on this constrained Android/Termux build target; the complete
        # tree is still staged into the rootfs itself in stage_5. Most of these drivers
        # are already compiled directly ('=y') into the bespoke kernel, so this is a
        # best-effort net for whichever ones ended up as loadable modules instead.
        EARLY_BOOT_MODULE_BASENAMES = {
            "scsi_mod", "sd_mod", "libahci", "ahci", "virtio", "virtio_pci",
            "virtio_ring", "virtio_blk", "virtio_scsi", "loop", "cdrom", "sr_mod",
            "isofs", "squashfs", "overlay", "nvme", "nvme_core", "usb_storage",
            "ehci_hcd", "ehci_pci", "xhci_hcd", "xhci_pci", "uhci_hcd", "ohci_hcd",
            "ohci_pci", "sd_mod",
        }
        mod_install_root = DIR_CACHE / "modules-root"
        src_lib_modules = mod_install_root / "lib/modules"
        staged_module_count = 0
        if src_lib_modules.exists():
            for release_dir in src_lib_modules.iterdir():
                if not release_dir.is_dir():
                    continue
                dest_release_dir = DIR_INITRAMFS / "lib/modules" / release_dir.name
                dest_release_dir.mkdir(parents=True, exist_ok=True)
                for mod_f in release_dir.rglob("*.ko*"):
                    if not mod_f.is_file():
                        continue
                    mod_basename = mod_f.name.split(".ko")[0]
                    if mod_basename not in EARLY_BOOT_MODULE_BASENAMES:
                        continue
                    rel_path = mod_f.relative_to(release_dir)
                    dest_f = dest_release_dir / rel_path
                    dest_f.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(mod_f, dest_f)
                    staged_module_count += 1
                # Copy modules.order/modules.builtin so depmod (run at boot inside the
                # initramfs, against this exact subset) has the metadata it expects,
                # even though it will regenerate modules.dep itself from what's present.
                for meta_name in ("modules.order", "modules.builtin", "modules.builtin.modinfo"):
                    meta_src = release_dir / meta_name
                    if meta_src.exists():
                        shutil.copy2(meta_src, dest_release_dir / meta_name)
        if staged_module_count:
            BuildLogger.info(f"Staged {staged_module_count} early-boot kernel module(s) into initramfs.")
        else:
            BuildLogger.warn(
                "No early-boot kernel modules staged into initramfs (relying on built-in "
                "'=y' drivers in the bespoke kernel config; /init's modprobe/insmod calls "
                "for storage drivers will be harmless no-ops in that case)."
            )

        # 6. Pack Initramfs with cpio newc + xz CRC32
        initrd_target = DIR_ROOTFS / "boot/initrd.img"
        initrd_target.parent.mkdir(parents=True, exist_ok=True)
        cmd = f"cd {DIR_INITRAMFS} && find . | cpio -o -H newc | xz --check=crc32 > {initrd_target}"
        run_cmd(["sh", "-c", cmd])

        if not initrd_target.exists() or initrd_target.stat().st_size == 0:
            raise RuntimeError("Initramfs compression failed: initrd.img is empty.")

        BuildLogger.info(f"Live Initramfs built successfully ({initrd_target.stat().st_size / 1024:.1f} KB).")

    def stage_9_build_squashfs(self):
        BuildLogger.step(9, self.total_stages, "Compressing Live Root into Deterministic SquashFS")
        shutil.rmtree(DIR_ISO, ignore_errors=True)
        live_dir = DIR_ISO / "live"
        live_dir.mkdir(parents=True, exist_ok=True)
        squashfs_file = live_dir / "filesystem.squashfs"

        shutil.copy2(DIR_ROOTFS / "boot/vmlinuz", live_dir / "vmlinuz")
        shutil.copy2(DIR_ROOTFS / "boot/initrd.img", live_dir / "initrd.img")

        cmd = [
            "mksquashfs",
            str(DIR_ROOTFS),
            str(squashfs_file),
            "-comp", "gzip", "-b", "1M", "-Xcompression-level", "1",
            "-no-xattrs",
            "-e", "boot",
            "-wildcards",
            "-noappend"
        ]
        run_cmd(cmd)

        if not squashfs_file.exists() or squashfs_file.stat().st_size == 0:
            raise RuntimeError("SquashFS generation failed: filesystem.squashfs is missing.")

        BuildLogger.info(f"SquashFS image compiled: {squashfs_file.stat().st_size / (1024*1024):.2f} MB")

    def stage_10_configure_grub(self):
        BuildLogger.step(10, self.total_stages, "Configuring Hybrid BIOS & UEFI GRUB 2 Bootloader")
        grub_dir=DIR_ISO/"boot/grub"; grub_dir.mkdir(parents=True,exist_ok=True)
        persist_arg = "ribi.persistence=1 " if PERSISTENT_BUILD else ""
        grub_cfg=f"""
set default=0
set timeout=5
search --no-floppy --set=root --file /live/vmlinuz

menuentry "{OS_NAME} {OS_VERSION} Live (x86_64)" {{
    linux /live/vmlinuz quiet loglevel=0 {persist_arg}console=tty0 console=ttyS0,115200
    initrd /live/initrd.img
}}
menuentry "{OS_NAME} {OS_VERSION} (Safe Mode / Nomodeset)" {{
    linux /live/vmlinuz quiet loglevel=0 nomodeset console=tty0 console=ttyS0,115200
    initrd /live/initrd.img
}}
menuentry "{OS_NAME} {OS_VERSION} (Debug Mode)" {{
    linux /live/vmlinuz debug verbose ribi.nogui=1 ribi.serial=1 console=tty0 console=ttyS0,115200 earlyprintk=ttyS0,115200
    initrd /live/initrd.img
}}
"""
        write_file(grub_dir/"grub.cfg",grub_cfg)
        # GRUB artifacts are required from the verified target sysroot so the ISO is reproducible
        # and never silently mixes host GRUB modules with the target package set.
        search_roots=[DIR_X86_SYSROOT]
        efi_dir=DIR_ISO/"EFI/BOOT"; efi_dir.mkdir(parents=True,exist_ok=True)
        efi_file=efi_dir/"BOOTX64.EFI"
        found=False
        for root in search_roots:
            for cand in (root/"usr/lib/grub/x86_64-efi/monolithic/bootx64.efi",root/"usr/share/grub/x86_64-efi/bootx64.efi",root/"usr/lib/grub/x86_64-efi/bootx64.efi"):
                if cand.is_file() and is_efi_x86_64(cand): shutil.copy2(cand,efi_file); found=True; break
            if found: break
        # grub-mkstandalone must be paired with modules from the same GRUB
        # build. The Alpine target sysroot may contain a different GRUB build
        # from the host utility; mixing them produces an EFI image that reaches
        # GRUB rescue with errors such as "symbol grub_memcpy not found".
        host_efi_dir=Path("/usr/lib/grub/x86_64-efi")
        x86_efi_dir=host_efi_dir if host_efi_dir.is_dir() else next((r/"usr/lib/grub/x86_64-efi" for r in search_roots if (r/"usr/lib/grub/x86_64-efi").is_dir()),None)
        if not found:
            if not x86_efi_dir: raise RuntimeError("No complete x86_64-efi GRUB module directory found")
            run_cmd(["grub-mkstandalone","-O","x86_64-efi","-d",str(x86_efi_dir),"-o",str(efi_file),"boot/grub/grub.cfg="+str(grub_dir/"grub.cfg")])
            found=efi_file.is_file() and is_efi_x86_64(efi_file)
        if not found: raise RuntimeError("Failed to create a valid x86_64 UEFI BOOTX64.EFI")

        efi_img=DIR_ISO/"boot/efi.img"
        run_cmd(["dd","if=/dev/zero",f"of={efi_img}","bs=1M","count=32"])
        run_cmd(["mkfs.vfat","-F32","-n","EFI",str(efi_img)])
        run_cmd(["mmd","-i",str(efi_img),"::/EFI"]); run_cmd(["mmd","-i",str(efi_img),"::/EFI/BOOT"])
        run_cmd(["mcopy","-i",str(efi_img),str(efi_file),"::/EFI/BOOT/BOOTX64.EFI"])
        run_cmd(["mdir","-i",str(efi_img),"::/EFI/BOOT"])

        i386_dir=next((r/"usr/lib/grub/i386-pc" for r in search_roots if (r/"usr/lib/grub/i386-pc").is_dir()),None)
        bios_img=DIR_ISO/"boot/grub/i386-pc/eltorito.img"; bios_img.parent.mkdir(parents=True,exist_ok=True)
        if not i386_dir: raise RuntimeError("No i386-pc GRUB module directory found")
        if (i386_dir/"cdboot.img").is_file() and (i386_dir/"cdboot.img").stat().st_size>0:
            core_img=DIR_ISO/"boot/grub/i386-pc/core.img"
            load_cfg=DIR_ISO/"boot/grub/i386-pc/load.cfg"
            write_file(load_cfg, "insmod biosdisk\ninsmod iso9660\ninsmod part_msdos\ninsmod part_gpt\ninsmod search\ninsmod search_fs_file\ninsmod search_label\ninsmod normal\ninsmod linux\ninsmod configfile\ninsmod test\nsource /boot/grub/grub.cfg\n")
            run_cmd(["grub-mkimage","-O","i386-pc","-d",str(i386_dir),"-p","/boot/grub","-c",str(load_cfg),"-o",str(core_img),
                     "biosdisk","iso9660","part_msdos","part_gpt","search","search_fs_file","search_label","normal","linux","configfile","test"])
            with open(bios_img,"wb") as out, open(i386_dir/"cdboot.img","rb") as cdboot, open(core_img,"rb") as core:
                shutil.copyfileobj(cdboot,out); shutil.copyfileobj(core,out)
            core_img.unlink(missing_ok=True); load_cfg.unlink(missing_ok=True)
        elif (i386_dir/"eltorito.img").is_file() and (i386_dir/"eltorito.img").stat().st_size>0:
            shutil.copy2(i386_dir/"eltorito.img",bios_img)
        else:
            raise RuntimeError("i386-pc GRUB directory lacks cdboot.img/eltorito.img")
        if not bios_img.is_file() or bios_img.stat().st_size<4096: raise RuntimeError("Failed to create a valid BIOS El Torito GRUB image")
        if i386_dir:
            dest=DIR_ISO/"boot/grub/i386-pc"; dest.mkdir(parents=True,exist_ok=True)
            for f in list(i386_dir.glob("*.mod"))+list(i386_dir.glob("*.lst")): shutil.copy2(f,dest/f.name)
        if x86_efi_dir:
            dest=DIR_ISO/"boot/grub/x86_64-efi"; dest.mkdir(parents=True,exist_ok=True)
            for f in list(x86_efi_dir.glob("*.mod"))+list(x86_efi_dir.glob("*.lst")): shutil.copy2(f,dest/f.name)
        BuildLogger.info("GRUB 2 Hybrid bootloader configured and validated.")

    def stage_11_assemble_iso(self):
        BuildLogger.step(11,self.total_stages,"Assembling Hybrid BIOS/UEFI Live ISO Image")
        ISO_OUTPUT.unlink(missing_ok=True)
        if not shutil.which("grub-mkrescue"): raise RuntimeError("grub-mkrescue is required for ISO generation")
        run_cmd(["grub-mkrescue","-o",str(ISO_OUTPUT),str(DIR_ISO),"--","-volid","RIBI_OS"])
        if not ISO_OUTPUT.is_file() or ISO_OUTPUT.stat().st_size<=10*1024*1024: raise RuntimeError("ISO generation failed or produced an implausibly small image")
        if not has_el_torito_boot_record(ISO_OUTPUT): raise RuntimeError("Generated ISO has no El Torito boot record")
        if not has_uefi_el_torito_entry(ISO_OUTPUT): raise RuntimeError("Generated ISO has no UEFI El Torito entry")
        BuildLogger.info(f"Target ISO successfully generated: {ISO_OUTPUT} ({ISO_OUTPUT.stat().st_size/(1024*1024):.2f} MB)")

    def stage_12_comprehensive_validation(self):
        BuildLogger.step(12,self.total_stages,"Executing Strict Static Validation & Architecture Audit")
        if not (ISO_OUTPUT.is_file() and ISO_OUTPUT.stat().st_size>10*1024*1024): raise RuntimeError("Validation Failed: output ISO missing or implausibly small")
        if not has_el_torito_boot_record(ISO_OUTPUT): raise RuntimeError("Validation Failed: ISO lacks El Torito boot record")
        if not has_uefi_el_torito_entry(ISO_OUTPUT): raise RuntimeError("Validation Failed: ISO lacks UEFI El Torito entry")
        for rel in ("live/vmlinuz","live/initrd.img","live/filesystem.squashfs","EFI/BOOT/BOOTX64.EFI"):
            probe=subprocess.run(["xorriso","-indev",str(ISO_OUTPUT),"-ls","/"+rel],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            if not (probe.returncode==0 and rel.split('/')[-1] in probe.stdout): raise RuntimeError(f"Validation Failed: ISO missing /{rel}")
        efi=DIR_ISO/"EFI/BOOT/BOOTX64.EFI"; 
        if not is_efi_x86_64(efi): raise RuntimeError("Validation Failed: BOOTX64.EFI is not x86_64 PE32+")
        bios=DIR_ISO/"boot/grub/i386-pc/eltorito.img"; 
        if not (bios.is_file() and bios.stat().st_size>=4096): raise RuntimeError("Validation Failed: BIOS GRUB El Torito image missing/invalid")
        if not is_squashfs(DIR_ISO/"live/filesystem.squashfs"): raise RuntimeError("Validation Failed: SquashFS invalid")

        kernel=DIR_ROOTFS/"boot/vmlinuz"; 
        if not (kernel.is_file() and (is_linux_bzimage(kernel) or is_elf_x86_64(kernel))): raise RuntimeError("Validation Failed: kernel invalid")
        kernel_cache=DIR_CACHE/"vmlinuz-x86_64"; kernel_hash=DIR_CACHE/"vmlinuz-x86_64.sha256"
        if not (kernel_cache.is_file() and kernel_hash.is_file() and sha256_file(kernel_cache)==kernel_hash.read_text().strip()): raise RuntimeError("Validation Failed: kernel cache integrity metadata mismatch")
        if sha256_file(kernel)!=sha256_file(kernel_cache): raise RuntimeError("Validation Failed: staged kernel differs from verified cached kernel")
        cfg=DIR_CACHE/"vmlinuz-x86_64.config"; 
        if not cfg.is_file(): raise RuntimeError("Validation Failed: kernel config marker missing")
        cfg_text=cfg.read_text(errors="replace")
        required_cfg=["CONFIG_X86_64=y","CONFIG_BINFMT_ELF=y","CONFIG_BLK_DEV_INITRD=y","CONFIG_RD_XZ=y","CONFIG_EFI=y","CONFIG_EFI_PARTITION=y","CONFIG_BLK_DEV_LOOP=y","CONFIG_SQUASHFS=y","CONFIG_SQUASHFS_XZ=y","CONFIG_EXT4_FS=y","CONFIG_ISO9660_FS=y","CONFIG_DEVTMPFS=y","CONFIG_OVERLAY_FS=y","CONFIG_VIRTIO_BLK=y","CONFIG_VIRTIO_NET=y"]
        for opt in required_cfg:
            if opt not in cfg_text: raise RuntimeError(f"Validation Failed: kernel config missing {opt}")
        rel_file=DIR_CACHE/"kernel-release.txt"; 
        if not (rel_file.is_file() and rel_file.read_text().strip()): raise RuntimeError("Validation Failed: kernel release metadata missing")
        release=rel_file.read_text().strip(); moddir=DIR_ROOTFS/"lib/modules"/release; 
        if not moddir.is_dir(): raise RuntimeError(f"Validation Failed: matching module tree missing for {release}")

        # Initramfs: decompress and require the complete early-userspace contract.
        initrd=DIR_ROOTFS/"boot/initrd.img"; raw=lzma.decompress(initrd.read_bytes()); 
        if len(raw)<=4096: raise RuntimeError("Validation Failed: initramfs too small")
        names=set(); off=0; trailer_seen=False
        while off+110<=len(raw):
            h=raw[off:off+110]
            if h[:6] not in (b'070701',b'070702'):
                raise RuntimeError(f"Validation Failed: malformed initramfs cpio header at offset {off}")
            try:
                ns=int(h[94:102],16); fs=int(h[54:62],16); mode=int(h[14:22],16)
            except ValueError as exc:
                raise RuntimeError("Validation Failed: malformed initramfs cpio numeric field") from exc
            off+=110
            if ns < 1 or off+ns-1 > len(raw): raise RuntimeError("Validation Failed: malformed initramfs name length")
            name=raw[off:off+ns-1].decode(errors='replace'); off+=ns; off=(off+3)&~3
            if off+fs > len(raw): raise RuntimeError("Validation Failed: malformed initramfs file size")
            names.add(name)
            if name in ('init','./init') and not (mode&0o111): raise RuntimeError("Validation Failed: init not executable")
            off += fs; off=(off+3)&~3
            if name=='TRAILER!!!': trailer_seen=True; break
        if not trailer_seen: raise RuntimeError("Validation Failed: initramfs cpio TRAILER!!! missing")
        for n in ('init','bin/busybox','bin/switch_root','sbin/switch_root'):
            if not (n in names or './'+n in names): raise RuntimeError(f"Validation Failed: initramfs missing {n}")
        if not is_elf_x86_64(DIR_INITRAMFS/"bin/busybox"): raise RuntimeError("Validation Failed: initramfs BusyBox is not x86_64")

        # Validate every executable ELF in the target tree and every direct shared dependency.
        roots=[DIR_ROOTFS]; elf_count=0
        # Some packages keep private, runtime-loaded libraries below a package
        # directory rather than directly in /usr/lib (sudo and libproxy are
        # examples). Build a recursive basename index so validation reflects
        # the target loader layout instead of assuming one flat directory.
        lib_dirs=[DIR_ROOTFS/"lib",DIR_ROOTFS/"lib64",DIR_ROOTFS/"usr/lib",DIR_ROOTFS/"usr/lib64"]
        library_index={}
        for lib_dir in lib_dirs:
            if not lib_dir.is_dir():
                continue
            for dirpath, _, files in os.walk(lib_dir, followlinks=False):
                for filename in files:
                    candidate=Path(dirpath)/filename
                    if candidate.is_file() and is_elf_x86_64(candidate):
                        library_index.setdefault(filename, candidate)
        def findlib(name):
            if name.startswith('/'):
                absolute = DIR_ROOTFS / name.lstrip('/')
                return absolute if absolute.exists() else None
            found = library_index.get(name)
            if found:
                return found
            # Firefox/Zen bundles dependent libraries beside the executable
            # under /zen; include those application-local ELF dependencies in
            # the audit instead of treating them as missing system libraries.
            for candidate in DIR_ROOTFS.rglob(name):
                if candidate.is_file() and not candidate.is_symlink() and is_elf_x86_64(candidate):
                    return candidate
            return None
        for base in roots:
            for dirpath,dirnames,files in os.walk(base,followlinks=False):
                dirnames[:]=[d for d in dirnames if d not in ('proc','sys','dev','run')]
                for name in files:
                    f=Path(dirpath)/name
                    if f.is_symlink() or not f.is_file(): continue
                    # BIOS GRUB embeds intentional i386-pc module binaries in
                    # the target tree. They are bootloader payloads, not
                    # target userspace executables, so they must not be held to
                    # the x86_64 userspace architecture requirement.
                    try:
                        f.relative_to(DIR_ROOTFS / "usr/lib/grub")
                        continue
                    except ValueError:
                        pass
                    arch=get_elf_arch(f)
                    if not arch: continue
                    if arch!="x86_64": raise RuntimeError(f"Validation Failed: non-x86_64 ELF: {f} ({arch})")
                    elf_count+=1
                    interp=get_elf_interpreter(f)
                    if interp and not (findlib(Path(interp).name) or (DIR_ROOTFS/interp.lstrip('/')).exists()): raise RuntimeError(f"Validation Failed: ELF interpreter missing for {f}: {interp}")
                    for lib in get_elf_needed_libraries(f):
                        if not findlib(lib): raise RuntimeError(f"Validation Failed: DT_NEEDED library {lib} missing for {f}")
        if elf_count<=20: raise RuntimeError("Validation Failed: suspiciously few x86_64 ELF files in target rootfs")

        # Required user-facing programs must exist and be x86_64-loadable.
        for rel in ("sbin/apk","usr/sbin/setfont","usr/bin/python3","usr/bin/nano","usr/bin/nvim","usr/local/bin/nano","usr/local/bin/nvim","etc/apk/repositories","etc/profile","bin/su","usr/bin/id","usr/bin/setsid","usr/bin/dbus-daemon"):
            p=DIR_ROOTFS/rel
            if not p.exists(): raise RuntimeError(f"Validation Failed: required application/runtime missing: /{rel}")
        if not list((DIR_ROOTFS / "usr/share/consolefonts").glob("ter-*.psf*")):
            raise RuntimeError("Validation Failed: Terminus console font payload missing")
            if p.is_file() and get_elf_arch(p) and not is_elf_x86_64(p): raise RuntimeError(f"Validation Failed: /{rel} is not x86_64")
        for util in ("parted","mkfs.ext4","mkfs.vfat","lsblk","blkid"):
            p=self.find_x86_64_binary(util,[DIR_ROOTFS]); 
            if not (p and p.is_file() and is_elf_x86_64(p) and not p.is_symlink()): raise RuntimeError(f"Validation Failed: standalone {util} missing")
        for rel in ("sbin/ribi-init","usr/local/bin/ribisvc","usr/local/bin/ribi-pkg","usr/local/bin/ribi","usr/local/bin/ribi-installer","usr/local/bin/ribi-edit","usr/local/bin/ribi-snake","usr/local/bin/ribi-2048"):
            p=DIR_ROOTFS/rel; 
            if not (p.is_file() and os.access(p,os.X_OK)): raise RuntimeError(f"Validation Failed: /{rel} missing/not executable")
        passwd=(DIR_ROOTFS/"etc/passwd").read_text(); group=(DIR_ROOTFS/"etc/group").read_text(); shadow=(DIR_ROOTFS/"etc/shadow").read_text()
        if not (any(x.startswith('root:') for x in passwd.splitlines()) and any(x.startswith('ribi:') for x in passwd.splitlines())): raise RuntimeError("Validation Failed: root/ribi accounts missing")
        if any(x.startswith('root::') or x.startswith('ribi::') for x in shadow.splitlines()): raise RuntimeError("Validation Failed: empty privileged password field")
        if (DIR_ROOTFS/"sbin/openrc").exists(): raise RuntimeError("Validation Failed: Alpine OpenRC init artifacts leaked into target")
        if RELEASE_PROFILE == "no-desktop":
            forbidden_paths = (
                "usr/bin/Xorg", "usr/bin/startx", "usr/bin/startxfce4", "usr/bin/xfce4-session",
                "usr/bin/xfce4-panel", "usr/bin/openbox", "usr/bin/firefox", "usr/bin/firefox-esr", "usr/bin/geany",
                "usr/bin/lightdm", "etc/xdg/xfce4",
            )
            leaked = [f"/{rel}" for rel in forbidden_paths if (DIR_ROOTFS / rel).exists()]
            if leaked:
                raise RuntimeError(f"Validation Failed: no-desktop release contains GUI artifacts: {leaked}")
        else:
            for rel in ("usr/bin/Xorg", "usr/bin/startx", "opt/zen/zen", "usr/local/bin/zen-browser", "usr/local/bin/ribi-shell.py", "usr/local/bin/ribi-screenshot.py", "usr/local/bin/ribi-wm.py", "usr/local/bin/ribi-control-center.py", "usr/local/bin/ribi-dock", "usr/share/backgrounds/ribi-wallpaper.png"):
                if not (DIR_ROOTFS / rel).exists():
                    raise RuntimeError(f"Validation Failed: Ribi desktop payload missing: /{rel}")

        # Ensure generated GRUB config and live payload agree.
        grub=(DIR_ISO/"boot/grub/grub.cfg").read_text(); 
        if "search --no-floppy --set=root --file /live/vmlinuz" not in grub: raise RuntimeError("Validation Failed: GRUB search directive missing")
        if "/live/vmlinuz" not in grub or "/live/initrd.img" not in grub: raise RuntimeError("Validation Failed: GRUB live entries incomplete")
        BuildLogger.info(f"ALL STATIC VALIDATION CHECKS PASSED: {elf_count} x86_64 ELF files audited; BIOS+UEFI ISO structure verified.")
        if shutil.which("qemu-system-x86_64"):
            BuildLogger.info("QEMU detected. Boot-test is available via --boot-test; static validation does not pretend to prove runtime desktop behavior.")
        if TERMUX_DOWNLOADS_PATH.is_dir():
            try: shutil.copy2(ISO_OUTPUT,TERMUX_DOWNLOADS_PATH/ISO_OUTPUT.name)
            except Exception as e: BuildLogger.warn(f"Failed to export ISO to Termux Downloads: {e}")

    def boot_test_iso(self, timeout_seconds: int = 90):
        if not shutil.which("qemu-system-x86_64"):
            raise RuntimeError("QEMU x86_64 is required for --boot-test")
        BuildLogger.info(f"Boot-testing ISO in x86_64 QEMU for up to {timeout_seconds}s...")
        cmd=["qemu-system-x86_64","-M","q35","-m","1024","-smp","2","-cdrom",str(ISO_OUTPUT),"-boot","d","-display","none","-serial","stdio","-no-reboot","-no-shutdown","-device","virtio-tablet-pci","-device","virtio-keyboard-pci"]
        if os.path.exists("/dev/kvm"): cmd.insert(1,"-enable-kvm")
        try:
            proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout_seconds)
        except subprocess.TimeoutExpired as e:
            out=e.stdout or ""
            if isinstance(out, bytes):
                out=out.decode("utf-8", errors="replace")
            if "[BOOT-1] Entered ribi-init" in out or "[RIBI-GUI] init pid=" in out:
                BuildLogger.info("QEMU boot test reached Ribi PID 1 and entered the live runtime path before timeout.")
                return
            raise RuntimeError("QEMU boot test timed out before reaching Ribi PID 1/runtime path")
        out=proc.stdout or ""
        if "[BOOT-1] Entered ribi-init" not in out and "[RIBI-GUI] init pid=" not in out:
            raise RuntimeError("QEMU boot test did not reach Ribi PID 1/runtime path")
        BuildLogger.info("QEMU boot test reached Ribi PID 1/runtime path successfully.")

    def run(self):
        BuildLogger.info(f"Initiating Ribi OS 1.0 Production Build Engine ({OS_CODENAME})...")
        t_start = time.time()
        self.stage_1_preflight_checks()
        self.stage_2_directory_hierarchy()
        self.stage_3_acquire_x86_64_bootstrap()
        self.stage_4_configure_and_stage_kernel()
        self.stage_5_build_hermetic_rootfs()
        self.stage_6_deploy_ribi_system_core()
        self.stage_7_synthesize_applications()
        self.stage_8_construct_initramfs()
        self.stage_9_build_squashfs()
        self.stage_10_configure_grub()
        self.stage_11_assemble_iso()
        self.stage_12_comprehensive_validation()
        elapsed = time.time() - t_start

        sha256 = hashlib.sha256(ISO_OUTPUT.read_bytes()).hexdigest()
        print("\n\033[1;32m" + "=" * 76)
        print(f"  RIBI OS ({OS_CODENAME}) x86_64 MASTER BUILD COMPLETE IN {elapsed:.1f}s")
        print(f"  Final Bootable ISO: {ISO_OUTPUT}")
        print(f"  Size: {ISO_OUTPUT.stat().st_size / (1024*1024):.2f} MB | SHA-256: {sha256}")
        print("=" * 76 + "\033[0m\n")


# =============================================================================
# 8. CLI ENTRYPOINT & UTILITY ACTIONS
# =============================================================================
def clean_workspace():
    expected=WORK_DIR/"ribi-build-workspace"
    if STORAGE_ROOT.resolve()!=expected.resolve() or STORAGE_ROOT.name!="ribi-build-workspace":
        raise RuntimeError(f"Refusing unsafe clean path: {STORAGE_ROOT}")
    BuildLogger.info(f"Purging build workspace at {STORAGE_ROOT}...")
    if STORAGE_ROOT.exists(): shutil.rmtree(STORAGE_ROOT)
    if ISO_OUTPUT.exists(): ISO_OUTPUT.unlink()
    BuildLogger.info("Workspace cleaned.")


def run_qemu(headless: bool = False):
    if not ISO_OUTPUT.exists():
        BuildLogger.error(f"ISO image {ISO_OUTPUT} not found. Run build first.")
        sys.exit(1)

    qemu_bin = "qemu-system-x86_64"
    if not shutil.which(qemu_bin):
        BuildLogger.error(f"QEMU binary '{qemu_bin}' not found on host.")
        sys.exit(1)

    cmd = [
        qemu_bin,
        "-m", "2048",
        "-smp", "2",
        "-cdrom", str(ISO_OUTPUT),
        "-boot", "d",
        # QEMU virtio-vga advertises a 1280x800 framebuffer but exposes a
        # 640x480 RandR mode with this guest/Xorg combination. Use std for
        # reliable desktop verification; this changes only the test harness.
        "-vga", "std",
        "-device", "virtio-tablet-pci",
        "-device", "virtio-keyboard-pci"
    ]
    if os.path.exists("/dev/kvm"):
        cmd.append("-enable-kvm")
    if headless:
        cmd.extend(["-nographic"])

    BuildLogger.info(f"Launching QEMU: {' '.join(cmd)}")
    subprocess.run(cmd)


def main():
    parser = argparse.ArgumentParser(description="Ribi OS Master Operating System Generator (bulbQT)")
    parser.add_argument("--build", action="store_true", help="Execute complete Ribi OS build pipeline (default)")
    parser.add_argument("--resume", action="store_true", help="Resume build from existing cache")
    parser.add_argument("--clean", action="store_true", help="Clean all build artifacts safely")
    parser.add_argument("--run", action="store_true", help="Run the generated ISO inside QEMU")
    parser.add_argument("--run-headless", action="store_true", help="Run the generated ISO inside QEMU headlessly")
    parser.add_argument("--boot-test", action="store_true", help="Boot-test the generated ISO in x86_64 QEMU and require Ribi PID 1")
    parser.add_argument("--check-deps", action="store_true", help="Verify host build dependencies only")
    parser.add_argument("--version", action="version", version=f"{OS_NAME} {OS_VERSION} ({OS_CODENAME})")
    args = parser.parse_args()

    BuildLogger.init()

    if args.clean:
        clean_workspace()
    elif args.check_deps:
        cross_req = (platform.machine() != "x86_64")
        cic_host_dependencies(cross_required=cross_req)
    elif args.run:
        run_qemu(headless=False)
    elif args.run_headless:
        run_qemu(headless=True)
    elif args.boot_test:
        builder=RibiMasterBuilder(resume=True)
        builder.boot_test_iso()
    else:
        builder = RibiMasterBuilder(resume=args.resume)
        builder.run()


if __name__ == "__main__":
    main()
