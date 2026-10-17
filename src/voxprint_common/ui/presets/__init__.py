"""Preset files that write one shared settings state.

Simple mode and Expert mode are two views of that state. A preset file is
schema ``voxprint-presets/1``. Each group is a row of plain chips. Each option
writes concrete values. The expert panel shows those live values.

Merge order, applied when the state is first built:

1. Start from each setting's default.
2. Walk groups in file order.
3. Write the chosen option's keys in the order they appear on that option.
4. When two groups write the same key, the later group in the file wins.
   That later group owns the key.
5. Apply stored overrides last. An override wins until the group is reset
   or the live values match a preset exactly.

Choosing a chip writes that option's keys immediately and leaves every other
group's keys alone. A hand edit marks the group that owns the key.

A stored option stays close while the number of owned keys that differ is
greater than zero and no more than half of those keys, rounded down. The chip
then reads "{name} + changes". Reset writes that option's values and does not
touch keys the option does not write. When the stored option is not close, and
no available option matches exactly, the chip reads "Custom". The stored
choice is kept so Reset can restore it.

Switching Expert mode does not write this state. The state file is
``image-settings.json`` in the app's State directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from voxprint_common.ui.safety import reject_reserved_key
from voxprint_common.ui.settings import Settings, SettingSpec, SettingType

__all__ = [
    "PRESET_SCHEMA",
    "PresetFile",
    "PresetGroup",
    "PresetMatch",
    "PresetOption",
    "PresetStatus",
    "SettingState",
    "load_presets",
    "parse_presets",
    "reference_presets",
]

PRESET_SCHEMA = "voxprint-presets/1"
_STATE_SCHEMA = 1
_STATE_NAME = "image-settings.json"
Value = bool | int | float | str


class PresetMatch(StrEnum):
    """How the live values relate to the stored preset."""

    EXACT = "exact"
    MODIFIED = "modified"
    CUSTOM = "custom"


@dataclass(frozen=True)
class PresetOption:
    """One chip and the values it writes."""

    option_id: str
    label_key: str
    values: tuple[tuple[str, Value], ...]
    group_key: str = ""
    available: bool = True
    reason_key: str = ""
    action_label_key: str = ""
    action_id: str = ""

    def as_map(self) -> dict[str, Value]:
        """Return the values in file order."""
        return dict(self.values)


@dataclass(frozen=True)
class PresetGroup:
    """One row of preset chips. Options write the same keys."""

    group_id: str
    label_key: str
    options: tuple[PresetOption, ...]
    default: str

    def option(self, option_id: str) -> PresetOption | None:
        """Return the option, or None when the id is unknown.

        Args:
            option_id: Chip id.
        """
        return next((item for item in self.options if item.option_id == option_id), None)

    @property
    def keys(self) -> tuple[str, ...]:
        """Return the keys every option in this group writes, in file order."""
        return tuple(key for key, _value in self.options[0].values)


@dataclass(frozen=True)
class PresetFile:
    """A validated ``voxprint-presets/1`` document."""

    groups: tuple[PresetGroup, ...]

    def group(self, group_id: str) -> PresetGroup | None:
        """Return a group, or None.

        Args:
            group_id: Group id.
        """
        return next((item for item in self.groups if item.group_id == group_id), None)

    def with_available(self, group_id: str, option_id: str, *, available: bool) -> PresetFile:
        """Return a copy with one option's availability changed.

        Args:
            group_id: Group that holds the option.
            option_id: Option to change.
            available: True makes the chip selectable.

        Raises:
            ValueError: The option is unknown, or the default would be unavailable.
        """
        groups: list[PresetGroup] = []
        found = False
        for group in self.groups:
            if group.group_id != group_id:
                groups.append(group)
                continue
            options: list[PresetOption] = []
            for option in group.options:
                if option.option_id != option_id:
                    options.append(option)
                    continue
                found = True
                if available:
                    options.append(
                        PresetOption(
                            option.option_id, option.label_key, option.values, option.group_key
                        )
                    )
                else:
                    options.append(option)
            groups.append(
                PresetGroup(group.group_id, group.label_key, tuple(options), group.default)
            )
        if not found:
            raise ValueError(f"unknown preset {group_id}={option_id}")
        document = PresetFile(tuple(groups))
        _require_available_default(document)
        return document


@dataclass(frozen=True)
class PresetStatus:
    """The chip label for one group."""

    kind: PresetMatch
    option_id: str
    label_key: str


class SettingState:
    """The one settings state for an app. Qt is not imported.

    Simple chips and the expert panel both read and write this object.
    """

    def __init__(
        self,
        presets: PresetFile,
        settings: Settings,
        *,
        state_dir: Path | None = None,
        state_name: str = _STATE_NAME,
    ) -> None:
        """Build the state from defaults, then the preset file, then overrides.

        Args:
            presets: Validated preset groups.
            settings: Descriptors for the values the expert panel shows.
            state_dir: The app's State directory. None keeps the state in memory.
            state_name: File name inside ``state_dir``. Image apps use
                ``image-settings.json``.
        """
        self._presets = presets
        self._settings = settings
        self._state_dir = state_dir
        self._state_name = state_name or _STATE_NAME
        self.values: dict[str, Value] = {}
        for spec in settings.visible(expert=True):
            if isinstance(spec.default, bool | int | float | str):
                self.values[spec.setting_id] = spec.default
        self.chosen: dict[str, str] = {}
        self.overrides: dict[str, set[str]] = {}
        for group in presets.groups:
            self.chosen[group.group_id] = group.default
            self.overrides[group.group_id] = set()
            option = group.option(group.default)
            if option is None:
                raise ValueError(f"preset {group.group_id} is missing its default")
            for key, value in option.values:
                reject_reserved_key(key)
                self.values[key] = value
        self._loaded = False
        self._load()
        self._loaded = True

    @property
    def presets(self) -> PresetFile:
        """Return the preset document."""
        return self._presets

    def get(self, key: str) -> Value:
        """Return the live value for ``key``.

        Args:
            key: Setting id or a preset key with no descriptor.

        Raises:
            KeyError: The key is not in the state.
        """
        if key not in self.values:
            raise KeyError(key)
        return self.values[key]

    def has(self, key: str) -> bool:
        """Return whether ``key`` is in the state.

        Args:
            key: Setting id.
        """
        return key in self.values

    def set_value(self, key: str, value: object) -> None:
        """Hand-edit one live value.

        The group that owns ``key`` is marked from the new distance. Other
        groups stay as they are. An exact match selects that preset again.

        Args:
            key: Setting id.
            value: The new value.

        Raises:
            ValueError: The value does not match the descriptor, or the key is reserved.
            KeyError: The key is unknown.
        """
        reject_reserved_key(key)
        if key not in self.values and self._settings.get(key) is None and self._owner(key) is None:
            raise KeyError(key)
        self.values[key] = _coerce(key, value, self._settings.get(key))
        self._normalize()
        self._write()

    def choose(self, group_id: str, option_id: str) -> None:
        """Select a preset and write its values immediately.

        Args:
            group_id: Preset group.
            option_id: Chip id.

        Raises:
            ValueError: The chip is unknown or unavailable.
        """
        group = self._presets.group(group_id)
        option = None if group is None else group.option(option_id)
        if group is None or option is None:
            raise ValueError(f"unknown preset {group_id}={option_id}")
        if not option.available:
            raise ValueError(f"preset {group_id}={option_id} is unavailable")
        for key, value in option.values:
            reject_reserved_key(key)
            self.values[key] = value
        self.chosen[group_id] = option_id
        self.overrides[group_id] = set()
        self._normalize()
        self._write()

    def reset_group(self, group_id: str) -> None:
        """Restore the stored preset's values for that group only.

        Args:
            group_id: Preset group.

        Raises:
            ValueError: The group is unknown.
        """
        group = self._presets.group(group_id)
        if group is None:
            raise ValueError(f"unknown preset group {group_id}")
        option = group.option(self.chosen[group_id])
        if option is None:
            raise ValueError(f"preset {group_id} has no stored choice")
        for key, value in option.values:
            self.values[key] = value
        self.overrides[group_id] = set()
        self._normalize()
        self._write()

    def status(self, group_id: str) -> PresetStatus:
        """Return whether the group is exact, changed, or custom.

        Args:
            group_id: Preset group.

        Raises:
            ValueError: The group is unknown.
        """
        group = self._presets.group(group_id)
        if group is None:
            raise ValueError(f"unknown preset group {group_id}")
        return self._classify(group)

    def snapshot(self) -> dict[str, object]:
        """Return the values, the chosen presets, and the overridden keys."""
        return {
            "values": dict(self.values),
            "chosen": dict(self.chosen),
            "overrides": {group_id: sorted(keys) for group_id, keys in self.overrides.items()},
        }

    def owned_keys(self, group_id: str) -> tuple[str, ...]:
        """Return the keys this group owns.

        A key belongs to the last group in the file that writes it.

        Args:
            group_id: Preset group.
        """
        group = self._presets.group(group_id)
        if group is None:
            return ()
        later: set[str] = set()
        seen = False
        for item in self._presets.groups:
            if item.group_id == group.group_id:
                seen = True
                continue
            if seen:
                later.update(item.keys)
        return tuple(key for key in group.keys if key not in later)

    def _owner(self, key: str) -> str | None:
        owner = None
        for group in self._presets.groups:
            if key in group.keys:
                owner = group.group_id
        return owner

    def _classify(self, group: PresetGroup) -> PresetStatus:
        owned = self.owned_keys(group.group_id)
        exact = [
            option
            for option in group.options
            if option.available and self._distance(option, owned) == 0
        ]
        if exact:
            option = exact[0]
            return PresetStatus(PresetMatch.EXACT, option.option_id, option.label_key)
        stored = group.option(self.chosen[group.group_id])
        if stored is None:
            fallback = group.option(group.default)
            if fallback is None:
                raise ValueError(f"preset {group.group_id} has no stored choice")
            stored = fallback
        distance = self._distance(stored, owned)
        limit = len(owned) // 2
        if distance > 0 and distance <= limit:
            return PresetStatus(PresetMatch.MODIFIED, stored.option_id, stored.label_key)
        return PresetStatus(PresetMatch.CUSTOM, stored.option_id, stored.label_key)

    def _distance(self, option: PresetOption, owned: tuple[str, ...]) -> int:
        values = option.as_map()
        return sum(1 for key in owned if not _same(self.values.get(key), values[key]))

    def _normalize(self) -> None:
        for group in self._presets.groups:
            status = self._classify(group)
            self.chosen[group.group_id] = status.option_id
            option = group.option(status.option_id)
            if option is None:
                continue
            if status.kind is PresetMatch.EXACT:
                self.overrides[group.group_id] = set()
                continue
            owned = set(self.owned_keys(group.group_id))
            values = option.as_map()
            self.overrides[group.group_id] = {
                key for key in owned if not _same(self.values.get(key), values[key])
            }

    def _load(self) -> None:
        if self._state_dir is None:
            return
        path = self._state_dir / self._state_name
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError, UnicodeError, json.JSONDecodeError:
            return
        if not isinstance(payload, dict) or payload.get("schema") != _STATE_SCHEMA:
            return
        values = payload.get("values")
        chosen = payload.get("chosen")
        if not isinstance(values, dict) or not isinstance(chosen, dict):
            return
        loaded: dict[str, Value] = {}
        for key, raw in values.items():
            if not isinstance(key, str):
                return
            reject_reserved_key(key)
            try:
                loaded[key] = _coerce(key, raw, self._settings.get(key))
            except ValueError:
                return
        for group in self._presets.groups:
            option_id = chosen.get(group.group_id)
            option = None if not isinstance(option_id, str) else group.option(option_id)
            if option is None:
                return
        overrides = payload.get("overrides")
        if isinstance(overrides, dict):
            for keys in overrides.values():
                if not isinstance(keys, list):
                    continue
                for key in keys:
                    if isinstance(key, str):
                        reject_reserved_key(key)
        self.values.update(loaded)
        for group in self._presets.groups:
            option_id = chosen[group.group_id]
            if isinstance(option_id, str):
                self.chosen[group.group_id] = option_id
        self._normalize()

    def _write(self) -> None:
        if self._state_dir is None or not self._loaded:
            return
        self._state_dir.mkdir(parents=True, exist_ok=True)
        payload = {"schema": _STATE_SCHEMA, **self.snapshot()}
        path = self._state_dir / self._state_name
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def parse_presets(payload: object, settings: Settings | None = None) -> PresetFile:
    """Validate a preset document.

    Args:
        payload: A decoded JSON value.
        settings: When given, every described key is checked against its type.

    Returns:
        The preset groups in file order.

    Raises:
        ValueError: The schema or a group is invalid.
    """
    if not isinstance(payload, dict) or payload.get("schema") != PRESET_SCHEMA:
        raise ValueError(f"presets schema must be {PRESET_SCHEMA}")
    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        raise ValueError("presets need at least one group")
    groups: list[PresetGroup] = []
    seen: set[str] = set()
    for raw in raw_groups:
        group = _group(raw, settings)
        if group.group_id in seen:
            raise ValueError(f"preset group {group.group_id} is repeated")
        seen.add(group.group_id)
        groups.append(group)
    document = PresetFile(tuple(groups))
    _require_available_default(document)
    return document


def load_presets(path: Path, settings: Settings | None = None) -> PresetFile:
    """Read and validate a preset file.

    Args:
        path: JSON file.
        settings: Descriptors used to check values.

    Raises:
        ValueError: The file is missing, unreadable, or invalid.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"presets file {path} is not valid JSON") from exc
    return parse_presets(payload, settings)


