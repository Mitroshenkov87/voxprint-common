"""Hold the GPU lock until a release file appears.

Pytest does not collect this module.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from voxprint_common.gpu_lock import gpu_job


def main() -> None:
    """Hold the lock at ``sys.argv[1]`` until ``sys.argv[3]`` exists."""
    lock, ready, release = sys.argv[1:4]
    with gpu_job("enlarge", "upscale", eta=120, timeout=30, path=Path(lock)):
        Path(ready).write_text("held", encoding="utf-8")
        deadline = time.monotonic() + 20
        while not Path(release).exists():
            if time.monotonic() > deadline:
                raise SystemExit(2)
            time.sleep(0.02)


if __name__ == "__main__":
    main()
