"""List and remove leftover ``.partial`` download files.

A dry run (the default) only lists orphans. ``--apply`` deletes a file or
parts directory that no active job owns. Partials named by a running or
queued job, or by the current status file while a download is in progress,
are kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from voxprint_common.mdl.catalog import CatalogError, load_manifest, split_row_selector
from voxprint_common.mdl.queue import QueueJob, load_queue
from voxprint_common.mdl.status import load_status
from voxprint_common.paths import models_dir

__all__ = ["CleanPartialsReport", "PartialRow", "clean_partials", "list_partials"]


@dataclass(frozen=True)
class PartialRow:
    """One ``.partial`` file or ``.partial.parts`` directory."""

    path: Path
    size: int
    digest: str
    owned: bool


@dataclass
class CleanPartialsReport:
    """Orphans considered by :func:`clean_partials`."""

    rows: list[PartialRow] = field(default_factory=list)
    kept: list[PartialRow] = field(default_factory=list)
    applied: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready payload for the CLI."""

        def _row(item: PartialRow) -> dict[str, object]:
            return {
                "path": str(item.path),
                "size": item.size,
                "digest": item.digest,
                "owned": item.owned,
            }

        return {
            "applied": self.applied,
            "orphans": [_row(item) for item in self.rows],
            "kept": [_row(item) for item in self.kept],
        }


def list_partials(root: Path | None = None) -> list[PartialRow]:
    """Return every ``.partial`` / ``.partial.parts`` under ``blobs/``."""
    folder = (root if root is not None else models_dir()) / "blobs"
    owned = _owned_digests()
    found: list[PartialRow] = []
    if not folder.is_dir():
        return found
    for path in sorted(folder.iterdir(), key=lambda item: item.name):
        name = path.name
        if name.endswith(".partial"):
            digest = name[: -len(".partial")].lower()
        elif name.endswith(".partial.parts") and path.is_dir():
            digest = name[: -len(".partial.parts")].lower()
        else:
            continue
        found.append(PartialRow(path, _size(path), digest, digest in owned))
    return found


def clean_partials(*, apply: bool = False, root: Path | None = None) -> CleanPartialsReport:
    """List orphan partials, and delete them when ``apply`` is true.

    Owned partials are never deleted. The default is a dry run.
    """
    rows = list_partials(root)
    report = CleanPartialsReport(
        rows=[item for item in rows if not item.owned],
        kept=[item for item in rows if item.owned],
        applied=False,
    )
    if not apply:
        return report
    for item in report.rows:
        _remove(item.path)
    report.applied = True
    return report


def _owned_digests() -> set[str]:
    owned: set[str] = set()
    try:
        status = load_status()
    except OSError:
        status = None
    # Steps stay authoritative even after a failure or a Preparing… rewrite
    # that has not yet filled them in, so a resume can keep its partials.
    if status is not None:
        for step in status.steps:
            digest = (step.sha256 or "").strip().lower()
            if digest:
                owned.add(digest)
    try:
        jobs = load_queue()
    except OSError:
        jobs = []
    for job in jobs:
        if job.state in {"queued", "running", "failed", "cancelled"}:
            owned.update(_job_digests(job))
    return owned


def _job_digests(job: QueueJob) -> set[str]:
    """Return blob digests named by a queued, running, or failed job."""
    wanted: set[str] | None
    if job.models is None:
        wanted = None
    else:
        wanted = set()
        for item in job.models:
            parsed = split_row_selector(item)
            wanted.add(parsed[0] if parsed is not None else item)
    owned: set[str] = set()
    for raw in job.manifests:
        try:
            manifest = load_manifest(Path(raw))
        except CatalogError, OSError:
            continue
        for model in manifest.models:
            if wanted is not None and model.id not in wanted:
                continue
            for item in model.files:
                digest = (item.sha256 or "").strip().lower()
                if digest:
                    owned.add(digest)
    return owned


def _size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    if path.is_dir():
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
    return total


def _remove(path: Path) -> None:
    if path.is_dir():
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        path.rmdir()
        return
    path.unlink(missing_ok=True)
