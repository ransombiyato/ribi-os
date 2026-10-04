# ribi OS roadmap

Goal: turn the current working live image into a polished, release-ready
desktop OS that feels as friendly and finished as Ubuntu or Windows, while
keeping ribi's from-scratch identity (own PID 1, service manager, package
engine, and shell).

Status legend: `[x]` done, `[~]` in progress, `[ ]` planned.

---

## Phase 0 - Structure and foundation

- [x] Split the 5092-line monolith into readable modules (`ribi/`).
- [x] Externalize the 10 embedded target scripts into `ribi/sources/`.
- [x] Bundle the desktop wallpaper as a real asset.
- [x] Preserve the original CLI and behaviour (verified equivalent).
- [ ] Add automated tests that pin build-time behaviour (imports, APK
      resolution, ELF audit, source round-trip).

## Phase 1 - Boot reliability and build quality

The OS must boot every time, on real hardware and in VMs.

- [ ] Fully pin the Alpine base (exact version + APKINDEX signature check on
      every package) so builds are reproducible.
- [ ] Make every stage resumable and idempotent; a failed stage should not
      require a full rebuild.
- [ ] Speed up builds: parallel package extraction, optional ccache for the
      kernel, cached squashfs when inputs are unchanged.
- [ ] Add GitHub Actions: build the ISO, upload it as an artifact, and run a
      QEMU boot-test (BIOS + UEFI) on every push.
- [ ] Emit a build manifest (kernel version, package versions, hashes) inside
      the ISO and next to it.

## Phase 2 - Base system and hardware support

- [ ] Ship `linux-firmware` so Wi-Fi, Ethernet, and GPUs work out of the box.
- [ ] Broaden DRM/KMS drivers (Intel, AMD, basic NVIDIA) plus common
      touchpad, USB, and webcam support.
- [ ] Networking: connection manager with Wi-Fi selection, DHCP, VPN basics.
- [ ] Audio: working mixer, default sink/source, volume keys.
- [ ] Bluetooth and printer support.
- [ ] Power management: battery indicator, suspend/resume, brightness keys.

## Phase 3 - Desktop experience (the "looks nice" phase)

Target: a cohesive, modern desktop that feels intentional, not assembled.

- [ ] Design system: one font family, one icon set, one accent color, and a
      consistent dark/light theme across every app.
- [ ] Compositing window manager (picom) for shadows, rounded corners, and
      smooth transparency.
- [ ] A real panel/dock: application menu, open-window list, system tray,
      clock/calendar, and status indicators (network, volume, battery).
- [ ] Application launcher with search (replaces the plain menu).
- [ ] Notifications with a daemon and a small popup theme.
- [ ] Lock screen and login greeter.
- [ ] Settings app: display, sound, network, appearance, users, power.
- [ ] File manager.
- [ ] Default app set: browser, terminal, text editor, calculator, image
      viewer, media player, archive tool, screenshot tool.
- [ ] Boot experience: clean GRUB theme + a graphical boot splash instead of
      raw kernel text.
- [ ] A proper About page with logo, version, and credits.

## Phase 4 - Installer, persistence, and accounts

- [ ] Guided installer: disk selection, partitioning (erase / alongside /
      manual), user account + password, hostname, timezone, locale.
- [ ] Persistent live mode finished and documented (keep changes on USB).
- [ ] Optional full-disk encryption.
- [ ] First-boot setup flow (welcome screen, language, Wi-Fi, account).

## Phase 5 - Software and updates

- [ ] Host a signed package repository and make `ribi-pkg` install/update from
      it with dependency resolution.
- [ ] A simple software store UI on top of `ribi-pkg`.
- [ ] Support Flatpak/AppImage for third-party apps.
- [ ] System update flow with rollback on failure.

## Phase 6 - Branding, docs, and release engineering

- [ ] Logo, wallpapers (light/dark), boot menu art, and installer art.
- [ ] Version scheme and release names; release notes generator.
- [ ] Signed ISOs, published checksums, and a download page.
- [ ] User and developer documentation.
- [ ] Accessibility (screen reader basics, large fonts, high contrast) and
      localization.

## Phase 7 - Quality and performance

- [ ] VM test matrix (BIOS, UEFI, BIOS+persistent, UEFI+install) in CI.
- [ ] Boot-time and memory budget targets.
- [ ] Hardware test reports from real machines.
- [ ] Crash reporting and recovery console.

---

## Immediate next steps

1. Phase 0 tests + Phase 1 CI workflow, so every change is validated.
2. Phase 2 firmware/driver packages, so the OS is usable on real hardware.
3. Phase 3 design system and panel, so it looks like a finished product.

The structure from Phase 0 makes all of the above safe to do: config lives in
`ribi/config.py`, target-side scripts are real files in `ribi/sources/`, and
build logic is isolated in `ribi/builder.py`.
