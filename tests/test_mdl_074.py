"""v0.7.4 manage status, registry, retries, and download-all helpers."""

from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

from voxprint_common.gpu_probe import GpuDevice
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.catalog import (
    discover,
    load_manifest,
    models_for_download_all,
    register_app,
    unregister_app,
)
from voxprint_common.mdl.control import Controller, _apps, _model_kind
from voxprint_common.mdl.fetch import is_transient_error
from voxprint_common.mdl.job import announce_preparing
from voxprint_common.mdl.partials import clean_partials, list_partials
from voxprint_common.mdl.present import AppSlot, ItemState, QueueItem, view_for
from voxprint_common.mdl.progress_ui import _overall_line, reset_speed_smoother
from voxprint_common.mdl.queue import enqueue, run_or_attach
from voxprint_common.mdl.safety import UrlGuard
from voxprint_common.mdl.segments import SegmentFailed, _fetch_part
from voxprint_common.mdl.status import JobState, Status, Step, load_status
from voxprint_common.paths import derived_dir, models_dir, state_dir

PAYLOAD = b"voxprint-074-blob"
DIGEST = sha256_bytes(PAYLOAD)


def _entry(
    model_id: str,
    *,
    digest: str = DIGEST,
    size: int = len(PAYLOAD),
    optional: bool = False,
    extra_files: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    files = [
        {
            "path": f"{model_id}.bin",
            "size": size,
            "sha256": digest,
            "mirrors": [{"source": "url", "url": "https://huggingface.co/org/w.bin"}],
        }
    ]
    if extra_files:
        files.extend(extra_files)
    return {
        "id": model_id,
        "version": "1",
        "vram_tier": "any",
        "optional": optional,
        "files": files,
    }


def _manifest(folder: Path, app: str, entries: list[dict[str, object]]) -> Path:
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "app": app, "models": entries}), encoding="utf-8")
    return path


def _blob(digest: str = DIGEST, payload: bytes = PAYLOAD) -> Path:
    path = models_dir() / "blobs" / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.with_name(path.name + ".verified").write_text(digest, encoding="utf-8")
    return path


def test_register_app_round_trip_and_discover_uses_descriptor(tmp_path: Path) -> None:
    install = tmp_path / "forge-install"
    install.mkdir()
    descriptor = _manifest(tmp_path / "forge-meta", "model-forge", [_entry("weights")])
    found = register_app(
        "model-forge",
        install,
        display_name="Model Forge",
        version="rc.18",
        descriptor=descriptor,
    )
    assert found.id == "model-forge"
    assert found.path == descriptor
    again = register_app("model-forge", install, descriptor=descriptor, version="rc.19")
    assert again.version == "rc.19"
    apps = discover()
    assert [app.id for app in apps] == ["model-forge"]
    assert apps[0].path == descriptor
    assert unregister_app("model-forge") is True
    assert discover() == []
    assert unregister_app("model-forge") is False


def test_never_installed_optional_is_not_needs_repair(tmp_path: Path) -> None:
    shared = "a" * 64
    unique = "b" * 64
    # A shared tokenizer blob exists; the optional writer's unique file does not.
    other = _entry("base", digest=shared, size=3)
    writer = _entry(
        "qwen3-4b-writer",
        digest=unique,
        size=8,
        optional=True,
        extra_files=[
            {
                "path": "tokenizer.json",
                "size": 3,
                "sha256": shared,
                "mirrors": [{"source": "url", "url": "https://huggingface.co/org/t.json"}],
            }
        ],
    )
    path = _manifest(tmp_path / "canvas", "canvas", [other, writer])
    # Only the shared tokenizer is in the store.
    (models_dir() / "blobs").mkdir(parents=True, exist_ok=True)
    blob = models_dir() / "blobs" / shared
    blob.write_bytes(b"tok")
    blob.with_name(blob.name + ".verified").write_text(shared, encoding="utf-8")
    parsed = load_manifest(path)
    kind = _model_kind(
        parsed.models[1],
        "",
        None,
        siblings=parsed.models,
        app="canvas",
    )
    assert kind == "missing"
    page = Controller().manage_page("canvas", path, devices=[], lang="en")
    rows = [row for tab in page.tabs for group in tab.groups for row in group.rows]
    if not rows:
        rows = [
            row
            for tab in page.tabs
            for group in tab.groups
            for section in group.sections
            for row in section.rows
        ]
    writer_row = next(row for row in rows if row.model_id == "qwen3-4b-writer")
    assert writer_row.state_label == "Not installed"
    assert writer_row.state != "error"


