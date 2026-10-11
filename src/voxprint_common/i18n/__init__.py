"""Shared UI language catalogs for the Voxprint suite.

Audiobook Builder already ships one JSON file per language in a ``locales``
directory, with flat keys and ``{placeholder}`` fields. Plotweaver and Canvas
keep the same strings in plain dictionaries. Those dictionaries convert by
writing them out as JSON: a flat ``{"menu.open": "Open"}`` table and a nested
``{"menu": {"open": "Open"}}`` table both become the key ``menu.open``.

This module is that mechanism, so an app can adopt it instead of copying its
own ``tr``. The languages shipped here are English (the source and the
fallback), German, Russian, Ukrainian, and Latvian.

An app adds its own folder once, at startup. Files in that folder override
the same key in this package:

```python
from pathlib import Path

from voxprint_common.i18n import add_catalogs

add_catalogs(Path(__file__).resolve().parent / "locales")
```

The folder holds ``en.json``, ``ru.json``, ``de.json``, ``uk.json``, and
``lv.json``. A missing line falls back to English, then to the key. ``tr``
never raises.

Plural forms are a nested object or dotted keys (``items.one``,
``items.few``, ``items.many``, ``items.other``, and ``items.zero`` for
Latvian). ``tr("items", count=3)`` selects the form for the active language.
Russian and Ukrainian use one, few, and many. Latvian uses zero, one, and
other. English and German use one and other. A missing form uses ``other``.

The language is chosen in this order:

1. ``ui_language`` in the shared suite settings, when the file sets it
2. ``VOXPRINT_LANG``
3. the operating-system UI language
4. English

:func:`set_language` changes this process only. An app that wants to remember
a choice calls :func:`voxprint_common.suite_settings.set_ui_language`.
"""

from __future__ import annotations

import ctypes
import json
import locale
import logging
import os
import sys
from pathlib import Path

from voxprint_common.suite_settings import normalize_language, read

__all__ = [
    "DEFAULT_LANG",
    "LANGS",
    "LANG_NAMES",
    "add_catalogs",
    "available_languages",
    "catalog_dir",
    "detect_language",
    "get_language",
    "load_catalog",
    "normalize_code",
    "plural_category",
    "reset",
    "set_language",
    "system_language",
    "tr",
    "tr_lang",
]

log = logging.getLogger(__name__)

LANGS = ("en", "de", "ru", "uk", "lv")
DEFAULT_LANG = "en"
LANG_NAMES = {
    "en": "English",
    "de": "Deutsch",
    "ru": "Русский",
    "uk": "Українська",
    "lv": "Latviešu",
}
_PLURAL = frozenset({"zero", "one", "two", "few", "many", "other"})

_catalogs: dict[tuple[str, tuple[str, ...]], dict[str, str]] = {}
_extra: list[Path] = []
_current: str | None = None


def catalog_dir() -> Path:
    """Return this package's ``locales`` directory."""
    return Path(__file__).with_name("locales")


def add_catalogs(directory: str | Path) -> None:
    """Search ``directory`` for ``<language>.json`` before the built-in file.

    Later folders override earlier ones, and every added folder overrides this
    package. Nested objects become dotted keys. The directory does not have to
    exist yet: a missing file is skipped.

    Args:
        directory: Folder of language catalogs, usually named ``locales``.
    """
    path = Path(directory)
    if path not in _extra:
        _extra.append(path)
    _catalogs.clear()


def available_languages() -> tuple[str, ...]:
    """Return language codes that have a catalog in an active folder.

    Built-in codes stay in :data:`LANGS` order. A code an app adds on top is
    appended after those.
    """
    found: set[str] = set()
    for folder in _search_dirs():
        if not folder.is_dir():
            continue
        for path in folder.glob("*.json"):
            code = normalize_code(path.stem)
            if code:
                found.add(code)
    ordered = [code for code in LANGS if code in found]
    ordered.extend(sorted(found.difference(LANGS)))
    return tuple(ordered)


def load_catalog(lang: str) -> dict[str, str]:
    """Load one language, with added folders overriding this package.

    A missing or broken file contributes nothing. The result is cached until
    :func:`add_catalogs` or :func:`reset`.

    Args:
        lang: A language code such as ``en`` or ``ru``.
    """
    code = normalize_code(lang) or DEFAULT_LANG
    token = (code, tuple(str(path) for path in _search_dirs()))
    cached = _catalogs.get(token)
    if cached is not None:
        return cached
    merged: dict[str, str] = {}
    for folder in _search_dirs():
        merged.update(_read_file(folder / f"{code}.json"))
    _catalogs[token] = merged
    return merged


