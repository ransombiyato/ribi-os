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
- **`legacy/ribi-iso-builder.monolith.py`** — the original single-file builder.
  It was kept as the reference for the split, and has now been deleted because
  the modular package fully replaces it and the file only invited edits to the
  wrong copy. Tests no longer compare against it; they pin behaviour instead.

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

- The split is behavioural: `tests/test_package.py` must keep passing. It no
  longer compares against the removed monolith; it pins that the package and
  every source/component/payload compiles, that the app catalogs match the
  package set, and that the compositor never paints a window below full opacity.
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
- **Never edit `ribi/sources/` or `ribi/payloads/` casually.** They are written
  verbatim into the image, so a change there is a change to the OS. They are no
  longer pinned byte-for-byte to a legacy reference (that file was deleted), so
  edit them directly and add or update a test that pins the new behaviour.
- **The sysroot cache is keyed by a package-list hash.** `stage_3_*` stores a
  `.sysroot_packages` signature next to `.sysroot_ready`; adding a package to
  `config.py` invalidates and rebuilds the sysroot automatically.
- **An Openbox theme needs its button glyphs.** A theme dir with only a
  `themerc` is silently rejected (`Unable to load the theme 'Ribi'`) and Openbox
  falls back to Clearlooks, so titlebars stay light. `builder.py` copies the
  stock `*.xbm` glyphs from `Bear2`/`Default` into `usr/share/themes/Ribi/openbox-3`
  and the themerc only recolours them; use `Flat Solid` title fills so the
  colour matches the Ribi tokens exactly.
- **Picom holds the composite selection.** Only one compositor can run per X
  server; a second `picom` exits with `Another composite manager is already
  running`. That message is the positive signal that compositing (shadows,
  rounded corners, fading from `etc/xdg/ribi/picom.conf`) is active. `ribi-wm.py`
  starts it before Openbox and stops it on exit.
- **Terminal palette lives in X resources, not app-defaults.** xterm/lxterminal
  read `/etc/X11/Xresources/ribi` via `xrdb -merge` (done in `ribi-wm.py`), so
  they match the Ribi tokens without replacing the stock `XTerm` app-defaults.
  `usr/local/bin/ribi-terminal` prefers lxterminal and falls back to xterm.
- **The launcher's focused search entry eats Return.** A `Gtk.Entry` consumes
  Enter and emits `activate`, so a window-level key handler never sees it and
  "Enter to launch" silently does nothing. Connect `activate` on the entry too
  and guard with a one-shot flag, because the same Return can also reach the
  window handler and would otherwise start the app twice.
- **`grab_focus` before a window is mapped is dropped.** Under Openbox the
  launcher could be visible while the search entry was not yet focused, so the
  first keystrokes went nowhere. `present()`, `set_focus()`, then retry
  `grab_focus()` on an idle callback and a short timeout.
- **QEMU `input-send-event` is unreliable for mouse/key sequences.** Pointer
  moves/clicks and repeated Super-key chords can be silently dropped or arrive
  out of order, which makes a broken UI look like a code bug. Verify GUI
  behaviour with `xdotool` inside the chroot against `Xvfb` (bind `/dev/pts`,
  `/tmp/.X11-unix`, and a real `/dev/null`) before blaming the code.
- **GLib apps abort unless the schema database is compiled.** Alpine ships the
  `.gschema.xml` files but never `gschemas.compiled`, so any app that reads a
  setting (Celluloid, File Roller) crashes with "No GSettings schemas are
  installed on the system". Stage 5 must run `glib-compile-schemas` in a chroot
  (there is no host binary), plus `update-mime-database` for `xdg-open` and
  `update-desktop-database` for the app-menu cache.
