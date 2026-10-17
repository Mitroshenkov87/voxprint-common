"""Plan, download, and verify a shared model set.

The download holds :func:`voxprint_common.power.keep_awake` and lowers CPU and
IO priority. Disk space is checked before the first request: remaining bytes
times 1.10, plus 2 GiB. Finished files are recorded in the shared runtime
manifest with one ref per app.
"""

from __future__ import annotations

import threading
import time
import urllib.parse
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from voxprint_common.cli_protocol import EXIT_CANCELLED, EXIT_FAILED, EXIT_OK
from voxprint_common.gpu_probe import GpuDevice, VramTier, query_gpus
from voxprint_common.hashing import HashMismatch
from voxprint_common.hashing import verify as verify_hash
from voxprint_common.mdl.catalog import (
    MODEL_PREFIX,
    AppManifest,
    CatalogError,
    PlannedFile,
    discover,
    load_manifest,
    plan_files,
    require_space,
)
from voxprint_common.mdl.events import (
    ItemProgress,
    JobProgress,
    ProgressEvent,
    TransferSession,
)
from voxprint_common.mdl.fetch import Cancelled, FetchError, FetchProgress, fetch_file
from voxprint_common.mdl.hooks import HookError, apply_hooks
from voxprint_common.mdl.names import DISPLAY_NAME
from voxprint_common.mdl.priority import background_priority
from voxprint_common.mdl.safety import UrlGuard
from voxprint_common.mdl.segments import ConnectionBudget, segment_count
from voxprint_common.mdl.status import JobState, Status, Step, load_status, save_status
from voxprint_common.paths import models_dir
from voxprint_common.power import keep_awake
from voxprint_common.runtime_manifest import ManifestError, add_ref, load
from voxprint_common.suite_settings import mdl_parallel_files

__all__ = ["Outcome", "detect_tier", "download", "plan", "verify"]

ProgressFn = Callable[[float | None, str], None]
StatusFn = Callable[[Status], None]
DiskFree = Callable[[Path], int]


@dataclass
class Outcome:
    """The exit code, a short message, and the status that was published."""

    code: int
    message: str
    status: Status


def detect_tier(devices: Sequence[GpuDevice] | None = None) -> VramTier:
    """Return the VRAM tier of the largest GPU.

    Args:
        devices: Already probed GPUs. When omitted, nvidia-smi is queried.

    An empty result is :attr:`VramTier.UNKNOWN`, which the plan treats as 8 GB.
    The 24 GB boundary is 23.5 GiB, from :func:`voxprint_common.gpu_probe.tier_for_gib`.
    """
    found = list(query_gpus() if devices is None else devices)
    if not found:
        return VramTier.UNKNOWN
    best = max(found, key=lambda device: device.memory_gib or 0.0)
    return best.tier


def plan(
    manifests: Sequence[Path] | None = None,
    *,
    devices: Sequence[GpuDevice] | None = None,
    disk_free: DiskFree | None = None,
    on_status: StatusFn | None = None,
    only: Collection[str] | None = None,
) -> Outcome:
    """Build the plan, check disk space, and publish a status file.

    Nothing is downloaded. A plan whose files are already present and the
    right size is marked ready.

    Args:
        manifests: ``mdl.json`` paths. When omitted, installed apps are discovered.
        devices: GPUs to plan for. When omitted, nvidia-smi is queried.
        disk_free: Replacement for the free-space check.
        on_status: Called with each published snapshot.
        only: Model ids to include. ``None`` plans the tier.
    """
    try:
        parsed, tier, cards = _inputs(manifests, devices)
        memory_gib, compute = _card(cards)
        files = plan_files(parsed, tier, memory_gib=memory_gib, compute=compute, only=only)
        status = _status_for(files, tier, JobState.PLANNED)
        _note_local(status, files)
        require_space(files, disk_free=disk_free, root=models_dir())
    except (CatalogError, OSError) as exc:
        return _fail(str(exc), on_status, tier="")
    if files and all(step.state == "verified" for step in status.steps):
        status.state = JobState.READY
        status.error = ""
    return _publish(status, EXIT_OK, _plan_message(status), on_status)


