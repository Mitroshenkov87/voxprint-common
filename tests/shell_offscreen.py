"""Drive the reference shell on an offscreen Qt platform.

Pytest starts this file in a subprocess so the parent process does not import
PySide6. The parent test checks the PNG files this script writes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    """Open the shell, exercise chords, and write English and Russian PNGs."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from voxprint_common.i18n import set_language, tr
    from voxprint_common.ui.shell import ReferenceShell, grab_shell

    out = Path(sys.argv[1])
    set_language("en")
    app = QApplication([])
    shell = ReferenceShell()
    shell.resize(1100, 700)
    shell.show()
    shell.widget.activateWindow()
    app.processEvents()

    edit = shell.find("QLineEdit", "promptEdit")
    assert edit is not None
    edit.setText("north")  # type: ignore[attr-defined]
    edit.setFocus()  # type: ignore[attr-defined]
    app.processEvents()
    assert app.focusWidget() is edit
    QTest.keyClick(edit, Qt.Key_K, Qt.ControlModifier)  # type: ignore[arg-type]
    app.processEvents()
    assert shell.palette_open() is False
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)  # type: ignore[arg-type]
    app.processEvents()
    assert edit.selectedText() == "north"  # type: ignore[attr-defined]

    shell.widget.setFocus()
    app.processEvents()
    assert app.focusWidget() is shell.widget
    QTest.keyClick(shell.widget, Qt.Key_K, Qt.ControlModifier)
    app.processEvents()
    assert shell.palette_open() is True
    QTest.keyClick(shell.widget, Qt.Key_Escape)
    app.processEvents()
    assert shell.palette_open() is False

    QTest.keyClick(shell.widget, Qt.Key_W, Qt.ControlModifier)
    app.processEvents()
    status = shell.find("QLabel", "status")
    assert status is not None
    assert status.text() == tr("shell.status.protected")  # type: ignore[attr-defined]
    assert shell.history.layout.contains("canvas")

    shell._activate("prompt")
    QTest.keyClick(shell.widget, Qt.Key_F, Qt.ControlModifier | Qt.ShiftModifier)
    app.processEvents()
    assert shell.history.layout.floating
    assert shell.history.layout.floating[-1].panels == ["prompt"]
    shell.close()
    app.processEvents()

    grab_shell(out / "en-shell.png", language="en")
    grab_shell(out / "ru-shell.png", language="ru")
    grab_shell(out / "en-palette.png", language="en", show_palette=True)
    grab_shell(out / "ru-palette.png", language="ru", show_palette=True)


if __name__ == "__main__":
    main()
