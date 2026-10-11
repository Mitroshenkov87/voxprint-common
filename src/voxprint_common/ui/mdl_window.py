"""The model-downloader window.

One process owns the queue. A second launch focuses that window and the new
app's files appear in the same list. PySide6 is imported only when a window
opens, so a silent install can import this module without Qt.

:func:`run_download_window` is what an installer calls. It does not launch
the app. The Open button does, and only after a person presses it.
"""

from __future__ import annotations

import ctypes
import shlex
import subprocess
import sys
import time
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from voxprint_common.i18n import tr
from voxprint_common.mdl.catalog import discover
from voxprint_common.mdl.control import app_outcome, shared_controller
from voxprint_common.mdl.instance import become_primary, send
from voxprint_common.mdl.names import DISPLAY_NAME
from voxprint_common.mdl.present import (
    GroupView,
    ItemState,
    ManageRow,
    RowView,
    SmoothedProgress,
    WindowView,
    view_for,
)
from voxprint_common.ui.splash import Motif
from voxprint_common.ui.theme import (
    ACCENT,
    AMBER,
    GREEN,
    NOTE_BG,
    NOTE_BORDER,
    RED,
    TEXT_MUTED,
    build_style,
)

__all__ = [
    "WindowResult",
    "ensure_worker",
    "grab_view",
    "run_download_window",
    "run_manage_window",
    "serve",
]

_MIN_W = 720
_MIN_H = 480
_FONT = '"Noto Sans", "DejaVu Sans", "Segoe UI Variable Text", "Segoe UI", sans-serif'


class WindowResult(StrEnum):
    """How a download window finished for the app it was opened for."""

    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


def run_download_window(
    app_id: str,
    open_app_cmd: str | None = None,
    *,
    manifest: str | Path | None = None,
    manifests: list[str | Path] | None = None,
) -> WindowResult:
    """Show the download queue for ``app_id`` and return when that app settles.

    A second call focuses the window that is already open. Silent installers
    must not call this: they run ``voxprint-mdl download`` without ``--gui``.

    Args:
        app_id: Suite id, for example ``clarity``.
        open_app_cmd: Command for the Open button. It is not run automatically.
        manifest: ``mdl.json`` to enqueue. Discovered when omitted.
        manifests: Extra ``mdl.json`` files to add to the same queue.

    Returns:
        :attr:`WindowResult.READY` when that app's files are checked,
        :attr:`WindowResult.FAILED` when they are not, or
        :attr:`WindowResult.CANCELLED` when the person cancelled.
    """
    return _enter(app_id, open_app_cmd, manifest=manifest, manifests=manifests, mode="queue")


def serve() -> int:
    """Own the single queue until it is idle and no window is open.

    A second process talks to this one. Closing the window leaves the queue
    running while files are still transferring.

    Returns:
        ``0`` when this process held the queue, or when another process
        already does.
    """
    primary = become_primary()
    if primary is None:
        return 0
    controller = shared_controller()
    if controller.snapshot().items:
        controller.start(display=False)
    idle = 0
    try:
        while True:
            opened, saw = _consume(primary, controller)
            if opened is not None:
                app_id, open_cmd, mode, manifest = opened
                _run(primary, controller, app_id, open_cmd, mode, manifest)
                idle = 0
            elif saw or not _settled(controller, ItemState):
                idle = 0
            else:
                idle += 1
            if idle >= 40:
                break
            time.sleep(0.1)
    finally:
        controller.stop()
        primary.close()
    return 0


def ensure_worker() -> bool:
    """Start the queue process when one is not already listening.

    Returns:
        True when a process accepted a ping.
    """
    if send({"cmd": "ping"}):
        return True
    subprocess.Popen(
        [sys.executable, "-m", "voxprint_common.mdl", "worker"],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(200):
        if send({"cmd": "ping"}):
            return True
        time.sleep(0.05)
    return False


def run_manage_window(
    app_id: str,
    open_app_cmd: str | None = None,
    *,
    manifest: str | Path | None = None,
) -> WindowResult:
    """Open the manage-models page for one app.

    Args:
        app_id: Suite id.
        open_app_cmd: Command for the Open button.
        manifest: ``mdl.json`` for that app. Discovered when omitted.

    Returns:
        The same result as :func:`run_download_window`.
    """
    return _enter(app_id, open_app_cmd, manifest=manifest, manifests=None, mode="manage")


def grab_view(view: WindowView, path: Path, size: tuple[int, int] = (960, 720)) -> None:
    """Paint ``view`` and save a PNG. Used by the offscreen screenshot script.

    Args:
        view: A fully translated view.
        path: Destination PNG. Parent directories are created.
        size: Logical window size.
    """
    window, app, tick = _shot(view, size)
    for _ in range(48):
        tick()
        app.processEvents()
    path.parent.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(path))
    window.hide()
    window.deleteLater()
    app.processEvents()


