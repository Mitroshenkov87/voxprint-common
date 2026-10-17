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


class _Allow:
    def review_prompt(self, prompt: str) -> str | None:
        return None

    def review_result(self, prompt: str, item_id: str) -> str | None:
        return None


_GATE = _Allow()


def test_import_does_not_load_qt() -> None:
    assert "PySide6" not in sys.modules


def test_defaults_are_plain_and_data_driven() -> None:
    layout = SimpleImageLayout(image_presets(), gate=_GATE)
    assert layout.phase is Phase.EMPTY
    assert layout.history == []
    assert layout.selected is None
    assert layout.full_layout is False
    assert layout.seed == 0
    assert layout.presets == {
        "quality": "balanced",
        "style": "photo",
        "size": "1:1",
        "output": "standard",
        "detail": "medium",
        "count": "1",
    }
    assert [row.row_id for row in layout.rows] == [
        "quality",
        "style",
        "size",
        "output",
        "detail",
        "count",
    ]
    assert layout.batch_size() == 1
    layout.choose("count", "4")
    assert layout.batch_size() == 4
    size = next(row for row in layout.rows if row.row_id == "size")
    assert [choice.value for choice in size.choices] == [
        "16:9",
        "21:9",
        "3:2",
        "4:3",
        "1:1",
        "9:16",
        "2:3",
        "3:4",
    ]
    assert [choice.group_key for choice in size.choices[:4]] == ["simple.size.landscape"] * 4
    style = next(row for row in layout.rows if row.row_id == "style")
    assert [choice.value for choice in style.choices] == [
        "photo",
        "cinematic",
        "portrait",
        "illustration",
        "anime",
        "3d",
        "painting",
        "sketch",
    ]
    prompt = "a red door"
    layout.set_prompt(prompt)
    layout.choose("style", "anime")
    assert layout.prompt == prompt
    assert layout.presets["style"] == "anime"
    with pytest.raises(ValueError):
        layout.choose("output", "4k")
    installed = SimpleImageLayout(image_presets(enlarge_installed=True), gate=_GATE)
    installed.choose("output", "4k")
    assert installed.presets["output"] == "4k"


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
        ),
        gate=_GATE,
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
    layout = SimpleImageLayout(image_presets(), gate=_GATE)
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
    layout = SimpleImageLayout(image_presets(), gate=_GATE)
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
    layout = SimpleImageLayout(image_presets(), gate=_GATE)
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
    with pytest.raises(ValueError):
        PresetChoice("4k", "simple.output.4k", available=False)
    with pytest.raises(ValueError):
        PresetRow(
            "output",
            "simple.output",
            (
                PresetChoice(
                    "4k",
                    "simple.output.4k",
                    available=False,
                    reason_key="simple.output.4k.reason",
                    action_label_key="simple.output.install",
                    action_id="install.enlarge",
                ),
            ),
            "4k",
        )


def test_progress_block_and_unavailable_return_to_idle() -> None:
    layout = SimpleImageLayout(image_presets(), gate=_GATE)
    layout.set_prompt("a hill")
    assert layout.generate() is True
    with pytest.raises(ValueError):
        layout.progress(2, 1, "Drawing")
    with pytest.raises(ValueError):
        layout.progress(0, 1, " ")
    layout.progress(1, 4, " Drawing the picture ")
    assert layout.step == 1
    assert layout.total == 4
    assert layout.stage_label == "Drawing the picture"
    layout.cancel()
    assert layout.phase is Phase.EMPTY
    assert layout.stage_label == ""
    with pytest.raises(ValueError):
        layout.progress(1, 4, "Drawing")
    with pytest.raises(ValueError):
        layout.block("  ")
    layout.block("  This picture was stopped. ")
    assert layout.phase is Phase.BLOCKED
    assert layout.reason == "This picture was stopped."
    layout.cancel()
    assert layout.phase is Phase.EMPTY
    assert layout.reason == ""
    layout.unavailable("That feature is turned off.")
    assert layout.phase is Phase.UNAVAILABLE
    assert layout.generate() is True
    assert layout.phase is Phase.BUSY
    assert layout.reason == ""


def test_history_keeps_fifty_items_seed_and_survives_a_bad_file(tmp_path) -> None:
    layout = SimpleImageLayout(image_presets(), state_dir=tmp_path, gate=_GATE)
    layout.set_prompt("door")
    layout.seed = 42
    layout.choose("style", "sketch")
    for index in range(55):
        assert layout.generate() is True
        layout.finish(f"id-{index}")
    assert len(layout.history) == 50
    assert layout.history[0].item_id == "id-54"
    assert layout.history[0].seed == 42
    assert layout.history[0].presets[1] == ("style", "sketch")
    again = SimpleImageLayout(image_presets(), state_dir=tmp_path, gate=_GATE)
    assert again.selected is None
    assert again.phase is Phase.EMPTY
    assert [item.item_id for item in again.history] == [f"id-{index}" for index in range(54, 4, -1)]
    assert again.use_prompt_again(again.history[0].item_id) == "door"
    assert again.seed == 42
    assert again.presets["style"] == "sketch"
    removed = again.delete("id-54")
    assert removed is not None and removed.item_id == "id-54"
    assert again.history[0].item_id == "id-53"
    stored = SimpleImageLayout(image_presets(), state_dir=tmp_path, gate=_GATE)
    assert stored.history[0].item_id == "id-53"
    assert stored.delete("missing") is None
    (tmp_path / "image-history.json").write_text("{", encoding="utf-8")
    broken = SimpleImageLayout(image_presets(), state_dir=tmp_path, gate=_GATE)
    assert broken.history == []
