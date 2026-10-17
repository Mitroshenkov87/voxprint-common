"""Move an existing suite directory to its final path without deleting it.

``migrate`` is the only entry point. A same-volume move is a rename. A
cross-volume move copies into a sibling staging directory, checks the copy,
writes a marker, and only then publishes it. The previous directory is renamed
to ``<name>.migrated-<date>`` and kept. Nothing in this module deletes a
directory it did not create as an unfinished staging copy.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from voxprint_common.atomic import exclusive_lock, write_json
from voxprint_common.hashing import sha256_file

__all__ = ["HomeMigrationError", "migrate"]

logger = logging.getLogger(__name__)

_MARKER = ".voxprint-home-migration.json"
_LOCK_TIMEOUT = 3600.0
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_settled: set[str] = set()
_rewritten: set[str] = set()
_warned: set[str] = set()


class HomeMigrationError(OSError):
    """The suite directory could not be moved. The previous directory is unchanged."""


def migrate(source: Path, destination: Path) -> None:
    """Move ``source`` to ``destination`` once.

    A missing ``source`` is left alone, and an empty ``destination`` is not
    created. When both directories exist and ``destination`` was not published
    by this move, ``destination`` is used and ``source`` is not changed. A
    same-volume move renames ``source``. Otherwise the tree is copied, the file
    count and sizes are compared, manifests and state files are hashed, a
    marker is written, and ``source`` is renamed to ``<source>.migrated-<date>``.

    Args:
        source: Legacy suite directory.
        destination: Final suite directory. It must not be ``source`` or nested
            inside it.

    Raises:
        HomeMigrationError: The copy does not match, or a staging directory is
            in the way and is not an unfinished copy from this move.
        LockTimeout: Another process held the migration lock until the timeout.
    """
    if _overlaps(source, destination):
        _warn_once(
            _path_key(destination) + " overlap",
            "suite home %s and legacy %s overlap; leaving both untouched",
            destination,
            source,
        )
        return
    key = _path_key(destination)
    if key in _settled and not _present(source):
        return
    if not _present(source) and not _present(destination):
        return
    if _real_dir(source) and _real_dir(destination) and not _marker_claims(destination, source):
        _warn_both(source, destination)
        return
    if not _present(source) and _real_dir(destination):
        _rewrite_once(destination, source)
        _settled.add(key)
        return
    with exclusive_lock(_lock_path(destination), timeout=_LOCK_TIMEOUT):
        _locked(source, destination)
    if not _present(source):
        _settled.add(key)


def _locked(source: Path, destination: Path) -> None:
    if _overlaps(source, destination):
        return
    if _present(destination) and not _real_dir(destination):
        raise HomeMigrationError(f"{destination} is not a directory")
    if _present(source) and not _real_dir(source):
        logger.warning("legacy suite home %s is not a directory; leaving it", source)
        return
    if _real_dir(destination) and _real_dir(source):
        if _marker_claims(destination, source):
            retired = _retire(source)
            _clear_partial(destination)
            _rewrite_once(destination, source)
            logger.info(
                "finished suite home move to %s and kept the previous directory at %s",
                destination,
                retired,
            )
            return
        _warn_both(source, destination)
        return
    if _real_dir(destination):
        _rewrite_once(destination, source)
        return
    if not _real_dir(source):
        return
    if _same_volume(source, destination.parent):
        try:
            os.rename(source, destination)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            _copy_publish(source, destination)
        else:
            _fsync_dir(destination.parent)
            logger.info("moved suite home from %s to %s", source, destination)
    else:
        _copy_publish(source, destination)
    _rewrite_once(destination, source)


def _copy_publish(source: Path, destination: Path) -> None:
    staging = _staging_dir(destination)
    if (staging / _MARKER).is_file():
        _resume(source, destination, staging)
        return
    _discard_incomplete(source, destination)
    staging.mkdir()
    write_json(_partial_path(destination), {"schema": 1, "partial": True})
    _copy_tree(source, staging)
    count = _verify(source, staging)
    _write_marker(staging, source, destination, count)
    _publish(staging, destination)
    retired = _retire(source)
    _clear_partial(destination)
    logger.info(
        "copied suite home from %s to %s and kept the previous directory at %s",
        source,
        destination,
        retired,
    )


def _resume(source: Path, destination: Path, staging: Path) -> None:
    marker = _read_marker(staging / _MARKER)
    count = _verify(source, staging)
    if marker is None or not _claims(marker, source, destination) or marker.get("files") != count:
        raise HomeMigrationError(
            f"{staging} has an unfinished suite home move; {source} was left in place"
        )
    _publish(staging, destination)
    retired = _retire(source)
    _clear_partial(destination)
    logger.info(
        "published a checked suite home copy at %s and kept the previous directory at %s",
        destination,
        retired,
    )


def _discard_incomplete(source: Path, destination: Path) -> None:
    """Remove a staging directory this process created and did not finish.

    The marker is written only after the copy matches. A staging directory
    without that marker still has the partial sentinel, or it is empty. The
    legacy directory remains the only complete copy, so the unfinished staging
    directory can be removed and the copy started again. Any other directory
    is left where it is.
    """
    staging = _staging_dir(destination)
    if not _present(staging):
        return
    if staging.is_symlink():
        raise HomeMigrationError(f"{staging} is a link and was left untouched")
    if (staging / _MARKER).is_file():
        return
    partial = _partial_path(destination)
    if partial.is_file() or _empty_dir(staging):
        _remove_staging(staging, source, destination)
        _clear_partial(destination)
        return
    raise HomeMigrationError(f"{staging} is in the way of a suite home move and was left untouched")


def _remove_staging(staging: Path, source: Path, destination: Path) -> None:
    if staging.name != f".{destination.name}.migrating":
        raise HomeMigrationError(f"refusing to remove {staging}")
    if _path_key(staging) in {_path_key(source), _path_key(destination)}:
        raise HomeMigrationError(f"refusing to remove {staging}")
    shutil.rmtree(staging)


def _copy_tree(source: Path, staging: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        current = Path(dirpath)
        relative = current.relative_to(source)
        target = staging / relative
        target.mkdir(parents=True, exist_ok=True)
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            if child.is_symlink():
                os.symlink(os.readlink(child), target / name)
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            child = current / name
            dest = target / name
            if child.is_symlink():
                os.symlink(os.readlink(child), dest)
            elif child.is_file():
                shutil.copy2(child, dest)
            else:
                raise HomeMigrationError(f"cannot copy {child}")


def _verify(source: Path, staging: Path) -> int:
    left = _entries(source, ignore=set())
    right = _entries(staging, ignore={_MARKER})
    if set(left) != set(right):
        raise HomeMigrationError("copied suite home does not match the original file list")
    for rel, src in left.items():
        dst = right[rel]
        if src.is_symlink() or dst.is_symlink():
            if not (src.is_symlink() and dst.is_symlink()):
                raise HomeMigrationError(f"copied suite home changed the kind of {rel}")
            if os.readlink(src) != os.readlink(dst):
                raise HomeMigrationError(f"copied suite home changed the link {rel}")
            continue
        if not src.is_file() or not dst.is_file():
            raise HomeMigrationError(f"copied suite home changed the kind of {rel}")
        if src.stat().st_size != dst.stat().st_size:
            raise HomeMigrationError(f"copied suite home changed the size of {rel}")
        if _needs_hash(rel) and sha256_file(src) != sha256_file(dst):
            raise HomeMigrationError(f"copied suite home changed the contents of {rel}")
    return len(left)


def _entries(root: Path, *, ignore: set[str]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            if not child.is_symlink():
                kept.append(name)
                continue
            rel = child.relative_to(root).as_posix()
            if rel not in ignore:
                found[rel] = child
        dirnames[:] = kept
        for name in filenames:
            child = current / name
            rel = child.relative_to(root).as_posix()
            if rel in ignore:
                continue
            found[rel] = child
    return found


def _needs_hash(relative: str) -> bool:
    name = relative.rsplit("/", 1)[-1]
    return (
        name == "manifest.json" or relative == "models/.users.json" or relative.startswith("state/")
    )


def _write_marker(staging: Path, source: Path, destination: Path, count: int) -> None:
    write_json(
        staging / _MARKER,
        {
            "schema": 1,
            "from": str(source),
            "to": str(destination),
            "files": count,
            "migrated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )


def _read_marker(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        return None
    origin = payload.get("from")
    target = payload.get("to")
    files = payload.get("files")
    if not isinstance(origin, str) or not origin or not isinstance(target, str) or not target:
        return None
    if isinstance(files, bool) or not isinstance(files, int) or files < 0:
        return None
    return payload


def _marker_claims(directory: Path, source: Path) -> bool:
    marker = _read_marker(directory / _MARKER)
    return marker is not None and _claims(marker, source, directory)


def _claims(marker: dict[str, Any], source: Path, destination: Path) -> bool:
    origin = marker.get("from")
    target = marker.get("to")
    if not isinstance(origin, str) or not isinstance(target, str):
        return False
    return _path_key(origin) == _path_key(source) and _path_key(target) == _path_key(destination)


def _publish(staging: Path, destination: Path) -> None:
    if _present(destination):
        raise HomeMigrationError(
            f"{destination} appeared during the suite home move; {staging} was left in place"
        )
    os.rename(staging, destination)
    _fsync_dir(destination.parent)


def _retire(source: Path) -> Path:
    if not _present(source):
        return source
    stamp = datetime.now().astimezone().strftime("%Y%m%d")
    candidate = Path(f"{source}.migrated-{stamp}")
    suffix = 2
    while _present(candidate):
        candidate = Path(f"{source}.migrated-{stamp}-{suffix}")
        suffix += 1
        if suffix > 1000:
            raise HomeMigrationError(f"could not keep a retired name for {source}")
    os.rename(source, candidate)
    _fsync_dir(candidate.parent)
    return candidate


def _rewrite_once(destination: Path, legacy: Path) -> None:
    key = _path_key(destination)
    if key in _rewritten:
        return
    _rewrite_manifest(destination, legacy)
    _rewritten.add(key)


def _rewrite_manifest(destination: Path, legacy: Path) -> None:
    path = destination / "shared" / "manifest.json"
    if not path.is_file():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError, UnicodeError, ValueError:
        logger.warning("suite manifest %s was not rewritten", path)
        return
    rewritten, changed = _rewrite_tree(payload, legacy, destination)
    if changed and isinstance(rewritten, dict):
        write_json(path, rewritten)


def _rewrite_tree(value: Any, legacy: Path, destination: Path) -> tuple[Any, bool]:
    if isinstance(value, str):
        updated = _rewrite_absolute(value, legacy, destination)
        return updated, updated != value
    if isinstance(value, list):
        changed = False
        items: list[Any] = []
        for item in value:
            updated, flag = _rewrite_tree(item, legacy, destination)
            items.append(updated)
            changed = changed or flag
        return items, changed
    if isinstance(value, dict):
        changed = False
        body: dict[Any, Any] = {}
        for key, item in value.items():
            updated, flag = _rewrite_tree(item, legacy, destination)
            body[key] = updated
            changed = changed or flag
        return body, changed
    return value, False


def _rewrite_absolute(text: str, legacy: Path, destination: Path) -> str:
    if not _absolute_text(text):
        return text
    raw = text.replace("\\", "/")
    old = os.path.normcase(str(legacy).replace("\\", "/")).rstrip("/")
    current = os.path.normcase(raw).rstrip("/")
    if current != old and not os.path.normcase(raw).startswith(old + "/"):
        return text
    rest = raw[len(old) :]
    slash = "\\" if "\\" in text and "/" not in text else "/"
    root = str(destination).replace("\\", "/")
    if slash == "\\":
        root = str(destination).replace("/", "\\")
        rest = rest.replace("/", "\\")
    return root.rstrip("/\\") + rest


def _absolute_text(text: str) -> bool:
    if text.startswith("\\\\") or text.startswith("//"):
        return True
    if _DRIVE.match(text):
        return True
    return Path(text).is_absolute()


def _same_volume(source: Path, destination_parent: Path) -> bool:
    try:
        return os.stat(source).st_dev == os.stat(destination_parent).st_dev
    except OSError:
        return False


def _warn_both(source: Path, destination: Path) -> None:
    _warn_once(
        _path_key(destination),
        "suite home %s and legacy directory %s both exist; using %s and leaving %s untouched",
        destination,
        source,
        destination,
        source,
    )


def _warn_once(key: str, message: str, *args: object) -> None:
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(message, *args)


def _staging_dir(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.migrating")


def _partial_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.migrating.partial")


def _lock_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.migrate.lock")


def _clear_partial(destination: Path) -> None:
    partial = _partial_path(destination)
    if partial.is_file():
        partial.unlink()


def _empty_dir(path: Path) -> bool:
    try:
        return path.is_dir() and not any(path.iterdir())
    except OSError:
        return False


def _real_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _present(path: Path) -> bool:
    try:
        path.lstat()
    except OSError:
        return False
    return True


def _overlaps(left: Path, right: Path) -> bool:
    a = _path_key(left)
    b = _path_key(right)
    return a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep)


def _path_key(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(value)))


def _fsync_dir(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
