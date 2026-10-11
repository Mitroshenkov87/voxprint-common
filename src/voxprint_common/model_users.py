"""Per-model users file at ``<models>/.users.json``.

Readers accept the legacy shapes written by older installers: ``{key: true}``
and ``{key: ["app"]}``. Every write re-reads the file under ``.users.lock``,
changes one key, and leaves every other key byte-for-byte in meaning: unknown
keys and other apps' entries stay. The publish is a temporary file, fsync,
and rename.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from voxprint_common.atomic import exclusive_lock, write_json
from voxprint_common.paths import models_dir

__all__ = [
    "UsersError",
    "add_user",
    "blocks_delete",
    "entry_blocks",
    "load_users",
    "remove_user",
    "users_file",
    "users_lock",
]

_TIMEOUT = 30.0


class UsersError(ValueError):
    """``models/.users.json`` is not a JSON object."""


def users_file(root: Path | None = None) -> Path:
    """Return ``<models>/.users.json``. The file is not created.

    Args:
        root: Models directory. Defaults to :func:`voxprint_common.paths.models_dir`.
    """
    return (root or models_dir()) / ".users.json"


def users_lock(path: Path | None = None) -> Path:
    """Return the lock file beside ``.users.json``.

    Args:
        path: Users file. Defaults to :func:`users_file`.
    """
    return (path or users_file()).with_name(".users.lock")


def load_users(path: Path | None = None, *, timeout: float = _TIMEOUT) -> dict[str, Any]:
    """Read the users file.

    A missing file is an empty object and is not created. ``{key: true}`` and
    ``{key: ["app"]}`` are returned as stored.

    Args:
        path: Users file. Defaults to :func:`users_file`.
        timeout: Seconds to wait for the lock.

    Raises:
        UsersError: The file is not a JSON object.
    """
    target = path or users_file()
    if not target.is_file():
        return {}
    with exclusive_lock(users_lock(target), timeout=timeout):
        return _read(target)


def entry_blocks(value: object) -> bool:
    """Return whether one users-file value still names a holder.

    Args:
        value: A legacy bool, a list of apps, or an object with ``refs``.
    """
    return _blocks(value)


def blocks_delete(keys: Sequence[str], *, path: Path | None = None) -> bool:
    """Return whether any key still has a user.

    A legacy ``true`` counts as a user. An unreadable file blocks deletion.

    Args:
        keys: Model ids or folder names to consult.
        path: Users file. Defaults to :func:`users_file`.
    """
    try:
        document = load_users(path)
    except UsersError:
        return True
    return any(_blocks(document.get(key)) for key in keys)


def add_user(model_id: str, app: str, *, path: Path | None = None) -> None:
    """Record ``app`` on ``model_id`` without dropping other keys.

    Args:
        model_id: Model or component id.
        app: Suite id to add.
        path: Users file. Defaults to :func:`users_file`.
    """
    key = model_id.strip()
    name = app.strip()
    if not key or not name:
        raise UsersError("model id and app are required")
    target = path or users_file()
    with exclusive_lock(users_lock(target), timeout=_TIMEOUT):
        document = _read(target) if target.is_file() else {}
        document[key] = _with_app(document.get(key), name)
        write_json(target, document)


def remove_user(model_id: str, app: str, *, path: Path | None = None) -> None:
    """Drop ``app`` from ``model_id`` and keep every other entry.

    A legacy ``true`` is left in place. It does not name an app, so removing
    one app must not clear it.

    Args:
        model_id: Model or component id.
        app: Suite id to remove.
        path: Users file. Defaults to :func:`users_file`.
    """
    key = model_id.strip()
    name = app.strip()
    if not key or not name:
        raise UsersError("model id and app are required")
    target = path or users_file()
    if not target.is_file():
        return
    with exclusive_lock(users_lock(target), timeout=_TIMEOUT):
        document = _read(target)
        if key not in document:
            return
        document[key] = _without_app(document[key], name)
        write_json(target, document)


@contextmanager
def hold_users(path: Path | None = None, *, timeout: float = _TIMEOUT) -> Iterator[dict[str, Any]]:
    """Hold the users lock and yield the current object.

    The object is written back when the block exits, including keys the caller
    did not change. Use this when a delete must not race a new user.

    Args:
        path: Users file. Defaults to :func:`users_file`.
        timeout: Seconds to wait for the lock.
    """
    target = path or users_file()
    with exclusive_lock(users_lock(target), timeout=timeout):
        document = _read(target) if target.is_file() else {}
        yield document
        write_json(target, document)


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise UsersError(f"{path.name} could not be read") from exc
    if not isinstance(payload, dict):
        raise UsersError(f"{path.name} must be a JSON object")
    return payload


def _blocks(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return any(isinstance(item, str) and item.strip() for item in value)
    if isinstance(value, Mapping):
        if value.get("legacy") is True:
            return True
        return _blocks(value.get("refs"))
    return value is not None


def _with_app(value: object, app: str) -> object:
    if isinstance(value, list):
        names = [item for item in value if isinstance(item, str) and item.strip()]
        if app not in names:
            names.append(app)
        return names
    if isinstance(value, dict):
        body = dict(value)
        refs = body.get("refs")
        names = [item for item in refs if isinstance(item, str)] if isinstance(refs, list) else []
        if app not in names:
            names.append(app)
        body["refs"] = names
        if not isinstance(refs, list):
            body["legacy"] = True
        return body
    if value is True:
        return {"refs": [app], "legacy": True}
    return [app]


def _without_app(value: object, app: str) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return [item for item in value if item != app]
    if isinstance(value, Mapping):
        body = dict(value)
        refs = body.get("refs")
        if isinstance(refs, list):
            body["refs"] = [item for item in refs if item != app]
        return body
    return value
