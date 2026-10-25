"""Shared ``suite.json`` (schema 1).

The file is ``<home>/state/suite.json``. The keys below are shared. Every other
key is preserved, so one app does not wipe another's settings.

* ``ui_language``: a language tag such as ``en`` or ``pt-BR``
* ``theme``: ``glass-dark``
* ``models_dir``: an absolute path, or JSON null for the default folder
* ``gpu``: ``auto`` or ``cuda:N``
* ``keep_awake_during_jobs``: true or false. True asks long jobs to block sleep.
* ``mdl_parallel_files``: how many model files the downloader fetches at once.
  Missing or invalid values mean 2. The accepted range is 1 through 8.
* ``keymap``: schema 1 overrides for the shared command chords. Bad entries
  are dropped by :mod:`voxprint_common.ui.keymap`. Other keys are preserved.
* ``ui.expert_mode``: true or false. False, the default, is simple mode.
  :class:`voxprint_common.ui.expert.ExpertMode` notifies listeners.
* ``ui.theme``: ``dark``, ``light``, or ``system``. Missing means dark.
  This is separate from ``theme``, which stays ``glass-dark``.
* ``ui.geometry``: ``x``, ``y``, ``w``, ``h``, ``maximized``, and ``fullscreen``
  for the last window. An older ``ui.window`` rectangle is copied here on read.
  Missing means the app uses its first-launch size.
* ``memory.ram_mode``: ``auto``, ``ram32``, ``ram64``, or ``ram96``. ``auto``,
  the default, follows the detected RAM tier.

Writers take ``suite.json.lock`` beside the file, read the latest object, and
replace only the keys they changed. The publish is a temporary file in the
same directory, an fsync, and ``os.replace``. A reader reloads when the file
changes. A partial or invalid file is ignored and the last good object for
that path is kept. A missing file is an empty object.

``VOXPRINT_MODELS_DIR`` is applied by :func:`voxprint_common.paths.models_dir`,
not by the getters here.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from voxprint_common.atomic import exclusive_lock, write_text
from voxprint_common.paths import suite_settings_file

__all__ = [
    "DEFAULT_GPU",
    "DEFAULT_THEME",
    "KEYS",
    "SCHEMA",
    "expert_mode",
    "file",
    "gpu",
    "keep_awake_during_jobs",
    "keymap_document",
    "mdl_parallel_files",
    "models_dir",
    "normalize_gpu",
    "ram_mode",
    "read",
    "set_expert_mode",
    "set_gpu",
    "set_keep_awake_during_jobs",
    "set_keymap",
    "set_mdl_parallel_files",
    "set_models_dir",
    "set_ram_mode",
    "set_theme",
    "set_ui_language",
    "set_ui_theme",
    "set_value",
    "set_window_geometry",
    "theme",
    "ui_geometry",
    "ui_language",
    "ui_theme",
    "values",
    "window_geometry",
]

SCHEMA = 1
KEYS = (
    "ui_language",
    "theme",
    "models_dir",
    "gpu",
    "keep_awake_during_jobs",
    "mdl_parallel_files",
    "ui.expert_mode",
    "ui.theme",
    "ui.window",
    "ui.geometry",
    "memory.ram_mode",
)
RAM_MODES = frozenset({"auto", "ram32", "ram64", "ram96"})
DEFAULT_RAM_MODE = "auto"
DEFAULT_PARALLEL_FILES = 2
DEFAULT_THEME = "glass-dark"
THEMES = frozenset({DEFAULT_THEME})
UI_THEMES = frozenset({"dark", "light", "system"})
DEFAULT_UI_THEME = "dark"
DEFAULT_GPU = "auto"
_GPU_RE = re.compile(r"^cuda:(\d{1,2})$")
_LANG_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
_LOCK_TIMEOUT = 30.0
_WINDOW_KEYS = ("width", "height", "x", "y")
_GEOMETRY_INTS = ("x", "y", "w", "h")
_good_path: str | None = None
_good_token: tuple[str, int, int, int] | None = None
_good: dict[str, Any] = {}


def file() -> Path:
    """Return the path of ``suite.json``. The file is not created."""
    return suite_settings_file()


def read() -> dict[str, Any]:
    """Return the suite file as a dict.

    A missing file yields an empty dict. A leading BOM is accepted. When the
    file's inode, size, or modification time changes, it is read again. An
    atomic replace can keep the previous timestamp and size, so the inode is
    part of that check. Unreadable text, a partial document, or a value that
    is not an object is ignored, and the last good object for this path is
    returned instead.

    Returns:
        The raw object. Keys this package does not interpret are included.
    """
    global _good, _good_path, _good_token
    path = file()
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        return {}
    token = (key, stat.st_ino, stat.st_mtime_ns, stat.st_size)
    if token == _good_token:
        return dict(_good)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError, UnicodeError, ValueError:
        return dict(_good) if _good_path == key else {}
    if not isinstance(payload, dict):
        return dict(_good) if _good_path == key else {}
    _good_path = key
    _good_token = token
    _good = payload
    return dict(payload)


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


def expert_mode() -> bool:
    """Return whether Expert mode is on.

    A missing or invalid stored value is false. The file is not created.
    """
    stored = read().get("ui.expert_mode")
    if isinstance(stored, bool):
        return stored
    return False


def set_expert_mode(enabled: bool) -> bool:
    """Store Expert mode. False is simple mode.

    Listeners live on :class:`voxprint_common.ui.expert.ExpertMode`. This
    function only writes the file.

    Args:
        enabled: True shows expert commands, panels, and settings.

    Returns:
        The stored value.

    Raises:
        ValueError: ``enabled`` is not a bool.
    """
    return bool(set_value("ui.expert_mode", enabled))


def ram_mode() -> str:
    """Return the RAM mode, or ``auto`` when the file does not set one.

    An invalid stored value is read as ``auto``. The file is not created.
    """
    stored = read().get("memory.ram_mode")
    if isinstance(stored, str) and stored in RAM_MODES:
        return stored
    return DEFAULT_RAM_MODE


def set_ram_mode(mode: str) -> str:
    """Store ``memory.ram_mode``.

    Args:
        mode: ``auto``, ``ram32``, ``ram64``, or ``ram96``.

    Returns:
        The stored mode.

    Raises:
        ValueError: ``mode`` is not one of those four.
    """
    return str(set_value("memory.ram_mode", mode))


def mdl_parallel_files() -> int:
    """Return how many model files to fetch at once.

    A missing or invalid stored value is 2. The file is not created.
    """
    stored = read().get("mdl_parallel_files")
    if isinstance(stored, bool) or not isinstance(stored, int):
        return DEFAULT_PARALLEL_FILES
    if stored < 1 or stored > 8:
        return DEFAULT_PARALLEL_FILES
    return stored


def set_mdl_parallel_files(count: int) -> int:
    """Store the model-download concurrency.

    Args:
        count: From 1 to 8 files at once.

    Returns:
        The stored count.

    Raises:
        ValueError: ``count`` is outside 1..8.
    """
    return int(set_value("mdl_parallel_files", count))


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
        "mdl_parallel_files": mdl_parallel_files(),
        "ui.expert_mode": expert_mode(),
        "memory.ram_mode": ram_mode(),
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


def ui_theme() -> str:
    """Return ``dark``, ``light``, or ``system``.

    A missing or unknown value is ``dark``. The file is unchanged. This is not
    :func:`theme`, which stays the glass theme id.
    """
    stored = read().get("ui.theme")
    if isinstance(stored, str) and stored in UI_THEMES:
        return stored
    return DEFAULT_UI_THEME


def set_ui_theme(value: str) -> str:
    """Store ``dark``, ``light``, or ``system``.

    Args:
        value: One of those three words.

    Returns:
        The stored value.

    Raises:
        ValueError: ``value`` is not one of those words.
    """
    return str(set_value("ui.theme", value))


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


def ui_geometry() -> dict[str, int | bool] | None:
    """Return the stored window geometry, or None when it has not been saved.

    The object is ``x``, ``y``, ``w``, ``h``, ``maximized``, and ``fullscreen``.
    When ``ui.geometry`` is missing and ``ui.window`` is present, that older
    rectangle is copied into ``ui.geometry`` with both flags false, and
    ``ui.window`` is removed.

    Returns:
        The geometry, or None when neither key is usable. An incomplete object
        is None.
    """
    data = read()
    stored = _normalize_geometry(data.get("ui.geometry"))
    if stored is not None:
        return stored
    legacy = _normalize_window(data.get("ui.window"))
    if legacy is None:
        return None
    migrated = {
        "x": legacy["x"],
        "y": legacy["y"],
        "w": legacy["width"],
        "h": legacy["height"],
        "maximized": False,
        "fullscreen": False,
    }
    _store_geometry(migrated)
    return migrated


def window_geometry() -> dict[str, int] | None:
    """Return the stored window rectangle, or None when it has not been saved.

    Returns:
        ``width``, ``height``, ``x``, and ``y`` taken from :func:`ui_geometry`.
        An incomplete or invalid object is None.
    """
    stored = ui_geometry()
    if stored is None:
        return None
    return {
        "width": int(stored["w"]),
        "height": int(stored["h"]),
        "x": int(stored["x"]),
        "y": int(stored["y"]),
    }


def set_window_geometry(
    width: int,
    height: int,
    x: int,
    y: int,
    *,
    maximized: bool = False,
    fullscreen: bool = False,
) -> dict[str, int]:
    """Store the window geometry. Other suite keys stay as they are.

    The file key is ``ui.geometry``. An older ``ui.window`` entry is removed.

    Args:
        width: Width in pixels, at least 1.
        height: Height in pixels, at least 1.
        x: Left edge. Negative values are kept.
        y: Top edge. Negative values are kept.
        maximized: True when the window was maximized.
        fullscreen: True when the window was fullscreen.

    Returns:
        ``width``, ``height``, ``x``, and ``y``.

    Raises:
        ValueError: A size is not a positive integer, a position is not an
            integer, or a flag is not a bool.
    """
    stored = _geometry_record(width, height, x, y, maximized=maximized, fullscreen=fullscreen)
    _store_geometry(stored)
    return {"width": width, "height": height, "x": x, "y": y}


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


def keymap_document() -> dict[str, Any]:
    """Return the raw ``keymap`` object, or an empty dict.

    A missing key or a value that is not an object yields ``{}``. The keymap
    module drops individual bad entries.
    """
    stored = read().get("keymap")
    if not isinstance(stored, dict):
        return {}
    return stored


def set_keymap(document: Mapping[str, Any]) -> None:
    """Store a schema 1 keymap document. Other suite keys stay as they are.

    Args:
        document: ``schema``, ``assigned``, and ``removed``.

    Raises:
        ValueError: ``document`` is not a schema 1 object.
    """
    if (
        document.get("schema") != SCHEMA
        or not isinstance(document.get("assigned"), list)
        or not isinstance(document.get("removed"), list)
    ):
        raise ValueError("keymap must be schema 1 with assigned and removed lists")
    target = file()
    with exclusive_lock(target.with_name(f"{target.name}.lock"), timeout=_LOCK_TIMEOUT):
        data = read()
        data["keymap"] = dict(document)
        payload = _ordered(data)
        write_text(target, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        _remember(target, payload)


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
        if key == "ui.geometry":
            data.pop("ui.window", None)
        payload = _ordered(data)
        write_text(target, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        _remember(target, payload)
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
    if key == "mdl_parallel_files":
        if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 8:
            raise ValueError(f"mdl_parallel_files must be an integer from 1 to 8, got {value!r}")
        return value
    if key == "ui.expert_mode":
        if not isinstance(value, bool):
            raise ValueError(f"ui.expert_mode must be true or false, got {value!r}")
        return value
    if key == "ui.theme":
        if not isinstance(value, str) or value not in UI_THEMES:
            raise ValueError(f"ui.theme must be dark, light, or system, got {value!r}")
        return value
    if key == "ui.window":
        stored = _normalize_window(value)
        if stored is None:
            raise ValueError(f"ui.window must be width, height, x, and y, got {value!r}")
        return stored
    if key == "ui.geometry":
        stored = _normalize_geometry(value)
        if stored is None:
            raise ValueError(
                f"ui.geometry must be x, y, w, h, maximized, and fullscreen, got {value!r}"
            )
        return stored
    if key == "memory.ram_mode":
        if not isinstance(value, str) or value not in RAM_MODES:
            raise ValueError(f"memory.ram_mode must be auto, ram32, ram64, or ram96, got {value!r}")
        return value
    raise ValueError(f"not a shared setting: {key!r}")


def _normalize_window(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    parsed: dict[str, int] = {}
    for name in _WINDOW_KEYS:
        item = value.get(name)
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        if name in {"width", "height"} and item < 1:
            return None
        parsed[name] = item
    return parsed


def _geometry_record(
    width: int,
    height: int,
    x: int,
    y: int,
    *,
    maximized: bool,
    fullscreen: bool,
) -> dict[str, int | bool]:
    record = {
        "x": x,
        "y": y,
        "w": width,
        "h": height,
        "maximized": maximized,
        "fullscreen": fullscreen,
    }
    stored = _normalize_geometry(record)
    if stored is None:
        raise ValueError("ui.geometry must be x, y, w, h, maximized, and fullscreen")
    return stored


def _normalize_geometry(value: object) -> dict[str, int | bool] | None:
    if not isinstance(value, dict):
        return None
    parsed: dict[str, int | bool] = {}
    for name in _GEOMETRY_INTS:
        item = value.get(name)
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        if name in {"w", "h"} and item < 1:
            return None
        parsed[name] = item
    for name in ("maximized", "fullscreen"):
        flag = value.get(name)
        if not isinstance(flag, bool):
            return None
        parsed[name] = flag
    return parsed


def _store_geometry(stored: dict[str, int | bool]) -> None:
    """Write ``ui.geometry`` and drop a leftover ``ui.window``."""
    target = file()
    with exclusive_lock(target.with_name(f"{target.name}.lock"), timeout=_LOCK_TIMEOUT):
        data = read()
        data["ui.geometry"] = stored
        data.pop("ui.window", None)
        payload = _ordered(data)
        write_text(target, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        _remember(target, payload)


def _remember(path: Path, data: dict[str, Any]) -> None:
    """Keep ``data`` as the last good object after this process publishes it.

    The publish is an atomic replace. On a clock that does not advance, the
    new file can share the previous size and timestamp. Recording the object
    that was just written keeps the next read in this process aligned with
    the file. The token still includes the inode, so another process reloads
    when the replace lands on a new inode.
    """
    global _good, _good_path, _good_token
    key = str(path)
    _good_path = key
    _good = dict(data)
    try:
        stat = path.stat()
    except OSError:
        _good_token = None
        return
    _good_token = (key, stat.st_ino, stat.st_mtime_ns, stat.st_size)


def _ordered(data: dict[str, Any]) -> dict[str, Any]:
    ordered: dict[str, Any] = {"schema": SCHEMA}
    for key, value in data.items():
        if key == "schema":
            continue
        ordered[key] = value
    return ordered
