"""Canvas model views over the shared blob store.

Canvas rc.28 opens a model at ``models_dir()/views/canvas/<id>-<digest12>/``.
The 12 hex digits are the leading bytes of the SHA-256 of the sorted
``path<TAB>sha256`` lines. Each entry is a hard link to ``blobs/<sha256>``.
``.voxprint-view.json`` records schema 1. Blobs are never deleted here, and
nothing outside ``views/canvas`` is removed. A Windows drive prefix, or any
name that would leave the view folder, is refused.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath

from voxprint_common.paths import models_dir

__all__ = ["materialize_views", "view_directory"]

MARKER = ".voxprint-view.json"
MARKER_SCHEMA = 1
_SMALL_COPY_BYTES = 4 * 1024 * 1024
_COPY_SUFFIXES = frozenset({".json", ".txt", ".model", ".tiktoken", ".jinja", ".md"})


def view_directory(root: Path, key: str, files: Mapping[str, str]) -> Path:
    """Return the Canvas view folder for ``key`` and its path-to-sha256 map.

    Args:
        root: Shared models directory (``models_dir()``).
        key: Model id. Slashes become ``--``, the same spelling Canvas uses.
        files: Repo-relative path to lowercase sha256.

    Returns:
        ``root/views/canvas/<key>-<12 hex>``.
    """
    listing = "\n".join(f"{name}\t{digest.lower()}" for name, digest in sorted(files.items()))
    digest = hashlib.sha256(listing.encode("utf-8")).hexdigest()[:12]
    safe = key.replace("/", "--").replace("\\", "--")
    return root / "views" / "canvas" / f"{safe}-{digest}"


def materialize_views(files: Sequence[object], ready: Sequence[object] | None = None) -> list[Path]:
    """Build a Canvas view for every model whose planned files are all in blobs.

    Args:
        files: Planned files for the job. A model is skipped when any of its
            files is absent from ``ready``.
        ready: Files already adopted, reused, or downloaded. Defaults to ``files``.

    Returns:
        View folders that are complete. A model that is not a Canvas model is
        left out.

    Raises:
        OSError: A weight file could not be linked into the view.
    """
    done = files if ready is None else ready
    finished = {id(item) for item in done}
    groups: dict[str, dict[str, tuple[object, str]]] = {}
    for item in files:
        if "canvas" not in getattr(item, "apps", ()):
            continue
        digest = str(getattr(item, "sha256", "")).lower()
        layouts = getattr(item, "layouts", ()) or (
            (str(getattr(item, "model_id", "")), str(getattr(item, "name", ""))),
        )
        for model_id, path in layouts:
            if not model_id or not path or not digest:
                continue
            groups.setdefault(str(model_id), {})[str(path)] = (item, digest)
    built: list[Path] = []
    root = models_dir()
    for model_id, rows in groups.items():
        if any(id(item) not in finished for item, _digest in rows.values()):
            continue
        mapping = {path: digest for path, (_item, digest) in rows.items()}
        folder = _ensure(root, model_id, mapping)
        if folder is not None:
            built.append(folder)
    return built


def _ensure(root: Path, key: str, files: Mapping[str, str]) -> Path | None:
    folder = view_directory(root, key, files)
    if not _blobs_present(root, files):
        _drop(root, key)
        return None
    if _valid(root, folder, files):
        _drop(root, key, keep=folder)
        return folder
    staging = folder.with_name(f"{folder.name}.staging-{os.getpid()}")
    _remove(root, staging)
    staging.mkdir(parents=True)
    try:
        for name, digest in sorted(files.items()):
            target = _entry(staging, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            _link_blob(root / "blobs" / digest, target)
        marker = {
            "schema": MARKER_SCHEMA,
            "key": key,
            "files": {name: digest.lower() for name, digest in sorted(files.items())},
        }
        text = json.dumps(marker, indent=2, sort_keys=True) + "\n"
        (staging / MARKER).write_text(text, encoding="utf-8")
        _remove(root, folder)
        os.replace(staging, folder)
    except BaseException:
        _remove(root, staging)
        raise
    _drop(root, key, keep=folder)
    return folder


def _blobs_present(root: Path, files: Mapping[str, str]) -> bool:
    if not files:
        return False
    return all((root / "blobs" / digest.lower()).is_file() for digest in files.values())


def _valid(root: Path, folder: Path, files: Mapping[str, str]) -> bool:
    try:
        marker = json.loads((folder / MARKER).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return False
    if not isinstance(marker, dict) or marker.get("schema") != MARKER_SCHEMA:
        return False
    expected = {name: digest.lower() for name, digest in files.items()}
    if marker.get("files") != expected:
        return False
    for name, digest in files.items():
        entry = _entry(folder, name)
        blob = root / "blobs" / digest.lower()
        try:
            if entry.is_symlink():
                if entry.resolve() != blob.resolve():
                    return False
            elif not os.path.samefile(entry, blob) and not _small_copy(entry, blob):
                return False
        except OSError:
            return False
    return True


def _link_blob(blob: Path, target: Path) -> None:
    try:
        os.link(blob, target)
        return
    except OSError:
        pass
    if sys.platform != "win32":
        try:
            os.symlink(blob, target)
            return
        except OSError:
            pass
    if target.suffix.lower() in _COPY_SUFFIXES and blob.stat().st_size <= _SMALL_COPY_BYTES:
        shutil.copyfile(blob, target)
        return
    raise OSError(f"could not link {target.name} into the canvas view")


def _small_copy(entry: Path, blob: Path) -> bool:
    return (
        entry.suffix.lower() in _COPY_SUFFIXES
        and blob.stat().st_size <= _SMALL_COPY_BYTES
        and entry.stat().st_size == blob.stat().st_size
    )


def _entry(folder: Path, name: str) -> Path:
    """Return ``folder / name`` when that path stays inside ``folder``."""
    relative = _safe_name(name)
    target = folder.joinpath(*relative.parts)
    windows_folder = PureWindowsPath(folder)
    windows_target = windows_folder.joinpath(*relative.parts)
    if windows_target.drive != windows_folder.drive or windows_target.root != windows_folder.root:
        raise OSError(f"view path {name} leaves the model folder")
    base = os.path.abspath(folder)
    landed = os.path.abspath(target)
    try:
        inside = os.path.commonpath([base, landed]) == base
    except ValueError:
        inside = False
    if not inside:
        raise OSError(f"view path {name} leaves the model folder")
    return target


def _safe_name(name: str) -> Path:
    text = name.replace("\\", "/")
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    parts = posix.parts
    if (
        not parts
        or posix.is_absolute()
        or windows.drive
        or windows.root
        or windows.is_absolute()
        or any(part in {"", ".", ".."} or _drive_part(part) for part in parts)
    ):
        raise OSError(f"view path {name} leaves the model folder")
    return Path(*parts)


def _drive_part(part: str) -> bool:
    windows = PureWindowsPath(part)
    return bool(windows.drive or windows.root or windows.is_absolute())


def _drop(root: Path, key: str, keep: Path | None = None) -> None:
    base = root / "views" / "canvas"
    if not base.is_dir():
        return
    owner = key.replace("/", "--").replace("\\", "--")
    for child in list(base.iterdir()):
        if _owner(child.name) != owner:
            continue
        if keep is not None and child.name == keep.name:
            continue
        _remove(root, child)


def _owner(name: str) -> str | None:
    stem = name
    marker = ".staging-"
    if marker in name:
        head, tail = name.rsplit(marker, 1)
        if tail.isdigit():
            stem = head
    if len(stem) < 14 or stem[-13] != "-":
        return None
    digest = stem[-12:]
    if any(ch not in "0123456789abcdef" for ch in digest):
        return None
    return stem[:-13]


def _remove(root: Path, folder: Path) -> None:
    base = (root / "views" / "canvas").resolve()
    if not (folder.exists() or folder.is_symlink()):
        return
    if folder.is_symlink():
        return
    target = folder.resolve()
    if target == base or base not in target.parents:
        return
    blobs = (root / "blobs").resolve()
    if target == blobs or blobs in target.parents:
        return
    shutil.rmtree(target)
