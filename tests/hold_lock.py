"""Hold an exclusive lock until a release file appears.

Pytest does not collect this module. The lock tests start it as a subprocess
so the lock is held by another process.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from voxprint_common.atomic import exclusive_lock


def main() -> None:
    """Lock ``sys.argv[1]`` until ``sys.argv[3]`` exists."""
    lock, ready, release = sys.argv[1:4]
    with exclusive_lock(Path(lock), timeout=5):
        Path(ready).write_text("held", encoding="utf-8")
        deadline = time.monotonic() + 20
        while not Path(release).exists():
            if time.monotonic() > deadline:
                raise SystemExit(2)
            time.sleep(0.02)


if __name__ == "__main__":
    main()
