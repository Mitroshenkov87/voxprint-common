"""Subprocess helper that adds one manifest ref.

Pytest must not collect this module. The parent passes the manifest path.
"""

from __future__ import annotations

import sys
from pathlib import Path

from voxprint_common.runtime_manifest import add_ref


def main(argv: list[str]) -> int:
    """Add one component and return 0."""
    manifest, component_id, app, path, digest = argv[1:6]
    add_ref(
        component_id,
        app,
        kind="runtime",
        version="1",
        path=path,
        sha256=digest,
        manifest=Path(manifest),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
