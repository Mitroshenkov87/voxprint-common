"""Drive the reference shell on an offscreen Qt platform.

Pytest starts this file in a subprocess so the parent process does not import
PySide6. The parent test checks the PNG files this script writes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_LOCALES = ("en", "ru", "de", "uk", "lv")
_GERMAN_TOOLS = ("Speichern", "Rückgängig", "Wiederholen", "Befehlspalette")


def main() -> None:
    """Open the shell, exercise chords, and write the reference PNGs."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from voxprint_common.i18n import set_language, tr
    from voxprint_common.ui.shell import ReferenceShell, grab_shell
    from voxprint_common.ui.theme import ACCENT

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

    for language in _LOCALES:
        _check_toolbar(app, language)
    _check_palette(app)
    _check_split_undo(app, QPoint, QEvent, QPointF, Qt, QMouseEvent, QApplication)
    _check_split_colour(app, ACCENT)

    grab_shell(out / "en-shell.png", language="en")
    grab_shell(out / "ru-shell.png", language="ru")
    grab_shell(out / "de-shell.png", language="de", reveal_close=True)
    grab_shell(out / "en-palette.png", language="en", show_palette=True, palette_query="pa")
    grab_shell(out / "ru-palette.png", language="ru", show_palette=True, palette_query="па")
    grab_shell(out / "en-split.png", language="en", hot_split=True)


def _check_toolbar(app: object, language: str) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language(language)
    shell = ReferenceShell()
    shell.resize(1024, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    buttons = [
        button
        for button in shell._toolbar.findChildren(shell._widgets.QPushButton)
        if button.isVisible()
    ]
    assert buttons, language
    labels: set[str] = set()
    for button in buttons:
        text = button.text()
        labels.add(text)
        assert "\u2026" not in text, (language, text)
        assert "..." not in text, (language, text)
        advance = button.fontMetrics().horizontalAdvance(text)
        inner = button.contentsRect().width()
        assert advance <= inner + 1, (language, text, advance, inner, button.width())
        assert button.geometry().right() <= shell._toolbar.width() + 1, (language, text)
    if language == "de":
        for needed in _GERMAN_TOOLS:
            assert needed in labels, labels
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_palette(app: object) -> None:
    from PySide6.QtWidgets import QLabel, QWidget

    from voxprint_common.i18n import set_language, tr
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell()
    shell.resize(1100, 700)
    shell.show()
    shell.open_palette()
    shell._query.setText("pa")
    app.processEvents()  # type: ignore[attr-defined]
    titles = shell.widget.findChildren(QLabel, "paletteTitle")
    assert any("font-weight: 600" in title.text() for title in titles)
    assert any("<b>" in title.text() for title in titles)
    rows = shell.widget.findChildren(QWidget, "paletteRow")
    selected = [row for row in rows if row.property("selected") == "true"]
    assert len(selected) == 1
    groups = {label.text() for label in shell.widget.findChildren(QLabel, "paletteGroup")}
    allowed = {tr("shell.group.file"), tr("shell.group.edit"), tr("shell.group.panels")}
    assert groups and groups <= allowed
    chips = shell.widget.findChildren(QLabel, "paletteChip")
    assert chips
    for chip in chips:
        row = chip.parent()
        title = row.findChild(QLabel, "paletteTitle")
        assert title is not None
        assert chip.x() > title.x()
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_split_undo(
    app: object,
    point: object,
    event_type: object,
    pointf: object,
    qt: object,
    mouse_event: object,
    application: object,
) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell()
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    splitter = shell.find("QSplitter", "rootSplit")
    assert splitter is not None
    handle = splitter.handle(1)  # type: ignore[attr-defined]
    before = shell.history.layout.root.size.value  # type: ignore[union-attr]
    assert shell.history.can_undo is False
    center = handle.rect().center()
    press = mouse_event(
        event_type.Type.MouseButtonPress,  # type: ignore[attr-defined]
        pointf(center),  # type: ignore[operator]
        pointf(handle.mapToGlobal(center)),  # type: ignore[attr-defined]
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.KeyboardModifier.NoModifier,  # type: ignore[attr-defined]
    )
    application.sendEvent(handle, press)  # type: ignore[attr-defined]
    moved = center + point(180, 0)  # type: ignore[operator]
    move = mouse_event(
        event_type.Type.MouseMove,  # type: ignore[attr-defined]
        pointf(moved),  # type: ignore[operator]
        pointf(handle.mapToGlobal(moved)),  # type: ignore[attr-defined]
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.KeyboardModifier.NoModifier,  # type: ignore[attr-defined]
    )
    application.sendEvent(handle, move)  # type: ignore[attr-defined]
    release = mouse_event(
        event_type.Type.MouseButtonRelease,  # type: ignore[attr-defined]
        pointf(moved),  # type: ignore[operator]
        pointf(handle.mapToGlobal(moved)),  # type: ignore[attr-defined]
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.MouseButton.NoButton,  # type: ignore[attr-defined]
        qt.KeyboardModifier.NoModifier,  # type: ignore[attr-defined]
    )
    application.sendEvent(handle, release)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert len(shell.history._undo) == 1, (splitter.sizes(), before)  # type: ignore[attr-defined]
    after = shell.history.layout.root.size.value  # type: ignore[union-attr]
    assert abs(after - before) >= 0.002, (before, after, splitter.sizes())  # type: ignore[attr-defined]
    shell.run("edit.undo")
    app.processEvents()  # type: ignore[attr-defined]
    restored = shell.history.layout.root.size.value  # type: ignore[union-attr]
    assert abs(restored - before) < 1e-9
    assert shell.history.can_undo is False
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_split_colour(app: object, accent: str) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell()
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    splitter = shell._root_splitter()
    handle = splitter.handle(1)  # type: ignore[attr-defined]
    line = handle.findChild(shell._widgets.QFrame, "splitLine")
    assert line is not None and line.width() == 1 and line.height() > 20
    shell._set_dragging(splitter, True)
    app.processEvents()  # type: ignore[attr-defined]
    image = shell.grab().toImage()  # type: ignore[attr-defined]
    point = line.mapTo(shell.widget, line.rect().center())
    colour = image.pixelColor(point).name().lower()
    assert colour == accent.lower(), (colour, accent, point.x(), point.y())
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
