"""Chord spelling, conflicts, context, and suite-settings overrides."""

from __future__ import annotations

import json

from voxprint_common.suite_settings import file, keymap_document
from voxprint_common.ui.commands import CommandSpec, Registry
from voxprint_common.ui.keymap import (
    display_chord,
    effective,
    keymap_from_document,
    load_overrides,
    normalize,
    save_overrides,
)
from voxprint_common.ui.palette import exact_bonus, fuzzy_score, score_text


def _registry() -> Registry:
    return Registry(
        [
            CommandSpec("view.palette", "command.view.palette", "Mod+K"),
            CommandSpec("file.save", "command.file.save", "Mod+S"),
            CommandSpec("edit.undo", "command.edit.undo", "Mod+Z"),
            CommandSpec("canvas.fit", "command.view.focus_canvas", "Mod+0", when="canvas"),
            CommandSpec(
                "file.preferences",
                "command.file.save",
                "Mod+,",
                platforms=(("darwin", "Mod+,"), ("linux", "Ctrl+,"), ("win32", "Ctrl+,")),
            ),
            CommandSpec(
                "edit.find", "command.view.palette", "Mod+F", when="text", enabled_id="find"
            ),
        ]
    )


def test_mod_is_ctrl_off_macos_and_distinct_on_macos() -> None:
    assert normalize("mod+shift+alt+v") == "Mod+Alt+Shift+V"
    assert normalize("Mod+=") == "Mod+="
    assert normalize("Mod++") == "Mod++"
    assert normalize("f12") == "F12"
    assert normalize("Ctrl+pagedown") == "Ctrl+PageDown"
    assert normalize("cmd+k") == "Mod+K"
    assert effective("Ctrl+K", "linux") == "Mod+K"
    assert effective("Ctrl+K", "win32") == "Mod+K"
    assert effective("Ctrl+K", "darwin") == "Ctrl+K"
    assert display_chord("Mod+K", "linux") == "Ctrl+K"
    assert display_chord("Mod+K", "darwin") == "Cmd+K"
    for junk in ("", "Mod+", "Hyper+K", "F99", "Mod+Shift+NotAKey", "Mod+\u0007", "x" * 100):
        assert normalize(junk) is None


def test_context_beats_global_and_text_keeps_the_keys() -> None:
    registry = _registry()
    keymap = load_overrides()
    assert keymap.resolve(registry, "Mod+K", platform="linux").command_id == "view.palette"
    assert keymap.resolve(registry, "Ctrl+K", platform="linux").command_id == "view.palette"
    canvas = keymap.resolve(registry, "Mod+0", active=("global", "canvas"), platform="linux")
    assert canvas is not None and canvas.command_id == "canvas.fit"
    assert keymap.resolve(registry, "Mod+0", active=("global",), platform="linux") is None
    assert keymap.resolve(registry, "Mod+K", text=True, platform="linux") is None
    assert keymap.resolve(registry, "Mod+F", text=True, platform="linux") is None
    registry.set_enabled("find", True)
    found = keymap.resolve(registry, "Mod+F", text=True, platform="linux")
    assert found is not None and found.command_id == "edit.find"
    assert keymap.resolve(registry, "Mod+F", active=("global",), platform="linux") is None


def test_conflicts_and_platform_variants() -> None:
    registry = Registry(
        [
            CommandSpec("view.palette", "command.view.palette", "Mod+K"),
            CommandSpec("file.save", "command.file.save", "Mod+K"),
            CommandSpec("canvas.fit", "command.view.focus_canvas", "Mod+0", when="canvas"),
            CommandSpec("view.zoom", "command.view.focus_canvas", "Mod+0"),
            CommandSpec(
                "file.preferences",
                "command.file.save",
                "Mod+,",
                platforms=(("linux", "Ctrl+,"), ("win32", "Ctrl+,"), ("darwin", "Mod+,")),
            ),
        ]
    )
    keymap = load_overrides()
    clashes = keymap.conflicts(registry, "linux")
    assert any(
        item.when == "global"
        and item.chord == "Mod+K"
        and set(item.command_ids) == {"file.save", "view.palette"}
        for item in clashes
    )
    assert not any(item.chord == "Mod+0" for item in clashes)
    assert (
        keymap.resolve(registry, "Mod+0", active=("global", "canvas"), platform="linux").command_id
        == "canvas.fit"
    )
    keymap.assign(registry, "file.save", "Mod+K", platform="linux")
    assert keymap.conflicts(registry, "linux") == ()
    assert keymap.resolve(registry, "Mod+K", platform="linux").command_id == "file.save"
    assert keymap.chord_for(registry, "view.palette", "linux") is None
    linux = keymap.chord_for(registry, "file.preferences", "linux")
    mac = keymap.chord_for(registry, "file.preferences", "darwin")
    assert linux == "Mod+,"
    assert mac == "Mod+,"
    assert display_chord(linux or "", "linux") == "Ctrl+,"


def test_bad_entries_are_dropped_and_the_file_round_trips() -> None:
    registry = _registry()
    loaded = keymap_from_document(
        {
            "schema": 1,
            "assigned": [
                {"chord": "Mod+K", "command": "file.save", "when": "global"},
                {"chord": "Not a key", "command": "file.save", "when": "global"},
                {"chord": "Mod+S", "command": "", "when": "global"},
                "junk",
            ],
            "removed": [
                {"chord": "Mod+Z", "when": "global"},
                {"chord": "Hyper+Z", "when": "global"},
            ],
        }
    )
    assert len(loaded.assigned) == 1
    assert len(loaded.removed) == 1
    assert keymap_from_document(["nope"]).assigned == []
    save_overrides(loaded)
    again = load_overrides()
    assert again.resolve(registry, "Mod+K", platform="linux").command_id == "file.save"
    assert again.resolve(registry, "Mod+Z", platform="linux") is None
    saved = json.loads(file().read_text(encoding="utf-8"))
    assert saved["schema"] == 1
    assert saved["keymap"]["schema"] == 1
    assert keymap_document()["assigned"][0]["command"] == "file.save"
    again.clear(registry, "file.save", "Mod+K", platform="linux")
    save_overrides(again)
    assert load_overrides().resolve(registry, "Mod+K", platform="linux") is None
    restored = load_overrides()
    restored.assign(registry, "view.palette", "Mod+K", platform="linux")
    save_overrides(restored)
    assert (
        load_overrides().resolve(registry, "Mod+K", platform="linux").command_id == "view.palette"
    )


def test_palette_scores_empty_exact_prefix_and_miss() -> None:
    assert fuzzy_score("", "Save") == 0
    assert score_text("", "Save", "file.save") == 0
    exact = score_text("save", "Save", "file.save")
    prefix = score_text("sa", "Save", "file.save")
    assert exact is not None and prefix is not None
    assert exact > prefix
    assert exact_bonus("save", ["Save"]) == 1000
    assert score_text("zzz", "Save", "file.save") is None
    assert fuzzy_score("sv", "Save") is not None
