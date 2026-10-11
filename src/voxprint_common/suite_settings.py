"""Shared ``suite.json`` (schema 1).

The file is ``<home>/state/suite.json``. Five values are shared. Every other
key is preserved, so one app does not wipe another's settings.

* ``ui_language``: a language tag such as ``en`` or ``pt-BR``
* ``theme``: ``glass-dark``
* ``models_dir``: an absolute path, or JSON null for the default folder
* ``gpu``: ``auto`` or ``cuda:N``
* ``keep_awake_during_jobs``: true or false. True asks long jobs to block sleep.

``VOXPRINT_MODELS_DIR`` is applied by :func:`voxprint_common.paths.models_dir`,
not by the getters here.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from voxprint_common.atomic import exclusive_lock, write_text
from voxprint_common.paths import suite_settings_file

__all__ = [
    "DEFAULT_GPU",
    "DEFAULT_THEME",
    "KEYS",
    "SCHEMA",
    "file",
    "gpu",
    "keep_awake_during_jobs",
    "models_dir",
    "normalize_gpu",
    "read",
    "set_gpu",
    "set_keep_awake_during_jobs",
    "set_models_dir",
    "set_theme",
    "set_ui_language",
    "set_value",
    "theme",
    "ui_language",
    "values",
]

SCHEMA = 1
KEYS = ("ui_language", "theme", "models_dir", "gpu", "keep_awake_during_jobs")
DEFAULT_THEME = "glass-dark"
THEMES = frozenset({DEFAULT_THEME})
DEFAULT_GPU = "auto"
_GPU_RE = re.compile(r"^cuda:(\d{1,2})$")
_LANG_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
_LOCK_TIMEOUT = 30.0


def file() -> Path:
    """Return the path of ``suite.json``. The file is not created."""
    return suite_settings_file()


def read() -> dict[str, Any]:
    """Return the suite file as a dict.

    A missing file, a non-object, or unreadable text yields an empty dict.
    A leading BOM is accepted.

    Returns:
        The raw object. Keys this package does not interpret are included.
    """
    try:
        payload = json.loads(file().read_text(encoding="utf-8-sig"))
    except OSError, UnicodeError, ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def ui_language() -> str:
    """Return the shared interface language, or the default from ``LANG``.

    An invalid stored value falls back to the default. The file is unchanged.
    """
    stored = normalize_language(read().get("ui_language"))
    return stored if stored is not None else default_language()


def theme() -> str:
    """Return ``glass-dark``.

    A theme id this package does not know stays in the file and is read as
    :data:`DEFAULT_THEME`.
    """
    return normalize_theme(read().get("theme"))


def models_dir() -> Path | None:
    """Return the absolute models folder stored in the file.

    Returns:
        The path, or None when the key is missing, null, empty, or relative.
        ``VOXPRINT_MODELS_DIR`` is not applied.
    """
    return normalize_models_dir(read().get("models_dir"))


def gpu() -> str:
    """Return the shared GPU preference, or ``auto``."""
    return normalize_gpu(read().get("gpu")) or DEFAULT_GPU


def keep_awake_during_jobs() -> bool:
    """Return whether long jobs should block system sleep.

    A missing or invalid stored value is true. The file is not created.
    :func:`voxprint_common.power.keep_awake` honours this value.
    """
    stored = read().get("keep_awake_during_jobs")
    if isinstance(stored, bool):
        return stored
    return True


def values() -> dict[str, Any]:
    """Return the shared values with defaults applied.

    ``models_dir`` is a string, or None for the default folder.
    """
    folder = models_dir()
    return {
        "ui_language": ui_language(),
        "theme": theme(),
        "models_dir": str(folder) if folder else None,
        "gpu": gpu(),
        "keep_awake_during_jobs": keep_awake_during_jobs(),
    }


def set_ui_language(value: str) -> str:
    """Store a language tag.

    Args:
        value: A tag such as ``en`` or ``pt-BR``.

    Returns:
        The stored tag.

    Raises:
        ValueError: The tag is not a short language code.
    """
    return str(set_value("ui_language", value))


def set_theme(value: str) -> str:
    """Store a theme id. Unknown ids are kept for the app that owns them.

    Args:
        value: Non-empty theme id.

    Returns:
        The stored id.
    """
    return str(set_value("theme", value))


def set_models_dir(folder: Path | str | None) -> None:
    """Store an absolute models folder, or None for the default.

    Args:
        folder: Absolute path, or None.

    Raises:
        ValueError: The path is relative or empty.
    """
    set_value("models_dir", None if folder is None else str(folder))


def set_keep_awake_during_jobs(enabled: bool) -> bool:
    """Store whether long jobs should block system sleep.

    Args:
        enabled: True blocks sleep while :func:`voxprint_common.power.keep_awake`
            is held. False makes that helper leave sleep alone.

    Returns:
        The stored value.

    Raises:
        ValueError: ``enabled`` is not a bool.
    """
    return bool(set_value("keep_awake_during_jobs", enabled))


def set_gpu(value: str) -> str:
    """Store ``auto`` or ``cuda:N``.

    Args:
        value: ``auto``, ``cuda``, ``gpu``, or ``cuda:N``. ``cpu`` is rejected.

    Returns:
        The stored value.

    Raises:
        ValueError: The value is not a GPU choice.
    """
    return str(set_value("gpu", value))


def set_value(key: str, value: object) -> object:
    """Validate and store one shared value. Other keys stay as they are.

    Args:
        key: One of :data:`KEYS`.
        value: The new value.

    Returns:
        The value that was written.

    Raises:
        ValueError: ``key`` is unknown or ``value`` is invalid.
    """
    stored = _validate(key, value)
    target = file()
    with exclusive_lock(target.with_name(f"{target.name}.lock"), timeout=_LOCK_TIMEOUT):
        data = read()
        if data.get("schema") == SCHEMA and data.get(key, object()) == stored:
            return stored
        data[key] = stored
        write_text(target, json.dumps(_ordered(data), indent=2, ensure_ascii=False) + "\n")
    return stored


def default_language() -> str:
    """Return the language implied by ``LANG``, or ``en``."""
    raw = os.environ.get("LANG", "")
    code = raw.split(".")[0].replace("_", "-")
    normalized = normalize_language(code)
    return normalized if normalized is not None else "en"


def normalize_language(value: object) -> str | None:
    """Return a language tag, or None when ``value`` is not one.

    Args:
        value: Candidate tag.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().replace("_", "-")
    if _LANG_RE.fullmatch(text) is None:
        return None
    primary, _, region = text.partition("-")
    if not region:
        return primary.lower()
    return f"{primary.lower()}-{region.upper()}"


