"""NVIDIA GPU probe and the suite VRAM tiers.

Tiers use gibibytes (1024-based), because a 24 GB card reports a little under
24 GiB through nvidia-smi:

* ``8gb``: below 15.5 GiB
* ``16gb``: at least 15.5 GiB and below 23.5 GiB
* ``24gb``: at least 23.5 GiB

Messages take the app's name. This module does not refuse to start on compute
capability alone unless the caller passes ``refuse_below``.
"""

from __future__ import annotations

import math
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "MIN_COMPUTE",
    "MIN_DRIVER_MAJOR",
    "TIER_16_MIN_GIB",
    "TIER_24_MIN_GIB",
    "GpuDevice",
    "Probe",
    "VramTier",
    "assess",
    "capability_warning",
    "driver_major",
    "gib_from_mib",
    "no_gpu_message",
    "old_driver_message",
    "parse_compute_capability",
    "parse_gpu_table",
    "query_gpus",
    "tier_for_gib",
]

MIN_COMPUTE = (8, 9)
MIN_DRIVER_MAJOR = 600
TIER_16_MIN_GIB = 15.5
TIER_24_MIN_GIB = 23.5

_QUERY = [
    "nvidia-smi",
    "--query-gpu=name,driver_version,compute_cap,memory.total",
    "--format=csv,noheader,nounits",
]
Runner = Callable[..., subprocess.CompletedProcess[str]]


class VramTier(StrEnum):
    """Card size used to choose a model plan."""

    GB8 = "8gb"
    GB16 = "16gb"
    GB24 = "24gb"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GpuDevice:
    """One GPU as nvidia-smi reported it."""

    index: int
    name: str
    driver: str
    compute: tuple[int, int] | None
    memory_mib: int | None

    @property
    def memory_gib(self) -> float | None:
        """Total memory in gibibytes, or None when nvidia-smi omitted it."""
        if self.memory_mib is None:
            return None
        return gib_from_mib(self.memory_mib)

    @property
    def tier(self) -> VramTier:
        """VRAM tier for this card."""
        return tier_for_gib(self.memory_gib)


@dataclass(frozen=True)
class Probe:
    """Whether an app may start, plus the devices nvidia-smi reported."""

    ok: bool
    message: str
    warning: str
    devices: tuple[GpuDevice, ...]
    device: GpuDevice | None


def gib_from_mib(mib: float) -> float:
    """Convert mebibytes to gibibytes.

    Args:
        mib: Memory reported by nvidia-smi in MiB.
    """
    return float(mib) / 1024.0


def tier_for_gib(total_gib: float | None) -> VramTier:
    """Return the suite tier for a card of ``total_gib`` gibibytes.

    Args:
        total_gib: Total VRAM. None, zero, or NaN is :attr:`VramTier.UNKNOWN`.
    """
    if total_gib is None:
        return VramTier.UNKNOWN
    try:
        size = float(total_gib)
    except TypeError, ValueError:
        return VramTier.UNKNOWN
    if math.isnan(size) or size <= 0:
        return VramTier.UNKNOWN
    if size >= TIER_24_MIN_GIB:
        return VramTier.GB24
    if size >= TIER_16_MIN_GIB:
        return VramTier.GB16
    return VramTier.GB8


def no_gpu_message(app: str) -> str:
    """Return the sentence shown when CUDA cannot see a GPU.

    Args:
        app: Display name of the app that is about to exit.
    """
    name = _name(app)
    return (
        f"{name} needs an NVIDIA GPU that CUDA can use. "
        f"This computer does not have one, so {name} will not start."
    )


def old_driver_message(app: str, driver: str) -> str:
    """Return the sentence shown when the driver is older than the 600 series.

    Args:
        app: Display name of the app.
        driver: Driver version string from nvidia-smi.
    """
    name = _name(app)
    shown = driver.strip() or "unknown"
    return (
        f"{name} needs an NVIDIA driver from the 600 series or newer. "
        f"This computer has driver {shown}. Update the driver, then start {name} again."
    )


def capability_warning(app: str, cap: tuple[int, int] | None) -> str:
    """Return a warning when the card is below compute capability 8.9.

    Args:
        app: Display name of the app.
        cap: ``(major, minor)``, or None when the card did not report one.

    Returns:
        An empty string when the card is 8.9 or newer, or when the capability
        is unknown.
    """
    if cap is None or cap >= MIN_COMPUTE:
        return ""
    major, minor = cap
    return (
        f"GPU compute capability {major}.{minor} is below 8.9 (RTX 40 series). "
        f"{_name(app)} is built for RTX 40 series and newer."
    )


