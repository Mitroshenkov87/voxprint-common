"""Chords, context overrides, and user keymap overrides.

``Mod`` is the primary modifier: Ctrl on Windows and Linux, Command on
macOS. The stored spelling is ``Mod+Shift+S``. On Windows and Linux,
``Ctrl`` is the same key as ``Mod`` and folds into it, so the two conflict.
On macOS they stay distinct. A context binding beats a global binding while
that context is active. A text field keeps the keys: only a ``when`` of
``text`` runs there.

Invalid saved entries are dropped. Overrides live in the suite settings
file under ``keymap``.

Chord spelling is translated from WordCraft ``crates/engine/src/keymap.rs``
at ``eae242176108d328c5dd81fbc10370c51ab90706`` and from FilmCraft
``crates/engine/src/shortcuts.rs`` at ``09aaf9bf15d510eda1c413c0ccabb1d7e08b41c5``,
both MIT OR Apache-2.0.
Copyright (c) 2026 ArtCraft Team and the WordCraft contributors.
Copyright (c) 2026 ArtCraft Team and the FilmCraft contributors.
VOXPrint distributes this translation under the Apache License, Version 2.0.

Their preset files are not included. Qt is not imported.
"""

from __future__ import annotations

import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from voxprint_common.suite_settings import keymap_document, set_keymap
from voxprint_common.ui.commands import CommandSpec, Registry

__all__ = [
    "MAX_ENTRIES",
    "Assignment",
    "Conflict",
    "KeyMap",
    "Removal",
    "display_chord",
    "effective",
    "host_platform",
    "load_overrides",
    "normalize",
    "save_overrides",
]

MAX_ENTRIES = 2000
_MAX_KEY_LEN = 48
_MAX_ID = 128
_MAX_WHEN = 64

_NAMED = (
    "Left",
    "Right",
    "Up",
    "Down",
    "Home",
    "End",
    "PageUp",
    "PageDown",
    "Enter",
    "Tab",
    "Backspace",
    "Delete",
    "Escape",
    "Space",
    "Insert",
)
_ALIAS = {
    "return": "Enter",
    "numpadenter": "Enter",
    "esc": "Escape",
    "del": "Delete",
    "forwarddelete": "Delete",
    "fwddelete": "Delete",
    "back": "Backspace",
    "arrowup": "Up",
    "arrowdown": "Down",
    "arrowleft": "Left",
    "arrowright": "Right",
    "pgup": "PageUp",
    "pageup": "PageUp",
    "pgdn": "PageDown",
    "pagedown": "PageDown",
    "spacebar": "Space",
    "ins": "Insert",
    "help": "Insert",
    "grave": "`",
    "backtick": "`",
    "backquote": "`",
    "minus": "-",
    "equal": "=",
    "equals": "=",
    "semicolon": ";",
    "quote": "'",
    "apostrophe": "'",
    "comma": ",",
    "period": ".",
    "dot": ".",
    "slash": "/",
    "backslash": "\\",
    "openbracket": "[",
    "bracketleft": "[",
    "closebracket": "]",
    "bracketright": "]",
}
_MOD_ORDER = ("Mod", "Ctrl", "Alt", "Shift")


@dataclass(frozen=True)
class Assignment:
    """A user chord for one command in one context."""

    chord: str
    command_id: str
    when: str = "global"


@dataclass(frozen=True)
class Removal:
    """A built-in chord the user turned off."""

    chord: str
    when: str = "global"


@dataclass(frozen=True)
class Conflict:
    """Two or more commands bound to one chord in one context."""

    when: str
    chord: str
    command_ids: tuple[str, ...]


@dataclass
class _Binding:
    command_id: str
    chord: str
    when: str


