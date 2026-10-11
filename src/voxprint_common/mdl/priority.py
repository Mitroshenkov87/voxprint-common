"""Best-effort low CPU and IO priority for a long download.

Windows uses ``PROCESS_MODE_BACKGROUND_BEGIN``, which lowers both priorities.
Linux raises the nice value and sets the idle IO class. A missing tool or a
permission error is ignored so the download still runs.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager

__all__ = ["background_priority"]

logger = logging.getLogger(__name__)

_BACKGROUND_BEGIN = 0x00100000
_BACKGROUND_END = 0x00200000
_IOPRIO_SET = {"x86_64": 251, "aarch64": 30, "i686": 289, "armv7l": 314}
_IOPRIO_WHO_PROCESS = 1
_IOPRIO_CLASS_IDLE = 3
_IOPRIO_CLASS_BE = 2


@contextmanager
def background_priority() -> Iterator[None]:
    """Lower CPU and IO priority for this process, then restore them.

    The restore is best-effort. Raising the priority again can fail without
    extra privileges, and a short-lived installer process exits anyway.
    """
    token = _lower()
    try:
        yield
    finally:
        _restore(token)


def _lower() -> object:
    if sys.platform == "win32":
        return _win_lower()
    return _posix_lower()


def _restore(token: object) -> None:
    try:
        if sys.platform == "win32":
            _win_restore(token if isinstance(token, int) else None)
            return
        if isinstance(token, int):
            _posix_restore(token)
    except OSError:
        logger.debug("could not restore process priority", exc_info=True)


def _win_lower() -> int | None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel.GetCurrentProcess()
    previous = int(kernel.GetPriorityClass(handle))
    if not kernel.SetPriorityClass(handle, _BACKGROUND_BEGIN):
        logger.debug("could not enter Windows background mode")
        return None
    return previous


def _win_restore(previous: int | None) -> None:
    if previous is None:
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel.GetCurrentProcess()
    kernel.SetPriorityClass(handle, _BACKGROUND_END)
    kernel.SetPriorityClass(handle, previous)


def _posix_lower() -> int:
    previous = os.getpriority(os.PRIO_PROCESS, 0)
    try:
        os.setpriority(os.PRIO_PROCESS, 0, min(19, previous + 10))
    except OSError:
        logger.debug("could not raise nice", exc_info=True)
    if not _ionice_idle() and not _ioprio_set(_IOPRIO_CLASS_IDLE):
        logger.debug("could not set idle IO priority")
    return previous


def _posix_restore(previous: int) -> None:
    try:
        os.setpriority(os.PRIO_PROCESS, 0, previous)
    except OSError:
        logger.debug("could not restore nice", exc_info=True)
    _ioprio_set(_IOPRIO_CLASS_BE)


def _ionice_idle() -> bool:
    try:
        completed = subprocess.run(
            ["ionice", "-c", "3", "-p", str(os.getpid())],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except OSError, subprocess.SubprocessError:
        return False
    return completed.returncode == 0


def _ioprio_set(ioprio_class: int) -> bool:
    machine = os.uname().machine if hasattr(os, "uname") else ""
    number = _IOPRIO_SET.get(machine)
    if number is None:
        return False
    libc = ctypes.CDLL(None, use_errno=True)
    value = (ioprio_class << 13) | 0
    result = libc.syscall(number, _IOPRIO_WHO_PROCESS, os.getpid(), value)
    return int(result) == 0