- **Chroot GUI tests need real `/proc` for bwrap.** glycin (gdk-pixbuf's modern
  image backend) sandboxes each decode with `bwrap --unshare-all`. With no
  `/proc` in the chroot, or on a host that forbids unprivileged user namespaces,
  the loader exits early and *every* image load fails with "Couldn't recognize
  the image file format" — even a valid PNG. Mount `/proc` before testing; glycin
  then falls back to "running without sandbox" and decoding works. This is a
  test-harness artifact, not an image bug.
- **The compositor can make windows invisible.** `ribi-picom.conf` must keep
  `inactive-opacity = 1.0`, `active-opacity = 1.0`, `unredir-if-possible =
  false`, and `fading = false`. With a sub-1.0 inactive opacity, or with
  unredirecting enabled, unfocused windows stopped being painted and only
  reappeared for a moment when clicked. `detect-rounded-corners` and a non-zero
  `corner-radius` are also avoided on the software xrender path. Keep the config
  conservative; cosmetics are not worth a disappearing window.
- **OBS 32's hybrid MP4 muxer segfaults on this image.** Recording with the
  default fragmented "hybrid" MP4/MOV output crashes right after
  `Writing Hybrid MP4/MOV file`. The shipped OBS profile sets `RecFormat2=mkv`
  (the mature, crash-safe muxer). The "Video Capture Device (V4L2)" source also
  needs `libv4l2`, which only `v4l-utils` provides; OBS depends on the soname
  `libv4l2.so.0`, which the resolver cannot map back to a package, so
  `v4l-utils` must stay in `TARGET_APK_PACKAGES_APPS`.
- **OBS reads the recording container from `[SimpleOutput]`, not `[Output]`.**
  A `RecFormat2=mkv` key under `[Output]` is silently ignored and OBS records
  hybrid MP4. The profile's `[General] Name` must match the profile directory
  name (`ribi`), and OBS selects the profile/scene collection from `user.ini`
  (`[Basic]`), *not* `global.ini`. The reliable fix is to name both on the
  command line: every launch path runs
  `obs --disable-shutdown-check --profile ribi --collection ribi`. A shipped
  scene collection (`ribi/payloads/obs-scene-collection.json`, `name=ribi`) is
  written to `basic/scenes/ribi.json` so the first launch is not "Untitled".
- **OBS's `FirstRun` must stay `false`.** With `FirstRun=true` OBS launches its
  auto-configuration wizard on first start, which re-detects the encoder and
  container and discards the shipped `RecFormat2=mkv` fix.
- **Zen Browser cannot run on musl + gcompat.** The official Zen tarball is a
  glibc Firefox build. `libc.so.6` is a symlink to `libgcompat.so.0`, and
  gcompat is missing several glibc-fortify symbols Zen's binary imports
  (`__vfprintf_chk`, `__strcat_chk`, `__vsnprintf_chk`, `__longjmp_chk`,
  `__memcpy_chk`, `isinf`, `isnan`). A musl shim exporting those forwards them
  to musl and clears `ldd`'s relocation errors, but Zen then deadlocks in early
  init on a `FUTEX_WAIT_PRIVATE` and never spawns a child. gcompat cannot
  provide Firefox's threading, so the bundled browser does not start; replace
  it with a musl-native browser or a real glibc rootfs before relying on it.
- **A chroot of the built rootfs can run the real guest apps for testing.**
  `sudo chroot ribi-build-workspace/rootfs <binary>` with `DISPLAY` pointed at a
  host Xvfb exercises the actual image binaries (GTK, X11, OBS, …). Bind `/proc`,
  `/dev`, `/dev/pts`, `/tmp/.X11-unix`, and mount a `tmpfs` on `/dev/shm` or
  Chromium/Firefox hang. Use `HOME=/home/ribi` so apps pick up the shipped
  config. Apps run as `root` here while the image runs them as `ribi`, so treat
  a chroot run as a smoke test, not a full desktop session.
