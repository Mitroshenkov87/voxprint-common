"""Progress window for the model downloader.

The window uses the suite stylesheet from :mod:`voxprint_common.ui.theme`.
PySide6 is imported inside :func:`run_window` so this module can be imported
on a headless installer without Qt. Download labels that predate the catalogs
stay Russian. The check-and-repair window reads :mod:`voxprint_common.i18n`.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from voxprint_common.cli_protocol import EXIT_CANCELLED, EXIT_FAILED, EXIT_OK
from voxprint_common.gpu_probe import GpuDevice
from voxprint_common.i18n import get_language, tr_lang
from voxprint_common.mdl.job import Outcome, verify_repair, verify_summary
from voxprint_common.mdl.names import DISPLAY_NAME
from voxprint_common.mdl.present import format_size as localized_size
from voxprint_common.mdl.status import JobState, Status, Step

__all__ = [
    "CANCELLED_TEXT",
    "CANCEL_TEXT",
    "COLUMNS",
    "ERROR_PREFIX",
    "HEADING",
    "OVERALL",
    "READY_TEXT",
    "RETRY_TEXT",
    "WindowMode",
    "build_verify_window",
    "format_eta",
    "format_size",
    "format_speed",
    "render_verify_window",
    "run_verify_repair",
    "run_window",
    "window_title",
]

_VERIFY_WIDTH = 1280
_VERIFY_HEIGHT = 720
_COLUMN_PAD = 28
_ROW_HEIGHT = 36
_ROW_BAR_HEIGHT = 17
_CELL_PAD_LEFT = 8

RETRY_TEXT = "Повторить"
CANCEL_TEXT = "Отмена"
CANCELLED_TEXT = "Отменено"
READY_TEXT = "Готово"
ERROR_PREFIX = "Ошибка"
HEADING = "Загрузка моделей"
OVERALL = "Общий прогресс"
COLUMNS = ("Модель", "Размер", "Источник", "Скорость", "Осталось")

Starter = Callable[[threading.Event, Callable[[Status], None]], Outcome]


class WindowMode(StrEnum):
    """Which progress window :func:`run_window` opens."""

    DOWNLOAD = "download"
    VERIFY = "verify"


def window_title() -> str:
    """Return the window title, which is the product name."""
    return DISPLAY_NAME


def format_size(amount: int) -> str:
    """Return a short Russian size, for example ``1.5 МБ``.

    Args:
        amount: Size in bytes.
    """
    value = float(max(0, amount))
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            if unit == "Б":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"


def format_speed(bps: float) -> str:
    """Return a throughput label, or an em dash when nothing has been measured.

    Args:
        bps: Bytes per second.
    """
    if bps <= 0 or math.isnan(bps):
        return "—"
    return f"{format_size(int(bps))}/с"


def format_eta(seconds: float | None) -> str:
    """Return a Russian ETA, or an em dash when it is unknown.

    Args:
        seconds: Remaining seconds.
    """
    if seconds is None or seconds < 0 or math.isnan(seconds) or math.isinf(seconds):
        return "—"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин {secs} с"
    return f"{secs} с"


def run_window(start: Starter, *, mode: str = "download") -> int:
    """Show the progress window and run ``start`` until it finishes or is cancelled.

    PySide6 is imported in this function. A missing install raises ``ImportError``.

    Args:
        start: ``start(cancel, on_status) -> Outcome``. Called on a worker
            thread. ``on_status`` may be called from that thread.
        mode: ``download`` is the queue window. ``verify`` is the check-and-repair
            window. Buttons use ``button_radius_px`` from the density tokens.

    Returns:
        The process exit code. A download success closes the window. A verify
        run stays open on the summary until Close. Failure of a download leaves
        the window open until Повторить or the window is closed.
    """
    chosen = WindowMode(mode)
    match chosen:
        case WindowMode.DOWNLOAD:
            return _run_download_window(start)
        case WindowMode.VERIFY:
            return _run_verify_window(start)
        case _ as unreachable:
            assert_never(unreachable)


def run_verify_repair(
    model_ids: Sequence[str] | None = None,
    *,
    repair: bool = True,
    manifests: Sequence[Path] | None = None,
    show: bool = True,
    devices: Sequence[GpuDevice] | None = None,
) -> Outcome:
    """Check ``model_ids`` and, when ``repair`` is set, download the bad ones.

    This is the call Model Forge uses for Check and repair models. The window
    opens on the verify phase, shows each model as OK, missing, or corrupt,
    repairs only the missing and corrupt models, then shows a summary.

    Args:
        model_ids: Models to check. ``None`` checks the planned tier.
        repair: Download models that are missing or corrupt.
        manifests: ``mdl.json`` paths. Discovered when omitted.
        show: Open the window. False runs the job on this thread.
        devices: GPUs to plan for.

    Returns:
        The job outcome. With ``show`` false, the outcome is the job's own.
        With the window, it is the outcome the worker stored.
    """
    holder: dict[str, Outcome] = {}

    def start(cancel: threading.Event, on_status: Callable[[Status], None]) -> Outcome:
        outcome = verify_repair(
            model_ids,
            repair=repair,
            manifests=manifests,
            devices=devices,
            on_status=on_status,
            cancel=cancel,
        )
        holder["outcome"] = outcome
        return outcome

    if not show:
        return start(threading.Event(), lambda _status: None)
    code = run_window(start, mode=WindowMode.VERIFY.value)
    found = holder.get("outcome")
    if found is not None:
        return found
    state = JobState.READY if code == EXIT_OK else JobState.FAILED
    if code == EXIT_CANCELLED:
        state = JobState.CANCELLED
    return Outcome(code, "", Status(state))


def build_verify_window(
    status: Status,
    *,
    lang: str | None = None,
    size: tuple[int, int] = (_VERIFY_WIDTH, _VERIFY_HEIGHT),
) -> object:
    """Build the check-and-repair window for ``status`` without the event loop.

    Column widths are fixed from the header, the status words, and the rows,
    and the model column takes the space that is left. At 1280 px every
    header and cell fits. Buttons use the density token ``button_radius_px``.

    Args:
        status: Snapshot to paint. ``extra['phase']`` selects the subtitle.
        lang: Catalog language. The active language is used when omitted.
        size: Window size in pixels. The check is specified at 1280 px wide.

    Returns:
        The window widget. PySide6 is imported in this function.
    """
    code = lang or get_language()
    window, app = _verify_shell(status, lang=code, size=size)
    window.show()
    app.processEvents()
    _fill_verify(window, status, code)
    app.processEvents()
    return window


def render_verify_window(
    status: Status,
    path: Path,
    *,
    lang: str | None = None,
    size: tuple[int, int] = (_VERIFY_WIDTH, _VERIFY_HEIGHT),
) -> None:
    """Paint ``status`` and save a PNG. Used for the headless phase renders.

    Args:
        status: Snapshot to paint.
        path: Destination PNG. Parent directories are created.
        lang: Catalog language. The active language is used when omitted.
        size: Window size in pixels.
    """
    code = lang or get_language()
    window = build_verify_window(status, lang=code, size=size)
    path.parent.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(path))
    window.hide()
    window.deleteLater()


def _run_download_window(start: Starter) -> int:
    """Show the download window and run ``start`` until it finishes or is cancelled.

    PySide6 is imported in this function. A missing install raises ``ImportError``.

    Args:
        start: ``start(cancel, on_status) -> Outcome``. Called on a worker thread.

    Returns:
        The process exit code. Success closes the window. Failure leaves it
        open until Повторить or the window is closed.
    """
    # Qt stays out of the module import so headless tests can format the labels.
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QLabel,
        QProgressBar,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    from voxprint_common.ui.theme import RED, build_style

    class _Window(QWidget):
        def closeEvent(self, event: object) -> None:
            """Cancel a running job when the window is closed."""
            closer = getattr(self, "_closer", None)
            if callable(closer):
                closer()
            accept = getattr(event, "accept", None)
            if callable(accept):
                accept()

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    window = _Window()
    window.setObjectName("root")
    window.setWindowTitle(window_title())
    window.setStyleSheet(build_style(False))
    window.resize(760, 480)
    layout = QVBoxLayout(window)
    title = QLabel(window_title())
    title.setObjectName("title")
    heading = QLabel(HEADING)
    heading.setObjectName("subtitle")
    table = QTableWidget(0, len(COLUMNS))
    table.setHorizontalHeaderLabels(list(COLUMNS))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    overall_label = QLabel(OVERALL)
    overall = QProgressBar()
    overall.setRange(0, 1000)
    error = QLabel("")
    error.setWordWrap(True)
    error.setStyleSheet(f"color: {RED};")
    buttons = QHBoxLayout()
    retry = QPushButton(RETRY_TEXT)
    retry.setObjectName("primary")
    retry.setEnabled(False)
    cancel_button = QPushButton(CANCEL_TEXT)
    buttons.addStretch(1)
    buttons.addWidget(retry)
    buttons.addWidget(cancel_button)
    layout.addWidget(title)
    layout.addWidget(heading)
    layout.addWidget(table, stretch=1)
    layout.addWidget(overall_label)
    layout.addWidget(overall)
    layout.addWidget(error)
    layout.addLayout(buttons)

    state: dict[str, object] = {"status": None, "outcome": None, "cancel": None}
    lock = threading.Lock()
    closing = {"value": False}

    def paint(status: Status) -> None:
        table.setRowCount(len(status.steps))
        for row, step in enumerate(status.steps):
            values = (
                step.model,
                format_size(step.size),
                step.source or "—",
                format_speed(step.speed_bps),
                format_eta(step.eta_s),
            )
            for column, text in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(text))
        if status.bytes_total:
            overall.setRange(0, 1000)
            fraction = min(1.0, status.bytes_done / status.bytes_total)
            overall.setValue(int(fraction * 1000))
            overall_label.setText(f"{OVERALL}: {int(fraction * 100)}%")
        else:
            overall.setRange(0, 1)
            overall.setValue(1 if status.ready else 0)
            overall_label.setText(OVERALL)
        if status.state is JobState.READY:
            error.setText(READY_TEXT)
            error.setStyleSheet("")
        elif status.state is JobState.CANCELLED:
            error.setText(CANCELLED_TEXT)
        elif status.error:
            error.setText(f"{ERROR_PREFIX}: {status.error}")
            error.setStyleSheet(f"color: {RED};")
        else:
            error.setText("")

    def launch() -> None:
        event = threading.Event()
        with lock:
            state["cancel"] = event
            state["outcome"] = None

        def run() -> None:
            def on_status(status: Status) -> None:
                with lock:
                    state["status"] = _copy(status)

            try:
                outcome = start(event, on_status)
            except Exception as exc:
                outcome = Outcome(EXIT_FAILED, str(exc), Status(JobState.FAILED, error=str(exc)))
            with lock:
                state["status"] = _copy(outcome.status)
                state["outcome"] = outcome

        thread = threading.Thread(target=run, name="voxprint-mdl", daemon=True)
        thread.start()
        retry.setEnabled(False)

    def refresh() -> None:
        with lock:
            status = state.get("status")
            outcome = state.get("outcome")
        if isinstance(status, Status):
            paint(status)
        if not isinstance(outcome, Outcome) or closing["value"]:
            return
        if outcome.code == EXIT_OK:
            closing["value"] = True
            QTimer.singleShot(800, lambda: app.exit(EXIT_OK))
            return
        if outcome.code == EXIT_CANCELLED:
            closing["value"] = True
            QTimer.singleShot(600, lambda: app.exit(EXIT_CANCELLED))
            return
        retry.setEnabled(True)

    def on_retry() -> None:
        if closing["value"]:
            return
        retry.setEnabled(False)
        error.setText("")
        launch()

    def on_cancel() -> None:
        with lock:
            event = state.get("cancel")
        if isinstance(event, threading.Event):
            event.set()
        if closing["value"]:
            return
        closing["value"] = True
        QTimer.singleShot(400, lambda: app.exit(EXIT_CANCELLED))

    retry.clicked.connect(on_retry)
    cancel_button.clicked.connect(on_cancel)
    window._closer = on_cancel
    timer = QTimer(window)
    timer.timeout.connect(refresh)
    timer.start(100)
    launch()
    window.show()
    code = int(app.exec())
    timer.stop()
    return code


def _run_verify_window(start: Starter) -> int:
    """Show the check-and-repair window until the summary is closed."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    holder: dict[str, Status | None] = {"status": None}
    lock = threading.Lock()
    closing = {"value": False}
    shell: dict[str, object] = {}

    def paint(status: Status) -> None:
        window = shell.get("window")
        if window is None:
            return
        _fill_verify(window, status, str(shell["lang"]))

    def launch() -> None:
        event = threading.Event()
        shell["cancel"] = event

        def run() -> None:
            def on_status(status: Status) -> None:
                with lock:
                    holder["status"] = _copy(status)

            try:
                outcome = start(event, on_status)
            except Exception as exc:
                outcome = Outcome(EXIT_FAILED, str(exc), Status(JobState.FAILED, error=str(exc)))
            with lock:
                holder["status"] = _copy(outcome.status)
                shell["outcome"] = outcome

        thread = threading.Thread(target=run, name="voxprint-mdl-verify", daemon=True)
        thread.start()

    def refresh() -> None:
        with lock:
            status = holder.get("status")
        if isinstance(status, Status):
            paint(status)
        outcome = shell.get("outcome")
        if not isinstance(outcome, Outcome) or closing["value"]:
            return
        close = shell.get("close")
        cancel = shell.get("cancel_button")
        if callable(getattr(close, "setEnabled", None)):
            close.setEnabled(True)
        if callable(getattr(cancel, "setEnabled", None)):
            cancel.setEnabled(False)

    def on_close() -> None:
        if closing["value"]:
            return
        closing["value"] = True
        outcome = shell.get("outcome")
        code = outcome.code if isinstance(outcome, Outcome) else EXIT_CANCELLED
        app.exit(code)

    def on_cancel() -> None:
        event = shell.get("cancel")
        if isinstance(event, threading.Event):
            event.set()
        if closing["value"]:
            return
        closing["value"] = True
        QTimer.singleShot(400, lambda: app.exit(EXIT_CANCELLED))

    initial = Status(JobState.DOWNLOADING, extra={"phase": "verify"})
    window, _app = _verify_shell(initial, lang=get_language())
    shell["window"] = window
    shell["lang"] = get_language()
    shell["close_handler"] = on_close
    shell["cancel_handler"] = on_cancel
    _wire_verify(window, on_close, on_cancel, shell)
    timer = QTimer(window)
    timer.timeout.connect(refresh)
    timer.start(100)
    launch()
    window.show()
    code = int(app.exec())
    timer.stop()
    return code


