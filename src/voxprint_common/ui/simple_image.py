"""Shared simple screen for every VOXPrint app.

:class:`SimpleLayout` is the headless description: an input, one primary
action, a result, and a history strip. :class:`SimpleWidget` is the Qt view.
Image, audio, video, text, tool, and viewer are thin configurations
(:mod:`voxprint_common.ui.variants`), not separate layouts. The viewer has
no input and no primary action, so the result fills that space.
:class:`SimpleImageLayout` and :class:`SimpleImageWidget` are the same classes.
Importing this module does not import PySide6. Expert mode adds a side panel
and does not move the rest. The dock stays a separate API.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from voxprint_common.i18n import tr
from voxprint_common.ui.presets import (
    PresetFile,
    PresetMatch,
    PresetStatus,
    SettingState,
    reference_presets,
)
from voxprint_common.ui.safety import (
    AdultPromptPolicy,
    ReviewedOutput,
    SafetyGate,
    adult_prompt_policy,
)
from voxprint_common.ui.section_view import install_sections, sync_sections
from voxprint_common.ui.sections import SectionDeck, SectionSet
from voxprint_common.ui.settings import (
    Settings,
    SettingSpec,
    SettingType,
    format_setting_value,
    reference_settings,
    spin_suffix,
)
from voxprint_common.ui.theme import CARD_PLAIN, CONTROL_BG, TEXT
from voxprint_common.ui.tokens import load
from voxprint_common.ui.variants import (
    AppSurface,
    InputKind,
    ResultKind,
    VariantId,
    surface_for,
)

__all__ = [
    "GalleryItem",
    "Phase",
    "PresetChoice",
    "PresetRow",
    "SimpleImageLayout",
    "SimpleImageWidget",
    "SimpleLayout",
    "SimpleWidget",
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
_SOURCE_ID = "image.source"
_MASK_ID = "image.mask"
_VOICE_KEY = "shell.voice"
GenerateHook = Callable[[Mapping[str, object]], None]


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
    """Headless state for the shared simple screen. Qt is not imported.

    Pass a preset file to share one settings state with Expert mode. The chips
    and the expert values read and write that state. Rows alone keep a chip
    list. A :class:`~voxprint_common.ui.safety.SafetyGate` reviews the prompt
    before a picture starts and the picture before it is kept. ``full_layout``
    is a host API for a dock. It is off unless a caller asks, and only while
    Expert mode is on. The widget does not draw that switch.
    """

    def __init__(
        self,
        rows: Iterable[PresetRow] = (),
        *,
        presets: PresetFile | None = None,
        settings: Settings | None = None,
        state_dir: Path | None = None,
        gate: SafetyGate | None = None,
        state_name: str = "image-settings.json",
        history_name: str = _HISTORY_NAME,
        section_set: SectionSet | None = None,
        capture: Callable[[str, str], Mapping[str, object]] | None = None,
        restore: Callable[[str, str, Mapping[str, object]], None] | None = None,
        variant: VariantId | None = None,
        source_picture: bool = False,
        mask: bool = False,
    ) -> None:
        """Copy ``rows`` and select each row's default.

        Pass ``presets`` to drive the chips and the expert values from one
        state. ``rows`` alone keeps the older chip list with no shared values.

        Args:
            rows: Preset rows. Ids must be unique. Ignored when ``presets`` is set.
            presets: Validated preset file. Its groups become the rows.
            settings: Descriptors for the shared state. Used with ``presets``.
            state_dir: The app's State directory. History and the settings
                state are read and written there. None keeps both in memory.
            gate: The app's review of the prompt and of each result. An image
                screen requires one. None blocks a non-image screen.
            state_name: Settings file name inside ``state_dir``.
            history_name: History file name inside ``state_dir``.
            section_set: Sections and wizards. None keeps the single shared screen.
            capture: Returns the app's wizard blob. The shell stores it and does not read it.
            restore: Receives that blob when a checkpoint is loaded.
            variant: Which surface this is. None means the image screen.
            source_picture: Show the simple "Add a picture" drop and store its path.
            mask: Store a mask path for an Expert field or :meth:`set_mask_path`.

        Raises:
            TypeError: An image screen was built without a gate.
            ValueError: Two rows share an id, a source or mask was asked for
                on a screen that is not an image screen, or those slots were
                asked for without a shared settings state.
        """
        self.variant = variant if variant is not None else VariantId.IMAGE
        if self.variant is VariantId.IMAGE and gate is None:
            raise TypeError("an image screen needs a gate")
        if (source_picture or mask) and self.variant is not VariantId.IMAGE:
            raise ValueError("a source picture and a mask belong to an image screen")
        self.source_picture = source_picture
        self.mask = mask
        if presets is not None:
            rows = _rows_from(presets)
        self._rows = tuple(rows)
        ids = [row.row_id for row in self._rows]
        if len(ids) != len(set(ids)):
            raise ValueError("preset row ids must be unique")
        self._state_dir = state_dir
        self._history_name = history_name or _HISTORY_NAME
        self._gate = gate
        held = settings
        if source_picture or mask:
            if presets is None:
                raise ValueError("a source picture and a mask need the shared settings state")
            if held is None:
                held = reference_settings()
            _add_image_inputs(held, source=source_picture, mask=mask)
        elif held is None and presets is not None:
            held = reference_settings()
        self._state = (
            SettingState(
                presets,
                held or reference_settings(),
                state_dir=state_dir,
                state_name=state_name,
            )
            if presets is not None
            else None
        )
        self.prompt = ""
        self.video_source = ""
        self.video_output = ""
        self.audio_output = ""
        self.chapters: tuple[tuple[str, int], ...] = ()
        self._input_path = ""
        self._voices: tuple[tuple[str, str], ...] = ()
        self._voice_id = ""
        self._custom_voices = False
        self._seed = 0
        self.phase = Phase.EMPTY
        self.step = 0
        self.total = 0
        self.stage_label = ""
        self.eta_seconds: int | None = None
        self.reason = ""
        self.presets = (
            dict(self._state.chosen)
            if self._state is not None
            else {row.row_id: row.default for row in self._rows}
        )
        self.history: list[GalleryItem] = []
        self.selected: str | None = None
        self.full_layout = False
        self.sections = (
            SectionDeck(
                section_set,
                self._state,
                state_dir=state_dir,
                capture=capture,
                restore=restore,
            )
            if section_set is not None and self._state is not None
            else None
        )
        self._load_history()

    @property
    def rows(self) -> tuple[PresetRow, ...]:
        """Return the preset rows in order."""
        return self._rows

    @property
    def state(self) -> SettingState | None:
        """Return the shared settings state, or None when only rows were given."""
        return self._state

    @property
    def seed(self) -> int:
        """Return the seed stored in the shared state."""
        if self._state is not None and self._state.has("shell.seed"):
            value = self._state.get("shell.seed")
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return self._seed

    @seed.setter
    def seed(self, value: int) -> None:
        """Store ``value`` in the shared state.

        Args:
            value: Seed number.
        """
        number = int(value)
        self._seed = number
        if (
            self._state is not None
            and self._state.has("shell.seed")
            and self._state.get("shell.seed") != number
        ):
            self._state.set_value("shell.seed", number)

    def set_prompt(self, text: str) -> None:
        """Replace the prompt text.

        Args:
            text: The words in the prompt box, including a blank string.
        """
        self.prompt = text

    def set_video_source(self, path: str) -> None:
        """Remember the film to preview before a dub.

        Args:
            path: The chosen video. An empty string clears the preview.
        """
        self.video_source = path

    def set_video_output(self, path: str) -> None:
        """Remember the dubbed file to play after a run.

        Args:
            path: The finished video, including its folder.
        """
        self.video_output = path

    def set_audio_output(self, path: str) -> None:
        """Remember the finished audio file.

        An empty path leaves the painted waveform in place. A path shows the
        player.

        Args:
            path: The finished file, including its folder.
        """
        self.audio_output = path.strip()

    def set_chapters(self, chapters: Iterable[tuple[str, int]]) -> None:
        """Remember chapter titles and the time each one starts.

        Args:
            chapters: ``(title, start_ms)`` pairs. An empty list clears them.

        Raises:
            ValueError: A title is blank or a start time is negative.
        """
        parsed: list[tuple[str, int]] = []
        for title, start in chapters:
            text = str(title).strip()
            if not text or isinstance(start, bool) or not isinstance(start, int) or start < 0:
                raise ValueError("a chapter needs a title and a start time")
            parsed.append((text, start))
        self.chapters = tuple(parsed)

    def input_path(self) -> str:
        """Return the absolute path of the dropped or picked file.

        Returns:
            The path, or an empty string when the input is only a name.
        """
        return self._input_path

    def set_input_path(self, path: str) -> None:
        """Keep the absolute path of a dropped or picked file.

        Args:
            path: A file path. An empty string clears it. A relative path is
                resolved from the current directory.
        """
        raw = path.strip()
        if not raw:
            self._input_path = ""
            return
        self._input_path = str(Path(raw).expanduser().resolve())

    def set_voices(self, voices: Iterable[tuple[str, str]]) -> None:
        """Replace the voice list. The selected id is kept when it is still there.

        Args:
            voices: ``(id, label)`` pairs. Labels are already translated.

        Raises:
            ValueError: A voice has no id or label, an id is repeated, or the
                list is empty.
        """
        parsed: list[tuple[str, str]] = []
        seen: set[str] = set()
        for voice_id, label in voices:
            ident = str(voice_id).strip()
            text = str(label).strip()
            if not ident or not text:
                raise ValueError("a voice needs an id and a label")
            if ident in seen:
                raise ValueError(f"voice {ident} is repeated")
            seen.add(ident)
            parsed.append((ident, text))
        if not parsed:
            raise ValueError("set_voices needs at least one voice")
        self._voices = tuple(parsed)
        self._custom_voices = True
        if self._voice_id not in seen:
            self._voice_id = parsed[0][0]
        self._remember_voice()

    def select_voice(self, voice_id: str) -> None:
        """Select a voice by id and store that id in the settings state.

        Args:
            voice_id: One of the ids from :meth:`set_voices` or the built-in list.

        Raises:
            ValueError: The id is not in the current list.
        """
        voices = self.ensure_voices()
        if voice_id not in {item[0] for item in voices}:
            raise ValueError(f"unknown voice {voice_id}")
        self._voice_id = voice_id
        self._remember_voice()

    def ensure_voices(self) -> tuple[tuple[str, str], ...]:
        """Return the voice list, filling the built-in Warm, Clear, and Low names.

        A caller that has used :meth:`set_voices` keeps that list. Otherwise the
        labels follow the current language.
        """
        if not self._custom_voices:
            self._voices = _builtin_voices()
            if self._voice_id not in {item[0] for item in self._voices}:
                self._voice_id = self._voices[0][0]
            self._remember_voice()
        return self._voices

    @property
    def voice_id(self) -> str:
        """Return the selected voice id, or an empty string before a list exists."""
        return self._voice_id

    @property
    def voices(self) -> tuple[tuple[str, str], ...]:
        """Return the current ``(id, label)`` pairs."""
        return self._voices

    def _remember_voice(self) -> None:
        if self._state is None or not self._voice_id:
            return
        self._state.remember(_VOICE_KEY, self._voice_id)

    def choose(self, row_id: str, value: str) -> None:
        """Select the chip ``value`` in ``row_id``.

        Args:
            row_id: Preset row id.
            value: One of that row's choice values.

        Raises:
            ValueError: The row or the value is unknown.
        """
        if self._state is not None:
            self._state.choose(row_id, value)
            self.presets = dict(self._state.chosen)
            return
        row = self._row(row_id)
        choice = None if row is None else self._choice(row, value)
        if choice is None:
            raise ValueError(f"unknown preset {row_id}={value}")
        if not choice.available:
            raise ValueError(f"preset {row_id}={value} is unavailable")
        self.presets[row_id] = value

    def get(self, key: str) -> object:
        """Return a live setting value.

        Args:
            key: Setting id.

        Raises:
            ValueError: This layout has no shared state.
            KeyError: The key is unknown.
        """
        if self._state is None:
            raise ValueError("this layout has no shared settings state")
        return self._state.get(key)

    def set_value(self, key: str, value: object) -> None:
        """Hand-edit a live setting value.

        Args:
            key: Setting id.
            value: The new value.

        Raises:
            ValueError: This layout has no shared state, or the value is invalid.
        """
        if self._state is None:
            raise ValueError("this layout has no shared settings state")
        self._state.set_value(key, value)
        self.presets = dict(self._state.chosen)

    def reset_group(self, group_id: str) -> None:
        """Restore one preset group's values.

        Args:
            group_id: Preset group id.
        """
        if self._state is None:
            raise ValueError("this layout has no shared settings state")
        self._state.reset_group(group_id)
        self.presets = dict(self._state.chosen)

    def preset_status(self, row_id: str) -> PresetStatus | None:
        """Return the chip status for ``row_id``.

        Args:
            row_id: Preset group id.
        """
        if self._state is None or self._state.presets.group(row_id) is None:
            return None
        return self._state.status(row_id)

    def prompt_policy(self) -> AdultPromptPolicy:
        """Return the adult-content prompt policy for the current setting.

        On allows adult content and returns a neutral policy. Off returns a
        general-audience template and negatives. A layout with no shared state
        uses the off policy. This value is not the minors gate.
        """
        allowed = False
        if self._state is not None and self._state.has("image.adult_content"):
            allowed = self._state.get("image.adult_content") is True
        return adult_prompt_policy(allowed=allowed)

    def snapshot(self) -> dict[str, object]:
        """Return the shared values, or the chip selection when there is no state."""
        if self._state is None:
            return {"presets": dict(self.presets), "seed": self.seed}
        return self._state.snapshot()

    def live_settings(self) -> dict[str, object]:
        """Return the live values the generator hook receives.

        With a shared settings state, the keys are the ones the Expert panel
        shows, including steps, guidance, sampler, seed, and resolution, plus
        the preset values stored beside them. Without that state, the mapping
        is the chip selection and the seed.
        """
        if self._state is None:
            values: dict[str, object] = {"presets": dict(self.presets), "seed": self.seed}
        else:
            values = dict(self._state.values)
        if self._voice_id:
            values[_VOICE_KEY] = self._voice_id
        return values

    def set_source_path(self, path: str) -> None:
        """Store the source picture path in the shared settings state.

        Args:
            path: A file path. An empty string clears it.

        Raises:
            ValueError: This screen has no source-picture slot.
        """
        if not self.source_picture:
            raise ValueError("this screen has no source picture")
        self.set_value(_SOURCE_ID, path)

    def set_mask_path(self, path: str) -> None:
        """Store the mask path. The simple screen does not draw an editor.

        The Expert panel shows the field when the mask slot is enabled. An app
        can also call this method from its own editor.

        Args:
            path: A file path. An empty string clears it.

        Raises:
            ValueError: This screen has no mask slot.
        """
        if not self.mask:
            raise ValueError("this screen has no mask")
        self.set_value(_MASK_ID, path)

    def generate(self, hook: GenerateHook | None = None) -> bool:
        """Start a picture when the prompt has words and the gate allows it.

        ``hook`` runs first, with :meth:`live_settings`, and only then does
        the phase become a clean busy state with Cancel. A second call while
        a job is running is ignored: the Generate button is disabled, and
        this method returns True so the busy job is not reported as a refusal.

        Args:
            hook: Called with the live settings before the phase changes.
                None starts the job with no callback.

        Returns:
            True when a job is running afterwards, including one that was
            already running. False when the prompt is blank or the gate blocks it.
        """
        if self.phase is Phase.BUSY:
            return True
        prompt = self.prompt.strip()
        if not prompt:
            return False
        if not self._review_prompt(prompt):
            return False
        if hook is not None:
            hook(self.live_settings())
        self._clear_run()
        self.phase = Phase.BUSY
        return True

    @property
    def percent(self) -> int:
        """Return how much of the job is done, from 0 through 100."""
        if self.total < 1:
            return 0
        return round(100 * self.step / self.total)

    def progress(
        self,
        step: int,
        total: int,
        stage_label: str,
        *,
        eta_seconds: int | None = None,
    ) -> None:
        """Record how far the current job has got.

        ``stage_label`` is already translated by the app. The percent is
        ``step`` divided by ``total``. ``eta_seconds`` is the remaining time.
        Omit it to clear the estimate.

        Args:
            step: Finished steps, from zero through ``total``.
            total: How many steps the job takes.
            stage_label: Plain words for the current stage.
            eta_seconds: Seconds still left, or None when there is no estimate.

        Raises:
            ValueError: Nothing is running, or the numbers do not fit.
        """
        if self.phase is not Phase.BUSY:
            raise ValueError("progress needs a job that is in progress")
        label = stage_label.strip()
        if total < 1 or step < 0 or step > total or not label:
            raise ValueError("progress needs a step inside the total and a stage label")
        if eta_seconds is not None and (
            isinstance(eta_seconds, bool) or not isinstance(eta_seconds, int) or eta_seconds < 0
        ):
            raise ValueError("eta needs a number of seconds")
        self.step = step
        self.total = total
        self.stage_label = label
        self.eta_seconds = eta_seconds

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

    def finish(self, item_id: str, output: ReviewedOutput | None = None) -> GalleryItem | None:
        """Store a finished picture at the front of the history and show it.

        Args:
            item_id: Stable id for this picture. The caller chooses it.
            output: The picture as a path or as bytes and a mime type. A
                missing picture is blocked. The gate's own yes or no is unchanged.

        Returns:
            The new history item, or None when the output is missing or the gate blocks it.

        Raises:
            ValueError: Nothing is in progress, or ``item_id`` is empty or already used.
        """
        if self.phase is not Phase.BUSY:
            raise ValueError("finish needs a picture that is in progress")
        if not item_id or any(item.item_id == item_id for item in self.history):
            raise ValueError(f"gallery id {item_id!r} is empty or already used")
        if output is None or not output.present():
            self.block(tr("simple.blocked"))
            return None
        if not self._review_result(self.prompt.strip(), item_id, output):
            return None
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
        for row_id, value in item.presets:
            row = self._row(row_id)
            choice = None if row is None else self._choice(row, value)
            if choice is None or not choice.available:
                continue
            if self._state is not None:
                self._state.choose(row_id, value)
            else:
                self.presets[row_id] = value
        if self._state is not None:
            self.presets = dict(self._state.chosen)
        self.seed = item.seed
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

    def more_like(self, item_id: str | None = None, hook: GenerateHook | None = None) -> bool:
        """Start another picture from one already in the strip.

        The prompt and chips are copied from that picture first. ``hook`` runs
        before the phase becomes busy, the same way as :meth:`generate`.

        Args:
            item_id: History item. None uses the picture on screen.
            hook: Called with the live settings before the phase changes.

        Returns:
            True when a picture is running afterwards.
        """
        if self.phase is Phase.BUSY:
            return True
        if self.use_prompt_again(item_id) is None:
            return False
        return self.generate(hook)

    def _review_prompt(self, prompt: str) -> bool:
        gate = self._gate
        if gate is None:
            self.block(tr("simple.blocked"))
            return False
        try:
            reason = gate.review_prompt(prompt)
        except Exception:
            self.block(tr("simple.blocked"))
            return False
        return self._verdict(reason)

    def _review_result(self, prompt: str, item_id: str, output: ReviewedOutput) -> bool:
        gate = self._gate
        if gate is None:
            self.block(tr("simple.blocked"))
            return False
        try:
            reason = gate.review_result(prompt, item_id, output)
        except Exception:
            self.block(tr("simple.blocked"))
            return False
        return self._verdict(reason)

    def _verdict(self, reason: object) -> bool:
        if reason is None:
            return True
        text = reason.strip() if isinstance(reason, str) else ""
        self.block(text or tr("simple.blocked"))
        return False

    def batch_size(self) -> int:
        """Return how many pictures one Generate makes.

        The ``count`` preset is the source. A missing or non-numeric value
        means one picture.
        """
        if self._state is not None and self._state.has("shell.count"):
            stored = self._state.get("shell.count")
            if isinstance(stored, int) and not isinstance(stored, bool) and stored >= 1:
                return stored
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
        self.eta_seconds = None
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
        path = self._state_dir / self._history_name
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
        path = self._state_dir / self._history_name
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
    """Return the reference preset rows from the Canvas preset file.

    The rows are Quality, Style, Size, Output size, Detail, and Number of
    pictures. Style writes a negative template into the shared state and does
    not change the prompt. ``enlarge_installed`` marks 4K available. When it
    is false, 4K stays on the row, greyed out, with a reason and an install action.

    Args:
        enlarge_installed: True when Enlarge can make a 4K picture.
    """
    return _rows_from(reference_presets(reference_settings(), enlarge_installed=enlarge_installed))


def _add_image_inputs(settings: Settings, *, source: bool, mask: bool) -> None:
    """Register the optional source and mask paths on ``settings``."""
    if source and settings.get(_SOURCE_ID) is None:
        settings.add(
            SettingSpec(
                _SOURCE_ID,
                "setting.image.source",
                "setting.image.source.help",
                SettingType.PATH,
                "",
                group="image",
            )
        )
    if mask and settings.get(_MASK_ID) is None:
        settings.add(
            SettingSpec(
                _MASK_ID,
                "setting.image.mask",
                "setting.image.mask.help",
                SettingType.PATH,
                "",
                expert=True,
                group="image",
            )
        )


def _rows_from(document: PresetFile) -> tuple[PresetRow, ...]:
    rows: list[PresetRow] = []
    for group in document.groups:
        choices = tuple(
            PresetChoice(
                option.option_id,
                option.label_key,
                option.group_key,
                option.available,
                option.reason_key,
                option.action_label_key,
                option.action_id,
            )
            for option in group.options
        )
        rows.append(PresetRow(group.group_id, group.label_key, choices, group.default))
    return tuple(rows)


def _builtin_voices() -> tuple[tuple[str, str], ...]:
    """Return Warm, Clear, and Low with stable ids."""
    return (
        ("warm", tr("simple.voice.warm")),
        ("clear", tr("simple.voice.clear")),
        ("low", tr("simple.voice.low")),
    )


class SimpleImageWidget:
    """Qt view of a :class:`SimpleImageLayout`. Construction imports PySide6.

    The screen is an input, one primary action, a result, and a history strip.
    The image configuration keeps a prompt. Enter starts a picture. Shift+Enter
    inserts a line. Size, output size, style, and how many pictures stay on
    screen, with More like this, Save, Open folder, and Use prompt again.
    Improve prompt, when the app supplies it, replaces the text in that same
    box. Expert mode adds a side panel and leaves every other control where it
    was. A long job shows a stage, a percent, an estimate, and Cancel. The
    generator hook runs first, with the live settings, and then the screen
    enters that busy state. A second Generate while busy is ignored because
    the button is disabled. The
    history strip shows a neutral placeholder for each finished item, not a
    photograph. The overflow menu is Open folder, Manage models, Settings, and
    Expert mode. Those commands, and an unavailable chip's action, call
    ``on_command``. Canvas runs ``voxprint-mdl-gui --app canvas`` for Manage
    models. This widget does not start that program.
    """

    def __init__(
        self,
        layout: SimpleImageLayout,
        settings: Settings,
        *,
        on_generate: GenerateHook | None = None,
        on_command: Callable[[str], None] | None = None,
        on_improve: Callable[[str], str] | None = None,
        on_history: Callable[[str, str], None] | None = None,
        on_seed: Callable[[int], None] | None = None,
        on_refresh: Callable[[], None] | None = None,
        on_pause: Callable[[], None] | None = None,
        on_resume: Callable[[], None] | None = None,
        surface: AppSurface | None = None,
    ) -> None:
        """Build the screen.

        Args:
            layout: Headless state. The widget does not replace it.
            settings: Expert settings the app supplied. They go in the side panel.
            on_generate: Called with the live settings before the phase becomes
                busy. None means the screen only changes phase.
            on_command: Called with a command id from Save, Open folder, Expert
                mode, Manage models, Settings, or an unavailable-chip action.
            on_improve: Replaces the prompt text in the same field. None hides
                Improve prompt and Revert. File inputs hide them too.
            on_history: Called with an action id and a history item id. The
                actions are ``open``, ``show_folder``, ``copy``, and ``use_input``.
            on_seed: Called with the restored seed after Use prompt again.
            on_refresh: Called after the screen paints, including a time-left change.
            on_pause: Called from Pause on the busy screen. None hides Pause.
            on_resume: Called from Resume on the busy screen. None hides Resume.
            surface: Which app this screen is. None is Canvas.
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
        self._on_refresh = on_refresh
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._surface = surface if surface is not None else surface_for("canvas")
        self._drop: object | None = None
        self._voice: object | None = None
        self._model: object | None = None
        self._player: object | None = None
        self._chapters: object | None = None
        self._before: object | None = None
        self._after: object | None = None
        self._compare: object | None = None
        self._wipe: object | None = None
        self._document: object | None = None
        self._report: object | None = None
        self._applying = False
        self._resets: dict[str, object] = {}
        self._customs: dict[str, object] = {}
        self._expert_resets: object | None = None
        self._size_orient = ""
        self._chip_groups: dict[str, object] = {}
        self._previous_prompt: str | None = None
        self._handling_enter = False
        self._source: object | None = None
        self._scaled_to: tuple[int, int] | None = None
        self._video_path: object | None = None
        self._video_play: object | None = None
        self._video_seek: object | None = None
        self._video_poster: object | None = None
        self._video_view: object | None = None
        self._video_stack: object | None = None
        self._video_controls: object | None = None
        self._media: object | None = None
        self._loaded_video = ""
        self._seeking = False
        self._video_started = False
        self._value_host: object | None = None
        self._prompt_row: object | None = None
        self._pause_button: object | None = None
        self._resume_button: object | None = None
        self._filling_voice = False
        self._pill_rows: list[object] = []
        self._audio_path: object | None = None
        self._audio_play: object | None = None
        self._audio_seek: object | None = None
        self._audio_deck: object | None = None
        self._audio_chapters: object | None = None
        self._chapter_heading: object | None = None
        self._audio: object | None = None
        self._loaded_audio = ""
        self._audio_seeking = False
        core = self._core

        class _Root(widgets.QWidget):
            def minimumSizeHint(_self: object) -> object:
                return core.QSize(0, 0)

        self.root = _Root()
        self.root.setObjectName("simpleRoot")
        self._build()
        if self._layout.sections is not None:
            install_sections(self)

    def place_chip(self, chip: object) -> None:
        """Keep the status chip out of the expert panel.

        The panel title is the heading. The chip stays in the tree so the
        shell can show it on the dock status row, and so a test can read it.
        It takes no space on this screen.

        Args:
            chip: The shell's status chip. It is moved, not copied.
        """
        layout = self._panel_layout
        if layout.indexOf(chip) >= 0:  # type: ignore[attr-defined]
            layout.removeWidget(chip)  # type: ignore[attr-defined]
        chip.setParent(self.root)  # type: ignore[attr-defined]
        chip.setFixedSize(0, 0)  # type: ignore[attr-defined]

    def set_expert(self, enabled: bool) -> None:
        """Show or hide the expert side panel.

        The window keeps its size. The result view gives up the space.

        Args:
            enabled: True shows the expert settings beside the picture.
        """
        window = self.root.window()  # type: ignore[attr-defined]
        held = None
        if window is not None and window.isVisible():
            held = (window.width(), window.height())
        self._expert_toggle.setChecked(enabled)  # type: ignore[attr-defined]
        self._reflow_expert_toggle()
        self._expert_panel.setVisible(enabled)  # type: ignore[attr-defined]
        _clear(self._expert_form)
        self._expert_resets = None
        if enabled:
            widgets = self._widgets
            title = widgets.QLabel(tr("simple.expert.title"))
            title.setObjectName("expertTitle")
            self._expert_form.addWidget(title)  # type: ignore[attr-defined]
            resets = widgets.QWidget()
            resets.setObjectName("expertResets")
            reset_layout = widgets.QVBoxLayout(resets)
            reset_layout.setContentsMargins(0, 4, 0, 4)
            reset_layout.setSpacing(6)
            self._expert_resets = resets
            self._expert_form.addWidget(resets)  # type: ignore[attr-defined]
            specs = tuple(spec for spec in self._settings.visible(expert=True) if spec.expert)
            if self._layout.sections is not None:
                specs = self._layout.sections.scoped(specs)
            _fill_form(
                widgets,
                self._expert_form,
                specs,
                values=_live_values(self._layout, specs),
                on_change=self._on_expert_edit,
            )
            self._expert_form.addStretch(1)  # type: ignore[attr-defined]
            self._refresh_resets()
            self._fit_expert_panel()
        if held is not None and window is not None:
            window.resize(held[0], held[1])

    def set_note(self, text: str) -> None:
        """Show a short line under the history, or hide it when ``text`` is empty.

        Args:
            text: Already translated.
        """
        self._note.setText(text)  # type: ignore[attr-defined]
        self._note.setVisible(bool(text))  # type: ignore[attr-defined]

    def set_voices(self, voices: Iterable[tuple[str, str]]) -> None:
        """Replace the voice combo and store the selected id.

        Args:
            voices: ``(id, label)`` pairs. Labels are already translated.
        """
        self._layout.set_voices(voices)
        self._fill_voices()

    def input_path(self) -> str:
        """Return the absolute path of the dropped or picked file."""
        return self._layout.input_path()

    def set_input_path(self, path: str) -> None:
        """Store a dropped or picked file and show only its name.

        The tooltip keeps the absolute path.

        Args:
            path: A file path. An empty string clears the file.
        """
        self._layout.set_input_path(path)
        stored = self._layout.input_path()
        self._layout.set_prompt(stored)
        if self._surface.video_result:
            self._layout.set_video_source(stored)
        self._show_input(stored)
        if self._surface.video_result and self._layout.phase is Phase.EMPTY and stored:
            self._paint_video()
            self._stack.setCurrentIndex(2)  # type: ignore[attr-defined]

    def set_audio_output(self, path: str) -> None:
        """Point the player at a finished audio file.

        Args:
            path: The file to play. An empty string returns to the waveform.
        """
        self._layout.set_audio_output(path)

    def set_chapters(self, chapters: Iterable[tuple[str, int]]) -> None:
        """Show a chapter list. Choosing one seeks to that chapter and plays it.

        Args:
            chapters: ``(title, start_ms)`` pairs.
        """
        self._layout.set_chapters(chapters)

    def pause(self) -> None:
        """Call the app's pause callback. Nothing happens when none was supplied."""
        if self._on_pause is not None:
            self._on_pause()

    def resume(self) -> None:
        """Call the app's resume callback. Nothing happens when none was supplied."""
        if self._on_resume is not None:
            self._on_resume()

    def set_prompt_text(self, text: str) -> None:
        """Put ``text`` in the input and the layout.

        A file path on a file or media input is stored as an absolute path.
        The drop shows the file name.

        Args:
            text: Prompt words, a file name, or a model name.
        """
        cleaned = text.strip()
        kind = self._surface.input_kind
        if kind in {InputKind.FILE, InputKind.MEDIA_VOICE} and _looks_like_path(cleaned):
            self.set_input_path(cleaned)
            return
        self._layout.set_prompt(text)
        if self._surface.video_result:
            self._layout.set_video_source(text.strip())
        self._show_input(text)
        if self._surface.video_result and self._layout.phase is Phase.EMPTY and text.strip():
            self._paint_video()
            self._stack.setCurrentIndex(2)  # type: ignore[attr-defined]

    def refresh(self, *, prompt: bool = False) -> None:
        """Paint the current phase, chips, and history.

        Args:
            prompt: Also copy the layout prompt into the box.
        """
        if prompt:
            self._show_input(self._layout.prompt)
        self._fill_voices()
        self._sync_presets()
        self._reflow_expert_toggle()
        self._paint_value_labels()
        self._paint_expert()
        self._fill_history()
        self._apply_history()
        self._paint_time_left()
        if self._on_refresh is not None:
            self._on_refresh()
        busy = self._layout.phase is Phase.BUSY
        if self._generate_button is not None:
            self._generate_button.setEnabled(not busy)  # type: ignore[attr-defined]
        if self._layout.sections is not None:
            sync_sections(self)
            if not self._layout.sections.shows_screen():
                return
        phase = self._layout.phase
        if phase is Phase.EMPTY:
            if self._surface.video_result and self._layout.video_source.strip():
                self._paint_video()
                self._stack.setCurrentIndex(2)  # type: ignore[attr-defined]
                return
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
        image = self._surface.variant is VariantId.IMAGE
        if confirming:
            question_key = "simple.history.delete.confirm" if image else "simple.history.delete.job"
            question = menu.addAction(tr(question_key))
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
        if image:
            menu.addAction(tr("simple.menu.use_prompt"), lambda: self._use(item_id))
            menu.addAction(
                tr("simple.history.use_input"),
                lambda: self._notify_history("use_input", item_id),
            )
        else:
            menu.addAction(tr("simple.menu.use_again"), lambda: self._use(item_id))
        menu.addAction(tr("simple.history.delete"), lambda: self._ask_delete(item_id))
        return menu

    def _build(self) -> None:
        widgets = self._widgets
        qt = self._qt
        core = self._core
        root = widgets.QVBoxLayout(self.root)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        self._top = widgets.QHBoxLayout()
        self._top.setSpacing(8)
        self._top.addStretch(1)
        self._overflow = widgets.QPushButton(f"{tr('shell.toolbar.overflow')}  ▾")
        self._overflow.setObjectName("overflow")
        self._overflow.setAutoDefault(False)
        _fit_plain(self._overflow, pad=28)
        overflow = widgets.QMenu(self.root)
        overflow.addAction(
            tr("simple.menu.open_folder"),
            lambda: self._command("file.open_folder"),
        )
        overflow.addAction(tr("simple.menu.models"), lambda: self._command("models.manage"))
        overflow.addAction(tr("simple.menu.settings"), lambda: self._command("view.settings"))
        overflow.addAction(
            tr("command.view.toggle_expert_mode"),
            lambda: self._command("view.toggle_expert_mode"),
        )
        self._overflow.setMenu(overflow)
        self._top.addWidget(self._overflow)
        self._expert_toggle = widgets.QPushButton(tr("command.view.toggle_expert_mode"))
        self._expert_toggle.setObjectName("expertToggle")
        self._expert_toggle.setCheckable(True)
        self._expert_toggle.setAutoDefault(False)
        self._expert_toggle.clicked.connect(lambda: self._command("view.toggle_expert_mode"))
        # The checked label is weight 600, same as a selected chip.
        _fit_chip(self._expert_toggle)
        self._top.addWidget(self._expert_toggle)
        root.addLayout(self._top)
        self._prompt = None
        self._generate_button = None
        if self._surface.input_kind is not InputKind.NONE:
            prompt_row = widgets.QHBoxLayout()
            prompt_row.setSpacing(12)
            self._prompt_row = prompt_row
        if self._surface.input_kind in {InputKind.PROMPT, InputKind.OUTLINE}:
            self._prompt = widgets.QPlainTextEdit()
            self._prompt.setObjectName("promptBox")
            self._prompt.setPlaceholderText(tr(self._surface.placeholder_key))
            self._prompt.setFixedHeight(84)
            prompt_col = widgets.QVBoxLayout()
            prompt_col.setSpacing(6)
            prompt_col.addWidget(self._prompt, 1)
            tools = widgets.QHBoxLayout()
            tools.setSpacing(8)
            self._improve = widgets.QPushButton(tr("simple.improve"))
            self._improve.setObjectName("improvePrompt")
            self._improve.setAutoDefault(False)
            self._improve.clicked.connect(self._improve_prompt)
            _fit_plain(self._improve, pad=28)
            self._revert = widgets.QPushButton(tr("simple.revert"))
            self._revert.setObjectName("revertPrompt")
            self._revert.setAutoDefault(False)
            self._revert.setEnabled(False)
            self._revert.clicked.connect(self._revert_prompt)
            _fit_plain(self._revert, pad=28)
            tools.addWidget(self._improve)
            tools.addWidget(self._revert)
            tools.addStretch(1)
            prompt_col.addLayout(tools)
            if self._layout.source_picture:
                prompt_col.insertWidget(1, self._source_drop())
            if self._on_improve is None or self._surface.input_kind is not InputKind.PROMPT:
                self._improve.hide()
                self._revert.hide()
            prompt_row.addLayout(prompt_col, 1)
        elif self._surface.input_kind is not InputKind.NONE:
            prompt_row.addLayout(self._source_column(), 1)
        if self._surface.input_kind is not InputKind.NONE:
            self._generate_button = widgets.QPushButton(tr(self._surface.action_key))
            self._generate_button.setObjectName("generate")
            self._generate_button.setAutoDefault(False)
            self._generate_button.setMinimumSize(148, 64)
            # The action is 16 px and weight 600. The width follows that font.
            _fit_bold(self._generate_button, pad=40, pixel=16)
            self._generate_button.clicked.connect(self._generate)
            prompt_row.addWidget(self._generate_button)
            root.addLayout(prompt_row)
        self._preset_host = widgets.QWidget()
        self._preset_host.setSizePolicy(
            widgets.QSizePolicy.Policy.Preferred,
            widgets.QSizePolicy.Policy.Maximum,
        )
        self._preset_rows = widgets.QVBoxLayout(self._preset_host)
        self._preset_rows.setContentsMargins(0, 0, 0, 0)
        self._preset_rows.setSpacing(8)
        root.addWidget(self._preset_host)
        self._fill_presets()
        if not self._layout.rows:
            self._preset_host.hide()
            self._preset_host.setFixedHeight(0)
        self._install_value_labels(root)

        class _Stage(widgets.QFrame):
            def minimumSizeHint(_self: object) -> object:
                return core.QSize(0, 0)

        self._stage = _Stage()
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
        core = self._core

        class _Panel(widgets.QFrame):
            def minimumSizeHint(_self: object) -> object:
                return core.QSize(220, 0)

            def sizeHint(_self: object) -> object:
                hint = widgets.QFrame.sizeHint(_self)  # type: ignore[arg-type]
                return core.QSize(280, hint.height())

        self._expert_panel = _Panel()
        self._expert_panel.setObjectName("expertPanel")
        self._expert_panel.setAttribute(qt.WA_StyledBackground, True)
        self._expert_panel.setMinimumWidth(220)
        self._expert_panel.setMaximumWidth(420)
        self._expert_panel.setMinimumHeight(0)
        panel_policy = widgets.QSizePolicy(
            widgets.QSizePolicy.Policy.Preferred,
            widgets.QSizePolicy.Policy.Expanding,
        )
        self._expert_panel.setSizePolicy(panel_policy)
        self._panel_layout = widgets.QVBoxLayout(self._expert_panel)
        self._panel_layout.setContentsMargins(12, 10, 12, 10)
        self._panel_layout.setSpacing(8)

        class _Scroll(widgets.QScrollArea):
            def minimumSizeHint(_self: object) -> object:
                return core.QSize(0, 0)

            def sizeHint(_self: object) -> object:
                parent = _self.parentWidget()  # type: ignore[attr-defined]
                height = parent.height() if parent is not None else 480
                return core.QSize(260, max(height, 240))

        scroll = _Scroll()
        scroll.setObjectName("expertScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(widgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(200)
        scroll.setSizePolicy(
            widgets.QSizePolicy.Policy.Expanding,
            widgets.QSizePolicy.Policy.Expanding,
        )
        inner = widgets.QWidget()
        inner.setObjectName("expertForm")
        self._expert_form = widgets.QVBoxLayout(inner)
        self._expert_form.setContentsMargins(0, 0, 0, 0)
        self._expert_form.setSpacing(4)
        scroll.setWidget(inner)
        _darken(scroll, self._gui)
        self._panel_layout.addWidget(scroll, 1)
        self._expert_panel.hide()
        self._centre_split = widgets.QSplitter(qt.Horizontal)
        self._centre_split.setObjectName("expertSplit")
        self._centre_split.setChildrenCollapsible(False)
        self._centre_split.addWidget(self._stage)
        self._centre_split.addWidget(self._expert_panel)
        self._centre_split.setStretchFactor(0, 1)
        self._centre_split.setStretchFactor(1, 0)
        self._fitting_panel = False
        self._install_panel_fit()
        root.addWidget(self._centre_split, 1)
        self._history_scroll = widgets.QScrollArea()
        self._history_scroll.setObjectName("historyStrip")
        self._history_scroll.setWidgetResizable(True)
        self._history_scroll.setFrameShape(widgets.QFrame.Shape.NoFrame)
        self._history_scroll.setHorizontalScrollBarPolicy(qt.ScrollBarAsNeeded)
        self._history_scroll.setVerticalScrollBarPolicy(qt.ScrollBarAlwaysOff)
        self._history_scroll.setFixedHeight(104)
        history = widgets.QWidget()
        history.setObjectName("historyRow")
        self._history = widgets.QHBoxLayout(history)
        self._history.setContentsMargins(0, 0, 0, 0)
        self._history.setSpacing(8)
        self._history_scroll.setWidget(history)
        _darken(self._history_scroll, self._gui)
        root.addWidget(self._history_scroll)
        self._status_row = widgets.QWidget()
        self._status_row.setObjectName("statusRow")
        status_layout = widgets.QHBoxLayout(self._status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(8)
        self._time_left = widgets.QLabel()
        self._time_left.setObjectName("timeLeft")
        status_layout.addWidget(self._time_left)
        status_layout.addStretch(1)
        self._status_row.hide()
        root.addWidget(self._status_row)
        self._note = widgets.QLabel()
        self._note.setObjectName("simpleNote")
        self._note.hide()
        root.addWidget(self._note)
        self._install_keys()
        self.refresh()

    def _apply_history(self) -> None:
        show = True
        state = self._layout.state
        if state is not None and state.has("viewer.show_history"):
            show = state.get("viewer.show_history") is True
        strip = self._history_scroll
        if show:
            strip.setFixedHeight(104)  # type: ignore[attr-defined]
            strip.show()  # type: ignore[attr-defined]
            return
        strip.hide()  # type: ignore[attr-defined]
        strip.setFixedHeight(0)  # type: ignore[attr-defined]

    def _paint_time_left(self) -> None:
        seconds = self._layout.eta_seconds if self._layout.phase is Phase.BUSY else None
        label = self._time_left
        if seconds is None:
            label.clear()  # type: ignore[attr-defined]
            label.hide()  # type: ignore[attr-defined]
            self._status_row.hide()  # type: ignore[attr-defined]
            return
        if seconds >= 60:
            minutes = max(1, round(seconds / 60))
            label.setText(tr("simple.progress.eta_minutes", minutes=minutes))  # type: ignore[attr-defined]
        else:
            label.setText(tr("simple.progress.eta_seconds", seconds=seconds))  # type: ignore[attr-defined]
        label.show()  # type: ignore[attr-defined]
        self._status_row.show()  # type: ignore[attr-defined]

    def _install_panel_fit(self) -> None:
        core = self._core
        widget = self

        class _Fit(core.QObject):
            def eventFilter(_self: object, _watched: object, event: object) -> bool:
                if event.type() != core.QEvent.Type.Resize:  # type: ignore[attr-defined]
                    return False
                if widget._fitting_panel or not widget._expert_panel.isVisible():  # type: ignore[attr-defined]
                    return False
                sizes = widget._centre_split.sizes()  # type: ignore[attr-defined]
                if not sizes or sizes[-1] != 0:
                    return False
                widget._fitting_panel = True
                try:
                    widget._fit_expert_panel()
                finally:
                    widget._fitting_panel = False
                return False

        watcher = _Fit(self._centre_split)
        self._centre_split.installEventFilter(watcher)  # type: ignore[attr-defined]
        self._panel_fit = watcher

    def _fit_expert_panel(self) -> None:
        split = self._centre_split
        total = split.width()  # type: ignore[attr-defined]
        handle = split.handleWidth()  # type: ignore[attr-defined]
        panel = 280
        available = max(panel, total - handle)
        # A short control list stays at the minimum until that minimum has
        # been the requested width once.
        frame = self._expert_panel
        frame.setMinimumWidth(panel)  # type: ignore[attr-defined]
        split.setSizes([max(1, available - panel), panel])  # type: ignore[attr-defined]
        frame.setMinimumWidth(220)  # type: ignore[attr-defined]

    def _empty_page(self) -> object:
        widgets = self._widgets
        page = widgets.QWidget()
        layout = widgets.QVBoxLayout(page)
        hint = widgets.QLabel(tr(self._surface.empty_key))
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
        percent = widgets.QLabel()
        percent.setObjectName("progressPercent")
        percent.setAlignment(qt.AlignCenter)
        self._percent_label = percent
        layout.addWidget(percent)
        eta = widgets.QLabel()
        eta.setObjectName("progressEta")
        eta.setAlignment(qt.AlignCenter)
        eta.hide()
        self._eta_label = eta
        layout.addWidget(eta)
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
        if self._on_pause is not None:
            pause = widgets.QPushButton(tr("simple.pause"))
            pause.setObjectName("pause")
            pause.setAutoDefault(False)
            _fit_plain(pause, pad=28)
            pause.clicked.connect(self.pause)
            self._pause_button = pause
            cancel_row.addWidget(pause)
        if self._on_resume is not None:
            resume = widgets.QPushButton(tr("simple.resume"))
            resume.setObjectName("resume")
            resume.setAutoDefault(False)
            _fit_plain(resume, pad=28)
            resume.clicked.connect(self.resume)
            self._resume_button = resume
            cancel_row.addWidget(resume)
        cancel = widgets.QPushButton(tr("simple.cancel"))
        cancel.setObjectName("cancel")
        cancel.setAutoDefault(False)
        _fit_plain(cancel, pad=28)
        cancel.clicked.connect(self._cancel)
        cancel_row.addWidget(cancel)
        cancel_row.addStretch(1)
        layout.addLayout(cancel_row)
        layout.addStretch(1)
        return page

    def _ready_page(self) -> object:
        kind = self._surface.result_kind
        if kind is ResultKind.PICTURE:
            return self._picture_page()
        if kind is ResultKind.PLAYER:
            if self._surface.video_result:
                return self._video_page()
            return self._player_page()
        if kind is ResultKind.COMPARE:
            return self._compare_page()
        if kind is ResultKind.DOCUMENT:
            return self._document_page()
        if kind is ResultKind.REPORT:
            return self._report_page()
        assert_never(kind)

    def _picture_page(self) -> object:
        widgets = self._widgets
        page = widgets.QWidget()
        layout = widgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        widget = self

        class _Picture(widgets.QLabel):
            def resizeEvent(_self: object, event: object) -> None:
                widgets.QLabel.resizeEvent(_self, event)  # type: ignore[arg-type]
                widget._rescale()

            def sizeHint(_self: object) -> object:
                return widget._core.QSize(0, 0)

            def minimumSizeHint(_self: object) -> object:
                return widget._core.QSize(0, 0)

        self._picture = _Picture()
        self._picture.setObjectName("result")
        self._picture.setAlignment(self._qt.AlignCenter)
        self._picture.setSizePolicy(
            widgets.QSizePolicy.Policy.Ignored,
            widgets.QSizePolicy.Policy.Ignored,
        )
        layout.addWidget(self._picture, 1)
        return page

    def _player_page(self) -> object:
        """Build the waveform, and the player that replaces it once audio exists."""
        widgets = self._widgets
        page = widgets.QWidget()
        page.setObjectName("audioResult")
        layout = widgets.QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)
        player = widgets.QLabel()
        player.setObjectName("player")
        player.setAlignment(self._qt.AlignCenter)
        player.setMinimumHeight(160)
        self._player = player
        layout.addWidget(player, 1)
        deck = widgets.QWidget()
        deck.setObjectName("audioDeck")
        deck_layout = widgets.QVBoxLayout(deck)
        deck_layout.setContentsMargins(0, 0, 0, 0)
        deck_layout.setSpacing(8)
        controls = widgets.QWidget()
        controls.setObjectName("audioControls")
        row = widgets.QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        play = widgets.QPushButton(tr("simple.video.play"))
        play.setObjectName("audioPlay")
        play.setAutoDefault(False)
        _fit_plain(play, pad=28)
        play.clicked.connect(self._toggle_audio)
        self._audio_play = play
        row.addWidget(play)
        seek = widgets.QSlider(self._qt.Horizontal)
        seek.setObjectName("audioSeek")
        seek.setRange(0, 1)
        seek.setValue(0)
        seek.sliderPressed.connect(self._audio_seek_pressed)
        seek.sliderReleased.connect(self._audio_seek_released)
        self._audio_seek = seek
        row.addWidget(seek, 1)
        deck_layout.addWidget(controls)
        heading = widgets.QLabel(tr("simple.player.chapters"))
        heading.setObjectName("chapters")
        self._chapter_heading = heading
        deck_layout.addWidget(heading)
        chapters = widgets.QListWidget()
        chapters.setObjectName("audioChapters")
        chapters.setMaximumHeight(96)
        chapters.setSpacing(2)
        chapters.itemClicked.connect(self._play_chapter)
        self._audio_chapters = chapters
        deck_layout.addWidget(chapters)
        path_row = widgets.QHBoxLayout()
        path_row.setSpacing(8)
        path = widgets.QLabel()
        path.setObjectName("audioPath")
        path.setWordWrap(True)
        path.setTextInteractionFlags(self._qt.TextSelectableByMouse)
        self._audio_path = path
        path_row.addWidget(path, 1)
        open_folder = widgets.QPushButton(tr("simple.menu.open_folder"))
        open_folder.setObjectName("audioOpen")
        open_folder.setAutoDefault(False)
        _fit_plain(open_folder, pad=28)
        open_folder.clicked.connect(lambda: self._command("file.open_folder"))
        path_row.addWidget(open_folder)
        deck_layout.addLayout(path_row)
        self._audio_deck = deck
        deck.hide()
        layout.addWidget(deck, 1)
        self._open_audio(page)
        return page

    def _video_page(self) -> object:
        """Build the optional video result: preview, then the dubbed file."""
        widgets = self._widgets
        page = widgets.QWidget()
        page.setObjectName("videoResult")
        layout = widgets.QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)
        stack = widgets.QStackedWidget()
        poster = self._poster_label()
        stack.addWidget(poster)
        self._video_poster = poster
        self._video_stack = stack
        self._open_media(page, stack)
        layout.addWidget(stack, 1)
        controls = widgets.QWidget()
        controls.setObjectName("videoControls")
        row = widgets.QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        play = widgets.QPushButton(tr("simple.video.play"))
        play.setObjectName("videoPlay")
        play.setAutoDefault(False)
        _fit_plain(play, pad=28)
        play.clicked.connect(self._toggle_video)
        self._video_play = play
        row.addWidget(play)
        seek = widgets.QSlider(self._qt.Horizontal)
        seek.setObjectName("videoSeek")
        seek.setRange(0, 1)
        seek.setValue(0)
        seek.sliderPressed.connect(self._seek_pressed)
        seek.sliderReleased.connect(self._seek_released)
        self._video_seek = seek
        row.addWidget(seek, 1)
        self._video_controls = controls
        if self._media is None:
            controls.hide()
        layout.addWidget(controls)
        path_row = widgets.QHBoxLayout()
        path_row.setSpacing(8)
        path = widgets.QLabel()
        path.setObjectName("videoPath")
        path.setWordWrap(True)
        path.setTextInteractionFlags(self._qt.TextSelectableByMouse)
        self._video_path = path
        path_row.addWidget(path, 1)
        open_folder = widgets.QPushButton(tr("simple.menu.open_folder"))
        open_folder.setObjectName("videoOpen")
        open_folder.setAutoDefault(False)
        _fit_plain(open_folder, pad=28)
        open_folder.clicked.connect(lambda: self._command("file.open_folder"))
        path_row.addWidget(open_folder)
        layout.addLayout(path_row)
        poster.setPixmap(_film_poster(self._gui, 640, 360))
        return page

    def _poster_label(self) -> object:
        widgets = self._widgets
        core = self._core

        class _Poster(widgets.QLabel):
            def sizeHint(_self: object) -> object:
                return core.QSize(0, 0)

            def minimumSizeHint(_self: object) -> object:
                return core.QSize(0, 0)

        poster = _Poster()
        poster.setObjectName("videoPoster")
        poster.setAlignment(self._qt.AlignCenter)
        poster.setMinimumHeight(160)
        poster.setScaledContents(True)
        poster.setSizePolicy(
            widgets.QSizePolicy.Policy.Ignored,
            widgets.QSizePolicy.Policy.Ignored,
        )
        return poster

    def _open_media(self, page: object, stack: object) -> None:
        """Attach Qt Multimedia, or leave the poster as the fallback."""
        loaded = _qt_media()
        if loaded is None:
            return
        player_cls, audio_cls, video_cls = loaded
        try:
            view = video_cls()
            player = player_cls(page)
            audio = audio_cls(page)
            player.setAudioOutput(audio)
            player.setVideoOutput(view)
        except AttributeError, ImportError, RuntimeError:
            return
        view.hide()
        stack.addWidget(view)  # type: ignore[attr-defined]
        self._video_view = view
        self._media = player
        player.playbackStateChanged.connect(self._on_video_state)
        player.positionChanged.connect(self._on_video_position)
        player.durationChanged.connect(self._on_video_duration)
        player.errorOccurred.connect(self._on_video_error)

    def _compare_page(self) -> object:
        widgets = self._widgets
        page = widgets.QWidget()
        layout = widgets.QGridLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        frame = widgets.QLabel()
        frame.setObjectName("compareView")
        frame.setAlignment(self._qt.AlignCenter)
        frame.setMinimumHeight(0)
        self._compare = frame
        slider = widgets.QSlider(self._qt.Horizontal)
        slider.setObjectName("compareSlider")
        slider.setRange(0, 1000)
        slider.setValue(500)
        slider.valueChanged.connect(lambda _value: self._paint_wipe())
        self._wipe = slider
        layout.addWidget(frame, 0, 0)
        layout.addWidget(slider, 0, 0)

        def _resized(_event: object) -> None:
            widgets.QLabel.resizeEvent(frame, _event)  # type: ignore[arg-type]
            self._paint_wipe()

        frame.resizeEvent = _resized  # type: ignore[method-assign]
        return page

    def _document_page(self) -> object:
        widgets = self._widgets
        page = widgets.QWidget()
        layout = widgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        document = widgets.QPlainTextEdit()
        document.setObjectName("documentView")
        document.setReadOnly(True)
        self._document = document
        layout.addWidget(document, 1)
        return page

    def _report_page(self) -> object:
        widgets = self._widgets
        page = widgets.QWidget()
        layout = widgets.QVBoxLayout(page)
        report = widgets.QLabel()
        report.setObjectName("reportView")
        report.setAlignment(self._qt.AlignCenter)
        report.setWordWrap(True)
        self._report = report
        layout.addWidget(report, 1)
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
        compact: object | None = None
        compact_host: object | None = None
        for row in self._layout.rows:
            group = widgets.QButtonGroup(self.root)
            group.setExclusive(True)
            self._chip_groups[row.row_id] = group
            lines = (("", row.choices),) if row.row_id == "size" else _choice_groups(row.choices)
            for index, (group_key, choices) in enumerate(lines):
                shared = compact if row.row_id == "detail" and index == 0 else None
                if shared is None:
                    host, line = _pill_flow(widgets, self._core)
                    self._pill_rows.append(host)
                else:
                    host = compact_host
                    line = shared
                    gap = widgets.QWidget()
                    gap.setFixedWidth(16)
                    line.addWidget(gap)  # type: ignore[attr-defined]
                label = widgets.QLabel(tr(row.label_key) if index == 0 else "")
                if index == 0:
                    label.setObjectName(f"preset-{row.row_id}")
                label.setProperty("presetLabel", "true")
                label.setMinimumWidth(72 if shared is not None else 108)
                line.addWidget(label)
                if row.row_id == "size" and index == 0:
                    seen: list[str] = []
                    for choice in row.choices:
                        key = choice.group_key
                        if not key or key in seen:
                            continue
                        seen.append(key)
                        orient = widgets.QPushButton(tr(key))
                        orient.setObjectName(f"orient-{key.rsplit('.', 1)[-1]}")
                        orient.setProperty("chip", "true")
                        orient.setCheckable(True)
                        orient.setAutoDefault(False)
                        orient.clicked.connect(
                            lambda _checked=False, group_key=key: self._set_orientation(group_key)
                        )
                        line.addWidget(orient)
                        _fit_chip(orient)
                if group_key and row.row_id != "size":
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
                    _fit_chip(button)
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
                    _fit_plain(action, pad=28)
                    line.addWidget(action)
                if index == 0 and self._layout.state is not None:
                    custom = widgets.QPushButton(tr("simple.preset.custom"))
                    custom.setObjectName(f"chip-{row.row_id}-custom")
                    custom.setProperty("chip", "true")
                    custom.setCheckable(True)
                    custom.setAutoDefault(False)
                    custom.hide()
                    custom.clicked.connect(
                        lambda _checked=False, row_id=row.row_id: self._sync_presets()
                    )
                    self._customs[row.row_id] = custom
                    line.addWidget(custom)
                    reset = widgets.QPushButton(tr("simple.preset.reset", name=""))
                    reset.setObjectName(f"reset-{row.row_id}")
                    reset.setAutoDefault(False)
                    reset.hide()
                    reset.clicked.connect(
                        lambda _checked=False, row_id=row.row_id: self._reset_group(row_id)
                    )
                    self._resets[row.row_id] = reset
                    line.addWidget(reset)
                if row.row_id == "output":
                    compact = line
                    compact_host = host
                    self._preset_rows.addWidget(host)  # type: ignore[attr-defined]
                    continue
                if shared is None:
                    self._preset_rows.addWidget(host)  # type: ignore[attr-defined]
                compact = None
                compact_host = None

    def _sync_presets(self) -> None:
        for row in self._layout.rows:
            status = self._layout.preset_status(row.row_id)
            selected = self._layout.presets[row.row_id]
            kind = None if status is None else status.kind
            group = self._chip_groups.get(row.row_id)
            if group is not None and kind is PresetMatch.CUSTOM:
                group.setExclusive(False)  # type: ignore[attr-defined]
            for choice in row.choices:
                button = self.root.findChild(
                    self._widgets.QPushButton,
                    _chip_name(row.row_id, choice.value),
                )
                if button is None or not button.isCheckable():
                    continue
                button.setText(_chip_text(choice.label_key, selected, choice.value, kind))
                button.setChecked(_chip_checked(selected, choice.value, kind))
                _fit_chip(button)
            if group is not None and kind is not PresetMatch.CUSTOM:
                group.setExclusive(True)  # type: ignore[attr-defined]
            custom = self._customs.get(row.row_id)
            if custom is not None:
                custom.setVisible(kind is PresetMatch.CUSTOM)  # type: ignore[attr-defined]
                custom.setChecked(kind is PresetMatch.CUSTOM)  # type: ignore[attr-defined]
                _fit_chip(custom)
            reset = self._resets.get(row.row_id)
            if reset is None or status is None:
                continue
            reset.setVisible(kind is not PresetMatch.EXACT)  # type: ignore[attr-defined]
            reset.setText(tr("simple.preset.reset", name=tr(status.label_key)))  # type: ignore[attr-defined]
            _fit_chip(reset)
        for host in self._pill_rows:
            layout = host.layout()  # type: ignore[attr-defined]
            if layout is not None:
                layout.invalidate()
            host.updateGeometry()  # type: ignore[attr-defined]
        self._apply_size_filter()
        self._refresh_resets()

    def _apply_size_filter(self) -> None:
        """Show ratio chips only for the orientation that is selected."""
        rows = [row for row in self._layout.rows if row.row_id == "size"]
        if not rows:
            return
        row = rows[0]
        selected = self._layout.presets.get("size", "")
        current = next((choice for choice in row.choices if choice.value == selected), None)
        orient = current.group_key if current is not None else self._size_orient
        if not orient:
            return
        self._size_orient = orient
        for choice in row.choices:
            button = self.root.findChild(
                self._widgets.QPushButton,
                _chip_name("size", choice.value),
            )
            if button is not None:
                button.setVisible(choice.group_key == orient)  # type: ignore[attr-defined]
        fitted: list[object] = []
        for choice in row.choices:
            if not choice.group_key:
                continue
            marker = self.root.findChild(
                self._widgets.QPushButton,
                f"orient-{choice.group_key.rsplit('.', 1)[-1]}",
            )
            if marker is None or marker in fitted:
                continue
            marker.setChecked(choice.group_key == orient)  # type: ignore[attr-defined]
            _fit_chip(marker)
            fitted.append(marker)

    def _set_orientation(self, group_key: str) -> None:
        rows = [row for row in self._layout.rows if row.row_id == "size"]
        if not rows:
            return
        row = rows[0]
        selected = self._layout.presets.get("size", "")
        current = next((choice for choice in row.choices if choice.value == selected), None)
        if current is not None and current.group_key == group_key:
            self._size_orient = group_key
            self._apply_size_filter()
            return
        nxt = next(
            (
                choice
                for choice in row.choices
                if choice.group_key == group_key and choice.available
            ),
            None,
        )
        if nxt is None:
            return
        self._choose("size", nxt.value)

    def _refresh_resets(self) -> None:
        """Show Reset at the top of the expert panel while a preset has changes."""
        host = self._expert_resets
        if host is None:
            return
        layout = host.layout()  # type: ignore[attr-defined]
        _clear(layout)
        visible = False
        for row in self._layout.rows:
            status = self._layout.preset_status(row.row_id)
            if status is None or status.kind is not PresetMatch.MODIFIED:
                continue
            visible = True
            button = self._widgets.QPushButton(tr("simple.preset.reset", name=tr(status.label_key)))
            button.setObjectName("expertReset")
            button.setAutoDefault(False)
            button.clicked.connect(
                lambda _checked=False, row_id=row.row_id: self._reset_group(row_id)
            )
            _fit_plain(button, pad=24)
            layout.addWidget(button)
        host.setVisible(visible)  # type: ignore[attr-defined]

    def _fill_history(self) -> None:
        widgets = self._widgets
        qt = self._qt
        _clear(self._history)
        group = widgets.QButtonGroup(self.root)
        group.setExclusive(True)
        self._thumb_group = group
        for index, item in enumerate(self._layout.history, start=1):
            button = widgets.QPushButton()
            button.setObjectName(f"thumb-{item.item_id}")
            button.setProperty("thumb", "true")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setFixedSize(88, 88)
            if self._surface.result_kind is ResultKind.PICTURE:
                button.setIcon(self._gui.QIcon(_scene(self._gui, item.item_id, 168, 168)))
                button.setIconSize(self._core.QSize(80, 80))
            else:
                button.setText(str(index))
                button.setToolTip(item.prompt)
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
            button.show()
            button.style().unpolish(button)
            button.style().polish(button)
        self._history.addStretch(1)

    def _action_bar(self) -> object:
        if self._surface.variant is VariantId.VIEWER:
            return self._viewer_bar()
        widgets = self._widgets
        bar = widgets.QWidget()
        bar.setObjectName("actionBar")
        row = widgets.QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 12)
        row.setSpacing(8)
        self._save = self._action_button("savePicture", "simple.menu.save", self._save_current)
        self._open = self._action_button(
            "openFolder",
            "simple.menu.open_folder",
            lambda: self._command("file.open_folder"),
        )
        buttons = [self._save, self._open]
        if self._surface.variant is VariantId.IMAGE:
            self._more = self._action_button("moreLike", "simple.more", self._more_like)
            self._use_button = self._action_button(
                "usePrompt",
                "simple.menu.use_prompt",
                self._use_current,
            )
            buttons = [self._more, self._save, self._open, self._use_button]
        for button in buttons:
            row.addWidget(button)
        row.addStretch(1)
        return bar

    def _viewer_bar(self) -> object:
        widgets = self._widgets
        bar = widgets.QWidget()
        bar.setObjectName("viewerBar")
        row = widgets.QHBoxLayout(bar)
        row.setContentsMargins(8, 8, 8, 0)
        row.setSpacing(6)
        specs = (
            ("viewerOpen", "simple.viewer.open", lambda: self._command("file.open")),
            ("viewerSave", "simple.viewer.save", self._save_current),
            ("viewerPrev", "simple.viewer.previous", self._viewer_previous),
            ("viewerNext", "simple.viewer.next", self._viewer_next),
        )
        for name, key, handler in specs:
            button = widgets.QPushButton(tr(key))
            button.setObjectName(name)
            button.setAutoDefault(False)
            button.setMinimumHeight(28)
            _fit_plain(button, pad=28)
            button.clicked.connect(handler)
            row.addWidget(button)
        row.addStretch(1)
        return bar

    def _viewer_previous(self) -> None:
        self._step_history(-1)

    def _viewer_next(self) -> None:
        self._step_history(1)

    def _step_history(self, step: int) -> None:
        history = self._layout.history
        if not history:
            return
        ids = [item.item_id for item in history]
        current = self._layout.selected
        index = ids.index(current) if current in ids else 0
        self._select(ids[(index + step) % len(ids)])

    def _action_button(self, name: str, label_key: str, handler: Callable[[], None]) -> object:
        button = self._widgets.QPushButton(tr(label_key))
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.setMinimumHeight(34)
        _fit_plain(button, pad=32)
        button.clicked.connect(handler)
        return button

    def _install_keys(self) -> None:
        core = self._core
        edit = self._prompt
        if edit is None:
            return

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

    def _source_column(self) -> object:
        widgets = self._widgets
        column = widgets.QVBoxLayout()
        column.setSpacing(8)
        kind = self._surface.input_kind
        if kind is InputKind.MODEL:
            label = widgets.QLabel(tr("simple.drop.model"))
            label.setObjectName("modelLabel")
            column.addWidget(label)
            model = _eliding_combo()
            model.setObjectName("modelPicker")
            model.addItem(tr("setting.tool.model.standard"))
            model.addItem(tr("setting.tool.model.large"))
            self._model = model
            column.addWidget(model)
            column.addStretch(1)
            return column
        if kind is InputKind.FILE or kind is InputKind.MEDIA_VOICE:
            return self._file_column(column, kind)
        assert_never(kind)

    def _source_drop(self) -> object:
        widgets = self._widgets
        drop = widgets.QLabel(tr("simple.source.add"))
        drop.setObjectName("sourceDrop")
        drop.setAlignment(self._qt.AlignCenter)
        drop.setWordWrap(True)
        drop.setMinimumHeight(64)
        drop.setAcceptDrops(True)
        self._source = drop
        self._install_source_drop(drop)
        return drop

    def _install_source_drop(self, label: object) -> None:
        core = self._core

        class _SourceDrop(core.QObject):
            def eventFilter(_self: object, _watched: object, event: object) -> bool:
                kind = event.type()  # type: ignore[attr-defined]
                if kind == core.QEvent.Type.DragEnter:
                    if event.mimeData().hasUrls():  # type: ignore[attr-defined]
                        event.acceptProposedAction()  # type: ignore[attr-defined]
                        return True
                    return False
                if kind == core.QEvent.Type.Drop:
                    urls = event.mimeData().urls()  # type: ignore[attr-defined]
                    if not urls:
                        return False
                    path = urls[0].toLocalFile() or urls[0].toString()
                    self._layout.set_source_path(path)
                    name = Path(path).name or path
                    label.setText(f"{tr('simple.source.add')}\n{name}")  # type: ignore[attr-defined]
                    event.acceptProposedAction()  # type: ignore[attr-defined]
                    return True
                return False

        watcher = _SourceDrop(label)
        label.installEventFilter(watcher)  # type: ignore[attr-defined]
        self._source_filter = watcher

    def _file_column(self, column: object, kind: InputKind) -> object:
        widgets = self._widgets
        drop = widgets.QLabel(tr(self._surface.drop_key))
        drop.setObjectName("fileDrop")
        drop.setAlignment(self._qt.AlignCenter)
        drop.setWordWrap(True)
        drop.setMinimumHeight(96)
        drop.setAcceptDrops(True)
        self._drop = drop
        self._install_drop(drop)
        column.addWidget(drop, 1)
        if kind is InputKind.MEDIA_VOICE and self._surface.voice_picker:
            row = widgets.QHBoxLayout()
            row.setSpacing(8)
            voice_label = widgets.QLabel(tr("simple.voice"))
            voice_label.setObjectName("voiceLabel")
            voice = _eliding_combo()
            voice.setObjectName("voicePicker")
            self._voice = voice
            self._fill_voices()
            voice.currentIndexChanged.connect(self._on_voice)
            row.addWidget(voice_label)
            row.addWidget(voice, 1)
            column.addLayout(row)
        return column

    def _install_drop(self, label: object) -> None:
        core = self._core

        class _Drop(core.QObject):
            def eventFilter(_self: object, _watched: object, event: object) -> bool:
                kind = event.type()  # type: ignore[attr-defined]
                if kind == core.QEvent.Type.DragEnter:
                    if event.mimeData().hasUrls():  # type: ignore[attr-defined]
                        event.acceptProposedAction()  # type: ignore[attr-defined]
                        return True
                    return False
                if kind == core.QEvent.Type.Drop:
                    urls = event.mimeData().urls()  # type: ignore[attr-defined]
                    if not urls:
                        return False
                    path = urls[0].toLocalFile() or urls[0].toString()
                    self.set_input_path(path)
                    event.acceptProposedAction()  # type: ignore[attr-defined]
                    return True
                return False

        watcher = _Drop(label)
        label.installEventFilter(watcher)  # type: ignore[attr-defined]
        self._drop_filter = watcher

    def _show_input(self, text: str) -> None:
        if self._prompt is not None:
            self._prompt.setPlainText(text)  # type: ignore[attr-defined]
        if self._drop is not None:
            hint = tr(self._surface.drop_key)
            stored = self._layout.input_path()
            if stored and not self._surface.video_result:
                name = Path(stored).name
                self._drop.setToolTip(stored)  # type: ignore[attr-defined]
            else:
                name = text.strip()
                if stored:
                    self._drop.setToolTip(stored)  # type: ignore[attr-defined]
                elif str(self._drop.toolTip()) != "":  # type: ignore[attr-defined]
                    self._drop.setToolTip("")  # type: ignore[attr-defined]
            self._drop.setText(f"{hint}\n{name}" if name else hint)  # type: ignore[attr-defined]
        if self._model is None or not text.strip():
            return
        index = self._model.findText(text)  # type: ignore[attr-defined]
        if index >= 0:
            self._model.setCurrentIndex(index)  # type: ignore[attr-defined]

    def _input_text(self) -> str:
        kind = self._surface.input_kind
        if kind is InputKind.PROMPT or kind is InputKind.OUTLINE:
            return self._prompt.toPlainText()  # type: ignore[attr-defined]
        if kind is InputKind.MODEL:
            return str(self._model.currentText())  # type: ignore[attr-defined]
        if kind is InputKind.FILE or kind is InputKind.MEDIA_VOICE:
            if self._drop is not None and self._drop.text() == tr(self._surface.drop_key):  # type: ignore[attr-defined]
                return ""
            text = self._layout.prompt.strip()
            if kind is InputKind.MEDIA_VOICE and text and self._voice is not None:
                return f"{text} · {self._voice.currentText()}"  # type: ignore[attr-defined]
            return text
        if kind is InputKind.NONE:
            return self._layout.prompt
        assert_never(kind)

    def _generate(self) -> None:
        if self._layout.phase is Phase.BUSY:
            return
        self._layout.set_prompt(self._input_text())
        if not self._layout.prompt.strip():
            kind = self._surface.input_kind
            key = (
                "simple.error.prompt"
                if kind in {InputKind.PROMPT, InputKind.OUTLINE}
                else "simple.error.input"
            )
            self.set_note(tr(key))
            self.refresh()
            return
        if not self._layout.generate(self._on_generate):
            self.refresh()
            return
        self.set_note("")
        self.refresh()

    def _more_like(self) -> None:
        if self._layout.phase is Phase.BUSY:
            return
        if self._layout.item(self._layout.selected) is None:
            self.set_note(tr("simple.error.picture"))
            return
        if not self._layout.more_like(hook=self._on_generate):
            self.refresh()
            return
        self._restore_seed()
        self.set_note("")
        self.refresh(prompt=True)

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

    def _fill_voices(self) -> None:
        combo = self._voice
        if combo is None:
            return
        voices = self._layout.ensure_voices()
        ids = [voice_id for voice_id, _label in voices]
        labels = [label for _voice_id, label in voices]
        current_ids = [combo.itemData(index) for index in range(combo.count())]  # type: ignore[attr-defined]
        current_labels = [combo.itemText(index) for index in range(combo.count())]  # type: ignore[attr-defined]
        if current_ids == ids and current_labels == labels:
            return
        self._filling_voice = True
        combo.blockSignals(True)  # type: ignore[attr-defined]
        combo.clear()  # type: ignore[attr-defined]
        for voice_id, label in voices:
            combo.addItem(label, voice_id)  # type: ignore[attr-defined]
        index = combo.findData(self._layout.voice_id)  # type: ignore[attr-defined]
        combo.setCurrentIndex(0 if index < 0 else index)  # type: ignore[attr-defined]
        combo.blockSignals(False)  # type: ignore[attr-defined]
        self._filling_voice = False
        chosen = combo.currentData()  # type: ignore[attr-defined]
        if isinstance(chosen, str) and chosen != self._layout.voice_id:
            self._layout.select_voice(chosen)

    def _on_voice(self, index: int) -> None:
        if self._filling_voice or self._voice is None or index < 0:
            return
        chosen = self._voice.itemData(index)  # type: ignore[attr-defined]
        if isinstance(chosen, str):
            self._layout.select_voice(chosen)

    def _cancel(self) -> None:
        self._layout.cancel()
        self.refresh()

    def _choose(self, row_id: str, value: str) -> None:
        self._layout.choose(row_id, value)
        self._sync_presets()
        self._paint_expert()

    def _reset_group(self, row_id: str) -> None:
        self._layout.reset_group(row_id)
        self._sync_presets()
        self._paint_expert()

    def _on_expert_edit(self, setting_id: str, value: object) -> None:
        if self._applying or self._layout.state is None:
            return
        self._layout.set_value(setting_id, value)
        self._sync_presets()

    def _paint_expert(self) -> None:
        if self._layout.state is None or not self._expert_panel.isVisible():  # type: ignore[attr-defined]
            return
        specs = tuple(spec for spec in self._settings.visible(expert=True) if spec.expert)
        self._applying = True
        try:
            for spec in specs:
                control = self.root.findChild(self._widgets.QWidget, spec.setting_id)
                if control is None:
                    continue
                control.blockSignals(True)
                _write_control(control, spec, self._layout.get(spec.setting_id))
                if spec.type is SettingType.INT or spec.type is SettingType.FLOAT:
                    _apply_suffix(control, spec)
                control.blockSignals(False)
        finally:
            self._applying = False

    def _select(self, item_id: str) -> None:
        if self._layout.select(item_id) is None:
            return
        self.refresh()

    def _paint_progress(self) -> None:
        label = self._layout.stage_label or tr("simple.progress")
        self._progress_text.setText(label)  # type: ignore[attr-defined]
        total = self._layout.total
        percent = self._percent_label
        eta = self._eta_label
        if total > 0:
            percent.setText(tr("simple.progress.percent", percent=self._layout.percent))  # type: ignore[attr-defined]
            percent.show()  # type: ignore[attr-defined]
            self._progress_bar.setRange(0, total)  # type: ignore[attr-defined]
            self._progress_bar.setValue(self._layout.step)  # type: ignore[attr-defined]
        else:
            percent.hide()  # type: ignore[attr-defined]
            self._progress_bar.setRange(0, 0)  # type: ignore[attr-defined]
        seconds = self._layout.eta_seconds
        if seconds is None:
            eta.hide()  # type: ignore[attr-defined]
            return
        if seconds >= 60:
            minutes = max(1, round(seconds / 60))
            eta.setText(tr("simple.progress.eta_minutes", minutes=minutes))  # type: ignore[attr-defined]
        else:
            eta.setText(tr("simple.progress.eta_seconds", seconds=seconds))  # type: ignore[attr-defined]
        eta.show()  # type: ignore[attr-defined]

    def _reflow_expert_toggle(self) -> None:
        """Resize the Expert toggle from the checked face after a label change."""
        button = self._expert_toggle
        label = tr("command.view.toggle_expert_mode")
        if str(button.text()) != label:  # type: ignore[attr-defined]
            button.setText(label)  # type: ignore[attr-defined]
        width = _chip_width(button)
        if int(button.minimumWidth()) != width:  # type: ignore[attr-defined]
            button.setMinimumWidth(width)  # type: ignore[attr-defined]

    def _simple_value_specs(self) -> tuple[SettingSpec, ...]:
        specs: list[SettingSpec] = []
        for spec in self._settings.visible(expert=False):
            if not spec.suffix_key:
                continue
            if spec.type is SettingType.INT or spec.type is SettingType.FLOAT:
                specs.append(spec)
        return tuple(specs)

    def _spec_value(self, spec: SettingSpec) -> object:
        state = self._layout.state
        if state is not None and state.has(spec.setting_id):
            return self._layout.get(spec.setting_id)
        return spec.default

    def _paint_value_labels(self) -> None:
        if self._value_host is None:
            return
        for spec in self._simple_value_specs():
            label = self.root.findChild(self._widgets.QLabel, f"value-{spec.setting_id}")
            if label is None:
                continue
            label.setText(format_setting_value(spec, self._spec_value(spec)))  # type: ignore[attr-defined]

    def _install_value_labels(self, root: object) -> None:
        specs = self._simple_value_specs()
        if not specs:
            return
        widgets = self._widgets
        host = widgets.QWidget()
        host.setObjectName("simpleValues")
        column = widgets.QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        for spec in specs:
            label = widgets.QLabel(format_setting_value(spec, self._spec_value(spec)))
            label.setObjectName(f"value-{spec.setting_id}")
            label.setWordWrap(True)
            column.addWidget(label)
        self._value_host = host
        root.addWidget(host)  # type: ignore[attr-defined]

    def _paint_video(self) -> None:
        """Show the source before a run, and the dubbed file once it is ready."""
        if self._video_path is None:
            return
        ready = self._layout.phase is Phase.READY and bool(self._layout.video_output)
        shown = self._layout.video_output if ready else self._layout.video_source
        self._video_path.setText(shown)  # type: ignore[attr-defined]
        self._sync_video_labels()
        self._load_video(shown)
        if not self._video_started:
            self._show_poster()

    def _sync_video_labels(self) -> None:
        play = self._video_play
        if play is None:
            return
        playing = self._video_is_playing()
        key = "simple.video.pause" if playing else "simple.video.play"
        play.setText(tr(key))  # type: ignore[attr-defined]
        _fit_plain(play, pad=28)
        opener = self.root.findChild(self._widgets.QPushButton, "videoOpen")
        if opener is not None:
            opener.setText(tr("simple.menu.open_folder"))
            _fit_plain(opener, pad=28)

    def _video_is_playing(self) -> bool:
        media = self._media
        if media is None:
            return False
        return media.playbackState() == media.PlaybackState.PlayingState  # type: ignore[attr-defined]

    def _load_video(self, path: str) -> None:
        if path == self._loaded_video:
            return
        self._loaded_video = path
        self._video_started = False
        media = self._media
        if media is None:
            return
        try:
            if not path:
                media.setSource(self._core.QUrl())  # type: ignore[attr-defined]
                return
            media.setSource(self._core.QUrl.fromLocalFile(path))  # type: ignore[attr-defined]
        except AttributeError, RuntimeError:
            self._show_poster()

    def _show_poster(self) -> None:
        stack = self._video_stack
        poster = self._video_poster
        if stack is None or poster is None:
            return
        stack.setCurrentWidget(poster)  # type: ignore[attr-defined]
        if self._video_view is not None:
            self._video_view.hide()  # type: ignore[attr-defined]

    def _toggle_video(self) -> None:
        media = self._media
        if media is None:
            return
        if self._video_is_playing():
            media.pause()  # type: ignore[attr-defined]
            return
        self._video_started = True
        media.play()  # type: ignore[attr-defined]

    def _seek_pressed(self) -> None:
        self._seeking = True

    def _seek_released(self) -> None:
        self._seeking = False
        media = self._media
        seek = self._video_seek
        if media is None or seek is None:
            return
        media.setPosition(int(seek.value()))  # type: ignore[attr-defined]

    def _on_video_state(self, _state: object) -> None:
        playing = self._video_is_playing()
        if playing:
            self._video_started = True
            stack = self._video_stack
            view = self._video_view
            if stack is not None and view is not None:
                view.show()  # type: ignore[attr-defined]
                stack.setCurrentWidget(view)  # type: ignore[attr-defined]
        elif not self._video_started:
            self._show_poster()
        self._sync_video_labels()

    def _on_video_position(self, position: int) -> None:
        seek = self._video_seek
        if self._seeking or seek is None:
            return
        seek.blockSignals(True)  # type: ignore[attr-defined]
        seek.setValue(int(position))  # type: ignore[attr-defined]
        seek.blockSignals(False)  # type: ignore[attr-defined]

    def _on_video_duration(self, duration: int) -> None:
        seek = self._video_seek
        if seek is None:
            return
        seek.setRange(0, max(int(duration), 1))  # type: ignore[attr-defined]

    def _open_audio(self, page: object) -> None:
        """Attach an audio player, or leave the controls as the fallback."""
        loaded = _qt_audio()
        if loaded is None:
            return
        player_cls, audio_cls = loaded
        try:
            player = player_cls(page)
            audio = audio_cls(page)
            player.setAudioOutput(audio)
        except AttributeError, ImportError, RuntimeError:
            return
        self._audio = player
        player.playbackStateChanged.connect(self._on_audio_state)
        player.positionChanged.connect(self._on_audio_position)
        player.durationChanged.connect(self._on_audio_duration)
        player.errorOccurred.connect(self._on_audio_error)

    def _paint_audio(self, item_id: str) -> None:
        """Show the waveform until an output path exists, then the player."""
        player = self._player
        deck = self._audio_deck
        if player is None or deck is None:
            return
        path = self._layout.audio_output.strip()
        if not path:
            deck.hide()  # type: ignore[attr-defined]
            player.show()  # type: ignore[attr-defined]
            player.setPixmap(_bars(self._gui, item_id, 880, 220))  # type: ignore[attr-defined]
            return
        player.hide()  # type: ignore[attr-defined]
        deck.show()  # type: ignore[attr-defined]
        label = self._audio_path
        if label is not None:
            label.setText(path)  # type: ignore[attr-defined]
            label.setToolTip(path)  # type: ignore[attr-defined]
        self._fill_audio_chapters()
        self._load_audio(path)
        self._sync_audio_labels()

    def _fill_audio_chapters(self) -> None:
        chapters = self._audio_chapters
        if chapters is None:
            return
        chapters.blockSignals(True)  # type: ignore[attr-defined]
        chapters.clear()  # type: ignore[attr-defined]
        for title, start in self._layout.chapters:
            item = self._widgets.QListWidgetItem(title)
            item.setData(self._qt.UserRole, start)
            chapters.addItem(item)  # type: ignore[attr-defined]
        chapters.blockSignals(False)  # type: ignore[attr-defined]
        visible = bool(self._layout.chapters)
        chapters.setVisible(visible)  # type: ignore[attr-defined]
        heading = self._chapter_heading
        if heading is not None:
            heading.setVisible(visible)  # type: ignore[attr-defined]

    def _load_audio(self, path: str) -> None:
        media = self._audio
        if media is None or path == self._loaded_audio:
            return
        url = self._core.QUrl.fromLocalFile(path)
        media.setSource(url)  # type: ignore[attr-defined]
        self._loaded_audio = path

    def _toggle_audio(self) -> None:
        media = self._audio
        if media is None:
            return
        if self._audio_is_playing():
            media.pause()  # type: ignore[attr-defined]
            return
        media.play()  # type: ignore[attr-defined]

    def _audio_is_playing(self) -> bool:
        media = self._audio
        if media is None:
            return False
        state = media.playbackState()  # type: ignore[attr-defined]
        return state == media.PlaybackState.PlayingState  # type: ignore[attr-defined]

    def _sync_audio_labels(self) -> None:
        play = self._audio_play
        if play is None:
            return
        key = "simple.video.pause" if self._audio_is_playing() else "simple.video.play"
        label = tr(key)
        if str(play.text()) != label:  # type: ignore[attr-defined]
            play.setText(label)  # type: ignore[attr-defined]
            _fit_plain(play, pad=28)

    def _audio_seek_pressed(self) -> None:
        self._audio_seeking = True

    def _audio_seek_released(self) -> None:
        self._audio_seeking = False
        media = self._audio
        seek = self._audio_seek
        if media is None or seek is None:
            return
        media.setPosition(int(seek.value()))  # type: ignore[attr-defined]

    def _play_chapter(self, item: object) -> None:
        start = item.data(self._qt.UserRole)  # type: ignore[attr-defined]
        if isinstance(start, bool) or not isinstance(start, int):
            return
        seek = self._audio_seek
        if seek is not None:
            if start > int(seek.maximum()):  # type: ignore[attr-defined]
                seek.setRange(0, start)  # type: ignore[attr-defined]
            seek.blockSignals(True)  # type: ignore[attr-defined]
            seek.setValue(start)  # type: ignore[attr-defined]
            seek.blockSignals(False)  # type: ignore[attr-defined]
        media = self._audio
        if media is None:
            return
        media.setPosition(start)  # type: ignore[attr-defined]
        media.play()  # type: ignore[attr-defined]

    def _on_audio_state(self, _state: object) -> None:
        self._sync_audio_labels()

    def _on_audio_position(self, position: int) -> None:
        seek = self._audio_seek
        if self._audio_seeking or seek is None:
            return
        seek.blockSignals(True)  # type: ignore[attr-defined]
        seek.setValue(int(position))  # type: ignore[attr-defined]
        seek.blockSignals(False)  # type: ignore[attr-defined]

    def _on_audio_duration(self, duration: int) -> None:
        seek = self._audio_seek
        if seek is None:
            return
        seek.setRange(0, max(int(duration), int(seek.value()), 1))  # type: ignore[attr-defined]

    def _on_audio_error(self, *_args: object) -> None:
        self._sync_audio_labels()

    def _on_video_error(self, *_args: object) -> None:
        self._video_started = False
        self._show_poster()

    def _show_ready(self) -> None:
        item = self._layout.item(self._layout.selected)
        if item is None:
            self._stack.setCurrentIndex(0)  # type: ignore[attr-defined]
            return
        kind = self._surface.result_kind
        if kind is ResultKind.PICTURE:
            self._scaled_to = None
            self._stack.setCurrentIndex(2)  # type: ignore[attr-defined]
            self._rescale()
            return
        if kind is ResultKind.PLAYER:
            if self._surface.video_result:
                self._paint_video()
            else:
                self._paint_audio(item.item_id)
        elif kind is ResultKind.COMPARE:
            self._paint_wipe()
        elif kind is ResultKind.DOCUMENT:
            self._document.setPlainText(_chapter_text(item.prompt))  # type: ignore[attr-defined]
        elif kind is ResultKind.REPORT:
            self._report.setText(_report_text())  # type: ignore[attr-defined]
        else:
            assert_never(kind)
        self._stack.setCurrentIndex(2)  # type: ignore[attr-defined]

    def _paint_wipe(self) -> None:
        frame = self._compare
        slider = self._wipe
        if frame is None or slider is None:
            return
        size = frame.size()  # type: ignore[attr-defined]
        width = max(int(size.width()), 2)
        height = max(int(size.height()), 2)
        split = int(slider.value()) / 1000  # type: ignore[attr-defined]
        frame.setPixmap(_wipe(self._gui, width, height, split))  # type: ignore[attr-defined]

    def _rescale(self) -> None:
        if self._surface.result_kind is not ResultKind.PICTURE:
            return
        if self._layout.phase is not Phase.READY or self._picture is None:
            return
        size = self._picture.size()  # type: ignore[attr-defined]
        width = int(size.width())
        height = int(size.height())
        if width < 2 or height < 2 or (width, height) == self._scaled_to:
            return
        item = self._layout.item(self._layout.selected)
        item_id = "picture" if item is None else item.item_id
        self._scaled_to = (width, height)
        self._picture.setPixmap(_scene(self._gui, item_id, width, height))  # type: ignore[attr-defined]

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
        if self._on_improve is None or self._prompt is None:
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
        if self._previous_prompt is None or self._prompt is None:
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


