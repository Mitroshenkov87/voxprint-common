"""Paint offscreen PNGs of the check-and-repair window.

Run with ``QT_QPA_PLATFORM=offscreen``. The rows are scripted. Nothing is hashed
or downloaded.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def _windows_font_dir() -> None:
    """Point offscreen Qt at the Windows font folder when nothing else did."""
    if os.name != "nt" or os.environ.get("QT_QPA_FONTDIR"):
        return
    fonts = r"C:\Windows\Fonts"
    if os.path.isdir(fonts):
        os.environ["QT_QPA_FONTDIR"] = fonts


_windows_font_dir()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from voxprint_common.mdl.progress_ui import render_verify_window
from voxprint_common.mdl.status import JobState, Status, Step

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "renders"
ART = Path("/opt/cursor/artifacts/renders")

_NAMES = {
    "en": (
        "Studio voice model",
        "Video enhancement model",
        "Face restoration",
        "Frame stabilizer",
    ),
    "ru": (
        "Студийный голос",
        "Модель улучшения видео",
        "Восстановление лиц",
        "Стабилизация кадров",
    ),
}


def _status(
    phase: str,
    rows: list[tuple[str, int, int, str]],
    *,
    done: int,
    total: int,
) -> Status:
    status = Status(
        JobState.DOWNLOADING,
        extra={"phase": phase, "mode": "verify", "done": done, "total": total},
    )
    status.steps = [
        Step(
            model=name,
            size=size,
            source="",
            speed_bps=0.0,
            eta_s=None,
            state=state,
            received=received,
            sha256="",
            apps=["model-forge"],
            item_id=name,
            display_name=name,
        )
        for name, size, received, state in rows
    ]
    return status


def _rows(lang: str, phase: str) -> list[tuple[str, int, int, str]]:
    labels = _NAMES[lang]
    if phase == "verify":
        return [
            (labels[0], 480_000_000, 200_000_000, "checking"),
            (labels[1], 1_200_000_000, 1_200_000_000, "ok"),
            (labels[2], 320_000_000, 0, "pending"),
            (labels[3], 90_000_000, 0, "pending"),
        ]
    if phase == "review":
        return [
            (labels[0], 480_000_000, 480_000_000, "ok"),
            (labels[1], 1_200_000_000, 1_200_000_000, "ok"),
            (labels[2], 320_000_000, 0, "corrupt"),
            (labels[3], 90_000_000, 0, "missing"),
        ]
    return [
        (labels[0], 480_000_000, 480_000_000, "ok"),
        (labels[1], 1_200_000_000, 760_000_000, "repairing"),
        (labels[2], 320_000_000, 0, "corrupt"),
        (labels[3], 90_000_000, 0, "missing"),
    ]


def main() -> None:
    """Write the English and Russian check and repair renders."""
    OUT.mkdir(parents=True, exist_ok=True)
    counters = {"verify": (1, 4), "review": (4, 4), "repair": (1, 2)}
    for lang in ("en", "ru"):
        for phase, stem in (
            ("verify", "mdl-verify"),
            ("review", "mdl-review"),
            ("repair", "mdl-repair"),
        ):
            done, total = counters[phase]
            path = OUT / f"{stem}-{lang}.png"
            print(f"painting {path.name}", flush=True)
            render_verify_window(
                _status(phase, _rows(lang, phase), done=done, total=total),
                path,
                lang=lang,
            )
    try:
        ART.mkdir(parents=True, exist_ok=True)
        for png in OUT.glob("*.png"):
            shutil.copyfile(png, ART / png.name)
    except OSError as exc:
        print(f"artifacts not copied: {exc}", flush=True)
    print(f"wrote {len(list(OUT.glob('*.png')))} pngs")


if __name__ == "__main__":
    main()