@dataclass
class KeyMap:
    """User assignments and removals on top of the registry defaults."""

    assigned: list[Assignment] = field(default_factory=list)
    removed: list[Removal] = field(default_factory=list)

    def assign(
        self,
        registry: Registry,
        command_id: str,
        chord: str,
        *,
        when: str = "global",
        platform: str | None = None,
    ) -> str | None:
        """Give ``chord`` to ``command_id`` in ``when``.

        The chord leaves whatever command had it in that context. Assigning
        a command its own built-in chord clears the customisation.

        Args:
            registry: Known commands.
            command_id: Command that should receive the chord.
            chord: A chord such as ``Mod+Shift+K``.
            when: Context id. ``global`` is the default.
            platform: Host used to fold ``Ctrl``. Defaults to this machine.

        Returns:
            The command id that lost the chord, or None.

        Raises:
            ValueError: The command is unknown or the chord is not a key.
        """
        if registry.get(command_id) is None:
            raise ValueError(f"unknown command {command_id}")
        host = platform or host_platform()
        stored = _stored(chord, host)
        if stored is None:
            raise ValueError(f"{chord!r} is not a chord")
        previous = self._owner(registry, stored, when, host)
        builtin = _builtin(registry, stored, when, host)
        self.assigned = [
            item
            for item in self.assigned
            if effective(item.chord, host) != stored or item.when != when
        ]
        self.removed = [
            item
            for item in self.removed
            if effective(item.chord, host) != stored or item.when != when
        ]
        if builtin != command_id:
            self.assigned.append(Assignment(stored, command_id, when))
            if builtin is not None:
                self.removed.append(Removal(stored, when))
        if previous == command_id:
            return None
        return previous

    def clear(
        self,
        registry: Registry,
        command_id: str,
        chord: str,
        *,
        when: str = "global",
        platform: str | None = None,
    ) -> bool:
        """Take ``chord`` away from ``command_id``.

        Args:
            registry: Known commands.
            command_id: Command that currently owns the chord.
            chord: Chord to clear.
            when: Context id.
            platform: Host used to fold ``Ctrl``.

        Returns:
            True when that command had the chord.

        Raises:
            ValueError: ``chord`` is not a key.
        """
        host = platform or host_platform()
        stored = _stored(chord, host)
        if stored is None:
            raise ValueError(f"{chord!r} is not a chord")
        if self._owner(registry, stored, when, host) != command_id:
            return False
        self.assigned = [
            item
            for item in self.assigned
            if effective(item.chord, host) != stored or item.when != when
        ]
        if _builtin(registry, stored, when, host) is not None:
            self.removed.append(Removal(stored, when))
        return True

    def resolve(
        self,
        registry: Registry,
        chord: str,
        *,
        active: Sequence[str] = ("global",),
        platform: str | None = None,
        text: bool = False,
    ) -> CommandSpec | None:
        """Return the command ``chord`` runs, or None.

        A non-global context in ``active`` beats ``global``. While ``text``
        is true, only a ``text`` binding runs.

        Args:
            registry: Known commands.
            chord: Pressed chord.
            active: Contexts that are currently active, most general first.
            platform: Host used to fold ``Ctrl``.
            text: True when a text field has focus.
        """
        host = platform or host_platform()
        stored = _stored(chord, host)
        if stored is None:
            return None
        rows = [row for row in self._bindings(registry, host) if row.chord == stored]
        if text:
            rows = [row for row in rows if row.when == "text"]
        else:
            rows = [
                row
                for row in rows
                if row.when != "text" and (row.when == "global" or row.when in active)
            ]
            specific = [row for row in rows if row.when != "global"]
            if specific:
                for context in reversed(tuple(active)):
                    match = next((row for row in specific if row.when == context), None)
                    if match is not None:
                        spec = registry.get(match.command_id)
                        if spec is not None and registry.enabled(spec):
                            return spec
                        return None
        for row in rows:
            spec = registry.get(row.command_id)
            if spec is not None and registry.enabled(spec):
                return spec
        return None

    def conflicts(self, registry: Registry, platform: str | None = None) -> tuple[Conflict, ...]:
        """Return chords claimed by more than one enabled command in one context.

        Args:
            registry: Known commands.
            platform: Host used to fold ``Ctrl``.
        """
        host = platform or host_platform()
        grouped: dict[tuple[str, str], list[str]] = {}
        for row in self._bindings(registry, host):
            spec = registry.get(row.command_id)
            if spec is None or not registry.enabled(spec):
                continue
            bucket = grouped.setdefault((row.when, row.chord), [])
            if row.command_id not in bucket:
                bucket.append(row.command_id)
        found = [
            Conflict(when, chord, tuple(ids))
            for (when, chord), ids in sorted(grouped.items())
            if len(ids) > 1
        ]
        return tuple(found)

    def chord_for(
        self, registry: Registry, command_id: str, platform: str | None = None
    ) -> str | None:
        """Return the chord that runs ``command_id``, after overrides.

        Args:
            registry: Known commands.
            command_id: Command id.
            platform: Host used to fold ``Ctrl``.
        """
        host = platform or host_platform()
        for row in self._bindings(registry, host):
            if row.command_id == command_id:
                return row.chord
        return None

    def document(self) -> dict[str, object]:
        """Return the schema 1 object stored in suite settings."""
        return {
            "schema": 1,
            "assigned": [
                {"chord": item.chord, "command": item.command_id, "when": item.when}
                for item in self.assigned[:MAX_ENTRIES]
            ],
            "removed": [
                {"chord": item.chord, "when": item.when} for item in self.removed[:MAX_ENTRIES]
            ],
        }

    def _bindings(self, registry: Registry, platform: str) -> list[_Binding]:
        rows: list[_Binding] = []
        for spec in registry:
            chord = _spec_chord(spec, platform)
            if chord is None or _is_removed(self.removed, chord, spec.when, platform):
                continue
            rows.append(_Binding(spec.command_id, chord, spec.when))
        for item in self.assigned:
            chord = effective(item.chord, platform)
            rows = [row for row in rows if row.chord != chord or row.when != item.when]
            if registry.get(item.command_id) is not None:
                rows.append(_Binding(item.command_id, chord, item.when))
        return rows

    def _owner(self, registry: Registry, chord: str, when: str, platform: str) -> str | None:
        for row in self._bindings(registry, platform):
            if row.chord == chord and row.when == when:
                return row.command_id
        return None


