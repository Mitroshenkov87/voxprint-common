"""Window-side view of the single model queue.

Downloads go through :mod:`voxprint_common.mdl.queue`. This module turns
:class:`~voxprint_common.mdl.events.ProgressEvent` and the status file into
the snapshot the window paints, and it lists models for the manage page.
Pause, resume, and cancel call the worker's :class:`TransferSession`.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import threading
from collections.abc import Sequence
from pathlib import Path

from voxprint_common.gpu_probe import GpuDevice, VramTier, query_gpus
from voxprint_common.i18n import tr, tr_lang
from voxprint_common.mdl.catalog import (
    MODEL_PREFIX,
    SUITE_APPS,
    AppManifest,
    CatalogError,
    Model,
    ModelFile,
    ModelTier,
    discover,
    load_manifest,
    model_share_key,
)
from voxprint_common.mdl.events import ItemProgress, ProgressEvent, TransferSession
from voxprint_common.mdl.hooks import remove_outputs
from voxprint_common.mdl.job import detect_tier, verify
from voxprint_common.mdl.presence import classify_file, remember_stat, stored_stat
from voxprint_common.mdl.present import (
    AppSlot,
    ItemState,
    ManageRow,
    ManageTab,
    ManageView,
    QueueItem,
    Snapshot,
    can_remove,
    display_path,
    format_size,
    group_manage_rows,
    localized_name,
    normalize_group,
)
from voxprint_common.mdl.queue import QueueJob, enqueue, load_queue, run_or_attach
from voxprint_common.mdl.status import JobState, Status, load_status
from voxprint_common.paths import models_dir
from voxprint_common.runtime_manifest import load, release_component
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
_TIER_BADGES = {ModelTier.GB8: "8gb", ModelTier.GB16: "16gb", ModelTier.GB24: "24gb"}
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
        label: str = "",
        purpose: str = "",
        group: str = "",
        tier: ModelTier = ModelTier.ANY,
        share_key: str = "",
        kind: str = "",
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
        self.label = label
        self.purpose = purpose
        self.group = group
        self.tier = tier
        self.share_key = share_key
        self.kind = kind or ("ok" if verified else "missing" if not installed else "damaged")


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


def _empty_retry(job: QueueJob) -> bool:
    """True when a failed job has no models and no manifests to re-queue."""
    return (job.models is not None and not job.models) and not job.manifests


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
        self._gpus_cache: list[GpuDevice] | None = None

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
            models: Model ids to fetch. ``None`` means the tier's required
                set. An entry may be a manage-row selector (``id|share_key|
                tier|label``) naming one exact row of a duplicate-id model,
                which is how Add/Repair targets the clicked row.
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
        models: tuple[str, ...] | None = None,
    ) -> str:
        """Enqueue each manifest. The last job id is returned."""
        job_id = ""
        for path in paths:
            manifest = load_manifest(path)
            job_id = self.enqueue_app(
                manifest.app,
                open_cmd=open_cmd,
                manifest=path,
                devices=devices,
                models=models,
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
        """Re-queue the focused failed job's exact models and manifests."""
        self._session.resume()
        job = self._retry_job()
        if job is not None:
            manifests = [Path(item) for item in job.manifests] if job.manifests else None
            self._job_id = enqueue(
                job.app or self._focus,
                job.models,
                manifests=manifests,
                include_groups=job.include_groups,
            )
        self.start()
        return self.snapshot()

    def _retry_job(self) -> QueueJob | None:
        """Return the failed job Retry should re-queue, or None.

        Prefers the job this window was tracking. An earlier Retry bug
        recorded empty ``models=[]`` jobs as failed; those are skipped so
        a later press re-queues the original request, never another empty
        job.
        """
        try:
            jobs = load_queue()
        except Exception:
            return None
        failed = [job for job in jobs if job.state == "failed" and not _empty_retry(job)]
        if self._job_id:
            for job in failed:
                if job.id == self._job_id:
                    return job
        if self._focus:
            failed = [job for job in failed if job.app == self._focus]
        return failed[-1] if failed else None

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
        all_tiers: bool = False,
    ) -> list[ManagedFile]:
        """Return models this card can run, including optional ones.

        Pass ``all_tiers=True`` to include models above this card's VRAM
        tier, which the manage page shows under "models for other cards".
        """
        parsed = load_manifest(manifest)
        return _visible(parsed, self._gpus(devices), all_tiers=all_tiers)

    def manage_page(
        self,
        app_id: str,
        manifest: Path,
        *,
        devices: list[GpuDevice] | None = None,
        lang: str | None = None,
    ) -> ManageView:
        """Build the manage-models page for one app.

        Unlike :meth:`listing`, the page also lists models above this
        card's VRAM tier. They stay addable and sit in the collapsed
        "other cards" section of a split task group.

        Rows split into "This app" and "Shared" tabs. A row is shared when
        the shared store refcount names more than one app, or another
        installed app lists the same share key (repo+revision) in its
        manifest. Shared rows name every app that refs the component or
        lists the key, never offer Remove on a model any installed app
        marks required or safety, and fail closed on a broken shared
        safety copy: Repair plus a "Required, …" status, never Remove.
        """
        code = lang
        gpus = self._gpus(devices)
        parsed = load_manifest(manifest)
        rows = _visible(parsed, gpus, all_tiers=True)
        keys = _row_keys(rows)
        listed_by, required_by, safety_by, installed_ids = _installed_model_flags(app_id)
        installed: list[ManageRow] = []
        available: list[ManageRow] = []
        shared_rows: list[ManageRow] = []
        own_rows: list[ManageRow] = []
        for index, row in enumerate(rows):
            title = row.label or localized_name(row.names, row.fallback, code)
            bits = [format_size(row.size, lang=code)]
            if row.tier_gb:
                bits.append(_say("mdl.manage.min_vram", code, gb=row.tier_gb))
            meta = " · ".join(bits)
            key = row.share_key
            others = [identity(name).display_name for name in row.refs if name != app_id]
            shared = len(row.refs) > 1 or any(name != app_id for name in listed_by.get(key, ()))
            group = normalize_group(row.group)
            badge = _TIER_BADGES.get(row.tier, "")
            removable = can_remove(group)
            note = ""
            state = "pending"
            if row.kind == "needs_preparing":
                state = "needs_preparing"
            elif row.installed and row.verified:
                state = "ready"
            elif row.installed:
                state = "error"
            if shared:
                removable = removable and key not in required_by
                users = _ordered_app_names(
                    (set(row.refs) & installed_ids) | set(listed_by.get(key, ()))
                )
                notes = [_say("mdl.manage.used_by", code, apps=", ".join(users))]
                if key in safety_by:
                    notes.append(_say("mdl.manage.required.safety", code))
                elif key in required_by:
                    names = _ordered_app_names(set(required_by[key]))
                    notes.append(_say("mdl.manage.required.by", code, apps=", ".join(names)))
                elif removable and state == "ready":
                    notes.append(_say("mdl.manage.shared_remove", code))
                note = "\n".join(notes)
            elif others:
                note = _say("mdl.manage.shared", code, apps=", ".join(others))
            damaged = state == "error"
            missing = state == "pending"
            if shared and key in safety_by:
                # A shared safety copy fails closed: repair, never Remove.
                removable = False
                if missing:
                    state_label = _say("mdl.manage.status.required_missing", code)
                    action, action_label = "repair", _say("mdl.manage.repair", code)
                elif damaged:
                    state_label = _say("mdl.manage.status.required_repair", code)
                    action, action_label = "repair", _say("mdl.manage.repair", code)
                else:
                    state_label = _say("mdl.manage.status.ok", code)
                    action, action_label = "", ""
            elif state == "needs_preparing":
                state_label = _say("mdl.manage.status.needs_preparing", code)
                action, action_label = "", ""
            elif damaged:
                state_label = _say("mdl.manage.status.bad", code)
                action, action_label = "repair", _say("mdl.manage.repair", code)
            elif missing:
                state_label = _say("mdl.manage.status.missing", code)
                action, action_label = "add", _say("mdl.manage.add", code)
            else:
                state_label = _say("mdl.manage.status.ok", code)
                if removable:
                    action, action_label = "remove", _say("mdl.manage.remove", code)
                else:
                    action, action_label = "", ""
            built = ManageRow(
                keys[index],
                title,
                meta,
                state,
                state_label,
                action,
                action_label,
                note,
                purpose=row.purpose,
                group=group,
                badge=badge,
                removable=removable,
                model_id=row.model_id,
                component_id=row.component_id,
            )
            # Every model from this app's manifest stays on This app, even
            # when it is also shared, so the tab is never an empty gap.
            own_rows.append(built)
            if shared:
                shared_rows.append(built)
            if built.state == "pending":
                available.append(built)
            else:
                installed.append(built)
        folder = models_dir()
        used = _used_bytes()
        free = shutil.disk_usage(folder).free if folder.exists() else 0
        name = identity(app_id).display_name
        tier = detect_tier(gpus)
        groups = group_manage_rows((*installed, *available), tier=tier, lang=code)
        tabs: list[ManageTab] = []
        if own_rows:
            tabs.append(
                ManageTab(
                    "this_app",
                    _say("mdl.manage.tab.this_app.count", code, count=len(own_rows)),
                    group_manage_rows(own_rows, tier=tier, lang=code),
                    len(own_rows),
                )
            )
        if shared_rows:
            tabs.append(
                ManageTab(
                    "shared",
                    _say("mdl.manage.tab.shared.count", code, count=len(shared_rows)),
                    group_manage_rows(shared_rows, tier=tier, lang=code),
                    len(shared_rows),
                )
            )
        return ManageView(
            tuple(installed),
            tuple(available),
            _say(
                "mdl.manage.disk",
                code,
                app=name,
                used=format_size(used, lang=code),
                free=format_size(free, lang=code),
                path=display_path(folder),
            ),
            _say("mdl.manage.empty_installed", code, app=name),
            _say("mdl.manage.empty_available", code),
            groups,
            tuple(tabs),
        )

    def remove_model(self, app_id: str, component_id: str, *, group: str | None = None) -> bool:
        """Drop this app's ref. A folder is removed only when the release checks pass.

        A ``group`` in the protected set (``safety`` or ``internal``) is
        refused even when the caller names the model directly. Without
        ``group`` the check is skipped, which keeps old callers working.
        """
        if group is not None and not can_remove(group):
            return False
        deleted = release_component(component_id, app_id).deleted
        if deleted:
            remove_outputs(app_id, component_id)
        return deleted

    def verify_app(self, app_id: str, manifest: Path) -> None:
        """Re-hash the planned files for one manifest."""
        del app_id
        verify([manifest])

    def _gpus(self, devices: list[GpuDevice] | None) -> list[GpuDevice]:
        """Return ``devices``, or the process's GPUs probed once."""
        if devices is not None:
            return list(devices)
        if self._gpus_cache is None:
            self._gpus_cache = list(query_gpus())
        return self._gpus_cache

    def _remember(self, event: ProgressEvent) -> None:
        self._event = event


