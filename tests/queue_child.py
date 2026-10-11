"""Subprocess helper for the model queue.

Pytest must not collect this module. The parent test sets ``VOXPRINT_HOME``
and ``VOXPRINT_MDL_TEST_GUARD``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from voxprint_common.atomic import exclusive_lock
from voxprint_common.mdl.queue import enqueue, run_or_attach
from voxprint_common.paths import state_dir


def main(argv: list[str]) -> int:
    """Run one queue action and print the job id or exit code."""
    command = argv[1] if len(argv) > 1 else ""
    if command == "enqueue":
        app = argv[2]
        manifest = argv[3]
        models = argv[4:] or None
        paths = None if manifest == "-" else [Path(manifest)]
        print(enqueue(app, models, manifests=paths))
        return 0
    if command == "work":
        outcome = run_or_attach(
            None,
            priority=False,
            fetch_options={
                "probe_bytes": 0,
                "batch_bytes": 1_000_000,
                "backoff_base": 30,
                "backoff_max": 30,
            },
        )
        print(outcome.code)
        return int(outcome.code)
    if command == "hold":
        ready = Path(argv[2])
        release = Path(argv[3])
        lock = state_dir() / "mdl-worker.lock"
        with exclusive_lock(lock, timeout=5):
            ready.write_text("held", encoding="utf-8")
            while not release.exists():
                time.sleep(0.05)
        return 0
    print(f"unknown helper command {command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
