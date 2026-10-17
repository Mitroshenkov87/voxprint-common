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
    shell = ReferenceShell(full_layout=True)
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
    _check_overflow(app)
    _check_focus_canvas(app, Qt, QTest)
    _check_close_retarget(app, Qt, QTest)
    _check_split_geometry(app)
    _check_split_click(app, QPoint, QEvent, QPointF, Qt, QMouseEvent, QApplication)
    _check_tab_close(app, Qt, QTest)
    _check_split_undo(app, QPoint, QEvent, QPointF, Qt, QMouseEvent, QApplication)
    _check_split_colour(app, ACCENT)

    _check_expert(app)
    _check_simple(app, Qt, QTest)
    grab_shell(out / "en-shell.png", language="en", full_layout=True)
    grab_shell(out / "ru-shell.png", language="ru", full_layout=True)
    grab_shell(out / "de-shell.png", language="de", full_layout=True, reveal_close=True)
    grab_shell(
        out / "en-palette.png",
        language="en",
        full_layout=True,
        show_palette=True,
        palette_query="pa",
    )
    grab_shell(
        out / "ru-palette.png",
        language="ru",
        full_layout=True,
        show_palette=True,
        palette_query="па",
    )
    grab_shell(out / "en-split.png", language="en", full_layout=True, hot_split=True)
    grab_shell(out / "en-simple-empty.png", language="en")
    grab_shell(out / "en-simple-result.png", language="en", result=True)
    grab_shell(
        out / "ru-simple-result.png",
        language="ru",
        result=True,
        prompt="Тихий холм в сумерках",
    )
    grab_shell(out / "en-expert.png", language="en", expert=True, result=True)