def _visible(
    manifest: AppManifest,
    devices: list[GpuDevice] | None,
    *,
    all_tiers: bool = False,
) -> list[ManagedFile]:
    tier = detect_tier(devices)
    cap = _GPU_RANK[tier]
    document = load()
    found: list[ManagedFile] = []
    for model in manifest.models:
        if model.unavailable or (not all_tiers and _TIER_RANK[model.tier] > cap):
            continue
        digest = model.files[0].sha256 if model.files else ""
        component_id = f"model-{digest[:MODEL_PREFIX]}" if digest else ""
        component = document.components.get(component_id)
        refs = tuple(component.refs) if component is not None else ()
        expected = sum(item.size for item in model.files)
        kind = _model_kind(
            model,
            component_id,
            component,
            siblings=manifest.models,
            app=manifest.app,
        )
        names = dict(model.names)
        if model.display_name and "en" not in names:
            names["en"] = model.display_name
        found.append(
            ManagedFile(
                model.id,
                names,
                model.display_name or model.id,
                expected,
                _tier_gb(model),
                model.optional or model.on_demand,
                digest,
                kind != "missing",
                refs,
                component_id,
                kind == "ok",
                label=model.label,
                purpose=model.purpose,
                group=model.group,
                tier=model.tier,
                share_key=model_share_key(model),
                kind=kind,
            )
        )
    return found


