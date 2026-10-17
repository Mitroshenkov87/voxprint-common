"""Qt pieces for a sectioned screen. Importing this module does not import Qt.

:func:`install_sections` adds the tab row and the table, note, and wizard
pages. A screen with no sections never calls it, so that screen's widget
tree stays as it was. Tabs that do not fit go into a More menu. The row is
not a scroll area.
"""

from __future__ import annotations

from typing import assert_never

from voxprint_common.i18n import tr
from voxprint_common.ui.sections import BodyKind, ColumnKind, StepKind, fit_tabs, picker_key
from voxprint_common.ui.settings import SettingType
from voxprint_common.ui.tokens import load

__all__ = ["install_sections", "sync_sections"]


def _qt() -> tuple[object, object, object]:
    from PySide6 import QtCore, QtGui, QtWidgets

    return QtCore, QtGui, QtWidgets


def install_sections(widget: object) -> None:
    """Add section tabs and pages to ``widget``.

    Args:
        widget: A simple screen whose layout has a section deck.
    """
    bar = _tab_bar(widget)
    root = widget.root.layout()  # type: ignore[attr-defined]
    root.insertWidget(1, bar)  # type: ignore[attr-defined]
    widget._section_bar = bar  # type: ignore[attr-defined]
    _build_pages(widget)
    _build_screen_actions(widget)
    sync_sections(widget)


def sync_sections(widget: object) -> None:
    """Show the open section, its wizard, or the shared screen.

    Args:
        widget: A simple screen whose layout has a section deck.
    """
    deck = widget._layout.sections  # type: ignore[attr-defined]
    if deck is None or not hasattr(widget, "_section_bar"):
        return
    screen = deck.shows_screen()
    _show_layout(widget._prompt_row, screen)  # type: ignore[attr-defined]
    rows = bool(widget._layout.rows)  # type: ignore[attr-defined]
    widget._preset_host.setVisible(screen and rows)  # type: ignore[attr-defined]
    widget._history_scroll.setVisible(screen)  # type: ignore[attr-defined]
    widget._section_bar.relayout()  # type: ignore[attr-defined]
    if deck.shows_wizard():
        _show_wizard(widget)
    elif deck.current.body is BodyKind.TABLE:
        _show_table(widget)
    elif deck.current.body is BodyKind.NOTE:
        widget._note_label.setText(tr(deck.current.note_key))  # type: ignore[attr-defined]
        widget._stack.setCurrentWidget(widget._note_page)  # type: ignore[attr-defined]
    elif deck.current.body is BodyKind.SCREEN:
        if widget._screen_signature != deck.current.section_id:  # type: ignore[attr-defined]
            _fill_actions(widget, widget._screen_actions, deck.current.actions)  # type: ignore[attr-defined]
            widget._screen_signature = deck.current.section_id  # type: ignore[attr-defined]
    else:
        assert_never(deck.current.body)
    _refresh_scope(widget)


def _tab_bar(widget: object) -> tuple[object, object, object]:
    _core, gui, widgets = _qt()

    class _Bar(widgets.QWidget):
        def __init__(self, host: object) -> None:
            super().__init__()
            self._host = host
            self._fitting = False
            self.setObjectName("sectionBar")
            row = widgets.QHBoxLayout(self)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            self._buttons: list[object] = []
            self._menu = widgets.QMenu(self)
            self._more = widgets.QPushButton(tr("shell.toolbar.overflow"), self)
            self._more.setObjectName("sectionMore")
            self._more.setMenu(self._menu)
            self._more.setAutoDefault(False)
            _fit_tab(gui, self._more)
            for section in host._layout.sections.document.sections:  # type: ignore[attr-defined]
                button = widgets.QPushButton(tr(section.label_key), self)
                button.setObjectName("sectionTab")
                button.setCheckable(True)
                button.setAutoDefault(False)
                button.setProperty("sectionId", section.section_id)
                _fit_tab(gui, button)
                section_id = section.section_id
                button.clicked.connect(
                    lambda _checked=False, chosen=section_id: _select(host, chosen)
                )
                self._buttons.append(button)
            self.setSizePolicy(
                widgets.QSizePolicy.Policy.Expanding,
                widgets.QSizePolicy.Policy.Fixed,
            )

        def resizeEvent(self, event: object) -> None:
            widgets.QWidget.resizeEvent(self, event)  # type: ignore[arg-type]
            self.relayout()

        def relayout(self) -> None:
            """Keep every visible tab at its text width and park the rest in More."""
            if self._fitting:
                return
            self._fitting = True
            try:
                _place_tabs(self)
            finally:
                self._fitting = False

    return _Bar(widget)


