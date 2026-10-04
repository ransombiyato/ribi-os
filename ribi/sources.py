"""Embedded target-OS source payloads.

Each ``SRC_*`` constant is the exact text written into the generated Ribi OS
rootfs or initramfs. The scripts live as real files under ``ribi/sources/`` so
they can be edited, linted, and reviewed on their own, then loaded here at
import time.
"""

from pathlib import Path

_SOURCES_DIR = Path(__file__).resolve().parent / "sources"


def _load(filename: str) -> str:
    return (_SOURCES_DIR / filename).read_text(encoding="utf-8")


# Native Ribi PID 1 supervisor -> /sbin/ribi-init
SRC_RIBI_INIT = _load("ribi-init.sh")
# Live-media resilient boot script -> initramfs /init
SRC_LIVE_INIT = _load("live-init.sh")
# Native service manager -> /usr/local/bin/ribisvc
SRC_RIBI_SVC = _load("ribisvc.py")
# Hardened .rpk package engine -> /usr/local/bin/ribi-pkg
SRC_RIBI_PKG = _load("ribi-pkg.py")
# Unified system controller -> /usr/local/bin/ribi
SRC_RIBI_CLI = _load("ribi-cli.py")
# Unified first-boot setup + disk installer -> /usr/local/bin/ribi-installer
SRC_RIBI_INSTALLER = _load("ribi-installer.py")
# Text editor -> /usr/local/bin/ribi-edit
SRC_RIBI_EDIT = _load("ribi-edit.py")
# Snake game -> /usr/local/bin/ribi-snake
SRC_RIBI_SNAKE = _load("ribi-snake.py")
# 2048 game -> /usr/local/bin/ribi-2048
SRC_RIBI_2048 = _load("ribi-2048.py")
# Desktop/app diagnostic -> /usr/local/bin/ribi-doctor
SRC_RIBI_DOCTOR = _load("ribi-doctor.py")
