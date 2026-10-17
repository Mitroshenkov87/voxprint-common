"""Shared simple screen: one layout, thin app configurations."""

from __future__ import annotations

from pathlib import Path

import pytest

from voxprint_common.i18n import set_language
from voxprint_common.ui.presets import PresetMatch
from voxprint_common.ui.safety import ReviewedOutput, reject_reserved_key
from voxprint_common.ui.settings import (
    UNIT_DB,
    UNIT_KHZ,
    UNIT_PERCENT,
    UNIT_PX,
    SettingSpec,
    SettingType,
    format_setting_value,
    setting_suffix,
    spin_suffix,
)
from voxprint_common.ui.simple_image import SimpleLayout
from voxprint_common.ui.variants import (
    LOWER_MIN_HEIGHT,
    LOWER_MIN_WIDTH,
    WINDOW_HEIGHT,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
    WINDOW_WIDTH,
    InputKind,
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
        "viewer",
    )
    seen: set[str] = set()
    for surface in surfaces():
        settings = settings_for(surface)
        document = variant_presets(surface, settings)
        if surface.variant is VariantId.VIEWER:
            assert document.group("view") is not None
        else:
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
    assert audio.voice_picker is True and audio.video_result is False
    assert dub.voice_picker is False and dub.video_result is True
    assert image.voice_picker is True and image.video_result is False
    assert surface_for("clarity").result_kind.value == "compare"
    assert surface_for("plotweaver").action_key == "simple.action.write"
    assert surface_for("model-forge").action_key == "simple.action.prepare"
    viewer = surface_for("viewer")
    assert viewer.variant is VariantId.VIEWER
    assert viewer.input_kind is InputKind.NONE
    assert (viewer.min_width, viewer.min_height) == (LOWER_MIN_WIDTH, LOWER_MIN_HEIGHT)
    assert (WINDOW_WIDTH, WINDOW_HEIGHT) == (1200, 900)
    assert (surface_for("canvas").min_width, surface_for("canvas").min_height) == (
        WINDOW_MIN_WIDTH,
        WINDOW_MIN_HEIGHT,
    )
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
        variant=surface.variant,
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
    layout.finish("job-1", ReviewedOutput(data=b"wav", mime="audio/wav"))
    pace = layout.preset_status("pace")
    assert pace is not None and pace.kind is PresetMatch.EXACT
    layout.choose("pace", "calm")
    assert (tmp_path / "audio-settings.json").is_file()
    assert (tmp_path / "audio-history.json").is_file()


def test_viewer_history_setting_defaults_on(tmp_path: Path) -> None:
    surface = surface_for("viewer")
    settings = settings_for(surface)
    assert settings.get("viewer.show_history") is not None
    layout = SimpleLayout(
        presets=variant_presets(surface, settings),
        settings=settings,
        state_dir=tmp_path,
        gate=_Allow(),
        state_name="viewer-settings.json",
        history_name="viewer-history.json",
        variant=surface.variant,
    )
    assert layout.get("viewer.show_history") is True
    layout.set_value("viewer.show_history", False)
    assert layout.get("viewer.show_history") is False
    assert state_names(surface) == ("viewer-settings.json", "viewer-history.json")


def test_missing_gate_blocks_every_variant(tmp_path: Path) -> None:
    surface = surface_for("plotweaver")
    settings = settings_for(surface)
    layout = SimpleLayout(
        presets=variant_presets(surface, settings),
        settings=settings,
        state_dir=tmp_path,
        variant=surface.variant,
    )
    layout.set_prompt("A quiet hill at dusk")
    assert layout.generate() is False
    assert layout.phase.value == "blocked"


def test_setting_suffix_follows_the_language() -> None:
    percent = SettingSpec(
        "demo.likeness",
        "setting.audio.loudness",
        "setting.audio.loudness.help",
        SettingType.INT,
        50,
        minimum=0,
        maximum=100,
        suffix_key="%",
    )
    level = SettingSpec(
        "demo.level",
        "setting.audio.loudness",
        "setting.audio.loudness.help",
        SettingType.FLOAT,
        -16.0,
        minimum=-24,
        maximum=0,
        suffix_key=UNIT_DB,
    )
    plain = SettingSpec(
        "demo.plain",
        "setting.audio.loudness",
        "setting.audio.loudness.help",
        SettingType.INT,
        1,
        minimum=0,
        maximum=2,
    )
    assert setting_suffix(percent) == "%"
    assert spin_suffix(percent) == "%"
    assert format_setting_value(percent, 50) == "50%"
    assert format_setting_value(plain, 1) == "1"
    assert spin_suffix(plain) == ""
    set_language("en")
    assert format_setting_value(level, -16) == "-16.0 dB"
    assert spin_suffix(level) == " dB"
    rate = SettingSpec(
        "demo.rate",
        "setting.audio.loudness",
        "setting.audio.loudness.help",
        SettingType.FLOAT,
        44.0,
        minimum=0,
        maximum=96,
        suffix_key=UNIT_KHZ,
    )
    pixels = SettingSpec(
        "demo.pixels",
        "setting.audio.loudness",
        "setting.audio.loudness.help",
        SettingType.INT,
        12,
        minimum=0,
        maximum=64,
        suffix_key=UNIT_PX,
    )
    assert format_setting_value(rate, 44) == "44.0 kHz"
    assert setting_suffix(pixels) == "px"
    assert UNIT_PERCENT == "unit.percent"
    set_language("ru")
    assert format_setting_value(level, -16) == "-16.0 дБ"
    assert setting_suffix(rate) == "кГц"
    set_language("de")
    assert setting_suffix(level) == "dB"
    set_language("en")


def test_movie_dubber_keeps_video_paths(tmp_path: Path) -> None:
    surface = surface_for("movie-dubber")
    settings = settings_for(surface)
    names = state_names(surface)
    layout = SimpleLayout(
        presets=variant_presets(surface, settings),
        settings=settings,
        state_dir=tmp_path,
        state_name=names[0],
        history_name=names[1],
        variant=surface.variant,
    )
    layout.set_video_source("/films/quiet.mp4")
    layout.set_video_output("/films/quiet/dubbed.mp4")
    assert layout.video_source == "/films/quiet.mp4"
    assert layout.video_output == "/films/quiet/dubbed.mp4"
    assert layout.video_source not in layout.prompt


class _Allow:
    def review_prompt(self, prompt: str) -> str | None:
        return None

    def review_result(self, prompt: str, item_id: str, output: ReviewedOutput) -> str | None:
        del prompt, item_id, output
        return None
