"""Sections, wizard steps, and the casting picker. Qt stays in a subprocess."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from voxprint_common.ui.presets import SettingState
from voxprint_common.ui.sections import (
    SectionDeck,
    StepKind,
    audiobook_sections,
    fit_tabs,
)
from voxprint_common.ui.variants import surface_for, variant_presets


def test_tabs_that_do_not_fit_leave_room_for_more() -> None:
    assert fit_tabs((10, 10, 10), 30, 10, 0) == 3
    assert fit_tabs((10, 10, 10), 25, 10, 0) == 1
    assert fit_tabs((10, 10, 10), 5, 10, 0) == 0


def test_next_stays_gated_until_the_step_is_filled(tmp_path: Path) -> None:
    _document, state, deck = _deck(tmp_path)
    deck.open_wizard("train")
    assert deck.step is not None and deck.step.step_id == "train.sample"
    assert deck.can_advance() is False
    state.set_value("train.sample", "sample.wav")
    assert deck.can_advance() is True
    deck.advance()
    assert deck.can_advance() is False
    state.set_value("train.name", "Anya")
    assert deck.can_advance() is True
    deck.advance()
    assert deck.step is not None and deck.step.kind is StepKind.RUNNING
    assert deck.can_advance() is False


def test_resume_restores_the_running_step_and_the_app_blob(tmp_path: Path) -> None:
    seen: list[tuple[str, str, dict[str, object]]] = []

    def capture(wizard_id: str, step_id: str) -> dict[str, str]:
        return {"mark": step_id, "wizard": wizard_id}

    def restore(wizard_id: str, step_id: str, blob: dict[str, object]) -> None:
        seen.append((wizard_id, step_id, dict(blob)))

    document, state, deck = _deck(tmp_path, capture, restore)
    deck.open_wizard("train")
    state.set_value("train.sample", "sample.wav")
    deck.advance()
    state.set_value("train.name", "Anya")
    deck.advance()
    deck.note_progress(2, 5, "Learning")
    resumed = SectionDeck(
        document,
        state,
        state_dir=tmp_path,
        capture=capture,
        restore=restore,
    )
    assert resumed.wizard is not None and resumed.wizard.wizard_id == "train"
    assert resumed.step is not None and resumed.step.step_id == "train.run"
    assert resumed.running is True
    assert resumed.progress == (2, 5, "Learning")
    assert seen == [("train", "train.run", {"mark": "train.run", "wizard": "train"})]
    resumed.cancel_run()
    assert resumed.running is False
    stopped = SectionDeck(document, state, state_dir=tmp_path, capture=capture, restore=restore)
    assert stopped.step is not None and stopped.step.step_id == "train.run"
    assert stopped.running is False


def test_picker_value_stays_in_the_section_namespace(tmp_path: Path) -> None:
    document, state, deck = _deck(tmp_path)
    assert deck.picker("roles", "mira", "voice") == "anna"
    deck.set_picker("roles", "mira", "voice", "boris")
    assert state.get("roles.mira.voice") == "boris"
    presets = variant_presets(surface_for("audiobook-builder"), document.settings)
    loaded = SettingState(presets, document.settings, state_dir=tmp_path)
    again = SectionDeck(document, loaded, state_dir=tmp_path / "empty")
    assert again.picker("roles", "mira", "voice") == "boris"


def test_expert_fields_follow_the_open_section_and_step(tmp_path: Path) -> None:
    document, _state, deck = _deck(tmp_path)
    specs = tuple(spec for spec in document.settings.visible(expert=True) if spec.expert)
    deck.select("narration")
    narration = {spec.setting_id for spec in deck.scoped(specs)}
    assert "audio.sample_rate" in narration
    assert "roles.strict" not in narration
    assert "train.epochs" not in narration
    deck.select("roles")
    assert [spec.setting_id for spec in deck.scoped(specs)] == ["roles.strict"]
    deck.open_wizard("train")
    assert [spec.setting_id for spec in deck.scoped(specs)] == ["train.sample_seconds"]
    assert "voices.engine" not in {spec.setting_id for spec in deck.scoped(specs)}


def test_section_tabs_do_not_clip_or_scroll(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["VOXPRINT_HOME"] = str(tmp_path / "home")
    script = Path(__file__).with_name("section_offscreen.py")
    completed = subprocess.run(
        [sys.executable, str(script), str(tmp_path / "state")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


def _deck(
    path: Path,
    capture: object = None,
    restore: object = None,
) -> tuple[object, SettingState, SectionDeck]:
    document = audiobook_sections()
    presets = variant_presets(surface_for("audiobook-builder"), document.settings)
    state = SettingState(presets, document.settings, state_dir=path)
    deck = SectionDeck(
        document,
        state,
        state_dir=path,
        capture=capture,  # type: ignore[arg-type]
        restore=restore,  # type: ignore[arg-type]
    )
    return document, state, deck
