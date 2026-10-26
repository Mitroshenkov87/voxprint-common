"""Shared catalogs: language order, plurals, and app folders."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from voxprint_common.i18n import (
    LANGS,
    add_catalogs,
    available_languages,
    catalog_dir,
    detect_language,
    get_language,
    plural_category,
    reset,
    set_language,
    tr,
    tr_lang,
)
from voxprint_common.suite_settings import set_ui_language
from voxprint_common.ui.tokens import tokens_path
from voxprint_common.ui.variants import settings_for, surfaces

_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
_REQUIRED = {
    "en": {"one", "other"},
    "de": {"one", "other"},
    "ru": {"one", "few", "many", "other"},
    "uk": {"one", "few", "many", "other"},
    "lv": {"zero", "one", "other"},
}


def _flat(lang: str) -> dict[str, str]:
    payload = json.loads((catalog_dir() / f"{lang}.json").read_text(encoding="utf-8"))

    def walk(value: object, prefix: str) -> dict[str, str]:
        found: dict[str, str] = {}
        if not isinstance(value, dict):
            return found
        for key, item in value.items():
            dotted = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(item, str):
                found[dotted] = item
            elif isinstance(item, dict):
                found.update(walk(item, dotted))
        return found

    return walk(payload, "")


def test_language_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOXPRINT_LANG", "de")
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    reset()
    assert detect_language() == "de"
    set_ui_language("ru")
    reset()
    assert detect_language() == "ru"
    reset()


def test_system_locale_when_nothing_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOXPRINT_LANG", raising=False)
    monkeypatch.setenv("LANG", "lv_LV.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setattr("voxprint_common.i18n._windows_ui_language", lambda: None)
    reset()
    assert detect_language() == "lv"
    monkeypatch.setattr("voxprint_common.i18n._windows_ui_language", lambda: "de")
    reset()
    assert detect_language() == "de"
    assert set_language("uk") == "uk"
    assert get_language() == "uk"
    reset()


def test_plurals_for_russian_ukrainian_and_latvian() -> None:
    assert [plural_category(n, "ru") for n in (1, 2, 5, 11, 21, 22)] == [
        "one",
        "few",
        "many",
        "many",
        "one",
        "few",
    ]
    assert plural_category(3, "uk") == "few"
    assert [plural_category(n, "lv") for n in (0, 1, 2, 10, 11, 21)] == [
        "zero",
        "one",
        "other",
        "zero",
        "zero",
        "one",
    ]
    assert tr_lang("mdl.files", "ru", count=1) == "1 файл"
    assert tr_lang("mdl.files", "ru", count=2) == "2 файла"
    assert tr_lang("mdl.files", "ru", count=5) == "5 файлов"
    assert tr_lang("mdl.files", "lv", count=0) == "0 failu"
    assert tr_lang("mdl.files", "lv", count=1) == "1 fails"
    assert tr_lang("mdl.files", "lv", count=3) == "3 faili"
    assert tr_lang("mdl.files", "de", count=1) == "1 Datei"
    assert tr_lang("mdl.files", "en", count=2) == "2 files"


def test_added_catalog_overrides_and_nested_keys(tmp_path: Path) -> None:
    folder = tmp_path / "locales"
    folder.mkdir()
    (folder / "en.json").write_text(
        json.dumps({"menu": {"open": "Open mine"}, "mdl.retry": "Again"}),
        encoding="utf-8",
    )
    (folder / "fr.json").write_text(json.dumps({"menu.open": "Ouvrir"}), encoding="utf-8")
    add_catalogs(folder)
    assert tr_lang("menu.open", "en") == "Open mine"
    assert tr_lang("mdl.retry", "en") == "Again"
    assert tr_lang("mdl.pause", "en") == "Pause"
    assert "fr" in available_languages()
    assert available_languages()[:5] == LANGS
    reset()


def test_catalogs_live_beside_tokens_and_fall_back_to_english(tmp_path: Path) -> None:
    assert catalog_dir().parent == tokens_path().parent
    assert catalog_dir().name == "locales"
    folder = tmp_path / "locales"
    folder.mkdir()
    (folder / "en.json").write_text(json.dumps({"only.english": "Hill"}), encoding="utf-8")
    add_catalogs(folder)
    assert tr_lang("only.english", "ru") == "Hill"
    assert tr_lang("missing.entirely", "de") == "missing.entirely"
    reset()


def test_preset_and_descriptor_label_keys_exist_in_english_and_russian() -> None:
    english = _flat("en")
    russian = _flat("ru")
    keys: set[str] = set()
    preset_dir = tokens_path().parent / "presets"
    for path in preset_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for group in payload["groups"]:
            keys.add(group["label_key"])
            for option in group["options"]:
                keys.add(option["label_key"])
    for surface in surfaces():
        for spec in settings_for(surface).visible(expert=True):
            keys.add(spec.label_key)
            for choice in spec.choices:
                keys.add(choice.label_key)
    missing = sorted(key for key in keys if key not in english or key not in russian)
    assert keys and missing == []


def test_catalogs_share_keys_and_placeholders() -> None:
    english = _flat("en")
    assert english["mdl.retry"] == "Retry"
    assert english["mdl.heading.all_set"] == "All set"
    assert english["mdl.pause"] == "Pause"
    stems = {key.rsplit(".", 1)[0] for key in english if key.rsplit(".", 1)[-1] in _REQUIRED["en"]}
    for lang in LANGS:
        flat = _flat(lang)
        for key, text in english.items():
            if any(key == f"{stem}.{suffix}" for stem in stems for suffix in _REQUIRED["en"]):
                continue
            assert key in flat, lang
            assert _PLACEHOLDER.findall(flat[key]) == _PLACEHOLDER.findall(text)
        for stem in stems:
            for suffix in _REQUIRED[lang]:
                key = f"{stem}.{suffix}"
                assert key in flat, f"{lang} {key}"
                assert _PLACEHOLDER.findall(flat[key]) == _PLACEHOLDER.findall(
                    english[f"{stem}.other"]
                )
    assert _flat("de")["mdl.pause"] == "Pause"
    assert _flat("ru")["mdl.retry"] == "Повторить"
    assert _flat("ru")["mdl.cancel"] == "Отмена"
    assert _flat("ru")["mdl.heading.all_set"] == "Всё готово"
    assert tr("mdl.retry")


def test_window_strings_come_from_the_catalog() -> None:
    english = set(_flat("en"))
    root = Path("src/voxprint_common")
    missing: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if (
                name in {"tr", "tr_lang", "_t"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                key = node.args[0].value
                if isinstance(key, str) and key not in english:
                    children = [item for item in english if item.startswith(f"{key}.")]
                    if not children:
                        missing.append(f"{path}:{key}")
            if path.name == "mdl_window.py" and name in {
                "setText",
                "setWindowTitle",
                "setPlainText",
                "setToolTip",
                "addAction",
            }:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value:
                        missing.append(f"{path}:{node.lineno}:{arg.value}")
    assert missing == []
