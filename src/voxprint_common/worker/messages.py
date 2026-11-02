"""Job descriptions and the events a host returns.

Wire messages between the parent and the child live in
:mod:`voxprint_common.worker.wire`. These types are the public ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import assert_never

__all__ = [
    "EventKind",
    "Handlers",
    "JobKind",
    "JobOutcome",
    "JobSpec",
    "JobState",
    "RestartPolicy",
    "WorkerEvent",
]


class JobKind(StrEnum):
    """What the worker should run.

    Training resumes after a crash. Inference, captioning, and checks fail
    fast. An app can override that with :class:`RestartPolicy` on the job.
    """

    INFERENCE = "inference"
    TRAINING = "training"
    CAPTION = "caption"
    CHECK = "check"


class RestartPolicy(StrEnum):
    """What to do with the in-flight job after the worker process dies."""

    FAIL = "fail"
    RESUME = "resume"


class JobState(StrEnum):
    """How a job ended."""

    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventKind(StrEnum):
    """One record from :meth:`voxprint_common.worker.WorkerHost.poll`."""

    PROGRESS = "progress"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CRASHED = "crashed"
    LOADED = "loaded"
    UNLOADED = "unloaded"


def default_restart(kind: JobKind) -> RestartPolicy:
    """Return the restart policy for ``kind`` when a job does not set one.

    Args:
        kind: Job type.

    Returns:
        Resume for training. Fail for inference, captioning, and checks.
    """
    if kind is JobKind.TRAINING:
        return RestartPolicy.RESUME
    if kind is JobKind.INFERENCE or kind is JobKind.CAPTION or kind is JobKind.CHECK:
        return RestartPolicy.FAIL
    assert_never(kind)


@dataclass(frozen=True)
class Handlers:
    """Importable callables the child process runs.

    Each value is ``module:callable``. Spawn imports that path in the child.
    Closures and lambdas cannot cross this boundary.

    ``run`` receives a :class:`~voxprint_common.worker.JobContext` and returns
    a picklable value. ``load_model`` receives a model id and returns the
    loaded object. ``unload_model`` receives the model id and that object.
    ``check`` receives a context when the job kind is ``check``. When
    ``check`` is omitted, check jobs use ``run``.
    """

    run: str
    load_model: str | None = None
    unload_model: str | None = None
    check: str | None = None


@dataclass(frozen=True)
class JobSpec:
    """One unit of work.

    ``payload`` is pickled and must not contain Qt objects or closures.
    ``model`` is the heavy weight to keep loaded. Consecutive jobs that name
    the same model reuse it. A different model, or an empty queue, unloads it.
    ``phase`` is a batch label carried on progress events. ``checkpoint`` is
    a path the handler can read when this job continues an earlier one.
    ``heartbeat_timeout`` overrides the host default. Set it above the longest
    stretch that does not return to Python. ``max_resumes`` is how many times
    a resume policy may start the job again after a crash.
    """

    kind: JobKind
    payload: object = None
    model: str | None = None
    phase: str | None = None
    checkpoint: str | None = None
    restart: RestartPolicy | None = None
    heartbeat_timeout: float | None = None
    max_resumes: int = 1

    def resolved_restart(self) -> RestartPolicy:
        """Return the restart policy, including the default for this kind."""
        if self.restart is not None:
            return self.restart
        return default_restart(self.kind)


@dataclass(frozen=True)
class JobOutcome:
    """The result, failure, or cancellation of one job."""

    job_id: str
    state: JobState
    value: object = None
    message: str = ""
    checkpoint: str | None = None
    attempts: int = 1


@dataclass(frozen=True)
class WorkerEvent:
    """One progress, result, or lifecycle record.

    ``metrics`` is a tuple of pairs so the event can be stored safely.
    :attr:`metric_map` is the same data as a dictionary.
    """

    kind: EventKind
    job_id: str = ""
    message: str = ""
    step: int | None = None
    epoch: int | None = None
    total_steps: int | None = None
    total_epochs: int | None = None
    eta_s: float | None = None
    metrics: tuple[tuple[str, float], ...] = ()
    fraction: float | None = None
    value: object = None
    checkpoint: str | None = None
    model: str | None = None
    phase: str | None = None
    exit_code: int | None = None
    attempts: int = 0
    metric_map: dict[str, float] = field(init=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_map", dict(self.metrics))
