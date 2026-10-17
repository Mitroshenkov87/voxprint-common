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

import contextlib
from collections.abc import Mapping
from html import escape
from pathlib import Path
from typing import assert_never

from voxprint_common.i18n import set_language, tr
from voxprint_common.paths import state_dir as suite_state_dir
from voxprint_common.suite_settings import set_expert_mode, set_window_geometry, window_geometry
from voxprint_common.ui.commands import CommandSpec, Registry, reference_commands
from voxprint_common.ui.dock import (
    Activate,
    Close,
    DockError,
    DockHistory,
    Float,
    Layout,
    PanelSpec,
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
    visible_layout,
)
from voxprint_common.ui.expert import ExpertMode, active_contexts
from voxprint_common.ui.keymap import (
    KeyMap,
    display_chord,
    effective,
    host_platform,
    load_overrides,
)
from voxprint_common.ui.palette import score_text
from voxprint_common.ui.safety import ReviewedOutput
from voxprint_common.ui.sections import audiobook_sections
from voxprint_common.ui.settings import SettingSpec, SettingType, reference_settings
from voxprint_common.ui.simple_image import (
    Phase,
    SimpleImageLayout,
    SimpleImageWidget,
)
from voxprint_common.ui.theme import (
    PRIMARY,
    TEXT,
    TEXT_MUTED,
    TEXT_ON_ACCENT,
    apply_shell_palette,
    build_shell_style,
)
from voxprint_common.ui.variants import (
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
    settings_for,
    state_names,
    surface_for,
    variant_presets,
)

