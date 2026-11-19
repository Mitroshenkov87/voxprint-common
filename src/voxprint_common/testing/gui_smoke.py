"""Headless walk of a main window for app CI.

Import this module before the ``QApplication`` is created. That sets
``QT_QPA_PLATFORM`` to ``offscreen`` when it is unset, and on Windows sets
``QT_QPA_FONTDIR`` to the system font directory when that exists and the
variable is unset. Tests use pytest-qt. The walk clicks every enabled
visible button and triggers every enabled menu action. A control passes
when it emits a signal or opens a window. The rest are reported together.

This module does not load Qt until a walk or a crash check runs.
``configure_headless`` is the exception: importing the module calls it, and
it only touches environment variables.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DeadControl",
    "DeadControls",
    "assert_survives_worker_crash",
    "configure_headless",
    "exercise",
]


def configure_headless() -> None:
    """Select the offscreen Qt platform, and fonts on Windows.

    Existing values are left as they are. On Windows the font directory is
    ``%WINDIR%\\Fonts`` when that directory exists.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform != "win32" or os.environ.get("QT_QPA_FONTDIR"):
        return
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if fonts.is_dir():
        os.environ["QT_QPA_FONTDIR"] = str(fonts)


configure_headless()


@dataclass(frozen=True)
class DeadControl:
    """One button or menu action that did nothing."""

    kind: str
    label: str
    path: str


class DeadControls(AssertionError):
    """Raised when one or more controls do not emit or open a target.

    The message lists every dead control.
    """

    def __init__(self, controls: Sequence[DeadControl]) -> None:
        """Store ``controls`` and build the readable list.

        Args:
            controls: Buttons and menu actions that did nothing.
        """
        self.controls = tuple(controls)
        lines = ["Dead controls:"]
        for control in self.controls:
            lines.append(f"- {control.kind} {control.label!r} [{control.path}]")
        super().__init__("\n".join(lines))


def exercise(window: object, *, qtbot: object | None = None) -> int:
    """Click every enabled visible button and trigger every enabled menu action.

    A button with a menu counts as opening that menu and is not clicked, so
    a modal menu cannot block the walk. Its actions are triggered with the
    other menu actions. After each activation, a newly shown top-level
    window is closed.

    Args:
        window: Main window to walk.
        qtbot: pytest-qt bot. When it is set, a normal button is clicked
            with it. Otherwise the button's ``click`` method is used.

    Returns:
        How many controls were activated.

    Raises:
        DeadControls: At least one control neither emitted nor opened a target.
            The message names each of them.
    """
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])
    if not isinstance(window, QWidget):
        raise TypeError("window must be a QWidget")
    dead: list[DeadControl] = []
    activated = 0
    buttons = list(reversed(_buttons(window)))
    actions = list(reversed(_actions(window)))
    for button in buttons:
        if not _usable(button):
            continue
        menu = _menu(button)
        receivers = _button_signals(button)
        if menu is not None or receivers > 0:
            activated += 1
            if menu is None:
                _click(button, qtbot, app, window)
            _close_extra(app, window)
            continue
        before = _shown_windows(app)
        _click(button, qtbot, app, window)
        opened = _opened(app, before)
        _close_extra(app, window)
        if opened:
            activated += 1
            continue
        dead.append(DeadControl("button", _label(button), _path(button)))
    for action in actions:
        if not _usable(action):
            continue
        if _is_separator(action):
            continue
        receivers = _action_signals(action)
        submenu = _action_menu(action)
        if submenu is not None or receivers > 0:
            activated += 1
            if submenu is None:
                _trigger(action, app, window)
            _close_extra(app, window)
            continue
        before = _shown_windows(app)
        _trigger(action, app, window)
        if _opened(app, before):
            activated += 1
            _close_extra(app, window)
            continue
        dead.append(DeadControl("menu", _action_label(action), _action_path(action)))
    if dead:
        raise DeadControls(dead)
    return activated


