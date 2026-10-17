"""Headless simple image layout: presets, progress, history, and the dock flag."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from voxprint_common.ui.presets import reference_presets
from voxprint_common.ui.safety import ReviewedOutput
from voxprint_common.ui.settings import reference_settings
from voxprint_common.ui.simple_image import (
    GalleryItem,
    Phase,
    PresetChoice,
    PresetRow,
    SimpleImageLayout,
    image_presets,
)
from voxprint_common.ui.variants import VariantId


class _Allow:
    def review_prompt(self, prompt: str) -> str | None:
        return None

    def review_result(self, prompt: str, item_id: str, output: ReviewedOutput) -> str | None:
        del prompt, item_id, output
        return None


_GATE = _Allow()
_OUT = ReviewedOutput(data=b"png", mime="image/png")


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
    assert layout.generate() is True
    assert layout.phase is Phase.BUSY
    layout.cancel()
    assert layout.phase is Phase.EMPTY
    assert layout.history == []
    assert layout.generate() is True
    item = layout.finish("one", _OUT)
    assert isinstance(item, GalleryItem)
    assert item is not None
    assert item.prompt == "a red door"
    assert item.presets == (("size", "wide"),)
    assert layout.phase is Phase.READY
    assert layout.selected == "one"
    layout.set_prompt("second")
    layout.choose("size", "square")
    assert layout.generate() is True
    assert layout.finish("two", _OUT) is not None
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
    assert layout.finish("one", _OUT) is not None
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
    assert layout.finish("same", _OUT) is not None
    layout.set_prompt("two")
    layout.generate()
    with pytest.raises(ValueError):
        layout.finish("same", _OUT)
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
        layout.finish(f"id-{index}", _OUT)
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


def test_hook_runs_before_busy_with_the_expert_values() -> None:
    settings = reference_settings()
    seen: list[Mapping[str, object]] = []
    phases: list[Phase] = []
    layout = SimpleImageLayout(
        presets=reference_presets(settings),
        settings=settings,
        gate=_GATE,
    )

    def hook(values: Mapping[str, object]) -> None:
        seen.append(values)
        phases.append(layout.phase)

    layout.set_value("shell.steps", 28)
    layout.set_value("shell.guidance", 4.5)
    layout.set_value("shell.sampler", "dpm")
    layout.seed = 7
    layout.set_prompt("a red door")
    assert layout.generate(hook) is True
    assert phases == [Phase.EMPTY]
    assert layout.phase is Phase.BUSY
    values = seen[0]
    assert values["shell.steps"] == 28
    assert values["shell.guidance"] == 4.5
    assert values["shell.sampler"] == "dpm"
    assert values["shell.seed"] == 7
    assert values["shell.resolution"]
    assert layout.generate(hook) is True
    assert len(seen) == 1
    assert layout.phase is Phase.BUSY


def test_image_gate_is_required_and_source_slots_stay_hidden() -> None:
    with pytest.raises(TypeError, match="gate"):
        SimpleImageLayout()
    text = SimpleImageLayout(variant=VariantId.TEXT)
    text.set_prompt("a quiet hill")
    assert text.generate() is False
    assert text.phase is Phase.BLOCKED
    with pytest.raises(ValueError, match="image screen"):
        SimpleImageLayout(variant=VariantId.TEXT, source_picture=True, gate=_GATE)
    settings = reference_settings()
    hidden = SimpleImageLayout(
        presets=reference_presets(settings),
        settings=settings,
        gate=_GATE,
    )
    assert hidden.source_picture is False
    assert hidden.mask is False
    assert settings.get("image.source") is None
    assert settings.get("image.mask") is None
    with pytest.raises(ValueError, match="source"):
        hidden.set_source_path("in.png")
    with pytest.raises(ValueError, match="mask"):
        hidden.set_mask_path("mask.png")
    shown_settings = reference_settings()
    shown = SimpleImageLayout(
        presets=reference_presets(shown_settings),
        settings=shown_settings,
        gate=_GATE,
        source_picture=True,
        mask=True,
    )
    shown.set_source_path("in.png")
    shown.set_mask_path("mask.png")
    assert shown.live_settings()["image.source"] == "in.png"
    assert shown.live_settings()["image.mask"] == "mask.png"
    source = shown_settings.get("image.source")
    mask = shown_settings.get("image.mask")
    assert source is not None and source.expert is False
    assert mask is not None and mask.expert is True
    with pytest.raises(ValueError, match="shared settings"):
        SimpleImageLayout(image_presets(), gate=_GATE, source_picture=True)


def test_finish_blocks_a_missing_picture_before_the_gate(tmp_path: Path) -> None:
    class _Seen:
        def __init__(self) -> None:
            self.outputs: list[ReviewedOutput] = []

        def review_prompt(self, prompt: str) -> str | None:
            del prompt
            return None

        def review_result(self, prompt: str, item_id: str, output: ReviewedOutput) -> str | None:
            del prompt, item_id
            self.outputs.append(output)
            return None

    gate = _Seen()
    layout = SimpleImageLayout(image_presets(), gate=gate)
    layout.set_prompt("a hill")
    assert layout.generate() is True
    assert layout.finish("missing") is None
    assert gate.outputs == []
    assert layout.phase is Phase.BLOCKED
    layout.cancel()
    layout.set_prompt("a hill")
    assert layout.generate() is True
    path = tmp_path / "out.png"
    assert layout.finish("path", ReviewedOutput(path=path)) is not None
    assert gate.outputs[0].path == path
    layout.set_prompt("bytes")
    assert layout.generate() is True
    payload = ReviewedOutput(data=b"png", mime="image/png")
    assert layout.finish("bytes", payload) is not None
    assert gate.outputs[1] is payload


def test_voices_chapters_and_input_path(tmp_path: Path) -> None:
    settings = reference_settings()
    layout = SimpleImageLayout(
        presets=reference_presets(settings),
        settings=settings,
        gate=_GATE,
        state_dir=tmp_path,
    )
    layout.set_voices((("anya", "Anya"), ("boris", "Boris")))
    assert layout.voice_id == "anya"
    assert layout.live_settings()["shell.voice"] == "anya"
    layout.select_voice("boris")
    assert layout.get("shell.voice") == "boris"
    again = SimpleImageLayout(
        presets=reference_presets(settings),
        settings=settings,
        gate=_GATE,
        state_dir=tmp_path,
    )
    assert again.get("shell.voice") == "boris"
    layout.set_input_path(str(tmp_path / "book.epub"))
    assert layout.input_path() == str((tmp_path / "book.epub").resolve())
    layout.set_chapters((("One", 0), ("Two", 1500)))
    assert layout.chapters == (("One", 0), ("Two", 1500))
    layout.set_audio_output("/books/book/audiobook.wav")
    assert layout.audio_output == "/books/book/audiobook.wav"
    with pytest.raises(ValueError):
        layout.set_voices(())
    with pytest.raises(ValueError):
        layout.set_chapters(((" ", 0),))
