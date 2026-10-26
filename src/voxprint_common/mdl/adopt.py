"""Copy or hardlink local model files into the shared blob store.

``voxprint-mdl adopt`` plans the same files as ``download``. Each file is
matched to one candidate under the ``--from`` directories, by relative path
or by filename, and only when the size matches. The candidate is hashed on
the calling thread, one file at a time. A matching digest reuses
``blobs/<sha256>`` when that file is already present. Otherwise the bytes
are hardlinked, or copied when a hardlink is impossible. The source file is
never deleted or moved. A mismatch is reported and left for ``download``.

A second run of the same files is a no-op: the verified blob is reused and
the source is not read again. Hooks run only when every planned file was
adopted or reused. Each Canvas model whose files are all in the blob store
gets ``views/canvas/<id>-<digest12>/``, the folder Canvas rc.28 opens.
"""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Literal, assert_never

from voxprint_common.cli_protocol import EXIT_CANCELLED, EXIT_FAILED, EXIT_OK
from voxprint_common.gpu_probe import GpuDevice
from voxprint_common.mdl.catalog import CatalogError, PlannedFile, plan_files
from voxprint_common.mdl.fetch import Cancelled
from voxprint_common.mdl.hooks import HookError, apply_hooks
from voxprint_common.mdl.job import (
    Outcome,
    _cancelled,
    _card,
    _fail,
    _hash_file,
    _inputs,
    _mark_hook,
    _mark_verified,
    _publish,
    _Publisher,
    _remember,
    _reusable,
    _status_for,
    _verified_marker,
)
from voxprint_common.mdl.presence import remember_digest
from voxprint_common.mdl.safety import UrlGuard
from voxprint_common.mdl.status import JobState, Status, Step
from voxprint_common.mdl.views import materialize_views

__all__ = ["adopt"]

ProgressFn = Callable[[float | None, str], None]
StatusFn = Callable[[Status], None]
_Row = dict[str, str]
_Kind = Literal["adopted", "reused", "skipped", "pending"]