def _check_simple(app: object, qt: object, test: object) -> None:
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QApplication

    from voxprint_common.i18n import set_language, tr
    from voxprint_common.suite_settings import set_expert_mode
    from voxprint_common.ui.dock import layout_json
    from voxprint_common.ui.shell import ReferenceShell
    from voxprint_common.ui.simple_image import Phase

    set_language("en")
    set_expert_mode(False)
    shell = ReferenceShell()
    shell.resize(1200, 900)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.simple.root.isVisible() is True  # type: ignore[attr-defined]
    assert shell._dock_host.isVisible() is False  # type: ignore[attr-defined]
    assert shell._toolbar.isVisible() is False  # type: ignore[attr-defined]
    assert all(not tab.isVisible() for tab in shell.widget.findChildren(shell._widgets.QTabWidget))
    splitter = shell.find("QSplitter", "rootSplit")
    assert splitter is not None and splitter.isVisible() is False  # type: ignore[attr-defined]
    prompt = shell.find("QPlainTextEdit", "promptBox")
    assert prompt is not None
    assert prompt.placeholderText() == tr("shell.prompt.placeholder")  # type: ignore[attr-defined]
    generate = shell.find("QPushButton", "generate")
    assert generate is not None and generate.text() == tr("simple.generate")  # type: ignore[attr-defined]
    assert shell.find("QLabel", "emptyHint").text() == tr("simple.empty")  # type: ignore[attr-defined]
    assert shell.find("QPushButton", "chip-size-square").isChecked() is True  # type: ignore[attr-defined]
    assert shell.find("QPushButton", "chip-style-none").isChecked() is True  # type: ignore[attr-defined]
    more = shell.find("QPushButton", "overflow")
    menu_labels = [action.text() for action in more.menu().actions()]  # type: ignore[attr-defined]
    assert tr("simple.menu.open_folder") in menu_labels
    assert tr("simple.menu.settings") in menu_labels
    assert tr("command.view.toggle_expert_mode") in menu_labels
    assert tr("simple.menu.panels") not in menu_labels
    prompt.setPlainText("   ")  # type: ignore[attr-defined]
    prompt.setFocus()  # type: ignore[attr-defined]
    test.keyClick(prompt, qt.Key_Return)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.image.phase is Phase.EMPTY
    shell.image.set_prompt("a hill")
    assert shell.image.generate() is True
    shell.simple.refresh()
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.find("QLabel", "progressText").isVisible() is True  # type: ignore[attr-defined]
    assert shell.find("QLabel", "progressText").text() == tr("simple.progress")  # type: ignore[attr-defined]
    assert shell.find("QProgressBar", "progress").isVisible() is True  # type: ignore[attr-defined]
    cancel = shell.find("QPushButton", "cancel")
    assert cancel is not None and cancel.isVisible() is True  # type: ignore[attr-defined]
    cancel.click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.image.phase is Phase.EMPTY
    prompt.setPlainText("line")  # type: ignore[attr-defined]
    prompt.setFocus()  # type: ignore[attr-defined]
    prompt.moveCursor(QTextCursor.MoveOperation.End)  # type: ignore[attr-defined]
    test.keyClick(prompt, qt.Key_Return, qt.ShiftModifier)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert prompt.toPlainText() == "line\n"  # type: ignore[attr-defined]
    assert shell.image.phase is Phase.EMPTY
    test.keyClick(prompt, qt.Key_Return)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.image.phase is Phase.READY
    assert shell.image.history
    wide = shell.find("QPushButton", "chip-size-wide")
    wide.click()  # type: ignore[attr-defined]
    assert shell.image.presets["size"] == "wide"
    assert wide.isChecked() is True  # type: ignore[attr-defined]
    item_id = shell.image.selected
    thumb = shell.find("QPushButton", f"thumb-{item_id}")
    assert thumb is not None and thumb.isVisible() is True  # type: ignore[attr-defined]
    assert thumb.contextMenuPolicy() == qt.CustomContextMenu  # type: ignore[attr-defined]
    thumb.click()  # type: ignore[attr-defined]
    assert shell.image.selected == item_id
    menu = shell.simple.history_menu(item_id)
    labels = [action.text() for action in menu.actions()]
    assert labels == [
        tr("simple.menu.save"),
        tr("simple.menu.copy"),
        tr("simple.menu.use_prompt"),
    ]
    menu.actions()[1].trigger()
    assert QApplication.clipboard().text() == shell.image.item(item_id).prompt
    prompt.setPlainText("other words")  # type: ignore[attr-defined]
    menu.actions()[2].trigger()
    app.processEvents()  # type: ignore[attr-defined]
    assert prompt.toPlainText() == shell.image.item(item_id).prompt  # type: ignore[attr-defined]
    shell.run("view.settings")
    app.processEvents()  # type: ignore[attr-defined]
    sheet = shell.find("QFrame", "settingsSheet")
    assert sheet is not None and sheet.isVisible() is True  # type: ignore[attr-defined]
    pictures = sheet.findChild(shell._widgets.QSpinBox, "shell.pictures")  # type: ignore[attr-defined]
    assert pictures is not None and pictures.value() == 1 and pictures.toolTip() == ""
    assert sheet.findChild(shell._widgets.QCheckBox, "image.content_filter") is None  # type: ignore[attr-defined]
    before = layout_json(shell.history.layout)
    shell.run("view.toggle_expert_mode")
    app.processEvents()  # type: ignore[attr-defined]
    assert layout_json(shell.history.layout) == before
    assert shell.find("QFrame", "expertPanel").isVisible() is True  # type: ignore[attr-defined]
    assert shell.find("QDoubleSpinBox", "shell.guidance").isVisible() is True  # type: ignore[attr-defined]
    assert shell.find("QSpinBox", "shell.seed").isVisible() is True  # type: ignore[attr-defined]
    assert shell.find("QLineEdit", "shell.lora").isVisible() is True  # type: ignore[attr-defined]
    negative = shell.find("QPlainTextEdit", "shell.negative")
    assert negative is not None and negative.isVisible() is True  # type: ignore[attr-defined]
    assert negative.toolTip() == tr("setting.shell.negative.help")  # type: ignore[attr-defined]
    content = shell.find("QCheckBox", "image.content_filter")
    assert content is not None and content.isChecked() is True and content.isVisible() is True  # type: ignore[attr-defined]
    panels = [action.text() for action in shell.find("QPushButton", "overflow").menu().actions()]  # type: ignore[attr-defined]
    assert tr("simple.menu.panels") in panels
    shell.run("view.full_layout")
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.full_layout is True
    assert shell._dock_host.isVisible() is True  # type: ignore[attr-defined]
    assert shell.simple.root.isVisible() is False  # type: ignore[attr-defined]
    shell.run("view.simple_layout")
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.full_layout is False
    assert shell.expert.enabled() is True
    assert shell.simple.root.isVisible() is True  # type: ignore[attr-defined]
    assert layout_json(shell.history.layout) == before
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_expert(app: object) -> None:
    from PySide6.QtWidgets import QMenuBar

    from voxprint_common.i18n import set_language, tr
    from voxprint_common.suite_settings import set_expert_mode
    from voxprint_common.ui.dock import layout_json
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    set_expert_mode(False)
    shell = ReferenceShell()
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    assert "advanced" in layout_json(shell.history.layout)
    assert tr("shell.panel.advanced") not in _tab_titles(shell)
    assert tr("shell.panel.settings") not in _tab_titles(shell)
    assert shell.full_layout is False
    shell.set_full_layout(True)
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.full_layout is False
    chip = shell.find("QLabel", "expertChip")
    assert chip is not None and chip.isVisible() is False  # type: ignore[attr-defined]
    assert shell.find("QCheckBox", "image.content_filter") is None
    assert shell.find("QSpinBox", "shell.steps") is None
    pictures = shell.find("QSpinBox", "shell.pictures")
    assert pictures is not None and pictures.value() == 1  # type: ignore[attr-defined]
    assert pictures.toolTip() == ""  # type: ignore[attr-defined]
    shell.show_panel("settings")
    app.processEvents()  # type: ignore[attr-defined]
    before = layout_json(shell.history.layout)
    shell.run("view.toggle_expert_mode")
    app.processEvents()  # type: ignore[attr-defined]
    assert layout_json(shell.history.layout) == before
    assert chip.isVisible() is True  # type: ignore[attr-defined]
    assert chip.text() == tr("shell.status.expert")  # type: ignore[attr-defined]
    assert shell.full_layout is False
    panel = shell.find("QFrame", "expertPanel")
    assert panel is not None and panel.isVisible() is True  # type: ignore[attr-defined]
    assert tr("shell.panel.advanced") not in _tab_titles(shell)
    shell.set_full_layout(True)
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.full_layout is True
    assert tr("shell.panel.advanced") in _tab_titles(shell)
    assert tr("shell.panel.settings") in _tab_titles(shell)
    shell.set_full_layout(False)
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.full_layout is False
    content = shell.find("QCheckBox", "image.content_filter")
    steps = shell.find("QSpinBox", "shell.steps")
    sampler = shell.find("QComboBox", "shell.sampler")
    assert content is not None and content.isChecked() is True  # type: ignore[attr-defined]
    assert content.toolTip() == tr("setting.image.content_filter.help")  # type: ignore[attr-defined]
    assert steps is not None and steps.value() == 20  # type: ignore[attr-defined]
    assert steps.toolTip() == tr("setting.shell.steps.help")  # type: ignore[attr-defined]
    assert sampler is not None and sampler.currentText() == tr("setting.shell.sampler.euler")  # type: ignore[attr-defined]
    pictures = shell.find("QSpinBox", "shell.pictures")
    assert pictures is not None and pictures.toolTip() == ""  # type: ignore[attr-defined]
    bar = shell.widget.findChild(QMenuBar)
    assert bar is not None
    view = next(action.menu() for action in bar.actions() if action.text() == tr("shell.menu.view"))
    labels = [action.text().split("\t")[0] for action in view.actions()]
    assert tr("command.view.toggle_expert_mode") in labels
    shell.open_palette()
    shell._query.setText("Expert")
    app.processEvents()  # type: ignore[attr-defined]
    ids = [shell._hits.item(row).data(shell._qt.UserRole) for row in range(shell._hits.count())]
    assert "view.toggle_expert_mode" in ids
    shell.run("view.toggle_expert_mode")
    app.processEvents()  # type: ignore[attr-defined]
    assert layout_json(shell.history.layout) == before
    assert chip.isVisible() is False  # type: ignore[attr-defined]
    hidden_filter = shell.find("QCheckBox", "image.content_filter")
    assert hidden_filter is None or hidden_filter.isVisible() is False  # type: ignore[attr-defined]
    assert tr("shell.panel.advanced") not in _tab_titles(shell)
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]

    for language in _LOCALES:
        set_language(language)
        set_expert_mode(False)
        local = ReferenceShell()
        local.resize(900, 600)
        local.show()
        app.processEvents()  # type: ignore[attr-defined]
        more = local.find("QPushButton", "overflow")
        assert more is not None and more.menu() is not None  # type: ignore[attr-defined]
        local_labels = [action.text() for action in more.menu().actions()]  # type: ignore[attr-defined]
        assert tr("command.view.toggle_expert_mode") in local_labels
        assert tr("simple.menu.panels") not in local_labels
        assert local.find("QPushButton", "generate").text() == tr("simple.generate")  # type: ignore[attr-defined]
        local.close()
        app.processEvents()  # type: ignore[attr-defined]


