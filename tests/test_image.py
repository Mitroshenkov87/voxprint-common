"""Shared image save format, save helper, and XMP metadata."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from voxprint_common.image import (
    LOSSY_DEFAULT_QUALITY,
    build_xmp,
    default_quality,
    parse_xmp,
    read_metadata,
    save_image,
)
from voxprint_common.suite_settings import file, image_save_format, set_image_save_format

Image = pytest.importorskip("PIL.Image")

_PROMPT = "café & \"quoted\" <tag>\r\nsecond line\nthird <&> 'line'"
_INPUT = {
    "prompt": _PROMPT,
    "seed": 0,
    "steps": 20,
    "base_model": "moss",
    "lora_strength": 0.0,
    "loras": [
        "flat.safetensors",
        {
            "name": "style.safetensors",
            "strength": 0.5,
            "sha256": "ab" * 32,
            "base": "z-image",
        },
        {"name": "alias.safetensors", "strength": 1, "base_model": "moss"},
    ],
}
_EXPECTED = {
    "prompt": _PROMPT,
    "seed": 0,
    "steps": 20,
    "base": "moss",
    "base_model": "moss",
    "lora_strength": 0.0,
    "loras": [
        {"name": "flat.safetensors", "strength": None, "sha256": None, "base": None},
        {
            "name": "style.safetensors",
            "strength": 0.5,
            "sha256": "ab" * 32,
            "base": "z-image",
        },
        {"name": "alias.safetensors", "strength": 1.0, "sha256": None, "base": "moss"},
    ],
}
_EMPTY = {
    "prompt": None,
    "seed": None,
    "steps": None,
    "base": None,
    "base_model": None,
    "lora_strength": None,
    "loras": [],
}
_REAL_HOME = Path.home() / ".local" / "share" / "voxprint-ai-suite"


def _skip_without_feature(fmt: str) -> None:
    feature = {"jpeg": "jpg", "avif": "avif"}.get(fmt)
    if feature is None:
        return
    features = pytest.importorskip("PIL.features")
    if not features.check(feature):
        pytest.skip(f"Pillow has no {fmt} support")


def test_settings_stay_off_the_real_home() -> None:
    resolved = file().resolve()
    assert resolved != _REAL_HOME
    assert _REAL_HOME not in resolved.parents


def test_default_setting_is_lossless_webp() -> None:
    assert image_save_format() == {"format": "webp", "quality": None, "metadata": False}
    assert default_quality(None) is None
    assert default_quality("master") is None
    assert default_quality("picture") == LOSSY_DEFAULT_QUALITY == 90


def test_lossy_quality_round_trips_through_the_suite_file() -> None:
    stored = set_image_save_format({"format": "webp", "quality": 80})
    assert stored == {"format": "webp", "quality": 80, "metadata": False}
    assert image_save_format() == stored
    saved = json.loads(file().read_text(encoding="utf-8"))
    assert saved["image.save_format"] == stored
    assert _REAL_HOME not in file().resolve().parents


@pytest.mark.parametrize(
    "value",
    [
        {"format": "webp", "quality": 0},
        {"format": "webp", "quality": 101},
        {"format": "webp", "quality": "80"},
        {"format": "png", "quality": 80},
        {"format": "tiff"},
        {"format": "webp", "metadata": "yes"},
        {"format": "webp", "metadata": 1},
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


def test_picture_purpose_is_lossy_webp_and_keeps_an_explicit_format(tmp_path: Path) -> None:
    noise = Image.effect_noise((64, 64), 64).convert("RGB")
    master = save_image(noise, tmp_path / "master", purpose="master")
    picture = save_image(noise, tmp_path / "picture", purpose="picture")
    custom = save_image(
        noise, tmp_path / "custom", {"format": "webp", "quality": 10}, purpose="picture"
    )
    assert master.suffix == picture.suffix == ".webp"
    assert picture.stat().st_size < master.stat().st_size
    assert custom.stat().st_size < picture.stat().st_size
    png = save_image(noise, tmp_path / "shot.png", {"format": "png"}, purpose="picture")
    assert png.suffix == ".png"
    with pytest.raises(ValueError):
        save_image(noise, tmp_path / "bad", purpose="thumb")


def test_save_image_writes_other_formats(tmp_path: Path) -> None:
    image = Image.new("RGB", (16, 16), (1, 2, 3))
    for setting, name in [({"format": "png"}, "out.png"), ("jpeg", "out.jpg")]:
        _skip_without_feature(str(setting if isinstance(setting, str) else setting["format"]))
        target = save_image(image, tmp_path / name, setting)
        assert target.name == name
        with Image.open(target) as loaded:
            assert loaded.format == name.split(".")[-1].upper().replace("JPG", "JPEG")
    transparent = Image.new("RGBA", (8, 8), (255, 0, 0, 128))
    save_image(transparent, tmp_path / "alpha", {"format": "jpeg"})


def test_unavailable_feature_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    features = pytest.importorskip("PIL.features")
    monkeypatch.setattr(features, "check", lambda name: False)
    with pytest.raises(RuntimeError, match="AVIF"):
        save_image(Image.new("RGB", (8, 8)), tmp_path / "x", {"format": "avif"})


def test_xmp_packet_round_trip() -> None:
    assert parse_xmp(build_xmp(_INPUT)) == _EXPECTED
    assert parse_xmp(build_xmp({})) == _EMPTY


def test_metadata_is_off_unless_asked(tmp_path: Path) -> None:
    image = Image.new("RGB", (8, 8), (4, 5, 6))
    silent = save_image(image, tmp_path / "off", metadata=_INPUT)
    assert silent.suffix == ".webp"
    assert read_metadata(silent) == _EMPTY
    opted = save_image(image, tmp_path / "arg", metadata=_INPUT, write_metadata=True)
    assert read_metadata(opted) == _EXPECTED
    set_image_save_format({"format": "webp", "metadata": True})
    via_setting = save_image(image, tmp_path / "setting", metadata=_INPUT)
    assert read_metadata(via_setting) == _EXPECTED
    forced_off = save_image(image, tmp_path / "forced-off", metadata=_INPUT, write_metadata=False)
    assert read_metadata(forced_off) == _EMPTY


@pytest.mark.parametrize("fmt", ["webp", "png", "jpeg", "avif"])
def test_metadata_round_trip_per_format(tmp_path: Path, fmt: str) -> None:
    _skip_without_feature(fmt)
    image = Image.new("RGB", (16, 16), (1, 2, 3))
    target = save_image(image, tmp_path / "meta.png", {"format": fmt}, _INPUT, write_metadata=True)
    with Image.open(target) as loaded:
        if fmt == "png":
            assert loaded.info.get("XML:com.adobe.xmp")
        else:
            assert loaded.info.get("xmp")
            exif = loaded.getexif()
            assert exif.get(0x010E)
            assert exif.get(0x9286)
    assert read_metadata(target) == _EXPECTED
    assert read_metadata(target)["prompt"] == _PROMPT


def test_import_does_not_load_pillow() -> None:
    script = "import sys, voxprint_common.image; raise SystemExit(1 if 'PIL' in sys.modules else 0)"
    completed = subprocess.run([sys.executable, "-c", script], check=False)
    assert completed.returncode == 0
