"""Local adopt: link a match, skip a mismatch, leave the source, reuse the blob."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from voxprint_common.gpu_probe import GpuDevice, VramTier
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.adopt import adopt
from voxprint_common.mdl.catalog import load_manifest, plan_files
from voxprint_common.mdl.cli import main
from voxprint_common.mdl.control import _visible
from voxprint_common.mdl.job import download
from voxprint_common.paths import models_dir, state_dir
from voxprint_common.runtime_manifest import load

GOOD = b"canvas-weight-ok"
BAD = b"canvas-weight-no"
OTHER = b"canvas-weight-qq"
NESTED = b"canvas-voice-pack"


def _gpu(mib: int) -> GpuDevice:
    return GpuDevice(0, "Test GPU", "610.00", (8, 9), mib)


def _file(model_id: str, name: str, payload: bytes) -> dict[str, object]:
    return {
        "id": model_id,
        "version": "1",
        "vram_tier": "8gb",
        "files": [
            {
                "path": name,
                "size": len(payload),
                "sha256": sha256_bytes(payload),
                "mirrors": [{"source": "url", "url": "https://example.invalid/" + name}],
            }
        ],
    }


def _manifest(folder: Path) -> Path:
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": 1,
        "app": "canvas",
        "models": [
            _file("weights", "weights.bin", GOOD),
            _file("voice", "voice.bin", NESTED),
            _file("other", "other.bin", OTHER),
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _drop(folder: Path) -> tuple[Path, Path, Path]:
    root = folder / "drop"
    (root / "nested").mkdir(parents=True)
    good = root / "weights.bin"
    good.write_bytes(GOOD)
    nested = root / "nested" / "voice.bin"
    nested.write_bytes(NESTED)
    wrong = root / "other.bin"
    wrong.write_bytes(BAD)
    return good, nested, wrong


def test_adopt_links_a_match_skips_a_mismatch_and_second_run_is_a_noop(tmp_path: Path) -> None:
    assert len(GOOD) == len(BAD) == len(OTHER)
    manifest = _manifest(tmp_path / "canvas")
    good, nested, wrong = _drop(tmp_path)
    good_stat = (good.stat().st_dev, good.stat().st_ino, good.stat().st_mtime_ns)
    nested_stat = (nested.stat().st_dev, nested.stat().st_ino, nested.stat().st_mtime_ns)
    wrong_bytes = wrong.read_bytes()
    devices = [_gpu(8192)]
    outcome = adopt([manifest], [tmp_path / "drop"], devices=devices)
    assert outcome.code == 0
    assert not outcome.status.ready
    adopted = {row["file"] for row in outcome.status.extra["adopted"]}
    assert adopted == {"weights.bin", "voice.bin"}
    assert outcome.status.extra["skipped"] == [
        {
            "model": "other",
            "file": "other.bin",
            "sha256": sha256_bytes(OTHER),
            "reason": "sha256 mismatch",
        }
    ]
    blob = models_dir() / "blobs" / sha256_bytes(GOOD)
    voice = models_dir() / "blobs" / sha256_bytes(NESTED)
    assert blob.read_bytes() == GOOD
    assert voice.read_bytes() == NESTED
    assert (blob.stat().st_dev, blob.stat().st_ino) == (good_stat[0], good_stat[1])
    assert (voice.stat().st_dev, voice.stat().st_ino) == (nested_stat[0], nested_stat[1])
    assert not (models_dir() / "blobs" / sha256_bytes(BAD)).exists()
    assert good.read_bytes() == GOOD
    assert nested.read_bytes() == NESTED
    assert wrong.read_bytes() == wrong_bytes
    assert (good.stat().st_dev, good.stat().st_ino, good.stat().st_mtime_ns) == good_stat
    assert outcome.status.extra["duplicate"] == []
    assert outcome.status.extra["reclaimable_bytes"] == 0
    assert "duplicate" not in outcome.message
    marker = blob.with_name(blob.name + ".verified")
    assert marker.read_text(encoding="utf-8").strip() == sha256_bytes(GOOD)
    component = load().components[f"model-{sha256_bytes(GOOD)[:12]}"]
    assert "canvas" in component.refs
    rows = {row.model_id: row for row in _visible(load_manifest(manifest), devices)}
    assert rows["weights"].installed and rows["weights"].verified
    assert rows["voice"].installed and rows["voice"].verified
    assert rows["other"].installed is False
    again = adopt([manifest], [tmp_path / "drop"], devices=devices)
    assert again.code == 0
    assert again.status.extra["adopted"] == []
    reused = {row["file"] for row in again.status.extra["reused"]}
    assert reused == {"weights.bin", "voice.bin"}
    assert again.status.extra["skipped"][0]["file"] == "other.bin"
    assert (good.stat().st_dev, good.stat().st_ino, good.stat().st_mtime_ns) == good_stat
    assert (blob.stat().st_dev, blob.stat().st_ino) == (good_stat[0], good_stat[1])
    assert good.read_bytes() == GOOD
    assert wrong.read_bytes() == wrong_bytes


def test_adopt_copies_when_a_hardlink_is_impossible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "canvas")
    good, _nested, _wrong = _drop(tmp_path)
    real_link = os.link

    def refuse(src: object, dst: object, *args: object, **kwargs: object) -> None:
        # A file on another volume cannot be linked into blobs. The view is on
        # the same volume as the blob, so that link still works.
        if Path(str(dst)).parent.name == "blobs":
            raise OSError("cross-device link")
        real_link(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "link", refuse)
    outcome = adopt(
        [manifest],
        [tmp_path / "drop"],
        devices=[_gpu(8192)],
        only=["weights"],
    )
    assert outcome.code == 0
    assert outcome.status.ready
    blob = models_dir() / "blobs" / sha256_bytes(GOOD)
    assert blob.read_bytes() == GOOD
    assert blob.stat().st_ino != good.stat().st_ino
    assert good.read_bytes() == GOOD
    assert outcome.status.extra["adopted"][0]["file"] == "weights.bin"
    assert outcome.status.extra["duplicate"] == [
        {"path": str(good), "bytes": len(GOOD), "blob": str(blob)}
    ]
    assert outcome.status.extra["reclaimable_bytes"] == len(GOOD)
    assert f"duplicate {good} {len(GOOD)} {blob}" in outcome.message


def test_adopt_dry_run_json_does_not_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest(tmp_path / "canvas")
    _drop(tmp_path)
    code = main(
        [
            "--json",
            "adopt",
            "--dry-run",
            "--app",
            "canvas",
            "--manifest",
            str(manifest),
            "--from",
            str(tmp_path / "drop"),
        ]
    )
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    assert code == 0
    result = rows[-1]
    assert result["type"] == "result"
    assert result["ok"] is True
    names = {row["file"] for row in result["would_adopt"]}
    assert names == {"weights.bin", "voice.bin", "other.bin"}
    assert result["adopted"] == []
    assert result["duplicate"] == []
    assert result["reclaimable_bytes"] == 0
    assert not (models_dir() / "blobs").exists()
    assert main(["adopt", "--manifest", str(manifest)]) == 1


def test_download_writes_verified_marker_for_a_fetched_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "canvas")
    fetches = 0
    hashes = 0

    def fetched(dest: Path, *_args: object, **_kwargs: object) -> None:
        nonlocal fetches
        fetches += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(GOOD)

    def hashed(*_args: object, **_kwargs: object) -> str:
        nonlocal hashes
        hashes += 1
        raise AssertionError("hashed")

    monkeypatch.setattr("voxprint_common.mdl.job.fetch_file", fetched)
    monkeypatch.setattr("voxprint_common.mdl.job.verify_hash", hashed)
    outcome = download([manifest], devices=[_gpu(8192)], priority=False, only=["weights"])
    assert outcome.code == 0
    assert outcome.status.ready
    planned = plan_files([load_manifest(manifest)], VramTier.GB8, memory_gib=8, only=["weights"])
    blob = planned[0].destination
    marker = blob.with_name(blob.name + ".verified")
    assert marker.read_text(encoding="utf-8").strip() == sha256_bytes(GOOD)
    assert fetches == 1
    cache = state_dir() / "mdl-hash-cache.json"
    if cache.exists():
        cache.unlink()
    os.utime(blob, (1, 1))
    again = download([manifest], devices=[_gpu(8192)], priority=False, only=["weights"])
    assert again.code == 0
    assert again.status.ready
    assert fetches == 1
    assert hashes == 0
    assert blob.read_bytes() == GOOD


def test_adopt_replaces_a_leftover_blob(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "canvas")
    good, _nested, _wrong = _drop(tmp_path)
    planned = plan_files([load_manifest(manifest)], VramTier.GB8, memory_gib=8, only=["weights"])
    blob = planned[0].destination
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(BAD)
    source = (good.stat().st_ino, good.stat().st_mtime_ns, good.read_bytes())
    devices = [_gpu(8192)]
    preview = adopt(
        [manifest],
        [tmp_path / "drop"],
        devices=devices,
        only=["weights"],
        dry_run=True,
    )
    assert preview.code == 0
    assert [row["file"] for row in preview.status.extra["would_adopt"]] == ["weights.bin"]
    assert blob.read_bytes() == BAD
    outcome = adopt([manifest], [tmp_path / "drop"], devices=devices, only=["weights"])
    assert outcome.code == 0
    assert outcome.status.ready
    assert outcome.status.extra["skipped"] == []
    assert [row["file"] for row in outcome.status.extra["adopted"]] == ["weights.bin"]
    assert blob.read_bytes() == GOOD
    assert os.path.samefile(good, blob)
    assert (good.stat().st_ino, good.stat().st_mtime_ns, good.read_bytes()) == source
    marker = blob.with_name(blob.name + ".verified")
    assert marker.read_text(encoding="utf-8").strip() == sha256_bytes(GOOD)
    assert not blob.with_name(blob.name + ".adopting").exists()


def test_download_reuses_an_existing_blob(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _manifest(tmp_path / "canvas")
    planned = plan_files([load_manifest(manifest)], VramTier.GB8, memory_gib=8, only=["weights"])
    blob = planned[0].destination
    blob.parent.mkdir(parents=True)
    blob.write_bytes(GOOD)

    def fetched(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("downloaded")

    monkeypatch.setattr("voxprint_common.mdl.job.fetch_file", fetched)
    outcome = download([manifest], devices=[_gpu(8192)], priority=False, only=["weights"])
    assert outcome.code == 0
    assert outcome.status.ready
    assert blob.read_bytes() == GOOD
    marker = blob.with_name(blob.name + ".verified")
    assert marker.read_text(encoding="utf-8").strip() == sha256_bytes(GOOD)
    cache = state_dir() / "mdl-hash-cache.json"
    if cache.exists():
        cache.unlink()

    def hashed(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("hashed")

    monkeypatch.setattr("voxprint_common.mdl.job.verify_hash", hashed)
    again = download([manifest], devices=[_gpu(8192)], priority=False, only=["weights"])
    assert again.code == 0
    assert again.status.ready
    assert blob.read_bytes() == GOOD


def test_adopt_json_lists_a_separate_copy_of_an_existing_blob(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest(tmp_path / "canvas")
    good, _nested, _wrong = _drop(tmp_path)
    blob = models_dir() / "blobs" / sha256_bytes(GOOD)
    blob.parent.mkdir(parents=True)
    blob.write_bytes(GOOD)
    source = (good.stat().st_ino, good.read_bytes())
    code = main(
        [
            "--json",
            "adopt",
            "--app",
            "canvas",
            "--manifest",
            str(manifest),
            "--model",
            "weights",
            "--from",
            str(tmp_path / "drop"),
        ]
    )
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    assert code == 0
    result = rows[-1]
    assert result["type"] == "result"
    assert result["reused"][0]["file"] == "weights.bin"
    assert result["adopted"] == []
    assert result["duplicate"] == [{"path": str(good), "bytes": len(GOOD), "blob": str(blob)}]
    assert result["reclaimable_bytes"] == len(GOOD)
    assert (good.stat().st_ino, good.read_bytes()) == source
    assert "duplicate" in result["message"]
