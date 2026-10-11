"""Resumable download with backoff, mirror speed, and an hf_xet path.

Mirrors are probed with a short ranged GET. The fastest one is used for the
next batch. A failure keeps the partial file, backs that mirror off, and
moves to another. When a backed-off mirror recovers and measures faster, the
next batch switches back to it. Hugging Face URLs use ``hf_xet`` when the
package is installed, ``HF_HUB_DISABLE_XET`` is not set, and the response
carries ``X-Xet-Hash``. Any other mirror, or a missing package, uses HTTP
Range.
"""

from __future__ import annotations

import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from voxprint_common import __version__
from voxprint_common.atomic import replace
from voxprint_common.hashing import HASH_RE, HashMismatch, verify
from voxprint_common.mdl.events import TransferCancelled, TransferSession
from voxprint_common.mdl.safety import UnsafeUrl, UrlGuard
from voxprint_common.mdl.segments import (
    SEGMENT_MIN_BYTES,
    SPEED_FLOOR_BPS,
    ConnectionBudget,
    RangeIgnored,
    SegmentFailed,
    download_segments,
)

__all__ = [
    "PROBE_HEADER",
    "Cancelled",
    "FetchError",
    "FetchProgress",
    "confirm_token",
    "fetch_file",
]

PROBE_HEADER = "X-Voxprint-Probe"
_USER_AGENT = f"voxprint-mdl/{__version__}"
_CHUNK = 256 * 1024
_HF_HOSTS = {"huggingface.co", "hf.co"}

Opener = Callable[..., object]
ProgressFn = Callable[["FetchProgress"], None]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class FetchError(Exception):
    """Every mirror failed, or the arguments are invalid."""

    def __init__(self, message: str, failures: Sequence[str] = ()) -> None:
        """Store the summary and the per-mirror lines.

        Args:
            message: Summary shown to the user.
            failures: One line per failed attempt.
        """
        self.failures = tuple(failures)
        super().__init__(message)


class Cancelled(FetchError):
    """The caller set the cancel event."""


@dataclass(frozen=True)
class FetchProgress:
    """One progress notice during a transfer."""

    source: str
    received: int
    total: int
    bps: float


@dataclass
class _Mirror:
    url: str
    bps: float | None = None
    failures: int = 0
    down_until: float = 0.0
    poisoned: bool = False
    batch_bytes: int = 0
    batch_seconds: float = 0.0


@dataclass
class _Offer:
    digest: str
    endpoint: str = ""


