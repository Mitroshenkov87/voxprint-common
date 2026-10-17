"""Reference main window for the shared dock, keymap, and palette.

PySide6 is imported inside :func:`_qt`, not when this module is imported, so
``reference_layout`` can be used without the ``ui`` extra. The window is one
surface: docks are child widgets, and a floated panel stays inside the
window. ``QDockWidget`` is not used.

Adoption order for the suite is :data:`ADOPTION`. Canvas is first. The Lucent
viewer process does not import the dock; its helper can use tokens, commands,
and the palette scorer.
"""

from __future__ import annotations

from pathlib import Path
from typing import assert_never

from voxprint_common.i18n import set_language, tr
from voxprint_common.ui.commands import Registry, reference_commands
from voxprint_common.ui.dock import (
    Activate,
    Close,
    DockError,
    DockHistory,
    Float,
    Layout,
    Permissions,
    ResizeSplit,
    SetStackOpen,
    SizeMode,
    Split,
    SplitAxis,
    SplitSize,
    Stack,
    Tabs,
    fit_toolbar,
)
from voxprint_common.ui.keymap import (
    KeyMap,
    display_chord,
    effective,
    host_platform,
    load_overrides,
)
from voxprint_common.ui.palette import score_text
from voxprint_common.ui.theme import build_shell_style

__all__ = [
    "ADOPTION",
    "ReferenceShell",
    "grab_shell",
    "reference_layout",
]

ADOPTION = (
    "canvas",
    "enlarge",
    "clarity",
    "audiobook-builder",
    "movie-dubber",
    "plotweaver",
    "lucent",
)

_CENTRE = "canvas"
_TOOLBAR = (
    ("file.save", 96.0),
    ("edit.undo", 96.0),
    ("edit.redo", 110.0),
    ("view.palette", 150.0),
)


def reference_layout() -> Layout[str]:
    """Return the factory layout: prompt and layers, the centre, and history.

    The centre panel is protected. Side splits around it are allowed.
    """
    left = Tabs(["prompt", "layers"], 0)
    centre = Tabs([_CENTRE], 0)
    history = Tabs(["history"], 0)
    inner = Split(SplitAxis.HORIZONTAL, SplitSize.fixed_second(260.0), centre, history, 140.0)
    root = Split(SplitAxis.HORIZONTAL, SplitSize.ratio(0.24), left, inner, 160.0)
    return Layout(root)


def grab_shell(
    path: Path, *, language: str, show_palette: bool = False, glass: bool = False
) -> None:
    """Paint the reference shell and save a PNG.

    Args:
        path: Destination PNG. Parent directories are created.
        language: Catalog language, for example ``en`` or ``ru``.
        show_palette: Open the command palette before the grab.
        glass: Use the translucent window background. Offscreen grabs use
            the plain background unless this is true.
    """
    set_language(language)
    _qt()
    widgets = _widgets()
    app = widgets.QApplication.instance() or widgets.QApplication([])
    shell = ReferenceShell(glass=glass)
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()
    if show_palette:
        shell.open_palette()
        app.processEvents()
    path.parent.mkdir(parents=True, exist_ok=True)
    shell.grab().save(str(path))
    shell.close()
    app.processEvents()


