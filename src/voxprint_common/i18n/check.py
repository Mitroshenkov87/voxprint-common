"""Fail a suite app when UI copy skips the catalogs.

The CI command is::

    python -m voxprint_common.i18n.check --src src/<pkg> --locales <dir> --langs en,ru,de,uk,lv

It fails when a Qt or PySide6 call carries a hard-coded user-facing string,
when a key is missing from any requested locale, or when ``{placeholder}``
fields differ between languages. A key that no call uses is a warning and
does not fail the run.

``# i18n: ignore`` on the call, on the string, or on the line above the call
skips that call. Brand names such as ``VOXPrint`` and ``Hugging Face`` are
allowed as a whole argument. An empty string, or a string with no letters,
is not treated as copy.

The first language in ``--langs`` is the source catalog. Plural objects use
one and other in English and German, one, few, many, and other in Russian and
Ukrainian, and zero, one, and other in Latvian. Any other language is checked
like English. A dynamic key such as ``f"mdl.source.{code}"`` counts as a use
of every key under that prefix. A JSON file in the source tree counts
``label_key``, ``reason_key``, ``action_label_key``, and ``group_key`` as uses.
Catalog files under a ``locales`` directory are not scanned that way.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CheckResult", "check_tree", "main"]

_PLURAL = frozenset({"zero", "one", "two", "few", "many", "other"})
_REQUIRED = {
    "en": ("one", "other"),
    "de": ("one", "other"),
    "ru": ("one", "few", "many", "other"),
    "uk": ("one", "few", "many", "other"),
    "lv": ("zero", "one", "other"),
}
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_IGNORE = re.compile(r"#\s*i18n:\s*ignore\b", re.IGNORECASE)
_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*\.")
_TRANSLATE = frozenset({"tr", "tr_lang", "_t"})
_TEXT_METHODS = frozenset(
    {
        "addAction",
        "addButton",
        "addItem",
        "addItems",
        "addTab",
        "drawText",
        "insertItem",
        "insertPlainText",
        "setAccessibleDescription",
        "setAccessibleName",
        "setDetailedText",
        "setHorizontalHeaderLabels",
        "setHtml",
        "setInformativeText",
        "setItemText",
        "setMarkdown",
        "setPlaceholderText",
        "setPlainText",
        "setStatusTip",
        "setTabText",
        "setText",
        "setTitle",
        "setToolTip",
        "setVerticalHeaderLabels",
        "setWhatsThis",
        "setWindowIconText",
        "setWindowTitle",
    }
)
_TEXT_TYPES = frozenset(
    {
        "QAction",
        "QCheckBox",
        "QCommandLinkButton",
        "QGroupBox",
        "QLabel",
        "QLineEdit",
        "QMenu",
        "QMessageBox",
        "QPlainTextEdit",
        "QPushButton",
        "QRadioButton",
        "QTableWidgetItem",
        "QTextEdit",
        "QToolButton",
    }
)
_MESSAGE_BOX = frozenset({"about", "critical", "information", "question", "warning"})
_TEXT_KEYWORDS = frozenset(
    {
        "accessibleDescription",
        "accessibleName",
        "detailedText",
        "informativeText",
        "label",
        "placeholderText",
        "statusTip",
        "text",
        "title",
        "toolTip",
        "whatsThis",
        "windowTitle",
    }
)
_JSON_MESSAGE_KEYS = frozenset({"label_key", "reason_key", "action_label_key", "group_key"})
_BRANDS = frozenset(
    {
        "Google Drive",
        "Hugging Face",
        "Kaggle",
        "ModelScope",
        "VOXPrint",
        "VOXPrint AI Audiobook Builder",
        "VOXPrint AI Canvas",
        "VOXPrint AI Clarity",
        "VOXPrint AI Enlarge",
        "VOXPrint AI Lucent",
        "VOXPrint AI Model Forge",
        "VOXPrint AI Movie Dubber",
        "VOXPrint AI Plotweaver",
        "VOXPrint AI Suite",
        # Joined so mdl.names keeps the only literal spelling of the downloader.
        "VOXPrint" + " MDL",
        "VOXPrint downloads",
    }
)


@dataclass(frozen=True)
class CheckResult:
    """Errors that fail the run, and unused-key warnings that do not."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Return whether the run has no errors."""
        return not self.errors