@dataclass
class _Pull:
    mirrors: list[_Mirror] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def fetch_file(
    destination: Path,
    urls: Sequence[str],
    *,
    sha256: str,
    size: int,
    progress: ProgressFn | None = None,
    timeout: float = 60.0,
    chunk_size: int = _CHUNK,
    probe_bytes: int = 64 * 1024,
    batch_bytes: int = 8 * 1024 * 1024,
    backoff_base: float = 0.5,
    backoff_max: float = 30.0,
    switch_ratio: float = 1.25,
    max_failures: int = 5,
    opener: Opener | None = None,
    clock: Clock | None = None,
    sleep: Sleeper | None = None,
    cancel: threading.Event | None = None,
    use_xet: bool = True,
    xet_module: object | None = None,
    guard: UrlGuard | None = None,
    control: TransferSession | None = None,
    segments: int = 1,
    segment_min: int = SEGMENT_MIN_BYTES,
    budget: ConnectionBudget | None = None,
    speed_floor: float = SPEED_FLOOR_BPS,
) -> Path:
    """Download ``destination`` from the fastest mirror whose bytes match ``sha256``.

    Args:
        destination: Finished file. The partial file is ``<name>.partial``
            beside it.
        urls: HTTP(S) mirror URLs.
        sha256: Expected digest.
        size: Exact size in bytes.
        progress: Called as bytes are written.
        timeout: Socket timeout for each open.
        chunk_size: Read size.
        probe_bytes: Bytes sampled to measure a mirror. ``0`` skips the probe
            and keeps the mirror order.
        batch_bytes: Bytes fetched from one mirror before the others are
            measured again.
        backoff_base: Seconds to wait after the first failure of a mirror.
        backoff_max: Cap on the exponential backoff.
        switch_ratio: Another mirror is used when its throughput is at least
            this multiple of the current mirror.
        max_failures: Network failures before a mirror is abandoned.
        opener: ``opener(request, timeout) -> response``. Defaults to urllib.
        clock: Monotonic clock. Defaults to :func:`time.monotonic`.
        sleep: Wait used for backoff and cancellation. Defaults to
            :func:`time.sleep`.
        cancel: Set to stop the transfer.
        use_xet: Try ``hf_xet`` for Hugging Face URLs that offer it.
        xet_module: Replacement for the ``hf_xet`` import, used by tests.
        guard: Host, scheme, and address rules. Defaults to HTTPS on the
            allowlist. Tests pass :meth:`UrlGuard.loopback`.
        control: Pause, resume, and cancel hooks. When omitted, the transfer
            runs until it finishes or ``cancel`` is set.
        segments: Range connections for a file larger than ``segment_min``.
            ``1`` keeps a single connection.
        segment_min: Size in bytes above which segments are used. The default
            is 256 MiB.
        budget: Shared connection cap. Segment transfers take one slot each.
        speed_floor: A mirror at or under this many bytes per second is
            dropped when another mirror is faster. The default is 84 KiB/s.

    Returns:
        ``destination`` after the digest has been checked.

    Raises:
        Cancelled: ``cancel`` was set.
        FetchError: No mirror produced the expected digest, or the arguments
            are invalid.
    """
    destination = Path(destination)
    digest = sha256.strip().lower()
    if HASH_RE.fullmatch(digest) is None:
        raise FetchError("sha256 must be 64 hexadecimal characters")
    if size < 0:
        raise FetchError("size must be zero or positive")
    if chunk_size <= 0 or batch_bytes <= 0:
        raise FetchError("chunk_size and batch_bytes must be positive")
    if switch_ratio < 1:
        raise FetchError("switch_ratio must be at least 1")
    policy = guard if guard is not None else UrlGuard()
    try:
        checked = [policy.check(url) for url in urls]
    except UnsafeUrl as exc:
        raise FetchError(str(exc)) from exc
    if not checked:
        raise FetchError("at least one mirror URL is required")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        try:
            verify(destination, digest)
        except HashMismatch:
            destination.unlink()
        else:
            _notify(progress, _host(checked[0]), size, size, 0.0)
            return destination
    open_url = opener or _guarded_opener(policy)
    now = clock or time.monotonic
    pause = sleep or time.sleep
    partial = destination.with_name(destination.name + ".partial")
    have = _partial_size(partial)
    if have > size:
        partial.unlink()
        have = 0
    state = _Pull([_Mirror(url) for url in checked])
    current: _Mirror | None = None
    xet_tried: set[str] = set()
    while True:
        while have < size:
            _checkpoint(control)
            _raise_if_cancelled(cancel)
            if all(mirror.poisoned for mirror in state.mirrors):
                _give_up(destination.name, state.failures)
            moment = now()
            waiting = [mirror for mirror in state.mirrors if not mirror.poisoned]
            ready = [mirror for mirror in waiting if mirror.down_until <= moment]
            if not ready:
                _interruptible(min(mirror.down_until for mirror in waiting) - moment, pause, cancel)
                continue
            _measure(
                ready,
                have,
                size,
                probe_bytes,
                open_url,
                timeout,
                chunk_size,
                now,
                state,
                backoff_base,
                backoff_max,
                max_failures,
                cancel,
            )
            chosen = _pick(state.mirrors, current, now(), switch_ratio, speed_floor)
            if chosen is None:
                continue
            if (
                use_xet
                and have == 0
                and chosen.url not in xet_tried
                and _is_huggingface(chosen.url)
            ):
                xet_tried.add(chosen.url)
                if _try_xet(
                    chosen.url,
                    partial,
                    size,
                    open_url,
                    timeout,
                    progress,
                    xet_module,
                ):
                    have = _partial_size(partial)
                    current = chosen
                    if have >= size:
                        break
            try:
                if segments > 1 and size > segment_min:
                    try:
                        download_segments(
                            chosen.url,
                            partial,
                            size,
                            segments,
                            opener=open_url,
                            timeout=timeout,
                            chunk_size=chunk_size,
                            clock=now,
                            on_bytes=lambda received, bps, source=chosen.url: _notify(
                                progress, _host(source), received, size, bps
                            ),
                            host=_host(chosen.url),
                            budget=budget,
                            user_agent=_USER_AGENT,
                            speed_floor=speed_floor,
                            other_mirrors=len(state.mirrors) > 1,
                            cancelled=lambda: _stop_requested(cancel, control),
                        )
                        have = _partial_size(partial)
                        current = chosen
                        continue
                    except RangeIgnored:
                        pass
                _pull(
                    chosen,
                    partial,
                    have,
                    size,
                    batch_bytes,
                    open_url,
                    timeout,
                    chunk_size,
                    now,
                    progress,
                    cancel,
                    control,
                )
            except SegmentFailed as exc:
                have = _partial_size(partial)
                state.failures.append(f"{chosen.url}: {exc}")
                _mark_down(chosen, now(), backoff_base, backoff_max, max_failures)
                current = None
                continue
            except _SourceFailed as exc:
                have = _partial_size(partial)
                state.failures.append(f"{chosen.url}: {exc}")
                _mark_down(chosen, now(), backoff_base, backoff_max, max_failures)
                current = None
                continue
            have = _partial_size(partial)
            if chosen.batch_seconds > 0:
                chosen.bps = chosen.batch_bytes / chosen.batch_seconds
            chosen.failures = 0
            chosen.down_until = 0.0
            current = chosen
            for other in state.mirrors:
                if other is not chosen and not other.poisoned:
                    other.bps = None
        try:
            verify(partial, digest)
        except HashMismatch as exc:
            partial.unlink(missing_ok=True)
            have = 0
            if current is not None:
                current.poisoned = True
                current.bps = None
                state.failures.append(f"{current.url}: {exc.found} does not match {exc.expected}")
            else:
                state.failures.append(str(exc))
            current = None
            if all(mirror.poisoned for mirror in state.mirrors):
                _give_up(destination.name, state.failures)
            continue
        replace(partial, destination)
        _notify(progress, _host((current or state.mirrors[0]).url), size, size, 0.0)
        return destination


