"""``voxprint-model/1`` and ``voxprint-app-models/1``.

The model document is checked against the vendored Model Forge schema rules.
``voxprint-model.json`` is not a member of ``files``. VRAM numbers are GiB:
a 24 GB card is 23.5 and a 16 GB card is 15.5.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

__all__ = [
    "APP_SCHEMA",
    "MODEL_SCHEMA",
    "PLACEHOLDERS",
    "AppModels",
    "BuildInfo",
    "Descriptor",
    "DescriptorError",
    "DescriptorFile",
    "FallbackSet",
    "FileGroup",
    "MirrorRef",
    "OnnxPrebuilt",
    "OptionalRef",
    "PostInstall",
    "RuntimeInfo",
    "TierInfo",
    "TierRef",
    "app_schema_bytes",
    "inside_relative",
    "parse_app_manifest",
    "parse_descriptor",
    "parse_descriptor_text",
    "parse_onnx_prebuilt",
    "parse_post_install",
    "schema_bytes",
]

MODEL_SCHEMA = "voxprint-model/1"
APP_SCHEMA = "voxprint-app-models/1"

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_LICENSE = re.compile(r"^[a-z0-9][a-z0-9.+-]*$")
_PATH = re.compile(r"^(?!voxprint-model\.json$)(?!/)(?!.*\\)(?!.*\.\./)[^\\]+$")
_CARD = re.compile(r"^(?:|https://\S+)$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ENGINES = frozenset({"llama.cpp", "diffusers", "nunchaku", "onnxruntime"})
_KINDS = frozenset({"hf", "kaggle", "gdrive", "modelscope", "url"})
_POLICIES = frozenset({"race", "strict-priority"})
_TIER_KEYS = frozenset({"8gb", "16gb", "24gb"})

_MODEL_KEYS = frozenset(
    {
        "schema",
        "id",
        "display_name",
        "app_ids",
        "task",
        "license",
        "files",
        "mirrors",
        "tier",
        "runtime",
        "base_model",
        "card_url",
        "flags",
        "on_demand",
        "build",
    }
)
_MODEL_OPTIONAL = frozenset(
    {"mirror_policy", "file_groups", "prebuilt", "onnx_prebuilt", "post_install"}
)
_CLI = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PLACEHOLDERS = frozenset({"id", "model_dir", "source_path", "prebuilt_path", "cache_dir"})
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
DEFAULT_TIMEOUT_S = 600.0
MAX_TIMEOUT_S = 3600.0
_MIRROR_KEYS = frozenset({"kind", "ref", "revision", "ours"})


class DescriptorError(ValueError):
    """A model or app document does not match its schema."""


@dataclass(frozen=True)
class DescriptorFile:
    """One weight file. The path is the same string on every mirror.

    ``mirror_policy`` and ``sources`` are empty when the file inherits the
    model, or its file group.
    """

    path: str
    size: int
    sha256: str
    mirror_policy: str = ""
    sources: tuple[MirrorRef, ...] = ()


@dataclass(frozen=True)
class MirrorRef:
    """One mirror. ``revision`` is required by the schema and may be empty."""

    kind: str
    ref: str
    revision: str
    ours: bool = False


@dataclass(frozen=True)
class FallbackSet:
    """A recorded alternative for some default files, from ``prebuilt``.

    ``replaces`` is a path, or a folder prefix ending in ``/``. Only entries
    with ``role`` ``fallback`` are used by the downloader.
    """

    id: str
    role: str
    replaces: str
    files: tuple[DescriptorFile, ...]
    note: str = ""

    def covers(self, path: str) -> bool:
        """Return whether ``path`` is one of the default files this set replaces."""
        if self.replaces.endswith("/"):
            return path.startswith(self.replaces)
        return path == self.replaces


@dataclass(frozen=True)
class FileGroup:
    """Files that share a source list and a mirror policy.

    Empty ``mirror_policy`` and empty ``sources`` inherit from the model.
    ``name`` is empty when the group cannot be selected from an app manifest.
    """

    paths: tuple[str, ...]
    mirror_policy: str = ""
    sources: tuple[MirrorRef, ...] = ()
    name: str = ""


@dataclass(frozen=True)
class TierInfo:
    """VRAM in GiB and a minimum compute capability."""

    min_vram_gib: float
    recommended_vram_gib: float
    gpu_min_cc: float


@dataclass(frozen=True)
class RuntimeInfo:
    """The engine that runs the weights."""

    engine: str
    version_req: str


@dataclass(frozen=True)
class BuildInfo:
    """Forge provenance. ``tag`` is empty when the build is unreleased."""

    forge_version: str
    tag: str
    date: str


@dataclass(frozen=True)
class Descriptor:
    """A parsed ``voxprint-model.json``."""

    id: str
    display_name: str
    app_ids: tuple[str, ...]
    task: str
    license: str
    files: tuple[DescriptorFile, ...]
    mirrors: tuple[MirrorRef, ...]
    tier: TierInfo
    runtime: RuntimeInfo
    base_model: str
    card_url: str
    flags: tuple[str, ...]
    on_demand: bool
    build: BuildInfo
    mirror_policy: str = "race"
    file_groups: tuple[FileGroup, ...] = ()
    fallbacks: tuple[FallbackSet, ...] = ()
    onnx_prebuilt: tuple[OnnxPrebuilt, ...] = ()
    post_install: PostInstall | None = None

    def policy_for(self, path: str) -> str:
        """Return the mirror policy for one file.

        A file override wins, then its file group, then the model. A missing
        policy is ``race``.

        Args:
            path: File path inside the repo.
        """
        for item in self.files:
            if item.path == path and item.mirror_policy:
                return item.mirror_policy
        for group in self.file_groups:
            if path in group.paths and group.mirror_policy:
                return group.mirror_policy
        return self.mirror_policy or "race"

    def mirrors_for(self, path: str) -> tuple[MirrorRef, ...]:
        """Return the ordered sources for one file.

        A file ``sources`` list wins, then its file group, then the model
        mirrors.

        Args:
            path: File path inside the repo.
        """
        for item in self.files:
            if item.path == path and item.sources:
                return item.sources
        for group in self.file_groups:
            if path in group.paths and group.sources:
                return group.sources
        return self.mirrors


@dataclass(frozen=True)
class TierRef:
    """One model in a VRAM tier, and the files that tier keeps.

    ``files`` are globs and ``file_groups`` are names of descriptor groups.
    ``constrained`` is false when the entry is a bare id, which keeps every
    file. An explicit ``files`` or ``file_groups`` list, even an empty one,
    keeps only the matches.
    """

    id: str
    files: tuple[str, ...] = ()
    file_groups: tuple[str, ...] = ()
    constrained: bool = False


@dataclass(frozen=True)
class OptionalRef:
    """A model the app can name, and the VRAM floor for that request."""

    id: str
    min_vram_gib: float | None


@dataclass(frozen=True)
class PostInstall:
    """A command run after the weights are in place.

    ``model_id`` is empty when the hook belongs to the whole app.
    ``produces`` paths are relative to ``{model_dir}``.
    """

    argv: tuple[str, ...]
    timeout_s: float
    produces: tuple[str, ...]
    model_id: str = ""
    cli: str = ""
    output_sha256: tuple[str, ...] = ()


@dataclass(frozen=True)
class OnnxPrebuilt:
    """An optional pre-built artifact, such as an ``.onnx`` export.

    ``onnxruntime`` is a version spec such as ``>=1.20,<2``. ``opset`` is the
    ONNX opset the artifact was exported with. ``sources`` are resolved URLs
    or mirror refs, tried with ``mirror_policy``.
    """

    path: str
    sha256: str
    size: int | None
    sources: tuple[Any, ...]
    url: str
    mirror_policy: str
    onnxruntime: str
    opset: int
    source_file: str = ""


@dataclass(frozen=True)
class AppModels:
    """A parsed ``voxprint-app-models/1`` document."""

    app_id: str
    tiers: tuple[tuple[str, tuple[str, ...]], ...]
    runtimes: tuple[str, ...]
    optional: tuple[OptionalRef, ...]
    post_install: PostInstall | None = None
    model_hooks: tuple[PostInstall, ...] = ()
    unavailable: tuple[tuple[str, str], ...] = ()
    selections: tuple[tuple[str, tuple[TierRef, ...]], ...] = ()

    def tier_ids(self, name: str) -> tuple[str, ...] | None:
        """Return the model ids listed for ``name``, or None when it is absent."""
        for key, ids in self.tiers:
            if key == name:
                return ids
        return None

    def tier_refs(self, name: str) -> tuple[TierRef, ...] | None:
        """Return the tier entries for ``name``, or None when it is absent."""
        for key, refs in self.selections:
            if key == name:
                return refs
        ids = self.tier_ids(name)
        if ids is None:
            return None
        return tuple(TierRef(model_id) for model_id in ids)


def schema_bytes() -> bytes:
    """Return the vendored ``voxprint-model/1`` schema file bytes, unchanged."""
    return files("voxprint_common.mdl.schemas").joinpath("voxprint-model.schema.json").read_bytes()


def app_schema_bytes() -> bytes:
    """Return the vendored ``voxprint-app-models/1`` schema file bytes."""
    name = "voxprint-app-models.schema.json"
    return files("voxprint_common.mdl.schemas").joinpath(name).read_bytes()


def parse_descriptor(payload: object) -> Descriptor:
    """Parse one ``voxprint-model/1`` document.

    Args:
        payload: A decoded JSON value.

    Raises:
        DescriptorError: The document does not match the schema.
    """
    row = _object(payload, _MODEL_KEYS, "voxprint-model", _MODEL_OPTIONAL)
    if row.get("schema") != MODEL_SCHEMA:
        raise DescriptorError("schema must be voxprint-model/1")
    files = _files(row.get("files"))
    model_id = _ident(row.get("id"), "id")
    policy = "race"
    if "mirror_policy" in row:
        policy = _named_policy(row.get("mirror_policy"), "mirror_policy")
    return Descriptor(
        id=model_id,
        display_name=_text(row.get("display_name"), "display_name"),
        app_ids=_app_ids(row.get("app_ids")),
        task=_text(row.get("task"), "task"),
        license=_license(row.get("license")),
        files=files,
        mirrors=_mirrors(row.get("mirrors")),
        mirror_policy=policy,
        file_groups=_file_groups(row.get("file_groups"), {item.path for item in files}),
        tier=_tier(row.get("tier")),
        runtime=_runtime(row.get("runtime")),
        base_model=_plain(row.get("base_model"), "base_model"),
        card_url=_card(row.get("card_url")),
        flags=_flags(row.get("flags")),
        on_demand=_bool(row.get("on_demand"), "on_demand"),
        build=_build(row.get("build")),
        fallbacks=_fallbacks(row.get("prebuilt"), {item.path for item in files}),
        onnx_prebuilt=_onnx_prebuilts(row),
        post_install=_post_install(row.get("post_install"), "post_install", model_id),
    )


def parse_descriptor_text(text: str) -> Descriptor:
    """Parse UTF-8 JSON text into a descriptor.

    Args:
        text: The file contents.

    Raises:
        DescriptorError: The text is not a matching document.
    """
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise DescriptorError("voxprint-model.json is not JSON") from exc
    return parse_descriptor(payload)


def parse_app_manifest(payload: object) -> AppModels:
    """Parse one ``voxprint-app-models/1`` document.

    Args:
        payload: A decoded JSON value.

    Raises:
        DescriptorError: The document is not that schema.
    """
    if not isinstance(payload, dict):
        raise DescriptorError("voxprint-app-models must be an object")
    required = {"schema", "app_id", "tiers", "runtimes", "optional"}
    allowed = required | {"post_install", "hooks", "unavailable"}
    extra = set(payload) - allowed
    if extra:
        raise DescriptorError(f"unknown app-manifest fields: {', '.join(sorted(extra))}")
    missing = required - set(payload)
    if missing:
        raise DescriptorError(f"app manifest missing {', '.join(sorted(missing))}")
    if payload.get("schema") != APP_SCHEMA:
        raise DescriptorError("schema must be voxprint-app-models/1")
    selections = _tiers(payload.get("tiers"))
    return AppModels(
        app_id=_ident(payload.get("app_id"), "app_id"),
        tiers=tuple((key, tuple(ref.id for ref in refs)) for key, refs in selections),
        runtimes=_runtimes(payload.get("runtimes")),
        optional=_optional(payload.get("optional")),
        post_install=_post_install(payload.get("post_install"), "post_install"),
        model_hooks=_hooks(payload.get("hooks", [])),
        unavailable=_unavailable(payload.get("unavailable", [])),
        selections=selections,
    )


def _object(
    value: object,
    keys: frozenset[str],
    label: str,
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DescriptorError(f"{label} must be an object")
    present = set(value)
    missing = keys - present
    extra = present - keys - optional
    if missing:
        raise DescriptorError(f"{label} missing {', '.join(sorted(missing))}")
    if extra:
        raise DescriptorError(f"{label} has unknown fields: {', '.join(sorted(extra))}")
    return value


def _files(value: object) -> tuple[DescriptorFile, ...]:
    rows = _list(value, "files", minimum=1)
    files: list[DescriptorFile] = []
    allowed = {"path", "size", "sha256", "mirror_policy", "sources", "onnx_prebuilt"}
    for row in rows:
        if not isinstance(row, dict) or not {"path", "size", "sha256"} <= set(row) <= allowed:
            raise DescriptorError("files entries need path, size, and sha256")
        path = row.get("path")
        if not isinstance(path, str) or _PATH.fullmatch(path) is None:
            raise DescriptorError(f"file path is not allowed: {path!r}")
        size = row.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise DescriptorError("file size must be a non-negative integer")
        digest = row.get("sha256")
        if not isinstance(digest, str) or _SHA.fullmatch(digest) is None:
            raise DescriptorError("file sha256 must be 64 lowercase hex characters")
        policy = ""
        if "mirror_policy" in row:
            policy = _named_policy(row.get("mirror_policy"), "files.mirror_policy")
        sources = _mirrors(row.get("sources"), "sources") if "sources" in row else ()
        files.append(DescriptorFile(path, size, digest, policy, sources))
    return tuple(files)


def _file_groups(value: object, paths: set[str]) -> tuple[FileGroup, ...]:
    if value is None:
        return ()
    rows = _list(value, "file_groups", minimum=0)
    seen: set[str] = set()
    groups: list[FileGroup] = []
    allowed = {"name", "paths", "mirror_policy", "sources"}
    named: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not {"paths"} <= set(row) <= allowed:
            raise DescriptorError("file_groups entries need paths")
        group_name = ""
        if "name" in row:
            group_name = _ident(row.get("name"), "file_groups.name")
            if group_name in named:
                raise DescriptorError(f"duplicate file group {group_name}")
            named.add(group_name)
        paths_in_group: list[str] = []
        for item in _list(row.get("paths"), "file_groups.paths", minimum=1):
            if not isinstance(item, str) or item not in paths:
                raise DescriptorError(f"file group path is not a file: {item!r}")
            if item in seen:
                raise DescriptorError(f"file {item} is in more than one group")
            seen.add(item)
            paths_in_group.append(item)
        policy = ""
        if "mirror_policy" in row:
            policy = _named_policy(row.get("mirror_policy"), "file_groups.mirror_policy")
        sources = _mirrors(row.get("sources"), "sources") if "sources" in row else ()
        groups.append(FileGroup(tuple(paths_in_group), policy, sources, group_name))
    return tuple(groups)


def _fallbacks(value: object, paths: set[str]) -> tuple[FallbackSet, ...]:
    if value is None:
        return ()
    found: list[FallbackSet] = []
    seen: set[str] = set()
    for row in _list(value, "prebuilt", minimum=0):
        if not isinstance(row, dict) or not {"id", "role", "replaces", "files"} <= set(row) <= {
            "id",
            "role",
            "replaces",
            "files",
            "note",
        }:
            raise DescriptorError("prebuilt entries need id, role, replaces, and files")
        set_id = _ident(row.get("id"), "prebuilt.id")
        if set_id in seen:
            raise DescriptorError(f"duplicate prebuilt id {set_id}")
        seen.add(set_id)
        role = _text(row.get("role"), "prebuilt.role")
        replaces = row.get("replaces")
        if not isinstance(replaces, str) or not replaces or _PATH.fullmatch(replaces) is None:
            raise DescriptorError(f"prebuilt.replaces is not allowed: {replaces!r}")
        note = row.get("note", "")
        if not isinstance(note, str):
            raise DescriptorError("prebuilt.note must be a string")
        files = _files(row.get("files"))
        group = FallbackSet(set_id, role, replaces, files, note)
        if not any(group.covers(path) for path in paths):
            raise DescriptorError(f"prebuilt {set_id} replaces no default file")
        found.append(group)
    return tuple(found)


def _named_policy(value: object, label: str) -> str:
    if value not in _POLICIES:
        raise DescriptorError(f"{label} must be race or strict-priority")
    return str(value)


def _mirrors(value: object, label: str = "mirrors") -> tuple[MirrorRef, ...]:
    rows = _list(value, label, minimum=1)
    mirrors: list[MirrorRef] = []
    for row in rows:
        keys = set(row) if isinstance(row, dict) else set()
        if not isinstance(row, dict) or not {"kind", "ref", "revision"} <= keys <= _MIRROR_KEYS:
            raise DescriptorError(f"{label} entries need kind, ref, and revision")
        ours = row.get("ours", False)
        if not isinstance(ours, bool):
            raise DescriptorError(f"{label} ours must be a boolean")
        kind = row.get("kind")
        if kind not in _KINDS:
            raise DescriptorError(f"unknown mirror kind {kind!r}")
        ref = row.get("ref")
        revision = row.get("revision")
        if not isinstance(ref, str) or not ref:
            raise DescriptorError("mirror ref is required")
        if not isinstance(revision, str):
            raise DescriptorError("mirror revision must be a string")
        mirrors.append(MirrorRef(str(kind), ref, revision, ours))
    return tuple(mirrors)


def _tier(value: object) -> TierInfo:
    row = _field_object(value, "tier", {"min_vram_gib", "recommended_vram_gib", "gpu_min_cc"})
    return TierInfo(
        min_vram_gib=_number(row.get("min_vram_gib"), "min_vram_gib"),
        recommended_vram_gib=_number(row.get("recommended_vram_gib"), "recommended_vram_gib"),
        gpu_min_cc=_number(row.get("gpu_min_cc"), "gpu_min_cc"),
    )


def _runtime(value: object) -> RuntimeInfo:
    row = _field_object(value, "runtime", {"engine", "version_req"})
    engine = row.get("engine")
    if engine not in _ENGINES:
        raise DescriptorError(f"unknown runtime engine {engine!r}")
    return RuntimeInfo(str(engine), _text(row.get("version_req"), "version_req"))


def _build(value: object) -> BuildInfo:
    row = _field_object(value, "build", {"forge_version", "tag", "date"})
    date = row.get("date")
    if not isinstance(date, str) or _DATE.fullmatch(date) is None:
        raise DescriptorError("build.date must be YYYY-MM-DD")
    tag = row.get("tag")
    if not isinstance(tag, str):
        raise DescriptorError("build.tag must be a string")
    return BuildInfo(_text(row.get("forge_version"), "forge_version"), tag, date)


def _app_ids(value: object) -> tuple[str, ...]:
    rows = _list(value, "app_ids", minimum=1)
    found: list[str] = []
    for item in rows:
        if not isinstance(item, str) or not item:
            raise DescriptorError("app_ids entries must be non-empty strings")
        if item in found:
            raise DescriptorError(f"duplicate app id {item}")
        found.append(item)
    return tuple(found)


def _flags(value: object) -> tuple[str, ...]:
    rows = _list(value, "flags", minimum=0)
    found: list[str] = []
    for item in rows:
        if not isinstance(item, str) or not item:
            raise DescriptorError("flags entries must be non-empty strings")
        if item in found:
            raise DescriptorError(f"duplicate flag {item}")
        found.append(item)
    return tuple(found)


def _tiers(value: object) -> tuple[tuple[str, tuple[TierRef, ...]], ...]:
    if not isinstance(value, dict) or not value:
        raise DescriptorError("tiers must be an object")
    rows: list[tuple[str, tuple[TierRef, ...]]] = []
    for key, raw in value.items():
        if key not in _TIER_KEYS:
            raise DescriptorError("tiers keys must be 8gb, 16gb, or 24gb")
        ids = _list(raw, f"tiers.{key}", minimum=0)
        refs = tuple(_tier_ref(item, f"tiers.{key}") for item in ids)
        rows.append((key, refs))
    return tuple(rows)


def _tier_ref(value: object, label: str) -> TierRef:
    if isinstance(value, str):
        return TierRef(_ident(value, label))
    if not isinstance(value, dict) or not {"id"} <= set(value) <= {"id", "files", "file_groups"}:
        raise DescriptorError(f"{label} entry must be an id or an object with id")
    model_id = _ident(value.get("id"), f"{label}.id")
    globs: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    constrained = "files" in value or "file_groups" in value
    if "files" in value:
        listed = _list(value.get("files"), f"{label}.files", minimum=0)
        globs = tuple(_glob(item, f"{label}.files") for item in listed)
    if "file_groups" in value:
        listed_groups = _list(value.get("file_groups"), f"{label}.file_groups", minimum=0)
        groups = tuple(_ident(item, f"{label}.file_groups") for item in listed_groups)
    return TierRef(model_id, globs, groups, constrained)


def _glob(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DescriptorError(f"{label} must be a glob")
    text = value.strip()
    if text.startswith("/") or "\\" in text or ".." in PurePosixPath(text).parts:
        raise DescriptorError(f"{label} must be a relative glob")
    return text


def _runtimes(value: object) -> tuple[str, ...]:
    rows = _list(value, "runtimes", minimum=0)
    return tuple(_ident(item, "runtimes") for item in rows)


def parse_post_install(
    value: object,
    label: str = "post_install",
    model_id: str = "",
) -> PostInstall | None:
    """Parse a ``post_install`` object, or return None when it is absent.

    Args:
        value: The JSON value, or None.
        label: Field name used in errors.
        model_id: Model this hook belongs to. Empty means the whole app.

    Raises:
        DescriptorError: The object is not a hook.
    """
    return _post_install(value, label, model_id)


def _post_install(value: object, label: str, model_id: str = "") -> PostInstall | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DescriptorError(f"{label} must be an object")
    if "cli" in value:
        return _cli_hook(value, label, model_id)
    allowed = {"argv", "timeout_s", "produces"}
    if set(value) != allowed:
        raise DescriptorError(f"{label} needs argv, timeout_s, and produces")
    argv = value.get("argv")
    bad_argv = (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(item, str) or not item for item in argv)
    )
    if bad_argv:
        raise DescriptorError(f"{label} argv must be a list of strings")
    timeout = value.get("timeout_s")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise DescriptorError(f"{label} timeout_s must be a positive number")
    produces = value.get("produces")
    bad_paths = not isinstance(produces, list) or any(
        not isinstance(item, str) or not item for item in produces
    )
    if bad_paths:
        raise DescriptorError(f"{label} produces must be a list of relative paths")
    for item in produces:
        path = Path(item)
        if path.is_absolute() or ".." in path.parts:
            raise DescriptorError(f"{label} produces must stay inside the model folder")
    return PostInstall(tuple(argv), float(timeout), tuple(produces), model_id)


def _cli_hook(value: dict[str, Any], label: str, model_id: str) -> PostInstall:
    extra = set(value) - {"cli", "args", "timeout_s", "outputs"}
    if extra or "args" not in value:
        raise DescriptorError(f"{label} takes cli, args, timeout_s, and outputs")
    cli = value.get("cli")
    if not isinstance(cli, str) or _CLI.fullmatch(cli) is None:
        raise DescriptorError(f"{label} cli must be the app's own executable name, not a path")
    args = value.get("args")
    if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
        raise DescriptorError(f"{label} args must be a list of strings")
    for item in args:
        for name in _PLACEHOLDER.findall(item):
            if name not in PLACEHOLDERS:
                raise DescriptorError(f"{label} placeholder {{{name}}} is not allowed")
        if _PLACEHOLDER.sub("", item).count("{") or _PLACEHOLDER.sub("", item).count("}"):
            raise DescriptorError(f"{label} args have an unbalanced brace")
    timeout = value.get("timeout_s", DEFAULT_TIMEOUT_S)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise DescriptorError(f"{label} timeout_s must be a positive number")
    outputs = value.get("outputs", [])
    if not isinstance(outputs, list):
        raise DescriptorError(f"{label} outputs must be a list")
    paths: list[str] = []
    digests: list[str] = []
    for row in outputs:
        if not isinstance(row, dict) or "path" not in row or set(row) - {"path", "sha256"}:
            raise DescriptorError(f"{label} outputs entries need path and optional sha256")
        paths.append(inside_relative(row.get("path"), f"{label} outputs"))
        digest = row.get("sha256", "")
        if digest and (not isinstance(digest, str) or _SHA.fullmatch(digest) is None):
            raise DescriptorError(f"{label} outputs sha256 must be 64 lowercase hex characters")
        digests.append(str(digest))
    return PostInstall(
        (cli, *args),
        min(float(timeout), MAX_TIMEOUT_S),
        tuple(paths),
        model_id,
        cli=cli,
        output_sha256=tuple(digests),
    )


def inside_relative(value: object, label: str) -> str:
    """Return ``value`` when it is a relative path that stays inside its folder.

    Args:
        value: The path string from a document.
        label: Field name used in errors.

    Raises:
        DescriptorError: The path is absolute, uses ``..``, or a backslash.
    """
    if not isinstance(value, str) or not value or "\\" in value:
        raise DescriptorError(f"{label} path must be a relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or PureWindowsPath(value).drive:
        raise DescriptorError(f"{label} path must stay inside the model folder")
    return value


def parse_onnx_prebuilt(
    value: object,
    label: str = "onnx_prebuilt",
    source_file: str = "",
    *,
    mirrors: Callable[[object, str], tuple[Any, ...]] | None = None,
) -> OnnxPrebuilt:
    """Parse one ``prebuilt`` object.

    Args:
        value: The JSON value.
        label: Field name used in errors.
        source_file: The file it replaces, when it sits on a file entry.
        mirrors: Parser for ``sources``. Defaults to ``voxprint-model/1``
            mirror refs; ``mdl.json`` passes its own mirror format.

    Raises:
        DescriptorError: The object is not a pre-built artifact.
    """
    allowed = {"path", "sha256", "size", "url", "sources", "mirror_policy", "built_for"}
    if not isinstance(value, dict) or not {"path", "sha256", "built_for"} <= set(value) <= allowed:
        raise DescriptorError(f"{label} needs path, sha256, built_for, and url or sources")
    if ("url" in value) == ("sources" in value):
        raise DescriptorError(f"{label} needs exactly one of url or sources")
    path = value.get("path")
    if not isinstance(path, str) or _PATH.fullmatch(path) is None:
        raise DescriptorError(f"{label} path is not allowed: {path!r}")
    digest = value.get("sha256")
    if not isinstance(digest, str) or _SHA.fullmatch(digest) is None:
        raise DescriptorError(f"{label} sha256 must be 64 lowercase hex characters")
    size = value.get("size")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
        raise DescriptorError(f"{label} size must be a non-negative integer")
    url = value.get("url", "")
    if "url" in value and (not isinstance(url, str) or not url.startswith("https://")):
        raise DescriptorError(f"{label} url must be https")
    parse = mirrors or _mirrors
    sources = parse(value.get("sources"), f"{label}.sources") if "sources" in value else ()
    policy = "race"
    if "mirror_policy" in value:
        policy = _named_policy(value.get("mirror_policy"), f"{label}.mirror_policy")
    built = value.get("built_for")
    if not isinstance(built, dict) or set(built) != {"onnxruntime", "opset"}:
        raise DescriptorError(f"{label} built_for needs onnxruntime and opset")
    spec = built.get("onnxruntime")
    opset = built.get("opset")
    if not isinstance(spec, str) or not spec.strip():
        raise DescriptorError(f"{label} built_for.onnxruntime must be a version spec")
    if isinstance(opset, bool) or not isinstance(opset, int) or opset < 1:
        raise DescriptorError(f"{label} built_for.opset must be a positive integer")
    return OnnxPrebuilt(path, digest, size, sources, url, policy, spec.strip(), opset, source_file)


def _onnx_prebuilts(row: dict[str, Any]) -> tuple[OnnxPrebuilt, ...]:
    found: list[OnnxPrebuilt] = []
    if "onnx_prebuilt" in row:
        found.append(parse_onnx_prebuilt(row.get("onnx_prebuilt")))
    for item in row.get("files") or ():
        if isinstance(item, dict) and "onnx_prebuilt" in item:
            found.append(
                parse_onnx_prebuilt(item["onnx_prebuilt"], "files.onnx_prebuilt", str(item["path"]))
            )
    return tuple(found)


def _unavailable(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise DescriptorError("unavailable must be a list")
    found: list[tuple[str, str]] = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {"id", "reason"}:
            raise DescriptorError("unavailable entries need id and reason")
        reason = row.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise DescriptorError("unavailable reason is required")
        found.append((_ident(row.get("id"), "unavailable.id"), reason.strip()))
    return tuple(found)


def _hooks(value: object) -> tuple[PostInstall, ...]:
    if not isinstance(value, list):
        raise DescriptorError("hooks must be a list")
    found: list[PostInstall] = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {"id", "post_install"}:
            raise DescriptorError("hooks entries need id and post_install")
        model_id = _ident(row.get("id"), "hooks.id")
        hook = _post_install(row.get("post_install"), f"hooks.{model_id}", model_id)
        if hook is None:
            raise DescriptorError(f"hooks.{model_id} needs post_install")
        found.append(hook)
    return tuple(found)


def _optional(value: object) -> tuple[OptionalRef, ...]:
    rows = _list(value, "optional", minimum=0)
    found: list[OptionalRef] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "min_vram_gib"}:
            raise DescriptorError("optional entries need id and min_vram_gib")
        floor = row.get("min_vram_gib")
        gib = None if floor is None else _number(floor, "min_vram_gib")
        found.append(OptionalRef(_ident(row.get("id"), "optional.id"), gib))
    return tuple(found)


def _field_object(value: object, label: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise DescriptorError(f"{label} fields must be {', '.join(sorted(keys))}")
    return value


def _list(value: object, label: str, *, minimum: int) -> list[object]:
    if not isinstance(value, list) or len(value) < minimum:
        raise DescriptorError(f"{label} must be a list")
    return list(value)


def _ident(value: object, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise DescriptorError(f"{label} must match [a-z0-9][a-z0-9._-]*")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise DescriptorError(f"{label} is required")
    return value


def _plain(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise DescriptorError(f"{label} must be a string")
    return value


def _license(value: object) -> str:
    if not isinstance(value, str) or _LICENSE.fullmatch(value) is None:
        raise DescriptorError("license must be a lowercase SPDX id")
    return value


def _card(value: object) -> str:
    if not isinstance(value, str) or _CARD.fullmatch(value) is None:
        raise DescriptorError("card_url must be empty or an https URL")
    return value


def _bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise DescriptorError(f"{label} must be a boolean")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DescriptorError(f"{label} must be a number")
    if value < 0:
        raise DescriptorError(f"{label} must be zero or positive")
    return float(value)