def reference_presets(settings: Settings, *, enlarge_installed: bool = False) -> PresetFile:
    """Return the reference preset file, using Canvas-like values.

    4K stays unavailable until ``enlarge_installed`` is true.

    Args:
        settings: Descriptors the values are checked against.
        enlarge_installed: True when Enlarge can make a 4K picture.
    """
    path = Path(__file__).with_name("canvas.json")
    document = load_presets(path, settings)
    if not enlarge_installed:
        return document
    return document.with_available("output", "4k", available=True)


def _group(raw: object, settings: Settings | None) -> PresetGroup:
    if not isinstance(raw, dict):
        raise ValueError("a preset group must be an object")
    group_id = raw.get("id")
    label_key = raw.get("label_key")
    default = raw.get("default")
    raw_options = raw.get("options")
    if (
        not isinstance(group_id, str)
        or not group_id
        or not isinstance(label_key, str)
        or not label_key
    ):
        raise ValueError("a preset group needs an id and a label")
    if not isinstance(default, str) or not isinstance(raw_options, list) or not raw_options:
        raise ValueError(f"preset group {group_id} needs a default and options")
    options = tuple(_option(item, settings) for item in raw_options)
    ids = [item.option_id for item in options]
    if len(ids) != len(set(ids)):
        raise ValueError(f"preset group {group_id} repeats an option")
    keys = options[0].as_map().keys()
    if any(set(item.as_map()) != set(keys) for item in options):
        raise ValueError(f"preset group {group_id} options must write the same keys")
    signatures = [tuple(item.values) for item in options]
    if len(signatures) != len(set(signatures)):
        raise ValueError(f"preset group {group_id} options must write different values")
    if default not in ids:
        raise ValueError(f"preset group {group_id} default is not an option")
    return PresetGroup(group_id, label_key, options, default)


