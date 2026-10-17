"""Drive the reference shell on an offscreen Qt platform.

Pytest starts this file in a subprocess so the parent process does not import
PySide6. The parent test checks the PNG files this script writes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_LOCALES = ("en", "ru", "de", "uk", "lv")
_GERMAN_TOOLS = ("Speichern", "Rückgängig", "Wiederholen", "Befehlspalette")


def main() -> None:
    """Open the shell, exercise chords, and write the reference PNGs."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from voxprint_common.i18n import set_language, tr
    from voxprint_common.ui.shell import ReferenceShell, grab_shell
    from voxprint_common.ui.theme import ACCENT

    out = Path(sys.argv[1])
    set_language("en")
    app = QApplication([])
    if len(sys.argv) > 2 and sys.argv[2] == "chips":
        _check_chip_fit(app)
        return
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    shell.widget.activateWindow()
    app.processEvents()

    edit = shell.find("QLineEdit", "promptEdit")
    assert edit is not None
    edit.setText("north")  # type: ignore[attr-defined]
    edit.setFocus()  # type: ignore[attr-defined]
    app.processEvents()
    assert app.focusWidget() is edit
    QTest.keyClick(edit, Qt.Key_K, Qt.ControlModifier)  # type: ignore[arg-type]
    app.processEvents()
    assert shell.palette_open() is False
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)  # type: ignore[arg-type]
    app.processEvents()
    assert edit.selectedText() == "north"  # type: ignore[attr-defined]

    shell.widget.setFocus()
    app.processEvents()
    assert app.focusWidget() is shell.widget
    QTest.keyClick(shell.widget, Qt.Key_K, Qt.ControlModifier)
    app.processEvents()
    assert shell.palette_open() is True
    QTest.keyClick(shell.widget, Qt.Key_Escape)
    app.processEvents()
    assert shell.palette_open() is False

    QTest.keyClick(shell.widget, Qt.Key_W, Qt.ControlModifier)
    app.processEvents()
    status = shell.find("QLabel", "status")
    assert status is not None
    assert status.text() == tr("shell.status.protected")  # type: ignore[attr-defined]
    assert shell.history.layout.contains("canvas")

    shell._activate("prompt")
    QTest.keyClick(shell.widget, Qt.Key_F, Qt.ControlModifier | Qt.ShiftModifier)
    app.processEvents()
    assert shell.history.layout.floating
    assert shell.history.layout.floating[-1].panels == ["prompt"]
    shell.close()
    app.processEvents()

    for language in _LOCALES:
        _check_toolbar(app, language)
    _check_palette(app)
    _check_overflow(app)
    _check_focus_canvas(app, Qt, QTest)
    _check_close_retarget(app, Qt, QTest)
    _check_split_geometry(app)
    _check_split_click(app, QPoint, QEvent, QPointF, Qt, QMouseEvent, QApplication)
    _check_tab_close(app, Qt, QTest)
    _check_split_undo(app, QPoint, QEvent, QPointF, Qt, QMouseEvent, QApplication)
    _check_split_colour(app, ACCENT)

    _check_expert(app)
    _check_simple(app, Qt, QTest)
    _check_variants(app, out)
    _check_audiobook(app)
    _check_viewer(app)
    grab_shell(out / "en-shell.png", language="en", full_layout=True)
    grab_shell(out / "ru-shell.png", language="ru", full_layout=True)
    grab_shell(out / "de-shell.png", language="de", full_layout=True, reveal_close=True)
    grab_shell(
        out / "en-palette.png",
        language="en",
        full_layout=True,
        show_palette=True,
        palette_query="pa",
    )
    grab_shell(
        out / "ru-palette.png",
        language="ru",
        full_layout=True,
        show_palette=True,
        palette_query="па",
    )
    grab_shell(out / "en-split.png", language="en", full_layout=True, hot_split=True)
    grab_shell(out / "en-simple-empty.png", language="en")
    grab_shell(out / "en-simple-result.png", language="en", result=True)
    grab_shell(
        out / "ru-simple-result.png",
        language="ru",
        result=True,
        prompt="Тихий холм в сумерках",
    )
    grab_shell(out / "en-expert.png", language="en", expert=True, result=True)
    grab_shell(
        out / "en-expert-modified.png",
        language="en",
        expert=True,
        result=True,
        modified=True,
    )
    grab_shell(out / "image-simple.png", language="en", result=True, app_id="canvas")
    grab_shell(
        out / "en-image-light.png",
        language="en",
        result=True,
        app_id="canvas",
        light=True,
    )
    grab_shell(out / "image-expert.png", language="en", expert=True, result=True, app_id="canvas")
    grab_shell(
        out / "audio-simple.png",
        language="en",
        result=True,
        app_id="audiobook-builder",
        prompt="quiet-chapter.epub",
    )
    grab_shell(
        out / "audio-expert.png",
        language="en",
        expert=True,
        result=True,
        app_id="audiobook-builder",
        prompt="quiet-chapter.epub",
    )
    grab_shell(
        out / "video-simple.png",
        language="en",
        result=True,
        app_id="clarity",
        prompt="quiet-clip.mp4",
    )
    grab_shell(
        out / "video-expert.png",
        language="en",
        expert=True,
        result=True,
        app_id="clarity",
        prompt="quiet-clip.mp4",
    )
    grab_shell(
        out / "text-simple.png",
        language="en",
        result=True,
        app_id="plotweaver",
        prompt="A quiet hill at dusk",
    )
    grab_shell(
        out / "text-expert.png",
        language="en",
        expert=True,
        result=True,
        app_id="plotweaver",
        prompt="A quiet hill at dusk",
    )
    grab_shell(
        out / "tool-simple.png",
        language="en",
        result=True,
        app_id="model-forge",
        prompt="Standard",
    )
    grab_shell(
        out / "tool-expert.png",
        language="en",
        expert=True,
        result=True,
        app_id="model-forge",
        prompt="Standard",
    )
    grab_shell(
        out / "ru-image-simple.png",
        language="ru",
        result=True,
        app_id="canvas",
        prompt="Тихий холм в сумерках",
    )
    grab_shell(
        out / "ru-image-expert.png",
        language="ru",
        expert=True,
        result=True,
        app_id="canvas",
        prompt="Тихий холм в сумерках",
    )
    grab_shell(
        out / "ru-audio-simple.png",
        language="ru",
        result=True,
        app_id="audiobook-builder",
        prompt="тихая-глава.epub",
    )
    grab_shell(
        out / "ru-audio-expert.png",
        language="ru",
        expert=True,
        result=True,
        app_id="audiobook-builder",
        prompt="тихая-глава.epub",
    )
    grab_shell(
        out / "ru-dub-simple.png",
        language="ru",
        result=True,
        app_id="movie-dubber",
        prompt="тихий-фильм.mp4",
    )
    grab_shell(
        out / "ru-dub-expert.png",
        language="ru",
        expert=True,
        result=True,
        app_id="movie-dubber",
        prompt="тихий-фильм.mp4",
    )
    grab_shell(
        out / "viewer-simple.png",
        language="en",
        result=True,
        app_id="viewer",
        prompt="A quiet hill at dusk",
    )
    grab_shell(
        out / "viewer-expert.png",
        language="en",
        expert=True,
        result=True,
        app_id="viewer",
        prompt="A quiet hill at dusk",
    )
    for app_id, prompt in (
        ("canvas", "A quiet hill at dusk"),
        ("audiobook-builder", "quiet-chapter.epub"),
        ("clarity", "quiet-clip.mp4"),
        ("plotweaver", "A quiet hill at dusk"),
        ("model-forge", "Standard"),
        ("viewer", "A quiet hill at dusk"),
    ):
        stem = {
            "canvas": "image",
            "audiobook-builder": "audio",
            "clarity": "video",
            "plotweaver": "text",
            "model-forge": "tool",
            "viewer": "viewer",
        }[app_id]
        grab_shell(
            out / f"{stem}-modified.png",
            language="en",
            expert=True,
            result=True,
            modified=True,
            app_id=app_id,
            prompt=prompt,
        )
    for app_id, prompt in (
        ("clarity", "тихий-клип.mp4"),
        ("plotweaver", "Тихий холм в сумерках"),
        ("model-forge", "Стандарт"),
        ("viewer", "Тихий холм в сумерках"),
    ):
        stem = {
            "clarity": "video",
            "plotweaver": "text",
            "model-forge": "tool",
            "viewer": "viewer",
        }[app_id]
        grab_shell(
            out / f"ru-{stem}-simple.png",
            language="ru",
            result=True,
            app_id=app_id,
            prompt=prompt,
        )
        grab_shell(
            out / f"ru-{stem}-expert.png",
            language="ru",
            expert=True,
            result=True,
            app_id=app_id,
            prompt=prompt,
        )
    grab_shell(
        out / "de-image-simple.png",
        language="de",
        result=True,
        app_id="canvas",
        prompt="Ein stiller Hügel",
    )
    grab_shell(
        out / "de-image-expert.png",
        language="de",
        expert=True,
        result=True,
        app_id="canvas",
        prompt="Ein stiller Hügel",
    )
    grab_shell(
        out / "ru-sections.png",
        language="ru",
        app_id="audiobook-builder",
        sections=True,
        prompt="тихая-глава.epub",
    )
    grab_shell(
        out / "ru-casting.png",
        language="ru",
        app_id="audiobook-builder",
        sections=True,
        section_id="roles",
    )
    grab_shell(
        out / "ru-steps.png",
        language="ru",
        app_id="audiobook-builder",
        sections=True,
        wizard_id="train",
    )
    grab_shell(
        out / "ru-steps-running.png",
        language="ru",
        app_id="audiobook-builder",
        sections=True,
        wizard_id="train",
        wizard_running=True,
    )


def _overflow_labels() -> list[str]:
    from voxprint_common.i18n import tr

    return [
        tr("simple.menu.open_folder"),
        tr("simple.menu.models"),
        tr("simple.menu.settings"),
        tr("command.view.toggle_expert_mode"),
    ]


