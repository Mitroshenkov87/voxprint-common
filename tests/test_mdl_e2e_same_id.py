"""End-to-end: two apps, same-id VRAM tiers, through register, ensure, views, repair."""

from __future__ import annotations

import json
from pathlib import Path

from voxprint_common.cli_protocol import EXIT_OK
from voxprint_common.gpu_probe import GpuDevice
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.catalog import load_manifest, model_share_key, register_app
from voxprint_common.mdl.control import Controller, _model_kind
from voxprint_common.mdl.fetch import FetchProgress
from voxprint_common.mdl.job import verify_repair
from voxprint_common.mdl.queue import enqueue, ensure_ready, run_or_attach
from voxprint_common.mdl.views import inspect_view, materialize_views, view_directory
from voxprint_common.paths import models_dir
from voxprint_common.runtime_manifest import load
from voxprint_common.suite_settings import set_keep_awake_during_jobs

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
GPU = GpuDevice(0, "Test GPU", "610.00", (8, 9), 24576)
APPS = ("canvas", "audiobook-builder")


def _payloads() -> dict[str, bytes]:
    found = {sha256_bytes(item): item for item in (VOCAB, UNIQUE_06, CFG_06, UNIQUE_17, CFG_17)}
    found.update({sha256_bytes(item): item for item in COMMON})
    return found


def _file(path: str, payload: bytes, repo: str) -> dict[str, object]:
    return {
        "path": path,
        "size": len(payload),
        "sha256": sha256_bytes(payload),
        "mirrors": [
            {
                "source": "huggingface",
                "repo": repo,
                "revision": "main",
                "filename": path,
            }
        ],
    }


def _tts_files(prefix: str, unique: bytes, cfg: bytes, repo: str) -> list[dict[str, object]]:
    files = [_file(f"{prefix}/vocab.json", VOCAB, repo)]
    for index, payload in enumerate(COMMON):
        files.append(_file(f"{prefix}/common-{index}.bin", payload, repo))
    files.append(_file(f"{prefix}/config.json", cfg, repo))
    files.append(_file(f"{prefix}/model.safetensors", unique, repo))
    assert len(files) == 12
    return files


def _entry(label: str, prefix: str, unique: bytes, cfg: bytes, tier: str, repo: str) -> dict:
    return {
        "id": "qwen3-tts",
        "version": "1",
        "vram_tier": tier,
        "label": label,
        "files": _tts_files(prefix, unique, cfg, repo),
    }


def _models() -> list[dict[str, object]]:
    return [
        _entry("Qwen3-TTS 0.6B", "Qwen--0.6B", UNIQUE_06, CFG_06, "8gb", "Qwen/Qwen3-TTS-0.6B"),
        _entry(
            "Qwen3-TTS 1.7B Base", "Qwen--1.7B", UNIQUE_17, CFG_17, "16gb", "Qwen/Qwen3-TTS-1.7B"
        ),
    ]


def _manifest(folder: Path, app: str) -> Path:
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": 1, "app": app, "models": _models()}),
        encoding="utf-8",
    )
    return path


def _selector(model: object) -> str:
    return f"{model.id}|{model_share_key(model)}|{model.tier.value}|{model.label}"


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


def _labels(app: str, manifest: Path) -> dict[str, str]:
    page = Controller().manage_page(app, manifest, devices=[GPU], lang="en")
    return {row.title: row.state_label for row in _rows(page)}


def _fake_fetch(
    destination: Path,
    urls: list[str],
    *,
    sha256: str,
    size: int,
    progress=None,
    **kwargs: object,
) -> None:
    del urls, kwargs
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_payloads()[sha256])
    if progress is not None:
        progress(FetchProgress("huggingface", size, size, 1.0))


def _refs_for(digest: str) -> set[str]:
    document = load()
    found: set[str] = set()
    for component in document.components.values():
        if component.sha256 == digest:
            found.update(component.refs)
    return found


