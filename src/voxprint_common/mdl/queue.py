"""One system-wide model download queue.

Installers and apps enqueue a job. A single worker, held with an exclusive
lock, performs the downloads. A second process enqueues and returns, or
attaches until its job finishes. The queue file is UTF-8 JSON, written
atomically under ``mdl-queue.lock``. A crash leaves ``.partial`` files and
jobs in ``running``; the next worker resumes them.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from voxprint_common.atomic import LockTimeout, exclusive_lock, write_json
from voxprint_common.cli_protocol import EXIT_CANCELLED, EXIT_FAILED, EXIT_OK
from voxprint_common.gpu_probe import GpuDevice, query_gpus
from voxprint_common.hashing import HashMismatch
from voxprint_common.hashing import verify as verify_hash
from voxprint_common.mdl.catalog import (
    CatalogError,
    PlannedFile,
    discover,
    load_manifest,
    plan_files,
    unavailable_rows,
)
from voxprint_common.mdl.events import ProgressEvent, TransferCancelled, TransferSession
from voxprint_common.mdl.fetch import Cancelled
from voxprint_common.mdl.hooks import files_awaiting_hooks
from voxprint_common.mdl.job import (
    Outcome,
    _append_unavailable,
    _card,
    _remember,
    detect_tier,
    download,
    progress_event,
)
from voxprint_common.mdl.safety import UrlGuard
from voxprint_common.mdl.status import JobState, Status, load_status, save_status
from voxprint_common.paths import state_dir
from voxprint_common.suite_settings import mdl_parallel_files

__all__ = [
    "QUEUE_NAME",
    "Failed",
    "QueueJob",
    "Ready",
    "enqueue",
    "ensure_ready",
    "load_queue",
    "run_or_attach",
]

QUEUE_NAME = "mdl-queue.json"
_SCHEMA = 1
_APP = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_MODEL = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ProgressCb = Callable[[float | None, str], None]


@dataclass(frozen=True)
class Ready:
    """The app's planned models are on disk."""

    app: str
    status: Status
    kind: Literal["ready"] = "ready"

    @property
    def ok(self) -> bool:
        """True. Plotweaver branches on this flag."""
        return True


@dataclass(frozen=True)
class Failed:
    """The app is not ready."""

    app: str
    error: str
    status: Status
    kind: Literal["failed"] = "failed"

    @property
    def ok(self) -> bool:
        """False. Plotweaver branches on this flag."""
        return False


