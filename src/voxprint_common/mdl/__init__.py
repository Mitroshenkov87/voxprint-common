"""Shared model downloader for the Voxprint AI Media Suite.

:data:`DISPLAY_NAME` is the only spelling of the product name. The console
script is :data:`CLI_NAME`. Installers run it after copying files. Apps poll
:func:`load_status` and open their own window only when ``ready`` is true.

Importing this package does not load Qt, download anything, or create directories.
Apps enqueue work with :func:`enqueue` or :func:`ensure_ready`. They do not
download. A window subscribes to :class:`TransferSession` and reads
:class:`ProgressEvent`, which includes every queued job grouped by app.
"""

from __future__ import annotations

import re
from pathlib import Path

from voxprint_common.mdl.catalog import (
    HEADROOM,
    CatalogError,
    DiskShort,
    discover,
    install_dir,
    load_manifest,
    suite_root,
)
from voxprint_common.mdl.events import (
    ItemProgress,
    JobProgress,
    ProgressEvent,
    TransferSession,
)
from voxprint_common.mdl.job import Outcome, download, plan, progress_event, verify
from voxprint_common.mdl.names import CLI_NAME, DISPLAY_NAME
from voxprint_common.mdl.queue import Failed, Ready, enqueue, ensure_ready, run_or_attach
from voxprint_common.mdl.readiness import disk_ready
from voxprint_common.mdl.status import Status, load_status
from voxprint_common.mdl.status import ready as _status_ready

_APP_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

__all__ = [
    "CLI_NAME",
    "DISPLAY_NAME",
    "HEADROOM",
    "CatalogError",
    "DiskShort",
    "Failed",
    "ItemProgress",
    "JobProgress",
    "Outcome",
    "ProgressEvent",
    "Ready",
    "Status",
    "TransferSession",
    "discover",
    "download",
    "enqueue",
    "ensure_ready",
    "install_dir",
    "load_manifest",
    "load_status",
    "plan",
    "progress_event",
    "ready",
    "run_or_attach",
    "suite_root",
    "verify",
]


def ready(target: str | Path | None = None) -> bool:
    """Return whether the models for ``target`` are on disk.

    An app id checks every file that app's manifest requires for this tier,
    including runtime components. A path, or no argument, reads one status
    file. An app id does not trust that file.

    Args:
        target: Suite app id, or a status-file path. ``None`` reads the
            default status file.
    """
    if isinstance(target, str) and _APP_ID.fullmatch(target):
        return disk_ready(target)
    if isinstance(target, Path):
        return _status_ready(target)
    return _status_ready(None)
