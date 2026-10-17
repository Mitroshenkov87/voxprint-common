"""Reference shell: headless import, offscreen screenshots, and chords."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from voxprint_common.ui.shell import ADOPTION, reference_layout
from voxprint_common.ui.theme import build_shell_style
from voxprint_common.ui.tokens import SCHEMA, load


def test_headless_layout_and_adoption() -> None:
    layout = reference_layout()
    assert layout.contains("prompt")
    assert layout.contains("layers")
    assert layout.contains("canvas")
    assert layout.contains("history")
    assert layout.contains("settings")
    assert layout.contains("advanced")
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
    assert tokens.density.border_px == 1
    assert tokens.density.focus_ring_px == 2
    style = build_shell_style(False)
    assert f"QPushButton:focus {{\n    border: {tokens.density.focus_ring_px}px solid" in style
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
    german = (tmp_path / "de-shell.png").read_bytes()
    palette = (tmp_path / "en-palette.png").read_bytes()
    russian_palette = (tmp_path / "ru-palette.png").read_bytes()
    split = (tmp_path / "en-split.png").read_bytes()
    empty = (tmp_path / "en-simple-empty.png").read_bytes()
    result = (tmp_path / "en-simple-result.png").read_bytes()
    russian_result = (tmp_path / "ru-simple-result.png").read_bytes()
    expert = (tmp_path / "en-expert.png").read_bytes()
    modified = (tmp_path / "en-expert-modified.png").read_bytes()
    variants = {
        name: (tmp_path / name).read_bytes()
        for name in (
            "image-simple.png",
            "image-expert.png",
            "audio-simple.png",
            "audio-expert.png",
            "video-simple.png",
            "video-expert.png",
            "text-simple.png",
            "text-expert.png",
            "tool-simple.png",
            "tool-expert.png",
            "ru-image-simple.png",
            "ru-image-expert.png",
            "ru-audio-simple.png",
            "ru-audio-expert.png",
            "viewer-simple.png",
            "viewer-expert.png",
            "image-modified.png",
            "audio-modified.png",
            "video-modified.png",
            "text-modified.png",
            "tool-modified.png",
            "viewer-modified.png",
            "ru-video-simple.png",
            "ru-video-expert.png",
            "ru-text-simple.png",
            "ru-text-expert.png",
            "ru-tool-simple.png",
            "ru-tool-expert.png",
            "ru-viewer-simple.png",
            "ru-viewer-expert.png",
            "de-image-simple.png",
            "de-image-expert.png",
        )
    }
    pictures = (
        english,
        russian,
        german,
        palette,
        russian_palette,
        split,
        empty,
        result,
        russian_result,
        expert,
        modified,
    )
    for blob in pictures:
        assert blob.startswith(b"\x89PNG")
        assert len(blob) > 1000
    assert english != russian
    assert english != german
    assert palette != russian_palette
    assert english != palette
    assert english != split
    assert empty != result
    assert result != russian_result
    assert result != expert
    assert expert != modified
    assert empty != english
    for blob in variants.values():
        assert blob.startswith(b"\x89PNG")
        assert len(blob) > 1000
    assert len(set(variants.values())) == len(variants)
    assert "PySide6" not in sys.modules
