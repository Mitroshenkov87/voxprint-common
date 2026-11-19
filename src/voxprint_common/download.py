"""Multi-source download with SHA-256, resume, and a loud disk check.

Callers pass mirror URLs in the order they should be tried. The first source
that delivers the expected digest wins. A network failure keeps the partial
file so the next source, or a later run, can resume with an HTTP Range. A
digest mismatch deletes the partial and tries the next source.

Before any byte is requested, the free space on the destination volume is
checked. The check requires ``size * margin`` bytes (margin 1.2, the suite's
room for the partial and the finished file). When the volume is short, this
raises :class:`DiskSpaceError` and does not start the transfer.
"""

from __future__ import annotations

import math
import shutil
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from voxprint_common import __version__
from voxprint_common.atomic import replace
from voxprint_common.hashing import HASH_RE, HashMismatch, verify

__all__ = [
    "DEFAULT_MARGIN",
    "DiskSpaceError",
    "DownloadError",
    "DownloadProgress",
    "download",
    "huggingface_url",
    "modelscope_url",
]

DEFAULT_MARGIN = 1.2
_USER_AGENT = f"voxprint-common/{__version__}"
_CHUNK = 256 * 1024

Opener = Callable[..., object]
DiskFree = Callable[[Path], int]
ProgressFn = Callable[["DownloadProgress"], None]


class DownloadError(Exception):
    """Every source failed, or the request itself was invalid.

    ``failures`` lists one line per source that was attempted.
    """

    def __init__(self, message: str, failures: Sequence[str] = ()) -> None:
        """Store the summary and the per-source lines.

        Args:
            message: Summary shown to the user.
            failures: One line per source, in try order.
        """
        self.failures = tuple(failures)
        super().__init__(message)


class DiskSpaceError(DownloadError):
    """The destination volume does not have the required free space."""

    def __init__(self, path: Path, need: int, free: int) -> None:
        """Name the path and both byte counts.

        Args:
            path: File that was going to be written.
            need: Bytes required, including the margin.
            free: Bytes free on the volume.
        """
        self.path = path
        self.need = need
        self.free = free
        super().__init__(
            f"Not enough disk space to download to {path}: need {need} bytes, {free} bytes free."
        )


@dataclass(frozen=True)
class DownloadProgress:
    """One progress notice during a transfer."""

    source: str
    received: int
    total: int | None
    fraction: float | None
    message: str


def huggingface_url(repo_id: str, filename: str, revision: str = "main") -> str:
    """Return a Hugging Face resolve URL for one file.

    Args:
        repo_id: ``owner/name``.
        filename: Path inside the repository. ``..`` is rejected.
        revision: Branch, tag, or commit.

    Returns:
        An https URL.
    """
    return _hub_url("https://huggingface.co", repo_id, filename, revision, "/resolve/")


def modelscope_url(repo_id: str, filename: str, revision: str = "master") -> str:
    """Return a ModelScope resolve URL for one file.

    Args:
        repo_id: ``owner/name``.
        filename: Path inside the repository. ``..`` is rejected.
        revision: Branch, tag, or commit.

    Returns:
        An https URL.
    """
    return _hub_url("https://www.modelscope.cn/models", repo_id, filename, revision, "/resolve/")


def download(
    destination: Path,
    sources: Sequence[str],
    *,
    sha256: str,
    size: int | None = None,
    progress: ProgressFn | None = None,
    timeout: float = 60.0,
    chunk_size: int = _CHUNK,
    margin: float = DEFAULT_MARGIN,
    disk_free: DiskFree | None = None,
    opener: Opener | None = None,
) -> Path:
    """Download ``destination`` from the first source whose bytes match ``sha256``.

    Args:
        destination: Finished file. The partial file is ``<name>.partial``
            beside it.
        sources: HTTP(S) URLs, tried in order.
        sha256: Expected lowercase or mixed-case digest.
        size: Expected size in bytes. When omitted, a HEAD request supplies
            Content-Length if a source sends one.
        progress: Called as bytes arrive. Optional.
        timeout: Socket timeout in seconds for each open.
        chunk_size: Read size.
        margin: Free-space multiplier applied to ``size``. The default is 1.2.
        disk_free: Replacement for ``shutil.disk_usage(path).free``.
        opener: ``opener(request, timeout) -> response``. Defaults to urllib.
            The response needs ``read``, ``close``, and ``status``.

    Returns:
        ``destination`` after the digest has been checked.

    Raises:
        DiskSpaceError: Free space is below ``size * margin``. The message
            includes both byte counts.
        DownloadError: No source produced the expected digest, or the arguments
            are invalid.
    """
    digest = sha256.strip().lower()
    if HASH_RE.fullmatch(digest) is None:
        raise DownloadError("sha256 must be 64 hexadecimal characters")
    if chunk_size <= 0:
        raise DownloadError("chunk_size must be positive")
    if margin < 1:
        raise DownloadError("margin must be at least 1")
    urls = [_check_url(url) for url in sources]
    if not urls:
        raise DownloadError("at least one source URL is required")
    if size is not None and size < 0:
        raise DownloadError("size must be zero or positive")
    destination.parent.mkdir(parents=True, exist_ok=True)
    free_of = disk_free or _free_bytes
    open_url = opener or _urlopen
    known = size
    if known is None:
        known = _probe_size(urls, open_url, timeout)
    if known is not None:
        _require_space(destination, known, margin, free_of)
    partial = destination.with_name(destination.name + ".partial")
    if known is not None and partial.is_file() and partial.stat().st_size > known:
        partial.unlink()
    failures: list[str] = []
    for url in urls:
        host = urllib.parse.urlparse(url).netloc
        try:
            _transfer(url, partial, known, open_url, timeout, chunk_size, progress, host)
            verify(partial, digest)
        except HashMismatch as exc:
            partial.unlink(missing_ok=True)
            failures.append(f"{url}: {exc.found} does not match {exc.expected}")
            continue
        except _SourceFailed as exc:
            failures.append(f"{url}: {exc}")
            continue
        replace(partial, destination)
        _notify(progress, host, destination.stat().st_size, known, "verified")
        return destination
    detail = "; ".join(failures)
    raise DownloadError(f"all sources failed for {destination.name}: {detail}", failures)