def driver_major(version: str) -> int | None:
    """Return the major number of an NVIDIA driver version.

    Args:
        version: Text such as ``610.73.01``.

    Returns:
        The major component, or None when the text is not a driver version.
    """
    head = version.strip().split(".", 1)[0]
    if not head.isdigit():
        return None
    return int(head)


def parse_compute_capability(text: str, index: int = 0) -> tuple[int, int] | None:
    """Parse ``compute_cap`` CSV from nvidia-smi.

    Args:
        text: Command stdout.
        index: GPU row. Out of range selects row 0.

    Returns:
        ``(major, minor)``, or None when that row has no number.
    """
    rows: list[tuple[int, int]] = []
    for line in text.splitlines():
        cell = line.split(",")[0].strip()
        major_text, dot, minor_text = cell.partition(".")
        if not dot:
            continue
        try:
            rows.append((int(major_text), int(minor_text)))
        except ValueError:
            continue
    if not rows:
        return None
    if index < 0 or index >= len(rows):
        index = 0
    return rows[index]


def parse_gpu_table(text: str) -> list[GpuDevice]:
    """Parse ``name, driver_version, compute_cap, memory.total`` CSV.

    The name may itself contain commas. Memory is MiB because the query uses
    ``nounits``.

    Args:
        text: nvidia-smi stdout.
    """
    devices: list[GpuDevice] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        name = ", ".join(parts[:-3]).strip()
        driver = parts[-3].strip()
        if not name or not driver or driver.upper() == "N/A":
            continue
        devices.append(
            GpuDevice(
                index=len(devices),
                name=name,
                driver=driver,
                compute=_compute(parts[-2]),
                memory_mib=_memory(parts[-1]),
            )
        )
    return devices


def query_gpus(runner: Runner | None = None) -> list[GpuDevice]:
    """Ask nvidia-smi for every GPU.

    Args:
        runner: Replacement for ``subprocess.run``, used by tests.

    Returns:
        An empty list when the tool is missing, times out, or fails.
    """
    run = runner or subprocess.run
    try:
        if os.name == "nt":
            completed = run(
                _QUERY,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
        else:
            completed = run(_QUERY, capture_output=True, text=True, timeout=15, check=False)
    except OSError, subprocess.SubprocessError:
        return []
    if completed.returncode != 0:
        return []
    return parse_gpu_table(completed.stdout or "")


def assess(
    app: str,
    *,
    index: int = 0,
    runner: Runner | None = None,
    refuse_below: tuple[int, int] | None = None,
    devices: Sequence[GpuDevice] | None = None,
) -> Probe:
    """Decide whether ``app`` may start.

    Args:
        app: Display name used in the sentences.
        index: GPU row.
        runner: Replacement for ``subprocess.run``. Ignored when ``devices``
            is passed.
        refuse_below: When set, a missing capability or a card below this pair
            refuses to start. When omitted, a card below 8.9 still starts and
            the warning is the only extra signal.
        devices: Already parsed GPUs. When omitted, nvidia-smi is queried.

    Returns:
        The gate result. ``device`` is the selected GPU when there is one.
    """
    found = tuple(devices) if devices is not None else tuple(query_gpus(runner))
    if not found:
        return Probe(False, no_gpu_message(app), "", found, None)
    if index < 0 or index >= len(found):
        index = 0
    gpu = found[index]
    major = driver_major(gpu.driver)
    if major is None or major < MIN_DRIVER_MAJOR:
        return Probe(False, old_driver_message(app, gpu.driver), "", found, gpu)
    if refuse_below is not None and (gpu.compute is None or gpu.compute < refuse_below):
        return Probe(False, _below_message(gpu, refuse_below), "", found, gpu)
    return Probe(True, "", capability_warning(app, gpu.compute), found, gpu)


def _name(app: str) -> str:
    text = app.strip()
    return text or "This program"


def _compute(text: str) -> tuple[int, int] | None:
    major_text, dot, minor_text = text.partition(".")
    if not dot:
        return None
    try:
        return (int(major_text), int(minor_text))
    except ValueError:
        return None


def _memory(text: str) -> int | None:
    head = text.strip().split()[0] if text.strip() else ""
    if not head.isdigit():
        return None
    return int(head)


def _below_message(gpu: GpuDevice, floor: tuple[int, int]) -> str:
    shown = f"{floor[0]}.{floor[1]}"
    if gpu.compute is None:
        return (
            "This GPU did not report a compute capability. "
            f"This program starts only on compute capability {shown} (RTX 40 series) or newer."
        )
    got = f"{gpu.compute[0]}.{gpu.compute[1]}"
    return (
        f"GPU compute capability {got} is below {shown} (RTX 40 series). "
        "This program starts only on RTX 40 series or newer."
    )
