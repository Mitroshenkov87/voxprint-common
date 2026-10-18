"""RAM size, the suite tier, and the pin budget.

Windows reads ``GlobalMemoryStatusEx``. Linux reads ``/proc/meminfo``.
There is no torch and no psutil dependency.

Tiers use gibibytes. A 32 GB machine reports about 31.7 GiB and is ``ram32``.
A 48 GB machine reports about 44.7 GiB and is ``ram64``. ``ram96`` starts at 88 GiB.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from enum import StrEnum
from pathlib import Path

from voxprint_common.suite_settings import ram_mode

__all__ = [
    "RamTier",
    "available_gib",
    "pin_budget_bytes",
    "pin_budget_gib",
    "ram_tier",
    "tier_for_gib",
    "total_gib",
]

logger = logging.getLogger(__name__)

GIB = 1024**3
_RAM64_MIN = 44.0
_RAM96_MIN = 88.0
_LOW_GIB = 24.0
_RESERVE_GIB = 12.0
_WINDOWS_FRACTION = 0.5
_OTHER_FRACTION = 0.6
_RAM64_FRACTION = 0.25
_MEMINFO = Path("/proc/meminfo")
_ENV = "VOXPRINT_RAM_MODE"
_warned = False


class RamTier(StrEnum):
    """How much host RAM the machine is treated as having."""

    RAM32 = "ram32"
    RAM64 = "ram64"
    RAM96 = "ram96"


class _MemoryStatus(ctypes.Structure):
    """The fields ``GlobalMemoryStatusEx`` fills. Length is set before the call."""

    _fields_ = (
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    )


def total_gib() -> float:
    """Return installed physical RAM in gibibytes."""
    return _memory_bytes()[0] / GIB


def available_gib() -> float:
    """Return physical RAM that is free for new allocations, in gibibytes."""
    return _memory_bytes()[1] / GIB


def tier_for_gib(total: float) -> RamTier:
    """Return the tier for ``total`` gibibytes of installed RAM.

    Args:
        total: Installed RAM. Below 24 GiB is still ``ram32``, and a warning
            is logged once.

    Returns:
        ``ram96`` at 88 GiB and above, ``ram64`` at 44 GiB and above, otherwise
        ``ram32``.
    """
    global _warned
    if total < _LOW_GIB and not _warned:
        _warned = True
        logger.warning("this machine has %.1f GiB of RAM; using ram32", total)
    if total >= _RAM96_MIN:
        return RamTier.RAM96
    if total >= _RAM64_MIN:
        return RamTier.RAM64
    return RamTier.RAM32


def ram_tier() -> RamTier:
    """Return the tier for this process.

    ``VOXPRINT_RAM_MODE`` wins when it is ``ram32``, ``ram64``, or ``ram96``.
    ``auto`` and a missing variable defer to ``memory.ram_mode``. ``auto``
    there, or a missing setting, uses :func:`tier_for_gib` on :func:`total_gib`.
    An unknown environment value is ignored.
    """
    env = os.environ.get(_ENV, "").strip().lower()
    if env and env != "auto":
        named = _named(env)
        if named is not None:
            return named
        logger.warning("ignoring %s=%s", _ENV, env)
    stored = ram_mode()
    if stored != "auto":
        named = _named(stored)
        if named is not None:
            return named
    return tier_for_gib(total_gib())


def pin_budget_gib(
    total: float,
    *,
    tier: RamTier | None = None,
    windows: bool | None = None,
) -> float:
    """Return how many gibibytes this process may pin.

    The budget is the smaller of a fraction of ``total`` and ``total`` minus
    12 GiB. The fraction is 0.5 on Windows and 0.6 elsewhere. ``ram64`` is
    also capped at 0.25 x ``total`` (12 GiB on 48 GiB, 16 GiB on 64 GiB).
    ``ram32`` is 0.

    Args:
        total: Installed RAM in gibibytes.
        tier: Tier to budget for. When omitted, :func:`tier_for_gib` is used.
        windows: Force the Windows fraction. When omitted, follow this OS.
    """
    chosen = tier_for_gib(total) if tier is None else tier
    if chosen is RamTier.RAM32:
        return 0.0
    on_windows = sys.platform == "win32" if windows is None else windows
    fraction = _WINDOWS_FRACTION if on_windows else _OTHER_FRACTION
    budget = min(fraction * total, total - _RESERVE_GIB)
    if chosen is RamTier.RAM64:
        budget = min(budget, _RAM64_FRACTION * total)
    return max(0.0, budget)


def pin_budget_bytes(
    total: float,
    *,
    tier: RamTier | None = None,
    windows: bool | None = None,
) -> int:
    """Return :func:`pin_budget_gib` in bytes, truncated toward zero.

    Args:
        total: Installed RAM in gibibytes.
        tier: Tier to budget for.
        windows: Force the Windows fraction.
    """
    return int(pin_budget_gib(total, tier=tier, windows=windows) * GIB)


def _named(value: str) -> RamTier | None:
    match value:
        case "ram32":
            return RamTier.RAM32
        case "ram64":
            return RamTier.RAM64
        case "ram96":
            return RamTier.RAM96
        case _:
            return None


def _memory_bytes() -> tuple[int, int]:
    if sys.platform == "win32":
        return _windows_memory()
    return _linux_memory()


def _linux_memory(path: Path | None = None) -> tuple[int, int]:
    text = (path or _MEMINFO).read_text(encoding="utf-8")
    total: int | None = None
    available: int | None = None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            total = _kib(line)
        elif line.startswith("MemAvailable:"):
            available = _kib(line)
    if total is None or available is None:
        raise OSError("meminfo has no MemTotal or MemAvailable")
    return total, available


def _kib(line: str) -> int:
    parts = line.split()
    return int(parts[1]) * 1024


def _windows_memory() -> tuple[int, int]:
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    return int(status.ullTotalPhys), int(status.ullAvailPhys)
