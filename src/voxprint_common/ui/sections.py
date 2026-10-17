"""Sections, wizards, and the Audiobook Builder demo.

A screen with one section draws nothing extra. A screen with several sections
gets a tab row, optional actions, and either the shared body, a table, or a
note. A wizard is a short list of steps. The shell stores which step is open
and a small blob the app returns from a callback. It does not train a voice
or read that blob.

Expert fields carry ``section`` and ``step`` on the descriptor. The panel
shows the current section, or only the current step while its wizard is open.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from voxprint_common.ui.presets import SettingState
from voxprint_common.ui.settings import Choice, Settings, SettingSpec, SettingType
from voxprint_common.ui.variants import settings_for, surface_for

__all__ = [
    "BodyKind",
    "ColumnKind",
    "Section",
    "SectionAction",
    "SectionDeck",
    "SectionSet",
    "StepKind",
    "TableColumn",
    "TableRow",
    "Wizard",
    "WizardStep",
    "audiobook_sections",
    "fit_tabs",
    "picker_key",
]

_CHECKPOINT = "wizard.json"
_CHECKPOINT_SCHEMA = 1

Capture = Callable[[str, str], Mapping[str, object]]
Restore = Callable[[str, str, Mapping[str, object]], None]


class BodyKind(StrEnum):
    """What a section puts in the centre of the screen."""

    SCREEN = "screen"
    TABLE = "table"
    NOTE = "note"


class ColumnKind(StrEnum):
    """One column of a section table."""

    LABEL = "label"
    PICKER = "picker"
    PREVIEW = "preview"


class StepKind(StrEnum):
    """A wizard step is a form or a long-running job."""

    FORM = "form"
    RUNNING = "running"


@dataclass(frozen=True)
class SectionAction:
    """A button beside the section's main action.

    ``wizard_id`` is the wizard the button opens. That wizard is a pre-step
    when the section's own work has not started.
    """

    action_id: str
    label_key: str
    wizard_id: str


@dataclass(frozen=True)
class TableColumn:
    """A column header. A picker stores one value per row."""

    column_id: str
    label_key: str
    kind: ColumnKind
    choices: tuple[Choice, ...] = ()

    def __post_init__(self) -> None:
        """A picker needs choices. Other columns do not."""
        if not self.column_id or not self.label_key:
            raise ValueError("a column needs an id and a label")
        if self.kind is ColumnKind.PICKER and not self.choices:
            raise ValueError("a picker column needs choices")
        if self.kind is not ColumnKind.PICKER and self.choices:
            raise ValueError("only a picker column has choices")


@dataclass(frozen=True)
class TableRow:
    """One row the app supplies. ``label_key`` is the row title."""

    row_id: str
    label_key: str


@dataclass(frozen=True)
class Section:
    """One tab.

    ``actions`` open wizards. A table's picker values use
    :func:`picker_key` in that section's settings namespace.
    """

    section_id: str
    label_key: str
    body: BodyKind = BodyKind.SCREEN
    actions: tuple[SectionAction, ...] = ()
    columns: tuple[TableColumn, ...] = ()
    rows: tuple[TableRow, ...] = ()
    note_key: str = ""

    def __post_init__(self) -> None:
        """Reject a section that does not match its body."""
        if not self.section_id or not self.label_key:
            raise ValueError("a section needs an id and a label")
        row_ids = [row.row_id for row in self.rows]
        if len(row_ids) != len(set(row_ids)):
            raise ValueError("section row ids must be unique")
        column_ids = [column.column_id for column in self.columns]
        if len(column_ids) != len(set(column_ids)):
            raise ValueError("section column ids must be unique")
        kind = self.body
        if kind is BodyKind.TABLE and not self.columns:
            raise ValueError("a table section needs columns")
        if kind is BodyKind.NOTE and not self.note_key:
            raise ValueError("a note section needs a note")
        if kind is BodyKind.SCREEN and (self.columns or self.rows or self.note_key):
            raise ValueError("the shared screen does not take a table or a note")
        if kind is not BodyKind.SCREEN and kind is not BodyKind.TABLE and kind is not BodyKind.NOTE:
            assert_never(kind)


@dataclass(frozen=True)
class WizardStep:
    """One step. ``requires`` lists setting ids that must be filled before Next."""

    step_id: str
    label_key: str
    kind: StepKind = StepKind.FORM
    requires: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """A running step does not gate Next. A form step may."""
        if not self.step_id or not self.label_key:
            raise ValueError("a step needs an id and a label")
        if self.kind is StepKind.RUNNING and self.requires:
            raise ValueError("a running step has no Next gate")
        if self.kind is not StepKind.FORM and self.kind is not StepKind.RUNNING:
            assert_never(self.kind)


@dataclass(frozen=True)
class Wizard:
    """Steps that belong to one section."""

    wizard_id: str
    title_key: str
    section_id: str
    steps: tuple[WizardStep, ...]

    def __post_init__(self) -> None:
        """Reject an empty wizard or a repeated step id."""
        if not self.wizard_id or not self.title_key or not self.section_id or not self.steps:
            raise ValueError("a wizard needs an id, a title, a section, and steps")
        ids = [step.step_id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("wizard step ids must be unique")

    def step(self, step_id: str) -> WizardStep | None:
        """Return the step with ``step_id``, or None."""
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None


@dataclass(frozen=True)
class SectionSet:
    """The sections, wizards, and settings of one app."""

    sections: tuple[Section, ...]
    wizards: tuple[Wizard, ...]
    settings: Settings

    def __post_init__(self) -> None:
        """The first section is the one the screen opens on."""
        if len(self.sections) < 2:
            raise ValueError("a section set needs at least two sections")
        ids = [section.section_id for section in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("section ids must be unique")
        known = set(ids)
        wizard_ids = [wizard.wizard_id for wizard in self.wizards]
        if len(wizard_ids) != len(set(wizard_ids)):
            raise ValueError("wizard ids must be unique")
        wizards = {wizard.wizard_id: wizard for wizard in self.wizards}
        for wizard in self.wizards:
            if wizard.section_id not in known:
                raise ValueError(f"wizard {wizard.wizard_id} names an unknown section")
        for section in self.sections:
            for action in section.actions:
                if action.wizard_id not in wizards:
                    raise ValueError(f"action {action.action_id} names an unknown wizard")

    def section(self, section_id: str) -> Section | None:
        """Return the section with ``section_id``, or None."""
        for section in self.sections:
            if section.section_id == section_id:
                return section
        return None

    def wizard(self, wizard_id: str) -> Wizard | None:
        """Return the wizard with ``wizard_id``, or None."""
        for wizard in self.wizards:
            if wizard.wizard_id == wizard_id:
                return wizard
        return None


def picker_key(section_id: str, row_id: str, column_id: str) -> str:
    """Return the settings id for one picker cell.

    The id lives in the section namespace: ``roles.mira.voice``.
    """
    return f"{section_id}.{row_id}.{column_id}"


def fit_tabs(widths: Sequence[int], available: int, more: int, gap: int = 0) -> int:
    """Return how many leading tabs fit in ``available`` pixels.

    Each shown tab keeps its full width. The More button is reserved only
    when at least one tab does not fit. A width of zero means that tab stays
    in the menu.

    Args:
        widths: Tab widths, in order.
        available: Pixels the row can use.
        more: Width of the More button.
        gap: Pixels between two items.
    """
    count = len(widths)

    def used(shown: int, *, with_more: bool) -> int:
        if shown <= 0:
            return more if with_more else 0
        total = sum(widths[:shown]) + gap * (shown - 1)
        if with_more:
            total += gap + more
        return total

    if used(count, with_more=False) <= available:
        return count
    shown = 0
    for index in range(1, count + 1):
        if used(index, with_more=True) <= available:
            shown = index
            continue
        break
    return shown


class SectionDeck:
    """Which section and wizard step are open. Qt is not imported.

    The settings values live in the shared :class:`SettingState`. The
    checkpoint file remembers the wizard, the step, the running job, and the
    blob from ``capture``. ``restore`` receives that blob after a restart.
    """

    def __init__(
        self,
        document: SectionSet,
        state: SettingState,
        *,
        state_dir: Path | None = None,
        capture: Capture | None = None,
        restore: Restore | None = None,
    ) -> None:
        """Open the first section, then resume a checkpoint when one is stored.

        Args:
            document: Sections and wizards.
            state: The shared settings state. Picker values are stored here.
            state_dir: Directory for ``wizard.json``. None keeps it in memory.
            capture: Returns the app blob for the open step. None stores an empty blob.
            restore: Receives the blob when a checkpoint is loaded. None ignores it.
        """
        self._document = document
        self._state = state
        self._state_dir = state_dir
        self._capture = capture
        self._restore = restore
        self._section = document.sections[0]
        self._wizard: Wizard | None = None
        self._index = 0
        self._running = False
        self._progress: tuple[int, int, str] | None = None
        self._memory: dict[str, object] | None = None
        self._resume()

    @property
    def document(self) -> SectionSet:
        """Return the sections and wizards."""
        return self._document

    @property
    def current(self) -> Section:
        """Return the open section."""
        return self._section

    @property
    def wizard(self) -> Wizard | None:
        """Return the open wizard, or None."""
        return self._wizard

    @property
    def step(self) -> WizardStep | None:
        """Return the open step, or None when no wizard is open."""
        if self._wizard is None:
            return None
        return self._wizard.steps[self._index]

    @property
    def index(self) -> int:
        """Return the open step index, starting at zero."""
        return self._index

    @property
    def running(self) -> bool:
        """Return whether the open step has a job in progress."""
        return self._running

    @property
    def progress(self) -> tuple[int, int, str] | None:
        """Return ``(step, total, label)`` while a job is in progress."""
        return self._progress

    def shows_wizard(self) -> bool:
        """Return whether the open section is showing its wizard."""
        return self._wizard is not None and self._section.section_id == self._wizard.section_id

    def shows_screen(self) -> bool:
        """Return whether the shared simple body should stay on screen."""
        if self.shows_wizard():
            return False
        return self._section.body is BodyKind.SCREEN

    def select(self, section_id: str) -> None:
        """Show ``section_id``.

        Args:
            section_id: A section in this set.

        Raises:
            ValueError: The section is unknown.
        """
        section = self._document.section(section_id)
        if section is None:
            raise ValueError(f"unknown section {section_id}")
        self._section = section

    def action(self, action_id: str) -> None:
        """Open the wizard declared by ``action_id`` on the current section.

        Args:
            action_id: An action on the open section.

        Raises:
            ValueError: The section has no such action.
        """
        for item in self._section.actions:
            if item.action_id == action_id:
                self.open_wizard(item.wizard_id)
                return
        raise ValueError(f"unknown action {action_id}")

    def open_wizard(self, wizard_id: str) -> None:
        """Open ``wizard_id`` on its first step and select its section.

        Args:
            wizard_id: A wizard in this set.

        Raises:
            ValueError: The wizard is unknown.
        """
        wizard = self._document.wizard(wizard_id)
        if wizard is None:
            raise ValueError(f"unknown wizard {wizard_id}")
        self._wizard = wizard
        self._index = 0
        self._running = False
        self._progress = None
        self.select(wizard.section_id)
        self._save()

    def can_advance(self) -> bool:
        """Return whether Next is allowed on the open form step."""
        step = self.step
        if step is None or step.kind is StepKind.RUNNING:
            return False
        for key in step.requires:
            if not self._state.has(key) or not _filled(self._state.get(key)):
                return False
        return True

    def advance(self) -> None:
        """Move to the next step.

        Raises:
            ValueError: Next is gated, or this is the last step.
        """
        if self._wizard is None or not self.can_advance():
            raise ValueError("next is not available")
        if self._index + 1 >= len(self._wizard.steps):
            raise ValueError("next is not available")
        self._index += 1
        self._running = False
        self._progress = None
        self._save()

    def retreat(self) -> None:
        """Move to the previous step.

        Raises:
            ValueError: This is the first step, or no wizard is open.
        """
        if self._wizard is None or self._index == 0:
            raise ValueError("back is not available")
        self._index -= 1
        self._running = False
        self._progress = None
        self._save()

    def note_progress(self, step: int, total: int, label: str) -> None:
        """Record a long-running step's progress and store the checkpoint.

        Args:
            step: Finished units, from zero through ``total``.
            total: How many units the job takes.
            label: Already translated stage text.

        Raises:
            ValueError: The open step is not a running step, or the numbers are wrong.
        """
        current = self.step
        if current is None or current.kind is not StepKind.RUNNING:
            raise ValueError("progress belongs on a running step")
        if total <= 0 or step < 0 or step > total:
            raise ValueError("progress needs a step inside the total")
        self._running = True
        self._progress = (step, total, label)
        self._save()

    def cancel_run(self) -> None:
        """Stop the job and keep the step so a later launch can resume it.

        Raises:
            ValueError: No running step is open.
        """
        if not self._running:
            raise ValueError("nothing is running")
        self._running = False
        self._progress = None
        self._save()

    def picker(self, section_id: str, row_id: str, column_id: str) -> str:
        """Return the stored picker value.

        Args:
            section_id: Section namespace.
            row_id: Row id.
            column_id: Picker column id.

        Raises:
            ValueError: The cell is not a picker.
            KeyError: The value is not in the shared state.
        """
        self._picker(section_id, row_id, column_id)
        value = self._state.get(picker_key(section_id, row_id, column_id))
        if not isinstance(value, str):
            raise KeyError(picker_key(section_id, row_id, column_id))
        return value

    def set_picker(self, section_id: str, row_id: str, column_id: str, value: str) -> None:
        """Store ``value`` for one picker cell in the shared settings state.

        Args:
            section_id: Section namespace.
            row_id: Row id.
            column_id: Picker column id.
            value: One of the column's choices.

        Raises:
            ValueError: The cell is not a picker, or ``value`` is not a choice.
        """
        column = self._picker(section_id, row_id, column_id)
        if value not in {choice.value for choice in column.choices}:
            raise ValueError(f"unknown choice {value}")
        self._state.set_value(picker_key(section_id, row_id, column_id), value)

    def scoped(self, specs: Iterable[SettingSpec]) -> tuple[SettingSpec, ...]:
        """Return the expert fields for the open section or wizard step.

        A wizard step shows only the fields tagged with that step. A section
        shows the fields tagged with that section and with no step.
        """
        if self.shows_wizard():
            step = self.step
            step_id = "" if step is None else step.step_id
            return tuple(spec for spec in specs if spec.step == step_id)
        section_id = self._section.section_id
        return tuple(spec for spec in specs if spec.section == section_id and spec.step == "")

    def _picker(self, section_id: str, row_id: str, column_id: str) -> TableColumn:
        section = self._document.section(section_id)
        if section is None or section.section_id != section_id:
            raise ValueError(f"unknown section {section_id}")
        if not any(row.row_id == row_id for row in section.rows):
            raise ValueError(f"unknown row {row_id}")
        for column in section.columns:
            if column.column_id == column_id:
                if column.kind is not ColumnKind.PICKER:
                    raise ValueError(f"{column_id} is not a picker")
                return column
        raise ValueError(f"unknown column {column_id}")

    def _resume(self) -> None:
        payload = self._read()
        if payload is None:
            return
        wizard = self._document.wizard(str(payload.get("wizard", "")))
        if wizard is None:
            return
        step_id = payload.get("step")
        if not isinstance(step_id, str) or wizard.step(step_id) is None:
            return
        self._wizard = wizard
        self._index = next(
            index for index, step in enumerate(wizard.steps) if step.step_id == step_id
        )
        self.select(wizard.section_id)
        running = payload.get("running") is True
        label = payload.get("progress_label")
        done = payload.get("progress_step")
        total = payload.get("progress_total")
        if (
            running
            and isinstance(label, str)
            and isinstance(done, int)
            and not isinstance(done, bool)
            and isinstance(total, int)
            and not isinstance(total, bool)
            and total > 0
            and 0 <= done <= total
        ):
            self._running = True
            self._progress = (done, total, label)
        blob = payload.get("blob")
        if self._restore is not None and isinstance(blob, dict):
            self._restore(wizard.wizard_id, step_id, blob)

    def _save(self) -> None:
        if self._wizard is None or self.step is None:
            return
        step = self.step
        blob: Mapping[str, object] = {}
        if self._capture is not None:
            blob = self._capture(self._wizard.wizard_id, step.step_id)
        if not isinstance(blob, Mapping):
            raise ValueError("a checkpoint blob must be an object")
        payload = {
            "schema": _CHECKPOINT_SCHEMA,
            "wizard": self._wizard.wizard_id,
            "step": step.step_id,
            "running": self._running,
            "progress_step": 0 if self._progress is None else self._progress[0],
            "progress_total": 0 if self._progress is None else self._progress[1],
            "progress_label": "" if self._progress is None else self._progress[2],
            "blob": dict(blob),
        }
        if self._state_dir is None:
            self._memory = payload
            return
        self._state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_dir / _CHECKPOINT
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _read(self) -> dict[str, object] | None:
        if self._state_dir is None:
            return self._memory
        path = self._state_dir / _CHECKPOINT
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError, UnicodeError, json.JSONDecodeError:
            return None
        if not isinstance(payload, dict) or payload.get("schema") != _CHECKPOINT_SCHEMA:
            return None
        return payload


def _filled(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def audiobook_sections() -> SectionSet:
    """Return the Audiobook Builder demo: six sections and three wizards.

    Narration keeps the shared audio screen and adds Prepare text. Roles is
    a casting table. Voices is a list plus Train a voice and Re-voice.
    Training is three steps, and the last one runs for a while.
    """
    base = settings_for(surface_for("audiobook-builder"))
    specs = [
        replace(spec, section="narration") if spec.expert else spec
        for spec in base.visible(expert=True)
    ]
    specs.extend(_demo_settings())
    return SectionSet(
        sections=_demo_sections(),
        wizards=_demo_wizards(),
        settings=Settings(specs),
    )


def _demo_sections() -> tuple[Section, ...]:
    voices = _voice_choices()
    return (
        Section(
            "narration",
            "section.narration",
            actions=(SectionAction("prepare", "section.action.prepare", "prepare"),),
        ),
        Section(
            "roles",
            "section.roles",
            body=BodyKind.TABLE,
            columns=(
                TableColumn("character", "section.column.character", ColumnKind.LABEL),
                TableColumn("voice", "section.column.voice", ColumnKind.PICKER, voices),
                TableColumn("preview", "section.column.preview", ColumnKind.PREVIEW),
            ),
            rows=(
                TableRow("mira", "section.row.mira"),
                TableRow("peter", "section.row.peter"),
                TableRow("narrator", "section.row.narrator"),
            ),
        ),
        Section(
            "voices",
            "section.voices",
            body=BodyKind.TABLE,
            actions=(
                SectionAction("train", "section.action.train", "train"),
                SectionAction("revoice", "section.action.revoice", "revoice"),
            ),
            columns=(
                TableColumn("name", "section.column.name", ColumnKind.LABEL),
                TableColumn("preview", "section.column.preview", ColumnKind.PREVIEW),
            ),
            rows=(
                TableRow("anna", "section.row.anna"),
                TableRow("boris", "section.row.boris"),
                TableRow("studio", "section.row.studio"),
            ),
        ),
        Section(
            "library",
            "section.library",
            body=BodyKind.TABLE,
            columns=(
                TableColumn("name", "section.column.name", ColumnKind.LABEL),
                TableColumn("preview", "section.column.preview", ColumnKind.PREVIEW),
            ),
            rows=(
                TableRow("quiet", "section.row.quiet"),
                TableRow("harbour", "section.row.harbour"),
                TableRow("letters", "section.row.letters"),
            ),
        ),
        Section(
            "maintenance",
            "section.maintenance",
            body=BodyKind.NOTE,
            note_key="section.note.maintenance",
        ),
        Section(
            "spare",
            "section.spare",
            body=BodyKind.NOTE,
            note_key="section.note.spare",
        ),
    )


def _demo_wizards() -> tuple[Wizard, ...]:
    return (
        Wizard(
            "prepare",
            "wizard.prepare.title",
            "narration",
            (WizardStep("prepare.text", "wizard.step.prepare", requires=("prepare.note",)),),
        ),
        Wizard(
            "revoice",
            "wizard.revoice.title",
            "voices",
            (WizardStep("revoice.pick", "wizard.step.revoice", requires=("revoice.voice",)),),
        ),
        Wizard(
            "train",
            "wizard.train.title",
            "voices",
            (
                WizardStep("train.sample", "wizard.step.sample", requires=("train.sample",)),
                WizardStep("train.check", "wizard.step.check", requires=("train.name",)),
                WizardStep("train.run", "wizard.step.run", kind=StepKind.RUNNING),
            ),
        ),
    )


def _voice_choices() -> tuple[Choice, ...]:
    return (
        Choice("anna", "setting.voice.anna"),
        Choice("boris", "setting.voice.boris"),
        Choice("studio", "setting.voice.studio"),
    )


def _demo_settings() -> tuple[SettingSpec, ...]:
    voices = _voice_choices()
    engines = (
        Choice("local", "setting.voices.engine.local"),
        Choice("cloud", "setting.voices.engine.cloud"),
    )
    sorts = (
        Choice("title", "setting.library.sort.title"),
        Choice("recent", "setting.library.sort.recent"),
    )
    return (
        SettingSpec(
            "roles.strict",
            "setting.roles.strict",
            "setting.roles.strict.help",
            SettingType.BOOL,
            False,
            expert=True,
            group="roles",
            section="roles",
        ),
        SettingSpec(
            picker_key("roles", "mira", "voice"),
            "section.column.voice",
            "setting.roles.voice.help",
            SettingType.ENUM,
            "anna",
            group="roles",
            section="roles",
            choices=voices,
        ),
        SettingSpec(
            picker_key("roles", "peter", "voice"),
            "section.column.voice",
            "setting.roles.voice.help",
            SettingType.ENUM,
            "boris",
            group="roles",
            section="roles",
            choices=voices,
        ),
        SettingSpec(
            picker_key("roles", "narrator", "voice"),
            "section.column.voice",
            "setting.roles.voice.help",
            SettingType.ENUM,
            "studio",
            group="roles",
            section="roles",
            choices=voices,
        ),
        SettingSpec(
            "voices.engine",
            "setting.voices.engine",
            "setting.voices.engine.help",
            SettingType.ENUM,
            "local",
            expert=True,
            group="voices",
            section="voices",
            choices=engines,
        ),
        SettingSpec(
            "library.sort",
            "setting.library.sort",
            "setting.library.sort.help",
            SettingType.ENUM,
            "title",
            expert=True,
            group="library",
            section="library",
            choices=sorts,
        ),
        SettingSpec(
            "maintenance.keep_days",
            "setting.maintenance.keep_days",
            "setting.maintenance.keep_days.help",
            SettingType.INT,
            30,
            expert=True,
            group="maintenance",
            section="maintenance",
            minimum=1,
            maximum=365,
        ),
        SettingSpec(
            "spare.note",
            "setting.spare.note",
            "setting.spare.note.help",
            SettingType.TEXT,
            "",
            expert=True,
            group="spare",
            section="spare",
        ),
        SettingSpec(
            "prepare.note",
            "setting.prepare.note",
            "setting.prepare.note.help",
            SettingType.TEXT,
            "",
            group="prepare",
        ),
        SettingSpec(
            "revoice.voice",
            "setting.revoice.voice",
            "setting.revoice.voice.help",
            SettingType.ENUM,
            "anna",
            group="revoice",
            choices=voices,
        ),
        SettingSpec(
            "train.sample",
            "setting.train.sample",
            "setting.train.sample.help",
            SettingType.TEXT,
            "",
            group="train",
        ),
        SettingSpec(
            "train.name",
            "setting.train.name",
            "setting.train.name.help",
            SettingType.TEXT,
            "",
            group="train",
        ),
        SettingSpec(
            "train.sample_seconds",
            "setting.train.sample_seconds",
            "setting.train.sample_seconds.help",
            SettingType.INT,
            8,
            expert=True,
            group="train",
            section="voices",
            step="train.sample",
            minimum=1,
            maximum=60,
        ),
        SettingSpec(
            "train.noise",
            "setting.train.noise",
            "setting.train.noise.help",
            SettingType.FLOAT,
            0.2,
            expert=True,
            group="train",
            section="voices",
            step="train.check",
            minimum=0.0,
            maximum=1.0,
        ),
        SettingSpec(
            "train.epochs",
            "setting.train.epochs",
            "setting.train.epochs.help",
            SettingType.INT,
            12,
            expert=True,
            group="train",
            section="voices",
            step="train.run",
            minimum=1,
            maximum=40,
        ),
    )
