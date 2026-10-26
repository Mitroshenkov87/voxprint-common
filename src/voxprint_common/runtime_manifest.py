"""Shared runtime manifest, schema 1.

The file is ``<home>/shared/manifest.json``. Top-level keys are ``schema``
and ``components``. Any other top-level key is preserved.

A component id matches ``[a-z0-9][a-z0-9._-]*``. Model files use
``model-`` plus the first 12 hex characters of the file sha256. A side
virtualenv uses ``runtime-py<major><minor>-<stack>-cu<version>``, for
example ``runtime-py311-nunchaku-cu130`` and ``runtime-py314-torch2.11-cu130``.
``refs`` lists the apps that installed the component. The component is
deleted only when that list becomes empty.
A ``model`` path may be absolute when it stays under the shared models
directory or a configured cache root. Every other absolute path is rejected.
Writes take ``manifest.lock`` (exclusive, 30 seconds) and publish through a
temporary file, fsync, and rename.

Readers migrate two older shapes and write schema 1 back:

* ``version`` plus ``resources`` (Canvas, and Plotweaver's extra ``apps`` key).
  Each resource becomes a component. ``apps`` becomes ``refs``. The old keys
  stay in the file.
* ``schema`` plus ``components`` whose entries say ``holders`` instead of
  ``refs``. ``holders`` is renamed to ``refs``.

Installing the CPU ``onnxruntime`` distribution into the shared runtime is
forbidden. It shadows ``onnxruntime-gpu``.
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import json
import logging
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from voxprint_common.atomic import exclusive_lock, write_json
from voxprint_common.model_users import entry_blocks, hold_users, load_users
from voxprint_common.paths import (
    cache_dir,
    home,
    manifest_file,
    models_dir,
    projects_dir,
    runtime_dir,
    shared_dir,
    state_dir,
    trt_cache_dir,
    voices_dir,
)

__all__ = [
    "KINDS",
    "SCHEMA",
    "Component",
    "Manifest",
    "ManifestConflict",
    "ManifestError",
    "ReleaseResult",
    "add_ref",
    "cpu_onnxruntime_conflicts",
    "load",
    "lock_path",
    "registered_paths_match",
    "release_component",
    "remember_model_stat",
    "remove_ref",
    "repair_manifest",
    "retarget_model_paths",
    "runtime_component_id",
]

logger = logging.getLogger(__name__)

SCHEMA = 1
KINDS = frozenset({"runtime", "wheels", "model", "tool"})
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_APP = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_TIMEOUT = 30.0
_KNOWN = frozenset({"kind", "version", "path", "sha256", "installed_at", "refs", "holders", "apps"})


class ManifestError(ValueError):
    """The manifest is damaged, or a field is not valid for schema 1."""


class ManifestConflict(ManifestError):
    """Another app still references a different copy of this component."""


@dataclass
class Component:
    """One installed component."""

    kind: str
    version: str
    path: str
    sha256: str
    installed_at: str
    refs: list[str]
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON object, including preserved unknown keys."""
        body: dict[str, Any] = {
            "kind": self.kind,
            "version": self.version,
            "path": self.path,
            "sha256": self.sha256,
            "installed_at": self.installed_at,
            "refs": list(self.refs),
        }
        for key, value in self.extra.items():
            if key not in body:
                body[key] = value
        return body


