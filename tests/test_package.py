"""Regression tests for the modularized builder.

These pin the behaviour of the split: the package must import, expose every
top-level definition that the original monolith did, and the externalized
target sources must match the original embedded strings byte-for-byte.
"""

import ast
import importlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEGACY = REPO / "legacy" / "ribi-iso-builder.monolith.py"

sys.path.insert(0, str(REPO))

MODULES = [
    "config", "logging_utils", "download", "apk", "audit",
    "deps", "kernel_config", "builder", "cli", "sources",
]


def _legacy_tree():
    return ast.parse(LEGACY.read_text(encoding="utf-8"))


def test_legacy_reference_present():
    assert LEGACY.is_file(), "the original monolith must be kept for reference"


def test_package_imports():
    import ribi
    assert ribi.OS_NAME == "Ribi OS"
    assert ribi.RibiMasterBuilder is not None


def test_every_top_level_definition_survived():
    names = [
        n.name for n in _legacy_tree().body
        if isinstance(n, (ast.FunctionDef, ast.ClassDef))
    ]
    present = set()
    for mod in MODULES:
        present |= set(dir(importlib.import_module(f"ribi.{mod}")))
    missing = [n for n in names if n not in present]
    assert missing == [], f"definitions lost during split: {missing}"


def test_externalized_sources_match_original():
    want = {}
    for node in _legacy_tree().body:
        if (isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.startswith("SRC_")):
            want[node.targets[0].id] = ast.literal_eval(node.value)

    from ribi import sources
    assert len(want) == 10, "expected 10 embedded target sources"
    for name, value in want.items():
        normalized = value if value.endswith("\n") else value + "\n"
        assert getattr(sources, name) == normalized, f"{name} changed during split"


def test_bundled_wallpaper_is_a_real_png():
    from ribi import config
    assert config.WALLPAPER_SOURCE.is_file()
    header = config.WALLPAPER_SOURCE.read_bytes()[:8]
    assert header == b"\x89PNG\r\n\x1a\n", "wallpaper must be a real PNG"


def test_cli_exposes_main():
    from ribi import cli
    assert callable(cli.main)


REQUIRED_COMPONENTS = [
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


def test_native_components_present_and_compile():
    """Every desktop component the builder writes into the OS must exist."""
    import py_compile

    components = REPO / "ribi" / "components"
    for name in REQUIRED_COMPONENTS:
        path = components / name
        assert path.is_file(), f"missing native component: {name}"
        py_compile.compile(str(path), doraise=True)


def test_builder_loads_components_from_components_dir():
    source = (REPO / "ribi" / "builder.py").read_text(encoding="utf-8")
    assert "_COMPONENTS_DIR" in source
    assert 'Path(__file__).with_name("ribi-dock.py")' not in source, (
        "components must be loaded via _component(), not relative to builder.py"
    )


def test_externalized_payloads_match_original():
    """Desktop/session config payloads must survive the move to ribi/payloads/."""
    names = {
        "lightdm_conf": "lightdm.conf",
        "desktop_session": "ribi-desktop-session.sh",
        "dump_session": "ribi-dump-session.sh",
        "xfce_session": "ribi-xfce-session.sh",
        "session_wrapper": "ribi-session-wrapper.sh",
        "direct_xsession": "ribi-direct-xsession.sh",
        "wallpaper_xml": "xfce4-desktop.xml",
        "wallpaper_viewer": "ribi-wallpaper-viewer.sh",
        "gtk_probe_py": "ribi-gtk-probe.py",
        "handoff_py": "ribi-user-handoff.py",
        "panel_xml": "xfce4-panel.xml",
        "shortcuts_xml": "xfce4-shortcuts.xml",
        "direct": "ribi-direct-desktop.sh",
        "user_desktop": "ribi-user-desktop.sh",
        "openbox_autostart": "openbox-autostart.sh",
        "visible": "ribi-visible-session.sh",
        "xorg_conf": "xorg.conf",
        "xfce_clients": "ribi-xfce-clients.sh",
        "net_up_script": "ribi-netup.sh",
    }
    want = {}
    for node in ast.walk(_legacy_tree()):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in names
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and len(node.value.value) >= 300):
            want[node.targets[0].id] = node.value.value

    assert len(want) == len(names), f"expected {len(names)} payloads, found {len(want)}"
    payloads = REPO / "ribi" / "payloads"
    for var, filename in names.items():
        got = (payloads / filename).read_text(encoding="utf-8")
        assert got == want[var], f"payload {var} ({filename}) changed during externalization"


def test_polish_payloads_present_and_installed():
    """Desktop polish added on top of the split must ship and be wired in."""
    from ribi import config

    payloads = REPO / "ribi" / "payloads"
    for filename in (
        "openbox-ribi-themerc",
        "ribi-Xresources",
        "ribi-picom.conf",
        "ribi-terminal.sh",
    ):
        assert (payloads / filename).is_file(), f"missing polish payload: {filename}"

    source = (REPO / "ribi" / "builder.py").read_text(encoding="utf-8")
    for marker in (
        'openbox-ribi-themerc',
        'ribi-Xresources',
        'ribi-picom.conf',
        'ribi-terminal.sh',
    ):
        assert marker in source, f"builder does not install {marker}"

    assert "picom" in config.TARGET_APK_PACKAGES_DESKTOP
    assert "lxterminal" in config.TARGET_APK_PACKAGES_DESKTOP

