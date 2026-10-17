"""Shared simple screen: one layout, five thin app configurations."""

from __future__ import annotations

from pathlib import Path

import pytest

from voxprint_common.ui.presets import PresetMatch
from voxprint_common.ui.safety import reject_reserved_key
from voxprint_common.ui.simple_image import SimpleLayout
from voxprint_common.ui.variants import (
    VariantId,
    settings_for,
    state_names,
    surface_for,
    surfaces,
    variant_presets,
)


def test_every_surface_loads_plain_presets() -> None:
    ids = tuple(surface.app_id for surface in surfaces())
    assert ids == (
        "canvas",
        "enlarge",
        "lucent",
        "audiobook-builder",
        "movie-dubber",
        "clarity",
        "plotweaver",
        "model-forge",
    )
    seen: set[str] = set()
    for surface in surfaces():
        settings = settings_for(surface)
        document = variant_presets(surface, settings)
        assert document.groups
        for group in document.groups:
            for option in group.options:
                for key, _value in option.values:
                    reject_reserved_key(key)
        seen.add(surface.variant.value)
    assert seen == {item.value for item in VariantId}


def test_variants_are_configurations_not_separate_layouts() -> None:
    image = surface_for("canvas")
    enlarge = surface_for("enlarge")
    audio = surface_for("audiobook-builder")
    dub = surface_for("movie-dubber")
    assert image.variant is enlarge.variant is VariantId.IMAGE
    assert image.action_key == "simple.generate"
    assert enlarge.action_key == "simple.action.enlarge"
    assert enlarge.drop_key == "simple.drop.picture"
    assert audio.variant is dub.variant is VariantId.AUDIO
    assert audio.action_key == "simple.action.audiobook"
    assert dub.action_key == "simple.action.dub"
    assert audio.preset_name == dub.preset_name == "audio"
    assert surface_for("clarity").result_kind.value == "compare"
    assert surface_for("plotweaver").action_key == "simple.action.write"
    assert surface_for("model-forge").action_key == "simple.action.prepare"
    with pytest.raises(ValueError, match="unknown app"):
        surface_for("missing")


def test_enlarge_scale_stays_unavailable_until_installed() -> None:
    surface = surface_for("enlarge")
    settings = settings_for(surface)
    plain = variant_presets(surface, settings)
    scale = plain.group("scale")
    assert scale is not None
    option = scale.option("4x")
    assert option is not None and option.available is False
    ready = variant_presets(surface, settings, enlarge_installed=True)
    ready_scale = ready.group("scale")
    assert ready_scale is not None
    unlocked = ready_scale.option("4x")
    assert unlocked is not None and unlocked.available is True


def test_audio_state_file_and_progress_eta(tmp_path: Path) -> None:
    surface = surface_for("audiobook-builder")
    settings = settings_for(surface)
    assert settings.get("image.adult_content") is None
    assert settings.get("audio.sample_rate") is not None
    settings_file, history_file = state_names(surface)
    layout = SimpleLayout(
        presets=variant_presets(surface, settings),
        settings=settings,
        state_dir=tmp_path,
        gate=_Allow(),
        state_name=settings_file,
        history_name=history_file,
    )
    layout.set_prompt("A quiet chapter")
    assert layout.generate() is True
    layout.progress(1, 4, "Reading", eta_seconds=45)
    assert layout.percent == 25
    assert layout.eta_seconds == 45
    layout.progress(2, 4, "Reading")
    assert layout.percent == 50
    assert layout.eta_seconds is None
    with pytest.raises(ValueError, match="eta"):
        layout.progress(2, 4, "Reading", eta_seconds=-1)
    layout.finish("job-1")
    pace = layout.preset_status("pace")
    assert pace is not None and pace.kind is PresetMatch.EXACT
    layout.choose("pace", "calm")
    assert (tmp_path / "audio-settings.json").is_file()
    assert (tmp_path / "audio-history.json").is_file()


def test_missing_gate_blocks_every_variant(tmp_path: Path) -> None:
    surface = surface_for("plotweaver")
    settings = settings_for(surface)
    layout = SimpleLayout(
        presets=variant_presets(surface, settings),
        settings=settings,
        state_dir=tmp_path,
    )
    layout.set_prompt("A quiet hill at dusk")
    assert layout.generate() is False
    assert layout.phase.value == "blocked"


class _Allow:
    def review_prompt(self, prompt: str) -> str | None:
        return None

    def review_result(self, prompt: str, item_id: str) -> str | None:
        return None