def download(
    manifests: Sequence[Path] | None = None,
    *,
    dry_run: bool = False,
    devices: Sequence[GpuDevice] | None = None,
    disk_free: DiskFree | None = None,
    opener: object | None = None,
    cancel: threading.Event | None = None,
    priority: bool = True,
    display: bool = False,
    on_status: StatusFn | None = None,
    on_progress: ProgressFn | None = None,
    fetch_options: Mapping[str, object] | None = None,
    session: TransferSession | None = None,
    guard: UrlGuard | None = None,
    workers: int = 1,
    only: Collection[str] | None = None,
    planned: Sequence[PlannedFile] | None = None,
    queue_jobs: Sequence[Mapping[str, object]] | None = None,
) -> Outcome:
    """Download the plan into the shared models directory.

    Args:
        manifests: ``mdl.json`` paths. When omitted, installed apps are discovered.
        dry_run: Check the plan and the disk, then return without downloading.
        devices: GPUs to plan for.
        disk_free: Replacement for the free-space check.
        opener: Passed to :func:`voxprint_common.mdl.fetch.fetch_file`.
        cancel: Set to stop the job.
        priority: Lower CPU and IO priority while bytes are moving.
        display: Also keep the display awake. The progress window sets this.
        on_status: Called with each published snapshot.
        on_progress: ``(fraction, message)`` for a ``--json`` session.
        fetch_options: Extra keyword arguments for
            :func:`~voxprint_common.mdl.fetch.fetch_file`.
        session: Progress subscription and pause/resume/cancel hooks.
        guard: Download URL rules. Defaults to the production allowlist.
        workers: How many files to fetch at once. ``1`` keeps the original
            order without threads.
        only: Model ids to include. ``None`` plans the tier and skips
            on-demand and optional models.
        planned: Files already chosen by the queue. When set, manifests are
            not read again.
        queue_jobs: Queue rows copied onto the status file for the window.

    Returns:
        An :class:`Outcome`. The status file is updated either way.
    """
    try:
        if planned is not None:
            cards = list(devices) if devices is not None else list(query_gpus())
            tier = detect_tier(cards)
            files = list(planned)
        else:
            parsed, tier, cards = _inputs(manifests, devices)
            memory_gib, compute = _card(cards)
            files = plan_files(parsed, tier, memory_gib=memory_gib, compute=compute, only=only)
        status = _status_for(files, tier, JobState.DRY_RUN if dry_run else JobState.DOWNLOADING)
        if queue_jobs is not None:
            status.jobs = [dict(row) for row in queue_jobs]
        _append_unavailable(status, queue_jobs)
        _note_local(status, files)
        require_space(files, disk_free=disk_free, root=models_dir())
    except Cancelled:
        return _fail("cancelled", on_status, state=JobState.CANCELLED, code=EXIT_CANCELLED)
    except (CatalogError, OSError) as exc:
        return _fail(str(exc), on_status)
    if dry_run:
        if files and all(step.state == "verified" for step in status.steps):
            status.state = JobState.READY
        return _publish(status, EXIT_OK, _plan_message(status), on_status, on_progress, session)
    if _cancelled(cancel) or (session is not None and session.cancelled):
        status.state = JobState.CANCELLED
        status.error = "cancelled"
        return _publish(status, EXIT_CANCELLED, "cancelled", on_status, on_progress, session)
    if workers < 1:
        return _fail("workers must be at least 1", on_status, tier=tier.value)
    options = dict(fetch_options or {})
    if opener is not None:
        options["opener"] = opener
    if guard is not None:
        options["guard"] = guard
    if session is not None:
        options["control"] = session
    publisher = _Publisher(status, on_status, on_progress, session)
    publisher.push(force=True)
    try:
        with ExitStack() as stack:
            if priority:
                stack.enter_context(background_priority())
            stack.enter_context(keep_awake(DISPLAY_NAME, display=display))
            _fetch_all(
                files,
                status,
                publisher,
                options,
                cancel,
                session,
                workers,
            )
            apply_hooks(files)
    except HookError as exc:
        _mark_hook(status, exc)
        return _publish(status, EXIT_FAILED, str(exc), on_status, on_progress, session)
    except Cancelled:
        status.state = JobState.CANCELLED
        status.error = "cancelled"
        return _publish(status, EXIT_CANCELLED, "cancelled", on_status, on_progress, session)
    except (FetchError, CatalogError, ManifestError, OSError) as exc:
        status.state = JobState.FAILED
        status.error = str(exc)
        for step in status.steps:
            if step.state in {"downloading", "paused"}:
                step.state = "failed"
        return _publish(status, EXIT_FAILED, str(exc), on_status, on_progress, session)
    status.state = JobState.READY
    status.error = ""
    status.recompute()
    return _publish(status, EXIT_OK, _ready_message(status), on_status, on_progress, session)