def confirm_token(html: str) -> str | None:
    """Return a Google Drive confirm token found in an HTML warning page.

    Args:
        html: Response body.

    Returns:
        The token, or None when the page has no confirm field.
    """
    match = re.search(r"confirm=([0-9A-Za-z_-]+)", html)
    if match is None:
        return None
    return match.group(1)


def _measure(
    ready: list[_Mirror],
    have: int,
    size: int,
    probe_bytes: int,
    opener: Opener,
    timeout: float,
    chunk_size: int,
    clock: Clock,
    state: _Pull,
    backoff_base: float,
    backoff_max: float,
    max_failures: int,
    cancel: threading.Event | None,
) -> None:
    for mirror in ready:
        if mirror.bps is not None:
            continue
        _raise_if_cancelled(cancel)
        try:
            mirror.bps = _probe(
                mirror.url, have, size, probe_bytes, opener, timeout, chunk_size, clock
            )
        except _SourceFailed as exc:
            state.failures.append(f"{mirror.url}: {exc}")
            _mark_down(mirror, clock(), backoff_base, backoff_max, max_failures)


def _probe(
    url: str,
    have: int,
    size: int,
    probe_bytes: int,
    opener: Opener,
    timeout: float,
    chunk_size: int,
    clock: Clock,
) -> float:
    if probe_bytes <= 0 or have >= size:
        return 1.0
    want = min(probe_bytes, size - have)
    headers = {
        "User-Agent": _USER_AGENT,
        "Range": f"bytes={have}-{have + want - 1}",
        PROBE_HEADER: "1",
    }
    started = clock()
    response = _open(url, headers, opener, timeout)
    try:
        status = _status(response)
        if status >= 400:
            raise _SourceFailed(f"HTTP {status}")
        received = 0
        while received < want:
            block = _read(response, min(chunk_size, want - received))
            if not block:
                break
            received += len(block)
    finally:
        _close(response)
    elapsed = max(clock() - started, 1e-6)
    if received <= 0:
        raise _SourceFailed("probe returned no bytes")
    return received / elapsed


