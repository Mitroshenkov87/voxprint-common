"""Headless platform setup, and the reference-shell smoke run.

The shell cases live in ``gui_smoke_cases.py`` and use pytest-qt. This module
starts them in a child pytest so the plugin is not loaded for the rest of
the suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from voxprint_common.testing.gui_smoke import configure_headless


def test_configure_selects_offscreen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    configure_headless()
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"


def test_configure_sets_windows_font_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fonts = tmp_path / "Fonts"
    fonts.mkdir()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("WINDIR", str(tmp_path))
    monkeypatch.delenv("QT_QPA_FONTDIR", raising=False)
    configure_headless()
    assert os.environ["QT_QPA_FONTDIR"] == str(fonts)


def test_reference_shell_smoke() -> None:
    """Walk the reference shell and survive a faked worker crash."""
    configure_headless()
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTEST_QT_API"] = "pyside6"
    cases = Path(__file__).with_name("gui_smoke_cases.py")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-p",
            "pytest-qt",
            str(cases),
            "-q",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
