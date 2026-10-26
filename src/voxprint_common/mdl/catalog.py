"""Per-app model manifests, install discovery, and the download plan.

Each app ships ``mdl.json``. A model id can have one file set per VRAM tier.
The plan keeps the highest tier that fits the card, so a 24 GB machine does
not also pull the 8 GB and 16 GB variants of the same id. Tier ``any`` is the
lowest rank and is used when no larger variant fits. An unknown GPU is planned
as 8 GB, which is the smallest set.

Files are deduplicated by SHA-256 into ``<models_dir>/blobs/<sha256>``.
The shared-manifest component id is ``model-`` plus the first 12 hex characters
of that digest. A side virtualenv uses
``runtime-py<major><minor>-<stack>-cu<version>`` (for example
``runtime-py311-nunchaku-cu130``); see
:func:`voxprint_common.runtime_manifest.runtime_component_id`.
"""

from __future__ import annotations

import fnmatch
import json
import math
import os
import re
import shutil
import sys
import urllib.parse
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import assert_never

from voxprint_common.download import DownloadError, huggingface_url, modelscope_url
from voxprint_common.gpu_probe import VramTier
from voxprint_common.hashing import HASH_RE
from voxprint_common.licence import is_allowed
from voxprint_common.mdl.descriptor import (
    APP_SCHEMA,
    AppModels,
    Descriptor,
    DescriptorError,
    DescriptorFile,
    MirrorRef,
    OnnxPrebuilt,
    PostInstall,
    TierRef,
    parse_app_manifest,
    parse_onnx_prebuilt,
    parse_post_install,
)
from voxprint_common.mdl.hooks import HookPlan
from voxprint_common.mdl.names import DISPLAY_NAME
from voxprint_common.mdl.onnx_prebuilt import note_stale, stale_reason
from voxprint_common.mdl.resolve import ResolveError, resolve_descriptors
from voxprint_common.mdl.safety import UrlGuard
from voxprint_common.mdl.variants import chosen_variants
from voxprint_common.paths import models_dir, state_dir

__all__ = [
    "APP_MANIFEST_NAME",
    "HEADROOM",
    "MANIFEST_NAME",
    "MODEL_PREFIX",
    "REGISTRY_NAME",
    "SPACE_RATIO",
    "SPACE_RESERVE_BYTES",
    "SUITE_APPS",
    "AppManifest",
    "CatalogError",
    "DiskShort",
    "InstalledApp",
    "Model",
    "ModelFile",
    "ModelTier",
    "PlannedFile",
    "SuiteApp",
    "discover",
    "gdrive_url",
    "install_dir",
    "is_gated_source",
    "kaggle_url",
    "listed_ids",
    "load_manifest",
    "plan_files",
    "public_mirror_urls",
    "require_space",
    "suite_root",
    "unavailable_rows",
]

HEADROOM = 1.10
SPACE_RATIO = 1.10
SPACE_RESERVE_BYTES = 2 * 1024 * 1024 * 1024
MODEL_PREFIX = 12
MANIFEST_NAME = "mdl.json"
APP_MANIFEST_NAME = "voxprint-app-models.json"
REGISTRY_NAME = "installed-apps.json"
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_APP = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_GDRIVE = re.compile(r"^[A-Za-z0-9_-]{10,}$")
GATED_UPSTREAMS = frozenset(
    {
        "pyannote/speaker-diarization-community-1",
        "pyannote/embedding",
    }
)

DiskFree = Callable[[Path], int]


class CatalogError(ValueError):
    """A model manifest, mirror, or plan input is not valid."""


class DiskShort(CatalogError):
    """Free space is below the remaining size times 1.10, plus 2 GiB."""

    def __init__(self, path: Path, need: int, free: int) -> None:
        """Name the folder and both byte counts.

        Args:
            path: Directory that was going to receive the files.
            need: Bytes required, including headroom.
            free: Bytes free on that volume.
        """
        self.path = path
        self.need = need
        self.free = free
        super().__init__(
            f"Not enough disk space for {DISPLAY_NAME}: "
            f"need {need} bytes (size x 1.10 plus 2 GiB), {free} bytes free."
        )


class ModelTier(StrEnum):
    """VRAM tier declared on one model variant."""

    ANY = "any"
    GB8 = "8gb"
    GB16 = "16gb"
    GB24 = "24gb"


@dataclass(frozen=True)
class SuiteApp:
    """One suite app and the folder name used on each platform."""

    id: str
    windows_name: str

    @property
    def linux_name(self) -> str:
        """Return the lower-case install directory name."""
        return self.id


SUITE_APPS: tuple[SuiteApp, ...] = (
    SuiteApp("audiobook-builder", "Audiobook Builder"),
    SuiteApp("plotweaver", "Plotweaver"),
    SuiteApp("movie-dubber", "Movie Dubber"),
    SuiteApp("clarity", "Clarity"),
    SuiteApp("enlarge", "Enlarge"),
    SuiteApp("canvas", "Canvas"),
    SuiteApp("model-forge", "Model Forge"),
)


@dataclass(frozen=True)
class InstalledApp:
    """An app whose install directory contains ``mdl.json``."""

    id: str
    path: Path


@dataclass(frozen=True)
class ModelFile:
    """One file inside a model variant."""

    path: str
    size: int
    sha256: str
    urls: tuple[str, ...]
    mirror_policy: str = "race"


@dataclass(frozen=True)
class FallbackVariant:
    """Files that replace some default files when the fallback is used."""

    id: str
    replaces: str
    files: tuple[ModelFile, ...]

    def covers(self, path: str) -> bool:
        """Return whether default file ``path`` is replaced by this variant."""
        if self.replaces.endswith("/"):
            return path.startswith(self.replaces)
        return path == self.replaces


@dataclass(frozen=True)
class OnnxPrebuiltFile:
    """A pre-built artifact with resolved download URLs."""

    path: str
    sha256: str
    size: int | None
    urls: tuple[str, ...]
    mirror_policy: str
    onnxruntime: str
    opset: int
    source_file: str = ""


@dataclass(frozen=True)
class Model:
    """One variant of a model id."""

    id: str
    version: str
    tier: ModelTier
    files: tuple[ModelFile, ...]
    on_demand: bool = False
    optional: bool = False
    min_vram_gib: float | None = None
    display_name: str = ""
    names: dict[str, str] = field(default_factory=dict)
    gpu_min_cc: float | None = None
    unavailable: bool = False
    reason: str = ""
    post_install: PostInstall | None = None
    fallbacks: tuple[FallbackVariant, ...] = ()
    onnx_prebuilts: tuple[OnnxPrebuiltFile, ...] = ()
    groups: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class AppManifest:
    """A parsed app manifest, either schema 1 or ``voxprint-app-models/1``."""

    app: str
    models: tuple[Model, ...]
    path: Path
    app_models: AppModels | None = None
    app_hook: PostInstall | None = None
    runtimes: tuple[str, ...] = ()