def _verify_shell(status: Status, *, lang: str, size: tuple[int, int]) -> tuple[object, object]:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QProgressBar,
        QPushButton,
        QTableWidget,
        QVBoxLayout,
        QWidget,
    )

    from voxprint_common.ui.theme import build_style

    class _VerifyWindow(QWidget):
        def closeEvent(self, event: object) -> None:
            """Cancel a running check when the window is closed."""
            closer = getattr(self, "_closer", None)
            if callable(closer):
                closer()
            accept = getattr(event, "accept", None)
            if callable(accept):
                accept()

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    window = _VerifyWindow()
    window.setObjectName("root")
    window.setWindowTitle(window_title())
    window.setStyleSheet(build_style(False))
    window.setFixedSize(*size)
    layout = QVBoxLayout(window)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(8)
    title = QLabel(window_title())
    title.setObjectName("title")
    heading = QLabel(tr_lang("mdl.verify.heading", lang))
    heading.setObjectName("subtitle")
    phase = QLabel(_phase_label(_phase_of(status), lang))
    phase.setObjectName("phase")
    phase.setWordWrap(True)
    table = QTableWidget(0, 4)
    table.setObjectName("verifyTable")
    table.setHorizontalHeaderLabels(_column_labels(lang))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.setTextElideMode(Qt.TextElideMode.ElideNone)
    table.setWordWrap(False)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    table.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
    table.setStyleSheet(
        "QTableWidget::item {"
        f" padding-left: {_CELL_PAD_LEFT}px; padding-right: 6px;"
        "}"
        "QHeaderView::section {"
        f" padding-left: {_CELL_PAD_LEFT}px; padding-right: 6px;"
        "}"
    )
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    overall_label = QLabel(tr_lang("mdl.overall", lang))
    overall_label.setObjectName("overall")
    overall = QProgressBar()
    overall.setObjectName("overallBar")
    overall.setRange(0, 1000)
    overall.setTextVisible(True)
    summary = QLabel("")
    summary.setObjectName("summary")
    summary.setWordWrap(True)
    buttons = QHBoxLayout()
    cancel = QPushButton(tr_lang("mdl.cancel", lang))
    cancel.setObjectName("cancel")
    close = QPushButton(tr_lang("mdl.close", lang))
    close.setObjectName("close")
    close.setEnabled(False)
    buttons.addStretch(1)
    buttons.addWidget(cancel)
    buttons.addWidget(close)
    layout.addWidget(title)
    layout.addWidget(heading)
    layout.addWidget(phase)
    layout.addWidget(table, stretch=1)
    layout.addWidget(overall_label)
    layout.addWidget(overall)
    layout.addWidget(summary)
    layout.addLayout(buttons)
    window._verify_table = table
    window._verify_phase = phase
    window._verify_overall = overall
    window._verify_summary = summary
    window._verify_cancel = cancel
    window._verify_close = close
    _fill_verify(window, status, lang)
    return window, app


