# ribi OS

ribi OS is a small, from-scratch x86_64 live operating system with its own
PID 1, service manager, package engine, installer, and desktop shell. The
whole OS is produced by a single Python build engine that assembles a
bootable hybrid BIOS/UEFI ISO.

This repository contains the ISO builder. Running it produces
`ribi-os-bulbQT-x86_64.iso`, a live image that boots to the ribi desktop and
can be installed to disk with `ribi-installer`.

## Repository layout

```
ribi-iso-builder.py        Thin launcher; keeps the original CLI.
ribi/
  config.py                OS identity, kernel spec, mirrors, paths, package sets.
  logging_utils.py         BuildLogger + safe subprocess/file helpers.
  download.py              Atomic, SHA-256 verified downloads.
  apk.py                   APKINDEX parsing, dependency closure, safe extraction.
  audit.py                 ELF/PE, bzImage, SquashFS and El Torito inspectors.
  deps.py                  Host toolchain audit and C.I.C. acquisition.
  kernel_config.py         Bespoke Linux kernel .config profile.
  builder.py               The 12-stage master build engine + ISO validation.
  cli.py                   Command-line entrypoint (clean/run/boot-test).
  sources.py               Loads the embedded target-OS scripts below.
  sources/                 The actual files written into the generated OS:
    ribi-init.sh             native PID 1 supervisor  (/sbin/ribi-init)
    live-init.sh             initramfs /init (overlayfs + fallback)
    ribisvc.py               service manager
    ribi-pkg.py              .rpk package engine
    ribi-cli.py              unified system controller
    ribi-setup.py            first-boot setup
    ribi-installer.py        disk installer
    ribi-edit.py             text editor
    ribi-snake.py / ribi-2048.py   games
  assets/wallpaper.png     Bundled desktop wallpaper.
legacy/                    The original single-file builder, kept for reference.
```

The builder was split out of one 5092-line file into the modules above with no
behavioural change; the embedded target scripts are now real, editable files
instead of Python string literals.

## Usage

```bash
# Show options
python3 ribi-iso-builder.py --help

# Check host build dependencies only
sudo python3 ribi-iso-builder.py --check-deps

# Build the ISO (needs root; cross-builds x86_64 from ARM64 too)
sudo python3 ribi-iso-builder.py --build

# Boot-test the result in QEMU
sudo python3 ribi-iso-builder.py --boot-test

# Run it interactively
sudo python3 ribi-iso-builder.py --run

# Remove all build artifacts
sudo python3 ribi-iso-builder.py --clean
```

Requirements: Python 3.10+, a POSIX filesystem for the workspace (not Android
FUSE), and the host tools listed in `ribi/config.py`
(`xorriso`, `mksquashfs`, `grub-mkrescue`, `cpio`, `rsync`, `mtools`, etc.).

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the plan to take ribi OS from a working live
image to a polished, release-ready desktop OS.
