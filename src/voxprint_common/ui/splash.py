"""Suite splash window.

The window is 960 by 540 logical pixels, borderless, centred, with 12 px
corners and a drop shadow. The left third, padded by 48 px, carries the app
name, the build label, and the stage. The progress bar sits at the bottom of
that column. It shimmers until the first call to :meth:`Splash.set_progress`.

Constructing :class:`Splash` shows the window. :meth:`Splash.finish` keeps it
up until it has been visible for 600 ms, then fades it out over 200 ms.

PySide6 is imported inside :class:`Splash`, not when this module is imported,
so a headless tool can import the layout helpers without Qt.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from voxprint_common.buildinfo import BuildInfo, label
from voxprint_common.i18n import tr

__all__ = [
    "BAR_PX",
    "FADE_MS",
    "HEIGHT",
    "MIN_VISIBLE_S",
    "NAME_PX",
    "PADDING",
    "RADIUS",
    "STAGE_PX",
    "VERSION_PX",
    "WIDTH",
    "AppIdentity",
    "Motif",
    "Splash",
    "SplashEvent",
    "SplashLayout",
    "display_name",
    "identity",
    "layout",
    "parse_colour",
]

WIDTH = 960
HEIGHT = 540
RADIUS = 12
PADDING = 48
NAME_PX = 40
VERSION_PX = 16
STAGE_PX = 14
BAR_PX = 4
MIN_VISIBLE_S = 0.6
FADE_MS = 200
_SHADOW = 28
_GAP = 8


class Motif(StrEnum):
    """Small mark painted beside an app name."""

    LENS = "lens"
    EXPAND = "expand"
    FRAME = "frame"
    PEN = "pen"
    BOOK = "book"
    WAVE = "wave"
    FORGE = "forge"
    DOT = "dot"


@dataclass(frozen=True)
class AppIdentity:
    """Splash title, identity colour, and motif for one suite id."""

    app_id: str
    display_name: str
    colour: str
    motif: Motif


_IDENTITIES = (
    AppIdentity("enlarge", "VOXPrint AI Enlarge", "#f5d76e", Motif.EXPAND),
    AppIdentity("clarity", "VOXPrint AI Clarity", "#7dd3fc", Motif.LENS),
    AppIdentity("canvas", "VOXPrint AI Canvas", "#c4b5fd", Motif.FRAME),
    AppIdentity("plotweaver", "VOXPrint AI Plotweaver", "#86efac", Motif.PEN),
    AppIdentity("audiobook-builder", "VOXPrint AI Audiobook Builder", "#fdba74", Motif.BOOK),
    AppIdentity("movie-dubber", "VOXPrint AI Movie Dubber", "#f9a8d4", Motif.WAVE),
    AppIdentity("model-forge", "VOXPrint AI Model Forge", "#fca5a5", Motif.FORGE),
    AppIdentity("lucent", "VOXPrint AI Lucent", "#fde68a", Motif.DOT),
)
_BY_ID = {item.app_id: item for item in _IDENTITIES}
_TITLE_KEYS = {
    "enlarge": "splash.app.enlarge",
    "clarity": "splash.app.clarity",
    "canvas": "splash.app.canvas",
    "plotweaver": "splash.app.plotweaver",
    "audiobook-builder": "splash.app.audiobook-builder",
    "movie-dubber": "splash.app.movie-dubber",
    "model-forge": "splash.app.model-forge",
    "lucent": "splash.app.lucent",
}
_ALIASES = {
    "audiobook": "audiobook-builder",
    "dubber": "movie-dubber",
    "forge": "model-forge",
}


@dataclass(frozen=True)
class SplashLayout:
    """Pixel boxes for the 960 by 540 splash."""

    width: int
    height: int
    radius: int
    padding: int
    left_third: int
    text_x: int
    text_width: int
    name_y: int
    version_y: int
    stage_y: int
    bar_x: int
    bar_y: int
    bar_width: int
    bar_height: int


def layout(width: int = WIDTH, height: int = HEIGHT) -> SplashLayout:
    """Return the splash boxes.

    The text column starts after the left padding. Its width is the left third
    minus that padding, so the column ends on the boundary of the third.

    Args:
        width: Logical window width.
        height: Logical window height.
    """
    left_third = width // 3
    text_x = PADDING
    text_width = left_third - PADDING
    name_y = PADDING
    version_y = name_y + NAME_PX + _GAP
    stage_y = version_y + VERSION_PX + _GAP
    return SplashLayout(
        width=width,
        height=height,
        radius=RADIUS,
        padding=PADDING,
        left_third=left_third,
        text_x=text_x,
        text_width=text_width,
        name_y=name_y,
        version_y=version_y,
        stage_y=stage_y,
        bar_x=text_x,
        bar_y=height - PADDING - BAR_PX,
        bar_width=text_width,
        bar_height=BAR_PX,
    )


def identity(app: str) -> AppIdentity:
    """Return the splash identity for a suite id.

    The title is ``splash.app.<id>`` from the UI catalogs. Brand spelling is
    the same in every language. A missing catalog line falls back to the
    English brand stored beside the motif.

    Args:
        app: ``BuildInfo.app`` or a suite id. A known id such as ``clarity``
            carries the VOXPrint display name, an identity colour, and a motif.
            Any other string keeps that text and a neutral mark.

    Returns:
        The identity used by the splash title and the download window.
    """
    key = app.strip().lower()
    found = _BY_ID.get(_ALIASES.get(key, key))
    if found is not None:
        title_key = _TITLE_KEYS[found.app_id]
        title = tr(title_key)
        if title == title_key:
            title = found.display_name
        return AppIdentity(found.app_id, title, found.colour, found.motif)
    text = app.strip() or tr("splash.app.fallback")
    if text == "splash.app.fallback":
        text = "VOXPrint"
    return AppIdentity(key or "app", text, "#60a5fa", Motif.DOT)


def display_name(app: str) -> str:
    """Return the splash title for a suite id or a name that is already written out.

    A known id is the brand string in the splash catalogs. The spelling is the
    same in every language.

    Args:
        app: ``BuildInfo.app``. A known id such as ``enlarge`` becomes
            ``VOXPrint AI Enlarge``. Any other string is used as written.
    """
    key = app.strip().lower()
    if key in _BY_ID or key in _ALIASES:
        return identity(app).display_name
    return app.strip()


def parse_colour(value: str) -> tuple[int, int, int]:
    """Parse an ``#RRGGBB`` identity colour.

    Args:
        value: Colour the app passes to the splash, for example ``#ff6b8a``.

    Returns:
        Red, green, and blue from 0 to 255.

    Raises:
        ValueError: The text is not ``#RRGGBB``.
    """
    text = value.strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) != 6 or any(character not in "0123456789abcdefABCDEF" for character in text):
        raise ValueError(f"identity colour must be #RRGGBB, got {value!r}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


@dataclass(frozen=True)
class SplashEvent:
    """A click on the splash, in coordinates inside the card.

    ``button`` is ``left``, ``right``, ``middle``, or ``other``. ``x`` and
    ``y`` are logical pixels from the card's top left, not from the shadow.
    """

    x: int
    y: int
    button: str


class Splash:
    """Borderless startup window for one VOXPrint app."""

    def __init__(
        self,
        app_art: str | Path,
        identity_colour: str,
        build_info: BuildInfo,
        *,
        on_click: Callable[[SplashEvent], None] | None = None,
    ) -> None:
        """Show the splash.

        Args:
            app_art: 16:9 artwork. A missing file paints a dark field instead.
            identity_colour: ``#RRGGBB`` fill for the progress bar.
            build_info: Name, version, candidate, build, and codename. The
                second line is :func:`voxprint_common.buildinfo.label`.
            on_click: Called for each click. Apps use this, or override
                :meth:`mouse_press`, to wire an easter egg. The private
                window is not part of the API.

        Raises:
            ValueError: ``identity_colour`` is not ``#RRGGBB``.
        """
        self.app_art = Path(app_art)
        self.identity_colour = parse_colour(identity_colour)
        self.build_info = build_info
        self.on_click = on_click
        self._stage = ""
        self._fraction: float | None = None
        self._shown = time.monotonic()
        self._window = _open_window(self)

    def set_stage(self, text: str) -> None:
        """Set the stage line under the build label.

        The splash does not translate this line. Pass text that already went
        through :func:`voxprint_common.i18n.tr`. The title is the app name from
        the splash catalogs, with the same brand spelling in every language.

        Args:
            text: Short status, already translated.
        """
        self._stage = text
        self._window.sync(self)

    def set_progress(self, fraction: float) -> None:
        """Set a determinate progress value and stop the shimmer.

        Args:
            fraction: From 0 to 1.

        Raises:
            ValueError: ``fraction`` is outside 0..1.
        """
        if not 0.0 <= float(fraction) <= 1.0:
            raise ValueError("fraction must be between 0 and 1")
        self._fraction = float(fraction)
        self._window.sync(self)

    def finish(self) -> None:
        """Wait until the splash has been up for 600 ms, then fade it out over 200 ms."""
        remaining = MIN_VISIBLE_S - (time.monotonic() - self._shown)
        if remaining > 0:
            _pump(remaining)
        self._window.fade()

    def mouse_press(self, event: SplashEvent) -> None:
        """Public event hook for a click on the splash.

        The window calls this on every press. The default calls
        :attr:`on_click` when it is set. Override this method to handle the
        click without using the private window.

        Args:
            event: Click position and button.
        """
        callback = self.on_click
        if callback is not None:
            callback(event)


def _open_window(splash: Splash) -> object:
    # PySide6 stays out of module import so headless tools can use layout().
    from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPixmap
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    boxes = layout()

    def _button_name(event: object) -> str:
        raw = event.button()  # type: ignore[attr-defined]
        if raw == Qt.MouseButton.LeftButton:
            return "left"
        if raw == Qt.MouseButton.RightButton:
            return "right"
        if raw == Qt.MouseButton.MiddleButton:
            return "middle"
        return "other"

    class _Window(QWidget):
        def __init__(self) -> None:
            super().__init__(
                None,
                Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool,
            )
            self._splash = splash
            self._phase = 0.0
            self._art = QPixmap(str(splash.app_art))
            self._anim: QPropertyAnimation | None = None
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setFixedSize(boxes.width + _SHADOW * 2, boxes.height + _SHADOW * 2)
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(30)
            self._place()
            self.show()
            app.processEvents()

        def sync(self, source: Splash) -> None:
            self._splash = source
            if source._fraction is not None:
                self._timer.stop()
            self.update()
            app.processEvents()

        def mousePressEvent(self, event: object) -> None:
            button = _button_name(event)
            point = event.position()  # type: ignore[attr-defined]
            self._splash.mouse_press(
                SplashEvent(int(point.x()) - _SHADOW, int(point.y()) - _SHADOW, button)
            )
            event.accept()  # type: ignore[attr-defined]

        def fade(self) -> None:
            self._timer.stop()
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(FADE_MS)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(self.close)
            self._anim = anim
            anim.start()
            deadline = time.monotonic() + (FADE_MS / 1000) + 0.5
            while self.isVisible() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)

        def _place(self) -> None:
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return
            area = screen.availableGeometry()
            frame = self.frameGeometry()
            frame.moveCenter(area.center())
            self.move(frame.topLeft())

        def _tick(self) -> None:
            if self._splash._fraction is not None:
                self._timer.stop()
            else:
                self._phase = (self._phase + 0.03) % 1.0
            self.update()

        def paintEvent(self, event: object) -> None:
            del event
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            card = QRectF(_SHADOW, _SHADOW, boxes.width, boxes.height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 90))
            painter.drawRoundedRect(card.adjusted(0, 8, 0, 10), boxes.radius, boxes.radius)
            path = QPainterPath()
            path.addRoundedRect(card, boxes.radius, boxes.radius)
            painter.setClipPath(path)
            self._paint_art(painter, card)
            self._paint_text(painter, card)
            self._paint_bar(painter, card)
            painter.end()

        def _paint_art(self, painter: QPainter, card: QRectF) -> None:
            if self._art.isNull():
                painter.fillRect(card, QColor("#0b1020"))
                return
            ratio = self.devicePixelRatioF()
            scaled = self._art.scaled(
                int(card.width() * ratio),
                int(card.height() * ratio),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(ratio)
            x = card.x() + (card.width() - scaled.width() / ratio) / 2
            y = card.y() + (card.height() - scaled.height() / ratio) / 2
            painter.drawPixmap(int(x), int(y), scaled)

        def _paint_text(self, painter: QPainter, card: QRectF) -> None:
            title = display_name(self._splash.build_info.app)
            version = label(self._splash.build_info)
            muted = QColor(242, 242, 245, 178)
            font = QFont()
            font.setFamilies(
                [
                    "Segoe UI Variable Text",
                    "Segoe UI",
                    "Ubuntu",
                    "Noto Sans",
                    "Cantarell",
                    "DejaVu Sans",
                ]
            )
            font.setPixelSize(NAME_PX)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor("#f2f2f5"))
            painter.drawText(
                QRectF(card.x() + boxes.text_x, card.y() + boxes.name_y, boxes.text_width, NAME_PX),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                title,
            )
            font.setPixelSize(VERSION_PX)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(muted)
            painter.drawText(
                QRectF(
                    card.x() + boxes.text_x,
                    card.y() + boxes.version_y,
                    boxes.text_width,
                    VERSION_PX,
                ),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                version,
            )
            font.setPixelSize(STAGE_PX)
            painter.setFont(font)
            painter.drawText(
                QRectF(
                    card.x() + boxes.text_x,
                    card.y() + boxes.stage_y,
                    boxes.text_width,
                    STAGE_PX,
                ),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._splash._stage,
            )

        def _paint_bar(self, painter: QPainter, card: QRectF) -> None:
            track = QRectF(
                card.x() + boxes.bar_x,
                card.y() + boxes.bar_y,
                boxes.bar_width,
                boxes.bar_height,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(242, 242, 245, 51))
            painter.drawRoundedRect(track, 2, 2)
            red, green, blue = self._splash.identity_colour
            fraction = self._splash._fraction
            if fraction is None:
                highlight = boxes.bar_width * 0.35
                start = track.x() + (track.width() + highlight) * self._phase - highlight
                painter.setBrush(QColor(red, green, blue))
                painter.drawRoundedRect(QRectF(start, track.y(), highlight, track.height()), 2, 2)
                return
            painter.setBrush(QColor(red, green, blue))
            painter.drawRoundedRect(
                QRectF(track.x(), track.y(), track.width() * fraction, track.height()),
                2,
                2,
            )

    window = _Window()
    window._app = app
    return window


def _pump(seconds: float) -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        time.sleep(0.01)