def _option(raw: object, settings: Settings | None) -> PresetOption:
    if not isinstance(raw, dict):
        raise ValueError("a preset option must be an object")
    option_id = raw.get("id")
    label_key = raw.get("label_key")
    values = raw.get("values")
    if (
        not isinstance(option_id, str)
        or not option_id
        or not isinstance(label_key, str)
        or not label_key
    ):
        raise ValueError("a preset option needs an id and a label")
    if not isinstance(values, dict) or not values:
        raise ValueError(f"preset option {option_id} needs values")
    group_key = raw.get("group_key", "")
    available = raw.get("available", True)
    reason_key = raw.get("reason_key", "")
    action_label_key = raw.get("action_label_key", "")
    action_id = raw.get("action_id", "")
    if not isinstance(group_key, str) or not isinstance(available, bool):
        raise ValueError(f"preset option {option_id} has a bad group or availability")
    if (
        not isinstance(reason_key, str)
        or not isinstance(action_label_key, str)
        or not isinstance(action_id, str)
    ):
        raise ValueError(f"preset option {option_id} has a bad action")
    if not available and (not reason_key or not action_label_key or not action_id):
        raise ValueError(f"preset option {option_id} needs a plain reason and an action")
    coerced: list[tuple[str, Value]] = []
    for key, value in values.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"preset option {option_id} has a bad key")
        reject_reserved_key(key)
        spec = None if settings is None else settings.get(key)
        coerced.append((key, _coerce(key, value, spec)))
    return PresetOption(
        option_id,
        label_key,
        tuple(coerced),
        group_key,
        available,
        reason_key,
        action_label_key,
        action_id,
    )


