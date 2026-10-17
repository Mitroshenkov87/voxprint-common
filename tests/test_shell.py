"""Reference shell: headless import, offscreen screenshots, and chords."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from voxprint_common.ui.shell import ADOPTION, reference_layout
from voxprint_common.ui.tokens import SCHEMA, load


def test_headless_layout_and_adoption() -> None:
    layout = reference_layout()
    assert layout.contains("prompt")
    assert layout.contains("layers")
    assert layout.contains("canvas")
    assert layout.contains("history")
    assert ADOPTION == (
        "canvas",
        "enlarge",
        "clarity",
        "audiobook-builder",
        "movie-dubber",
        "plotweaver",
        "lucent",
    )
    tokens = load()
    assert tokens.schema == SCHEMA == 1
    assert tokens.name == "voxprint-dark"
    assert tokens.density.body_px == 13
    assert tokens.density.icon_px == 22
    assert tokens.density.panel_header_px == 28
    assert tokens.density.control_radius_px == 4
    assert tokens.identity["audiobook-builder"] == "#f5d76e"
    assert tokens.identity["movie-dubber"] == "#b98cff"
    assert tokens.identity["enlarge"] == "#ff6b8a"
    assert tokens.identity["clarity"] == "#2dd4bf"
    assert "PySide6" not in sys.modules


def test_offscreen_shell_screenshots(tmp_path: Path) -> None:
    if importlib.util.find_spec("PySide6") is None:
        pytest.skip("ui extra is not installed")
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    script = Path(__file__).with_name("shell_offscreen.py")
    completed = subprocess.run(
        [sys.executable, str(script), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    english = (tmp_path / "en-shell.png").read_bytes()
    russian = (tmp_path / "ru-shell.png").read_bytes()
    palette = (tmp_path / "en-palette.png").read_bytes()
    russian_palette = (tmp_path / "ru-palette.png").read_bytes()
    for blob in (english, russian, palette, russian_palette):
        assert blob.startswith(b"\x89PNG")
        assert len(blob) > 1000
    assert english != russian
    assert palette != russian_palette
    assert english != palette
    assert "PySide6" not in sys.modules
