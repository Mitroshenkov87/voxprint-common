"""Paint the check gate at 100% and 150%, in English and Russian.

Run with ``QT_QPA_PLATFORM=offscreen``. The rows are scripted. The job does
not run. A child process paints the same board at 150% (``QT_SCALE_FACTOR``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _windows_font_dir() -> None:
    """Point offscreen Qt at the Windows font folder when nothing else did."""
    if os.name != "nt" or os.environ.get("QT_QPA_FONTDIR"):
        return
    fonts = r"C:\Windows\Fonts"
    if os.path.isdir(fonts):
        os.environ["QT_QPA_FONTDIR"] = fonts


_windows_font_dir()
if os.environ.get("CHECK_GATE_SCALE") == "150":
    os.environ["QT_SCALE_FACTOR"] = "1.5"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from voxprint_common.i18n import set_language
from voxprint_common.ui.check_gate import CheckGate, GateState, ItemResult
from voxprint_common.ui.theme import apply_shell_palette, build_style

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "renders"
ART = Path("/opt/cursor/artifacts/renders")


def _job(item, _token) -> ItemResult:
    return ItemResult(item.id, True)


def _items() -> list:
    return []


def _board(lang: str) -> QWidget:
    set_language(lang)
    board = QWidget()
    board.setObjectName("root")
    board.setStyleSheet(build_style(False))
    apply_shell_palette(board)
    layout = QVBoxLayout(board)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(8)
    rows = (
        (GateState.DIRTY, {}),
        (GateState.RUNNING, {"done": 2, "total": 5}),
        (GateState.PASSED, {"deactivated": ("take-a", "take-b")}),
    )
    for state, options in rows:
        gate = CheckGate(_job, items=_items)
        gate._present(state, **options)
        layout.addWidget(gate)
    return board


def _paint(lang: str, suffix: str) -> None:
    app = QApplication.instance() or QApplication([])
    board = _board(lang)
    board.adjustSize()
    hint = board.sizeHint()
    board.resize(max(640, hint.width()), hint.height())
    board.show()
    app.processEvents()
    path = OUT / f"check-gate-{lang}{suffix}.png"
    print(f"painting {path.name}", flush=True)
    board.grab().save(str(path))
    print(f"  {path.name} bytes={path.stat().st_size}", flush=True)
    board.hide()
    board.deleteLater()
    app.processEvents()


def main() -> None:
    """Write the 100% renders, then the 150% renders in a child process."""
    OUT.mkdir(parents=True, exist_ok=True)
    scaled = os.environ.get("CHECK_GATE_SCALE") == "150"
    suffix = "-150" if scaled else ""
    for lang in ("en", "ru"):
        _paint(lang, suffix)
    if not scaled:
        env = os.environ.copy()
        env["CHECK_GATE_SCALE"] = "150"
        env["QT_SCALE_FACTOR"] = "1.5"
        env["QT_QPA_PLATFORM"] = "offscreen"
        subprocess.check_call([sys.executable, str(Path(__file__).resolve())], env=env)
    try:
        ART.mkdir(parents=True, exist_ok=True)
        for png in OUT.glob("check-gate-*.png"):
            shutil.copyfile(png, ART / png.name)
    except OSError as exc:
        print(f"artifacts not copied: {exc}", flush=True)


if __name__ == "__main__":
    main()