def _place_tabs(bar: object) -> None:
    available = bar.width()  # type: ignore[attr-defined]
    if available <= 0:
        return
    buttons = bar._buttons  # type: ignore[attr-defined]
    more = bar._more  # type: ignore[attr-defined]
    deck = bar._host._layout.sections  # type: ignore[attr-defined]
    current = deck.current.section_id
    for button in buttons:
        button.setChecked(button.property("sectionId") == current)  # type: ignore[attr-defined]
    shown = fit_tabs(
        [button.minimumWidth() for button in buttons],  # type: ignore[attr-defined]
        available,
        more.minimumWidth(),  # type: ignore[attr-defined]
        4,
    )
    row = bar.layout()  # type: ignore[attr-defined]
    while row.count():  # type: ignore[attr-defined]
        row.takeAt(0)  # type: ignore[attr-defined]
    for index, button in enumerate(buttons):
        visible = index < shown
        button.setVisible(visible)  # type: ignore[attr-defined]
        if visible:
            row.addWidget(button)  # type: ignore[attr-defined]
    menu = bar._menu  # type: ignore[attr-defined]
    menu.clear()  # type: ignore[attr-defined]
    for button in buttons[shown:]:
        section_id = str(button.property("sectionId"))  # type: ignore[attr-defined]
        menu.addAction(  # type: ignore[attr-defined]
            button.text(),  # type: ignore[attr-defined]
            lambda _checked=False, chosen=section_id: _select(bar._host, chosen),  # type: ignore[attr-defined]
        )
    overflow = shown < len(buttons)
    more.setVisible(overflow)  # type: ignore[attr-defined]
    if overflow:
        row.addWidget(more)  # type: ignore[attr-defined]
    row.addStretch(1)  # type: ignore[attr-defined]


def _fit_tab(gui: object, button: object) -> None:
    button.ensurePolished()  # type: ignore[attr-defined]
    font = gui.QFont(button.font())  # type: ignore[attr-defined]
    font.setPixelSize(load().density.body_px)  # type: ignore[attr-defined]
    font.setWeight(gui.QFont.Weight.DemiBold)  # type: ignore[attr-defined]
    advance = gui.QFontMetrics(font).horizontalAdvance(button.text())  # type: ignore[attr-defined]
    button.setMinimumWidth(int(advance) + 28)  # type: ignore[attr-defined]


