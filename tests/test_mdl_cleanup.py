"""Cleanup reclaims a legacy copy only when a fresh hash matches a blob."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from voxprint_common.hashing import sha256_bytes, sha256_file
from voxprint_common.mdl.cleanup import cleanup
from voxprint_common.mdl.cli import main
from voxprint_common.mdl.presence import cached_digest, remember_digest
from voxprint_common.paths import manifest_file, models_dir, shared_dir, state_dir
from voxprint_common.runtime_manifest import add_ref, load, registered_paths_match

_PINS = Path(__file__).parent / "fixtures" / "laptop-2026-10-26" / "pins.json"
_SAME = b"same-verified-bytes"
_OTHER = b"different-bytes"


def test_registered_paths_match_windows_separators_and_reject_drive_relative() -> None:
    assert registered_paths_match(r"C:\models\config.json", "C:/models/config.json")
    assert registered_paths_match("C:/Models/Config.json", r"c:\models\config.json")
    assert not registered_paths_match(r"C:models\config.json", r"C:\models\config.json")
    assert not registered_paths_match("", r"C:\models\config.json")


def test_relative_manifest_path_matches_the_absolute_file_and_delete_retargets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = shared_dir() / "legacy"
    legacy = folder / "weights.bin"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(_SAME)
    digest = sha256_bytes(_SAME)
    blob = models_dir() / "blobs" / digest
    blob.parent.mkdir(parents=True)
    blob.write_bytes(_SAME)
    monkeypatch.chdir(tmp_path)
    assert registered_paths_match("legacy/weights.bin", str(legacy))
    assert not registered_paths_match("weights.bin", str(legacy))
    add_ref(
        "relative-weights",
        "canvas",
        kind="model",
        version="1",
        path="legacy/weights.bin",
        sha256=digest,
    )
    outcome = cleanup([folder], mode="delete", apply=True)
    assert outcome.code == 0
    assert not legacy.exists()
    component = load().components["relative-weights"]
    assert registered_paths_match(component.path, str(blob))
    assert component.extra["retargeted_from"] == "legacy/weights.bin"


def test_link_keeps_the_bytes_shares_the_inode_and_a_second_run_is_a_noop(tmp_path: Path) -> None:
    legacy, blob = _pair(tmp_path, _SAME)
    before = legacy.stat().st_ino
    dry = cleanup([tmp_path], mode="link", apply=False)
    assert dry.code == 0
    assert dry.status.extra["would_link"] == [
        {"path": str(legacy), "bytes": len(_SAME), "blob": str(blob)}
    ]
    assert dry.status.extra["reclaimable_bytes"] == len(_SAME)
    assert legacy.stat().st_ino == before
    assert legacy.read_bytes() == _SAME
    outcome = cleanup([tmp_path], mode="link", apply=True)
    assert outcome.code == 0
    assert outcome.status.extra["linked"] == [
        {"path": str(legacy), "bytes": len(_SAME), "blob": str(blob)}
    ]
    assert os.path.samefile(legacy, blob)
    assert legacy.stat().st_nlink >= 2
    assert legacy.stat().st_ino == blob.stat().st_ino
    assert legacy.read_bytes() == _SAME
    assert blob.read_bytes() == _SAME
    again = cleanup([tmp_path], mode="link", apply=True)
    assert again.code == 0
    assert again.status.extra["linked"] == []
    assert again.status.extra["already"][0]["path"] == str(legacy)
    assert legacy.read_bytes() == _SAME
    assert os.path.samefile(legacy, blob)


def test_link_skips_a_different_volume_and_does_not_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy, blob = _pair(tmp_path, _SAME)
    inode = legacy.stat().st_ino

    def refuse(src: object, dst: object, *args: object, **kwargs: object) -> None:
        raise OSError("cross-device link")

    monkeypatch.setattr(os, "link", refuse)
    outcome = cleanup([tmp_path], mode="link", apply=True)
    assert outcome.code == 0
    assert outcome.status.extra["linked"] == []
    assert outcome.status.extra["skipped"][0]["reason"] == "link failed"
    assert legacy.stat().st_ino == inode
    assert legacy.read_bytes() == _SAME
    assert blob.read_bytes() == _SAME
    assert not legacy.with_name(legacy.name + ".voxprint-link").exists()


def test_interrupted_link_leaves_the_original_and_a_later_run_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy, blob = _pair(tmp_path, _SAME)
    inode = legacy.stat().st_ino
    real = os.replace
    failed = {"done": False}

    def boom(src: object, dst: object, *args: object, **kwargs: object) -> None:
        if not failed["done"] and Path(str(dst)) == legacy:
            failed["done"] = True
            raise OSError("interrupted")
        real(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", boom)
    outcome = cleanup([tmp_path], mode="link", apply=True)
    assert outcome.code == 0
    assert failed["done"]
    assert legacy.stat().st_ino == inode
    assert legacy.read_bytes() == _SAME
    temporary = legacy.with_name(legacy.name + ".voxprint-link")
    assert temporary.is_file()
    assert os.path.samefile(temporary, blob)
    monkeypatch.setattr(os, "replace", real)
    again = cleanup([tmp_path], mode="link", apply=True)
    assert again.code == 0
    assert again.status.extra["linked"][0]["path"] == str(legacy)
    assert not temporary.exists()
    assert os.path.samefile(legacy, blob)
    assert legacy.read_bytes() == _SAME
    assert legacy.stat().st_nlink >= 2


def test_delete_refuses_unique_cache_mismatch_and_symlink_and_retargets() -> None:
    root = models_dir() / "Tongyi-MAI--Z-Image-Turbo"
    nested = root / "drop" / "weights.bin"
    unique = root / "keep" / "notes.bin"
    poisoned = root / "keep" / "cached.bin"
    mismatched = root / "keep" / "wrong.bin"
    linked = root / "keep" / "alias.bin"
    for path, payload in (
        (nested, _SAME),
        (unique, b"only-here"),
        (poisoned, b"only-cached"),
        (mismatched, b"label"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    digest = sha256_bytes(_SAME)
    blob = models_dir() / "blobs" / digest
    blob.parent.mkdir(parents=True)
    blob.write_bytes(_SAME)
    bad = models_dir() / "blobs" / sha256_bytes(b"label")
    bad.write_bytes(b"label-does-not-match")
    remember_digest(poisoned, digest)
    assert cached_digest(poisoned) == digest
    symlink_ok = True
    try:
        linked.symlink_to(nested)
    except OSError:
        symlink_ok = False
        linked.unlink(missing_ok=True)
    stored = _windows_path(nested)
    add_ref(
        "z-image-weights",
        "canvas",
        kind="model",
        version="1",
        path=str(nested),
        sha256=digest,
    )
    _rewrite_path("z-image-weights", stored)
    dry = cleanup([root], mode="delete", apply=False)
    assert dry.status.extra["would_delete"][0]["path"] == str(nested)
    assert {row["path"] for row in dry.status.extra["unique"]} >= {str(unique), str(poisoned)}
    assert dry.status.extra["mismatch"][0]["path"] == str(mismatched)
    if symlink_ok:
        assert dry.status.extra["skipped"][0]["reason"] == "symlink"
    assert load().components["z-image-weights"].path == stored
    outcome = cleanup([root], mode="delete", apply=True)
    assert outcome.code == 0
    assert not nested.exists()
    assert not (root / "drop").exists()
    assert unique.read_bytes() == b"only-here"
    assert poisoned.read_bytes() == b"only-cached"
    assert mismatched.read_bytes() == b"label"
    if symlink_ok:
        assert linked.is_symlink()
    assert (root / "keep").is_dir()
    assert blob.read_bytes() == _SAME
    component = load().components["z-image-weights"]
    assert registered_paths_match(component.path, str(blob))
    assert component.path.endswith(digest)
    assert component.extra["retargeted_from"] == stored
    document = json.loads(manifest_file().read_text(encoding="utf-8"))
    assert document["schema"] == 1
    again = cleanup([root], mode="delete", apply=True)
    assert again.status.extra["deleted"] == []
    assert unique.is_file()


def test_delete_rechecks_the_bytes_and_refuses_an_unverified_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy, blob = _pair(tmp_path, _SAME)
    digest = blob.name
    calls = {"n": 0}
    real = sha256_file

    def sequenced(path: Path, chunk: int = 1024 * 1024) -> str:
        calls["n"] += 1
        if calls["n"] <= 2:
            return digest if path.resolve() in {legacy.resolve(), blob.resolve()} else real(path)
        return "0" * 64

    monkeypatch.setattr(sys.modules["voxprint_common.mdl.cleanup"], "sha256_file", sequenced)
    outcome = cleanup([tmp_path], mode="delete", apply=True)
    assert outcome.code == 0
    assert outcome.status.extra["deleted"] == []
    assert outcome.status.extra["skipped"][0]["reason"] == "not verified"
    assert legacy.read_bytes() == _SAME


def test_gc_dry_run_keeps_the_blob_and_apply_removes_only_the_orphan(tmp_path: Path) -> None:
    blobs = models_dir() / "blobs"
    blobs.mkdir(parents=True)
    orphan = _blob(b"orphan-bytes")
    marker = orphan.with_name(orphan.name + ".verified")
    marker.write_text(orphan.name + "\n", encoding="utf-8")
    view_link = _blob(b"view-link")
    marker_only = _blob(b"marker-only")
    component_blob = _blob(b"component-bytes")
    discovered = _blob(b"discovered-app")
    view = models_dir() / "views" / "canvas" / "kept"
    view.mkdir(parents=True)
    os.link(view_link, view / "weights.bin")
    (view / ".voxprint-view.json").write_text(
        json.dumps({"schema": 1, "key": "kept", "files": {"other.bin": marker_only.name}}),
        encoding="utf-8",
    )
    add_ref(
        "kept-component",
        "canvas",
        kind="model",
        version="1",
        path=str(component_blob),
        sha256=component_blob.name,
    )
    app = tmp_path / "enlarge"
    app.mkdir()
    (app / "mdl.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "app": "enlarge",
                "models": [
                    {
                        "id": "weights",
                        "version": "1",
                        "vram_tier": "8gb",
                        "files": [
                            {
                                "path": "weights.bin",
                                "size": len(b"discovered-app"),
                                "sha256": discovered.name,
                                "mirrors": [{"source": "url", "url": "https://example.invalid/w"}],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    canvas = tmp_path / "canvas"
    canvas.mkdir()
    canvas_manifest = canvas / "mdl.json"
    canvas_manifest.write_text(
        json.dumps({"schema": 1, "app": "canvas", "models": []}),
        encoding="utf-8",
    )
    registry = state_dir() / "installed-apps.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "schema": 1,
                "apps": [
                    {"id": "canvas", "path": str(canvas)},
                    {"id": "enlarge", "path": str(app)},
                ],
            }
        ),
        encoding="utf-8",
    )
    dry = cleanup([], gc=True, apply=False)
    assert dry.code == 0
    assert [row["sha256"] for row in dry.status.extra["would_gc"]] == [orphan.name]
    assert orphan.is_file() and marker.is_file()
    outcome = cleanup([], manifests=[canvas_manifest], app="canvas", gc=True, apply=True)
    assert outcome.code == 0
    assert not orphan.exists()
    assert not marker.exists()
    assert [row["sha256"] for row in outcome.status.extra["gced"]] == [orphan.name]
    for kept in (view_link, marker_only, component_blob, discovered):
        assert kept.is_file()
    again = cleanup([], gc=True, apply=True)
    assert again.status.extra["would_gc"] == []
    assert again.status.extra["gced"] == []


def test_cleanup_json_defaults_to_a_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    legacy, blob = _pair(tmp_path, _SAME)
    code = main(["--json", "cleanup", "--legacy", str(tmp_path)])
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    assert code == 0
    result = rows[-1]
    assert result["cleanup_mode"] == "link"
    assert result["state"] == "dry-run"
    assert result["would_link"] == [{"path": str(legacy), "bytes": len(_SAME), "blob": str(blob)}]
    assert result["linked"] == []
    assert result["reclaimable_bytes"] == len(_SAME)
    assert legacy.stat().st_ino != blob.stat().st_ino
    assert main(["cleanup", "--apply", "--dry-run", "--gc"]) == 1
    assert main(["--gui", "cleanup", "--gc"]) == 1


def test_laptop_pins_link_reclaims_duplicate_copies_and_leaves_unique_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = json.loads(_PINS.read_text(encoding="utf-8"))["models"]
    root = models_dir()
    recorded = _pin_layout(root, models)
    reported = _pin_sizes(root, models)
    original = os.stat
    real_hash = sha256_file

    def sized(path: object, *args: object, **kwargs: object) -> os.stat_result:
        info = original(path, *args, **kwargs)  # type: ignore[arg-type]
        size = reported.get(Path(path).resolve())  # type: ignore[arg-type]
        if size is None:
            return info
        fields = list(info)
        fields[6] = size
        return os.stat_result(tuple(fields))

    def hashed(path: Path, chunk: int = 1024 * 1024) -> str:
        found = recorded.get(path.resolve())
        return found if found is not None else real_hash(path, chunk)

    monkeypatch.setattr(os, "stat", sized)
    monkeypatch.setattr(sys.modules["voxprint_common.mdl.cleanup"], "sha256_file", hashed)
    seeded = {
        str(item["sha256"])
        for model in models
        for item in model["files"]
        if item["blob_in_snapshot"]
    }
    expected_link: set[tuple[str, int, str]] = set()
    expected_unique: set[str] = set()
    reclaim = 0
    for model in models:
        legacy = root / str(model["legacy"])
        for item in model["files"]:
            if not item["in_legacy"]:
                continue
            path = legacy / str(item["path"])
            if str(item["sha256"]) in seeded:
                expected_link.add(
                    (str(path), int(item["size"]), str(root / "blobs" / str(item["sha256"])))
                )
                reclaim += int(item["size"])
            else:
                expected_unique.add(str(path))
    manifest = root.parent / "canvas-mdl.json"
    _write_pin_manifest(manifest, models)
    dry = cleanup(
        [root / str(model["legacy"]) for model in models], manifests=[manifest], app="canvas"
    )
    assert dry.code == 0
    found = {(row["path"], row["bytes"], row["blob"]) for row in dry.status.extra["would_link"]}
    assert found == expected_link
    assert {row["path"] for row in dry.status.extra["unique"]} == expected_unique
    assert dry.status.extra["reclaimable_bytes"] == reclaim
    sample = root / "Tongyi-MAI--Z-Image-Turbo" / "text_encoder" / "config.json"
    digest = "8ba006f74fecfaaeb392872a60f4a480e7ec9860153d2e1b769ec81f9a147f8a"
    blob = root / "blobs" / digest
    assert sample.stat().st_ino != blob.stat().st_ino
    outcome = cleanup(
        [root / str(model["legacy"]) for model in models],
        manifests=[manifest],
        app="canvas",
        mode="link",
        apply=True,
    )
    assert outcome.code == 0
    assert os.path.samefile(sample, blob)
    assert sample.stat().st_nlink >= 2
    assert sample.read_bytes() == b""
    unique = root / "huihui-ai--Huihui-Qwen3-4B-Instruct-2507-abliterated" / "added_tokens.json"
    assert unique.read_bytes() == b""
    assert unique.stat().st_ino != blob.stat().st_ino
    assert outcome.status.extra["would_link"] == []
    again = cleanup(
        [root / str(model["legacy"]) for model in models],
        manifests=[manifest],
        app="canvas",
        apply=True,
    )
    assert again.status.extra["linked"] == []
    assert again.status.extra["would_link"] == []
    assert {row["path"] for row in again.status.extra["already"]} == {
        path for path, _size, _blob in expected_link
    }
    assert os.path.samefile(sample, blob)


def _pair(folder: Path, payload: bytes) -> tuple[Path, Path]:
    legacy = folder / "weights.bin"
    legacy.write_bytes(payload)
    digest = sha256_bytes(payload)
    blob = models_dir() / "blobs" / digest
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(payload)
    return legacy, blob


def _blob(payload: bytes) -> Path:
    path = models_dir() / "blobs" / sha256_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _windows_path(path: Path) -> str:
    return str(path.resolve()).replace("/", "\\")


def _rewrite_path(component_id: str, stored: str) -> None:
    target = manifest_file()
    document = json.loads(target.read_text(encoding="utf-8"))
    document["components"][component_id]["path"] = stored
    target.write_text(json.dumps(document), encoding="utf-8")


def _pin_layout(root: Path, models: list[dict[str, object]]) -> dict[Path, str]:
    recorded: dict[Path, str] = {}
    blobs = root / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    for model in models:
        legacy = root / str(model["legacy"])
        files = model["files"]
        assert isinstance(files, list)
        for item in files:
            digest = str(item["sha256"])
            blob = blobs / digest
            if item["blob_in_snapshot"] and not blob.exists():
                blob.write_bytes(b"")
            if blob.exists():
                recorded[blob.resolve()] = digest
            if not item["in_legacy"]:
                continue
            path = legacy / str(item["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
            recorded[path.resolve()] = digest
    return recorded


def _pin_sizes(root: Path, models: list[dict[str, object]]) -> dict[Path, int]:
    found: dict[Path, int] = {}
    for model in models:
        legacy = root / str(model["legacy"])
        files = model["files"]
        assert isinstance(files, list)
        for item in files:
            size = int(item["size"])
            found[(root / "blobs" / str(item["sha256"])).resolve()] = size
            if item["in_legacy"]:
                found[(legacy / str(item["path"])).resolve()] = size
    return found


def _write_pin_manifest(path: Path, models: list[dict[str, object]]) -> None:
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
                        "mirrors": [{"source": "url", "url": "https://example.invalid/canvas"}],
                    }
                    for item in files
                ],
            }
        )
    path.write_text(json.dumps({"schema": 1, "app": "canvas", "models": rows}), encoding="utf-8")
