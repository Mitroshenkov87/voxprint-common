"""The minors gate stays outside settings, and adult content is a prompt policy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxprint_common.ui.presets import SettingState, parse_presets, reference_presets
from voxprint_common.ui.safety import (
    ReviewedOutput,
    SafetyGate,
    adult_prompt_policy,
    reject_reserved_key,
)
from voxprint_common.ui.settings import SettingSpec, SettingType, reference_settings
from voxprint_common.ui.simple_image import Phase, SimpleImageLayout

_SAFETY = Path(__file__).parents[1] / "src" / "voxprint_common" / "ui" / "safety.py"


class _Record:
    def __init__(
        self, *, prompt: str | None = None, result: str | None = None, boom: str = ""
    ) -> None:
        self.prompts: list[str] = []
        self.results: list[tuple[str, str]] = []
        self.outputs: list[ReviewedOutput] = []
        self.prompt_reason = prompt
        self.result_reason = result
        self.boom = boom

    def review_prompt(self, prompt: str) -> str | None:
        self.prompts.append(prompt)
        if self.boom == "prompt":
            raise RuntimeError("prompt review failed")
        return self.prompt_reason

    def review_result(self, prompt: str, item_id: str, output: ReviewedOutput) -> str | None:
        self.results.append((prompt, item_id))
        self.outputs.append(output)
        if self.boom == "result":
            raise RuntimeError("result review failed")
        return self.result_reason


def test_adult_content_policy_is_neutral_only_when_on() -> None:
    off = adult_prompt_policy(allowed=False)
    on = adult_prompt_policy(allowed=True)
    assert off.template
    assert off.negatives
    assert on.template == ""
    assert on.negatives == ""
    settings = reference_settings()
    layout = SimpleImageLayout(
        presets=reference_presets(settings),
        settings=settings,
        gate=_Record(),
    )
    assert layout.get("image.adult_content") is False
    assert layout.prompt_policy().template == off.template
    layout.set_value("image.adult_content", True)
    assert layout.prompt_policy().template == ""
    assert layout.prompt_policy().negatives == ""
    source = _SAFETY.read_text(encoding="utf-8")
    assert "Safety filter" not in source
    assert "Content filter" not in source
    assert "disable_safety" not in source
    assert set(SafetyGate.__protocol_attrs__) == {"review_prompt", "review_result"}


def test_gate_runs_for_every_example_preset_while_adult_content_is_off() -> None:
    settings = reference_settings()
    document = reference_presets(settings, enlarge_installed=True)
    gate = _Record()
    layout = SimpleImageLayout(
        presets=document,
        settings=settings,
        gate=gate,
    )
    assert layout.get("image.adult_content") is False
    assert layout.prompt_policy().negatives
    index = 0
    for group in document.groups:
        for option in group.options:
            layout.choose(group.group_id, option.option_id)
            prompt = f"picture {index}"
            layout.set_prompt(prompt)
            assert layout.generate() is True
            picture = ReviewedOutput(data=f"png-{index}".encode(), mime="image/png")
            item = layout.finish(f"id-{index}", picture)
            assert item is not None
            assert layout.get("image.adult_content") is False
            index += 1
    assert gate.prompts == [f"picture {i}" for i in range(index)]
    assert gate.results == [(f"picture {i}", f"id-{i}") for i in range(index)]
    assert [output.data for output in gate.outputs] == [f"png-{i}".encode() for i in range(index)]
    assert all(output.mime == "image/png" for output in gate.outputs)
    assert index == sum(len(group.options) for group in document.groups)


def test_gate_failure_blocks_the_picture() -> None:
    raised = SimpleImageLayout(gate=_Record(boom="prompt"))
    raised.set_prompt("a hill")
    assert raised.generate() is False
    assert raised.phase is Phase.BLOCKED
    assert raised.reason
    assert raised.history == []

    refused = SimpleImageLayout(gate=_Record(prompt="Stopped."))
    refused.set_prompt("a hill")
    assert refused.generate() is False
    assert refused.phase is Phase.BLOCKED
    assert refused.reason == "Stopped."

    with pytest.raises(TypeError, match="gate"):
        SimpleImageLayout()

    result_gate = _Record(boom="result")
    result = SimpleImageLayout(gate=result_gate)
    result.set_prompt("a hill")
    assert result.generate() is True
    assert result.finish("one", ReviewedOutput(data=b"png", mime="image/png")) is None
    assert result.phase is Phase.BLOCKED
    assert result.history == []
    assert result_gate.outputs[0].present()

    missing_gate = _Record()
    missing = SimpleImageLayout(gate=missing_gate)
    missing.set_prompt("a hill")
    assert missing.generate() is True
    assert missing.finish("gone") is None
    assert missing.phase is Phase.BLOCKED
    assert missing_gate.results == []

    words_gate = _Record(result="Not this picture.")
    words = SimpleImageLayout(gate=words_gate)
    words.set_prompt("a hill")
    assert words.generate() is True
    assert words.finish("one", ReviewedOutput(path=Path("picture.png"))) is None
    assert words.phase is Phase.BLOCKED
    assert words.reason == "Not this picture."
    assert words.history == []


def test_reserved_keys_fail_validation(tmp_path: Path) -> None:
    for key in ("safety", "safety.enabled", "image.minor", "shell.age_limit", "content.guard"):
        with pytest.raises(ValueError, match="reserved"):
            reject_reserved_key(key)
        with pytest.raises(ValueError, match="reserved"):
            SettingSpec(
                key,
                "setting.shell.steps",
                "setting.shell.steps.help",
                SettingType.BOOL,
                False,
            )
    reject_reserved_key("image.adult_content")
    reject_reserved_key("shell.guidance")
    payload = {
        "schema": "voxprint-presets/1",
        "groups": [
            {
                "id": "quality",
                "label_key": "simple.quality",
                "default": "fast",
                "options": [
                    {
                        "id": "fast",
                        "label_key": "simple.quality.fast",
                        "values": {"safety.minor": 1},
                    },
                    {
                        "id": "best",
                        "label_key": "simple.quality.best",
                        "values": {"safety.minor": 2},
                    },
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="reserved"):
        parse_presets(payload)
    settings = reference_settings()
    state = SettingState(reference_presets(settings), settings)
    with pytest.raises(ValueError, match="reserved"):
        state.set_value("image.age", 1)
    path = tmp_path / "image-settings.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "values": {"safety.enabled": True, "shell.steps": 20},
                "chosen": {
                    group.group_id: group.default for group in reference_presets(settings).groups
                },
                "overrides": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="reserved"):
        SettingState(reference_presets(settings), settings, state_dir=tmp_path)
