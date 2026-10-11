"""Schema 1 manifest migration, refs, and the lock."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from voxprint_common.atomic import LockTimeout
from voxprint_common.runtime_manifest import (
    ManifestConflict,
    ManifestError,
    add_ref,
    cpu_onnxruntime_conflicts,
    load,
    remove_ref,
)

HOLD_LOCK = Path(__file__).with_name("hold_lock.py")
SHA_A = "a" * 64
SHA_B = "b" * 64


def test_migrates_resources_and_holders(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "apps": ["plotweaver"],
                "resources": {
                    "wheels": {
                        "kind": "wheels",
                        "path": "wheels",
                        "bytes": 10,
                        "files": {"torch.whl": SHA_A},
                        "apps": ["canvas", "plotweaver"],
                        "refcount": 2,
                        "source": "https://example.invalid/torch",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    document = load(path)
    component = document.components["wheels"]
    assert component.kind == "wheels"
    assert component.refs == ["canvas", "plotweaver"]
    assert component.sha256 == SHA_A
    assert component.extra["bytes"] == 10
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema"] == 1
    assert saved["version"] == 1
    assert saved["apps"] == ["plotweaver"]
    assert "resources" in saved
    assert saved["components"]["wheels"]["refs"] == ["canvas", "plotweaver"]
    assert "holders" not in saved["components"]["wheels"]

    holders = tmp_path / "holders.json"
    holders.write_text(
        json.dumps(
            {
                "schema": 1,
                "components": {
                    "python": {"holders": ["enlarge", "clarity"], "version": "3.14"},
                    "model": {"holders": ["enlarge"], "version": "4x-nomos"},
                },
            }
        ),
        encoding="utf-8",
    )
    migrated = load(holders)
    assert migrated.components["python"].refs == ["enlarge", "clarity"]
    assert migrated.components["python"].kind == "runtime"
    assert migrated.components["model"].kind == "model"
    rewritten = json.loads(holders.read_text(encoding="utf-8"))
    assert "holders" not in rewritten["components"]["python"]
    assert rewritten["components"]["model"]["refs"] == ["enlarge"]


def test_add_and_remove_refs(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    add_ref(
        "python-3.14",
        "enlarge",
        kind="runtime",
        version="3.14",
        path="runtime/py314",
        sha256=SHA_A,
        manifest=path,
        installed_at="2026-10-11T08:40:00+00:00",
    )
    add_ref(
        "python-3.14",
        "canvas",
        kind="runtime",
        version="3.14",
        path="runtime/py314",
        sha256=SHA_A,
        manifest=path,
    )
    document = load(path)
    assert document.components["python-3.14"].refs == ["enlarge", "canvas"]
    assert remove_ref("python-3.14", "enlarge", manifest=path) is False
    assert load(path).components["python-3.14"].refs == ["canvas"]
    assert remove_ref("python-3.14", "canvas", manifest=path) is True
    assert "python-3.14" not in load(path).components


def test_refuses_to_overwrite_another_apps_component(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    add_ref(
        "torch-2.11.0-cu130",
        "enlarge",
        kind="wheels",
        version="2.11.0+cu130",
        path="wheels/torch",
        sha256=SHA_A,
        manifest=path,
    )
    with pytest.raises(ManifestConflict, match="held by enlarge"):
        add_ref(
            "torch-2.11.0-cu130",
            "canvas",
            kind="wheels",
            version="2.12.0+cu130",
            path="wheels/torch-new",
            sha256=SHA_B,
            manifest=path,
        )
    assert load(path).components["torch-2.11.0-cu130"].sha256 == SHA_A
    add_ref(
        "torch-2.11.0-cu130",
        "enlarge",
        kind="wheels",
        version="2.12.0+cu130",
        path="wheels/torch-new",
        sha256=SHA_B,
        manifest=path,
    )
    updated = load(path).components["torch-2.11.0-cu130"]
    assert updated.version == "2.12.0+cu130"
    assert updated.refs == ["enlarge"]


def test_unsupported_schema_is_not_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"schema": 2, "components": {}}\n', encoding="utf-8")
    with pytest.raises(ManifestError, match="schema 2"):
        load(path)
    assert '"schema": 2' in path.read_text(encoding="utf-8")


def test_missing_manifest_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    assert load(path).components == {}
    assert not path.exists()


def test_lock_timeout(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"schema": 1, "components": {}}\n', encoding="utf-8")
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    process = subprocess.Popen(
        [
            sys.executable,
            str(HOLD_LOCK),
            str(path.with_name("manifest.lock")),
            str(ready),
            str(release),
        ]
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise AssertionError("lock holder exited early")
            time.sleep(0.02)
        with pytest.raises(LockTimeout):
            add_ref(
                "ffmpeg-8",
                "clarity",
                kind="tool",
                version="8",
                path="ffmpeg",
                sha256=SHA_A,
                manifest=path,
                timeout=0.2,
            )
    finally:
        release.write_text("go", encoding="utf-8")
        process.wait(10)


def test_cpu_onnxruntime_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    class Dist:
        def __init__(self, name: str) -> None:
            self.metadata = {"Name": name}

    def install(names: list[str]) -> None:
        monkeypatch.setattr(
            "voxprint_common.runtime_manifest.importlib_metadata.distributions",
            lambda: [Dist(name) for name in names],
        )

    install(["onnxruntime", "onnxruntime-gpu"])
    assert cpu_onnxruntime_conflicts()
    install(["onnxruntime-gpu"])
    assert not cpu_onnxruntime_conflicts()
    install([])
    assert not cpu_onnxruntime_conflicts()
    install(["onnxruntime"])
    assert cpu_onnxruntime_conflicts()