@dataclass(frozen=True)
class QueueJob:
    """One request stored in the queue file."""

    id: str
    app: str
    models: tuple[str, ...] | None
    manifests: tuple[str, ...]
    state: str
    error: str
    enqueued_at: str
    unavailable: tuple[tuple[str, str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the JSON object for this job."""
        return {
            "id": self.id,
            "app": self.app,
            "models": None if self.models is None else list(self.models),
            "manifests": list(self.manifests),
            "state": self.state,
            "error": self.error,
            "enqueued_at": self.enqueued_at,
            "unavailable": [
                {"id": model_id, "display_name": name, "reason": reason}
                for model_id, name, reason in self.unavailable
            ],
        }


def queue_path() -> Path:
    """Return ``<home>/state/mdl-queue.json``. The file is not created."""
    return state_dir() / QUEUE_NAME


def enqueue(
    app: str,
    models: Sequence[str] | None = None,
    *,
    manifests: Sequence[Path] | None = None,
    devices: Sequence[GpuDevice] | None = None,
) -> str:
    """Append a job and return its id. This does not download anything.

    Files already in the shared models directory are not queued. A matching
    size and verified marker is enough; the digest is read only when the
    marker is missing. The app is added as a ref. A ``.partial`` file stays
    in place so the worker can resume it. Broken copies are queued again.

    Args:
        app: Suite app id.
        models: Model ids to fetch, including on-demand and optional ones.
            ``None`` means whatever that app's manifest needs for this GPU tier.
        manifests: Manifest paths to read instead of install discovery.
        devices: GPUs used to choose the tier. ``None`` queries nvidia-smi.

    Raises:
        CatalogError: ``app`` is not a suite id, or the manifest is not valid.
    """
    app_id = _check_app(app)
    listed = None if models is None else tuple(_check_model(model) for model in models)
    paths = tuple(str(Path(item)) for item in manifests) if manifests else ()
    _needed, notices, kept = _prepare(app_id, listed, paths, devices)
    if kept is not None:
        listed = kept
    job = QueueJob(
        id=f"job-{uuid.uuid4().hex[:12]}",
        app=app_id,
        models=listed,
        manifests=paths,
        state="done" if kept is not None and not kept else "queued",
        error="",
        enqueued_at=_now(),
        unavailable=notices,
    )
    path = queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(_lock(path)):
        document = _read(path)
        document.append(job)
        _write(path, document)
    if job.state == "done":
        _publish_settled(job)
    return job.id


def load_queue(path: Path | None = None) -> list[QueueJob]:
    """Return the jobs in the queue file.

    A missing file is an empty queue.

    Args:
        path: Override for tests.

    Raises:
        CatalogError: The file is not a schema 1 queue.
    """
    return _read(path or queue_path())


def run_or_attach(
    job_id: str | None = None,
    *,
    session: TransferSession | None = None,
    devices: Sequence[GpuDevice] | None = None,
    guard: UrlGuard | None = None,
    priority: bool = True,
    poll: float = 0.2,
    opener: object | None = None,
    fetch_options: Mapping[str, object] | None = None,
    display: bool = False,
) -> Outcome:
    """Run the queue if this process can take the worker lock, otherwise watch.

    Args:
        job_id: When set, an attached process returns when this job leaves
            the queue's active states. ``None`` waits until the queue is idle.
        session: Pause, resume, cancel, and progress subscription.
        devices: GPUs passed to planning. ``None`` queries nvidia-smi.
        guard: URL rules. Defaults to production, or the loopback guard when
            ``VOXPRINT_MDL_TEST_GUARD=loopback``.
        priority: Lower CPU and IO priority while this process is the worker.
        poll: Seconds between attach checks.
        opener: Passed through to the downloader.
        fetch_options: Extra downloader options.
        display: Also keep the display awake. The progress window sets this.

    Returns:
        The worker's outcome, or the outcome of ``job_id`` when attaching.
    """
    chosen = guard if guard is not None else _guard_from_env()
    while True:
        if session is not None and session.cancelled:
            _cancel_open()
            return _cancelled_outcome()
        try:
            with exclusive_lock(_worker_lock(), timeout=0):
                try:
                    return _work(
                        session,
                        devices,
                        chosen,
                        priority,
                        opener,
                        fetch_options,
                        display,
                    )
                except Cancelled:
                    _cancel_open()
                    return _cancelled_outcome()
        except LockTimeout:
            finished = _attached(job_id)
            if finished is not None:
                _emit(session)
                return finished
            _emit(session)
            time.sleep(poll)


def ensure_ready(app: str, progress_cb: ProgressCb | None = None) -> Ready | Failed:
    """Enqueue what ``app`` needs for this tier, then run or attach.

    Args:
        app: Suite app id.
        progress_cb: ``(fraction, message)`` as bytes move. ``fraction`` is
            ``None`` until a total is known.

    Returns:
        :class:`Ready` when the status file is ready, otherwise :class:`Failed`.
    """
    job_id = enqueue(app)
    session = TransferSession()
    if progress_cb is not None:
        session.subscribe(lambda event: progress_cb(_fraction(event), event.state))
    try:
        outcome = run_or_attach(job_id, session=session)
    except TransferCancelled:
        outcome = _cancelled_outcome()
    finished = _queue_job(job_id)
    done = finished is not None and finished.state == "done"
    idle = outcome.message == "nothing to download" or outcome.status.state is JobState.READY
    if outcome.code == EXIT_OK and (done or idle):
        return Ready(app, outcome.status)
    return Failed(app, outcome.message or outcome.status.error, outcome.status)


def _work(
    session: TransferSession | None,
    devices: Sequence[GpuDevice] | None,
    guard: UrlGuard | None,
    priority: bool,
    opener: object | None,
    fetch_options: Mapping[str, object] | None,
    display: bool,
) -> Outcome:
    cards = None if devices is None else list(devices)
    while True:
        if session is not None:
            try:
                session.checkpoint()
            except TransferCancelled as exc:
                _cancel_open()
                raise Cancelled("cancelled") from exc
        pending = [job for job in load_queue() if job.state in {"queued", "running"}]
        if not pending:
            status = load_status()
            if status.state is not JobState.READY:
                status.state = JobState.READY if status.steps else status.state
            return Outcome(EXIT_OK, "ready" if status.ready else "nothing to download", status)
        active = [job.id for job in pending]
        _set_states(active, "running")
        try:
            files, jobs = _plan(pending, cards)
        except (CatalogError, OSError) as exc:
            _set_states(active, "failed", str(exc))
            return Outcome(EXIT_FAILED, str(exc), _status_jobs())
        if not files:
            _set_states(active, "done")
            continue
        outcome = download(
            planned=files,
            devices=cards,
            dry_run=False,
            priority=priority,
            session=session,
            guard=guard,
            workers=mdl_parallel_files(),
            queue_jobs=jobs,
            opener=opener,
            fetch_options=fetch_options,
            display=display,
        )
        if outcome.code == EXIT_CANCELLED:
            _set_states(active, "cancelled", "cancelled")
            return outcome
        if outcome.code != EXIT_OK:
            _set_states(active, "failed", outcome.message)
            return outcome
        _set_states(active, "done")


def _plan(
    jobs: Sequence[QueueJob],
    devices: Sequence[GpuDevice] | None,
) -> tuple[list[PlannedFile], list[dict[str, object]]]:
    cards = list(query_gpus() if devices is None else devices)
    tier = detect_tier(cards)
    memory_gib, compute = _card(cards)
    merged: dict[str, PlannedFile] = {}
    order: list[str] = []
    for job in jobs:
        paths = _manifest_paths(job)
        manifests = [load_manifest(path) for path in paths]
        if job.app and all(item.app != job.app for item in manifests):
            raise CatalogError(f"manifests for {job.app} do not match the job")
        files = plan_files(
            manifests,
            tier,
            only=job.models,
            memory_gib=memory_gib,
            compute=compute,
        )
        for item in files:
            current = merged.get(item.sha256)
            if current is None:
                merged[item.sha256] = item
                order.append(item.sha256)
                continue
            for app in item.apps:
                if app not in current.apps:
                    current.apps.append(app)
            for model_id in item.model_ids:
                if model_id not in current.model_ids:
                    current.model_ids.append(model_id)
            for url in item.urls:
                if url not in current.urls:
                    current.urls = tuple([*current.urls, url])
            for hook in item.hooks:
                if hook not in current.hooks:
                    current.hooks = (*current.hooks, hook)
    rows = [job.to_dict() for job in load_queue()]
    return [merged[key] for key in order], rows


def _manifest_paths(job: QueueJob) -> list[Path]:
    if job.manifests:
        return [Path(item) for item in job.manifests]
    found = [app.path for app in discover() if app.id == job.app]
    if not found:
        raise CatalogError(f"no installed manifest for {job.app}")
    return found


def _attached(job_id: str | None) -> Outcome | None:
    jobs = load_queue()
    if job_id is not None:
        match = next((job for job in jobs if job.id == job_id), None)
        if match is None or match.state in {"done", "failed", "cancelled"}:
            return _outcome_for(match)
        return None
    if any(job.state in {"queued", "running"} for job in jobs):
        return None
    status = load_status()
    return Outcome(EXIT_OK, "ready" if status.ready else "idle", status)


def _outcome_for(job: QueueJob | None) -> Outcome:
    status = _status_jobs()
    if job is None:
        return Outcome(EXIT_FAILED, "job is not in the queue", status)
    if job.state == "done":
        return Outcome(EXIT_OK, "ready", status)
    if job.state == "cancelled":
        return Outcome(EXIT_CANCELLED, job.error or "cancelled", status)
    return Outcome(EXIT_FAILED, job.error or job.state, status)


def _status_jobs() -> Status:
    try:
        status = load_status()
    except OSError, ValueError:
        status = Status(JobState.FAILED)
    status.jobs = [job.to_dict() for job in load_queue()]
    return status


def _set_states(ids: Sequence[str], state: str, error: str = "") -> None:
    path = queue_path()
    wanted = set(ids)
    with exclusive_lock(_lock(path)):
        jobs = _read(path)
        for index, job in enumerate(jobs):
            if job.id in wanted and job.state in {"queued", "running", state}:
                jobs[index] = QueueJob(
                    job.id,
                    job.app,
                    job.models,
                    job.manifests,
                    state,
                    error or job.error,
                    job.enqueued_at,
                    job.unavailable,
                )
        _write(path, jobs)


def _emit(session: TransferSession | None) -> None:
    if session is None:
        return
    try:
        event = progress_event(load_status())
    except OSError, ValueError:
        return
    session.publish(event)


def _queue_job(job_id: str) -> QueueJob | None:
    return next((job for job in load_queue() if job.id == job_id), None)


def _cancel_open() -> None:
    ids = [job.id for job in load_queue() if job.state in {"queued", "running"}]
    if ids:
        _set_states(ids, "cancelled", "cancelled")


def _cancelled_outcome() -> Outcome:
    status = Status(JobState.CANCELLED, error="cancelled")
    return Outcome(EXIT_CANCELLED, "cancelled", status)


def _fraction(event: ProgressEvent) -> float | None:
    if not event.bytes_total:
        return None
    return min(1.0, event.bytes_done / event.bytes_total)


def _guard_from_env() -> UrlGuard | None:
    if os.environ.get("VOXPRINT_MDL_TEST_GUARD") == "loopback":
        return UrlGuard.loopback()
    return None


def _read(path: Path) -> list[QueueJob]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CatalogError(f"{path.name} could not be read") from exc
    if not isinstance(payload, dict) or payload.get("schema") != _SCHEMA:
        raise CatalogError(f"{path.name} schema must be 1")
    rows = payload.get("jobs")
    if not isinstance(rows, list):
        raise CatalogError("jobs must be a list")
    return [_job(row) for row in rows]


def _write(path: Path, jobs: Sequence[QueueJob]) -> None:
    body = {"schema": _SCHEMA, "jobs": [job.to_dict() for job in jobs]}
    write_json(path, body)


def _job(value: object) -> QueueJob:
    if not isinstance(value, dict):
        raise CatalogError("queue job must be an object")
    models = value.get("models")
    listed: tuple[str, ...] | None
    if models is None:
        listed = None
    elif isinstance(models, list) and all(isinstance(item, str) for item in models):
        listed = tuple(models)
    else:
        raise CatalogError("job models must be a list of ids or null")
    manifests = value.get("manifests", [])
    if not isinstance(manifests, list) or any(not isinstance(item, str) for item in manifests):
        raise CatalogError("job manifests must be a list of paths")
    state = value.get("state")
    if state not in {"queued", "running", "done", "failed", "cancelled"}:
        raise CatalogError(f"unknown job state {state!r}")
    job_id = value.get("id")
    app = value.get("app")
    if not isinstance(job_id, str) or not job_id or not isinstance(app, str):
        raise CatalogError("job id and app are required")
    return QueueJob(
        job_id,
        app,
        listed,
        tuple(manifests),
        str(state),
        str(value.get("error") or ""),
        str(value.get("enqueued_at") or ""),
        _unavailable_rows(value.get("unavailable")),
    )


def _unavailable_rows(value: object) -> tuple[tuple[str, str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CatalogError("job unavailable must be a list")
    rows: list[tuple[str, str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise CatalogError("unavailable entry must be an object")
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id:
            raise CatalogError("unavailable entry needs an id")
        name = item.get("display_name") or model_id
        reason = item.get("reason") or "unavailable"
        rows.append((model_id, str(name), str(reason)))
    return tuple(rows)


def _lock(path: Path) -> Path:
    return path.with_name("mdl-queue.lock")


def _worker_lock() -> Path:
    path = state_dir() / "mdl-worker.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _prepare(
    app: str,
    models: tuple[str, ...] | None,
    manifests: tuple[str, ...],
    devices: Sequence[GpuDevice] | None,
) -> tuple[list[PlannedFile], tuple[tuple[str, str, str], ...], tuple[str, ...] | None]:
    """Return files still needed, unavailable rows, and the model ids to store.

    The third value is None when the manifests could not be resolved yet.
    The worker then plans them. Otherwise it is the ids that still need a
    download, which may be empty.
    """
    paths = _manifest_paths(QueueJob("pending", app, models, manifests, "queued", "", ""))
    if not paths:
        return [], (), None
    parsed = [load_manifest(path) for path in paths]
    if app and all(item.app != app for item in parsed):
        raise CatalogError(f"manifests for {app} do not match the job")
    cards = list(query_gpus() if devices is None else devices)
    tier = detect_tier(cards)
    memory_gib, compute = _card(cards)
    files = plan_files(parsed, tier, only=models, memory_gib=memory_gib, compute=compute)
    notices = tuple(unavailable_rows(parsed))
    waiting = files_awaiting_hooks(files)
    needed: list[PlannedFile] = []
    for item in files:
        if _already_installed(item):
            _remember(item)
            if id(item) not in waiting:
                continue
        needed.append(item)
    ids = [item.model_id or item.model_ids[0] for item in needed if item.model_id or item.model_ids]
    return needed, notices, tuple(dict.fromkeys(ids))


def _publish_settled(job: QueueJob) -> None:
    """Publish a job that finished before the worker ran.

    An idle snapshot becomes ready. Unavailable rows are copied on so the
    window can show them. A download that is still queued or running is left
    alone.
    """
    if any(item.state in {"queued", "running"} for item in load_queue()):
        return
    status = load_status()
    if status.state is JobState.DOWNLOADING:
        return
    _append_unavailable(status, [job.to_dict()])
    status.jobs = [item.to_dict() for item in load_queue()]
    if status.state in {JobState.IDLE, JobState.DRY_RUN}:
        status.state = JobState.READY
        status.error = ""
    save_status(status)


def _already_installed(item: PlannedFile) -> bool:
    """Return whether the blob can be reused without another download.

    A partial file is not installed. It is left on disk for Range resume.
    """
    path = item.destination
    partial = path.with_name(path.name + ".partial")
    if partial.is_file() and not path.is_file():
        return False
    if not path.is_file() or path.stat().st_size != item.size:
        return False
    marker = path.with_name(path.name + ".verified")
    try:
        recorded = marker.read_text(encoding="utf-8").strip()
    except OSError:
        recorded = ""
    if recorded == item.sha256:
        return True
    try:
        verify_hash(path, item.sha256)
    except HashMismatch, OSError, ValueError:
        return False
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(item.sha256 + "\n", encoding="utf-8")
    return True


def _check_app(value: str) -> str:
    text = value.strip()
    if _APP.fullmatch(text) is None:
        raise CatalogError(f"app id {value!r} is not a suite id")
    return text


def _check_model(value: str) -> str:
    text = value.strip()
    if _MODEL.fullmatch(text) is None:
        raise CatalogError(f"model id {value!r} is not valid")
    return text


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
