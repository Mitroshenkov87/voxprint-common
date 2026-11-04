"""Shared image save format, save helper, and WebP XMP metadata."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from voxprint_common.image import build_xmp, parse_xmp, read_metadata, save_image
from voxprint_common.suite_settings import file, image_save_format, set_image_save_format

Image = pytest.importorskip("PIL.Image")

_METADATA = {"prompt": 'A "red" cat & dog', "seed": 123456789, "loras": ["flat.safetensors"]}


def test_default_setting_is_lossless_webp() -> None:
    assert image_save_format() == {"format": "webp", "quality": None}


def test_lossy_quality_round_trips_through_the_suite_file() -> None:
    stored = set_image_save_format({"format": "webp", "quality": 80})
    assert stored == {"format": "webp", "quality": 80}
    assert image_save_format() == {"format": "webp", "quality": 80}
    saved = json.loads(file().read_text(encoding="utf-8"))
    assert saved["image.save_format"] == {"format": "webp", "quality": 80}


@pytest.mark.parametrize(
    "value",
    [
        {"format": "webp", "quality": 0},
        {"format": "webp", "quality": 101},
        {"format": "webp", "quality": "80"},
        {"format": "png", "quality": 80},
        {"format": "tiff"},
    ],
)
def test_bad_save_format_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        set_image_save_format(value)  # type: ignore[arg-type]


def test_save_image_uses_setting_and_fixes_extension(tmp_path: Path) -> None:
    set_image_save_format({"format": "webp"})
    image = Image.new("RGB", (32, 32), (10, 200, 30))
    target = save_image(image, tmp_path / "result.png")
    assert target == tmp_path / "result.webp"
    with Image.open(target) as loaded:
        assert loaded.format == "WEBP"
        assert loaded.getpixel((0, 0)) == (10, 200, 30)


def test_lossy_webp_applies_quality(tmp_path: Path) -> None:
    noise = Image.effect_noise((64, 64), 64).convert("RGB")
    lossless = save_image(noise, tmp_path / "lossless", {"format": "webp"})
    lossy = save_image(noise, tmp_path / "lossy", {"format": "webp", "quality": 10})
    assert lossy.stat().st_size < lossless.stat().st_size


def test_save_image_writes_other_formats(tmp_path: Path) -> None:
    image = Image.new("RGB", (16, 16), (1, 2, 3))
    for setting, name in [({"format": "png"}, "out.png"), ("jpeg", "out.jpg")]:
        target = save_image(image, tmp_path / name, setting)
        assert target.name == name
        with Image.open(target) as loaded:
            assert loaded.format == name.split(".")[-1].upper().replace("JPG", "JPEG")
    transparent = Image.new("RGBA", (8, 8), (255, 0, 0, 128))
    save_image(transparent, tmp_path / "alpha", {"format": "jpeg"})


def test_metadata_requires_webp(tmp_path: Path) -> None:
    image = Image.new("RGB", (8, 8))
    with pytest.raises(ValueError, match="webp"):
        save_image(image, tmp_path / "x", {"format": "png"}, metadata=_METADATA)


def test_unavailable_feature_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    features = pytest.importorskip("PIL.features")
    monkeypatch.setattr(features, "check", lambda name: False)
    with pytest.raises(RuntimeError, match="AVIF"):
        save_image(Image.new("RGB", (8, 8)), tmp_path / "x", {"format": "avif"})


def test_xmp_round_trip_through_webp(tmp_path: Path) -> None:
    image = Image.new("RGB", (16, 16), (1, 2, 3))
    target = save_image(image, tmp_path / "meta", metadata=_METADATA)
    assert read_metadata(target) == _METADATA


def test_xmp_prompt_special_characters_round_trip_through_webp(tmp_path: Path) -> None:
    prompt = "first & \"quoted\" <tag> 'single' >\r\nsecond line\nthird <&> 'line' \"end\""
    image = Image.new("RGB", (16, 16), (1, 2, 3))
    target = save_image(image, tmp_path / "special", metadata={"prompt": prompt})
    assert read_metadata(target)["prompt"] == prompt


def test_xmp_packet_round_trip() -> None:
    assert parse_xmp(build_xmp(_METADATA)) == _METADATA
    assert parse_xmp(build_xmp({})) == {"prompt": None, "seed": None, "loras": []}


def test_import_does_not_load_pillow() -> None:
    script = "import sys, voxprint_common.image; raise SystemExit(1 if 'PIL' in sys.modules else 0)"
    completed = subprocess.run([sys.executable, "-c", script], check=False)
    assert completed.returncode == 0
