"""0.7.8: the MDL mark on the download and check windows.

The download and check windows used to open with no window icon and no
desktop file name. Qt runs in a child process so the in-process
"import does not load Qt" tests stay valid.
"""

from __future__ import annotations

import os
import subprocess
import sys

_CHILD = """
from PySide6.QtWidgets import QApplication, QWidget
from voxprint_common.mdl import progress_ui
from voxprint_common.mdl.status import JobState, Status

app = QApplication([])
window = QWidget()
progress_ui._brand_window(app, window)
assert app.desktopFileName() == "voxprint-mdl", app.desktopFileName()
assert not window.windowIcon().isNull()
app.setDesktopFileName("")
status = Status(JobState.DOWNLOADING, extra={"phase": "verify"})
verify = progress_ui.build_verify_window(status, lang="en")
assert not verify.windowIcon().isNull()
assert app.desktopFileName() == "voxprint-mdl"
print("ok")
"""


def test_download_and_check_windows_carry_the_mdl_mark() -> None:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    result = subprocess.run(
        [sys.executable, "-c", _CHILD],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ok")
