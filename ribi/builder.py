"""The master build engine: the 12 build stages and ISO validation.

This module is part of the Ribi OS ISO builder package. Split out of the
original monolithic builder for readability; behaviour is unchanged.
"""

import base64
import concurrent.futures
import hashlib
import io
import json
import lzma
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Set
from .apk import _gzip_streams, determine_dest_subdir, parse_apkindex, resolve_apk_closure, safe_tar_extract, verify_apk_integrity, verify_apkindex_signature
from .audit import get_elf_arch, get_elf_interpreter, get_elf_needed_libraries, has_el_torito_boot_record, has_uefi_el_torito_entry, is_efi_x86_64, is_elf_x86_64, is_linux_bzimage, is_squashfs
from .config import ALPINE_FALLBACK_BASE_URLS, ALPINE_MIRROR_BASE, APKINDEX_COMMUNITY_URL, APKINDEX_URL, BOOTSTRAP_X86_64_TARBALL, BOOTSTRAP_X86_64_URL, DEFAULT_HOSTNAME, DIR_APPS, DIR_BUILD, DIR_CACHE, DIR_INITRAMFS, DIR_ISO, DIR_LOGS, DIR_PKGS, DIR_ROOTFS, DIR_SRC, DIR_X86_SYSROOT, ESSENTIAL_TARGET_BINARIES, FORBIDDEN_NO_DESKTOP_PACKAGES, ISO_OUTPUT, KERNEL_FALLBACK_URLS, KERNEL_TARBALL, KERNEL_URL, KERNEL_VERSION, OS_ARCH, OS_CODENAME, OS_IDENTIFIER, OS_NAME, OS_VERSION, PERSISTENT_BUILD, RELEASE_PROFILE, STORAGE_ROOT, TARGET_APK_PACKAGES, TERMUX_DOWNLOADS_PATH, WALLPAPER_SOURCE, WALLPAPER_TARGET, ZEN_BROWSER_TARBALL, ZEN_BROWSER_URL
from .deps import cic_host_dependencies
from .download import download_file, download_with_sidecar_hash
from .kernel_config import get_bespoke_kernel_config
from .logging_utils import BuildLogger, run_cmd, sha256_file, write_file
from .sources import SRC_LIVE_INIT, SRC_RIBI_2048, SRC_RIBI_CLI, SRC_RIBI_DOCTOR, SRC_RIBI_INIT, SRC_RIBI_INSTALLER, SRC_RIBI_PKG, SRC_RIBI_SNAKE, SRC_RIBI_SVC

_PAYLOADS_DIR = Path(__file__).resolve().parent / "payloads"
_COMPONENTS_DIR = Path(__file__).resolve().parent / "components"


def _payload(filename: str) -> str:
    """Load a target-OS payload file staged under ``ribi/payloads/``."""
    return (_PAYLOADS_DIR / filename).read_text(encoding="utf-8")