def normalize(chord: str, platform: str | None = None) -> str | None:
    """Return the canonical spelling of ``chord``, or None.

    Modifiers are ordered ``Mod+Ctrl+Alt+Shift``. A single letter is
    upper-cased. ``Cmd`` is accepted as ``Mod``. Empty text, unknown
    modifiers, and chords longer than 48 characters are None.

    Args:
        chord: User text such as ``mod+shift+k`` or ``Ctrl+PageDown``.
        platform: Used when folding is applied by :func:`effective`. Parsing
            itself keeps ``Ctrl`` distinct. The argument is accepted so
            callers can pass the host they already have.
    """
    del platform
    text = _expand_glyphs(chord.strip())
    if not text or len(text) > _MAX_KEY_LEN:
        return None
    if text.endswith("++"):
        mods, name = text[:-2], "+"
    elif "+" in text:
        mods, name = text.rsplit("+", 1)
    else:
        mods, name = "", text
    flags = {"Mod": False, "Ctrl": False, "Alt": False, "Shift": False}
    for token in mods.split("+"):
        piece = token.strip()
        if not piece:
            continue
        low = piece.lower()
        if low in {"mod", "cmd", "command", "super"}:
            flags["Mod"] = True
        elif low in {"ctrl", "control", "ctl"}:
            flags["Ctrl"] = True
        elif low in {"alt", "opt", "option"}:
            flags["Alt"] = True
        elif low == "shift":
            flags["Shift"] = True
        elif low in {"meta", "win", "windows"}:
            flags["Mod"] = True
        else:
            return None
    key = _canonical_name(name.strip())
    if key is None:
        return None
    parts = [name for name in _MOD_ORDER if flags[name]]
    parts.append(key)
    return "+".join(parts)


def effective(chord: str, platform: str) -> str:
    """Fold ``Ctrl`` into ``Mod`` on Windows and Linux.

    Args:
        chord: Canonical chord from :func:`normalize`.
        platform: ``linux``, ``win32``, or ``darwin``.
    """
    if platform == "darwin":
        return chord
    parts = chord.split("+")
    if "Ctrl" not in parts:
        return chord
    kept = [part for part in parts if part != "Ctrl"]
    if "Mod" not in kept:
        kept.insert(0, "Mod")
    return "+".join(kept)


def display_chord(chord: str, platform: str | None = None) -> str:
    """Return the chord as a menu label.

    ``Mod`` is ``Ctrl`` on Windows and Linux and ``Cmd`` on macOS.

    Args:
        chord: Canonical chord.
        platform: Host. Defaults to this machine.
    """
    host = platform or host_platform()
    shown = effective(chord, host)
    parts = shown.split("+")
    labels: list[str] = []
    for part in parts[:-1]:
        if part == "Mod":
            labels.append("Cmd" if host == "darwin" else "Ctrl")
        else:
            labels.append(part)
    labels.append(parts[-1])
    return "+".join(labels)