class ReferenceShell:
    """The reference window. Construction imports PySide6."""

    def __init__(self, *, glass: bool = False) -> None:
        qt, gui, widgets = _qt()
        self._qt = qt
        self._gui = gui
        self._widgets = widgets
        self.history = DockHistory(reference_layout())
        self.registry = Registry(reference_commands())
        overrides = load_overrides()
        self.keymap = KeyMap()
        self.keymap.assigned = list(overrides.assigned)
        self.keymap.removed = list(overrides.removed)
        self.active = _CENTRE
        self._building = False
        self._glass = glass
        self._float_cards: list[object] = []
        self._shortcuts: list[tuple[object, str, str]] = []
        self.widget = widgets.QWidget()
        self.widget.setObjectName("shell")
        self.widget.setFocusPolicy(qt.StrongFocus)
        self.widget.setAttribute(qt.WA_StyledBackground, True)
        self.widget.setStyleSheet(build_shell_style(glass))
        self.widget.setWindowTitle(tr("shell.window.title"))
        self._status = widgets.QLabel(tr("shell.status.ready"))
        self._status.setObjectName("status")
        self._toolbar = widgets.QWidget()
        self._dock = widgets.QWidget()
        self._palette = widgets.QFrame(self.widget)
        self._palette.setObjectName("float")
        self._palette.hide()
        self._query = widgets.QLineEdit()
        self._query.setObjectName("paletteQuery")
        self._query.setPlaceholderText(tr("shell.palette.placeholder"))
        self._hits = widgets.QListWidget()
        self._empty = widgets.QLabel(tr("shell.palette.empty"))
        self._empty.setObjectName("panel")
        self._build_chrome()
        self._build_palette()
        self._install_shortcuts()
        self.rebuild()

    def resize(self, width: int, height: int) -> None:
        """Resize the window.

        Args:
            width: Width in pixels.
            height: Height in pixels.
        """
        self.widget.resize(width, height)

    def show(self) -> None:
        """Show the window."""
        self.widget.show()

    def close(self) -> None:
        """Hide and close the window."""
        self.widget.close()

    def grab(self) -> object:
        """Return a pixmap of the window contents."""
        return self.widget.grab()

    def find(self, type_name: str, name: str) -> object | None:
        """Return a child widget by Qt type name and object name.

        Args:
            type_name: ``QLineEdit`` or ``QLabel``.
            name: Qt object name.
        """
        kind = getattr(self._widgets, type_name)
        return self.widget.findChild(kind, name)

    def palette_open(self) -> bool:
        """Return whether the command palette is visible."""
        return self._palette.isVisible()

    def open_palette(self) -> None:
        """Show the in-window command palette and focus its search field."""
        self._query.clear()
        self._refill_palette("")
        self._palette.setGeometry(280, 84, 540, 360)
        self._palette.show()
        self._palette.raise_()
        self._query.setFocus()

    def run(self, command_id: str) -> None:
        """Run one registered command.

        Args:
            command_id: Stable command id.
        """
        if command_id == "view.palette":
            self.open_palette()
            return
        if command_id == "file.save":
            self._status.setText(tr("shell.status.saved"))
            return
        if command_id == "edit.undo":
            self._history_step(self.history.undo)
            return
        if command_id == "edit.redo":
            self._history_step(self.history.redo)
            return
        if command_id == "view.close_panel":
            self._close_active()
            return
        if command_id == "view.float_panel":
            self._float_active()
            return
        if command_id == "view.focus_canvas":
            self._activate(_CENTRE)
            return

    def rebuild(self) -> None:
        """Paint the current dock tree again."""
        self._building = True
        layout = self._dock.layout()
        if layout is None:
            layout = self._widgets.QVBoxLayout(self._dock)
            layout.setContentsMargins(8, 0, 8, 0)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        root = self.history.layout.root
        if root is not None:
            layout.addWidget(self._mount(root, ()))
        self._mount_floats()
        self._fill_toolbar()
        if self._palette.isVisible():
            self._palette.raise_()
        self._building = False

    def _build_chrome(self) -> None:
        widgets = self._widgets
        root = widgets.QVBoxLayout(self.widget)
        root.setContentsMargins(12, 10, 12, 8)
        root.setSpacing(8)
        title = widgets.QLabel(tr("shell.window.title"))
        title.setObjectName("titlebar")
        root.addWidget(title)
        menu_bar = widgets.QMenuBar()
        file_menu = menu_bar.addMenu(tr("shell.menu.file"))
        edit_menu = menu_bar.addMenu(tr("shell.menu.edit"))
        view_menu = menu_bar.addMenu(tr("shell.menu.view"))
        self._add_action(file_menu, "file.save", "command.file.save")
        self._add_action(edit_menu, "edit.undo", "command.edit.undo")
        self._add_action(edit_menu, "edit.redo", "command.edit.redo")
        self._add_action(view_menu, "view.palette", "command.view.palette")
        self._add_action(view_menu, "view.close_panel", "command.view.close_panel")
        self._add_action(view_menu, "view.float_panel", "command.view.float_panel")
        self._add_action(view_menu, "view.focus_canvas", "command.view.focus_canvas")
        root.addWidget(menu_bar)
        root.addWidget(self._toolbar)
        root.addWidget(self._dock, 1)
        root.addWidget(self._status)

    def _add_action(self, menu: object, command_id: str, title_key: str) -> None:
        spec = self.registry.get(command_id)
        label = tr(title_key)
        if spec is not None and spec.chord:
            label = f"{label}\t{display_chord(spec.chord)}"
        action = menu.addAction(label)  # type: ignore[attr-defined]
        action.triggered.connect(lambda _checked=False, command=command_id: self.run(command))

    def _build_palette(self) -> None:
        widgets = self._widgets
        layout = widgets.QVBoxLayout(self._palette)
        layout.setContentsMargins(12, 12, 12, 12)
        heading = widgets.QLabel(tr("shell.palette.title"))
        heading.setObjectName("titlebar")
        layout.addWidget(heading)
        layout.addWidget(self._query)
        layout.addWidget(self._hits, 1)
        layout.addWidget(self._empty)
        self._query.textChanged.connect(self._refill_palette)
        self._query.returnPressed.connect(self._run_palette)
        self._hits.itemActivated.connect(lambda _item: self._run_palette())
        escape = self._gui.QShortcut(self._gui.QKeySequence("Escape"), self._palette)
        escape.setContext(self._qt.WidgetWithChildrenShortcut)
        escape.activated.connect(self._palette.hide)

    def _install_shortcuts(self) -> None:
        app = self._widgets.QApplication.instance() or self._widgets.QApplication([])
        app.focusChanged.connect(self._sync_shortcuts)
        for spec in self.registry:
            chord = self.keymap.chord_for(self.registry, spec.command_id)
            sequence = _qt_sequence(chord) if chord else ""
            if not sequence:
                continue
            shortcut = self._gui.QShortcut(self._gui.QKeySequence(sequence), self.widget)
            shortcut.setContext(self._qt.WindowShortcut)
            shortcut.activated.connect(lambda command=spec.command_id: self._run_shortcut(command))
            self._shortcuts.append((shortcut, spec.command_id, spec.when))
        self._sync_shortcuts()

    def _run_shortcut(self, command_id: str) -> None:
        spec = self.registry.get(command_id)
        if spec is None or not self.registry.enabled(spec):
            return
        self._palette.hide()
        self.run(command_id)

    def _sync_shortcuts(self, *_widgets: object) -> None:
        text = self._text_focus()
        active = ("global", "canvas") if self.active == _CENTRE else ("global",)
        for shortcut, command_id, when in self._shortcuts:
            spec = self.registry.get(command_id)
            allowed = spec is not None and self.registry.enabled(spec)
            if not allowed:
                shortcut.setEnabled(False)  # type: ignore[attr-defined]
                continue
            if text:
                shortcut.setEnabled(when == "text")  # type: ignore[attr-defined]
            else:
                shortcut.setEnabled(when != "text" and (when == "global" or when in active))  # type: ignore[attr-defined]

    def _refill_palette(self, query: str) -> None:
        self._hits.clear()
        scored: list[tuple[int, str, str]] = []
        for spec in self.registry:
            if not self.registry.enabled(spec):
                continue
            value = score_text(query, tr(spec.title_key), spec.command_id)
            if value is None:
                continue
            scored.append((value, tr(spec.title_key), spec.command_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        for _score, label, command_id in scored:
            chord = self.keymap.chord_for(self.registry, command_id)
            suffix = f"    {display_chord(chord)}" if chord else ""
            item = self._widgets.QListWidgetItem(label + suffix)
            item.setData(self._qt.UserRole, command_id)
            self._hits.addItem(item)
        self._empty.setVisible(not scored)
        if scored:
            self._hits.setCurrentRow(0)

    def _run_palette(self) -> None:
        current = self._hits.currentItem()
        if current is None:
            return
        command_id = current.data(self._qt.UserRole)
        self._palette.hide()
        if isinstance(command_id, str):
            self.run(command_id)

    def _fill_toolbar(self) -> None:
        widgets = self._widgets
        layout = self._toolbar.layout()
        if layout is None:
            layout = widgets.QHBoxLayout(self._toolbar)
            layout.setContentsMargins(0, 0, 0, 0)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        width = max(self.widget.width() - 48, 200)
        fitted = fit_toolbar([size for _command, size in _TOOLBAR], float(width), 8.0, 72.0)
        shown = {index for index, _size in fitted.shown} if fitted is not None else set()
        overflow = list(fitted.overflow) if fitted is not None else []
        for index, (command_id, _size) in enumerate(_TOOLBAR):
            if index not in shown:
                continue
            button = widgets.QPushButton(_command_label(command_id))
            if command_id == "file.save":
                button.setObjectName("primary")
            button.clicked.connect(lambda _checked=False, command=command_id: self.run(command))
            layout.addWidget(button)
        if overflow:
            more = widgets.QPushButton(tr("shell.toolbar.overflow"))
            menu = widgets.QMenu(more)
            for index in overflow:
                command_id = _TOOLBAR[index][0]
            action = menu.addAction(_command_label(command_id))
            action.triggered.connect(lambda _checked=False, command=command_id: self.run(command))
            more.setMenu(menu)
            layout.addWidget(more)
        layout.addStretch(1)

    def _mount(self, node: Split[str] | Tabs[str] | Stack[str], path: tuple[bool, ...]) -> object:
        widgets = self._widgets
        if isinstance(node, Tabs):
            tabs = widgets.QTabWidget()
            panels = tuple(node.panels)
            for panel in panels:
                tabs.addTab(_page(self, panel), _panel_title(panel))
            tabs.setCurrentIndex(node.active)
            tabs.currentChanged.connect(lambda index, names=panels: self._on_tab(index, names))
            return tabs
        if isinstance(node, Split):
            vertical = node.axis is SplitAxis.VERTICAL
            splitter = widgets.QSplitter(self._qt.Vertical if vertical else self._qt.Horizontal)
            splitter.addWidget(self._mount(node.first, (*path, False)))
            splitter.addWidget(self._mount(node.second, (*path, True)))
            splitter.setChildrenCollapsible(False)
            total = 1000
            mode = node.size.mode
            if mode is SizeMode.RATIO:
                first = int(total * node.size.value)
            elif mode is SizeMode.FIXED_FIRST:
                first = int(min(node.size.value, total))
            elif mode is SizeMode.FIXED_SECOND:
                first = int(max(total - node.size.value, 0))
            else:
                assert_never(mode)
            splitter.setSizes([first, total - first])
            splitter.splitterMoved.connect(
                lambda _pos, _index, here=path, widget=splitter: self._resized(here, widget)
            )
            return splitter
        if isinstance(node, Stack):
            column = widgets.QWidget()
            layout = widgets.QVBoxLayout(column)
            layout.setContentsMargins(0, 0, 0, 0)
            for entry in node.entries:
                button = widgets.QPushButton(_panel_title(entry.panel))
                body = _page(self, entry.panel)
                body.setVisible(entry.open)
                button.clicked.connect(
                    lambda _checked=False, panel=entry.panel, box=body: self._toggle_stack(
                        panel, box
                    )
                )
                layout.addWidget(button)
                layout.addWidget(body)
            layout.addStretch(1)
            return column
        assert_never(node)

    def _on_tab(self, index: int, panels: tuple[str, ...]) -> None:
        if index < 0 or index >= len(panels):
            return
        self._activate(panels[index])

    def _mount_floats(self) -> None:
        self._clear_floats()
        for group in self.history.layout.floating:
            card = self._widgets.QFrame(self.widget)
            card.setObjectName("float")
            card.setGeometry(*(int(value) for value in group.rect))
            layout = self._widgets.QVBoxLayout(card)
            tabs = self._widgets.QTabWidget()
            for panel in group.panels:
                tabs.addTab(_page(self, panel), _panel_title(panel))
            tabs.setCurrentIndex(group.active)
            layout.addWidget(tabs)
            card.raise_()
            card.show()
            self._float_cards.append(card)

    def _clear_floats(self) -> None:
        for card in self._float_cards:
            card.hide()  # type: ignore[attr-defined]
            card.setParent(None)  # type: ignore[attr-defined]
            card.deleteLater()  # type: ignore[attr-defined]
        self._float_cards.clear()

    def _resized(self, path: tuple[bool, ...], splitter: object) -> None:
        if self._building:
            return
        sizes = splitter.sizes()  # type: ignore[attr-defined]
        total = sum(sizes)
        if total <= 0:
            return
        try:
            self.history.layout.apply(ResizeSplit(path, SplitSize.ratio(sizes[0] / total)))
        except DockError:
            return

    def _activate(self, panel: str) -> None:
        if self._building:
            return
        self.active = panel
        self._sync_shortcuts()
        try:
            self.history.layout.apply(Activate(panel))
        except DockError:
            return

    def _toggle_stack(self, panel: str, body: object) -> None:
        open_now = not body.isVisible()  # type: ignore[attr-defined]
        try:
            self.history.layout.apply(SetStackOpen(panel, open_now))
        except DockError:
            return
        body.setVisible(open_now)  # type: ignore[attr-defined]

    def _close_active(self) -> None:
        panel = self.active
        if panel == _CENTRE or not self.history.layout.contains(panel):
            self._status.setText(tr("shell.status.protected"))
            return
        try:
            self.history.apply(Close(panel), _permissions)
        except DockError:
            self._status.setText(tr("shell.status.protected"))
            return
        self.rebuild()

    def _float_active(self) -> None:
        panel = self.active
        if panel == _CENTRE or not self.history.layout.contains(panel):
            self._status.setText(tr("shell.status.protected"))
            return
        try:
            self.history.apply(Float(panel, (48.0, 72.0, 280.0, 220.0)), _permissions)
        except DockError:
            self._status.setText(tr("shell.status.protected"))
            return
        self.rebuild()

    def _history_step(self, step: object) -> None:
        try:
            step()  # type: ignore[operator]
        except DockError:
            return
        self.rebuild()

    def _text_focus(self) -> bool:
        focus = self._widgets.QApplication.focusWidget()
        return isinstance(
            focus, self._widgets.QLineEdit | self._widgets.QPlainTextEdit | self._widgets.QTextEdit
        )


def _page(shell: ReferenceShell, panel: str) -> object:
    widgets = shell._widgets
    page = widgets.QWidget()
    layout = widgets.QVBoxLayout(page)
    label = widgets.QLabel(_panel_body(panel))
    label.setObjectName("panel")
    label.setWordWrap(True)
    layout.addWidget(label)
    if panel == "prompt":
        edit = widgets.QLineEdit()
        edit.setObjectName("promptEdit")
        edit.setPlaceholderText(tr("shell.prompt.placeholder"))
        layout.addWidget(edit)
    layout.addStretch(1)
    page.setObjectName(panel)
    return page


def _panel_title(panel: str) -> str:
    if panel == "prompt":
        return tr("shell.panel.prompt")
    if panel == "layers":
        return tr("shell.panel.layers")
    if panel == _CENTRE:
        return tr("shell.panel.canvas")
    if panel == "history":
        return tr("shell.panel.history")
    return panel


def _panel_body(panel: str) -> str:
    if panel == "prompt":
        return tr("shell.panel.prompt.body")
    if panel == "layers":
        return tr("shell.panel.layers.body")
    if panel == _CENTRE:
        return tr("shell.panel.canvas.body")
    if panel == "history":
        return tr("shell.panel.history.body")
    return panel


def _command_label(command_id: str) -> str:
    if command_id == "file.save":
        return tr("command.file.save")
    if command_id == "edit.undo":
        return tr("command.edit.undo")
    if command_id == "edit.redo":
        return tr("command.edit.redo")
    if command_id == "view.palette":
        return tr("command.view.palette")
    if command_id == "view.close_panel":
        return tr("command.view.close_panel")
    if command_id == "view.float_panel":
        return tr("command.view.float_panel")
    if command_id == "view.focus_canvas":
        return tr("command.view.focus_canvas")
    return command_id


def _permissions(panel: str) -> Permissions:
    if panel == _CENTRE:
        return Permissions.protected()
    return Permissions()


def _qt_sequence(chord: str) -> str:
    stored = effective(chord, host_platform())
    parts = stored.split("+")
    mapped: list[str] = []
    for part in parts[:-1]:
        if part == "Mod":
            mapped.append("Ctrl")
        elif part == "Ctrl":
            mapped.append("Meta")
        elif part == "Alt":
            mapped.append("Alt")
        elif part == "Shift":
            mapped.append("Shift")
        else:
            return ""
    mapped.append(parts[-1])
    return "+".join(mapped)


def _qt() -> tuple[object, object, object]:
    """Import PySide6.

    The import stays inside this function so a headless caller can import
    :func:`reference_layout` without the ``ui`` extra.
    """
    from PySide6 import QtCore, QtGui, QtWidgets

    return QtCore.Qt, QtGui, QtWidgets


def _widgets() -> object:
    return _qt()[2]
