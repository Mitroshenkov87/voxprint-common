"""Command registry shared by menus, the palette, and the keymap.

A command id is stable (``view.palette``, ``canvas.generate``). The title is
an i18n key, not a hard-coded sentence. The default chord uses ``Mod`` for
the primary modifier. ``platforms`` replaces that chord on one operating
system. ``when`` names the context that must be active. ``expert`` hides the
command until Expert mode is on. ``enabled_id`` is a predicate the app turns
on and off.

Qt is not imported.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from voxprint_common.i18n import tr

__all__ = [
    "CommandSpec",
    "Registry",
    "reference_commands",
    "title",
]

_MAX_ID = 128


@dataclass(frozen=True)
class CommandSpec:
    """One command.

    ``platforms`` is a tuple of ``(platform, chord)`` pairs. Platform names
    are ``linux``, ``win32``, and ``darwin``. A listed platform replaces
    ``chord`` on that system.
    """

    command_id: str
    title_key: str
    chord: str | None = None
    platforms: tuple[tuple[str, str], ...] = ()
    when: str = "global"
    enabled_id: str | None = None
    expert: bool = False


class Registry:
    """Commands registered by the shell and by one app."""

    def __init__(self, commands: Iterable[CommandSpec] = ()) -> None:
        self._commands: dict[str, CommandSpec] = {}
        self._enabled: dict[str, bool] = {}
        for spec in commands:
            self.add(spec)

    def add(self, spec: CommandSpec) -> None:
        """Register ``spec``.

        Args:
            spec: A command whose id is not already registered.

        Raises:
            ValueError: The id is empty, too long, or already present.
        """
        if not spec.command_id or len(spec.command_id) > _MAX_ID:
            raise ValueError("command id must be 1 to 128 characters")
        if spec.command_id in self._commands:
            raise ValueError(f"command {spec.command_id} is already registered")
        self._commands[spec.command_id] = spec

    def get(self, command_id: str) -> CommandSpec | None:
        """Return a command, or None when the id is unknown.

        Args:
            command_id: Stable command id.
        """
        return self._commands.get(command_id)

    def set_enabled(self, enabled_id: str, enabled: bool) -> None:
        """Set the predicate named by :attr:`CommandSpec.enabled_id`.

        Args:
            enabled_id: Predicate id.
            enabled: Whether commands using that predicate may run.
        """
        self._enabled[enabled_id] = enabled

    def enabled(self, spec: CommandSpec) -> bool:
        """Return whether ``spec`` may run.

        A command with no predicate is enabled. A predicate the app has not
        set is disabled.

        Args:
            spec: Registered command.
        """
        if spec.enabled_id is None:
            return True
        return self._enabled.get(spec.enabled_id, False)

    def visible(self, *, expert: bool) -> tuple[CommandSpec, ...]:
        """Return commands to show for this mode.

        Expert commands are left out when ``expert`` is false. Whether a
        command is enabled is a separate check.

        Args:
            expert: True when Expert mode is on.
        """
        return tuple(spec for spec in self._commands.values() if expert or not spec.expert)

    def __iter__(self) -> Iterator[CommandSpec]:
        return iter(self._commands.values())


def title(spec: CommandSpec) -> str:
    """Return the localised title of ``spec``.

    Args:
        spec: Command whose ``title_key`` is in the catalogs.
    """
    return tr(spec.title_key)


def reference_commands() -> tuple[CommandSpec, ...]:
    """Return the shared commands the reference shell registers.

    App commands are added beside these. Ids already used here stay shared:
    ``file.save``, ``edit.undo``, ``edit.redo``, ``view.palette``, and
    ``view.toggle_expert_mode``. The toggle has no default chord. The image
    screen does not register a command that opens the dock.
    """
    return (
        CommandSpec("file.save", "command.file.save", "Mod+S"),
        CommandSpec("file.open_folder", "simple.menu.open_folder"),
        CommandSpec("edit.undo", "command.edit.undo", "Mod+Z"),
        CommandSpec("edit.redo", "command.edit.redo", "Mod+Shift+Z"),
        CommandSpec("view.palette", "command.view.palette", "Mod+K"),
        CommandSpec("view.close_panel", "command.view.close_panel", "Mod+W"),
        CommandSpec("view.float_panel", "command.view.float_panel", "Mod+Shift+F"),
        CommandSpec("view.focus_canvas", "command.view.focus_canvas", "Mod+1"),
        CommandSpec("view.settings", "simple.menu.settings"),
        CommandSpec("view.toggle_expert_mode", "command.view.toggle_expert_mode"),
    )
