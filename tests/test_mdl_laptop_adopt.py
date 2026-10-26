"""Adopt against the 2026-10-26 laptop model layout.

``tests/fixtures/laptop-2026-10-26/pins.json`` is the Z-Image-Turbo and
huihui-Qwen3-4B subset of that snapshot: real relative paths, sizes, and
sha256 values. The weight bytes are not in the repo. The integration run
gives each file its recorded size and stubs the hash to the recorded sha256.
A second run, with the real hasher and zero-byte weights, skips them.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from voxprint_common.gpu_probe import GpuDevice
from voxprint_common.hashing import HashMismatch, sha256_file
from voxprint_common.mdl.adopt import adopt
from voxprint_common.mdl.views import view_directory
from voxprint_common.paths import models_dir

_FIXTURE = Path(__file__).parent / "fixtures" / "laptop-2026-10-26" / "pins.json"
_Z_IMAGE = "z-image-turbo-bf16-16gb"
_WRITER = "qwen3-4b-writer"
_Z_SUFFIX = "84e2483222ba"
_WRITER_SUFFIX = "95e3d033af57"
_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _gpu() -> GpuDevice:
    return GpuDevice(0, "Test GPU", "610.00", (8, 9), 24576)


def _pins() -> list[dict[str, object]]:
    document = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    models = document["models"]
    assert isinstance(models, list)
    return models


def _manifest(folder: Path, models: list[dict[str, object]]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "mdl.json"
    rows = []
    for model in models:
        files = model["files"]
        assert isinstance(files, list)
        rows.append(
            {
                "id": model["id"],
                "version": model["version"],
                "vram_tier": model["vram_tier"],
                "optional": model["optional"],
                "files": [
                    {
                        "path": item["path"],
                        "size": item["size"],
                        "sha256": item["sha256"],
                        "mirrors": [f"https://example.invalid/canvas/{item['path']}"],
                    }
                    for item in files
                ],
            }
        )
    path.write_text(
        json.dumps({"schema": 1, "app": "canvas", "models": rows}),
        encoding="utf-8",
    )
    return path


def _truncate(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        os.ftruncate(fd, size)
    finally:
        os.close(fd)


def _layout(root: Path, models: list[dict[str, object]], *, full_size: bool) -> Path:
    """Rebuild the legacy folders. ``full_size`` is a sparse file of the recorded size."""
    for model in models:
        legacy = root / str(model["legacy"])
        files = model["files"]
        assert isinstance(files, list)
        for item in files:
            if not item["in_legacy"]:
                continue
            _truncate(legacy / str(item["path"]), int(item["size"]) if full_size else 0)
    sentinel = root / "model-forge" / "keep.txt"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_bytes(b"stay")
    return sentinel


def _stamp(root: Path) -> dict[str, tuple[int, int, int]]:
    found: dict[str, tuple[int, int, int]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        info = path.stat()
        found[path.as_posix()] = (info.st_ino, info.st_mtime_ns, info.st_size)
    return found


def _seed_blobs(models: list[dict[str, object]]) -> dict[str, int]:
    folder = models_dir() / "blobs"
    folder.mkdir(parents=True, exist_ok=True)
    inodes: dict[str, int] = {}
    for model in models:
        files = model["files"]
        assert isinstance(files, list)
        for item in files:
            if not item["blob_in_snapshot"]:
                continue
            digest = str(item["sha256"])
            path = folder / digest
            if not path.exists():
                _truncate(path, int(item["size"]))
            inodes[digest] = path.stat().st_ino
    return inodes


def _digests(root: Path, models: list[dict[str, object]]) -> dict[Path, str]:
    found: dict[Path, str] = {}
    for model in models:
        legacy = root / str(model["legacy"])
        files = model["files"]
        assert isinstance(files, list)
        for item in files:
            digest = str(item["sha256"])
            found[(models_dir() / "blobs" / digest).resolve()] = digest
            if item["in_legacy"]:
                found[(legacy / str(item["path"])).resolve()] = digest
    return found


def test_laptop_layout_reuses_z_image_adopts_writer_and_second_run_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models = _pins()
    root = tmp_path / "models"
    sentinel = _layout(root, models, full_size=True)
    manifest = _manifest(tmp_path / "canvas", models)
    seeded = _seed_blobs(models)
    recorded = _digests(root, models)
    before = _stamp(root)

    def hash_file(path: Path, on_chunk: object) -> tuple[str, int]:
        digest = recorded[path.resolve()]
        size = path.stat().st_size
        on_chunk(size)  # type: ignore[operator]
        return digest, size

    def verify(path: Path, expected: str) -> str:
        found = recorded[path.resolve()]
        wanted = expected.strip().lower()
        if found != wanted:
            raise HashMismatch(path, wanted, found)
        return found

    module = sys.modules["voxprint_common.mdl.adopt"]
    monkeypatch.setattr(module, "_hash_file", hash_file)
    monkeypatch.setattr("voxprint_common.mdl.job.verify_hash", verify)
    sources = [root / str(model["legacy"]) for model in models]
    only = [str(model["id"]) for model in models]
    outcome = adopt([manifest], sources, devices=[_gpu()], only=only)
    assert outcome.code == 0
    assert outcome.status.ready
    assert outcome.status.extra["skipped"] == []
    reused = {(row["model"], row["file"]) for row in outcome.status.extra["reused"]}
    adopted = {(row["model"], row["file"]) for row in outcome.status.extra["adopted"]}
    blobs = models_dir() / "blobs"
    for model in models:
        files = model["files"]
        assert isinstance(files, list)
        mapping = {str(item["path"]): str(item["sha256"]) for item in files}
        folder = view_directory(models_dir(), str(model["id"]), mapping)
        suffix = _Z_SUFFIX if model["id"] == _Z_IMAGE else _WRITER_SUFFIX
        assert folder.name == f"{model['id']}-{suffix}"
        marker = json.loads((folder / ".voxprint-view.json").read_text(encoding="utf-8"))
        assert marker == {"files": mapping, "key": model["id"], "schema": 1}
        for item in files:
            digest = str(item["sha256"])
            blob = blobs / digest
            assert os.path.samefile(folder / str(item["path"]), blob)
            key = (str(model["id"]), str(item["path"]))
            source = root / str(model["legacy"]) / str(item["path"])
            if item["blob_in_snapshot"]:
                assert blob.stat().st_ino == seeded[digest]
                if item["in_legacy"]:
                    assert source.stat().st_ino != blob.stat().st_ino
                if model["id"] == _Z_IMAGE:
                    assert key in reused
            else:
                assert key in adopted
                assert os.path.samefile(source, blob)
    assert _stamp(root) == before
    assert sentinel.read_bytes() == b"stay"
    marker_paths = list((models_dir() / "views" / "canvas").glob("*/.voxprint-view.json"))
    marks = {path: path.stat().st_mtime_ns for path in marker_paths}
    blob_inodes = {path.name: path.stat().st_ino for path in blobs.iterdir() if path.is_file()}
    again = adopt([manifest], sources, devices=[_gpu()], only=only)
    assert again.code == 0
    assert again.status.ready
    assert again.status.extra["adopted"] == []
    assert again.status.extra["skipped"] == []
    assert {(row["model"], row["file"]) for row in again.status.extra["reused"]} == reused | adopted
    assert _stamp(root) == before
    assert {path: path.stat().st_mtime_ns for path in marker_paths} == marks
    afterwards = {path.name: path.stat().st_ino for path in blobs.iterdir() if path.is_file()}
    assert afterwards == blob_inodes


def test_laptop_layout_without_hash_stub_skips_mismatches(tmp_path: Path) -> None:
    models = _pins()
    root = tmp_path / "models"
    sentinel = _layout(root, models, full_size=False)
    writer = next(model for model in models if model["id"] == _WRITER)
    files = writer["files"]
    assert isinstance(files, list)
    sample = next(item for item in files if item["path"] == "config.json")
    sample_path = root / str(writer["legacy"]) / "config.json"
    _truncate(sample_path, int(sample["size"]))
    sample_path.write_bytes(b"\x00" * int(sample["size"]))
    sample_stat = (sample_path.stat().st_ino, sample_path.stat().st_mtime_ns)
    weight = next(item for item in files if str(item["path"]).endswith(".safetensors"))
    weight_path = root / str(writer["legacy"]) / str(weight["path"])
    assert weight_path.stat().st_size == 0
    assert sha256_file(weight_path) == _EMPTY
    assert sha256_file(weight_path) != weight["sha256"]
    before = _stamp(root)
    manifest = _manifest(tmp_path / "canvas", models)
    sources = [root / str(model["legacy"]) for model in models]
    only = [str(model["id"]) for model in models]
    outcome = adopt([manifest], sources, devices=[_gpu()], only=only)
    assert outcome.code == 0
    assert not outcome.status.ready
    assert outcome.status.extra["adopted"] == []
    assert outcome.status.extra["reused"] == []
    skipped = {(row["file"], row["reason"]) for row in outcome.status.extra["skipped"]}
    assert ("config.json", "sha256 mismatch") in skipped
    assert (str(weight["path"]), "no candidate") in skipped
    assert not (models_dir() / "blobs").exists()
    assert not (models_dir() / "views").exists()
    assert _stamp(root) == before
    assert sample_path.stat().st_ino == sample_stat[0]
    assert sample_path.read_bytes() == b"\x00" * int(sample["size"])
    assert sentinel.read_bytes() == b"stay"
