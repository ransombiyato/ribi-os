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