def _check_variants(app: object, out: Path) -> None:
    from voxprint_common.i18n import set_language, tr
    from voxprint_common.suite_settings import set_expert_mode
    from voxprint_common.ui.shell import ReferenceShell
    from voxprint_common.ui.simple_image import Phase

    set_language("en")
    set_expert_mode(False)
    cases = (
        ("canvas", "simple.generate", "promptBox", "result"),
        ("enlarge", "simple.action.enlarge", "fileDrop", "result"),
        ("lucent", "simple.generate", "promptBox", "result"),
        ("audiobook-builder", "simple.action.audiobook", "voicePicker", "audioPlay"),
        ("movie-dubber", "simple.action.dub", "fileDrop", "videoPoster"),
        ("clarity", "simple.action.improve_video", "fileDrop", "compareSlider"),
        ("plotweaver", "simple.action.write", "promptBox", "documentView"),
        ("model-forge", "simple.action.prepare", "modelPicker", "reportView"),
    )
    prompts = {
        "canvas": "A quiet hill at dusk",
        "enlarge": "hill.png",
        "lucent": "A quiet hill at dusk",
        "audiobook-builder": "A quiet chapter",
        "movie-dubber": "A quiet clip",
        "clarity": "A quiet clip",
        "plotweaver": "A quiet hill at dusk",
        "model-forge": "Standard",
    }
    for app_id, action_key, source_name, result_name in cases:
        shell = ReferenceShell(app=app_id, state_dir=out / "variant-state" / app_id)
        shell.resize(1200, 900)
        shell.show()
        app.processEvents()  # type: ignore[attr-defined]
        button = shell.find("QPushButton", "generate")
        assert button is not None and button.text() == tr(action_key)  # type: ignore[attr-defined]
        found = (
            shell.find("QPlainTextEdit", source_name)
            or shell.find("QLabel", source_name)
            or shell.find("QComboBox", source_name)
            or shell.find("QSlider", source_name)
        )
        assert found is not None, source_name
        overflow = shell.find("QPushButton", "overflow")
        labels = [action.text() for action in overflow.menu().actions()]  # type: ignore[attr-defined]
        assert labels == _overflow_labels()
        shell.show_result(prompts[app_id], pictures=1)
        app.processEvents()  # type: ignore[attr-defined]
        result = (
            shell.find("QLabel", result_name)
            or shell.find("QSlider", result_name)
            or shell.find("QPlainTextEdit", result_name)
            or shell.find("QPushButton", result_name)
        )
        assert result is not None and result.isVisible()  # type: ignore[attr-defined]
        shell.close()
        app.processEvents()  # type: ignore[attr-defined]

    audio = ReferenceShell(app="audiobook-builder", state_dir=out / "variant-state" / "progress")
    audio.resize(900, 700)
    audio.show()
    app.processEvents()  # type: ignore[attr-defined]
    audio.simple.set_prompt_text("A quiet chapter")
    assert audio.image.generate() is True
    audio.image.progress(1, 4, tr("simple.progress"))
    audio.simple.refresh()
    app.processEvents()  # type: ignore[attr-defined]
    quiet = audio.find("QLabel", "timeLeft")
    eta = audio.find("QLabel", "progressEta")
    assert quiet is not None and quiet.isVisible() is False  # type: ignore[attr-defined]
    assert eta is not None and eta.isVisible() is False  # type: ignore[attr-defined]
    status = audio.find("QLabel", "status")
    assert status is not None and "45" not in status.text()  # type: ignore[attr-defined]
    audio.image.progress(1, 4, tr("simple.progress"), eta_seconds=45)
    audio.simple.refresh()
    app.processEvents()  # type: ignore[attr-defined]
    assert audio.image.phase is Phase.BUSY
    percent = audio.find("QLabel", "progressPercent")
    cancel = audio.find("QPushButton", "cancel")
    assert percent is not None and percent.text() == tr("simple.progress.percent", percent=25)  # type: ignore[attr-defined]
    assert eta.text() == tr("simple.progress.eta_seconds", seconds=45)  # type: ignore[attr-defined]
    assert quiet.isVisible() is True and quiet.text() == eta.text()  # type: ignore[attr-defined]
    assert status.text() == eta.text()  # type: ignore[attr-defined]
    assert cancel is not None and cancel.isVisible()  # type: ignore[attr-defined]
    cancel.click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert audio.image.phase is Phase.EMPTY
    assert quiet.isVisible() is False  # type: ignore[attr-defined]
    assert status.text() == tr("shell.status.ready")  # type: ignore[attr-defined]
    audio.run("view.toggle_expert_mode")
    app.processEvents()  # type: ignore[attr-defined]
    rate = audio.find("QComboBox", "audio.sample_rate")
    assert rate is not None and rate.isVisible()  # type: ignore[attr-defined]
    assert audio.find("QFrame", "expertPanel").isVisible()  # type: ignore[attr-defined]
    audio.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_viewer(app: object) -> None:
    import json

    from voxprint_common.suite_settings import file, read, set_expert_mode
    from voxprint_common.ui.shell import ReferenceShell

    set_expert_mode(False)

    path = file()
    if path.is_file():
        data = read()
        data.pop("ui.window", None)
        data.pop("ui.geometry", None)
        path.write_text(json.dumps(data), encoding="utf-8")
    shell = ReferenceShell(state_dir=path.parent / "viewer-geometry")
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    assert (shell.widget.width(), shell.widget.height()) == (1200, 900)  # type: ignore[attr-defined]
    assert (shell.widget.minimumWidth(), shell.widget.minimumHeight()) == (960, 640)  # type: ignore[attr-defined]
    shell.resize(1100, 760)
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]
    again = ReferenceShell(state_dir=path.parent / "viewer-geometry-2")
    again.show()
    app.processEvents()  # type: ignore[attr-defined]
    assert (again.widget.width(), again.widget.height()) == (1100, 760)  # type: ignore[attr-defined]
    again.close()
    app.processEvents()  # type: ignore[attr-defined]

    if path.is_file():
        data = read()
        data.pop("ui.window", None)
        data.pop("ui.geometry", None)
        path.write_text(json.dumps(data), encoding="utf-8")
    viewer = ReferenceShell(app="viewer", state_dir=path.parent / "viewer-screen")
    viewer.show()
    app.processEvents()  # type: ignore[attr-defined]
    assert (viewer.widget.minimumWidth(), viewer.widget.minimumHeight()) == (640, 400)  # type: ignore[attr-defined]
    assert viewer.find("QPushButton", "generate") is None
    assert viewer.find("QPlainTextEdit", "promptBox") is None
    assert viewer.find("QLabel", "fileDrop") is None
    viewer.show_result("A quiet hill at dusk", pictures=1)
    app.processEvents()  # type: ignore[attr-defined]
    result = viewer.find("QLabel", "result")
    assert result is not None and result.isVisible()  # type: ignore[attr-defined]
    strip = viewer.find("QScrollArea", "historyStrip")
    assert strip is not None and strip.isVisible() and strip.height() == 104  # type: ignore[attr-defined]
    viewer.image.set_value("viewer.show_history", False)
    viewer.simple.refresh()
    app.processEvents()  # type: ignore[attr-defined]
    assert strip.isVisible() is False and strip.height() == 0  # type: ignore[attr-defined]
    viewer.run("view.toggle_expert_mode")
    app.processEvents()  # type: ignore[attr-defined]
    panel = viewer.find("QFrame", "expertPanel")
    split = viewer.find("QSplitter", "expertSplit")
    assert panel is not None and split is not None and panel.isVisible()  # type: ignore[attr-defined]
    assert abs(panel.width() - 280) <= split.handleWidth()  # type: ignore[attr-defined]
    viewer.resize(700, 450)
    viewer.close()
    app.processEvents()  # type: ignore[attr-defined]
    restored = ReferenceShell(app="viewer", state_dir=path.parent / "viewer-screen-2")
    restored.show()
    app.processEvents()  # type: ignore[attr-defined]
    assert (restored.widget.width(), restored.widget.height()) == (700, 450)  # type: ignore[attr-defined]
    restored.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_simple(app: object, qt: object, test: object) -> None:
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QApplication

    from voxprint_common.i18n import set_language, tr
    from voxprint_common.suite_settings import set_expert_mode
    from voxprint_common.ui.dock import layout_json
    from voxprint_common.ui.shell import ReferenceShell
    from voxprint_common.ui.simple_image import Phase

    set_language("en")
    set_expert_mode(False)
    shell = ReferenceShell()
    shell.resize(1200, 900)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.simple.root.isVisible() is True  # type: ignore[attr-defined]
    assert shell._dock_host.isVisible() is False  # type: ignore[attr-defined]
    assert shell._toolbar.isVisible() is False  # type: ignore[attr-defined]
    assert all(not tab.isVisible() for tab in shell.widget.findChildren(shell._widgets.QTabWidget))
    splitter = shell.find("QSplitter", "rootSplit")
    assert splitter is not None and splitter.isVisible() is False  # type: ignore[attr-defined]
    prompt = shell.find("QPlainTextEdit", "promptBox")
    assert prompt is not None
    assert prompt.placeholderText() == tr("shell.prompt.placeholder")  # type: ignore[attr-defined]
    generate = shell.find("QPushButton", "generate")
    assert generate is not None and generate.text() == tr("simple.generate")  # type: ignore[attr-defined]
    assert shell.find("QLabel", "emptyHint").text() == tr("simple.empty")  # type: ignore[attr-defined]
    balanced = shell.find("QPushButton", "chip-quality-balanced")
    assert balanced is not None and balanced.isChecked() is True  # type: ignore[attr-defined]
    assert shell.find("QPushButton", "chip-detail-medium").isChecked() is True  # type: ignore[attr-defined]
    hidden_reset = shell.find("QPushButton", "reset-quality")
    assert hidden_reset is not None and hidden_reset.isVisible() is False  # type: ignore[attr-defined]
    assert shell.find("QPushButton", "chip-size-1-1").isChecked() is True  # type: ignore[attr-defined]
    assert shell.find("QPushButton", "chip-style-photo").isChecked() is True  # type: ignore[attr-defined]
    assert shell.find("QPushButton", "chip-output-standard").isChecked() is True  # type: ignore[attr-defined]
    assert shell.find("QPushButton", "chip-count-1").isChecked() is True  # type: ignore[attr-defined]
    four_k = shell.find("QPushButton", "chip-output-4k")
    assert four_k is not None and four_k.isEnabled() is False  # type: ignore[attr-defined]
    assert shell.find("QLabel", "reason-output-4k").text() == tr("simple.output.4k.reason")  # type: ignore[attr-defined]
    install = shell.find("QPushButton", "unavailable-output-4k")
    assert install is not None and install.text() == tr("simple.output.install")  # type: ignore[attr-defined]
    install.click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.find("QLabel", "simpleNote").text() == tr("simple.output.install")  # type: ignore[attr-defined]
    overflow = shell.find("QPushButton", "overflow")
    assert overflow is not None  # type: ignore[attr-defined]
    overflow_labels = [action.text() for action in overflow.menu().actions()]  # type: ignore[attr-defined]
    assert overflow_labels == _overflow_labels()
    models = next(
        action
        for action in overflow.menu().actions()  # type: ignore[attr-defined]
        if action.text() == tr("simple.menu.models")
    )
    models.trigger()
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.find("QLabel", "simpleNote").text() == tr("simple.menu.models")  # type: ignore[attr-defined]
    improve = shell.find("QPushButton", "improvePrompt")
    revert = shell.find("QPushButton", "revertPrompt")
    assert improve is not None and improve.text() == tr("simple.improve")  # type: ignore[attr-defined]
    assert revert is not None and revert.isEnabled() is False  # type: ignore[attr-defined]
    prompt.setPlainText("a  quiet hill")  # type: ignore[attr-defined]
    improve.click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert prompt.toPlainText() == "a quiet hill"  # type: ignore[attr-defined]
    assert revert.isEnabled() is True  # type: ignore[attr-defined]
    revert.click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert prompt.toPlainText() == "a  quiet hill"  # type: ignore[attr-defined]
    assert revert.isEnabled() is False  # type: ignore[attr-defined]
    assert shell.find("QFrame", "settingsSheet") is None
    assert shell.registry.get("view.full_layout") is None
    assert shell.registry.get("view.simple_layout") is None
    toggle = shell.find("QPushButton", "expertToggle")
    assert toggle is not None and toggle.text() == tr("command.view.toggle_expert_mode")  # type: ignore[attr-defined]
    assert shell.find("QLabel", "preset-count").text() == tr("simple.count")  # type: ignore[attr-defined]
    more = shell.find("QPushButton", "moreLike")
    save = shell.find("QPushButton", "savePicture")
    folder = shell.find("QPushButton", "openFolder")
    again = shell.find("QPushButton", "usePrompt")
    assert more.text() == tr("simple.more") and more.isEnabled() is True  # type: ignore[attr-defined]
    assert save.text() == tr("simple.menu.save") and save.isEnabled() is True  # type: ignore[attr-defined]
    assert folder.text() == tr("simple.menu.open_folder") and folder.isEnabled() is True  # type: ignore[attr-defined]
    assert again.text() == tr("simple.menu.use_prompt") and again.isEnabled() is True  # type: ignore[attr-defined]
    save.click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.find("QLabel", "simpleNote").text() == tr("simple.error.picture")  # type: ignore[attr-defined]
    prompt.setPlainText("   ")  # type: ignore[attr-defined]
    prompt.setFocus()  # type: ignore[attr-defined]
    test.keyClick(prompt, qt.Key_Return)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.image.phase is Phase.EMPTY
    assert shell.find("QLabel", "simpleNote").text() == tr("simple.error.prompt")  # type: ignore[attr-defined]
    shell.image.set_prompt("a hill")
    assert shell.image.generate() is True
    shell.simple.refresh()
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.find("QLabel", "progressText").isVisible() is True  # type: ignore[attr-defined]
    assert shell.find("QLabel", "progressText").text() == tr("simple.progress")  # type: ignore[attr-defined]
    assert shell.find("QProgressBar", "progress").isVisible() is True  # type: ignore[attr-defined]
    shell.image.progress(1, 3, "Drawing the picture")
    shell.simple.refresh()
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.find("QLabel", "progressText").text() == "Drawing the picture"  # type: ignore[attr-defined]
    assert shell.find("QProgressBar", "progress").value() == 1  # type: ignore[attr-defined]
    cancel = shell.find("QPushButton", "cancel")
    assert cancel is not None and cancel.isVisible() is True  # type: ignore[attr-defined]
    cancel.click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.image.phase is Phase.EMPTY
    assert shell.image.stage_label == ""
    shell.image.block("This picture was stopped.")
    shell.simple.refresh()
    app.processEvents()  # type: ignore[attr-defined]
    blocked = shell.find("QLabel", "blockedText")
    assert blocked is not None and blocked.isVisible() is True  # type: ignore[attr-defined]
    assert blocked.text() == "This picture was stopped."  # type: ignore[attr-defined]
    shell.image.cancel()
    shell.simple.refresh()
    assert shell.image.phase is Phase.EMPTY
    shell.image.unavailable("That feature is turned off.")
    shell.simple.refresh()
    app.processEvents()  # type: ignore[attr-defined]
    feature = shell.find("QLabel", "featureText")
    assert feature is not None and feature.isVisible() is True  # type: ignore[attr-defined]
    assert feature.text() == "That feature is turned off."  # type: ignore[attr-defined]
    shell.image.cancel()
    shell.simple.refresh()
    assert shell.image.phase is Phase.EMPTY
    prompt.setPlainText("line")  # type: ignore[attr-defined]
    prompt.setFocus()  # type: ignore[attr-defined]
    prompt.moveCursor(QTextCursor.MoveOperation.End)  # type: ignore[attr-defined]
    test.keyClick(prompt, qt.Key_Return, qt.ShiftModifier)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert prompt.toPlainText() == "line\n"  # type: ignore[attr-defined]
    assert shell.image.phase is Phase.EMPTY
    test.keyClick(prompt, qt.Key_Return)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.image.phase is Phase.READY
    assert shell.image.history
    ratio = shell.find("QPushButton", "chip-size-16-9")
    ratio.click()  # type: ignore[attr-defined]
    assert shell.image.presets["size"] == "16:9"
    assert ratio.isChecked() is True  # type: ignore[attr-defined]
    item_id = shell.image.selected
    thumb = shell.find("QPushButton", f"thumb-{item_id}")
    assert thumb is not None and thumb.isVisible() is True  # type: ignore[attr-defined]
    assert thumb.contextMenuPolicy() == qt.CustomContextMenu  # type: ignore[attr-defined]
    thumb.click()  # type: ignore[attr-defined]
    assert shell.image.selected == item_id
    menu = shell.simple.history_menu(item_id)
    labels = [action.text() for action in menu.actions()]
    assert labels == [
        tr("simple.history.open"),
        tr("simple.history.show_folder"),
        tr("simple.menu.copy"),
        tr("simple.menu.use_prompt"),
        tr("simple.history.use_input"),
        tr("simple.history.delete"),
    ]
    menu.actions()[2].trigger()
    assert QApplication.clipboard().text() == shell.image.item(item_id).prompt
    confirm = shell.simple.history_menu(item_id, confirming=True)
    confirm_labels = [action.text() for action in confirm.actions()]
    assert confirm_labels == [
        tr("simple.history.delete.confirm"),
        tr("simple.history.delete"),
        tr("simple.history.keep"),
    ]
    assert confirm.actions()[0].isEnabled() is False
    kept = len(shell.image.history)
    confirm.actions()[2].trigger()
    assert len(shell.image.history) == kept
    prompt.setPlainText("other words")  # type: ignore[attr-defined]
    menu.actions()[3].trigger()
    app.processEvents()  # type: ignore[attr-defined]
    assert prompt.toPlainText() == shell.image.item(item_id).prompt  # type: ignore[attr-defined]
    shell.find("QPushButton", "chip-count-4").click()  # type: ignore[attr-defined]
    assert shell.image.presets["count"] == "4"
    prompt.setPlainText("four hills")  # type: ignore[attr-defined]
    test.keyClick(prompt, qt.Key_Return)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.image.phase is Phase.READY
    assert len(shell.image.history) == 5
    more.click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert len(shell.image.history) == 9
    assert shell.image.prompt == "four hills"
    saved = shell.image.snapshot()
    shell.run("view.toggle_expert_mode")
    app.processEvents()  # type: ignore[attr-defined]
    shell.run("view.toggle_expert_mode")
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.expert.enabled() is False
    assert shell.image.snapshot() == saved
    before = layout_json(shell.history.layout)
    steady = (
        ("QPlainTextEdit", "promptBox"),
        ("QPushButton", "generate"),
        ("QPushButton", "expertToggle"),
        ("QPushButton", "overflow"),
        ("QPushButton", "improvePrompt"),
        ("QPushButton", "revertPrompt"),
        ("QPushButton", "chip-size-1-1"),
        ("QPushButton", "chip-style-photo"),
        ("QPushButton", "chip-output-standard"),
        ("QPushButton", "chip-output-4k"),
        ("QPushButton", "unavailable-output-4k"),
        ("QPushButton", "chip-count-4"),
        ("QPushButton", "moreLike"),
        ("QPushButton", "savePicture"),
        ("QPushButton", "openFolder"),
        ("QPushButton", "usePrompt"),
        ("QLabel", "preset-size"),
        ("QLabel", "preset-count"),
        ("QScrollArea", "historyStrip"),
    )
    simple_boxes = _window_boxes(shell, steady)
    stage = shell.find("QFrame", "stage")
    result = shell.find("QLabel", "result")
    stage_box = _window_box(shell, stage)
    result_box = _window_box(shell, result)
    style_before = shell.widget.styleSheet()  # type: ignore[attr-defined]
    shell.run("view.toggle_expert_mode")
    app.processEvents()  # type: ignore[attr-defined]
    assert layout_json(shell.history.layout) == before
    assert shell.widget.styleSheet() == style_before  # type: ignore[attr-defined]
    after = _window_boxes(shell, steady)
    toggle_before = simple_boxes["expertToggle"]
    toggle_after = after["expertToggle"]
    overflow_before = simple_boxes["overflow"]
    overflow_after = after["overflow"]
    # The checked Expert pill paints a heavier face, so it grows left.
    # The overflow button beside it shifts by that same amount.
    delta = toggle_after[2] - toggle_before[2]
    assert delta >= 0
    assert toggle_after == (
        toggle_before[0] - delta,
        toggle_before[1],
        toggle_after[2],
        toggle_before[3],
    )
    assert overflow_after == (
        overflow_before[0] - delta,
        overflow_before[1],
        overflow_before[2],
        overflow_before[3],
    )
    for name, box in simple_boxes.items():
        if name in {"expertToggle", "overflow"}:
            continue
        assert after[name] == box, (name, box, after[name])
    stage_after = _window_box(shell, stage)
    result_after = _window_box(shell, result)
    assert stage_after[0] == stage_box[0] and stage_after[1] == stage_box[1]
    assert stage_after[3] == stage_box[3]
    assert stage_after[2] < stage_box[2]
    assert result_after[2] < result_box[2]
    panel = shell.find("QFrame", "expertPanel")
    assert panel is not None and panel.isVisible() is True  # type: ignore[attr-defined]
    assert panel.minimumWidth() == 220 and panel.maximumWidth() == 420  # type: ignore[attr-defined]
    assert panel.width() == 280  # type: ignore[attr-defined]
    panel_box = _window_box(shell, panel)
    assert panel_box[0] >= stage_after[0] + stage_after[2]
    split = shell.find("QSplitter", "expertSplit")
    assert split is not None
    # A 1200-wide window can be too narrow for a 420 panel once Windows
    # font metrics widen the stage. Give the splitter room, then request
    # sizes that already sum to its content width.
    shell.resize(1800, 900)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]

    def panel_width(width: int) -> None:
        handle = split.handleWidth()  # type: ignore[attr-defined]
        room = split.width() - handle  # type: ignore[attr-defined]
        split.setSizes([max(1, room - width), width])  # type: ignore[attr-defined]
        app.processEvents()  # type: ignore[attr-defined]

    panel_width(420)
    assert panel.width() == 420  # type: ignore[attr-defined]
    panel_width(220)
    assert panel.width() == 220  # type: ignore[attr-defined]
    shell.resize(1200, 900)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    panel_width(280)
    assert shell.find("QDoubleSpinBox", "shell.guidance").isVisible() is True  # type: ignore[attr-defined]
    assert shell.find("QSpinBox", "shell.seed").isVisible() is True  # type: ignore[attr-defined]
    assert shell.find("QLineEdit", "shell.addons").isVisible() is True  # type: ignore[attr-defined]
    assert shell.find("QComboBox", "shell.vram").isVisible() is True  # type: ignore[attr-defined]
    negative = shell.find("QPlainTextEdit", "shell.negative")
    assert negative is not None and negative.isVisible() is True  # type: ignore[attr-defined]
    assert negative.toolTip() == tr("setting.shell.negative.help")  # type: ignore[attr-defined]
    adult = shell.find("QCheckBox", "image.adult_content")
    assert adult is not None and adult.isChecked() is False and adult.isVisible() is True  # type: ignore[attr-defined]
    assert adult.toolTip() == tr("setting.image.adult_content.help")  # type: ignore[attr-defined]
    assert shell.find("QCheckBox", "image.content_filter") is None
    assert shell.find("QLineEdit", "shell.lora") is None
    assert shell.find("QLineEdit", "shell.save_folder") is None
    steps = shell.find("QSpinBox", "shell.steps")
    guidance = shell.find("QDoubleSpinBox", "shell.guidance")
    sampler = shell.find("QComboBox", "shell.sampler")
    resolution = shell.find("QPlainTextEdit", "shell.resolution")
    assert steps is not None and steps.value() == 20  # type: ignore[attr-defined]
    assert guidance is not None and guidance.value() == 7.0  # type: ignore[attr-defined]
    assert sampler is not None and sampler.currentData() == "euler"  # type: ignore[attr-defined]
    assert resolution is not None and resolution.toPlainText() == "1024x1024"  # type: ignore[attr-defined]
    negative = shell.image.get("shell.negative")
    shell.find("QPushButton", "chip-quality-fast").click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.image.get("shell.steps") == 12
    assert shell.image.get("shell.guidance") == 5.0
    assert shell.image.get("shell.sampler") == "euler"
    assert steps.value() == 12  # type: ignore[attr-defined]
    assert guidance.value() == 5.0  # type: ignore[attr-defined]
    assert shell.image.get("shell.negative") == negative
    shell.find("QPushButton", "chip-quality-balanced").click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert steps.value() == 20  # type: ignore[attr-defined]
    steps.setValue(24)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    changed = tr("simple.preset.changed", name=tr("simple.quality.balanced"))
    assert balanced.text() == changed  # type: ignore[attr-defined]
    reset = shell.find("QPushButton", "reset-quality")
    assert reset is not None and reset.isVisible() is True  # type: ignore[attr-defined]
    assert reset.text() == tr("simple.preset.reset", name=tr("simple.quality.balanced"))  # type: ignore[attr-defined]
    reset.click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert steps.value() == 20  # type: ignore[attr-defined]
    assert shell.image.get("shell.negative") == negative
    assert balanced.text() == tr("simple.quality.balanced")  # type: ignore[attr-defined]
    assert reset.isVisible() is False  # type: ignore[attr-defined]
    steps.setValue(24)  # type: ignore[attr-defined]
    guidance.setValue(3.0)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    custom = shell.find("QPushButton", "chip-quality-custom")
    assert custom is not None and custom.isVisible() is True and custom.isChecked() is True  # type: ignore[attr-defined]
    assert custom.text() == tr("simple.preset.custom")  # type: ignore[attr-defined]
    assert balanced.isChecked() is False  # type: ignore[attr-defined]
    reset.click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert custom.isVisible() is False  # type: ignore[attr-defined]
    assert balanced.isChecked() is True  # type: ignore[attr-defined]
    assert steps.value() == 20  # type: ignore[attr-defined]
    shell.find("QSpinBox", "shell.seed").setValue(11)  # type: ignore[attr-defined]
    prompt.setPlainText("seeded hill")  # type: ignore[attr-defined]
    test.keyClick(prompt, qt.Key_Return)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.image.history[0].seed == 11
    shell.find("QSpinBox", "shell.seed").setValue(3)  # type: ignore[attr-defined]
    shell.find("QPushButton", "usePrompt").click()  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.image.seed == 11
    assert shell.find("QSpinBox", "shell.seed").value() == 11  # type: ignore[attr-defined]
    assert shell.full_layout is False
    shell.set_full_layout(True)
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.full_layout is True
    assert shell._dock_host.isVisible() is True  # type: ignore[attr-defined]
    assert shell.simple.root.isVisible() is False  # type: ignore[attr-defined]
    shell.set_full_layout(False)
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.full_layout is False
    assert shell.expert.enabled() is True
    assert shell.simple.root.isVisible() is True  # type: ignore[attr-defined]
    assert layout_json(shell.history.layout) == before
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_expert(app: object) -> None:
    from PySide6.QtWidgets import QMenuBar

    from voxprint_common.i18n import set_language, tr
    from voxprint_common.suite_settings import set_expert_mode
    from voxprint_common.ui.dock import layout_json
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    set_expert_mode(False)
    shell = ReferenceShell()
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    assert "advanced" in layout_json(shell.history.layout)
    assert tr("shell.panel.advanced") not in _tab_titles(shell)
    assert tr("shell.panel.settings") not in _tab_titles(shell)
    assert shell.full_layout is False
    shell.set_full_layout(True)
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.full_layout is False
    chip = shell.find("QLabel", "expertChip")
    assert chip is not None and chip.isVisible() is False  # type: ignore[attr-defined]
    assert shell.find("QCheckBox", "image.adult_content") is None
    assert shell.find("QSpinBox", "shell.steps") is None
    count = shell.find("QPushButton", "chip-count-1")
    assert count is not None and count.isChecked() is True and count.toolTip() == ""  # type: ignore[attr-defined]
    assert shell.find("QSpinBox", "shell.pictures") is None
    shell.show_panel("settings")
    app.processEvents()  # type: ignore[attr-defined]
    before = layout_json(shell.history.layout)
    shell.run("view.toggle_expert_mode")
    app.processEvents()  # type: ignore[attr-defined]
    assert layout_json(shell.history.layout) == before
    assert chip.isVisible() is True  # type: ignore[attr-defined]
    assert chip.text() == tr("shell.status.expert")  # type: ignore[attr-defined]
    assert shell.full_layout is False
    panel = shell.find("QFrame", "expertPanel")
    assert panel is not None and panel.isVisible() is True  # type: ignore[attr-defined]
    assert tr("shell.panel.advanced") not in _tab_titles(shell)
    shell.set_full_layout(True)
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.full_layout is True
    assert tr("shell.panel.advanced") in _tab_titles(shell)
    assert tr("shell.panel.settings") in _tab_titles(shell)
    shell.set_full_layout(False)
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.full_layout is False
    adult = shell.find("QCheckBox", "image.adult_content")
    steps = shell.find("QSpinBox", "shell.steps")
    sampler = shell.find("QComboBox", "shell.sampler")
    assert adult is not None and adult.isChecked() is False  # type: ignore[attr-defined]
    assert adult.toolTip() == tr("setting.image.adult_content.help")  # type: ignore[attr-defined]
    assert steps is not None and steps.value() == 20  # type: ignore[attr-defined]
    assert steps.toolTip() == tr("setting.shell.steps.help")  # type: ignore[attr-defined]
    assert sampler is not None and sampler.currentText() == tr("setting.shell.sampler.euler")  # type: ignore[attr-defined]
    count = shell.find("QPushButton", "chip-count-1")
    assert count is not None and count.toolTip() == ""  # type: ignore[attr-defined]
    addons = shell.find("QLineEdit", "shell.addons")
    assert addons is not None and addons.toolTip() == tr("setting.shell.addons.help")  # type: ignore[attr-defined]
    memory = shell.find("QComboBox", "shell.vram")
    assert memory is not None and memory.currentText() == tr("setting.shell.vram.auto")  # type: ignore[attr-defined]
    bar = shell.widget.findChild(QMenuBar)
    assert bar is not None
    view = next(action.menu() for action in bar.actions() if action.text() == tr("shell.menu.view"))
    labels = [action.text().split("\t")[0] for action in view.actions()]
    assert tr("command.view.toggle_expert_mode") in labels
    shell.open_palette()
    shell._query.setText("Expert")
    app.processEvents()  # type: ignore[attr-defined]
    ids = [shell._hits.item(row).data(shell._qt.UserRole) for row in range(shell._hits.count())]
    assert "view.toggle_expert_mode" in ids
    shell.run("view.toggle_expert_mode")
    app.processEvents()  # type: ignore[attr-defined]
    assert layout_json(shell.history.layout) == before
    assert chip.isVisible() is False  # type: ignore[attr-defined]
    hidden_adult = shell.find("QCheckBox", "image.adult_content")
    assert hidden_adult is None or hidden_adult.isVisible() is False  # type: ignore[attr-defined]
    assert tr("shell.panel.advanced") not in _tab_titles(shell)
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]

    for language in _LOCALES:
        set_language(language)
        set_expert_mode(False)
        local = ReferenceShell()
        local.resize(900, 600)
        local.show()
        app.processEvents()  # type: ignore[attr-defined]
        overflow = local.find("QPushButton", "overflow")
        assert overflow is not None  # type: ignore[attr-defined]
        overflow_labels = [action.text() for action in overflow.menu().actions()]  # type: ignore[attr-defined]
        assert overflow_labels == _overflow_labels()
        toggle = local.find("QPushButton", "expertToggle")
        assert toggle is not None  # type: ignore[attr-defined]
        assert toggle.text() == tr("command.view.toggle_expert_mode")  # type: ignore[attr-defined]
        assert local.find("QPushButton", "moreLike").text() == tr("simple.more")  # type: ignore[attr-defined]
        assert local.find("QPushButton", "generate").text() == tr("simple.generate")  # type: ignore[attr-defined]
        local.close()
        app.processEvents()  # type: ignore[attr-defined]


