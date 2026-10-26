"""The model-downloader window.

One process owns the queue. A second launch focuses that window and the new
app's files appear in the same list. PySide6 is imported only when a window
opens, so a silent install can import this module without Qt.

:func:`run_download_window` is what an installer calls. It does not launch
the app. The Open button does, and only after a person presses it.
"""

from __future__ import annotations

import ctypes
import logging
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from voxprint_common.branding import (
    detach_console,
    identity_colour,
    install_identity,
    set_app_user_model_id,
)
from voxprint_common.i18n import tr
from voxprint_common.mdl.catalog import SUITE_APPS, discover, install_dir
from voxprint_common.mdl.control import app_outcome, shared_controller
from voxprint_common.mdl.instance import become_primary, send
from voxprint_common.mdl.names import DISPLAY_NAME
from voxprint_common.mdl.present import (
    GroupView,
    ItemState,
    ManageRow,
    ManageView,
    RowView,
    SmoothedProgress,
    Snapshot,
    WindowView,
    view_for,
)
from voxprint_common.mdl.progress_ui import run_verify_repair, verify_job_running
from voxprint_common.ui.splash import Motif
from voxprint_common.ui.splash import identity as splash_identity
from voxprint_common.ui.theme import (
    ACCENT,
    AMBER,
    GREEN,
    NOTE_BG,
    NOTE_BORDER,
    PRIMARY,
    RED,
    ROOT_PLAIN,
    TEXT_MUTED,
    apply_shell_palette,
    build_style,
)
from voxprint_common.ui.tokens import load

logger = logging.getLogger(__name__)

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
_MANAGE_W = 1280
_FONT = '"Noto Sans", "DejaVu Sans", "Segoe UI Variable Text", "Segoe UI", sans-serif'


def _no_window_flag() -> int:
    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))


def _hidden_popen(args: list[str]) -> None:
    """Start ``args`` without a console window on Windows."""
    subprocess.Popen(
        args,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_no_window_flag(),
    )


def _icon_file(app_id: str) -> str:
    """Return the installed ``.ico`` for ``app_id``, or an empty string."""
    for spec in SUITE_APPS:
        if spec.id != app_id:
            continue
        path = install_dir(spec) / "assets" / install_identity(spec.windows_name).icon_file
        if path.is_file():
            return str(path)
    return ""


def _taskbar_id(app_id: str) -> None:
    """Set the Windows taskbar identity before the first window opens."""
    for spec in SUITE_APPS:
        if spec.id == app_id:
            set_app_user_model_id(install_identity(spec.windows_name).app_user_model_id)
            return


def _scan_failure_view(focus: str, technical: str) -> WindowView:
    """Manage page shown when the background scan could not be built."""
    manage = ManageView(
        (),
        (),
        "",
        tr("mdl.manage.scan_failed"),
        tr("mdl.manage.empty_available"),
    )
    base = view_for(Snapshot(), mode="manage", focus_app=focus, manage=manage)
    return replace(
        base,
        show_error=True,
        show_retry=True,
        error_what_title=tr("mdl.error.what"),
        error_what=tr("mdl.manage.scan_failed"),
        error_do_title=tr("mdl.error.do"),
        error_do=tr("mdl.manage.scan_retry"),
        technical=technical,
        retry_label=tr("mdl.retry"),
    )


def _compose_manage_view(controller: object, app_id: str, manifest: Path) -> WindowView:
    """Build the manage page from stored size and mtime. This does not hash."""
    page = controller.manage_page(app_id, manifest)  # type: ignore[attr-defined]
    snap = controller.snapshot()  # type: ignore[attr-defined]
    return view_for(snap, mode="manage", focus_app=app_id, manage=page)


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
    _hidden_popen([sys.executable, "-m", "voxprint_common.mdl", "worker"])
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
    return _builder("shot")


def _load_qt() -> object:
    return _builder("run")


def _probe_manage() -> object:
    """Return a function that opens the manage window and waits for its scan."""
    return _builder("probe")