def adopt(
    manifests: Sequence[Path] | None = None,
    sources: Sequence[Path] | None = None,
    *,
    dry_run: bool = False,
    devices: Sequence[GpuDevice] | None = None,
    on_status: StatusFn | None = None,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    only: Collection[str] | None = None,
    opener: object | None = None,
    guard: UrlGuard | None = None,
) -> Outcome:
    """Link or copy local files that match the planned model descriptors.

    Args:
        manifests: ``mdl.json`` paths. When omitted, installed apps are discovered.
        sources: Directories to search. The command's ``--from`` flag.
        dry_run: Report path and size matches without hashing, linking, or copying.
        devices: GPUs to plan for. When omitted, nvidia-smi is queried.
        on_status: Called with each published snapshot.
        on_progress: ``(fraction, message)`` for a ``--json`` session.
        cancel: Set to stop after the file that is hashing now.
        only: Model ids to include. ``None`` plans the tier and skips
            on-demand and optional models.
        opener: Passed through when an app manifest still needs descriptors.
        guard: URL rules for that fetch.

    Returns:
        An :class:`~voxprint_common.mdl.job.Outcome`. Exit code 0 reports both
        adopted files and files left for ``download``. The status ``ready``
        flag is true only when every planned file is in the blob store.
    """
    roots = [Path(path) for path in sources or ()]
    if not roots:
        return _fail("adopt needs at least one --from directory", on_status)
    for root in roots:
        if not root.is_dir():
            return _fail(f"{root} is not a directory", on_status)
    try:
        parsed, tier, cards = _inputs(manifests, devices)
        memory_gib, compute = _card(cards)
        files = plan_files(
            parsed,
            tier,
            memory_gib=memory_gib,
            compute=compute,
            only=only,
            opener=opener,
            guard=guard,
        )
    except Cancelled:
        return _fail("cancelled", on_status, state=JobState.CANCELLED, code=EXIT_CANCELLED)
    except (CatalogError, OSError) as exc:
        return _fail(str(exc), on_status)
    state = JobState.DRY_RUN if dry_run else JobState.DOWNLOADING
    status = _status_for(files, tier, state)
    status.extra["mode"] = "adopt"
    publisher = _Publisher(status, on_status, on_progress, None)
    chosen = _candidates(files, roots)
    adopted: list[_Row] = []
    reused: list[_Row] = []
    skipped: list[_Row] = []
    pending: list[_Row] = []
    placed: list[PlannedFile] = []
    try:
        for item, step in zip(files, status.steps, strict=True):
            if _cancelled(cancel):
                _note(status, adopted, reused, skipped, pending)
                status.state = JobState.CANCELLED
                status.error = "cancelled"
                return _publish(status, EXIT_CANCELLED, "cancelled", on_status, on_progress)
            kind, detail = _take(item, chosen.get(id(item)), step, status, publisher, dry_run)
            row = {"model": item.model_id, "file": item.name, "sha256": item.sha256}
            match kind:
                case "skipped":
                    row["reason"] = detail
                    skipped.append(row)
                case "reused":
                    reused.append(row)
                    placed.append(item)
                case "adopted":
                    adopted.append(row)
                    placed.append(item)
                case "pending":
                    pending.append(row)
                case _ as unreachable:
                    assert_never(unreachable)
            step.source = detail
            status.recompute()
            publisher.push(force=True)
        if not dry_run and placed:
            materialize_views(files, placed)
        if not dry_run and files and not skipped and placed:
            apply_hooks(placed)
    except HookError as exc:
        _mark_hook(status, exc)
        _note(status, adopted, reused, skipped, pending)
        return _publish(status, EXIT_FAILED, str(exc), on_status, on_progress)
    except (CatalogError, OSError) as exc:
        status.state = JobState.FAILED
        status.error = str(exc)
        _note(status, adopted, reused, skipped, pending)
        return _publish(status, EXIT_FAILED, str(exc), on_status, on_progress)
    _note(status, adopted, reused, skipped, pending)
    if not skipped and not pending:
        status.state = JobState.READY
        status.error = ""
    elif dry_run:
        status.state = JobState.DRY_RUN
    else:
        status.state = JobState.PLANNED
        status.error = ""
    message = _summary(adopted, reused, skipped, pending)
    status.extra["summary"] = message
    return _publish(status, EXIT_OK, message, on_status, on_progress)


def _take(
    item: PlannedFile,
    candidate: Path | None,
    step: Step,
    status: Status,
    publisher: _Publisher,
    dry_run: bool,
) -> tuple[_Kind, str]:
    if dry_run:
        return _preview(item, candidate, step)
    if _reusable(item):
        _mark_verified(item)
        _remember(item)
        step.state = "verified"
        step.received = item.size
        return "reused", "reused"
    if candidate is None:
        step.state = "skipped"
        step.received = 0
        return "skipped", "no candidate"
    step.state = "checking"
    step.received = 0
    status.recompute()
    publisher.push(force=True)

    def tick(done: int) -> None:
        step.received = min(step.size, done)
        status.recompute()
        publisher.push()

    try:
        digest, _amount = _hash_file(candidate, tick)
    except OSError:
        step.state = "skipped"
        step.received = 0
        return "skipped", "could not read"
    remember_digest(candidate, digest)
    if digest != item.sha256.strip().lower():
        step.state = "skipped"
        step.received = 0
        return "skipped", "sha256 mismatch"
    if item.destination.exists():
        step.state = "skipped"
        step.received = 0
        return "skipped", "blob differs"
    how = _place(candidate, item.destination)
    _mark_verified(item)
    remember_digest(item.destination, digest)
    _remember(item)
    step.state = "verified"
    step.received = item.size
    if how == "reused":
        return "reused", "reused"
    return "adopted", how


