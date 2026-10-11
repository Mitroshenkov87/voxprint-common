"""Window-side view of the single model queue.

Downloads go through :mod:`voxprint_common.mdl.queue`. This module turns
:class:`~voxprint_common.mdl.events.ProgressEvent` and the status file into
the snapshot the window paints, and it lists models for the manage page.
Pause, resume, and cancel call the worker's :class:`TransferSession`.
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from voxprint_common.gpu_probe import GpuDevice, VramTier
from voxprint_common.i18n import tr, tr_lang
from voxprint_common.mdl.catalog import (
    MODEL_PREFIX,
    AppManifest,
    Model,
    ModelTier,
    load_manifest,
)
from voxprint_common.mdl.events import ItemProgress, ProgressEvent, TransferSession
from voxprint_common.mdl.job import detect_tier, verify
from voxprint_common.mdl.present import (
    AppSlot,
    ItemState,
    ManageRow,
    ManageView,
    QueueItem,
    Snapshot,
    format_size,
    localized_name,
)
from voxprint_common.mdl.queue import enqueue, run_or_attach
from voxprint_common.mdl.status import JobState, Status, load_status
from voxprint_common.paths import models_dir
from voxprint_common.runtime_manifest import load, remove_ref
from voxprint_common.ui.splash import identity

__all__ = [
    "Controller",
    "ManagedFile",
    "app_outcome",
    "app_ready",
    "backend_name",
    "reset_shared",
    "shared_controller",
]

logger = logging.getLogger(__name__)
_shared: Controller | None = None
_TIER_RANK = {ModelTier.ANY: 0, ModelTier.GB8: 1, ModelTier.GB16: 2, ModelTier.GB24: 3}
_GPU_RANK = {VramTier.UNKNOWN: 1, VramTier.GB8: 1, VramTier.GB16: 2, VramTier.GB24: 3}
_STATE = {
    "pending": ItemState.PENDING,
    "queued": ItemState.PENDING,
    "planned": ItemState.PENDING,
    "downloading": ItemState.DOWNLOADING,
    "running": ItemState.DOWNLOADING,
    "verifying": ItemState.VERIFYING,
    "verified": ItemState.READY,
    "checking": ItemState.VERIFYING,
    "ready": ItemState.READY,
    "done": ItemState.READY,
    "failed": ItemState.FAILED,
    "error": ItemState.FAILED,
    "unavailable": ItemState.FAILED,
    "cancelled": ItemState.FAILED,
    "paused": ItemState.PAUSED,
}


class ManagedFile:
    """One manifest model as the manage page should list it."""

    def __init__(
        self,
        model_id: str,
        names: dict[str, str],
        fallback: str,
        size: int,
        tier_gb: int,
        optional: bool,
        sha256: str,
        installed: bool,
        refs: tuple[str, ...],
        component_id: str,
        verified: bool,
    ) -> None:
        self.model_id = model_id
        self.names = names
        self.fallback = fallback
        self.size = size
        self.tier_gb = tier_gb
        self.optional = optional
        self.sha256 = sha256
        self.installed = installed
        self.refs = refs
        self.component_id = component_id
        self.verified = verified


def backend_name() -> str:
    """Return ``queue`` because the window uses :mod:`voxprint_common.mdl.queue`."""
    return "queue"


def shared_controller() -> Controller:
    """Return the process-wide window controller, creating it on first use."""
    global _shared
    if _shared is None:
        _shared = Controller()
    return _shared


def reset_shared() -> None:
    """Drop the process-wide controller. Tests call this between cases."""
    global _shared
    if _shared is not None:
        _shared.stop()
    _shared = None


def app_ready(app_id: str, snap: Snapshot | None = None) -> bool:
    """Return whether every file for ``app_id`` is ready."""
    return (snap or shared_controller().snapshot()).app_ready(app_id)


def app_outcome(app_id: str, snap: Snapshot | None = None) -> str:
    """Return ``ready``, ``failed``, ``cancelled``, or ``running`` for one app."""
    current = snap or shared_controller().snapshot()
    if current.app_failed(app_id):
        return "failed"
    if current.app_ready(app_id):
        return "ready"
    if current.held and not current.for_app(app_id):
        return "cancelled"
    return "running"


class Controller:
    """Subscribe to the queue and remember which app asked to be shown."""

    def __init__(self) -> None:
        self._session = TransferSession()
        self._session.subscribe(self._remember)
        self._event: ProgressEvent | None = None
        self._opens: dict[str, str] = {}
        self._job_id: str | None = None
        self._worker: threading.Thread | None = None
        self._focus = ""

    def enqueue_app(
        self,
        app_id: str,
        *,
        open_cmd: str = "",
        manifest: Path | None = None,
        devices: list[GpuDevice] | None = None,
        models: tuple[str, ...] | None = None,
    ) -> str:
        """Append one app to the shared queue and return the job id.

        Args:
            app_id: Suite id.
            open_cmd: Command stored for the Open button.
            manifest: ``mdl.json`` to read. Discovered when omitted.
            devices: GPUs used to choose the tier.
            models: Model ids to fetch. ``None`` means the tier's required set.
        """
        self._opens[app_id] = open_cmd
        self._focus = app_id
        paths = [manifest] if manifest is not None else None
        self._job_id = enqueue(app_id, models, manifests=paths, devices=devices)
        return self._job_id

    def enqueue_manifests(
        self,
        paths: list[Path],
        *,
        open_cmd: str = "",
        devices: list[GpuDevice] | None = None,
    ) -> str:
        """Enqueue each manifest. The last job id is returned."""
        job_id = ""
        for path in paths:
            manifest = load_manifest(path)
            job_id = self.enqueue_app(
                manifest.app, open_cmd=open_cmd, manifest=path, devices=devices
            )
        return job_id

    def start(self, display: bool = True) -> None:
        """Run the single worker, or attach to it, on a background thread."""
        if self._worker is not None and self._worker.is_alive():
            return
        job_id = self._job_id
        session = self._session

        def run() -> None:
            try:
                run_or_attach(job_id, session=session, display=display)
            except Exception:
                logger.exception("mdl worker stopped")

        self._worker = threading.Thread(target=run, name="mdl-queue", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        """Wait for the worker thread to finish. The queue itself is not cancelled."""
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2)

    def pause(self) -> Snapshot:
        """Ask the worker to stop reading further bytes."""
        self._session.pause()
        return self.snapshot()

    def resume(self) -> Snapshot:
        """Continue a paused transfer."""
        self._session.resume()
        return self.snapshot()

    def cancel(self) -> Snapshot:
        """Stop the worker. Partials stay on disk so a later run can resume."""
        self._session.cancel()
        return self.snapshot()

    def retry(self) -> Snapshot:
        """Enqueue the focused app again and make sure a worker is running."""
        self._session.resume()
        if self._focus:
            self._job_id = enqueue(self._focus)
        self.start()
        return self.snapshot()

    def snapshot(self) -> Snapshot:
        """Return the latest progress event, or the status file when none has arrived."""
        paused = self._session.paused
        if self._event is not None:
            return _from_event(self._event, paused=paused, opens=self._opens)
        return _from_status(load_status(), paused=paused, opens=self._opens)

    def listing(
        self,
        app_id: str,
        manifest: Path,
        *,
        devices: list[GpuDevice] | None = None,
    ) -> list[ManagedFile]:
        """Return models this card can run, including optional ones."""
        parsed = load_manifest(manifest)
        return _visible(parsed, devices)

    def manage_page(
        self,
        app_id: str,
        manifest: Path,
        *,
        devices: list[GpuDevice] | None = None,
        lang: str | None = None,
    ) -> ManageView:
        """Build the manage-models page for one app."""
        code = lang
        rows = self.listing(app_id, manifest, devices=devices)
        installed: list[ManageRow] = []
        available: list[ManageRow] = []
        for row in rows:
            title = localized_name(row.names, row.fallback, code)
            bits = [format_size(row.size, lang=code)]
            if row.tier_gb:
                bits.append(_say("mdl.manage.min_vram", code, gb=row.tier_gb))
            meta = " · ".join(bits)
            others = [identity(name).display_name for name in row.refs if name != app_id]
            note = _say("mdl.manage.shared", code, apps=", ".join(others)) if others else ""
            if row.installed and row.verified:
                installed.append(
                    ManageRow(
                        row.model_id,
                        title,
                        meta,
                        "ready",
                        _say("mdl.manage.status.ok", code),
                        "remove",
                        _say("mdl.manage.remove", code),
                        note,
                    )
                )
            elif row.installed:
                installed.append(
                    ManageRow(
                        row.model_id,
                        title,
                        meta,
                        "error",
                        _say("mdl.manage.status.bad", code),
                        "repair",
                        _say("mdl.manage.repair", code),
                        note,
                    )
                )
            else:
                available.append(
                    ManageRow(
                        row.model_id,
                        title,
                        meta,
                        "pending",
                        _say("mdl.manage.status.missing", code),
                        "add",
                        _say("mdl.manage.add", code),
                        note,
                    )
                )
        folder = models_dir()
        used = _used_bytes()
        free = shutil.disk_usage(folder).free if folder.exists() else 0
        name = identity(app_id).display_name
        return ManageView(
            tuple(installed),
            tuple(available),
            _say(
                "mdl.manage.disk",
                code,
                app=name,
                used=format_size(used, lang=code),
                free=format_size(free, lang=code),
                path=str(folder),
            ),
            _say("mdl.manage.empty_installed", code, app=name),
            _say("mdl.manage.empty_available", code),
        )

    def remove_model(self, app_id: str, component_id: str) -> bool:
        """Drop this app's ref. The file is deleted only when no app still uses it."""
        document = load()
        component = document.components.get(component_id)
        path = Path(component.path) if component is not None and component.path else None
        deleted = remove_ref(component_id, app_id)
        if deleted and path is not None and path.is_file():
            path.unlink()
        return deleted

    def verify_app(self, app_id: str, manifest: Path) -> None:
        """Re-hash the planned files for one manifest."""
        del app_id
        verify([manifest])

    def _remember(self, event: ProgressEvent) -> None:
        self._event = event


