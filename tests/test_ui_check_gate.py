"""CheckGate runs only from Check, on a worker, and treats handled failures as passed.

PySide6 stays out of this module's import. Pytest collects every test module
before it runs any test, and several earlier tests assert that PySide6 is
still absent from sys.modules. This file is named so its tests run after
those checks.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from voxprint_common.i18n import set_language

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.usefixtures("_english")

_QT: SimpleNamespace | None = None


@pytest.fixture
def _english() -> None:
    set_language("en")


def _qt() -> SimpleNamespace:
    """Import Qt on first use so collection itself does not load PySide6."""
    global _QT
    if _QT is not None:
        return _QT
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QEventLoop, QThread, QTimer
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from voxprint_common.ui.check_gate import CheckGate, CheckItem, GateState, ItemResult

    _QT = SimpleNamespace(
        QEventLoop=QEventLoop,
        QThread=QThread,
        QTimer=QTimer,
        QApplication=QApplication,
        QLabel=QLabel,
        QPushButton=QPushButton,
        CheckGate=CheckGate,
        CheckItem=CheckItem,
        GateState=GateState,
        ItemResult=ItemResult,
    )
    return _QT


def _app(qt: SimpleNamespace):
    current = qt.QApplication.instance()
    if isinstance(current, qt.QApplication):
        return current
    return qt.QApplication([])


def _wait(qt: SimpleNamespace, gate, action, timeout_ms: int = 3000):
    loop = qt.QEventLoop()
    seen: list = []

    def _done(state) -> None:
        seen.append(state)
        loop.quit()

    gate.finished.connect(_done)
    qt.QTimer.singleShot(timeout_ms, loop.quit)
    action()
    loop.exec()
    if not seen:
        raise AssertionError("check did not finish")
    return seen[-1]


def _gate(qt: SimpleNamespace, job, items, *, unload=None, on_deactivate=None):
    _app(qt)
    return qt.CheckGate(job, items=items, unload=unload, on_deactivate=on_deactivate)


def test_edit_marks_dirty_and_does_not_run_the_job() -> None:
    qt = _qt()
    calls: list[str] = []

    def job(item, _token):
        calls.append(item.id)
        return qt.ItemResult(item.id, True)

    gate = _gate(qt, job, lambda: [qt.CheckItem("a", "First")])
    assert gate.state is qt.GateState.DIRTY
    assert gate.can_proceed is False
    gate.mark_dirty()
    _app(qt).processEvents()
    assert calls == []
    assert gate.state is qt.GateState.DIRTY
    assert {item.name for item in qt.GateState} == {
        "DIRTY",
        "RUNNING",
        "PASSED",
        "FAILED",
        "CANCELLED",
    }
    source = Path("src/voxprint_common/ui/check_gate.py").read_text(encoding="utf-8")
    assert "QTimer" not in source


def test_next_stays_blocked_until_the_check_passes() -> None:
    qt = _qt()
    threads: list[threading.Thread] = []
    qt_threads: list = []
    progress: list[tuple[int, int, str]] = []

    def job(item, _token):
        threads.append(threading.current_thread())
        qt_threads.append(qt.QThread.currentThread())
        return qt.ItemResult(item.id, True, "ok")

    gate = _gate(
        qt,
        job,
        lambda: [qt.CheckItem("a", "First"), qt.CheckItem("b", "Second", active=False)],
    )
    gate.progress.connect(lambda done, total, item_id: progress.append((done, total, item_id)))
    button = qt.QPushButton()
    button.setEnabled(gate.can_proceed)
    gate.canProceedChanged.connect(button.setEnabled)
    assert button.isEnabled() is False
    assert _wait(qt, gate, gate.start) is qt.GateState.PASSED
    assert gate.can_proceed is True
    assert button.isEnabled() is True
    assert progress == [(1, 1, "a")]
    assert gate.results["a"].ok is True
    assert "b" not in gate.results
    assert threads[0] is not threading.main_thread()
    assert qt_threads[0] is not _app(qt).thread()
    gate.mark_dirty()
    assert gate.state is qt.GateState.DIRTY
    assert gate.can_proceed is False
    assert button.isEnabled() is False
    assert len(threads) == 1


def test_cancel_stays_red_and_unloads_off_the_ui_thread() -> None:
    qt = _qt()
    seen: list[str] = []
    unload_threads: list[threading.Thread] = []

    def job(item, token):
        seen.append(item.id)
        if item.id == "first":
            deadline = time.monotonic() + 2
            while not token.is_cancelled and time.monotonic() < deadline:
                time.sleep(0.01)
        return qt.ItemResult(item.id, True)

    def unload() -> None:
        unload_threads.append(threading.current_thread())

    gate = _gate(
        qt,
        job,
        lambda: [qt.CheckItem("first", "First"), qt.CheckItem("second", "Second")],
        unload=unload,
    )

    def cancel_after_start() -> None:
        gate.start()
        deadline = time.monotonic() + 2
        while not seen and time.monotonic() < deadline:
            _app(qt).processEvents()
            time.sleep(0.01)
        gate.cancel()

    assert _wait(qt, gate, cancel_after_start) is qt.GateState.CANCELLED
    assert seen == ["first"]
    assert gate.can_proceed is False
    assert len(unload_threads) == 1
    assert unload_threads[0] is not threading.main_thread()


def test_handled_failure_passes_when_one_item_passed() -> None:
    qt = _qt()
    hooked: list[tuple[str, ...]] = []
    hook_thread: list = []

    def job(item, _token):
        return qt.ItemResult(item.id, item.id != "bad")

    def on_deactivate(ids) -> None:
        hooked.append(tuple(ids))
        hook_thread.append(qt.QThread.currentThread())

    gate = _gate(
        qt,
        job,
        lambda: [qt.CheckItem("good", "Good"), qt.CheckItem("bad", "Bad")],
        on_deactivate=on_deactivate,
    )
    assert _wait(qt, gate, gate.start) is qt.GateState.PASSED
    assert gate.can_proceed is True
    assert gate.deactivated == ("bad",)
    assert hooked == [("bad",)]
    assert hook_thread[0] is _app(qt).thread()
    detail = gate.findChild(qt.QLabel, "ok")
    assert detail is not None
    assert detail.isHidden() is False
    assert detail.text() == "1 deactivated"


def test_failure_when_hook_missing_raises_or_nothing_passed() -> None:
    qt = _qt()

    def fail(item, _token):
        return qt.ItemResult(item.id, False)

    def mixed(item, _token):
        return qt.ItemResult(item.id, item.id == "good")

    missing = _gate(
        qt,
        mixed,
        lambda: [qt.CheckItem("good", "Good"), qt.CheckItem("bad", "Bad")],
    )
    assert _wait(qt, missing, missing.start) is qt.GateState.FAILED
    assert missing.can_proceed is False
    assert missing.deactivated == ()

    def boom(_ids) -> None:
        raise RuntimeError("stop")

    raised = _gate(
        qt,
        mixed,
        lambda: [qt.CheckItem("good", "Good"), qt.CheckItem("bad", "Bad")],
        on_deactivate=boom,
    )
    assert _wait(qt, raised, raised.start) is qt.GateState.FAILED
    assert raised.deactivated == ()

    seen: list[str] = []
    all_bad = _gate(
        qt,
        fail,
        lambda: [
            qt.CheckItem("a", "A"),
            qt.CheckItem("b", "B"),
            qt.CheckItem("off", "Off", active=False),
        ],
        on_deactivate=lambda ids: seen.extend(ids),
    )
    assert _wait(qt, all_bad, all_bad.start) is qt.GateState.FAILED
    assert all_bad.can_proceed is False
    assert all_bad.deactivated == ("a", "b")
    assert seen == ["a", "b"]
    detail = all_bad.findChild(qt.QLabel, "ok")
    assert detail is not None
    assert detail.isHidden() is True


def test_job_exception_unloads_and_does_not_deactivate() -> None:
    qt = _qt()
    unloaded: list[int] = []
    hooked: list[object] = []

    def job(_item, _token):
        raise RuntimeError("boom")

    gate = _gate(
        qt,
        job,
        lambda: [qt.CheckItem("a", "A")],
        unload=lambda: unloaded.append(1),
        on_deactivate=hooked.append,
    )
    assert _wait(qt, gate, gate.start) is qt.GateState.FAILED
    assert unloaded == [1]
    assert hooked == []
    assert gate.deactivated == ()


def test_no_active_items_passes() -> None:
    qt = _qt()
    calls: list[str] = []
    gate = _gate(
        qt,
        lambda item, _token: calls.append(item.id) or qt.ItemResult(item.id, True),
        lambda: [qt.CheckItem("off", "Off", active=False)],
    )
    assert _wait(qt, gate, gate.start) is qt.GateState.PASSED
    assert calls == []
    assert gate.deactivated == ()


def test_second_start_while_running_is_ignored() -> None:
    qt = _qt()
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def job(item, _token):
        calls.append(item.id)
        entered.set()
        release.wait(2)
        return qt.ItemResult(item.id, True)

    gate = _gate(qt, job, lambda: [qt.CheckItem("a", "A")])
    gate.start()
    assert entered.wait(2)
    gate.start()
    release.set()
    assert _wait(qt, gate, lambda: None) is qt.GateState.PASSED
    assert calls == ["a"]
