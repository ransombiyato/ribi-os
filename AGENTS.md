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

## Native desktop components (`ribi/components/`)

These are Ribi's own GTK3/Python programs written into the OS. `ribi-shell.py`
is the session owner: it paints the wallpaper, starts `ribi-dock`, and restarts
the dock if it exits. `ribi-wm.py` launches the ICCCM/EWMH window manager
(Openbox) plus an optional compositor.

Gotchas learned the hard way:

- **No stock icon themes.** The Alpine v3.24 GTK3 stack ships only the XPM
  gdk-pixbuf loader, so themed/builtin PNG icon lookups abort GTK. Draw all
  glyphs with Cairo via the shared `ribi/components/ribi_theme.py` helper
  instead of `Gtk.Image.new_from_icon_name`. `py3-cairo` must be in
  `TARGET_APK_PACKAGES_DESKTOP`.
- **Do not size/position a window from `get_size()` before it is mapped.** It
  returns a placeholder (the dock came up 10x10 and invisible). Use
  `set_size_request` for the width and a `size-allocate` handler for the real
  height before `move()`.
- **Never edit `ribi/sources/` or `ribi/payloads/` casually.** They are pinned
  byte-for-byte to `legacy/ribi-iso-builder.monolith.py` by
  `tests/test_package.py`; changing them requires updating the legacy reference
  too. Prefer adding new files over editing the pinned ones.
- **The sysroot cache is keyed by a package-list hash.** `stage_3_*` stores a
  `.sysroot_packages` signature next to `.sysroot_ready`; adding a package to
  `config.py` invalidates and rebuilds the sysroot automatically.
- **An Openbox theme needs its button glyphs.** A theme dir with only a
  `themerc` is silently rejected (`Unable to load the theme 'Ribi'`) and Openbox
  falls back to Clearlooks, so titlebars stay light. `builder.py` copies the
  stock `*.xbm` glyphs from `Bear2`/`Default` into `usr/share/themes/Ribi/openbox-3`
  and the themerc only recolours them; use `Flat Solid` title fills so the
  colour matches the Ribi tokens exactly.
