"""Per-app model manifests, install discovery, and the download plan.

Each app ships ``mdl.json``. A model id can have one file set per VRAM tier.
The plan keeps the highest tier that fits the card, so a 24 GB machine does
not also pull the 8 GB and 16 GB variants of the same id. Tier ``any`` is the
lowest rank and is used when no larger variant fits. An unknown GPU is planned
as 8 GB, which is the smallest set.

Files are deduplicated by SHA-256 into ``<models_dir>/blobs/<sha256>``.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from voxprint_common.download import DownloadError, huggingface_url, modelscope_url
from voxprint_common.gpu_probe import VramTier
from voxprint_common.hashing import HASH_RE
from voxprint_common.mdl.names import DISPLAY_NAME
from voxprint_common.paths import models_dir, state_dir

__all__ = [
    "HEADROOM",
    "MANIFEST_NAME",
    "REGISTRY_NAME",
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
    "kaggle_url",
    "load_manifest",
    "plan_files",
    "require_space",
    "suite_root",
]

HEADROOM = 1.15
MANIFEST_NAME = "mdl.json"
REGISTRY_NAME = "installed-apps.json"
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_APP = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_GDRIVE = re.compile(r"^[A-Za-z0-9_-]{10,}$")

DiskFree = Callable[[Path], int]


class CatalogError(ValueError):
    """A model manifest, mirror, or plan input is not valid."""


class DiskShort(CatalogError):
    """Free space is below the plan size plus 15 percent headroom."""

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
            f"need {need} bytes (including 15% headroom), {free} bytes free."
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


@dataclass(frozen=True)
class Model:
    """One variant of a model id."""

    id: str
    version: str
    tier: ModelTier
    files: tuple[ModelFile, ...]


@dataclass(frozen=True)
class AppManifest:
    """A parsed ``mdl.json``."""

    app: str
    models: tuple[Model, ...]
    path: Path


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
            if (folder / MANIFEST_NAME).is_file():
                found.append(InstalledApp(app_id, folder))
        return found
    apps: list[InstalledApp] = []
    for spec in SUITE_APPS:
        folder = install_dir(spec, root, platform=platform)
        if (folder / MANIFEST_NAME).is_file():
            apps.append(InstalledApp(spec.id, folder))
    return apps


def load_manifest(path: Path) -> AppManifest:
    """Read one ``mdl.json``.

    Args:
        path: Manifest file, or a directory that contains ``mdl.json``.

    Raises:
        CatalogError: The document is not schema 1, or a field is invalid.
    """
    target = path / MANIFEST_NAME if path.is_dir() else path
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CatalogError(f"{target} could not be read") from exc
    if not isinstance(payload, dict):
        raise CatalogError(f"{target} must be a JSON object")
    if payload.get("schema") != 1:
        raise CatalogError(f"{target} schema must be 1")
    app = _app(payload.get("app"), "app")
    rows = payload.get("models")
    if not isinstance(rows, list):
        raise CatalogError("models must be a list")
    models = tuple(_model(row) for row in rows)
    return AppManifest(app, models, target)


def plan_files(
    manifests: Sequence[AppManifest],
    tier: VramTier,
    *,
    models_root: Path | None = None,
) -> list[PlannedFile]:
    """Select the variants that fit ``tier`` and deduplicate them by digest.

    Args:
        manifests: Parsed app manifests.
        tier: GPU tier. :attr:`VramTier.UNKNOWN` is planned as 8 GB.
        models_root: Shared models directory. Defaults to
            :func:`voxprint_common.paths.models_dir`.

    Raises:
        CatalogError: The same digest is declared with two sizes.
    """
    root = models_root or models_dir()
    cap = _gpu_rank(tier)
    buckets: dict[str, PlannedFile] = {}
    order: list[str] = []
    for manifest in manifests:
        for model in _select(manifest.models, cap):
            for item in model.files:
                current = buckets.get(item.sha256)
                if current is None:
                    planned = PlannedFile(
                        component_id=f"model-{item.sha256[:16]}",
                        sha256=item.sha256,
                        size=item.size,
                        version=item.sha256,
                        name=item.path,
                        destination=root / "blobs" / item.sha256,
                        urls=item.urls,
                        apps=[manifest.app],
                        model_ids=[model.id],
                        tier=model.tier.value,
                    )
                    buckets[item.sha256] = planned
                    order.append(item.sha256)
                    continue
                if current.size != item.size:
                    raise CatalogError(
                        f"sha256 {item.sha256} is declared as {current.size} and {item.size} bytes"
                    )
                if manifest.app not in current.apps:
                    current.apps.append(manifest.app)
                if model.id not in current.model_ids:
                    current.model_ids.append(model.id)
                merged: list[str] = list(current.urls)
                for url in item.urls:
                    if url not in merged:
                        merged.append(url)
                current.urls = tuple(merged)
    return [buckets[key] for key in order]


def require_space(
    files: Sequence[PlannedFile],
    *,
    disk_free: DiskFree | None = None,
    root: Path | None = None,
) -> tuple[int, int]:
    """Fail before any download when free space is under size times 1.15.

    Bytes already present in a finished file of the expected size are not
    counted. A partial file counts only for the bytes still missing.

    Args:
        files: The plan.
        disk_free: Replacement for ``shutil.disk_usage(path).free``.
        root: Directory whose volume is checked. Defaults to the models dir.

    Returns:
        ``(need, free)`` in bytes. ``need`` includes the 15 percent headroom.

    Raises:
        DiskShort: ``free`` is below ``need``.
        CatalogError: Free space could not be read.
    """
    folder = root or (files[0].destination.parent.parent if files else models_dir())
    folder.mkdir(parents=True, exist_ok=True)
    remaining = _remaining(files)
    need = math.ceil(remaining * HEADROOM)
    free_of = disk_free or _free_bytes
    try:
        free = int(free_of(folder))
    except OSError as exc:
        raise CatalogError(f"could not check free space for {folder}: {exc}") from exc
    if free < need:
        raise DiskShort(folder, need, free)
    return need, free


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


def _model(value: object) -> Model:
    if not isinstance(value, dict):
        raise CatalogError("model must be an object")
    model_id = _ident(value.get("id"), "model id")
    version = value.get("version")
    if not isinstance(version, str) or not version.strip():
        raise CatalogError(f"model {model_id} needs a version")
    tier = _tier(value.get("vram_tier"))
    rows = value.get("files")
    if not isinstance(rows, list) or not rows:
        raise CatalogError(f"model {model_id} needs at least one file")
    return Model(model_id, version.strip(), tier, tuple(_file(model_id, row) for row in rows))


def _file(model_id: str, value: object) -> ModelFile:
    if not isinstance(value, dict):
        raise CatalogError(f"model {model_id} file must be an object")
    name = _rel(str(value.get("path") or "model.bin"))
    size = value.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise CatalogError(f"model {model_id} file size must be a positive integer")
    digest = str(value.get("sha256") or "").strip().lower()
    if HASH_RE.fullmatch(digest) is None:
        raise CatalogError(f"model {model_id} sha256 must be 64 hexadecimal characters")
    mirrors = value.get("mirrors")
    if not isinstance(mirrors, list) or not mirrors:
        raise CatalogError(f"model {model_id} needs at least one mirror")
    urls = tuple(_mirror(row) for row in mirrors)
    return ModelFile(name, size, digest, urls)


def _mirror(value: object) -> str:
    if isinstance(value, str):
        return _http(value)
    if not isinstance(value, dict):
        raise CatalogError("mirror must be an object or an http(s) URL")
    source = value.get("source")
    try:
        match source:
            case "huggingface":
                return huggingface_url(
                    _text(value.get("repo"), "repo"),
                    _text(value.get("filename"), "filename"),
                    _text(value.get("revision") or "main", "revision"),
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
    if not parts or ".." in parts or text.startswith("/"):
        raise CatalogError(f"filename must stay inside the model, got {filename!r}")
    return "/".join(parts)


def _http(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CatalogError(f"mirror must be an http(s) URL, got {url!r}")
    return url.strip()
