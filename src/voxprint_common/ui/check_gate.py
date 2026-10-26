"""A Check button that blocks Next until a background check passes.

Importing this module loads PySide6. An edit calls :meth:`CheckGate.mark_dirty`
and does not run a model. The job runs on a worker thread, only after Check.

``DIRTY``, ``FAILED``, and ``CANCELLED`` paint a red stop. ``PASSED`` paints a
green arrow. ``RUNNING`` paints a static accent arc. The widget starts no
timer. ``on_deactivate`` runs on the UI thread. ``items``, ``job``, and
``unload`` run on the worker and must not touch widgets.
"""

from __future__ import annotations

import contextlib
import enum
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import assert_never

import shiboken6
from PySide6.QtCore import QCoreApplication, QPoint, Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent, QColor, QPainter, QPaintEvent, QPen, QPolygon
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from voxprint_common.i18n import tr
from voxprint_common.ui.theme import (
    ACCENT,
    GREEN,
    RED,
    ROOT_PLAIN,
    apply_shell_palette,
    build_style,
)

__all__ = [
    "CancelToken",
    "CheckGate",
    "CheckItem",
    "GateState",
    "ItemResult",
]

logger = logging.getLogger(__name__)


def _halt_worker(thread: QThread, token: CancelToken) -> None:
    """Cancel ``thread`` and return only after ``run`` has returned."""
    if not shiboken6.isValid(thread):
        return
    token._cancel()
    for signal in (getattr(thread, "advanced", None), getattr(thread, "finished_run", None)):
        if signal is None:
            continue
        with contextlib.suppress(RuntimeError):
            signal.disconnect()
    if shiboken6.isValid(thread) and thread.isRunning():
        thread.wait()


class GateState(enum.Enum):
    """State of a check.

    ``DIRTY``, ``FAILED``, and ``CANCELLED`` are the red stop. ``PASSED`` is
    the green arrow. ``RUNNING`` is the accent arc. A handled failure is still
    ``PASSED`` or ``FAILED``; there is no extra value.
    """

    DIRTY = enum.auto()
    RUNNING = enum.auto()
    PASSED = enum.auto()
    FAILED = enum.auto()
    CANCELLED = enum.auto()


@dataclass(frozen=True)
class CheckItem:
    """One thing the gate can check.

    Attributes:
        id: Stable id. ``on_deactivate`` reports failed ids.
        label: Text the host shows for this item. The gate does not paint it.
        active: When false, the worker skips the item.
    """

    id: str
    label: str
    active: bool = True


@dataclass(frozen=True)
class ItemResult:
    """The job's answer for one item.

    Attributes:
        id: Item id.
        ok: True when the item passed.
        message: Short note. Empty when there is nothing to say.
    """

    id: str
    ok: bool
    message: str = ""


class CancelToken:
    """Flag the job reads so it can return once Cancel is pressed."""

    def __init__(self) -> None:
        """Start uncancelled."""
        self._flag = threading.Event()

    @property
    def is_cancelled(self) -> bool:
        """Return whether Cancel was pressed."""
        return self._flag.is_set()

    def _cancel(self) -> None:
        self._flag.set()


@dataclass(frozen=True)
class _Outcome:
    """What the worker reports when a run ends."""

    results: tuple[ItemResult, ...]
    cancelled: bool
    error: Exception | None
    generation: int