@dataclass
class PlannedFile:
    """One deduplicated file the downloader may fetch."""

    component_id: str
    sha256: str
    size: int
    version: str
    name: str
    destination: Path
    urls: tuple[str, ...]
    apps: list[str] = field(default_factory=list)
    model_ids: list[str] = field(default_factory=list)
    tier: str = ""
    display_name: str = ""
    model_id: str = ""
    layouts: tuple[tuple[str, str], ...] = ()
    hooks: tuple[HookPlan, ...] = ()
    mirror_policy: str = "race"
    fallback_key: str = ""
    fallback_files: tuple[PlannedFile, ...] = ()

    @property
    def label(self) -> str:
        """Return the step-list name, including the file when a model has several."""
        models = ", ".join(self.model_ids)
        if self.name and self.name not in {".", models}:
            return f"{models}/{self.name}" if models else self.name
        return models or self.name


def suite_root(platform: str | None = None) -> Path:
    """Return the suite install root.

    ``VOXPRINT_SUITE_ROOT`` wins. Otherwise Windows is
    ``%PROGRAMFILES%\\VOXPrint AI Suite`` and Linux is ``/opt/voxprint-ai-suite``.

    Args:
        platform: ``win32`` or another value. Defaults to this machine.
    """
    env = os.environ.get("VOXPRINT_SUITE_ROOT", "").strip()
    if env:
        return Path(env)
    plat = platform or sys.platform
    if plat == "win32":
        program = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        return Path(program) / "VOXPrint AI Suite"
    return Path("/opt/voxprint-ai-suite")


def install_dir(
    app: SuiteApp | str,
    root: Path | None = None,
    *,
    platform: str | None = None,
) -> Path:
    """Return the install directory for one suite app.

    Args:
        app: A :class:`SuiteApp` or a suite id.
        root: Install root. Defaults to :func:`suite_root`.
        platform: ``win32`` selects the display folder name. Any other value
            selects the lower-case id.

    Raises:
        CatalogError: ``app`` is not one of :data:`SUITE_APPS`.
    """
    spec = app if isinstance(app, SuiteApp) else _spec(app)
    plat = platform or sys.platform
    base = root if root is not None else suite_root(plat)
    name = spec.windows_name if plat == "win32" else spec.linux_name
    return base / name


def discover(root: Path | None = None, *, platform: str | None = None) -> list[InstalledApp]:
    """Return installed apps that ship ``mdl.json``.

    When ``<home>/state/installed-apps.json`` exists, it is the registry and
    the default install folders are not scanned. A missing registry falls
    back to those folders.

    Args:
        root: Install root used for the fallback scan.
        platform: Passed to :func:`install_dir` for the fallback scan.

    Raises:
        CatalogError: The registry file is not the expected object.
    """
    registered = _registry()
    if registered is not None:
        found: list[InstalledApp] = []
        for app_id, folder in registered:
            if _has_manifest(folder):
                found.append(InstalledApp(app_id, folder))
        return found
    apps: list[InstalledApp] = []
    for spec in SUITE_APPS:
        folder = install_dir(spec, root, platform=platform)
        if _has_manifest(folder):
            apps.append(InstalledApp(spec.id, folder))
    return apps