def test_all_blobs_present_marks_installed_and_creates_view(tmp_path: Path) -> None:
    path = _manifest(tmp_path / "canvas", "canvas", [_entry("turbo")])
    _blob()
    parsed = load_manifest(path)
    kind = _model_kind(parsed.models[0], "", None, siblings=parsed.models, app="canvas")
    assert kind in {"ok", "needs_preparing"}
    page = Controller().manage_page("canvas", path, devices=[], lang="en")
    rows = [row for tab in page.tabs for group in tab.groups for row in group.rows]
    if not rows:
        rows = [
            row
            for tab in page.tabs
            for group in tab.groups
            for section in group.sections
            for row in section.rows
        ]
    assert rows[0].state_label in {"Installed", "Installed, needs preparing"}


def test_share_key_sibling_resolves_without_download(tmp_path: Path) -> None:
    installed = _entry("z-image-turbo")
    lite = _entry("z-image-turbo-16gb")
    # Same repo+revision share key via identical default mirrors? share_key is
    # repo@revision. Give them the same HF repo.
    for entry, ident in ((installed, "z-image-turbo"), (lite, "z-image-turbo-16gb")):
        entry["files"][0]["mirrors"] = [
            {
                "source": "huggingface",
                "repo": "Org/Z-Image-Turbo",
                "revision": "main",
                "filename": "w.bin",
            }
        ]
        entry["id"] = ident
    lite["files"][0]["sha256"] = "c" * 64
    lite["files"][0]["size"] = 4
    path = _manifest(tmp_path / "canvas", "canvas", [installed, lite])
    _blob()
    parsed = load_manifest(path)
    kind = _model_kind(parsed.models[1], "", None, siblings=parsed.models, app="canvas")
    assert kind == "ok"


def test_this_app_lists_shared_turbo(tmp_path: Path) -> None:
    canvas = _manifest(
        tmp_path / "canvas",
        "canvas",
        [
            _entry("z-image-turbo"),
            _entry("solo", digest="d" * 64, size=2),
        ],
    )
    forge = _manifest(tmp_path / "forge", "model-forge", [_entry("z-image-turbo")])
    state_dir().mkdir(parents=True, exist_ok=True)
    (state_dir() / "installed-apps.json").write_text(
        json.dumps(
            {
                "apps": [
                    {"id": "canvas", "path": str(canvas.parent)},
                    {"id": "model-forge", "path": str(forge.parent)},
                ]
            }
        ),
        encoding="utf-8",
    )
    page = Controller().manage_page("canvas", canvas, devices=[], lang="en")
    this_app = next(tab for tab in page.tabs if tab.key == "this_app")
    keys: list[str] = []
    for group in this_app.groups:
        keys.extend(row.key for row in group.rows)
        for section in group.sections:
            keys.extend(row.key for row in section.rows)
    assert "z-image-turbo" in keys


def test_models_for_download_all_includes_optional_and_drops_moss(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path / "canvas",
        "canvas",
        [
            _entry("z-image-turbo"),
            _entry("qwen3-4b-writer", optional=True, digest="e" * 64),
            _entry("moss-image-base", optional=True, digest="f" * 64),
        ],
    )
    parsed = load_manifest(path)
    ids = models_for_download_all(parsed)
    assert "z-image-turbo" in ids
    assert "qwen3-4b-writer" in ids
    assert "moss-image-base" not in ids
    assert "moss" not in ids


def test_derived_dir_layout(tmp_path: Path) -> None:
    folder = derived_dir("clarity", "upscaler", "abcdef0123456789", root=tmp_path)
    assert folder == tmp_path / "derived" / "clarity" / "upscaler" / "abcdef012345"
    assert not folder.exists()


def test_waiting_card_omits_sharing_app_without_a_job() -> None:
    item = QueueItem(
        key="file",
        names={"en": "ControlNet lite"},
        fallback="ControlNet lite",
        size=100,
        received=50,
        state=ItemState.DOWNLOADING,
        source="",
        speed_bps=0.0,
        eta_s=None,
        apps=["canvas", "model-forge"],
    )
    slots = _apps([item], ["canvas"], {})
    assert [slot.app_id for slot in slots] == ["canvas"]


def test_overall_line_labels_speed() -> None:
    reset_speed_smoother()
    status = Status(
        state=JobState.DOWNLOADING,
        bytes_total=1000,
        bytes_done=200,
        steps=[
            Step(
                model="file",
                size=1000,
                source="hf",
                speed_bps=12_300_000,
                eta_s=1,
                state="downloading",
                received=200,
                sha256="ab" * 32,
                apps=["canvas"],
            )
        ],
    )
    line = _overall_line(status, "en", "Overall")
    assert "Speed:" in line
    assert "MB/s" in line or "MiB/s" in line or "KB/s" in line or "kB/s" in line


