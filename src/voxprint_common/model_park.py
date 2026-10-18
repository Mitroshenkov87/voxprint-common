"""Keep model weights on disk, in pageable RAM, or in pinned RAM.

Torch is imported only when a staging buffer is created. Install it with
``voxprint-common[torch]``. A process that never calls :meth:`Parked.to`
does not import torch.

One allocation is pinned only up to ``max_pin_chunk`` (2 GiB, or 4 GiB on
``ram96``). A larger tensor stays pageable and is uploaded through a
:class:`StagingRing` of two 256 MiB pinned buffers. ``pin_memory`` raising
``RuntimeError`` (including CUDA out of memory) leaves that tensor pageable
and lowers this process's pin budget to the bytes already pinned.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from voxprint_common.atomic import exclusive_lock, write_json
from voxprint_common.gpu_lock import pid_exists
from voxprint_common.paths import state_dir
from voxprint_common.ram_probe import (
    GIB,
    RamTier,
    available_gib,
    pin_budget_bytes,
    ram_tier,
    total_gib,
)

__all__ = [
    "ModelPark",
    "ParkStats",
    "Parked",
    "Role",
    "StagingRing",
    "for_process",
    "reset_process_park",
]

MIB = 1024**2
STAGING_BYTES = 256 * MIB
STAGING_SLOTS = 2
CHUNK_BYTES = 2 * GIB
CHUNK_RAM96 = 4 * GIB
LOW_GIB = 6.0
RECOVER_GIB = 8.0

Loader = Callable[[], Mapping[str, object]]

_process_park: ModelPark | None = None
_process_guard = threading.Lock()


class Role(StrEnum):
    """What a parked model is for."""

    MAIN = "main"
    AUX = "aux"
    OPTIONAL = "optional"


def _torch():
    """Import torch for the staging buffers.

    Torch is an optional extra. The import stays inside this function so the
    module loads, and CI runs, when torch is not installed.
    """
    import torch

    return torch


@dataclass(frozen=True)
class ParkStats:
    """Bytes currently parked, plus pin fallbacks and cache hits."""

    pinned: int
    pageable: int
    disk: int
    fallbacks: int
    hits: int


class StagingRing:
    """Two pinned 256 MiB buffers, created on first use.

    The same ring is used on ``ram32``, ``ram64``, and ``ram96``. A failure
    to pin a buffer disables the ring for this process.
    """

    def __init__(self) -> None:
        """Start with no buffers allocated."""
        self._slots: tuple[object, ...] | None = None
        self._failed = False
        self._cursor = 0

    def slot(self) -> object | None:
        """Return the next pinned buffer, creating both on the first call.

        Returns:
            A pinned byte tensor, or None when the ring could not be pinned.
        """
        if self._failed:
            return None
        if self._slots is None:
            try:
                torch = _torch()
                self._slots = tuple(
                    torch.empty(STAGING_BYTES, dtype=torch.uint8, pin_memory=True)
                    for _ in range(STAGING_SLOTS)
                )
            except ImportError, OSError, RuntimeError:
                self._failed = True
                return None
        slot = self._slots[self._cursor % STAGING_SLOTS]
        self._cursor += 1
        return slot


@dataclass
class _Entry:
    key: str
    role: Role
    size_bytes: int
    weights: dict[str, object]
    pinned: set[str]
    refs: int
    used: float


class Parked:
    """One model held by the park, with an in-use refcount."""

    def __init__(self, park: ModelPark, entry: _Entry) -> None:
        """Bind ``entry`` to the park that owns it.

        Args:
            park: The process park.
            entry: The stored weights.
        """
        self._park = park
        self._entry = entry

    @property
    def key(self) -> str:
        """Return the key passed to :meth:`ModelPark.get`."""
        return self._entry.key

    def to(self, device: object, non_blocking: bool = True) -> dict[str, object]:
        """Return the state dict on ``device``.

        Pinned tensors at or under the chunk cap move with ``non_blocking``.
        Pageable tensors and oversized tensors go through the staging ring,
        then move with ``non_blocking`` set. When the ring cannot be created,
        the copy is blocking.

        Args:
            device: Torch device, such as ``cuda``.
            non_blocking: Ask the copy to overlap the host when the source is
                pinned or staged.
        """
        return self._park._move(self._entry, device, non_blocking)

    def release(self) -> None:
        """Drop one in-use reference. Parked weights stay until policy drops them."""
        self._park._release(self._entry)


class ModelPark:
    """The weights this process is keeping, and the pin budget it has left.

    :meth:`for_process` returns one park for the process. Further calls return
    that same object.
    """

    def __init__(
        self,
        tier: RamTier,
        *,
        app: str = "voxprint",
        total: float | None = None,
        windows: bool | None = None,
    ) -> None:
        """Park weights for ``tier``.

        Args:
            tier: RAM tier. Pin budget is 0 on ``ram32``.
            app: Name recorded in the shared pin file.
            total: Installed RAM in GiB. When omitted, the machine is probed.
            windows: Force the Windows pin fraction. When omitted, follow this OS.
        """
        self.tier = tier
        self.app = app
        self._total = total
        self._windows = windows
        self._entries: dict[str, _Entry] = {}
        self._loaders: dict[str, Loader] = {}
        self._sizes: dict[str, int] = {}
        self._roles: dict[str, Role] = {}
        self._fallbacks = 0
        self._hits = 0
        self._lowered: int | None = None
        self._ring = StagingRing()
        self._lock = threading.Lock()

    @classmethod
    def for_process(cls, tier: RamTier | None = None, *, app: str = "voxprint") -> ModelPark:
        """Return this process's park, creating it on the first call.

        Args:
            tier: Tier to use when creating the park. When omitted, the
                detected or configured tier is used.
            app: Name recorded in the shared pin file.
        """
        global _process_park
        with _process_guard:
            if _process_park is None:
                chosen = ram_tier() if tier is None else tier
                _process_park = cls(chosen, app=app)
            return _process_park

    @property
    def chunk(self) -> int:
        """Return the largest single pin, in bytes."""
        if self.tier is RamTier.RAM96:
            return CHUNK_RAM96
        return CHUNK_BYTES

    def get(self, key: str, loader: Loader, *, size_bytes: int, role: str) -> Parked:
        """Return ``key``, loading it when the park does not already hold it.

        Args:
            key: Stable id for this state dict.
            loader: Called when the weights are not parked. It returns a
                state dict and must not call back into the park.
            size_bytes: Declared size, used for the pin budget.
            role: ``main``, ``aux``, or ``optional``.

        Raises:
            ValueError: ``role`` is not one of those three.
        """
        chosen = _role(role)
        with self._lock:
            self._pressure()
            self._loaders[key] = loader
            self._sizes[key] = size_bytes
            self._roles[key] = chosen
            current = self._entries.get(key)
            if current is not None:
                self._hits += 1
                current.refs += 1
                current.used = time.monotonic()
                return Parked(self, current)
            weights = dict(loader())
            entry = _Entry(key, chosen, size_bytes, weights, set(), 1, time.monotonic())
            if self._wants_pin(chosen):
                self._make_room(size_bytes)
                self._pin(entry)
            self._entries[key] = entry
            self._publish()
            self._pressure()
            return Parked(self, entry)

    def prefetch(self, keys: Sequence[str]) -> None:
        """Load ``keys`` on a background thread.

        ``ram32`` does nothing. A key is loaded only after :meth:`get` has
        seen its loader. The background load drops its in-use reference, so
        the weights stay parked and can be evicted.

        Args:
            keys: Model ids to warm.
        """
        if self.tier is RamTier.RAM32:
            return
        names = tuple(keys)

        def warm() -> None:
            for name in names:
                with self._lock:
                    loader = self._loaders.get(name)
                    size = self._sizes.get(name)
                    role = self._roles.get(name)
                if loader is None or size is None or role is None:
                    continue
                parked = self.get(name, loader, size_bytes=size, role=role.value)
                parked.release()

        threading.Thread(target=warm, name="voxprint-ram-prefetch", daemon=True).start()

    def stats(self) -> ParkStats:
        """Return pinned, pageable, and disk bytes, plus fallbacks and hits."""
        with self._lock:
            pinned = pageable = disk = 0
            for entry in self._entries.values():
                pinned += self._pinned_of(entry)
                pageable += self._pageable_of(entry)
                disk += self._disk_of(entry)
            return ParkStats(pinned, pageable, disk, self._fallbacks, self._hits)

    def _wants_pin(self, role: Role) -> bool:
        match self.tier:
            case RamTier.RAM32:
                return False
            case RamTier.RAM64:
                return role is Role.MAIN
            case RamTier.RAM96:
                return True
            case _ as unreachable:
                assert_never(unreachable)

    def _keeps(self, role: Role) -> bool:
        match self.tier:
            case RamTier.RAM32:
                return False
            case RamTier.RAM64:
                return role is not Role.OPTIONAL
            case RamTier.RAM96:
                return True
            case _ as unreachable:
                assert_never(unreachable)

    def _pin(self, entry: _Entry) -> None:
        pending = 0
        room = self._room()
        for name, tensor in list(entry.weights.items()):
            need = _nbytes(tensor) or entry.size_bytes
            if need > self.chunk or need > room - pending:
                self._fallbacks += 1
                continue
            pin = getattr(tensor, "pin_memory", None)
            if not callable(pin):
                self._fallbacks += 1
                continue
            try:
                pinned = pin()
            except RuntimeError:
                self._fallbacks += 1
                self._lowered = self._pinned_bytes() + pending
                break
            entry.weights[name] = pinned
            entry.pinned.add(name)
            pending += need

    def _move(self, entry: _Entry, device: object, non_blocking: bool) -> dict[str, object]:
        with self._lock:
            moved: dict[str, object] = {}
            for name, tensor in entry.weights.items():
                direct = name in entry.pinned and _nbytes(tensor) <= self.chunk
                if direct:
                    moved[name] = tensor.to(device, non_blocking=non_blocking)
                    continue
                moved[name] = self._through_ring(tensor, device, non_blocking)
            return moved

    def _through_ring(self, tensor: object, device: object, non_blocking: bool) -> object:
        slot = self._ring.slot()
        if slot is None:
            return tensor.to(device, non_blocking=False)
        staged = _copy_through(slot, tensor, device, non_blocking)
        if staged is None:
            return tensor.to(device, non_blocking=non_blocking)
        return staged

    def _release(self, entry: _Entry) -> None:
        with self._lock:
            current = self._entries.get(entry.key)
            if current is None or current.refs <= 0:
                return
            current.refs -= 1
            if current.refs == 0 and not self._keeps(current.role):
                self._entries.pop(current.key, None)
                self._publish()

    def _pressure(self) -> None:
        if available_gib() >= LOW_GIB:
            return
        while available_gib() <= RECOVER_GIB:
            victim = self._lru()
            if victim is None:
                return
            self._step_down(victim)
        self._publish()

    def _make_room(self, need: int) -> None:
        while self._room() < need:
            victim = self._lru()
            if victim is None:
                return
            self._step_down(victim)

    def _step_down(self, entry: _Entry) -> None:
        if entry.pinned:
            for name in list(entry.pinned):
                tensor = entry.weights[name]
                entry.weights[name] = _as_pageable(tensor)
            entry.pinned.clear()
            return
        self._entries.pop(entry.key, None)

    def _lru(self) -> _Entry | None:
        free = [entry for entry in self._entries.values() if entry.refs <= 0]
        if not free:
            return None
        return min(free, key=lambda entry: entry.used)

    def _room(self) -> int:
        return max(0, self._limit() - _others_pinned() - self._pinned_bytes())

    def _limit(self) -> int:
        natural = pin_budget_bytes(self._installed(), tier=self.tier, windows=self._windows)
        if self._lowered is None:
            return natural
        return min(natural, self._lowered)

    def _installed(self) -> float:
        if self._total is not None:
            return self._total
        return total_gib()

    def _pinned_bytes(self) -> int:
        return sum(self._pinned_of(entry) for entry in self._entries.values())

    def _pinned_of(self, entry: _Entry) -> int:
        if not entry.pinned:
            return 0
        if len(entry.pinned) == len(entry.weights) and entry.weights:
            return entry.size_bytes
        return sum(_nbytes(entry.weights[name]) for name in entry.pinned)

    def _pageable_of(self, entry: _Entry) -> int:
        if not self._keeps(entry.role):
            return 0
        if entry.pinned and len(entry.pinned) == len(entry.weights):
            return 0
        if not entry.pinned:
            return entry.size_bytes
        return entry.size_bytes - self._pinned_of(entry)

    def _disk_of(self, entry: _Entry) -> int:
        if self._keeps(entry.role):
            return 0
        return entry.size_bytes

    def _publish(self) -> None:
        _publish_pin(self.app, self._pinned_bytes())


def for_process(tier: RamTier | None = None, *, app: str = "voxprint") -> ModelPark:
    """Return :meth:`ModelPark.for_process`.

    Args:
        tier: Tier used when the process park is created.
        app: Name recorded in the shared pin file.
    """
    return ModelPark.for_process(tier, app=app)


def reset_process_park() -> None:
    """Forget the park returned by :func:`for_process`.

    The next call creates a new one. Tests use this between cases.
    """
    global _process_park
    with _process_guard:
        _process_park = None


def _role(value: str) -> Role:
    try:
        return Role(value)
    except ValueError as exc:
        raise ValueError(f"role must be main, aux, or optional, got {value!r}") from exc


def _nbytes(tensor: object) -> int:
    value = getattr(tensor, "nbytes", 0)
    if callable(value):
        value = value()
    try:
        return int(value)
    except TypeError, ValueError:
        return 0


def _copy_through(
    slot: object, tensor: object, device: object, non_blocking: bool
) -> object | None:
    """Copy ``tensor`` through ``slot`` onto ``device``, or return None.

    None means the tensor has no torch storage. The caller then uses ``to``.
    """
    if not callable(getattr(tensor, "detach", None)):
        return None
    try:
        torch = _torch()
        source = tensor.detach().reshape(-1).view(torch.uint8)
        out = torch.empty_like(tensor, device=device)
        dest = out.view(torch.uint8).reshape(-1)
        total = int(source.numel())
        offset = 0
        while offset < total:
            count = min(STAGING_BYTES, total - offset)
            slot[:count].copy_(source[offset : offset + count])
            dest[offset : offset + count].copy_(slot[:count], non_blocking=non_blocking)
            offset += count
    except AttributeError, RuntimeError, TypeError, ValueError:
        return None
    return out


def _as_pageable(tensor: object) -> object:
    """Return a pageable copy of ``tensor``.

    ``to("cpu")`` returns a pinned CPU tensor unchanged, so a pinned torch
    tensor is copied into a new pageable buffer to release the pinned one.
    """
    pinned = getattr(tensor, "is_pinned", None)
    if callable(pinned):
        try:
            if pinned():
                torch = _torch()
                copy = torch.empty_like(tensor, device="cpu", pin_memory=False)
                copy.copy_(tensor)
                return copy
        except ImportError, AttributeError, RuntimeError, TypeError, ValueError:
            return tensor
    move = getattr(tensor, "to", None)
    if not callable(move):
        return tensor
    try:
        return move("cpu", non_blocking=False)
    except RuntimeError, TypeError, ValueError:
        return tensor


def _budget_path() -> Path:
    return state_dir() / "ram-park.json"


def _publish_pin(app: str, pinned_bytes: int, *, pid: int | None = None) -> None:
    """Record this process's pinned bytes and drop dead pids.

    A row is ``pid``, ``app``, and ``pinned_bytes``. A pid that
    :func:`voxprint_common.gpu_lock.pid_exists` rejects is removed. Rows have
    no expiry time: a live process keeps its reservation until it publishes
    a smaller total.
    """
    me = os.getpid() if pid is None else pid
    path = _budget_path()
    with exclusive_lock(path.with_name(path.name + ".lock")):
        rows = [row for row in _prune(_load(path)) if row.get("pid") != me]
        if pinned_bytes > 0:
            rows.append({"pid": me, "app": app, "pinned_bytes": int(pinned_bytes)})
        write_json(path, {"holders": rows})


def _others_pinned(pid: int | None = None) -> int:
    me = os.getpid() if pid is None else pid
    path = _budget_path()
    with exclusive_lock(path.with_name(path.name + ".lock")):
        rows = _prune(_load(path))
        write_json(path, {"holders": rows})
        total = 0
        for row in rows:
            if row.get("pid") == me:
                continue
            total += int(row.get("pinned_bytes") or 0)
        return total


def _load(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return []
    rows = payload.get("holders") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _prune(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    kept: list[dict[str, object]] = []
    for row in rows:
        pid = row.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or not pid_exists(pid):
            continue
        kept.append(row)
    return kept