class _Worker(QThread):
    """Run one check off the UI thread."""

    advanced = Signal(int, int, str, int)
    finished_run = Signal(object)

    def __init__(
        self,
        job: Callable[[CheckItem, CancelToken], ItemResult],
        items: Callable[[], Sequence[CheckItem]],
        unload: Callable[[], None] | None,
        token: CancelToken,
        generation: int,
        parent: QWidget,
    ) -> None:
        """Keep the callbacks for one check.

        Args:
            job: Called once per active item.
            items: Returns the items to consider.
            unload: Called after the run, including cancel and failure.
            token: Cancel flag for this run.
            generation: Run id. A newer edit drops the result.
            parent: Gate that owns this thread.
        """
        super().__init__(parent)
        self._job = job
        self._items = items
        self._unload = unload
        self._token = token
        self._generation = generation

    def run(self) -> None:
        """Check active items in order, then unload."""
        results: list[ItemResult] = []
        cancelled = False
        error: Exception | None = None
        try:
            active = [item for item in self._items() if item.active]
            total = len(active)
            self.advanced.emit(0, total, "", self._generation)
            for index, item in enumerate(active):
                if self._token.is_cancelled:
                    cancelled = True
                    break
                result = self._job(item, self._token)
                if not isinstance(result, ItemResult):
                    raise TypeError(f"job for {item.id} must return ItemResult")
                results.append(result)
                self.advanced.emit(index + 1, total, item.id, self._generation)
                if self._token.is_cancelled:
                    cancelled = True
                    break
        except Exception as exc:
            error = exc
            logger.exception("check gate job failed")
        finally:
            if self._unload is not None:
                try:
                    self._unload()
                except Exception as exc:
                    logger.exception("check gate unload failed")
                    if error is None:
                        error = exc
        outcome = _Outcome(tuple(results), cancelled, error, self._generation)
        self.finished_run.emit(outcome)


