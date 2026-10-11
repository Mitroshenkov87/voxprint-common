"""Progress window for the model downloader.

The window uses the suite stylesheet from :mod:`voxprint_common.ui.theme`.
PySide6 is imported inside :func:`run_window` so this module can be imported
on a headless installer without Qt. The labels are Russian.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable

from voxprint_common.cli_protocol import EXIT_CANCELLED, EXIT_FAILED, EXIT_OK
from voxprint_common.mdl.job import Outcome
from voxprint_common.mdl.names import DISPLAY_NAME
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
    "format_eta",
    "format_size",
    "format_speed",
    "run_window",
    "window_title",
]

RETRY_TEXT = "Повторить"
CANCEL_TEXT = "Отмена"
CANCELLED_TEXT = "Отменено"
READY_TEXT = "Готово"
ERROR_PREFIX = "Ошибка"
HEADING = "Загрузка моделей"
OVERALL = "Общий прогресс"
COLUMNS = ("Модель", "Размер", "Источник", "Скорость", "Осталось")

Starter = Callable[[threading.Event, Callable[[Status], None]], Outcome]


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


def run_window(start: Starter) -> int:
    """Show the progress window and run ``start`` until it finishes or is cancelled.

    PySide6 is imported in this function. A missing install raises ``ImportError``.

    Args:
        start: ``start(cancel, on_status) -> Outcome``. Called on a worker
            thread. ``on_status`` may be called from that thread.

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
            )
            for step in status.steps
        ],
        extra=dict(status.extra),
    )
