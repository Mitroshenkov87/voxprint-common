"""Repair-store integration: adoption from app folders, view repair, CLI exits.

Tiny fake files only: every weight is a few dozen bytes under a
``VOXPRINT_MODELS_DIR`` tmp root, and the Forge tree mirrors the real
``model-forge`` layout. ``repair_store`` gets an explicit fake GPU; CLI
runs plan with no GPU, which the catalog treats as an 8 GB card.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from voxprint_common.gpu_probe import GpuDevice
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.cli import main
from voxprint_common.mdl.fetch import FetchError
from voxprint_common.mdl.repair_store import repair_store
from voxprint_common.mdl.views import view_directory
from voxprint_common.paths import models_dir, state_dir
from voxprint_common.suite_settings import set_keep_awake_during_jobs

_MODEL = "z-image-turbo"
_FILES = (
    ("text_encoder/model.bin", b"text-encoder-weight-bytes-0123456789abcdef"),
    (
        "transformer/diffusion_pytorch_model.safetensors",
        b"transformer-weight-bytes-fedcba9876543210",
    ),
)


@pytest.fixture(autouse=True)
def _models_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VOXPRINT_MODELS_DIR", str(tmp_path / "models"))
    set_keep_awake_during_jobs(False)
    return models_dir()


def _gpu() -> GpuDevice:
    return GpuDevice(0, "Test GPU", "610.00", (8, 9), 24576)


def _manifest(folder: Path) -> Path:
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "app": "canvas",
                "models": [
                    {
                        "id": _MODEL,
                        "version": "1",
                        "vram_tier": "8gb",
                        "files": [
                            {
                                "path": name,
                                "size": len(payload),
                                "sha256": sha256_bytes(payload),
                                "mirrors": [f"https://example.invalid/canvas/{name}"],
                            }
                            for name, payload in _FILES
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _forge(root: Path, entries: tuple[tuple[str, bytes], ...] = _FILES) -> dict[str, Path]:
    """Lay the model out the way Model Forge keeps it, under model-forge."""
    found: dict[str, Path] = {}
    for name, payload in entries:
        path = root / "model-forge" / _MODEL / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        found[name] = path
    return found


def _digests() -> dict[str, str]:
    return {name: sha256_bytes(payload) for name, payload in _FILES}


def _stub_view(root: Path) -> Path:
    mapping = _digests()
    folder = view_directory(root, _MODEL, mapping)
    folder.mkdir(parents=True)
    return folder


def _write_blobs(root: Path) -> None:
    for _name, payload in _FILES:
        digest = sha256_bytes(payload)
        blob = root / "blobs" / digest
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(payload)
        blob.with_name(blob.name + ".verified").write_text(digest + "\n", encoding="utf-8")


def _result(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    out = capsys.readouterr().out
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert rows, out
    result = rows[-1]
    assert result["type"] == "result"
    return result


def _file_states(report: object) -> dict[str, str]:
    return {str(row["path"]): str(row["state"]) for row in report.files}


def test_laptop_adoption_repairs_the_stub_and_second_run_is_a_noop(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "canvas")
    root = models_dir()
    forge = _forge(root)
    stub = _stub_view(root)
    digests = _digests()

    report = repair_store([manifest], devices=[_gpu()])

    assert report.ready
    assert report.adopted == 2
    assert _file_states(report) == {name: "adopted" for name, _ in _FILES}
    for name, payload in _FILES:
        blob = root / "blobs" / digests[name]
        assert os.path.samefile(blob, forge[name])
        assert forge[name].read_bytes() == payload
    (view,) = report.views
    assert view["app"] == "canvas"
    assert view["model"] == _MODEL
    assert view["before"] == "stub"
    assert view["after"] == "repaired"
    assert Path(view["folder"]).is_dir()
    assert (
        json.loads((Path(view["folder"]) / ".voxprint-view.json").read_text(encoding="utf-8"))[
            "files"
        ]
        == digests
    )
    assert stub.samefile(Path(view["folder"]))
    # The Forge tree kept its bytes; nothing was moved or renamed.
    assert sorted(path.name for path in (root / "model-forge").rglob("*") if path.is_file()) == [
        "diffusion_pytorch_model.safetensors",
        "model.bin",
    ]

    def hashing_is_a_failure(path: Path) -> str:
        raise AssertionError(f"second run hashed {path}")

    again = repair_store([manifest], devices=[_gpu()], hasher=hashing_is_a_failure)
    assert again.ready
    assert again.adopted == 0
    assert _file_states(again) == {name: "store" for name, _ in _FILES}
    (view_again,) = again.views
    assert view_again["before"] == "ok"
    assert view_again["after"] == "ok"


def test_dry_run_changes_nothing_and_reports_would_repair(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "canvas")
    root = models_dir()
    _forge(root)
    stub = _stub_view(root)

    report = repair_store([manifest], devices=[_gpu()], dry_run=True)

    assert report.ready
    assert report.adopted == 0
    assert _file_states(report) == {name: "found" for name, _ in _FILES}
    (view,) = report.views
    assert view["before"] == "stub"
    assert view["after"] == "would-repair"
    assert not (root / "blobs").exists()
    assert not (root / "refs").exists()
    assert stub.is_dir() and not any(stub.iterdir())


def test_wrong_hash_copy_is_missing_and_the_cli_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest(tmp_path / "canvas")
    root = models_dir()
    same_size = bytearray(_FILES[0][1])
    same_size.reverse()
    entries = ((_FILES[0][0], bytes(same_size)), _FILES[1])
    _forge(root, entries)
    stub = _stub_view(root)

    code = main(["repair-store", "--app-manifest", str(manifest)])

    assert code == 2
    err = capsys.readouterr().err
    assert "1 missing" in err
    assert "view missing: canvas/z-image-turbo" in err
    # The matching file was still adopted; only the mismatch is missing.
    assert (root / "blobs" / sha256_bytes(_FILES[1][1])).is_file()
    assert not (root / "blobs" / sha256_bytes(_FILES[0][1])).exists()
    assert not stub.exists()
    assert not any((root / "views" / "canvas").iterdir())


def test_repair_store_drops_the_view_when_a_blob_is_truncated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A leftover truncated blob must not back a rebuilt view: the laptop path."""
    manifest = _manifest(tmp_path / "canvas")
    root = models_dir()
    digests = _digests()
    good_name, good_payload = _FILES[0]
    good_blob = root / "blobs" / digests[good_name]
    good_blob.parent.mkdir(parents=True)
    good_blob.write_bytes(good_payload)
    good_blob.with_name(good_blob.name + ".verified").write_text(
        digests[good_name] + "\n", encoding="utf-8"
    )
    bad_name, bad_payload = _FILES[1]
    bad_blob = root / "blobs" / digests[bad_name]
    truncated = bad_payload[:8]
    bad_blob.write_bytes(truncated)
    stub = _stub_view(root)

    code = main(["repair-store", "--app-manifest", str(manifest)])

    assert code == 2
    err = capsys.readouterr().err
    assert "1 missing" in err
    assert "view missing: canvas/z-image-turbo" in err
    # The bad blob is left untouched and never backs a view.
    assert bad_blob.read_bytes() == truncated
    assert good_blob.read_bytes() == good_payload
    assert not stub.exists()
    assert not any((root / "views" / "canvas").iterdir())


