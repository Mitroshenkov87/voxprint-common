"""The pin checker fails when a checkout disagrees with a wheel sha256."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_SHA = "a" * 64
_OTHER = "b" * 64


def test_check_accepts_a_matching_pin_and_rejects_a_mismatch(tmp_path: Path) -> None:
    checker = _load()
    good = tmp_path / "pyproject.toml"
    good.write_text(
        f'dependencies = ["voxprint-common==0.5.2"]\n# wheel {_SHA}\n',
        encoding="utf-8",
    )
    assert checker.check(good, "0.5.2", _SHA, []) == []
    missing_sha = tmp_path / "version-only.toml"
    missing_sha.write_text('dependencies = ["voxprint-common==0.5.2"]\n', encoding="utf-8")
    assert any("sha256" in line for line in checker.check(missing_sha, "0.5.2", _SHA, []))
    wrong_version = tmp_path / "old.toml"
    wrong_version.write_text(
        f'dependencies = ["voxprint-common==0.5.1"]\n# wheel {_SHA}\n',
        encoding="utf-8",
    )
    version_errors = checker.check(wrong_version, "0.5.2", _SHA, [])
    assert any("0.5.1" in line for line in version_errors)
    wrong_sha = tmp_path / "sha.toml"
    wrong_sha.write_text(
        f'dependencies = ["voxprint-common==0.5.2"]\n# wheel {_OTHER}\n',
        encoding="utf-8",
    )
    sha_errors = checker.check(wrong_sha, "0.5.2", _SHA, [])
    assert any(_OTHER in line for line in sha_errors)
    assert any(_SHA in line for line in sha_errors)
    empty = tmp_path / "empty.toml"
    empty.write_text("[project]\nname = 'app'\n", encoding="utf-8")
    assert checker.check(empty, "0.5.2", _SHA, []) == ["voxprint-common is not pinned"]
    lock = tmp_path / "uv.lock"
    lock.write_text(
        f'name = "voxprint-common"\nversion = "0.5.1"\n# {_SHA}\n',
        encoding="utf-8",
    )
    locked = checker.check(good, "0.5.2", _SHA, [lock])
    assert any("0.5.1" in line for line in locked)


def test_main_uses_the_wheel_env_and_does_not_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = _load()
    project = tmp_path / "pyproject.toml"
    project.write_text(
        f'dependencies = ["voxprint-common==0.5.2"]\n# wheel {_SHA}\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WANTED_TAG", "v0.5.2")
    monkeypatch.setenv("VOXPRINT_WHEEL_SHA256", _SHA)
    monkeypatch.delenv("LOCK", raising=False)

    def refuse(version: str) -> str:
        raise AssertionError(version)

    monkeypatch.setattr(checker, "_download", refuse)
    assert checker.main() == 0
    monkeypatch.setenv("VOXPRINT_WHEEL_SHA256", _OTHER)
    assert checker.main() == 1


def _load() -> Any:
    path = Path(__file__).parents[1] / "docs" / "ci" / "check_pin.py"
    spec = importlib.util.spec_from_file_location("check_pin", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
