"""Presence checks that do not trust the status file.

``ready`` in ``mdl-status.json`` is a claim. The files it names can be deleted
after that claim was written. :func:`disk_ready` plans the app's tier and
checks each model file's size, plus each required runtime's stored file list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from voxprint_common.gpu_probe import GpuDevice
from voxprint_common.mdl.catalog import (
    AppManifest,
    CatalogError,
    PlannedFile,
    discover,
    load_manifest,
    plan_files,
)
from voxprint_common.mdl.hooks import files_awaiting_hooks
from voxprint_common.mdl.job import _card, detect_tier
from voxprint_common.mdl.status import JobState, Status, Step, load_status, save_status
from voxprint_common.paths import shared_dir
from voxprint_common.runtime_manifest import Component, load

__all__ = [
    "Gap",
    "disk_ready",
    "gaps_for",
    "publish_presence",
]


@dataclass(frozen=True)
class Gap:
    """One model file or runtime component that is missing or short."""

    label: str
    state: str
    kind: str


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