def _build_pages(widget: object) -> None:
    widgets = widget._widgets  # type: ignore[attr-defined]
    table = widgets.QWidget()
    table.setObjectName("sectionTable")
    table_layout = widgets.QVBoxLayout(table)
    table_layout.setContentsMargins(16, 12, 16, 12)
    table_layout.setSpacing(8)
    actions = widgets.QHBoxLayout()
    actions.setSpacing(8)
    table_layout.addLayout(actions)
    grid_host = widgets.QWidget()
    grid_host.setObjectName("sectionGrid")
    table_layout.addWidget(grid_host, 1)
    widget._table_page = table  # type: ignore[attr-defined]
    widget._table_actions = actions  # type: ignore[attr-defined]
    widget._table_grid = grid_host  # type: ignore[attr-defined]
    widget._table_signature = ""  # type: ignore[attr-defined]
    note = widgets.QWidget()
    note.setObjectName("sectionNote")
    note_layout = widgets.QVBoxLayout(note)
    note_layout.setContentsMargins(16, 16, 16, 16)
    label = widgets.QLabel()
    label.setObjectName("emptyHint")
    label.setWordWrap(True)
    note_layout.addWidget(label)
    note_layout.addStretch(1)
    widget._note_page = note  # type: ignore[attr-defined]
    widget._note_label = label  # type: ignore[attr-defined]
    wizard = widgets.QWidget()
    wizard.setObjectName("sectionWizard")
    wizard_layout = widgets.QVBoxLayout(wizard)
    wizard_layout.setContentsMargins(16, 16, 16, 16)
    wizard_layout.setSpacing(8)
    title = widgets.QLabel()
    title.setObjectName("expertTitle")
    count = widgets.QLabel()
    count.setObjectName("wizardCount")
    name = widgets.QLabel()
    name.setObjectName("wizardStep")
    body = widgets.QWidget()
    body.setObjectName("wizardBody")
    buttons = widgets.QHBoxLayout()
    buttons.setSpacing(8)
    back = widgets.QPushButton(tr("wizard.back"))
    back.setObjectName("wizardBack")
    back.setAutoDefault(False)
    nxt = widgets.QPushButton(tr("wizard.next"))
    nxt.setObjectName("wizardNext")
    nxt.setAutoDefault(False)
    start = widgets.QPushButton(tr("wizard.start"))
    start.setObjectName("wizardStart")
    start.setAutoDefault(False)
    cancel = widgets.QPushButton(tr("simple.cancel"))
    cancel.setObjectName("wizardCancel")
    cancel.setAutoDefault(False)
    for button in (back, nxt, start, cancel):
        _fit_tab(widget._gui, button)  # type: ignore[attr-defined]
    back.clicked.connect(lambda: _retreat(widget))
    nxt.clicked.connect(lambda: _advance(widget))
    start.clicked.connect(lambda: _start(widget))
    cancel.clicked.connect(lambda: _cancel(widget))
    buttons.addWidget(back)
    buttons.addWidget(nxt)
    buttons.addWidget(start)
    buttons.addWidget(cancel)
    buttons.addStretch(1)
    wizard_layout.addWidget(title)
    wizard_layout.addWidget(count)
    wizard_layout.addWidget(name)
    wizard_layout.addWidget(body, 1)
    wizard_layout.addLayout(buttons)
    widget._wizard_page = wizard  # type: ignore[attr-defined]
    widget._wizard_title = title  # type: ignore[attr-defined]
    widget._wizard_count = count  # type: ignore[attr-defined]
    widget._wizard_name = name  # type: ignore[attr-defined]
    widget._wizard_body = body  # type: ignore[attr-defined]
    widget._wizard_back = back  # type: ignore[attr-defined]
    widget._wizard_next = nxt  # type: ignore[attr-defined]
    widget._wizard_start = start  # type: ignore[attr-defined]
    widget._wizard_cancel = cancel  # type: ignore[attr-defined]
    widget._wizard_signature = None  # type: ignore[attr-defined]
    stack = widget._stack  # type: ignore[attr-defined]
    stack.addWidget(table)
    stack.addWidget(note)
    stack.addWidget(wizard)


def _build_screen_actions(widget: object) -> None:
    widgets = widget._widgets  # type: ignore[attr-defined]
    row = widget._prompt_row  # type: ignore[attr-defined]
    if row is None:
        widget._screen_actions = None  # type: ignore[attr-defined]
        widget._screen_signature = ""  # type: ignore[attr-defined]
        return
    actions = widgets.QHBoxLayout()
    actions.setSpacing(8)
    widget._screen_actions = actions  # type: ignore[attr-defined]
    widget._screen_signature = ""  # type: ignore[attr-defined]
    row.insertLayout(row.count() - 1, actions)  # type: ignore[attr-defined]


def _show_table(widget: object) -> None:
    deck = widget._layout.sections  # type: ignore[attr-defined]
    section = deck.current
    if widget._table_signature != section.section_id:  # type: ignore[attr-defined]
        _fill_actions(widget, widget._table_actions, section.actions)  # type: ignore[attr-defined]
        _fill_grid(widget, section)
        widget._table_signature = section.section_id  # type: ignore[attr-defined]
    widget._stack.setCurrentWidget(widget._table_page)  # type: ignore[attr-defined]