def assert_survives_worker_crash(qtbot: object, window: object, host: object) -> None:
    """Kill the worker and require an InfoBar without quitting.

    The window must already show an InfoBar when the host reports a crash.
    :func:`voxprint_common.worker.qt.install_crash_bar` does that. The
    application must stay up, and ``aboutToQuit`` must not run.

    Args:
        qtbot: pytest-qt bot with ``waitUntil``.
        window: Main window that should stay visible.
        host: Running :class:`~voxprint_common.worker.WorkerHost`.

    Raises:
        AssertionError: No InfoBar appeared, the window closed, or the
            application started to quit.
    """
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance()
    if not isinstance(app, QApplication) or not isinstance(window, QWidget):
        raise AssertionError("A QApplication and a QWidget are required")
    if host.pid is None:  # type: ignore[attr-defined]
        raise AssertionError("The worker is not running")
    quitting: list[bool] = []

    def mark_quit() -> None:
        quitting.append(True)

    app.aboutToQuit.connect(mark_quit)
    host.kill_worker()  # type: ignore[attr-defined]

    def shown() -> bool:
        return _find_infobar(window) is not None

    qtbot.waitUntil(shown, timeout=5000)  # type: ignore[attr-defined]
    bar = _find_infobar(window)
    if bar is None or not bar.isVisible():
        raise AssertionError("The window did not show an InfoBar after the worker crash")
    if quitting:
        raise AssertionError("The application started to quit after the worker crash")
    if not window.isVisible():
        raise AssertionError("The window closed after the worker crash")
    if QApplication.instance() is not app:
        raise AssertionError("The QApplication was replaced after the worker crash")


def _buttons(window: object) -> list[object]:
    from PySide6.QtWidgets import QAbstractButton

    return list(window.findChildren(QAbstractButton))  # type: ignore[attr-defined]


def _actions(window: object) -> list[object]:
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMenu, QMenuBar

    found: list[object] = []
    seen: set[int] = set()
    holders = list(window.findChildren(QMenu)) + list(window.findChildren(QMenuBar))  # type: ignore[attr-defined]
    for button in _buttons(window):
        menu = _menu(button)
        if menu is not None:
            holders.append(menu)
    for holder in holders:
        for action in holder.actions():  # type: ignore[attr-defined]
            if not isinstance(action, QAction):
                continue
            identity = id(action)
            if identity in seen:
                continue
            seen.add(identity)
            found.append(action)
    return found


def _usable(widget: object) -> bool:
    try:
        enabled = bool(widget.isEnabled())  # type: ignore[attr-defined]
        visible = bool(widget.isVisible())  # type: ignore[attr-defined]
    except RuntimeError:
        return False
    return enabled and visible


def _menu(button: object) -> object | None:
    try:
        menu = button.menu()  # type: ignore[attr-defined]
    except AttributeError, RuntimeError:
        return None
    return menu


def _button_signals(button: object) -> int:
    return sum(
        _receivers(button, signature)
        for signature in ("2clicked()", "2pressed()", "2toggled()", "2released()")
    )


def _action_signals(action: object) -> int:
    triggered = _receivers(action, "2triggered()")
    if _menu_owned(action) and triggered:
        triggered -= 1
    return triggered + _receivers(action, "2toggled()")


def _receivers(obj: object, signature: str) -> int:
    try:
        return int(obj.receivers(signature))  # type: ignore[attr-defined]
    except RuntimeError, TypeError:
        return 0


def _menu_owned(action: object) -> bool:
    from PySide6.QtWidgets import QMenu

    try:
        objects = action.associatedObjects()  # type: ignore[attr-defined]
    except AttributeError, RuntimeError:
        return False
    return any(isinstance(obj, QMenu) for obj in objects)


def _action_menu(action: object) -> object | None:
    try:
        return action.menu()  # type: ignore[attr-defined]
    except AttributeError, RuntimeError:
        return None


def _is_separator(action: object) -> bool:
    try:
        return bool(action.isSeparator())  # type: ignore[attr-defined]
    except AttributeError, RuntimeError:
        return False