def test_verify_flags_a_stub_view_and_verify_repair_fixes_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest(tmp_path / "canvas")
    root = models_dir()
    _write_blobs(root)
    _stub_view(root)

    code = main(["verify", "--app-manifest", str(manifest), "--json"])
    result = _result(capsys)

    assert code == 2
    assert result["ready"] is False
    rows = {row["id"]: row for row in result["models"]}
    assert rows[_MODEL]["state"] == "stub"
    assert result["views"][0]["before"] == "stub"
    assert result["views"][0]["after"] == "would-repair"

    code = main(["verify", "--repair", "--app-manifest", str(manifest), "--json"])
    result = _result(capsys)

    assert code == 0
    assert result["ready"] is True


def test_ensure_adopts_from_the_forge_folder_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest(tmp_path / "canvas")
    root = models_dir()
    _forge(root)

    def no_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("ensure tried to download")

    monkeypatch.setattr("voxprint_common.mdl.job.fetch_file", no_network)

    code = main(["--json", "ensure", "--app-manifest", str(manifest)])
    result = _result(capsys)

    assert code == 0
    assert result["ready"] is True
    rows = {row["id"]: row for row in result["models"]}
    assert rows[_MODEL]["state"] == "reused"
    (view,) = result["views"]
    assert view["after"] == "ok"
    blob = root / "blobs" / sha256_bytes(_FILES[0][1])
    assert os.path.samefile(blob, root / "model-forge" / _MODEL / _FILES[0][0])