def _visible(manifest: AppManifest, devices: list[GpuDevice] | None) -> list[ManagedFile]:
    tier = detect_tier(devices)
    cap = _GPU_RANK[tier]
    document = load()
    found: list[ManagedFile] = []
    for model in manifest.models:
        if model.unavailable or _TIER_RANK[model.tier] > cap:
            continue
        digest = model.files[0].sha256 if model.files else ""
        component_id = f"model-{digest[:MODEL_PREFIX]}" if digest else ""
        component = document.components.get(component_id)
        refs = tuple(component.refs) if component is not None else ()
        blob = models_dir() / "blobs" / digest if digest else None
        installed = blob is not None and blob.is_file()
        names = dict(model.names)
        if model.display_name and "en" not in names:
            names["en"] = model.display_name
        found.append(
            ManagedFile(
                model.id,
                names,
                model.display_name or model.id,
                sum(item.size for item in model.files),
                _tier_gb(model),
                model.optional or model.on_demand,
                digest,
                installed,
                refs,
                component_id,
                _verified(blob, digest),
            )
        )
    return found


def _verified(blob: Path | None, digest: str) -> bool:
    if blob is None or not digest or not blob.is_file():
        return False
    marker = blob.with_name(blob.name + ".verified")
    try:
        return marker.read_text(encoding="utf-8").strip() == digest
    except OSError:
        return False


