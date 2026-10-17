"""Thin configurations of the shared simple screen.

Image, audio, video, text, and tool are not separate layouts. Each app names
the input, the primary action, the result, and a ``voxprint-presets/1`` file.
The screen itself is :class:`~voxprint_common.ui.simple_image.SimpleLayout`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from voxprint_common.ui.presets import PresetFile, load_presets, reference_presets
from voxprint_common.ui.settings import (
    Choice,
    Settings,
    SettingSpec,
    SettingType,
    reference_settings,
)

__all__ = [
    "AppSurface",
    "InputKind",
    "ResultKind",
    "VariantId",
    "preset_path",
    "settings_for",
    "state_names",
    "surface_for",
    "surfaces",
    "variant_presets",
]


class VariantId(StrEnum):
    """The five shapes of the shared screen."""

    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    TEXT = "text"
    TOOL = "tool"


class InputKind(StrEnum):
    """What the top of the screen asks for."""

    PROMPT = "prompt"
    FILE = "file"
    MEDIA_VOICE = "media_voice"
    OUTLINE = "outline"
    MODEL = "model"


class ResultKind(StrEnum):
    """What the large result area shows."""

    PICTURE = "picture"
    PLAYER = "player"
    COMPARE = "compare"
    DOCUMENT = "document"
    REPORT = "report"


@dataclass(frozen=True)
class AppSurface:
    """One app's configuration of the shared screen.

    ``preset_name`` is the file stem under ``presets/``. ``action_key``,
    ``empty_key``, ``placeholder_key``, and ``drop_key`` are catalog keys.
    """

    app_id: str
    variant: VariantId
    input_kind: InputKind
    result_kind: ResultKind
    action_key: str
    empty_key: str
    placeholder_key: str
    preset_name: str
    drop_key: str = ""


def surfaces() -> tuple[AppSurface, ...]:
    """Return every app surface, in suite order, then Model Forge."""
    return _SURFACES


def surface_for(app_id: str) -> AppSurface:
    """Return the surface for ``app_id``.

    Args:
        app_id: Suite app id, for example ``canvas`` or ``clarity``.

    Raises:
        ValueError: The app has no surface.
    """
    for surface in _SURFACES:
        if surface.app_id == app_id:
            return surface
    raise ValueError(f"unknown app surface {app_id}")


def preset_path(name: str) -> Path:
    """Return the example preset file for ``name``.

    Args:
        name: File stem, for example ``audio`` or ``canvas``.
    """
    return Path(__file__).with_name("presets") / f"{name}.json"


def settings_for(surface: AppSurface) -> Settings:
    """Return the settings state descriptors for ``surface``.

    Image apps use the reference list. Enlarge adds a scale. The other
    variants use a short list of their own. None of these ids is a minors gate.

    Args:
        surface: The app configuration.
    """
    variant = surface.variant
    if variant is VariantId.IMAGE:
        if surface.app_id == "enlarge":
            return _enlarge_settings()
        return reference_settings()
    if variant is VariantId.AUDIO:
        return _audio_settings()
    if variant is VariantId.VIDEO:
        return _video_settings()
    if variant is VariantId.TEXT:
        return _text_settings()
    if variant is VariantId.TOOL:
        return _tool_settings()
    assert_never(variant)


def variant_presets(
    surface: AppSurface,
    settings: Settings,
    *,
    enlarge_installed: bool = False,
) -> PresetFile:
    """Load the example preset file for ``surface``.

    Canvas and the Lucent helper use the Canvas file. 4K, and Enlarge's 4x,
    stay unavailable until ``enlarge_installed`` is true.

    Args:
        surface: The app configuration.
        settings: Descriptors the values are checked against.
        enlarge_installed: True when Enlarge can make a larger result.
    """
    if surface.preset_name == "canvas":
        return reference_presets(settings, enlarge_installed=enlarge_installed)
    document = load_presets(preset_path(surface.preset_name), settings)
    if surface.preset_name == "enlarge" and enlarge_installed:
        return document.with_available("scale", "4x", available=True)
    return document


def state_names(surface: AppSurface) -> tuple[str, str]:
    """Return the settings file name and the history file name.

    Image apps keep ``image-settings.json`` and ``image-history.json``. The
    other variants use the variant id, so an app directory does not mix them.

    Args:
        surface: The app configuration.
    """
    if surface.variant is VariantId.IMAGE:
        return ("image-settings.json", "image-history.json")
    name = surface.variant.value
    return (f"{name}-settings.json", f"{name}-history.json")


def _enlarge_settings() -> Settings:
    settings = Settings(reference_settings().visible(expert=True))
    settings.add(
        SettingSpec(
            "image.scale",
            "setting.image.scale",
            "setting.image.scale.help",
            SettingType.INT,
            2,
            group="image",
            minimum=2,
            maximum=4,
        )
    )
    return settings


def _audio_settings() -> Settings:
    return Settings(
        (
            SettingSpec(
                "audio.pace",
                "setting.audio.pace",
                "setting.audio.pace.help",
                SettingType.ENUM,
                "natural",
                group="audio",
                choices=(
                    Choice("calm", "simple.preset.pace.calm"),
                    Choice("natural", "simple.preset.pace.natural"),
                    Choice("brisk", "simple.preset.pace.brisk"),
                ),
            ),
            SettingSpec(
                "audio.sample_rate",
                "setting.audio.sample_rate",
                "setting.audio.sample_rate.help",
                SettingType.ENUM,
                "44",
                expert=True,
                group="audio",
                choices=(
                    Choice("22", "setting.audio.sample_rate.22"),
                    Choice("44", "setting.audio.sample_rate.44"),
                ),
            ),
        )
    )


def _video_settings() -> Settings:
    return Settings(
        (
            SettingSpec(
                "video.strength",
                "setting.video.strength",
                "setting.video.strength.help",
                SettingType.ENUM,
                "gentle",
                group="video",
                choices=(
                    Choice("gentle", "simple.preset.strength.gentle"),
                    Choice("strong", "simple.preset.strength.strong"),
                ),
            ),
            SettingSpec(
                "video.stabilize",
                "setting.video.stabilize",
                "setting.video.stabilize.help",
                SettingType.BOOL,
                True,
                expert=True,
                group="video",
            ),
        )
    )


def _text_settings() -> Settings:
    return Settings(
        (
            SettingSpec(
                "text.length",
                "setting.text.length",
                "setting.text.length.help",
                SettingType.ENUM,
                "chapter",
                group="text",
                choices=(
                    Choice("short", "simple.preset.length.short"),
                    Choice("chapter", "simple.preset.length.chapter"),
                    Choice("long", "simple.preset.length.long"),
                ),
            ),
            SettingSpec(
                "text.tone",
                "setting.text.tone",
                "setting.text.tone.help",
                SettingType.ENUM,
                "plain",
                expert=True,
                group="text",
                choices=(
                    Choice("plain", "setting.text.tone.plain"),
                    Choice("warm", "setting.text.tone.warm"),
                ),
            ),
        )
    )


def _tool_settings() -> Settings:
    return Settings(
        (
            SettingSpec(
                "tool.precision",
                "setting.tool.precision",
                "setting.tool.precision.help",
                SettingType.ENUM,
                "careful",
                group="tool",
                choices=(
                    Choice("fast", "simple.preset.precision.fast"),
                    Choice("careful", "simple.preset.precision.careful"),
                ),
            ),
            SettingSpec(
                "tool.device",
                "setting.tool.device",
                "setting.tool.device.help",
                SettingType.ENUM,
                "auto",
                expert=True,
                group="tool",
                choices=(
                    Choice("auto", "setting.tool.device.auto"),
                    Choice("cpu", "setting.tool.device.cpu"),
                ),
            ),
        )
    )


_SURFACES = (
    AppSurface(
        "canvas",
        VariantId.IMAGE,
        InputKind.PROMPT,
        ResultKind.PICTURE,
        "simple.generate",
        "simple.empty",
        "shell.prompt.placeholder",
        "canvas",
    ),
    AppSurface(
        "enlarge",
        VariantId.IMAGE,
        InputKind.FILE,
        ResultKind.PICTURE,
        "simple.action.enlarge",
        "simple.empty",
        "simple.drop.picture",
        "enlarge",
        "simple.drop.picture",
    ),
    AppSurface(
        "lucent",
        VariantId.IMAGE,
        InputKind.PROMPT,
        ResultKind.PICTURE,
        "simple.generate",
        "simple.empty",
        "shell.prompt.placeholder",
        "canvas",
    ),
    AppSurface(
        "audiobook-builder",
        VariantId.AUDIO,
        InputKind.MEDIA_VOICE,
        ResultKind.PLAYER,
        "simple.action.audiobook",
        "simple.empty.audio",
        "simple.drop.book",
        "audio",
        "simple.drop.book",
    ),
    AppSurface(
        "movie-dubber",
        VariantId.AUDIO,
        InputKind.MEDIA_VOICE,
        ResultKind.PLAYER,
        "simple.action.dub",
        "simple.empty.audio",
        "simple.drop.video",
        "audio",
        "simple.drop.video",
    ),
    AppSurface(
        "clarity",
        VariantId.VIDEO,
        InputKind.FILE,
        ResultKind.COMPARE,
        "simple.action.improve_video",
        "simple.empty.video",
        "simple.drop.video",
        "video",
        "simple.drop.video",
    ),
    AppSurface(
        "plotweaver",
        VariantId.TEXT,
        InputKind.OUTLINE,
        ResultKind.DOCUMENT,
        "simple.action.write",
        "simple.empty.text",
        "simple.placeholder.outline",
        "text",
    ),
    AppSurface(
        "model-forge",
        VariantId.TOOL,
        InputKind.MODEL,
        ResultKind.REPORT,
        "simple.action.prepare",
        "simple.empty.tool",
        "simple.drop.model",
        "tool",
        "simple.drop.model",
    ),
)
