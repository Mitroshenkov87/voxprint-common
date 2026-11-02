"""Qt adapter for :class:`voxprint_common.worker.WorkerHost`.

PySide6 is imported when an adapter or an info bar is constructed, not when
this module is imported. The adapter polls the host from a ``QTimer``. It
does not call :meth:`~voxprint_common.worker.WorkerHost.wait`.
"""

from __future__ import annotations

from typing import assert_never

from voxprint_common.worker.host import WorkerHost
from voxprint_common.worker.messages import EventKind

__all__ = ["InfoBar", "QtWorkerHost", "install_crash_bar"]

_CACHE: dict[str, type] = {}


class QtWorkerHost:
    """Signals for progress, results, and crashes.

    ``progress``, ``finished``, ``failed``, and ``crashed`` are Qt signals.
    Each emits a :class:`~voxprint_common.worker.WorkerEvent`. A cancelled
    job is delivered on ``failed`` with kind ``cancelled``. Connect them
    after construction. The timer runs on the thread that constructs this
    object, which should be the UI thread.
    """

    def __init__(self, host: WorkerHost, *, interval_ms: int = 30) -> None:
        """Poll ``host`` every ``interval_ms`` milliseconds.

        Args:
            host: Qt-free worker host.
            interval_ms: Poll period. The poll itself does not block.

        Raises:
            ValueError: ``interval_ms`` is less than 1.
        """
        if interval_ms < 1:
            raise ValueError("interval_ms must be at least 1")
        self._host = host
        self._bridge = _bridge_type()(host, interval_ms)
        self.progress = self._bridge.progress
        self.finished = self._bridge.finished
        self.failed = self._bridge.failed
        self.crashed = self._bridge.crashed

    def shutdown(self, *, timeout: float = 2.0) -> None:
        """Stop the timer and shut the worker down.

        Args:
            timeout: Seconds :meth:`WorkerHost.shutdown` may block.
        """
        self._bridge.stop()
        self._host.shutdown(timeout=timeout)


class InfoBar:
    """A small bar shown on a window.

    The Qt widget's object name is ``InfoBar``. Apps that already have an
    info bar can show that instead. :func:`install_crash_bar` uses this one.
    """

    def __init__(self, parent: object, title: str, body: str) -> None:
        """Build a hidden bar.

        Args:
            parent: Widget that owns the bar.
            title: Already translated title.
            body: Already translated body.
        """
        self._widget = _bar_type()(parent, title, body)

    def present(self) -> None:
        """Show the bar above the parent."""
        self._widget.present()

    @property
    def widget(self) -> object:
        """Return the Qt widget."""
        return self._widget


def install_crash_bar(parent: object, source: QtWorkerHost, *, title: str, body: str) -> InfoBar:
    """Show an info bar when the worker crashes, and do not quit.

    Args:
        parent: Window or widget that should stay open.
        source: Adapter whose ``crashed`` signal is connected.
        title: Already translated title.
        body: Already translated body.

    Returns:
        The bar. It stays hidden until a crash.
    """
    bar = InfoBar(parent, title, body)

    def show(_event: object) -> None:
        bar.present()

    source.crashed.connect(show)
    return bar


def _bridge_type() -> type:
    cached = _CACHE.get("bridge")
    if cached is not None:
        return cached
    from PySide6.QtCore import QObject, QTimer, Signal

    class _Bridge(QObject):
        progress = Signal(object)
        finished = Signal(object)
        failed = Signal(object)
        crashed = Signal(object)

        def __init__(self, host: WorkerHost, interval_ms: int) -> None:
            super().__init__()
            self._host = host
            self._timer = QTimer(self)
            self._timer.setInterval(interval_ms)
            self._timer.timeout.connect(self._on_timeout)
            self._timer.start()

        def _on_timeout(self) -> None:
            for event in self._host.poll():
                _emit(self, event)

        def stop(self) -> None:
            self._timer.stop()

    _CACHE["bridge"] = _Bridge
    return _Bridge


def _emit(bridge: object, event: object) -> None:
    kind = event.kind  # type: ignore[attr-defined]
    if kind is EventKind.PROGRESS:
        bridge.progress.emit(event)  # type: ignore[attr-defined]
    elif kind is EventKind.FINISHED:
        bridge.finished.emit(event)  # type: ignore[attr-defined]
    elif kind is EventKind.FAILED or kind is EventKind.CANCELLED:
        bridge.failed.emit(event)  # type: ignore[attr-defined]
    elif kind is EventKind.CRASHED:
        bridge.crashed.emit(event)  # type: ignore[attr-defined]
    elif kind is EventKind.LOADED or kind is EventKind.UNLOADED:
        return
    else:
        assert_never(kind)


def _bar_type() -> type:
    cached = _CACHE.get("bar")
    if cached is not None:
        return cached
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

    class InfoBar(QFrame):
        """Visible crash notice. The class name is part of the smoke check."""

        def __init__(self, parent: object, title: str, body: str) -> None:
            super().__init__(parent)  # type: ignore[arg-type]
            self.setObjectName("InfoBar")
            self.setFrameShape(QFrame.Shape.StyledPanel)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 8, 12, 8)
            heading = QLabel(title)
            heading.setObjectName("InfoBarTitle")
            text = QLabel(body)
            text.setWordWrap(True)
            text.setObjectName("InfoBarBody")
            layout.addWidget(heading)
            layout.addWidget(text)
            self._title = heading
            self.hide()

        def present(self) -> None:
            parent = self.parentWidget()
            width = 420
            if parent is not None:
                width = max(280, parent.width() - 24)
            self.setGeometry(12, 12, width, self.sizeHint().height())
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self.show()
            self.raise_()

    _CACHE["bar"] = InfoBar
    return InfoBar
