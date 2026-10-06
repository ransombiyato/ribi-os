"""Regression tests for the ribi OS builder.

These pin the behaviour the OS depends on rather than a byte-for-byte copy of a
historical file:

  * the builder package imports and compiles,
  * the target-OS sources and payloads that get written into the image exist,
  * the package seed sets include the apps and runtimes the desktop needs,
  * the compositor config never paints a window at less than full opacity,
  * the desktop app catalogs (launcher + dock) agree with the package set.

The original single-file builder was removed once the modular package replaced
it, so the tests no longer compare against a legacy reference.
"""

import importlib
import json
import py_compile
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MODULES = [
    "config", "logging_utils", "download", "apk", "audit",
    "deps", "kernel_config", "builder", "cli", "sources",
]

COMPONENTS = [
    "ribi-dock.py",
    "ribi-file-explorer.py",
    "ribi-code-editor.py",
    "ribi-app-prompt.py",
    "ribi-screenshot.py",
    "ribi-shell.py",
    "ribi-wm.py",
    "ribi-control-center.py",
    "ribi-launcher.py",
    "ribi_theme.py",
]

# The target-OS programs written into /usr/local/bin by the builder.
SOURCES = [
    "ribi-init.sh",
    "live-init.sh",
    "ribisvc.py",
    "ribi-pkg.py",
    "ribi-cli.py",
    "ribi-installer.py",
    "ribi-edit.py",
    "ribi-snake.py",
    "ribi-2048.py",
    "ribi-doctor.py",
]

# Desktop/session config written into the image by the builder.
PAYLOADS = [
    "lightdm.conf",
    "ribi-desktop-session.sh",
    "ribi-dump-session.sh",
    "ribi-xfce-session.sh",
    "ribi-session-wrapper.sh",
    "ribi-direct-xsession.sh",
    "ribi-wallpaper-viewer.sh",
    "ribi-gtk-probe.py",
    "ribi-user-handoff.py",
    "ribi-direct-desktop.sh",
    "ribi-user-desktop.sh",
    "openbox-autostart.sh",
    "ribi-visible-session.sh",
    "xorg.conf",
    "ribi-xfce-clients.sh",
    "ribi-netup.sh",
    "openbox-ribi-themerc",
    "ribi-Xresources",
    "ribi-picom.conf",
    "ribi-terminal.sh",
]


def test_package_imports():
    import ribi
    assert ribi.OS_NAME == "Ribi OS"
    assert ribi.RibiMasterBuilder is not None


def test_all_modules_import():
    for mod in MODULES:
        importlib.import_module(f"ribi.{mod}")


def test_legacy_monolith_is_gone():
    """The modular package replaced the monolith; it must not creep back."""
    assert not (REPO / "legacy").exists()


def test_sources_present_and_compile():
    sources = REPO / "ribi" / "sources"
    for name in SOURCES:
        path = sources / name
        assert path.is_file(), f"missing target source: {name}"
        if name.endswith(".py"):
            py_compile.compile(str(path), doraise=True)


def test_sources_module_exposes_every_source():
    from ribi import sources
    for attr in ("SRC_RIBI_INIT", "SRC_LIVE_INIT", "SRC_RIBI_SVC", "SRC_RIBI_PKG",
                 "SRC_RIBI_CLI", "SRC_RIBI_INSTALLER", "SRC_RIBI_EDIT",
                 "SRC_RIBI_SNAKE", "SRC_RIBI_2048", "SRC_RIBI_DOCTOR"):
        value = getattr(sources, attr)
        assert isinstance(value, str) and value.strip(), f"{attr} is empty"
    assert not hasattr(sources, "SRC_RIBI_SETUP"), (
        "setup was merged into the installer; SRC_RIBI_SETUP must be gone"
    )


def test_components_present_and_compile():
    components = REPO / "ribi" / "components"
    for name in COMPONENTS:
        path = components / name
        assert path.is_file(), f"missing native component: {name}"
        py_compile.compile(str(path), doraise=True)


def test_payloads_present():
    payloads = REPO / "ribi" / "payloads"
    for name in PAYLOADS:
        assert (payloads / name).is_file(), f"missing payload: {name}"


def test_bundled_wallpaper_is_a_real_png():
    from ribi import config
    assert config.WALLPAPER_SOURCE.is_file()
    header = config.WALLPAPER_SOURCE.read_bytes()[:8]
    assert header == b"\x89PNG\r\n\x1a\n", "wallpaper must be a real PNG"


def test_cli_exposes_main():
    from ribi import cli
    assert callable(cli.main)