def _require_available_default(document: PresetFile) -> None:
    for group in document.groups:
        option = group.option(group.default)
        if option is None or not option.available:
            raise ValueError(f"preset group {group.group_id} default must be available")


def _coerce(key: str, value: object, spec: SettingSpec | None) -> Value:
    if spec is None:
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            raise ValueError(f"preset value {key} must be text or a number")
        return value
    kind = spec.type
    if kind is SettingType.BOOL:
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value
    if kind is SettingType.INT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer")
        _check(key, value, spec)
        return value
    if kind is SettingType.FLOAT:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{key} must be a number")
        number = float(value)
        _check(key, number, spec)
        return number
    if kind is SettingType.ENUM:
        if not isinstance(value, str) or value not in {choice.value for choice in spec.choices}:
            raise ValueError(f"{key} must be one of the choices")
        return value
    if kind is SettingType.PATH or kind is SettingType.TEXT:
        if not isinstance(value, str):
            raise ValueError(f"{key} must be text")
        return value
    assert_never(kind)


def _check(key: str, value: float, spec: SettingSpec) -> None:
    if spec.minimum is None or spec.maximum is None or not spec.minimum <= value <= spec.maximum:
        raise ValueError(f"{key} is outside its range")


def _same(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, int | float) and isinstance(right, int | float):
        return abs(float(left) - float(right)) < 1e-6
    return left == right