def _fill_form(
    widgets: object,
    form: object,
    specs: tuple[SettingSpec, ...],
    *,
    values: dict[str, object] | None = None,
    on_change: Callable[[str, object], None] | None = None,
) -> None:
    core, _gui, _widgets = _qt()
    rows = widgets.QFormLayout()  # type: ignore[attr-defined]
    rows.setContentsMargins(0, 0, 0, 0)
    rows.setHorizontalSpacing(8)
    rows.setVerticalSpacing(4)
    rows.setFieldGrowthPolicy(widgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)  # type: ignore[attr-defined]
    rows.setRowWrapPolicy(widgets.QFormLayout.RowWrapPolicy.DontWrapRows)  # type: ignore[attr-defined]
    rows.setLabelAlignment(  # type: ignore[attr-defined]
        core.Qt.AlignmentFlag.AlignLeft | core.Qt.AlignmentFlag.AlignVCenter  # type: ignore[attr-defined]
    )
    rows.setFormAlignment(  # type: ignore[attr-defined]
        core.Qt.AlignmentFlag.AlignLeft | core.Qt.AlignmentFlag.AlignTop  # type: ignore[attr-defined]
    )
    form.addLayout(rows)  # type: ignore[attr-defined]
    # One group is the whole panel, so its name would only repeat the title.
    # Two or more groups keep their own names.
    show_groups = len({spec.group for spec in specs}) > 1
    group = ""
    for spec in specs:
        if show_groups and spec.group != group:
            group = spec.group
            heading = widgets.QLabel(tr(f"setting.group.{group}"))  # type: ignore[attr-defined]
            heading.setObjectName("settingGroup")
            rows.addRow(heading)
        current = spec.default if values is None else values.get(spec.setting_id, spec.default)
        control = _control(widgets, spec, current)
        if on_change is not None:
            _watch(control, spec, on_change)
        if spec.type is SettingType.BOOL:
            rows.addRow(control)
            continue
        label = widgets.QLabel(tr(spec.label_key))  # type: ignore[attr-defined]
        label.setObjectName("panel")
        label.setWordWrap(False)
        label.setMinimumWidth(_label_width(label))
        rows.addRow(label, control)


