"""One ``BUILD.json`` document for every Voxprint app.

The file this package writes has five keys:

* ``app``: suite id or the name drawn on the splash
* ``version``: ``X.Y.Z``
* ``rc``: release-candidate number, or JSON null for a final build
* ``build``: integer build number, or JSON null for a local dev tree
* ``codename``: short name, or an empty string

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


def load(path: Path, *, app: str = "", env: Mapping[str, str] | None = None) -> BuildInfo:
    """Read ``BUILD.json`` from a file or from a directory that contains it.

    Args:
        path: A ``BUILD.json`` file, or a directory with one at its root.
        app: Suite id used when the file has no ``app`` field.
        env: Environment for ``VOXPRINT_BUILD`` and ``GITHUB_RUN_NUMBER``.
            Defaults to ``os.environ``.

    Returns:
        The parsed identity. Older shapes are mapped onto the five fields.
    """
    target = path / "BUILD.json" if path.is_dir() else path
    payload = json.loads(target.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{target} must contain a JSON object")
    return parse(payload, app=app, env=env)


def parse(
    data: Mapping[str, object],
    *,
    app: str = "",
    env: Mapping[str, str] | None = None,
) -> BuildInfo:
    """Map a ``BUILD.json`` object, including older shapes, onto :class:`BuildInfo`.

    ``VOXPRINT_BUILD`` overrides the build number. Otherwise an integer
    ``build`` or Lucent's ``number`` is used. When those are absent, or when
    ``build`` is JSON null, ``GITHUB_RUN_NUMBER`` plus ``offset`` is used.
    A null ``build`` with no run number stays null (a dev tree). A missing
    build with no run number is ``0``.

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
    explicit = _as_int(data.get("build")) if "build" in data else None
    if explicit is None and "number" in data:
        explicit = _as_int(data.get("number"))
    if explicit is not None:
        return explicit
    null_build = "build" in data and data.get("build") is None
    run = str(env.get("GITHUB_RUN_NUMBER", "")).strip()
    if run.isdigit():
        offset = _as_int(data.get("offset")) or 0
        return int(run) + offset
    if null_build:
        return None
    return 0
