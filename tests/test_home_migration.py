"""Move an existing suite directory without deleting it."""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

import pytest

from voxprint_common import home_migration, paths
from voxprint_common.atomic import write_json
from voxprint_common.home_migration import HomeMigrationError
from voxprint_common.model_users import load_users
from voxprint_common.runtime_manifest import ManifestError, add_ref, load

SHA = "a" * 64
_RETIRED = re.compile(r"^voxprint\.migrated-\d{8}(-\d+)?$")


def _roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, windows: bool = False
) -> tuple[Path, Path]:
    monkeypatch.delenv("VOXPRINT_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(paths, "_windows", lambda: windows)
    if windows:
        base = tmp_path / "local"
        return base / "Voxprint", base / "VOXPrint AI Suite"
    base = tmp_path / "xdg"
    return base / "voxprint", base / "voxprint-ai-suite"


def _seed(root: Path, manifest: dict[str, object] | None = None) -> None:
    (root / "state").mkdir(parents=True)
    (root / "state" / "suite.json").write_text('{"ready": true}\n', encoding="utf-8")
    (root / "models" / "blobs").mkdir(parents=True)
    (root / "models" / "blobs" / "weight.bin").write_bytes(b"weights")
    (root / "models" / ".users.json").write_text(
        '{"model-weight": {"refs": ["canvas"]}}\n', encoding="utf-8"
    )
    if manifest is not None:
        shared = root / "shared"
        shared.mkdir(parents=True)
        (shared / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _manifest(legacy: Path) -> dict[str, object]:
    weight = (legacy / "models" / "blobs" / "weight.bin").as_posix()
    return {
        "schema": 1,
        "components": {
            "runtime-py314-torch-cu130": {
                "kind": "runtime",
                "version": "1",
                "path": "runtime/python",
                "sha256": SHA,
                "installed_at": "2026-01-01T00:00:00+00:00",
                "refs": ["canvas"],
                "files": [{"path": "runtime/python", "size": 1}],
            },
            "model-weight": {
                "kind": "model",
                "version": "1",
                "path": weight,
                "sha256": SHA,
                "installed_at": "2026-01-01T00:00:00+00:00",
                "refs": ["canvas"],
                "note": (legacy / "models").as_posix(),
                "elsewhere": "/usr/share/other",
                "relative": "models/blobs/weight.bin",
            },
        },
    }


def _retired(legacy: Path) -> list[Path]:
    return [item for item in legacy.parent.iterdir() if _RETIRED.fullmatch(item.name)]


def test_fresh_install_creates_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    legacy, new = _roots(monkeypatch, tmp_path)
    assert paths.home() == new
    assert paths.read_home() == new
    assert not legacy.exists()
    assert not new.exists()
    assert not home_migration._lock_path(new).exists()
    _seed(legacy)
    assert paths.home() == new
    assert (new / "state" / "suite.json").is_file()
    assert not legacy.exists()


def test_same_volume_renames_legacy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    legacy, new = _roots(monkeypatch, tmp_path)
    _seed(legacy)
    inode = (legacy / "state" / "suite.json").stat().st_ino
    assert paths.home() == new
    assert (new / "state" / "suite.json").stat().st_ino == inode
    assert (new / "models" / "blobs" / "weight.bin").read_bytes() == b"weights"
    assert not legacy.exists()
    assert _retired(legacy) == []
    assert not (new / ".voxprint-home-migration.json").exists()
    assert paths.home() == new
    assert load_users() == {"model-weight": {"refs": ["canvas"]}}


def test_copy_keeps_a_retired_legacy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(home_migration, "_same_volume", lambda *_args: False)
    legacy, new = _roots(monkeypatch, tmp_path)
    _seed(legacy, _manifest(legacy))
    original = (legacy / "shared" / "manifest.json").read_bytes()
    users = (legacy / "models" / ".users.json").read_bytes()
    assert paths.home() == new
    assert (new / "models" / "blobs" / "weight.bin").read_bytes() == b"weights"
    assert (new / "models" / ".users.json").read_bytes() == users
    retired = _retired(legacy)
    assert len(retired) == 1
    assert (retired[0] / "shared" / "manifest.json").read_bytes() == original
    assert not legacy.exists()
    assert not home_migration._staging_dir(new).exists()
    assert not home_migration._partial_path(new).exists()
    document = json.loads((new / "shared" / "manifest.json").read_text(encoding="utf-8"))
    components = document["components"]
    assert components["runtime-py314-torch-cu130"]["path"] == "runtime/python"
    weight = (new / "models" / "blobs" / "weight.bin").as_posix()
    assert components["model-weight"]["path"] == weight
    assert components["model-weight"]["relative"] == "models/blobs/weight.bin"
    paths.home()
    assert _retired(legacy) == retired


def test_both_directories_use_the_new_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    legacy, new = _roots(monkeypatch, tmp_path)
    _seed(legacy)
    (new / "state").mkdir(parents=True)
    (new / "state" / "suite.json").write_text('{"ready": false}\n', encoding="utf-8")
    legacy_bytes = (legacy / "state" / "suite.json").read_bytes()
    new_bytes = (new / "state" / "suite.json").read_bytes()
    with caplog.at_level(logging.WARNING):
        assert paths.home() == new
    assert (legacy / "state" / "suite.json").read_bytes() == legacy_bytes
    assert (new / "state" / "suite.json").read_bytes() == new_bytes
    assert any("untouched" in record.message for record in caplog.records)


def test_interrupted_copy_restarts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(home_migration, "_same_volume", lambda *_args: False)
    legacy, new = _roots(monkeypatch, tmp_path)
    _seed(legacy)
    staging = home_migration._staging_dir(new)
    staging.mkdir()
    (staging / "junk.bin").write_bytes(b"partial")
    write_json(home_migration._partial_path(new), {"schema": 1, "partial": True})
    assert paths.home() == new
    assert not staging.exists()
    assert not (new / "junk.bin").exists()
    assert (new / "state" / "suite.json").read_text(encoding="utf-8") == '{"ready": true}\n'
    assert len(_retired(legacy)) == 1


def test_checked_staging_is_published_not_recopied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(home_migration, "_same_volume", lambda *_args: False)
    legacy, new = _roots(monkeypatch, tmp_path)
    _seed(legacy)
    real_publish = home_migration._publish

    def fail_publish(staging: Path, destination: Path) -> None:
        raise RuntimeError("interrupted")

    monkeypatch.setattr(home_migration, "_publish", fail_publish)
    with pytest.raises(RuntimeError, match="interrupted"):
        paths.home()
    assert legacy.is_dir()
    assert not new.exists()
    staging = home_migration._staging_dir(new)
    inode = (staging / "state" / "suite.json").stat().st_ino
    assert (staging / ".voxprint-home-migration.json").is_file()
    monkeypatch.setattr(home_migration, "_publish", real_publish)
    assert paths.home() == new
    assert (new / "state" / "suite.json").stat().st_ino == inode
    assert len(_retired(legacy)) == 1


def test_foreign_staging_is_left_in_place(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(home_migration, "_same_volume", lambda *_args: False)
    legacy, new = _roots(monkeypatch, tmp_path)
    _seed(legacy)
    staging = home_migration._staging_dir(new)
    staging.mkdir(parents=True)
    (staging / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(HomeMigrationError):
        paths.home()
    assert (staging / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert (legacy / "state" / "suite.json").is_file()
    assert not new.exists()


def test_mismatched_copy_is_not_published(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(home_migration, "_same_volume", lambda *_args: False)
    legacy, new = _roots(monkeypatch, tmp_path)
    _seed(legacy)
    original = home_migration._copy_tree

    def corrupt(source: Path, staging: Path) -> None:
        original(source, staging)
        target = staging / "state" / "suite.json"
        target.write_bytes(b"x" * target.stat().st_size)

    monkeypatch.setattr(home_migration, "_copy_tree", corrupt)
    with pytest.raises(HomeMigrationError):
        paths.home()
    assert (legacy / "state" / "suite.json").read_text(encoding="utf-8") == '{"ready": true}\n'
    assert not new.exists()
    assert not (home_migration._staging_dir(new) / ".voxprint-home-migration.json").exists()


def test_concurrent_first_access(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(home_migration, "_same_volume", lambda *_args: False)
    legacy, new = _roots(monkeypatch, tmp_path)
    _seed(legacy)
    original = home_migration._copy_tree
    calls: list[int] = []

    def slow(source: Path, staging: Path) -> None:
        calls.append(1)
        threading.Event().wait(0.2)
        original(source, staging)

    monkeypatch.setattr(home_migration, "_copy_tree", slow)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            paths.home()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert calls == [1]
    assert (new / "models" / "blobs" / "weight.bin").read_bytes() == b"weights"
    assert len(_retired(legacy)) == 1
    assert load_users() == {"model-weight": {"refs": ["canvas"]}}


def test_manifest_paths_and_refcounts_follow_the_move(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    legacy, new = _roots(monkeypatch, tmp_path)
    _seed(legacy, _manifest(legacy))
    assert paths.home() == new
    document = load()
    assert document.components["runtime-py314-torch-cu130"].path == "runtime/python"
    assert document.components["runtime-py314-torch-cu130"].extra["files"] == [
        {"path": "runtime/python", "size": 1}
    ]
    weight = (new / "models" / "blobs" / "weight.bin").as_posix()
    model = document.components["model-weight"]
    assert model.path == weight
    assert model.extra["note"] == (new / "models").as_posix()
    assert model.extra["elsewhere"] == "/usr/share/other"
    assert model.extra["relative"] == "models/blobs/weight.bin"
    assert load_users() == {"model-weight": {"refs": ["canvas"]}}
    with pytest.raises(ManifestError, match="suite home"):
        add_ref(
            "model-home",
            "canvas",
            kind="model",
            version="1",
            path=str(paths.home()),
            sha256=SHA,
        )
    with pytest.raises(ManifestError, match="models"):
        add_ref(
            "model-root",
            "canvas",
            kind="model",
            version="1",
            path=str(paths.models_dir()),
            sha256=SHA,
        )
    other = new / "models" / "blobs" / "other.bin"
    added = add_ref(
        "model-other",
        "canvas",
        kind="model",
        version="1",
        path=str(other),
        sha256=SHA,
    )
    assert added.path == other.as_posix()


def test_windows_paths_are_injected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    legacy, new = _roots(monkeypatch, tmp_path, windows=True)
    _seed(legacy, _manifest(legacy))
    assert paths.legacy_home() == legacy
    assert paths.final_home() == new
    assert paths.home() == new
    assert paths.trt_cache_dir() == new / "trt-cache"
    assert paths.trt_cache_dir().is_relative_to(paths.home())
    assert (new / "state" / "suite.json").is_file()
    assert not legacy.exists()
    document = json.loads((new / "shared" / "manifest.json").read_text(encoding="utf-8"))
    assert (
        document["components"]["model-weight"]["path"]
        == (new / "models" / "blobs" / "weight.bin").as_posix()
    )
    assert document["components"]["model-weight"]["relative"] == "models/blobs/weight.bin"
    assert paths.models_dir() == new / "models"
    assert paths.shared_dir() == new / "shared"


def test_windows_manifest_strings_keep_their_slashes() -> None:
    legacy = Path(r"C:\Users\alex\AppData\Local\Voxprint")
    new = Path(r"C:\Users\alex\AppData\Local\VOXPrint AI Suite")
    rewritten = home_migration._rewrite_absolute(
        r"C:\Users\alex\AppData\Local\Voxprint\models\blobs\a.bin", legacy, new
    )
    assert rewritten == r"C:\Users\alex\AppData\Local\VOXPrint AI Suite\models\blobs\a.bin"
    posix = home_migration._rewrite_absolute(
        "C:/Users/alex/AppData/Local/Voxprint/models/a.bin", legacy, new
    )
    assert posix == "C:/Users/alex/AppData/Local/VOXPrint AI Suite/models/a.bin"
    relative = home_migration._rewrite_absolute(r"models\blobs\a.bin", legacy, new)
    assert relative == r"models\blobs\a.bin"
    assert (
        home_migration._rewrite_absolute(
            r"C:\Users\alex\AppData\Local\Voxprint-extra\a.bin", legacy, new
        )
        == r"C:\Users\alex\AppData\Local\Voxprint-extra\a.bin"
    )


def test_rewrite_matches_after_windows_normcase(monkeypatch: pytest.MonkeyPatch) -> None:
    def normcase(value: str) -> str:
        return value.replace("/", "\\").lower()

    monkeypatch.setattr(home_migration.os.path, "normcase", normcase)
    legacy = Path(r"C:\Users\alex\AppData\Local\Voxprint")
    new = Path(r"C:\Users\alex\AppData\Local\VOXPrint AI Suite")
    rewritten = home_migration._rewrite_absolute(
        "C:/Users/alex/AppData/Local/Voxprint/models/a.bin", legacy, new
    )
    assert rewritten == "C:/Users/alex/AppData/Local/VOXPrint AI Suite/models/a.bin"


def test_env_override_is_not_migrated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    legacy, new = _roots(monkeypatch, tmp_path)
    _seed(legacy)
    override = tmp_path / "override"
    monkeypatch.setenv("VOXPRINT_HOME", str(override))
    assert paths.home() == override
    assert paths.read_home() == override
    assert not override.exists()
    assert (legacy / "state" / "suite.json").is_file()
    assert not new.exists()


def test_readers_use_legacy_through_one_minor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    legacy, new = _roots(monkeypatch, tmp_path)
    _seed(legacy)
    assert paths.read_home() == legacy
    assert not new.exists()
    monkeypatch.setattr(paths, "__version__", "0.3.9")
    assert paths.read_home() == legacy
    monkeypatch.setattr(paths, "__version__", "0.4.0")
    assert paths.read_home() == new
    assert (legacy / "models" / ".users.json").is_file()
    assert not new.exists()