def _control(widgets: object, spec: SettingSpec, value: object) -> object:
    kind = spec.type
    if kind is SettingType.BOOL:
        box = widgets.QCheckBox(tr(spec.label_key))  # type: ignore[attr-defined]
        box.setObjectName(spec.setting_id)
        box.setChecked(value is True)
        control = box
    elif kind is SettingType.ENUM:
        combo = _eliding_combo()
        combo.setObjectName(spec.setting_id)  # type: ignore[attr-defined]
        selected = 0
        for index, choice in enumerate(spec.choices):
            combo.addItem(tr(choice.label_key), choice.value)  # type: ignore[attr-defined]
            if choice.value == value:
                selected = index
        combo.setCurrentIndex(selected)  # type: ignore[attr-defined]
        control = combo
    elif kind is SettingType.INT:
        spin = widgets.QSpinBox()  # type: ignore[attr-defined]
        spin.setObjectName(spec.setting_id)
        spin.setMinimumWidth(72)
        _expand(spin)
        spin.setRange(int(spec.minimum or 0), int(spec.maximum or 0))
        spin.setValue(int(value) if isinstance(value, int) and not isinstance(value, bool) else 0)
        _apply_suffix(spin, spec)
        control = spin
    elif kind is SettingType.FLOAT:
        spin = widgets.QDoubleSpinBox()  # type: ignore[attr-defined]
        spin.setObjectName(spec.setting_id)
        spin.setMinimumWidth(72)
        _expand(spin)
        spin.setDecimals(1)
        spin.setRange(float(spec.minimum or 0), float(spec.maximum or 0))
        spin.setSingleStep(0.5)
        number = (
            float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0
        )
        spin.setValue(number)
        _apply_suffix(spin, spec)
        control = spin
    elif kind is SettingType.PATH:
        edit = widgets.QLineEdit()  # type: ignore[attr-defined]
        edit.setObjectName(spec.setting_id)
        edit.setText(value if isinstance(value, str) else "")
        _expand(edit)
        control = edit
    elif kind is SettingType.TEXT:
        edit = widgets.QPlainTextEdit()  # type: ignore[attr-defined]
        edit.setObjectName(spec.setting_id)
        edit.setPlainText(value if isinstance(value, str) else "")
        edit.setMinimumWidth(0)
        edit.setFixedHeight(48)
        control = edit
    else:
        assert_never(kind)
    if spec.expert:
        tip = tr(spec.help_key)
        control.setProperty("helpTip", tip)  # type: ignore[attr-defined]
        control.setToolTip(tip)  # type: ignore[attr-defined]
    if kind is SettingType.ENUM:
        _note_combo_fit(control)
    return control


