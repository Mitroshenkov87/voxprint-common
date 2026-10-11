"""Split a large file across several HTTP Range connections.

Segments are used only when the file is bigger than 256 MiB and the server
answers 206. Each part resumes from ``.partial.parts``. A mirror at or under
84 KiB/s is abandoned when another mirror is faster. Connections take a slot
from the shared budget and from that host's cap.
"""

from __future__ import annotations

import shutil
import threading
import urllib.request
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

__all__ = [
    "DEFAULT_SEGMENTS",
    "MAX_SEGMENTS",
    "PER_HOST_CAP",
    "SEGMENT_MIN_BYTES",
    "SPEED_FLOOR_BPS",
    "ConnectionBudget",
    "RangeIgnored",
    "SegmentFailed",
    "download_segments",
    "segment_count",
]

SEGMENT_MIN_BYTES = 256 * 1024 * 1024
DEFAULT_SEGMENTS = 4
MAX_SEGMENTS = 8
PER_HOST_CAP = 8
SPEED_FLOOR_BPS = 84 * 1024

Opener = Callable[..., object]
Clock = Callable[[], float]
OnBytes = Callable[[int, float], None]


class RangeIgnored(Exception):
    """The server sent HTTP 200 for a Range request."""


class SegmentFailed(Exception):
    """One segment failed, or the mirror is under the speed floor."""

    def __init__(self, message: str, *, slow: bool = False) -> None:
        """Record whether the caller should switch mirrors.

        Args:
            message: Short failure text.
            slow: True when throughput is at or under the floor.
        """
        super().__init__(message)
        self.slow = slow


class ConnectionBudget:
    """Cap in-flight connections for the whole job and for one host."""

    def __init__(self, total: int, per_host: int = PER_HOST_CAP) -> None:
        """Create the semaphores.

        Args:
            total: Connections for every file in this job.
            per_host: Connections to one host at a time.
        """
        self.total = max(1, total)
        self.per_host = max(1, per_host)
        self._total = threading.Semaphore(self.total)
        self._hosts: dict[str, threading.Semaphore] = {}
        self._guard = threading.Lock()

    @contextmanager
    def slot(self, host: str) -> Iterator[None]:
        """Hold one connection slot for ``host``."""
        with self._guard:
            host_slot = self._hosts.get(host)
            if host_slot is None:
                host_slot = threading.Semaphore(self.per_host)
                self._hosts[host] = host_slot
        self._total.acquire()
        host_slot.acquire()
        try:
            yield
        finally:
            host_slot.release()
            self._total.release()


