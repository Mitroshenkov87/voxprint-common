"""Shared-store resolution between blob views and per-app model folders.

``<models_dir>/blobs/<sha256>`` is the single content-addressed copy of a
model file. Apps such as Model Forge keep their own trees under
``<models_dir>/<app>/``, and every view under ``<models_dir>/views/<app>/``
hard-links the same blobs. This module answers, for one catalogued file:
where is a complete copy, is the blob already trusted, and (when asked)
can a copy be folded into the blob store without extra disk usage.

Resolution never deletes or renames anything in an app folder. A copy is
confirmed by SHA-256 through the hash cache when possible, and hashed
otherwise. Nothing here downloads; a missing file is simply ``"missing"``.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from voxprint_common.atomic import write_json
from voxprint_common.hashing import sha256_file
from voxprint_common.mdl import presence

try:
    import fcntl
except ImportError:  # Windows has no fcntl.
    fcntl = None  # type: ignore[assignment]

__all__ = [
    "StoreFile",
    "adopt_into_store",
    "blob_path",
    "blob_ready",
    "copy_roots",
    "find_copy",
    "link_or_copy",
    "load_refs",
    "record_ref",
    "refs_path",
    "resolve_file",
]

_FICLONE = 0x40049409
_RESERVED_ROOTS = frozenset({"blobs", "views", "refs", ".staging"})
_INCOMPLETE_SUFFIXES = (".partial", ".linking", ".adopting")
_SKIP_WALK_DIRS = frozenset({"blobs", ".git", "__pycache__"})


@dataclass(frozen=True)
class StoreFile:
    """One file of a model that the shared store may already hold.

    Attributes:
        path: Repo-relative path with forward slashes, the spelling used in
            manifests and inside view folders.
        sha256: Lowercase SHA-256 of the file's bytes.
        size: Length in bytes.
        repo: Source repository, for example a hub repo id. Optional; only
            used for the informational refs index.
        revision: Revision of ``repo``. Optional; only used for the refs
            index.
    """

    path: str
    sha256: str
    size: int
    repo: str = ""
    revision: str = ""


def link_or_copy(
    source: Path, dest: Path, *, allow_copy: bool = True, allow_symlink: bool = True
) -> str:
    """Create ``dest`` with the bytes of ``source``, atomically.

    A symlinked ``source`` is followed to its real path first. When ``dest``
    already names the same file, nothing is written. Otherwise the content is
    produced next to ``dest`` under the name ``<dest>.linking`` and published
    with :func:`os.replace`, so a reader never sees a half-written file.

    The order of attempts is:

    1. hard link (``"hardlink"``);
    2. reflink on Linux only, via ``FICLONE`` (``"reflink"``);
    3. symbolic link when ``allow_symlink``, never on Windows (``"symlink"``);
    4. a full byte copy when ``allow_copy`` (``"copy"``), else :exc:`OSError`.

    On Windows only the hard link and the copy are tried; hard links need no
    administrator rights on NTFS, while symlinks may. Store adoption passes
    ``allow_symlink=False`` so a published blob is always a regular file;
    views pass the default so an entry may still be a symlink to a blob.

    Args:
        source: Existing file whose bytes should appear at ``dest``.
        dest: Destination path. Replaced atomically when different.
        allow_copy: Whether the byte-copy fallback is permitted.
        allow_symlink: Whether the symbolic-link fallback is permitted.

    Returns:
        ``"reused"``, ``"hardlink"``, ``"reflink"``, ``"symlink"``, or
        ``"copy"``, describing how ``dest`` came to hold the bytes.

    Raises:
        OSError: Every permitted attempt failed and ``allow_copy`` is false,
            or the copy itself failed. The temporary file is removed first.
    """
    real = Path(os.path.realpath(source))
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        # ``samefile`` follows symlinks, so an existing symlinked ``dest``
        # only counts as reused when symlinks are allowed; store adoption
        # replaces it with a real link below so the blob stays a regular
        # file.
        symlinked = dest.is_symlink()
        if (
            (not symlinked or allow_symlink)
            and (dest.exists() or symlinked)
            and os.path.samefile(real, dest)
        ):
            return "reused"
    except OSError:
        pass
    temp = dest.with_name(dest.name + ".linking")
    if temp.exists() or temp.is_symlink():
        temp.unlink()
    steps = ["hardlink"]
    if sys.platform != "win32":
        if sys.platform.startswith("linux"):
            steps.append("reflink")
        if allow_symlink:
            steps.append("symlink")
    last_error: OSError | None = None
    for step in steps:
        try:
            if step == "hardlink":
                _hardlink(real, temp)
            elif step == "reflink":
                _reflink(real, temp)
            else:
                _symlink(real, temp)
        except OSError as exc:
            last_error = exc
            _remove_quietly(temp)
            continue
        os.replace(temp, dest)
        return step
    if not allow_copy:
        _remove_quietly(temp)
        if last_error is not None:
            raise last_error
        raise OSError(f"could not link {real} onto {dest}")
    try:
        shutil.copyfile(real, temp)
    except OSError:
        _remove_quietly(temp)
        raise
    os.replace(temp, dest)
    return "copy"


def blob_path(root: Path, sha256: str) -> Path:
    """Return the blob file for ``sha256`` under ``root``.

    Args:
        root: Shared models directory.
        sha256: Digest of the blob. Case is normalised to lowercase.

    Returns:
        ``root/blobs/<sha256>``.
    """
    return root / "blobs" / sha256.lower()


def blob_ready(root: Path, item: StoreFile) -> bool:
    """Return whether the blob for ``item`` is complete and trusted.

    The blob must be a regular file — never a symlink, so the store never
    relies on bytes living outside ``blobs/`` — of exactly ``item.size``
    bytes, and be marked verified: ``blobs/<sha>.verified`` holding the
    digest, or the hash cache holding the digest for the file's current size
    and mtime. Nothing is hashed here.

    Args:
        root: Shared models directory.
        item: Catalogued file whose blob is checked.

    Returns:
        True when the blob can be used without reading its bytes again.
    """
    path = blob_path(root, item.sha256)
    try:
        if path.is_symlink():
            return False
        info = path.stat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size != item.size:
        return False
    wanted = item.sha256.strip().lower()
    try:
        recorded = path.with_name(path.name + ".verified").read_text(encoding="utf-8").strip()
    except OSError, UnicodeError:
        recorded = ""
    if recorded == wanted:
        return True
    return presence.cached_digest(path) == wanted


def copy_roots(root: Path) -> list[Path]:
    """Return per-app folders that may hold complete copies of model files.

    A folder qualifies when it is a directory directly under ``root`` other
    than the reserved names ``blobs``, ``views``, ``refs``, and ``.staging``
    and other than any name starting with ``.``, or a directory directly
    under ``root/views`` (another app's view folders).

    Args:
        root: Shared models directory. May be missing.

    Returns:
        Sorted folder list, empty when ``root`` does not exist.
    """
    found: list[Path] = []
    if root.is_dir():
        for child in root.iterdir():
            if child.name in _RESERVED_ROOTS or child.name.startswith("."):
                continue
            if child.is_dir():
                found.append(child)
    views = root / "views"
    if views.is_dir():
        for child in views.iterdir():
            if child.is_dir():
                found.append(child)
    return sorted(found)


def find_copy(
    root: Path,
    item: StoreFile,
    extra_roots: Sequence[Path] = (),
    *,
    hasher: Callable[[Path], str] | None = None,
    walk_cache: dict[Path, list[Path]] | None = None,
) -> Path | None:
    """Find a confirmed complete copy of ``item`` outside the blob store.

    ``extra_roots`` are searched first, then :func:`copy_roots`. In each
    root a candidate is (a) any regular file whose absolute path ends with
    the item's relative path parts, then (b) any regular file with the same
    basename. A candidate must have exactly ``item.size`` bytes, must not be
    inside ``root/blobs``, and must not be an in-flight ``.partial``,
    ``.linking``, or ``.adopting`` file. Symlinked candidates are resolved
    to their real file. Each root is walked once per call.

    The first size match is confirmed by SHA-256: the hash cache is used
    when it has a row for the file's current size and mtime, otherwise
    ``hasher`` (default :func:`voxprint_common.hashing.sha256_file`) runs
    and the result is remembered in the cache.

    Args:
        root: Shared models directory.
        item: Catalogued file to locate.
        extra_roots: Folders to search before the per-app copy roots.
        hasher: Digest callable used when the cache has no valid row.
        walk_cache: Optional shared walk cache. When given, each root is
            walked at most once per cache lifetime, which keeps a repair
            pass over many files from re-walking large app folders. The
            cache maps a resolved root path to its walked file list.

    Returns:
        The first confirmed copy, or ``None`` when no candidate matches.
    """
    parts = _path_parts(item.path)
    if parts is None:
        return None
    wanted = item.sha256.strip().lower()
    hash_fn = hasher if hasher is not None else sha256_file
    blobs = (root / "blobs").resolve()
    for base in (*extra_roots, *copy_roots(root)):
        candidates = _walk_files(base) if walk_cache is None else _cached_walk(walk_cache, base)
        suffix_hits = [path for path in candidates if path.parts[-len(parts) :] == parts]
        name_hits = [path for path in candidates if path.name == parts[-1]]
        for group in (suffix_hits, name_hits):
            for candidate in group:
                if not _complete_copy(candidate, item.size, blobs):
                    continue
                digest = presence.cached_digest(candidate)
                if digest is None:
                    digest = hash_fn(candidate)
                    presence.remember_digest(candidate, digest)
                if digest == wanted:
                    return candidate
    return None


def adopt_into_store(root: Path, item: StoreFile, source: Path) -> str:
    """Fold ``source`` into the blob store as ``item``'s blob.

    The blob is created with :func:`link_or_copy` and ``allow_symlink=False``:
    hard link first, then reflink on Linux, then a byte copy, so the blob is
    always a regular file and never a pointer at an app folder or a ``--from``
    directory that could later disappear. The ``blobs/<sha>.verified`` marker
    is written in the exact format
    :func:`voxprint_common.mdl.job._mark_verified` uses, the digest enters
    the hash cache, and the ref is recorded in the refs index. ``source`` and
    every app folder are left untouched.

    Args:
        root: Shared models directory.
        item: Catalogued file the bytes belong to.
        source: Complete copy of the file, confirmed by the caller.

    Returns:
        The link mode from :func:`link_or_copy`: ``"reused"``,
        ``"hardlink"``, ``"reflink"``, or ``"copy"``.
    """
    digest = item.sha256.strip().lower()
    blob = blob_path(root, digest)
    mode = link_or_copy(source, blob, allow_symlink=False)
    _mark_blob_verified(blob, digest)
    presence.remember_digest(blob, digest)
    record_ref(root, item)
    return mode


def refs_path(root: Path) -> Path:
    """Return the refs index path for ``root``.

    Args:
        root: Shared models directory.

    Returns:
        ``root/refs/index.json``.
    """
    return root / "refs" / "index.json"


def record_ref(root: Path, item: StoreFile) -> None:
    """Add ``item`` to the informational refs index.

    The index maps ``"<repo>@<revision>"`` to ``{path: sha256}``. Nothing is
    written when ``item.repo`` or ``item.revision`` is empty, and blobs stay
    keyed only by sha256.

    Args:
        root: Shared models directory.
        item: Catalogued file to record.
    """
    repo = item.repo.strip()
    revision = item.revision.strip()
    if not repo or not revision:
        return
    document = load_refs(root)
    refs = document["refs"]
    key = f"{repo}@{revision}"
    entry = refs.get(key)
    if not isinstance(entry, dict):
        entry = {}
        refs[key] = entry
    entry[item.path] = item.sha256.strip().lower()
    write_json(refs_path(root), document)


def load_refs(root: Path) -> dict[str, object]:
    """Read the refs index, returning an empty document when absent.

    Args:
        root: Shared models directory.

    Returns:
        ``{"schema": 1, "refs": {...}}`` with one ``"<repo>@<revision>"``
        key per recorded source. A damaged index reads as empty.
    """
    try:
        payload = json.loads(refs_path(root).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {"schema": 1, "refs": {}}
    if not isinstance(payload, dict):
        return {"schema": 1, "refs": {}}
    refs = payload.get("refs")
    if not isinstance(refs, dict):
        refs = {}
    return {"schema": 1, "refs": refs}


def resolve_file(
    root: Path,
    item: StoreFile,
    extra_roots: Sequence[Path] = (),
    *,
    hasher: Callable[[Path], str] | None = None,
    adopt: bool = True,
    walk_cache: dict[Path, list[Path]] | None = None,
) -> tuple[str, Path | None]:
    """Resolve ``item`` to a usable copy of its bytes.

    The order is:

    1. the blob is ready -> ``("store", blob)``;
    2. :func:`find_copy` locates a confirmed copy -> with ``adopt`` the copy
       is folded into the blob store and ``("adopted", blob)`` is returned,
       otherwise ``("found", copy_path)``;
    3. nothing matches -> ``("missing", None)``.

    Args:
        root: Shared models directory.
        item: Catalogued file to resolve.
        extra_roots: Folders to search before the per-app copy roots.
        hasher: Digest callable used when the cache has no valid row.
        adopt: Whether a found copy is hard-linked into the blob store.
        walk_cache: Optional shared walk cache passed to :func:`find_copy`,
            so each copy root is walked at most once for a whole batch of
            resolutions.

    Returns:
        ``(state, path)`` with state ``"store"``, ``"adopted"``, ``"found"``,
        or ``"missing"``.
    """
    if blob_ready(root, item):
        return "store", blob_path(root, item.sha256)
    copy = find_copy(root, item, extra_roots, hasher=hasher, walk_cache=walk_cache)
    if copy is None:
        return "missing", None
    if adopt:
        adopt_into_store(root, item, copy)
        return "adopted", blob_path(root, item.sha256)
    return "found", copy


def _mark_blob_verified(blob: Path, digest: str) -> None:
    """Write ``blobs/<sha>.verified`` in the exact format job.py uses."""
    marker = blob.with_name(blob.name + ".verified")
    marker.parent.mkdir(parents=True, exist_ok=True)
    text = digest + "\n"
    try:
        if marker.is_file() and marker.read_text(encoding="utf-8") == text:
            return
    except OSError, UnicodeError:
        pass
    marker.write_text(text, encoding="utf-8")


def _hardlink(source: Path, dest: Path) -> None:
    os.link(source, dest)


def _reflink(source: Path, dest: Path) -> None:
    if fcntl is None:
        raise OSError("reflink needs fcntl")
    with source.open("rb") as src, dest.open("wb") as dst:
        fcntl.ioctl(dst.fileno(), _FICLONE, src.fileno())


def _symlink(source: Path, dest: Path) -> None:
    os.symlink(source, dest)


def _remove_quietly(path: Path) -> None:
    try:
        if path.exists() or path.is_symlink():
            path.unlink()
    except OSError:
        pass


def _path_parts(name: str) -> tuple[str, ...] | None:
    text = name.replace("\\", "/").strip()
    if not text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.parts


def _walk_files(base: Path) -> list[Path]:
    found: list[Path] = []
    if not base.is_dir():
        return found
    for folder, dirs, names in os.walk(base):
        dirs[:] = sorted(
            name for name in dirs if name not in _SKIP_WALK_DIRS and ".staging-" not in name
        )
        for name in sorted(names):
            if name.endswith(_INCOMPLETE_SUFFIXES):
                continue
            found.append(Path(folder) / name)
    return found


def _cached_walk(cache: dict[Path, list[Path]], base: Path) -> list[Path]:
    """Return the walked files of ``base``, walking each root at most once."""
    key = base.resolve()
    if key not in cache:
        cache[key] = _walk_files(base)
    return cache[key]


def _complete_copy(candidate: Path, size: int, blobs: Path) -> bool:
    try:
        resolved = candidate.resolve()
        info = resolved.stat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size != size:
        return False
    return resolved != blobs and blobs not in resolved.parents
