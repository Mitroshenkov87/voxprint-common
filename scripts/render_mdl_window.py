"""Paint offscreen PNGs of the download window.

Run with ``QT_QPA_PLATFORM=offscreen``. The views are scripted. Nothing is
downloaded.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from voxprint_common.i18n import set_language, tr_lang
from voxprint_common.mdl.present import (
    AppSlot,
    ItemState,
    ManageRow,
    ManageView,
    QueueItem,
    Snapshot,
    format_size,
    view_for,
)
from voxprint_common.ui.mdl_window import grab_view

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "img" / "mdl-window"
ART = Path("/opt/cursor/artifacts/mdl-window")


def _item(
    key: str,
    title: str,
    *,
    state: ItemState,
    source: str,
    size: int,
    received: int,
    apps: list[str],
    speed: float = 0,
    eta: float | None = None,
    names: dict[str, str] | None = None,
) -> QueueItem:
    return QueueItem(
        key=key,
        names=names or {"en": title, "ru": title, "de": title, "uk": title, "lv": title},
        fallback=title,
        size=size,
        received=received,
        state=state,
        source=source,
        speed_bps=speed,
        eta_s=eta,
        apps=list(apps),
    )


def _named(table: dict[str, str]) -> dict[str, str]:
    return {code: table[code] for code in ("en", "ru", "de", "uk", "lv")}


_CLARITY = [
    {
        "en": "Video enhancement model",
        "ru": "Модель улучшения видео",
        "de": "Videoverbesserungsmodell",
        "uk": "Модель покращення відео",
        "lv": "Video uzlabošanas modelis",
    },
    {
        "en": "Runtime ONNX + TensorRT",
        "ru": "Runtime ONNX + TensorRT",
        "de": "Runtime ONNX + TensorRT",
        "uk": "Runtime ONNX + TensorRT",
        "lv": "Runtime ONNX + TensorRT",
    },
    {
        "en": "Face restoration",
        "ru": "Восстановление лиц",
        "de": "Gesichtsrekonstruktion",
        "uk": "Відновлення облич",
        "lv": "Seju atjaunošana",
    },
    {
        "en": "Frame stabilizer",
        "ru": "Стабилизация кадров",
        "de": "Bildstabilisierung",
        "uk": "Стабілізація кадрів",
        "lv": "Kadru stabilizācija",
    },
    {
        "en": "Colour model",
        "ru": "Цветовая модель",
        "de": "Farbmodell",
        "uk": "Колірна модель",
        "lv": "Krāsu modelis",
    },
]

_ENLARGE = {
    "en": "Upscale model",
    "ru": "Модель увеличения",
    "de": "Vergrößerungsmodell",
    "uk": "Модель збільшення",
    "lv": "Palielināšanas modelis",
}

_LEVI = {
    "en": "Levi narrator voice",
    "ru": "Голос диктора Levi",
    "de": "Erzählerstimme Levi",
    "uk": "Голос диктора Levi",
    "lv": "Diktora balss Levi",
}

_QWEN = {
    "en": "Qwen3-TTS speech model",
    "ru": "Модель речи Qwen3-TTS",
    "de": "Sprachmodell Qwen3-TTS",
    "uk": "Модель мовлення Qwen3-TTS",
    "lv": "Runas modelis Qwen3-TTS",
}


def _queue(lang: str) -> Snapshot:
    names = [row[lang] for row in _CLARITY]
    specs = [
        (ItemState.DOWNLOADING, "huggingface", 480_000_000, 190_000_000, 2_400_000, 120),
        (ItemState.VERIFYING, "kaggle", 90_000_000, 90_000_000, 0, None),
        (ItemState.PENDING, "modelscope", 220_000_000, 0, 0, None),
        (ItemState.READY, "huggingface", 40_000_000, 40_000_000, 0, None),
        (ItemState.PENDING, "huggingface", 15_000_000, 0, 0, None),
    ]
    items = []
    for index, (state, source, size, received, speed, eta) in enumerate(specs):
        items.append(
            _item(
                f"{index:064x}",
                names[index],
                state=state,
                source=source,
                size=size,
                received=received,
                apps=["clarity"],
                speed=speed,
                eta=eta,
                names={code: names[index] for code in ("en", "ru", "de", "uk", "lv")},
            )
        )
    snap = Snapshot(items=items, apps=[AppSlot("clarity", "")])
    snap.recompute()
    return snap


def _paused(lang: str) -> Snapshot:
    title = _ENLARGE[lang]
    item = _item(
        "a" * 64,
        title,
        state=ItemState.PAUSED,
        source="huggingface",
        size=800_000_000,
        received=260_000_000,
        apps=["enlarge"],
        names={code: title for code in ("en", "ru", "de", "uk", "lv")},
    )
    snap = Snapshot(items=[item], apps=[AppSlot("enlarge", "")], paused=True)
    snap.recompute()
    return snap


def _ready(lang: str) -> Snapshot:
    title = _ENLARGE[lang]
    item = _item(
        "b" * 64,
        title,
        state=ItemState.READY,
        source="local",
        size=800_000_000,
        received=800_000_000,
        apps=["enlarge"],
        names={code: title for code in ("en", "ru", "de", "uk", "lv")},
    )
    snap = Snapshot(items=[item], apps=[AppSlot("enlarge", "")])
    snap.recompute()
    return snap


def _error(lang: str) -> Snapshot:
    title = _ENLARGE[lang]
    item = _item(
        "c" * 64,
        title,
        state=ItemState.FAILED,
        source="huggingface",
        size=800_000_000,
        received=20_000_000,
        apps=["enlarge"],
        names={code: title for code in ("en", "ru", "de", "uk", "lv")},
    )
    item.error_code = "disk"
    snap = Snapshot(
        items=[item],
        apps=[AppSlot("enlarge", "")],
        boot_code="disk",
        boot_technical="OSError: [Errno 28] No space left on device",
        boot_params={
            "path": "/home/alex/.local/share/voxprint/models",
            "need_bytes": str(5_200_000_000),
            "free_bytes": str(180_000_000),
        },
    )
    snap.recompute()
    return snap


def _multi() -> Snapshot:
    clarity = _item(
        "d" * 64,
        _CLARITY[0]["en"],
        state=ItemState.READY,
        source="huggingface",
        size=120_000_000,
        received=120_000_000,
        apps=["clarity"],
        names=_named(_CLARITY[0]),
    )
    runtime = _item(
        "e" * 64,
        _CLARITY[1]["en"],
        state=ItemState.READY,
        source="huggingface",
        size=86_000_000,
        received=86_000_000,
        apps=["clarity"],
        names=_named(_CLARITY[1]),
    )
    enlarge = _item(
        "f" * 64,
        _ENLARGE["en"],
        state=ItemState.DOWNLOADING,
        source="kaggle",
        size=900_000_000,
        received=240_000_000,
        apps=["enlarge"],
        speed=3_100_000,
        eta=210,
        names=_named(_ENLARGE),
    )
    levi = _item(
        "1" * 64,
        _LEVI["en"],
        state=ItemState.PENDING,
        source="huggingface",
        size=180_000_000,
        received=0,
        apps=["audiobook-builder"],
        names=_named(_LEVI),
    )
    qwen = _item(
        "2" * 64,
        _QWEN["en"],
        state=ItemState.PENDING,
        source="huggingface",
        size=1_600_000_000,
        received=0,
        apps=["audiobook-builder"],
        names=_named(_QWEN),
    )
    snap = Snapshot(
        items=[clarity, runtime, enlarge, levi, qwen],
        apps=[
            AppSlot("clarity", ""),
            AppSlot("enlarge", ""),
            AppSlot("audiobook-builder", ""),
        ],
    )
    snap.recompute()
    return snap


def _manage(lang: str) -> ManageView:
    source = tr_lang("mdl.row.from", lang, source=tr_lang("mdl.source.huggingface", lang))
    installed = ManageRow(
        key="enhance",
        title=_CLARITY[0][lang],
        meta=" · ".join((format_size(120_000_000, lang=lang), source)),
        state="ready",
        state_label=tr_lang("mdl.manage.status.ok", lang),
        action="remove",
        action_label=tr_lang("mdl.manage.remove", lang),
        note=tr_lang("mdl.manage.shared", lang, apps="VOXPrint AI Enlarge"),
    )
    available = ManageRow(
        key="colour",
        title=_CLARITY[4][lang],
        meta=" · ".join(
            (
                format_size(640_000_000, lang=lang),
                tr_lang("mdl.manage.min_vram", lang, gb=8),
            )
        ),
        state="pending",
        state_label=tr_lang("mdl.manage.status.missing", lang),
        action="add",
        action_label=tr_lang("mdl.manage.add", lang),
    )
    return ManageView(
        installed=(installed,),
        available=(available,),
        disk=tr_lang(
            "mdl.manage.disk",
            lang,
            used=format_size(120_000_000, lang=lang),
            free=format_size(51_750_000_000, lang=lang),
            app="VOXPrint AI Clarity",
            path="/home/alex/.local/share/voxprint/models",
        ),
        empty_installed=tr_lang("mdl.manage.empty_installed", lang, app="VOXPrint AI Clarity"),
        empty_available=tr_lang("mdl.manage.empty_available", lang),
    )


def _paint(name: str, lang: str, snap: Snapshot, *, mode: str = "queue", size: tuple[int, int], background: str = "") -> None:
    set_language(lang)
    manage = _manage(lang) if mode == "manage" else None
    focus = "clarity" if mode == "manage" or any(slot.app_id == "clarity" for slot in snap.apps) else snap.apps[0].app_id
    if name.endswith("paused") or name.endswith("all-set") or name.endswith("error"):
        focus = "enlarge"
    view = view_for(
        snap,
        mode=mode,
        focus_app=focus,
        manage=manage,
        lang=lang,
        background=background,
    )
    path = OUT / f"{name}.png"
    print(f"painting {name}", flush=True)
    grab_view(view, path, size)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    wide = (980, 720)
    narrow = (720, 640)
    for lang in ("en", "ru", "uk", "lv"):
        _paint(f"{lang}-queue", lang, _queue(lang), size=wide if lang in {"en", "ru"} else narrow)
    _paint("en-paused", "en", _paused("en"), size=wide)
    _paint("ru-paused", "ru", _paused("ru"), size=wide)
    _paint("en-all-set", "en", _ready("en"), size=wide)
    _paint("ru-all-set", "ru", _ready("ru"), size=wide)
    _paint("en-error", "en", _error("en"), size=wide)
    _paint("ru-error", "ru", _error("ru"), size=wide)
    _paint("de-error", "de", _error("de"), size=narrow)
    _paint("en-multi", "en", _multi(), size=wide)
    _paint("ru-multi", "ru", _multi(), size=wide)
    _paint("de-multi", "de", _multi(), size=narrow)
    empty = Snapshot(apps=[AppSlot("clarity", "")])
    _paint("en-manage", "en", empty, mode="manage", size=wide)
    _paint("ru-manage", "ru", empty, mode="manage", size=wide)
    _paint("de-manage", "de", empty, mode="manage", size=narrow)
    _paint(
        "en-background",
        "en",
        _queue("en"),
        size=wide,
        background=tr_lang("mdl.subtitle.background", "en"),
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
