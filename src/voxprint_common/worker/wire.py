"""Messages pickled across the worker pipe.

These names stay private to the package. Handlers do not construct them.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__: list[str] = []


@dataclass(frozen=True)
class Submit:
    """Run one job."""

    job_id: str
    kind: str
    payload: object
    model: str | None
    phase: str | None
    checkpoint: str | None


@dataclass(frozen=True)
class Unload:
    """Drop the resident model."""


@dataclass(frozen=True)
class Shutdown:
    """Leave the child process."""


@dataclass(frozen=True)
class Cancel:
    """Ask the running job to stop."""

    job_id: str


@dataclass(frozen=True)
class Beat:
    """The child is still scheduling Python."""

    job_id: str


@dataclass(frozen=True)
class Noted:
    """The handler recorded a checkpoint path."""

    job_id: str
    checkpoint: str


@dataclass(frozen=True)
class Loaded:
    """A model is resident in the child."""

    model: str


@dataclass(frozen=True)
class Unloaded:
    """A model was dropped."""

    model: str


@dataclass(frozen=True)
class Progress:
    """Handler progress, including training step, epoch, ETA, and metrics."""

    job_id: str
    message: str
    step: int | None
    epoch: int | None
    total_steps: int | None
    total_epochs: int | None
    eta_s: float | None
    metrics: tuple[tuple[str, float], ...]
    fraction: float | None
    phase: str | None


@dataclass(frozen=True)
class DidFinish:
    """The handler returned."""

    job_id: str
    value: object
    checkpoint: str | None


@dataclass(frozen=True)
class DidFail:
    """The handler raised."""

    job_id: str
    message: str
    checkpoint: str | None


@dataclass(frozen=True)
class DidCancel:
    """The handler stopped because the job was cancelled."""

    job_id: str
    checkpoint: str | None
    message: str
