"""Post-install hook marker cleanup."""

from __future__ import annotations

import json

from voxprint_common.mdl.hooks import remove_outputs
from voxprint_common.paths import models_dir, state_dir


def test_remove_outputs_skips_a_damaged_marker_line() -> None:
    """A non-list marker line does not stop cleanup of the lines after it."""
    folder = models_dir() / "enlarge" / "upscaler"
    folder.mkdir(parents=True)
    output = folder / "out.bin"
    output.write_bytes(b"ok")
    digest = "abc123def456"
    marker = state_dir() / "mdl-hooks" / "enlarge" / "upscaler" / digest
    marker.parent.mkdir(parents=True)
    marker.write_text(
        "\n".join(
            [
                digest,
                "5",
                "{}",
                json.dumps(["out.bin", output.stat().st_size, 1]),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    remove_outputs("enlarge", f"model-{digest}")

    assert not output.exists()
    assert not marker.exists()
