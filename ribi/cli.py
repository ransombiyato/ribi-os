"""Command-line entrypoint and utility actions (clean, run, boot-test).

This module is part of the Ribi OS ISO builder package. Split out of the
original monolithic builder for readability; behaviour is unchanged.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from .builder import RibiMasterBuilder
from .config import ISO_OUTPUT, OS_CODENAME, OS_NAME, OS_VERSION, STORAGE_ROOT, WORK_DIR
from .deps import cic_host_dependencies
from .logging_utils import BuildLogger


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