def verify(
    manifests: Sequence[Path] | None = None,
    *,
    devices: Sequence[GpuDevice] | None = None,
    on_status: StatusFn | None = None,
    only: Collection[str] | None = None,
) -> Outcome:
    """Re-hash the planned files and publish whether they are ready.

    Args:
        manifests: ``mdl.json`` paths. When omitted, installed apps are discovered.
        devices: GPUs to plan for.
        on_status: Called with the published snapshot.
        only: Model ids to include. ``None`` plans the tier.
    """
    try:
        parsed, tier, cards = _inputs(manifests, devices)
        memory_gib, compute = _card(cards)
        files = plan_files(parsed, tier, memory_gib=memory_gib, compute=compute, only=only)
    except (CatalogError, OSError) as exc:
        return _fail(str(exc), on_status)
    status = _status_for(files, tier, JobState.READY)
    mismatches: list[str] = []
    for item, step in zip(files, status.steps, strict=True):
        if _hash_ok(item):
            step.state = "verified"
            step.received = item.size
            continue
        step.state = "failed"
        step.received = 0
        mismatches.append(item.label)
    if mismatches:
        status.state = JobState.FAILED
        status.error = "sha256 mismatch: " + ", ".join(mismatches)
        return _publish(status, EXIT_FAILED, status.error, on_status)
    try:
        apply_hooks(files, deep=True)
    except HookError as exc:
        _mark_hook(status, exc)
        return _publish(status, EXIT_FAILED, str(exc), on_status)
    status.error = ""
    return _publish(status, EXIT_OK, _ready_message(status), on_status)


def _fetch_all(
    files: Sequence[PlannedFile],
    status: Status,
    publisher: _Publisher,
    options: dict[str, object],
    cancel: threading.Event | None,
    session: TransferSession | None,
    workers: int,
) -> None:
    stop = threading.Event()
    extras = [stop]
    if cancel is not None:
        extras.append(cancel)
    view: threading.Event | _OrFlag
    if session is not None:
        view = session.bind_cancel(extras)  # type: ignore[assignment]
    elif cancel is not None:
        view = _OrFlag(cancel, stop)
    else:
        view = stop
    errors: list[BaseException] = []
    lock = threading.Lock()
    parallel = mdl_parallel_files()
    if "budget" not in options:
        total = min(8, max(4, parallel * 4))
        options["budget"] = ConnectionBudget(total)

    def one(index: int, item: PlannedFile) -> None:
        if view.is_set():
            return
        step = status.steps[index]
        with lock:
            if step.state == "verified" and _hash_ok(item):
                present = True
            else:
                present = False
                if item.destination.exists() and not _hash_ok(item):
                    item.destination.unlink()
                step.state = "paused" if session is not None and session.paused else "downloading"
                publisher.push(force=True)
        if present:
            _remember(item)
            return

        def _on_fetch(
            notice: FetchProgress,
            current: Step = step,
            planned: PlannedFile = item,
        ) -> None:
            with lock:
                if session is not None and session.paused:
                    current.state = "paused"
                elif current.state != "failed":
                    current.state = "downloading"
                current.source = notice.source or _host(planned.urls[0] if planned.urls else "")
                current.received = notice.received
                current.speed_bps = notice.bps
                remaining = max(0, current.size - notice.received)
                current.eta_s = remaining / notice.bps if notice.bps > 0 else None
                status.recompute()
                publisher.push()

        try:
            extra = dict(options)
            concurrent = 1 if workers == 1 else min(workers, max(1, len(files)))
            extra.setdefault("segments", segment_count(item.size, concurrent, parallel))
            extra.setdefault("mirror_policy", item.mirror_policy)
            fetch_file(
                item.destination,
                item.urls,
                sha256=item.sha256,
                size=item.size,
                progress=_on_fetch,
                cancel=view,  # type: ignore[arg-type]
                **extra,
            )
        except Cancelled as exc:
            stop.set()
            errors.append(exc)
            return
        except (FetchError, CatalogError, ManifestError, OSError) as exc:
            stop.set()
            with lock:
                step.state = "failed"
                publisher.push(force=True)
            errors.append(exc)
            return
        _remember(item)
        with lock:
            step.state = "verified"
            step.received = item.size
            step.eta_s = 0
            status.recompute()
            publisher.push(force=True)

    if workers == 1:
        for index, item in enumerate(files):
            one(index, item)
            if errors:
                raise errors[0]
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, index, item) for index, item in enumerate(files)]
        for future in futures:
            future.result()
    if errors:
        for exc in errors:
            if isinstance(exc, Cancelled):
                raise exc
        raise errors[0]


