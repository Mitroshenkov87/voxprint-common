"""Headless simple image layout: presets, progress, history, and the dock flag."""

from __future__ import annotations

import sys

import pytest

from voxprint_common.ui.simple_image import (
    GalleryItem,
    Phase,
    PresetChoice,
    PresetRow,
    SimpleImageLayout,
    image_presets,
)


def test_import_does_not_load_qt() -> None:
    assert "PySide6" not in sys.modules


def test_defaults_are_plain_and_data_driven() -> None:
    layout = SimpleImageLayout(image_presets())
    assert layout.phase is Phase.EMPTY
    assert layout.history == []
    assert layout.selected is None
    assert layout.full_layout is False
    assert layout.presets == {"size": "square", "style": "none", "count": "1"}
    assert [row.row_id for row in layout.rows] == ["size", "style", "count"]
    assert layout.batch_size() == 1
    layout.choose("count", "4")
    assert layout.batch_size() == 4
    size = layout.rows[0]
    assert [choice.value for choice in size.choices] == [
        "square",
        "portrait",
        "landscape",
        "wide",
    ]


def test_custom_rows_generate_cancel_and_history() -> None:
    layout = SimpleImageLayout(
        (
            PresetRow(
                "size",
                "simple.size",
                (
                    PresetChoice("square", "simple.size.square"),
                    PresetChoice("wide", "simple.size.wide"),
                ),
                "wide",
            ),
        )
    )
    assert layout.presets == {"size": "wide"}
    assert layout.generate() is False
    layout.set_prompt("   ")
    assert layout.generate() is False
    layout.set_prompt("  a red door  ")
    assert layout.generate() is True
    assert layout.phase is Phase.BUSY
    assert layout.generate() is False
    layout.cancel()
    assert layout.phase is Phase.EMPTY
    assert layout.history == []
    assert layout.generate() is True
    item = layout.finish("one")
    assert isinstance(item, GalleryItem)
    assert item is not None
    assert item.prompt == "a red door"
    assert item.presets == (("size", "wide"),)
    assert layout.phase is Phase.READY
    assert layout.selected == "one"
    layout.set_prompt("second")
    layout.choose("size", "square")
    assert layout.generate() is True
    assert layout.finish("two") is not None
    assert [saved.item_id for saved in layout.history] == ["two", "one"]
    assert layout.select("one") is not None
    assert layout.selected == "one"
    assert layout.phase is Phase.READY
    assert layout.use_prompt_again() == "a red door"
    assert layout.prompt == "a red door"
    assert layout.presets["size"] == "wide"
    assert layout.more_like("missing") is False
    layout.set_prompt("elsewhere")
    assert layout.more_like("one") is True
    assert layout.phase is Phase.BUSY
    assert layout.prompt == "a red door"
    assert layout.presets["size"] == "wide"


def test_busy_cancel_returns_to_the_picture_on_screen() -> None:
    layout = SimpleImageLayout(image_presets())
    layout.set_prompt("first")
    assert layout.generate() is True
    assert layout.finish("one") is not None
    layout.set_prompt("second")
    assert layout.generate() is True
    layout.cancel()
    assert layout.phase is Phase.READY
    assert layout.selected == "one"
    assert [item.item_id for item in layout.history] == ["one"]


def test_full_layout_requires_expert_mode() -> None:
    layout = SimpleImageLayout(image_presets())
    assert layout.request_full_layout(True, expert=False) is False
    assert layout.dock_visible(expert=False) is False
    assert layout.dock_visible(expert=True) is False
    assert layout.request_full_layout(True, expert=True) is True
    assert layout.dock_visible(expert=True) is True
    assert layout.request_full_layout(True, expert=False) is False
    assert layout.full_layout is False
    assert layout.dock_visible(expert=True) is False


def test_invalid_presets_are_refused() -> None:
    with pytest.raises(ValueError):
        PresetRow("size", "simple.size", (), "square")
    with pytest.raises(ValueError):
        PresetRow(
            "size",
            "simple.size",
            (PresetChoice("square", "simple.size.square"),),
            "wide",
        )
    layout = SimpleImageLayout(image_presets())
    with pytest.raises(ValueError):
        layout.choose("size", "round")
    with pytest.raises(ValueError):
        layout.choose("missing", "square")
    layout.set_prompt("one")
    layout.generate()
    assert layout.finish("same") is not None
    layout.set_prompt("two")
    layout.generate()
    with pytest.raises(ValueError):
        layout.finish("same")