# ---- behaviour the desktop relies on --------------------------------------

def test_default_desktop_apps_present_and_catalogued():
    """Everyday apps must be installed, validated, and searchable."""
    from ribi import config

    desktop_apps = ("galculator", "ristretto", "celluloid", "file-roller",
                    "mousepad", "xdg-utils", "zathura", "zathura-pdf-poppler")
    for app in desktop_apps:
        assert app in config.TARGET_APK_PACKAGES_DESKTOP, f"{app} missing from desktop packages"

    for app in ("audacity", "obs-studio", "v4l-utils"):
        assert app in config.TARGET_APK_PACKAGES_APPS, f"{app} missing from apps packages"

    source = (REPO / "ribi" / "builder.py").read_text(encoding="utf-8")
    for binary in ("usr/bin/galculator", "usr/bin/ristretto", "usr/bin/celluloid",
                   "usr/bin/file-roller", "usr/bin/mousepad", "usr/bin/zathura",
                   "usr/bin/audacity", "usr/bin/obs"):
        assert binary in source, f"builder does not validate {binary}"

    launcher = (REPO / "ribi" / "components" / "ribi-launcher.py").read_text(encoding="utf-8")
    dock = (REPO / "ribi" / "components" / "ribi-dock.py").read_text(encoding="utf-8")
    for command in ("galculator", "ristretto", "celluloid", "file-roller",
                    "mousepad", "zathura", "audacity"):
        assert command in launcher, f"launcher catalog missing {command}"
        assert command in dock, f"dock catalog missing {command}"


def test_obs_defaults_use_crash_safe_muxer():
    """OBS must not default to the hybrid MP4 muxer that segfaulted.

    OBS only reads the recording container from [SimpleOutput]; a key placed
    under [Output] is silently ignored and the hybrid MP4 muxer is used. OBS
    also only loads a profile whose [General] Name matches, and the profile and
    scene collection must be named on the command line or OBS falls back to a
    fresh "Untitled" profile.
    """
    source = (REPO / "ribi" / "builder.py").read_text(encoding="utf-8")
    assert "[SimpleOutput]" in source and "RecFormat2=mkv" in source, \
        "OBS should default to the mkv muxer under [SimpleOutput]"
    # The mkv key must live under [SimpleOutput], not the ignored [Output] block.
    simple = source.split("[SimpleOutput]", 1)[1].split("[", 1)[0]
    assert "RecFormat2=mkv" in simple, "RecFormat2 must be under [SimpleOutput]"
    assert "Name=ribi" in source, "the shipped OBS profile must be named ribi"
    assert "FirstRun=false" in source, "OBS first-run wizard must be disabled"
    # Every launch path must select the shipped profile and scene collection.
    for rel in ("builder.py", "components/ribi-dock.py", "components/ribi-launcher.py"):
        text = (REPO / "ribi" / rel).read_text(encoding="utf-8")
        assert "--profile ribi --collection ribi" in text, \
            f"{rel} must launch OBS with the ribi profile/collection"
    scene = json.loads((REPO / "ribi" / "payloads" / "obs-scene-collection.json").read_text())
    assert scene["name"] == "ribi", "the scene collection payload must be named ribi"


def test_v4l2_runtime_is_installed_for_obs():
    from ribi import config
    assert "v4l-utils" in config.TARGET_APK_PACKAGES_APPS


def test_compositor_never_makes_windows_transparent():
    """Regression: unfocused windows went invisible with sub-1.0 opacity."""
    conf = (REPO / "ribi" / "payloads" / "ribi-picom.conf").read_text(encoding="utf-8")
    for option in ("inactive-opacity = 1.0", "active-opacity = 1.0",
                   "frame-opacity = 1.0", "unredir-if-possible = false"):
        assert option in conf, f"compositor config must set '{option}'"
    assert "inactive-opacity = 0." not in conf
    assert "unredir-if-possible = true" not in conf


def test_doctor_reports_healthy_apps_without_false_failures():
    """The doctor's app smoke test must not crash or flag benign warnings.

    subprocess.TimeoutExpired can hand back str/bytes/None for stdout/stderr; a
    GUI app that is still alive after the 8s timeout is healthy. A bare "error"
    marker also matches benign GTK warnings, so the marker list stays specific.
    """
    src = (REPO / "ribi" / "sources" / "ribi-doctor.py").read_text(encoding="utf-8")
    # The timeout branch must normalise each stream independently.
    assert 'isinstance(stream, bytes)' in src, "TimeoutExpired streams must be normalised"
    assert 'result.stdout or ""' in src, "non-timeout output must stay str"
    # Specific markers only; a bare "error" would fail every GTK app.
    assert '"error"' not in src.split("ERROR_MARKERS", 1)[1].split(")", 1)[0], \
        "ERROR_MARKERS must not contain a bare 'error'"
    assert "cannot open display" in src and "segfault" in src
    # The OBS probe must select the shipped profile so it is not flagged.
    assert '"--profile", "ribi"' in src, "doctor must probe OBS with the ribi profile"