def _enter(
    app_id: str,
    open_app_cmd: str | None,
    *,
    manifest: str | Path | None,
    manifests: list[str | Path] | None,
    mode: str,
) -> WindowResult:
    paths = [str(path) for path in manifests] if manifests else []
    if manifest and str(manifest) not in paths:
        paths.insert(0, str(manifest))
    message = {
        "cmd": "manage" if mode == "manage" else "show",
        "app": app_id,
        "open_cmd": open_app_cmd or "",
        "manifest": paths[0] if paths else "",
        "manifests": paths,
    }
    if not ensure_worker():
        return WindowResult.FAILED
    accepted = False
    for _ in range(20):
        if send(message):
            accepted = True
            break
        time.sleep(0.05)
    if not accepted:
        return WindowResult.FAILED
    return _wait_remote(app_id)


def _wait_remote(app_id: str) -> WindowResult:
    deadline = time.monotonic() + 24 * 60 * 60
    while time.monotonic() < deadline:
        outcome = app_outcome(app_id)
        if outcome == "ready":
            return WindowResult.READY
        if outcome == "failed":
            return WindowResult.FAILED
        if outcome == "cancelled":
            return WindowResult.CANCELLED
        time.sleep(0.4)
    return WindowResult.FAILED


def _run(
    primary: object,
    controller: object,
    app_id: str,
    open_cmd: str,
    mode: str,
    manifest: Path | None,
) -> WindowResult:
    qt = _load_qt()
    return qt(primary, controller, app_id, open_cmd, mode, manifest)


def _shot(view: WindowView, size: tuple[int, int]) -> tuple[object, object, object]:
    built = _load_shot()
    return built(view, size)


def _load_shot() -> object:
    return _builder(shot=True)


def _load_qt() -> object:
    return _builder(shot=False)


