"""Cross-process GPU lock.

The file is ``%TEMP%/voxprint-gpu.lock``, or ``VOXPRINT_GPU_LOCK`` when that
variable is set. Creation uses ``O_CREAT|O_EXCL``. The holder record is:

* ``app``: suite id of the process that owns the GPU
* ``stage``: what that process is doing
* ``pid``, ``started``, ``eta``: liveness. ``eta`` and ``started`` are UTC

A holder is stale when its pid is gone, or when ``eta`` (else ``started``) is
more than two hours in the past. A stale file is replaced.

``VOXPRINT_GPU_PARENT_PID``, when it equals the lock's pid, means this process
is already covered and must not delete the file. The same is true when the
lock pid is this process's parent and the app id matches.

Older files that say ``owner`` and ``job`` are read as ``app`` and ``stage``.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from ctypes import wintypes
except ImportError:  # POSIX builds of CPython have no wintypes.
    wintypes = None  # type: ignore[assignment]

__all__ = [
    "LOCK_NAME",
    "STALE_AFTER",
    "Acquired",
    "GpuLockTimeout",
    "Holder",
    "gpu_job",
    "is_stale",
    "lock_path",
    "pid_exists",
    "read_holder",
    "refresh",
    "release",
    "try_acquire",
]

LOCK_NAME = "voxprint-gpu.lock"
STALE_AFTER = timedelta(hours=2)

_depth = 0


class GpuLockTimeout(TimeoutError):
    """Another app still holds the GPU and the wait expired, or the wait was cancelled."""


@dataclass(frozen=True)
class Holder:
    """The process that currently owns the GPU lock."""

    app: str
    stage: str
    pid: int
    started: str
    eta: str

    def as_dict(self) -> dict[str, object]:
        """Return the JSON object written to the lock file."""
        return {
            "app": self.app,
            "stage": self.stage,
            "pid": self.pid,
            "started": self.started,
            "eta": self.eta,
        }


@dataclass(frozen=True)
class Acquired:
    """:func:`try_acquire` succeeded.

    ``borrowed`` is true when a parent process already holds the file. The
    caller must run the job and must not delete the lock.
    """

    borrowed: bool


def lock_path() -> Path:
    """Return the suite GPU lock path."""
    override = os.environ.get("VOXPRINT_GPU_LOCK", "").strip()
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / LOCK_NAME


def pid_exists(pid: int) -> bool:
    """Return whether ``pid`` is a live process.

    On POSIX this sends signal 0. On Windows it opens the process with
    ``PROCESS_QUERY_LIMITED_INFORMATION`` and treats access-denied as alive.
    It does not signal or terminate the process.

    Args:
        pid: Process id. Non-positive values are not alive.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_holder(path: Path | None = None) -> Holder | None:
    """Return the current holder, or None when the file is missing or unreadable.

    Args:
        path: Lock file. Defaults to :func:`lock_path`.
    """
    return _holder(_read_raw(path or lock_path()))


def is_stale(data: Mapping[str, Any], now: datetime | None = None) -> bool:
    """Return whether a holder record should be replaced.

    Args:
        data: Lock JSON. An empty mapping is stale.
        now: Clock for the two-hour rule. Defaults to the current UTC time.
    """
    if not data:
        return True
    pid = _pid(data.get("pid"))
    if pid is None or not pid_exists(pid):
        return True
    moment = _parse(data.get("eta")) or _parse(data.get("started"))
    if moment is None:
        return True
    current = now or datetime.now(UTC)
    return current - moment > STALE_AFTER


def try_acquire(
    app: str,
    stage: str,
    eta: float = 600.0,
    path: Path | None = None,
) -> Holder | Acquired:
    """Create the lock, or report the process that already holds it.

    Args:
        app: Suite id stored in the file.
        stage: Stage name stored in the file.
        eta: Seconds from now until the record is allowed to go stale,
            after the two-hour grace in :func:`is_stale`.
        path: Lock file. Defaults to :func:`lock_path`.

    Returns:
        :class:`Acquired` when this process may use the GPU.
        :class:`Holder` when a live lock belongs to someone else.
    """
    target = path or lock_path()
    current = _read_raw(target)
    if current is not None:
        if (
            _ours(current, app)
            or _held_by_parent(current, app)
            or _held_by_declared_parent(current)
        ):
            borrowed = not _ours(current, app)
            return Acquired(borrowed=borrowed)
        if not is_stale(current):
            holder = _holder(current)
            return holder if holder is not None else Holder("?", "?", 0, "", "")
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            holder = _holder(current)
            return holder if holder is not None else Holder("?", "?", 0, "", "")
    return _create(target, app, stage, eta)


def release(path: Path | None = None, app: str | None = None) -> None:
    """Delete the lock when this process owns it.

    A lock held by ``VOXPRINT_GPU_PARENT_PID`` is left in place. A lock owned
    by another pid is left in place.

    Args:
        path: Lock file. Defaults to :func:`lock_path`.
        app: App id that must match. Defaults to the id recorded in the file
            when it is this process.
    """
    target = path or lock_path()
    current = _read_raw(target)
    if current is None or _held_by_declared_parent(current):
        return
    if not _ours(current, app):
        return
    try:
        target.unlink()
    except OSError:
        return