def test_show_retry_hidden_for_cancelled_job() -> None:
    from voxprint_common.mdl.present import Snapshot

    item = QueueItem(
        key="file",
        names={"en": "x"},
        fallback="x",
        size=10,
        received=1,
        state=ItemState.PENDING,
        source="",
        speed_bps=0.0,
        eta_s=None,
        apps=["canvas"],
    )
    snap = Snapshot(items=[item], apps=[AppSlot("canvas", "")], held=True, paused=True)
    view = view_for(snap, mode="queue", focus_app="canvas", lang="en")
    assert view.show_retry is False


class _TimeoutBody:
    def __init__(self) -> None:
        self.status = 206

    def read(self, size: int = -1) -> bytes:
        del size
        raise TimeoutError("The read operation timed out")

    def close(self) -> None:
        return None


def test_segment_read_timeout_is_segment_failed(tmp_path: Path) -> None:
    part = tmp_path / "0000"
    part.write_bytes(b"abc")

    def opener(request: object, timeout: float) -> object:
        del request, timeout
        return _TimeoutBody()

    from voxprint_common.mdl.segments import ConnectionBudget

    with pytest.raises(SegmentFailed, match="timed out"):
        _fetch_part(
            "https://example.invalid/file",
            part,
            0,
            99,
            opener,
            timeout=1.0,
            chunk_size=16,
            host="example.invalid",
            budget=ConnectionBudget(4),
            user_agent="test",
            cancelled=lambda: False,
        )
    # The already-written prefix is kept so a later retry can resume.
    assert part.read_bytes() == b"abc"


def _gpu() -> GpuDevice:
    return GpuDevice(0, "Test GPU", "610.00", (8, 9), 16384)


def test_register_does_not_hide_other_installed_apps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VOXPRINT_SUITE_ROOT", str(tmp_path))
    _manifest(tmp_path / "enlarge", "enlarge", [_entry("upscaler")])
    install = tmp_path / "canvas-install"
    install.mkdir()
    descriptor = _manifest(tmp_path / "canvas-meta", "canvas", [_entry("turbo")])
    register_app("canvas", install, descriptor=descriptor)
    assert {app.id for app in discover()} == {"canvas", "enlarge"}


def test_shared_only_partial_is_not_needs_repair(tmp_path: Path) -> None:
    shared = "a" * 64
    first = _entry("base", digest=shared, size=8)
    twin = _entry("twin", digest=shared, size=8, optional=True)
    path = _manifest(tmp_path / "canvas", "canvas", [first, twin])
    folder = models_dir() / "blobs"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{shared}.partial").write_bytes(b"partial")
    parsed = load_manifest(path)
    for model in parsed.models:
        kind = _model_kind(model, "", None, siblings=parsed.models, app="canvas")
        assert kind == "missing"


def test_planning_failure_writes_failed_status(tmp_path: Path) -> None:
    path = _manifest(tmp_path / "canvas", "canvas", [_entry("turbo")])
    enqueue("canvas", manifests=[path], devices=[_gpu()])
    announce_preparing("Planning")
    path.write_text("{", encoding="utf-8")
    outcome = run_or_attach(
        None,
        devices=[_gpu()],
        guard=UrlGuard.loopback(),
        priority=False,
    )
    status = load_status()
    assert outcome.code != 0
    assert status.state is JobState.FAILED
    assert status.error
    assert status.extra.get("phase") != "preparing"


def test_clean_partials_keeps_queued_and_failed_job_files(tmp_path: Path) -> None:
    path = _manifest(tmp_path / "canvas", "canvas", [_entry("turbo")])
    folder = models_dir() / "blobs"
    folder.mkdir(parents=True, exist_ok=True)
    partial = folder / f"{DIGEST}.partial"
    partial.write_bytes(b"abc")
    orphan = folder / f"{'c' * 64}.partial"
    orphan.write_bytes(b"x")
    enqueue("canvas", models=["turbo"], manifests=[path], devices=[_gpu()])
    announce_preparing("Planning")
    rows = {item.digest: item for item in list_partials()}
    assert rows[DIGEST].owned
    assert not rows["c" * 64].owned
    report = clean_partials(apply=True)
    assert partial.is_file()
    assert not orphan.exists()
    assert any(item.digest == DIGEST for item in report.kept)


def test_disk_errors_are_not_transient() -> None:
    assert is_transient_error(TimeoutError("The read operation timed out"))
    assert is_transient_error(ConnectionResetError("Connection reset by peer"))
    assert not is_transient_error(OSError(errno.ENOSPC, "No space left on device"))
    assert not is_transient_error(PermissionError("Permission denied"))
    assert not is_transient_error(FileNotFoundError("No such file"))