def _inputs(
    manifests: Sequence[Path] | None,
    devices: Sequence[GpuDevice] | None,
) -> tuple[list[AppManifest], VramTier, list[GpuDevice]]:
    paths = (
        [Path(item) for item in manifests]
        if manifests is not None
        else [app.path for app in discover()]
    )
    if not paths:
        raise CatalogError(
            "no installed app manifests. Pass --manifest, or install an app that ships mdl.json."
        )
    parsed = [load_manifest(path) for path in paths]
    cards = list(query_gpus() if devices is None else devices)
    return parsed, detect_tier(cards), cards


def _status_for(files: Sequence[PlannedFile], tier: VramTier, state: JobState) -> Status:
    status = Status(state=state, tier=tier.value)
    status.steps = [
        Step(
            model=item.label,
            size=item.size,
            source="",
            speed_bps=0.0,
            eta_s=None,
            state="pending",
            received=0,
            sha256=item.sha256,
            apps=list(item.apps),
            item_id=item.model_id or (item.model_ids[0] if item.model_ids else ""),
            display_name=item.display_name or item.label,
        )
        for item in files
    ]
    status.recompute()
    return status


def _note_local(status: Status, files: Sequence[PlannedFile]) -> None:
    for item, step in zip(files, status.steps, strict=True):
        if not item.destination.is_file():
            step.state = "needs download"
            step.received = 0
            step.source = "local"
            continue
        have = item.destination.stat().st_size
        if have != item.size:
            step.state = "needs repair"
            step.received = have
            step.source = "local"
            continue
        step.state = "verified"
        step.received = item.size
        step.source = "local"
    status.recompute()


def _hash_ok(item: PlannedFile) -> bool:
    if not item.destination.is_file():
        return False
    try:
        verify_hash(item.destination, item.sha256)
    except HashMismatch, OSError, ValueError:
        return False
    return True


def _remember(item: PlannedFile) -> None:
    document = load()
    preferred = f"model-{item.sha256[:MODEL_PREFIX]}"
    component_id = preferred
    current = document.components.get(preferred)
    if current is not None and current.sha256 not in {"", item.sha256}:
        component_id = ""
        for key, component in document.components.items():
            if key.startswith("model-") and component.sha256 == item.sha256:
                component_id = key
                break
        if not component_id:
            raise ManifestError(f"component {preferred} already records a different sha256")
    elif current is None:
        for key, component in document.components.items():
            if key.startswith("model-") and component.sha256 == item.sha256:
                component_id = key
                break
    for app in item.apps:
        add_ref(
            component_id,
            app,
            kind="model",
            version=item.version,
            path=str(item.destination.resolve()),
            sha256=item.sha256,
        )


def _append_unavailable(status: Status, queue_jobs: Sequence[Mapping[str, object]] | None) -> None:
    if not queue_jobs:
        return
    present = {step.item_id for step in status.steps}
    for row in queue_jobs:
        rows = row.get("unavailable") or []
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "")
            if not model_id or model_id in present:
                continue
            present.add(model_id)
            reason = str(item.get("reason") or "unavailable")
            name = str(item.get("display_name") or model_id)
            status.steps.append(
                Step(
                    model=name,
                    size=0,
                    source=reason,
                    speed_bps=0.0,
                    eta_s=None,
                    state="unavailable",
                    received=0,
                    sha256="",
                    apps=[str(row.get("app") or "")],
                    item_id=model_id,
                    display_name=name,
                )
            )
    status.recompute()


def _mark_hook(status: Status, exc: HookError) -> None:
    for step in status.steps:
        matched = (exc.model_id and step.item_id == exc.model_id) or (
            not exc.model_id and exc.app in step.apps
        )
        if matched:
            step.state = "error"
    status.state = JobState.FAILED
    status.error = str(exc)
    status.recompute()


def _cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()


def _fail(
    message: str,
    on_status: StatusFn | None,
    *,
    tier: str = "",
    state: JobState = JobState.FAILED,
    code: int = EXIT_FAILED,
) -> Outcome:
    previous = _safe_load()
    status = Status(
        state=state,
        tier=tier or previous.tier,
        error=message,
        steps=previous.steps,
        jobs=list(previous.jobs),
        extra=previous.extra,
    )
    status.recompute()
    return _publish(status, code, message, on_status)