def _wire_verify(
    window: object,
    on_close: Callable[[], None],
    on_cancel: Callable[[], None],
    shell: dict[str, object],
) -> None:
    close = window._verify_close
    cancel = window._verify_cancel
    close.clicked.connect(on_close)
    cancel.clicked.connect(on_cancel)
    shell["close"] = close
    shell["cancel_button"] = cancel

    def closer() -> None:
        on_cancel()

    window._closer = closer


def _fill_verify(window: object, status: Status, lang: str) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QTableWidgetItem

    from voxprint_common.ui.theme import AMBER, GREEN, RED

    phase = window._verify_phase
    phase.setText(_phase_label(_phase_of(status), lang))
    table = window._verify_table
    _drop_stray_bars(table)
    if table.rowCount() != len(status.steps):
        table.setRowCount(len(status.steps))
    done = 0
    total = 0
    for row, step in enumerate(status.steps):
        done += min(step.received, step.size)
        total += step.size
        name = step.display_name or step.model
        values = (
            name,
            localized_size(step.size, lang=lang),
            "",
            _status_label(step.state, lang),
        )
        for column, text in enumerate(values):
            if column == 2:
                continue
            item = table.item(row, column)
            if item is None:
                item = QTableWidgetItem(text)
                table.setItem(row, column, item)
            else:
                item.setText(text)
            if column == 1:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            else:
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            if column == 3:
                color = _status_color(step.state, GREEN, AMBER, RED)
                if color:
                    item.setForeground(QColor(color))
        fraction = 0.0 if step.size <= 0 else min(1.0, step.received / step.size)
        bar = _row_bar(table, row)
        bar.setValue(int(fraction * 1000))
    _size_verify_columns(table, lang)
    overall = window._verify_overall
    if total:
        overall.setRange(0, 1000)
        overall.setValue(int(min(1.0, done / total) * 1000))
    else:
        overall.setRange(0, 1)
        overall.setValue(0)
    counts = _count_states(status)
    window._verify_summary.setText(verify_summary(counts, lang))
    done_phase = _phase_of(status) == "summary"
    window._verify_close.setEnabled(done_phase)
    window._verify_cancel.setEnabled(not done_phase)
    _style_verify_buttons(window._verify_cancel, window._verify_close, done=done_phase)


