"""Presence checks that do not trust the status file.

``ready`` in ``mdl-status.json`` is a claim. The files it names can be deleted
after that claim was written. :func:`disk_ready` plans the app's tier and
checks each model file's size, plus each required runtime's stored file list.

:func:`shared_safety_gaps` is the fail-closed gate for the suite's safety
models: it returns every shared safety copy that is missing, corrupt, or
hash-mismatched, keyed by share key (repo+revision), so an app can refuse to
run the action those models guard. It never weakens :func:`gaps_for` or
:func:`disk_ready`; it adds a second, stricter check on top.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from voxprint_common.gpu_probe import GpuDevice
from voxprint_common.hashing import sha256_file
from voxprint_common.mdl.catalog import (
    AppManifest,
    CatalogError,
    Model,
    PlannedFile,
    discover,
    load_manifest,
    model_share_key,
    plan_files,
)
from voxprint_common.mdl.hooks import files_awaiting_hooks
from voxprint_common.mdl.job import _card, detect_tier
from voxprint_common.mdl.presence import cached_digest, remember_digest
from voxprint_common.mdl.present import normalize_group
from voxprint_common.mdl.status import JobState, Status, Step, load_status, save_status
from voxprint_common.paths import models_dir, shared_dir
from voxprint_common.runtime_manifest import Component, load

__all__ = [
    "Gap",
    "SafetyGap",
    "disk_ready",
    "gaps_for",
    "publish_presence",
    "shared_safety_gaps",
]


@dataclass(frozen=True)
class Gap:
    """One model file or runtime component that is missing or short."""

    label: str
    state: str
    kind: str


@dataclass(frozen=True)
class SafetyGap:
    """One shared safety model whose shared copy cannot be trusted.

    ``share_key`` is the repo+revision key (see
    :func:`voxprint_common.mdl.catalog.model_share_key`), so apps can match
    the gap against the model they are about to run. ``state`` is
    ``"missing"`` when no shared copy is on disk and ``"corrupt"`` when a
    file is truncated or its SHA-256 does not match the manifest.
    """

    share_key: str
    model_id: str
    state: str


def disk_ready(
    app: str,
    *,
    manifests: list[Path] | None = None,
    devices: list[GpuDevice] | None = None,
) -> bool:
    """Return whether ``app`` has every file its manifest requires.

    Args:
        app: Suite app id.
        manifests: Manifest paths. Discovered when omitted.
        devices: GPUs used to choose the tier. nvidia-smi is queried when
            omitted.

    A missing manifest is not ready. The status file is not read.
    """
    return not gaps_for(app, manifests=manifests, devices=devices)


def gaps_for(
    app: str,
    *,
    manifests: list[Path] | None = None,
    devices: list[GpuDevice] | None = None,
) -> list[Gap]:
    """Return missing and truncated items for ``app``.

    Args:
        app: Suite app id.
        manifests: Manifest paths. Discovered when omitted.
        devices: GPUs used to choose the tier.

    Model files are checked by size. A runtime component uses the file list
    stored on the component or in ``<path>/.voxprint-files.json``.
    """
    parsed = _manifests(app, manifests)
    if not parsed:
        return [Gap(app, "needs download", "model")]
    cards = list(devices) if devices is not None else None
    tier = detect_tier(cards)
    memory, compute = _card(cards or [])
    files = plan_files(parsed, tier, memory_gib=memory, compute=compute)
    found = [_file_gap(item) for item in files]
    waiting = files_awaiting_hooks(files)
    found.extend(
        Gap(item.label, "needs preparing", "model")
        for item in files
        if id(item) in waiting and _file_gap(item) is None
    )
    found.extend(_runtime_gaps(parsed))
    return [item for item in found if item is not None]


def shared_safety_gaps(
    app: str = "",
    *,
    manifests: list[Path] | None = None,
) -> tuple[SafetyGap, ...]:
    """Return shared safety models whose shared copy fails closed.

    Every safety model (``group`` normalising to ``safety``) from ``app``'s
    manifest — or from every installed app's manifest when ``app`` is empty —
    is checked against the shared store. A copy is reported when any file is
    missing, when a file size differs from the manifest, or when the SHA-256
    mismatches. Results are deduplicated by share key (repo+revision), so a
    model two apps share is reported once. The digest cache is honoured and
    refreshed, so a verified file is not hashed again.

    An app about to run a gated action refuses to run when this returns a
    gap for one of the models that action loads. The gated action is never
    skipped silently: missing, corrupt, and hash-mismatched copies all block.
    ``gaps_for`` / ``disk_ready`` are unchanged; this check is stricter, not
    a bypass.

    Args:
        app: Suite app id restricting which manifests are scanned. Empty
            scans every installed app, which is what a shared store gate
            wants, since another app's safety model can be the shared copy.
        manifests: Manifest paths. Discovered when omitted.

    Returns:
        One :class:`SafetyGap` per bad share key, in stable share-key order.
    """
    parsed = _manifests(app, manifests) if manifests is not None else _safety_manifests(app)
    found: dict[str, SafetyGap] = {}
    for manifest in parsed:
        for model in manifest.models:
            if model.unavailable or normalize_group(model.group) != "safety":
                continue
            key = model_share_key(model)
            if key in found:
                continue
            state = _shared_safety_state(model)
            if state is not None:
                found[key] = SafetyGap(key, model.id, state)
    return tuple(found[key] for key in sorted(found))


def _safety_manifests(app: str) -> list[AppManifest]:
    """Load the manifests of ``app`` or, when empty, every installed app."""
    if app:
        return _manifests(app, None)
    loaded: list[AppManifest] = []
    for found in discover():
        try:
            loaded.append(load_manifest(found.path))
        except CatalogError:
            continue
    return loaded


def _shared_safety_state(model: Model) -> str | None:
    """Return ``missing`` or ``corrupt`` for one safety model, else None."""
    if not model.files:
        return "missing"
    root = models_dir() / "blobs"
    for item in model.files:
        blob = root / item.sha256
        if not blob.is_file():
            return "missing"
        if blob.stat().st_size != item.size:
            return "corrupt"
        if not _blob_matches(blob, item.sha256):
            return "corrupt"
    return None


def _blob_matches(blob: Path, expected: str) -> bool:
    """Return whether ``blob`` hashes to ``expected``.

    A cache hit equal to ``expected`` is trusted. A cache miss, and a cached
    mismatch alike, are settled by a fresh SHA-256: the size/mtime pair can
    be reused by a rapid in-place rewrite, and a stale negative must not be
    allowed to block a healthy safety copy.
    """
    digest = cached_digest(blob)
    if digest is None or digest != expected:
        digest = sha256_file(blob)
        remember_digest(blob, digest)
    return digest == expected


def publish_presence(
    app: str | None = None,
    *,
    devices: list[GpuDevice] | None = None,
) -> Status:
    """Recompute readiness from disk and publish it.

    Args:
        app: Suite app id. When omitted, every discovered app is checked.
        devices: GPUs used to choose the tier.

    The previous status file is the starting snapshot. Gaps replace a stale
    ``ready`` claim. When nothing is installed, the file is returned unchanged.
    """
    status = _existing()
    targets = [app] if app else [item.id for item in discover()]
    if app and not targets:
        status.state = JobState.PLANNED
        status.error = "needs download"
        save_status(status)
        return status
    if not targets:
        return status
    found: list[Gap] = []
    for name in targets:
        if not name:
            continue
        try:
            found.extend(gaps_for(name, devices=devices))
        except CatalogError as exc:
            found.append(Gap(name, "needs download", str(exc)))
    if not found:
        return status
    _apply(status, found)
    save_status(status)
    return status


def _manifests(app: str, manifests: list[Path] | None) -> list[AppManifest]:
    if manifests is not None:
        paths = list(manifests)
    else:
        paths = [item.path for item in discover() if item.id == app]
    parsed = [load_manifest(path) for path in paths]
    if app:
        parsed = [item for item in parsed if item.app == app]
    return parsed


def _file_gap(item: PlannedFile) -> Gap | None:
    path = item.destination
    if not path.is_file():
        return Gap(item.label, "needs download", "model")
    if path.stat().st_size != item.size:
        return Gap(item.label, "needs repair", "model")
    return None


def _runtime_gaps(manifests: list[AppManifest]) -> list[Gap]:
    wanted: list[str] = []
    for manifest in manifests:
        wanted.extend(manifest.runtimes)
        if manifest.app_models is not None:
            wanted.extend(manifest.app_models.runtimes)
    if not wanted:
        return []
    document = load()
    gaps: list[Gap] = []
    for component_id in dict.fromkeys(wanted):
        component = document.components.get(component_id)
        if component is None or not component.path:
            gaps.append(Gap(component_id, "needs download", "runtime"))
            continue
        gaps.extend(_runtime_files(component_id, component))
    return gaps


def _runtime_files(component_id: str, component: Component) -> list[Gap]:
    root = _component_root(component)
    listing = _file_list(component, root)
    if listing is None:
        return [Gap(component_id, "needs repair", "runtime")]
    base = root if root.is_dir() else root.parent
    gaps: list[Gap] = []
    for rel, size in listing:
        target = base / rel
        label = f"{component_id}/{rel}"
        if not target.is_file():
            gaps.append(Gap(label, "needs download", "runtime"))
        elif target.stat().st_size != size:
            gaps.append(Gap(label, "needs repair", "runtime"))
    return gaps


def _component_root(component: Component) -> Path:
    path = Path(component.path)
    if path.is_absolute():
        return path
    return shared_dir() / path


def _file_list(component: Component, root: Path) -> list[tuple[str, int]] | None:
    stored = component.extra.get("files")
    parsed = _rows(stored)
    if parsed is not None:
        return parsed
    for candidate in (root / ".voxprint-files.json", Path(str(root) + ".voxprint-files.json")):
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except OSError, UnicodeError, ValueError:
            return None
        rows = payload.get("files") if isinstance(payload, dict) else payload
        return _rows(rows)
    return None


def _rows(value: object) -> list[tuple[str, int]] | None:
    if not isinstance(value, list) or not value:
        return None
    rows: list[tuple[str, int]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        rel = item.get("path")
        size = item.get("size")
        if not isinstance(rel, str) or isinstance(size, bool) or not isinstance(size, int):
            return None
        if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            return None
        rows.append((rel, size))
    return rows


def _existing() -> Status:
    try:
        return load_status()
    except OSError, ValueError:
        return Status(JobState.IDLE)


def _apply(status: Status, found: list[Gap]) -> None:
    by_label = {gap.label: gap for gap in found}
    seen: set[str] = set()
    for step in status.steps:
        gap = by_label.get(step.model)
        if gap is None:
            gap = by_label.get(step.item_id) or by_label.get(step.display_name)
        if gap is None:
            continue
        step.state = gap.state
        seen.add(gap.label)
    for gap in found:
        if gap.label in seen:
            continue
        status.steps.append(
            Step(
                model=gap.label,
                size=0,
                source=gap.kind,
                speed_bps=0.0,
                eta_s=None,
                state=gap.state,
                received=0,
                sha256="",
                apps=[],
                item_id=gap.label,
                display_name=gap.label,
            )
        )
    status.state = JobState.PLANNED
    status.error = "; ".join(f"{gap.state}: {gap.label}" for gap in found)
    status.recompute()