def _safe_load() -> Status:
    try:
        return load_status()
    except OSError, ValueError:
        return Status(JobState.IDLE)


def _publish(
    status: Status,
    code: int,
    message: str,
    on_status: StatusFn | None,
    on_progress: ProgressFn | None = None,
    session: TransferSession | None = None,
) -> Outcome:
    status.recompute()
    status.touch()
    save_status(status)
    if session is not None:
        session.publish(progress_event(status))
    if on_status is not None:
        on_status(status)
    if on_progress is not None:
        fraction = None
        if status.bytes_total:
            fraction = min(1.0, status.bytes_done / status.bytes_total)
        on_progress(fraction, message)
    return Outcome(code, message, status)


def _plan_message(status: Status) -> str:
    if status.ready:
        return "ready"
    gaps = [step for step in status.steps if step.state in {"needs download", "needs repair"}]
    if gaps:
        return "; ".join(f"{step.state}: {step.model}" for step in gaps)
    if not status.steps:
        return "nothing to download"
    return f"planned {len(status.steps)} file(s), {status.bytes_total} bytes"


def _ready_message(status: Status) -> str:
    if not status.steps:
        return "nothing to download"
    return "ready"


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc


def _card(devices: Sequence[GpuDevice]) -> tuple[float | None, tuple[int, int] | None]:
    if not devices:
        return None, None
    best = max(devices, key=lambda device: device.memory_gib or 0.0)
    return best.memory_gib, best.compute


def progress_event(status: Status) -> ProgressEvent:
    """Build the snapshot a window subscribes to, including queued jobs."""
    items = tuple(
        ItemProgress(
            id=step.item_id or step.sha256[:MODEL_PREFIX],
            display_name=step.display_name or step.model,
            bytes_done=step.received,
            bytes_total=step.size,
            speed_bps=step.speed_bps,
            eta_s=step.eta_s,
            source=step.source,
            state=step.state,
            apps=tuple(step.apps),
            reason=step.source if step.state == "unavailable" else "",
        )
        for step in status.steps
    )
    jobs = tuple(_job_progress(row) for row in status.jobs)
    state = status.state.value if isinstance(status.state, JobState) else str(status.state)
    return ProgressEvent(items, jobs, status.bytes_done, status.bytes_total, state)


def _job_progress(row: Mapping[str, object]) -> JobProgress:
    models = row.get("models")
    listed: tuple[str, ...] | None
    if models is None:
        listed = None
    elif isinstance(models, list):
        listed = tuple(str(item) for item in models)
    else:
        listed = None
    return JobProgress(
        str(row.get("id") or ""),
        str(row.get("app") or ""),
        listed,
        str(row.get("state") or ""),
    )


class _OrFlag:
    """True when any linked cancel event is set."""

    def __init__(self, *flags: threading.Event) -> None:
        self._flags = flags

    def is_set(self) -> bool:
        """Return whether one of the flags is set."""
        return any(flag.is_set() for flag in self._flags)


class _Publisher:
    """Write the status file at a steady pace while bytes arrive."""

    def __init__(
        self,
        status: Status,
        on_status: StatusFn | None,
        on_progress: ProgressFn | None,
        session: TransferSession | None = None,
    ) -> None:
        """Bind the snapshot and the optional listeners.

        Args:
            status: Mutable snapshot for this job.
            on_status: Called when the file is written.
            on_progress: Called with the overall fraction.
            session: Receives a :class:`ProgressEvent` on each publish.
        """
        self.status = status
        self.on_status = on_status
        self.on_progress = on_progress
        self.session = session
        self._last = 0.0
        self._lock = threading.Lock()

    def push(self, *, force: bool = False) -> None:
        """Publish when ``force`` is set or 250 ms have passed."""
        with self._lock:
            moment = time.monotonic()
            if not force and moment - self._last < 0.25:
                return
            self._last = moment
            self.status.touch()
            save_status(self.status)
            event = progress_event(self.status)
            listeners = (self.on_status, self.on_progress, self.session)
        if listeners[0] is not None:
            listeners[0](self.status)
        if listeners[1] is not None and self.status.bytes_total:
            fraction = min(1.0, self.status.bytes_done / self.status.bytes_total)
            listeners[1](fraction, self.status.state.value)
        if listeners[2] is not None:
            listeners[2].publish(event)