def normalize_theme(value: object) -> str:
    """Return a known theme id, or :data:`DEFAULT_THEME`.

    Args:
        value: Stored theme id.
    """
    if isinstance(value, str) and value in THEMES:
        return value
    return DEFAULT_THEME


def normalize_gpu(value: object) -> str | None:
    """Return ``auto`` or ``cuda:N``, or None for anything else including ``cpu``.

    Args:
        value: Stored GPU choice. A dict with ``device`` or ``gpu`` is accepted.
    """
    if isinstance(value, dict):
        value = value.get("device", value.get("gpu"))
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text == "auto":
        return text
    if text in ("cuda", "gpu"):
        return "cuda:0"
    match = _GPU_RE.fullmatch(text)
    if match is None:
        return None
    return f"cuda:{int(match.group(1))}"


def normalize_models_dir(value: object) -> Path | None:
    """Return an absolute path, or None for null, empty, or relative text.

    Args:
        value: Stored models directory.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    folder = Path(os.path.expandvars(os.path.expanduser(value.strip().strip('"'))))
    if not folder.is_absolute():
        return None
    return folder


def _validate(key: str, value: object) -> object:
    if key == "ui_language":
        stored = normalize_language(value)
        if stored is None:
            raise ValueError(f"unsupported UI language {value!r}")
        return stored
    if key == "theme":
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"invalid theme {value!r}")
        return value.strip()
    if key == "models_dir":
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        folder = normalize_models_dir(str(value))
        if folder is None:
            raise ValueError(f"models_dir must be an absolute path or null, got {value!r}")
        return str(folder)
    if key == "gpu":
        stored = normalize_gpu(value)
        if stored is None:
            raise ValueError(f"gpu must be auto or cuda:N, got {value!r}")
        return stored
    if key == "keep_awake_during_jobs":
        if not isinstance(value, bool):
            raise ValueError(f"keep_awake_during_jobs must be true or false, got {value!r}")
        return value
    raise ValueError(f"not a shared setting: {key!r}")


def _ordered(data: dict[str, Any]) -> dict[str, Any]:
    ordered: dict[str, Any] = {"schema": SCHEMA}
    for key, value in data.items():
        if key == "schema":
            continue
        ordered[key] = value
    return ordered
