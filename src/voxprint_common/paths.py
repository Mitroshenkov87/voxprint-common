"""Suite directories shared by every Voxprint app.

The suite root is ``%LOCALAPPDATA%\\VOXPrint AI Suite`` on Windows and
``~/.local/share/voxprint-ai-suite`` on Linux. ``VOXPRINT_HOME`` overrides
that root and is never moved. The first call to :func:`home` moves an existing
``%LOCALAPPDATA%\\Voxprint`` or ``~/.local/share/voxprint`` directory.
:func:`home` does not create an empty directory. :func:`read_home` reads the
new directory, or the legacy directory through version 0.3, and does not move it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from voxprint_common import __version__
from voxprint_common.home_migration import HomeMigrationError
from voxprint_common.home_migration import migrate as _migrate_home

__all__ = [
    "HomeMigrationError",
    "cache_dir",
    "final_home",
    "home",
    "legacy_home",
    "logs_dir",
    "manifest_file",
    "models_dir",
    "projects_dir",
    "read_home",
    "runtime_dir",
    "shared_dir",
    "state_dir",
    "suite_settings_file",
    "trt_cache_dir",
    "voices_dir",
]

_WIN_FINAL = "VOXPrint AI Suite"
_WIN_LEGACY = "Voxprint"
_POSIX_FINAL = "voxprint-ai-suite"
_POSIX_LEGACY = "voxprint"
# Readers may open the legacy directory through the 0.3 line.
_LEGACY_READ_THROUGH = (0, 3)


def home() -> Path:
    """Return the suite data directory, moving a legacy directory on first use.

    ``VOXPRINT_HOME`` wins when it is set to a non-empty value. That directory
    is not moved. Otherwise the directory is :func:`final_home`. When the
    legacy directory exists and the new one does not, this moves it. An empty
    suite directory is not created.

    Returns:
        The suite root.

    Raises:
        HomeMigrationError: A legacy directory was found and the move did not
            finish. The previous directory is still in place.
    """
    override = _override()
    if override is not None:
        return override
    destination = final_home()
    _migrate_home(legacy_home(), destination)
    return destination


def read_home() -> Path:
    """Return a suite directory for reading, without moving or creating it.

    ``VOXPRINT_HOME`` wins. Otherwise an existing :func:`final_home` wins.
    Through version 0.3, an existing :func:`legacy_home` is used when the new
    directory is absent. The next minor version returns the new path only.

    Returns:
        The directory a reader should open. It may not exist.
    """
    override = _override()
    if override is not None:
        return override
    destination = final_home()
    if destination.is_dir() and not destination.is_symlink():
        return destination
    source = legacy_home()
    if _legacy_fallback_allowed() and source.is_dir() and not source.is_symlink():
        return source
    return destination


def final_home() -> Path:
    """Return the platform suite directory, ignoring ``VOXPRINT_HOME``.

    Windows uses ``%LOCALAPPDATA%\\VOXPrint AI Suite``. Elsewhere the directory
    is ``$XDG_DATA_HOME/voxprint-ai-suite`` when ``XDG_DATA_HOME`` is absolute,
    and ``~/.local/share/voxprint-ai-suite`` when it is unset or relative.
    Nothing is created or moved.

    Returns:
        The final suite root.
    """
    name = _WIN_FINAL if _windows() else _POSIX_FINAL
    return _data_base() / name


def legacy_home() -> Path:
    """Return the previous suite directory, ignoring ``VOXPRINT_HOME``.

    Windows uses ``%LOCALAPPDATA%\\Voxprint``. Elsewhere the directory is
    ``$XDG_DATA_HOME/voxprint`` when ``XDG_DATA_HOME`` is absolute, and
    ``~/.local/share/voxprint`` when it is unset or relative. Nothing is
    created or moved.

    Returns:
        The legacy suite root.
    """
    name = _WIN_LEGACY if _windows() else _POSIX_LEGACY
    return _data_base() / name


def _override() -> Path | None:
    env = os.environ.get("VOXPRINT_HOME", "").strip()
    if not env:
        return None
    return Path(env)


def _data_base() -> Path:
    if _windows():
        local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(local)
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg and os.path.isabs(xdg):
        return Path(xdg)
    return Path.home() / ".local" / "share"


def _windows() -> bool:
    return sys.platform == "win32"


def _legacy_fallback_allowed() -> bool:
    return _version_pair(__version__) <= _LEGACY_READ_THROUGH


def _version_pair(text: str) -> tuple[int, int]:
    parts = text.split(".")
    if len(parts) < 2:
        return (0, 0)
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return (0, 0)


def shared_dir() -> Path:
    """Return ``<home>/shared``, where the runtime and ``manifest.json`` live."""
    return home() / "shared"


def runtime_dir() -> Path:
    """Return the shared runtime root.

    The torch and Python runtimes live under :func:`shared_dir`. That directory
    is a protected root: a component must not claim it.
    """
    return shared_dir()


def voices_dir() -> Path:
    """Return ``<home>/voices``. The directory is not created."""
    return home() / "voices"


def projects_dir() -> Path:
    """Return ``<home>/projects``. The directory is not created."""
    return home() / "projects"


def cache_dir() -> Path:
    """Return ``<home>/cache``, the suite cache root.

    The TensorRT cache is :func:`trt_cache_dir`, ``<home>/trt-cache``.
    """
    return home() / "cache"


def state_dir() -> Path:
    """Return ``<home>/state``, where ``suite.json`` lives."""
    return home() / "state"


def logs_dir() -> Path:
    """Return ``<home>/logs``."""
    return home() / "logs"


def suite_settings_file() -> Path:
    """Return the path of the shared ``suite.json``."""
    return state_dir() / "suite.json"


def manifest_file() -> Path:
    """Return the path of ``shared/manifest.json``."""
    return shared_dir() / "manifest.json"


def trt_cache_dir() -> Path:
    """Return the TensorRT engine cache root, ``<home>/trt-cache``.

    ``VOXPRINT_TRT_CACHE`` wins. Otherwise the cache lives under :func:`home`,
    so it never recreates the old ``%LOCALAPPDATA%\\Voxprint`` folder. On
    Windows the old ``Voxprint\\trt-cache`` moves with the home migration to
    this same path. Elsewhere the old ``~/.cache/voxprint/trt-cache`` is left
    alone and engines are rebuilt from ONNX. The cache is not refcounted.
    """
    env = os.environ.get("VOXPRINT_TRT_CACHE", "").strip()
    if env:
        return Path(env)
    return home() / "trt-cache"


def models_dir() -> Path:
    """Return the shared weights directory.

    The first absolute path wins:

    1. ``VOXPRINT_MODELS_DIR``
    2. ``models_dir`` in ``suite.json``
    3. The legacy one-line file ``state/models_dir.txt``
    4. ``<home>/models``

    Relative paths are ignored. A missing or damaged settings file is skipped.
    """
    chosen = _absolute_env("VOXPRINT_MODELS_DIR")
    if chosen is not None:
        return chosen
    chosen = _suite_models_dir()
    if chosen is not None:
        return chosen
    chosen = _legacy_models_dir()
    if chosen is not None:
        return chosen
    return home() / "models"


def _absolute_env(name: str) -> Path | None:
    return _absolute(os.environ.get(name, ""))


def _absolute(text: str) -> Path | None:
    raw = (text or "").strip().strip('"')
    if not raw:
        return None
    folder = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not folder.is_absolute():
        return None
    return folder


def _suite_models_dir() -> Path | None:
    try:
        payload = json.loads(suite_settings_file().read_text(encoding="utf-8-sig"))
    except OSError, UnicodeError, ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("models_dir")
    if not isinstance(value, str):
        return None
    return _absolute(value)


def _legacy_models_dir() -> Path | None:
    """Read ``state/models_dir.txt`` written by older installers."""
    try:
        lines = (state_dir() / "models_dir.txt").read_text(encoding="utf-8-sig").splitlines()
    except OSError, UnicodeError:
        return None
    for line in lines:
        if line.strip():
            return _absolute(line)
    return None
