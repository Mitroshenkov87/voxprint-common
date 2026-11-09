"""Shared settings state: preset files, live values, and persistence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from voxprint_common.ui.presets import (
    PRESET_SCHEMA,
    PresetMatch,
    SettingState,
    load_presets,
    parse_presets,
    reference_presets,
)
from voxprint_common.ui.safety import ReviewedOutput
from voxprint_common.ui.settings import reference_settings
from voxprint_common.ui.simple_image import SimpleImageLayout

_CANVAS = Path(__file__).parents[1] / "src" / "voxprint_common" / "ui" / "presets" / "canvas.json"


class _Allow:
    def review_prompt(self, prompt: str) -> str | None:
        del prompt
        return None

    def review_result(self, prompt: str, item_id: str, output: ReviewedOutput) -> str | None:
        del prompt, item_id, output
        return None


def test_import_does_not_load_qt() -> None:
    assert "PySide6" not in sys.modules


def test_reference_presets_write_canvas_values() -> None:
    settings = reference_settings()
    document = reference_presets(settings)
    state = SettingState(document, settings)
    assert state.get("shell.steps") == 8
    assert state.get("shell.guidance") == 0.0
    assert state.get("shell.sampler") == "euler"
    assert state.get("shell.resolution") == "1024x1024"
    assert state.get("shell.output") == "standard"
    assert state.get("shell.negative") == ""
    assert state.get("shell.lora_weight") == 0.7
    assert state.get("shell.count") == 1
    assert state.status("quality").kind is PresetMatch.EXACT
    assert state.status("quality").option_id == "balanced"
    state.choose("quality", "fast")
    assert state.get("shell.steps") == 4
    assert state.get("shell.guidance") == 0.0
    assert state.get("shell.sampler") == "euler"
    assert state.get("shell.negative") == ""
    state.choose("size", "16:9")
    assert state.get("shell.resolution") == "1344x768"
    assert state.get("shell.steps") == 4
    with pytest.raises(ValueError):
        state.choose("output", "4k")
    text = _CANVAS.read_text(encoding="utf-8")
    assert "adult" not in text
    assert "disable_safety" not in text
    source = Path(reference_presets.__code__.co_filename).read_text(encoding="utf-8")
    assert "disable_safety" not in source


def test_hand_edit_marks_one_group_and_reset_restores_it() -> None:
    settings = reference_settings()
    layout = SimpleImageLayout(
        presets=reference_presets(settings), settings=settings, gate=_Allow()
    )
    layout.choose("style", "sketch")
    assert layout.get("shell.negative") == "full color"
    layout.set_value("shell.steps", 24)
    status = layout.preset_status("quality")
    assert status is not None
    assert status.kind is PresetMatch.MODIFIED
    assert status.option_id == "balanced"
    assert layout.presets["quality"] == "balanced"
    assert layout.preset_status("style") is not None
    assert layout.preset_status("style").kind is PresetMatch.EXACT  # type: ignore[union-attr]
    layout.set_value("shell.guidance", 3.0)
    custom = layout.preset_status("quality")
    assert custom is not None and custom.kind is PresetMatch.CUSTOM
    assert custom.option_id == "balanced"
    layout.reset_group("quality")
    assert layout.get("shell.steps") == 8
    assert layout.get("shell.guidance") == 0.0
    assert layout.get("shell.sampler") == "euler"
    assert layout.get("shell.negative") == "full color"
    restored = layout.preset_status("quality")
    assert restored is not None and restored.kind is PresetMatch.EXACT
    layout.set_value("shell.lora_weight", 0.9)
    detail = layout.preset_status("detail")
    assert detail is not None and detail.kind is PresetMatch.CUSTOM


def test_later_group_owns_a_shared_key() -> None:
    settings = reference_settings()
    document = parse_presets(
        {
            "schema": PRESET_SCHEMA,
            "groups": [
                {
                    "id": "a",
                    "label_key": "simple.quality",
                    "default": "one",
                    "options": [
                        {
                            "id": "one",
                            "label_key": "simple.quality.fast",
                            "values": {"shell.steps": 10, "shell.guidance": 1.0},
                        },
                        {
                            "id": "two",
                            "label_key": "simple.quality.best",
                            "values": {"shell.steps": 20, "shell.guidance": 2.0},
                        },
                    ],
                },
                {
                    "id": "b",
                    "label_key": "simple.detail",
                    "default": "x",
                    "options": [
                        {
                            "id": "x",
                            "label_key": "simple.detail.light",
                            "values": {"shell.steps": 30},
                        },
                        {
                            "id": "y",
                            "label_key": "simple.detail.strong",
                            "values": {"shell.steps": 40},
                        },
                    ],
                },
            ],
        },
        settings,
    )
    state = SettingState(document, settings)
    assert state.get("shell.steps") == 30
    assert state.get("shell.guidance") == 1.0
    assert state.owned_keys("a") == ("shell.guidance",)
    assert state.owned_keys("b") == ("shell.steps",)
    state.set_value("shell.steps", 35)
    assert state.status("b").kind is PresetMatch.CUSTOM
    assert state.status("a").kind is PresetMatch.EXACT
    state.reset_group("b")
    assert state.get("shell.steps") == 30
    assert state.get("shell.guidance") == 1.0


def test_mode_switch_is_outside_the_image_state() -> None:
    settings = reference_settings()
    layout = SimpleImageLayout(
        presets=reference_presets(settings), settings=settings, gate=_Allow()
    )
    layout.set_value("shell.steps", 24)
    layout.seed = 9
    first = layout.snapshot()
    assert set(first) == {"values", "chosen", "overrides"}
    assert layout.snapshot() == first
    assert layout.get("shell.steps") == 24
    assert layout.seed == 9


def test_expert_toggle_round_trip_keeps_live_values() -> None:
    """Simple and Expert are two views of one state.

    The toggle only re-filters which descriptors render
    (:meth:`Settings.visible`); it does not write the state. A value edited in
    the Expert panel and a chip chosen in simple mode are still there after
    the toggle leaves and returns.
    """
    settings = reference_settings()
    layout = SimpleImageLayout(
        presets=reference_presets(settings), settings=settings, gate=_Allow()
    )
    expert_ids = {spec.setting_id for spec in settings.visible(expert=True)}
    assert "shell.steps" in expert_ids
    assert "image.adult_content" in expert_ids
    assert {spec.setting_id for spec in settings.visible(expert=False)} == set()
    layout.choose("quality", "fast")
    layout.set_value("shell.steps", 24)
    layout.set_value("image.adult_content", True)
    layout.seed = 9
    held = layout.snapshot()
    settings.visible(expert=False)
    assert {spec.setting_id for spec in settings.visible(expert=True)} == expert_ids
    assert layout.snapshot() == held
    assert layout.get("shell.steps") == 24
    assert layout.get("image.adult_content") is True
    assert layout.presets["quality"] == "fast"
    assert layout.seed == 9


def test_settings_state_round_trip(tmp_path: Path) -> None:
    settings = reference_settings()
    document = reference_presets(settings)
    layout = SimpleImageLayout(
        presets=document, settings=settings, state_dir=tmp_path, gate=_Allow()
    )
    layout.choose("quality", "fast")
    layout.choose("style", "anime")
    layout.set_value("shell.steps", 15)
    layout.seed = 9
    again = SimpleImageLayout(
        presets=document, settings=settings, state_dir=tmp_path, gate=_Allow()
    )
    assert again.snapshot() == layout.snapshot()
    assert again.get("shell.steps") == 15
    assert again.get("shell.guidance") == 0.0
    assert again.get("shell.negative") == "realistic photo"
    assert again.presets["quality"] == "fast"
    status = again.preset_status("quality")
    assert status is not None and status.kind is PresetMatch.MODIFIED
    assert again.seed == 9
    (tmp_path / "image-settings.json").write_text("{", encoding="utf-8")
    broken = SettingState(document, settings, state_dir=tmp_path)
    assert broken.get("shell.steps") == 8
    assert broken.status("quality").option_id == "balanced"


def test_bad_preset_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="voxprint-presets/1"):
        parse_presets({"schema": 1})
    with pytest.raises(ValueError, match="at least one group"):
        parse_presets({"schema": PRESET_SCHEMA})
    path = tmp_path / "presets.json"
    path.write_text(json.dumps({"schema": "other"}), encoding="utf-8")
    with pytest.raises(ValueError, match="voxprint-presets/1"):
        load_presets(path)
    with pytest.raises(ValueError, match="same keys"):
        parse_presets(
            {
                "schema": PRESET_SCHEMA,
                "groups": [
                    {
                        "id": "quality",
                        "label_key": "simple.quality",
                        "default": "fast",
                        "options": [
                            {
                                "id": "fast",
                                "label_key": "simple.quality.fast",
                                "values": {"shell.steps": 12},
                            },
                            {
                                "id": "best",
                                "label_key": "simple.quality.best",
                                "values": {"shell.steps": 40, "shell.sampler": "dpm"},
                            },
                        ],
                    }
                ],
            }
        )

def test_image_presets_use_z_image_turbo_defaults() -> None:
    """Simple-mode quality chips match Z-Image Turbo when model.default is z-image."""
    from voxprint_common.suite_settings import DEFAULT_MODEL, model_default

    assert DEFAULT_MODEL == "z-image"
    assert model_default() == "z-image"
    settings = reference_settings()
    assert settings.get("shell.steps").default == 8
    assert settings.get("shell.guidance").default == 0.0
    assert settings.get("shell.guidance").minimum == 0.0
    state = SettingState(reference_presets(settings), settings)
    assert state.get("shell.steps") == 8
    assert state.get("shell.guidance") == 0.0
    assert state.get("shell.sampler") == "euler"
    state.choose("quality", "fast")
    assert state.get("shell.steps") == 4
    assert state.get("shell.guidance") == 0.0
    state.choose("quality", "best")
    assert state.get("shell.steps") == 16
    assert state.get("shell.guidance") == 0.0
    assert state.get("shell.sampler") == "euler"