def _watch(control: object, spec: SettingSpec, on_change: Callable[[str, object], None]) -> None:
    kind = spec.type
    setting_id = spec.setting_id

    def emit(*_args: object) -> None:
        on_change(setting_id, _read_control(control, spec))

    if kind is SettingType.BOOL:
        control.toggled.connect(emit)  # type: ignore[attr-defined]
    elif kind is SettingType.ENUM:
        control.currentIndexChanged.connect(emit)  # type: ignore[attr-defined]
    elif kind is SettingType.INT or kind is SettingType.FLOAT:
        control.valueChanged.connect(emit)  # type: ignore[attr-defined]
    elif kind is SettingType.PATH or kind is SettingType.TEXT:
        control.textChanged.connect(emit)  # type: ignore[attr-defined]
    else:
        assert_never(kind)


def _read_control(control: object, spec: SettingSpec) -> object:
    kind = spec.type
    if kind is SettingType.BOOL:
        return control.isChecked()  # type: ignore[attr-defined]
    if kind is SettingType.ENUM:
        return control.currentData()  # type: ignore[attr-defined]
    if kind is SettingType.INT or kind is SettingType.FLOAT:
        return control.value()  # type: ignore[attr-defined]
    if kind is SettingType.PATH:
        return control.text()  # type: ignore[attr-defined]
    if kind is SettingType.TEXT:
        return control.toPlainText()  # type: ignore[attr-defined]
    assert_never(kind)


