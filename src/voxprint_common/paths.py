"""Suite directories shared by every Voxprint app.

The suite root is ``%LOCALAPPDATA%\\Voxprint`` on Windows and
``~/.local/share/voxprint`` on Linux. ``VOXPRINT_HOME`` overrides that root.
Nothing in this module creates a directory.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

__all__ = [
    "home",
    "logs_dir",
    "manifest_file",
    "models_dir",
    "shared_dir",
    "state_dir",
    "suite_settings_file",
    "trt_cache_dir",
]


def home() -> Path:
    """Return the suite data directory.

    ``VOXPRINT_HOME`` wins when it is set to a non-empty value. Otherwise the
    directory is ``%LOCALAPPDATA%\\Voxprint`` on Windows and
    ``$XDG_DATA_HOME/voxprint`` elsewhere. When ``XDG_DATA_HOME`` is unset or
    relative, the Linux path is ``~/.local/share/voxprint``.

    Returns:
        The suite root. The directory is not created.
    """
    env = os.environ.get("VOXPRINT_HOME", "").strip()
    if env:
        return Path(env)
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(local) / "Voxprint"
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg and os.path.isabs(xdg) else Path.home() / ".local" / "share"
    return base / "voxprint"


def shared_dir() -> Path:
    """Return ``<home>/shared``, where the runtime and ``manifest.json`` live."""
    return home() / "shared"


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
    """Return the TensorRT engine cache root.

    ``VOXPRINT_TRT_CACHE`` wins. Otherwise the cache is
    ``%LOCALAPPDATA%\\Voxprint\\trt-cache`` on Windows and
    ``$XDG_CACHE_HOME/voxprint/trt-cache`` elsewhere (``~/.cache/voxprint/trt-cache``
    when that variable is unset). Engines are rebuilt from ONNX, so this cache
    is not refcounted and does not follow ``VOXPRINT_HOME``.
    """
    env = os.environ.get("VOXPRINT_TRT_CACHE", "").strip()
    if env:
        return Path(env)
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(local) / "Voxprint" / "trt-cache"
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg) if xdg and os.path.isabs(xdg) else Path.home() / ".cache"
    return base / "voxprint" / "trt-cache"


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