def _component(filename: str) -> Path:
    """Return the path to a native desktop component under ``ribi/components/``."""
    return _COMPONENTS_DIR / filename


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
        pkg_marker = DIR_X86_SYSROOT / ".sysroot_packages"
        pkg_signature = hashlib.sha256(
            "\n".join(sorted(TARGET_APK_PACKAGES)).encode("utf-8")
        ).hexdigest()
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
            cached_signature = pkg_marker.read_text().strip() if pkg_marker.is_file() else ""
            if cached_signature != pkg_signature:
                stale.append("package-list-changed")
            if not stale:
                BuildLogger.cic("CHECK", "x86_64 Bootstrap Sysroot", "EXISTS (Ready)")
                return
            BuildLogger.warn(
                "Cached x86_64 Bootstrap Sysroot is incomplete; rebuilding package staging "
                f"because: {stale}"
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
        #    Extraction is I/O- and CPU-bound and each package writes to a
        #    disjoint set of paths, so unpack them in a small thread pool. The
        #    whole closure is ~200 packages; doing it serially dominated the
        #    stage. Any failure still surfaces with the offending package name.
        def _fetch_one(pkg: str) -> str:
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
            return pkg

        workers = min(8, max(4, (os.cpu_count() or 4) * 2))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in pool.map(_fetch_one, install_set):
                pass

        # Final structural gate: do not mark a partially staged sysroot as ready.
        required_standalone = ["busybox", "python3", "blkid", "lsblk", "parted", "mkfs.ext4", "mkfs.vfat", "rsync", "acpid"]
        missing=[n for n in required_standalone if not self.find_x86_64_binary(n,[DIR_X86_SYSROOT])]
        if missing:
            raise RuntimeError(f"x86_64 sysroot verification failed; missing genuine ELF tools: {missing}")
        pkg_marker.write_text(pkg_signature + "\n")
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
        # Zen is a glibc-linked Firefox build and this OS is musl/Alpine. The
        # gcompat shim cannot host Zen's launcher: it deadlocks during glibc
        # pthread/rtld early init (single-threaded FUTEX_WAIT before relocation
        # completes), so `zen --version` hangs forever. Ship a self-contained
        # glibc runtime under /opt/zen/rt and point Zen's interpreter at it so
        # the whole process tree (including the X11/GDK stack, which inherits
        # the runtime RPATH) stays glibc and never mixes in musl libraries.
        zen_rt_lib = zen_opt_dir / "rt/lib"
        if zen_rt_lib.parent.exists():
            shutil.rmtree(zen_rt_lib.parent)
        zen_rt_lib.mkdir(parents=True, exist_ok=True)

        # Resolve the full transitive NEEDED closure of the bundle against the
        # host's glibc directories with patchelf --print-needed. Using the ELF
        # metadata directly (rather than ldd) keeps this working on build hosts
        # whose ldd cannot introspect foreign glibc binaries.
        loader_dirs = [
            Path("/lib/x86_64-linux-gnu"), Path("/usr/lib/x86_64-linux-gnu"),
            Path("/lib64"), Path("/usr/lib64"), Path("/lib"), Path("/usr/lib"),
        ]

        def needed_of(path: Path) -> List[str]:
            probe = run_cmd(["patchelf", "--print-needed", str(path)],
                            capture=True, check=False)
            return [ln.strip() for ln in (probe.stdout or "").splitlines() if ln.strip()]

        zen_root = zen_opt_dir.resolve()
        runtime_names: Set[str] = set()
        seen: Set[str] = set()
        queue = [e for e in sorted(zen_opt_dir.iterdir())
                 if e.is_file() and is_elf_x86_64(e)]
        while queue:
            current = queue.pop()
            for soname in needed_of(current):
                if soname in seen:
                    continue
                seen.add(soname)
                bundled = zen_opt_dir / soname
                if bundled.exists():
                    continue
                source = next((d / soname for d in loader_dirs if (d / soname).exists()), None)
                if source is None or not source.is_file():
                    continue
                if source.resolve().parent == zen_root:
                    continue
                runtime_names.add(source.name)
                if is_elf_x86_64(source):
                    queue.append(source)

        # The dynamic loader is not itself a NEEDED entry.
        host_loader = next(
            (p for p in (
                Path("/lib64/ld-linux-x86-64.so.2"),
                Path("/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"),
                Path("/usr/lib64/ld-linux-x86-64.so.2"),
            ) if p.exists()),
            None,
        )
        if host_loader is None:
            raise RuntimeError("Validation Failed: host glibc loader not found for Zen runtime bundle")
        host_loader = host_loader.resolve()

        # glibc dlopens NSS service modules by name, so they are invisible to
        # DT_NEEDED. Browsing needs DNS and profile lookup needs passwd/group.
        for nss in ("libnss_dns.so.2", "libnss_files.so.2", "libnss_compat.so.2",
                    "libnss_hesiod.so.2"):
            source = next((d / nss for d in loader_dirs if (d / nss).is_file()), None)
            if source is not None:
                runtime_names.add(nss)

        for name in sorted(runtime_names):
            source = next((d / name for d in loader_dirs if (d / name).is_file()), None)
            if source is not None:
                shutil.copy2(source, zen_rt_lib / name)
        shutil.copy2(host_loader, zen_rt_lib / host_loader.name)

        staged_zen_rt = DIR_CACHE / "zen-rt"
        shutil.copy2(zen_opt_dir / "zen", staged_zen_rt)
        run_cmd([
            "patchelf",
            "--force-rpath",
            "--set-interpreter", f"/opt/zen/rt/lib/{host_loader.name}",
            "--set-rpath", "/opt/zen/rt/lib:/opt/zen",
            str(staged_zen_rt),
        ])
        shutil.copy2(staged_zen_rt, zen_opt_dir / "zen-rt")
        (zen_opt_dir / "zen-rt").chmod(0o755)

        zen_launcher = """#!/bin/sh
set -eu
export MOZ_ENABLE_WAYLAND=0
export GDK_BACKEND=x11
# The bespoke kernel does not grant unprivileged user namespaces, so Zen's
# content sandbox cannot initialise and it prints
# "CanCreateUserNamespace() clone() failure: EPERM". Disable the sandbox here
# so the dock, launcher, and desktop entry all match the autostart entries
# (which already pass --no-sandbox) instead of warning on every launch.
exec /opt/zen/zen-rt --no-sandbox --disable-dev-shm-usage "$@"
"""
        write_file(DIR_ROOTFS / "usr/local/bin/zen-browser", zen_launcher, mode=0o755)
        
        # Other gcompat-hosted glibc binaries declare libdl.so.2 while modern
        # glibc folds libdl into libc. gcompat provides the ABI entry point, and
        # these copies make the merged-library names resolvable in the rootfs.
        # (Zen itself no longer depends on this: it runs on /opt/zen/rt.)
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
            "accessories-calculator": "roundrectangle 12%,8% 88%,92% 3,3",
            "image-x-generic": "roundrectangle 8%,20% 92%,84% 3,3",
            "multimedia-player": "polygon 30%,16% 84%,50% 30%,84%",
            "package-x-generic": "polygon 50%,8% 90%,30% 90%,70% 50%,92% 10%,70% 10%,30%",
            "application-pdf": "polygon 50%,16% 86%,50% 50%,84% 14%,50%",
            "audio-x-generic": "polygon 16%,38% 62%,38% 62%,16% 62%,84% 16%,62%",
            "audio-editor": "circle 12%,12% 88%,88% circle 50%,50% 68%,68%",
            "applications-internet": "circle 8%,8% 92%,92% line 8%,50% 92%,50%",
            "preferences-desktop-display": "roundrectangle 8%,18% 92%,72% 3,3 line 50%,72% 50%,90%",
            "preferences-desktop-theme": "circle 26%,26% 74%,74% circle 50%,50% 66%,66%",
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
        # Compile the GSettings and shared-mime databases. Without
        # gschemas.compiled any GLib app that reads a setting (Celluloid,
        # File Roller) aborts with "No GSettings schemas are installed", and
        # without mime.cache xdg-open cannot map file types to applications.
        schemas_dir = DIR_ROOTFS / "usr/share/glib-2.0/schemas"
        if (DIR_ROOTFS / "usr/bin/glib-compile-schemas").exists() and schemas_dir.is_dir():
            subprocess.run(["chroot", str(DIR_ROOTFS), "/usr/bin/glib-compile-schemas", "/usr/share/glib-2.0/schemas"], check=False)
        if (DIR_ROOTFS / "usr/bin/update-mime-database").exists() and (DIR_ROOTFS / "usr/share/mime").is_dir():
            subprocess.run(["chroot", str(DIR_ROOTFS), "/usr/bin/update-mime-database", "/usr/share/mime"], check=False)
        if (DIR_ROOTFS / "usr/bin/update-desktop-database").exists() and (DIR_ROOTFS / "usr/share/applications").is_dir():
            subprocess.run(["chroot", str(DIR_ROOTFS), "/usr/bin/update-desktop-database", "/usr/share/applications"], check=False)
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
        lightdm_conf = _payload("lightdm.conf")
        write_file(DIR_ROOTFS / "etc/lightdm/lightdm.conf", lightdm_conf)
        write_file(DIR_ROOTFS / "etc/lightdm/lightdm-gtk-greeter.conf", "[greeter]\nbackground=#101820\n")
        write_file(DIR_ROOTFS / "etc/skel/.xinitrc", "exec startxfce4\n")
        # Start the display manager as root; LightDM drops the session to ribi.

        desktop_session = _payload("ribi-desktop-session.sh")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-desktop-session", desktop_session, mode=0o755)
        dump_session = _payload("ribi-dump-session.sh")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-dump-session", dump_session, mode=0o755)
        xfce_session = _payload("ribi-xfce-session.sh")
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
        session_wrapper = _payload("ribi-session-wrapper.sh")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-session-wrapper", session_wrapper, mode=0o755)
        # Alpine's stock Xsession sources interactive profile logic before doing
        # an unquoted exec. Use a minimal deterministic handoff for LightDM.
        direct_xsession = _payload("ribi-direct-xsession.sh")
        wallpaper_xml = _payload("xfce4-desktop.xml")
        wallpaper_cfg = DIR_ROOTFS / "home/ribi/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml"
        write_file(wallpaper_cfg, wallpaper_xml)
        wallpaper_data = base64.b64encode(wallpaper_dest.read_bytes()).decode("ascii")
        wallpaper_html = f"""<!doctype html><html><head><meta charset=\"utf-8\"><style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:linear-gradient(135deg,#08090b 0%,#2b1118 42%,#62545b 72%,#111216 100%)}}img{{width:100vw;height:100vh;object-fit:cover;display:block}}</style></head><body><img src=\"data:image/png;base64,{wallpaper_data}\" alt=\"Ribi OS wallpaper\"></body></html>\n"""
        write_file(DIR_ROOTFS / "usr/share/backgrounds/ribi-wallpaper.html", wallpaper_html)
        wallpaper_viewer = _payload("ribi-wallpaper-viewer.sh")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-wallpaper-viewer", wallpaper_viewer, mode=0o755)
        dock_src = _component("ribi-dock.py")
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
        # now uses the searchable native launcher and never imports GTK for the
        # app menu.
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-app-launcher", "#!/bin/sh\nexec /usr/local/bin/ribi-launcher\n", mode=0o755)
        gtk_probe_py = _payload("ribi-gtk-probe.py")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-gtk-probe", gtk_probe_py, mode=0o755)
        handoff_py = _payload("ribi-user-handoff.py")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-user-handoff", handoff_py, mode=0o755)
        panel_xml = _payload("xfce4-panel.xml")
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
        shortcuts_xml = _payload("xfce4-shortcuts.xml")
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
        direct = _payload("ribi-direct-desktop.sh")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-direct-desktop", direct, mode=0o755)
        user_desktop = _payload("ribi-user-desktop.sh")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-user-desktop", user_desktop, mode=0o755)
        openbox_autostart = _payload("openbox-autostart.sh")
        write_file(DIR_ROOTFS / "home/ribi/.config/openbox/autostart", openbox_autostart, mode=0o755)
        # Ship a Ribi-coloured window decoration theme so titlebars, menus and
        # OSD match the native dark apps instead of the stock light Clearlooks.
        openbox_theme_dir = DIR_ROOTFS / "usr/share/themes/Ribi/openbox-3"
        openbox_theme_dir.mkdir(parents=True, exist_ok=True)
        write_file(openbox_theme_dir / "themerc", _payload("openbox-ribi-themerc"))
        # Openbox rejects a theme whose button image files are missing, so copy
        # the stock xbm glyphs in; the themerc above recolours them.
        for stock_theme in ("Bear2", "Default"):
            button_src = DIR_ROOTFS / f"usr/share/themes/{stock_theme}/openbox-3"
            if button_src.is_dir():
                for glyph in button_src.glob("*.xbm"):
                    shutil.copy2(glyph, openbox_theme_dir / glyph.name)
                break
        # Terminal + X client palette so xterm/lxterminal match the Ribi tokens.
        write_file(DIR_ROOTFS / "etc/X11/Xresources/ribi", _payload("ribi-Xresources"))
        # Compositor config: shadows, rounded corners and fading on top of Openbox.
        write_file(DIR_ROOTFS / "etc/xdg/ribi/picom.conf", _payload("ribi-picom.conf"))
        # Terminal front-end: prefers lxterminal, falls back to xterm.
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-terminal", _payload("ribi-terminal.sh"), mode=0o755)
        # Patch the shipped keymap: inject the launcher shortcuts and point the
        # theme at Ribi. The stock bindings are kept, so only these keys change.
        openbox_rc = DIR_ROOTFS / "etc/xdg/openbox/rc.xml"
        if openbox_rc.is_file():
            rc_text = openbox_rc.read_text(encoding="utf-8")
            launcher_binds = (
                '  <keybind key="W-space">\n'
                '    <action name="Execute"><command>/usr/local/bin/ribi-launcher</command></action>\n'
                '  </keybind>\n'
                '  <keybind key="W-S-d">\n'
                '    <action name="Execute"><command>/usr/local/bin/ribi-launcher</command></action>\n'
                '  </keybind>\n'
            )
            anchor = "  <chainQuitKey>C-g</chainQuitKey>\n"
            # Rebuilds reuse the extracted rootfs, so strip any launcher binding
            # from a previous run before injecting; otherwise the guard below
            # would keep a stale (possibly conflicting) shortcut forever.
            rc_text = re.sub(
                r'[ \t]*<keybind key="[^"]*">\s*'
                r'<action name="Execute"><command>/usr/local/bin/ribi-launcher</command></action>\s*'
                r'</keybind>\n',
                "",
                rc_text,
            )
            if anchor in rc_text:
                rc_text = rc_text.replace(anchor, anchor + launcher_binds, 1)
            rc_text = re.sub(
                r"(<theme>\s*<name>)[^<]*(</name>)",
                r"\g<1>Ribi\g<2>",
                rc_text,
                count=1,
            )
            openbox_rc.write_text(rc_text, encoding="utf-8")
        visible = _payload("ribi-visible-session.sh")
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

        xorg_conf = _payload("xorg.conf")
        write_file(DIR_ROOTFS / "etc/X11/xorg.conf", xorg_conf)
        xorg_launcher = """#!/bin/sh
set -u
printf '[RIBI-XORG] launching: %s\n' "$*" >/dev/ttyS0 2>/dev/null || true
exec /usr/libexec/Xorg "$@" -logfile /dev/ttyS0 -verbose 3
"""
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-xorg", xorg_launcher, mode=0o755)

        xfce_clients = _payload("ribi-xfce-clients.sh")
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
        editor_src = _component("ribi-code-editor.py")
        if not editor_src.is_file():
            raise RuntimeError(f"Missing Code Editor source: {editor_src}")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-edit", editor_src.read_text(), mode=0o755)
        prompt_src = _component("ribi-app-prompt.py")
        if not prompt_src.is_file():
            raise RuntimeError(f"Missing app action helper: {prompt_src}")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-app-prompt", prompt_src.read_text(), mode=0o755)
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-snake", SRC_RIBI_SNAKE, mode=0o755)
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-2048", SRC_RIBI_2048, mode=0o755)
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-doctor", SRC_RIBI_DOCTOR, mode=0o755)

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
        # dhcpcd is built with --enable-privsep; without its separation account it
        # refuses to drop privileges and logs "no such user dhcpcd". Alpine's
        # dhcpcd.pre-install creates this account and its home is group-writable.
        if not any(line.startswith("dhcpcd:") for line in passwd.splitlines()):
            passwd += "dhcpcd:x:100:101:dhcpcd:/var/lib/dhcpcd:/sbin/nologin\n"
        if not any(line.startswith("root:") for line in group.splitlines()): group += "root:x:0:\n"
        if not any(line.startswith("ribi:") for line in group.splitlines()): group += "ribi:x:1000:\n"
        if not any(line.startswith("dhcpcd:") for line in group.splitlines()): group += "dhcpcd:x:101:\n"

        def _gid_in_use(text, gid):
            for line in text.splitlines():
                f = line.split(":")
                if len(f) >= 3 and f[2].isdigit() and int(f[2]) == gid:
                    return True
            return False

        def _next_free_gid(text, start=102):
            gid = start
            while _gid_in_use(text, gid):
                gid += 1
            return gid

        # gid 27 is video, 100 is users, and 1000 is the ribi user's primary
        # group; derive a free system gid for sudo so no two groups collide.
        sudo_gid = _next_free_gid(group)
        for gname,gid in (("audio",29),("video",44),("sudo",sudo_gid)):
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
        dhcpcd_dir=DIR_ROOTFS / "var/lib/dhcpcd"
        if dhcpcd_dir.is_dir():
            try: os.chown(dhcpcd_dir, 100, 101)
            except OSError: pass
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
        net_up_script = _payload("ribi-netup.sh")
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

        # OBS Studio defaults. OBS 32's Simple output defaults to a fragmented
        # "hybrid" MP4/MOV muxer, which segfaults in this minimal image the
        # moment recording starts (the crash lands right after the muxer logs
        # "Writing Hybrid MP4/MOV file"). MKV is the mature, crash-safe muxer
        # and needs no fragmented-MP4 path, so ship a profile that selects it.
        # The audio encoder is left at OBS's default; only the container changes.
        #
        # OBS only loads a profile/scene selection that its own configuration
        # names, and it looks those up in user.ini (not global.ini). We ship
        # both a named "ribi" profile and the "ribi" scene collection below.
        # The desktop launcher still passes --profile ribi --collection ribi,
        # so the container is guaranteed even if the wizard is ever re-enabled.
        obs_base = DIR_ROOTFS / "home/ribi/.config/obs-studio"
        # FirstRun=false skips OBS's auto-configuration wizard, which would
        # otherwise re-detect the encoder/container on first launch and discard
        # the MKV recording format set below.
        write_file(obs_base / "global.ini", """[General]
EnableAutoUpdates=false
FirstRun=false
SafeMode=false

[Basic]
Profile=ribi
ProfileDir=ribi
SceneCollection=ribi
SceneCollectionFile=ribi
""")
        write_file(obs_base / "basic/profiles/ribi/basic.ini", """[General]
Name=ribi

[Output]
Mode=Simple

[SimpleOutput]
RecFormat2=mkv
RecQuality=Stream
RecEncoder=x264
RecTracks=1

[Video]
BaseCX=1280
BaseCY=800
OutputCX=1280
OutputCY=800
FPSCommon=30

[Audio]
SampleRate=48000
ChannelSetup=Stereo
""")
        write_file(obs_base / "basic/scenes/ribi.json", _payload("obs-scene-collection.json"))
        (obs_base / "basic/scenes").mkdir(parents=True, exist_ok=True)
        for sub in (".config", ".config/obs-studio"):
            try:
                os.chown(DIR_ROOTFS / "home/ribi" / sub, 1000, 1000)
            except OSError:
                pass

        BuildLogger.info("Ribi core management services deployed.")

    def stage_7_synthesize_applications(self):
        BuildLogger.step(7, self.total_stages, "Synthesizing Native Console Applications")
        apps_dir = DIR_ROOTFS / "usr/share/applications"
        apps_dir.mkdir(parents=True, exist_ok=True)
        explorer_src = _component("ribi-file-explorer.py")
        if not explorer_src.is_file():
            raise RuntimeError(f"Missing native explorer prototype: {explorer_src}")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-file-explorer", explorer_src.read_text(), mode=0o755)
        for native_name in ("ribi-shell.py", "ribi-screenshot.py", "ribi-wm.py", "ribi-control-center.py", "ribi-launcher.py"):
            native_src = _component(native_name)
            if not native_src.is_file():
                raise RuntimeError(f"Missing native Ribi component: {native_src}")
            write_file(DIR_ROOTFS / "usr/local/bin" / native_name, native_src.read_text(), mode=0o755)
        theme_src = _component("ribi_theme.py")
        if not theme_src.is_file():
            raise RuntimeError(f"Missing Ribi GTK theme helper: {theme_src}")
        write_file(DIR_ROOTFS / "usr/local/bin/ribi_theme.py", theme_src.read_text(), mode=0o644)
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-screenshot", '#!/bin/sh\nexec /usr/local/bin/ribi-screenshot.py "$@"\n', mode=0o755)
        write_file(DIR_ROOTFS / "usr/local/bin/ribi-launcher", '#!/bin/sh\nexec /usr/local/bin/ribi-launcher.py "$@"\n', mode=0o755)

        desktop_manifest = [
            ("ribi-app-menu.desktop", "Applications", "ribi-app-launcher", "view-app-grid", "System;Utility;", False),
            ("ribi-file-explorer.desktop", "Ribi File Explorer", "ribi-file-explorer %U", "system-file-manager", "System;FileManager;", False),
            ("ribi-terminal.desktop", "Ribi Terminal", "ribi-terminal", "utilities-terminal", "System;TerminalEmulator;", False),
            ("zen-browser-ribi.desktop", "Zen Browser", "zen-browser %U", "zen-browser", "Network;WebBrowser;", False),
            ("obs-studio-ribi.desktop", "OBS Studio", "obs --disable-shutdown-check --profile ribi --collection ribi", "obs", "AudioVideo;Recorder;", False),
            ("ribi-screenshot.desktop", "Ribi Screenshot", "ribi-screenshot", "camera-photo", "Graphics;Utility;", False),
            ("ribi-calculator.desktop", "Calculator", "galculator", "accessories-calculator", "Utility;Calculator;", False),
            ("ribi-image-viewer.desktop", "Image Viewer", "ristretto %U", "image-x-generic", "Graphics;Viewer;", False),
            ("ribi-media-player.desktop", "Media Player", "celluloid %U", "multimedia-player", "AudioVideo;Player;", False),
            ("ribi-archive-manager.desktop", "Archive Manager", "file-roller %U", "package-x-generic", "Utility;Archiving;", False),
            ("ribi-text-editor.desktop", "Text Editor", "mousepad %F", "accessories-text-editor", "Utility;TextEditor;", False),
            ("ribi-document-viewer.desktop", "Document Viewer", "zathura %U", "application-pdf", "Office;Viewer;Graphics;", False),
            ("ribi-audio-editor.desktop", "Audacity", "audacity %F", "audio-editor", "AudioVideo;Audio;Editor;", False),
            ("ribi-control-center.desktop", "Ribi Control Center", "ribi-control-center.py", "preferences-system", "System;Settings;", False),
            ("ribi-installer.desktop", "Install Ribi OS", "ribi-terminal -e sudo -n /usr/local/bin/ribi-installer", "system-software-install", "System;", False),
            ("ribi-edit.desktop", "Ribi Code Editor", "ribi-edit %F", "nvim", "Utility;TextEditor;Development;", True),
            ("ribi-snake.desktop", "Ribi Snake", "ribi-snake", "applications-games", "Game;", True),
            ("ribi-2048.desktop", "Ribi 2048", "ribi-2048", "applications-games", "Game;", True),
        ]

        for fname, name, exec_cmd, icon, cats, term in desktop_manifest:
            content = (
                f"[Desktop Entry]\nVersion=1.0\nType=Application\nName={name}\nExec={exec_cmd}\n"
                f"Icon={icon}\nCategories={cats}\nTerminal={'true' if term else 'false'}\nStartupNotify=false\n"
            )
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
                   "grep", "head", "modprobe", "insmod", "find", "depmod", "chroot", "tr",
                   "pivot_root"]
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
        if not re.search(r"(?:^|\n)pivot_root(?:\n|$)", available_text):
            raise RuntimeError("Initramfs BusyBox does not advertise the required pivot_root applet")
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
            # xz with the x86 BCJ filter roughly halves the live image versus the
            # old gzip level 1, and the kernel ships CONFIG_SQUASHFS_XZ=y.
            "-comp", "xz", "-b", "1M", "-Xbcj", "x86",
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
        for rel in ("sbin/ribi-init","usr/local/bin/ribisvc","usr/local/bin/ribi-pkg","usr/local/bin/ribi","usr/local/bin/ribi-installer","usr/local/bin/ribi-doctor","usr/local/bin/ribi-edit","usr/local/bin/ribi-snake","usr/local/bin/ribi-2048"):
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
            for rel in ("usr/bin/Xorg", "usr/bin/startx", "usr/bin/galculator", "usr/bin/ristretto", "usr/bin/celluloid", "usr/bin/file-roller", "usr/bin/mousepad", "usr/bin/zathura", "usr/bin/audacity", "usr/bin/obs", "opt/zen/zen", "opt/zen/zen-rt", "usr/local/bin/zen-browser", "usr/local/bin/ribi-shell.py", "usr/local/bin/ribi-screenshot.py", "usr/local/bin/ribi-wm.py", "usr/local/bin/ribi-control-center.py", "usr/local/bin/ribi-dock", "usr/share/backgrounds/ribi-wallpaper.png"):
                if not (DIR_ROOTFS / rel).exists():
                    raise RuntimeError(f"Validation Failed: Ribi desktop payload missing: /{rel}")
            for rel in ("usr/share/glib-2.0/schemas/gschemas.compiled", "usr/share/mime/mime.cache"):
                if not (DIR_ROOTFS / rel).exists():
                    raise RuntimeError(f"Validation Failed: GLib runtime database missing: /{rel}")

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
