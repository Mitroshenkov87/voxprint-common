"""Offscreen checks: history cell sizing, whole-cell snap, and the compact mark."""

from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication, QPushButton

    from voxprint_common.ui.presets import reference_presets
    from voxprint_common.ui.safety import ReviewedOutput
    from voxprint_common.ui.settings import reference_settings
    from voxprint_common.ui.simple_image import (
        HISTORY_CELL_GAP,
        HISTORY_CELL_SIZE,
        SimpleImageLayout,
        SimpleImageWidget,
        snap_history_offset,
    )
    from voxprint_common.ui.theme import build_shell_style, mark_compact

    app = QApplication([])

    class Gate:
        def review_prompt(self, prompt: str) -> str | None:
            del prompt
            return None

        def review_result(self, prompt: str, item_id: str, output: ReviewedOutput) -> str | None:
            del prompt, item_id, output
            return None

    settings = reference_settings()
    layout = SimpleImageLayout(
        presets=reference_presets(settings),
        settings=settings,
        gate=Gate(),
    )
    view = SimpleImageWidget(layout, settings)
    assert view.cell_size == HISTORY_CELL_SIZE == 88
    assert view.cell_gap == HISTORY_CELL_GAP == 8
    assert view._history_scroll.height() == 104
    assert view._history.spacing() == 8
    for bad in (0, -4):
        try:
            view.cell_size = bad
        except ValueError:
            pass
        else:
            raise AssertionError(f"cell_size accepted {bad}")
        try:
            view.cell_gap = bad
        except ValueError:
            pass
        else:
            raise AssertionError(f"cell_gap accepted {bad}")
    assert view.cell_size == 88 and view.cell_gap == 8

    view.root.resize(900, 700)
    view.root.show()
    app.processEvents()
    for index in range(12):
        layout.set_prompt(f"picture {index}")
        assert layout.generate() is True
        finished = layout.finish(f"item-{index}", ReviewedOutput(data=b"png", mime="image/png"))
        assert finished is not None
    view.refresh()
    app.processEvents()
    thumbs = [
        button
        for button in view.root.findChildren(QPushButton)
        if str(button.objectName()).startswith("thumb-")
    ]
    assert len(thumbs) == 12
    for button in thumbs:
        assert button.width() == 88 and button.height() == 88
        assert button.iconSize().width() == 80

    view.cell_size = 64
    view.cell_gap = 4
    assert view.cell_size == 64 and view.cell_gap == 4
    app.processEvents()
    assert view._history_scroll.height() == 80
    assert view._history.spacing() == 4
    for button in thumbs:
        assert button.width() == 64 and button.height() == 64
        assert button.iconSize().width() == 56

    def expected(value: int) -> int:
        return snap_history_offset(
            value,
            cell_size=view.cell_size,
            cell_gap=view.cell_gap,
            viewport=view._history_scroll.viewport().width(),
            content=view._history_scroll.widget().width(),
        )

    # Narrow the window so the strip overflows, then scroll and snap.
    view.root.resize(500, 700)
    app.processEvents()
    bar = view._history_bar
    assert bar.maximum() > 0
    view._snap_history()
    assert bar.value() == 0
    stride = 64 + 4
    bar.setValue(30)
    app.processEvents()
    assert bar.value() == expected(30)
    assert bar.value() % stride == 0
    bar.setValue(31 + bar.maximum())
    view._snap_history()
    assert bar.value() == expected(bar.maximum())
    bar.setValue(90)
    app.processEvents()
    view.root.resize(520, 700)
    app.processEvents()
    assert bar.value() == expected(90)

    view.cell_size = 88
    view.cell_gap = 8
    assert view._history_scroll.height() == 104
    assert view._history.spacing() == 8
    app.processEvents()
    for button in thumbs:
        assert button.width() == 88 and button.height() == 88
        assert button.iconSize().width() == 80
    view.root.close()
    app.processEvents()

    style = build_shell_style(False)
    button = QPushButton("compact")
    button.setStyleSheet(style)
    assert mark_compact(button) is button
    assert button.property("compact") is True
    app.processEvents()


if __name__ == "__main__":
    main()
    sys.exit(0)