def _window_box(shell: object, widget: object) -> tuple[int, int, int, int]:
    from PySide6.QtCore import QPoint

    origin = widget.mapTo(shell.widget, QPoint(0, 0))  # type: ignore[attr-defined]
    return (origin.x(), origin.y(), widget.width(), widget.height())  # type: ignore[attr-defined]


def _window_boxes(
    shell: object, names: tuple[tuple[str, str], ...]
) -> dict[str, tuple[int, int, int, int]]:
    boxes: dict[str, tuple[int, int, int, int]] = {}
    for type_name, name in names:
        widget = shell.find(type_name, name)  # type: ignore[attr-defined]
        assert widget is not None and widget.isVisible()  # type: ignore[attr-defined]
        boxes[name] = _window_box(shell, widget)
    return boxes


def _tab_titles(shell: object) -> set[str]:
    titles: set[str] = set()
    bars = shell.widget.findChildren(shell._widgets.QTabBar)  # type: ignore[attr-defined]
    for bar in bars:
        if not bar.isVisible():
            continue
        for index in range(bar.count()):
            titles.add(bar.tabText(index))
    return titles


def _check_toolbar(app: object, language: str) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language(language)
    shell = ReferenceShell(full_layout=True)
    shell.resize(1024, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    buttons = [
        button
        for button in shell._toolbar.findChildren(shell._widgets.QPushButton)
        if button.isVisible()
    ]
    assert buttons, language
    labels: set[str] = set()
    for button in buttons:
        text = button.text()
        labels.add(text)
        assert "\u2026" not in text, (language, text)
        assert "..." not in text, (language, text)
        advance = button.fontMetrics().horizontalAdvance(text)
        inner = button.contentsRect().width()
        assert advance <= inner + 1, (language, text, advance, inner, button.width())
        assert button.geometry().right() <= shell._toolbar.width() + 1, (language, text)
    if language == "de":
        for needed in _GERMAN_TOOLS:
            assert needed in labels, labels
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_palette(app: object) -> None:
    from PySide6.QtWidgets import QLabel, QWidget

    from voxprint_common.i18n import set_language, tr
    from voxprint_common.suite_settings import set_expert_mode
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    set_expert_mode(False)
    shell = ReferenceShell()
    shell.resize(1100, 700)
    shell.show()
    shell.open_palette()
    shell._query.setText("pa")
    app.processEvents()  # type: ignore[attr-defined]
    titles = shell.widget.findChildren(QLabel, "paletteTitle")
    assert any("font-weight: 600" in title.text() for title in titles)
    assert any("<b>" in title.text() for title in titles)
    rows = shell.widget.findChildren(QWidget, "paletteRow")
    selected = [row for row in rows if row.property("selected") == "true"]
    assert len(selected) == 1
    groups = {label.text() for label in shell.widget.findChildren(QLabel, "paletteGroup")}
    allowed = {tr("shell.group.file"), tr("shell.group.edit"), tr("shell.group.panels")}
    assert groups and groups <= allowed
    chips = shell.widget.findChildren(QLabel, "paletteChip")
    assert chips
    for chip in chips:
        row = chip.parent()
        title = row.findChild(QLabel, "paletteTitle")
        assert title is not None
        assert chip.x() > title.x()
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_split_undo(
    app: object,
    point: object,
    event_type: object,
    pointf: object,
    qt: object,
    mouse_event: object,
    application: object,
) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    splitter = shell.find("QSplitter", "rootSplit")
    assert splitter is not None
    handle = splitter.handle(1)  # type: ignore[attr-defined]
    before = shell.history.layout.root.size.value  # type: ignore[union-attr]
    assert shell.history.can_undo is False
    center = handle.rect().center()
    press = mouse_event(
        event_type.Type.MouseButtonPress,  # type: ignore[attr-defined]
        pointf(center),  # type: ignore[operator]
        pointf(handle.mapToGlobal(center)),  # type: ignore[attr-defined]
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.KeyboardModifier.NoModifier,  # type: ignore[attr-defined]
    )
    application.sendEvent(handle, press)  # type: ignore[attr-defined]
    moved = center + point(180, 0)  # type: ignore[operator]
    move = mouse_event(
        event_type.Type.MouseMove,  # type: ignore[attr-defined]
        pointf(moved),  # type: ignore[operator]
        pointf(handle.mapToGlobal(moved)),  # type: ignore[attr-defined]
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.KeyboardModifier.NoModifier,  # type: ignore[attr-defined]
    )
    application.sendEvent(handle, move)  # type: ignore[attr-defined]
    release = mouse_event(
        event_type.Type.MouseButtonRelease,  # type: ignore[attr-defined]
        pointf(moved),  # type: ignore[operator]
        pointf(handle.mapToGlobal(moved)),  # type: ignore[attr-defined]
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.MouseButton.NoButton,  # type: ignore[attr-defined]
        qt.KeyboardModifier.NoModifier,  # type: ignore[attr-defined]
    )
    application.sendEvent(handle, release)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert len(shell.history._undo) == 1, (splitter.sizes(), before)  # type: ignore[attr-defined]
    after = shell.history.layout.root.size.value  # type: ignore[union-attr]
    assert abs(after - before) >= 0.002, (before, after, splitter.sizes())  # type: ignore[attr-defined]
    shell.run("edit.undo")
    app.processEvents()  # type: ignore[attr-defined]
    restored = shell.history.layout.root.size.value  # type: ignore[union-attr]
    assert abs(restored - before) < 1e-9
    assert shell.history.can_undo is False
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_overflow(app: object) -> None:
    from voxprint_common.i18n import set_language, tr
    from voxprint_common.ui.shell import ReferenceShell

    set_language("de")
    commands = (
        tr("command.file.save"),
        tr("command.edit.undo"),
        tr("command.edit.redo"),
        tr("command.view.palette"),
    )
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    shell._toolbar_available = 280
    shell._fill_toolbar()
    app.processEvents()  # type: ignore[attr-defined]
    buttons = [
        button
        for button in shell._toolbar.findChildren(shell._widgets.QPushButton)
        if button.isVisible()
    ]
    more = next((button for button in buttons if button.menu() is not None), None)
    assert more is not None
    menu_labels = [action.text() for action in more.menu().actions()]
    visible = [button.text() for button in buttons if button.menu() is None]
    missing = [label for label in commands if label not in visible]
    assert len(missing) >= 2, (visible, menu_labels)
    assert menu_labels == missing, (menu_labels, missing)
    assert more.width() + 1 >= more.sizeHint().width(), (more.width(), more.sizeHint().width())
    limit = shell._toolbar_available
    assert more.geometry().right() <= limit + 1
    for button in buttons:
        text = button.text()
        assert "\u2026" not in text and "..." not in text
        advance = button.fontMetrics().horizontalAdvance(text)
        assert advance <= button.contentsRect().width() + 1, (text, advance, button.width())
        assert button.geometry().right() <= limit + 1
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_focus_canvas(app: object, qt: object, test: object) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    shell.widget.setFocus()
    app.processEvents()  # type: ignore[attr-defined]
    spec = shell.registry.get("view.focus_canvas")
    assert spec is not None and spec.when == "global"
    shell._activate("prompt")
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.active == "prompt"
    test.keyClick(shell.widget, qt.Key_1, qt.ControlModifier)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.active == "canvas"
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_close_retarget(app: object, qt: object, test: object) -> None:
    from voxprint_common.i18n import set_language, tr
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    shell.widget.setFocus()
    shell._activate("prompt")
    app.processEvents()  # type: ignore[attr-defined]
    test.keyClick(shell.widget, qt.Key_W, qt.ControlModifier)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert not shell.history.layout.contains("prompt")
    assert shell.active == "layers"
    status = shell.find("QLabel", "status")
    assert status is not None
    assert status.text() != tr("shell.status.protected")  # type: ignore[attr-defined]
    test.keyClick(shell.widget, qt.Key_W, qt.ControlModifier)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert not shell.history.layout.contains("layers")
    assert shell.active == "canvas"
    assert shell.history.layout.contains("canvas")
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_split_geometry(app: object) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    inner = shell.find("QSplitter", "dockSplit")
    root = shell.find("QSplitter", "rootSplit")
    assert inner is not None and root is not None
    assert abs(inner.sizes()[1] - 260) <= 1, inner.sizes()  # type: ignore[attr-defined]
    assert inner.widget(0).minimumWidth() >= 140  # type: ignore[attr-defined]
    assert inner.widget(1).minimumWidth() >= 140  # type: ignore[attr-defined]
    assert root.widget(0).minimumWidth() >= 160  # type: ignore[attr-defined]
    assert root.widget(1).minimumWidth() >= 160  # type: ignore[attr-defined]
    inner.moveSplitter(inner.width(), 1)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert inner.sizes()[1] >= 140, inner.sizes()  # type: ignore[attr-defined]
    shell.resize(1280, 700)
    app.processEvents()  # type: ignore[attr-defined]
    assert abs(inner.sizes()[1] - 260) <= 1, inner.sizes()  # type: ignore[attr-defined]
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_split_click(
    app: object,
    point: object,
    event_type: object,
    pointf: object,
    qt: object,
    mouse_event: object,
    application: object,
) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.dock import SizeMode
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    root = shell.history.layout.root
    inner = root.second  # type: ignore[union-attr]
    assert root.size.mode is SizeMode.RATIO  # type: ignore[union-attr]
    assert inner.size.mode is SizeMode.FIXED_SECOND  # type: ignore[union-attr]
    ratio = root.size.value  # type: ignore[union-attr]
    fixed = inner.size.value  # type: ignore[union-attr]
    for name in ("rootSplit", "dockSplit"):
        splitter = shell.find("QSplitter", name)
        assert splitter is not None
        _click_handle(splitter, point, event_type, pointf, qt, mouse_event, application)
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.history.can_undo is False
    assert shell.history.layout.root.size.mode is SizeMode.RATIO  # type: ignore[union-attr]
    assert shell.history.layout.root.size.value == ratio  # type: ignore[union-attr]
    assert shell.history.layout.root.second.size.mode is SizeMode.FIXED_SECOND  # type: ignore[union-attr]
    assert shell.history.layout.root.second.size.value == fixed  # type: ignore[union-attr]
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _click_handle(
    splitter: object,
    point: object,
    event_type: object,
    pointf: object,
    qt: object,
    mouse_event: object,
    application: object,
) -> None:
    handle = splitter.handle(1)  # type: ignore[attr-defined]
    center = handle.rect().center()
    local = pointf(center)  # type: ignore[operator]
    global_pos = pointf(handle.mapToGlobal(center))  # type: ignore[attr-defined]
    press = mouse_event(
        event_type.Type.MouseButtonPress,  # type: ignore[attr-defined]
        local,
        global_pos,
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.KeyboardModifier.NoModifier,  # type: ignore[attr-defined]
    )
    application.sendEvent(handle, press)  # type: ignore[attr-defined]
    release = mouse_event(
        event_type.Type.MouseButtonRelease,  # type: ignore[attr-defined]
        local,
        global_pos,
        qt.MouseButton.LeftButton,  # type: ignore[attr-defined]
        qt.MouseButton.NoButton,  # type: ignore[attr-defined]
        qt.KeyboardModifier.NoModifier,  # type: ignore[attr-defined]
    )
    application.sendEvent(handle, release)  # type: ignore[attr-defined]


def _check_tab_close(app: object, qt: object, test: object) -> None:
    from voxprint_common.i18n import set_language, tr
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    side = shell._widgets.QTabBar.ButtonPosition.RightSide
    bars = shell.widget.findChildren(shell._widgets.QTabBar)
    inactive = None
    for bar in bars:
        for index in range(bar.count()):
            button = bar.tabButton(index, side)
            if bar.tabText(index) == tr("shell.panel.canvas"):
                assert button is None
                continue
            assert button is not None
            if index == bar.currentIndex():
                assert button.isVisible()
            else:
                assert not button.isVisible()
                inactive = (bar, index, button)
    assert inactive is not None
    bar, index, button = inactive
    test.mouseMove(bar, bar.tabRect(index).center())  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert button.isVisible()
    current = bar.currentIndex()
    test.mouseMove(bar, bar.tabRect(current).center())  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert button.isVisible() is False
    assert bar.tabButton(current, side).isVisible()
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QHoverEvent
    from PySide6.QtWidgets import QApplication

    outside = QPointF(-20, -20)
    leave = QHoverEvent(
        QEvent.Type.HoverLeave,
        outside,
        outside,
        QPointF(bar.tabRect(index).center()),
    )
    QApplication.sendEvent(bar, leave)
    app.processEvents()  # type: ignore[attr-defined]
    assert button.isVisible() is False
    assert bar.tabButton(current, side).isVisible()
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_split_colour(app: object, accent: str) -> None:
    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    shell = ReferenceShell(full_layout=True)
    shell.resize(1100, 700)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    splitter = shell._root_splitter()
    handle = splitter.handle(1)  # type: ignore[attr-defined]
    line = handle.findChild(shell._widgets.QFrame, "splitLine")
    assert line is not None and line.width() == 1 and line.height() > 20
    shell._set_dragging(splitter, True)
    app.processEvents()  # type: ignore[attr-defined]
    assert line.width() >= 3 and line.height() > 20
    image = shell.grab().toImage()  # type: ignore[attr-defined]
    point = line.mapTo(shell.widget, line.rect().center())
    colour = image.pixelColor(point).name().lower()
    assert colour == accent.lower(), (colour, accent, point.x(), point.y())
    for dx in (-1, 1):
        side = image.pixelColor(point.x() + dx, point.y()).name().lower()
        assert side == accent.lower(), (dx, side, accent)
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _painted_button_font(button: object) -> object:
    """Return the font the stylesheet actually draws for this button."""
    from PySide6.QtGui import QFont

    font = QFont(button.font())  # type: ignore[attr-defined]
    name = str(button.objectName())  # type: ignore[attr-defined]
    checked = bool(button.isChecked())  # type: ignore[attr-defined]
    bold = name in {"generate", "primary"} or (
        checked and (name == "expertToggle" or button.property("chip") == "true")  # type: ignore[attr-defined]
    )
    if bold:
        font.setWeight(QFont.Weight.DemiBold)
    if name == "generate":
        font.setPixelSize(16)
    return font


def _combo_usable_width(combo: object) -> int:
    from PySide6.QtWidgets import QStyle, QStyleOptionComboBox

    option = QStyleOptionComboBox()
    combo.initStyleOption(option)  # type: ignore[attr-defined]
    rect = combo.style().subControlRect(  # type: ignore[attr-defined]
        QStyle.ComplexControl.CC_ComboBox,
        option,
        QStyle.SubControl.SC_ComboBoxEditField,
        combo,  # type: ignore[arg-type]
    )
    return max(rect.width() - 2, 0)


def _near_white(widget: object) -> bool:
    image = widget.grab().toImage()  # type: ignore[attr-defined]
    color = image.pixelColor(max(image.width() // 2, 0), max(image.height() // 2, 0))
    return min(color.red(), color.green(), color.blue()) >= 220


def _check_viewports(shell: object) -> None:
    """A scroll area the app creates uses the token palette, not Qt's grey base."""
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication, QScrollArea, QTextEdit, QWidget

    from voxprint_common.ui.theme import TEXT_DISABLED, apply_shell_palette, palette_colors

    role = QPalette.ColorRole
    group = QPalette.ColorGroup
    area = QScrollArea(shell.widget)  # type: ignore[attr-defined]
    area.setObjectName("appScroll")
    host = QWidget()
    area.setWidget(host)
    QApplication.processEvents()
    dark = palette_colors()
    base = area.viewport().palette().color(role.Base).name()
    assert base == QColor(dark["Base"]).name()
    assert base != "#efefef"
    application = QApplication.palette()
    assert application.color(role.Window).name() == QColor(dark["Window"]).name()
    assert application.color(role.Text).name() == QColor(dark["Text"]).name()
    disabled = application.color(group.Disabled, role.Text).name()
    assert disabled == QColor(TEXT_DISABLED).name()
    loose = QWidget()
    apply_shell_palette(loose, light=True)
    light_area = QScrollArea(loose)
    light_area.setWidget(QWidget())
    light_base = light_area.viewport().palette().color(role.Base).name()
    assert light_base == QColor(palette_colors(light=True)["Base"]).name()
    assert light_base != "#efefef"
    top = QScrollArea()
    top.setWidget(QWidget())
    top_base = top.viewport().palette().color(role.Base).name()
    assert top_base == QColor(dark["Base"]).name()
    edit = QTextEdit(shell.widget)  # type: ignore[attr-defined]
    edit.resize(120, 80)
    edit.show()
    area.resize(120, 80)
    area.show()
    QApplication.processEvents()
    assert not _near_white(edit.viewport())
    assert not _near_white(area.viewport())


def _check_busy_generate(app: object) -> None:
    """Generate becomes busy, and a second click does not start another job."""
    from PySide6.QtWidgets import QPushButton

    from voxprint_common.ui.shell import ReferenceShell
    from voxprint_common.ui.simple_image import Phase

    shell = ReferenceShell(app="canvas", state_dir=Path(sys.argv[1]) / "chip-state" / "busy")
    shell.resize(1200, 900)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    shell.simple.set_prompt_text("a red door")
    button = shell.widget.findChild(QPushButton, "generate")
    assert button is not None and button.isEnabled()
    before = shell._picture_count
    button.click()
    assert shell.image.phase is Phase.BUSY
    assert button.isEnabled() is False
    button.click()
    assert shell._picture_count == before
    assert shell.image.phase is Phase.BUSY
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_source_drop(app: object) -> None:
    """The source drop is absent until the app turns that slot on."""
    from PySide6.QtWidgets import QLabel

    from voxprint_common.ui.presets import reference_presets
    from voxprint_common.ui.safety import ReviewedOutput
    from voxprint_common.ui.settings import reference_settings
    from voxprint_common.ui.simple_image import SimpleImageLayout, SimpleImageWidget

    class _Gate:
        def review_prompt(self, prompt: str) -> str | None:
            del prompt
            return None

        def review_result(self, prompt: str, item_id: str, output: ReviewedOutput) -> str | None:
            del prompt, item_id, output
            return None

    settings = reference_settings()
    hidden = SimpleImageLayout(
        presets=reference_presets(settings),
        settings=settings,
        gate=_Gate(),
    )
    plain = SimpleImageWidget(hidden, settings)
    plain.root.show()
    app.processEvents()  # type: ignore[attr-defined]
    assert plain.root.findChild(QLabel, "sourceDrop") is None
    plain.root.close()

    shown_settings = reference_settings()
    shown = SimpleImageLayout(
        presets=reference_presets(shown_settings),
        settings=shown_settings,
        gate=_Gate(),
        source_picture=True,
        mask=True,
    )
    view = SimpleImageWidget(shown, shown_settings)
    view.root.show()
    app.processEvents()  # type: ignore[attr-defined]
    drop = view.root.findChild(QLabel, "sourceDrop")
    assert drop is not None and drop.isVisible()
    assert drop.text() == "Add a picture"
    view.root.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_suffix(app: object) -> None:
    """A unit is painted on the expert spin and on the simple value label."""
    from PySide6.QtWidgets import QLabel, QSpinBox

    from voxprint_common.i18n import set_language
    from voxprint_common.ui.settings import (
        UNIT_DB,
        SettingSpec,
        SettingType,
        format_setting_value,
    )
    from voxprint_common.ui.simple_image import SimpleImageLayout, SimpleImageWidget
    from voxprint_common.ui.variants import settings_for, surface_for, variant_presets

    surface = surface_for("audiobook-builder")
    settings = settings_for(surface)
    likeness = SettingSpec(
        "demo.likeness",
        "setting.audio.loudness",
        "setting.audio.loudness.help",
        SettingType.INT,
        50,
        expert=True,
        group="audio",
        minimum=0,
        maximum=100,
        suffix_key="%",
    )
    level = SettingSpec(
        "demo.level",
        "setting.audio.loudness",
        "setting.audio.loudness.help",
        SettingType.FLOAT,
        -16.0,
        group="audio",
        minimum=-24,
        maximum=0,
        suffix_key=UNIT_DB,
    )
    settings.add(likeness)
    settings.add(level)
    layout = SimpleImageLayout(
        presets=variant_presets(surface, settings),
        settings=settings,
        variant=surface.variant,
    )
    view = SimpleImageWidget(layout, settings, surface=surface)
    view.root.resize(900, 700)
    view.root.show()
    view.set_expert(True)
    app.processEvents()  # type: ignore[attr-defined]
    spin = view.root.findChild(QSpinBox, "demo.likeness")
    assert spin is not None
    assert spin.suffix() == "%"
    assert spin.text() == "50%"
    label = view.root.findChild(QLabel, "value-demo.level")
    assert label is not None and label.isVisible()
    assert label.text() == format_setting_value(level, -16.0) == "-16.0 dB"
    set_language("ru")
    view.refresh()
    view.set_expert(True)
    app.processEvents()  # type: ignore[attr-defined]
    label = view.root.findChild(QLabel, "value-demo.level")
    spin = view.root.findChild(QSpinBox, "demo.likeness")
    assert label is not None and spin is not None
    assert label.text() == "-16.0 дБ"
    assert spin.suffix() == "%"
    set_language("en")
    view.root.close()
    app.processEvents()  # type: ignore[attr-defined]


def _check_dub_surface(app: object) -> None:
    """Movie Dubber previews and plays a video, and does not append a narrator."""
    from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QWidget

    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    root = Path(sys.argv[1]) / "chip-state"
    book = ReferenceShell(app="audiobook-builder", state_dir=root / "book")
    book.resize(1200, 900)
    book.show()
    app.processEvents()  # type: ignore[attr-defined]
    assert book.widget.findChild(QComboBox, "voicePicker") is not None
    assert book.widget.findChild(QWidget, "videoResult") is None
    book.simple.set_prompt_text("book.epub")
    assert book.simple._input_text().startswith("book.epub · ")
    book.close()
    app.processEvents()  # type: ignore[attr-defined]

    set_language("ru")
    shell = ReferenceShell(app="movie-dubber", state_dir=root / "dub")
    shell.resize(1200, 900)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.widget.findChild(QComboBox, "voicePicker") is None
    assert shell.widget.findChild(QLabel, "voiceLabel") is None
    shell.simple.set_prompt_text("/films/тихий-фильм.mp4")
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.simple._input_text() == "/films/тихий-фильм.mp4"
    poster = shell.widget.findChild(QLabel, "videoPoster")
    path = shell.widget.findChild(QLabel, "videoPath")
    opener = shell.widget.findChild(QPushButton, "videoOpen")
    region = shell.widget.findChild(QWidget, "videoResult")
    assert region is not None and poster is not None and path is not None and opener is not None
    assert region.isVisible() and poster.isVisible()
    assert path.text() == "/films/тихий-фильм.mp4"
    assert "\u2026" not in path.text()
    shell.show_result("тихий-фильм.mp4")
    app.processEvents()  # type: ignore[attr-defined]
    assert path.text() == "/films/тихий-фильм/dubbed.mp4"
    assert poster.isVisible()
    play = shell.widget.findChild(QPushButton, "videoPlay")
    if shell.simple._media is None:
        assert play is None or not play.isVisible()
    else:
        assert play is not None and play.isVisible()
        assert play.text() == "Воспроизвести"
        assert not shell.simple._video_is_playing()
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]
    set_language("en")


def _check_chip_reflow(app: object) -> None:
    """A chip and the Expert toggle follow a new label, language, or checked state."""
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QPushButton

    from voxprint_common.i18n import set_language, tr
    from voxprint_common.ui.shell import ReferenceShell
    from voxprint_common.ui.simple_image import _chip_width, _fit_chip
    from voxprint_common.ui.tokens import load

    pad = load().density.chip_pad_px
    set_language("ru")
    shell = ReferenceShell(app="canvas")
    shell.resize(1200, 900)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    toggle = shell.widget.findChild(QPushButton, "expertToggle")
    assert toggle is not None
    assert toggle.text() == "Режим эксперта"
    assert toggle.minimumWidth() == _chip_width(toggle)

    def side(button: QPushButton) -> int:
        advance = QFontMetrics(_painted_button_font(button)).horizontalAdvance(button.text())  # type: ignore[arg-type]
        return (button.width() - advance) // 2

    shell.simple.set_expert(True)
    app.processEvents()  # type: ignore[attr-defined]
    assert toggle.isChecked()
    assert toggle.minimumWidth() == _chip_width(toggle)
    assert side(toggle) >= pad
    shell.simple.set_expert(False)
    app.processEvents()  # type: ignore[attr-defined]
    assert toggle.isChecked() is False
    assert toggle.minimumWidth() == _chip_width(toggle)
    chips = [
        button
        for button in shell.widget.findChildren(QPushButton)
        if button.property("chip") == "true" and button.isVisible() and button.text()
    ]
    assert chips
    chip = chips[0]
    for label in ("Голоса актёров", "Оставить голоса актёров"):
        chip.setText(label)
        chip.setChecked(True)
        _fit_chip(chip)
        app.processEvents()  # type: ignore[attr-defined]
        assert chip.minimumWidth() == _chip_width(chip)
        assert side(chip) >= pad
        assert label in chip.text()
    set_language("en")
    shell.simple.refresh()
    app.processEvents()  # type: ignore[attr-defined]
    assert toggle.text() == tr("command.view.toggle_expert_mode")
    assert toggle.minimumWidth() == _chip_width(toggle)
    assert side(toggle) >= pad
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]
    set_language("en")


def _check_chip_fit(app: object) -> None:
    """Labels stay inside chips, the expert toggle, buttons, and combos.

    A combo may elide with an ellipsis when the panel is narrower than its
    longest item. The full value stays on the tooltip and on ``currentText``.
    """
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QComboBox, QPushButton

    from voxprint_common.i18n import set_language
    from voxprint_common.suite_settings import set_expert_mode
    from voxprint_common.ui.shell import _MODIFIED, ReferenceShell
    from voxprint_common.ui.simple_image import _chip_width
    from voxprint_common.ui.tokens import load

    pad = load().density.chip_pad_px
    _check_busy_generate(app)
    _check_source_drop(app)

    surfaces = (
        "canvas",
        "audiobook-builder",
        "movie-dubber",
        "clarity",
        "plotweaver",
        "model-forge",
        "viewer",
    )
    _check_suffix(app)
    _check_dub_surface(app)
    for language in ("en", "ru", "de", "uk", "lv"):
        for app_id in surfaces:
            set_language(language)
            set_expert_mode(False)
            shell = ReferenceShell(app=app_id)
            shell.resize(1200, 900)
            shell.show()
            app.processEvents()  # type: ignore[attr-defined]
            key, value = _MODIFIED[app_id]
            shell.image.set_value(key, value)
            shell.simple.refresh()
            shell.simple.set_expert(True)
            app.processEvents()  # type: ignore[attr-defined]
            assert shell.widget.width() == 1200, (language, app_id, shell.widget.width())
            assert shell.widget.height() == 900, (language, app_id, shell.widget.height())
            if language == "en" and app_id == "plotweaver":
                _check_viewports(shell)
            for button in shell.widget.findChildren(QPushButton):
                if not button.isVisible():
                    continue
                text = button.text()
                if not text:
                    continue
                font = _painted_button_font(button)
                advance = QFontMetrics(font).horizontalAdvance(text)  # type: ignore[arg-type]
                pill = button.objectName() == "expertToggle" or button.property("chip") == "true"
                if pill:
                    assert button.minimumWidth() == _chip_width(button), (
                        language,
                        app_id,
                        button.objectName(),
                        text,
                        button.minimumWidth(),
                        _chip_width(button),
                    )
                    side = (button.width() - advance) // 2
                    assert side >= pad, (
                        language,
                        app_id,
                        button.objectName(),
                        text,
                        advance,
                        button.width(),
                        side,
                        pad,
                    )
                    continue
                contents = button.contentsRect().width()
                assert advance <= contents, (
                    language,
                    app_id,
                    button.objectName(),
                    text,
                    advance,
                    contents,
                )
            for combo in shell.widget.findChildren(QComboBox):
                if not combo.isVisible():
                    continue
                combo.repaint()
                text = combo.currentText()
                if not text:
                    continue
                usable = _combo_usable_width(combo)
                advance = QFontMetrics(combo.font()).horizontalAdvance(text)
                fitted = combo.property("fittedText")
                assert isinstance(fitted, str), (language, app_id, combo.objectName(), text)
                if advance <= usable:
                    assert fitted == text, (language, app_id, text, fitted, usable)
                    continue
                assert fitted.endswith("…") and fitted != text, (
                    language,
                    app_id,
                    combo.objectName(),
                    text,
                    fitted,
                    advance,
                    usable,
                )
                assert QFontMetrics(combo.font()).horizontalAdvance(fitted) <= usable
                assert text in combo.toolTip(), (language, app_id, text, combo.toolTip())
                assert combo.currentText() == text
            shell.close()
            app.processEvents()  # type: ignore[attr-defined]
    _check_chip_reflow(app)
    _check_pill_wrap(app)


def _check_pill_wrap(app: object) -> None:
    """A long pill wraps onto a second row instead of clipping, in Russian and German."""
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QPushButton, QWidget

    from voxprint_common.i18n import set_language
    from voxprint_common.ui.shell import ReferenceShell
    from voxprint_common.ui.simple_image import _chip_width, _fit_chip
    from voxprint_common.ui.tokens import load

    label = "Оставить язык книги"
    pad = load().density.chip_pad_px
    for language in ("ru", "de"):
        set_language(language)
        shell = ReferenceShell(app="canvas")
        shell.resize(1200, 900)
        shell.show()
        app.processEvents()  # type: ignore[attr-defined]
        chips = [
            button
            for button in shell.widget.findChildren(QPushButton)
            if button.property("chip") == "true" and button.isVisible() and button.text()
        ]
        assert len(chips) >= 4
        for chip in chips:
            chip.setChecked(False)
            chip.setText(label)
            _fit_chip(chip)
        shell.resize(1190, 900)
        shell.resize(1200, 900)
        app.processEvents()  # type: ignore[attr-defined]
        wrapped = False
        for host in shell.widget.findChildren(QWidget, "pillRow"):
            buttons = [
                button
                for button in host.findChildren(QPushButton)
                if button.isVisible() and button.text() == label
            ]
            if len({button.y() for button in buttons}) >= 2:
                wrapped = True
        assert wrapped, language
        for chip in chips:
            if not chip.isVisible():
                continue
            advance = QFontMetrics(_painted_button_font(chip)).horizontalAdvance(label)  # type: ignore[arg-type]
            assert chip.text() == label
            assert chip.minimumWidth() == _chip_width(chip)
            assert chip.width() >= advance, (language, chip.width(), advance)
            assert (chip.width() - advance) // 2 >= pad, (language, chip.text(), chip.width())
        shell.close()
        app.processEvents()  # type: ignore[attr-defined]
    set_language("en")


def _check_audiobook(app: object) -> None:
    """Voices, the book path, pause and resume, and the audio player."""
    from PySide6.QtCore import QMimeData, Qt, QUrl
    from PySide6.QtGui import QDropEvent

    from voxprint_common.i18n import set_language
    from voxprint_common.ui.safety import ReviewedOutput
    from voxprint_common.ui.shell import ReferenceShell

    set_language("en")
    root = Path(sys.argv[1]) / "audio-state"
    plain = ReferenceShell(app="audiobook-builder", state_dir=root / "plain")
    plain.resize(1200, 900)
    plain.show()
    app.processEvents()  # type: ignore[attr-defined]
    plain.simple.set_prompt_text("book.epub")
    assert plain.image.generate() is True
    plain.simple.refresh()
    app.processEvents()  # type: ignore[attr-defined]
    assert plain.find("QPushButton", "pause") is None
    assert plain.find("QPushButton", "resume") is None
    plain.image.cancel()
    bars = ReferenceShell(app="audiobook-builder", state_dir=root / "bars")
    bars.resize(1200, 900)
    bars.show()
    bars.simple.set_prompt_text("book.epub")
    assert bars.image.generate() is True
    finished = bars.image.finish(
        "bars-only",
        ReviewedOutput(data=b"wav", mime="audio/wav"),
    )
    assert finished is not None
    bars.simple.refresh()
    app.processEvents()  # type: ignore[attr-defined]
    waveform = bars.find("QLabel", "player")
    deck = bars.find("QWidget", "audioDeck")
    assert waveform is not None and waveform.isVisible()  # type: ignore[attr-defined]
    assert deck is not None and deck.isVisible() is False  # type: ignore[attr-defined]
    bars.close()
    plain.close()
    paused: list[str] = []
    shell = ReferenceShell(
        app="audiobook-builder",
        state_dir=root / "player",
        on_pause=lambda: paused.append("pause"),
        on_resume=lambda: paused.append("resume"),
    )
    shell.resize(1200, 900)
    shell.show()
    app.processEvents()  # type: ignore[attr-defined]
    shell.simple.set_prompt_text("book.epub")
    assert shell.image.generate() is True
    shell.simple.refresh()
    app.processEvents()  # type: ignore[attr-defined]
    pause = shell.find("QPushButton", "pause")
    resume = shell.find("QPushButton", "resume")
    assert pause is not None and pause.isEnabled() and pause.isVisible()  # type: ignore[attr-defined]
    assert resume is not None and resume.isEnabled() and resume.isVisible()  # type: ignore[attr-defined]
    pause.click()  # type: ignore[attr-defined]
    resume.click()  # type: ignore[attr-defined]
    assert paused == ["pause", "resume"]
    shell.image.cancel()
    shell.simple.set_voices((("anya", "Anya"), ("boris", "Boris")))
    app.processEvents()  # type: ignore[attr-defined]
    voice = shell.find("QComboBox", "voicePicker")
    assert voice is not None
    assert voice.currentData() == "anya"  # type: ignore[attr-defined]
    assert voice.currentText() == "Anya"  # type: ignore[attr-defined]
    voice.setCurrentIndex(1)  # type: ignore[attr-defined]
    app.processEvents()  # type: ignore[attr-defined]
    assert shell.image.voice_id == "boris"
    assert shell.image.live_settings()["shell.voice"] == "boris"
    drop = shell.find("QLabel", "fileDrop")
    assert drop is not None
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("/tmp/quiet-chapter.epub")])
    dropped = QDropEvent(
        drop.rect().center(),  # type: ignore[attr-defined]
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert shell.simple._drop_filter.eventFilter(drop, dropped) is True  # type: ignore[attr-defined]
    stored = shell.simple.input_path()
    assert stored.endswith("quiet-chapter.epub")
    assert Path(stored).is_absolute()
    assert drop.text().endswith("quiet-chapter.epub")  # type: ignore[attr-defined]
    assert stored not in drop.text()  # type: ignore[attr-defined]
    assert drop.toolTip() == stored  # type: ignore[attr-defined]
    shell.show_result("quiet-chapter.epub")
    app.processEvents()  # type: ignore[attr-defined]
    play = shell.find("QPushButton", "audioPlay")
    path = shell.find("QLabel", "audioPath")
    waveform = shell.find("QLabel", "player")
    chapters = shell.find("QListWidget", "audioChapters")
    assert play is not None and play.isVisible()  # type: ignore[attr-defined]
    assert path is not None and path.text() == "/books/quiet-chapter/audiobook.wav"  # type: ignore[attr-defined]
    assert waveform is not None and waveform.isVisible() is False  # type: ignore[attr-defined]
    assert chapters is not None and chapters.count() == 2  # type: ignore[attr-defined]
    shell.simple._play_chapter(chapters.item(1))  # type: ignore[attr-defined]
    seek = shell.find("QSlider", "audioSeek")
    assert seek is not None and seek.value() == 75_000  # type: ignore[attr-defined]
    shell.close()
    app.processEvents()  # type: ignore[attr-defined]
    set_language("en")


if __name__ == "__main__":
    main()