def host_platform() -> str:
    """Return ``darwin``, ``win32``, or ``linux``."""
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform == "win32":
        return "win32"
    return "linux"


def load_overrides() -> KeyMap:
    """Read keymap overrides from suite settings.

    A missing file, a broken value, and individual bad entries become an
    empty map or a skipped row. This does not raise.
    """
    return keymap_from_document(keymap_document())


def save_overrides(keymap: KeyMap) -> None:
    """Write ``keymap`` into suite settings.

    Args:
        keymap: Overrides to store. Entries are already canonical.
    """
    set_keymap(keymap.document())


def keymap_from_document(value: object) -> KeyMap:
    """Build a keymap from JSON, dropping entries that are not usable.

    Args:
        value: The ``keymap`` object from suite settings, or anything else.
    """
    keymap = KeyMap()
    if not isinstance(value, Mapping):
        return keymap
    assigned = value.get("assigned")
    removed = value.get("removed")
    if isinstance(assigned, list):
        for item in assigned[:MAX_ENTRIES]:
            parsed = _assignment(item)
            if parsed is not None:
                keymap.assigned.append(parsed)
    if isinstance(removed, list):
        for item in removed[:MAX_ENTRIES]:
            parsed_removal = _removal(item)
            if parsed_removal is not None:
                keymap.removed.append(parsed_removal)
    return keymap


def _stored(chord: str, platform: str) -> str | None:
    canon = normalize(chord)
    if canon is None:
        return None
    return effective(canon, platform)


def _spec_chord(spec: CommandSpec, platform: str) -> str | None:
    raw = dict(spec.platforms).get(platform, spec.chord)
    if raw is None:
        return None
    return _stored(raw, platform)


def _builtin(registry: Registry, chord: str, when: str, platform: str) -> str | None:
    for spec in registry:
        if spec.when == when and _spec_chord(spec, platform) == chord:
            return spec.command_id
    return None


def _is_removed(removed: Sequence[Removal], chord: str, when: str, platform: str) -> bool:
    return any(effective(item.chord, platform) == chord and item.when == when for item in removed)


def _assignment(value: object) -> Assignment | None:
    if not isinstance(value, Mapping):
        return None
    chord = value.get("chord")
    command = value.get("command")
    when = value.get("when", "global")
    if not isinstance(chord, str) or not isinstance(command, str) or not isinstance(when, str):
        return None
    stored = normalize(chord)
    if stored is None or not command or len(command) > _MAX_ID or not _when_ok(when):
        return None
    return Assignment(stored, command, when)


def _removal(value: object) -> Removal | None:
    if not isinstance(value, Mapping):
        return None
    chord = value.get("chord")
    when = value.get("when", "global")
    if not isinstance(chord, str) or not isinstance(when, str):
        return None
    stored = normalize(chord)
    if stored is None or not _when_ok(when):
        return None
    return Removal(stored, when)


def _when_ok(when: str) -> bool:
    if not when or len(when) > _MAX_WHEN:
        return False
    first, *rest = when
    if not first.isascii() or not first.isalpha():
        return False
    return all(char.isascii() and (char.isalnum() or char in "._-") for char in rest)


def _expand_glyphs(text: str) -> str:
    parts: list[str] = []
    for char in text:
        if char == "⌘":
            parts.append("Mod+")
        elif char == "⌃":
            parts.append("Ctrl+")
        elif char == "⌥":
            parts.append("Alt+")
        elif char == "⇧":
            parts.append("Shift+")
        else:
            parts.append(char)
    return "".join(parts)


def _canonical_name(name: str) -> str | None:
    if name == "+":
        return "+"
    if len(name) == 1:
        char = name
        if char.isspace() or unicodedata.category(char).startswith("C"):
            return None
        return char.upper() if char.isalpha() else char
    folded = name.replace(" ", "").replace("_", "")
    for named in _NAMED:
        if named.lower() == folded.lower():
            return named
    alias = _ALIAS.get(folded.lower())
    if alias is not None:
        return alias
    if folded[:1] in {"F", "f"} and folded[1:].isdigit():
        number = int(folded[1:])
        if 1 <= number <= 24:
            return f"F{number}"
    return None