def _write_control(control: object, spec: SettingSpec, value: object) -> None:
    kind = spec.type
    if kind is SettingType.BOOL:
        control.setChecked(value is True)  # type: ignore[attr-defined]
        return
    if kind is SettingType.ENUM:
        index = control.findData(value)  # type: ignore[attr-defined]
        if index >= 0:
            control.setCurrentIndex(index)  # type: ignore[attr-defined]
        _note_combo_fit(control)
        return
    if kind is SettingType.INT:
        if isinstance(value, int) and not isinstance(value, bool):
            control.setValue(value)  # type: ignore[attr-defined]
        return
    if kind is SettingType.FLOAT:
        if isinstance(value, int | float) and not isinstance(value, bool):
            control.setValue(float(value))  # type: ignore[attr-defined]
        return
    if kind is SettingType.PATH:
        control.setText(value if isinstance(value, str) else "")  # type: ignore[attr-defined]
        return
    if kind is SettingType.TEXT:
        control.setPlainText(value if isinstance(value, str) else "")  # type: ignore[attr-defined]
        return
    assert_never(kind)


def _live_values(layout: SimpleImageLayout, specs: tuple[SettingSpec, ...]) -> dict[str, object]:
    if layout.state is None:
        return {spec.setting_id: spec.default for spec in specs}
    return {spec.setting_id: layout.get(spec.setting_id) for spec in specs}