def test_two_apps_same_id_tiers_through_ensure_views_and_repair(
    tmp_path: Path, monkeypatch
) -> None:
    set_keep_awake_during_jobs(False)
    canvas_manifest = _manifest(tmp_path / "canvas", "canvas")
    book_manifest = _manifest(tmp_path / "audiobook", "audiobook-builder")
    register_app(
        "canvas", canvas_manifest.parent, display_name="Canvas", descriptor=canvas_manifest
    )
    register_app(
        "audiobook-builder",
        book_manifest.parent,
        display_name="Audiobook Builder",
        descriptor=book_manifest,
    )
    (models_dir() / "views" / "audiobook-builder").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("voxprint_common.mdl.job.query_gpus", lambda: [GPU])
    monkeypatch.setattr("voxprint_common.mdl.queue.query_gpus", lambda: [GPU])
    monkeypatch.setattr("voxprint_common.mdl.job.fetch_file", _fake_fetch)

    canvas_ready = ensure_ready("canvas")
    book_ready = ensure_ready("audiobook-builder")
    assert canvas_ready.ok and book_ready.ok

    parsed = load_manifest(canvas_manifest)
    row_06 = next(model for model in parsed.models if model.label == "Qwen3-TTS 0.6B")
    row_17 = next(model for model in parsed.models if model.label == "Qwen3-TTS 1.7B Base")
    assert (
        len({item.sha256 for item in row_06.files} & {item.sha256 for item in row_17.files}) == 10
    )
    files_06 = {item.path: item.sha256 for item in row_06.files}
    files_17 = {item.path: item.sha256 for item in row_17.files}
    sizes_06 = {item.path: item.size for item in row_06.files}
    sizes_17 = {item.path: item.size for item in row_17.files}

    for app, manifest in (("canvas", canvas_manifest), ("audiobook-builder", book_manifest)):
        labels = _labels(app, manifest)
        assert labels["Qwen3-TTS 1.7B Base"] == "Installed"
        assert labels["Qwen3-TTS 0.6B"] == "Not installed"
        assert _model_kind(row_17, "", None, siblings=parsed.models, app=app) == "ok"
        assert _model_kind(row_06, "", None, siblings=parsed.models, app=app) == "missing"

    root = models_dir()
    for app in APPS:
        assert inspect_view(root, "qwen3-tts", files_17, app, sizes=sizes_17) == "ok"
        assert inspect_view(root, "qwen3-tts", files_06, app, sizes=sizes_06) == "absent"
    for item in row_17.files:
        assert _refs_for(item.sha256) == {"canvas", "audiobook-builder"}
    for item in row_06.files:
        if item.sha256 not in {entry.sha256 for entry in row_17.files}:
            assert _refs_for(item.sha256) == set()

    selector = _selector(row_06)
    enqueue(
        "canvas",
        [selector],
        manifests=[canvas_manifest],
        devices=[GPU],
        all_tiers=True,
    )
    enqueue(
        "audiobook-builder",
        [selector],
        manifests=[book_manifest],
        devices=[GPU],
        all_tiers=True,
    )
    outcome = run_or_attach(devices=[GPU], priority=False)
    assert outcome.code == EXIT_OK, outcome.message

    from voxprint_common.gpu_probe import VramTier
    from voxprint_common.mdl.catalog import plan_files

    planned = plan_files(
        [load_manifest(canvas_manifest), load_manifest(book_manifest)],
        VramTier.GB24,
        memory_gib=24,
        only=[_selector(row_06), _selector(row_17)],
        all_tiers=True,
    )
    built = set(materialize_views(planned))
    for app in APPS:
        assert view_directory(root, "qwen3-tts", files_17, app) in built
        assert view_directory(root, "qwen3-tts", files_06, app) in built
        labels = _labels(app, canvas_manifest if app == "canvas" else book_manifest)
        assert labels["Qwen3-TTS 1.7B Base"] == "Installed"
        assert labels["Qwen3-TTS 0.6B"] == "Installed"
        assert inspect_view(root, "qwen3-tts", files_17, app, sizes=sizes_17) == "ok"
        assert inspect_view(root, "qwen3-tts", files_06, app, sizes=sizes_06) == "ok"

    for item in (*row_17.files, *row_06.files):
        assert _refs_for(item.sha256) == {"canvas", "audiobook-builder"}

    repaired = verify_repair(
        ("qwen3-tts",),
        repair=True,
        manifests=[canvas_manifest, book_manifest],
        devices=[GPU],
    )
    assert repaired.code == EXIT_OK, repaired.message
    for app in APPS:
        assert inspect_view(root, "qwen3-tts", files_17, app, sizes=sizes_17) == "ok"
        assert inspect_view(root, "qwen3-tts", files_06, app, sizes=sizes_06) == "ok"
        labels = _labels(app, canvas_manifest if app == "canvas" else book_manifest)
        assert labels["Qwen3-TTS 1.7B Base"] == "Installed"
        assert labels["Qwen3-TTS 0.6B"] == "Installed"
    for item in (*row_17.files, *row_06.files):
        assert _refs_for(item.sha256) == {"canvas", "audiobook-builder"}
