"""Split a large file across several HTTP Range connections.

Segments are used only when the file is bigger than 256 MiB and the server
answers 206. Each part resumes from ``.partial.parts``. A mirror at or under
84 KiB/s is abandoned when another mirror is faster. Connections take a slot
from the shared budget and from that host's cap.
"""

from __future__ import annotations

import http.client
import json
import shutil
import ssl
import threading
import urllib.error
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
# Mid-file progress is published at least this often so the window and
# mdl-status.json move while Range parts are still open.
PROGRESS_INTERVAL_S = 1.0
PROGRESS_INTERVAL_BYTES = 8 * 1024 * 1024

Opener = Callable[..., object]
Clock = Callable[[], float]
OnBytes = Callable[[int, float], None]


class RangeIgnored(Exception):
    """The server sent HTTP 200 for a Range request."""


class SegmentFailed(Exception):
    """One segment failed, or the mirror is under the speed floor."""

    def __init__(self, message: str, *, slow: bool = False, status: int | None = None) -> None:
        """Record whether the caller should switch mirrors.

        Args:
            message: Short failure text.
            slow: True when throughput is at or under the floor.
            status: HTTP status, or None when the failure was not an HTTP response.
        """
        super().__init__(message)
        self.slow = slow
        self.status = status


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
    parts_dir = partial.with_name(partial.name + ".parts")
    # Keep the part layout already on disk so a later run with a different
    # planned segment count still resumes via HTTP Range from each part's
    # current size, and never rewrites a finished part under a new boundary.
    # The layout file records that count so trailing parts that were never
    # started are not mistaken for a shorter split.
    parts_dir.mkdir(parents=True, exist_ok=True)
    count = _resume_segment_count(parts_dir, max(2, segments), have, size)
    ranges = _ranges(have, size, count)
    part_paths = [parts_dir / f"{index:04d}" for index in range(len(ranges))]
    limiter = budget or ConnectionBudget(len(ranges), PER_HOST_CAP)
    started = clock()
    fresh = 0
    fresh_guard = threading.Lock()
    part_sizes = [_part_size(path) for path in part_paths]
    last_report = [0.0]
    last_bytes = [0]
    already = min(size, have + sum(part_sizes))
    on_bytes(already, 0.0)
    last_bytes[0] = already

    def _received() -> int:
        return min(size, have + sum(part_sizes))

    def _report(*, force: bool = False) -> None:
        moment = clock()
        received = _received()
        moved = received - last_bytes[0]
        if (
            not force
            and moment - last_report[0] < PROGRESS_INTERVAL_S
            and moved < PROGRESS_INTERVAL_BYTES
        ):
            return
        last_report[0] = moment
        last_bytes[0] = received
        elapsed = max(moment - started, 1e-6)
        on_bytes(received, fresh / elapsed)

    def _tick_part(index: int) -> Callable[[int], None]:
        def on_progress(stored: int) -> None:
            nonlocal fresh
            with fresh_guard:
                before = part_sizes[index]
                if stored > before:
                    fresh += stored - before
                part_sizes[index] = stored
            _report()

        return on_progress

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
            on_progress=_tick_part(index),
        )

    try:
        with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
            futures = [pool.submit(one, index) for index in range(len(ranges))]
            for future in futures:
                future.result()
                with fresh_guard:
                    received = _received()
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


def _part_size(path: Path) -> int:
    """Return the size of a part file, or 0 when it is missing."""
    return path.stat().st_size if path.is_file() else 0


def _resume_segment_count(parts_dir: Path, planned: int, prefix: int, size: int) -> int:
    """Return the segment count to resume with, and record it in the parts dir.

    A ``layout`` file written by an earlier run is authoritative. Without one,
    complete part sizes are compared to the planned ranges and to the count
    implied by existing part names, so a cancelled 8-way download that only
    created ``0000``…``0003`` is not resumed as a 4-way split, and a real
    4-way leftover is not rewritten under a new 8-way plan.
    """
    recorded = _read_layout(parts_dir)
    if recorded is not None:
        return recorded
    existing = _existing_part_count(parts_dir)
    if existing is None or existing == planned:
        count = planned
    else:
        count = _infer_segment_count(parts_dir, planned, existing, prefix, size)
    _write_layout(parts_dir, count)
    return count


def _read_layout(parts_dir: Path) -> int | None:
    """Return the recorded segment count, or None when the file is missing."""
    path = parts_dir / "layout"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text.startswith("{"):
            payload = json.loads(text)
            count = int(payload.get("segments") or 0)
        else:
            count = int(text)
    except OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError:
        return None
    return count if count >= 2 else None


def _write_layout(parts_dir: Path, count: int) -> None:
    """Record ``count`` so a later resume keeps the same Range layout."""
    try:
        (parts_dir / "layout").write_text(
            json.dumps({"segments": count}),
            encoding="utf-8",
        )
    except OSError:
        return


def _infer_segment_count(
    parts_dir: Path,
    planned: int,
    existing: int,
    prefix: int,
    size: int,
) -> int:
    """Choose planned vs existing when no layout file is present."""
    planned_ranges = _ranges(prefix, size, planned)
    existing_ranges = _ranges(prefix, size, existing)

    def _length(ranges: list[tuple[int, int]], index: int) -> int:
        start, end = ranges[index]
        return end - start + 1

    overflow_planned = False
    complete_planned = False
    complete_existing = False
    for index in range(max(planned, existing)):
        have = _part_size(parts_dir / f"{index:04d}")
        if not have:
            continue
        if index < len(planned_ranges) and have > _length(planned_ranges, index):
            overflow_planned = True
        if index < len(planned_ranges) and have == _length(planned_ranges, index):
            complete_planned = True
        if index < len(existing_ranges) and have == _length(existing_ranges, index):
            complete_existing = True
    if overflow_planned or complete_existing:
        return existing
    if complete_planned:
        return planned
    return existing


def _existing_part_count(parts_dir: Path) -> int | None:
    """Return the segment count implied by an existing ``.partial.parts`` dir.

    Part files are named ``0000``, ``0001``, … The count is taken from the
    highest index plus one, so a hole still keeps the original layout. A
    directory with fewer than two parts is ignored so a new plan can take over.
    """
    if not parts_dir.is_dir():
        return None
    highest = -1
    found = False
    for child in parts_dir.iterdir():
        if not child.is_file() or len(child.name) != 4 or not child.name.isdigit():
            continue
        found = True
        highest = max(highest, int(child.name))
    if not found or highest + 1 < 2:
        return None
    return highest + 1


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
    on_progress: Callable[[int], None] | None = None,
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
        except (OSError, TimeoutError, urllib.error.URLError, ssl.SSLError) as exc:
            raise SegmentFailed(str(exc)) from exc
        try:
            status = int(getattr(response, "status", 200) or 200)
            if status == 200:
                raise RangeIgnored(url)
            if status != 206:
                raise SegmentFailed(f"HTTP {status}", status=status)
            with part.open("ab" if have else "wb") as handle:
                while written < need - have:
                    if cancelled():
                        raise SegmentFailed("cancelled")
                    try:
                        block = response.read(min(chunk_size, need - have - written))
                    except (
                        OSError,
                        TimeoutError,
                        urllib.error.URLError,
                        ssl.SSLError,
                        http.client.IncompleteRead,
                    ) as exc:
                        raise SegmentFailed(str(exc)) from exc
                    if not block:
                        break
                    handle.write(block)
                    written += len(block)
                    if on_progress is not None:
                        on_progress(have + written)
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
