"""Paint the manage-models window at 1280 px, in English and Russian.

Run with ``QT_QPA_PLATFORM=offscreen``. The rows are scripted. Nothing is
hashed. A child process paints the same window at 150% (``QT_SCALE_FACTOR``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _windows_font_dir() -> None:
    """Point offscreen Qt at the Windows font folder when nothing else did."""
    if os.name != "nt" or os.environ.get("QT_QPA_FONTDIR"):
        return
    fonts = r"C:\Windows\Fonts"
    if os.path.isdir(fonts):
        os.environ["QT_QPA_FONTDIR"] = fonts


_windows_font_dir()

if os.environ.get("MDL_RENDER_SCALE") == "150":
    os.environ["QT_SCALE_FACTOR"] = "1.5"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from voxprint_common.i18n import set_language, tr_lang
from voxprint_common.mdl.present import (
    ManageRow,
    ManageView,
    Snapshot,
    display_path,
    format_size,
    view_for,
)
from voxprint_common.ui.mdl_window import grab_view

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "renders"
ART = Path("/opt/cursor/artifacts/renders")

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


def main() -> None:
    """Write the 100% renders, then the 150% renders in a child process."""
    OUT.mkdir(parents=True, exist_ok=True)
    scaled = os.environ.get("MDL_RENDER_SCALE") == "150"
    suffix = "-150" if scaled else ""
    for lang in ("en", "ru"):
        _paint(lang, suffix)
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
