# AGENTS.md

Repository-specific notes for working on ribi OS.

## What this repo is

The builder for **ribi OS**, a from-scratch x86_64 live/installable OS. The
builder assembles a bootable hybrid BIOS/UEFI ISO from an Alpine x86_64
bootstrap plus a bespoke-compiled Linux kernel and ribi's own userspace.

## Layout and where to change things

- `ribi/config.py` — everything static: OS identity/version, kernel version and
  URLs, Alpine mirrors, workspace paths, the APK seed package sets, and the
  host tool requirement lists. Most tweaks start here.
- `ribi/builder.py` — `RibiMasterBuilder` with 12 numbered `stage_N_*` methods,
  run in order by `run()`. This is the file to edit for build logic. It is
  still the largest module; keep the stage numbering.
- `ribi/sources/` — the actual target-OS programs (PID 1, service manager,
  package engine, installer, games). Edited like normal files, loaded by
  `ribi/sources.py`. These are written verbatim into the generated OS.
- `ribi/payloads/` — desktop/session config written into the OS (lightdm,
  xfce4 panel/desktop XML, xorg.conf, session wrappers). Loaded via
  `_payload()` in `builder.py`.
- `ribi/kernel_config.py` — the kernel `.config` used for the build.
- `ribi/assets/wallpaper.png` — bundled desktop wallpaper.
- `legacy/ribi-iso-builder.monolith.py` — the original single-file builder,
  kept as the reference for the split. Tests compare against it.

## Commands

```bash
python3 -m pytest tests/ -q                     # split-integrity tests
python3 -m py_compile ribi/*.py                 # syntax check
python3 ribi-iso-builder.py --version           # CLI smoke test
sudo -E python3 ribi-iso-builder.py --check-deps  # host tool audit (installs missing tools)
sudo -E python3 ribi-iso-builder.py --build       # full ISO build (~30+ min)
sudo -E python3 ribi-iso-builder.py --boot-test   # QEMU boot check
sudo -E python3 ribi-iso-builder.py --clean       # purge workspace
```

The build needs root and a POSIX filesystem for `ribi-build-workspace/`
(never Android FUSE). CI in `.github/workflows/tests.yml` runs the unit tests
on every push and attempts a full ISO build on `main`.

## Invariants to preserve

- The split is behavioural: `legacy/` must stay, and
  `tests/test_package.py` must keep passing (it pins that every original
  top-level definition survived and that `sources/` + `payloads/` match the
  original embedded strings byte-for-byte).
- `builder.py` cross-module imports are explicit; when adding a name used
  across modules, add the import rather than relying on a wildcard.
- Keep the workspace path check in `clean_workspace()` — it refuses to delete
  anything that is not `ribi-build-workspace/`.

## Style

- Target-side scripts (in `sources/`) are POSIX `sh` or standalone Python 3
  using only the standard library; the target OS has no systemd and no
  desktop framework assumptions beyond what is staged.
- Keep build logic deterministic and offline-friendly: downloads are
  SHA-256 verified and cached; prefer adding to `config.py` over hardcoding
  URLs or versions inside `builder.py`.
