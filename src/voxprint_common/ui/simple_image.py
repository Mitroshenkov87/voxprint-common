"""Image-app pattern: a prompt, plain presets, the picture, and a history strip.

Canvas, Enlarge, and the Lucent helper use this screen. :class:`SimpleImageLayout`
is the headless description. :class:`SimpleImageWidget` is the Qt view. Importing
this module does not import PySide6. Expert mode adds a side panel beside the
picture. The dock stays a separate API and is not a control on this screen.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
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
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


_HISTORY_LIMIT = 50
_HISTORY_NAME = "image-history.json"


@dataclass(frozen=True)
class PresetChoice:
    """One chip in a preset row.

    ``label_key`` is a catalog key. The visible word is plain. ``group_key``
    gathers chips under a second plain label, such as Landscape. An unavailable
    choice stays on the row, greyed out, with a plain reason and an action id
    the app handles.
    """

    value: str
    label_key: str
    group_key: str = ""
    available: bool = True
    reason_key: str = ""
    action_label_key: str = ""
    action_id: str = ""

    def __post_init__(self) -> None:
        """An unavailable chip needs a reason and an action the app can run."""
        if self.available:
            return
        if not self.reason_key or not self.action_label_key or not self.action_id:
            raise ValueError("an unavailable choice needs a plain reason and an action")


@dataclass(frozen=True)
class PresetRow:
    """A row of segmented chips an app supplies.

    ``default`` is the chip selected when the screen opens. It has to be available.
    """

    row_id: str
    label_key: str
    choices: tuple[PresetChoice, ...]
    default: str

    def __post_init__(self) -> None:
        """Reject an empty row or a default that is not an available chip."""
        values = [choice.value for choice in self.choices]
        if not self.row_id or not self.label_key or not values or len(values) != len(set(values)):
            raise ValueError("a preset row needs an id and distinct choices")
        if self.default not in values:
            raise ValueError("a preset default must be one of the choices")
        chosen = next(choice for choice in self.choices if choice.value == self.default)
        if not chosen.available:
            raise ValueError("a preset default must be available")


@dataclass(frozen=True)
class GalleryItem:
    """One finished picture in the history strip.

    ``presets`` is the chip selection that produced it, in row order. ``seed``
    is the number that repeats it. The layout stores the style id and does not
    rewrite the prompt.
    """

    item_id: str
    prompt: str
    presets: tuple[tuple[str, str], ...]
    seed: int = 0


class SimpleImageLayout:
    """Headless state for the simple image screen. Qt is not imported.

    Apps supply preset rows. Size, style, and how many pictures are the
    reference rows from :func:`image_presets`. ``full_layout`` is a host API
    for a dock. It is off unless a caller asks, and only while Expert mode
    is on. The widget does not draw that switch.
    """

    def __init__(self, rows: Iterable[PresetRow] = (), *, state_dir: Path | None = None) -> None:
        """Copy ``rows`` and select each row's default.

        Args:
            rows: Preset rows. Ids must be unique.
            state_dir: The app's State directory. History is read and written
                there. None keeps the strip in memory.

        Raises:
            ValueError: Two rows share an id.
        """
        self._rows = tuple(rows)
        ids = [row.row_id for row in self._rows]
        if len(ids) != len(set(ids)):
            raise ValueError("preset row ids must be unique")
        self._state_dir = state_dir
        self.prompt = ""
        self.seed = 0
        self.phase = Phase.EMPTY
        self.step = 0
        self.total = 0
        self.stage_label = ""
        self.reason = ""
        self.presets = {row.row_id: row.default for row in self._rows}
        self.history: list[GalleryItem] = []
        self.selected: str | None = None
        self.full_layout = False
        self._load_history()

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
        choice = None if row is None else self._choice(row, value)
        if choice is None:
            raise ValueError(f"unknown preset {row_id}={value}")
        if not choice.available:
            raise ValueError(f"preset {row_id}={value} is unavailable")
        self.presets[row_id] = value

    def generate(self) -> bool:
        """Start a picture when the prompt has words and nothing is running.

        Returns:
            True when the phase changed to busy.
        """
        if self.phase is Phase.BUSY or not self.prompt.strip():
            return False
        self._clear_run()
        self.phase = Phase.BUSY
        return True

    def progress(self, step: int, total: int, stage_label: str) -> None:
        """Record how far the current picture has got.

        ``stage_label`` is already translated by the app.

        Args:
            step: Finished steps, from zero through ``total``.
            total: How many steps the picture takes.
            stage_label: Plain words for the current stage.

        Raises:
            ValueError: Nothing is running, or the numbers do not fit.
        """
        if self.phase is not Phase.BUSY:
            raise ValueError("progress needs a picture that is in progress")
        label = stage_label.strip()
        if total < 1 or step < 0 or step > total or not label:
            raise ValueError("progress needs a step inside the total and a stage label")
        self.step = step
        self.total = total
        self.stage_label = label

    def cancel(self) -> None:
        """Return to a clean idle state.

        Progress, a blocked reason, and an unavailable reason are cleared. A
        picture already in the strip stays up. An idle screen stays idle.
        """
        if self.phase in {Phase.EMPTY, Phase.READY}:
            return
        self._clear_run()
        self.phase = Phase.READY if self.selected else Phase.EMPTY

    def block(self, reason: str) -> None:
        """Show a plain reason in the result area.

        Args:
            reason: Already translated. It is shown in the picture area.

        Raises:
            ValueError: ``reason`` is blank.
        """
        self._show_reason(reason, Phase.BLOCKED)

    def unavailable(self, reason: str) -> None:
        """Show that a feature is unavailable, in the result area.

        Args:
            reason: Already translated.

        Raises:
            ValueError: ``reason`` is blank.
        """
        self._show_reason(reason, Phase.UNAVAILABLE)

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
            self.seed,
        )
        self.history.insert(0, item)
        del self.history[_HISTORY_LIMIT:]
        self.selected = item.item_id
        self._clear_run()
        self.phase = Phase.READY
        self._write_history()
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
        self.seed = item.seed
        for row_id, value in item.presets:
            row = self._row(row_id)
            choice = None if row is None else self._choice(row, value)
            if choice is not None and choice.available:
                self.presets[row_id] = value
        return item.prompt

    def delete(self, item_id: str) -> GalleryItem | None:
        """Remove one history item and write the strip back to the State dir.

        Args:
            item_id: History item id.

        Returns:
            The removed item, or None when the id is unknown.
        """
        item = self.item(item_id)
        if item is None:
            return None
        self.history = [saved for saved in self.history if saved.item_id != item_id]
        if self.selected == item_id:
            self.selected = self.history[0].item_id if self.history else None
            if self.phase is not Phase.BUSY:
                self._clear_run()
                self.phase = Phase.READY if self.selected else Phase.EMPTY
        self._write_history()
        return item

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

    def _choice(self, row: PresetRow, value: str) -> PresetChoice | None:
        return next((choice for choice in row.choices if choice.value == value), None)

    def _clear_run(self) -> None:
        self.step = 0
        self.total = 0
        self.stage_label = ""
        self.reason = ""

    def _show_reason(self, reason: str, phase: Phase) -> None:
        text = reason.strip()
        if not text:
            raise ValueError("the result needs a plain reason")
        self._clear_run()
        self.reason = text
        self.phase = phase

    def _load_history(self) -> None:
        if self._state_dir is None:
            return
        path = self._state_dir / _HISTORY_NAME
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError, UnicodeError, json.JSONDecodeError:
            return
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            return
        items = payload.get("items")
        if not isinstance(items, list):
            return
        loaded: list[GalleryItem] = []
        seen: set[str] = set()
        for raw in items:
            item = _history_item(raw)
            if item is None or item.item_id in seen:
                continue
            seen.add(item.item_id)
            loaded.append(item)
            if len(loaded) == _HISTORY_LIMIT:
                break
        self.history = loaded

    def _write_history(self) -> None:
        if self._state_dir is None:
            return
        self._state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 1,
            "items": [
                {
                    "id": item.item_id,
                    "prompt": item.prompt,
                    "presets": [list(pair) for pair in item.presets],
                    "seed": item.seed,
                }
                for item in self.history[:_HISTORY_LIMIT]
            ],
        }
        path = self._state_dir / _HISTORY_NAME
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _history_item(raw: object) -> GalleryItem | None:
    if not isinstance(raw, dict):
        return None
    item_id = raw.get("id")
    prompt = raw.get("prompt")
    presets = raw.get("presets")
    seed = raw.get("seed", 0)
    if not isinstance(item_id, str) or not item_id or not isinstance(prompt, str):
        return None
    if isinstance(seed, bool) or not isinstance(seed, int):
        return None
    if not isinstance(presets, list):
        return None
    pairs: list[tuple[str, str]] = []
    for pair in presets:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not isinstance(pair[0], str)
            or not isinstance(pair[1], str)
        ):
            return None
        pairs.append((pair[0], pair[1]))
    return GalleryItem(item_id, prompt, tuple(pairs), seed)


def _safe_id(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value)


def _chip_name(row_id: str, value: str) -> str:
    return f"chip-{row_id}-{_safe_id(value)}"


def _choice_groups(
    choices: tuple[PresetChoice, ...],
) -> tuple[tuple[str, tuple[PresetChoice, ...]], ...]:
    groups: list[tuple[str, list[PresetChoice]]] = []
    for choice in choices:
        if groups and groups[-1][0] == choice.group_key:
            groups[-1][1].append(choice)
        else:
            groups.append((choice.group_key, [choice]))
    return tuple((key, tuple(items)) for key, items in groups)


def image_presets(*, enlarge_installed: bool = False) -> tuple[PresetRow, ...]:
    """Return Canvas's size, output, style, and picture-count rows.

    The style value is only an id. The app owns any words added to the prompt.
    ``enlarge_installed`` marks 4K available. When it is false, 4K stays on the
    row, greyed out, with a reason and an install action.

    Args:
        enlarge_installed: True when Enlarge can make a 4K picture.
    """
    four_k = (
        PresetChoice("4k", "simple.output.4k")
        if enlarge_installed
        else PresetChoice(
            "4k",
            "simple.output.4k",
            available=False,
            reason_key="simple.output.4k.reason",
            action_label_key="simple.output.install",
            action_id="install.enlarge",
        )
    )
    return (
        PresetRow(
            "size",
            "simple.size",
            (
                PresetChoice("16:9", "simple.size.16-9", "simple.size.landscape"),
                PresetChoice("21:9", "simple.size.21-9", "simple.size.landscape"),
                PresetChoice("3:2", "simple.size.3-2", "simple.size.landscape"),
                PresetChoice("4:3", "simple.size.4-3", "simple.size.landscape"),
                PresetChoice("1:1", "simple.size.1-1", "simple.size.square"),
                PresetChoice("9:16", "simple.size.9-16", "simple.size.portrait"),
                PresetChoice("2:3", "simple.size.2-3", "simple.size.portrait"),
                PresetChoice("3:4", "simple.size.3-4", "simple.size.portrait"),
            ),
            "1:1",
        ),
        PresetRow(
            "output",
            "simple.output",
            (PresetChoice("standard", "simple.output.standard"), four_k),
            "standard",
        ),
        PresetRow(
            "style",
            "simple.style",
            (
                PresetChoice("photo", "simple.style.photo"),
                PresetChoice("cinematic", "simple.style.cinematic"),
                PresetChoice("portrait", "simple.style.portrait"),
                PresetChoice("illustration", "simple.style.illustration"),
                PresetChoice("anime", "simple.style.anime"),
                PresetChoice("3d", "simple.style.3d"),
                PresetChoice("painting", "simple.style.painting"),
                PresetChoice("sketch", "simple.style.sketch"),
            ),
            "photo",
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
    output size, style, and how many pictures stay on screen, with More like
    this, Save, Open folder, and Use prompt again. Improve prompt, when the
    app supplies it, replaces the text in that same box. Expert mode adds a
    side panel and leaves every other control where it was. The history strip
    shows a neutral gradient for each finished item. That gradient is a
    placeholder, not a photograph. A blocked result and an unavailable feature
    are plain sentences in the picture area. Manage models and an unavailable
    chip's action call ``on_command``. Canvas runs
    ``voxprint-mdl --gui --app canvas`` for Manage models. This widget does
    not start that program.
    """

    def __init__(
        self,
        layout: SimpleImageLayout,
        settings: Settings,
        *,
        on_generate: Callable[[], None] | None = None,
        on_command: Callable[[str], None] | None = None,
        on_improve: Callable[[str], str] | None = None,
        on_history: Callable[[str, str], None] | None = None,
        on_seed: Callable[[int], None] | None = None,
    ) -> None:
        """Build the screen.

        Args:
            layout: Headless state. The widget does not replace it.
            settings: Expert settings the app supplied. They go in the side panel.
            on_generate: Called after a prompt starts a picture.
            on_command: Called with a command id from Save, Open folder, Expert
                mode, Manage models, or an unavailable-chip action.
            on_improve: Replaces the prompt text in the same field. None hides
                Improve prompt and Revert.
            on_history: Called with an action id and a history item id. The
                actions are ``open``, ``show_folder``, ``copy``, and ``use_input``.
            on_seed: Called with the restored seed after Use prompt again.
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
        self._on_improve = on_improve
        self._on_history = on_history
        self._on_seed = on_seed
        self._previous_prompt: str | None = None
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
        phase = self._layout.phase
        if phase is Phase.EMPTY:
            self._stack.setCurrentIndex(0)  # type: ignore[attr-defined]
            return
        if phase is Phase.BUSY:
            self._paint_progress()
            self._stack.setCurrentIndex(1)  # type: ignore[attr-defined]
            return
        if phase is Phase.BLOCKED:
            self._blocked.setText(self._layout.reason)  # type: ignore[attr-defined]
            self._stack.setCurrentIndex(3)  # type: ignore[attr-defined]
            return
        if phase is Phase.UNAVAILABLE:
            self._feature.setText(self._layout.reason)  # type: ignore[attr-defined]
            self._stack.setCurrentIndex(4)  # type: ignore[attr-defined]
            return
        if phase is Phase.READY:
            self._show_ready()
            return
        assert_never(phase)

    def history_menu(self, item_id: str, *, confirming: bool = False) -> object:
        """Return the history menu for ``item_id``.

        Delete opens a second step in the same menu: the question, Delete, and
        Keep. Nothing is removed until that second Delete.

        Args:
            item_id: History item the menu acts on.
            confirming: True returns the confirm step.
        """
        widgets = self._widgets
        menu = widgets.QMenu(self.root)
        if confirming:
            question = menu.addAction(tr("simple.history.delete.confirm"))
            question.setEnabled(False)
            menu.addAction(tr("simple.history.delete"), lambda: self._delete(item_id))
            menu.addAction(tr("simple.history.keep"))
            return menu
        menu.addAction(tr("simple.history.open"), lambda: self._notify_history("open", item_id))
        menu.addAction(
            tr("simple.history.show_folder"),
            lambda: self._notify_history("show_folder", item_id),
        )
        menu.addAction(tr("simple.menu.copy"), lambda: self._copy(item_id))
        menu.addAction(tr("simple.menu.use_prompt"), lambda: self._use(item_id))
        menu.addAction(
            tr("simple.history.use_input"),
            lambda: self._notify_history("use_input", item_id),
        )
        menu.addAction(tr("simple.history.delete"), lambda: self._ask_delete(item_id))
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
        self._overflow = widgets.QPushButton(tr("shell.toolbar.overflow"))
        self._overflow.setObjectName("overflow")
        self._overflow.setAutoDefault(False)
        overflow = widgets.QMenu(self.root)
        overflow.addAction(tr("simple.menu.models"), lambda: self._command("models.manage"))
        self._overflow.setMenu(overflow)
        self._top.addWidget(self._overflow)
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
        prompt_col = widgets.QVBoxLayout()
        prompt_col.setSpacing(6)
        prompt_col.addWidget(self._prompt, 1)
        tools = widgets.QHBoxLayout()
        tools.setSpacing(8)
        self._improve = widgets.QPushButton(tr("simple.improve"))
        self._improve.setObjectName("improvePrompt")
        self._improve.setAutoDefault(False)
        self._improve.clicked.connect(self._improve_prompt)
        self._revert = widgets.QPushButton(tr("simple.revert"))
        self._revert.setObjectName("revertPrompt")
        self._revert.setAutoDefault(False)
        self._revert.setEnabled(False)
        self._revert.clicked.connect(self._revert_prompt)
        tools.addWidget(self._improve)
        tools.addWidget(self._revert)
        tools.addStretch(1)
        prompt_col.addLayout(tools)
        if self._on_improve is None:
            self._improve.hide()
            self._revert.hide()
        self._generate_button = widgets.QPushButton(tr("simple.generate"))
        self._generate_button.setObjectName("generate")
        self._generate_button.setAutoDefault(False)
        self._generate_button.setMinimumSize(168, 96)
        self._generate_button.clicked.connect(self._generate)
        prompt_row.addLayout(prompt_col, 1)
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
        self._blocked = self._reason_page("blockedText")
        self._feature = self._reason_page("featureText")
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
        text.setWordWrap(True)
        self._progress_text = text
        layout.addWidget(text)
        bar_row = widgets.QHBoxLayout()
        bar_row.addStretch(1)
        bar = widgets.QProgressBar()
        bar.setObjectName("progress")
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedWidth(280)
        self._progress_bar = bar
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

    def _reason_page(self, name: str) -> object:
        widgets = self._widgets
        page = widgets.QWidget()
        layout = widgets.QVBoxLayout(page)
        label = widgets.QLabel()
        label.setObjectName(name)
        label.setAlignment(self._qt.AlignCenter)
        label.setWordWrap(True)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addStretch(1)
        self._stack.addWidget(page)  # type: ignore[attr-defined]
        return label

    def _fill_presets(self) -> None:
        widgets = self._widgets
        for row in self._layout.rows:
            group = widgets.QButtonGroup(self.root)
            group.setExclusive(True)
            lines = _choice_groups(row.choices)
            for index, (group_key, choices) in enumerate(lines):
                line = widgets.QHBoxLayout()
                line.setSpacing(8)
                label = widgets.QLabel(tr(row.label_key) if index == 0 else "")
                if index == 0:
                    label.setObjectName(f"preset-{row.row_id}")
                label.setProperty("presetLabel", "true")
                label.setMinimumWidth(156)
                line.addWidget(label)
                if group_key:
                    group_label = widgets.QLabel(tr(group_key))
                    group_label.setProperty("presetLabel", "true")
                    group_label.setMinimumWidth(96)
                    line.addWidget(group_label)
                for choice in choices:
                    button = widgets.QPushButton(tr(choice.label_key))
                    button.setObjectName(_chip_name(row.row_id, choice.value))
                    button.setProperty("chip", "true")
                    button.setAutoDefault(False)
                    if choice.available:
                        button.setCheckable(True)
                        button.setChecked(choice.value == self._layout.presets[row.row_id])
                        button.clicked.connect(
                            lambda _checked=False, row_id=row.row_id, value=choice.value: (
                                self._choose(row_id, value)
                            )
                        )
                        group.addButton(button)
                    else:
                        button.setEnabled(False)
                    line.addWidget(button)
                    button.style().unpolish(button)
                    button.style().polish(button)
                    if choice.available:
                        continue
                    reason = widgets.QLabel(tr(choice.reason_key))
                    reason.setObjectName(f"reason-{row.row_id}-{_safe_id(choice.value)}")
                    reason.setProperty("presetLabel", "true")
                    line.addWidget(reason)
                    action = widgets.QPushButton(tr(choice.action_label_key))
                    action.setObjectName(f"unavailable-{row.row_id}-{_safe_id(choice.value)}")
                    action.setAutoDefault(False)
                    action.clicked.connect(
                        lambda _checked=False, command=choice.action_id: self._command(command)
                    )
                    line.addWidget(action)
                line.addStretch(1)
                self._preset_rows.addLayout(line)

    def _sync_presets(self) -> None:
        for row in self._layout.rows:
            selected = self._layout.presets[row.row_id]
            for choice in row.choices:
                button = self.root.findChild(
                    self._widgets.QPushButton,
                    _chip_name(row.row_id, choice.value),
                )
                if button is not None and button.isCheckable():
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
        self._restore_seed()
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
        self._restore_seed()
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

    def _paint_progress(self) -> None:
        label = self._layout.stage_label or tr("simple.progress")
        self._progress_text.setText(label)  # type: ignore[attr-defined]
        total = self._layout.total
        if total > 0:
            self._progress_bar.setRange(0, total)  # type: ignore[attr-defined]
            self._progress_bar.setValue(self._layout.step)  # type: ignore[attr-defined]
            return
        self._progress_bar.setRange(0, 0)  # type: ignore[attr-defined]

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
        self._notify_history("copy", item_id)

    def _use(self, item_id: str) -> None:
        if self._layout.use_prompt_again(item_id) is None:
            return
        self._restore_seed()
        self.refresh(prompt=True)

    def _improve_prompt(self) -> None:
        if self._on_improve is None:
            return
        current = self._prompt.toPlainText()  # type: ignore[attr-defined]
        if not current.strip():
            self.set_note(tr("simple.error.prompt"))
            return
        improved = self._on_improve(current)
        if improved == current:
            return
        self._previous_prompt = current
        self._layout.set_prompt(improved)
        self._prompt.setPlainText(improved)  # type: ignore[attr-defined]
        self._revert.setEnabled(True)  # type: ignore[attr-defined]
        self.set_note("")

    def _revert_prompt(self) -> None:
        if self._previous_prompt is None:
            return
        text = self._previous_prompt
        self._previous_prompt = None
        self._layout.set_prompt(text)
        self._prompt.setPlainText(text)  # type: ignore[attr-defined]
        self._revert.setEnabled(False)  # type: ignore[attr-defined]

    def _restore_seed(self) -> None:
        if self._on_seed is not None:
            self._on_seed(self._layout.seed)

    def _notify_history(self, action: str, item_id: str) -> None:
        if self._layout.item(item_id) is None:
            return
        notes = {
            "open": "simple.history.opened",
            "show_folder": "simple.history.shown",
            "copy": "simple.copied",
            "use_input": "simple.history.using",
        }
        key = notes.get(action)
        if key is not None:
            self.set_note(tr(key))
        if self._on_history is not None:
            self._on_history(action, item_id)

    def _ask_delete(self, item_id: str) -> None:
        menu = self.history_menu(item_id, confirming=True)
        menu.exec(self._gui.QCursor.pos())  # type: ignore[attr-defined]

    def _delete(self, item_id: str) -> None:
        if self._layout.delete(item_id) is None:
            return
        self.set_note("")
        self.refresh()

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
