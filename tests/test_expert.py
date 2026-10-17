"""Expert mode: suite setting, filters, layout, and localised titles."""

from __future__ import annotations

import json
import sys

import pytest

from voxprint_common.i18n import tr_lang
from voxprint_common.suite_settings import expert_mode, file, set_ui_language
from voxprint_common.ui.commands import CommandSpec, Registry, reference_commands
from voxprint_common.ui.dock import (
    Float,
    Layout,
    PanelSpec,
    Split,
    SplitAxis,
    SplitSize,
    Tabs,
    layout_json,
    visible_layout,
)
from voxprint_common.ui.expert import ExpertMode, active_contexts
from voxprint_common.ui.keymap import WHEN_EXPERT, KeyMap
from voxprint_common.ui.settings import (
    IMAGE_CONTENT_FILTER,
    Choice,
    Settings,
    SettingSpec,
    SettingType,
    reference_settings,
)


def test_expert_mode_defaults_off_and_round_trips() -> None:
    assert expert_mode() is False
    mode = ExpertMode()
    assert mode.enabled() is False
    seen: list[bool] = []
    cancel = mode.subscribe(seen.append)
    assert mode.set_enabled(True) is True
    assert seen == [True]
    payload = json.loads(file().read_text(encoding="utf-8"))
    assert payload["ui.expert_mode"] is True
    set_ui_language("ru")
    kept = json.loads(file().read_text(encoding="utf-8"))
    assert kept["ui.expert_mode"] is True
    assert kept["ui_language"] == "ru"
    assert mode.set_enabled(True) is True
    assert seen == [True]
    cancel()
    assert mode.toggle() is False
    assert seen == [True]
    assert expert_mode() is False
    assert ExpertMode().enabled() is False
    file().write_text('{"ui.expert_mode": "yes"}\n', encoding="utf-8")
    assert expert_mode() is False
    with pytest.raises(ValueError):
        mode.set_enabled("yes")  # type: ignore[arg-type]
    assert "PySide6" not in sys.modules


def test_registry_hides_expert_commands_until_the_mode_is_on() -> None:
    registry = Registry(
        [
            CommandSpec("view.toggle_expert_mode", "command.view.toggle_expert_mode"),
            CommandSpec("canvas.steps", "setting.shell.steps", expert=True),
        ]
    )
    assert [spec.command_id for spec in registry.visible(expert=False)] == [
        "view.toggle_expert_mode"
    ]
    assert [spec.command_id for spec in registry.visible(expert=True)] == [
        "view.toggle_expert_mode",
        "canvas.steps",
    ]
    toggle = next(
        spec for spec in reference_commands() if spec.command_id == "view.toggle_expert_mode"
    )
    assert toggle.chord is None
    assert toggle.expert is False
    assert toggle.title_key == "command.view.toggle_expert_mode"


def test_settings_hide_expert_rows_and_content_filter_is_standard() -> None:
    settings = reference_settings()
    simple = [item.setting_id for item in settings.visible(expert=False)]
    assert simple == ["shell.pictures", "shell.save_folder"]
    shown = [item.setting_id for item in settings.visible(expert=True)]
    assert shown[:2] == ["shell.pictures", "shell.save_folder"]
    assert shown[2:] == [
        "shell.steps",
        "shell.sampler",
        "shell.guidance",
        "shell.seed",
        "shell.lora",
        "shell.negative",
        "image.content_filter",
    ]
    negative = settings.get("shell.negative")
    assert negative is not None and negative.type is SettingType.TEXT and negative.expert is True
    assert IMAGE_CONTENT_FILTER.expert is True
    assert IMAGE_CONTENT_FILTER.type is SettingType.BOOL
    assert IMAGE_CONTENT_FILTER.default is True
    assert IMAGE_CONTENT_FILTER.group == "image"
    assert IMAGE_CONTENT_FILTER.setting_id == "image.content_filter"
    steps = settings.get("shell.steps")
    assert steps is not None and steps.expert is True and steps.type is SettingType.INT
    sampler = settings.get("shell.sampler")
    assert sampler is not None and sampler.type is SettingType.ENUM and sampler.expert is True
    folder = settings.get("shell.save_folder")
    assert folder is not None and folder.type is SettingType.PATH and folder.expert is False
    custom = Settings(
        [
            SettingSpec(
                "preview.strength",
                "setting.shell.steps",
                "setting.shell.steps.help",
                SettingType.FLOAT,
                0.5,
                minimum=0.0,
                maximum=1.0,
            )
        ]
    )
    assert custom.visible(expert=False)[0].type is SettingType.FLOAT
    with pytest.raises(ValueError):
        SettingSpec(
            "bad.sampler",
            "setting.shell.sampler",
            "setting.shell.sampler.help",
            SettingType.ENUM,
            "missing",
            choices=(Choice("euler", "setting.shell.sampler.euler"),),
        )


