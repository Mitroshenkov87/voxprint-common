"""Suite-wide Expert mode.

Simple mode is the default. One bool in ``state/suite.json``, ``ui.expert_mode``,
turns expert commands, panels, and settings on. This module does not import Qt.
"""

from __future__ import annotations

from collections.abc import Callable

from voxprint_common.suite_settings import expert_mode, set_expert_mode

__all__ = ["ExpertMode", "active_contexts"]

Listener = Callable[[bool], None]


class ExpertMode:
    """The persisted Expert mode flag, with change notifications.

    The value is shared by every app through suite settings. A listener is
    called with the new value after a change is stored. Setting the same
    value again does not notify.
    """

    def __init__(self) -> None:
        """Read the current value from suite settings. No listener runs yet."""
        self._listeners: list[Listener] = []

    def enabled(self) -> bool:
        """Return the stored flag. Missing or invalid storage is false."""
        return expert_mode()

    def set_enabled(self, enabled: bool) -> bool:
        """Store ``enabled`` and notify listeners when the value changes.

        Args:
            enabled: True shows expert commands, panels, and settings.

        Returns:
            The stored value.

        Raises:
            ValueError: ``enabled`` is not a bool.
        """
        if not isinstance(enabled, bool):
            raise ValueError(f"expert mode must be true or false, got {enabled!r}")
        previous = self.enabled()
        stored = set_expert_mode(enabled)
        if stored != previous:
            for listener in tuple(self._listeners):
                listener(stored)
        return stored

    def toggle(self) -> bool:
        """Turn Expert mode on when it is off, and off when it is on.

        Returns:
            The stored value after the change.
        """
        return self.set_enabled(not self.enabled())

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener`` after a later change.

        Args:
            listener: Receives the new bool. It is not called for the current value.

        Returns:
            A function that removes ``listener``.
        """
        self._listeners.append(listener)

        def cancel() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return cancel


def active_contexts(*focused: str, expert: bool) -> tuple[str, ...]:
    """Return keymap contexts. ``expert`` is included only when the mode is on.

    ``global`` is first. Focused names are appended in order, without
    duplicates. The ``expert`` context is last when ``expert`` is true.

    Args:
        focused: Contexts that are active besides ``global``, such as ``canvas``.
        expert: True when Expert mode is on.
    """
    found = ["global"]
    for name in focused:
        if name and name not in found and name != "expert":
            found.append(name)
    if expert:
        found.append("expert")
    return tuple(found)
