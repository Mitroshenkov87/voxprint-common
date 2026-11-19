"""Headless GUI smoke, including the reference shell.

These tests need pytest-qt. The suite disables that plugin by default because
it loads PySide6 at startup. ``tests/test_worker_gui_smoke.py`` runs this
file with the plugin enabled.
"""

from __future__ import annotations

import pytest

from voxprint_common.testing.gui_smoke import (
    DeadControls,
    assert_survives_worker_crash,
    exercise,
)


def test_dead_controls_are_listed(qtbot: object) -> None:
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMainWindow, QMenuBar, QPushButton, QVBoxLayout, QWidget

    window = QMainWindow()
    root = QWidget()
    layout = QVBoxLayout(root)
    live = QPushButton("Save")
    live.clicked.connect(lambda: None)
    dead = QPushButton("Nowhere")
    layout.addWidget(live)
    layout.addWidget(dead)
    window.setCentralWidget(root)
    bar = QMenuBar(window)
    menu = bar.addMenu("File")
    opening = QAction("Open", window)
    opening.triggered.connect(lambda: None)
    nothing = QAction("Nothing", window)
    menu.addAction(opening)
    menu.addAction(nothing)
    window.setMenuBar(bar)
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.resize(320, 200)
    window.show()
    with pytest.raises(DeadControls) as caught:
        exercise(window, qtbot=qtbot)
    message = str(caught.value).split("Dead controls:", 1)[1]
    assert "Nowhere" in message
    assert "Nothing" in message
    assert "Save" not in message
    assert "Open" not in message


def test_a_button_that_opens_a_dialog_passes(qtbot: object) -> None:
    from PySide6.QtWidgets import QDialog, QMainWindow, QPushButton

    window = QMainWindow()
    button = QPushButton("Show")

    def show() -> None:
        dialog = QDialog(window)
        dialog.setWindowTitle("Shown")
        dialog.show()

    button.clicked.connect(show)
    window.setCentralWidget(button)
    qtbot.addWidget(window)  # type: ignore[attr-defined]
    window.resize(200, 120)
    window.show()
    assert exercise(window, qtbot=qtbot) >= 1


def test_reference_shell_controls_respond(qtbot: object) -> None:
    from voxprint_common.ui.shell import ReferenceShell

    shell = ReferenceShell()
    qtbot.addWidget(shell.widget)  # type: ignore[attr-defined]
    shell.resize(1200, 900)
    shell.show()
    try:
        assert exercise(shell.widget, qtbot=qtbot) > 0
    finally:
        shell.close()


def test_reference_shell_survives_a_worker_crash(qtbot: object) -> None:
    from voxprint_common.ui.shell import ReferenceShell
    from voxprint_common.worker import Handlers, WorkerHost
    from voxprint_common.worker.qt import QtWorkerHost, install_crash_bar

    shell = ReferenceShell()
    qtbot.addWidget(shell.widget)  # type: ignore[attr-defined]
    shell.resize(1200, 900)
    shell.show()
    host = WorkerHost(Handlers(run="tests.worker_handlers:run"), backoff_initial=0)
    adapter = QtWorkerHost(host, interval_ms=20)
    install_crash_bar(
        shell.widget,
        adapter,
        title="The model stopped",
        body="The window stays open. You can try again.",
    )
    host.start()
    try:
        assert_survives_worker_crash(qtbot, shell.widget, host)
    finally:
        adapter.shutdown(timeout=2)
        shell.close()