def _builder(kind: str) -> object:
    from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl
    from PySide6.QtGui import (
        QColor,
        QDesktopServices,
        QFont,
        QIcon,
        QLinearGradient,
        QPainter,
        QPainterPath,
        QPen,
        QPixmap,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMenu,
        QMessageBox,
        QPlainTextEdit,
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

    def _mix(colour: str, toward: float) -> QColor:
        """Shift ``colour`` toward white when ``toward`` is positive, else black."""
        base = QColor(colour)
        if toward >= 0:
            amount = min(1.0, toward)
            red = int(base.red() + (255 - base.red()) * amount)
            green = int(base.green() + (255 - base.green()) * amount)
            blue = int(base.blue() + (255 - base.blue()) * amount)
        else:
            amount = min(1.0, -toward)
            red = int(base.red() * (1.0 - amount))
            green = int(base.green() * (1.0 - amount))
            blue = int(base.blue() * (1.0 - amount))
        return QColor(red, green, blue)

    def _paint_cube(painter: QPainter, tile: QRectF, colour: str) -> None:
        """Isometric cube, the Model Forge glyph, about 60% of the tile."""
        cx = tile.center().x()
        cy = tile.center().y()
        half = tile.width() * 0.31
        top = (cx, cy - 0.55 * half)
        upper_left = (cx - half, cy - 0.22 * half)
        upper_right = (cx + half, cy - 0.22 * half)
        centre = (cx, cy + 0.05 * half)
        lower_left = (cx - half, cy + 0.28 * half)
        lower_right = (cx + half, cy + 0.28 * half)
        bottom = (cx, cy + 0.62 * half)
        faces = (
            (0.35, (top, upper_right, centre, upper_left)),
            (0.0, (upper_left, centre, bottom, lower_left)),
            (-0.42, (upper_right, lower_right, bottom, centre)),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        for toward, corners in faces:
            path = QPainterPath()
            start, *rest = corners
            path.moveTo(start[0], start[1])
            for point_x, point_y in rest:
                path.lineTo(point_x, point_y)
            path.closeSubpath()
            painter.setBrush(_mix(colour, toward))
            painter.drawPath(path)

    def _paint_vox_mark(painter: QPainter, tile: QRectF, colour: str) -> None:
        """VOXPrint V, used when an app has no icon file and no glyph."""
        pen = QPen(QColor(colour))
        pen.setWidthF(max(2.0, tile.width() * 0.15))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        margin = tile.width() * 0.26
        left = tile.left() + margin
        right = tile.right() - margin
        top = tile.top() + margin * 0.7
        bottom = tile.bottom() - margin * 0.5
        path = QPainterPath()
        path.moveTo(left, top)
        path.lineTo((left + right) / 2, bottom)
        path.lineTo(right, top)
        painter.drawPath(path)

    def paint_motif(painter: QPainter, rect: object, shape: str, colour: str) -> None:
        box = rect
        painter.setPen(Qt.PenStyle.NoPen)
        gradient = QLinearGradient(box.topLeft(), box.bottomLeft())
        gradient.setColorAt(0.0, QColor("#2c2c38"))
        gradient.setColorAt(1.0, QColor("#1b1b23"))
        painter.setBrush(gradient)
        radius = max(4.0, float(box.width()) * 0.18)
        painter.drawRoundedRect(box, radius, radius)
        if shape not in Motif._value2member_map_:
            _paint_vox_mark(painter, box, colour)
            return
        pen = QPen(QColor(colour))
        pen.setWidth(3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QColor(colour))
        left = int(box.left()) + 12
        top = int(box.top()) + 12
        width = max(8, int(box.width()) - 24)
        height = max(8, int(box.height()) - 24)
        motif = Motif(shape)
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
                _paint_cube(painter, box, colour)
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
            self._pixmap = QPixmap()
            self.setFixedSize(side, side)
            self.setAccessibleName(title)

        def set_mark(self, colour: str, shape: str, title: str, icon_path: str = "") -> None:
            """Repaint this tile. One widget stays in the header."""
            self._colour = colour
            self._shape = shape
            loaded = QPixmap(icon_path) if icon_path else QPixmap()
            self._pixmap = loaded if not loaded.isNull() else QPixmap()
            self.setAccessibleName(title)
            self.update()

        def paintEvent(self, event: object) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            inset = 1.0
            rect = QRectF(inset, inset, self.width() - 2 * inset, self.height() - 2 * inset)
            if not self._pixmap.isNull():
                clip = QPainterPath()
                radius = max(4.0, rect.width() * 0.18)
                clip.addRoundedRect(rect, radius, radius)
                painter.setClipPath(clip)
                painter.drawPixmap(rect, self._pixmap, QRectF(self._pixmap.rect()))
                painter.end()
                return
            painter.setClipRect(self.rect())
            paint_motif(painter, rect, self._shape, self._colour)
            painter.end()

    class SheenBar(QWidget):
        """Rounded progress with a blue-to-yellow fill and a moving sheen."""

        def __init__(self) -> None:
            super().__init__()
            self.setFixedHeight(8)
            self._value = 0.0
            self._phase = 0.0
            self._active = False

        def set_fraction(self, value: float, active: bool) -> None:
            """Set the filled fraction and whether the sheen should move."""
            self._value = min(1.0, max(0.0, value))
            self._active = active and self._value < 0.999
            self.update()

        def advance(self) -> None:
            """Move the sheen one step while the bar is active."""
            if not self._active:
                return
            self._phase = (self._phase + 0.035) % 1.0
            self.update()

        def paintEvent(self, event: object) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            track = QRectF(0, 0, float(self.width()), float(self.height()))
            painter.setBrush(QColor("#2a2a35"))
            painter.drawRoundedRect(track, 4, 4)
            filled = track.width() * self._value
            if filled > 0.5:
                fill = QRectF(0, 0, min(track.width(), max(8.0, filled)), track.height())
                gradient = QLinearGradient(fill.topLeft(), fill.topRight())
                gradient.setColorAt(0.0, QColor(ACCENT))
                gradient.setColorAt(1.0, QColor(PRIMARY))
                painter.setBrush(gradient)
                painter.drawRoundedRect(fill, 4, 4)
                if self._active and fill.width() > 16:
                    painter.setClipRect(fill)
                    center = fill.left() + self._phase * (fill.width() + 36) - 18
                    sheen = QLinearGradient(center - 16, 0, center + 16, 0)
                    sheen.setColorAt(0.0, QColor(255, 255, 255, 0))
                    sheen.setColorAt(0.5, QColor(255, 255, 255, 80))
                    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
                    painter.setBrush(sheen)
                    painter.drawRect(fill)
            painter.end()

    class WrapLabel(QLabel):
        """Word-wrapped text whose height is the lines that fit its width."""

        def __init__(self, text: str = "") -> None:
            super().__init__(text)
            self.setWordWrap(True)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        def hasHeightForWidth(self) -> bool:
            return True

        def heightForWidth(self, width: int) -> int:
            return self._height(width)

        def sizeHint(self) -> QSize:
            return QSize(self._width(), self._height(self._width()))

        def minimumSizeHint(self) -> QSize:
            return QSize(0, self._line())

        def resizeEvent(self, event: object) -> None:
            super().resizeEvent(event)
            self._fit_lines()

        def _fit_lines(self) -> None:
            """Lock the height to the wrapped lines once the width is real."""
            if self.width() <= 40:
                return
            needed = self._height(self.width())
            if self.maximumHeight() != needed:
                self.setFixedHeight(needed)

        def _width(self) -> int:
            if self.width() > 40:
                return self.width()
            limit = self.maximumWidth()
            if 0 < limit < 10_000:
                return limit
            return max(self.fontMetrics().horizontalAdvance(self.text()) + 2, self._line())

        def _height(self, width: int) -> int:
            if width <= 0:
                return self._line()
            return max(self._line(), QLabel.heightForWidth(self, width))

        def _line(self) -> int:
            return self.fontMetrics().height() + 2

    class EdgeFade(QWidget):
        """A short wash so a clipped row reads as more to scroll."""

        def __init__(self, parent: QWidget) -> None:
            super().__init__(parent)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        def paintEvent(self, event: object) -> None:
            del event
            painter = QPainter(self)
            gradient = QLinearGradient(0, 0, 0, self.height())
            colour = QColor(ROOT_PLAIN)
            clear = QColor(colour)
            clear.setAlpha(0)
            soft = QColor(colour)
            soft.setAlpha(90)
            solid = QColor(colour)
            solid.setAlpha(255)
            gradient.setColorAt(0.0, clear)
            gradient.setColorAt(0.4, soft)
            gradient.setColorAt(1.0, solid)
            painter.fillRect(self.rect(), gradient)
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
            self.resize(980, 640)
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
            self._scan_lock = threading.Lock()
            self._scan_running = False
            self._scan_again = False
            self._scan_generation = 0
            self._scan_failures = 0
            self._check_busy = False
            self._pending_view: tuple[int, WindowView] | None = None
            self._height_cap = 900
            self.setStyleSheet(_stylesheet(False))
            root = QVBoxLayout(self)
            root.setContentsMargins(24, 16, 24, 16)
            root.setSpacing(8)
            self._header = QHBoxLayout()
            self._header.setSpacing(16)
            self._badge = Badge("#60a5fa", "dot", 56, DISPLAY_NAME)
            titles = QVBoxLayout()
            titles.setSpacing(8)
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
            self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self._body = QWidget()
            self._body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            self._rows = QVBoxLayout(self._body)
            self._rows.setContentsMargins(4, 8, 12, 8)
            self._rows.setSpacing(8)
            self._scroll.setWidget(self._body)
            self._scroll.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            self._scroll.viewport().setAutoFillBackground(False)
            self._fade = EdgeFade(self._scroll.viewport())
            bar = self._scroll.verticalScrollBar()
            bar.valueChanged.connect(self._place_fade)
            bar.rangeChanged.connect(self._place_fade)
            root.addWidget(self._scroll, 0)
            self._overall_caption = QLabel("")
            self._overall_caption.setObjectName("sectiontitle")
            self._bar = SheenBar()
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
            self._open_folder = QPushButton("")
            self._open_folder.setObjectName("link")
            self._open_folder.setCursor(Qt.CursorShape.PointingHandCursor)
            self._open_folder.hide()
            self._details_button = QPushButton("")
            self._details_button.setObjectName("disclosure")
            self._details_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._technical = QPlainTextEdit()
            self._technical.setReadOnly(True)
            self._technical.setMaximumHeight(140)
            self._technical.hide()
            error_layout.addWidget(self._what_title)
            error_layout.addWidget(self._what)
            error_layout.addWidget(self._do_title)
            error_layout.addWidget(self._do)
            error_layout.addWidget(self._open_folder, 0, Qt.AlignmentFlag.AlignLeft)
            error_layout.addWidget(self._details_button, 0, Qt.AlignmentFlag.AlignLeft)
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
            self._footer = QLabel("")
            self._footer.setObjectName("footer")
            self._footer.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            root.addLayout(secondary)
            root.addWidget(self._primary)
            root.addWidget(self._footer)
            apply_shell_palette(self)
            apply_shell_palette(self._scroll.viewport())
            self._details_button.clicked.connect(self._toggle_details)
            self._open_folder.clicked.connect(self._on_open_folder)
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
            colour = identity_colour(view.focus_app) if view.focus_app else view.colour
            icon_path = _icon_file(view.focus_app)
            self._badge.set_mark(colour, view.motif, view.heading, icon_path)
            if icon_path:
                icon = QIcon(icon_path)
                if not icon.isNull():
                    self.setWindowIcon(icon)
            self._footer.setText(view.footer)
            self._footer.setVisible(view.mode != "manage")
            self._rule.setVisible(view.mode != "manage")
            self._rule.setStyleSheet(f"background: {colour}; border: none; border-radius: 2px;")
            if view.mode == "manage":
                self.setMinimumSize(_MIN_W, 280)
            self._overall_caption.setText(view.overall_caption)
            self._overall_detail.setText(view.overall_detail)
            self._status.setText(view.status)
            self._status.setVisible(
                bool(view.status) and not view.show_error and view.mode != "manage"
            )
            self._overall_caption.setVisible(view.mode != "manage")
            self._bar.setVisible(view.mode != "manage")
            self._overall_detail.setVisible(view.mode != "manage")
            self._error.setVisible(view.show_error)
            self._what_title.setText(view.error_what_title)
            self._what.setText(view.error_what)
            self._do_title.setText(view.error_do_title)
            self._do.setText(view.error_do)
            self._open_folder.setText(view.open_folder_label)
            self._open_folder.setVisible(bool(view.open_folder_label))
            self._technical.setPlainText(view.technical)
            self._details_button.setText(self._details_caption(view))
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
            view = self._view
            active = bool(
                view
                and view.mode != "manage"
                and not view.paused
                and not view.all_set
                and not view.show_error
                and view.overall < 1
            )
            self._bar.set_fraction(value, active)
            self._bar.advance()

        def resizeEvent(self, event: object) -> None:
            super().resizeEvent(event)
            self._fit_width()
            self._fit_scroll()

        def keyPressEvent(self, event: object) -> None:
            key = getattr(event, "key", lambda: None)()
            if key == Qt.Key.Key_Escape and self._cancel.isVisible():
                self._on_cancel()
                return
            super().keyPressEvent(event)

        def closeEvent(self, event: object) -> None:
            if self._check_busy:
                ignore = getattr(event, "ignore", None)
                if callable(ignore):
                    ignore()
                return
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
            viewport = self._scroll.viewport().width()
            if viewport < 80:
                return
            self._body.setMinimumWidth(0)
            self._body.setMaximumWidth(16_777_215)
            limit = max(160, viewport - 32)
            for label in self._body.findChildren(QLabel):
                label.setMaximumWidth(limit)
                fit = getattr(label, "_fit_lines", None)
                if callable(fit):
                    fit()
                label.updateGeometry()

        def _fit_scroll(self) -> None:
            needed = max(1, self._body.sizeHint().height())
            if self._mode == "manage":
                room = max(140, self._height_cap - 260)
            else:
                room = max(96, self.height() - 300)
            self._scroll.setFixedHeight(min(needed, room))
            self._place_fade()

        def _place_fade(self, *_args: object) -> None:
            if self._mode == "manage":
                self._fade.setVisible(False)
                return
            viewport = self._scroll.viewport()
            bar = self._scroll.verticalScrollBar()
            more = bar.maximum() > 0 and bar.value() < bar.maximum()
            band = 72
            self._fade.setGeometry(0, max(0, viewport.height() - band), viewport.width(), band)
            self._fade.setVisible(more)
            if more:
                self._fade.raise_()

        def _fit_to_content(self, limit: tuple[int, int]) -> None:
            """Size the window to its rows, and keep a tall list inside the cap."""
            manage = self._mode == "manage"
            width = _MANAGE_W if manage else limit[0]
            self._height_cap = limit[1]
            self.resize(width, limit[1])
            self._fit_width()
            self._fit_scroll()
            layout = self.layout()
            if layout is not None:
                layout.activate()
            hint = max(self.minimumHeight(), self.sizeHint().height())
            height = min(limit[1], hint)
            if not manage:
                height = max(_MIN_H, height)
            self.resize(width, height)
            self._fit_width()
            self._fit_scroll()

        def _details_caption(self, view: WindowView) -> str:
            label = view.details_hide_label if self._details else view.details_label
            mark = "▾ " if self._details else "▸ "
            return mark + label

        def _toggle_details(self) -> None:
            self._details = not self._details
            self._technical.setVisible(self._details)
            if self._view is not None:
                self._details_button.setText(self._details_caption(self._view))

        def _on_open_folder(self) -> None:
            view = self._view
            if view is None or not view.error_path:
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(view.error_path))

        def _on_manage(self) -> None:
            if self._controller is None:
                return
            self._mode = "queue" if self._mode == "manage" else "manage"
            if self._mode == "manage":
                self.apply(self._placeholder_manage())
                self._request_scan()
                return
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
            if view.mode == "manage" and view.show_retry:
                with self._scan_lock:
                    self._scan_failures = 0
                self._request_scan()
                return
            if view.show_retry and self._controller is not None:
                self._controller.retry()
                self._controller.start(display=True)
                self._refresh()

        def _refresh(self) -> None:
            if self._mode == "manage":
                self._apply_pending()
                return
            controller = self._controller
            if controller is None:
                return
            view = view_for(
                controller.snapshot(),
                mode=self._mode,
                focus_app=self._focus,
                manage=None,
            )
            self.apply(view)

        def _placeholder_manage(self) -> WindowView:
            """A manage page with no rows, shown before the scan finishes."""
            controller = self._controller
            snap = controller.snapshot() if controller is not None else Snapshot()
            name = splash_identity(self._focus).display_name if self._focus else ""
            empty = ManageView(
                (),
                (),
                "",
                tr("mdl.manage.empty_installed", app=name),
                tr("mdl.manage.empty_available"),
            )
            return view_for(snap, mode="manage", focus_app=self._focus, manage=empty)

        def _request_scan(self) -> None:
            """Scan install state on a worker. The UI thread only paints the result."""
            if self._mode != "manage" or self._controller is None or self._manifest is None:
                return
            with self._scan_lock:
                if self._scan_running:
                    self._scan_again = True
                    return
                self._scan_running = True
                self._scan_generation += 1
                token = self._scan_generation
            controller = self._controller
            focus = self._focus
            manifest = self._manifest

            def work() -> None:
                view = None
                technical = ""
                try:
                    view = _compose_manage_view(controller, focus, manifest)
                except Exception as exc:
                    logger.exception("manage scan failed")
                    technical = str(exc) or exc.__class__.__name__
                self._finish_scan(token, view, technical, focus)

            threading.Thread(target=work, name="mdl-manage-scan", daemon=True).start()

        def _finish_scan(
            self,
            token: int,
            view: WindowView | None,
            technical: str = "",
            focus: str = "",
        ) -> None:
            again = False
            with self._scan_lock:
                if view is None and technical:
                    if self._scan_failures < 1:
                        self._scan_failures += 1
                        self._scan_again = True
                    else:
                        self._scan_failures = 0
                        self._pending_view = (token, _scan_failure_view(focus, technical))
                else:
                    self._scan_failures = 0
                    if view is not None:
                        self._pending_view = (token, view)
                self._scan_running = False
                again = self._scan_again
                self._scan_again = False
            if again:
                self._request_scan()

        def _apply_pending(self) -> None:
            """Paint a scan that the worker already finished."""
            with self._scan_lock:
                pending = self._pending_view
                self._pending_view = None
            if pending is None:
                return
            token, view = pending
            if token != self._scan_generation:
                return
            self.apply(view)
            self._fit_to_content((_MANAGE_W, self._height_cap))

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
            if mode == "manage":
                self.apply(self._placeholder_manage())
                self._request_scan()
                return
            self._refresh()

    def _rebuild(window: DownloadWindow, view: WindowView) -> None:
        while window._rows.count():
            item = window._rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if view.mode == "manage" and view.manage is not None:
            disk = WrapLabel(view.manage.disk)
            disk.setObjectName("hint")
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
                window._rows.addWidget(_group_card(window, group), 0, Qt.AlignmentFlag.AlignTop)

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
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        outer = QHBoxLayout(card)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        stripe = QFrame()
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(
            f"background: {group.colour}; border: none; "
            "border-top-left-radius: 16px; border-bottom-left-radius: 16px;"
        )
        outer.addWidget(stripe)
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(Badge(group.colour, group.motif, 32, group.title))
        headline = WrapLabel(group.headline)
        headline.setObjectName("sectiontitle")
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
        outer.addLayout(layout, 1)
        return card

    def _file_row(row: RowView) -> QWidget:
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        mark = QWidget()
        mark.setFixedSize(12, 12)
        mark.setStyleSheet(f"background: {_dot(row.state)}; border-radius: 6px;")
        text = QVBoxLayout()
        title = WrapLabel(row.title)
        meta = WrapLabel(row.meta)
        meta.setObjectName("hint")
        text.addWidget(title)
        text.addWidget(meta)
        if row.note:
            note = WrapLabel(row.note)
            note.setObjectName("hint")
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
        title = WrapLabel(row.title)
        meta = WrapLabel(row.meta)
        meta.setObjectName("hint")
        text.addWidget(title)
        if row.note:
            note = WrapLabel(row.note)
            note.setObjectName("hint")
            text.addWidget(note)
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
            window._request_scan()
            return
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
        if window._manifest is None or window._check_busy or verify_job_running():
            return
        window._check_busy = True
        window.setEnabled(False)

        def done(_outcome: object) -> None:
            window._check_busy = False
            window.setEnabled(True)
            window._request_scan()

        try:
            run_verify_repair(
                None,
                repair=True,
                manifests=[window._manifest],
                show=True,
                on_done=done,
            )
        except Exception:
            logger.exception("check and repair failed to open")
            done(None)

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
        ring = load().density.focus_ring_px
        extra = f"""
        QPushButton:focus {{ border: {ring}px solid {ACCENT}; }}
        QPushButton#primary:focus {{ border: {ring}px solid #1c1604; }}
        QPushButton#disclosure {{
            background: transparent; border: none; padding: 4px 2px;
            font-weight: 400; font-size: 13px; text-align: left; color: {TEXT_MUTED};
        }}
        QPushButton#disclosure:hover {{ color: {ACCENT}; background: transparent; }}
        QPushButton#link {{
            background: transparent; border: none; padding: 2px 0;
            color: {ACCENT}; text-align: left; font-weight: 400;
        }}
        QPushButton#link:hover {{ color: #93c5fd; background: transparent; }}
        QFrame#card {{ border-radius: 16px; }}
        QFrame#card:hover {{ border: 1px solid #4c6a92; }}
        QFrame#errorCard {{
            background: {NOTE_BG}; border: 1px solid {NOTE_BORDER}; border-radius: 16px;
        }}
        QScrollArea, QScrollArea > QWidget > QWidget,
        QAbstractScrollArea, QAbstractScrollArea::viewport {{
            background: transparent; border: none;
        }}
        QScrollBar:vertical {{
            background: transparent; width: 8px; margin: 4px 0 4px 0;
        }}
        QScrollBar::handle:vertical {{
            background: #3d4f6f; min-height: 24px; border-radius: 4px;
        }}
        QScrollBar::handle:vertical:hover {{ background: #5b7aa8; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0; background: transparent; border: none;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QScrollBar:horizontal {{ height: 0; background: transparent; }}
        QLabel {{ background: transparent; }}
        """
        return style + extra

    def _font() -> QFont:
        font = QFont()
        font.setFamilies(["Noto Sans", "DejaVu Sans", "Segoe UI", "sans-serif"])
        font.setPointSize(10)
        return font

    def _prepare_app() -> QApplication:
        app = QApplication.instance() or QApplication([])
        app.setFont(_font())
        apply_shell_palette(app)
        return app

    def _shell(window: DownloadWindow) -> None:
        manage = window._mode == "manage"
        apply_shell_palette(window)
        apply_shell_palette(window._scroll.viewport())
        if manage:
            return
        if apply_backdrop(window):
            window.setStyleSheet(_stylesheet(True))
            if window._view is not None:
                window.apply(window._view)

    if kind == "shot":

        def build(view: WindowView, size: tuple[int, int]) -> tuple[QWidget, QApplication, object]:
            app = _prepare_app()
            window = DownloadWindow()
            window.resize(size[0], size[1])
            window.apply(view)
            for _ in range(8):
                window.tick()
            window.show()
            app.processEvents()
            window._fit_to_content(size)
            app.processEvents()
            _shell(window)
            app.processEvents()
            window._fit_width()
            window._fit_scroll()

            def tick() -> None:
                window.tick()
                app.processEvents()

            return window, app, tick

        return build

    def probe(
        controller: object,
        app_id: str,
        open_cmd: str,
        manifest: Path,
    ) -> tuple[DownloadWindow, QApplication]:
        """Open the manage window and wait until the worker scan is painted."""
        detach_console()
        _taskbar_id(app_id)
        app = _prepare_app()
        window = DownloadWindow()
        window.bind(controller, app_id, open_cmd, "manage", manifest)
        window.resize(_MANAGE_W, 720)
        window.show()
        _shell(window)
        app.processEvents()
        for _ in range(100):
            window._apply_pending()
            app.processEvents()
            view = window._view
            ready = view is not None and view.manage is not None and bool(view.manage.disk)
            if ready:
                break
            time.sleep(0.02)
        window._fit_to_content((_MANAGE_W, 900))
        app.processEvents()
        return window, app

    def run(
        primary: object,
        controller: object,
        app_id: str,
        open_cmd: str,
        mode: str,
        manifest: Path | None,
    ) -> WindowResult:
        detach_console()
        _taskbar_id(app_id)
        app = _prepare_app()
        window = DownloadWindow()
        window.bind(controller, app_id, open_cmd, mode, manifest)
        if mode == "manage":
            window.resize(_MANAGE_W, 720)
        window.show()
        app.processEvents()
        cap = 900 if mode == "manage" else 720
        width = _MANAGE_W if mode == "manage" else window.width()
        window._fit_to_content((width, cap))
        app.processEvents()
        _shell(window)
        timer = QTimer(window)
        polls = {"n": 0}

        def on_timer() -> None:
            window.tick()
            polls["n"] += 1
            if polls["n"] % 4 == 0:
                if window._mode == "manage":
                    window._apply_pending()
                else:
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

    if kind == "probe":
        return probe
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
    if window._mode == "manage":
        window._request_scan()
    else:
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
    _hidden_popen(args)


def _shlex_posix() -> bool:
    return sys.platform != "win32"
