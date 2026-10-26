"""Installed-file status and the hash cache.

The manage window decides installed, missing, or damaged from the stored size
and mtime. It does not hash. A full SHA-256 runs only for Check and repair,
and a file whose size and mtime are unchanged is not hashed again.
"""

from __future__ import annotations

import json
from pathlib import Path

from voxprint_common.atomic import write_json
from voxprint_common.paths import state_dir
from voxprint_common.runtime_manifest import Component, remember_model_stat

_STAT_NAME = "mdl-install-stat.json"
_HASH_NAME = "mdl-hash-cache.json"


def classify_file(
    path: Path | None,
    expected_size: int,
    stored: tuple[int, int] | None,
) -> str:
    """Return ``ok``, ``missing``, ``damaged``, or ``adopt``.

    ``adopt`` means the file's size matches the catalog and no size/mtime pair
    is stored yet. The caller records the current stat and treats it as
    installed. This function does not read file bytes.

    Args:
        path: Blob path. ``None`` when the model has no file.
        expected_size: Size declared by the model manifest.
        stored: ``(size, mtime_ns)`` from the install manifest, if one was saved.
    """
    if path is None or not path.is_file():
        return "missing"
    size = path.stat().st_size
    if stored is not None:
        recorded_size, recorded_mtime = stored
        if size == recorded_size and path.stat().st_mtime_ns == recorded_mtime:
            return "ok"
        return "damaged"
    if size == expected_size:
        return "adopt"
    return "damaged"


def stored_stat(path: Path, component: Component | None) -> tuple[int, int] | None:
    """Return the stored ``(size, mtime_ns)`` for ``path``.

    The component record wins. The stat file is the fallback for a blob that
    is not in the install manifest yet.

    Args:
        path: Blob path.
        component: Install-manifest row, when this model has one.
    """
    if component is not None:
        pair = _pair(component.extra.get("size"), component.extra.get("mtime_ns"))
        if pair is not None:
            return pair
    table = _read_json(_stat_path())
    raw = table.get(_key(path))
    if not isinstance(raw, dict):
        return None
    return _pair(raw.get("size"), raw.get("mtime_ns"))


def remember_stat(path: Path, component_id: str) -> tuple[int, int] | None:
    """Record ``path``'s current size and mtime without reading its bytes.

    Args:
        path: Blob to stat.
        component_id: Install-manifest id. Empty when the model has no row yet.

    Returns:
        The recorded pair, or ``None`` when the path is not a file.
    """
    if not path.is_file():
        return None
    info = path.stat()
    pair = (info.st_size, info.st_mtime_ns)
    table = _read_json(_stat_path())
    table[_key(path)] = {"size": pair[0], "mtime_ns": pair[1]}
    _write_json(_stat_path(), table)
    if component_id:
        remember_model_stat(component_id, pair[0], pair[1])
    return pair


def cached_digest(path: Path) -> str | None:
    """Return the cached SHA-256 when size and mtime still match.

    Args:
        path: File that might have been hashed before.

    Returns:
        64 lowercase hex characters, or ``None`` when the cache has no row
        for this path, size, and mtime.
    """
    if not path.is_file():
        return None
    info = path.stat()
    raw = _read_json(_hash_path()).get(_cache_key(path, info.st_size, info.st_mtime_ns))
    if isinstance(raw, str) and len(raw) == 64:
        return raw
    return None


def remember_digest(path: Path, digest: str) -> None:
    """Cache ``digest`` for ``path``'s current size and mtime.

    Args:
        path: File that was hashed.
        digest: Lowercase SHA-256 of that file.
    """
    if not path.is_file():
        return
    info = path.stat()
    table = _read_json(_hash_path())
    table[_cache_key(path, info.st_size, info.st_mtime_ns)] = digest.strip().lower()
    _write_json(_hash_path(), table)


def _pair(size: object, mtime_ns: object) -> tuple[int, int] | None:
    if isinstance(size, bool) or isinstance(mtime_ns, bool):
        return None
    if isinstance(size, int) and isinstance(mtime_ns, int):
        return size, mtime_ns
    return None


def _key(path: Path) -> str:
    return str(path.resolve())


def _cache_key(path: Path, size: int, mtime_ns: int) -> str:
    return f"{_key(path)}|{size}|{mtime_ns}"


def _stat_path() -> Path:
    return state_dir() / _STAT_NAME


def _hash_path() -> Path:
    return state_dir() / _HASH_NAME


def _read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, ValueError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)
