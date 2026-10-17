"""The reusable catalog checker used by suite apps in CI."""

from __future__ import annotations

import json
from pathlib import Path

from voxprint_common.i18n import LANGS, tr_lang
from voxprint_common.i18n.check import check_tree, main
from voxprint_common.ui import splash

_ROOT = Path("src/voxprint_common")
_LOCALES = _ROOT / "ui" / "locales"


def _catalogs(folder: Path, payloads: dict[str, dict[str, object]]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for code, payload in payloads.items():
        (folder / f"{code}.json").write_text(json.dumps(payload), encoding="utf-8")
    return folder


def _filled(key: str, text: str) -> dict[str, dict[str, str]]:
    return {code: {key: text} for code in LANGS}


def test_hardcoded_qt_string_fails(tmp_path: Path) -> None:
    source = tmp_path / "ui.py"
    source.write_text(
        "\n".join(
            [
                "def build(parent, painter, rect, flags):",
                '    parent.setText("Save")',
                '    parent.setWindowTitle("Downloads")',
                '    parent.setPlaceholderText("Search")',
                '    parent.addAction("Quit")',
                '    label = QLabel("Ready")',
                '    button = QPushButton("Pause")',
                '    QMessageBox.information(parent, "Title", "Body")',
                '    painter.drawText(rect, flags, "Loading")',
            ]
        ),
        encoding="utf-8",
    )
    result = check_tree(
        source, _catalogs(tmp_path / "locales", _filled("menu.open", "Open")), LANGS
    )
    assert result.ok is False
    text = "\n".join(result.errors)
    for snippet in ("setText", "setWindowTitle", "setPlaceholderText", "addAction", "QLabel"):
        assert snippet in text
    assert "QPushButton" in text
    assert "information" in text
    assert "drawText" in text


def test_ignore_comment_brand_and_letterless_strings_pass(tmp_path: Path) -> None:
    source = tmp_path / "ui.py"
    source.write_text(
        "\n".join(
            [
                "def build(widget, label, pct, items):",
                '    widget.setText("Save")  # i18n: ignore',
                "    # i18n: ignore",
                '    widget.setToolTip("Hint")',
                '    widget.setWindowTitle("VOXPrint")',
                '    widget.setText("Hugging Face")',
                '    widget.setText("")',
                '    widget.setText("...")',
                '    widget.setText(f"{label}: {pct}%")',
                '    widget.setObjectName("root")',
                '    widget.setStyleSheet("QLabel { color: red }")',
                '    items.append("Save")',
            ]
        ),
        encoding="utf-8",
    )
    result = check_tree(
        source, _catalogs(tmp_path / "locales", _filled("menu.open", "Open")), LANGS
    )
    assert result.errors == ()


def test_extra_allowlist_entry(tmp_path: Path) -> None:
    source = tmp_path / "ui.py"
    source.write_text('def build(w):\n    w.setText("Acme")\n', encoding="utf-8")
    locales = _catalogs(tmp_path / "locales", _filled("menu.open", "Open"))
    blocked = check_tree(source, locales, LANGS)
    allowed = check_tree(source, locales, LANGS, allow=["Acme"])
    assert blocked.errors
    assert allowed.errors == ()


def test_missing_key_and_placeholder_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "ui.py"
    source.write_text('def build():\n    return tr("menu.greet")\n', encoding="utf-8")
    payloads: dict[str, dict[str, object]] = {
        code: {"menu.greet": "Hello {name}"} for code in LANGS
    }
    payloads["de"] = {"menu.greet": "Hallo {person}"}
    del payloads["ru"]["menu.greet"]
    result = check_tree(source, _catalogs(tmp_path / "locales", payloads), LANGS)
    text = "\n".join(result.errors)
    assert "missing from ru" in text
    assert "placeholder mismatch" in text
    assert "menu.greet" in text


def test_plural_forms_and_placeholder_order(tmp_path: Path) -> None:
    source = tmp_path / "ui.py"
    source.write_text('def build(n):\n    return tr("items", count=n)\n', encoding="utf-8")
    payloads: dict[str, dict[str, object]] = {
        "en": {"items": {"one": "{count} item", "other": "{count} items"}},
        "de": {"items": {"one": "{count} Stück", "other": "{count} Stücke"}},
        "ru": {"items": {"one": "{count} x", "other": "{count} x"}},
        "uk": {
            "items": {
                "one": "{count} x",
                "few": "{count} x",
                "many": "{count} x",
                "other": "{count} x",
            }
        },
        "lv": {
            "items": {
                "zero": "{count} x",
                "one": "{count} x",
                "other": "{count} x",
            }
        },
    }
    result = check_tree(source, _catalogs(tmp_path / "locales", payloads), LANGS)
    assert any("items.few" in error and "missing from ru" in error for error in result.errors)
    payloads["ru"] = {
        "items": {
            "one": "{count} x",
            "few": "{name} x",
            "many": "{count} x",
            "other": "{count} x",
        }
    }
    mismatched = check_tree(source, _catalogs(tmp_path / "locales", payloads), LANGS)
    assert any(
        "placeholder mismatch" in error and "items.few" in error for error in mismatched.errors
    )


def test_unused_key_warns_and_cli_exits_zero(tmp_path: Path, capsys: object) -> None:
    source = tmp_path / "ui.py"
    source.write_text('def build(code):\n    return tr(f"menu.item.{code}")\n', encoding="utf-8")
    payloads = {code: {"menu.item.open": "Open", "menu.old": "Old"} for code in LANGS}
    locales = _catalogs(tmp_path / "locales", payloads)
    result = check_tree(source, locales, LANGS)
    assert result.errors == ()
    assert result.warnings == ("unused key 'menu.old'",)
    code = main(["--src", str(source), "--locales", str(locales), "--langs", "en,ru,de,uk,lv"])
    assert code == 0
    captured = capsys.readouterr()
    assert "unused key" in captured.err


def test_error_fstrings_are_not_translation_keys(tmp_path: Path) -> None:
    source = tmp_path / "ui.py"
    source.write_text(
        'def build(name):\n    tr("menu.open")\n    raise RuntimeError(f"hooks.{name}")\n',
        encoding="utf-8",
    )
    locales = _catalogs(tmp_path / "locales", _filled("menu.open", "Open"))
    result = check_tree(source, locales, LANGS)
    assert result.errors == ()


def test_unknown_prefix_and_missing_locale_fail(tmp_path: Path) -> None:
    source = tmp_path / "ui.py"
    source.write_text('def build(code):\n    return tr(f"menu.item.{code}")\n', encoding="utf-8")
    locales = _catalogs(tmp_path / "locales", _filled("menu.open", "Open"))
    result = check_tree(source, locales, LANGS)
    assert any("menu.item." in error for error in result.errors)
    (locales / "lv.json").unlink()
    missing = check_tree(source, locales, LANGS)
    assert any("locale file missing" in error for error in missing.errors)


def test_preset_json_keys_count_as_used(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "ui.py").write_text("def build():\n    return tr('menu.open')\n", encoding="utf-8")
    (package / "presets.json").write_text(
        json.dumps(
            {
                "groups": [
                    {
                        "label_key": "simple.quality",
                        "options": [{"label_key": "simple.quality.fast", "group_key": ""}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    payloads = {
        code: {
            "menu.open": "Open",
            "simple.quality": "Quality",
            "simple.quality.fast": "Fast",
            "menu.old": "Old",
        }
        for code in LANGS
    }
    result = check_tree(package, _catalogs(tmp_path / "locales", payloads), LANGS)
    assert result.errors == ()
    assert result.warnings == ("unused key 'menu.old'",)


def test_package_window_and_splash_pass() -> None:
    result = check_tree(_ROOT, _LOCALES, ("en", "ru", "de", "uk", "lv"))
    assert result.errors == ()
    assert result.warnings == ()


def test_splash_titles_come_from_the_catalogs() -> None:
    titles = {
        "enlarge": "VOXPrint AI Enlarge",
        "clarity": "VOXPrint AI Clarity",
        "canvas": "VOXPrint AI Canvas",
        "plotweaver": "VOXPrint AI Plotweaver",
        "audiobook-builder": "VOXPrint AI Audiobook Builder",
        "movie-dubber": "VOXPrint AI Movie Dubber",
        "model-forge": "VOXPrint AI Model Forge",
        "lucent": "VOXPrint AI Lucent",
    }
    for app_id, title in titles.items():
        for code in LANGS:
            assert tr_lang(f"splash.app.{app_id}", code) == title
        assert splash.display_name(app_id) == title
    assert splash.display_name("audiobook") == "VOXPrint AI Audiobook Builder"
    for code in LANGS:
        assert tr_lang("splash.app.fallback", code) == "VOXPrint"