def _tab_titles(shell: object) -> set[str]:
    titles: set[str] = set()
    bars = shell.widget.findChildren(shell._widgets.QTabBar)  # type: ignore[attr-defined]
    for bar in bars:
        if not bar.isVisible():
            continue
        for index in range(bar.count()):
            titles.add(bar.tabText(index))
    return titles


def _check_toolbar(app: object, language: str) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language(language)
    shell = ReferenceShell(full_layout=True)
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
    from voxprint_common.suite_settings import set_expert_mode
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    set_expert_mode(False)
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
    shell = ReferenceShell(full_layout=True)
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


def _check_overflow(app: object) -> None:
    from voxprint_common.i18n import set_language, tr
    from voxprint_common.ui.shell import ReferenceShell

    set_language("de")
    commands = (
        tr("command.file.save"),
        tr("command.edit.undo"),
        tr("command.edit.redo"),
        tr("command.view.palette"),
    )
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    shell._toolbar_available = 280
    shell._fill_toolbar()
    app.processEvents()  # type: ignore[attr-defined]
    buttons = [
        button
        for button in shell._toolbar.findChildren(shell._widgets.QPushButton)
        if button.isVisible()
    ]
    more = next((button for button in buttons if button.menu() is not None), None)
    assert more is not None
    menu_labels = [action.text() for action in more.menu().actions()]
    visible = [button.text() for button in buttons if button.menu() is None]
    missing = [label for label in commands if label not in visible]
    assert len(missing) >= 2, (visible, menu_labels)
    assert menu_labels == missing, (menu_labels, missing)
    assert more.width() + 1 >= more.sizeHint().width(), (more.width(), more.sizeHint().width())
    limit = shell._toolbar_available
    assert more.geometry().right() <= limit + 1
    for button in buttons:
        text = button.text()
        assert "\u2026" not in text and "..." not in text
        advance = button.fontMetrics().horizontalAdvance(text)
        assert advance <= button.contentsRect().width() + 1, (text, advance, button.width())
        assert button.geometry().right() <= limit + 1
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_focus_canvas(app: object, qt: object, test: object) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    shell.widget.setFocus()
    app.processEvents()  # type: ignore[attr-defined]
    spec = shell.registry.get("view.focus_canvas")
    assert spec is not None and spec.when == "global"
    shell._activate("prompt")
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.active == "prompt"
    test.keyClick(shell.widget, qt.Key_1, qt.ControlModifier)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.active == "canvas"
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_close_retarget(app: object, qt: object, test: object) -> None:
    from voxprint_common.i18n import set_language, tr
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    shell.widget.setFocus()
    shell._activate("prompt")
    app.processEvents()  # type: ignore[attr-defined]
    test.keyClick(shell.widget, qt.Key_W, qt.ControlModifier)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert not shell.history.layout.contains("prompt")
    assert shell.active == "layers"
    status = shell.find("QLabel", "status")
    assert status is not None
    assert status.text() != tr("shell.status.protected")  # type: ignore[attr-defined]
    test.keyClick(shell.widget, qt.Key_W, qt.ControlModifier)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert not shell.history.layout.contains("layers")
    assert shell.active == "canvas"
    assert shell.history.layout.contains("canvas")
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_split_geometry(app: object) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    inner = shell.find("QSplitter", "dockSplit")
    root = shell.find("QSplitter", "rootSplit")
    assert inner is not None and root is not None
    assert abs(inner.sizes()[1] - 260) <= 1, inner.sizes()  # type: ignore[attr-defined]
    assert inner.widget(0).minimumWidth() >= 140  # type: ignore[attr-defined]
    assert inner.widget(1).minimumWidth() >= 140  # type: ignore[attr-defined]
    assert root.widget(0).minimumWidth() >= 160  # type: ignore[attr-defined]
    assert root.widget(1).minimumWidth() >= 160  # type: ignore[attr-defined]
    inner.moveSplitter(inner.width(), 1)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert inner.sizes()[1] >= 140, inner.sizes()  # type: ignore[attr-defined]
    shell.resize(1280, 700)
    app.processEvents()  # type: ignore[attr-defined]
    assert abs(inner.sizes()[1] - 260) <= 1, inner.sizes()  # type: ignore[attr-defined]
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_split_click(
    app: object,
    point: object,
    event_type: object,
    pointf: object,
    qt: object,
    mouse_event: object,
    application: object,
) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.dock import SizeMode
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    root = shell.history.layout.root
    inner = root.second  # type: ignore[union-attr]
    assert root.size.mode is SizeMode.RATIO  # type: ignore[union-attr]
    assert inner.size.mode is SizeMode.FIXED_SECOND  # type: ignore[union-attr]
    ratio = root.size.value  # type: ignore[union-attr]
    fixed = inner.size.value  # type: ignore[union-attr]
    for name in ("rootSplit", "dockSplit"):
        splitter = shell.find("QSplitter", name)
        assert splitter is not None
        _click_handle(splitter, point, event_type, pointf, qt, mouse_event, application)
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.history.can_undo is False
    assert shell.history.layout.root.size.mode is SizeMode.RATIO  # type: ignore[union-attr]
    assert shell.history.layout.root.size.value == ratio  # type: ignore[union-attr]
    assert shell.history.layout.root.second.size.mode is SizeMode.FIXED_SECOND  # type: ignore[union-attr]
    assert shell.history.layout.root.second.size.value == fixed  # type: ignore[union-attr]
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _click_handle(
    splitter: object,
    point: object,
    event_type: object,
    pointf: object,
    qt: object,
    mouse_event: object,
    application: object,
) -> None:
    handle = splitter.handle(1)  # type: ignore[attr-defined]
    center = handle.rect().center()
    local = pointf(center)  # type: ignore[operator]
    global_pos = pointf(handle.mapToGlobal(center))  # type: ignore[attr-defined]
    press = mouse_event(
        event_type.Type.MouseButtonPress,  # type: ignore[attr-defined]
        local,
        global_pos,
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.KeyboardModifier.NoModifier,  # type: ignore[attr-defined]
    )
    application.sendEvent(handle, press)  # type: ignore[attr-defined]
    release = mouse_event(
        event_type.Type.MouseButtonRelease,  # type: ignore[attr-defined]
        local,
        global_pos,
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.MouseButton.NoButton,  # type: ignore[attr-defined]
        qt.KeyboardModifier.NoModifier,  # type: ignore[attr-defined]
    )
    application.sendEvent(handle, release)  # type: ignore[attr-defined]