def _builder(shot: bool) -> object:
    from PySide6.QtCore import QRectF, Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMenu,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSystemTrayIcon,
        QVBoxLayout,
        QWidget,
    )

    def apply_backdrop(widget: QWidget) -> bool:
        if sys.platform != "win32":
            return False
        try:
            hwnd = int(widget.winId())
            dwm = ctypes.windll.dwmapi  # type: ignore[attr-defined]
            for backdrop in (3, 2):
                value = ctypes.c_int(backdrop)
                code = dwm.DwmSetWindowAttribute(
                    hwnd, 38, ctypes.byref(value), ctypes.sizeof(value)
                )
                if code == 0:
                    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
                    return True
        except AttributeError, OSError:
            return False
        return False

    def paint_motif(painter: QPainter, rect: object, shape: str, colour: str) -> None:
        box = rect
        painter.setPen(Qt.PenStyle.NoPen)
        wash = QColor(colour)
        wash.setAlpha(56)
        painter.setBrush(wash)
        painter.drawRoundedRect(box, 14, 14)
        pen = QPen(QColor(colour))
        pen.setWidth(3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QColor(colour))
        left = int(box.left()) + 12
        top = int(box.top()) + 12
        width = max(8, int(box.width()) - 24)
        height = max(8, int(box.height()) - 24)
        motif = Motif(shape) if shape in Motif._value2member_map_ else Motif.DOT
        match motif:
            case Motif.LENS:
                painter.drawEllipse(left, top, width, height)
            case Motif.EXPAND:
                painter.setPen(pen)
                mid_x = left + width // 2
                mid_y = top + height // 2
                painter.drawLine(left, mid_y, left + width, mid_y)
                painter.drawLine(mid_x, top, mid_x, top + height)
            case Motif.FRAME:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(left, top, width, height, 4, 4)
            case Motif.PEN:
                painter.drawLine(left, top + height, left + width, top)
            case Motif.BOOK:
                painter.drawRoundedRect(left, top, width // 2 - 2, height, 3, 3)
                painter.drawRoundedRect(left + width // 2 + 2, top, width // 2 - 2, height, 3, 3)
            case Motif.WAVE:
                bar = max(3, width // 5)
                for index, scale in enumerate((0.45, 1.0, 0.7)):
                    bar_h = int(height * scale)
                    painter.drawRoundedRect(
                        left + index * (bar + 4),
                        top + height - bar_h,
                        bar,
                        bar_h,
                        2,
                        2,
                    )
            case Motif.FORGE:
                painter.drawRoundedRect(left + 4, top + 4, width - 8, height - 8, 4, 4)
            case Motif.DOT:
                side = min(width, height) // 2
                painter.drawEllipse(left + width // 4, top + height // 4, side, side)
            case _ as unreachable:
                assert_never(unreachable)

    class Badge(QWidget):
        """Painted app motif. The accessible name is the app title."""

        def __init__(self, colour: str, shape: str, side: int, title: str) -> None:
            super().__init__()
            self._colour = colour
            self._shape = shape
            self.setFixedSize(side, side)
            self.setAccessibleName(title)

        def paintEvent(self, event: object) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint_motif(
                painter, QRectF(0, 0, self.width(), self.height()), self._shape, self._colour
            )
            painter.end()

    def chip_style(state: str) -> str:
        if state == "ready":
            colour, background = "#0b1f12", GREEN
        elif state == "failed":
            colour, background = RED, "#3a1d1d"
        elif state in {"paused", "verifying"}:
            colour, background = AMBER, NOTE_BG
        elif state == "downloading":
            colour, background = "#ffffff", ACCENT
        else:
            colour, background = TEXT_MUTED, "#2a2a35"
        return (
            f"color: {colour}; background: {background}; "
            "border-radius: 10px; padding: 3px 8px; font-size: 12px;"
        )

    class DownloadWindow(QWidget):
        """The single download and manage-models window."""

        def __init__(self) -> None:
            super().__init__()
            self.setObjectName("root")
            self.setWindowTitle(DISPLAY_NAME)
            self.setMinimumSize(_MIN_W, _MIN_H)
            self.resize(980, 720)
            self._view: WindowView | None = None
            self._smooth = SmoothedProgress()
            self._mode = "queue"
            self._result = WindowResult.FAILED
            self._quitting = False
            self._tray: QSystemTrayIcon | None = None
            self._controller: object | None = None
            self._focus = ""
            self._manifest: Path | None = None
            self._open_cmd = ""
            self._details = False
            self._sig = ""
            self.setStyleSheet(_stylesheet(False))
            root = QVBoxLayout(self)
            root.setContentsMargins(22, 18, 22, 18)
            root.setSpacing(12)
            self._header = QHBoxLayout()
            self._badge = Badge("#60a5fa", "dot", 64, DISPLAY_NAME)
            titles = QVBoxLayout()
            self._heading = QLabel("")
            self._heading.setObjectName("title")
            self._heading.setWordWrap(True)
            self._subtitle = QLabel("")
            self._subtitle.setObjectName("subtitle")
            self._subtitle.setWordWrap(True)
            titles.addWidget(self._heading)
            titles.addWidget(self._subtitle)
            self._header.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignTop)
            self._header.addLayout(titles, 1)
            root.addLayout(self._header)
            self._rule = QFrame()
            self._rule.setFixedHeight(3)
            root.addWidget(self._rule)
            self._scroll = QScrollArea()
            self._scroll.setWidgetResizable(True)
            self._scroll.setFrameShape(QFrame.Shape.NoFrame)
            self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._body = QWidget()
            self._rows = QVBoxLayout(self._body)
            self._rows.setContentsMargins(0, 0, 0, 0)
            self._rows.setSpacing(10)
            self._scroll.setWidget(self._body)
            root.addWidget(self._scroll, 1)
            self._overall_caption = QLabel("")
            self._overall_caption.setObjectName("sectiontitle")
            self._bar = QProgressBar()
            self._bar.setRange(0, 1000)
            self._bar.setTextVisible(False)
            self._bar.setFixedHeight(14)
            self._overall_detail = QLabel("")
            self._overall_detail.setObjectName("hint")
            self._overall_detail.setWordWrap(True)
            self._status = QLabel("")
            self._status.setObjectName("status")
            self._status.setWordWrap(True)
            root.addWidget(self._overall_caption)
            root.addWidget(self._bar)
            root.addWidget(self._overall_detail)
            root.addWidget(self._status)
            self._error = QFrame()
            self._error.setObjectName("errorCard")
            error_layout = QVBoxLayout(self._error)
            self._what_title = QLabel("")
            self._what_title.setObjectName("sectiontitle")
            self._what = QLabel("")
            self._what.setWordWrap(True)
            self._do_title = QLabel("")
            self._do_title.setObjectName("sectiontitle")
            self._do = QLabel("")
            self._do.setWordWrap(True)
            self._details_button = QPushButton("")
            self._details_button.setObjectName("disclosure")
            self._technical = QPlainTextEdit()
            self._technical.setReadOnly(True)
            self._technical.setMaximumHeight(140)
            self._technical.hide()
            error_layout.addWidget(self._what_title)
            error_layout.addWidget(self._what)
            error_layout.addWidget(self._do_title)
            error_layout.addWidget(self._do)
            error_layout.addWidget(self._details_button)
            error_layout.addWidget(self._technical)
            root.addWidget(self._error)
            self._background = QLabel("")
            self._background.setObjectName("warn")
            self._background.setWordWrap(True)
            self._background.hide()
            root.addWidget(self._background)
            secondary = QHBoxLayout()
            self._manage = QPushButton("")
            self._pause = QPushButton("")
            self._cancel = QPushButton("")
            self._close = QPushButton("")
            secondary.addWidget(self._manage)
            secondary.addStretch(1)
            secondary.addWidget(self._pause)
            secondary.addWidget(self._cancel)
            secondary.addWidget(self._close)
            self._primary = QPushButton("")
            self._primary.setObjectName("primary")
            self._primary.setMinimumHeight(42)
            root.addLayout(secondary)
            root.addWidget(self._primary)
            self._details_button.clicked.connect(self._toggle_details)
            self._manage.clicked.connect(self._on_manage)
            self._pause.clicked.connect(self._on_pause)
            self._cancel.clicked.connect(self._on_cancel)
            self._close.clicked.connect(self._on_close)
            self._primary.clicked.connect(self._on_primary)
            self._primary.setDefault(True)

        def apply(self, view: WindowView) -> None:
            """Paint ``view``. Repeated calls keep the scroll position."""
            self._view = view
            self._mode = view.mode
            self._smooth.set_target(view.overall)
            self._heading.setText(view.heading)
            self._subtitle.setText(view.subtitle)
            self._badge = _swap_badge(self, self._badge, view.colour, view.motif, view.heading)
            self._rule.setStyleSheet(
                f"background: {view.colour}; border: none; border-radius: 2px;"
            )
            self._overall_caption.setText(view.overall_caption)
            self._overall_detail.setText(view.overall_detail)
            self._status.setText(view.status)
            show_bar = view.mode != "manage" or view.overall < 1
            self._overall_caption.setVisible(view.mode != "manage")
            self._bar.setVisible(view.mode != "manage")
            self._overall_detail.setVisible(view.mode != "manage")
            del show_bar
            self._error.setVisible(view.show_error)
            self._what_title.setText(view.error_what_title)
            self._what.setText(view.error_what)
            self._do_title.setText(view.error_do_title)
            self._do.setText(view.error_do)
            self._technical.setPlainText(view.technical)
            self._details_button.setText(
                view.details_hide_label if self._details else view.details_label
            )
            self._background.setText(view.background)
            self._background.setVisible(bool(view.background))
            self._pause.setText(view.pause_label)
            self._pause.setVisible(view.show_pause or view.paused)
            self._cancel.setText(view.cancel_label)
            self._cancel.setVisible(view.show_cancel)
            self._close.setText(view.close_label)
            self._manage.setText(view.back_label if view.mode == "manage" else view.manage_label)
            if view.show_open and view.all_set:
                self._primary.setText(view.open_label)
                self._primary.setVisible(True)
            elif view.show_retry:
                self._primary.setText(view.retry_label)
                self._primary.setVisible(True)
            elif view.show_open:
                self._primary.setText(view.open_label)
                self._primary.setVisible(True)
            else:
                self._primary.setVisible(False)
            signature = _signature(view)
            if signature != self._sig:
                self._sig = signature
                _rebuild(self, view)
            self._fit_width()

        def tick(self) -> None:
            """Ease the overall bar one frame."""
            value = self._smooth.tick(0.05)
            self._bar.setValue(int(value * 1000))

        def resizeEvent(self, event: object) -> None:
            super().resizeEvent(event)
            self._fit_width()

        def keyPressEvent(self, event: object) -> None:
            key = getattr(event, "key", lambda: None)()
            if key == Qt.Key.Key_Escape and self._cancel.isVisible():
                self._on_cancel()
                return
            super().keyPressEvent(event)

        def closeEvent(self, event: object) -> None:
            view = self._view
            busy = bool(view and (view.show_pause or view.show_cancel) and not view.all_set)
            if busy and not self._quitting:
                self._background.setText(tr("mdl.subtitle.background"))
                self._background.show()
                if QSystemTrayIcon.isSystemTrayAvailable():
                    self._ensure_tray()
                    self.hide()
                else:
                    self._tell(tr("mdl.subtitle.background"))
                    self.hide()
                ignore = getattr(event, "ignore", None)
                if callable(ignore):
                    ignore()
                return
            accept = getattr(event, "accept", None)
            if callable(accept):
                accept()
            self._finish_from_view()

        def _fit_width(self) -> None:
            width = max(200, self._scroll.viewport().width() - 4)
            self._body.setMaximumWidth(width)
            for label in self._body.findChildren(QLabel):
                label.setMaximumWidth(max(120, width - 48))

        def _toggle_details(self) -> None:
            self._details = not self._details
            self._technical.setVisible(self._details)
            if self._view is not None:
                self._details_button.setText(
                    self._view.details_hide_label if self._details else self._view.details_label
                )

        def _on_manage(self) -> None:
            if self._controller is None:
                if self._view is not None and self._view.mode == "manage":
                    return
                return
            self._mode = "queue" if self._mode == "manage" else "manage"
            self._refresh()

        def _on_pause(self) -> None:
            controller = self._controller
            view = self._view
            if controller is None or view is None:
                return
            if view.paused or view.pause_label == tr("mdl.resume"):
                controller.resume()
            else:
                controller.pause()
            self._refresh()

        def _on_cancel(self) -> None:
            if self._view is None or self._controller is None:
                return
            if not _ask(
                self,
                tr("mdl.confirm.cancel.title"),
                tr("mdl.confirm.cancel.body"),
                tr("mdl.confirm.cancel.yes"),
                tr("mdl.confirm.cancel.no"),
            ):
                return
            self._controller.cancel()
            self._refresh()

        def _on_close(self) -> None:
            self._quitting = bool(self._view and (self._view.all_set or not self._view.show_pause))
            self.close()

        def _on_primary(self) -> None:
            view = self._view
            if view is None:
                return
            if view.show_open and (view.all_set or view.open_app):
                _launch_app(self._open_cmd or _command_for(self._controller, view.open_app))
                if view.all_set:
                    self._result = WindowResult.READY
                    self._quitting = True
                    self.close()
                return
            if view.show_retry and self._controller is not None:
                self._controller.retry()
                self._controller.start(display=True)
                self._refresh()

        def _refresh(self) -> None:
            controller = self._controller
            if controller is None:
                return
            manage = None
            if self._mode == "manage" and self._manifest is not None:
                manage = controller.manage_page(self._focus, self._manifest)
            view = view_for(
                controller.snapshot(),
                mode=self._mode,
                focus_app=self._focus,
                manage=manage,
            )
            self.apply(view)

        def _ensure_tray(self) -> None:
            if self._tray is not None:
                return
            pixmap = QPixmap(32, 32)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            colour = self._view.colour if self._view else ACCENT
            shape = self._view.motif if self._view else "dot"
            paint_motif(painter, QRectF(0, 0, 32, 32), shape, colour)
            painter.end()
            tray = QSystemTrayIcon(pixmap, self)
            tray.setToolTip(tr("mdl.tray.tooltip"))
            menu = QMenu()
            show = menu.addAction(tr("mdl.tray.show"))
            quit_action = menu.addAction(tr("mdl.tray.quit"))
            show.triggered.connect(self._show_again)
            quit_action.triggered.connect(self._quit_tray)
            tray.setContextMenu(menu)
            tray.activated.connect(lambda _reason: self._show_again())
            tray.show()
            self._tray = tray

        def _show_again(self) -> None:
            self.show()
            self.raise_()
            self.activateWindow()

        def _quit_tray(self) -> None:
            if not _ask(
                self,
                tr("mdl.confirm.quit.title"),
                tr("mdl.confirm.quit.body"),
                tr("mdl.confirm.quit.yes"),
                tr("mdl.confirm.quit.no"),
            ):
                return
            self._quitting = True
            if self._controller is not None:
                self._controller.cancel()
            self._result = WindowResult.CANCELLED
            self.close()

        def _tell(self, text: str) -> None:
            box = QMessageBox(self)
            box.setWindowTitle(DISPLAY_NAME)
            box.setText(text)
            box.setIcon(QMessageBox.Icon.Information)
            box.exec()

        def _finish_from_view(self) -> None:
            view = self._view
            if view is not None and view.all_set:
                self._result = WindowResult.READY
            elif view is not None and view.show_error:
                self._result = WindowResult.FAILED
            app = QApplication.instance()
            if app is not None and self._quitting:
                app.quit()

        def bind(
            self, controller: object, focus: str, open_cmd: str, mode: str, manifest: Path | None
        ) -> None:
            """Attach the live queue."""
            self._controller = controller
            self._focus = focus
            self._open_cmd = open_cmd
            self._mode = mode
            self._manifest = manifest
            self._refresh()

    def _swap_badge(
        window: DownloadWindow, old: Badge, colour: str, shape: str, title: str
    ) -> Badge:
        if old._colour == colour and old._shape == shape:
            return old
        badge = Badge(colour, shape, 64, title)
        window._header.replaceWidget(old, badge)
        old.deleteLater()
        return badge

    def _rebuild(window: DownloadWindow, view: WindowView) -> None:
        while window._rows.count():
            item = window._rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if view.mode == "manage" and view.manage is not None:
            disk = QLabel(view.manage.disk)
            disk.setObjectName("hint")
            disk.setWordWrap(True)
            window._rows.addWidget(disk)
            _section(
                window,
                tr("mdl.manage.installed"),
                view.manage.installed,
                view.manage.empty_installed,
            )
            _section(
                window,
                tr("mdl.manage.available"),
                view.manage.available,
                view.manage.empty_available,
            )
            verify = QPushButton(tr("mdl.manage.verify"))
            verify.clicked.connect(lambda: _verify(window))
            window._rows.addWidget(verify)
        else:
            if not view.groups:
                empty = QLabel(view.status)
                empty.setWordWrap(True)
                window._rows.addWidget(empty)
            for group in view.groups:
                window._rows.addWidget(_group_card(window, group))
        window._rows.addStretch(1)

    def _section(
        window: DownloadWindow, title: str, rows: tuple[ManageRow, ...], empty: str
    ) -> None:
        label = QLabel(title)
        label.setObjectName("sectiontitle")
        window._rows.addWidget(label)
        if not rows:
            note = QLabel(empty)
            note.setObjectName("hint")
            note.setWordWrap(True)
            window._rows.addWidget(note)
            return
        for row in rows:
            window._rows.addWidget(_manage_row(window, row))

    def _group_card(window: DownloadWindow, group: GroupView) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(Badge(group.colour, group.motif, 36, group.title))
        headline = QLabel(group.headline)
        headline.setObjectName("sectiontitle")
        headline.setWordWrap(True)
        head.addWidget(headline, 1)
        if group.open_label:
            button = QPushButton(group.open_label)
            button.setObjectName("primary")
            button.clicked.connect(
                lambda _checked=False, app=group.app_id: _open_group(window, app)
            )
            head.addWidget(button)
        layout.addLayout(head)
        for row in group.rows:
            layout.addWidget(_file_row(row))
        return card

    def _file_row(row: RowView) -> QWidget:
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 4, 0, 4)
        mark = QWidget()
        mark.setFixedSize(12, 12)
        mark.setStyleSheet(f"background: {_dot(row.state)}; border-radius: 6px;")
        text = QVBoxLayout()
        title = QLabel(row.title)
        title.setWordWrap(True)
        meta = QLabel(row.meta)
        meta.setObjectName("hint")
        meta.setWordWrap(True)
        text.addWidget(title)
        text.addWidget(meta)
        if row.note:
            note = QLabel(row.note)
            note.setObjectName("hint")
            note.setWordWrap(True)
            text.addWidget(note)
        chip = QLabel(row.state_label)
        chip.setStyleSheet(chip_style(row.state))
        chip.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text, 1)
        layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)
        host.setAccessibleName(f"{row.title}. {row.state_label}. {row.meta}")
        return host

    def _manage_row(window: DownloadWindow, row: ManageRow) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        text = QVBoxLayout()
        title = QLabel(row.title)
        title.setWordWrap(True)
        meta = QLabel(row.meta)
        meta.setObjectName("hint")
        meta.setWordWrap(True)
        text.addWidget(title)
        text.addWidget(meta)
        chip = QLabel(row.state_label)
        chip.setStyleSheet(chip_style(row.state))
        button = QPushButton(row.action_label)
        button.clicked.connect(
            lambda _checked=False, action=row.action, key=row.key: _manage_action(
                window, action, key
            )
        )
        layout.addLayout(text, 1)
        layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        card.setAccessibleName(f"{row.title}. {row.state_label}. {row.meta}")
        return card

    def _dot(state: str) -> str:
        if state == "ready":
            return GREEN
        if state == "failed":
            return RED
        if state in {"paused", "verifying"}:
            return AMBER
        if state == "downloading":
            return ACCENT
        return TEXT_MUTED

    def _open_group(window: DownloadWindow, app_id: str) -> None:
        _launch_app(_command_for(window._controller, app_id) or window._open_cmd)

    def _manage_action(window: DownloadWindow, action: str, key: str) -> None:
        controller = window._controller
        if controller is None or window._manifest is None:
            return
        if action == "remove":
            row_title = key
            if window._view and window._view.manage:
                for row in (*window._view.manage.installed, *window._view.manage.available):
                    if row.key == key:
                        row_title = row.title
            if not _ask(
                window,
                tr("mdl.confirm.remove.title"),
                tr(
                    "mdl.confirm.remove.body",
                    name=row_title,
                    app=window._heading.text() or window._focus,
                ),
                tr("mdl.confirm.remove.yes"),
                tr("mdl.confirm.remove.no"),
            ):
                return
            component = f"model-{key}" if not key.startswith("model-") else key
            listing = controller.listing(window._focus, window._manifest)
            for item in listing:
                if item.model_id == key:
                    component = item.component_id
            controller.remove_model(window._focus, component)
        elif action in {"add", "repair"}:
            controller.enqueue_app(
                window._focus,
                manifest=window._manifest,
                models=(key,),
                open_cmd=window._open_cmd,
            )
            controller.start(display=True)
            window._mode = "queue"
        window._refresh()

    def _verify(window: DownloadWindow) -> None:
        if window._controller is None or window._manifest is None:
            return
        window._controller.verify_app(window._focus, window._manifest)
        window._controller.start(display=True)
        window._refresh()

    def _signature(view: WindowView) -> str:
        parts = [view.mode, view.focus_app, str(view.show_error), str(view.all_set)]
        for group in view.groups:
            parts.append(group.app_id + ":" + ",".join(row.state + row.title for row in group.rows))
        if view.manage is not None:
            parts.append("i" + ",".join(row.key + row.state for row in view.manage.installed))
            parts.append("a" + ",".join(row.key + row.state for row in view.manage.available))
        return "|".join(parts)

    def _ask(parent: QWidget, title: str, body: str, yes: str, no: str) -> bool:
        box = QMessageBox(parent)
        box.setWindowTitle(title)
        box.setText(body)
        box.setIcon(QMessageBox.Icon.Question)
        accept = box.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(no, QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is accept

    def _stylesheet(glass: bool) -> str:
        style = build_style(glass)
        style = style.replace(
            '"Segoe UI Variable Text"',
            '"Noto Sans", "DejaVu Sans", "Segoe UI Variable Text"',
            1,
        )
        extra = f"""
        QPushButton:focus {{ border: 2px solid {ACCENT}; }}
        QPushButton#primary:focus {{ border: 2px solid #1c1604; }}
        QFrame#errorCard {{
            background: {NOTE_BG}; border: 1px solid {NOTE_BORDER}; border-radius: 12px;
        }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QLabel {{ background: transparent; }}
        """
        return style + extra

    def _font() -> QFont:
        font = QFont()
        font.setFamilies(["Noto Sans", "DejaVu Sans", "Segoe UI", "sans-serif"])
        font.setPointSize(10)
        return font

    if shot:

        def build(view: WindowView, size: tuple[int, int]) -> tuple[QWidget, QApplication, object]:
            app = QApplication.instance() or QApplication([])
            app.setFont(_font())
            window = DownloadWindow()
            window.resize(size[0], size[1])
            window.apply(view)
            for _ in range(8):
                window.tick()
            window.show()
            app.processEvents()
            if apply_backdrop(window):
                window.setStyleSheet(_stylesheet(True))
            app.processEvents()

            def tick() -> None:
                window.tick()
                app.processEvents()

            return window, app, tick

        return build

    def run(
        primary: object,
        controller: object,
        app_id: str,
        open_cmd: str,
        mode: str,
        manifest: Path | None,
    ) -> WindowResult:
        app = QApplication.instance() or QApplication([])
        app.setFont(_font())
        window = DownloadWindow()
        window.bind(controller, app_id, open_cmd, mode, manifest)
        window.show()
        app.processEvents()
        if apply_backdrop(window):
            window.setStyleSheet(_stylesheet(True))
            window.apply(window._view) if window._view else None
        timer = QTimer(window)
        polls = {"n": 0}

        def on_timer() -> None:
            window.tick()
            polls["n"] += 1
            if polls["n"] % 4 == 0:
                window._refresh()
            _drain(primary, window, controller)
            if not window.isVisible():
                snap = controller.snapshot()
                busy = any(
                    item.state.value in {"pending", "downloading", "verifying", "paused"}
                    for item in snap.items
                )
                if window._focus and snap.app_ready(window._focus):
                    window._result = WindowResult.READY
                if not busy and not snap.paused:
                    window._quitting = True
                    app.quit()

        timer.timeout.connect(on_timer)
        timer.start(50)
        app.exec()
        timer.stop()
        if window._tray is not None:
            window._tray.hide()
        return window._result

    return run


def _drain(primary: object, window: object, controller: object) -> None:
    opened, _saw = _consume(primary, controller)
    if opened is None:
        return
    app_id, open_cmd, mode, manifest = opened
    window._focus = app_id or window._focus
    window._mode = mode
    if open_cmd:
        window._open_cmd = open_cmd
    if manifest is not None:
        window._manifest = manifest
    window._show_again()
    window._refresh()


def _consume(
    primary: object, controller: object
) -> tuple[tuple[str, str, str, Path | None] | None, bool]:
    poll = getattr(primary, "poll", None)
    if not callable(poll):
        return None, False
    messages = poll()
    opened: tuple[str, str, str, Path | None] | None = None
    for message in messages:
        if not isinstance(message, dict):
            continue
        command = str(message.get("cmd") or "")
        if command in {"", "ping"}:
            continue
        app_id = str(message.get("app") or "")
        open_cmd = str(message.get("open_cmd") or "")
        paths = _message_paths(message)
        if command in {"show", "enqueue"} and (app_id or paths):
            if paths:
                controller.enqueue_manifests(paths, open_cmd=open_cmd)
            elif app_id:
                controller.enqueue_app(app_id, open_cmd=open_cmd)
            controller.start(display=command == "show")
        if command == "manage" and app_id:
            opened = (app_id, open_cmd, "manage", paths[0] if paths else _find_manifest(app_id))
        elif command == "show" and app_id:
            opened = (app_id, open_cmd, "queue", paths[0] if paths else _find_manifest(app_id))
    return opened, bool(messages)


def _message_paths(message: dict[str, object]) -> list[Path]:
    raw = message.get("manifests")
    paths = (
        [Path(item) for item in raw if isinstance(item, str) and item]
        if isinstance(raw, list)
        else []
    )
    single = message.get("manifest")
    if isinstance(single, str) and single:
        path = Path(single)
        if path not in paths:
            paths.insert(0, path)
    return paths


def _find_manifest(app_id: str) -> Path | None:
    for app in discover():
        if app.id == app_id:
            return app.path
    return None


def _settled(controller: object, item_state: object) -> bool:
    snap = controller.snapshot()
    pending = getattr(item_state, "PENDING", None)
    downloading = getattr(item_state, "DOWNLOADING", None)
    verifying = getattr(item_state, "VERIFYING", None)
    paused = getattr(item_state, "PAUSED", None)
    active = {pending, downloading, verifying, paused}
    return not any(item.state in active for item in snap.items) and not snap.paused


def _command_for(controller: object | None, app_id: str) -> str:
    if controller is None:
        return ""
    snap = controller.snapshot()
    for slot in snap.apps:
        if slot.app_id == app_id:
            return slot.open_cmd
    return ""


def _launch_app(command: str) -> None:
    text = command.strip()
    if not text:
        return
    args = shlex.split(text, posix=_shlex_posix())
    if not args:
        return
    subprocess.Popen(args, start_new_session=True)


def _shlex_posix() -> bool:
    return sys.platform != "win32"