def _row_keys(rows: list[ManagedFile]) -> list[str]:
    """Return one unique row key per entry, in ``rows`` order.

    The key is the raw model id while ids are unique in the manifest. When an
    id repeats (Movie Dubber lists three ``qwen3-tts`` rows), the key carries
    the share key, tier, and label so rows and their actions do not collide;
    a identical descriptor listed twice still gets a numeric suffix.
    """
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.model_id] = counts.get(row.model_id, 0) + 1
    seen: dict[str, int] = {}
    keys: list[str] = []
    for row in rows:
        if counts[row.model_id] == 1:
            keys.append(row.model_id)
            continue
        stem = "|".join((row.model_id, row.share_key, row.tier.value, row.label))
        number = seen.get(stem, 0)
        seen[stem] = number + 1
        keys.append(stem if number == 0 else f"{stem}#{number + 1}")
    return keys


def _installed_model_flags(
    app_id: str,
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[str]], set[str]]:
    """Scan installed app manifests for share-key usage.

    Returns one map from share key to the installed apps that list it, one
    for the apps that mark it required (not ``optional`` and not
    ``on_demand``, or normalising to ``group=safety``), one for the apps
    that mark it ``safety``, and the set of installed app ids. A manifest
    that cannot be read is skipped. ``app_id`` is only used for logging; the
    maps cover every installed app, including the caller.
    """
    del app_id
    listed_by: dict[str, list[str]] = {}
    required_by: dict[str, list[str]] = {}
    safety_by: dict[str, list[str]] = {}
    installed_ids: set[str] = set()
    try:
        installed = discover()
    except CatalogError:
        logger.exception("installed app discovery failed")
        installed = []
    for found in installed:
        installed_ids.add(found.id)
        try:
            manifest = load_manifest(found.path)
        except CatalogError:
            logger.warning("installed app %s manifest could not be read", found.id)
            continue
        for model in manifest.models:
            key = model_share_key(model)
            if found.id not in listed_by.setdefault(key, []):
                listed_by[key].append(found.id)
            safety = normalize_group(model.group) == "safety"
            if safety and found.id not in safety_by.setdefault(key, []):
                safety_by[key].append(found.id)
            if (safety or (not model.optional and not model.on_demand)) and (
                found.id not in required_by.setdefault(key, [])
            ):
                required_by[key].append(found.id)
    return listed_by, required_by, safety_by, installed_ids


