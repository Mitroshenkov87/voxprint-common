"""Suite directory layout."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from voxprint_common import paths


@pytest.mark.posix
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory layout")
def test_xdg_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("VOXPRINT_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert paths.home() == tmp_path / "voxprint-ai-suite"
    assert paths.shared_dir() == tmp_path / "voxprint-ai-suite" / "shared"
    assert paths.state_dir() == tmp_path / "voxprint-ai-suite" / "state"
    assert not paths.home().exists()


@pytest.mark.posix
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory layout")
def test_default_linux_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOXPRINT_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert paths.final_home() == Path.home() / ".local" / "share" / "voxprint-ai-suite"
    assert paths.legacy_home() == Path.home() / ".local" / "share" / "voxprint"
    monkeypatch.setenv("XDG_DATA_HOME", "relative")
    assert paths.final_home() == Path.home() / ".local" / "share" / "voxprint-ai-suite"


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory layout")
def test_localappdata_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("VOXPRINT_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.home() == tmp_path / "VOXPrint AI Suite"
    assert paths.trt_cache_dir() == tmp_path / "Voxprint" / "trt-cache"


def test_home_override_and_children(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOXPRINT_HOME", str(tmp_path / "suite"))
    assert paths.home() == tmp_path / "suite"
    assert paths.manifest_file() == tmp_path / "suite" / "shared" / "manifest.json"
    assert paths.suite_settings_file() == tmp_path / "suite" / "state" / "suite.json"
    assert paths.logs_dir() == tmp_path / "suite" / "logs"
    assert not paths.home().exists()


def test_models_dir_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "suite"
    monkeypatch.setenv("VOXPRINT_HOME", str(home))
    assert paths.models_dir() == home / "models"

    legacy = home / "state"
    legacy.mkdir(parents=True)
    (legacy / "models_dir.txt").write_text(f"{tmp_path / 'legacy'}\n", encoding="utf-8")
    assert paths.models_dir() == tmp_path / "legacy"

    suite = {"models_dir": str(tmp_path / "shared-models"), "other": 1}
    (legacy / "suite.json").write_text(json.dumps(suite), encoding="utf-8")
    assert paths.models_dir() == tmp_path / "shared-models"

    monkeypatch.setenv("VOXPRINT_MODELS_DIR", str(tmp_path / "env-models"))
    assert paths.models_dir() == tmp_path / "env-models"

    monkeypatch.setenv("VOXPRINT_MODELS_DIR", "relative/models")
    assert paths.models_dir() == tmp_path / "shared-models"


def test_models_dir_ignores_relative_suite_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "suite"
    monkeypatch.setenv("VOXPRINT_HOME", str(home))
    state = home / "state"
    state.mkdir(parents=True)
    (state / "suite.json").write_text('{"models_dir": "relative"}', encoding="utf-8")
    assert paths.models_dir() == home / "models"


def test_trt_cache_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOXPRINT_TRT_CACHE", str(tmp_path / "cache"))
    assert paths.trt_cache_dir() == tmp_path / "cache"
