"""Hold one pin reservation until a stop file appears."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from voxprint_common.model_park import _publish_pin


def main() -> None:
    """Publish ``argv[1]`` bytes, touch ``argv[2]``, and wait for ``argv[3]``."""
    pinned = int(sys.argv[1])
    ready = Path(sys.argv[2])
    stop = Path(sys.argv[3])
    _publish_pin("child", pinned)
    ready.write_text("ok", encoding="utf-8")
    while not stop.exists():
        time.sleep(0.02)


if __name__ == "__main__":
    main()