def load_manifest(
    path: Path,
    *,
    descriptors: Mapping[str, Descriptor] | None = None,
) -> AppManifest:
    """Read one app manifest.

    Schema 1 (``mdl.json``) lists files on each model. ``voxprint-app-models/1``
    lists model ids per VRAM tier. Pass ``descriptors`` to turn those ids into
    file plans. Without them, the ids are kept on :attr:`AppManifest.app_models`
    and :func:`plan_files` resolves them later.

    Args:
        path: Manifest file, or a directory that contains one.
        descriptors: Parsed ``voxprint-model/1`` documents keyed by model id.

    Raises:
        CatalogError: The document is not a known schema, or a field is invalid.
    """
    target = _resolve_manifest(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CatalogError(f"{target} could not be read") from exc
    if not isinstance(payload, dict):
        raise CatalogError(f"{target} must be a JSON object")
    schema = payload.get("schema")
    if schema == APP_SCHEMA:
        return _load_app_models(target, payload, descriptors)
    if schema != 1:
        raise CatalogError(f"{target} schema must be 1 or {APP_SCHEMA}")
    app = _app(payload.get("app"), "app")
    rows = payload.get("models")
    if not isinstance(rows, list):
        raise CatalogError("models must be a list")
    models = tuple(_model(row) for row in rows)
    return AppManifest(
        app,
        models,
        target,
        app_hook=_optional_hook(payload.get("post_install")),
        runtimes=_runtime_names(payload.get("runtimes")),
    )


def plan_files(
    manifests: Sequence[AppManifest],
    tier: VramTier,
    *,
    models_root: Path | None = None,
    only: Collection[str] | None = None,
    memory_gib: float | None = None,
    compute: tuple[int, int] | None = None,
    descriptors: Mapping[str, Descriptor] | None = None,
    variants: Mapping[str, str] | None = None,
    opener: object | None = None,
    guard: UrlGuard | None = None,
) -> list[PlannedFile]:
    """Select the variants that fit ``tier`` and deduplicate them by digest.

    Optional and ``on_demand`` models are left out unless their id is in
    ``only``. A ``voxprint-app-models/1`` manifest uses the tier list
    (``24gb``, ``16gb``, or ``8gb``) instead of stacking variants.

    Args:
        manifests: Parsed app manifests.
        tier: GPU tier. :attr:`VramTier.UNKNOWN` is planned as 8 GB.
        models_root: Shared models directory. Defaults to
            :func:`voxprint_common.paths.models_dir`.
        only: Model ids to plan. When set, on-demand and optional ids are
            included and every other id is left out.
        memory_gib: Card memory. When omitted, the tier's boundary is used
            (23.5, 15.5, or 8).
        compute: ``(major, minor)`` of the card. When omitted, compute
            capability is not checked.
        descriptors: Model documents for app manifests that were loaded
            without them.
        variants: ``{model id: fallback id}`` to use instead of the default
            files. Added to the choices saved by
            :func:`voxprint_common.mdl.variants.choose_variant`.
        opener: Passed to the index when an app manifest still needs
            descriptors. Defaults to urllib.
        guard: URL rules for that fetch. Defaults to the production allowlist.

    Raises:
        CatalogError: The same digest is declared with two sizes, a model
            does not fit the card, a descriptor is missing, or a tier
            selection matches no files.
    """
    manifests = _attach_resolved(manifests, tier, only, opener, guard, descriptors)
    root = models_root or models_dir()
    cap = _gpu_rank(tier)
    remembered = memory_gib if memory_gib is not None else _tier_gib(tier)
    buckets: dict[str, PlannedFile] = {}
    order: list[str] = []
    chosen = {**chosen_variants(), **dict(variants or {})}
    if only is not None:
        unlisted = set(only).difference(*(listed_ids(item) for item in manifests))
        if unlisted:
            first = next(model_id for model_id in only if model_id in unlisted)
            raise CatalogError(f"model {first} is not available for this card")
    for manifest in manifests:
        prepared = _with_descriptors(manifest, descriptors)
        wanted = None if only is None else [m for m in only if m in listed_ids(manifest)]
        if wanted == []:
            continue
        for model in _chosen(prepared, cap, tier, wanted):
            model = _apply_selection(prepared, tier, model)
            try:
                _check_fit(model, remembered, compute)
            except CatalogError:
                if only is not None:
                    raise
                continue
            label = model.display_name or model.id
            variant = next((v for v in model.fallbacks if v.id == chosen.get(model.id)), None)
            chosen_files = model.files
            if variant is not None:
                kept = tuple(f for f in model.files if not variant.covers(f.path))
                chosen_files = (*kept, *variant.files)
            spare = None if variant is not None else next(iter(model.fallbacks), None)
            spare_plans = (
                tuple(
                    _planned(f, root, prepared.app, model, label, _hook_plans(prepared, model))
                    for f in spare.files
                )
                if spare is not None
                else ()
            )
            fresh: list[OnnxPrebuiltFile] = []
            for extra in model.onnx_prebuilts:
                why = "the artifact has no size" if extra.size is None else ""
                why = why or stale_reason(extra.onnxruntime, extra.opset)
                if why:
                    note_stale(prepared.app, model.id, extra.path, why)
                else:
                    fresh.append(extra)
            prebuilt_path = str(root / "blobs" / fresh[0].sha256) if fresh else ""
            model_hooks = _hook_plans(prepared, model, prebuilt_path, skip_model=bool(fresh))
            extras = [
                ModelFile(
                    extra.path, extra.size or 0, extra.sha256, extra.urls, extra.mirror_policy
                )
                for extra in fresh
            ]
            for item in (*chosen_files, *extras):
                current = buckets.get(item.sha256)
                if current is None:
                    planned = PlannedFile(
                        component_id=f"model-{item.sha256[:MODEL_PREFIX]}",
                        sha256=item.sha256,
                        size=item.size,
                        version=item.sha256,
                        name=item.path,
                        destination=root / "blobs" / item.sha256,
                        urls=item.urls,
                        apps=[prepared.app],
                        model_ids=[model.id],
                        tier=model.tier.value,
                        display_name=label,
                        model_id=model.id,
                        layouts=((model.id, item.path),),
                        hooks=model_hooks,
                        mirror_policy=item.mirror_policy,
                    )
                    if spare is not None and spare.covers(item.path):
                        planned.fallback_key = f"{model.id}:{spare.id}"
                        planned.fallback_files = spare_plans
                    buckets[item.sha256] = planned
                    order.append(item.sha256)
                    continue
                if current.size != item.size:
                    raise CatalogError(
                        f"sha256 {item.sha256} is declared as {current.size} and {item.size} bytes"
                    )
                if prepared.app not in current.apps:
                    current.apps.append(prepared.app)
                if model.id not in current.model_ids:
                    current.model_ids.append(model.id)
                pair = (model.id, item.path)
                if pair not in current.layouts:
                    current.layouts = (*current.layouts, pair)
                if not current.display_name:
                    current.display_name = label
                if not current.model_id:
                    current.model_id = model.id
                for hook in model_hooks:
                    if hook not in current.hooks:
                        current.hooks = (*current.hooks, hook)
                merged: list[str] = list(current.urls)
                for url in item.urls:
                    if url not in merged:
                        merged.append(url)
                current.urls = tuple(merged)
                if "strict-priority" in {current.mirror_policy, item.mirror_policy}:
                    current.mirror_policy = "strict-priority"
    planned = [buckets[key] for key in order]
    if not planned:
        _refuse_unresolved(manifests, tier)
    return planned


def _planned(
    item: ModelFile,
    root: Path,
    app: str,
    model: Model,
    label: str,
    hooks: tuple[HookPlan, ...],
) -> PlannedFile:
    return PlannedFile(
        component_id=f"model-{item.sha256[:MODEL_PREFIX]}",
        sha256=item.sha256,
        size=item.size,
        version=item.sha256,
        name=item.path,
        destination=root / "blobs" / item.sha256,
        urls=item.urls,
        apps=[app],
        model_ids=[model.id],
        tier=model.tier.value,
        display_name=label,
        model_id=model.id,
        layouts=((model.id, item.path),),
        hooks=hooks,
        mirror_policy=item.mirror_policy,
    )


def require_space(
    files: Sequence[PlannedFile],
    *,
    disk_free: DiskFree | None = None,
    root: Path | None = None,
) -> tuple[int, int]:
    """Fail before any download when free space is under size times 1.10 plus 2 GiB.

    Bytes already present in a finished file of the expected size are not
    counted. A partial file counts only for the bytes still missing.

    Args:
        files: The plan.
        disk_free: Replacement for ``shutil.disk_usage(path).free``.
        root: Directory whose volume is checked. Defaults to the models dir.

    Returns:
        ``(need, free)`` in bytes. ``need`` is the remaining size times 1.10
        plus 2 GiB. Nothing left to fetch needs no reserve.

    Raises:
        DiskShort: ``free`` is below ``need``.
        CatalogError: Free space could not be read.
    """
    folder = root or (files[0].destination.parent.parent if files else models_dir())
    folder.mkdir(parents=True, exist_ok=True)
    remaining = _remaining(files)
    need = _bytes_required(remaining)
    free_of = disk_free or _free_bytes
    try:
        free = int(free_of(folder))
    except OSError as exc:
        raise CatalogError(f"could not check free space for {folder}: {exc}") from exc
    if free < need:
        raise DiskShort(folder, need, free)
    return need, free


def unavailable_rows(manifests: Sequence[AppManifest]) -> list[tuple[str, str, str]]:
    """Return ``(id, display_name, reason)`` for models that must not be fetched.

    Args:
        manifests: Parsed app manifests.
    """
    rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for manifest in manifests:
        for model in manifest.models:
            if not model.unavailable or model.id in seen:
                continue
            seen.add(model.id)
            rows.append((model.id, model.display_name or model.id, model.reason or "unavailable"))
    return rows


def is_gated_source(url: str) -> bool:
    """Return whether ``url`` points at a known gated Hugging Face repo.

    Args:
        url: A mirror URL.
    """
    parsed = urllib.parse.urlparse(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if host != "huggingface.co" and not host.endswith(".huggingface.co"):
        return False
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return False
    return f"{parts[0]}/{parts[1]}".lower() in GATED_UPSTREAMS


def public_mirror_urls(repo: str, revision: str, filename: str) -> tuple[str, str]:
    """Return the suite Hugging Face and Kaggle URLs for one mirrored file.

    No token is read or sent. ``repo`` is ``owner/name`` on Hugging Face.
    The Kaggle dataset is ``mitroshenkov87/<name>``.

    Args:
        repo: Hugging Face repo id, for example
            ``Mitroshenkov87/voxprint-pyannote-embedding``.
        revision: Pinned revision. Hugging Face requires it.
        filename: Path inside the repo. The Kaggle name is the same string.
    """
    name = repo.strip().split("/")[-1]
    return (
        huggingface_url(repo.strip(), filename, revision.strip()),
        kaggle_url("mitroshenkov87", name, filename),
    )


def kaggle_url(owner: str, dataset: str, filename: str) -> str:
    """Return a Kaggle dataset-file URL.

    The suite mirror owner is ``mitroshenkov87``.

    Args:
        owner: Kaggle owner slug.
        dataset: Dataset slug.
        filename: Path inside the dataset. ``..`` is rejected.

    Returns:
        An https URL.
    """
    own = _slug(owner, "kaggle owner")
    data = _slug(dataset, "kaggle dataset")
    name = _rel(filename)
    quoted = "/".join(urllib.parse.quote(part) for part in name.split("/"))
    return f"https://www.kaggle.com/api/v1/datasets/download/{own}/{data}/{quoted}"


def gdrive_url(file_id: str) -> str:
    """Return a Google Drive download URL for one file id.

    Args:
        file_id: Drive file id.

    Returns:
        An https URL.
    """
    text = file_id.strip()
    if _GDRIVE.fullmatch(text) is None:
        raise CatalogError(f"google drive file id is not valid: {file_id!r}")
    return "https://drive.google.com/uc?export=download&id=" + urllib.parse.quote(text)


def _hook_plans(
    manifest: AppManifest,
    model: Model,
    prebuilt_path: str = "",
    *,
    skip_model: bool = False,
) -> tuple[HookPlan, ...]:
    install = manifest.path.parent
    found: list[HookPlan] = []
    if manifest.app_hook is not None:
        found.append(HookPlan(manifest.app, install, manifest.app_hook))
    if model.post_install is not None and not skip_model:
        found.append(HookPlan(manifest.app, install, model.post_install, prebuilt_path))
    if manifest.app_models is not None:
        for hook in manifest.app_models.model_hooks:
            if hook.model_id == model.id:
                found.append(HookPlan(manifest.app, install, hook))
    return tuple(found)


def _optional_hook(value: object, model_id: str = "") -> PostInstall | None:
    if value is None:
        return None
    try:
        return parse_post_install(value, "post_install", model_id)
    except DescriptorError as exc:
        raise CatalogError(str(exc)) from exc


def _reject_gated(model_id: str, urls: Sequence[str]) -> None:
    if urls and all(is_gated_source(url) for url in urls):
        raise CatalogError(
            f"model {model_id} is only published on a gated upstream; "
            "add the suite mirror before install"
        )


def _bytes_required(remaining: int) -> int:
    if remaining <= 0:
        return 0
    return math.ceil(remaining * SPACE_RATIO) + SPACE_RESERVE_BYTES


def _has_manifest(folder: Path) -> bool:
    return _find_manifest(folder) is not None


def _find_manifest(folder: Path) -> Path | None:
    for name in (MANIFEST_NAME, APP_MANIFEST_NAME):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def listed_ids(manifest: AppManifest) -> frozenset[str]:
    """Return every model id that ``manifest`` lists, resolved or not.

    Args:
        manifest: A parsed app manifest.
    """
    found = {model.id for model in manifest.models}
    document = manifest.app_models
    if document is not None:
        for _name, ids in document.tiers:
            found.update(ids)
        found.update(item.id for item in document.optional)
    return frozenset(found)


def _resolve_manifest(path: Path) -> Path:
    if not path.is_dir():
        return path
    found = _find_manifest(path)
    return found if found is not None else path / MANIFEST_NAME


def _load_app_models(
    target: Path,
    payload: Mapping[str, object],
    descriptors: Mapping[str, Descriptor] | None,
) -> AppManifest:
    try:
        document = parse_app_manifest(dict(payload))
    except DescriptorError as exc:
        raise CatalogError(str(exc)) from exc
    models = () if descriptors is None else _models_from_descriptors(document, descriptors)
    models = _mark_unavailable(models, document.unavailable)
    return AppManifest(
        document.app_id,
        models,
        target,
        document,
        document.post_install,
        runtimes=document.runtimes,
    )


def _models_from_descriptors(
    document: AppModels,
    descriptors: Mapping[str, Descriptor],
    resolved: Collection[str] | None = None,
) -> tuple[Model, ...]:
    order: list[str] = []
    for _name, ids in document.tiers:
        for model_id in ids:
            if model_id not in order:
                order.append(model_id)
    optional_ids = {item.id for item in document.optional}
    for item in document.optional:
        if item.id not in order:
            order.append(item.id)
    models: list[Model] = []
    blocked = {model_id for model_id, _reason in document.unavailable}
    for model_id in order:
        if model_id in blocked:
            continue
        if resolved is not None and model_id not in resolved:
            continue
        descriptor = descriptors.get(model_id)
        if descriptor is None:
            raise CatalogError(f"model {model_id} has no voxprint-model descriptor")
        if descriptor.id != model_id:
            raise CatalogError(f"descriptor id {descriptor.id} does not match {model_id}")
        models.append(
            _model_from_descriptor(
                descriptor,
                _tier_name_for(document, model_id),
                optional=model_id in optional_ids,
            )
        )
    return tuple(models)


def _mark_unavailable(
    models: tuple[Model, ...],
    blocked: tuple[tuple[str, str], ...],
) -> tuple[Model, ...]:
    if not blocked:
        return models
    reasons = dict(blocked)
    found = {model.id for model in models}
    updated: list[Model] = []
    for model in models:
        reason = reasons.get(model.id)
        if reason is None:
            updated.append(model)
            continue
        updated.append(
            Model(
                model.id,
                model.version,
                model.tier,
                (),
                on_demand=model.on_demand,
                optional=model.optional,
                min_vram_gib=model.min_vram_gib,
                display_name=model.display_name,
                gpu_min_cc=model.gpu_min_cc,
                unavailable=True,
                reason=reason,
                post_install=model.post_install,
                onnx_prebuilts=model.onnx_prebuilts,
            )
        )
    for model_id, reason in blocked:
        if model_id in found:
            continue
        updated.append(
            Model(
                model_id,
                "0",
                ModelTier.ANY,
                (),
                display_name=model_id,
                unavailable=True,
                reason=reason,
            )
        )
    return tuple(updated)


def _with_descriptors(
    manifest: AppManifest,
    descriptors: Mapping[str, Descriptor] | None,
) -> AppManifest:
    if manifest.app_models is None or manifest.models or descriptors is None:
        return manifest
    models = _models_from_descriptors(manifest.app_models, descriptors)
    return AppManifest(
        manifest.app,
        _mark_unavailable(models, manifest.app_models.unavailable),
        manifest.path,
        manifest.app_models,
        manifest.app_hook,
        runtimes=manifest.runtimes or manifest.app_models.runtimes,
    )


def _tier_name_for(document: AppModels, model_id: str) -> ModelTier:
    for name in (ModelTier.GB24.value, ModelTier.GB16.value, ModelTier.GB8.value):
        ids = document.tier_ids(name)
        if ids is not None and model_id in ids:
            return _tier(name)
    return ModelTier.ANY


def _model_from_descriptor(descriptor: Descriptor, tier: ModelTier, *, optional: bool) -> Model:
    version = descriptor.build.tag or descriptor.build.date
    files = tuple(
        ModelFile(
            item.path,
            item.size,
            item.sha256,
            _descriptor_urls(descriptor, item.path),
            descriptor.policy_for(item.path),
        )
        for item in descriptor.files
    )
    return Model(
        descriptor.id,
        version,
        tier,
        files,
        on_demand=descriptor.on_demand,
        optional=optional,
        min_vram_gib=descriptor.tier.min_vram_gib,
        display_name=descriptor.display_name,
        gpu_min_cc=descriptor.tier.gpu_min_cc,
        fallbacks=tuple(
            FallbackVariant(
                group.id,
                group.replaces,
                tuple(
                    ModelFile(
                        item.path,
                        item.size,
                        item.sha256,
                        _fallback_urls(descriptor, item),
                        item.mirror_policy or descriptor.mirror_policy,
                    )
                    for item in group.files
                ),
            )
            for group in descriptor.fallbacks
            if group.role == "fallback"
        ),
        post_install=descriptor.post_install,
        onnx_prebuilts=tuple(
            _descriptor_onnx_prebuilt(descriptor, item) for item in descriptor.onnx_prebuilt
        ),
        groups=tuple((group.name, group.paths) for group in descriptor.file_groups if group.name),
    )


def _descriptor_onnx_prebuilt(descriptor: Descriptor, item: OnnxPrebuilt) -> OnnxPrebuiltFile:
    if item.url:
        urls: tuple[str, ...] = (item.url,)
    else:
        urls = tuple(
            _descriptor_mirror(ref.kind, ref.ref, ref.revision, item.path, 2)
            for ref in item.sources
            if isinstance(ref, MirrorRef)
        )
    _reject_gated(descriptor.id, urls)
    return OnnxPrebuiltFile(
        item.path,
        item.sha256,
        item.size,
        urls,
        item.mirror_policy,
        item.onnxruntime,
        item.opset,
        item.source_file,
    )


def _mdl_onnx_prebuilts(model_id: str, value: Mapping[str, object]) -> tuple[OnnxPrebuiltFile, ...]:
    rows: list[tuple[object, str]] = []
    if "onnx_prebuilt" in value:
        rows.append((value["onnx_prebuilt"], ""))
    for row in value.get("files") or ():  # type: ignore[attr-defined]
        if isinstance(row, dict) and "onnx_prebuilt" in row:
            rows.append((row["onnx_prebuilt"], str(row.get("path") or "")))
    found: list[OnnxPrebuiltFile] = []
    for raw, source in rows:
        try:
            item = parse_onnx_prebuilt(
                raw,
                f"model {model_id} onnx_prebuilt",
                source,
                mirrors=lambda entries, _label: _mirror_list(model_id, entries),
            )
        except DescriptorError as exc:
            raise CatalogError(str(exc)) from exc
        urls = (item.url,) if item.url else tuple(str(entry) for entry in item.sources)
        _reject_gated(model_id, urls)
        found.append(
            OnnxPrebuiltFile(
                item.path,
                item.sha256,
                item.size,
                urls,
                item.mirror_policy,
                item.onnxruntime,
                item.opset,
                item.source_file,
            )
        )
    return tuple(found)


def _fallback_urls(descriptor: Descriptor, item: DescriptorFile) -> tuple[str, ...]:
    mirrors = item.sources or descriptor.mirrors
    _check_licence_split(descriptor, mirrors)
    urls = tuple(
        _descriptor_mirror(ref.kind, ref.ref, ref.revision, item.path, len(descriptor.files))
        for ref in mirrors
    )
    _reject_gated(descriptor.id, urls)
    return urls


def _descriptor_urls(descriptor: Descriptor, filename: str) -> tuple[str, ...]:
    mirrors = descriptor.mirrors_for(filename)
    _check_licence_split(descriptor, mirrors)
    urls = tuple(
        _descriptor_mirror(
            mirror.kind, mirror.ref, mirror.revision, filename, len(descriptor.files)
        )
        for mirror in mirrors
    )
    _reject_gated(descriptor.id, urls)
    return urls


def _check_licence_split(descriptor: Descriptor, mirrors: tuple[MirrorRef, ...]) -> None:
    """Keep a restrictive licence on the author's own source.

    Args:
        descriptor: The model document.
        mirrors: Sources that would be used for one file.

    Raises:
        CatalogError: The model is not an optional plug-in, or it lists a
            suite mirror or more than one source.
    """
    if is_allowed(descriptor.license):
        return
    if not descriptor.on_demand:
        raise CatalogError(
            f"model {descriptor.id} has a restrictive licence and must be an optional plug-in"
        )
    if len(mirrors) != 1 or _suite_ref(mirrors[0]):
        raise CatalogError(
            f"model {descriptor.id} has a restrictive licence; "
            "use the official source only, with no mirror"
        )


def _suite_ref(mirror: MirrorRef) -> bool:
    """Return whether ``mirror`` is a suite copy rather than the author's repo."""
    ref = mirror.ref.strip()
    if mirror.kind == "hf":
        return ref.lower().startswith("mitroshenkov87/")
    if mirror.kind == "kaggle":
        owner, _slash, _rest = ref.partition("/")
        return owner.lower() == "mitroshenkov87"
    return False


def _descriptor_mirror(kind: str, ref: str, revision: str, filename: str, count: int) -> str:
    match kind:
        case "hf":
            if not revision.strip():
                raise CatalogError(f"hf mirror for {filename} needs a revision")
            return huggingface_url(ref, filename, revision.strip())
        case "modelscope":
            return modelscope_url(ref, filename, revision.strip() or "master")
        case "kaggle":
            owner, _slash, dataset = ref.partition("/")
            if not owner or not dataset or "/" in dataset:
                raise CatalogError("kaggle ref must be owner/dataset")
            return kaggle_url(owner, dataset, filename)
        case "gdrive":
            if count != 1:
                raise CatalogError("a gdrive mirror can name one file")
            return gdrive_url(ref)
        case "url":
            return _join_https(ref, filename)
        case _:
            raise CatalogError(f"unknown mirror kind {kind!r}")


def _join_https(ref: str, filename: str) -> str:
    parsed = urllib.parse.urlparse(ref.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise CatalogError("url mirror must be an https URL")
    base = ref.strip() if ref.strip().endswith("/") else ref.strip() + "/"
    quoted = "/".join(urllib.parse.quote(part) for part in filename.split("/"))
    return base + quoted


def _chosen(
    manifest: AppManifest,
    cap: int,
    tier: VramTier,
    only: Collection[str] | None,
) -> list[Model]:
    wanted = None if only is None else set(only)
    if manifest.app_models is None:
        pool = manifest.models
        if wanted is not None:
            pool = tuple(model for model in pool if model.id in wanted)
        chosen = _select(pool, cap)
    elif wanted is not None:
        chosen = [model for model in manifest.models if model.id in wanted]
    else:
        ids = _listed_ids(manifest.app_models, tier)
        by_id = {model.id: model for model in manifest.models}
        missing = [model_id for model_id in ids if model_id not in by_id]
        if missing:
            raise CatalogError(f"model {missing[0]} has no voxprint-model descriptor")
        chosen = [by_id[model_id] for model_id in ids]
    if wanted is None and manifest.app_models is None:
        chosen = [model for model in chosen if not model.on_demand and not model.optional]
    elif wanted is None:
        chosen = [model for model in chosen if not model.optional]
    blocked = [model for model in chosen if model.unavailable]
    if wanted is not None and blocked:
        first = blocked[0]
        detail = first.reason or "unavailable"
        raise CatalogError(f"model {first.id} is unavailable: {detail}")
    chosen = [model for model in chosen if not model.unavailable]
    if wanted is None:
        return chosen
    found = {model.id for model in chosen}
    missing = [model_id for model_id in wanted if model_id not in found]
    if missing:
        raise CatalogError(f"model {missing[0]} is not available for this card")
    return chosen


def _attach_resolved(
    manifests: Sequence[AppManifest],
    tier: VramTier,
    only: Collection[str] | None,
    opener: object | None,
    guard: UrlGuard | None,
    descriptors: Mapping[str, Descriptor] | None,
) -> list[AppManifest]:
    if descriptors is not None:
        return list(manifests)
    attached: list[AppManifest] = []
    for manifest in manifests:
        document = manifest.app_models
        if document is None or manifest.models:
            attached.append(manifest)
            continue
        ids = _ids_to_resolve(document, tier, only)
        if not ids:
            attached.append(manifest)
            continue
        try:
            found = resolve_descriptors(ids, opener=opener, guard=guard)
        except ResolveError as exc:
            raise CatalogError(str(exc)) from exc
        models = _mark_unavailable(
            _models_from_descriptors(document, found, resolved=set(ids)),
            document.unavailable,
        )
        attached.append(
            AppManifest(
                manifest.app,
                models,
                manifest.path,
                document,
                manifest.app_hook,
                runtimes=manifest.runtimes or document.runtimes,
            )
        )
    return attached


def _ids_to_resolve(
    document: AppModels,
    tier: VramTier,
    only: Collection[str] | None,
) -> list[str]:
    blocked = {model_id for model_id, _reason in document.unavailable}
    if only is None:
        return [model_id for model_id in _listed_ids(document, tier) if model_id not in blocked]
    known: list[str] = []
    for _name, ids in document.tiers:
        known.extend(ids)
    known.extend(item.id for item in document.optional)
    present = set(known)
    return [model_id for model_id in only if model_id in present and model_id not in blocked]


def _apply_selection(manifest: AppManifest, tier: VramTier, model: Model) -> Model:
    document = manifest.app_models
    if document is None:
        return model
    ref = next((item for item in _listed_refs(document, tier) if item.id == model.id), None)
    if ref is None or not ref.constrained:
        return model
    files = _selected_files(model, ref)
    if not files:
        raise CatalogError(f"model {model.id} selection matches no files")
    return replace(model, files=files)


def _selected_files(model: Model, ref: TierRef) -> tuple[ModelFile, ...]:
    wanted: set[str] = set()
    named = dict(model.groups)
    for name in ref.file_groups:
        paths = named.get(name)
        if paths is None:
            raise CatalogError(f"model {model.id} has no file group {name}")
        wanted.update(paths)
    for pattern in ref.files:
        for item in model.files:
            if fnmatch.fnmatchcase(item.path, pattern):
                wanted.add(item.path)
    return tuple(item for item in model.files if item.path in wanted)


def _refuse_unresolved(manifests: Sequence[AppManifest], tier: VramTier) -> None:
    for manifest in manifests:
        document = manifest.app_models
        if document is None or manifest.models:
            continue
        blocked = {model_id for model_id, _reason in document.unavailable}
        pending = [model_id for model_id in _listed_ids(document, tier) if model_id not in blocked]
        if pending:
            raise CatalogError(f"model {pending[0]} has no voxprint-model descriptor")


def _listed_refs(document: AppModels, tier: VramTier) -> list[TierRef]:
    name = _tier_key(tier)
    order = {"24gb": ("24gb", "16gb", "8gb"), "16gb": ("16gb", "8gb"), "8gb": ("8gb",)}
    for key in order[name]:
        refs = document.tier_refs(key)
        if refs is not None:
            return list(refs)
    return []


def _listed_ids(document: AppModels, tier: VramTier) -> list[str]:
    name = _tier_key(tier)
    order = {"24gb": ("24gb", "16gb", "8gb"), "16gb": ("16gb", "8gb"), "8gb": ("8gb",)}
    for key in order[name]:
        ids = document.tier_ids(key)
        if ids is not None:
            return list(ids)
    return []


def _tier_key(tier: VramTier) -> str:
    match tier:
        case VramTier.GB24:
            return "24gb"
        case VramTier.GB16:
            return "16gb"
        case VramTier.GB8 | VramTier.UNKNOWN:
            return "8gb"
        case _ as unreachable:
            assert_never(unreachable)


def _tier_gib(tier: VramTier) -> float:
    match tier:
        case VramTier.GB24:
            return 23.5
        case VramTier.GB16:
            return 15.5
        case VramTier.GB8 | VramTier.UNKNOWN:
            return 8.0
        case _ as unreachable:
            assert_never(unreachable)


def _check_fit(model: Model, memory_gib: float, compute: tuple[int, int] | None) -> None:
    if model.min_vram_gib is not None and memory_gib + 1e-9 < model.min_vram_gib:
        raise CatalogError(
            f"{model.id} needs {model.min_vram_gib:g} GiB; this card has {memory_gib:g} GiB"
        )
    if model.gpu_min_cc is None or compute is None:
        return
    capability = compute[0] + compute[1] / 10
    if capability + 1e-9 < model.gpu_min_cc:
        raise CatalogError(f"{model.id} needs compute capability {model.gpu_min_cc:g}")


def _select(models: Sequence[Model], cap: int) -> list[Model]:
    grouped: dict[str, list[Model]] = {}
    for model in models:
        grouped.setdefault(model.id, []).append(model)
    chosen: list[Model] = []
    for group in grouped.values():
        fitting = [model for model in group if _tier_rank(model.tier) <= cap]
        if not fitting:
            continue
        best = max(_tier_rank(model.tier) for model in fitting)
        chosen.extend(model for model in fitting if _tier_rank(model.tier) == best)
    return chosen


def _tier_rank(tier: ModelTier) -> int:
    match tier:
        case ModelTier.ANY:
            return 0
        case ModelTier.GB8:
            return 1
        case ModelTier.GB16:
            return 2
        case ModelTier.GB24:
            return 3
        case _ as unreachable:
            assert_never(unreachable)


def _gpu_rank(tier: VramTier) -> int:
    match tier:
        case VramTier.GB24:
            return 3
        case VramTier.GB16:
            return 2
        case VramTier.GB8 | VramTier.UNKNOWN:
            return 1
        case _ as unreachable:
            assert_never(unreachable)


def _display_names(model_id: str, value: object, display: str) -> tuple[dict[str, str], str]:
    """Return per-language names and the English label.

    ``name`` may be a string or a map of language codes. English is the
    fallback. ``display_name`` fills in when the map has no English entry.
    """
    if value in (None, ""):
        names = {"en": display} if display else {}
        return names, display
    if isinstance(value, str):
        text = value.strip()
        return ({"en": text} if text else {}), text or display
    if not isinstance(value, dict):
        raise CatalogError(f"model {model_id} name must be a string or a language map")
    names = {
        str(key): str(item)
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str) and item.strip()
    }
    shown = names.get("en") or display or next(iter(names.values()), "")
    if shown and "en" not in names:
        names["en"] = shown
    return names, shown


def _vram_gib(model_id: str, value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CatalogError(f"model {model_id} min_vram_gb must be a number")
    return float(value)


def _runtime_names(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    bad = not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    )
    if bad:
        raise CatalogError("runtimes must be a list of component ids")
    return tuple(item.strip() for item in value)


def _model(value: object) -> Model:
    if not isinstance(value, dict):
        raise CatalogError("model must be an object")
    model_id = _ident(value.get("id"), "model id")
    version = value.get("version")
    if not isinstance(version, str) or not version.strip():
        raise CatalogError(f"model {model_id} needs a version")
    tier = _tier(value.get("vram_tier"))
    on_demand = value.get("on_demand", False)
    if not isinstance(on_demand, bool):
        raise CatalogError(f"model {model_id} on_demand must be a boolean")
    display = value.get("display_name", "")
    if not isinstance(display, str):
        raise CatalogError(f"model {model_id} display_name must be a string")
    optional = value.get("optional", False)
    if not isinstance(optional, bool):
        raise CatalogError(f"model {model_id} optional must be a boolean")
    names, display = _display_names(model_id, value.get("name", display), display)
    min_vram = _vram_gib(model_id, value.get("min_vram_gb", value.get("min_vram_gib")))
    status = value.get("status", "available")
    if status not in {"available", "unavailable"}:
        raise CatalogError(f"model {model_id} status must be available or unavailable")
    reason = value.get("reason", "")
    if not isinstance(reason, str):
        raise CatalogError(f"model {model_id} reason must be a string")
    if status == "unavailable" and not reason.strip():
        raise CatalogError(f"model {model_id} needs a reason when it is unavailable")
    hook = _optional_hook(value.get("post_install"), model_id)
    model_policy = "race"
    if "mirror_policy" in value:
        model_policy = _mirror_policy(value.get("mirror_policy"), f"model {model_id} mirror_policy")
    groups = _mdl_groups(model_id, value.get("file_groups"))
    rows = value.get("files", [])
    if status == "unavailable":
        files = ()
    elif not isinstance(rows, list) or not rows:
        raise CatalogError(f"model {model_id} needs at least one file")
    else:
        files = tuple(_file(model_id, row, model_policy, groups) for row in rows)
        missing = [path for path in groups if path not in {item.path for item in files}]
        if missing:
            raise CatalogError(f"file group path is not a file: {missing[0]!r}")
    if status != "unavailable" and not files:
        raise CatalogError(f"model {model_id} needs at least one file")
    return Model(
        model_id,
        version.strip(),
        tier,
        files,
        on_demand=on_demand,
        optional=optional,
        min_vram_gib=min_vram,
        display_name=display,
        names=names,
        unavailable=status == "unavailable",
        reason=reason.strip(),
        post_install=hook,
        onnx_prebuilts=() if status == "unavailable" else _mdl_onnx_prebuilts(model_id, value),
    )


def _file(
    model_id: str,
    value: object,
    model_policy: str = "race",
    groups: dict[str, _MdlGroup] | None = None,
) -> ModelFile:
    if not isinstance(value, dict):
        raise CatalogError(f"model {model_id} file must be an object")
    name = _rel(str(value.get("path") or "model.bin"))
    size = value.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise CatalogError(f"model {model_id} file size must be a positive integer")
    digest = str(value.get("sha256") or "").strip().lower()
    if HASH_RE.fullmatch(digest) is None:
        raise CatalogError(f"model {model_id} sha256 must be 64 hexadecimal characters")
    group = (groups or {}).get(name)
    own_policy = ""
    if "mirror_policy" in value:
        own_policy = _mirror_policy(value.get("mirror_policy"), f"model {model_id} mirror_policy")
    if "sources" in value:
        urls = _mirror_list(model_id, value.get("sources"))
    elif group is not None and group.sources is not None:
        urls = group.sources
    else:
        urls = _mirror_list(model_id, value.get("mirrors"))
    inherited = group.policy if group is not None and group.policy else ""
    policy = own_policy or inherited or model_policy
    _reject_gated(model_id, urls)
    return ModelFile(name, size, digest, urls, policy)


def _mirror_list(model_id: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise CatalogError(f"model {model_id} needs at least one mirror")
    return tuple(_mirror(row) for row in value)


def _mirror_policy(value: object, label: str) -> str:
    if value not in {"race", "strict-priority"}:
        raise CatalogError(f"{label} must be race or strict-priority")
    return str(value)


@dataclass(frozen=True)
class _MdlGroup:
    """A file group's policy and optional replacement source list."""

    policy: str
    sources: tuple[str, ...] | None


def _mdl_groups(model_id: str, value: object) -> dict[str, _MdlGroup]:
    if value is None:
        return {}
    if not isinstance(value, list):
        raise CatalogError(f"model {model_id} file_groups must be a list")
    found: dict[str, _MdlGroup] = {}
    allowed = {"paths", "mirror_policy", "sources"}
    for row in value:
        if not isinstance(row, dict) or not {"paths"} <= set(row) <= allowed:
            raise CatalogError(f"model {model_id} file group needs paths")
        paths = row.get("paths")
        if not isinstance(paths, list) or not paths:
            raise CatalogError(f"model {model_id} file group needs paths")
        policy = ""
        if "mirror_policy" in row:
            policy = _mirror_policy(row.get("mirror_policy"), f"model {model_id} mirror_policy")
        sources = _mirror_list(model_id, row.get("sources")) if "sources" in row else None
        for path in paths:
            if not isinstance(path, str) or not path:
                raise CatalogError(f"file group path is not a file: {path!r}")
            if path in found:
                raise CatalogError(f"file {path} is in more than one group")
            found[path] = _MdlGroup(policy, sources)
    return found


def _mirror(value: object) -> str:
    if isinstance(value, str):
        return _http(value)
    if not isinstance(value, dict):
        raise CatalogError("mirror must be an object or an http(s) URL")
    source = value.get("source")
    try:
        match source:
            case "huggingface":
                revision = value.get("revision")
                if not isinstance(revision, str) or not revision.strip():
                    raise CatalogError("huggingface mirror needs a revision")
                return huggingface_url(
                    _text(value.get("repo"), "repo"),
                    _text(value.get("filename"), "filename"),
                    revision.strip(),
                )
            case "modelscope":
                return modelscope_url(
                    _text(value.get("repo"), "repo"),
                    _text(value.get("filename"), "filename"),
                    _text(value.get("revision") or "master", "revision"),
                )
            case "kaggle":
                return kaggle_url(
                    _text(value.get("owner"), "owner"),
                    _text(value.get("dataset"), "dataset"),
                    _text(value.get("filename"), "filename"),
                )
            case "gdrive":
                return gdrive_url(_text(value.get("id"), "id"))
            case "url":
                return _http(_text(value.get("url"), "url"))
            case _:
                raise CatalogError(f"unknown mirror source {source!r}")
    except DownloadError as exc:
        raise CatalogError(str(exc)) from exc


def _registry() -> list[tuple[str, Path]] | None:
    path = state_dir() / REGISTRY_NAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CatalogError(f"{REGISTRY_NAME} could not be read") from exc
    if not isinstance(payload, dict):
        raise CatalogError(f"{REGISTRY_NAME} must be a JSON object")
    rows = payload.get("apps")
    if not isinstance(rows, list):
        raise CatalogError(f"{REGISTRY_NAME} apps must be a list")
    found: list[tuple[str, Path]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise CatalogError("registry app must be an object")
        app_id = _app(row.get("id"), "registry app id")
        raw = row.get("path")
        if not isinstance(raw, str) or not raw.strip():
            raise CatalogError(f"registry app {app_id} needs a path")
        found.append((app_id, Path(raw)))
    return found


def _remaining(files: Sequence[PlannedFile]) -> int:
    total = 0
    for item in files:
        if item.destination.is_file() and item.destination.stat().st_size == item.size:
            continue
        partial = item.destination.with_name(item.destination.name + ".partial")
        have = partial.stat().st_size if partial.is_file() else 0
        total += max(0, item.size - min(have, item.size))
    return total


def _free_bytes(path: Path) -> int:
    return int(shutil.disk_usage(path).free)


def _spec(app_id: str) -> SuiteApp:
    for spec in SUITE_APPS:
        if spec.id == app_id:
            return spec
    raise CatalogError(f"unknown suite app {app_id!r}")


def _tier(value: object) -> ModelTier:
    if not isinstance(value, str):
        raise CatalogError("vram_tier is required")
    for tier in ModelTier:
        if value.strip().lower() == tier.value:
            return tier
    raise CatalogError("vram_tier must be any, 8gb, 16gb, or 24gb")


def _app(value: object, label: str) -> str:
    if not isinstance(value, str) or _APP.fullmatch(value.strip()) is None:
        raise CatalogError(f"{label} must be a suite id")
    return value.strip()


def _ident(value: object, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value.strip()) is None:
        raise CatalogError(f"{label} must match [a-z0-9][a-z0-9._-]*")
    return value.strip()


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{label} is required")
    return value.strip()


def _slug(value: str, label: str) -> str:
    text = value.strip()
    if _SLUG.fullmatch(text) is None:
        raise CatalogError(f"{label} must be a single path segment")
    return urllib.parse.quote(text)


def _rel(filename: str) -> str:
    text = filename.strip().replace("\\", "/")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    windows = PureWindowsPath(text)
    escaped = windows.drive or windows.root or windows.is_absolute()
    for part in parts:
        piece = PureWindowsPath(part)
        if piece.drive or piece.root or piece.is_absolute():
            escaped = True
    if not parts or ".." in parts or text.startswith("/") or escaped:
        raise CatalogError(f"filename must stay inside the model, got {filename!r}")
    return "/".join(parts)


def _http(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CatalogError(f"mirror must be an http(s) URL, got {url!r}")
    return url.strip()
