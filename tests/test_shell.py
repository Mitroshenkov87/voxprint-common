"""Reference shell: headless import, offscreen screenshots, and chords."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from voxprint_common.ui.shell import ADOPTION, reference_layout
from voxprint_common.ui.theme import (
    build_shell_style,
    contrast_ratio,
    light_contrast_pairs,
    palette_colors,
)
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
    assert tokens.light_name == "voxprint-light"
    assert tokens.density.body_px == 13
    assert tokens.density.icon_px == 22
    assert tokens.density.panel_header_px == 28
    assert tokens.density.control_radius_px == 4
    assert tokens.density.button_radius_px == 4
    from voxprint_common.ui.theme import build_style

    for style in (build_style(False), build_shell_style(False)):
        assert "border-radius: 14px" not in style and "border-radius: 15px" not in style
    assert tokens.density.border_px == 1
    assert tokens.density.focus_ring_px == 2
    assert tokens.density.chip_pad_px == 16
    style = build_shell_style(False)
    assert f"padding: 2px {tokens.density.chip_pad_px}px" in style
    assert "QScrollArea > QWidget > QWidget" in style
    assert "QTextEdit > QWidget" in style
    assert "QPlainTextEdit > QWidget" in style
    assert "QAbstractScrollArea > QWidget" in style
    assert "QAbstractScrollArea QScrollBar:vertical" in style
    assert "QScrollArea#expertScroll QScrollBar:horizontal" in style
    handle = f"QScrollBar::handle:vertical {{\n    background: {tokens.colors['CONTROL_BORDER']}"
    groove = f"QScrollBar::groove:vertical {{\n    background: {tokens.colors['CARD_PLAIN']}"
    assert handle in style
    assert groove in style
    dark = palette_colors()
    light = palette_colors(light=True)
    assert dark["Base"] == tokens.colors["CARD_PLAIN"]
    assert dark["Window"] == tokens.colors["ROOT_PLAIN"]
    assert dark["Text"] == tokens.colors["TEXT"]
    assert dark["AlternateBase"] == tokens.colors["CONTROL_BG"]
    assert light["Base"] == tokens.light["Base"] == tokens.light_colors["CARD_PLAIN"]
    assert light["Window"] == tokens.light_colors["ROOT_PLAIN"]
    assert light["Text"] == tokens.light_colors["TEXT"]
    for foreground, background in light_contrast_pairs():
        assert contrast_ratio(foreground, background) >= 4.5, (foreground, background)
    light_style = build_shell_style(False, light=True)
    assert f"background: {tokens.light_colors['ROOT_PLAIN']}" in light_style
    assert f"color: {light['Text']}" in light_style
    assert tokens.colors["TEXT"] not in light_style.split("QWidget#shell", 1)[0]
    assert dark["Base"] != "#efefef"
    assert light["Base"] != "#efefef"
    assert f"QPushButton:focus {{\n    border: {tokens.density.focus_ring_px}px solid" in style
    assert tokens.identity["audiobook-builder"] == "#f5d76e"
    assert tokens.identity["movie-dubber"] == "#b98cff"
    assert tokens.identity["enlarge"] == "#ff6b8a"
    assert tokens.identity["clarity"] == "#2dd4bf"
    assert tokens.identity["lucent"] == "#5ce1ff"
    assert "PySide6" not in sys.modules


def test_compact_button_style_and_mark() -> None:
    from voxprint_common.ui.theme import build_style, mark_compact

    for style in (build_style(False), build_shell_style(False)):
        assert 'QPushButton[compact="true"]' in style
        block = style.split('QPushButton[compact="true"]', 1)[1].split("}", 1)[0]
        assert "border-radius: 4px" in block
        assert "padding: 2px 8px" in block
        assert "min-height: 24px" in block
    assert 'QPushButton[chip="true"]' in build_shell_style(False)

    class _Dummy:
        def __init__(self) -> None:
            self.properties: dict[str, object] = {}

        def setProperty(self, name: str, value: object) -> None:
            self.properties[name] = value

        def style(self) -> None:
            return None

    dummy = _Dummy()
    assert mark_compact(dummy) is dummy
    assert dummy.properties.get("compact") is True
    assert "PySide6" not in sys.modules


def test_chip_text_fits(tmp_path: Path) -> None:
    if importlib.util.find_spec("PySide6") is None:
        pytest.skip("ui extra is not installed")
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    script = Path(__file__).with_name("shell_offscreen.py")
    completed = subprocess.run(
        [sys.executable, str(script), str(tmp_path), "chips"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
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
    ukrainian = (tmp_path / "uk-shell.png").read_bytes()
    german = (tmp_path / "de-shell.png").read_bytes()
    palette = (tmp_path / "en-palette.png").read_bytes()
    ukrainian_palette = (tmp_path / "uk-palette.png").read_bytes()
    split = (tmp_path / "en-split.png").read_bytes()
    empty = (tmp_path / "en-simple-empty.png").read_bytes()
    result = (tmp_path / "en-simple-result.png").read_bytes()
    ukrainian_result = (tmp_path / "uk-simple-result.png").read_bytes()
    expert = (tmp_path / "en-expert.png").read_bytes()
    modified = (tmp_path / "en-expert-modified.png").read_bytes()
    variants = {
        name: (tmp_path / name).read_bytes()
        for name in (
            "image-simple.png",
            "en-image-light.png",
            "image-expert.png",
            "audio-simple.png",
            "audio-expert.png",
            "video-simple.png",
            "video-expert.png",
            "text-simple.png",
            "text-expert.png",
            "tool-simple.png",
            "tool-expert.png",
            "uk-image-simple.png",
            "uk-image-expert.png",
            "uk-audio-simple.png",
            "uk-audio-expert.png",
            "uk-dub-simple.png",
            "uk-dub-expert.png",
            "viewer-simple.png",
            "viewer-expert.png",
            "image-modified.png",
            "audio-modified.png",
            "video-modified.png",
            "text-modified.png",
            "tool-modified.png",
            "viewer-modified.png",
            "uk-video-simple.png",
            "uk-video-expert.png",
            "uk-text-simple.png",
            "uk-text-expert.png",
            "uk-tool-simple.png",
            "uk-tool-expert.png",
            "uk-viewer-simple.png",
            "uk-viewer-expert.png",
            "de-image-simple.png",
            "de-image-expert.png",
            "uk-sections.png",
            "uk-casting.png",
            "uk-steps.png",
            "uk-steps-running.png",
        )
    }
    pictures = (
        english,
        ukrainian,
        german,
        palette,
        ukrainian_palette,
        split,
        empty,
        result,
        ukrainian_result,
        expert,
        modified,
    )
    for blob in pictures:
        assert blob.startswith(b"\x89PNG")
        assert len(blob) > 1000
    assert english != ukrainian
    assert english != german
    assert palette != ukrainian_palette
    assert english != palette
    assert english != split
    assert empty != result
    assert result != ukrainian_result
    assert result != expert
    assert expert != modified
    assert empty != english
    for blob in variants.values():
        assert blob.startswith(b"\x89PNG")
        assert len(blob) > 1000
    assert len(set(variants.values())) == len(variants)
    assert "PySide6" not in sys.modules