def segment_count(size: int, file_count: int, parallel_files: int) -> int:
    """Return how many Range connections one file should open.

    Files of 256 MiB or less stay on one connection. A larger file uses 4 to
    8 connections, fewer when several files share the job.

    Args:
        size: File size in bytes.
        file_count: Files that will be fetched at the same time.
        parallel_files: Suite setting for how many files may run at once.
    """
    if size <= SEGMENT_MIN_BYTES:
        return 1
    total = min(MAX_SEGMENTS, max(DEFAULT_SEGMENTS, parallel_files * DEFAULT_SEGMENTS))
    return max(1, min(MAX_SEGMENTS, PER_HOST_CAP, total // max(1, file_count)))


def download_segments(
    url: str,
    partial: Path,
    size: int,
    segments: int,
    *,
    opener: Opener,
    timeout: float,
    chunk_size: int,
    clock: Clock,
    on_bytes: OnBytes,
    host: str,
    budget: ConnectionBudget | None,
    user_agent: str,
    speed_floor: float,
    other_mirrors: bool,
    cancelled: Callable[[], bool],
) -> None:
    """Fill ``partial`` from ``url`` using several Range requests.

    An existing ``partial`` prefix is kept. Part files under
    ``partial.parts`` are reused when their length already matches.

    Args:
        url: Mirror URL.
        partial: The ``.partial`` file beside the destination.
        size: Exact size in bytes.
        segments: How many ranges to open. Values below 2 do nothing useful.
        opener: ``opener(request, timeout) -> response``.
        timeout: Socket timeout.
        chunk_size: Read size.
        clock: Monotonic clock.
        on_bytes: ``(received, bps)`` for the whole file.
        host: Host name used for the connection cap.
        budget: Shared connection budget. A private one is used when omitted.
        user_agent: HTTP User-Agent.
        speed_floor: Bytes per second. At or under this, and with another
            mirror available, the transfer stops so the caller can switch.
        other_mirrors: Whether a different mirror can take over.
        cancelled: Returns true when the caller asked to stop.

    Raises:
        RangeIgnored: The server did not honor Range.
        SegmentFailed: A part failed, or the mirror is too slow.
    """
    have = partial.stat().st_size if partial.is_file() else 0
    if have > size:
        partial.unlink()
        have = 0
    if have >= size:
        on_bytes(size, 0.0)
        return
    ranges = _ranges(have, size, max(2, segments))
    parts_dir = partial.with_name(partial.name + ".parts")
    parts_dir.mkdir(parents=True, exist_ok=True)
    part_paths = [parts_dir / f"{index:04d}" for index in range(len(ranges))]
    limiter = budget or ConnectionBudget(len(ranges), PER_HOST_CAP)
    started = clock()
    fresh = 0
    fresh_guard = threading.Lock()

    def one(index: int) -> int:
        start, end = ranges[index]
        return _fetch_part(
            url,
            part_paths[index],
            start,
            end,
            opener,
            timeout,
            chunk_size,
            host,
            limiter,
            user_agent,
            cancelled,
        )

    try:
        with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
            futures = [pool.submit(one, index) for index in range(len(ranges))]
            for future in futures:
                count = future.result()
                with fresh_guard:
                    fresh += count
                    received = min(size, have + fresh)
                elapsed = max(clock() - started, 1e-6)
                on_bytes(received, fresh / elapsed)
    except RangeIgnored:
        shutil.rmtree(parts_dir, ignore_errors=True)
        raise
    except SegmentFailed:
        raise
    elapsed = max(clock() - started, 1e-6)
    bps = fresh / elapsed if fresh else speed_floor + 1
    if other_mirrors and fresh > 0 and bps <= speed_floor:
        raise SegmentFailed(f"{host} at {bps:.0f} B/s", slow=True)
    _assemble(partial, have, part_paths)
    shutil.rmtree(parts_dir, ignore_errors=True)
    on_bytes(size, bps)


def _ranges(start: int, size: int, segments: int) -> list[tuple[int, int]]:
    span = size - start
    count = max(1, min(segments, span))
    ranges: list[tuple[int, int]] = []
    for index in range(count):
        begin = start + (span * index) // count
        end = start + (span * (index + 1)) // count - 1
        if end >= begin:
            ranges.append((begin, end))
    return ranges


def _fetch_part(
    url: str,
    part: Path,
    start: int,
    end: int,
    opener: Opener,
    timeout: float,
    chunk_size: int,
    host: str,
    budget: ConnectionBudget,
    user_agent: str,
    cancelled: Callable[[], bool],
) -> int:
    need = end - start + 1
    have = part.stat().st_size if part.is_file() else 0
    if have == need:
        return 0
    if have > need:
        part.unlink()
        have = 0
    headers = {"User-Agent": user_agent, "Range": f"bytes={start + have}-{end}"}
    request = urllib.request.Request(url, headers=headers, method="GET")
    written = 0
    with budget.slot(host):
        if cancelled():
            raise SegmentFailed("cancelled")
        try:
            response = opener(request, timeout=timeout)
        except OSError as exc:
            raise SegmentFailed(str(exc)) from exc
        try:
            status = int(getattr(response, "status", 200) or 200)
            if status == 200:
                raise RangeIgnored(url)
            if status != 206:
                raise SegmentFailed(f"HTTP {status}")
            with part.open("ab" if have else "wb") as handle:
                while written < need - have:
                    if cancelled():
                        raise SegmentFailed("cancelled")
                    block = response.read(min(chunk_size, need - have - written))
                    if not block:
                        break
                    handle.write(block)
                    written += len(block)
                handle.flush()
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()
    got = part.stat().st_size if part.is_file() else 0
    if got != need:
        raise SegmentFailed(f"part ended at {got} of {need} bytes")
    return written


def _assemble(partial: Path, prefix: int, parts: list[Path]) -> None:
    assembled = partial.with_name(partial.name + ".assembling")
    try:
        with assembled.open("wb") as handle:
            if prefix and partial.is_file():
                with partial.open("rb") as source:
                    remaining = prefix
                    while remaining:
                        block = source.read(min(1024 * 1024, remaining))
                        if not block:
                            break
                        handle.write(block)
                        remaining -= len(block)
            for part in parts:
                with part.open("rb") as source:
                    shutil.copyfileobj(source, handle, length=1024 * 1024)
        assembled.replace(partial)
    finally:
        assembled.unlink(missing_ok=True)