def _tier_gb(model: Model) -> int:
    if model.min_vram_gib:
        return int(model.min_vram_gib)
    return {ModelTier.GB8: 8, ModelTier.GB16: 16, ModelTier.GB24: 24}.get(model.tier, 0)


def _used_bytes() -> int:
    folder = models_dir() / "blobs"
    if not folder.is_dir():
        return 0
    return sum(path.stat().st_size for path in folder.glob("*") if path.is_file())


def _from_event(event: ProgressEvent, *, paused: bool, opens: dict[str, str]) -> Snapshot:
    items = [_item_from_progress(item, paused=paused) for item in event.items]
    apps = _apps(items, [job.app for job in event.jobs], opens)
    snap = Snapshot(
        items=items,
        apps=apps,
        paused=paused,
        held=event.state == "cancelled",
        bytes_done=event.bytes_done,
        bytes_total=event.bytes_total,
        boot_technical=next((item.reason for item in event.items if item.reason), ""),
    )
    snap.recompute()
    return snap


def _from_status(status: Status, *, paused: bool, opens: dict[str, str]) -> Snapshot:
    items = [
        QueueItem(
            key=step.item_id or step.sha256 or step.model,
            names={"en": step.display_name or step.model},
            fallback=step.display_name or step.model,
            size=step.size,
            received=step.received,
            state=_map_state(step.state, paused=paused),
            source=step.source,
            speed_bps=step.speed_bps,
            eta_s=step.eta_s,
            apps=list(step.apps),
            sha256=step.sha256,
            technical=status.error if step.state in {"failed", "error"} else "",
        )
        for step in status.steps
    ]
    job_apps = [str(row.get("app") or "") for row in status.jobs]
    snap = Snapshot(
        items=items,
        apps=_apps(items, job_apps, opens),
        paused=paused or status.state is JobState.CANCELLED,
        held=status.state is JobState.CANCELLED,
        bytes_done=status.bytes_done,
        bytes_total=status.bytes_total,
        boot_code="generic" if status.error else "",
        boot_technical=status.error,
    )
    snap.recompute()
    return snap


def _item_from_progress(item: ItemProgress, *, paused: bool) -> QueueItem:
    return QueueItem(
        key=item.id,
        names={"en": item.display_name},
        fallback=item.display_name,
        size=item.bytes_total,
        received=item.bytes_done,
        state=_map_state(item.state, paused=paused),
        source=item.source,
        speed_bps=item.speed_bps,
        eta_s=item.eta_s,
        apps=list(item.apps),
        technical=item.reason,
        error_code="generic" if item.state in {"failed", "error", "unavailable"} else "",
    )


def _apps(items: list[QueueItem], job_apps: list[str], opens: dict[str, str]) -> list[AppSlot]:
    ordered: list[str] = []
    for app_id in job_apps:
        if app_id and app_id not in ordered:
            ordered.append(app_id)
    for item in items:
        for app_id in item.apps:
            if app_id not in ordered:
                ordered.append(app_id)
    return [AppSlot(app_id, opens.get(app_id, "")) for app_id in ordered]


def _say(key: str, lang: str | None, **params: object) -> str:
    if lang is None:
        return tr(key, **params)
    return tr_lang(key, lang, **params)


def _map_state(value: str, *, paused: bool) -> ItemState:
    state = _STATE.get(value, ItemState.PENDING)
    if paused and state is ItemState.DOWNLOADING:
        return ItemState.PAUSED
    return state
