"""Offscreen check: six Russian section tabs, no clipping and no scroll arrows."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    """Open the Audiobook Builder demo at 960 px and at a width that overflows."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication, QWidget

    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language("ru")
    app = QApplication([])
    state = Path(sys.argv[1])
    shell = ReferenceShell(app="audiobook-builder", sections=True, state_dir=state)
    shell.resize(960, 640)
    shell.show()
    app.processEvents()
    shell.simple.refresh()
    app.processEvents()
    _check(shell, 960, overflow=False)
    shell.widget.setMinimumSize(480, 640)
    shell.resize(520, 640)
    app.processEvents()
    shell.simple.refresh()
    app.processEvents()
    _check(shell, 520, overflow=True)
    plain = ReferenceShell(app="canvas", state_dir=state / "plain")
    plain.resize(1200, 900)
    plain.show()
    app.processEvents()
    assert plain.image.sections is None
    assert plain.simple.root.findChild(QWidget, "sectionBar") is None


def _check(shell: object, width: int, *, overflow: bool) -> None:
    from PySide6.QtWidgets import QPushButton, QScrollBar, QWidget

    from voxprint_common.i18n import tr

    bar = shell.simple.root.findChild(QWidget, "sectionBar")  # type: ignore[attr-defined]
    assert bar is not None
    assert shell.widget.width() == width  # type: ignore[attr-defined]
    assert [item for item in bar.findChildren(QScrollBar) if item.isVisible()] == []
    tabs = [button for button in bar.findChildren(QPushButton, "sectionTab")]
    visible = [button for button in tabs if button.isVisible()]
    more = bar.findChild(QPushButton, "sectionMore")
    assert more is not None
    assert more.text() == tr("shell.toolbar.overflow")
    for button in visible:
        assert button.width() >= button.minimumWidth(), button.text()
    labels = {button.text() for button in tabs}
    shown = {button.text() for button in visible}
    menu = {action.text() for action in more.menu().actions()} if more.isVisible() else set()
    assert shown.isdisjoint(menu)
    assert shown | menu == labels
    assert len(labels) == 6
    if overflow:
        assert more.isVisible()
        assert menu
        return
    assert len(visible) == 6
    assert more.isVisible() is False


if __name__ == "__main__":
    main()
