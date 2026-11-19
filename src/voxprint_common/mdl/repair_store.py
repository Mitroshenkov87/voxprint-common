"""Repair the shared store around the bytes that are already on disk.

:func:`repair_store` plans the models one machine needs the same way the
queue does, then resolves every planned file against the shared store: a
verified blob wins, otherwise a complete copy in another app's folder, in
a view, or in an ``extra_roots`` folder is confirmed by SHA-256 and
adopted (hard-linked) into ``<models_dir>/blobs``. Nothing is downloaded;
a file with no usable copy is simply reported ``"missing"``. With
``dry_run`` the same report is produced without writing anything.

Every app view that the plan calls for (Canvas always, other apps that
opted in with a ``views/<app>/`` folder) is inspected and, unless
``dry_run``, rebuilt through :func:`voxprint_common.mdl.views.repair_view`,
so a stub is never left behind for an app to trip over.

The repair is deliberately conservative:

* blobs are never deleted, and app folders (``<models>/<app>/...``) are
  never written, renamed, or removed — a Forge tree stays the read-only
  source it always was;
* shared roots are never refcounted or deleted: neither
  :func:`voxprint_common.paths.shared_dir` nor the blob store is touched
  by the refcount/release machinery here;
* safety models are not special-cased: they go through the same
  resolution, and the fail-closed gate
  :func:`voxprint_common.mdl.readiness.shared_safety_gaps` stays the
  authority on whether an app may open.

The module also hosts the manifest discovery and machine planning helpers
that the CLI and this repair share, so both plan files identically.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from voxprint_common.gpu_probe import GpuDevice, query_gpus
from voxprint_common.mdl import views
from voxprint_common.mdl.catalog import (
    AppManifest,
    CatalogError,
    PlannedFile,
    discover,
    load_manifest,
    model_share_key,
    plan_files,
)
from voxprint_common.mdl.job import _card, detect_tier
from voxprint_common.mdl.store import StoreFile, resolve_file
from voxprint_common.paths import models_dir

__all__ = ["RepairReport", "discover_parsed", "planned", "repair_store"]


@dataclass
class RepairReport:
    """The outcome of one :func:`repair_store` run.

    Attributes:
        files: One row per planned file with the model ``id``, the
            repo-relative ``path``, the ``sha256``, the resolution
            ``state`` (``"store"``, ``"adopted"``, ``"found"``, or
            ``"missing"``), and the ``source`` path the bytes came from
            (``None`` when missing).
        views: One row per (app, model) view with the ``app``, the model
            ``id``, the view ``folder``, the ``before`` state, and the
            ``after`` state (``"ok"``, ``"repaired"``, ``"would-repair"``,
            or ``"missing"``).
        ready: True when no file is missing and no view is left missing.
        adopted: Number of files folded into the blob store by this run.
        missing: Number of files with no usable copy anywhere on disk.
        repaired_views: Number of views rebuilt by this run.
    """

    files: list[dict[str, object]]
    views: list[dict[str, object]] = field(default_factory=list)
    ready: bool = field(init=False)
    adopted: int = field(init=False)
    missing: int = field(init=False)
    repaired_views: int = field(init=False)

    def __post_init__(self) -> None:
        """Derive the counters and the ready flag from the row lists."""
        self.adopted = sum(1 for row in self.files if row["state"] == "adopted")
        self.missing = sum(1 for row in self.files if row["state"] == "missing")
        self.repaired_views = sum(1 for row in self.views if row["after"] == "repaired")
        self.ready = self.missing == 0 and all(row["after"] != "missing" for row in self.views)

    def to_json(self) -> dict[str, object]:
        """Return the JSON-ready form of the report, including ``ready``."""
        return {
            "files": [dict(row) for row in self.files],
            "views": [dict(row) for row in self.views],
            "ready": self.ready,
            "adopted": self.adopted,
            "missing": self.missing,
            "repaired_views": self.repaired_views,
        }


def repair_store(
    manifests: Sequence[Path] | None = None,
    *,
    only: tuple[str, ...] | None = None,
    extra_roots: Sequence[Path] = (),
    dry_run: bool = False,
    include_groups: Sequence[str] = ("safety",),
    devices: Sequence[GpuDevice] | None = None,
    hasher: Callable[[Path], str] | None = None,
) -> RepairReport:
    """Adopt complete on-disk copies into the store and rebuild stub views.

    The manifests are loaded (given paths, else every discovered app
    manifest, skipping broken ones) and planned for this machine exactly
    like the CLI does. Each planned file is then resolved with
    :func:`voxprint_common.mdl.store.resolve_file`: a verified blob is
    ``"store"``, a confirmed copy elsewhere is adopted as ``"adopted"``
    (or reported ``"found"`` in a dry run), and no copy at all is
    ``"missing"``. Adoption hard-links the copy onto the blob, so the app
    folder keeps its bytes and no extra disk is used; every copy root is
    walked at most once per run through a shared walk cache.

    Afterwards every (app, model) view the plan calls for — Canvas always,
    any app whose ``views/<app>/`` folder exists — is inspected; unless
    ``dry_run`` it is repaired with
    :func:`voxprint_common.mdl.views.repair_view`, which rebuilds the view
    from the blobs or drops the folder when a blob is absent or not trusted
    (truncated, unverified, or a symlink), never leaving an empty stub. In a
    dry run the would-be ``after`` state is
    ``"would-repair"`` when the view is stub or absent and every blob is
    present or adoptable, ``"missing"`` when a blob is gone, else ``"ok"``.

    Nothing here downloads, deletes blobs, touches an app folder, or
    refcounts a shared root. Safety models resolve like any other file;
    the fail-closed safety gate in
    :func:`voxprint_common.mdl.readiness.shared_safety_gaps` is unchanged.

    Args:
        manifests: App manifest paths. ``None`` discovers installed apps.
        only: Model ids to restrict the plan to, or ``None`` for whatever
            the manifests need on this machine.
        extra_roots: Folders searched for complete copies before the
            per-app copy roots (the CLI's repeatable ``--from``).
        dry_run: Report what a real run would do without adopting files
            or rebuilding views.
        include_groups: Normalized group ids (for example ``safety``)
            whose models are always planned. The installer paths pass
            ``("safety",)`` so the fail-closed gate can go green.
        devices: GPUs used to choose the tier and card fit. ``None``
            queries nvidia-smi.
        hasher: Digest callable used when the hash cache has no valid row.

    Returns:
        The :class:`RepairReport` with one row per file and per view.
    """
    parsed = discover_parsed(manifests)
    files = planned(parsed, only, include_groups=include_groups, devices=devices)
    share = {model.id: model_share_key(model) for manifest in parsed for model in manifest.models}
    root = models_dir()
    walk_cache: dict[Path, list[Path]] = {}
    rows: list[dict[str, object]] = []
    states: dict[int, str] = {}
    for item in files:
        repo, revision = _split_share_key(share.get(item.model_id, ""))
        wanted = StoreFile(
            path=item.name,
            sha256=item.sha256,
            size=item.size,
            repo=repo,
            revision=revision,
        )
        state, source = resolve_file(
            root,
            wanted,
            extra_roots,
            hasher=hasher,
            adopt=not dry_run,
            walk_cache=walk_cache,
        )
        states[id(item)] = state
        rows.append(
            {
                "model": item.model_id,
                "path": item.name,
                "sha256": item.sha256,
                "state": state,
                "source": str(source) if source is not None else None,
            }
        )
    view_rows = _repair_views(root, files, states, dry_run=dry_run)
    return RepairReport(files=rows, views=view_rows)


def discover_parsed(paths: Sequence[Path] | None) -> list[AppManifest]:
    """Load the given manifests, or every discovered app manifest.

    A manifest given explicitly must load: its error is the caller's to
    report. Discovery instead skips broken manifests, matching the CLI.

    Args:
        paths: App manifest paths, or ``None`` to discover installed apps.

    Returns:
        The parsed manifests in a stable order.
    """
    if paths is not None:
        return [load_manifest(path) for path in paths]
    found: list[AppManifest] = []
    for installed in discover():
        try:
            found.append(load_manifest(installed.path))
        except CatalogError:
            continue
    return found


def planned(
    parsed: Sequence[AppManifest],
    only: tuple[str, ...] | None,
    include_groups: Sequence[str] = (),
    devices: Sequence[GpuDevice] | None = None,
) -> list[PlannedFile]:
    """Plan ``parsed`` for this machine the way the queue would.

    Args:
        parsed: Parsed app manifests.
        only: Model ids to restrict the plan to, or ``None`` for all.
        include_groups: Normalized group ids always planned.
        devices: GPUs used to choose the tier and card fit. ``None``
            queries nvidia-smi.
    """
    cards = list(query_gpus() if devices is None else devices)
    memory_gib, compute = _card(cards)
    return plan_files(
        list(parsed),
        detect_tier(cards),
        memory_gib=memory_gib,
        compute=compute,
        only=only,
        include_groups=include_groups,
    )


def _split_share_key(key: str) -> tuple[str, str]:
    """Split ``repo@revision`` on the last ``@``; empty when there is none."""
    if "@" not in key:
        return "", ""
    repo, revision = key.rsplit("@", 1)
    return repo.strip(), revision.strip()


def _repair_views(
    root: Path,
    files: Sequence[PlannedFile],
    states: Mapping[int, str],
    *,
    dry_run: bool,
) -> list[dict[str, object]]:
    """Inspect and, unless ``dry_run``, repair every needed app view.

    Files are grouped by layout per app exactly like
    :func:`voxprint_common.mdl.views.materialize_views`, so a view row
    exists for every (app, model) the plan would build a view for.

    Args:
        root: Shared models directory.
        files: The planned files.
        states: Resolution state per planned file, keyed by ``id()``.
        dry_run: Whether to leave disk untouched.

    Returns:
        One report row per (app, model) view.
    """
    groups: dict[tuple[str, str, str], dict[str, tuple[PlannedFile, str]]] = {}
    for item in files:
        apps = views.view_apps(root, getattr(item, "apps", ()))
        if not apps:
            continue
        digest = str(getattr(item, "sha256", "")).lower()
        if not digest:
            continue
        for model_id, path, stamp in views.layout_entries(item):
            for app in apps:
                groups.setdefault((app, model_id, stamp), {})[path] = (item, digest)
    rows: list[dict[str, object]] = []
    for (app, model_id, _stamp), entries in groups.items():
        mapping = {path: digest for path, (_item, digest) in entries.items()}
        sizes = {path: item.size for path, (item, _digest) in entries.items()}
        folder = views.view_directory(root, model_id, mapping, app)
        before = views.inspect_view(root, model_id, mapping, app, sizes=sizes)
        if dry_run:
            after = _dry_run_state(before, (states[id(item)] for item, _ in entries.values()))
        else:
            after, repaired_folder = views.repair_view(root, model_id, mapping, app, sizes=sizes)
            if repaired_folder is not None:
                folder = repaired_folder
        rows.append(
            {
                "app": app,
                "model": model_id,
                "folder": str(folder),
                "before": before,
                "after": after,
            }
        )
    return rows


def _dry_run_state(before: str, file_states: Iterable[str]) -> str:
    """Return the view state a real run would end in without touching disk.

    Args:
        before: The inspected view state.
        file_states: The resolution states of the view's files.

    Returns:
        ``"ok"`` when the view is already valid, ``"would-repair"`` when
        the view is stub or absent but every blob is present or adoptable,
        and ``"missing"`` otherwise.
    """
    if before == "ok":
        return "ok"
    if all(state in {"store", "adopted", "found"} for state in file_states):
        return "would-repair"
    return "missing"