def test_ensure_adopts_over_a_symlink_blob_and_repairs_the_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A leftover symlink blob with a live Forge copy becomes a regular file."""
    manifest = _manifest(tmp_path / "canvas")
    root = models_dir()
    forge = _forge(root)
    name, payload = _FILES[0]
    digest = sha256_bytes(payload)
    blob = root / "blobs" / digest
    blob.parent.mkdir(parents=True)
    external = tmp_path / "leftover.safetensors"
    external.write_bytes(payload)
    try:
        blob.symlink_to(external)
    except OSError:
        pytest.skip("this platform cannot create symlinks")
    blob.with_name(blob.name + ".verified").write_text(digest + "\n", encoding="utf-8")

    def no_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("ensure tried to download")

    monkeypatch.setattr("voxprint_common.mdl.job.fetch_file", no_network)

    code = main(["--json", "ensure", "--app-manifest", str(manifest)])
    result = _result(capsys)

    assert code == 0
    assert result["ready"] is True
    (view,) = result["views"]
    assert view["after"] == "ok"
    assert not blob.is_symlink()
    assert stat.S_ISREG(blob.lstat().st_mode)
    assert os.path.samefile(blob, forge[name])


def test_ensure_with_a_symlink_blob_and_no_source_is_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no copy to adopt, ensure must not skip the download or report ready."""
    manifest = _manifest(tmp_path / "canvas")
    root = models_dir()
    for name, payload in _FILES:
        digest = sha256_bytes(payload)
        blob = root / "blobs" / digest
        blob.parent.mkdir(parents=True, exist_ok=True)
        external = tmp_path / f"leftover-{name.replace('/', '-')}"
        external.write_bytes(payload)
        try:
            blob.symlink_to(external)
        except OSError:
            pytest.skip("this platform cannot create symlinks")
        blob.with_name(blob.name + ".verified").write_text(digest + "\n", encoding="utf-8")

    def no_source(*args: object, **kwargs: object) -> object:
        raise FetchError("no network in this test")

    monkeypatch.setattr("voxprint_common.mdl.job.fetch_file", no_source)

    code = main(["--json", "ensure", "--app-manifest", str(manifest)])
    result = _result(capsys)

    assert code != 0
    assert result["ready"] is False
    for _name, payload in _FILES:
        blob = root / "blobs" / sha256_bytes(payload)
        assert not blob.is_symlink()


def test_repair_store_adopts_from_a_from_directory(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "canvas")
    root = models_dir()
    external = tmp_path / "incoming"
    for name, payload in _FILES:
        path = external / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    code = main(["repair-store", "--app-manifest", str(manifest), "--from", str(external)])

    assert code == 0
    for name, payload in _FILES:
        blob = root / "blobs" / sha256_bytes(payload)
        assert os.path.samefile(blob, external / name)
    view = next((root / "views" / "canvas").iterdir())
    assert (view / ".voxprint-view.json").is_file()


def test_no_arguments_discover_every_installed_app_and_repair(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The no-argument laptop migration: discover, adopt, and repair views."""
    manifest = _manifest(tmp_path / "canvas")
    root = models_dir()
    forge = _forge(root)
    _stub_view(root)
    state_dir().mkdir(parents=True, exist_ok=True)
    (state_dir() / "installed-apps.json").write_text(
        json.dumps({"schema": 1, "apps": [{"id": "canvas", "path": str(manifest.parent)}]}),
        encoding="utf-8",
    )

    code = main(["repair-store", "--json"])
    result = _result(capsys)

    assert code == 0
    assert result["ok"] is True
    assert result["ready"] is True
    assert result["adopted"] == 2
    assert result["missing"] == 0
    assert result["repaired_views"] == 1
    digests = _digests()
    for name, payload in _FILES:
        blob = root / "blobs" / digests[name]
        assert os.path.samefile(blob, forge[name])
        assert forge[name].read_bytes() == payload
    (view,) = result["views"]
    assert view["app"] == "canvas"
    assert view["model"] == _MODEL
    assert view["before"] == "stub"
    assert view["after"] == "repaired"
    assert (root / "views" / "canvas").is_dir()