def test_installer_is_the_single_setup_flow():
    """Setup and the disk installer must be one program, not two."""
    source = (REPO / "ribi" / "builder.py").read_text(encoding="utf-8")
    assert "ribi-setup" not in source, "ribi-setup must not be installed or catalogued"
    installer = (REPO / "ribi" / "sources" / "ribi-installer.py").read_text(encoding="utf-8")
    assert "def wizard" in installer, "the installer must own the guided setup wizard"
    assert "def prepare_persistence" in installer, "the wizard must offer the data/persistence mode"


def test_installer_matches_alpine_wording():
    """The questions and their wording must follow Alpine's setup-* family."""
    installer = (REPO / "ribi" / "sources" / "ribi-installer.py").read_text(encoding="utf-8")
    for marker in (
        "def ask_yesno",
        "Enter system hostname (fully qualified form",
        "Which timezone are you in?",
        "Select keyboard layout:",
        "Which NTP client to run?",
        "Which ssh server?",
        "Which one do you want to initialize?",
        "DNS nameserver(s)?",
        "Which disk(s) would you like to use?",
        "How would you like to use it? ('sys', 'data' or '?')",
        "WARNING: Erase the above disk(s) and continue?",
    ):
        assert marker in installer, f"installer missing Alpine-style wording: {marker!r}"


def test_boot_applies_console_keymap():
    """The keymap chosen at setup must be loaded on boot, not just written."""
    init = (REPO / "ribi" / "sources" / "ribi-init.sh").read_text(encoding="utf-8")
    assert "/etc/conf.d/keymaps" in init, "boot must read the saved keymap"
    assert "loadkmap" in init, "boot must load the console keymap"
    assert "gzip -dc" in init, "kbd keymaps are gzipped and must be decompressed"


def test_dock_has_open_window_taskbar():
    dock = (REPO / "ribi" / "components" / "ribi-dock.py").read_text(encoding="utf-8")
    for marker in ("def list_windows", "def window_action", "def refresh_tasks",
                   "xdotool", "getactivewindow", "windowminimize", "windowclose"):
        assert marker in dock, f"dock taskbar missing {marker}"
    assert "threading.Thread" in dock, "window polling must not block the GTK loop"


def test_dock_strips_xprop_string_quoting():
    """Taskbar titles came out as literal `"Title"`; xprop quotes string values."""
    dock = (REPO / "ribi" / "components" / "ribi-dock.py").read_text(encoding="utf-8")
    assert 'value[0] == \'"\'' in dock, "xprop string values must be unquoted"


def test_dock_maximise_uses_ewmh_state():
    """A raw resize left the frame off-screen; maximise must go through the WM."""
    dock = (REPO / "ribi" / "components" / "ribi-dock.py").read_text(encoding="utf-8")
    assert "windowstate" in dock and "MAXIMIZED_VERT" in dock, \
        "maximise must request the EWMH maximised state"


def test_live_squashfs_uses_xz():
    source = (REPO / "ribi" / "builder.py").read_text(encoding="utf-8")
    assert '"-comp", "xz"' in source, "live squashfs must use xz compression"


def test_glib_databases_are_compiled():
    source = (REPO / "ribi" / "builder.py").read_text(encoding="utf-8")
    assert "glib-compile-schemas" in source, "builder must compile GSettings schemas"
    assert "update-mime-database" in source, "builder must build the shared-mime database"
    assert "gschemas.compiled" in source, "builder must validate gschemas.compiled"


def test_launcher_enter_activates_from_search_entry():
    source = (REPO / "ribi" / "components" / "ribi-launcher.py").read_text(encoding="utf-8")
    assert 'search.connect("activate"' in source, (
        "Enter is swallowed by the search entry unless 'activate' is connected"
    )
    assert '"launched"' in source, "launcher must guard against double-launching"


def test_builder_loads_components_from_components_dir():
    source = (REPO / "ribi" / "builder.py").read_text(encoding="utf-8")
    assert "_COMPONENTS_DIR" in source
    assert 'Path(__file__).with_name("ribi-dock.py")' not in source, (
        "components must be loaded via _component(), not relative to builder.py"
    )


