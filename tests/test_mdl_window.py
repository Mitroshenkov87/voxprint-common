"""Queue window: errors, smoothing, pause, dedup, and one shared instance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from voxprint_common.gpu_probe import GpuDevice, VramTier
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.catalog import DiskShort, load_manifest, plan_files
from voxprint_common.mdl.control import (
    Controller,
    app_outcome,
    app_ready,
    backend_name,
    reset_shared,
)
from voxprint_common.mdl.events import ItemProgress, JobProgress, ProgressEvent
from voxprint_common.mdl.fetch import Cancelled, FetchError
from voxprint_common.mdl.instance import become_primary, send
from voxprint_common.mdl.present import (
    AppSlot,
    ItemState,
    QueueItem,
    SmoothedProgress,
    Snapshot,
    explain,
    view_for,
)
from voxprint_common.paths import models_dir
from voxprint_common.runtime_manifest import add_ref
from voxprint_common.suite_settings import set_keep_awake_during_jobs

PAYLOAD = b"voxprint-window"
DIGEST = sha256_bytes(PAYLOAD)


@pytest.fixture(autouse=True)
def _quiet_power() -> None:
    set_keep_awake_during_jobs(False)
    reset_shared()
    yield
    reset_shared()


def _gpu(mib: int) -> GpuDevice:
    return GpuDevice(0, "Test GPU", "610.00", (8, 9), mib)


def _manifest(
    folder: Path,
    *,
    app: str,
    digest: str = DIGEST,
    size: int = len(PAYLOAD),
    model: str = "voice",
    tier: str = "8gb",
    optional: bool = False,
    names: dict[str, str] | None = None,
    url: str = "https://huggingface.co/org/voice.bin",
) -> Path:
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": 1,
        "app": app,
        "models": [
            {
                "id": model,
                "version": "1",
                "vram_tier": tier,
                "optional": optional,
                "name": names or {"en": "Voice", "ru": "Голос", "de": "Stimme"},
                "files": [
                    {
                        "path": "voice.bin",
                        "size": size,
                        "sha256": digest,
                        "mirrors": [{"source": "url", "url": url}],
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_explanations_hide_tracebacks() -> None:
    disk = explain(DiskShort(Path("/models"), 2000, 10))
    assert disk.code.value == "disk"
    assert "Traceback" not in disk.what_key
    view = view_for(
        Snapshot(boot_code="disk", boot_technical="Traceback (most recent call last):\nOSError"),
        lang="en",
    )
    assert "Traceback" not in view.error_what
    assert "Traceback" not in view.error_do
    assert "Traceback" in view.technical
    assert view.show_retry
    network = explain(FetchError("timed out"))
    assert network.code.value == "network"
    assert "timed out" not in explain(FetchError("timed out")).what_key
    assert explain(Cancelled("cancelled")).code.value == "cancelled"
    generic = explain(RuntimeError("boom"))
    assert generic.code.value == "generic"
    assert "boom" not in generic.what_key


def test_progress_does_not_jump() -> None:
    bar = SmoothedProgress()
    bar.set_target(1)
    previous = 0.0
    for _ in range(6):
        shown = bar.tick(0.05)
        assert shown - previous <= 0.08 + 1e-9
        previous = shown
    assert previous < 1
    bar.set_target(0)
    for _ in range(4):
        shown = bar.tick(0.05)
        assert previous - shown <= 0.08 + 1e-9
        assert shown <= previous
        previous = shown


def test_pause_resume_and_cancel() -> None:
    controller = Controller()
    event = ProgressEvent(
        items=(
            ItemProgress(
                "voice",
                "Voice",
                10,
                100,
                50.0,
                2.0,
                "huggingface",
                "downloading",
                ("clarity",),
            ),
        ),
        jobs=(JobProgress("job-1", "clarity", None, "running"),),
        bytes_done=10,
        bytes_total=100,
        state="downloading",
    )
    controller._session.publish(event)
    paused = controller.pause()
    assert paused.paused
    assert paused.items[0].state is ItemState.PAUSED
    resumed = controller.resume()
    assert resumed.paused is False
    assert resumed.items[0].state is ItemState.DOWNLOADING
    controller.cancel()
    assert controller._session.cancelled
    assert controller.snapshot().paused is False


def test_same_file_is_downloaded_once(tmp_path: Path) -> None:
    clarity = load_manifest(_manifest(tmp_path / "clarity", app="clarity"))
    enlarge = load_manifest(_manifest(tmp_path / "enlarge", app="enlarge"))
    planned = plan_files([clarity, enlarge], VramTier.GB8, models_root=tmp_path / "models")
    assert len(planned) == 1
    assert sorted(planned[0].apps) == ["clarity", "enlarge"]


def test_one_app_can_be_ready_while_another_downloads() -> None:
    clarity = QueueItem(
        key="a" * 64,
        names={"en": "Voice"},
        fallback="Voice",
        size=100,
        received=100,
        state=ItemState.READY,
        source="huggingface",
        speed_bps=0,
        eta_s=None,
        apps=["clarity"],
    )
    enlarge = QueueItem(
        key="b" * 64,
        names={"en": "Upscaler"},
        fallback="Upscaler",
        size=200,
        received=40,
        state=ItemState.DOWNLOADING,
        source="huggingface",
        speed_bps=10,
        eta_s=16,
        apps=["enlarge"],
    )
    snap = Snapshot(
        items=[clarity, enlarge],
        apps=[AppSlot("clarity", ""), AppSlot("enlarge", "")],
    )
    snap.recompute()
    assert snap.app_ready("clarity")
    assert snap.app_ready("enlarge") is False
    view = view_for(snap, focus_app="clarity", lang="en")
    ready = next(group for group in view.groups if group.app_id == "clarity")
    assert "ready" in ready.headline.lower()
    assert ready.open_label
    assert view.all_set is False
    assert app_ready("clarity", snap)


def test_optional_tier_and_shared_remove(tmp_path: Path) -> None:
    big = sha256_bytes(b"too-big")
    small = _manifest(tmp_path / "clarity", app="clarity", optional=True, model="extra")
    path = small
    document = json.loads(path.read_text(encoding="utf-8"))
    document["models"].append(
        {
            "id": "huge",
            "version": "1",
            "vram_tier": "24gb",
            "on_demand": True,
            "name": "Huge",
            "files": [
                {
                    "path": "huge.bin",
                    "size": 10,
                    "sha256": big,
                    "mirrors": [{"source": "url", "url": "https://huggingface.co/org/huge.bin"}],
                }
            ],
        }
    )
    path.write_text(json.dumps(document), encoding="utf-8")
    controller = Controller()
    rows = controller.listing("clarity", path, devices=[_gpu(8192)])
    assert [row.model_id for row in rows] == ["extra"]
    page = controller.manage_page("clarity", path, devices=[_gpu(8192)], lang="de")
    assert page.available
    assert "GB" in page.available[0].meta or "Videospeicher" in page.available[0].meta
    blob = models_dir() / "blobs" / DIGEST
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(PAYLOAD)
    component = f"model-{DIGEST[:16]}"
    add_ref(component, "clarity", kind="model", version=DIGEST, path=str(blob), sha256=DIGEST)
    add_ref(component, "enlarge", kind="model", version=DIGEST, path=str(blob), sha256=DIGEST)
    assert controller.remove_model("clarity", component) is False
    assert blob.is_file()
    assert controller.remove_model("enlarge", component) is True
    assert not blob.exists()


def test_second_launch_focuses_the_same_instance() -> None:
    primary = become_primary()
    assert primary is not None
    try:
        assert send({"cmd": "show", "app": "plotweaver", "open_cmd": "", "manifests": []})
        found = primary.poll()
        assert found[0]["app"] == "plotweaver"
        assert backend_name() == "queue"
        assert app_outcome("plotweaver") == "running"
    finally:
        primary.close()


def test_silent_import_does_not_load_qt() -> None:
    assert "PySide6" not in sys.modules
    assert "PySide6" not in Path("src/voxprint_common/mdl/cli.py").read_text(encoding="utf-8")


def test_view_uses_manifest_language() -> None:
    item = QueueItem(
        key=DIGEST,
        names={"en": "Voice", "de": "Stimme"},
        fallback="voice",
        size=1536,
        received=512,
        state=ItemState.DOWNLOADING,
        source="huggingface",
        speed_bps=256,
        eta_s=4,
        apps=["audiobook-builder"],
    )
    snap = Snapshot(items=[item], apps=[AppSlot("audiobook-builder", "")])
    snap.recompute()
    view = view_for(snap, lang="de")
    assert view.groups[0].rows[0].title == "Stimme"
    assert "Hugging Face" in view.groups[0].rows[0].meta
    assert "Paus" in view.pause_label or view.pause_label == "Pause"