def refresh(eta: float, path: Path | None = None, app: str | None = None) -> None:
    """Move ``eta`` forward while this process holds the lock.

    Args:
        eta: Seconds from now.
        path: Lock file. Defaults to :func:`lock_path`.
        app: App id that must match.
    """
    target = path or lock_path()
    current = _read_raw(target)
    if not _ours(current, app):
        return
    holder = _holder(current)
    if holder is None:
        return
    moment = datetime.now(UTC)
    updated = Holder(
        holder.app,
        holder.stage,
        holder.pid,
        holder.started,
        _iso(moment + timedelta(seconds=max(1.0, float(eta)))),
    )
    _write_existing(target, updated)


@contextmanager
def gpu_job(
    app: str,
    stage: str,
    *,
    eta: float = 600.0,
    timeout: float | None = None,
    poll: float = 0.25,
    path: Path | None = None,
    cancel: Callable[[], bool] | None = None,
    on_wait: Callable[[Holder, float], None] | None = None,
) -> Iterator[None]:
    """Hold the suite lock for one stage.

    Nested calls in the same process reuse the outer hold and only the outer
    call releases the file. A borrowed parent lock is never deleted.

    Args:
        app: Suite id.
        stage: Stage name.
        eta: Seconds until the new record's eta.
        timeout: Seconds to wait for a live holder. None waits until the
            holder leaves or ``cancel`` reports true.
        poll: Seconds between attempts.
        path: Lock file.
        cancel: Called while waiting. True aborts the wait.
        on_wait: Called with the holder and the seconds already waited.

    Raises:
        GpuLockTimeout: The wait was cancelled or ``timeout`` elapsed.
    """
    global _depth
    if _depth > 0:
        _depth += 1
        try:
            yield
        finally:
            _depth -= 1
        return
    started = time.monotonic()
    borrowed = False
    while True:
        outcome = try_acquire(app, stage, eta, path)
        if isinstance(outcome, Acquired):
            borrowed = outcome.borrowed
            break
        waited = time.monotonic() - started
        if cancel is not None and cancel():
            raise GpuLockTimeout("cancelled while waiting for the GPU")
        if timeout is not None and waited > timeout:
            raise GpuLockTimeout(f"the GPU is busy: {outcome.app} / {outcome.stage}")
        if on_wait is not None:
            on_wait(outcome, waited)
        time.sleep(poll)
    _depth = 1
    try:
        yield
    finally:
        _depth = 0
        if not borrowed:
            release(path, app)


def _create(target: Path, app: str, stage: str, eta: float) -> Holder | Acquired:
    moment = datetime.now(UTC)
    holder = Holder(
        app,
        stage,
        os.getpid(),
        _iso(moment),
        _iso(moment + timedelta(seconds=max(1.0, float(eta)))),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        current = _read_raw(target)
        found = _holder(current)
        return found if found is not None else Holder("?", "?", 0, "", "")
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(holder.as_dict(), handle)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return Acquired(borrowed=False)


def _write_existing(target: Path, holder: Holder) -> None:
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(holder.as_dict()) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, target)


def _read_raw(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except OSError:
        return {}
    try:
        payload = json.loads(text)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _holder(data: Mapping[str, Any] | None) -> Holder | None:
    if not data:
        return None
    pid = _pid(data.get("pid"))
    app = data.get("app", data.get("owner", "?"))
    stage = data.get("stage", data.get("job", ""))
    return Holder(
        str(app) if isinstance(app, str) and app else "?",
        str(stage) if isinstance(stage, str) else "",
        pid or 0,
        str(data.get("started") or ""),
        str(data.get("eta") or ""),
    )


def _ours(data: Mapping[str, Any] | None, app: str | None) -> bool:
    if not data:
        return False
    expected = _app_of(data) if app is None else app
    return _app_of(data) == expected and _pid(data.get("pid")) == os.getpid()


def _held_by_declared_parent(data: Mapping[str, Any] | None) -> bool:
    declared = _declared_parent_pid()
    if declared is None or not data:
        return False
    return _pid(data.get("pid")) == declared


def _held_by_parent(data: Mapping[str, Any] | None, app: str) -> bool:
    if not data or _app_of(data) != app:
        return False
    try:
        parent = os.getppid()
    except OSError:
        return False
    return _pid(data.get("pid")) == parent


def _declared_parent_pid() -> int | None:
    raw = os.environ.get("VOXPRINT_GPU_PARENT_PID", "").strip()
    if not raw:
        return None
    try:
        number = int(raw)
    except ValueError:
        return None
    if number <= 0:
        return None
    return number


def _app_of(data: Mapping[str, Any]) -> str:
    value = data.get("app", data.get("owner", ""))
    return value if isinstance(value, str) else ""


def _pid(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        number = int(value)
    except TypeError, ValueError:
        return None
    if number <= 0:
        return None
    return number


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _windows_pid_exists(pid: int) -> bool:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    query = 0x1000
    handle = kernel.OpenProcess(query, False, pid)
    if handle:
        kernel.CloseHandle(handle)
        return True
    error = ctypes.get_last_error()
    return error == 5
