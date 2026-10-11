"""Shared model downloader for the Voxprint AI Media Suite.

:data:`DISPLAY_NAME` is the only spelling of the product name. The console
script is :data:`CLI_NAME`. Installers run it after copying files. Apps poll
:func:`load_status` and open their own window only when ``ready`` is true.

Importing this package does not load Qt, download anything, or create directories.
"""

from __future__ import annotations

from voxprint_common.mdl.catalog import (
    HEADROOM,
    CatalogError,
    DiskShort,
    discover,
    install_dir,
    load_manifest,
    suite_root,
)
from voxprint_common.mdl.job import Outcome, download, plan, verify
from voxprint_common.mdl.names import CLI_NAME, DISPLAY_NAME
from voxprint_common.mdl.status import Status, load_status, ready

__all__ = [
    "CLI_NAME",
    "DISPLAY_NAME",
    "HEADROOM",
    "CatalogError",
    "DiskShort",
    "Outcome",
    "Status",
    "discover",
    "download",
    "install_dir",
    "load_manifest",
    "load_status",
    "plan",
    "ready",
    "suite_root",
    "verify",
]