@dataclass
class Manifest:
    """A schema 1 document."""

    components: dict[str, Component] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON object. Unknown top-level keys are kept."""
        body: dict[str, Any] = {
            "schema": SCHEMA,
            "components": {key: value.to_dict() for key, value in self.components.items()},
        }
        for key, value in self.extra.items():
            if key not in body:
                body[key] = value
        return body


def lock_path(path: Path | None = None) -> Path:
    """Return ``manifest.lock`` beside the manifest.

    Args:
        path: Manifest path. Defaults to the suite file.
    """
    return (path or manifest_file()).with_name("manifest.lock")


def load(path: Path | None = None, *, timeout: float = _TIMEOUT) -> Manifest:
    """Read the manifest, migrating an older shape in place.

    A missing file is an empty schema 1 document and is not created.

    Args:
        path: Manifest path. Defaults to :func:`voxprint_common.paths.manifest_file`.
        timeout: Seconds to wait for ``manifest.lock``.

    Returns:
        The schema 1 document.

    Raises:
        ManifestError: The file is not a JSON object, or ``schema`` is not 1.
        LockTimeout: The lock was not acquired in time.
    """
    target = path or manifest_file()
    if not target.is_file():
        return Manifest()
    with exclusive_lock(lock_path(target), timeout=timeout):
        return _load_migrate(target)


def runtime_component_id(major: int, minor: int, stack: str, cuda: str) -> str:
    """Return the component id for one side virtualenv.

    The shape is ``runtime-py311-nunchaku-cu130``. ``stack`` is a short name
    such as ``nunchaku`` or ``torch2.11``. ``cuda`` is the toolkit digits,
    ``130`` or ``cu130``.

    Args:
        major: Python major version.
        minor: Python minor version.
        stack: Runtime stack name.
        cuda: CUDA version digits.

    Raises:
        ManifestError: A part is not a valid component token.
    """
    if isinstance(major, bool) or isinstance(minor, bool):
        raise ManifestError("python version must be integers")
    if not isinstance(major, int) or not isinstance(minor, int) or major < 1 or minor < 0:
        raise ManifestError("python version must be a positive major and a minor")
    stack_id = stack.strip()
    if re.fullmatch(r"[a-z0-9]+(?:\.[0-9]+)*", stack_id) is None:
        raise ManifestError(f"runtime stack {stack!r} is not a component token")
    digits = cuda.strip().removeprefix("cu")
    if not digits.isdigit():
        raise ManifestError(f"cuda version {cuda!r} must be digits")
    return f"runtime-py{major}{minor}-{stack_id}-cu{digits}"


def add_ref(
    component_id: str,
    app: str,
    *,
    kind: str,
    version: str,
    path: str,
    sha256: str,
    installed_at: str | None = None,
    manifest: Path | None = None,
    timeout: float = _TIMEOUT,
    model_roots: Sequence[Path] | None = None,
    files: Sequence[Mapping[str, object]] | None = None,
) -> Component:
    """Add ``app`` to a component, installing the record when it is new.

    The same id, version, and sha256 is reused. A different version or digest
    is written only when no other app still refs the existing component.
    Otherwise :class:`ManifestConflict` is raised and the caller picks a new id
    and a new path. An existing component is never overwritten while another
    app refs it.

    Args:
        component_id: Component id.
        app: Suite id to add.
        kind: ``runtime``, ``wheels``, ``model``, or ``tool``.
        version: Component version.
        path: Path relative to the shared directory, or, for a ``model``, an
            absolute path under the shared models directory or a configured
            cache root.
        sha256: Lowercase digest of the archive or lock file.
        installed_at: ISO-8601 timestamp. Defaults to the current local time.
        manifest: Manifest path.
        timeout: Lock timeout in seconds.
        model_roots: Extra absolute directories a ``model`` path may live in,
            besides :func:`voxprint_common.paths.models_dir` and
            ``VOXPRINT_MODEL_CACHE``.
        files: Optional size list for a runtime directory. Each item has
            ``path`` (relative) and ``size`` (bytes). Stored on the component
            so a later presence check can see a truncated runtime.

    Returns:
        The component after the update.

    Raises:
        ManifestError: A field is invalid.
        ManifestConflict: Another app refs a different version or digest.
        LockTimeout: The lock was not acquired in time.
    """
    component_id = _check_id(component_id, new=True)
    app = _check_app(app)
    kind = _check_kind(kind)
    version = _required_text(version, "version")
    if path.strip():
        _refuse_protected(_resolve_registered_path(path.strip().replace("\\", "/")), path.strip())
    rel = _check_component_path(path, kind=kind, model_roots=_model_roots(model_roots))
    digest = _check_sha(sha256, required=True)
    listed = None if files is None else _stored_files(files)
    stamp = installed_at if installed_at is not None else _now()
    target = manifest or manifest_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(lock_path(target), timeout=timeout):
        document = _load_migrate(target) if target.is_file() else Manifest()
        resolved = _resolve_registered_path(rel)
        _refuse_protected(resolved, rel)
        _refuse_overlap(resolved, document, component_id)
        current = document.components.get(component_id)
        if current is None:
            created = Component(kind, version, rel, digest, stamp, [app])
            if listed is not None:
                created.extra["files"] = listed
            document.components[component_id] = created
            _write(target, document)
            return created
        if current.version == version and current.sha256 == digest:
            changed = False
            if app not in current.refs:
                current.refs.append(app)
                changed = True
            if listed is not None and current.extra.get("files") != listed:
                current.extra["files"] = listed
                changed = True
            if current.path != rel:
                current.path = rel
                changed = True
            if changed:
                _write(target, document)
            return current
        others = [ref for ref in current.refs if ref != app]
        if others:
            who = ", ".join(others)
            raise ManifestConflict(
                f"component {component_id} is version {current.version} "
                f"sha256 {current.sha256 or '(none)'} held by {who}; "
                f"refusing to overwrite it for {app}"
            )
        current.kind = kind
        current.version = version
        current.path = rel
        current.sha256 = digest
        current.installed_at = stamp
        current.refs = [app]
        if listed is not None:
            current.extra["files"] = listed
        _write(target, document)
        return current


def remember_model_stat(
    component_id: str,
    size: int,
    mtime_ns: int,
    *,
    manifest: Path | None = None,
    timeout: float = _TIMEOUT,
) -> None:
    """Store a file's size and mtime on an installed component.

    The manage window compares these two numbers to ``stat`` and does not hash.
    A missing component is left unchanged. The file itself is not read.

    Args:
        component_id: Component id, such as ``model-`` plus a digest prefix.
        size: File size in bytes.
        mtime_ns: ``st_mtime_ns`` from ``stat``.
        manifest: Manifest path. Defaults to the suite file.
        timeout: Seconds to wait for ``manifest.lock``.
    """
    if not component_id or isinstance(size, bool) or isinstance(mtime_ns, bool):
        return
    if not isinstance(size, int) or not isinstance(mtime_ns, int):
        return
    target = manifest or manifest_file()
    if not target.is_file():
        return
    with exclusive_lock(lock_path(target), timeout=timeout):
        document = _load_migrate(target)
        current = document.components.get(component_id)
        if current is None:
            return
        if current.extra.get("size") == size and current.extra.get("mtime_ns") == mtime_ns:
            return
        current.extra["size"] = size
        current.extra["mtime_ns"] = mtime_ns
        _write(target, document)


def remove_ref(
    component_id: str,
    app: str,
    *,
    manifest: Path | None = None,
    timeout: float = _TIMEOUT,
) -> bool:
    """Remove ``app`` from a component.

    The component is deleted when its ref list becomes empty.

    Args:
        component_id: Component id.
        app: Suite id to remove.
        manifest: Manifest path.
        timeout: Lock timeout in seconds.

    Returns:
        True when the component was deleted. False when it remains, or when
        the app was not a ref.

    Raises:
        LockTimeout: The lock was not acquired in time.
    """
    app = _check_app(app)
    target = manifest or manifest_file()
    if not target.is_file():
        return False
    with exclusive_lock(lock_path(target), timeout=timeout):
        document = _load_migrate(target)
        current = document.components.get(component_id)
        if current is None or app not in current.refs:
            return False
        current.refs = [ref for ref in current.refs if ref != app]
        if current.refs:
            _write(target, document)
            return False
        del document.components[component_id]
        _write(target, document)
        return True


@dataclass(frozen=True)
class ReleaseResult:
    """What a release did to one component."""

    deleted: bool
    ref_dropped: bool
    message: str


def release_component(
    component_id: str,
    app: str,
    *,
    manifest: Path | None = None,
    timeout: float = _TIMEOUT,
    dry_run: bool = False,
    users: Path | None = None,
) -> ReleaseResult:
    """Drop ``app`` and delete the folder only when every safety check passes.

    A delete is allowed only for a directory that is a direct child of the
    models root, matches this component, has no remaining refs in the shared
    manifest or in ``models/.users.json``, is not a protected root, and does
    not contain another component. Anything else drops the ref and leaves the
    files in place.

    Args:
        component_id: Component id.
        app: Suite id to remove.
        manifest: Manifest path.
        timeout: Lock timeout in seconds.
        dry_run: Report the decision and do not write or delete.
        users: ``.users.json`` path. Defaults to the file in the models root.

    Returns:
        Whether the folder was removed and whether the ref was dropped.
    """
    app = _check_app(app)
    target = manifest or manifest_file()
    if not target.is_file():
        return ReleaseResult(False, False, f"no manifest entry for {component_id}")
    if dry_run:
        return _release_locked(target, component_id, app, users, timeout, dry_run=True)
    with hold_users(users, timeout=timeout) as users_document:
        return _release_locked(
            target,
            component_id,
            app,
            users,
            timeout,
            dry_run=False,
            users_document=users_document,
        )


def registered_paths_match(recorded: str, candidate: str) -> bool:
    """Return whether two manifest paths name the same file.

    Separators and drive-letter case follow Windows rules, including when
    this process is not running on Windows. A drive-relative path does not
    match a rooted path.

    Args:
        recorded: Path stored in ``shared/manifest.json``.
        candidate: Path found on disk.
    """
    left = recorded.strip()
    right = candidate.strip()
    if not left or not right:
        return False
    if PureWindowsPath(left).as_posix().casefold() == PureWindowsPath(right).as_posix().casefold():
        return True
    if _windows_anchor(left) != _windows_anchor(right):
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False


def _windows_anchor(text: str) -> tuple[str, str]:
    """Return the drive and a rooted flag, so ``C:name`` is not ``C:\\name``."""
    path = PureWindowsPath(text)
    return path.drive.casefold(), "\\" if path.root else ""


def retarget_model_paths(
    replacements: Mapping[str, str],
    *,
    manifest: Path | None = None,
    timeout: float = _TIMEOUT,
    dry_run: bool = False,
) -> list[dict[str, str]]:
    """Point model components from a legacy file to its blob.

    This is the migration that ships with cleanup. The previous path is kept
    on the component as ``retargeted_from``. Schema stays 1. A dry run reports
    the same rows and does not write.

    Args:
        replacements: Legacy path to blob path.
        manifest: Manifest path. Defaults to the suite file.
        timeout: Lock timeout in seconds.
        dry_run: Report matches and do not write.

    Returns:
        One row per component that is, or would be, retargeted.
    """
    target = manifest or manifest_file()
    if not target.is_file() or not replacements:
        return []
    with exclusive_lock(lock_path(target), timeout=timeout):
        document = _load_migrate(target)
        notes: list[dict[str, str]] = []
        changed = False
        for component_id, component in document.components.items():
            if component.kind != "model" or not component.path:
                continue
            blob = ""
            for legacy, dest in replacements.items():
                if registered_paths_match(component.path, legacy):
                    blob = dest
                    break
            if not blob or registered_paths_match(component.path, blob):
                continue
            stored = _check_component_path(blob, kind="model", model_roots=_model_roots(None))
            notes.append({"component": component_id, "from": component.path, "to": stored})
            if dry_run:
                continue
            component.extra["retargeted_from"] = component.path
            component.path = stored
            changed = True
        if changed:
            _write(target, document)
        return notes


def repair_manifest(
    *,
    manifest: Path | None = None,
    timeout: float = _TIMEOUT,
    dry_run: bool = False,
) -> list[str]:
    """Clear component paths that could delete a protected or shared directory.

    The files on disk are never removed. A neutralised component keeps its
    refs and records the old path in ``neutralised_from``.

    Args:
        manifest: Manifest path.
        timeout: Lock timeout in seconds.
        dry_run: Report the paths and do not write.

    Returns:
        One line per component that is, or would be, neutralised.
    """
    target = manifest or manifest_file()
    if not target.is_file():
        return []
    with exclusive_lock(lock_path(target), timeout=timeout):
        document = _load_migrate(target)
        notes: list[str] = []
        changed = False
        for component_id, component in document.components.items():
            if not component.path:
                continue
            resolved = _resolve_registered_path(component.path)
            why = _neutralise_reason(component_id, resolved, document)
            if not why:
                continue
            notes.append(f"neutralised {component_id}: {why}")
            if dry_run:
                continue
            component.extra["neutralised_from"] = component.path
            component.path = ""
            changed = True
        if changed:
            _write(target, document)
        return notes


def cpu_onnxruntime_conflicts() -> bool:
    """Return whether the CPU ``onnxruntime`` distribution is installed.

    The shared runtime may contain ``onnxruntime-gpu`` only. The CPU wheel
    uses the same import name and shadows the GPU build.
    """
    names: set[str] = set()
    for dist in importlib_metadata.distributions():
        meta = dist.metadata
        raw = meta.get("Name") if meta is not None else None
        if isinstance(raw, str) and raw:
            names.add(raw.lower())
    return "onnxruntime" in names


def _release_locked(
    target: Path,
    component_id: str,
    app: str,
    users: Path | None,
    timeout: float,
    *,
    dry_run: bool,
    users_document: dict[str, Any] | None = None,
) -> ReleaseResult:
    """Drop one ref under the manifest lock, then delete only a safe folder."""
    snapshot = users_document if users_document is not None else load_users(users)
    folder: Path | None = None
    reason = ""
    with exclusive_lock(lock_path(target), timeout=timeout):
        document = _load_migrate(target)
        current = document.components.get(component_id)
        if current is None or app not in current.refs:
            return ReleaseResult(False, False, f"{app} does not reference {component_id}")
        remaining = [ref for ref in current.refs if ref != app]
        folder = _resolve_registered_path(current.path) if current.path else None
        if remaining:
            reason = f"still referenced by {', '.join(remaining)}"
        else:
            reason = _delete_block(component_id, current, document, folder, snapshot)
        if dry_run:
            if reason:
                return ReleaseResult(
                    False,
                    True,
                    f"would drop {app} from {component_id} and leave the files ({reason})",
                )
            return ReleaseResult(True, True, f"would remove {folder}")
        if remaining:
            current.refs = remaining
        else:
            del document.components[component_id]
        _write(target, document)
    if reason:
        logger.warning(
            "release left %s in place after dropping %s from %s: %s",
            folder or component_id,
            app,
            component_id,
            reason,
        )
        return ReleaseResult(
            False,
            True,
            f"dropped {app} from {component_id}; left the files in place ({reason})",
        )
    if folder is not None and folder.is_dir():
        shutil.rmtree(folder)
    elif folder is not None and folder.is_file():
        folder.unlink()
    return ReleaseResult(True, True, f"removed {folder}")


def _delete_block(
    component_id: str,
    current: Component,
    document: Manifest,
    folder: Path | None,
    users_document: Mapping[str, Any],
) -> str:
    """Return why ``folder`` must not be deleted. An empty string allows it."""
    if folder is None:
        return "the component has no path"
    hit = _protected_hit(folder)
    if hit:
        return f"the path is or contains the {hit} root"
    if _contains_other(folder, document, component_id):
        return "the path contains another component"
    if any(entry_blocks(users_document.get(key)) for key in (component_id, folder.name)):
        return "models/.users.json still lists a user"
    models = models_dir().resolve()
    blobs = (models / "blobs").resolve()
    if folder.is_dir() and _same_path(folder.parent, models) and folder.name not in {"blobs", "."}:
        if current.kind != "model":
            return "only a model folder inside the models root can be removed"
        return ""
    if folder.is_file() and _same_path(folder.parent, blobs):
        return ""
    return "the path is not a per-model folder directly inside the models root"


def _neutralise_reason(component_id: str, resolved: Path, document: Manifest) -> str:
    """Return why this path must be cleared, or an empty string."""
    hit = _protected_hit(resolved)
    if hit:
        return f"{resolved} is or contains the {hit} root"
    for key, component in document.components.items():
        if key == component_id or not component.path:
            continue
        other = _resolve_registered_path(component.path)
        if resolved == other or _strict_contains(resolved, other):
            return f"{resolved} overlaps component {key} at {other}"
    return ""


def _contains_other(folder: Path, document: Manifest, component_id: str) -> bool:
    for key, component in document.components.items():
        if key == component_id or not component.path:
            continue
        other = _resolve_registered_path(component.path)
        if other == folder or _strict_contains(folder, other):
            return True
    return False


def _refuse_protected(resolved: Path, shown: str) -> None:
    hit = _protected_hit(resolved)
    if hit:
        raise ManifestError(f"refusing to register {shown}: the path is or contains the {hit} root")


def _refuse_overlap(resolved: Path, document: Manifest, component_id: str) -> None:
    for key, component in document.components.items():
        if key == component_id or not component.path:
            continue
        other = _resolve_registered_path(component.path)
        if (
            resolved == other
            or _strict_contains(resolved, other)
            or _strict_contains(other, resolved)
        ):
            raise ManifestError(
                f"refusing to register {resolved}: it overlaps component {key} at {other}"
            )


def _protected_hit(path: Path) -> str:
    resolved = path.resolve()
    roots = (
        ("suite home", home()),
        ("models", models_dir()),
        ("model blobs", models_dir() / "blobs"),
        ("runtime", runtime_dir()),
        ("voices", voices_dir()),
        ("projects", projects_dir()),
        ("cache", cache_dir()),
        ("trt cache", trt_cache_dir()),
        ("state", state_dir()),
    )
    for name, root in roots:
        if _same_or_ancestor(resolved, root.resolve()):
            return name
    return ""


def _resolve_registered_path(text: str) -> Path:
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate.resolve()
    return (shared_dir() / candidate).resolve()


def _same_or_ancestor(path: Path, root: Path) -> bool:
    left = os.path.normcase(str(path))
    right = os.path.normcase(str(root))
    return left == right or right.startswith(left + os.sep)


def _strict_contains(outer: Path, inner: Path) -> bool:
    base = os.path.normcase(str(outer))
    cand = os.path.normcase(str(inner))
    return cand.startswith(base + os.sep)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _stored_files(files: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in files:
        raw = item.get("path")
        rel = raw.strip() if isinstance(raw, str) else ""
        if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise ManifestError("component file list paths must stay inside the component")
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ManifestError("component file list sizes must be byte counts")
        rows.append({"path": Path(rel).as_posix(), "size": size})
    return rows


def _load_migrate(path: Path) -> Manifest:
    document, changed = _migrate(_read(path))
    if changed:
        _write(path, document)
    return document


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ManifestError(f"{path.name} could not be read") from exc
    if not isinstance(payload, dict):
        raise ManifestError(f"{path.name} must be a JSON object")
    schema = payload.get("schema")
    if schema not in (None, SCHEMA):
        raise ManifestError(f"unsupported manifest schema {schema}")
    return payload


def _migrate(data: dict[str, Any]) -> tuple[Manifest, bool]:
    changed = False
    components_raw = data.get("components")
    resources = data.get("resources")
    if not isinstance(components_raw, dict):
        if isinstance(resources, dict):
            components_raw = {
                str(key): _from_resource(value) for key, value in resources.items() if _row(value)
            }
            changed = True
        else:
            if "components" in data or "resources" in data:
                raise ManifestError("manifest components must be an object")
            components_raw = {}
    components: dict[str, Component] = {}
    for key, value in components_raw.items():
        if not _row(value):
            raise ManifestError(f"component {key} is not an object")
        component, row_changed = _from_component(str(key), value)
        components[str(key)] = component
        changed = changed or row_changed
    if data.get("schema") != SCHEMA:
        changed = True
    extra = {key: value for key, value in data.items() if key not in {"schema", "components"}}
    return Manifest(components, extra), changed


def _from_resource(row: Mapping[str, Any]) -> dict[str, Any]:
    files = row.get("files") if isinstance(row.get("files"), dict) else {}
    digest = ""
    if len(files) == 1:
        only = str(next(iter(files.values()))).strip().lower()
        if _SHA.fullmatch(only):
            digest = only
    body = {
        "kind": str(row.get("kind") or "tool"),
        "version": str(row.get("version") or ""),
        "path": str(row.get("path") or ""),
        "sha256": digest,
        "installed_at": str(row.get("installed_at") or ""),
        "refs": _names(row.get("apps") if "apps" in row else row.get("refs")),
    }
    for key, value in row.items():
        if key not in body and key not in {"apps", "refcount"}:
            body[key] = value
    return body


def _from_component(component_id: str, row: Mapping[str, Any]) -> tuple[Component, bool]:
    changed = "holders" in row or "refs" not in row
    refs = _names(row.get("refs"))
    if "holders" in row:
        for name in _names(row.get("holders")):
            if name not in refs:
                refs.append(name)
        changed = True
    elif "apps" in row and "refs" not in row:
        refs = _names(row.get("apps"))
        changed = True
    kind = row.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        kind = _guess_kind(component_id)
        changed = True
    version = row.get("version")
    path = row.get("path")
    sha = row.get("sha256")
    installed = row.get("installed_at")
    if not isinstance(version, str):
        version = "" if version is None else str(version)
        changed = True
    if not isinstance(path, str):
        path = "" if path is None else str(path)
        changed = True
    if not isinstance(sha, str):
        sha = ""
        changed = True
    else:
        sha = sha.strip().lower()
    if not isinstance(installed, str):
        installed = "" if installed is None else str(installed)
        changed = True
    extra = {key: value for key, value in row.items() if key not in _KNOWN}
    return Component(str(kind), version, path, sha, installed, refs, extra), changed


def _guess_kind(component_id: str) -> str:
    if (
        component_id == "model"
        or component_id.startswith("model-")
        or component_id.startswith("model.")
    ):
        return "model"
    return "runtime"


def _write(path: Path, document: Manifest) -> None:
    write_json(path, document.to_dict())


def _names(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in names:
            names.append(item.strip())
    return names


def _row(value: object) -> bool:
    return isinstance(value, Mapping)


def _check_id(value: str, *, new: bool) -> str:
    text = value.strip()
    if new and _ID.fullmatch(text) is None:
        raise ManifestError(f"component id {value!r} must match [a-z0-9][a-z0-9._-]*")
    return text


def _check_app(value: str) -> str:
    text = value.strip()
    if _APP.fullmatch(text) is None:
        raise ManifestError(f"app id {value!r} is not a suite id")
    return text


def _check_kind(value: str) -> str:
    text = value.strip()
    if text not in KINDS:
        raise ManifestError("kind must be runtime, wheels, model, or tool")
    return text


def _required_text(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise ManifestError(f"{label} is required")
    return text


def _model_roots(extra: Sequence[Path] | None) -> tuple[Path, ...]:
    roots: list[Path] = [models_dir(), shared_dir()]
    cache = os.environ.get("VOXPRINT_MODEL_CACHE", "").strip()
    if cache:
        folder = Path(os.path.expandvars(os.path.expanduser(cache)))
        if not folder.is_absolute():
            raise ManifestError("VOXPRINT_MODEL_CACHE must be an absolute path")
        roots.append(folder)
    for item in extra or ():
        folder = Path(item)
        if not folder.is_absolute():
            raise ManifestError("a model cache root must be an absolute path")
        roots.append(folder)
    return tuple(roots)


def _check_component_path(value: str, *, kind: str, model_roots: Sequence[Path]) -> str:
    text = value.strip().replace("\\", "/")
    if not text:
        raise ManifestError("component path is required")
    parts = Path(text).parts
    if ".." in parts:
        raise ManifestError("component path must stay inside an allowed directory")
    candidate = Path(text)
    if candidate.is_absolute() or text.startswith("/"):
        if kind != "model" or not _inside_any(candidate, model_roots):
            raise ManifestError(
                "component path must stay inside the shared directory, "
                "or under the shared models directory for a model"
            )
        return candidate.absolute().as_posix()
    return candidate.as_posix()


def _inside_any(candidate: Path, roots: Sequence[Path]) -> bool:
    cand = os.path.normcase(str(candidate.absolute()))
    for root in roots:
        base = os.path.normcase(str(Path(root).absolute()))
        if cand == base or cand.startswith(base + os.sep) or cand.startswith(base + "/"):
            return True
    return False


def _check_sha(value: str, *, required: bool) -> str:
    text = value.strip().lower()
    if not text and not required:
        return ""
    if _SHA.fullmatch(text) is None:
        raise ManifestError("sha256 must be 64 hexadecimal characters")
    return text


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
