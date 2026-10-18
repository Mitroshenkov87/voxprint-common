"""Atomic publishes and an exclusive file lock.

A publish writes a temporary file in the destination directory, flushes it,
then renames it over the destination. Readers see the previous file or the
finished file.

The lock is ``flock`` on POSIX and ``msvcrt.locking`` on Windows. It is not
re-entrant. Hold it around one read-modify-write, and do not take it again
on the same thread.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # Windows has no fcntl.
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt
except ImportError:  # POSIX has no msvcrt.
    msvcrt = None  # type: ignore[assignment]

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_GUARD = threading.Lock()

__all__ = [
    "LockTimeout",
    "exclusive_lock",
    "replace",
    "write_bytes",
    "write_json",
    "write_text",
]


class LockTimeout(TimeoutError):
    """The exclusive lock was not acquired before the timeout."""


def write_text(path: Path, text: str) -> None:
    """Replace a text file with UTF-8 bytes and a single trailing newline policy.

    ``text`` is written exactly as given. Newlines are not translated.

    Args:
        path: Destination file.
        text: Full contents of the file.
    """
    write_bytes(path, text.encode("utf-8"))


def write_json(path: Path, payload: Any) -> None:
    """Replace a JSON file with an indented UTF-8 document and a trailing newline.

    Args:
        path: Destination file.
        payload: Value accepted by ``json.dumps``.
    """
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    write_text(path, text)


def write_bytes(path: Path, data: bytes) -> None:
    """Replace ``path`` by writing a sibling temporary file, fsyncing, and renaming.

    Args:
        path: Destination file. Parent directories are created.
        data: Full contents of the file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def replace(source: Path, destination: Path) -> None:
    """Fsync ``source`` and rename it onto ``destination``.

    Args:
        source: A finished file in the same directory as ``destination``.
        destination: The published name. An existing file is replaced.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A read-only handle cannot be flushed on Windows (EBADF). Read-write can.
    descriptor = os.open(source, os.O_RDWR)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(source, destination)
    _fsync_dir(destination.parent)


@contextmanager
def exclusive_lock(path: Path, timeout: float = 30.0, poll: float = 0.05) -> Iterator[None]:
    """Hold an exclusive advisory lock on ``path``.

    The lock file is created if needed and is not deleted on release, so
    waiters keep a stable inode. ``timeout`` is measured with a monotonic clock.

    Args:
        path: Lock file. This is not the data file.
        timeout: Seconds to wait. ``0`` tries once.
        poll: Seconds between attempts.

    Raises:
        LockTimeout: The lock was still held when the timeout expired.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock(path)
    deadline = time.monotonic() + max(0.0, timeout)
    if not thread_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise LockTimeout(f"timed out waiting for lock {path}")
    try:
        descriptor = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            if os.lseek(descriptor, 0, os.SEEK_END) == 0:
                # Windows: another process may have seeded the byte and locked it first.
                with contextlib.suppress(PermissionError):
                    os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            while True:
                try:
                    _lock(descriptor)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LockTimeout(f"timed out waiting for lock {path}") from None
                    time.sleep(poll)
            try:
                yield
            finally:
                _unlock(descriptor)
        finally:
            os.close(descriptor)
    finally:
        thread_lock.release()


def _fsync_dir(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _thread_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path))
    with _THREAD_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


def _lock(descriptor: int) -> None:
    if msvcrt is not None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError(str(exc)) from exc
        return
    if fcntl is None:
        raise OSError("this platform has no file lock")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            raise BlockingIOError(str(exc)) from exc
        raise


def _unlock(descriptor: int) -> None:
    if msvcrt is not None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    if fcntl is None:
        return
    fcntl.flock(descriptor, fcntl.LOCK_UN)