def _click(button: object, qtbot: object | None, app: object, window: object) -> None:
    from PySide6.QtCore import Qt, QTimer

    _arm_dismiss(QTimer, Qt, app, window)
    if qtbot is not None and _has_size(button):
        try:
            qtbot.mouseClick(button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
            app.processEvents()  # type: ignore[attr-defined]
            return
        except Exception:
            pass
    try:
        button.click()  # type: ignore[attr-defined]
    except RuntimeError:
        return
    app.processEvents()  # type: ignore[attr-defined]


def _trigger(action: object, app: object, window: object) -> None:
    from PySide6.QtCore import Qt, QTimer

    _arm_dismiss(QTimer, Qt, app, window)
    try:
        action.trigger()  # type: ignore[attr-defined]
    except RuntimeError:
        return
    app.processEvents()  # type: ignore[attr-defined]


def _arm_dismiss(timer_type: object, qt: object, app: object, window: object) -> None:
    def dismiss() -> None:
        _close_extra(app, window)

    timer_type.singleShot(0, dismiss)  # type: ignore[attr-defined]
    del qt


def _shown_windows(app: object) -> set[int]:
    shown: set[int] = set()
    for widget in app.topLevelWidgets():  # type: ignore[attr-defined]
        try:
            if widget.isVisible():
                shown.add(id(widget))
        except RuntimeError:
            continue
    return shown


def _opened(app: object, before: set[int]) -> bool:
    for widget in app.topLevelWidgets():  # type: ignore[attr-defined]
        try:
            if widget.isVisible() and id(widget) not in before and widget.isWindow():
                return True
        except RuntimeError:
            continue
    return False


def _close_extra(app: object, keep: object) -> None:
    from PySide6.QtWidgets import QDialog, QMenu

    keep_window = keep.window() if hasattr(keep, "window") else keep  # type: ignore[attr-defined]
    for widget in list(app.topLevelWidgets()):  # type: ignore[attr-defined]
        try:
            if not widget.isVisible() or widget is keep or widget is keep_window:
                continue
            if isinstance(widget, QDialog | QMenu) or widget.objectName() == "InfoBar":
                widget.close()
        except RuntimeError:
            continue


def _has_size(widget: object) -> bool:
    try:
        return int(widget.width()) > 0 and int(widget.height()) > 0  # type: ignore[attr-defined]
    except RuntimeError:
        return False


def _label(widget: object) -> str:
    try:
        text = str(widget.text())  # type: ignore[attr-defined]
    except AttributeError, RuntimeError:
        text = ""
    text = text.strip()
    if text:
        return text
    try:
        return str(widget.objectName())  # type: ignore[attr-defined]
    except RuntimeError:
        return ""


def _path(widget: object) -> str:
    parts: list[str] = []
    current: object | None = widget
    while current is not None:
        parts.append(_label(current) or type(current).__name__)
        try:
            current = current.parent()  # type: ignore[attr-defined]
        except AttributeError, RuntimeError:
            break
    return " / ".join(reversed(parts))


def _action_label(action: object) -> str:
    try:
        text = str(action.text())  # type: ignore[attr-defined]
    except RuntimeError:
        return ""
    return text.replace("&", "").strip()


def _action_path(action: object) -> str:
    try:
        parent = action.parent()  # type: ignore[attr-defined]
    except RuntimeError:
        parent = None
    if parent is None:
        return _action_label(action)
    return f"{_label(parent)} / {_action_label(action)}"


def _find_infobar(window: object) -> object | None:
    from PySide6.QtWidgets import QApplication, QWidget

    candidates: list[object] = []
    if isinstance(window, QWidget):
        candidates.append(window)
        candidates.extend(window.findChildren(QWidget))
    app = QApplication.instance()
    if isinstance(app, QApplication):
        candidates.extend(app.topLevelWidgets())
    for widget in candidates:
        if not isinstance(widget, QWidget):
            continue
        try:
            if not widget.isVisible():
                continue
            if widget.objectName() == "InfoBar" or type(widget).__name__ == "InfoBar":
                return widget
        except RuntimeError:
            continue
    return None
