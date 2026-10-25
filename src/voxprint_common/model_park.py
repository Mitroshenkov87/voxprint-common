"""Keep model weights resident in RAM and move them to the GPU per stage.

Torch is imported only when a staging buffer is created. Install it with
``voxprint-common[torch]``. Importing this module does not import torch.

Each model is read from disk once and kept in host RAM while the tier's RAM
budget allows. :meth:`ModelPark.stage` copies the models a pipeline step needs
onto the GPU and sends the others back to RAM. Copies use pinned host memory
when ``pin_memory`` succeeds, and fall back to pageable memory when it does
not. A transfer never changes dtype: FP8 stays FP8.

``ram32`` loads from SSD when a stage asks and does not keep the weights.
``ram64`` keeps main models. ``ram96`` keeps every model. The VRAM budget
defaults to 16 GiB and leaves 1 GiB free. Least-recently-used VRAM residents
move back to RAM. They are not dropped to disk while the RAM budget allows.

One allocation is pinned only up to 2 GiB, or 4 GiB on ``ram96``. A larger
tensor stays pageable and is uploaded through a :class:`StagingRing` of two
256 MiB pinned buffers. ``pin_memory`` raising ``RuntimeError`` leaves that
tensor pageable and lowers this process's pin budget to the bytes already pinned.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
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
    "DEFAULT_VRAM_BUDGET",
    "VRAM_HEADROOM_BYTES",
    "Direction",
    "ModelPark",
    "ParkStats",
    "Parked",
    "Role",
    "StagingRing",
    "Transfer",
    "for_process",
    "reset_process_park",
]

logger = logging.getLogger(__name__)

MIB = 1024**2
STAGING_BYTES = 256 * MIB
STAGING_SLOTS = 2
CHUNK_BYTES = 2 * GIB
CHUNK_RAM96 = 4 * GIB
LOW_GIB = 6.0
RECOVER_GIB = 8.0
DEFAULT_VRAM_BUDGET = 16 * GIB
VRAM_HEADROOM_BYTES = GIB

Loader = Callable[[], Mapping[str, object]]

_process_park: ModelPark | None = None
_process_guard = threading.Lock()


class Role(StrEnum):
    """What a parked model is for."""

    MAIN = "main"
    AUX = "aux"
    OPTIONAL = "optional"


class Direction(StrEnum):
    """Which way a timed copy moved."""

    DISK_TO_RAM = "disk_to_ram"
    RAM_TO_VRAM = "ram_to_vram"
    VRAM_TO_RAM = "vram_to_ram"


@dataclass(frozen=True)
class Transfer:
    """One timed copy between disk, RAM, and VRAM.

    ``ms`` is the wall time of the copy. ``nbytes`` is the model's declared
    size. ``direction`` is ``disk_to_ram``, ``ram_to_vram``, or ``vram_to_ram``.
    """

    stage: str
    model: str
    direction: Direction
    nbytes: int
    ms: float


def _torch():
    """Import torch for the staging buffers.

    Torch is an optional extra. The import stays inside this function so the
    module loads, and CI runs, when torch is not installed.
    """
    import torch

    return torch


@dataclass(frozen=True)
class ParkStats:
    """A snapshot of resident bytes, disk reads, and copy timings.

    ``pinned``, ``pageable``, and ``disk`` are the host policy totals.
    ``ram_bytes`` is pinned plus pageable. ``vram_bytes`` is the GPU working
    set. ``disk_reads`` counts real loader calls per model. ``reloads`` counts
    ``get`` calls that found the model already parked and did not touch disk.
    ``timings`` is every copy recorded since this park was created.
    """

    pinned: int
    pageable: int
    disk: int
    fallbacks: int
    hits: int
    ram_bytes: int = 0
    vram_bytes: int = 0
    disk_reads: dict[str, int] = field(default_factory=dict)
    timings: tuple[Transfer, ...] = ()
    reloads: int = 0


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
    device_weights: dict[str, object] | None = None


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
        vram_budget: int | None = None,
    ) -> None:
        """Park weights for ``tier``.

        Args:
            tier: RAM tier. Pin budget is 0 on ``ram32``.
            app: Name recorded in the shared pin file.
            total: Installed RAM in GiB. When omitted, the machine is probed.
            windows: Force the Windows pin fraction. When omitted, follow this OS.
            vram_budget: GPU working-set cap in bytes. The default is 16 GiB.
                One GiB of that cap stays free. A smaller cap leaves a tenth free.
        """
        self.tier = tier
        self.app = app
        self._total = total
        self._windows = windows
        self._vram_budget = DEFAULT_VRAM_BUDGET if vram_budget is None else max(0, vram_budget)
        self._entries: dict[str, _Entry] = {}
        self._loaders: dict[str, Loader] = {}
        self._sizes: dict[str, int] = {}
        self._roles: dict[str, Role] = {}
        self._fallbacks = 0
        self._hits = 0
        self._reloads = 0
        self._disk_reads: dict[str, int] = {}
        self._timings: list[Transfer] = []
        self._lowered: int | None = None
        self._ring = StagingRing()
        self._lock = threading.Lock()

    @classmethod
    def for_process(
        cls,
        tier: RamTier | None = None,
        *,
        app: str = "voxprint",
        vram_budget: int | None = None,
    ) -> ModelPark:
        """Return this process's park, creating it on the first call.

        Args:
            tier: Tier to use when creating the park. When omitted, the
                detected or configured tier is used.
            app: Name recorded in the shared pin file.
            vram_budget: GPU cap used when this call creates the park.
                Later calls return the park that already exists.
        """
        global _process_park
        with _process_guard:
            if _process_park is None:
                chosen = ram_tier() if tier is None else tier
                _process_park = cls(chosen, app=app, vram_budget=vram_budget)
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
                self._reloads += 1
                current.refs += 1
                current.used = time.monotonic()
                logger.warning("model %s is already parked; skipped a disk read", key)
                return Parked(self, current)
            return Parked(self, self._materialise(key, loader, size_bytes, chosen))

    def ram_budget(self) -> int:
        """Return how many host bytes this tier keeps parked.

        ``ram32`` is 0, so a model is read from SSD when a stage asks and is
        not kept afterwards. ``ram64`` is the pin budget, which holds the main
        models. ``ram96`` is installed RAM minus 12 GiB, which holds every model.
        """
        total = self._installed()
        match self.tier:
            case RamTier.RAM32:
                return 0
            case RamTier.RAM64:
                return pin_budget_bytes(total, tier=self.tier, windows=self._windows)
            case RamTier.RAM96:
                return int(max(0.0, total - 12.0) * GIB)
            case _ as unreachable:
                assert_never(unreachable)

    def vram_limit(self) -> int:
        """Return the GPU bytes a stage may occupy.

        The default budget is 16 GiB and the limit is that budget minus 1 GiB.
        A budget of 1 GiB or less leaves a tenth of itself free.
        """
        budget = self._vram_budget
        if budget <= 0:
            return 0
        if budget > VRAM_HEADROOM_BYTES:
            return budget - VRAM_HEADROOM_BYTES
        return max(0, budget - max(1, budget // 10))

    @contextmanager
    def stage(
        self,
        name: str,
        models: Sequence[str],
        *,
        device: object = "cuda",
    ) -> Iterator[dict[str, dict[str, object]]]:
        """Unpark ``models`` onto ``device`` for one pipeline step.

        Models already in RAM are not read from disk. A model that was dropped
        is read once, through the loader registered by :meth:`get`. Other GPU
        residents move back to RAM. Least-recently-used GPU residents are
        evicted the same way when the requested set does not fit in
        :meth:`vram_limit`. Nothing is dropped to disk while :meth:`ram_budget`
        still keeps that model. Each copy is logged with its stage, model,
        direction, byte count, and milliseconds.

        The yielded dict is valid inside the block. On exit, and when entering
        the stage fails partway, the stage's models move back to RAM and the
        borrowed references are dropped.

        Args:
            name: Pipeline step, recorded on each timing.
            models: Keys to place on the GPU, in priority order.
            device: Torch device passed to ``tensor.to``. Tests may pass a
                stand-in; the default is ``cuda``.
        """
        borrowed: list[_Entry] = []
        try:
            with self._lock:
                placed = self._enter_stage(name, models, device, borrowed)
            yield placed
        finally:
            with self._lock:
                self._leave_stage(name, borrowed)

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
        """Return resident RAM and VRAM, disk reads per model, and copy timings."""
        with self._lock:
            pinned = pageable = disk = 0
            vram = 0
            for entry in self._entries.values():
                pinned += self._pinned_of(entry)
                pageable += self._pageable_of(entry)
                disk += self._disk_of(entry)
                if entry.device_weights:
                    vram += entry.size_bytes
            return ParkStats(
                pinned,
                pageable,
                disk,
                self._fallbacks,
                self._hits,
                ram_bytes=pinned + pageable,
                vram_bytes=vram,
                disk_reads=dict(self._disk_reads),
                timings=tuple(self._timings),
                reloads=self._reloads,
            )

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
                    copied = tensor.to(device, non_blocking=non_blocking)
                else:
                    copied = self._through_ring(tensor, device, non_blocking)
                moved[name] = _keep_dtype(tensor, copied)
            return moved

    def _materialise(self, key: str, loader: Loader, size_bytes: int, role: Role) -> _Entry:
        started = time.perf_counter()
        weights = dict(loader())
        self._disk_reads[key] = self._disk_reads.get(key, 0) + 1
        self._record("load", key, Direction.DISK_TO_RAM, size_bytes, started)
        entry = _Entry(key, role, size_bytes, weights, set(), 1, time.monotonic())
        if self._wants_pin(role):
            self._make_room(size_bytes)
            self._pin(entry)
        self._entries[key] = entry
        self._publish()
        self._pressure()
        return entry

    def _enter_stage(
        self,
        name: str,
        models: Sequence[str],
        device: object,
        borrowed: list[_Entry],
    ) -> dict[str, dict[str, object]]:
        wanted = list(dict.fromkeys(models))
        protect = set(wanted)
        for entry in list(self._entries.values()):
            if entry.device_weights and entry.key not in protect:
                self._offload(name, entry)
        placed: dict[str, dict[str, object]] = {}
        for key in wanted:
            existed = key in self._entries
            entry = self._resident_or_load(key)
            if entry is None:
                continue
            if existed:
                entry.refs += 1
            entry.used = time.monotonic()
            borrowed.append(entry)
            if self._seat(name, entry, device, protect):
                placed[key] = dict(entry.device_weights or {})
        return placed

    def _leave_stage(self, name: str, borrowed: list[_Entry]) -> None:
        changed = False
        for entry in borrowed:
            if entry.device_weights:
                self._offload(name, entry)
            current = self._entries.get(entry.key)
            if current is None or current.refs <= 0:
                continue
            current.refs -= 1
            if current.refs <= 0 and not self._keeps(current.role):
                self._entries.pop(current.key, None)
                changed = True
        if changed:
            self._publish()

    def _resident_or_load(self, key: str) -> _Entry | None:
        current = self._entries.get(key)
        if current is not None:
            return current
        loader = self._loaders.get(key)
        size = self._sizes.get(key)
        role = self._roles.get(key)
        if loader is None or size is None or role is None:
            logger.warning("model %s is not parked; the stage left it out", key)
            return None
        return self._materialise(key, loader, size, role)

    def _seat(self, name: str, entry: _Entry, device: object, protect: set[str]) -> bool:
        if entry.device_weights:
            return True
        limit = self.vram_limit()
        if entry.size_bytes > limit:
            logger.warning(
                "model %s needs %d bytes of VRAM and the limit is %d",
                entry.key,
                entry.size_bytes,
                limit,
            )
            return False
        self._make_vram_room(entry.size_bytes, protect, name)
        if self._vram_resident() + entry.size_bytes > limit:
            logger.warning(
                "model %s stayed in RAM; VRAM limit is %d bytes",
                entry.key,
                limit,
            )
            return False
        started = time.perf_counter()
        moved: dict[str, object] = {}
        for tensor_name, tensor in entry.weights.items():
            direct = tensor_name in entry.pinned and _nbytes(tensor) <= self.chunk
            if direct:
                copied = tensor.to(device, non_blocking=True)
            else:
                copied = self._through_ring(tensor, device, True)
            moved[tensor_name] = _keep_dtype(tensor, copied)
        entry.device_weights = moved
        entry.used = time.monotonic()
        self._record(name, entry.key, Direction.RAM_TO_VRAM, entry.size_bytes, started)
        return True

    def _offload(self, name: str, entry: _Entry) -> None:
        if not entry.device_weights:
            return
        started = time.perf_counter()
        nbytes = entry.size_bytes
        entry.device_weights = None
        entry.used = time.monotonic()
        self._record(name, entry.key, Direction.VRAM_TO_RAM, nbytes, started)

    def _make_vram_room(self, need: int, protect: set[str], name: str) -> None:
        while self._vram_resident() + need > self.vram_limit():
            victim = self._lru_vram(protect)
            if victim is None:
                return
            self._offload(name, victim)

    def _lru_vram(self, protect: set[str]) -> _Entry | None:
        seated = [
            entry
            for entry in self._entries.values()
            if entry.device_weights and entry.key not in protect
        ]
        if not seated:
            return None
        return min(seated, key=lambda entry: entry.used)

    def _vram_resident(self) -> int:
        return sum(entry.size_bytes for entry in self._entries.values() if entry.device_weights)

    def _record(
        self,
        stage: str,
        model: str,
        direction: Direction,
        nbytes: int,
        started: float,
    ) -> None:
        elapsed = (time.perf_counter() - started) * 1000.0
        self._timings.append(Transfer(stage, model, direction, nbytes, elapsed))
        logger.info(
            "park stage=%s model=%s direction=%s bytes=%d ms=%.3f",
            stage,
            model,
            direction.value,
            nbytes,
            elapsed,
        )

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


def for_process(
    tier: RamTier | None = None,
    *,
    app: str = "voxprint",
    vram_budget: int | None = None,
) -> ModelPark:
    """Return :meth:`ModelPark.for_process`.

    Args:
        tier: Tier used when the process park is created.
        app: Name recorded in the shared pin file.
        vram_budget: GPU cap used when this call creates the park.
    """
    return ModelPark.for_process(tier, app=app, vram_budget=vram_budget)


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


def _keep_dtype(source: object, moved: object) -> object:
    """Return ``moved`` when it has the same dtype as ``source``.

    A copy that would cast, including FP8 to another floating type, is refused.
    Tensors without a ``dtype`` attribute are returned unchanged.
    """
    before = getattr(source, "dtype", None)
    after = getattr(moved, "dtype", None)
    if before is not None and after is not None and before != after:
        raise TypeError(f"transfer changed dtype from {before!r} to {after!r}")
    return moved


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

    The destination is ``empty_like`` of the source, so the dtype is unchanged.
    The bytes move through a ``uint8`` view. FP8 stays FP8.
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
