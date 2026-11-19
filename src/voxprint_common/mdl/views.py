"""App model views over the shared blob store.

Canvas rc.28 opens a model at ``models_dir()/views/canvas/<id>-<digest12>/``.
Other apps opt in with their own ``views/<app>/`` folder. The 12 hex digits
are the leading bytes of the SHA-256 of the sorted ``path<TAB>sha256`` lines.
Each entry is a hard link to ``blobs/<sha256>`` when the platform allows it,
a reflink or symlink when that works, and a full copy as the last resort.
``.voxprint-view.json`` records schema 1 and, when a fallback mode was used,
per-file ``modes``. Blobs are never deleted here, and nothing outside
``views/<app>`` is removed. A view is built or kept only from blobs the
store accepts as ready — complete, verified, regular files, never symlinks —
so a leftover or truncated ``blobs/<sha>`` can never back an app-visible
view. A Windows drive prefix, or any name that would leave the view folder,
is refused.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath

from voxprint_common.mdl import presence, store
from voxprint_common.paths import models_dir

__all__ = ["inspect_view", "materialize_views", "repair_view", "view_apps", "view_directory"]

MARKER = ".voxprint-view.json"
MARKER_SCHEMA = 1
_SMALL_COPY_BYTES = 4 * 1024 * 1024
_COPY_SUFFIXES = frozenset({".json", ".txt", ".model", ".tiktoken", ".jinja", ".md"})


def file_set_key(files: Mapping[str, str]) -> str:
    """Return the 12-hex digest used in a view folder name.

    Args:
        files: Repo-relative path to sha256.

    Returns:
        The first 12 hex digits of the SHA-256 of the sorted
        ``path<TAB>sha256`` lines.
    """
    listing = "\n".join(f"{name}\t{digest.lower()}" for name, digest in sorted(files.items()))
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()[:12]


def view_directory(root: Path, key: str, files: Mapping[str, str], app: str = "canvas") -> Path:
    """Return the view folder for ``key`` and its path-to-sha256 map.

    Args:
        root: Shared models directory (``models_dir()``).
        key: Model id. Slashes become ``--``, the same spelling apps use.
        files: Repo-relative path to lowercase sha256.
        app: App whose views folder is used. Defaults to ``"canvas"``.

    Returns:
        ``root/views/<app>/<key>-<12 hex>``.
    """
    safe = key.replace("/", "--").replace("\\", "--")
    return root / "views" / app / f"{safe}-{file_set_key(files)}"


def view_apps(root: Path, apps: Sequence[object]) -> list[str]:
    """Return which of ``apps`` get a view folder under ``root/views``.

    Canvas always gets a view. Any other app gets one only when its
    ``views/<app>/`` folder already exists under ``root``, so apps opt in
    by creating that folder.

    Args:
        root: Shared models directory (``models_dir()``).
        apps: App ids named on a planned file.

    Returns:
        The app ids whose views are built or repaired.
    """
    return [str(app) for app in apps if app == "canvas" or (root / "views" / str(app)).is_dir()]


def materialize_views(files: Sequence[object], ready: Sequence[object] | None = None) -> list[Path]:
    """Build a view for every model whose planned files are all in blobs.

    Canvas views are always built. Any other app named in ``item.apps`` is
    built only when its ``views/<app>/`` folder already exists, so apps opt
    in by creating that folder. A file's blob must pass
    :func:`voxprint_common.mdl.store.blob_ready` — complete, verified, and a
    regular file — before the view is built from it.

    Args:
        files: Planned files for the job. A model is skipped when any of its
            files is absent from ``ready``.
        ready: Files already adopted, reused, or downloaded. Defaults to
            ``files``.

    Returns:
        View folders that are complete.

    Raises:
        OSError: A weight file could not be linked into a view.
    """
    done = files if ready is None else ready
    finished = {id(item) for item in done}
    root = models_dir()
    groups: dict[tuple[str, str, str], dict[str, tuple[object, str]]] = {}
    for item in files:
        apps = view_apps(root, getattr(item, "apps", ()))
        if not apps:
            continue
        digest = str(getattr(item, "sha256", "")).lower()
        if not digest:
            continue
        for model_id, path, stamp in layout_entries(item):
            for app in apps:
                groups.setdefault((app, model_id, stamp), {})[path] = (item, digest)
    built: list[Path] = []
    for (app, model_id, _stamp), rows in groups.items():
        if any(id(item) not in finished for item, _digest in rows.values()):
            continue
        mapping = {path: digest for path, (_item, digest) in rows.items()}
        sizes = _row_sizes(rows)
        folder = _ensure(root, model_id, mapping, app, sizes=sizes)
        if folder is not None:
            built.append(folder)
    return built


def _row_sizes(rows: Mapping[str, tuple[object, str]]) -> dict[str, int]:
    """Collect ``item.size`` per repo-relative path, skipping rows without one."""
    sizes: dict[str, int] = {}
    for path, (item, _digest) in rows.items():
        size = getattr(item, "size", None)
        if isinstance(size, int) and not isinstance(size, bool):
            sizes[path] = size
    return sizes


def inspect_view(
    root: Path,
    key: str,
    files: Mapping[str, str],
    app: str = "canvas",
    *,
    sizes: Mapping[str, int] | None = None,
) -> str:
    """Classify the view of ``key`` under ``views/<app>/``.

    Every blob named by ``files`` must pass
    :func:`voxprint_common.mdl.store.blob_ready` — a leftover, truncated,
    unverified, or symlinked blob means the view is a stub, never ``"ok"``.

    Args:
        root: Shared models directory.
        key: Model id, the same spelling passed to :func:`view_directory`.
        files: Repo-relative path to lowercase sha256.
        app: App whose views folder is inspected.
        sizes: Optional catalogued byte size per repo-relative path, the same
            keys as ``files``. When a size is known the blob must match it;
            when it is missing the blob must still be a verified regular
            file, never a symlink.

    Returns:
        ``"ok"`` when the expected folder is a valid view, ``"absent"`` when
        that folder does not exist (another digest of the same model id,
        such as a second VRAM tier, does not count), and ``"stub"`` when the
        expected folder exists but is not valid: no or invalid marker, empty,
        an entry missing, zero bytes, wrong size, not linked to its blob, or
        backed by a blob the store does not accept as ready.
    """
    folder = view_directory(root, key, files, app)
    if folder.is_dir() and _valid(root, folder, files, sizes):
        return "ok"
    if folder.is_dir() or folder.is_symlink():
        return "stub"
    return "absent"


def repair_view(
    root: Path,
    key: str,
    files: Mapping[str, str],
    app: str = "canvas",
    *,
    sizes: Mapping[str, int] | None = None,
) -> tuple[str, Path | None]:
    """Repair the view of ``key`` under ``views/<app>/``.

    When every blob is present and accepted by
    :func:`voxprint_common.mdl.store.blob_ready` — complete, verified, and a
    regular file — the view is rebuilt atomically through the staging folder
    and :func:`os.replace`, unless it was already valid. When any blob is
    missing or not trusted, every folder with this owner key is removed so a
    stub is never left behind, and the blobs themselves stay untouched.

    Args:
        root: Shared models directory.
        key: Model id, the same spelling passed to :func:`view_directory`.
        files: Repo-relative path to lowercase sha256.
        app: App whose views folder is repaired.
        sizes: Optional catalogued byte size per repo-relative path, the same
            keys as ``files``. When a size is known the blob must match it;
            when it is missing the blob must still be a verified regular
            file, never a symlink.

    Returns:
        ``("ok", folder)`` when the view was already valid,
        ``("repaired", folder)`` after a rebuild, or ``("missing", None)``
        when a blob is absent and the folders were dropped.

    Raises:
        OSError: A weight file could not be linked into the view.
    """
    folder = view_directory(root, key, files, app)
    if not _blobs_present(root, files, sizes):
        _drop(root, key, app, drop=folder)
        return "missing", None
    if _valid(root, folder, files, sizes):
        _drop(root, key, app, keep=folder)
        return "ok", folder
    staging = folder.with_name(f"{folder.name}.staging-{os.getpid()}")
    _remove(root, staging, app)
    staging.mkdir(parents=True)
    try:
        modes: dict[str, str] = {}
        for name, digest in sorted(files.items()):
            target = _entry(staging, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            modes[name] = _link_blob(root / "blobs" / digest.lower(), target)
        marker: dict[str, object] = {
            "schema": MARKER_SCHEMA,
            "key": key,
            "files": {name: digest.lower() for name, digest in sorted(files.items())},
        }
        if any(mode != "hardlink" for mode in modes.values()):
            marker["modes"] = {name: modes[name] for name in sorted(files)}
        text = json.dumps(marker, indent=2, sort_keys=True) + "\n"
        (staging / MARKER).write_text(text, encoding="utf-8")
        _remove(root, folder, app)
        os.replace(staging, folder)
    except BaseException:
        _remove(root, staging, app)
        raise
    _drop(root, key, app, keep=folder)
    return "repaired", folder


def _ensure(
    root: Path,
    key: str,
    files: Mapping[str, str],
    app: str = "canvas",
    *,
    sizes: Mapping[str, int] | None = None,
) -> Path | None:
    state, folder = repair_view(root, key, files, app, sizes=sizes)
    return folder if state in {"ok", "repaired"} else None


def _blobs_present(
    root: Path, files: Mapping[str, str], sizes: Mapping[str, int] | None = None
) -> bool:
    if not files:
        return False
    return all(_blob_ready(root, name, digest, sizes) for name, digest in files.items())


def _blob_ready(root: Path, name: str, digest: str, sizes: Mapping[str, int] | None) -> bool:
    """Return whether ``name``'s blob is accepted by the store as ready.

    The blob must be a regular file — never a symlink — and trusted. With a
    catalogued size (from ``sizes``) this is exactly
    :func:`voxprint_common.mdl.store.blob_ready`, so a leftover or truncated
    blob of the wrong size is refused. Without one the blob must still carry
    its ``.verified`` marker or a fresh hash-cache row; a bare ``is_file()``
    never counts.

    Args:
        root: Shared models directory.
        name: Repo-relative path, the key used to look up ``sizes``.
        digest: Lowercase SHA-256 of the file's bytes.
        sizes: Optional catalogued byte size per repo-relative path.
    """
    blob = root / "blobs" / digest.lower()
    if blob.is_symlink():
        return False
    try:
        info = blob.stat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    size = sizes.get(name) if sizes is not None else None
    if size is not None:
        return store.blob_ready(root, store.StoreFile(path=name, sha256=digest, size=size))
    wanted = digest.strip().lower()
    try:
        recorded = blob.with_name(blob.name + ".verified").read_text(encoding="utf-8").strip()
    except OSError, UnicodeError:
        recorded = ""
    if recorded == wanted:
        return True
    return presence.cached_digest(blob) == wanted


def _valid(
    root: Path, folder: Path, files: Mapping[str, str], sizes: Mapping[str, int] | None = None
) -> bool:
    try:
        marker = json.loads((folder / MARKER).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return False
    if not isinstance(marker, dict) or marker.get("schema") != MARKER_SCHEMA:
        return False
    expected = {name: digest.lower() for name, digest in files.items()}
    if marker.get("files") != expected:
        return False
    modes = marker.get("modes")
    if not isinstance(modes, dict):
        modes = {}
    for name, digest in files.items():
        if not _blob_ready(root, name, digest, sizes):
            return False
        entry = _entry(folder, name)
        blob = root / "blobs" / digest.lower()
        try:
            if entry.is_symlink():
                if not blob.is_file() or entry.resolve() != blob.resolve():
                    return False
            elif os.path.samefile(entry, blob):
                continue
            elif modes.get(name) in ("copy", "reflink"):
                if not entry.is_file() or entry.stat().st_size != blob.stat().st_size:
                    return False
            elif not _small_copy(entry, blob):
                return False
        except OSError:
            return False
    return True


def _link_blob(blob: Path, target: Path) -> str:
    """Link ``blob`` into the view, returning the mode store used."""
    return store.link_or_copy(blob, target)


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


def layout_entries(item: object) -> list[tuple[str, str, str]]:
    """Return ``(model_id, path, file_set_key)`` rows from a planned file."""
    layouts = getattr(item, "layouts", ()) or (
        (str(getattr(item, "model_id", "")), str(getattr(item, "name", ""))),
    )
    found: list[tuple[str, str, str]] = []
    for entry in layouts:
        if not isinstance(entry, tuple) or len(entry) < 2:
            continue
        model_id = str(entry[0])
        path = str(entry[1])
        stamp = str(entry[2]) if len(entry) > 2 else ""
        if model_id and path:
            found.append((model_id, path, stamp))
    return found


def _is_staging(name: str) -> bool:
    marker = ".staging-"
    if marker not in name:
        return False
    _head, tail = name.rsplit(marker, 1)
    return tail.isdigit()


def _complete_view(root: Path, folder: Path) -> bool:
    """Return whether ``folder`` is a valid view of the files in its marker."""
    try:
        marker = json.loads((folder / MARKER).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return False
    if not isinstance(marker, dict):
        return False
    files = marker.get("files")
    if not isinstance(files, dict) or not all(
        isinstance(name, str) and isinstance(digest, str) for name, digest in files.items()
    ):
        return False
    return _valid(root, folder, files)


def _drop(
    root: Path,
    key: str,
    app: str,
    keep: Path | None = None,
    drop: Path | None = None,
) -> None:
    base = root / "views" / app
    if not base.is_dir():
        return
    owner = _safe_key(key)
    for child in list(base.iterdir()):
        if _owner(child.name) != owner:
            continue
        if keep is not None and child.name == keep.name:
            continue
        if drop is not None and child.name == drop.name:
            _remove(root, child, app)
            continue
        if _is_staging(child.name) or not _complete_view(root, child):
            _remove(root, child, app)


def _safe_key(key: str) -> str:
    return key.replace("/", "--").replace("\\", "--")


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


def _remove(root: Path, folder: Path, app: str) -> None:
    base = (root / "views" / app).resolve()
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