def _chip_text(label_key: str, selected: str, value: str, kind: PresetMatch | None) -> str:
    label = tr(label_key)
    if kind is None or kind is PresetMatch.EXACT or kind is PresetMatch.CUSTOM:
        return label
    if kind is PresetMatch.MODIFIED:
        if value == selected:
            return tr("simple.preset.changed", name=label)
        return label
    assert_never(kind)


def _chip_checked(selected: str, value: str, kind: PresetMatch | None) -> bool:
    if kind is PresetMatch.CUSTOM:
        return False
    return value == selected


def _clear(layout: object) -> None:
    """Remove every item of a layout, including nested layouts and their widgets."""
    while layout.count():  # type: ignore[attr-defined]
        item = layout.takeAt(0)  # type: ignore[attr-defined]
        widget = item.widget()
        child = item.layout()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif child is not None:
            _clear(child)
            child.setParent(None)
            child.deleteLater()


def _chapter_text(prompt: str) -> str:
    title = prompt.strip() or tr("simple.chapter.1")
    return "\n\n".join(
        (
            title,
            tr("simple.document.opening"),
            tr("simple.chapter.2"),
            tr("simple.document.closing"),
        )
    )


def _report_text() -> str:
    return "\n\n".join((tr("simple.report.ready"), tr("simple.report.body")))