def _ordered_app_names(app_ids: set[str] | list[str]) -> list[str]:
    """Return display names in stable :data:`SUITE_APPS` order."""
    order = {spec.id: index for index, spec in enumerate(SUITE_APPS)}

    def rank(app_id: str) -> tuple[int, str]:
        return (order.get(app_id, len(order)), app_id)

    return [identity(app_id).display_name for app_id in sorted(app_ids, key=rank)]


def _model_kind(
    model: Model,
    component_id: str,
    component: object | None,
    *,
    siblings: Sequence[Model] = (),
    app: str = "",
) -> str:
    """Classify one manifest row for the manage page.

    ``ok`` means every planned file is a verified blob and the app view
    resolves (the view is created when the blobs are already in the shared
    store). ``needs_preparing`` is the same, with the app's derived files
    still missing. ``missing`` is a model that was never installed: no
    unique files, no stub view, no leftover partials — even when another
    model already has a shared blob this row also names. ``damaged`` is a
    stub or partial view, or a unique file that was started but is
    incomplete. A sibling or fallback with the same share key whose blobs
    are already verified counts as ``ok``, so a 16 GB Turbo row resolves
    to the full Turbo already on disk without a download.
    """
    from voxprint_common.mdl.store import StoreFile, blob_ready
    from voxprint_common.mdl.views import inspect_view, repair_view
    from voxprint_common.paths import derived_dir, models_dir

    root = models_dir()
    files = {item.path: item.sha256 for item in model.files}
    sizes = {item.path: item.size for item in model.files}

    def _view() -> str:
        if not (app and files):
            return "absent"
        try:
            return inspect_view(root, model.id, files, app, sizes=sizes)
        except OSError:
            return "absent"

    def _ready(item: ModelFile) -> bool:
        try:
            return blob_ready(root, StoreFile(item.path, item.sha256, item.size))
        except OSError:
            return False

    def _present(item: ModelFile) -> bool:
        """Return whether the blob is usable or a size-matched file to adopt."""
        if _ready(item):
            return True
        blob = root / "blobs" / item.sha256 if item.sha256 else None
        record = component if len(model.files) == 1 else None
        stored = stored_stat(blob, record) if blob is not None else None
        kind = classify_file(blob, item.size, stored)
        if kind == "adopt" and blob is not None:
            remember_stat(blob, component_id if len(model.files) == 1 else "")
            return True
        return kind == "ok"

    def _partial(item: ModelFile) -> bool:
        blob = root / "blobs" / item.sha256.lower()
        return (
            blob.with_name(blob.name + ".partial").is_file()
            or blob.with_name(blob.name + ".partial.parts").is_dir()
        )

    all_ready = bool(model.files) and all(_ready(item) for item in model.files)
    all_present = bool(model.files) and all(_present(item) for item in model.files)
    unique = [
        item
        for item in model.files
        if not any(
            other is not model and any(f.sha256 == item.sha256 for f in other.files)
            for other in siblings
        )
    ]
    unique_ready = all(_present(item) for item in unique) if unique else False
    unique_partial = any(_partial(item) for item in unique)
    any_unique_ready = any(_present(item) for item in unique) if unique else False

    def _derived_missing() -> bool:
        if model.post_install is None or not model.files:
            return False
        folder = derived_dir(app or "shared", model.id, model.files[0].sha256, root=root)
        try:
            return not folder.is_dir() or not any(folder.iterdir())
        except OSError:
            return True

    def _finish_ok() -> str:
        if app and files and all_ready:
            with contextlib.suppress(OSError):
                repair_view(root, model.id, files, app, sizes=sizes)
        if _derived_missing():
            return "needs_preparing"
        return "ok"

    if all_ready or all_present:
        return _finish_ok()
    view = _view()
    if view == "ok":
        return _finish_ok()
    if view == "stub":
        if all_ready:
            return _finish_ok()
        return "damaged"

    # Resolve to an already-installed sibling or fallback (16 GB Turbo).
    share = model_share_key(model)
    for other in siblings:
        if other.id == model.id or not other.files:
            continue
        if model_share_key(other) == share and all(_present(item) for item in other.files):
            return "ok"
    for variant in model.fallbacks:
        if variant.files and all(_present(item) for item in variant.files):
            return "ok"

    if unique_partial or (any_unique_ready and not unique_ready):
        return "damaged"
    return "missing"


def _tier_gb(model: Model) -> int:
    """Return the minimum-VRAM hint for the meta line; badges use ``model.tier``."""
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
    """Return a card only for an app that has a real queued or active job.

    Sharing a file with another app does not add a "waiting" card. When the
    status file has no job list (legacy), apps on unfinished items are kept
    so a running download still has a card.
    """
    ordered: list[str] = []
    for app_id in job_apps:
        if app_id and app_id not in ordered:
            ordered.append(app_id)
    if not ordered:
        for item in items:
            if item.state is ItemState.READY:
                continue
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