def test_zen_runs_on_bundled_glibc_runtime():
    """Zen is glibc and the OS is musl; gcompat deadlocks its launcher.

    The builder must ship a self-contained glibc runtime beside Zen and point
    the launcher's interpreter/RPATH at it, so the whole process tree stays
    glibc instead of deadlocking in the gcompat pthread/rtld early init.
    """
    from ribi import config

    assert "patchelf" in config.REQUIRED_HOST_COMMANDS, \
        "patchelf must be a required host tool for the Zen runtime bundle"

    source = (REPO / "ribi" / "builder.py").read_text(encoding="utf-8")
    # The closure is resolved from ELF NEEDED entries, not from ldd.
    assert "patchelf" in source and "--print-needed" in source
    assert "--set-interpreter" in source and "--set-rpath" in source
    assert "/opt/zen/rt/lib" in source, "the bundled glibc runtime path must be stable"
    assert "zen-rt" in source, "the launcher must exec the reinterpreted Zen binary"
    # The launcher must not fall back to the bare gcompat launcher.
    launcher = source.split("#!/bin/sh", 1)[1].split('"""', 1)[0]
    assert "exec /opt/zen/zen-rt" in launcher, "zen-browser must start /opt/zen/zen-rt"
    assert "exec /opt/zen/zen " not in launcher
    # zen-rt must be part of the desktop payload validation.
    assert "opt/zen/zen-rt" in source


def test_installer_decompresses_keymap_before_loadkmap():
    """loadkmap wants a raw table; the live apply must not pipe a gzipped map."""
    installer = (REPO / "ribi" / "sources" / "ribi-installer.py").read_text(encoding="utf-8")
    assert "import gzip" in installer, "installer must import gzip for keymap handling"
    assert "gzip.decompress" in installer, \
        "apply_live_session must decompress .map.gz before calling loadkmap"


def test_dhcpcd_privsep_account_is_provisioned():
    """dhcpcd is built with privsep; without its user it logs 'no such user'."""
    source = (REPO / "ribi" / "builder.py").read_text(encoding="utf-8")
    assert "dhcpcd:x:100:101:" in source, "the dhcpcd privsep user must be added"
    assert "dhcpcd:x:101:" in source, "the dhcpcd privsep group must be added"
    assert 'var/lib/dhcpcd' in source and "os.chown" in source, \
        "the dhcpcd state directory must be owned by its privsep user"


def test_provisioned_group_gids_do_not_collide():
    """The builder used to add sudo with a gid already owned by video/users."""
    source = (REPO / "ribi" / "builder.py").read_text(encoding="utf-8")
    assert "sudo_gid = _next_free_gid(group)" in source, \
        "sudo's gid must be derived from the groups already present"
    assert "sudo_gid = _next_free_gid(group)" in source and '"sudo",27' not in source
    assert '(("audio",29),("video",44),("sudo",sudo_gid))' in source


def test_live_init_pivots_root_for_user_namespaces():
    """chroot leaves / above the mount root and disables user namespaces."""
    live = (REPO / "ribi" / "sources" / "live-init.sh").read_text(encoding="utf-8")
    assert "ribi_pivot_handoff" in live, "the handoff must be centralised"
    assert "pivot_root . .ribi-oldroot" in live, \
        "the initramfs must pivot_root before exec'ing the target init"
    assert "exec chroot /sysroot /sbin/ribi-init" not in live, \
        "a plain chroot handoff disables user namespaces (breaks Zen sandbox)"
    assert "exec chroot /sysroot" in live, "a chroot fallback must remain"


def test_initramfs_provisions_pivot_root_applet():
    source = (REPO / "ribi" / "builder.py").read_text(encoding="utf-8")
    assert '"pivot_root"]' in source, "the initramfs applet list must include pivot_root"
    assert "does not advertise the required pivot_root applet" in source, \
        "the builder must guard the pivot_root applet like switch_root"


def test_zen_launcher_disables_sandbox():
    """Without user namespaces Zen's sandbox EPERMs; the shared launcher must opt out."""
    source = (REPO / "ribi" / "builder.py").read_text(encoding="utf-8")
    launcher = source.split("#!/bin/sh", 1)[1].split('"""', 1)[0]
    assert "--no-sandbox" in launcher, \
        "zen-browser must pass --no-sandbox because the kernel lacks user namespaces"
    assert "exec /opt/zen/zen-rt" in launcher