def normalize_code(value: object) -> str | None:
    """Return a supported language code, or None.

    ``ru-RU``, ``de_AT.UTF-8``, and ``UK`` become ``ru``, ``de``, and ``uk``.
    A language this process does not have a catalog for, including the raw
    tag ``C``, is None.

    Args:
        value: A locale name or language tag.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace("_", "-").split(".", 1)[0].split("@", 1)[0]
    primary = text.split("-", 1)[0]
    if primary in _known_codes():
        return primary
    return None


def _windows_ui_language() -> str | None:
    """Return the Windows UI language, or None on another operating system."""
    if sys.platform != "win32":
        return None
    try:
        buffer = ctypes.create_unicode_buffer(85)
        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        if kernel.GetUserDefaultLocaleName(buffer, len(buffer)):
            return normalize_code(buffer.value)
    except AttributeError, OSError:
        return None
    return None


def system_language() -> str | None:
    """Return the operating-system UI language when a catalog exists for it."""
    windows = _windows_ui_language()
    if windows:
        return windows
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        code = normalize_code(os.environ.get(var))
        if code:
            return code
    try:
        return normalize_code(locale.getlocale()[0])
    except TypeError, ValueError:
        return None


def detect_language() -> str:
    """Resolve the UI language using the order in the module docstring."""
    payload = read()
    if "ui_language" in payload and payload.get("ui_language") is not None:
        stored = payload.get("ui_language")
        code = normalize_code(normalize_language(stored) or stored)
        if code:
            return code
    forced = normalize_code(os.environ.get("VOXPRINT_LANG"))
    if forced:
        return forced
    return system_language() or DEFAULT_LANG


def get_language() -> str:
    """Return the active UI language, detecting it on first use."""
    global _current
    if _current is None:
        _current = detect_language()
    return _current


def set_language(lang: str) -> str:
    """Switch the active language for this process.

    This does not write suite settings.

    Args:
        lang: A language tag. An unsupported tag becomes English.

    Returns:
        The code that is now active.
    """
    global _current
    _current = normalize_code(lang) or DEFAULT_LANG
    return _current


def reset() -> None:
    """Forget the active language, added folders, and cached catalogs.

    Tests use this. An app does not.
    """
    global _current
    _current = None
    _extra.clear()
    _catalogs.clear()


def plural_category(count: int, lang: str | None = None) -> str:
    """Return the CLDR plural category for an integer count.

    Russian and Ukrainian: ``one`` (1, 21, 31…), ``few`` (2–4, 22–24…),
    ``many`` (0, 5–20, 11–14…). Latvian: ``zero`` (0, 10, 11–19, 20…),
    ``one`` (1, 21…), otherwise ``other``. English and German: ``one`` when
    the count is 1, otherwise ``other``.

    Args:
        count: Integer count. The sign is ignored.
        lang: Language code. Defaults to the active UI language.
    """
    number = abs(int(count))
    code = normalize_code(lang) or (get_language() if lang is None else DEFAULT_LANG)
    if code in {"ru", "uk"}:
        mod10 = number % 10
        mod100 = number % 100
        if mod10 == 1 and mod100 != 11:
            return "one"
        if mod10 in {2, 3, 4} and mod100 not in {12, 13, 14}:
            return "few"
        return "many"
    if code == "lv":
        mod10 = number % 10
        mod100 = number % 100
        if mod10 == 0 or 11 <= mod100 <= 19:
            return "zero"
        if mod10 == 1 and mod100 != 11:
            return "one"
        return "other"
    if number == 1:
        return "one"
    return "other"


def tr(key: str, **params: object) -> str:
    """Translate ``key`` into the active language.

    ``{placeholder}`` fields are filled from ``params``. Pass ``count`` as an
    integer to select a plural form. A missing translation falls back to
    English, then to the key. A broken placeholder returns the unformatted text.

    Args:
        key: Catalog key, for example ``mdl.retry`` or ``mdl.files``.
        params: Values for the ``{placeholder}`` fields. ``count`` also selects
            the plural form.
    """
    return tr_lang(key, get_language(), **params)


def tr_lang(key: str, lang: str, **params: object) -> str:
    """Translate ``key`` into ``lang`` without changing the active language.

    Args:
        key: Catalog key.
        lang: Language code. An unsupported code uses English.
        params: Values for the ``{placeholder}`` fields. ``count`` selects the
            plural form.
    """
    code = normalize_code(lang) or DEFAULT_LANG
    count = _count(params.get("count"))
    text = _lookup(key, code, count)
    if text is None and code != DEFAULT_LANG:
        text = _lookup(key, DEFAULT_LANG, count)
    if text is None:
        text = key
    if not params:
        return text
    try:
        return text.format(**params)
    except IndexError, KeyError, ValueError:
        return text


def _search_dirs() -> list[Path]:
    return [catalog_dir(), *_extra]


def _known_codes() -> set[str]:
    found = set(LANGS)
    for folder in _search_dirs():
        if not folder.is_dir():
            continue
        for path in folder.glob("*.json"):
            stem = path.stem.strip().lower().split("-", 1)[0].split("_", 1)[0]
            if stem.isalpha() and 2 <= len(stem) <= 3:
                found.add(stem)
    return found


def _read_file(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        if path.is_file():
            log.warning("locale %s not loaded: %s", path, exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    return _flatten(payload)


def _flatten(payload: dict[object, object], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            continue
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, str):
            flat[dotted] = value
        elif isinstance(value, dict):
            nested = _flatten(value, dotted)
            if nested:
                flat.update(nested)
            elif set(value).issubset(_PLURAL) and not value:
                continue
    return flat


def _lookup(key: str, lang: str, count: int | None) -> str | None:
    catalog = load_catalog(lang)
    if count is not None:
        category = plural_category(count, lang)
        for suffix in (category, "other"):
            found = catalog.get(f"{key}.{suffix}")
            if found is not None:
                return found
    return catalog.get(key)


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
