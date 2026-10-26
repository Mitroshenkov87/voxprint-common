"""Verify/repair classifies models and the window fits at 1280 px."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

from voxprint_common.cli_protocol import EXIT_CANCELLED, EXIT_OK
from voxprint_common.gpu_probe import GpuDevice, VramTier
from voxprint_common.hashing import sha256_bytes
from voxprint_common.i18n import set_language, tr_lang
from voxprint_common.mdl.catalog import load_manifest, plan_files
from voxprint_common.mdl.cli import main
from voxprint_common.mdl.fetch import FetchProgress
from voxprint_common.mdl.job import (
    Outcome,
    verify,
    verify_plain_line,
    verify_repair,
    verify_summary,
)
from voxprint_common.mdl.present import format_size
from voxprint_common.mdl.progress_ui import build_verify_window, clamp_window_size, run_window
from voxprint_common.mdl.status import JobState, Status, Step
from voxprint_common.suite_settings import set_keep_awake_during_jobs
from voxprint_common.ui.tokens import load

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

VOICE = b"voxprint-voice-payload"
BROKEN = b"voxprint-broken-payload"
GONE = b"voxprint-missing-payload"
PAYLOADS = {
    "voice": VOICE,
    "broken": BROKEN,
    "gone": GONE,
}
DIGESTS = {key: sha256_bytes(blob) for key, blob in PAYLOADS.items()}
GPU = GpuDevice(0, "Test GPU", "610.00", (8, 9), 8192)


def _manifest(folder: Path) -> Path:
    path = folder / "mdl.json"
    models = []
    for model_id, blob in PAYLOADS.items():
        models.append(
            {
                "id": model_id,
                "version": "1",
                "vram_tier": "8gb",
                "name": {"en": model_id},
                "files": [
                    {
                        "path": f"{model_id}.bin",
                        "size": len(blob),
                        "sha256": DIGESTS[model_id],
                        "mirrors": [
                            {
                                "source": "url",
                                "url": f"https://huggingface.co/org/{model_id}.bin",
                            }
                        ],
                    }
                ],
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": 1, "app": "model-forge", "models": models}),
        encoding="utf-8",
    )
    return path


def _write_store(manifest: Path) -> dict[str, Path]:
    planned = plan_files([load_manifest(manifest)], VramTier.GB8, memory_gib=8)
    assert len(planned) == 3
    paths = {item.model_id: item.destination for item in planned}
    paths["voice"].parent.mkdir(parents=True, exist_ok=True)
    paths["voice"].write_bytes(VOICE)
    paths["broken"].write_bytes(b"xxxx")
    return paths


def _assert_window_floor(window: object, preferred: tuple[int, int] = (1280, 720)) -> None:
    """The shown window stays at or above 760 by 480 and inside the screen."""
    assert window.minimumWidth() >= 760
    assert window.minimumHeight() >= 480
    assert window.width() >= window.minimumWidth()
    assert window.height() >= window.minimumHeight()
    screen = window.screen()
    assert screen is not None
    area = screen.availableGeometry()
    assert window.width() <= area.width()
    assert window.height() <= area.height()
    assert window.width() == min(max(preferred[0], 760), area.width())


def test_window_floor_stays_inside_a_small_screen() -> None:
    assert clamp_window_size(1280, 200, available=None) == (1280, 480)
    assert clamp_window_size(400, 200, available=None) == (760, 480)
    assert clamp_window_size(1280, 900, available=(1920, 1080)) == (1280, 900)
    assert clamp_window_size(1280, 900, available=(1000, 600)) == (1000, 600)
    assert clamp_window_size(400, 200, available=(700, 400)) == (760, 480)


def test_plain_verify_prints_model_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    set_language("en")
    manifest = _manifest(tmp_path)
    paths = _write_store(manifest)
    code = main(["verify", "--manifest", str(manifest)])
    err = capsys.readouterr().err.strip()
    assert code != 0
    assert err == "3 models: 1 OK, 1 damaged, 1 missing"
    assert err != "ready"
    assert verify([manifest], devices=[GPU]).message.startswith("sha256 mismatch")
    set_language("ru")
    assert (
        verify_plain_line(verify([manifest], devices=[GPU]).status, "ru")
        == "3 модели: 1 в порядке, 1 повреждена, 1 отсутствует"
    )
    paths["broken"].write_bytes(BROKEN)
    paths["gone"].write_bytes(GONE)
    set_language("en")
    code = main(["verify", "--manifest", str(manifest)])
    err = capsys.readouterr().err.strip()
    assert code == 0
    assert err == "3 models: 3 OK, 0 damaged, 0 missing"
    code = main(["verify", "--manifest", str(manifest), "--json"])
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    result = rows[-1]
    assert result["message"] == "ready"
    assert result["ok"] is True
    assert set(result) == {"type", "ok", "code", "message", "ready", "state", "jobs"}
    assert "3 models" not in captured.err


def test_repair_downloads_only_missing_and_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_keep_awake_during_jobs(False)
    manifest = _manifest(tmp_path)
    paths = _write_store(manifest)
    voice_before = paths["voice"].read_bytes()
    seen: list[str] = []

    def fake_fetch(
        destination: Path,
        urls: list[str],
        *,
        sha256: str,
        size: int,
        progress=None,
        **kwargs: object,
    ) -> None:
        del urls, kwargs
        seen.append(sha256)
        destination.parent.mkdir(parents=True, exist_ok=True)
        blob = next(body for key, body in PAYLOADS.items() if DIGESTS[key] == sha256)
        destination.write_bytes(blob)
        if progress is not None:
            progress(FetchProgress("huggingface", size, size, 1.0))

    monkeypatch.setattr("voxprint_common.mdl.job.fetch_file", fake_fetch)
    phases: list[str] = []
    checked = verify_repair(
        ("voice", "broken", "gone"),
        repair=False,
        manifests=[manifest],
        devices=[GPU],
        on_status=lambda status: phases.append(str(status.extra.get("phase"))),
    )
    assert checked.code != 0
    states = {step.item_id: step.state for step in checked.status.steps}
    assert states == {"voice": "ok", "broken": "corrupt", "gone": "missing"}
    assert phases[0] == "verify"
    assert phases[-1] == "summary"
    assert seen == []
    code = main(
        [
            "verify",
            "--repair",
            "--manifest",
            str(manifest),
            "--model",
            "voice",
            "--model",
            "broken",
            "--model",
            "gone",
        ]
    )
    assert code == 0
    assert seen == [DIGESTS["broken"], DIGESTS["gone"]]
    assert paths["voice"].read_bytes() == voice_before
    assert paths["broken"].read_bytes() == BROKEN
    assert paths["gone"].read_bytes() == GONE


def _snapshot(phase: str, rows: list[tuple[str, int, int, str]]) -> Status:
    status = Status(JobState.DOWNLOADING, extra={"phase": phase, "mode": "verify"})
    status.steps = [
        Step(
            model=name,
            size=size,
            source="",
            speed_bps=0.0,
            eta_s=None,
            state=state,
            received=received,
            sha256="",
            apps=["model-forge"],
            item_id=name,
            display_name=name,
        )
        for name, size, received, state in rows
    ]
    return status


def test_repair_runs_deep_hooks_for_models_that_were_already_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_keep_awake_during_jobs(False)
    manifest = _manifest(tmp_path)
    _write_store(manifest)
    calls: list[tuple[bool, set[str]]] = []

    def spy(files: list[object], *, deep: bool = False) -> None:
        calls.append((deep, {getattr(item, "model_id", "") for item in files}))

    def fake_fetch(
        destination: Path,
        urls: list[str],
        *,
        sha256: str,
        size: int,
        progress=None,
        **kwargs: object,
    ) -> None:
        del urls, kwargs
        destination.parent.mkdir(parents=True, exist_ok=True)
        blob = next(body for key, body in PAYLOADS.items() if DIGESTS[key] == sha256)
        destination.write_bytes(blob)
        if progress is not None:
            progress(FetchProgress("huggingface", size, size, 1.0))

    monkeypatch.setattr("voxprint_common.mdl.job.apply_hooks", spy)
    monkeypatch.setattr("voxprint_common.mdl.job.fetch_file", fake_fetch)
    outcome = verify_repair(
        ("voice", "broken", "gone"),
        repair=True,
        manifests=[manifest],
        devices=[GPU],
    )
    assert outcome.code == 0
    assert any(deep and calls_ids == {"voice", "broken", "gone"} for deep, calls_ids in calls)


def test_verify_window_opens_and_title_close_returns_the_job_code() -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QMetaObject, QObject, Qt, QTimer, Slot
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    prior = {id(widget) for widget in app.topLevelWidgets()}

    class _TitleBar(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.window: object | None = None

        @Slot()
        def shut(self) -> None:
            window = self.window
            assert window is not None
            button = window._verify_close
            attempts = 0

            def try_close() -> None:
                nonlocal attempts
                attempts += 1
                if button.isEnabled():
                    assert window.isVisible()
                    _assert_window_floor(window)
                    window.close()
                    return
                if attempts > 50:
                    app.exit(99)
                    return
                QTimer.singleShot(20, try_close)

            QTimer.singleShot(0, try_close)

    bar = _TitleBar()

    def start(cancel: object, on_status: object) -> Outcome:
        del cancel
        status = Status(JobState.READY, extra={"phase": "summary", "mode": "verify"})
        on_status(status)
        window = next(
            widget
            for widget in app.topLevelWidgets()
            if widget.objectName() == "root" and id(widget) not in prior
        )
        bar.window = window
        assert QMetaObject.invokeMethod(bar, "shut", Qt.ConnectionType.QueuedConnection)
        return Outcome(EXIT_OK, "", status)

    assert run_window(start, mode="verify") == EXIT_OK


def test_summary_uses_plural_forms() -> None:
    ru = verify_summary({"ok": 0, "missing": 0, "corrupt": 2, "repaired": 5}, "ru")
    assert ru == "0 в порядке, 0 отсутствует, 2 повреждены, 5 восстановлено"
    uk = verify_summary({"ok": 1, "missing": 0, "corrupt": 2, "repaired": 5}, "uk")
    assert "0 відсутньо" in uk and "2 пошкоджені" in uk
    lv = verify_summary({"ok": 0, "missing": 0, "corrupt": 1, "repaired": 2}, "lv")
    assert "0 nav atrasts" in lv and "2 salaboti" in lv
    assert verify_summary({"ok": 1, "missing": 2, "corrupt": 0, "repaired": 0}, "de") == (
        "1 in Ordnung, 2 fehlen, 0 beschädigt, 0 repariert"
    )


def test_verify_window_fits_at_1280_in_english_and_russian() -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QPushButton, QTableWidget

    names = {
        "en": (
            "Studio voice model",
            "Video enhancement model",
            "Face restoration",
            "Frame stabilizer",
        ),
        "ru": (
            "Студийный голос",
            "Модель улучшения видео",
            "Восстановление лиц",
            "Стабилизация кадров",
        ),
    }
    radius = load().density.button_radius_px
    assert radius == 4
    widths: dict[tuple[str, str], list[int]] = {}
    for lang, labels in names.items():
        verify = _snapshot(
            "verify",
            [
                (labels[0], 480_000_000, 200_000_000, "checking"),
                (labels[1], 1_200_000_000, 1_200_000_000, "ok"),
                (labels[2], 320_000_000, 0, "pending"),
                (labels[3], 90_000_000, 0, "pending"),
            ],
        )
        repair = _snapshot(
            "repair",
            [
                (labels[0], 480_000_000, 480_000_000, "ok"),
                (labels[1], 1_200_000_000, 760_000_000, "repairing"),
                (labels[2], 320_000_000, 0, "corrupt"),
                (labels[3], 90_000_000, 0, "missing"),
            ],
        )
        for phase, status in (("verify", verify), ("repair", repair)):
            window = build_verify_window(status, lang=lang, size=(1280, 720))
            app = QApplication.instance()
            assert app is not None
            app.processEvents()
            _assert_window_floor(window, preferred=(1280, 720))
            assert f"border-radius: {radius}px" in window.styleSheet()
            table = window.findChild(QTableWidget, "verifyTable")
            assert table is not None
            header = table.horizontalHeader()
            metrics = table.fontMetrics()
            section = [header.sectionSize(index) for index in range(table.columnCount())]
            assert sum(section) <= table.viewport().width() + 1
            for column in range(table.columnCount()):
                title = table.horizontalHeaderItem(column).text()
                assert metrics.horizontalAdvance(title) + 8 <= section[column]
                for row in range(table.rowCount()):
                    item = table.item(row, column)
                    if item is None or not item.text():
                        continue
                    advance = metrics.horizontalAdvance(item.text())
                    assert advance + 8 <= section[column]
            phase_label = window.findChild(QLabel, "phase")
            assert phase_label is not None
            assert phase_label.text() == tr_lang(f"mdl.verify.phase.{phase}", lang)
            assert phase_label.height() + 4 >= phase_label.heightForWidth(phase_label.width())
            assert table.item(1, 3).text() == tr_lang(
                "mdl.verify.status.ok" if phase == "verify" else "mdl.verify.status.repairing",
                lang,
            )
            assert table.item(1, 1).text() == format_size(1_200_000_000, lang=lang)
            bars = table.viewport().findChildren(QProgressBar)
            assert len(bars) == table.rowCount()
            row_height = table.rowHeight(0)
            model_right = header.sectionSize(0)
            for bar in bars:
                origin = bar.mapTo(table.viewport(), QPoint(0, 0))
                assert origin.x() >= model_right
                host = bar.parentWidget()
                inside = bar.mapTo(host, QPoint(0, 0))
                pad_top = inside.y()
                pad_bottom = host.height() - (inside.y() + bar.height())
                assert pad_top == pad_bottom
                assert pad_top >= 6
                assert origin.y() + bar.height() < ((origin.y() // row_height) + 1) * row_height
            buttons = window.findChildren(QPushButton)
            assert len(buttons) == 2
            assert buttons[0].size() == buttons[1].size()
            close = window._verify_close
            assert close.objectName() == "close"
            assert close.isEnabled() is False
            if lang == "ru" and phase == "verify":
                summary = window.findChild(QLabel, "summary")
                assert summary is not None
                assert "0 отсутствует" in summary.text()
                assert "нет" not in summary.text()
            widths[(lang, phase)] = section
            window.hide()
        assert widths[(lang, "verify")] == widths[(lang, "repair")]


def test_phase_label_appends_the_counter() -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    status = _snapshot("review", [("Voice", 10, 10, "ok")])
    status.extra["done"] = 2
    status.extra["total"] = 4
    window = build_verify_window(status, lang="en", size=(1280, 720))
    app.processEvents()
    label = window.findChild(QLabel, "phase")
    assert label is not None
    assert label.text() == f"{tr_lang('mdl.verify.phase.review', 'en')} · 2/4"
    window.hide()
    window.deleteLater()
    app.processEvents()


def test_every_hash_finishes_before_repair_and_hashes_do_not_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_keep_awake_during_jobs(False)
    manifest = _manifest(tmp_path)
    _write_store(manifest)
    events: list[object] = []
    live = {"n": 0, "peak": 0}
    real = hashlib.sha256

    class _Counting:
        def __init__(self, data: bytes = b"") -> None:
            live["n"] += 1
            live["peak"] = max(live["peak"], live["n"])
            events.append("hash")
            self._inner = real(data)

        def update(self, data: bytes) -> None:
            self._inner.update(data)

        def hexdigest(self) -> str:
            try:
                return self._inner.hexdigest()
            finally:
                live["n"] -= 1
                events.append("hash-done")

        def digest(self) -> bytes:
            return self._inner.digest()

        def copy(self) -> object:
            return self._inner.copy()

    def factory(data: bytes = b"", *, usedforsecurity: bool = True) -> _Counting:
        del usedforsecurity
        return _Counting(data)

    def fake_fetch(
        destination: Path,
        urls: list[str],
        *,
        sha256: str,
        size: int,
        progress=None,
        **kwargs: object,
    ) -> None:
        del urls, kwargs
        events.append("fetch")
        destination.parent.mkdir(parents=True, exist_ok=True)
        blob = next(body for key, body in PAYLOADS.items() if DIGESTS[key] == sha256)
        destination.write_bytes(blob)
        if progress is not None:
            progress(FetchProgress("huggingface", size, size, 1.0))

    def on_status(status: Status) -> None:
        events.append(
            (
                "phase",
                status.extra.get("phase"),
                tuple(step.state for step in status.steps),
                status.extra.get("done"),
                status.extra.get("total"),
            )
        )

    monkeypatch.setattr(hashlib, "sha256", factory)
    monkeypatch.setattr("voxprint_common.mdl.job.fetch_file", fake_fetch)
    outcome = verify_repair(
        ("voice", "broken", "gone"),
        repair=True,
        manifests=[manifest],
        devices=[GPU],
        on_status=on_status,
    )
    assert outcome.code == EXIT_OK
    assert live["peak"] == 1
    fetch_at = events.index("fetch")
    hash_dones = [index for index, item in enumerate(events) if item == "hash-done"]
    assert hash_dones
    assert max(hash_dones) < fetch_at
    review_at = next(
        index
        for index, item in enumerate(events)
        if isinstance(item, tuple) and item[1] == "review"
    )
    assert review_at < fetch_at
    review = events[review_at]
    assert isinstance(review, tuple)
    assert review[1:] == ("review", ("ok", "corrupt", "missing"), 3, 3)


def test_cached_digest_skips_a_second_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_keep_awake_during_jobs(False)
    manifest = _manifest(tmp_path)
    _write_store(manifest)
    calls = {"n": 0}
    real = hashlib.sha256

    def spy(data: bytes = b"", *, usedforsecurity: bool = True) -> object:
        calls["n"] += 1
        return real(data, usedforsecurity=usedforsecurity)

    monkeypatch.setattr(hashlib, "sha256", spy)
    first = verify_repair(
        ("voice", "broken", "gone"),
        repair=False,
        manifests=[manifest],
        devices=[GPU],
    )
    assert first.status.steps[0].state == "ok"
    hashed = calls["n"]
    assert hashed > 0
    verify_repair(
        ("voice", "broken", "gone"),
        repair=False,
        manifests=[manifest],
        devices=[GPU],
    )
    assert calls["n"] == hashed


def test_gui_entry_point_implies_the_gui_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, list[str]] = {}

    def fake(argv: list[str]) -> int:
        seen["argv"] = list(argv)
        return 0

    monkeypatch.setattr(sys, "argv", ["voxprint-mdl-gui", "--app", "model-forge"])
    monkeypatch.setattr("voxprint_common.mdl.cli._open_named_window", fake)
    assert main() == 0
    assert seen["argv"][:2] == ["--gui", "--app"]


def test_multi_file_model_matches_each_file(tmp_path: Path) -> None:
    from voxprint_common.mdl.control import Controller
    from voxprint_common.paths import models_dir

    first = b"alpha-weights"
    second = b"beta-weights-extra"
    digests = (sha256_bytes(first), sha256_bytes(second))
    blobs = models_dir() / "blobs"
    blobs.mkdir(parents=True)
    (blobs / digests[0]).write_bytes(first)
    (blobs / digests[1]).write_bytes(second)
    manifest = tmp_path / "mdl.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "app": "model-forge",
                "models": [
                    {
                        "id": "pack",
                        "version": "1",
                        "vram_tier": "8gb",
                        "name": {"en": "Pack"},
                        "files": [
                            {
                                "path": "a.bin",
                                "size": len(first),
                                "sha256": digests[0],
                                "mirrors": [
                                    {"source": "url", "url": "https://huggingface.co/org/a.bin"}
                                ],
                            },
                            {
                                "path": "b.bin",
                                "size": len(second),
                                "sha256": digests[1],
                                "mirrors": [
                                    {"source": "url", "url": "https://huggingface.co/org/b.bin"}
                                ],
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    page = Controller().manage_page("model-forge", manifest, devices=[GPU], lang="en")
    assert [row.state for row in page.installed] == ["ready"]
    assert page.available == ()
    again = Controller().manage_page("model-forge", manifest, devices=[GPU], lang="en")
    assert again.installed[0].state == "ready"
    (blobs / digests[1]).unlink()
    partial = Controller().manage_page("model-forge", manifest, devices=[GPU], lang="en")
    assert partial.installed[0].state == "error"


def test_failed_manage_scan_retries_then_shows_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("PySide6")
    from voxprint_common.mdl.control import Controller
    from voxprint_common.ui.mdl_window import _probe_manage

    calls = {"n": 0}

    def boom(*_args: object, **_kwargs: object) -> None:
        calls["n"] += 1
        raise RuntimeError("scan broke")

    monkeypatch.setattr("voxprint_common.ui.mdl_window._compose_manage_view", boom)
    manifest = tmp_path / "mdl.json"
    manifest.write_text("{}", encoding="utf-8")
    window, app = _probe_manage()(Controller(), "model-forge", "", manifest)
    try:
        for _ in range(40):
            window._apply_pending()
            app.processEvents()
            view = window._view
            if view is not None and view.show_error:
                break
            time.sleep(0.05)
        assert calls["n"] >= 2
        view = window._view
        assert view is not None and view.manage is not None
        assert view.show_error
        assert "no models" not in view.manage.empty_installed.lower()
        assert window._error.isVisible()
        assert window._primary.isVisible()
        assert window._primary.text() == "Retry"
    finally:
        window._check_busy = False
        window.hide()
        window.deleteLater()
        app.processEvents()


def test_manage_check_does_not_start_another_event_loop() -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from voxprint_common.mdl.progress_ui import _run_verify_window

    app = QApplication.instance() or QApplication([])
    seen: dict[str, object] = {}

    def start(cancel: object, on_status: object) -> Outcome:
        del cancel
        on_status(Status(JobState.READY, extra={"phase": "summary"}))
        return Outcome(EXIT_OK, "ok", Status(JobState.READY, extra={"phase": "summary"}))

    def on_done(outcome: Outcome) -> None:
        seen["code"] = outcome.code

    before = {id(widget) for widget in app.topLevelWidgets()}
    assert _run_verify_window(start, on_done=on_done) == 0
    app.processEvents()
    created = [widget for widget in app.topLevelWidgets() if id(widget) not in before]
    assert created
    window = created[-1]
    assert window.windowModality() == Qt.WindowModality.ApplicationModal
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not window._verify_close.isEnabled():
        app.processEvents()
        time.sleep(0.02)
    window.close()
    app.processEvents()
    assert seen.get("code") == EXIT_OK


def test_manage_open_does_not_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PySide6")
    from voxprint_common.gpu_probe import VramTier
    from voxprint_common.mdl.control import Controller
    from voxprint_common.ui.mdl_window import _compose_manage_view, _probe_manage

    payload = b"manage-open-bytes"
    digest = hashlib.sha256(payload).hexdigest()
    manifest = tmp_path / "mdl.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "app": "model-forge",
                "models": [
                    {
                        "id": "voice",
                        "version": "1",
                        "vram_tier": "8gb",
                        "name": {"en": "Voice"},
                        "files": [
                            {
                                "path": "voice.bin",
                                "size": len(payload),
                                "sha256": digest,
                                "mirrors": [
                                    {
                                        "source": "url",
                                        "url": "https://huggingface.co/org/voice.bin",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    planned = plan_files([load_manifest(manifest)], VramTier.GB8, memory_gib=8)
    planned[0].destination.parent.mkdir(parents=True, exist_ok=True)
    planned[0].destination.write_bytes(payload)
    calls = {"n": 0}
    real = hashlib.sha256

    def spy(data: bytes = b"", *, usedforsecurity: bool = True) -> object:
        calls["n"] += 1
        return real(data, usedforsecurity=usedforsecurity)

    monkeypatch.setattr(hashlib, "sha256", spy)
    controller = Controller()
    painted = _compose_manage_view(controller, "model-forge", manifest)
    assert painted.manage is not None
    assert painted.manage.installed[0].state == "ready"
    assert calls["n"] == 0
    window, app = _probe_manage()(controller, "model-forge", "", manifest)
    try:
        assert calls["n"] == 0
        view = window._view
        assert view is not None and view.manage is not None
        assert view.manage.installed[0].state == "ready"
        _assert_window_floor(window, preferred=(1280, 900))
        assert window._footer.isVisible() is False
        assert window._rule.isVisible() is False
        assert window._fade.isVisible() is False
        assert window._status.isVisible() is False
        assert "Model Forge" in window._subtitle.text()
        assert window._badge._colour == "#f58a3c"
        badge = window._badge.grab().toImage()

        def colour_at(image: object, across: float, down: float) -> object:
            x = min(image.width() - 1, max(0, int(across * image.width())))
            y = min(image.height() - 1, max(0, int(down * image.height())))
            return image.pixelColor(x, y)

        top = colour_at(badge, 0.50, 0.43)
        left = colour_at(badge, 0.35, 0.55)
        right = colour_at(badge, 0.65, 0.55)
        edge = colour_at(badge, 0.12, 0.50)
        assert edge.red() < 80
        assert top.red() > 180
        assert top.red() > right.red() + 20
        assert left.red() > right.red() + 20
        window._check_busy = True
        window.close()
        assert window.isVisible()
        window._check_busy = False
        window._badge.set_mark("#60a5fa", "no-such-glyph", "VOXPrint")
        mark = window._badge.grab().toImage()
        arm = colour_at(mark, 0.40, 0.56)
        gap = colour_at(mark, 0.50, 0.36)
        assert arm.blue() > 140
        assert gap.red() < 80
        assert gap.blue() < 90
    finally:
        window.hide()
        window.deleteLater()
        app.processEvents()


def test_closing_the_check_reenables_manage_immediately() -> None:
    pytest.importorskip("PySide6")
    import threading

    from PySide6.QtWidgets import QApplication, QWidget

    from voxprint_common.mdl.progress_ui import _run_verify_window, verify_job_running

    app = QApplication.instance() or QApplication([])
    manage = QWidget()
    manage.setEnabled(False)
    hold = threading.Event()
    started = threading.Event()
    seen: dict[str, int] = {}

    def start(cancel: threading.Event, on_status: object) -> Outcome:
        started.set()
        on_status(Status(JobState.DOWNLOADING, extra={"phase": "verify"}))
        hold.wait(2)
        code = EXIT_CANCELLED if cancel.is_set() else EXIT_OK
        state = JobState.CANCELLED if cancel.is_set() else JobState.READY
        return Outcome(code, "", Status(state, extra={"phase": "summary"}))

    def on_done(outcome: Outcome) -> None:
        manage.setEnabled(True)
        seen["code"] = outcome.code

    try:
        before = {id(widget) for widget in app.topLevelWidgets()}
        assert _run_verify_window(start, on_done=on_done) == 0
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not started.is_set():
            app.processEvents()
            time.sleep(0.01)
        assert started.is_set()
        assert verify_job_running()
        created = [widget for widget in app.topLevelWidgets() if id(widget) not in before]
        window = created[-1]
        window._verify_cancel.click()
        assert manage.isEnabled()
        assert seen["code"] == EXIT_CANCELLED
        assert verify_job_running()
        hold.set()
        deadline = time.monotonic() + 2
        while verify_job_running() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not verify_job_running()

        manage.setEnabled(False)
        seen.clear()
        started.clear()
        hold.clear()
        before = {id(widget) for widget in app.topLevelWidgets()}
        assert _run_verify_window(start, on_done=on_done) == 0
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not started.is_set():
            app.processEvents()
            time.sleep(0.01)
        created = [widget for widget in app.topLevelWidgets() if id(widget) not in before]
        window = created[-1]
        window.close()
        assert manage.isEnabled()
        assert seen["code"] == EXIT_CANCELLED
    finally:
        hold.set()
        deadline = time.monotonic() + 2
        while verify_job_running() and time.monotonic() < deadline:
            time.sleep(0.01)
        manage.deleteLater()
        app.processEvents()