def _drop_stray_bars(table: object) -> None:
    """Hide progress bars that a refill left behind in the model column."""
    from PySide6.QtWidgets import QProgressBar

    live: set[int] = set()
    for row in range(table.rowCount()):
        for column in range(table.columnCount()):
            widget = table.cellWidget(row, column)
            if widget is not None:
                live.add(id(widget))
    viewport = table.viewport()
    for child in list(viewport.children()):
        if id(child) in live:
            continue
        bars = child.findChildren(QProgressBar) if hasattr(child, "findChildren") else []
        if isinstance(child, QProgressBar) or bars:
            child.hide()
            child.setParent(None)
            child.deleteLater()


def _row_bar(table: object, row: int) -> object:
    """Return the progress bar for ``row``, creating a centred one of fixed height."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QWidget

    host = table.cellWidget(row, 2)
    bar = host.findChild(QProgressBar) if host is not None else None
    if bar is not None:
        return bar
    host = QWidget()
    host.setStyleSheet("background: transparent;")
    layout = QHBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    bar = QProgressBar()
    bar.setObjectName("rowProgress")
    bar.setRange(0, 1000)
    bar.setFixedHeight(_ROW_BAR_HEIGHT)
    bar.setFormat("%p%")
    bar.setTextVisible(True)
    layout.addWidget(bar, 0, Qt.AlignmentFlag.AlignVCenter)
    table.setCellWidget(row, 2, host)
    return bar


def _style_verify_buttons(cancel: object, close: object, *, done: bool) -> None:
    """Keep Cancel and Close the same size.

    Close uses the secondary style while the check is still running, and the
    primary style only after the summary is ready.
    """
    width = 0
    height = 0
    for name in ("close", "primary"):
        close.setObjectName(name)
        close.style().unpolish(close)
        close.style().polish(close)
        hint = close.sizeHint()
        width = max(width, hint.width())
        height = max(height, hint.height())
    cancel.style().unpolish(cancel)
    cancel.style().polish(cancel)
    hint = cancel.sizeHint()
    width = max(width, hint.width())
    height = max(height, hint.height())
    close.setObjectName("primary" if done else "close")
    close.style().unpolish(close)
    close.style().polish(close)
    cancel.setFixedSize(width, height)
    close.setFixedSize(width, height)


def _size_verify_columns(table: object, lang: str) -> None:
    from PySide6.QtCore import Qt

    labels = _column_labels(lang)
    metrics = table.fontMetrics()
    widths = [metrics.horizontalAdvance(label) + _COLUMN_PAD for label in labels]
    for state in (
        "pending",
        "checking",
        "ok",
        "missing",
        "corrupt",
        "repairing",
        "repaired",
        "failed",
    ):
        label = _status_label(state, lang)
        widths[3] = max(widths[3], metrics.horizontalAdvance(label) + _COLUMN_PAD)
    wide_size = localized_size(999 * 1024**3, lang=lang)
    widths[1] = max(widths[1], metrics.horizontalAdvance(wide_size) + _COLUMN_PAD)
    widths[2] = max(widths[2], metrics.horizontalAdvance("100%") + _COLUMN_PAD, 120)
    for row in range(table.rowCount()):
        for column in (0, 1, 3):
            item = table.item(row, column)
            if item is None:
                continue
            widths[column] = max(
                widths[column], metrics.horizontalAdvance(item.text()) + _COLUMN_PAD
            )
    viewport = table.viewport().width()
    if viewport < 400:
        viewport = _VERIFY_WIDTH - 48
    spare = viewport - sum(widths)
    if spare > 0:
        widths[0] += spare
    elif spare < 0:
        widths[0] = max(48, widths[0] + spare)
    header = table.horizontalHeader()
    left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    for index, width in enumerate(widths):
        header.resizeSection(index, width)
        item = table.horizontalHeaderItem(index)
        if item is not None:
            item.setTextAlignment(right if index == 1 else left)


def _column_labels(lang: str) -> list[str]:
    return [
        tr_lang("mdl.verify.column.model", lang),
        tr_lang("mdl.verify.column.size", lang),
        tr_lang("mdl.verify.column.progress", lang),
        tr_lang("mdl.verify.column.status", lang),
    ]


def _phase_of(status: Status) -> str:
    phase = status.extra.get("phase")
    if phase in {"verify", "repair", "summary"}:
        return str(phase)
    return "verify"


def _phase_label(phase: str, lang: str) -> str:
    match phase:
        case "verify":
            return tr_lang("mdl.verify.phase.verify", lang)
        case "repair":
            return tr_lang("mdl.verify.phase.repair", lang)
        case "summary":
            return tr_lang("mdl.verify.phase.summary", lang)
        case _:
            return tr_lang("mdl.verify.phase.verify", lang)


def _status_label(state: str, lang: str) -> str:
    match state:
        case "pending":
            return tr_lang("mdl.verify.status.pending", lang)
        case "checking":
            return tr_lang("mdl.verify.status.checking", lang)
        case "ok" | "verified":
            return tr_lang("mdl.verify.status.ok", lang)
        case "missing":
            return tr_lang("mdl.verify.status.missing", lang)
        case "corrupt":
            return tr_lang("mdl.verify.status.corrupt", lang)
        case "repairing":
            return tr_lang("mdl.verify.status.repairing", lang)
        case "repaired":
            return tr_lang("mdl.verify.status.repaired", lang)
        case "failed":
            return tr_lang("mdl.verify.status.failed", lang)
        case _:
            return tr_lang("mdl.verify.status.pending", lang)


def _status_color(state: str, ok: str, warn: str, bad: str) -> str:
    match state:
        case "ok" | "verified" | "repaired":
            return ok
        case "missing" | "repairing" | "checking" | "pending":
            return warn
        case "corrupt" | "failed":
            return bad
        case _:
            return ""


def _count_states(status: Status) -> dict[str, int]:
    counts = {"ok": 0, "missing": 0, "corrupt": 0, "repaired": 0}
    for step in status.steps:
        if step.state in {"ok", "verified"}:
            counts["ok"] += 1
        elif step.state in counts:
            counts[step.state] += 1
    return counts


def _copy(status: Status) -> Status:
    return Status(
        state=status.state,
        tier=status.tier,
        bytes_total=status.bytes_total,
        bytes_done=status.bytes_done,
        error=status.error,
        updated_at=status.updated_at,
        steps=[
            Step(
                model=step.model,
                size=step.size,
                source=step.source,
                speed_bps=step.speed_bps,
                eta_s=step.eta_s,
                state=step.state,
                received=step.received,
                sha256=step.sha256,
                apps=list(step.apps),
                item_id=step.item_id,
                display_name=step.display_name,
            )
            for step in status.steps
        ],
        jobs=[dict(row) for row in status.jobs],
        extra=dict(status.extra),
    )
