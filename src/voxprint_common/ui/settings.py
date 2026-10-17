"""Setting descriptors for simple and expert controls.

A descriptor is the label, the help, the type, the default, the expert flag,
and the group. It does not store a user's value and it does not import Qt.
A minors-safety gate stays inside the app. This module has no setting, hook,
or function that turns that gate off. Adult content is an ordinary descriptor
the app supplies.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

__all__ = [
    "Choice",
    "SettingSpec",
    "SettingType",
    "Settings",
    "reference_settings",
]


class SettingType(StrEnum):
    """The control a setting draws."""

    BOOL = "bool"
    ENUM = "enum"
    INT = "int"
    FLOAT = "float"
    PATH = "path"
    TEXT = "text"


@dataclass(frozen=True)
class Choice:
    """One enum value and the i18n key of its label."""

    value: str
    label_key: str


@dataclass(frozen=True)
class SettingSpec:
    """One setting an app can show or hide with Expert mode.

    ``label_key`` and ``help_key`` are catalog keys. ``help_key`` is a tooltip,
    and tooltips are for expert settings. ``default`` is the sensible value
    used while the control is hidden.
    """

    setting_id: str
    label_key: str
    help_key: str
    type: SettingType
    default: bool | int | float | str | None
    expert: bool = False
    group: str = ""
    choices: tuple[Choice, ...] = ()
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        """Reject a default that does not match ``type``."""
        if not self.setting_id or not self.label_key or not self.help_key:
            raise ValueError("a setting needs an id, a label, and help")
        kind = self.type
        if kind is SettingType.BOOL:
            if not isinstance(self.default, bool):
                raise ValueError("a bool setting default must be true or false")
            return
        if kind is SettingType.ENUM:
            values = {choice.value for choice in self.choices}
            if not values or self.default not in values:
                raise ValueError("an enum default must be one of the choices")
            return
        if kind is SettingType.INT:
            if isinstance(self.default, bool) or not isinstance(self.default, int):
                raise ValueError("an int setting default must be an integer")
            _check_range(self.default, self.minimum, self.maximum)
            return
        if kind is SettingType.FLOAT:
            if isinstance(self.default, bool) or not isinstance(self.default, int | float):
                raise ValueError("a float setting default must be a number")
            _check_range(float(self.default), self.minimum, self.maximum)
            return
        if kind is SettingType.PATH:
            if self.default is not None and not isinstance(self.default, str):
                raise ValueError("a path setting default must be text or null")
            return
        if kind is SettingType.TEXT:
            if not isinstance(self.default, str):
                raise ValueError("a text setting default must be text")
            return
        assert_never(kind)


class Settings:
    """The settings an app registered, in registration order."""

    def __init__(self, specs: Iterable[SettingSpec] = ()) -> None:
        """Register ``specs``.

        Args:
            specs: Descriptors. Ids must be unique.

        Raises:
            ValueError: An id is already registered.
        """
        self._specs: dict[str, SettingSpec] = {}
        for spec in specs:
            self.add(spec)

    def add(self, spec: SettingSpec) -> None:
        """Register ``spec``.

        Args:
            spec: A setting whose id is not already registered.

        Raises:
            ValueError: The id is already present.
        """
        if spec.setting_id in self._specs:
            raise ValueError(f"setting {spec.setting_id} is already registered")
        self._specs[spec.setting_id] = spec

    def get(self, setting_id: str) -> SettingSpec | None:
        """Return a setting, or None when the id is unknown.

        Args:
            setting_id: Stable setting id.
        """
        return self._specs.get(setting_id)

    def visible(self, *, expert: bool) -> tuple[SettingSpec, ...]:
        """Return settings to show for this mode.

        Expert settings are left out when ``expert`` is false.

        Args:
            expert: True when Expert mode is on.
        """
        return tuple(spec for spec in self._specs.values() if expert or not spec.expert)


def reference_settings() -> Settings:
    """Return the settings the reference shell shows.

    How many pictures is a preset chip on the image screen, not a setting.
    The expert side panel adds steps, guidance, seed, sampler, a negative
    prompt, add-ons, a memory limit, and adult content. The app owns what
    those values do. A path field is expert: Open folder is the everyday action.
    """
    return Settings(
        (
            SettingSpec(
                "shell.steps",
                "setting.shell.steps",
                "setting.shell.steps.help",
                SettingType.INT,
                20,
                expert=True,
                group="tuning",
                minimum=1,
                maximum=150,
            ),
            SettingSpec(
                "shell.guidance",
                "setting.shell.guidance",
                "setting.shell.guidance.help",
                SettingType.FLOAT,
                7.0,
                expert=True,
                group="tuning",
                minimum=1.0,
                maximum=30.0,
            ),
            SettingSpec(
                "shell.seed",
                "setting.shell.seed",
                "setting.shell.seed.help",
                SettingType.INT,
                0,
                expert=True,
                group="tuning",
                minimum=0,
                maximum=2_147_483_647,
            ),
            SettingSpec(
                "shell.sampler",
                "setting.shell.sampler",
                "setting.shell.sampler.help",
                SettingType.ENUM,
                "euler",
                expert=True,
                group="tuning",
                choices=(
                    Choice("euler", "setting.shell.sampler.euler"),
                    Choice("dpm", "setting.shell.sampler.dpm"),
                    Choice("heun", "setting.shell.sampler.heun"),
                ),
            ),
            SettingSpec(
                "shell.negative",
                "setting.shell.negative",
                "setting.shell.negative.help",
                SettingType.TEXT,
                "",
                expert=True,
                group="tuning",
            ),
            SettingSpec(
                "shell.addons",
                "setting.shell.addons",
                "setting.shell.addons.help",
                SettingType.PATH,
                "",
                expert=True,
                group="tuning",
            ),
            SettingSpec(
                "shell.vram",
                "setting.shell.vram",
                "setting.shell.vram.help",
                SettingType.ENUM,
                "auto",
                expert=True,
                group="tuning",
                choices=(
                    Choice("auto", "setting.shell.vram.auto"),
                    Choice("8", "setting.shell.vram.8"),
                    Choice("12", "setting.shell.vram.12"),
                    Choice("16", "setting.shell.vram.16"),
                    Choice("24", "setting.shell.vram.24"),
                ),
            ),
            SettingSpec(
                "image.adult_content",
                "setting.image.adult_content",
                "setting.image.adult_content.help",
                SettingType.BOOL,
                False,
                expert=True,
                group="image",
            ),
        )
    )


def _check_range(value: float, minimum: float | None, maximum: float | None) -> None:
    if minimum is None or maximum is None or minimum > maximum or not minimum <= value <= maximum:
        raise ValueError("a ranged setting needs a default inside minimum and maximum")