class _Mark(QWidget):
    """Red stop, green arrow, or a static accent arc."""

    def __init__(self) -> None:
        """Paint a 28 px mark."""
        super().__init__()
        self._state = GateState.DIRTY
        self.setFixedSize(28, 28)

    def set_state(self, state: GateState) -> None:
        """Show ``state`` on the next paint."""
        self._state = state
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the glyph for the current state."""
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        match self._state:
            case GateState.DIRTY | GateState.FAILED | GateState.CANCELLED:
                _paint_stop(painter)
            case GateState.PASSED:
                _paint_arrow(painter)
            case GateState.RUNNING:
                _paint_running(painter)
            case _ as unreachable:
                assert_never(unreachable)
        painter.end()


def _paint_stop(painter: QPainter) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(RED))
    painter.drawEllipse(2, 2, 24, 24)
    painter.setBrush(QColor(ROOT_PLAIN))
    painter.drawRoundedRect(7, 12, 14, 4, 1, 1)


def _paint_arrow(painter: QPainter) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(GREEN))
    triangle = QPolygon([QPoint(6, 5), QPoint(6, 23), QPoint(22, 14)])
    painter.drawPolygon(triangle)


def _paint_running(painter: QPainter) -> None:
    pen = QPen(QColor(ACCENT))
    pen.setWidth(3)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(4, 4, 20, 20, 40 * 16, 280 * 16)


class CheckGate(QWidget):
    """Check button, state mark, and the Next lock.

    Only active items are checked, one at a time, on a worker thread. Cancel
    stops after the current item and leaves the gate ``CANCELLED``. ``unload``
    runs on that worker after every ending, including cancel and a raised
    error.

    ``on_deactivate`` receives the failed ids after every active item has been
    checked. It is not called when the run is cancelled or the job raises.
    When the hook is set and returns, those items count as handled. If at
    least one checked item passed, the gate ends ``PASSED``, ``can_proceed``
    is true, and ``deactivated`` lists the ids that were switched off. The
    passed row then shows how many were deactivated. The gate ends ``FAILED``
    only when the hook is missing, when it raises, or when no active item
    passed. A run where every active item failed still records ``deactivated``
    when the hook returns; the red stop does not show that count.

    Turning an item back on is the host's job: mark it active and call
    :meth:`mark_dirty`. Create the gate on the UI thread.
    """

    stateChanged = Signal(GateState)
    canProceedChanged = Signal(bool)
    progress = Signal(int, int, str)
    finished = Signal(GateState)

    def __init__(
        self,
        job: Callable[[CheckItem, CancelToken], ItemResult],
        *,
        items: Callable[[], Sequence[CheckItem]],
        unload: Callable[[], None] | None = None,
        on_deactivate: Callable[[Sequence[str]], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build a dirty gate. Nothing runs until :meth:`start`.

        Args:
            job: Called on the worker with the item and the cancel token.
            items: Called on the worker. Inactive items are skipped.
            unload: Called on the worker after the run. Use it to free a model.
            on_deactivate: Called on the UI thread with the failed ids, in
                check order, when the check finishes.
            parent: Owning widget.
        """
        super().__init__(parent)
        self._job = job
        self._items = items
        self._unload = unload
        self._on_deactivate = on_deactivate
        self._state = GateState.DIRTY
        self._results: dict[str, ItemResult] = {}
        self._deactivated: tuple[str, ...] = ()
        self._generation = 0
        self._joined = False
        self._stop_hook: Callable[..., None] | None = None
        self._thread: _Worker | None = None
        self._token: CancelToken | None = None
        self._done = 0
        self._total = 0
        self.setObjectName("root")
        self.setStyleSheet(build_style(False))
        apply_shell_palette(self)
        self._mark = _Mark()
        self._status = QLabel()
        self._detail = QLabel()
        self._detail.setObjectName("ok")
        self._check = QPushButton(tr("check_gate.check"))
        self._check.setObjectName("primary")
        self._check.clicked.connect(self._on_check_clicked)
        self._cancel = QPushButton(tr("check_gate.cancel"))
        self._cancel.clicked.connect(self._on_cancel_clicked)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        text.addWidget(self._status)
        text.addWidget(self._detail)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(12)
        row.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(text, 1)
        row.addWidget(self._check)
        row.addWidget(self._cancel)
        self._refresh_copy()

    @property
    def state(self) -> GateState:
        """Return the current state. A new gate is ``DIRTY``."""
        return self._state

    @property
    def can_proceed(self) -> bool:
        """Return whether Next may continue. True only in ``PASSED``."""
        return self._state is GateState.PASSED

    @property
    def results(self) -> Mapping[str, ItemResult]:
        """Return results from the latest applied run, keyed by item id.

        :meth:`start` replaces this map. :meth:`mark_dirty` leaves it.
        """
        return MappingProxyType(self._results)

    @property
    def deactivated(self) -> tuple[str, ...]:
        """Return the ids switched off by a successful ``on_deactivate``.

        Empty when the hook is missing, the hook raises, the run is cancelled,
        or the job raises. :meth:`start` clears it.
        """
        return self._deactivated

    def mark_dirty(self) -> None:
        """Turn the gate red. Do not run the job.

        A result already in flight is ignored, so an edit during a check
        cannot end ``PASSED``. Results already stored stay until the next
        :meth:`start`.
        """
        self._generation += 1
        if self._state is GateState.DIRTY:
            return
        self._set_state(GateState.DIRTY, finished=False)

    def start(self) -> None:
        """Start a check on a worker. A check already running is left as it is."""
        if self._thread is not None and self._thread.isRunning():
            return
        self._joined = False
        self._generation += 1
        generation = self._generation
        self._results = {}
        self._deactivated = ()
        self._done = 0
        self._total = 0
        token = CancelToken()
        self._token = token
        worker = _Worker(
            self._job,
            self._items,
            self._unload,
            token,
            generation,
            None,
        )
        worker.advanced.connect(self._on_progress)
        worker.finished_run.connect(self._on_finished)
        self._thread = worker
        self._arm_lifetime(worker, token)
        worker.start()
        self._set_state(GateState.RUNNING, finished=False)

    def cancel(self) -> None:
        """Ask the worker to stop after the item it is checking now."""
        if self._thread is None or not self._thread.isRunning() or self._token is None:
            return
        self._token._cancel()

    def _present(
        self,
        state: GateState,
        *,
        done: int = 0,
        total: int = 0,
        deactivated: tuple[str, ...] = (),
    ) -> None:
        """Paint one state for a screenshot. The job does not run."""
        self._done = done
        self._total = total
        self._deactivated = deactivated
        self._set_state(state, finished=False)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop the worker before the gate goes away."""
        self._join_worker()
        super().closeEvent(event)

    def _arm_lifetime(self, thread: _Worker, token: CancelToken) -> None:
        """Stop ``thread`` when the gate is destroyed.

        A bound method is not called from ``destroyed`` while the gate is
        already going away, so the slot is a function that owns the thread.
        """
        previous = self._stop_hook
        if previous is not None:
            with contextlib.suppress(RuntimeError):
                self.destroyed.disconnect(previous)

        def stop(*_args: object, thread: _Worker = thread, token: CancelToken = token) -> None:
            _halt_worker(thread, token)

        self._stop_hook = stop
        self.destroyed.connect(stop)

    def _join_worker(self) -> None:
        """Cancel the worker and wait until it has left ``run``."""
        if self._joined:
            return
        self._joined = True
        thread = self._thread
        token = self._token
        self._thread = None
        if thread is None or token is None:
            return
        _halt_worker(thread, token)
        QCoreApplication.removePostedEvents(self)
        thread.deleteLater()

    def _on_check_clicked(self, _checked: bool = False) -> None:
        self.start()

    def _on_cancel_clicked(self, _checked: bool = False) -> None:
        self.cancel()

    def _on_progress(self, done: int, total: int, item_id: str, generation: int) -> None:
        if generation != self._generation:
            return
        self._done = done
        self._total = total
        if item_id:
            self.progress.emit(done, total, item_id)
        if self._state is GateState.RUNNING:
            self._refresh_copy()

    def _on_finished(self, payload: object) -> None:
        if not isinstance(payload, _Outcome):
            return
        sender = self.sender()
        if sender is self._thread:
            self._thread = None
        if isinstance(sender, QThread):
            if sender.isRunning():
                sender.wait()
            sender.deleteLater()
        if payload.generation != self._generation:
            self._refresh_copy()
            return
        results = {result.id: result for result in payload.results}
        state, deactivated = self._resolve(payload)
        if payload.generation != self._generation:
            self._refresh_copy()
            return
        self._results = results
        self._deactivated = deactivated
        self._set_state(state, finished=True)

    def _resolve(self, payload: _Outcome) -> tuple[GateState, tuple[str, ...]]:
        if payload.error is not None:
            return GateState.FAILED, ()
        if payload.cancelled:
            return GateState.CANCELLED, ()
        failed = tuple(result.id for result in payload.results if not result.ok)
        if not failed:
            return GateState.PASSED, ()
        if self._on_deactivate is None:
            return GateState.FAILED, ()
        try:
            self._on_deactivate(failed)
        except Exception:
            logger.exception("check gate deactivate failed")
            return GateState.FAILED, ()
        if any(result.ok for result in payload.results):
            return GateState.PASSED, failed
        return GateState.FAILED, failed

    def _set_state(self, state: GateState, *, finished: bool) -> None:
        previous = self._state
        self._state = state
        self._mark.set_state(state)
        self._refresh_copy()
        if state is not previous:
            self.stateChanged.emit(state)
        proceed = state is GateState.PASSED
        if proceed is not (previous is GateState.PASSED):
            self.canProceedChanged.emit(proceed)
        if finished:
            self.finished.emit(state)

    def _refresh_copy(self) -> None:
        self._status.setText(self._status_text())
        count = len(self._deactivated)
        show = self._state is GateState.PASSED and count > 0
        if show:
            self._detail.setText(tr("check_gate.deactivated", count=count))
        self._detail.setVisible(show)
        running = self._thread is not None and self._thread.isRunning()
        self._check.setEnabled(not running)
        self._cancel.setEnabled(running)

    def _status_text(self) -> str:
        match self._state:
            case GateState.DIRTY:
                return tr("check_gate.state.dirty")
            case GateState.RUNNING:
                return tr(
                    "check_gate.state.running",
                    done=self._done,
                    total=self._total,
                )
            case GateState.PASSED:
                return tr("check_gate.state.passed")
            case GateState.FAILED:
                return tr("check_gate.state.failed")
            case GateState.CANCELLED:
                return tr("check_gate.state.cancelled")
            case _ as unreachable:
                assert_never(unreachable)
