"""Shared-manifest paths cannot wipe another app's models."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from voxprint_common.model_users import add_user, load_users
from voxprint_common.paths import home, models_dir
from voxprint_common.runtime_manifest import (
    ManifestError,
    add_ref,
    load,
    release_component,
    repair_manifest,
)

CHILD = Path(__file__).with_name("manifest_child.py")
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _component(path: str, refs: list[str], *, kind: str = "model") -> dict[str, object]:
    return {
        "kind": kind,
        "version": "1",
        "path": path,
        "sha256": SHA_A,
        "installed_at": "2026-10-11T00:00:00+00:00",
        "refs": refs,
    }


def test_models_root_is_not_deleted_when_it_is_the_only_ref(tmp_path: Path) -> None:
    models = models_dir()
    models.mkdir(parents=True)
    kept = models / "audiobook.bin"
    kept.write_bytes(b"plotweaver-and-audiobook")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "components": {"models": _component(str(models), ["movie-dubber"])},
            }
        ),
        encoding="utf-8",
    )
    preview = release_component("models", "movie-dubber", manifest=manifest, dry_run=True)
    assert preview.deleted is False
    assert kept.read_bytes() == b"plotweaver-and-audiobook"
    result = release_component("models", "movie-dubber", manifest=manifest)
    assert result.deleted is False
    assert result.ref_dropped is True
    assert kept.is_file()
    assert models.is_dir()
    assert "models" not in load(manifest).components


def test_directory_that_contains_the_models_root_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom" / "models"
    custom.mkdir(parents=True)
    monkeypatch.setenv("VOXPRINT_MODELS_DIR", str(custom))
    manifest = tmp_path / "manifest.json"
    with pytest.raises(ManifestError, match="models root"):
        add_ref(
            "models",
            "movie-dubber",
            kind="model",
            version="1",
            path=str(custom),
            sha256=SHA_A,
            manifest=manifest,
        )
    with pytest.raises(ManifestError, match="models root"):
        add_ref(
            "parent",
            "movie-dubber",
            kind="model",
            version="1",
            path=str(custom.parent),
            sha256=SHA_B,
            manifest=manifest,
        )
    with pytest.raises(ManifestError, match="suite home"):
        add_ref(
            "home",
            "movie-dubber",
            kind="model",
            version="1",
            path=str(home()),
            sha256=SHA_C,
            manifest=manifest,
        )
    assert not manifest.exists()


def test_overlapping_component_paths_are_rejected(tmp_path: Path) -> None:
    models = models_dir()
    parent = models / "pack"
    child = parent / "inner"
    child.mkdir(parents=True)
    manifest = tmp_path / "manifest.json"
    add_ref(
        "pack",
        "movie-dubber",
        kind="model",
        version="1",
        path=str(parent),
        sha256=SHA_A,
        manifest=manifest,
    )
    with pytest.raises(ManifestError, match="overlaps"):
        add_ref(
            "inner",
            "enlarge",
            kind="model",
            version="1",
            path=str(child),
            sha256=SHA_B,
            manifest=manifest,
        )
    other = tmp_path / "other.json"
    add_ref(
        "inner",
        "enlarge",
        kind="model",
        version="1",
        path=str(child),
        sha256=SHA_B,
        manifest=other,
    )
    with pytest.raises(ManifestError, match="overlaps"):
        add_ref(
            "pack",
            "movie-dubber",
            kind="model",
            version="1",
            path=str(parent),
            sha256=SHA_A,
            manifest=other,
        )


def test_release_deletes_only_an_unreferenced_model_folder(tmp_path: Path) -> None:
    folder = models_dir() / "whisper"
    folder.mkdir(parents=True)
    (folder / "weights.bin").write_bytes(b"whisper")
    manifest = tmp_path / "manifest.json"
    add_ref(
        "whisper",
        "movie-dubber",
        kind="model",
        version="1",
        path=str(folder),
        sha256=SHA_A,
        manifest=manifest,
    )
    users = models_dir() / ".users.json"
    users.write_text(
        json.dumps({"whisper": ["audiobook-builder"], "note": {"keep": True}, "other": True}),
        encoding="utf-8",
    )
    blocked = release_component("whisper", "movie-dubber", manifest=manifest, users=users)
    assert blocked.deleted is False
    assert (folder / "weights.bin").is_file()
    assert load_users(users)["note"] == {"keep": True}
    assert load_users(users)["other"] is True
    users.write_text(json.dumps({"note": {"keep": True}}), encoding="utf-8")
    add_ref(
        "whisper",
        "movie-dubber",
        kind="model",
        version="1",
        path=str(folder),
        sha256=SHA_A,
        manifest=manifest,
    )
    removed = release_component("whisper", "movie-dubber", manifest=manifest, users=users)
    assert removed.deleted is True
    assert not folder.exists()
    assert load_users(users)["note"] == {"keep": True}


def test_users_merge_keeps_legacy_shapes(tmp_path: Path) -> None:
    path = tmp_path / ".users.json"
    path.write_text(
        json.dumps(
            {
                "whisper": ["audiobook-builder"],
                "dubber-model": True,
                "embed": {"refs": ["plotweaver"], "vendor": "keep"},
            }
        ),
        encoding="utf-8",
    )
    add_user("whisper", "plotweaver", path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["whisper"] == ["audiobook-builder", "plotweaver"]
    assert saved["dubber-model"] is True
    assert saved["embed"]["vendor"] == "keep"
    assert saved["embed"]["refs"] == ["plotweaver"]


def test_repair_clears_a_models_root_entry_without_deleting(tmp_path: Path) -> None:
    models = models_dir()
    models.mkdir(parents=True)
    kept = models / "plotweaver.bin"
    kept.write_bytes(b"stay")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"schema": 1, "components": {"models": _component(str(models), ["movie-dubber"])}}
        ),
        encoding="utf-8",
    )
    preview = repair_manifest(manifest=manifest, dry_run=True)
    assert preview
    assert kept.is_file()
    assert json.loads(manifest.read_text(encoding="utf-8"))["components"]["models"]["path"]
    notes = repair_manifest(manifest=manifest)
    assert notes
    assert kept.read_bytes() == b"stay"
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["components"]["models"]["path"] == ""
    assert saved["components"]["models"]["neutralised_from"]
    assert saved["components"]["models"]["refs"] == ["movie-dubber"]


def test_concurrent_writers_keep_both_components(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema": 1, "components": {}, "vendor": "keep"}),
        encoding="utf-8",
    )
    first = [
        sys.executable,
        str(CHILD),
        str(manifest),
        "alpha",
        "enlarge",
        "runtime/alpha",
        SHA_A,
    ]
    second = [
        sys.executable,
        str(CHILD),
        str(manifest),
        "beta",
        "canvas",
        "runtime/beta",
        SHA_B,
    ]
    processes = [subprocess.Popen(first), subprocess.Popen(second)]
    codes = [process.wait(30) for process in processes]
    assert codes == [0, 0]
    document = load(manifest)
    assert set(document.components) == {"alpha", "beta"}
    assert document.extra["vendor"] == "keep"