def check_tree(
    src: Path,
    locales: Path,
    langs: Sequence[str],
    *,
    allow: Sequence[str] = (),
) -> CheckResult:
    """Check one source tree against a locales directory.

    Args:
        src: A package directory, or one Python file.
        locales: Directory of ``<lang>.json`` catalogs.
        langs: Language codes. The first is the source catalog.
        allow: Extra whole strings that may appear in a UI call.

    Returns:
        Errors and unused-key warnings. ``errors`` is empty when the tree passes.
    """
    codes = tuple(code.strip() for code in langs if code.strip())
    if not codes:
        return CheckResult(("--langs is empty",), ())
    brands = _BRANDS | {item.strip() for item in allow if item.strip()}
    catalogs, load_errors = _load_locales(locales, codes)
    hardcoded, referenced, constants, prefixes = _scan_python(src, brands)
    json_errors, json_keys = _scan_json_keys(src)
    referenced.update(json_keys)
    catalog_errors, warnings = _catalog_issues(catalogs, referenced, constants, prefixes, codes)
    errors = tuple(hardcoded + load_errors + json_errors + catalog_errors)
    return CheckResult(errors, tuple(warnings))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the checker from the command line.

    Args:
        argv: Arguments without the program name. Defaults to ``sys.argv``.

    Returns:
        ``0`` when there is no error. Unused keys are warnings and still return ``0``.
    """
    parser = argparse.ArgumentParser(prog="python -m voxprint_common.i18n.check")
    parser.add_argument("--src", type=Path, required=True, help="Package directory or Python file")
    parser.add_argument(
        "--locales", type=Path, required=True, help="Directory of <lang>.json files"
    )
    parser.add_argument(
        "--langs", required=True, help="Comma-separated language codes, source first"
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        help="Extra exact UI string to allow (repeatable)",
    )
    args = parser.parse_args(None if argv is None else list(argv))
    langs = [part.strip() for part in str(args.langs).split(",")]
    result = check_tree(args.src, args.locales, langs, allow=args.allow)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(error, file=sys.stderr)
    return 0 if result.ok else 1


def _load_locales(
    locales: Path, langs: Sequence[str]
) -> tuple[dict[str, dict[str, str]], list[str]]:
    catalogs: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for code in langs:
        path = locales / f"{code}.json"
        if not path.is_file():
            errors.append(f"locale file missing: {path.as_posix()}")
            catalogs[code] = {}
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"{path.as_posix()}: {exc}")
            catalogs[code] = {}
            continue
        if not isinstance(payload, dict):
            errors.append(f"{path.as_posix()}: catalog must be a JSON object")
            catalogs[code] = {}
            continue
        catalogs[code] = _flatten(payload)
    return catalogs, errors


def _flatten(payload: Mapping[object, object], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            continue
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, str):
            flat[dotted] = value
        elif isinstance(value, dict):
            flat.update(_flatten(value, dotted))
    return flat


def _scan_python(
    src: Path, brands: frozenset[str]
) -> tuple[list[str], set[str], set[str], set[str]]:
    root = src if src.is_dir() else src.parent
    files = [src] if src.is_file() else sorted(path for path in src.rglob("*.py") if path.is_file())
    errors: list[str] = []
    referenced: set[str] = set()
    prefixes: set[str] = set()
    constants: set[str] = set()
    for path in files:
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except OSError as exc:
            errors.append(f"{_rel(path, root)}: {exc}")
            continue
        except SyntaxError as exc:
            errors.append(f"{_rel(path, root)}:{exc.lineno}: {exc.msg}")
            continue
        lines = source.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                constants.add(node.value)
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name in _TRANSLATE:
                    _record_key(node, referenced, prefixes)
                if _is_ui_call(node):
                    errors.extend(_hardcoded(path, root, lines, node, name, brands))
    return errors, referenced, constants, prefixes


def _scan_json_keys(src: Path) -> tuple[list[str], set[str]]:
    root = src if src.is_dir() else src.parent
    if src.is_file():
        files = [src] if src.suffix == ".json" else []
    else:
        files = sorted(path for path in src.rglob("*.json") if path.is_file())
    errors: list[str] = []
    found: set[str] = set()
    for path in files:
        if "locales" in path.parts:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"{_rel(path, root)}: {exc}")
            continue
        _collect_json_keys(payload, found)
    return errors, found


def _collect_json_keys(payload: object, found: set[str]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in _JSON_MESSAGE_KEYS and isinstance(value, str) and value:
                found.add(value)
                continue
            _collect_json_keys(value, found)
        return
    if isinstance(payload, list):
        for item in payload:
            _collect_json_keys(item, found)


def _record_key(node: ast.Call, exact: set[str], prefixes: set[str]) -> None:
    if not node.args:
        return
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        exact.add(arg.value)
        return
    prefix = _key_prefix(arg)
    if prefix:
        prefixes.add(prefix)


def _key_prefix(node: ast.AST) -> str | None:
    if not isinstance(node, ast.JoinedStr) or not node.values:
        return None
    first = node.values[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return None
    if _PREFIX.fullmatch(first.value) is None:
        return None
    if any(isinstance(part, ast.FormattedValue) for part in node.values):
        return first.value
    return None


def _is_ui_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _TEXT_TYPES
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in _TEXT_METHODS:
        return True
    if func.attr not in _MESSAGE_BOX:
        return False
    receiver = func.value
    if isinstance(receiver, ast.Name):
        return receiver.id == "QMessageBox"
    return isinstance(receiver, ast.Attribute) and receiver.attr == "QMessageBox"


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return "call"


def _hardcoded(
    path: Path,
    root: Path,
    lines: list[str],
    node: ast.Call,
    name: str,
    brands: frozenset[str],
) -> list[str]:
    if _ignored(lines, node.lineno, node.lineno):
        return []
    found: list[str] = []
    pieces = list(node.args)
    pieces.extend(kw.value for kw in node.keywords if kw.arg in _TEXT_KEYWORDS)
    for arg in pieces:
        for literal, text in _literals(arg):
            if not _has_letters(text) or text.strip() in brands:
                continue
            if _ignored(lines, node.lineno, literal.lineno):
                continue
            where = _rel(path, root)
            found.append(f"{where}:{literal.lineno}: hard-coded UI string in {name}: {text!r}")
    return found


def _literals(node: ast.AST) -> list[tuple[ast.AST, str]]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [(node, node.value)]
    if isinstance(node, ast.JoinedStr):
        return [
            (part, part.value)
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        ]
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        found: list[tuple[ast.AST, str]] = []
        for item in node.elts:
            found.extend(_literals(item))
        return found
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literals(node.left) + _literals(node.right)
    if isinstance(node, ast.IfExp):
        return _literals(node.body) + _literals(node.orelse)
    return []


def _ignored(lines: list[str], call_line: int, string_line: int) -> bool:
    numbers = {call_line, string_line}
    if call_line > 1:
        numbers.add(call_line - 1)
    if string_line > 1:
        numbers.add(string_line - 1)
    return any(_IGNORE.search(lines[number - 1]) for number in numbers if 0 < number <= len(lines))


def _has_letters(text: str) -> bool:
    return any(character.isalpha() for character in text)


def _catalog_issues(
    catalogs: Mapping[str, Mapping[str, str]],
    referenced: set[str],
    constants: set[str],
    prefixes: set[str],
    langs: Sequence[str],
) -> tuple[list[str], list[str]]:
    source = langs[0]
    english = dict(catalogs.get(source, {}))
    stems = _plural_stems(catalogs)
    known = {key for catalog in catalogs.values() for key in catalog}
    used = set(referenced)
    used.update(item for item in constants if item in known or item in stems)
    errors: list[str] = []
    for stem in sorted(stems):
        errors.extend(_plural_errors(catalogs, english, stem, langs))
    for key, text in sorted(english.items()):
        if _is_plural_form(key, stems):
            continue
        expected = _placeholders(text)
        for code in langs:
            if code == source:
                continue
            other = catalogs.get(code, {}).get(key)
            if other is None:
                errors.append(f"key {key!r} missing from {code}")
            elif _placeholders(other) != expected:
                errors.append(
                    f"placeholder mismatch for {key!r} in {code}: "
                    f"{source} has {_show(expected)}, {code} has {_show(_placeholders(other))}"
                )
    for key in sorted(referenced):
        if key in known or key in stems:
            continue
        if any(f"{key}.{suffix}" in known for suffix in _PLURAL):
            continue
        missing = [code for code in langs if key not in catalogs.get(code, {})]
        if missing:
            errors.append(f"key {key!r} missing from {', '.join(missing)}")
    for prefix in sorted(prefixes):
        if not any(key.startswith(prefix) for key in english):
            errors.append(f"key prefix {prefix!r} matches no {source} key")
    warnings = [
        f"unused key {key!r}" for key in sorted(english) if not _is_used(key, used, prefixes, stems)
    ]
    return errors, warnings


def _plural_errors(
    catalogs: Mapping[str, Mapping[str, str]],
    english: Mapping[str, str],
    stem: str,
    langs: Sequence[str],
) -> list[str]:
    source = langs[0]
    errors: list[str] = []
    for code in langs:
        for suffix in _REQUIRED.get(code, _REQUIRED["en"]):
            key = f"{stem}.{suffix}"
            text = catalogs.get(code, {}).get(key)
            if text is None:
                errors.append(f"key {key!r} missing from {code}")
                continue
            ref_key = key if key in english else f"{stem}.other"
            reference = english.get(ref_key)
            if reference is None or code == source:
                continue
            expected = _placeholders(reference)
            found = _placeholders(text)
            if found != expected:
                errors.append(
                    f"placeholder mismatch for {key!r} in {code}: "
                    f"{source} has {_show(expected)}, {code} has {_show(found)}"
                )
    return errors


def _plural_stems(catalogs: Mapping[str, Mapping[str, str]]) -> set[str]:
    stems: set[str] = set()
    for catalog in catalogs.values():
        for key in catalog:
            name, _, suffix = key.rpartition(".")
            if name and suffix in _PLURAL:
                stems.add(name)
    return stems


def _is_plural_form(key: str, stems: set[str]) -> bool:
    name, _, suffix = key.rpartition(".")
    return bool(name) and name in stems and suffix in _PLURAL


def _is_used(key: str, exact: set[str], prefixes: set[str], stems: set[str]) -> bool:
    if key in exact or any(key.startswith(prefix) for prefix in prefixes):
        return True
    name, _, _suffix = key.rpartition(".")
    return name in stems and name in exact


def _placeholders(text: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in _PLACEHOLDER.findall(text))


def _show(fields: Iterable[str]) -> str:
    return ", ".join(fields) if fields else "(none)"


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