def _fill_actions(widget: object, row: object, actions: tuple[object, ...]) -> None:
    if row is None:
        return
    _clear_layout(row)
    widgets = widget._widgets  # type: ignore[attr-defined]
    for action in actions:
        button = widgets.QPushButton(tr(action.label_key))  # type: ignore[attr-defined]
        button.setObjectName("sectionAction")
        button.setAutoDefault(False)
        _fit_tab(widget._gui, button)  # type: ignore[attr-defined]
        button.clicked.connect(
            lambda _checked=False, action_id=action.action_id: _run_action(widget, action_id)  # type: ignore[attr-defined]
        )
        row.addWidget(button)  # type: ignore[attr-defined]
    row.addStretch(1)  # type: ignore[attr-defined]


def _fill_grid(widget: object, section: object) -> None:
    widgets = widget._widgets  # type: ignore[attr-defined]
    host = widget._table_grid  # type: ignore[attr-defined]
    grid = host.layout()  # type: ignore[attr-defined]
    if grid is None:
        grid = widgets.QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
    else:
        _clear_layout(grid)
    for column_index, column in enumerate(section.columns):  # type: ignore[attr-defined]
        header = widgets.QLabel(tr(column.label_key))
        header.setObjectName("panel")
        grid.addWidget(header, 0, column_index)
    deck = widget._layout.sections  # type: ignore[attr-defined]
    for row_index, row in enumerate(section.rows, start=1):  # type: ignore[attr-defined]
        for column_index, column in enumerate(section.columns):  # type: ignore[attr-defined]
            grid.addWidget(
                _cell(widget, deck, section, row, column),
                row_index,
                column_index,
            )
    grid.setRowStretch(len(section.rows) + 1, 1)  # type: ignore[attr-defined]
    grid.setColumnStretch(len(section.columns), 1)  # type: ignore[attr-defined]


def _cell(widget: object, deck: object, section: object, row: object, column: object) -> object:
    widgets = widget._widgets  # type: ignore[attr-defined]
    kind = column.kind  # type: ignore[attr-defined]
    if kind is ColumnKind.LABEL:
        label = widgets.QLabel(tr(row.label_key))  # type: ignore[attr-defined]
        label.setObjectName("panel")
        return label
    if kind is ColumnKind.PREVIEW:
        button = widgets.QPushButton(tr("section.preview.play"))
        button.setObjectName("sectionPreview")
        button.setAutoDefault(False)
        _fit_tab(widget._gui, button)  # type: ignore[attr-defined]
        button.clicked.connect(
            lambda _checked=False, row_id=row.row_id: widget.set_note(tr("section.preview.playing"))  # type: ignore[attr-defined]
        )
        return button
    if kind is ColumnKind.PICKER:
        key = picker_key(section.section_id, row.row_id, column.column_id)  # type: ignore[attr-defined]
        combo = widgets.QComboBox()
        combo.setObjectName(key)
        selected = 0
        current = deck.picker(section.section_id, row.row_id, column.column_id)  # type: ignore[attr-defined]
        for index, choice in enumerate(column.choices):  # type: ignore[attr-defined]
            combo.addItem(tr(choice.label_key), choice.value)
            if choice.value == current:
                selected = index
        combo.setCurrentIndex(selected)
        section_id = section.section_id  # type: ignore[attr-defined]
        row_id = row.row_id  # type: ignore[attr-defined]
        column_id = column.column_id  # type: ignore[attr-defined]

        def _changed(_index: int, box: object = combo) -> None:
            _pick(widget, box, section_id, row_id, column_id)

        combo.currentIndexChanged.connect(_changed)
        return combo
    assert_never(kind)