def _bars(gui: object, item_id: str, width: int, height: int) -> object:
    """Return neutral sound bars. They are not a recording."""
    image = gui.QImage(  # type: ignore[attr-defined]
        max(width, 1),
        max(height, 1),
        gui.QImage.Format.Format_RGB32,  # type: ignore[attr-defined]
    )
    image.fill(gui.QColor(28, 34, 46))  # type: ignore[attr-defined]
    painter = gui.QPainter(image)  # type: ignore[attr-defined]
    shift = sum(ord(char) for char in item_id) % 12
    count = 48
    gap = 4
    bar_width = max(2, (width - gap * (count + 1)) // count)
    for index in range(count):
        level = 28 + ((index * 17 + shift) % 58)
        bar_height = int(height * level / 100)
        x = gap + index * (bar_width + gap)
        y = (height - bar_height) // 2
        painter.fillRect(x, y, bar_width, bar_height, gui.QColor(186, 168, 120))  # type: ignore[attr-defined]
    painter.end()
    return gui.QPixmap.fromImage(image)  # type: ignore[attr-defined]


def _swatch(
    gui: object,
    width: int,
    height: int,
    label: str,
    cool: bool,
    align: int,
) -> object:
    """Return a flat colour with a word. It is not a video frame."""
    image = gui.QImage(  # type: ignore[attr-defined]
        max(width, 1),
        max(height, 1),
        gui.QImage.Format.Format_RGB32,  # type: ignore[attr-defined]
    )
    color = gui.QColor(46, 62, 84) if cool else gui.QColor(92, 78, 64)  # type: ignore[attr-defined]
    image.fill(color)
    painter = gui.QPainter(image)  # type: ignore[attr-defined]
    painter.setPen(gui.QColor(232, 226, 214))  # type: ignore[attr-defined]
    font = painter.font()
    font.setPointSize(18)
    painter.setFont(font)
    painter.drawText(image.rect(), align, label)
    painter.end()
    return gui.QPixmap.fromImage(image)  # type: ignore[attr-defined]


_COMBO: type | None = None


def _eliding_combo() -> object:
    """Return a combo box that elides the current text instead of clipping it."""
    global _COMBO
    if _COMBO is None:
        _core, _gui, widgets = _qt()

        class _ElidingCombo(widgets.QComboBox):  # type: ignore[misc]
            def __init__(self, parent: object = None) -> None:
                super().__init__(parent)  # type: ignore[arg-type]
                self.setSizeAdjustPolicy(self.SizeAdjustPolicy.AdjustToContents)
                self.setMinimumContentsLength(1)
                policy = self.sizePolicy()
                policy.setHorizontalPolicy(policy.Policy.Expanding)
                self.setSizePolicy(policy)
                self.currentIndexChanged.connect(lambda *_args: _note_combo_fit(self))

            def minimumSizeHint(self) -> object:  # type: ignore[override]
                core, _gui, _widgets = _qt()
                hint = super().minimumSizeHint()
                # A long translation may be wider than the panel. Elide instead.
                return core.QSize(72, hint.height())

            def paintEvent(self, event: object) -> None:
                del event
                _paint_elided_combo(self)

            def resizeEvent(self, event: object) -> None:
                super().resizeEvent(event)  # type: ignore[arg-type]
                _note_combo_fit(self)

            def showEvent(self, event: object) -> None:
                super().showEvent(event)  # type: ignore[arg-type]
                _note_combo_fit(self)

            def showPopup(self) -> None:
                view = self.view()
                view.setMinimumWidth(max(view.sizeHintForColumn(0) + 24, self.width()))
                super().showPopup()

        _COMBO = _ElidingCombo
    return _COMBO()


def _combo_usable_width(combo: object) -> int:
    """Return the width the closed combo can paint, inside the arrow."""
    _core, _gui, widgets = _qt()
    option = widgets.QStyleOptionComboBox()  # type: ignore[attr-defined]
    combo.initStyleOption(option)  # type: ignore[attr-defined]
    rect = combo.style().subControlRect(  # type: ignore[attr-defined]
        widgets.QStyle.ComplexControl.CC_ComboBox,  # type: ignore[attr-defined]
        option,
        widgets.QStyle.SubControl.SC_ComboBoxEditField,  # type: ignore[attr-defined]
        combo,
    )
    # The style draws the label one pixel in from each side of the field.
    return max(int(rect.width()) - 2, 0)


def _clip_with_ellipsis(metrics: object, text: str, usable: int) -> str:
    """Shorten ``text`` until it and an ellipsis fit in ``usable`` pixels."""
    mark = "…"
    mark_width = int(metrics.horizontalAdvance(mark))  # type: ignore[attr-defined]
    if mark_width >= usable:
        return mark
    kept = text
    limit = usable - mark_width
    while kept and int(metrics.horizontalAdvance(kept)) > limit:  # type: ignore[attr-defined]
        kept = kept[:-1]
    return kept.rstrip() + mark


def _note_combo_fit(combo: object) -> None:
    """Remember the elided label, and put the full value on the tooltip."""
    text = str(combo.currentText())  # type: ignore[attr-defined]
    usable = _combo_usable_width(combo)
    if usable < 8:
        combo.setProperty("fittedText", text)  # type: ignore[attr-defined]
        return
    core, gui, _widgets = _qt()
    metrics = gui.QFontMetrics(combo.font())  # type: ignore[attr-defined]
    advance = int(metrics.horizontalAdvance(text))  # type: ignore[attr-defined]
    if advance <= usable:
        fitted = text
    else:
        fitted = str(
            metrics.elidedText(text, core.Qt.TextElideMode.ElideRight, usable)  # type: ignore[attr-defined]
        )
        if not fitted.endswith("…"):
            fitted = _clip_with_ellipsis(metrics, text, usable)
    combo.setProperty("fittedText", fitted)  # type: ignore[attr-defined]
    help_tip = combo.property("helpTip")  # type: ignore[attr-defined]
    help_text = help_tip if isinstance(help_tip, str) else ""
    if fitted == text:
        tip = help_text
    elif help_text:
        tip = f"{text}\n{help_text}"
    else:
        tip = text
    if combo.toolTip() != tip:  # type: ignore[attr-defined]
        combo.setToolTip(tip)  # type: ignore[attr-defined]


def _paint_elided_combo(combo: object) -> None:
    _note_combo_fit(combo)
    _core, gui, widgets = _qt()
    painter = widgets.QStylePainter(combo)  # type: ignore[attr-defined]
    option = widgets.QStyleOptionComboBox()  # type: ignore[attr-defined]
    combo.initStyleOption(option)  # type: ignore[attr-defined]
    fitted = combo.property("fittedText")  # type: ignore[attr-defined]
    if isinstance(fitted, str):
        option.currentText = fitted
    role = gui.QPalette.ColorRole  # type: ignore[attr-defined]
    painter.setPen(combo.palette().color(role.Text))  # type: ignore[attr-defined]
    painter.drawComplexControl(widgets.QStyle.ComplexControl.CC_ComboBox, option)  # type: ignore[attr-defined]
    painter.drawControl(widgets.QStyle.ControlElement.CE_ComboBoxLabel, option)  # type: ignore[attr-defined]
    painter.end()


def _expand(control: object) -> None:
    """Let a field fill the value column."""
    policy = control.sizePolicy()  # type: ignore[attr-defined]
    policy.setHorizontalPolicy(policy.Policy.Expanding)
    control.setSizePolicy(policy)  # type: ignore[attr-defined]


def _label_width(label: object) -> int:
    """Return the width of one label, measured in the body font."""
    _core, gui, _widgets = _qt()
    font = gui.QFont(label.font())  # type: ignore[attr-defined]
    font.setPixelSize(load().density.body_px)
    return int(gui.QFontMetrics(font).horizontalAdvance(str(label.text())))  # type: ignore[attr-defined]


def _fit_bold(button: object, *, pad: int, pixel: int | None = None) -> None:
    """Reserve the bold label width, including the horizontal padding."""
    text = str(button.text())  # type: ignore[attr-defined]
    _core, gui, _widgets = _qt()
    font = gui.QFont(button.font())  # type: ignore[attr-defined]
    font.setWeight(gui.QFont.Weight.DemiBold)  # type: ignore[attr-defined]
    if pixel is not None:
        font.setPixelSize(pixel)
    advance = int(gui.QFontMetrics(font).horizontalAdvance(text))  # type: ignore[attr-defined]
    button.setMinimumWidth(max(advance + pad, 36))  # type: ignore[attr-defined]


def _fit_plain(button: object, *, pad: int) -> None:
    """Reserve the label width, including the horizontal padding."""
    text = str(button.text())  # type: ignore[attr-defined]
    if not text:
        return
    _core, gui, _widgets = _qt()
    advance = int(gui.QFontMetrics(button.font()).horizontalAdvance(text))  # type: ignore[attr-defined]
    button.setMinimumWidth(max(advance + pad, 36))  # type: ignore[attr-defined]


def _fit_chip(button: object) -> None:
    """Size one chip or pill to the face it paints, checked or not.

    The width is that font's advance plus ``chip_pad_px`` and ``border_px`` on
    each side. Those are the same density tokens the stylesheet uses for
    ``QPushButton#expertToggle`` and ``QPushButton[chip="true"]``.
    """
    button.setMinimumWidth(_chip_width(button))  # type: ignore[attr-defined]


def _chip_width(button: object) -> int:
    """Return the width of the face this pill actually paints.

    A checked chip or Expert toggle is weight 600. Every other pill uses the
    regular face. The padding and the border are the density tokens the
    stylesheet uses, on each side.
    """
    density = load().density
    text = str(button.text())  # type: ignore[attr-defined]
    _core, gui, _widgets = _qt()
    button.ensurePolished()  # type: ignore[attr-defined]
    font = gui.QFont(button.font())  # type: ignore[attr-defined]
    font.setPixelSize(density.body_px)
    checked = bool(button.isChecked()) and (  # type: ignore[attr-defined]
        button.property("chip") == "true" or str(button.objectName()) == "expertToggle"  # type: ignore[attr-defined]
    )
    if checked:
        font.setWeight(gui.QFont.Weight.DemiBold)
    advance = int(gui.QFontMetrics(font).horizontalAdvance(text))  # type: ignore[attr-defined]
    return advance + (density.chip_pad_px + density.border_px) * 2


def _looks_like_path(text: str) -> bool:
    """Return whether ``text`` is a file path rather than a bare name."""
    if not text:
        return False
    if Path(text).is_absolute():
        return True
    return "/" in text or "\\" in text


def _pill_flow(widgets: object, core: object) -> tuple[object, object]:
    """Return a row that wraps pills onto another line instead of clipping them."""

    class _Flow(widgets.QLayout):  # type: ignore[name-defined]
        def __init__(self, parent: object = None) -> None:
            super().__init__(parent)
            self._items: list[object] = []
            self.setSpacing(8)
            self.setContentsMargins(0, 0, 0, 0)

        def addItem(self, item: object) -> None:
            self._items.append(item)

        def count(self) -> int:
            return len(self._items)

        def itemAt(self, index: int) -> object:
            if 0 <= index < len(self._items):
                return self._items[index]
            return None  # type: ignore[return-value]

        def takeAt(self, index: int) -> object:
            if 0 <= index < len(self._items):
                return self._items.pop(index)
            return None  # type: ignore[return-value]

        def expandingDirections(self) -> object:
            return core.Qt.Orientations(0)  # type: ignore[attr-defined]

        def hasHeightForWidth(self) -> bool:
            return True

        def heightForWidth(self, width: int) -> int:
            return self._place(core.QRect(0, 0, width, 0), apply=False)  # type: ignore[attr-defined]

        def setGeometry(self, rect: object) -> None:
            super().setGeometry(rect)  # type: ignore[arg-type]
            self._place(rect, apply=True)

        def sizeHint(self) -> object:
            return self.minimumSize()

        def minimumSize(self) -> object:
            width = 0
            height = 0
            for item in self._items:
                item_width, item_height = _pill_size(item)
                width = max(width, item_width)
                height = max(height, item_height)
            margins = self.contentsMargins()
            return core.QSize(  # type: ignore[attr-defined]
                width + margins.left() + margins.right(),
                height + margins.top() + margins.bottom(),
            )

        def _place(self, rect: object, *, apply: bool) -> int:
            margins = self.contentsMargins()
            spacing = self.spacing()
            left = rect.x() + margins.left()  # type: ignore[attr-defined]
            top = rect.y() + margins.top()  # type: ignore[attr-defined]
            available = max(0, rect.width() - margins.left() - margins.right())  # type: ignore[attr-defined]
            x = left
            y = top
            line_height = 0
            for item in self._items:
                item_width, item_height = _pill_size(item)
                if available > 0 and x > left and x + item_width > left + available:
                    x = left
                    y += line_height + spacing
                    line_height = 0
                if apply:
                    item.setGeometry(  # type: ignore[attr-defined]
                        core.QRect(x, y, item_width, item_height)  # type: ignore[attr-defined]
                    )
                x += item_width + spacing
                line_height = max(line_height, item_height)
            return y + line_height + margins.bottom() - rect.y()  # type: ignore[attr-defined]

    host = widgets.QWidget()  # type: ignore[attr-defined]
    host.setObjectName("pillRow")
    flow = _Flow(host)
    host.setLayout(flow)
    return host, flow


def _pill_size(item: object) -> tuple[int, int]:
    hint = item.sizeHint()  # type: ignore[attr-defined]
    minimum = item.minimumSize()  # type: ignore[attr-defined]
    width = max(int(hint.width()), int(minimum.width()))
    height = max(int(hint.height()), int(minimum.height()))
    return width, height


def _apply_suffix(spin: object, spec: SettingSpec) -> None:
    suffix = spin_suffix(spec)
    if suffix:
        spin.setSuffix(suffix)  # type: ignore[attr-defined]


def _paint_scroll(scroll: object, gui: object) -> None:
    colour = gui.QColor(CARD_PLAIN)  # type: ignore[attr-defined]
    text = gui.QColor(TEXT)  # type: ignore[attr-defined]
    role = gui.QPalette.ColorRole  # type: ignore[attr-defined]
    targets = [scroll, scroll.viewport()]  # type: ignore[attr-defined]
    content = scroll.widget()  # type: ignore[attr-defined]
    if content is not None:
        targets.append(content)
    for target in targets:
        palette = target.palette()
        palette.setColor(role.Window, colour)
        palette.setColor(role.Base, colour)
        palette.setColor(role.Text, text)
        palette.setColor(role.WindowText, text)
        target.setPalette(palette)
        target.setAutoFillBackground(True)


_SCROLL_FILTERS: dict[int, object] = {}


def _darken(scroll: object, gui: object) -> None:
    """Paint a scroll area, its viewport and its content from the tokens.

    Qt resets the palette after a reparent or a new content widget, so an
    event filter paints them again on those events.
    """
    _paint_scroll(scroll, gui)
    key = id(scroll)
    if key in _SCROLL_FILTERS:
        return
    from PySide6 import QtCore

    watched = (
        QtCore.QEvent.Type.ParentChange,
        QtCore.QEvent.Type.ChildAdded,
        QtCore.QEvent.Type.Show,
    )

    class _Repaint(QtCore.QObject):
        def eventFilter(self, obj: object, event: object) -> bool:
            if event.type() in watched:  # type: ignore[attr-defined]
                QtCore.QTimer.singleShot(0, self._apply)
            return False

        def _apply(self) -> None:
            with contextlib.suppress(RuntimeError):
                _paint_scroll(scroll, gui)

    repaint = _Repaint(scroll)  # type: ignore[arg-type]
    scroll.installEventFilter(repaint)  # type: ignore[attr-defined]
    scroll.viewport().installEventFilter(repaint)  # type: ignore[attr-defined]
    _SCROLL_FILTERS[key] = repaint
    scroll.destroyed.connect(lambda *_: _SCROLL_FILTERS.pop(key, None))  # type: ignore[attr-defined]


def _hill(gui: object, points: tuple[tuple[float, float], ...]) -> object:
    path = gui.QPainterPath()  # type: ignore[attr-defined]
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    path.closeSubpath()
    return path


def _scene(gui: object, item_id: str, width: int, height: int) -> object:
    """Return a painted landscape. It is not a photograph."""
    image = gui.QImage(  # type: ignore[attr-defined]
        max(width, 1),
        max(height, 1),
        gui.QImage.Format.Format_RGB32,  # type: ignore[attr-defined]
    )
    shift = sum(ord(char) for char in item_id) % 18
    painter = gui.QPainter(image)  # type: ignore[attr-defined]
    sky = gui.QLinearGradient(0, 0, 0, height)  # type: ignore[attr-defined]
    sky.setColorAt(0, gui.QColor(28, 42 + shift, 72))  # type: ignore[attr-defined]
    sky.setColorAt(0.55, gui.QColor(186, 132 + shift, 86))  # type: ignore[attr-defined]
    sky.setColorAt(1, gui.QColor(92, 78, 64))  # type: ignore[attr-defined]
    painter.fillRect(image.rect(), sky)
    sun = gui.QColor(245, 214, 140)  # type: ignore[attr-defined]
    painter.setBrush(sun)
    painter.setPen(gui.QPen(sun))  # type: ignore[attr-defined]
    sun_size = max(18, width // 10)
    painter.drawEllipse(int(width * 0.72), int(height * 0.16), sun_size, sun_size)
    painter.setPen(gui.QPen(gui.QColor(0, 0, 0, 0)))  # type: ignore[attr-defined]
    far = _hill(
        gui,
        (
            (0, height * 0.72),
            (width * 0.28, height * 0.48),
            (width * 0.55, height * 0.7),
            (width, height * 0.42),
            (width, height),
            (0, height),
        ),
    )
    painter.setBrush(gui.QColor(58, 62, 74))  # type: ignore[attr-defined]
    painter.drawPath(far)  # type: ignore[attr-defined]
    near = _hill(
        gui,
        (
            (0, height * 0.78),
            (width * 0.4, height * 0.62),
            (width, height * 0.8),
            (width, height),
            (0, height),
        ),
    )
    painter.setBrush(gui.QColor(36, 40, 34))  # type: ignore[attr-defined]
    painter.drawPath(near)  # type: ignore[attr-defined]
    painter.setPen(gui.QPen(gui.QColor(214, 196, 150), max(2, width // 180)))  # type: ignore[attr-defined]
    painter.drawLine(int(width * 0.5), height, int(width * 0.62), int(height * 0.7))
    painter.end()
    return gui.QPixmap.fromImage(image)  # type: ignore[attr-defined]


def _wipe(gui: object, width: int, height: int, split: float) -> object:
    """Return one frame with a before/after split. It is not a video."""
    image = gui.QImage(  # type: ignore[attr-defined]
        max(width, 1),
        max(height, 1),
        gui.QImage.Format.Format_RGB32,  # type: ignore[attr-defined]
    )
    painter = gui.QPainter(image)  # type: ignore[attr-defined]
    painter.drawPixmap(0, 0, _scene(gui, "after", width, height))  # type: ignore[attr-defined]
    cut = int(max(0.0, min(split, 1.0)) * width)
    before = _scene(gui, "before", width, height)
    painter.drawPixmap(0, 0, before, 0, 0, cut, height)  # type: ignore[attr-defined]
    painter.fillRect(0, 0, cut, height, gui.QColor(8, 12, 20, 90))  # type: ignore[attr-defined]
    painter.setPen(gui.QPen(gui.QColor(242, 242, 245), 2))  # type: ignore[attr-defined]
    painter.drawLine(cut, 0, cut, height)
    painter.end()
    return gui.QPixmap.fromImage(image)  # type: ignore[attr-defined]


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


SimpleLayout = SimpleImageLayout
SimpleWidget = SimpleImageWidget


def _film_poster(gui: object, width: int, height: int) -> object:
    """Return a dark still, so the result is not a blank page before playback."""
    image = gui.QImage(max(width, 1), max(height, 1), gui.QImage.Format.Format_RGB32)  # type: ignore[attr-defined]
    image.fill(gui.QColor(CARD_PLAIN))  # type: ignore[attr-defined]
    painter = gui.QPainter(image)  # type: ignore[attr-defined]
    painter.setRenderHint(gui.QPainter.RenderHint.Antialiasing, True)  # type: ignore[attr-defined]
    painter.setPen(gui.QPen(gui.QColor(CONTROL_BG), 8))  # type: ignore[attr-defined]
    painter.setBrush(gui.QColor("#18181f"))  # type: ignore[attr-defined]
    painter.drawRoundedRect(24, 24, max(width - 48, 1), max(height - 48, 1), 12, 12)  # type: ignore[attr-defined]
    side = max(min(width, height) // 5, 12)
    cx, cy = width // 2, height // 2
    path = gui.QPainterPath()  # type: ignore[attr-defined]
    path.moveTo(cx - side // 3, cy - side // 2)
    path.lineTo(cx - side // 3, cy + side // 2)
    path.lineTo(cx + side // 2, cy)
    path.closeSubpath()
    painter.fillPath(path, gui.QColor(TEXT))  # type: ignore[attr-defined]
    painter.end()
    return gui.QPixmap.fromImage(image)  # type: ignore[attr-defined]


def _qt() -> tuple[object, object, object]:
    """Import PySide6 when a widget is constructed."""
    from PySide6 import QtCore, QtGui, QtWidgets

    return QtCore, QtGui, QtWidgets


def _qt_audio() -> tuple[object, object] | None:
    """Import the audio player, or return None when Qt Multimedia is missing."""
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    except ImportError:
        return None
    return QMediaPlayer, QAudioOutput


def _qt_media() -> tuple[object, object, object] | None:
    """Import Qt Multimedia, or return None when it is not installed."""
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
    except ImportError:
        return None
    return QMediaPlayer, QAudioOutput, QVideoWidget