__all__ = [
    "ADOPTION",
    "ReferenceShell",
    "grab_shell",
    "reference_layout",
    "reference_panels",
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

    Settings sits beside history. Advanced is an expert panel in that same
    group. The centre panel is protected. Side splits around it are allowed.
    Hiding Advanced does not remove it from the stored layout.
    """
    left = Tabs(["prompt", "layers"], 0)
    centre = Tabs([_CENTRE], 0)
    right = Tabs(["history", "settings", "advanced"], 0)
    inner = Split(SplitAxis.HORIZONTAL, SplitSize.fixed_second(260.0), centre, right, 140.0)
    root = Split(SplitAxis.HORIZONTAL, SplitSize.ratio(0.24), left, inner, 160.0)
    return Layout(root)


def reference_panels() -> tuple[PanelSpec, ...]:
    """Return the panels the reference shell knows, including expert ones."""
    return (
        PanelSpec("prompt", "shell.panel.prompt"),
        PanelSpec("layers", "shell.panel.layers"),
        PanelSpec("canvas", "shell.panel.canvas"),
        PanelSpec("history", "shell.panel.history"),
        PanelSpec("settings", "shell.panel.settings"),
        PanelSpec("advanced", "shell.panel.advanced", expert=True),
    )


_MODIFIED: dict[str, tuple[str, object]] = {
    "canvas": ("shell.steps", 24),
    "lucent": ("shell.steps", 24),
    "enlarge": ("shell.steps", 24),
    "audiobook-builder": ("audio.loudness", -14.0),
    "movie-dubber": ("audio.loudness", -14.0),
    "clarity": ("video.crf", 20),
    "plotweaver": ("text.temperature", 0.9),
    "model-forge": ("tool.quant", "int4"),
    "viewer": ("viewer.background", "grey"),
}


def grab_shell(
    path: Path,
    *,
    language: str,
    show_palette: bool = False,
    glass: bool = False,
    palette_query: str = "",
    hot_split: bool = False,
    reveal_close: bool = False,
    show_settings: bool = False,
    expert: bool = False,
    full_layout: bool = False,
    result: bool = False,
    modified: bool = False,
    prompt: str = "A quiet hill at dusk",
    app_id: str = "canvas",
    sections: bool = False,
    section_id: str = "",
    wizard_id: str = "",
    wizard_running: bool = False,
) -> None:
    """Paint the reference shell and save a PNG.

    Args:
        path: Destination PNG. Parent directories are created.
        language: Catalog language, for example ``en`` or ``ru``.
        show_palette: Open the command palette before the grab.
        glass: Use the translucent window background. Offscreen grabs use
            the plain background unless this is true.
        palette_query: Filter typed into the palette after it opens.
        hot_split: Paint the root divider in the drag colour.
        reveal_close: Show the close control on the first closable tab.
        show_settings: Select the Settings panel before the grab.
        expert: Turn Expert mode on before the grab.
        full_layout: Show the dock. This also turns Expert mode on.
        result: Show four neutral gradients as finished pictures.
        modified: Hand-edit steps so Quality shows a changed chip and Reset.
        prompt: Words placed in the input when ``result`` is true.
        app_id: Which app surface to draw. ``canvas`` is the image screen.
        sections: Draw the Audiobook Builder section demo.
        section_id: Section to select when ``sections`` is true.
        wizard_id: Wizard to open when ``sections`` is true.
        wizard_running: Advance that wizard to its running step and show progress.
    """
    set_language(language)
    set_expert_mode(expert or full_layout)
    _qt()
    widgets = _widgets()
    app = widgets.QApplication.instance() or widgets.QApplication([])
    shell = ReferenceShell(
        glass=glass,
        full_layout=full_layout,
        state_dir=path.parent / "state" / path.stem,
        app=app_id,
        sections=sections,
    )
    shell.resize(1200, 900)
    shell.show()
    app.processEvents()
    if show_settings:
        if shell.full_layout:
            shell.show_panel("settings")
        else:
            shell.run("view.settings")
    if result and not shell.full_layout:
        shell.show_result(prompt, pictures=4)
    if modified and not shell.full_layout:
        key, value = _MODIFIED.get(app_id, ("shell.steps", 24))
        shell.image.set_value(key, value)
        shell.simple.refresh()
    shell.resize(1200, 900)
    app.processEvents()
    if show_palette:
        shell.open_palette()
        if palette_query:
            shell._query.setText(palette_query)
        app.processEvents()
    if hot_split:
        shell._set_dragging(shell._root_splitter(), True)
        app.processEvents()
    if reveal_close:
        shell._reveal_one_close()
        app.processEvents()
    if sections and shell.image.sections is not None:
        if prompt:
            shell.simple.set_prompt_text(prompt)
        deck = shell.image.sections
        if wizard_id:
            deck.open_wizard(wizard_id)
            if wizard_running:
                shell.image.set_value("train.sample", "sample.wav")
                deck.advance()
                shell.image.set_value("train.name", "Anya")
                deck.advance()
                deck.note_progress(2, 5, tr("wizard.train.running"))
        elif section_id:
            deck.select(section_id)
        shell.simple.refresh()
        app.processEvents()
    path.parent.mkdir(parents=True, exist_ok=True)
    shell.grab().save(str(path))
    shell.close()
    app.processEvents()


class _ReferenceReview:
    """Review hook for the reference shell's placeholder pictures."""

    def review_prompt(self, prompt: str) -> str | None:
        """Allow the placeholder prompt.

        Args:
            prompt: The words in the prompt box.
        """
        return None

    def review_result(self, prompt: str, item_id: str, output: ReviewedOutput) -> str | None:
        """Allow the placeholder picture.

        Args:
            prompt: The words that produced the picture.
            item_id: The id assigned to the picture.
            output: The picture, as a path or as bytes and a mime type.
        """
        del output
        return None


def _checkpoint_blob(wizard_id: str, step_id: str) -> dict[str, str]:
    """Return the app blob for this step.

    The shell writes the blob into the checkpoint and does not look inside it.

    Args:
        wizard_id: Open wizard.
        step_id: Open step.
    """
    return {"wizard": wizard_id, "step": step_id}


def _restore_checkpoint(wizard_id: str, step_id: str, blob: Mapping[str, object]) -> None:
    """Hand a stored blob back to the app.

    There is no training step here. The app owns whatever the blob means.

    Args:
        wizard_id: Wizard that was open.
        step_id: Step that was open.
        blob: The object ``capture`` returned.
    """
    del wizard_id, step_id, blob


class ReferenceShell:
    """The reference window. Construction imports PySide6."""

    def __init__(
        self,
        *,
        glass: bool = False,
        full_layout: bool = False,
        state_dir: Path | None = None,
        app: str = "canvas",
        sections: bool = False,
    ) -> None:
        """Build the reference window.

        Args:
            glass: Use the translucent window background.
            full_layout: Show the dock. This also turns Expert mode on.
            state_dir: Directory for the history. None uses the app directory
                under the suite State dir.
            app: Which simple screen to show. ``canvas`` is the image screen.
            sections: Use the Audiobook Builder section demo on that audio screen.
        """
        if sections and app != "audiobook-builder":
            raise ValueError("sections are the Audiobook Builder demo")
        qt, gui, widgets = _qt()
        self._qt = qt
        self._gui = gui
        self._widgets = widgets
        self.history = DockHistory(reference_layout())
        self.expert = ExpertMode()
        self.surface = surface_for(app)
        history_dir = suite_state_dir() / self.surface.app_id if state_dir is None else state_dir
        self._section_set = audiobook_sections() if sections else None
        self.settings = settings_for(self.surface)
        if self._section_set is not None:
            self.settings = self._section_set.settings
        settings_file, history_file = state_names(self.surface)
        self.image = SimpleImageLayout(
            presets=variant_presets(self.surface, self.settings),
            settings=self.settings,
            state_dir=history_dir,
            gate=_ReferenceReview(),
            state_name=settings_file,
            history_name=history_file,
            section_set=self._section_set,
            capture=_checkpoint_blob,
            restore=_restore_checkpoint,
            variant=self.surface.variant,
        )
        self.prompt_policy = self.image.prompt_policy()
        self._picture_count = 0
        self._stop_expert = lambda: None
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
        self._watches: list[object] = []
        self._fitting = False
        self._applying_splits: set[int] = set()
        self._press_sizes: dict[tuple[bool, ...], tuple[int, ...]] = {}
        self._toolbar_available: int | None = None
        self._palette_query = ""
        self._chrome_ready = False
        self._geometry_ready = False
        self._fitting_geometry = False
        self._status_time = False
        self.widget = widgets.QWidget()
        self.widget.setObjectName("shell")
        self.widget.setFocusPolicy(qt.StrongFocus)
        self.widget.setAttribute(qt.WA_StyledBackground, True)
        self.widget.setStyleSheet(build_shell_style(glass))
        apply_shell_palette(widgets.QApplication.instance() or self.widget)
        apply_shell_palette(self.widget)
        self.widget.setWindowTitle(tr("shell.window.title"))
        self._status = widgets.QLabel(tr("shell.status.ready"))
        self._status.setObjectName("status")
        self._expert_chip = widgets.QLabel(tr("shell.status.expert"))
        self._expert_chip.setObjectName("expertChip")
        self._expert_chip.setVisible(self.expert.enabled())
        self._toolbar = widgets.QWidget()
        self._dock = widgets.QWidget()
        self._palette = widgets.QFrame(self.widget)
        self._palette.setObjectName("float")
        self._palette.hide()
        self._query = widgets.QLineEdit()
        self._query.setObjectName("paletteQuery")
        self._query.setPlaceholderText(tr("shell.palette.placeholder"))
        self._hits = widgets.QListWidget()
        self._hits.setObjectName("paletteHits")
        self._hits.setSpacing(4)
        self._hits.setFocusPolicy(qt.NoFocus)
        self._hits.setHorizontalScrollBarPolicy(qt.ScrollBarAlwaysOff)
        self._hits.currentRowChanged.connect(self._sync_palette_selection)
        self._empty = widgets.QLabel(tr("shell.palette.empty"))
        self._empty.setObjectName("panel")
        self._build_chrome()
        self._build_palette()
        self._install_shortcuts()
        self._install_resize_watch()
        self._stop_expert = self.expert.subscribe(self._on_expert)
        if full_layout:
            self.expert.set_enabled(True)
            self.image.request_full_layout(True, expert=self.expert.enabled())
            self._fill_menus()
            self._sync_shortcuts()
            self._apply_surface()
        self._chrome_ready = True
        self.rebuild()
        self._apply_geometry()

    def _apply_geometry(self) -> None:
        """Use 1200 by 900 the first time, then the saved rectangle.

        The minimum is the variant's. A saved edge that is smaller is raised
        to that minimum. The saved file is left unchanged until the window
        moves or is resized.
        """
        minimum_width = self.surface.min_width
        minimum_height = self.surface.min_height
        self._fitting_geometry = True
        self.widget.setMinimumSize(minimum_width, minimum_height)
        stored = window_geometry()
        if stored is None:
            self.widget.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        else:
            self.widget.resize(
                max(minimum_width, stored["width"]),
                max(minimum_height, stored["height"]),
            )
            self.widget.move(stored["x"], stored["y"])
        self._fitting_geometry = False
        self._geometry_ready = True

    def _save_geometry(self) -> None:
        widget = self.widget
        set_window_geometry(widget.width(), widget.height(), widget.x(), widget.y())

    def _sync_time_left(self) -> None:
        """Show remaining time on the status row only when a job supplied it."""
        seconds = self.image.eta_seconds if self.image.phase is Phase.BUSY else None
        if seconds is None:
            if self._status_time:
                self._status.setText(tr("shell.status.ready"))
                self._status_time = False
            return
        self._status_time = True
        if seconds >= 60:
            minutes = max(1, round(seconds / 60))
            self._status.setText(tr("simple.progress.eta_minutes", minutes=minutes))
            return
        self._status.setText(tr("simple.progress.eta_seconds", seconds=seconds))

    def resize(self, width: int, height: int) -> None:
        """Resize the window.

        Args:
            width: Width in pixels.
            height: Height in pixels.
        """
        self.widget.resize(width, height)
        self._fill_toolbar()

    def show(self) -> None:
        """Show the window."""
        self.widget.show()

    def close(self) -> None:
        """Hide and close the window."""
        self._stop_expert()
        app = self._widgets.QApplication.instance()
        if app is not None:
            with contextlib.suppress(RuntimeError):
                app.focusChanged.disconnect(self._sync_shortcuts)
        self._shortcuts.clear()
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
            self.simple.set_note(tr("shell.status.saved"))
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
        if command_id == "view.toggle_expert_mode":
            self.expert.toggle()
            return
        if command_id == "file.open_folder":
            self._status.setText(tr("simple.menu.open_folder"))
            self.simple.set_note(tr("simple.menu.open_folder"))
            return
        if command_id == "models.manage":
            self._status.setText(tr("simple.menu.models"))
            self.simple.set_note(tr("simple.menu.models"))
            return
        if command_id == "install.enlarge":
            self._status.setText(tr("simple.output.install"))
            self.simple.set_note(tr("simple.output.install"))
            return
        if command_id == "view.settings":
            if self.full_layout:
                self.show_panel("settings")
                return
            self._status.setText(tr("simple.menu.settings"))
            self.simple.set_note(tr("simple.menu.settings"))
            return

    def show_panel(self, panel: str) -> None:
        """Select ``panel`` and paint the dock again.

        Args:
            panel: Panel id in the stored layout.
        """
        self.active = panel
        try:
            self.history.layout.apply(Activate(panel))
        except DockError:
            return
        self.rebuild()
        self._sync_shortcuts()

    @property
    def full_layout(self) -> bool:
        """Return whether the dock is on screen. It stays off in simple mode."""
        return self.image.dock_visible(expert=self.expert.enabled())

    def set_full_layout(self, enabled: bool) -> None:
        """Show the dock, or return to the simple screen.

        The dock is ignored unless Expert mode is on. Turning Expert mode off
        clears this choice.

        Args:
            enabled: True asks for the dock.
        """
        self.image.request_full_layout(enabled, expert=self.expert.enabled())
        self._fill_menus()
        self._apply_surface()
        if self.full_layout:
            self.rebuild()

    def show_result(self, prompt: str, *, pictures: int | None = None) -> None:
        """Finish pictures and show a neutral gradient for each one.

        Args:
            prompt: Words placed in the prompt box.
            pictures: How many to finish. None uses the count preset.
        """
        if pictures is not None and "count" in self.image.presets:
            self.image.choose("count", str(pictures))
        self.prompt_policy = self.image.prompt_policy()
        self.simple.set_prompt_text(prompt)
        if self.image.phase is Phase.BUSY:
            self.image.cancel()
        if not self.image.generate():
            return
        extra = pictures if pictures is not None and "count" not in self.image.presets else None
        self._finish_batch("preview", total=extra)

    def _complete_picture(self, _settings: Mapping[str, object]) -> None:
        self.prompt_policy = self.image.prompt_policy()
        core = _qt_core()
        core.QTimer.singleShot(0, lambda: self._finish_batch("picture"))

    def _improve_prompt(self, text: str) -> str:
        """Collapse repeated spaces in the same prompt field.

        Args:
            text: The words currently in the prompt box.
        """
        return " ".join(text.split())

    def _on_history(self, action: str, _item_id: str) -> None:
        """Record a history action on the status line.

        Args:
            action: ``open``, ``show_folder``, ``copy``, or ``use_input``.
            _item_id: History item id. The widget already applied the action.
        """
        notes = {
            "open": "simple.history.opened",
            "show_folder": "simple.history.shown",
            "copy": "simple.copied",
            "use_input": "simple.history.using",
        }
        key = notes.get(action)
        if key is None:
            return
        self._status.setText(tr(key))

    def _apply_seed(self, seed: int) -> None:
        """Put a restored seed into the expert field.

        Args:
            seed: The number copied from a history item.
        """
        self.image.seed = seed
        self._write_seed()

    def _read_seed(self) -> None:
        spin = self.find("QSpinBox", "shell.seed")
        if spin is not None and spin.isVisible():  # type: ignore[attr-defined]
            self.image.seed = int(spin.value())  # type: ignore[attr-defined]

    def _write_seed(self) -> None:
        spin = self.find("QSpinBox", "shell.seed")
        if spin is not None:
            spin.setValue(int(self.image.seed))  # type: ignore[attr-defined]

    def _finish_batch(self, prefix: str, *, total: int | None = None) -> None:
        if self.image.phase is not Phase.BUSY:
            return
        self._read_seed()
        count = self.image.batch_size() if total is None else total
        total = count
        for index in range(total):
            if index > 0 and not self.image.generate():
                break
            self._picture_count += 1
            if (
                self.image.finish(
                    f"{prefix}-{self._picture_count}",
                    ReviewedOutput(data=b"placeholder", mime="image/png"),
                )
                is None
            ):
                break
        self.simple.refresh(prompt=True)

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
        view = self._view()
        root = view.root
        if root is not None:
            layout.addWidget(self._mount(root, ()))
        self._mount_floats(view)
        self._fill_toolbar()
        if self._palette.isVisible():
            self._palette.raise_()
        self._building = False

    def _build_chrome(self) -> None:
        widgets = self._widgets
        root = widgets.QVBoxLayout(self.widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.simple = SimpleImageWidget(
            self.image,
            self.settings,
            on_generate=self._complete_picture,
            on_command=self.run,
            on_improve=self._improve_prompt,
            on_history=self._on_history,
            on_seed=self._apply_seed,
            on_refresh=self._sync_time_left,
            surface=self.surface,
        )
        root.addWidget(self.simple.root, 1)
        self._dock_host = widgets.QWidget()
        self._dock_host.setObjectName("dockHost")
        host = widgets.QVBoxLayout(self._dock_host)
        host.setContentsMargins(12, 10, 12, 8)
        host.setSpacing(8)
        title = widgets.QLabel(tr("shell.window.title"))
        title.setObjectName("titlebar")
        host.addWidget(title)
        menu_bar = widgets.QMenuBar()
        file_menu = menu_bar.addMenu(tr("shell.menu.file"))
        edit_menu = menu_bar.addMenu(tr("shell.menu.edit"))
        view_menu = menu_bar.addMenu(tr("shell.menu.view"))
        host.addWidget(menu_bar)
        host.addWidget(self._toolbar)
        host.addWidget(self._dock, 1)
        status_row = widgets.QWidget()
        self._status_layout = widgets.QHBoxLayout(status_row)
        self._status_layout.setContentsMargins(0, 0, 0, 0)
        self._status_layout.setSpacing(8)
        self._status_layout.addWidget(self._status, 1)
        host.addWidget(status_row)
        root.addWidget(self._dock_host, 1)
        self._file_menu = file_menu
        self._edit_menu = edit_menu
        self._view_menu = view_menu
        self._fill_menus()
        self._apply_surface()

    def _apply_surface(self) -> None:
        dock = self.full_layout
        self._dock_host.setVisible(dock)
        self.simple.root.setVisible(not dock)
        self.simple.set_expert(self.expert.enabled() and not dock)
        if dock:
            chip = self._expert_chip
            chip.setMinimumSize(0, 0)
            chip.setMaximumSize(16777215, 16777215)
            if self._status_layout.indexOf(chip) < 0:
                self._status_layout.addWidget(chip)
        else:
            self.simple.place_chip(self._expert_chip)
        self._expert_chip.setText(tr("shell.status.expert"))
        self._expert_chip.setVisible(self.expert.enabled())
        self._write_seed()

    def _fill_menus(self) -> None:
        self._file_menu.clear()
        self._edit_menu.clear()
        self._view_menu.clear()
        for spec in self.registry.visible(expert=self.expert.enabled()):
            if spec.command_id.startswith("file."):
                self._add_action(self._file_menu, spec)
            elif spec.command_id.startswith("edit."):
                self._add_action(self._edit_menu, spec)
            elif spec.command_id.startswith("view."):
                self._add_action(self._view_menu, spec)

    def _add_action(self, menu: object, spec: CommandSpec) -> None:
        label = tr(spec.title_key)
        if spec.chord:
            label = f"{label}\t{display_chord(spec.chord)}"
        action = menu.addAction(label)  # type: ignore[attr-defined]
        command_id = spec.command_id
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

    def _contexts(self) -> tuple[str, ...]:
        focused = ("canvas",) if self.active == _CENTRE else ()
        return active_contexts(*focused, expert=self.expert.enabled())

    def _on_expert(self, enabled: bool) -> None:
        if not enabled:
            self.image.request_full_layout(False, expert=False)
        if not self._chrome_ready:
            return
        self._fill_menus()
        if self.palette_open():
            self._refill_palette(self._palette_query)
        self._sync_shortcuts()
        self.rebuild()
        self._apply_surface()

    def _view(self) -> Layout[str]:
        return visible_layout(self.history.layout, reference_panels(), expert=self.expert.enabled())

    def _sync_shortcuts(self, *_widgets: object) -> None:
        text = self._text_focus()
        active = self._contexts()
        expert_on = self.expert.enabled()
        for shortcut, command_id, when in self._shortcuts:
            spec = self.registry.get(command_id)
            allowed = spec is not None and self.registry.enabled(spec)
            if spec is not None and spec.expert and not expert_on:
                allowed = False
            if not allowed:
                shortcut.setEnabled(False)  # type: ignore[attr-defined]
                continue
            if text:
                shortcut.setEnabled(when == "text")  # type: ignore[attr-defined]
            else:
                shortcut.setEnabled(when != "text" and (when == "global" or when in active))  # type: ignore[attr-defined]

    def _refill_palette(self, query: str) -> None:
        self._palette_query = query
        for index in range(self._hits.count()):
            item = self._hits.item(index)
            widget = self._hits.itemWidget(item)
            if widget is None:
                continue
            self._hits.removeItemWidget(item)
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        self._hits.clear()
        scored: list[tuple[int, str, str]] = []
        for spec in self.registry.visible(expert=self.expert.enabled()):
            if not self.registry.enabled(spec):
                continue
            label = tr(spec.title_key)
            value = score_text(query, label, spec.command_id)
            if value is None:
                continue
            scored.append((value, label, spec.command_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        for _score, label, command_id in scored:
            item = self._widgets.QListWidgetItem()
            item.setData(self._qt.UserRole, command_id)
            item.setData(self._qt.UserRole + 1, label)
            row = self._palette_row(command_id, label, query, selected=False)
            item.setSizeHint(row.sizeHint())
            self._hits.addItem(item)
            self._hits.setItemWidget(item, row)
        self._empty.setVisible(not scored)
        if scored:
            self._hits.setCurrentRow(0)
        self._sync_palette_selection(self._hits.currentRow())

    def _run_palette(self) -> None:
        current = self._hits.currentItem()
        if current is None:
            return
        command_id = current.data(self._qt.UserRole)
        self._palette.hide()
        if isinstance(command_id, str):
            self.run(command_id)

    def _fill_toolbar(self) -> None:
        if self._fitting:
            return
        self._fitting = True
        try:
            self._fill_toolbar_now()
        finally:
            self._fitting = False

    def _fill_toolbar_now(self) -> None:
        widgets = self._widgets
        layout = self._toolbar.layout()
        if layout is None:
            layout = widgets.QHBoxLayout(self._toolbar)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        made: list[tuple[str, object, int]] = []
        for command_id, _size in _TOOLBAR:
            button = self._toolbar_button(_command_label(command_id), command_id == "file.save")
            made.append((command_id, button, int(button.minimumWidth())))
        more = self._toolbar_button(tr("shell.toolbar.overflow"), False)
        menu = widgets.QMenu(more)
        more.setMenu(menu)
        more.ensurePolished()
        more.setMinimumWidth(max(int(more.minimumWidth()), int(more.sizeHint().width())))
        if self._toolbar_available is None:
            available = self.widget.width() - 24
        else:
            available = self._toolbar_available
        if available < 120:
            available = 1000
        fitted = fit_toolbar(
            [float(width) for _command, _button, width in made],
            float(available),
            8.0,
            float(more.minimumWidth()),
        )
        shown = {index for index, _size in fitted.shown} if fitted is not None else set()
        overflow = list(fitted.overflow) if fitted is not None else list(range(len(made)))
        for index, (command_id, button, _width) in enumerate(made):
            if index not in shown:
                button.hide()  # type: ignore[attr-defined]
                button.setParent(None)  # type: ignore[attr-defined]
                button.deleteLater()  # type: ignore[attr-defined]
                continue
            button.clicked.connect(lambda _checked=False, command=command_id: self.run(command))  # type: ignore[attr-defined]
            button.show()  # type: ignore[attr-defined]
            layout.addWidget(button)
        if overflow:
            for index in overflow:
                command_id = made[index][0]
                action = menu.addAction(_command_label(command_id))
                action.triggered.connect(
                    lambda _checked=False, command=command_id: self.run(command)
                )
            more.show()
            layout.addWidget(more)
        else:
            more.hide()
            more.setParent(None)
            more.deleteLater()
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
            self._arm_tabs(tabs, panels)
            return tabs
        if isinstance(node, Split):
            vertical = node.axis is SplitAxis.VERTICAL
            splitter = widgets.QSplitter(self._qt.Vertical if vertical else self._qt.Horizontal)
            splitter.setObjectName("rootSplit" if path == () else "dockSplit")
            splitter.setHandleWidth(9)
            splitter.setChildrenCollapsible(False)
            splitter.addWidget(self._mount(node.first, (*path, False)))
            splitter.addWidget(self._mount(node.second, (*path, True)))
            self._paint_split(splitter, vertical, path)
            self._apply_split_geometry(splitter, path)
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

    def _mount_floats(self, view: Layout[str]) -> None:
        self._clear_floats()
        for group in view.floating:
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

    def _commit_split(self, path: tuple[bool, ...], splitter: object) -> None:
        if self._building or self._applying_splits:
            return
        sizes = tuple(int(value) for value in splitter.sizes())  # type: ignore[attr-defined]
        if self._press_sizes.get(path) == sizes:
            return
        total = sum(sizes)
        if total <= 0 or len(sizes) < 2:
            return
        node = _split_at(self.history.layout.root, path)
        if node is None:
            return
        mode = node.size.mode
        if mode is SizeMode.RATIO:
            updated = SplitSize.ratio(sizes[0] / total)
            if abs(node.size.value - updated.value) < 0.002:
                return
        elif mode is SizeMode.FIXED_FIRST:
            updated = SplitSize.fixed_first(float(sizes[0]))
            if abs(node.size.value - updated.value) < 1.0:
                return
        elif mode is SizeMode.FIXED_SECOND:
            updated = SplitSize.fixed_second(float(sizes[1]))
            if abs(node.size.value - updated.value) < 1.0:
                return
        else:
            assert_never(mode)
        try:
            self.history.apply(ResizeSplit(path, updated))
        except DockError:
            return

    def _apply_split_geometry(self, splitter: object, path: tuple[bool, ...]) -> None:
        key = id(splitter)
        if key in self._applying_splits or bool(splitter.property("dragging")):  # type: ignore[attr-defined]
            return
        node = _split_at(self.history.layout.root, path)
        if node is None or splitter.count() < 2:  # type: ignore[attr-defined]
            return
        self._applying_splits.add(key)
        try:
            self._apply_split_geometry_now(splitter, node)
        finally:
            self._applying_splits.discard(key)

    def _apply_split_geometry_now(self, splitter: object, node: Split[str]) -> None:
        vertical = node.axis is SplitAxis.VERTICAL
        minimum = max(int(node.minimum), 0)
        for index in (0, 1):
            child = splitter.widget(index)  # type: ignore[attr-defined]
            if child is None:
                return
            if vertical:
                child.setMinimumHeight(minimum)
            else:
                child.setMinimumWidth(minimum)
        span = splitter.height() if vertical else splitter.width()  # type: ignore[attr-defined]
        available = int(span) - int(splitter.handleWidth())  # type: ignore[attr-defined]
        if available <= minimum:
            return
        mode = node.size.mode
        if mode is SizeMode.RATIO:
            first = round(available * node.size.value)
            splitter.setStretchFactor(0, 1)  # type: ignore[attr-defined]
            splitter.setStretchFactor(1, 1)  # type: ignore[attr-defined]
        elif mode is SizeMode.FIXED_FIRST:
            first = round(node.size.value)
            splitter.setStretchFactor(0, 0)  # type: ignore[attr-defined]
            splitter.setStretchFactor(1, 1)  # type: ignore[attr-defined]
        elif mode is SizeMode.FIXED_SECOND:
            first = available - round(node.size.value)
            splitter.setStretchFactor(0, 1)  # type: ignore[attr-defined]
            splitter.setStretchFactor(1, 0)  # type: ignore[attr-defined]
        else:
            assert_never(mode)
        high = available - minimum
        low = 0 if high < minimum else minimum
        limit = available if high < minimum else high
        first = min(max(first, low), limit)
        splitter.setSizes([first, available - first])  # type: ignore[attr-defined]

    def _palette_row(self, command_id: str, label: str, query: str, *, selected: bool) -> object:
        widgets = self._widgets
        row = widgets.QWidget()
        row.setObjectName("paletteRow")
        row.setAttribute(self._qt.WA_StyledBackground, True)
        row.setProperty("selected", "true" if selected else "false")
        row.setMinimumHeight(40)
        layout = widgets.QHBoxLayout(row)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        title = widgets.QLabel()
        title.setObjectName("paletteTitle")
        title.setTextFormat(self._qt.RichText)
        title.setText(self._highlight(label, query, selected))
        layout.addWidget(title)
        group = widgets.QLabel(tr(_group_key(command_id)))
        group.setObjectName("paletteGroup")
        layout.addWidget(group)
        layout.addStretch(1)
        spec = self.registry.get(command_id)
        chord = self.keymap.chord_for(self.registry, command_id) if spec is not None else None
        if chord:
            for part in display_chord(chord).split("+"):
                chip = widgets.QLabel(part)
                chip.setObjectName("paletteChip")
                layout.addWidget(chip)
        return row

    def _highlight(self, text: str, query: str, selected: bool) -> str:
        base = TEXT_ON_ACCENT if selected else TEXT
        marks = _match_indexes(text, query)
        parts: list[str] = []
        for index, char in enumerate(text):
            piece = escape(char)
            if index not in marks:
                parts.append(f'<span style="color: {base};">{piece}</span>')
            elif selected:
                parts.append(f'<b><span style="color: {base};">{piece}</span></b>')
            else:
                parts.append(f'<span style="color: {PRIMARY}; font-weight: 600;">{piece}</span>')
        return "".join(parts)

    def _sync_palette_selection(self, row: int) -> None:
        for index in range(self._hits.count()):
            item = self._hits.item(index)
            widget = self._hits.itemWidget(item)
            if widget is None:
                continue
            selected = index == row
            widget.setProperty("selected", "true" if selected else "false")
            label = item.data(self._qt.UserRole + 1)
            title = widget.findChild(self._widgets.QLabel, "paletteTitle")
            if title is not None and isinstance(label, str):
                title.setText(self._highlight(label, self._palette_query, selected))
            _restyle(widget)

    def _toolbar_button(self, text: str, primary: bool) -> object:
        button = self._widgets.QPushButton(text, self._toolbar)
        if primary:
            button.setObjectName("primary")
        policy = self._widgets.QSizePolicy.Policy
        button.setSizePolicy(policy.Minimum, policy.Fixed)
        button.ensurePolished()
        button.setMinimumWidth(button.sizeHint().width())
        return button

    def _install_resize_watch(self) -> None:
        watch = _resize_watch(self)
        self.widget.installEventFilter(watch)
        self._watches.append(watch)

    def _paint_split(self, splitter: object, vertical: bool, path: tuple[bool, ...]) -> None:
        widgets = self._widgets
        handle = splitter.handle(1)  # type: ignore[attr-defined]
        line = widgets.QFrame(handle)
        line.setObjectName("splitLine")
        line.setFrameShape(widgets.QFrame.Shape.NoFrame)
        line.setAttribute(self._qt.WA_StyledBackground, True)
        line.setAttribute(self._qt.WA_TransparentForMouseEvents, True)
        if vertical:
            line.setFixedHeight(1)
        else:
            line.setFixedWidth(1)
        handle.setMouseTracking(True)
        handle.setAttribute(self._qt.WA_Hover, True)
        _place_hairline(handle, line, vertical)
        watch = _split_watch(self, splitter, handle, line, vertical, path)
        handle.installEventFilter(watch)
        splitter.installEventFilter(watch)  # type: ignore[attr-defined]
        self._watches.append(watch)

    def _set_dragging(self, splitter: object, dragging: bool) -> None:
        splitter.setProperty("dragging", dragging)  # type: ignore[attr-defined]
        self._paint_lines(splitter, dragging)

    def _set_hot(self, splitter: object, hot: bool) -> None:
        if bool(splitter.property("dragging")):  # type: ignore[attr-defined]
            return
        self._paint_lines(splitter, hot)

    def _paint_lines(self, splitter: object, hot: bool) -> None:
        handle = splitter.handle(1)  # type: ignore[attr-defined]
        line = handle.findChild(self._widgets.QFrame, "splitLine")
        if line is None or bool(line.property("hot")) == hot:
            return
        line.setProperty("hot", hot)
        thickness = 3 if hot else 1
        vertical = splitter.orientation() == self._qt.Vertical  # type: ignore[attr-defined]
        if vertical:
            line.setFixedHeight(thickness)
        else:
            line.setFixedWidth(thickness)
        _place_hairline(handle, line, vertical)
        _restyle(line)

    def _root_splitter(self) -> object:
        found = self._dock.findChild(self._widgets.QSplitter, "rootSplit")
        if found is None:
            return self.widget.findChild(self._widgets.QSplitter, "rootSplit")
        return found

    def _reveal_one_close(self) -> None:
        buttons = self.widget.findChildren(self._widgets.QToolButton, "tabClose")
        if buttons:
            buttons[0].show()

    def _arm_tabs(self, tabs: object, panels: tuple[str, ...]) -> None:
        bar = tabs.tabBar()  # type: ignore[attr-defined]
        bar.setMouseTracking(True)
        bar.setAttribute(self._qt.WA_Hover, True)
        buttons: dict[int, object] = {}
        icon = _close_icon()
        for index, panel in enumerate(panels):
            if panel == _CENTRE:
                continue
            button = self._widgets.QToolButton(bar)
            button.setObjectName("tabClose")
            button.setIcon(icon)
            button.setIconSize(_qt_core().QSize(12, 12))
            button.setToolTip(tr("shell.tab.close"))
            button.clicked.connect(lambda _checked=False, name=panel: self._close_panel(name))
            side = self._widgets.QTabBar.ButtonPosition.RightSide
            bar.setTabButton(index, side, button)
            button.setVisible(index == tabs.currentIndex())  # type: ignore[attr-defined]
            buttons[index] = button
        tabs.currentChanged.connect(  # type: ignore[attr-defined]
            lambda index, buttons=buttons: _set_close_visible(buttons, index, index)
        )
        watch = _tab_watch(bar, buttons)
        bar.installEventFilter(watch)
        self._watches.append(watch)

    def _close_panel(self, panel: str) -> None:
        self.active = panel
        self._close_active()

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
        group = _tab_group(self.history.layout, panel)
        try:
            self.history.apply(Close(panel), _permissions)
        except DockError:
            self._status.setText(tr("shell.status.protected"))
            return
        self.active = _successor(self.history.layout, group, panel)
        self.rebuild()
        self._sync_shortcuts()

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


def _match_indexes(text: str, query: str) -> set[int]:
    """Return indexes of a case-insensitive subsequence match.

    An empty query, or a query that is not a subsequence, matches nothing.
    The walk is the same one :func:`voxprint_common.ui.palette.fuzzy_score` uses.
    """
    if query == "":
        return set()
    letters = text.lower()
    marks: set[int] = set()
    index = 0
    for char in query.lower():
        found = False
        while index < len(letters):
            if letters[index] == char:
                marks.add(index)
                index += 1
                found = True
                break
            index += 1
        if not found:
            return set()
    return marks


def _group_key(command_id: str) -> str:
    """Return the palette category key for a command id."""
    if command_id in {"view.toggle_expert_mode", "view.settings"}:
        return "shell.group.view"
    prefix = command_id.split(".", 1)[0]
    if prefix == "file":
        return "shell.group.file"
    if prefix == "edit":
        return "shell.group.edit"
    return "shell.group.panels"


def _split_at(
    node: Split[str] | Tabs[str] | Stack[str] | None, path: tuple[bool, ...]
) -> Split[str] | None:
    """Return the split at ``path``.

    None means the path does not end on a split.
    """
    current: Split[str] | Tabs[str] | Stack[str] | None = node
    for step in path:
        if not isinstance(current, Split):
            return None
        current = current.second if step else current.first
    if isinstance(current, Split):
        return current
    return None


def _tab_group(layout: Layout[str], panel: str) -> tuple[str, ...]:
    """Return the tab ids that share a group with ``panel``."""
    found = _tabs_containing(layout.root, panel)
    if found is not None:
        return found
    for group in layout.floating:
        if panel in group.panels:
            return tuple(group.panels)
    return ()


def _tabs_containing(
    node: Split[str] | Tabs[str] | Stack[str] | None, panel: str
) -> tuple[str, ...] | None:
    if node is None:
        return None
    if isinstance(node, Tabs):
        if panel in node.panels:
            return tuple(node.panels)
        return None
    if isinstance(node, Stack):
        return None
    if isinstance(node, Split):
        return _tabs_containing(node.first, panel) or _tabs_containing(node.second, panel)
    assert_never(node)


def _successor(layout: Layout[str], group: tuple[str, ...], closed: str) -> str:
    """Return the tab that should become active after ``closed`` is removed."""
    for group_id in layout.floating:
        if closed in group_id.panels:
            continue
        if any(name in group_id.panels for name in group):
            return str(group_id.panels[group_id.active])
    for name in group:
        if name == closed:
            continue
        current = _active_panel(layout.root, name)
        if current is not None:
            return current
    if layout.contains(_CENTRE):
        return _CENTRE
    return _CENTRE


def _active_panel(node: Split[str] | Tabs[str] | Stack[str] | None, panel: str) -> str | None:
    if node is None:
        return None
    if isinstance(node, Tabs):
        if panel in node.panels:
            return str(node.panels[node.active])
        return None
    if isinstance(node, Stack):
        return None
    if isinstance(node, Split):
        return _active_panel(node.first, panel) or _active_panel(node.second, panel)
    assert_never(node)


def _restyle(widget: object) -> None:
    style = widget.style()  # type: ignore[attr-defined]
    style.unpolish(widget)
    style.polish(widget)
    widget.update()  # type: ignore[attr-defined]
    kind = _widgets().QWidget  # type: ignore[attr-defined]
    for child in widget.findChildren(kind):  # type: ignore[attr-defined]
        child_style = child.style()
        child_style.unpolish(child)
        child_style.polish(child)
        child.update()


def _place_hairline(handle: object, line: object, vertical: bool) -> None:
    rect = handle.rect()  # type: ignore[attr-defined]
    if vertical:
        thickness = max(int(line.height()), 1)  # type: ignore[attr-defined]
        top = max((rect.height() - thickness) // 2, 0)
        line.setGeometry(0, top, max(rect.width(), 1), thickness)  # type: ignore[attr-defined]
        return
    thickness = max(int(line.width()), 1)  # type: ignore[attr-defined]
    left = max((rect.width() - thickness) // 2, 0)
    line.setGeometry(left, 0, thickness, max(rect.height(), 1))  # type: ignore[attr-defined]


def _set_close_visible(buttons: dict[int, object], current: int, hovered: int) -> None:
    for tab_index, button in buttons.items():
        button.setVisible(tab_index in {current, hovered})  # type: ignore[attr-defined]


def _close_icon() -> object:
    core = _qt_core()
    gui = _qt()[1]
    pixmap = gui.QPixmap(12, 12)
    pixmap.fill(core.Qt.transparent)
    painter = gui.QPainter(pixmap)
    painter.setPen(gui.QPen(gui.QColor(TEXT_MUTED)))
    painter.drawLine(3, 3, 8, 8)
    painter.drawLine(8, 3, 3, 8)
    painter.end()
    return gui.QIcon(pixmap)


def _resize_watch(shell: ReferenceShell) -> object:
    core = _qt_core()

    class _Watch(core.QObject):
        def eventFilter(self, watched: object, event: object) -> bool:
            kind = event.type()  # type: ignore[attr-defined]
            if kind == core.QEvent.Type.Resize and not shell._building:
                shell._fill_toolbar()
            if (
                kind in {core.QEvent.Type.Resize, core.QEvent.Type.Move}
                and shell._geometry_ready
                and not shell._fitting_geometry
            ):
                shell._save_geometry()
            return False

    return _Watch(shell.widget)


def _split_watch(
    shell: ReferenceShell,
    splitter: object,
    handle: object,
    line: object,
    vertical: bool,
    path: tuple[bool, ...],
) -> object:
    core = _qt_core()

    class _Watch(core.QObject):
        def eventFilter(self, watched: object, event: object) -> bool:
            kind = event.type()  # type: ignore[attr-defined]
            if kind in {core.QEvent.Type.Resize, core.QEvent.Type.Show}:
                shell._apply_split_geometry(splitter, path)
                _place_hairline(handle, line, vertical)
            elif kind == core.QEvent.Type.MouseButtonPress:
                shell._press_sizes[path] = tuple(int(value) for value in splitter.sizes())  # type: ignore[attr-defined]
                shell._set_dragging(splitter, True)
            elif kind == core.QEvent.Type.MouseButtonRelease:
                shell._commit_split(path, splitter)
                shell._set_dragging(splitter, False)
                shell._set_hot(splitter, False)
                shell._apply_split_geometry(splitter, path)
            elif kind in {core.QEvent.Type.HoverEnter, core.QEvent.Type.HoverMove}:
                shell._set_hot(splitter, True)
            elif kind == core.QEvent.Type.HoverLeave:
                shell._set_hot(splitter, False)
            return False

    return _Watch(handle)


def _tab_watch(bar: object, buttons: dict[int, object]) -> object:
    core = _qt_core()

    class _Watch(core.QObject):
        def eventFilter(self, watched: object, event: object) -> bool:
            kind = event.type()  # type: ignore[attr-defined]
            if kind in {core.QEvent.Type.HoverMove, core.QEvent.Type.MouseMove}:
                index = bar.tabAt(event.position().toPoint())  # type: ignore[attr-defined]
                _set_close_visible(buttons, bar.currentIndex(), index)  # type: ignore[attr-defined]
            elif kind == core.QEvent.Type.HoverLeave:
                local = bar.mapFromGlobal(_qt()[1].QCursor.pos())  # type: ignore[attr-defined]
                if bar.rect().contains(local):  # type: ignore[attr-defined]
                    return False
                current = bar.currentIndex()  # type: ignore[attr-defined]
                _set_close_visible(buttons, current, current)
            return False

    return _Watch(bar)


def _page(shell: ReferenceShell, panel: str) -> object:
    if panel == "settings":
        return _settings_page(shell)
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


def _settings_page(shell: ReferenceShell) -> object:
    """Build the Settings panel from the descriptors the current mode shows."""
    widgets = shell._widgets
    page = widgets.QWidget()
    page.setObjectName("settings")
    column = widgets.QVBoxLayout(page)
    column.setContentsMargins(12, 8, 12, 8)
    column.setSpacing(6)
    group = ""
    for spec in reference_settings().visible(expert=shell.expert.enabled()):
        if spec.group != group:
            group = spec.group
            heading = widgets.QLabel(tr(f"setting.group.{group}"))
            heading.setObjectName("settingGroup")
            column.addWidget(heading)
        control = _setting_control(shell, spec)
        if spec.type is not SettingType.BOOL:
            label = widgets.QLabel(tr(spec.label_key))
            label.setObjectName("panel")
            column.addWidget(label)
        column.addWidget(control)
    column.addStretch(1)
    return page


def _setting_control(shell: ReferenceShell, spec: SettingSpec) -> object:
    widgets = shell._widgets
    kind = spec.type
    if kind is SettingType.BOOL:
        box = widgets.QCheckBox(tr(spec.label_key))
        box.setObjectName(spec.setting_id)
        box.setChecked(spec.default is True)
        control = box
    elif kind is SettingType.ENUM:
        combo = widgets.QComboBox()
        combo.setObjectName(spec.setting_id)
        selected = 0
        for index, choice in enumerate(spec.choices):
            combo.addItem(tr(choice.label_key), choice.value)
            if choice.value == spec.default:
                selected = index
        combo.setCurrentIndex(selected)
        control = combo
    elif kind is SettingType.INT:
        spin = widgets.QSpinBox()
        spin.setObjectName(spec.setting_id)
        spin.setRange(int(spec.minimum or 0), int(spec.maximum or 0))
        spin.setValue(int(spec.default) if isinstance(spec.default, int) else 0)
        control = spin
    elif kind is SettingType.FLOAT:
        spin = widgets.QDoubleSpinBox()
        spin.setObjectName(spec.setting_id)
        spin.setRange(float(spec.minimum or 0), float(spec.maximum or 0))
        spin.setSingleStep(0.1)
        spin.setValue(float(spec.default) if isinstance(spec.default, int | float) else 0.0)
        control = spin
    elif kind is SettingType.PATH:
        edit = widgets.QLineEdit()
        edit.setObjectName(spec.setting_id)
        edit.setText(spec.default if isinstance(spec.default, str) else "")
        control = edit
    elif kind is SettingType.TEXT:
        edit = widgets.QPlainTextEdit()
        edit.setObjectName(spec.setting_id)
        edit.setPlainText(spec.default if isinstance(spec.default, str) else "")
        edit.setFixedHeight(64)
        control = edit
    else:
        assert_never(kind)
    if spec.expert:
        control.setToolTip(tr(spec.help_key))  # type: ignore[attr-defined]
    return control


def _panel_title(panel: str) -> str:
    if panel == "prompt":
        return tr("shell.panel.prompt")
    if panel == "layers":
        return tr("shell.panel.layers")
    if panel == _CENTRE:
        return tr("shell.panel.canvas")
    if panel == "history":
        return tr("shell.panel.history")
    if panel == "settings":
        return tr("shell.panel.settings")
    if panel == "advanced":
        return tr("shell.panel.advanced")
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
    if panel == "advanced":
        return tr("shell.panel.advanced.body")
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
    if command_id == "view.toggle_expert_mode":
        return tr("command.view.toggle_expert_mode")
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


def _qt_core() -> object:
    """Import ``PySide6.QtCore``.

    The import stays inside this function so a headless caller can import
    :func:`reference_layout` without the ``ui`` extra.
    """
    from PySide6 import QtCore

    return QtCore


def _widgets() -> object:
    return _qt()[2]
