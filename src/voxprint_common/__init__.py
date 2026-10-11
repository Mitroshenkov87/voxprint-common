"""Shared library for the Voxprint AI Media Suite.

Each desktop app bundles this package, or installs it into the shared runtime.
It is not an application and it does not install, launch, or require another app.

Import a submodule directly, for example ``voxprint_common.paths``. Importing
this package does not load Qt and does not touch the network or the disk.
"""

from __future__ import annotations

__version__ = "0.1.2"
API = 1

__all__ = ["API", "__version__"]