- **The doctor's app smoke test must tolerate warnings.** Every GTK app on this
  image logs benign warnings (missing AT-SPI bus, DRI3 unavailable, an optional
  icon), so `ERROR_MARKERS` in `ribi-doctor.py` lists only hard failures — a
  bare `"error"` marker fails every app. `subprocess.TimeoutExpired` can return
  `str`, `bytes`, or `None` for `stdout`/`stderr`; normalise each stream before
  joining or the doctor crashes with `TypeError`. A GUI app still alive after
  the timeout is healthy, not a failure. The doctor's OBS probe also needs
  `--profile ribi --collection ribi`.
- **The boot handoff must make /sysroot the mount-namespace root.** A bare
  `exec chroot /sysroot` leaves the initramfs root above the target, and
  `create_user_ns()` then rejects `unshare(CLONE_NEWUSER)` with `EPERM`; the
  effect is that every app sandbox (Zen's content sandbox, bwrap, glycin) fails
  after boot even though it works inside the initramfs. `pivot_root` fixes the
  root but cannot run from the initramfs `rootfs` (its root has no parent mount)
  or on the live overlay root, so it fails with `EINVAL`. Replay the kernel's own
  `prepare_namespace`/`switch_root` sequence instead — in PID 1, `cd /sysroot`,
  `mount --move . /`, then `exec chroot . <init>` — which works on the
  initramfs rootfs, the live overlay root, and the installed ext4 root.
  Reproduce headlessly: boot the initramfs with `rdinit=/bin/sh`, set up a
  mount, run the sequence, and check `unshare -U true`.
- **The desktop right-click menu comes from Openbox, not the dock.**
  `/etc/xdg/openbox/menu.xml` is loaded via `rc.xml`'s `<file>menu.xml</file>`
  (the ribi user has no `~/.config/openbox/menu.xml` override). Stage 5 must
  overwrite the stock Alpine menu with `ribi/payloads/openbox-menu.xml`, or the
  dropdown lists distro apps the image does not ship.
- **Setup and the installer are one program.** `ribi-setup.py` was merged into
  `ribi-installer.py`; the single `ribi-installer` runs the whole wizard
  (identity, networking, then erase-install / persistence / ram-only). Do not
  add a second setup entry point.
- **The dock's window list must not block the GTK loop.** `ribi-dock.py` polls
  the EWMH window list with `xdotool`/`xprop` on a worker thread and applies the
  result on an idle callback. Never call those tools synchronously from the main
  loop: the panel freezes while an app is busy starting.
- **`xprop` prints string properties with surrounding quotes.** A taskbar built
  straight from `xprop -id <id> _NET_WM_NAME` shows `"Title"` with literal quote
  characters. `_xprop` in `ribi-dock.py` strips a leading/trailing `"`; keep that
  when reading any string property (`WM_NAME`, `_NET_WM_NAME`).
- **Maximise must go through the window manager.** Resizing a window to
  `100% 100%` at `0,0` leaves Openbox's frame hanging off-screen. Use the EWMH
  state (`xdotool windowstate --add MAXIMIZED_VERT MAXIMIZED_HORZ`) so the WM
  accounts for decorations.
- **Verify compositor changes with the real config, not by eye.** The opacity
  regression is easy to reproduce headlessly: run Xvfb + openbox + the shipped
  `ribi-picom.conf` + picom, open two white windows, focus one, and compare the
  mean brightness of each window from a root screendump. Fixed config gives a
  ratio of ~1.0 (both ~0.93); the old options (`unredir-if-possible = true`,
  `inactive-opacity = 0.97`) render both windows at ~0.026 (black).
- **`ribi.serial=1` replaces the GUI, it does not accompany it.** `/sbin/ribi-init`
  uses `if ribi.serial=1 … elif … GUI`, so a serial-console boot gives a root
  shell on `ttyS0` and never starts Xorg. To inspect the desktop, boot normally
  and use the QEMU monitor `screendump`, or start Xorg by hand in the serial
  shell.

