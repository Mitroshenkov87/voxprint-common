"""UTF-8 status file installers and apps poll for ``ready``.

The file is ``<home>/state/mdl-status.json``. Top-level keys other than the
ones this module writes are preserved. ``ready`` is true only when ``state``
is ``ready``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from voxprint_common.atomic import write_json
from voxprint_common.mdl.names import DISPLAY_NAME
from voxprint_common.paths import state_dir

__all__ = [
    "STATUS_NAME",
    "JobState",
    "Status",
    "StatusError",
    "Step",
    "load_status",
    "ready",
    "save_status",
    "status_path",
]

STATUS_NAME = "mdl-status.json"
_KNOWN = frozenset(
    {
        "schema",
        "tool",
        "ready",
        "state",
        "tier",
        "bytes_total",
        "bytes_done",
        "error",
        "updated_at",
        "steps",
        "jobs",
    }
)


class StatusError(ValueError):
    """The status file is not a schema 1 document."""


class JobState(StrEnum):
    """Lifecycle stored in the status file."""

    IDLE = "idle"
    PLANNED = "planned"
    DOWNLOADING = "downloading"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DRY_RUN = "dry-run"


@dataclass
class Step:
    """One file in the step list."""

    model: str
    size: int
    source: str
    speed_bps: float
    eta_s: float | None
    state: str
    received: int
    sha256: str
    apps: list[str]
    item_id: str = ""
    display_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON object for this step."""
        return {
            "model": self.model,
            "size": self.size,
            "source": self.source,
            "speed_bps": self.speed_bps,
            "eta_s": self.eta_s,
            "state": self.state,
            "received": self.received,
            "sha256": self.sha256,
            "apps": list(self.apps),
            "id": self.item_id,
            "display_name": self.display_name,
        }


@dataclass
class Status:
    """One snapshot of the downloader."""

    state: JobState
    tier: str = ""
    bytes_total: int = 0
    bytes_done: int = 0
    error: str = ""
    updated_at: str = ""
    steps: list[Step] = field(default_factory=list)
    jobs: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        """True when the job finished and every planned file checked out."""
        return self.state is JobState.READY

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON object, including preserved unknown keys."""
        body: dict[str, Any] = {
            "schema": 1,
            "tool": DISPLAY_NAME,
            "ready": self.ready,
            "state": self.state.value,
            "tier": self.tier,
            "bytes_total": self.bytes_total,
            "bytes_done": self.bytes_done,
            "error": self.error,
            "updated_at": self.updated_at or _now(),
            "steps": [step.to_dict() for step in self.steps],
            "jobs": list(self.jobs),
        }
        for key, value in self.extra.items():
            if key not in body:
                body[key] = value
        return body

    def touch(self) -> None:
        """Set ``updated_at`` to the current local time."""
        self.updated_at = _now()

    def recompute(self) -> None:
        """Refresh byte totals from the steps."""
        self.bytes_total = sum(step.size for step in self.steps)
        self.bytes_done = sum(min(step.received, step.size) for step in self.steps)


def status_path() -> Path:
    """Return ``<home>/state/mdl-status.json``. The file is not created."""
    return state_dir() / STATUS_NAME


def load_status(path: Path | None = None) -> Status:
    """Read the status file.

    A missing file is an idle snapshot with ``ready`` false. It is not created.

    Args:
        path: Override for tests. Defaults to :func:`status_path`.

    Raises:
        StatusError: The file is not a schema 1 object.
    """
    target = path or status_path()
    if not target.is_file():
        return Status(JobState.IDLE)
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise StatusError(f"{target.name} could not be read") from exc
    if not isinstance(payload, dict):
        raise StatusError(f"{target.name} must be a JSON object")
    if payload.get("schema") != 1:
        raise StatusError(f"unsupported status schema {payload.get('schema')!r}")
    state = _state(payload.get("state"))
    steps_raw = payload.get("steps", [])
    if not isinstance(steps_raw, list):
        raise StatusError("steps must be a list")
    steps = [_step(row) for row in steps_raw]
    extra = {key: value for key, value in payload.items() if key not in _KNOWN}
    status = Status(
        state=state,
        tier=_text(payload.get("tier")),
        bytes_total=_count(payload.get("bytes_total")),
        bytes_done=_count(payload.get("bytes_done")),
        error=_text(payload.get("error")),
        updated_at=_text(payload.get("updated_at")),
        steps=steps,
        jobs=_jobs(payload.get("jobs")),
        extra=extra,
    )
    status.recompute()
    return status


def save_status(status: Status, path: Path | None = None) -> None:
    """Atomically replace the status file with a UTF-8 JSON document.

    Args:
        status: Snapshot to publish.
        path: Override for tests. Defaults to :func:`status_path`.
    """
    if not status.updated_at:
        status.touch()
    write_json(path or status_path(), status.to_dict())


def ready(path: Path | None = None) -> bool:
    """Return whether the status file reports the models ready.

    Args:
        path: Override for tests. Defaults to :func:`status_path`.

    A missing file is not ready. A damaged file raises :class:`StatusError`.
    """
    return load_status(path).ready


def _state(value: object) -> JobState:
    if not isinstance(value, str):
        raise StatusError("state is required")
    for state in JobState:
        if value == state.value:
            return state
    raise StatusError(f"unknown state {value!r}")


def _step(value: object) -> Step:
    if not isinstance(value, dict):
        raise StatusError("step must be an object")
    apps = value.get("apps", [])
    if not isinstance(apps, list) or any(not isinstance(item, str) for item in apps):
        raise StatusError("step apps must be a list of strings")
    eta = value.get("eta_s")
    if eta is not None and not isinstance(eta, (int, float)):
        raise StatusError("eta_s must be a number or null")
    return Step(
        model=_text(value.get("model")),
        size=_count(value.get("size")),
        source=_text(value.get("source")),
        speed_bps=float(value.get("speed_bps") or 0),
        eta_s=None if eta is None else float(eta),
        state=_text(value.get("state")) or "pending",
        received=_count(value.get("received")),
        sha256=_text(value.get("sha256")),
        apps=list(apps),
        item_id=_text(value.get("id")),
        display_name=_text(value.get("display_name")),
    )


def _jobs(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise StatusError("jobs must be a list")
    jobs: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            raise StatusError("job must be an object")
        jobs.append(dict(row))
    return jobs


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
