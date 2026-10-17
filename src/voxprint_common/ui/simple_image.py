"""Image-app pattern: a prompt, plain presets, the picture, and a history strip.

Canvas, Enlarge, and the Lucent helper use this screen. :class:`SimpleImageLayout`
is the headless description. :class:`SimpleImageWidget` is the Qt view. Importing
this module does not import PySide6. Expert mode adds a side panel beside the
picture. The dock stays a separate API and is not a control on this screen.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from voxprint_common.i18n import tr
from voxprint_common.ui.settings import Settings, SettingSpec, SettingType

__all__ = [
    "GalleryItem",
    "Phase",
    "PresetChoice",
    "PresetRow",
    "SimpleImageLayout",
    "SimpleImageWidget",
    "image_presets",
]


class Phase(StrEnum):
    """What the centre of the screen is showing."""

    EMPTY = "empty"
    BUSY = "busy"
    READY = "ready"


@dataclass(frozen=True)
class PresetChoice:
    """One chip in a preset row.

    ``label_key`` is a catalog key. The visible word is plain.
    """

    value: str
    label_key: str


@dataclass(frozen=True)
class PresetRow:
    """A row of segmented chips an app supplies.

    ``default`` is the chip selected when the screen opens.
    """

    row_id: str
    label_key: str
    choices: tuple[PresetChoice, ...]
    default: str

    def __post_init__(self) -> None:
        """Reject an empty row or a default that is not one of the chips."""
        values = [choice.value for choice in self.choices]
        if not self.row_id or not self.label_key or not values or len(values) != len(set(values)):
            raise ValueError("a preset row needs an id and distinct choices")
        if self.default not in values:
            raise ValueError("a preset default must be one of the choices")


@dataclass(frozen=True)
class GalleryItem:
    """One finished picture in the history strip.

    ``presets`` is the chip selection that produced it, in row order.
    """

    item_id: str
    prompt: str
    presets: tuple[tuple[str, str], ...]


class SimpleImageLayout:
    """Headless state for the simple image screen. Qt is not imported.

    Apps supply preset rows. Size, style, and how many pictures are the
    reference rows from :func:`image_presets`. ``full_layout`` is a host API
    for a dock. It is off unless a caller asks, and only while Expert mode
    is on. The widget does not draw that switch.
    """

    def __init__(self, rows: Iterable[PresetRow] = ()) -> None:
        """Copy ``rows`` and select each row's default.

        Args:
            rows: Preset rows. Ids must be unique.

        Raises:
            ValueError: Two rows share an id.
        """
        self._rows = tuple(rows)
        ids = [row.row_id for row in self._rows]
        if len(ids) != len(set(ids)):
            raise ValueError("preset row ids must be unique")
        self.prompt = ""
        self.phase = Phase.EMPTY
        self.presets = {row.row_id: row.default for row in self._rows}
        self.history: list[GalleryItem] = []
        self.selected: str | None = None
        self.full_layout = False

    @property
    def rows(self) -> tuple[PresetRow, ...]:
        """Return the preset rows in order."""
        return self._rows

    def set_prompt(self, text: str) -> None:
        """Replace the prompt text.

        Args:
            text: The words in the prompt box, including a blank string.
        """
        self.prompt = text

    def choose(self, row_id: str, value: str) -> None:
        """Select the chip ``value`` in ``row_id``.

        Args:
            row_id: Preset row id.
            value: One of that row's choice values.

        Raises:
            ValueError: The row or the value is unknown.
        """
        row = self._row(row_id)
        if row is None or value not in {choice.value for choice in row.choices}:
            raise ValueError(f"unknown preset {row_id}={value}")
        self.presets[row_id] = value

    def generate(self) -> bool:
        """Start a picture when the prompt has words and nothing is running.

        Returns:
            True when the phase changed to busy.
        """
        if self.phase is Phase.BUSY or not self.prompt.strip():
            return False
        self.phase = Phase.BUSY
        return True

    def cancel(self) -> None:
        """Leave the busy phase. A picture already in the strip stays up."""
        if self.phase is not Phase.BUSY:
            return
        self.phase = Phase.READY if self.selected else Phase.EMPTY

    def finish(self, item_id: str) -> GalleryItem:
        """Store a finished picture at the front of the history and show it.

        Args:
            item_id: Stable id for this picture. The caller chooses it.

        Returns:
            The new history item.

        Raises:
            ValueError: Nothing is in progress, or ``item_id`` is already used.
        """
        if self.phase is not Phase.BUSY:
            raise ValueError("finish needs a picture that is in progress")
        if not item_id or any(item.item_id == item_id for item in self.history):
            raise ValueError(f"gallery id {item_id!r} is empty or already used")
        item = GalleryItem(
            item_id,
            self.prompt.strip(),
            tuple(self.presets.items()),
        )
        self.history.insert(0, item)
        self.selected = item.item_id
        self.phase = Phase.READY
        return item

    def select(self, item_id: str) -> GalleryItem | None:
        """Show ``item_id`` large. A busy generation is left running.

        Args:
            item_id: History item id.

        Returns:
            The item, or None when the id is unknown.
        """
        item = self.item(item_id)
        if item is None:
            return None
        self.selected = item.item_id
        if self.phase is not Phase.BUSY:
            self.phase = Phase.READY
        return item

    def item(self, item_id: str | None) -> GalleryItem | None:
        """Return a history item, or None.

        Args:
            item_id: History item id, or None.
        """
        if item_id is None:
            return None
        return next((item for item in self.history if item.item_id == item_id), None)

    def use_prompt_again(self, item_id: str | None = None) -> str | None:
        """Copy a history item's words and chips back into the prompt.

        Args:
            item_id: History item. None uses the picture on screen.

        Returns:
            The restored prompt, or None when that picture is missing.
        """
        item = self.item(item_id or self.selected)
        if item is None:
            return None
        self.prompt = item.prompt
        for row_id, value in item.presets:
            row = self._row(row_id)
            if row is not None and value in {choice.value for choice in row.choices}:
                self.presets[row_id] = value
        return item.prompt

    def more_like(self, item_id: str | None = None) -> bool:
        """Start another picture from one already in the strip.

        The prompt and chips are copied from that picture first.

        Args:
            item_id: History item. None uses the picture on screen.

        Returns:
            True when a picture starts.
        """
        if self.phase is Phase.BUSY or self.use_prompt_again(item_id) is None:
            return False
        return self.generate()

    def batch_size(self) -> int:
        """Return how many pictures one Generate makes.

        The ``count`` preset is the source. A missing or non-numeric value
        means one picture.
        """
        raw = self.presets.get("count", "1")
        if raw.isdigit() and int(raw) >= 1:
            return int(raw)
        return 1

    def request_full_layout(self, enabled: bool, *, expert: bool) -> bool:
        """Ask for the dock. The dock stays off unless Expert mode is on.

        Turning Expert mode off clears the request, so the next open is the
        simple screen again.

        Args:
            enabled: True asks for the dock.
            expert: True when Expert mode is on.

        Returns:
            Whether the dock should be on screen.
        """
        if not expert:
            self.full_layout = False
            return False
        self.full_layout = enabled
        return self.full_layout

    def dock_visible(self, *, expert: bool) -> bool:
        """Return whether the dock is on screen.

        Args:
            expert: True when Expert mode is on.
        """
        return expert and self.full_layout

    def _row(self, row_id: str) -> PresetRow | None:
        return next((row for row in self._rows if row.row_id == row_id), None)


def image_presets() -> tuple[PresetRow, ...]:
    """Return the reference size and style rows.

    Apps pass their own rows to :class:`SimpleImageLayout` instead of these.
    """
    return (
        PresetRow(
            "size",
            "simple.size",
            (
                PresetChoice("square", "simple.size.square"),
                PresetChoice("portrait", "simple.size.portrait"),
                PresetChoice("landscape", "simple.size.landscape"),
                PresetChoice("wide", "simple.size.wide"),
            ),
            "square",
        ),
        PresetRow(
            "style",
            "simple.style",
            (
                PresetChoice("none", "simple.style.none"),
                PresetChoice("photo", "simple.style.photo"),
                PresetChoice("illustration", "simple.style.illustration"),
                PresetChoice("anime", "simple.style.anime"),
                PresetChoice("3d", "simple.style.3d"),
            ),
            "none",
        ),
        PresetRow(
            "count",
            "simple.count",
            (
                PresetChoice("1", "simple.count.1"),
                PresetChoice("2", "simple.count.2"),
                PresetChoice("4", "simple.count.4"),
            ),
            "1",
        ),
    )


class SimpleImageWidget:
    """Qt view of a :class:`SimpleImageLayout`. Construction imports PySide6.

    Enter in the prompt starts a picture. Shift+Enter inserts a line. Size,
    style, and how many pictures stay on screen, with More like this, Save,
    Open folder, and Use prompt again. Expert mode adds a side panel and
    leaves every other control where it was. The history strip shows a
    neutral gradient for each finished item. That gradient is a placeholder,
    not a photograph. A problem is one plain sentence under the strip.
    """

    def __init__(
        self,
        layout: SimpleImageLayout,
        settings: Settings,
        *,
        on_generate: Callable[[], None] | None = None,
        on_command: Callable[[str], None] | None = None,
    ) -> None:
        """Build the screen.

        Args:
            layout: Headless state. The widget does not replace it.
            settings: Expert settings go in the side panel. Plain settings are
                not drawn here; everyday choices are preset chips.
            on_generate: Called after a prompt starts a picture.
            on_command: Called with a command id from Save, Open folder, or
                Expert mode.
        """
        core, gui, widgets = _qt()
        self._core = core
        self._gui = gui
        self._widgets = widgets
        self._qt = core.Qt
        self._layout = layout
        self._settings = settings
        self._on_generate = on_generate
        self._on_command = on_command
        self._handling_enter = False
        self._source: object | None = None
        self.root = widgets.QWidget()
        self.root.setObjectName("simpleRoot")
        self._build()

    def place_chip(self, chip: object) -> None:
        """Put the Expert chip at the top of the side panel.

        The chip stays inside the panel, so turning Expert mode on does not
        move the prompt, the chips, or the everyday actions.

        Args:
            chip: The shell's status chip. It is moved, not copied.
        """
        if self._panel_layout.indexOf(chip) >= 0:  # type: ignore[attr-defined]
            return
        self._panel_layout.insertWidget(0, chip)  # type: ignore[attr-defined]

    def set_expert(self, enabled: bool) -> None:
        """Show or hide the expert side panel.

        Args:
            enabled: True shows the expert settings beside the picture.
        """
        self._expert_panel.setVisible(enabled)  # type: ignore[attr-defined]
        _clear(self._expert_form)
        if enabled:
            specs = tuple(spec for spec in self._settings.visible(expert=True) if spec.expert)
            _fill_form(self._widgets, self._expert_form, specs)
            self._expert_form.addStretch(1)  # type: ignore[attr-defined]

    def set_note(self, text: str) -> None:
        """Show a short line under the history, or hide it when ``text`` is empty.

        Args:
            text: Already translated.
        """
        self._note.setText(text)  # type: ignore[attr-defined]
        self._note.setVisible(bool(text))  # type: ignore[attr-defined]

    def set_prompt_text(self, text: str) -> None:
        """Put ``text`` in the prompt box and the layout.

        Args:
            text: Prompt words.
        """
        self._layout.set_prompt(text)
        self._prompt.setPlainText(text)  # type: ignore[attr-defined]

    def refresh(self, *, prompt: bool = False) -> None:
        """Paint the current phase, chips, and history.

        Args:
            prompt: Also copy the layout prompt into the box.
        """
        if prompt:
            self._prompt.setPlainText(self._layout.prompt)  # type: ignore[attr-defined]
        self._sync_presets()
        self._fill_history()
        busy = self._layout.phase is Phase.BUSY
        self._generate_button.setEnabled(not busy)  # type: ignore[attr-defined]
        if self._layout.phase is Phase.EMPTY:
            self._stack.setCurrentIndex(0)  # type: ignore[attr-defined]
            return
        if self._layout.phase is Phase.BUSY:
            self._stack.setCurrentIndex(1)  # type: ignore[attr-defined]
            return
        self._show_ready()

    def history_menu(self, item_id: str) -> object:
        """Return the Save, Copy, and Use prompt again menu for ``item_id``.

        Args:
            item_id: History item the menu acts on.
        """
        widgets = self._widgets
        menu = widgets.QMenu(self.root)
        menu.addAction(tr("simple.menu.save"), lambda: self._save(item_id))
        menu.addAction(tr("simple.menu.copy"), lambda: self._copy(item_id))
        menu.addAction(tr("simple.menu.use_prompt"), lambda: self._use(item_id))
        return menu

    def _build(self) -> None:
        widgets = self._widgets
        qt = self._qt
        root = widgets.QVBoxLayout(self.root)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        self._top = widgets.QHBoxLayout()
        self._top.setSpacing(8)
        self._top.addStretch(1)
        self._expert_toggle = widgets.QPushButton(tr("command.view.toggle_expert_mode"))
        self._expert_toggle.setObjectName("expertToggle")
        self._expert_toggle.setAutoDefault(False)
        self._expert_toggle.clicked.connect(lambda: self._command("view.toggle_expert_mode"))
        self._top.addWidget(self._expert_toggle)
        root.addLayout(self._top)
        prompt_row = widgets.QHBoxLayout()
        prompt_row.setSpacing(12)
        self._prompt = widgets.QPlainTextEdit()
        self._prompt.setObjectName("promptBox")
        self._prompt.setPlaceholderText(tr("shell.prompt.placeholder"))
        self._prompt.setMinimumHeight(96)
        self._generate_button = widgets.QPushButton(tr("simple.generate"))
        self._generate_button.setObjectName("generate")
        self._generate_button.setAutoDefault(False)
        self._generate_button.setMinimumSize(168, 96)
        self._generate_button.clicked.connect(self._generate)
        prompt_row.addWidget(self._prompt, 1)
        prompt_row.addWidget(self._generate_button)
        root.addLayout(prompt_row)
        self._preset_host = widgets.QWidget()
        self._preset_rows = widgets.QVBoxLayout(self._preset_host)
        self._preset_rows.setContentsMargins(0, 0, 0, 0)
        self._preset_rows.setSpacing(8)
        root.addWidget(self._preset_host)
        self._fill_presets()
        centre = widgets.QHBoxLayout()
        centre.setSpacing(12)
        self._stage = widgets.QFrame()
        self._stage.setObjectName("stage")
        self._stage.setAttribute(qt.WA_StyledBackground, True)
        stage_layout = widgets.QVBoxLayout(self._stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        self._stack = widgets.QStackedWidget()
        self._stack.setObjectName("stageStack")
        self._stack.addWidget(self._empty_page())
        self._stack.addWidget(self._busy_page())
        self._stack.addWidget(self._ready_page())
        stage_layout.addWidget(self._stack, 1)
        stage_layout.addWidget(self._action_bar())
        centre.addWidget(self._stage, 1)
        self._expert_panel = widgets.QFrame()
        self._expert_panel.setObjectName("expertPanel")
        self._expert_panel.setAttribute(qt.WA_StyledBackground, True)
        self._expert_panel.setFixedWidth(280)
        self._expert_panel.setMinimumHeight(0)
        panel_policy = widgets.QSizePolicy(
            widgets.QSizePolicy.Policy.Fixed,
            widgets.QSizePolicy.Policy.Expanding,
        )
        self._expert_panel.setSizePolicy(panel_policy)
        self._panel_layout = widgets.QVBoxLayout(self._expert_panel)
        self._panel_layout.setContentsMargins(12, 10, 12, 10)
        self._panel_layout.setSpacing(8)
        scroll = widgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(widgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(qt.ScrollBarAlwaysOff)
        scroll.setSizePolicy(
            widgets.QSizePolicy.Policy.Expanding,
            widgets.QSizePolicy.Policy.Ignored,
        )
        inner = widgets.QWidget()
        self._expert_form = widgets.QVBoxLayout(inner)
        self._expert_form.setContentsMargins(0, 0, 0, 0)
        self._expert_form.setSpacing(4)
        scroll.setWidget(inner)
        self._panel_layout.addWidget(scroll, 1)
        self._expert_panel.hide()
        centre.addWidget(self._expert_panel)
        root.addLayout(centre, 1)
        self._history_scroll = widgets.QScrollArea()
        self._history_scroll.setObjectName("historyStrip")
        self._history_scroll.setWidgetResizable(True)
        self._history_scroll.setFrameShape(widgets.QFrame.Shape.NoFrame)
        self._history_scroll.setHorizontalScrollBarPolicy(qt.ScrollBarAsNeeded)
        self._history_scroll.setVerticalScrollBarPolicy(qt.ScrollBarAlwaysOff)
        self._history_scroll.setFixedHeight(104)
        history = widgets.QWidget()
        self._history = widgets.QHBoxLayout(history)
        self._history.setContentsMargins(0, 0, 0, 0)
        self._history.setSpacing(8)
        self._history_scroll.setWidget(history)
        root.addWidget(self._history_scroll)
        self._note = widgets.QLabel()
        self._note.setObjectName("simpleNote")
        self._note.hide()
        root.addWidget(self._note)
        self._install_keys()
        self.refresh()

    def _empty_page(self) -> object:
        widgets = self._widgets
        page = widgets.QWidget()
        layout = widgets.QVBoxLayout(page)
        hint = widgets.QLabel(tr("simple.empty"))
        hint.setObjectName("emptyHint")
        hint.setAlignment(self._qt.AlignCenter)
        hint.setWordWrap(True)
        layout.addStretch(1)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def _busy_page(self) -> object:
        widgets = self._widgets
        qt = self._qt
        page = widgets.QWidget()
        layout = widgets.QVBoxLayout(page)
        layout.addStretch(1)
        text = widgets.QLabel(tr("simple.progress"))
        text.setObjectName("progressText")
        text.setAlignment(qt.AlignCenter)
        layout.addWidget(text)
        bar_row = widgets.QHBoxLayout()
        bar_row.addStretch(1)
        bar = widgets.QProgressBar()
        bar.setObjectName("progress")
        bar.setRange(0, 100)
        bar.setValue(42)
        bar.setTextVisible(False)
        bar.setFixedWidth(280)
        bar_row.addWidget(bar)
        bar_row.addStretch(1)
        layout.addLayout(bar_row)
        cancel_row = widgets.QHBoxLayout()
        cancel_row.addStretch(1)
        cancel = widgets.QPushButton(tr("simple.cancel"))
        cancel.setObjectName("cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self._cancel)
        cancel_row.addWidget(cancel)
        cancel_row.addStretch(1)
        layout.addLayout(cancel_row)
        layout.addStretch(1)
        return page

    def _ready_page(self) -> object:
        widgets = self._widgets
        page = widgets.QWidget()
        layout = widgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        self._picture = widgets.QLabel()
        self._picture.setObjectName("result")
        self._picture.setAlignment(self._qt.AlignCenter)
        layout.addWidget(self._picture, 1)

        def _resized(_event: object) -> None:
            widgets.QLabel.resizeEvent(self._picture, _event)  # type: ignore[arg-type]
            self._rescale()

        self._picture.resizeEvent = _resized  # type: ignore[method-assign]
        return page

    def _fill_presets(self) -> None:
        widgets = self._widgets
        for row in self._layout.rows:
            line = widgets.QHBoxLayout()
            line.setSpacing(8)
            label = widgets.QLabel(tr(row.label_key))
            label.setObjectName(f"preset-{row.row_id}")
            label.setProperty("presetLabel", "true")
            label.setMinimumWidth(156)
            line.addWidget(label)
            group = widgets.QButtonGroup(self.root)
            group.setExclusive(True)
            for choice in row.choices:
                button = widgets.QPushButton(tr(choice.label_key))
                button.setObjectName(f"chip-{row.row_id}-{choice.value}")
                button.setProperty("chip", "true")
                button.setCheckable(True)
                button.setAutoDefault(False)
                button.setChecked(choice.value == self._layout.presets[row.row_id])
                button.clicked.connect(
                    lambda _checked=False, row_id=row.row_id, value=choice.value: self._choose(
                        row_id, value
                    )
                )
                group.addButton(button)
                line.addWidget(button)
                button.style().unpolish(button)
                button.style().polish(button)
            line.addStretch(1)
            self._preset_rows.addLayout(line)

    def _sync_presets(self) -> None:
        for row in self._layout.rows:
            selected = self._layout.presets[row.row_id]
            for choice in row.choices:
                button = self.root.findChild(
                    self._widgets.QPushButton,
                    f"chip-{row.row_id}-{choice.value}",
                )
                if button is not None:
                    button.setChecked(choice.value == selected)

    def _fill_history(self) -> None:
        widgets = self._widgets
        qt = self._qt
        _clear(self._history)
        group = widgets.QButtonGroup(self.root)
        group.setExclusive(True)
        self._thumb_group = group
        for item in self._layout.history:
            button = widgets.QPushButton()
            button.setObjectName(f"thumb-{item.item_id}")
            button.setProperty("thumb", "true")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setFixedSize(88, 88)
            button.setIcon(self._gui.QIcon(_placeholder(self._gui, item.item_id, 168, 168)))
            button.setIconSize(self._core.QSize(80, 80))
            button.setChecked(item.item_id == self._layout.selected)
            button.setContextMenuPolicy(qt.CustomContextMenu)
            button.clicked.connect(
                lambda _checked=False, item_id=item.item_id: self._select(item_id)
            )
            button.customContextMenuRequested.connect(
                lambda pos, item_id=item.item_id, source=button: self._popup(item_id, source, pos)
            )
            group.addButton(button)
            self._history.addWidget(button)
            button.style().unpolish(button)
            button.style().polish(button)
        self._history.addStretch(1)

    def _action_bar(self) -> object:
        widgets = self._widgets
        bar = widgets.QWidget()
        bar.setObjectName("actionBar")
        row = widgets.QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 12)
        row.setSpacing(8)
        self._more = self._action_button("moreLike", "simple.more", self._more_like)
        self._save = self._action_button("savePicture", "simple.menu.save", self._save_current)
        self._open = self._action_button(
            "openFolder",
            "simple.menu.open_folder",
            lambda: self._command("file.open_folder"),
        )
        self._use_button = self._action_button(
            "usePrompt",
            "simple.menu.use_prompt",
            self._use_current,
        )
        for button in (self._more, self._save, self._open, self._use_button):
            row.addWidget(button)
        row.addStretch(1)
        return bar

    def _action_button(self, name: str, label_key: str, handler: Callable[[], None]) -> object:
        button = self._widgets.QPushButton(tr(label_key))
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.setMinimumHeight(34)
        button.clicked.connect(handler)
        return button

    def _install_keys(self) -> None:
        core = self._core
        edit = self._prompt

        class _Keys(core.QObject):
            def eventFilter(_self: object, _watched: object, event: object) -> bool:
                kind = event.type()  # type: ignore[attr-defined]
                if kind != core.QEvent.Type.KeyPress:
                    return False
                key = event.key()  # type: ignore[attr-defined]
                if key not in {core.Qt.Key_Return, core.Qt.Key_Enter}:
                    return False
                modifiers = event.modifiers()  # type: ignore[attr-defined]
                if modifiers & core.Qt.KeyboardModifier.ShiftModifier:
                    return False
                self._plain_enter()
                return True

        keys = _Keys(edit)
        edit.installEventFilter(keys)
        edit.viewport().installEventFilter(keys)
        self._keys = keys

    def _plain_enter(self) -> None:
        if self._handling_enter:
            return
        self._handling_enter = True
        try:
            self._generate()
        finally:
            self._handling_enter = False

    def _generate(self) -> None:
        self._layout.set_prompt(self._prompt.toPlainText())  # type: ignore[attr-defined]
        if not self._layout.prompt.strip():
            self.set_note(tr("simple.error.prompt"))
            self.refresh()
            return
        if not self._layout.generate():
            self.refresh()
            return
        self.set_note("")
        self.refresh()
        if self._on_generate is not None:
            self._on_generate()

    def _more_like(self) -> None:
        if self._layout.item(self._layout.selected) is None:
            self.set_note(tr("simple.error.picture"))
            return
        if not self._layout.more_like():
            return
        self.set_note("")
        self.refresh(prompt=True)
        if self._on_generate is not None:
            self._on_generate()

    def _save_current(self) -> None:
        item_id = self._layout.selected
        if item_id is None or self._layout.item(item_id) is None:
            self.set_note(tr("simple.error.picture"))
            return
        self._save(item_id)

    def _use_current(self) -> None:
        if self._layout.use_prompt_again() is None:
            self.set_note(tr("simple.error.picture"))
            return
        self.set_note("")
        self.refresh(prompt=True)

    def _cancel(self) -> None:
        self._layout.cancel()
        self.refresh()

    def _choose(self, row_id: str, value: str) -> None:
        self._layout.choose(row_id, value)
        self._sync_presets()

    def _select(self, item_id: str) -> None:
        if self._layout.select(item_id) is None:
            return
        self.refresh()

    def _show_ready(self) -> None:
        item = self._layout.item(self._layout.selected)
        if item is None:
            self._stack.setCurrentIndex(0)  # type: ignore[attr-defined]
            return
        self._source = _placeholder(self._gui, item.item_id, 960, 640)
        self._stack.setCurrentIndex(2)  # type: ignore[attr-defined]
        self._rescale()

    def _rescale(self) -> None:
        if self._source is None or self._layout.phase is not Phase.READY:
            return
        size = self._picture.size()  # type: ignore[attr-defined]
        if size.width() < 2 or size.height() < 2:
            return
        scaled = self._source.scaled(  # type: ignore[attr-defined]
            size,
            self._qt.KeepAspectRatio,
            self._qt.SmoothTransformation,
        )
        self._picture.setPixmap(scaled)  # type: ignore[attr-defined]

    def _popup(self, item_id: str, source: object, pos: object) -> None:
        menu = self.history_menu(item_id)
        menu.exec(source.mapToGlobal(pos))  # type: ignore[attr-defined]

    def _save(self, item_id: str) -> None:
        if self._layout.item(item_id) is None:
            return
        self._command("file.save")

    def _copy(self, item_id: str) -> None:
        item = self._layout.item(item_id)
        if item is None:
            return
        self._gui.QGuiApplication.clipboard().setText(item.prompt)
        self.set_note(tr("simple.copied"))

    def _use(self, item_id: str) -> None:
        if self._layout.use_prompt_again(item_id) is None:
            return
        self.refresh(prompt=True)

    def _command(self, command_id: str) -> None:
        if self._on_command is not None:
            self._on_command(command_id)


def _fill_form(widgets: object, form: object, specs: tuple[SettingSpec, ...]) -> None:
    group = ""
    for spec in specs:
        if spec.group != group:
            group = spec.group
            heading = widgets.QLabel(tr(f"setting.group.{group}"))  # type: ignore[attr-defined]
            heading.setObjectName("settingGroup")
            form.addWidget(heading)  # type: ignore[attr-defined]
        control = _control(widgets, spec)
        if spec.type is not SettingType.BOOL:
            label = widgets.QLabel(tr(spec.label_key))  # type: ignore[attr-defined]
            label.setObjectName("panel")
            form.addWidget(label)  # type: ignore[attr-defined]
        form.addWidget(control)  # type: ignore[attr-defined]


def _control(widgets: object, spec: SettingSpec) -> object:
    kind = spec.type
    if kind is SettingType.BOOL:
        box = widgets.QCheckBox(tr(spec.label_key))  # type: ignore[attr-defined]
        box.setObjectName(spec.setting_id)
        box.setChecked(spec.default is True)
        control = box
    elif kind is SettingType.ENUM:
        combo = widgets.QComboBox()  # type: ignore[attr-defined]
        combo.setObjectName(spec.setting_id)
        selected = 0
        for index, choice in enumerate(spec.choices):
            combo.addItem(tr(choice.label_key), choice.value)
            if choice.value == spec.default:
                selected = index
        combo.setCurrentIndex(selected)
        control = combo
    elif kind is SettingType.INT:
        spin = widgets.QSpinBox()  # type: ignore[attr-defined]
        spin.setObjectName(spec.setting_id)
        spin.setRange(int(spec.minimum or 0), int(spec.maximum or 0))
        spin.setValue(int(spec.default) if isinstance(spec.default, int) else 0)
        control = spin
    elif kind is SettingType.FLOAT:
        spin = widgets.QDoubleSpinBox()  # type: ignore[attr-defined]
        spin.setObjectName(spec.setting_id)
        spin.setDecimals(1)
        spin.setRange(float(spec.minimum or 0), float(spec.maximum or 0))
        spin.setSingleStep(0.5)
        spin.setValue(float(spec.default) if isinstance(spec.default, int | float) else 0.0)
        control = spin
    elif kind is SettingType.PATH:
        edit = widgets.QLineEdit()  # type: ignore[attr-defined]
        edit.setObjectName(spec.setting_id)
        edit.setText(spec.default if isinstance(spec.default, str) else "")
        control = edit
    elif kind is SettingType.TEXT:
        edit = widgets.QPlainTextEdit()  # type: ignore[attr-defined]
        edit.setObjectName(spec.setting_id)
        edit.setPlainText(spec.default if isinstance(spec.default, str) else "")
        edit.setFixedHeight(64)
        control = edit
    else:
        assert_never(kind)
    if spec.expert:
        control.setToolTip(tr(spec.help_key))  # type: ignore[attr-defined]
    return control


def _clear(layout: object) -> None:
    while layout.count():  # type: ignore[attr-defined]
        item = layout.takeAt(0)  # type: ignore[attr-defined]
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


def _placeholder(gui: object, item_id: str, width: int, height: int) -> object:
    """Return a neutral gradient. It is not a photograph."""
    image = gui.QImage(  # type: ignore[attr-defined]
        max(width, 1),
        max(height, 1),
        gui.QImage.Format.Format_RGB32,  # type: ignore[attr-defined]
    )
    shift = sum(ord(char) for char in item_id) % 24
    start = gui.QColor(32 + shift, 40, 56)  # type: ignore[attr-defined]
    end = gui.QColor(168, 156 + shift // 3, 140)  # type: ignore[attr-defined]
    painter = gui.QPainter(image)  # type: ignore[attr-defined]
    gradient = gui.QLinearGradient(0, 0, width, height)  # type: ignore[attr-defined]
    gradient.setColorAt(0, start)
    gradient.setColorAt(1, end)
    painter.fillRect(image.rect(), gradient)
    painter.end()
    return gui.QPixmap.fromImage(image)  # type: ignore[attr-defined]


def _qt() -> tuple[object, object, object]:
    """Import PySide6 when a widget is constructed."""
    from PySide6 import QtCore, QtGui, QtWidgets

    return QtCore, QtGui, QtWidgets