def _preview(item: PlannedFile, candidate: Path | None, step: Step) -> tuple[_Kind, str]:
    """Report a dry run from the marker and the candidate size. Nothing is hashed."""
    if _marked(item):
        step.state = "verified"
        step.received = item.size
        return "reused", "reused"
    if candidate is None:
        step.state = "skipped"
        step.received = 0
        return "skipped", "no candidate"
    step.state = "pending"
    step.received = 0
    return "pending", "would adopt"


def _marked(item: PlannedFile) -> bool:
    path = item.destination
    try:
        ready = path.is_file() and path.stat().st_size == item.size
    except OSError:
        return False
    return ready and _verified_marker(item)


def _candidates(files: Sequence[PlannedFile], roots: Sequence[Path]) -> dict[int, Path]:
    chosen: dict[int, Path] = {}
    pending: list[PlannedFile] = []
    for item in files:
        direct = _direct(item, roots)
        if direct is not None:
            chosen[id(item)] = direct
            continue
        pending.append(item)
    if not pending:
        return chosen
    by_name = _filenames(roots)
    for item in pending:
        found = _named(item, by_name)
        if found is not None:
            chosen[id(item)] = found
    return chosen


def _direct(item: PlannedFile, roots: Sequence[Path]) -> Path | None:
    parts = _parts(item.name)
    if parts is None:
        return None
    for root in roots:
        candidate = root.joinpath(*parts)
        if _inside(root, candidate) and _sized(candidate, item.size):
            return candidate
    return None


def _filenames(roots: Sequence[Path]) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {}
    for root in roots:
        for folder, _dirs, names in os.walk(root):
            for name in names:
                found.setdefault(name, []).append(Path(folder) / name)
    return found


def _named(item: PlannedFile, by_name: Mapping[str, list[Path]]) -> Path | None:
    parts = _parts(item.name)
    if parts is None:
        return None
    for candidate in by_name.get(parts[-1], ()):
        if _sized(candidate, item.size):
            return candidate
    return None


def _parts(name: str) -> tuple[str, ...] | None:
    text = name.replace("\\", "/").strip()
    if not text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.parts


def _inside(root: Path, candidate: Path) -> bool:
    base = os.path.abspath(root)
    target = os.path.abspath(candidate)
    try:
        return os.path.commonpath([base, target]) == base
    except ValueError:
        return False


def _sized(path: Path, size: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size == size
    except OSError:
        return False


def _place(source: Path, dest: Path) -> str:
    """Hardlink ``source`` onto ``dest``, or copy when the link is impossible.

    The source path is not removed. A symlink is linked or copied from its
    target so the blob is a regular file.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    target = Path(os.path.realpath(source))
    if target == dest:
        return "reused"
    try:
        os.link(target, dest)
    except FileExistsError:
        return "reused"
    except OSError:
        temporary = dest.with_name(dest.name + ".adopting")
        shutil.copyfile(target, temporary)
        os.replace(temporary, dest)
        return "copied"
    return "linked"


def _note(
    status: Status,
    adopted: list[_Row],
    reused: list[_Row],
    skipped: list[_Row],
    pending: list[_Row],
) -> None:
    status.extra["adopted"] = list(adopted)
    status.extra["reused"] = list(reused)
    status.extra["skipped"] = list(skipped)
    status.extra["would_adopt"] = list(pending)


def _summary(
    adopted: Sequence[_Row],
    reused: Sequence[_Row],
    skipped: Sequence[_Row],
    pending: Sequence[_Row],
) -> str:
    message = (
        f"adopted {len(adopted)}, reused {len(reused)}, "
        f"skipped {len(skipped)}, would adopt {len(pending)}"
    )
    if not skipped:
        return message
    reasons = ", ".join(f"{row['file']} ({row['reason']})" for row in skipped)
    return f"{message}: {reasons}"