def _show_wizard(widget: object) -> None:
    deck = widget._layout.sections  # type: ignore[attr-defined]
    wizard = deck.wizard
    step = deck.step
    if wizard is None or step is None:
        return
    signature = (wizard.wizard_id, step.step_id)
    if widget._wizard_signature != signature:  # type: ignore[attr-defined]
        widget._wizard_title.setText(tr(wizard.title_key))  # type: ignore[attr-defined]
        widget._wizard_name.setText(tr(step.label_key))  # type: ignore[attr-defined]
        _fill_wizard_body(widget, step)
        widget._wizard_signature = signature  # type: ignore[attr-defined]
    total = len(wizard.steps)
    widget._wizard_count.setText(  # type: ignore[attr-defined]
        tr("wizard.step_count", current=deck.index + 1, total=total)
    )
    running = step.kind is StepKind.RUNNING
    widget._wizard_back.setEnabled(deck.index > 0)  # type: ignore[attr-defined]
    widget._wizard_next.setVisible(not running)  # type: ignore[attr-defined]
    widget._wizard_next.setEnabled(deck.can_advance())  # type: ignore[attr-defined]
    widget._wizard_start.setVisible(running and not deck.running)  # type: ignore[attr-defined]
    widget._wizard_cancel.setVisible(running and deck.running)  # type: ignore[attr-defined]
    _paint_run(widget)
    widget._stack.setCurrentWidget(widget._wizard_page)  # type: ignore[attr-defined]


def _fill_wizard_body(widget: object, step: object) -> None:
    widgets = widget._widgets  # type: ignore[attr-defined]
    body = widget._wizard_body  # type: ignore[attr-defined]
    layout = body.layout()  # type: ignore[attr-defined]
    if layout is None:
        layout = widgets.QVBoxLayout(body)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
    else:
        _clear_layout(layout)
    if step.kind is StepKind.RUNNING:  # type: ignore[attr-defined]
        text = widgets.QLabel()
        text.setObjectName("progressText")
        text.setWordWrap(True)
        percent = widgets.QLabel()
        percent.setObjectName("wizardPercent")
        bar = widgets.QProgressBar()
        bar.setObjectName("progress")
        bar.setTextVisible(False)
        bar.setFixedWidth(280)
        widget._run_text = text  # type: ignore[attr-defined]
        widget._run_percent = percent  # type: ignore[attr-defined]
        widget._run_bar = bar  # type: ignore[attr-defined]
        layout.addWidget(text)
        layout.addWidget(percent)
        layout.addWidget(bar)
        layout.addStretch(1)
        return
    widget._run_text = None  # type: ignore[attr-defined]
    settings = widget._settings  # type: ignore[attr-defined]
    state = widget._layout.state  # type: ignore[attr-defined]
    for key in step.requires:  # type: ignore[attr-defined]
        spec = settings.get(key)
        label = widgets.QLabel(tr(spec.label_key) if spec is not None else key)
        label.setObjectName("panel")
        layout.addWidget(label)
        current = state.get(key) if state is not None and state.has(key) else ""
        if spec is not None and spec.type is SettingType.ENUM:
            combo = widgets.QComboBox()
            combo.setObjectName(key)
            selected = 0
            for index, choice in enumerate(spec.choices):
                combo.addItem(tr(choice.label_key), choice.value)
                if choice.value == current:
                    selected = index
            combo.setCurrentIndex(selected)
            combo.currentIndexChanged.connect(
                lambda _index, box=combo, setting_id=key: _write_step(
                    widget, setting_id, str(box.currentData())
                )
            )
            layout.addWidget(combo)
            continue
        edit = widgets.QLineEdit()
        edit.setObjectName(key)
        edit.setText(current if isinstance(current, str) else "")
        edit.textChanged.connect(lambda text, setting_id=key: _write_step(widget, setting_id, text))
        layout.addWidget(edit)
    layout.addStretch(1)


