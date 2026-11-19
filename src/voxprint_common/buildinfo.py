"""Build identity for every Voxprint app.

``BUILD.json`` is the catalogue: ``offset``, ``codename``, and the version.
A packaged app also carries a frozen ``build_info.json`` with the build
number recorded at pack time. :func:`load` reads those two files together.

The file this package writes has five keys:

* ``app``: suite id or the name drawn on the splash
* ``version``: ``X.Y.Z``
* ``rc``: release-candidate number, or JSON null for a final build
* ``build``: integer build number, or JSON null for a local dev tree
* ``codename``: short name, or an empty string

The build number is ``GITHUB_RUN_NUMBER`` plus ``offset``. When that
variable is absent, the recorded ``build`` in ``build_info.json`` or
``BUILD.json`` is used. A missing recorded build stays null. It is not
reported as 0.

:func:`load` also accepts the older per-app documents: Enlarge
(``version``, ``offset``, ``codename``), Clarity (those plus ``build``),
Canvas (``version``, ``channel``, ``build``, ``offset``, ``codename``),
Lucent (``number``, ``offset``, ``codename``), Plotweaver (``version``,
``prerelease``, ``codename``, ``offset``, ``build``), Model Forge and
Movie Dubber (``offset``, ``codename``), and Audiobook Builder (``offset``,
``codename``, plus any other keys). Unknown keys are ignored.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = ["BuildInfo", "dump", "label", "load", "parse"]

_VERSION_RC = re.compile(r"^(\d+\.\d+\.\d+)(?:-rc(?:\.(\d+))?)?$", re.IGNORECASE)
_RC_NUMBER = re.compile(r"^rc\.?(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class BuildInfo:
    """Build identity used by ``--version``, the splash, and ``--json``."""

    app: str
    version: str
    rc: int | None
    build: int | None
    codename: str
    show_rc: bool = False

    def label(self) -> str:
        """Return ``X.Y.Z RC N build B "Codename"``.

        The ``RC`` word is omitted for a final build. A null ``build`` is
        rendered as ``dev``. An empty codename is omitted.

        Returns:
            The single-line build label.
        """
        return label(self)


def label(info: BuildInfo) -> str:
    """Return the human build label for ``info``.

    Args:
        info: Parsed build identity.

    Returns:
        Text such as ``0.2.0 RC 4 build 12 "North"``.
    """
    parts: list[str] = []
    if info.version:
        parts.append(info.version)
    if info.rc is not None:
        parts.append(f"RC {info.rc}")
    elif info.show_rc:
        parts.append("RC")
    parts.append("dev" if info.build is None else f"build {info.build}")
    text = " ".join(parts)
    if info.codename:
        text += f' "{info.codename}"'
    return text


def load(
    path: Path | str | None = None,
    *,
    app: str = "",
    package_dir: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> BuildInfo:
    """Read the build identity from ``BUILD.json`` and ``build_info.json``.

    An explicit ``path`` is a file or a directory and is used on its own.
    Otherwise the search is the app package directory, then the frozen
    executable directory (and PyInstaller's bundle directory), then the
    repository root. In a directory that has both files, ``BUILD.json``
    supplies ``offset`` and ``build_info.json`` supplies the recorded build.

    Args:
        path: A ``BUILD.json`` or ``build_info.json`` file, or a directory
            that contains either. Omit it to search.
        app: Suite id used when the file has no ``app`` field.
        package_dir: Directory of the app package. Used only when ``path``
            is omitted.
        env: Environment for ``VOXPRINT_BUILD`` and ``GITHUB_RUN_NUMBER``.
            Defaults to ``os.environ``.

    Returns:
        The parsed identity. Older shapes are mapped onto the five fields.

    Raises:
        FileNotFoundError: Neither file was found.
        ValueError: A file that was found is not a JSON object.
    """
    pairs = _collect(path, package_dir)
    if not pairs:
        raise FileNotFoundError("BUILD.json or build_info.json was not found")
    payload: dict[str, object] = {}
    offset: object = None
    # Later directories in the search are lower priority, so fold from the end.
    for catalog_path, frozen_path in reversed(pairs):
        catalog = _read_object(catalog_path) if catalog_path is not None else None
        frozen = _read_object(frozen_path) if frozen_path is not None else None
        if catalog is not None and "offset" in catalog:
            offset = catalog["offset"]
        layer = _merge(catalog, frozen)
        if not str(layer.get("codename") or "").strip():
            layer.pop("codename", None)
        payload = _merge(payload or None, layer)
    if offset is not None:
        payload["offset"] = offset
    return parse(payload, app=app, env=env)


def parse(
    data: Mapping[str, object],
    *,
    app: str = "",
    env: Mapping[str, str] | None = None,
) -> BuildInfo:
    """Map a ``BUILD.json`` object, including older shapes, onto :class:`BuildInfo`.

    ``VOXPRINT_BUILD`` overrides the build number. Otherwise
    ``GITHUB_RUN_NUMBER`` plus ``offset`` is the build number. When that
    variable is absent, the recorded ``build`` (or Lucent's ``number``) is
    used. A missing recorded build stays null.

    Args:
        data: JSON object.
        app: Fallback suite id.
        env: Environment mapping. Defaults to ``os.environ``.

    Returns:
        The parsed identity.
    """
    source = os.environ if env is None else env
    version, version_rc, version_show = _split_version(data.get("version"))
    pre_rc, pre_show = _rc_token(data.get("prerelease"))
    channel_rc, channel_show = _rc_token(data.get("channel"))
    explicit_rc = _as_int(data.get("rc")) if "rc" in data else None
    rc = explicit_rc
    if rc is None:
        rc = version_rc if version_rc is not None else pre_rc if pre_rc is not None else channel_rc
    show_rc = explicit_rc is not None or version_show or pre_show or channel_show or rc is not None
    if "rc" in data and data.get("rc") is None and not (version_show or pre_show or channel_show):
        show_rc = False
        rc = None
    name = data.get("app")
    app_id = name.strip() if isinstance(name, str) and name.strip() else app.strip()
    return BuildInfo(
        app=app_id,
        version=version,
        rc=rc,
        build=_build_number(data, source),
        codename=_codename(data.get("codename")),
        show_rc=show_rc,
    )


def dump(info: BuildInfo) -> dict[str, object]:
    """Return the canonical five-key ``BUILD.json`` object.

    Args:
        info: Identity to write. ``show_rc`` is not a file field; a candidate
            number lives in ``rc``.

    Returns:
        A JSON-ready dict with ``app``, ``version``, ``rc``, ``build``, and
        ``codename``.
    """
    return {
        "app": info.app,
        "version": info.version,
        "rc": info.rc,
        "build": info.build,
        "codename": info.codename,
    }


def _split_version(value: object) -> tuple[str, int | None, bool]:
    if not isinstance(value, str):
        return "", None, False
    text = value.strip()
    match = _VERSION_RC.fullmatch(text)
    if match is None:
        return text, None, False
    number = match.group(2)
    showed = "-rc" in text.lower()
    return match.group(1), int(number) if number is not None else None, showed


def _rc_token(value: object) -> tuple[int | None, bool]:
    if not isinstance(value, str):
        return None, False
    text = value.strip()
    if not text:
        return None, False
    if text.lower() == "rc":
        return None, True
    match = _RC_NUMBER.fullmatch(text)
    if match is None:
        return None, False
    return int(match.group(1)), True


def _codename(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].strip()
    return text


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _build_number(data: Mapping[str, object], env: Mapping[str, str]) -> int | None:
    override = str(env.get("VOXPRINT_BUILD", "")).strip()
    if override.isdigit():
        return int(override)
    run = str(env.get("GITHUB_RUN_NUMBER", "")).strip()
    if run.isdigit():
        offset = _as_int(data.get("offset")) or 0
        return int(run) + offset
    recorded = _as_int(data.get("build")) if "build" in data else None
    if recorded is None and "number" in data:
        recorded = _as_int(data.get("number"))
    return recorded


def _collect(
    path: Path | str | None,
    package_dir: Path | str | None,
) -> list[tuple[Path | None, Path | None]]:
    if path is not None:
        return [_pair(Path(path))]
    found: list[tuple[Path | None, Path | None]] = []
    for folder in _search_folders(package_dir):
        if not folder.is_dir():
            continue
        catalog, frozen = _pair(folder)
        if catalog is not None or frozen is not None:
            found.append((catalog, frozen))
    return found


def _search_folders(package_dir: Path | str | None) -> list[Path]:
    folders: list[Path] = []
    if package_dir is not None:
        folders.append(Path(package_dir))
    folders.extend(_frozen_dirs())
    start = Path(package_dir) if package_dir is not None else Path.cwd()
    root = _repo_root(start)
    if root is not None and all(root != folder for folder in folders):
        folders.append(root)
    return folders


def _frozen_dirs() -> list[Path]:
    folders: list[Path] = []
    if getattr(sys, "frozen", False):
        folders.append(Path(sys.executable).resolve().parent)
    bundle = getattr(sys, "_MEIPASS", None)
    if isinstance(bundle, str) and bundle:
        folder = Path(bundle)
        if all(folder != item for item in folders):
            folders.append(folder)
    return folders


def _repo_root(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "BUILD.json").is_file() and (
            (candidate / "pyproject.toml").is_file() or (candidate / ".git").exists()
        ):
            return candidate
    return None


def _pair(target: Path) -> tuple[Path | None, Path | None]:
    if target.name in {"BUILD.json", "build_info.json"} and not target.is_dir():
        if not target.is_file():
            raise FileNotFoundError(target)
        folder = target.parent
        catalog = folder / "BUILD.json"
        frozen = folder / "build_info.json"
        if target.name == "BUILD.json":
            return catalog, frozen if frozen.is_file() else None
        return catalog if catalog.is_file() else None, frozen
    if not target.is_dir():
        raise FileNotFoundError(target)
    catalog = target / "BUILD.json"
    frozen = target / "build_info.json"
    return catalog if catalog.is_file() else None, frozen if frozen.is_file() else None


def _read_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _merge(
    catalog: dict[str, object] | None,
    frozen: dict[str, object] | None,
) -> dict[str, object]:
    data = dict(catalog or {})
    if not frozen:
        return data
    for key, value in frozen.items():
        if value is None:
            continue
        data[key] = value
    if catalog is not None and "offset" in catalog:
        data["offset"] = catalog["offset"]
    frozen_name = frozen.get("codename")
    catalog_name = None if catalog is None else catalog.get("codename")
    frozen_blank = not isinstance(frozen_name, str) or not frozen_name.strip()
    if frozen_blank and isinstance(catalog_name, str) and catalog_name.strip():
        data["codename"] = catalog_name
    return data
