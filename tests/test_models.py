"""Shared base-model registry and quant selector."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from voxprint_common.models import (
    MEMORY_BUDGET_GB,
    MODEL_PEAK_GB,
    QUANT_ORDER,
    default_model,
    list_models,
    lora_base_matches,
    normalize_base,
    select_quant,
)
from voxprint_common.suite_settings import (
    file,
    model_default,
    model_quant,
    set_model_default,
    set_model_quant,
    values,
)

_REAL_HOME = Path.home() / ".local" / "share" / "voxprint-ai-suite"


def test_registry_defaults_to_z_image_turbo() -> None:
    specs = list_models()
    by_id = {item.id: item for item in specs}
    assert [item.id for item in specs] == ["z-image-turbo", "z-image", "moss"]
    assert set(by_id) == set(MODEL_PEAK_GB)
    assert sum(item.default for item in specs) == 1
    assert by_id["z-image-turbo"].title == "Z-Image-Turbo"
    assert by_id["z-image-turbo"].default is True
    assert by_id["z-image-turbo"].licence == "Apache-2.0"
    assert by_id["z-image"].title == "Z-Image"
    assert by_id["z-image"].default is False
    assert by_id["moss"].title == "MOSS-Image"
    assert by_id["moss"].licence == "Apache-2.0"
    assert by_id["moss"].default is False
    assert default_model() == "z-image-turbo"
    assert MEMORY_BUDGET_GB == 14.0
    assert QUANT_ORDER == ("bf16", "fp8", "int8", "nf4")


def test_peak_table_covers_every_quant_and_mode() -> None:
    for peaks in MODEL_PEAK_GB.values():
        assert set(peaks) == {"generate", "train"}
        for mode in peaks.values():
            assert set(mode) == set(QUANT_ORDER)
            assert all(isinstance(value, float) and value > 0 for value in mode.values())


@pytest.mark.parametrize(
    ("model", "mode", "chosen"),
    [
        ("moss", "generate", "fp8"),
        ("moss", "train", "nf4"),
        ("z-image", "generate", "fp8"),
        ("z-image", "train", "nf4"),
        ("z-image-turbo", "generate", "fp8"),
        ("z-image-turbo", "train", "nf4"),
    ],
)
def test_default_budget_picks_the_best_quant_that_fits(model: str, mode: str, chosen: str) -> None:
    peaks = MODEL_PEAK_GB[model][mode]
    fitting = [quant for quant in QUANT_ORDER if peaks[quant] <= MEMORY_BUDGET_GB]
    assert fitting[0] == chosen
    assert select_quant(model, mode=mode) == chosen
    assert select_quant(model, max(peaks.values()), mode) == "bf16"
    floor = min(peaks.values())
    assert select_quant(model, floor, mode) == next(q for q in QUANT_ORDER if peaks[q] <= floor)
    with pytest.raises(ValueError):
        select_quant(model, floor - 0.01, mode)


def test_select_quant_rejects_unknown_model_and_mode() -> None:
    with pytest.raises(ValueError, match="unknown model"):
        select_quant("sdxl")
    with pytest.raises(ValueError, match="mode"):
        select_quant("moss", mode="edit")


@pytest.mark.parametrize(
    ("tag", "model_id"),
    [
        (None, "z-image"),
        ("", "z-image"),
        ("  ", "z-image"),
        ("z-image", "z-image"),
        ("Z-Image", "z-image"),
        ("z-image-turbo", "z-image"),
        ("Z-Image-Turbo", "z-image"),
        ("moss", "moss"),
        ("MOSS", "moss"),
    ],
)
def test_normalize_base(tag: object, model_id: str) -> None:
    # PIN (moss-final-spec item 4): untagged legacy LoRAs, including Garnis,
    # read as "z-image" by design (Z-Image family / training base), even
    # though the suite default generation model is now z-image-turbo.
    assert normalize_base(tag) == model_id


def test_normalize_base_rejects_unknown_tags() -> None:
    with pytest.raises(ValueError):
        normalize_base("sdxl")
    with pytest.raises(ValueError):
        normalize_base(1)


def test_lora_base_matches() -> None:
    assert lora_base_matches(None, "z-image") is True
    assert lora_base_matches("", "z-image") is True
    assert lora_base_matches(None, "z-image-turbo") is True
    assert lora_base_matches("z-image", "z-image-turbo") is True
    assert lora_base_matches("z-image-turbo", "z-image") is True
    assert lora_base_matches(None, "moss") is False
    assert lora_base_matches("moss", "moss") is True
    assert lora_base_matches("MOSS", "moss") is True
    assert lora_base_matches("moss", "z-image") is False
    assert lora_base_matches("moss", "z-image-turbo") is False
    assert lora_base_matches("z-image", "moss") is False
    with pytest.raises(ValueError):
        lora_base_matches("moss", "sdxl")


def test_model_settings_round_trip() -> None:
    assert _REAL_HOME not in file().resolve().parents
    assert model_default() == "z-image-turbo"
    assert model_quant() == "auto"
    assert values()["model.default"] == "z-image-turbo"
    assert values()["model.quant"] == "auto"
    assert set_model_default("z-image") == "z-image"
    assert model_default() == "z-image"
    assert set_model_default("MOSS") == "moss"
    assert set_model_quant("NF4") == "nf4"
    assert model_default() == "moss"
    assert model_quant() == "nf4"
    saved = json.loads(file().read_text(encoding="utf-8"))
    assert saved["model.default"] == "moss"
    assert saved["model.quant"] == "nf4"
    with pytest.raises(ValueError):
        set_model_default("sdxl")
    with pytest.raises(ValueError):
        set_model_quant("int4")


def test_unknown_model_settings_read_as_defaults() -> None:
    target = file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"model.default": "sdxl", "model.quant": "gguf"}) + "\n",
        encoding="utf-8",
    )
    assert model_default() == "z-image-turbo"
    assert model_quant() == "auto"


def test_import_does_not_load_torch() -> None:
    script = (
        "import sys, voxprint_common.models; raise SystemExit(1 if 'torch' in sys.modules else 0)"
    )
    completed = subprocess.run([sys.executable, "-c", script], check=False)
    assert completed.returncode == 0