def _paint_run(widget: object) -> None:
    text = widget._run_text  # type: ignore[attr-defined]
    if text is None:
        return
    deck = widget._layout.sections  # type: ignore[attr-defined]
    progress = deck.progress
    if progress is None:
        text.setText(tr("wizard.running.idle"))
        widget._run_percent.setText("")  # type: ignore[attr-defined]
        widget._run_bar.setRange(0, 1)  # type: ignore[attr-defined]
        widget._run_bar.setValue(0)  # type: ignore[attr-defined]
        return
    done, total, label = progress
    text.setText(label)
    percent = 0 if total <= 0 else int(done * 100 / total)
    widget._run_percent.setText(tr("simple.progress.percent", percent=percent))  # type: ignore[attr-defined]
    widget._run_bar.setRange(0, total)  # type: ignore[attr-defined]
    widget._run_bar.setValue(done)  # type: ignore[attr-defined]


def _refresh_scope(widget: object) -> None:
    deck = widget._layout.sections  # type: ignore[attr-defined]
    step_id = ""
    if deck.shows_wizard() and deck.step is not None:
        step_id = deck.step.step_id
    scope = (deck.current.section_id, step_id)
    if scope == getattr(widget, "_section_scope", None):
        return
    widget._section_scope = scope  # type: ignore[attr-defined]
    if widget._expert_panel.isVisible():  # type: ignore[attr-defined]
        widget.set_expert(True)  # type: ignore[attr-defined]


def _select(widget: object, section_id: str) -> None:
    widget._layout.sections.select(section_id)  # type: ignore[attr-defined]
    widget.refresh()  # type: ignore[attr-defined]


def _run_action(widget: object, action_id: str) -> None:
    widget._layout.sections.action(action_id)  # type: ignore[attr-defined]
    widget._wizard_signature = None  # type: ignore[attr-defined]
    widget.refresh()  # type: ignore[attr-defined]


def _advance(widget: object) -> None:
    widget._layout.sections.advance()  # type: ignore[attr-defined]
    widget._wizard_signature = None  # type: ignore[attr-defined]
    widget.refresh()  # type: ignore[attr-defined]


def _retreat(widget: object) -> None:
    widget._layout.sections.retreat()  # type: ignore[attr-defined]
    widget._wizard_signature = None  # type: ignore[attr-defined]
    widget.refresh()  # type: ignore[attr-defined]


def _start(widget: object) -> None:
    widget._layout.sections.note_progress(0, 1, tr("wizard.train.running"))  # type: ignore[attr-defined]
    widget.refresh()  # type: ignore[attr-defined]


def _cancel(widget: object) -> None:
    widget._layout.sections.cancel_run()  # type: ignore[attr-defined]
    widget.refresh()  # type: ignore[attr-defined]


def _pick(
    widget: object,
    combo: object,
    section_id: str,
    row_id: str,
    column_id: str,
) -> None:
    value = combo.currentData()  # type: ignore[attr-defined]
    if not isinstance(value, str):
        return
    widget._layout.sections.set_picker(section_id, row_id, column_id, value)  # type: ignore[attr-defined]


def _write_step(widget: object, key: str, text: str) -> None:
    widget._layout.set_value(key, text)  # type: ignore[attr-defined]
    widget._wizard_next.setEnabled(widget._layout.sections.can_advance())  # type: ignore[attr-defined]


def _show_layout(layout: object, visible: bool) -> None:
    if layout is None:
        return
    layout.setSpacing(8 if visible else 0)  # type: ignore[attr-defined]
    for index in range(layout.count()):  # type: ignore[attr-defined]
        item = layout.itemAt(index)  # type: ignore[attr-defined]
        child = item.widget()  # type: ignore[attr-defined]
        if child is not None:
            child.setVisible(visible)  # type: ignore[attr-defined]
        nested = item.layout()  # type: ignore[attr-defined]
        if nested is not None:
            _show_layout(nested, visible)


def _clear_layout(layout: object) -> None:
    widgets = layout.count()  # type: ignore[attr-defined]
    while widgets:
        item = layout.takeAt(0)  # type: ignore[attr-defined]
        widgets -= 1
        child = item.widget()  # type: ignore[attr-defined]
        if child is not None:
            child.deleteLater()  # type: ignore[attr-defined]
        nested = item.layout()  # type: ignore[attr-defined]
        if nested is not None:
            _clear_layout(nested)