def test_hiding_expert_panels_keeps_the_layout_json() -> None:
    layout = Layout(
        Split(
            SplitAxis.HORIZONTAL,
            SplitSize.ratio(0.5),
            Tabs(["prompt", "sampler"], 1),
            Tabs(["settings"], 0),
            120.0,
        )
    )
    panels = (
        PanelSpec("prompt", "shell.panel.prompt"),
        PanelSpec("sampler", "shell.panel.advanced", expert=True),
        PanelSpec("settings", "shell.panel.settings"),
    )
    saved = layout_json(layout)
    hidden = visible_layout(layout, panels, expert=False)
    assert hidden.panels() == ["prompt", "settings"]
    assert "sampler" in saved
    assert layout_json(layout) == saved
    restored = visible_layout(layout, panels, expert=True)
    assert restored.panels() == ["prompt", "sampler", "settings"]
    assert layout_json(layout) == saved
    held = Layout(Tabs(["canvas", "sampler"], 0))
    held.apply(Float("sampler", (12.0, 16.0, 180.0, 100.0)))
    before = layout_json(held)
    view = visible_layout(
        held,
        (PanelSpec("canvas", "shell.panel.canvas"), PanelSpec("sampler", "x", expert=True)),
        expert=False,
    )
    assert view.floating == []
    assert view.contains("canvas")
    assert layout_json(held) == before
    assert visible_layout(
        held,
        (PanelSpec("canvas", "shell.panel.canvas"), PanelSpec("sampler", "x", expert=True)),
        expert=True,
    ).floating


def test_expert_keymap_context_runs_only_while_the_mode_is_on() -> None:
    registry = Registry(
        [
            CommandSpec("view.palette", "command.view.palette", "Mod+K"),
            CommandSpec("canvas.latent", "command.view.palette", "Mod+L", when=WHEN_EXPERT),
            CommandSpec("canvas.steps", "setting.shell.steps", "Mod+Shift+S", expert=True),
        ]
    )
    keymap = KeyMap()
    assert WHEN_EXPERT == "expert"
    off = active_contexts("canvas", expert=False)
    on = active_contexts("canvas", expert=True)
    assert "expert" not in off
    assert off[0] == "global"
    assert "expert" in on
    assert keymap.resolve(registry, "Mod+L", active=off, platform="linux") is None
    found = keymap.resolve(registry, "Mod+L", active=on, platform="linux", expert=True)
    assert found is not None and found.command_id == "canvas.latent"
    assert keymap.resolve(registry, "Mod+Shift+S", active=on, platform="linux") is None
    steps = keymap.resolve(registry, "Mod+Shift+S", active=on, platform="linux", expert=True)
    assert steps is not None and steps.command_id == "canvas.steps"
    assert keymap.resolve(registry, "Mod+K", active=off, platform="linux") is not None


def test_toggle_expert_mode_titles() -> None:
    assert tr_lang("command.view.toggle_expert_mode", "en") == "Expert mode"
    assert tr_lang("command.view.toggle_expert_mode", "ru") == "Режим эксперта"
    assert tr_lang("command.view.toggle_expert_mode", "de") == "Expertenmodus"
    assert tr_lang("command.view.toggle_expert_mode", "uk") == "Режим експерта"
    assert tr_lang("command.view.toggle_expert_mode", "lv") == "Eksperta režīms"