def _pull(
    mirror: _Mirror,
    partial: Path,
    have: int,
    size: int,
    batch_bytes: int,
    opener: Opener,
    timeout: float,
    chunk_size: int,
    clock: Clock,
    progress: ProgressFn | None,
    cancel: threading.Event | None,
    control: TransferSession | None = None,
) -> None:
    url = _prepare(mirror.url, opener, timeout)
    headers = {"User-Agent": _USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    started = clock()
    response = _open(url, headers, opener, timeout)
    written = 0
    try:
        status = _status(response)
        if status == 416 and have == size:
            mirror.batch_bytes = 0
            mirror.batch_seconds = 0.0
            return
        if status >= 400:
            raise _SourceFailed(f"HTTP {status}")
        restart = status != 206
        if restart:
            if partial.exists():
                partial.unlink()
            cursor = 0
        else:
            cursor = have
        mode = "wb" if restart else "ab"
        target = min(size, cursor + batch_bytes)
        with partial.open(mode) as handle:
            while cursor + written < target:
                _checkpoint(control)
                _raise_if_cancelled(cancel)
                block = _read(response, min(chunk_size, target - cursor - written))
                if not block:
                    break
                handle.write(block)
                written += len(block)
                _notify(
                    progress, _host(url), cursor + written, size, _rate(written, clock() - started)
                )
            handle.flush()
        if cursor + written < target and cursor + written < size:
            raise _SourceFailed(f"ended at {cursor + written} of {size} bytes")
    finally:
        _close(response)
    mirror.batch_bytes = written
    mirror.batch_seconds = max(clock() - started, 1e-6)


def _prepare(url: str, opener: Opener, timeout: float) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "drive.google.com" not in host:
        return url
    headers = {"User-Agent": _USER_AGENT}
    response = _open(url, headers, opener, timeout)
    try:
        kind = _header(getattr(response, "headers", {}), "Content-Type") or ""
        if "html" not in kind.lower():
            return url
        body = _read(response, 256 * 1024)
    finally:
        _close(response)
    token = confirm_token(body.decode("utf-8", errors="replace"))
    if token is None:
        return url
    join = "&" if "?" in url else "?"
    return f"{url}{join}confirm={urllib.parse.quote(token)}"


def _try_xet(
    url: str,
    partial: Path,
    size: int,
    opener: Opener,
    timeout: float,
    progress: ProgressFn | None,
    module: object | None,
) -> bool:
    if os.environ.get("HF_HUB_DISABLE_XET", "").strip().lower() in {"1", "true", "yes"}:
        return False
    loaded = module if module is not None else _import_xet()
    if loaded is None:
        return False
    offer = _xet_offer(url, opener, timeout)
    if offer is None:
        return False
    info_cls = getattr(loaded, "PyXetDownloadInfo", None)
    download_files = getattr(loaded, "download_files", None)
    if info_cls is None or not callable(download_files):
        return False
    try:
        info = info_cls(str(partial), offer.digest, int(size))
    except TypeError:
        info = info_cls(destination_path=str(partial), hash=offer.digest, file_size=int(size))

    def _on_update(update: object) -> None:
        received = getattr(update, "downloaded", None)
        if received is None:
            received = getattr(update, "received", 0)
        _notify(progress, _host(url), int(received or 0), size, 0.0)

    try:
        try:
            download_files(
                [info],
                endpoint=offer.endpoint or None,
                progress_updater=[_on_update],
            )
        except TypeError:
            download_files([info], endpoint=offer.endpoint or None)
    except KeyboardInterrupt, SystemExit, Cancelled:
        raise
    except Exception:
        return False
    return partial.is_file()


def _xet_offer(url: str, opener: Opener, timeout: float) -> _Offer | None:
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/vnd.xet-file+json"}
    request = urllib.request.Request(url, headers=headers, method="HEAD")
    try:
        response = opener(request, timeout=timeout)
    except OSError, urllib.error.URLError, TimeoutError:
        return None
    try:
        status = _status(response)
        if status >= 400:
            return None
        digest = _header(getattr(response, "headers", {}), "X-Xet-Hash")
        if not digest:
            return None
        endpoint = _http_endpoint(_header(getattr(response, "headers", {}), "X-Xet-Endpoint") or "")
        return _Offer(digest.strip(), endpoint)
    finally:
        _close(response)


def _import_xet() -> object | None:
    """Import ``hf_xet`` when it is installed.

    The import stays inside this function so a missing optional package does
    not break the HTTP download path.
    """
    try:
        import hf_xet
    except ImportError:
        return None
    return hf_xet


def _pick(
    mirrors: list[_Mirror],
    current: _Mirror | None,
    moment: float,
    ratio: float,
    speed_floor: float = 0,
) -> _Mirror | None:
    live = [
        mirror
        for mirror in mirrors
        if not mirror.poisoned and mirror.bps is not None and mirror.down_until <= moment
    ]
    if not live:
        return None
    best = max(live, key=lambda mirror: (mirror.bps or 0.0, -mirrors.index(mirror)))
    if (
        current is None
        or current.poisoned
        or current.down_until > moment
        or current.bps is None
        or current not in live
    ):
        return best
    if best is current:
        return current
    current_bps = current.bps or 0.0
    best_bps = best.bps or 0.0
    if speed_floor > 0 and current_bps <= speed_floor < best_bps:
        return best
    if best_bps >= current_bps * ratio:
        return best
    return current


def _mark_down(
    mirror: _Mirror,
    moment: float,
    base: float,
    cap: float,
    max_failures: int,
) -> None:
    mirror.failures += 1
    delay = min(cap, base * (2 ** (mirror.failures - 1)))
    mirror.down_until = moment + delay
    mirror.bps = None
    if mirror.failures >= max_failures:
        mirror.poisoned = True


def _give_up(name: str, failures: list[str]) -> None:
    detail = "; ".join(failures)
    raise FetchError(f"all mirrors failed for {name}: {detail}", failures)


def _partial_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return 0


def _open(url: str, headers: dict[str, str], opener: Opener, timeout: float) -> object:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        return opener(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 416:
            return exc
        raise _SourceFailed(f"HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise _SourceFailed(str(exc)) from exc


def _read(response: object, size: int) -> bytes:
    try:
        block = response.read(size)
    except (OSError, TimeoutError) as exc:
        raise _SourceFailed(str(exc)) from exc
    return bytes(block or b"")


def _status(response: object) -> int:
    return int(getattr(response, "status", 200) or 200)


def _close(response: object) -> None:
    close = getattr(response, "close", None)
    if close is not None:
        close()


def _notify(
    progress: ProgressFn | None,
    source: str,
    received: int,
    total: int,
    bps: float,
) -> None:
    if progress is not None:
        progress(FetchProgress(source, received, total, bps))


def _rate(written: int, elapsed: float) -> float:
    if elapsed <= 0:
        return 0.0
    return written / elapsed


def _checkpoint(control: TransferSession | None) -> None:
    if control is None:
        return
    try:
        control.checkpoint()
    except TransferCancelled as exc:
        raise Cancelled("cancelled") from exc


def _guarded_opener(guard: UrlGuard) -> Opener:
    def open_url(request: urllib.request.Request, timeout: float) -> object:
        return _urlopen(request, timeout, guard)

    return open_url


def _stop_requested(cancel: threading.Event | None, control: TransferSession | None) -> bool:
    if cancel is not None and cancel.is_set():
        return True
    return control is not None and control.cancelled


def _raise_if_cancelled(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled("cancelled")


def _interruptible(seconds: float, pause: Sleeper, cancel: threading.Event | None) -> None:
    remaining = max(0.0, seconds)
    while remaining > 0:
        _raise_if_cancelled(cancel)
        step = min(0.1, remaining)
        pause(step)
        remaining -= step


def _http_endpoint(value: str) -> str:
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return value.strip()


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc


def _is_huggingface(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    return host in _HF_HOSTS or host.endswith(".huggingface.co")


def _header(headers: object, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter(name)
    if value is None:
        value = getter(name.lower())
    if value is None:
        return None
    return str(value).strip()


def _urlopen(request: urllib.request.Request, timeout: float, guard: UrlGuard) -> object:
    try:
        guard.check(request.full_url)
    except UnsafeUrl as exc:
        raise FetchError(str(exc)) from exc
    opener = urllib.request.build_opener(_GuardedRedirect(guard))
    return opener.open(request, timeout=timeout)


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only when the target stays on the allowlist."""

    def __init__(self, guard: UrlGuard) -> None:
        """Remember the rules applied to ``newurl``.

        Args:
            guard: The same guard that accepted the original URL.
        """
        super().__init__()
        self._guard = guard

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Reject a redirect that leaves the allowlist or uses a private address."""
        try:
            self._guard.check(newurl)
        except UnsafeUrl as exc:
            raise FetchError(str(exc)) from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _SourceFailed(FetchError):
    """One mirror failed. The caller tries another, or the same one later."""
