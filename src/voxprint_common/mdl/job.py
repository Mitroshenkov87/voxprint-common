"""Plan, download, and verify a shared model set.

The download holds :func:`voxprint_common.power.keep_awake` and lowers CPU and
IO priority. Disk space is checked, with 15 percent headroom, before the first
request. Finished files are recorded in the shared runtime manifest with one
ref per app.
"""

from __future__ import annotations

import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from voxprint_common.cli_protocol import EXIT_CANCELLED, EXIT_FAILED, EXIT_OK
from voxprint_common.gpu_probe import GpuDevice, VramTier, query_gpus
from voxprint_common.hashing import HashMismatch
from voxprint_common.hashing import verify as verify_hash
from voxprint_common.mdl.catalog import (
    AppManifest,
    CatalogError,
    PlannedFile,
    discover,
    load_manifest,
    plan_files,
    require_space,
)
from voxprint_common.mdl.fetch import Cancelled, FetchError, FetchProgress, fetch_file
from voxprint_common.mdl.names import DISPLAY_NAME
from voxprint_common.mdl.priority import background_priority
from voxprint_common.mdl.status import JobState, Status, Step, load_status, save_status
from voxprint_common.paths import models_dir
from voxprint_common.power import keep_awake
from voxprint_common.runtime_manifest import ManifestError, add_ref, load

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
) -> Outcome:
    """Build the plan, check disk space, and publish a status file.

    Nothing is downloaded. A plan whose files are already present and the
    right size is marked ready.

    Args:
        manifests: ``mdl.json`` paths. When omitted, installed apps are discovered.
        devices: GPUs to plan for. When omitted, nvidia-smi is queried.
        disk_free: Replacement for the free-space check.
        on_status: Called with each published snapshot.
    """
    try:
        parsed, tier = _inputs(manifests, devices)
        files = plan_files(parsed, tier)
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

    Returns:
        An :class:`Outcome`. The status file is updated either way.
    """
    try:
        parsed, tier = _inputs(manifests, devices)
        files = plan_files(parsed, tier)
        status = _status_for(files, tier, JobState.DRY_RUN if dry_run else JobState.DOWNLOADING)
        _note_local(status, files)
        require_space(files, disk_free=disk_free, root=models_dir())
    except Cancelled:
        return _fail("cancelled", on_status, state=JobState.CANCELLED, code=EXIT_CANCELLED)
    except (CatalogError, OSError) as exc:
        return _fail(str(exc), on_status)
    if dry_run:
        if files and all(step.state == "verified" for step in status.steps):
            status.state = JobState.READY
        return _publish(status, EXIT_OK, _plan_message(status), on_status, on_progress)
    if _cancelled(cancel):
        status.state = JobState.CANCELLED
        status.error = "cancelled"
        return _publish(status, EXIT_CANCELLED, "cancelled", on_status, on_progress)
    options = dict(fetch_options or {})
    if opener is not None:
        options["opener"] = opener
    publisher = _Publisher(status, on_status, on_progress)
    publisher.push(force=True)
    try:
        with ExitStack() as stack:
            if priority:
                stack.enter_context(background_priority())
            stack.enter_context(keep_awake(DISPLAY_NAME, display=display))
            for index, item in enumerate(files):
                if _cancelled(cancel):
                    raise Cancelled("cancelled")
                step = status.steps[index]
                if step.state == "verified" and _hash_ok(item):
                    _remember(item)
                    continue
                if item.destination.exists():
                    item.destination.unlink()
                step.state = "downloading"

                def _on_fetch(
                    notice: FetchProgress,
                    current: Step = step,
                    planned: PlannedFile = item,
                ) -> None:
                    current.source = notice.source or _host(planned.urls[0] if planned.urls else "")
                    current.received = notice.received
                    current.speed_bps = notice.bps
                    remaining = max(0, current.size - notice.received)
                    current.eta_s = remaining / notice.bps if notice.bps > 0 else None
                    status.recompute()
                    publisher.push()

                fetch_file(
                    item.destination,
                    item.urls,
                    sha256=item.sha256,
                    size=item.size,
                    progress=_on_fetch,
                    cancel=cancel,
                    **options,
                )
                _remember(item)
                step.state = "verified"
                step.received = item.size
                step.eta_s = 0
                status.recompute()
                publisher.push(force=True)
    except Cancelled:
        status.state = JobState.CANCELLED
        status.error = "cancelled"
        return _publish(status, EXIT_CANCELLED, "cancelled", on_status, on_progress)
    except (FetchError, CatalogError, ManifestError, OSError) as exc:
        status.state = JobState.FAILED
        status.error = str(exc)
        for step in status.steps:
            if step.state == "downloading":
                step.state = "failed"
        return _publish(status, EXIT_FAILED, str(exc), on_status, on_progress)
    status.state = JobState.READY
    status.error = ""
    status.recompute()
    return _publish(status, EXIT_OK, _ready_message(status), on_status, on_progress)


def verify(
    manifests: Sequence[Path] | None = None,
    *,
    devices: Sequence[GpuDevice] | None = None,
    on_status: StatusFn | None = None,
) -> Outcome:
    """Re-hash the planned files and publish whether they are ready.

    Args:
        manifests: ``mdl.json`` paths. When omitted, installed apps are discovered.
        devices: GPUs to plan for.
        on_status: Called with the published snapshot.
    """
    try:
        parsed, tier = _inputs(manifests, devices)
        files = plan_files(parsed, tier)
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
    status.error = ""
    return _publish(status, EXIT_OK, _ready_message(status), on_status)


def _inputs(
    manifests: Sequence[Path] | None,
    devices: Sequence[GpuDevice] | None,
) -> tuple[list[AppManifest], VramTier]:
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
    return parsed, detect_tier(devices)


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
        )
        for item in files
    ]
    status.recompute()
    return status


def _note_local(status: Status, files: Sequence[PlannedFile]) -> None:
    for item, step in zip(files, status.steps, strict=True):
        if item.destination.is_file() and item.destination.stat().st_size == item.size:
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
    component_id = item.component_id
    for length in (16, 32, 64):
        candidate = f"model-{item.sha256[:length]}"
        current = document.components.get(candidate)
        if current is None or current.sha256 == item.sha256:
            component_id = candidate
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
) -> Outcome:
    status.recompute()
    status.touch()
    save_status(status)
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
    if not status.steps:
        return "nothing to download"
    return f"planned {len(status.steps)} file(s), {status.bytes_total} bytes"


def _ready_message(status: Status) -> str:
    if not status.steps:
        return "nothing to download"
    return "ready"


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc


class _Publisher:
    """Write the status file at a steady pace while bytes arrive."""

    def __init__(
        self, status: Status, on_status: StatusFn | None, on_progress: ProgressFn | None
    ) -> None:
        """Bind the snapshot and the optional listeners.

        Args:
            status: Mutable snapshot for this job.
            on_status: Called when the file is written.
            on_progress: Called with the overall fraction.
        """
        self.status = status
        self.on_status = on_status
        self.on_progress = on_progress
        self._last = 0.0

    def push(self, *, force: bool = False) -> None:
        """Publish when ``force`` is set or 250 ms have passed."""
        moment = time.monotonic()
        if not force and moment - self._last < 0.25:
            return
        self._last = moment
        self.status.touch()
        save_status(self.status)
        if self.on_status is not None:
            self.on_status(self.status)
        if self.on_progress is not None and self.status.bytes_total:
            fraction = min(1.0, self.status.bytes_done / self.status.bytes_total)
            self.on_progress(fraction, self.status.state.value)
