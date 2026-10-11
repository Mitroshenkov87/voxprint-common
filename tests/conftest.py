"""Keep suite tests off the real Voxprint home and the real GPU lock."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_suite(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    root = tmp_path_factory.mktemp("suite")
    monkeypatch.setenv("VOXPRINT_HOME", str(root / "home"))
    monkeypatch.setenv("VOXPRINT_GPU_LOCK", str(root / "gpu.lock"))
    monkeypatch.delenv("VOXPRINT_MODELS_DIR", raising=False)
    monkeypatch.delenv("VOXPRINT_BUILD", raising=False)
    monkeypatch.delenv("VOXPRINT_GPU_PARENT_PID", raising=False)
    monkeypatch.delenv("GITHUB_RUN_NUMBER", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
