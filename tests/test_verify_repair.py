"""Verify/repair classifies models and the window fits at 1280 px."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from voxprint_common.gpu_probe import GpuDevice, VramTier
from voxprint_common.hashing import sha256_bytes
from voxprint_common.i18n import tr_lang
from voxprint_common.mdl.catalog import load_manifest, plan_files
from voxprint_common.mdl.cli import main
from voxprint_common.mdl.fetch import FetchProgress
from voxprint_common.mdl.job import verify_repair, verify_summary
from voxprint_common.mdl.present import format_size
from voxprint_common.mdl.progress_ui import build_verify_window
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
            assert window.width() == 1280
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
