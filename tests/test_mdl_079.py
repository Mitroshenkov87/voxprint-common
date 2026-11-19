"""0.7.9: same-id VRAM tiers stay Not installed and keep separate views.

Qwen3-TTS 0.6B and 1.7B share an id and 10 of 12 blobs. With 1.7B installed,
0.6B must read Not installed, not Needs repair, and repairing or
materializing one tier must leave the other view in place.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.catalog import load_manifest
from voxprint_common.mdl.control import Controller, _model_kind
from voxprint_common.mdl.views import (
    file_set_key,
    inspect_view,
    materialize_views,
    repair_view,
    view_directory,
)
from voxprint_common.paths import models_dir

VOCAB = b"shared-vocab-json"
COMMON = [
    b"gitattributes",
    b"gen-cfg",
    b"merges",
    b"tok-cfg",
    b"preproc",
    b"st-cfg",
    b"st-conf",
    b"st-weights",
    b"st-pre",
]
UNIQUE_06 = b"weights-0.6b"
CFG_06 = b"config-0.6b"
UNIQUE_17 = b"weights-1.7b"
CFG_17 = b"config-1.7b"


def _file(path: str, payload: bytes) -> dict[str, object]:
    return {
        "path": path,
        "size": len(payload),
        "sha256": sha256_bytes(payload),
        "mirrors": [{"source": "url", "url": "https://huggingface.co/org/w.bin"}],
    }


def _tts_files(prefix: str, unique: bytes, cfg: bytes) -> list[dict[str, object]]:
    files = [_file(f"{prefix}/vocab.json", VOCAB)]
    for index, payload in enumerate(COMMON):
        files.append(_file(f"{prefix}/common-{index}.bin", payload))
    files.append(_file(f"{prefix}/config.json", cfg))
    files.append(_file(f"{prefix}/model.safetensors", unique))
    assert len(files) == 12
    return files


def _entry(label: str, prefix: str, unique: bytes, cfg: bytes, tier: str) -> dict[str, object]:
    return {
        "id": "qwen3-tts",
        "version": "1",
        "vram_tier": tier,
        "label": label,
        "files": _tts_files(prefix, unique, cfg),
    }


def _manifest(folder: Path) -> Path:
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "app": "audiobook-builder",
                "models": [
                    _entry("Qwen3-TTS 0.6B", "Qwen--0.6B", UNIQUE_06, CFG_06, "8gb"),
                    _entry("Qwen3-TTS 1.7B Base", "Qwen--1.7B", UNIQUE_17, CFG_17, "16gb"),
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _put_blob(payload: bytes) -> str:
    digest = sha256_bytes(payload)
    folder = models_dir() / "blobs"
    folder.mkdir(parents=True, exist_ok=True)
    blob = folder / digest
    blob.write_bytes(payload)
    blob.with_name(blob.name + ".verified").write_text(digest, encoding="utf-8")
    return digest


def _seed_17() -> None:
    for payload in (VOCAB, UNIQUE_17, CFG_17, *COMMON):
        _put_blob(payload)


def _seed_both() -> None:
    _seed_17()
    _put_blob(UNIQUE_06)
    _put_blob(CFG_06)


def _rows(page):
    found = [row for tab in page.tabs for group in tab.groups for row in group.rows]
    if found:
        return found
    return [
        row
        for tab in page.tabs
        for group in tab.groups
        for section in group.sections
        for row in section.rows
    ]


def _maps(tmp_path: Path) -> tuple[object, object, dict[str, str], dict[str, str]]:
    parsed = load_manifest(_manifest(tmp_path / "audiobook"))
    row_06 = next(model for model in parsed.models if model.label == "Qwen3-TTS 0.6B")
    row_17 = next(model for model in parsed.models if model.label == "Qwen3-TTS 1.7B Base")
    files_06 = {item.path: item.sha256 for item in row_06.files}
    files_17 = {item.path: item.sha256 for item in row_17.files}
    return row_06, row_17, files_06, files_17


def test_same_id_tier_is_not_installed_when_the_other_tier_is_present(tmp_path: Path) -> None:
    path = _manifest(tmp_path / "audiobook")
    _seed_17()
    parsed = load_manifest(path)
    row_06 = next(model for model in parsed.models if model.label == "Qwen3-TTS 0.6B")
    row_17 = next(model for model in parsed.models if model.label == "Qwen3-TTS 1.7B Base")
    assert len(row_06.files) == 12
    shared = {item.sha256 for item in row_06.files} & {item.sha256 for item in row_17.files}
    assert len(shared) == 10
    assert _model_kind(row_17, "", None, siblings=parsed.models, app="audiobook-builder") == "ok"
    assert (
        _model_kind(row_06, "", None, siblings=parsed.models, app="audiobook-builder") == "missing"
    )
    page = Controller().manage_page("audiobook-builder", path, devices=[], lang="en")
    labels = {row.title: row.state_label for row in _rows(page)}
    assert labels["Qwen3-TTS 1.7B Base"] == "Installed"
    assert labels["Qwen3-TTS 0.6B"] == "Not installed"


def test_inspect_view_does_not_treat_the_other_tier_as_a_stub(tmp_path: Path) -> None:
    row_06, row_17, files_06, files_17 = _maps(tmp_path)
    _seed_17()
    root = models_dir()
    sizes_17 = {item.path: item.size for item in row_17.files}
    state, folder = repair_view(root, row_17.id, files_17, "audiobook-builder", sizes=sizes_17)
    assert state in {"ok", "repaired"} and folder is not None
    assert inspect_view(root, row_06.id, files_06, "audiobook-builder") == "absent"


def test_repair_and_materialize_leave_the_other_tier_view(tmp_path: Path) -> None:
    row_06, row_17, files_06, files_17 = _maps(tmp_path)
    _seed_both()
    root = models_dir()
    sizes_06 = {item.path: item.size for item in row_06.files}
    sizes_17 = {item.path: item.size for item in row_17.files}
    _, folder_17 = repair_view(root, row_17.id, files_17, "audiobook-builder", sizes=sizes_17)
    _, folder_06 = repair_view(root, row_06.id, files_06, "audiobook-builder", sizes=sizes_06)
    assert folder_17 is not None and folder_17.is_dir()
    assert folder_06 is not None and folder_06.is_dir()
    assert folder_17 != folder_06
    assert inspect_view(root, row_17.id, files_17, "audiobook-builder", sizes=sizes_17) == "ok"
    assert inspect_view(root, row_06.id, files_06, "audiobook-builder", sizes=sizes_06) == "ok"

    (root / "views" / "audiobook-builder").mkdir(parents=True, exist_ok=True)
    items = _planned_items("audiobook-builder", row_06, row_17)
    built = set(materialize_views(items))
    assert folder_17 in built and folder_06 in built
    assert inspect_view(root, row_17.id, files_17, "audiobook-builder", sizes=sizes_17) == "ok"
    assert inspect_view(root, row_06.id, files_06, "audiobook-builder", sizes=sizes_06) == "ok"
    expected = {view_directory(root, "qwen3-tts", files_06, "audiobook-builder"), folder_17}
    present = {
        path
        for path in (root / "views" / "audiobook-builder").iterdir()
        if path.is_dir() and ".staging-" not in path.name
    }
    assert present == expected


def _planned_items(app: str, row_06: object, row_17: object) -> list[SimpleNamespace]:
    """One planned file per digest, with a layout row for each fileset that uses it."""
    groups: dict[str, list[tuple[object, object, str]]] = {}
    for row in (row_06, row_17):
        stamp = file_set_key({item.path: item.sha256 for item in row.files})
        for item in row.files:
            groups.setdefault(item.sha256, []).append((row, item, stamp))
    found: list[SimpleNamespace] = []
    for digest, rows in groups.items():
        layouts = tuple((row.id, item.path, stamp) for row, item, stamp in rows)
        first = rows[0][1]
        found.append(
            SimpleNamespace(
                apps=(app,),
                sha256=digest,
                size=first.size,
                model_id="qwen3-tts",
                name=first.path,
                layouts=layouts,
            )
        )
    return found