def _check_tab_close(app: object, qt: object, test: object) -> None:
    from voxprint_common.i18n import set_language, tr
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    side = shell._widgets.QTabBar.ButtonPosition.RightSide
    bars = shell.widget.findChildren(shell._widgets.QTabBar)
    inactive = None
    for bar in bars:
        for index in range(bar.count()):
            button = bar.tabButton(index, side)
            if bar.tabText(index) == tr("shell.panel.canvas"):
                assert button is None
                continue
            assert button is not None
            if index == bar.currentIndex():
                assert button.isVisible()
            else:
                assert not button.isVisible()
                inactive = (bar, index, button)
    assert inactive is not None
    bar, index, button = inactive
    test.mouseMove(bar, bar.tabRect(index).center())  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert button.isVisible()
    current = bar.currentIndex()
    test.mouseMove(bar, bar.tabRect(current).center())  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert button.isVisible() is False
    assert bar.tabButton(current, side).isVisible()
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QHoverEvent
    from PySide6.QtWidgets import QApplication

    outside = QPointF(-20, -20)
    leave = QHoverEvent(
        QEvent.Type.HoverLeave,
        outside,
        outside,
        QPointF(bar.tabRect(index).center()),
    )
    QApplication.sendEvent(bar, leave)
    app.processEvents()  # type: ignore[attr-defined]
    assert button.isVisible() is False
    assert bar.tabButton(current, side).isVisible()
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_split_colour(app: object, accent: str) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    splitter = shell._root_splitter()
    handle = splitter.handle(1)  # type: ignore[attr-defined]
    line = handle.findChild(shell._widgets.QFrame, "splitLine")
    assert line is not None and line.width() == 1 and line.height() > 20
    shell._set_dragging(splitter, True)
    app.processEvents()  # type: ignore[attr-defined]
    assert line.width() >= 3 and line.height() > 20
    image = shell.grab().toImage()  # type: ignore[attr-defined]
    point = line.mapTo(shell.widget, line.rect().center())
    colour = image.pixelColor(point).name().lower()
    assert colour == accent.lower(), (colour, accent, point.x(), point.y())
    for dx in (-1, 1):
        side = image.pixelColor(point.x() + dx, point.y()).name().lower()
        assert side == accent.lower(), (dx, side, accent)
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
