"""Host build-dependency audit and C.I.C. acquisition.

This module is part of the Ribi OS ISO builder package. Split out of the
original monolithic builder for readability; behaviour is unchanged.
"""

import os
import platform
import shutil
import sys
from .config import REQUIRED_HOST_COMMANDS
from .logging_utils import BuildLogger, run_cmd


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
            "patchelf": "patchelf",
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
                "patchelf": "patchelf",
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
                "gcc": "gcc", "mformat": "mtools", "mcopy": "mtools", "strings": "binutils", "grub-install": "grub2-tools",
                "patchelf": "patchelf"
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
                "gcc": "gcc", "mformat": "mtools", "mcopy": "mtools", "strings": "binutils", "grub-install": "grub2-tools",
                "patchelf": "patchelf"
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
                "mformat": "mtools", "mcopy": "mtools", "gcc": "gcc", "strings": "binutils", "grub-install": "grub",
                "patchelf": "patchelf"
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
