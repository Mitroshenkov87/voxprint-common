"""Reclaim legacy model copies that already exist in the blob store.

``voxprint-mdl cleanup`` is a dry run unless ``--apply`` is set. ``--mode link``
replaces a matching legacy file with a hardlink to ``blobs/<sha256>``: the
link is created under a temporary name in the same directory, then renamed
over the original. A failed link is skipped and nothing is copied. ``--mode
delete`` removes a file only after a fresh SHA-256 of the file and of the
blob match. Empty directories are removed only when every file under them was
verified. ``--gc`` removes blobs that no view, manifest component, or app
manifest names.

An interrupted link leaves the original file or the hardlink. The shared
manifest is retargeted at the blob before a delete, so a component never
points at a path this command has removed.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Literal, assert_never

from voxprint_common.cli_protocol import EXIT_CANCELLED, EXIT_OK
from voxprint_common.hashing import HASH_RE, sha256_file
from voxprint_common.mdl.catalog import CatalogError, discover, load_manifest
from voxprint_common.mdl.job import Outcome, _cancelled, _fail, _publish
from voxprint_common.mdl.status import JobState, Status
from voxprint_common.paths import models_dir
from voxprint_common.runtime_manifest import ManifestError, load, retarget_model_paths

__all__ = ["cleanup"]

ProgressFn = Callable[[float | None, str], None]
StatusFn = Callable[[Status], None]
Mode = Literal["link", "delete"]
_Row = dict[str, object]


def cleanup(
    legacy: Sequence[Path] | None = None,
    *,
    manifests: Sequence[Path] | None = None,
    app: str | None = None,
    mode: Mode = "link",
    apply: bool = False,
    gc: bool = False,
    on_status: StatusFn | None = None,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> Outcome:
    """Report or reclaim legacy copies of blobs.

    Args:
        legacy: Directories to scan. The command's ``--legacy`` flag.
        manifests: App ``mdl.json`` paths. With ``app``, they limit which
            blob digests a legacy file may match. Garbage collection still
            keeps a blob that any discovered manifest names.
        app: Suite app id. Limits the match set the same way as ``manifests``.
        mode: ``link`` replaces a match with a hardlink. ``delete`` removes it.
        apply: Mutate the disk. The default reports the same plan and writes nothing.
        gc: Include unreferenced blobs in the report, and remove them when
            ``apply`` is set.
        on_status: Called with the finished snapshot.
        on_progress: ``(fraction, message)`` for a ``--json`` session.
        cancel: Set to stop before the next file. The file in progress is left
            as the original or as the link.

    Returns:
        An :class:`~voxprint_common.mdl.job.Outcome`. Exit code 0 lists the
        plan even when some files were skipped. ``ready`` is true only after
        ``apply``.
    """
    roots = [Path(path) for path in legacy or ()]
    if not roots and not gc:
        return _fail("cleanup needs --legacy or --gc", on_status)
    for root in roots:
        if not root.is_dir():
            return _fail(f"{root} is not a directory", on_status)
    try:
        allowed = _allowed(manifests, app)
    except (CatalogError, OSError) as exc:
        return _fail(str(exc), on_status)
    state = JobState.READY if apply else JobState.DRY_RUN
    status = Status(state=state)
    status.extra["mode"] = mode
    rows: dict[str, list[_Row]] = {
        "would_link": [],
        "linked": [],
        "would_delete": [],
        "deleted": [],
        "already": [],
        "unique": [],
        "skipped": [],
        "mismatch": [],
        "would_gc": [],
        "gced": [],
        "manifest": [],
        "removed_dirs": [],
    }
    blocked: set[Path] = set()
    replacements: dict[str, str] = {}
    planned: list[tuple[Path, Path, int]] = []
    try:
        files = _files(roots)
        for index, path in enumerate(files):
            if _cancelled(cancel):
                _finish(status, rows)
                status.state = JobState.CANCELLED
                status.error = "cancelled"
                return _publish(status, EXIT_CANCELLED, "cancelled", on_status, on_progress)
            if on_progress is not None:
                on_progress(index / max(1, len(files)), path.name)
            _classify(path, roots, allowed, mode, rows, blocked, replacements, planned)
        if gc:
            _classify_gc(manifests, rows)
        if apply and replacements:
            rows["manifest"] = [
                dict(row) for row in retarget_model_paths(replacements, dry_run=False)
            ]
        elif replacements:
            rows["manifest"] = [
                dict(row) for row in retarget_model_paths(replacements, dry_run=True)
            ]
        if apply:
            _apply(mode, planned, rows, roots, blocked)
            if gc:
                _apply_gc(rows)
    except (CatalogError, ManifestError, OSError) as exc:
        _finish(status, rows)
        status.state = JobState.FAILED
        status.error = str(exc)
        return _publish(status, 2, str(exc), on_status, on_progress)
    _finish(status, rows)
    if not apply:
        status.state = JobState.DRY_RUN
    message = _summary(rows, mode, apply=apply)
    status.extra["summary"] = message
    return _publish(status, EXIT_OK, message, on_status, on_progress)


def _classify(
    path: Path,
    roots: Sequence[Path],
    allowed: set[str] | None,
    mode: Mode,
    rows: dict[str, list[_Row]],
    blocked: set[Path],
    replacements: dict[str, str],
    planned: list[tuple[Path, Path, int]],
) -> None:
    if path.is_symlink():
        rows["skipped"].append({"path": str(path), "bytes": _size(path), "reason": "symlink"})
        _block(path, blocked)
        return
    if not _inside_any(path, roots):
        rows["skipped"].append({"path": str(path), "bytes": _size(path), "reason": "outside"})
        _block(path, blocked)
        return
    try:
        digest = sha256_file(path)
        blob = models_dir() / "blobs" / digest
        if allowed is not None and digest not in allowed:
            rows["unique"].append({"path": str(path), "bytes": path.stat().st_size})
            _block(path, blocked)
            return
        if not blob.is_file() or blob.is_symlink():
            rows["unique"].append({"path": str(path), "bytes": path.stat().st_size})
            _block(path, blocked)
            return
        if sha256_file(blob) != digest:
            rows["mismatch"].append(
                {"path": str(path), "bytes": path.stat().st_size, "blob": str(blob)}
            )
            _block(path, blocked)
            return
        if os.path.samefile(path, blob):
            rows["already"].append(
                {"path": str(path), "bytes": path.stat().st_size, "blob": str(blob)}
            )
            return
    except OSError:
        rows["skipped"].append(
            {"path": str(path), "bytes": _size(path), "reason": "could not read"}
        )
        _block(path, blocked)
        return
    size = path.stat().st_size
    row = {"path": str(path), "bytes": size, "blob": str(blob)}
    match mode:
        case "link":
            rows["would_link"].append(row)
        case "delete":
            rows["would_delete"].append(row)
        case _ as unreachable:
            assert_never(unreachable)
    replacements[str(path)] = str(blob)
    planned.append((path, blob, size))


def _apply(
    mode: Mode,
    planned: Sequence[tuple[Path, Path, int]],
    rows: dict[str, list[_Row]],
    roots: Sequence[Path],
    blocked: set[Path],
) -> None:
    for path, blob, size in planned:
        row = {"path": str(path), "bytes": size, "blob": str(blob)}
        match mode:
            case "link":
                how = _replace_with_link(blob, path)
                rows["would_link"].remove(row)
                if how == "linked":
                    rows["linked"].append(row)
                elif how == "already":
                    rows["already"].append(row)
                else:
                    rows["skipped"].append({**row, "reason": "link failed"})
                    _block(path, blocked)
            case "delete":
                if not _verified_pair(path, blob):
                    rows["would_delete"].remove(row)
                    rows["skipped"].append({**row, "reason": "not verified"})
                    _block(path, blocked)
                    continue
                path.unlink()
                rows["deleted"].append(row)
                rows["would_delete"].remove(row)
            case _ as unreachable:
                assert_never(unreachable)
    if mode == "delete":
        rows["removed_dirs"] = [{"path": path} for path in _remove_empty(roots, blocked)]


def _replace_with_link(blob: Path, dest: Path) -> str:
    """Point ``dest`` at ``blob`` without a window where ``dest`` is missing.

    The temporary name is created in ``dest``'s directory, then renamed over
    ``dest``. A failed ``os.link`` leaves ``dest`` unchanged and copies nothing.
    """
    try:
        if dest.exists() and os.path.samefile(blob, dest):
            return "already"
    except OSError:
        return "skipped"
    temporary = dest.with_name(dest.name + ".voxprint-link")
    try:
        if temporary.exists() or temporary.is_symlink():
            if temporary.is_symlink() or not os.path.samefile(temporary, blob):
                return "skipped"
            temporary.unlink()
        os.link(blob, temporary)
    except OSError:
        return "skipped"
    try:
        os.replace(temporary, dest)
    except OSError:
        return "skipped"
    return "linked"


def _verified_pair(path: Path, blob: Path) -> bool:
    """Re-read the file and the blob. The hash cache is not consulted."""
    try:
        if path.is_symlink() or not path.is_file() or blob.is_symlink() or not blob.is_file():
            return False
        digest = sha256_file(path)
        return digest == blob.name and sha256_file(blob) == digest
    except OSError:
        return False


def _classify_gc(manifests: Sequence[Path] | None, rows: dict[str, list[_Row]]) -> None:
    referenced = _referenced(manifests)
    folder = models_dir() / "blobs"
    if not folder.is_dir():
        return
    for blob in sorted(folder.iterdir(), key=lambda item: item.name):
        if not blob.is_file() or blob.is_symlink() or HASH_RE.fullmatch(blob.name) is None:
            continue
        if blob.name in referenced:
            continue
        rows["would_gc"].append(
            {"path": str(blob), "bytes": blob.stat().st_size, "sha256": blob.name}
        )


def _apply_gc(rows: dict[str, list[_Row]]) -> None:
    for row in list(rows["would_gc"]):
        blob = Path(str(row["path"]))
        blob.unlink()
        marker = blob.with_name(blob.name + ".verified")
        if marker.is_file():
            marker.unlink()
        rows["gced"].append(row)
        rows["would_gc"].remove(row)


def _referenced(manifests: Sequence[Path] | None) -> set[str]:
    found: set[str] = set()
    root = models_dir()
    blobs = root / "blobs"
    inodes: dict[tuple[int, int], str] = {}
    if blobs.is_dir():
        for blob in blobs.iterdir():
            if blob.is_file() and not blob.is_symlink() and HASH_RE.fullmatch(blob.name):
                info = blob.stat()
                inodes[(info.st_dev, info.st_ino)] = blob.name
    views = root / "views"
    if views.is_dir():
        for path in views.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            info = path.stat()
            digest = inodes.get((info.st_dev, info.st_ino))
            if digest:
                found.add(digest)
            if path.name != ".voxprint-view.json":
                continue
            found.update(_marker_digests(path))
    for component in load().components.values():
        digest = component.sha256.strip().lower()
        if HASH_RE.fullmatch(digest):
            found.add(digest)
        name = Path(component.path).name.lower()
        if HASH_RE.fullmatch(name):
            found.add(name)
    for path in _gc_manifests(manifests):
        document = load_manifest(path)
        for model in document.models:
            for item in model.files:
                found.add(item.sha256.lower())
    return found


def _marker_digests(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return set()
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        return set()
    found: set[str] = set()
    for value in files.values():
        if isinstance(value, str) and HASH_RE.fullmatch(value.strip().lower()):
            found.add(value.strip().lower())
    return found


def _gc_manifests(manifests: Sequence[Path] | None) -> list[Path]:
    found = [Path(path) for path in manifests or ()]
    seen = {path.resolve() for path in found if path.exists()}
    for app in discover():
        if app.path.resolve() not in seen:
            found.append(app.path)
    return found


def _allowed(manifests: Sequence[Path] | None, app: str | None) -> set[str] | None:
    paths = [Path(path) for path in manifests or ()]
    if not paths and app:
        paths = [item.path for item in discover() if item.id == app]
        if not paths:
            raise CatalogError(f"no installed manifest for {app}")
    if not paths and not app:
        return None
    digests: set[str] = set()
    for path in paths:
        document = load_manifest(path)
        if app and document.app != app:
            raise CatalogError(f"{path} is not the manifest for {app}")
        for model in document.models:
            for item in model.files:
                digests.add(item.sha256.lower())
    return digests


def _files(roots: Sequence[Path]) -> list[Path]:
    found: list[Path] = []
    blobs = (models_dir() / "blobs").resolve()
    views = (models_dir() / "views").resolve()
    for root in roots:
        for folder, _dirs, names in os.walk(root, followlinks=False):
            current = Path(folder)
            if _under(current, blobs) or _under(current, views):
                continue
            for name in names:
                path = current / name
                if path.is_symlink():
                    found.append(path)
                    continue
                if path.is_file():
                    found.append(path)
    return sorted(found, key=lambda item: str(item))


def _under(path: Path, root: Path) -> bool:
    try:
        return path.resolve() == root or root in path.resolve().parents
    except OSError:
        return False


def _inside_any(path: Path, roots: Sequence[Path]) -> bool:
    try:
        target = os.path.abspath(path)
    except OSError:
        return False
    for root in roots:
        base = os.path.abspath(root)
        try:
            if os.path.commonpath([base, target]) == base:
                return True
        except ValueError:
            continue
    return False


def _block(path: Path, blocked: set[Path]) -> None:
    blocked.add(path.parent.resolve())


def _remove_empty(roots: Sequence[Path], blocked: set[Path]) -> list[str]:
    removed: list[str] = []
    blocked_resolved = {path.resolve() for path in blocked}
    for root in roots:
        for folder, _dirs, _names in os.walk(root, topdown=False, followlinks=False):
            current = Path(folder)
            try:
                resolved = current.resolve()
            except OSError:
                continue
            if resolved in blocked_resolved:
                continue
            try:
                if any(current.iterdir()):
                    continue
                current.rmdir()
            except OSError:
                continue
            removed.append(str(current))
    return removed


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _finish(status: Status, rows: Mapping[str, list[_Row]]) -> None:
    for key, value in rows.items():
        status.extra[key] = list(value)
    seen: set[str] = set()
    total = 0
    for key in ("would_link", "linked", "would_delete", "deleted", "would_gc", "gced"):
        for row in rows[key]:
            path = str(row["path"])
            if path in seen:
                continue
            seen.add(path)
            total += int(row["bytes"])
    status.extra["reclaimable_bytes"] = total


def _summary(rows: Mapping[str, list[_Row]], mode: Mode, *, apply: bool) -> str:
    if apply:
        head = (
            f"cleanup {mode}: linked {len(rows['linked'])}, deleted {len(rows['deleted'])}, "
            f"unique {len(rows['unique'])}, skipped {len(rows['skipped'])}, "
            f"gc {len(rows['gced'])}"
        )
    else:
        head = (
            f"cleanup dry-run {mode}: would link {len(rows['would_link'])}, "
            f"would delete {len(rows['would_delete'])}, unique {len(rows['unique'])}, "
            f"skipped {len(rows['skipped'])}, would gc {len(rows['would_gc'])}"
        )
    lines = [head]
    for key in (
        "would_link",
        "linked",
        "would_delete",
        "deleted",
        "unique",
        "mismatch",
        "skipped",
        "would_gc",
        "gced",
    ):
        for row in rows[key]:
            blob = row.get("blob", row.get("sha256", ""))
            lines.append(f"{key} {row['path']} {row.get('bytes', 0)} {blob}".rstrip())
    return "\n".join(lines)
