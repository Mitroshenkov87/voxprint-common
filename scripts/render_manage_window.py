"""Paint the manage-models window at 1280 px, in English and Russian.

Run with ``QT_QPA_PLATFORM=offscreen``. The rows are scripted. Nothing is
hashed. A child process paints the same window at 150% (``QT_SCALE_FACTOR``).

The 0.7.1 fixtures paint the grouped manage page: Canvas's 13-row table
(no real weights, 16 GB assumed) and a small audio app. They are written
to ``/workspace/voxprint-components/renders/0.7.1/`` in English only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

if os.environ.get("MDL_RENDER_SCALE") == "150":
    os.environ["QT_SCALE_FACTOR"] = "1.5"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from voxprint_common.gpu_probe import VramTier
from voxprint_common.i18n import set_language, tr_lang
from voxprint_common.mdl.present import (
    ManageRow,
    ManageView,
    Snapshot,
    display_path,
    format_size,
    group_manage_rows,
    view_for,
)
from voxprint_common.ui.mdl_window import grab_view

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "renders"
ART = Path("/opt/cursor/artifacts/renders")
OUT_071 = Path("/workspace/voxprint-components/renders/0.7.1")

_INSTALLED = (
    ("Flux Schnell", "Flux Schnell"),
    ("SDXL Base", "SDXL Base"),
    ("Real-ESRGAN", "Real-ESRGAN"),
    ("Whisper Large", "Whisper Large"),
    ("Z-Image-Turbo", "Z-Image-Turbo"),
)
_AVAILABLE = (("Extra detail", "Дополнительная детализация"),)


def _page(lang: str) -> ManageView:
    installed = []
    for names in _INSTALLED:
        title = names[0] if lang == "en" else names[1]
        installed.append(
            ManageRow(
                key=title,
                title=title,
                meta=" · ".join(
                    (
                        format_size(1_200_000_000, lang=lang),
                        tr_lang("mdl.manage.min_vram", lang, gb=8),
                    )
                ),
                state="ready",
                state_label=tr_lang("mdl.manage.status.ok", lang),
                action="remove",
                action_label=tr_lang("mdl.manage.remove", lang),
            )
        )
    missing_name = _AVAILABLE[0][0] if lang == "en" else _AVAILABLE[0][1]
    available = ManageRow(
        key="extra",
        title=missing_name,
        meta=format_size(640_000_000, lang=lang),
        state="pending",
        state_label=tr_lang("mdl.manage.status.missing", lang),
        action="add",
        action_label=tr_lang("mdl.manage.add", lang),
    )
    app = "VOXPrint AI Model Forge"
    return ManageView(
        installed=tuple(installed),
        available=(available,),
        disk=tr_lang(
            "mdl.manage.disk",
            lang,
            used=format_size(8_400_000_000, lang=lang),
            free=format_size(51_750_000_000, lang=lang),
            app=app,
            path=display_path("C:/Program Files/VOXPrint AI Suite/Model Forge"),
        ),
        empty_installed=tr_lang("mdl.manage.empty_installed", lang, app=app),
        empty_available=tr_lang("mdl.manage.empty_available", lang),
    )


def _paint(lang: str, suffix: str) -> None:
    set_language(lang)
    view = view_for(
        Snapshot(),
        mode="manage",
        focus_app="model-forge",
        manage=_page(lang),
        lang=lang,
    )
    path = OUT / f"mdl-manage-{lang}{suffix}.png"
    print(f"painting {path.name}", flush=True)
    grab_view(view, path, (1280, 900))
    print(f"  {path.name} bytes={path.stat().st_size}", flush=True)


# 0.7.1 fixtures: id, group, vram_tier, label, purpose, installed, size.
_CANVAS = (
    (
        "z-image-turbo-bf16",
        "generate",
        "24gb",
        "Z-Image Turbo (recommended)",
        "Fast, high-quality pictures from text; the default.",
        False,
        11_300_000_000,
    ),
    (
        "z-image-turbo-bf16-16gb",
        "generate",
        "16gb",
        "Z-Image Turbo for 16 GB cards",
        "The default model, sized to fit 16 GB graphics cards.",
        True,
        6_100_000_000,
    ),
    (
        "z-image-turbo-svdquant-int4",
        "generate",
        "16gb",
        "Z-Image Turbo, compact 4-bit",
        "Smaller and lighter; slightly lower detail.",
        True,
        3_900_000_000,
    ),
    (
        "z-image-turbo-svdquant-int4-r256",
        "generate",
        "16gb",
        "Z-Image Turbo, compact 4-bit (higher detail)",
        "4-bit build that keeps more fine detail.",
        False,
        4_400_000_000,
    ),
    (
        "z-image-creative-bf16",
        "generate",
        "24gb",
        "Z-Image Creative",
        "Slower, more varied and artistic results.",
        False,
        12_800_000_000,
    ),
    (
        "z-image-turbo-fun-controlnet-union-bf16",
        "edit",
        "24gb",
        "Pose & edge guidance",
        "Follows a pose, edges or depth from a reference picture.",
        False,
        10_500_000_000,
    ),
    (
        "z-image-turbo-fun-controlnet-union-bf16-lite",
        "edit",
        "16gb",
        "Pose & edge guidance, lite",
        "The same guidance, sized for 16 GB cards.",
        True,
        5_600_000_000,
    ),
    (
        "qwen3-4b-writer",
        "writer",
        "any",
        "Photoshoot writer (Qwen3 4B)",
        "Writes the shot list for Photoshoot series.",
        False,
        2_600_000_000,
    ),
    (
        "qwen3guard-gen-0.6b",
        "safety",
        "any",
        "Prompt safety check",
        "Checks prompts before generating.",
        True,
        1_500_000_000,
    ),
    (
        "siglip2-so400m-patch14-384",
        "safety",
        "any",
        "Image safety check",
        "Checks input and output pictures.",
        False,
        1_700_000_000,
    ),
    (
        "yunet-face",
        "safety",
        "any",
        "Face finder",
        "Finds faces for the safety and likeness checks.",
        True,
        60_000_000,
    ),
    (
        "fairface-age",
        "safety",
        "any",
        "Age check",
        "Part of the adults-only check.",
        False,
        220_000_000,
    ),
    (
        "nsfw-image-detection",
        "safety",
        "any",
        "Content check",
        "Classifies picture content for the 18+ setting.",
        False,
        350_000_000,
    ),
)

_AUDIO = (
    (
        "narrator-voice",
        "voice",
        "any",
        "Narrator voice",
        "Reads the book aloud in a warm studio voice.",
        True,
        700_000_000,
    ),
    (
        "character-voices",
        "voice",
        "any",
        "Character voices",
        "Distinct voices for dialogue and roles.",
        False,
        900_000_000,
    ),
    (
        "speech-to-text-large",
        "transcribe",
        "any",
        "Speech to text",
        "Turns recordings into text for editing.",
        False,
        1_400_000_000,
    ),
    (
        "audio-segmenter",
        "internal",
        "any",
        "Audio segmenter",
        "Splits long recordings into sentences.",
        True,
        120_000_000,
    ),
)


def _fixture_row(
    model_id: str,
    group: str,
    tier: str,
    label: str,
    purpose: str,
    installed: bool,
    size: int,
) -> ManageRow:
    lang = "en"
    bits = [format_size(size, lang=lang)]
    if tier != "any":
        bits.append(tr_lang("mdl.manage.min_vram", lang, gb=int(tier[:2])))
    removable = group not in {"safety", "internal"}
    if installed:
        return ManageRow(
            key=model_id,
            title=label,
            meta=" · ".join(bits),
            state="ready",
            state_label=tr_lang("mdl.manage.status.ok", lang),
            action="remove" if removable else "",
            action_label=tr_lang("mdl.manage.remove", lang) if removable else "",
            purpose=purpose,
            group=group,
            badge="" if tier == "any" else tier,
            removable=removable,
        )
    return ManageRow(
        key=model_id,
        title=label,
        meta=" · ".join(bits),
        state="pending",
        state_label=tr_lang("mdl.manage.status.missing", lang),
        action="add",
        action_label=tr_lang("mdl.manage.add", lang),
        purpose=purpose,
        group=group,
        badge="" if tier == "any" else tier,
        removable=removable,
    )


def _grouped_page(app_id: str, app_name: str, rows: tuple[tuple[object, ...], ...]) -> ManageView:
    lang = "en"
    built = [_fixture_row(*row) for row in rows]
    groups = group_manage_rows(built, tier=VramTier.UNKNOWN, lang=lang)
    return ManageView(
        installed=tuple(row for row in built if row.state == "ready"),
        available=tuple(row for row in built if row.state == "pending"),
        disk=tr_lang(
            "mdl.manage.disk",
            lang,
            used=format_size(21_300_000_000, lang=lang),
            free=format_size(451_200_000_000, lang=lang),
            app=app_name,
            path=display_path(f"C:/Program Files/VOXPrint AI Suite/{app_name}"),
        ),
        empty_installed=tr_lang("mdl.manage.empty_installed", lang, app=app_name),
        empty_available=tr_lang("mdl.manage.empty_available", lang),
        groups=groups,
    )


def _paint_fixture(
    name: str,
    app_id: str,
    app_name: str,
    rows: tuple[tuple[object, ...], ...],
    size: tuple[int, int] = (1280, 900),
) -> None:
    set_language("en")
    view = view_for(
        Snapshot(),
        mode="manage",
        focus_app=app_id,
        manage=_grouped_page(app_id, app_name, rows),
        lang="en",
    )
    suffix = "-150" if os.environ.get("MDL_RENDER_SCALE") == "150" else "-100"
    path = OUT_071 / f"mdl-manage-{name}{suffix}.png"
    print(f"painting {path}", flush=True)
    grab_view(view, path, size)
    print(f"  {path.name} bytes={path.stat().st_size}", flush=True)


def main() -> None:
    """Write the 100% renders, then the 150% renders in a child process."""
    OUT.mkdir(parents=True, exist_ok=True)
    OUT_071.mkdir(parents=True, exist_ok=True)
    scaled = os.environ.get("MDL_RENDER_SCALE") == "150"
    suffix = "-150" if scaled else ""
    for lang in ("en", "ru"):
        _paint(lang, suffix)
    _paint_fixture("canvas", "canvas", "Canvas", _CANVAS, (1280, 1120))
    _paint_fixture("audio", "audiobook-builder", "Audiobook Builder", _AUDIO)
    if not scaled:
        env = os.environ.copy()
        env["MDL_RENDER_SCALE"] = "150"
        env["QT_SCALE_FACTOR"] = "1.5"
        env["QT_QPA_PLATFORM"] = "offscreen"
        subprocess.check_call([sys.executable, str(Path(__file__).resolve())], env=env)
    try:
        ART.mkdir(parents=True, exist_ok=True)
        for png in OUT.glob("mdl-manage-*.png"):
            shutil.copyfile(png, ART / png.name)
    except OSError as exc:
        print(f"artifacts not copied: {exc}", flush=True)


if __name__ == "__main__":
    main()
