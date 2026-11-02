"""Model jobs in a spawned child process.

Importing this package does not load Qt and does not start a process.
The Qt adapter is :mod:`voxprint_common.worker.qt`.
"""

from __future__ import annotations

from voxprint_common.worker.context import JobCancelled, JobContext
from voxprint_common.worker.host import CheckHook, WorkerHost
from voxprint_common.worker.messages import (
    EventKind,
    Handlers,
    JobKind,
    JobOutcome,
    JobSpec,
    JobState,
    RestartPolicy,
    WorkerEvent,
)

__all__ = [
    "CheckHook",
    "EventKind",
    "Handlers",
    "JobCancelled",
    "JobContext",
    "JobKind",
    "JobOutcome",
    "JobSpec",
    "JobState",
    "RestartPolicy",
    "WorkerEvent",
    "WorkerHost",
]