def _hub_url(origin: str, repo_id: str, filename: str, revision: str, marker: str) -> str:
    repo = _repo(repo_id)
    name = _filename(filename)
    rev = urllib.parse.quote(revision, safe="")
    return f"{origin}/{repo}{marker}{rev}/{name}"


def _repo(repo_id: str) -> str:
    text = repo_id.strip().strip("/")
    parts = text.split("/")
    if len(parts) != 2 or any(part in ("", ".", "..") or "\\" in part for part in parts):
        raise DownloadError(f"repository id must be owner/name, got {repo_id!r}")
    return "/".join(urllib.parse.quote(part, safe="") for part in parts)


def _filename(filename: str) -> str:
    text = filename.strip().replace("\\", "/")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or ".." in parts or text.startswith("/"):
        raise DownloadError(f"filename must stay inside the repository, got {filename!r}")
    return "/".join(urllib.parse.quote(part, safe="") for part in parts)


def _check_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DownloadError(f"source must be an http(s) URL, got {url!r}")
    return url.strip()


def _free_bytes(path: Path) -> int:
    return int(shutil.disk_usage(path).free)


def _require_space(destination: Path, size: int, margin: float, disk_free: DiskFree) -> None:
    need = math.ceil(size * margin)
    try:
        free = int(disk_free(destination.parent))
    except OSError as exc:
        raise DownloadError(f"could not check free space for {destination.parent}: {exc}") from exc
    if free < need:
        raise DiskSpaceError(destination, need, free)


def _probe_size(urls: Sequence[str], opener: Opener, timeout: float) -> int | None:
    for url in urls:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _USER_AGENT})
        try:
            response = opener(request, timeout=timeout)
        except OSError, urllib.error.URLError, TimeoutError:
            continue
        try:
            status = int(getattr(response, "status", 200) or 200)
            if status >= 400:
                continue
            length = _header(getattr(response, "headers", {}), "Content-Length")
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()
        if length is not None and length.isdigit():
            return int(length)
    return None


def _transfer(
    url: str,
    partial: Path,
    size: int | None,
    opener: Opener,
    timeout: float,
    chunk_size: int,
    progress: ProgressFn | None,
    host: str,
) -> None:
    have = partial.stat().st_size if partial.is_file() else 0
    if size is not None and have == size and have > 0:
        _notify(progress, host, have, size, "resuming")
        return
    if size is not None and have == size == 0 and partial.is_file():
        return
    headers = {"User-Agent": _USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        response = opener(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and size is not None and have == size:
            return
        raise _SourceFailed(f"HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise _SourceFailed(str(exc)) from exc
    try:
        status = int(getattr(response, "status", 200) or 200)
        if status == 416 and size is not None and have == size:
            return
        if status >= 400:
            raise _SourceFailed(f"HTTP {status}")
        if status != 206:
            have = 0
        mode = "ab" if status == 206 and have else "wb"
        received = have if mode == "ab" else 0
        _notify(progress, host, received, size, "downloading")
        with partial.open(mode) as handle:
            while True:
                try:
                    block = response.read(chunk_size)
                except (OSError, TimeoutError) as exc:
                    raise _SourceFailed(str(exc)) from exc
                if not block:
                    break
                handle.write(block)
                received += len(block)
                _notify(progress, host, received, size, "downloading")
            handle.flush()
        if size is not None and received < size:
            raise _SourceFailed(f"ended at {received} of {size} bytes")
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()


def _notify(
    progress: ProgressFn | None,
    source: str,
    received: int,
    total: int | None,
    message: str,
) -> None:
    if progress is None:
        return
    fraction = None if not total else min(1.0, received / total)
    progress(DownloadProgress(source, received, total, fraction, message))


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


def _urlopen(request: urllib.request.Request, timeout: float) -> object:
    opener = urllib.request.build_opener(_SafeRedirect)
    return opener.open(request, timeout=timeout)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects that stay on http or https."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Reject a redirect whose scheme is not http or https."""
        scheme = urllib.parse.urlparse(newurl).scheme.lower()
        if scheme not in {"http", "https"}:
            raise DownloadError(f"refusing redirect to {scheme or 'an unknown scheme'}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _SourceFailed(DownloadError):
    """One source failed. The caller tries the next URL."""
