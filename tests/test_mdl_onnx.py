"""onnxruntime engine, pre-built artifacts, and post_install commands."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import jsonschema
import pytest

from voxprint_common.gpu_probe import GpuDevice, VramTier
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.catalog import load_manifest, plan_files
from voxprint_common.mdl.descriptor import DescriptorError, parse_descriptor, schema_bytes
from voxprint_common.mdl.hooks import hook_problems
from voxprint_common.mdl.job import verify
from voxprint_common.mdl.onnx_prebuilt import set_onnxruntime_probe
from voxprint_common.paths import models_dir

PAYLOAD = b"voxprint-onnx-source-weights" * 8
DIGEST = sha256_bytes(PAYLOAD)
ONNX = b"fp16-onnx"
ONNX_SHA = sha256_bytes(ONNX)
_SCRIPT = """
import pathlib, sys, time
out = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
if mode == "sleep":
    time.sleep(60)
out.mkdir(parents=True, exist_ok=True)
(out / "model.onnx").write_bytes(b"fp16-onnx" if mode == "ok" else b"wrong")
"""


def _post_install(**overrides: object) -> dict[str, object]:
    hook: dict[str, object] = {
        "cli": "prep.cmd" if os.name == "nt" else "prep",
        "args": ["{model_dir}", "ok"],
        "outputs": [{"path": "model.onnx", "sha256": ONNX_SHA}],
    }
    hook.update(overrides)
    return hook


def _descriptor(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": "voxprint-model/1",
        "id": "upscaler",
        "display_name": "Upscaler",
        "app_ids": ["enlarge"],
        "task": "upscale",
        "license": "apache-2.0",
        "files": [{"path": "weights/model.pth", "size": len(PAYLOAD), "sha256": DIGEST}],
        "mirrors": [{"kind": "hf", "ref": "voxprint/upscaler", "revision": "main"}],
        "tier": {"min_vram_gib": 2, "recommended_vram_gib": 4, "gpu_min_cc": 7.5},
        "runtime": {"engine": "onnxruntime", "version_req": ">=1.20"},
        "base_model": "",
        "card_url": "",
        "flags": [],
        "on_demand": False,
        "build": {"forge_version": "0.1.0", "tag": "", "date": "2026-10-17"},
        "onnx_prebuilt": {
            "path": "onnx/model.onnx",
            "sha256": ONNX_SHA,
            "size": len(ONNX),
            "url": "https://example.invalid/model.onnx",
            "built_for": {"onnxruntime": ">=1.20,<2", "opset": 17},
        },
        "post_install": _post_install(),
    }
    document.update(overrides)
    return document


def _app(folder: Path, hook: dict[str, object], prebuilt: bool = False) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "prep.py").write_text(_SCRIPT, encoding="utf-8")
    if os.name == "nt":
        (folder / "prep.cmd").write_text(
            f'@"{sys.executable}" "%~dp0prep.py" %*\r\n', encoding="utf-8"
        )
    else:
        script = folder / "prep"
        script.write_text(f"#!{sys.executable}\n{_SCRIPT}", encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    model: dict[str, object] = {
        "id": "upscaler",
        "version": "1",
        "vram_tier": "8gb",
        "post_install": hook,
        "files": [
            {
                "path": "model.pth",
                "size": len(PAYLOAD),
                "sha256": DIGEST,
                "mirrors": ["https://example.invalid/model.pth"],
            }
        ],
    }
    if prebuilt:
        model["onnx_prebuilt"] = _descriptor()["onnx_prebuilt"]
    path = folder / "mdl.json"
    path.write_text(
        json.dumps({"schema": 1, "app": "enlarge", "models": [model]}), encoding="utf-8"
    )
    blob = models_dir() / "blobs" / DIGEST
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(PAYLOAD)
    return path


def _gpu() -> GpuDevice:
    return GpuDevice(0, "Test GPU", "610.00", (8, 9), 8192)


def test_schema_accepts_onnxruntime_prebuilt_and_post_install() -> None:
    document = _descriptor()
    jsonschema.Draft202012Validator(json.loads(schema_bytes())).validate(document)
    parsed = parse_descriptor(document)
    assert parsed.runtime.engine == "onnxruntime"
    assert parsed.onnx_prebuilt[0].opset == 17
    assert parsed.post_install is not None and parsed.post_install.timeout_s == 600


@pytest.mark.parametrize(
    "hook",
    [
        _post_install(args=["{models_root}"]),
        _post_install(cli="../bin/prep"),
        _post_install(cli="/usr/bin/python3"),
        _post_install(outputs=[{"path": "../escape.onnx"}]),
    ],
)
def test_unsafe_post_install_is_rejected(hook: dict[str, object]) -> None:
    with pytest.raises(DescriptorError):
        parse_descriptor(_descriptor(post_install=hook))


def test_stale_prebuilt_falls_back_to_source_and_post_install(tmp_path: Path) -> None:
    manifest = load_manifest(_app(tmp_path / "enlarge", _post_install(), prebuilt=True))
    try:
        set_onnxruntime_probe(lambda: ("1.18.0", 21))
        stale = plan_files([manifest], VramTier.GB8)
        set_onnxruntime_probe(lambda: ("1.22.1", 21))
        fresh = plan_files([manifest], VramTier.GB8)
    finally:
        set_onnxruntime_probe(None)
    assert [item.name for item in stale] == ["model.pth"]
    assert any(plan.hook.cli for plan in stale[0].hooks)
    assert [item.name for item in fresh] == ["model.pth", "onnx/model.onnx"]
    assert not any(plan.hook.cli for plan in fresh[0].hooks)


def test_post_install_success(tmp_path: Path) -> None:
    outcome = verify([_app(tmp_path / "enlarge", _post_install())], devices=[_gpu()])
    assert outcome.code == 0, outcome.message
    assert (models_dir() / "enlarge" / "upscaler" / "model.onnx").read_bytes() == ONNX
    assert hook_problems() == {}


def test_post_install_timeout_leaves_model_not_ready(tmp_path: Path) -> None:
    hook = _post_install(args=["{model_dir}", "sleep"], timeout_s=1)
    outcome = verify([_app(tmp_path / "enlarge", hook)], devices=[_gpu()])
    assert outcome.code != 0
    assert "too long" in hook_problems()["enlarge/upscaler"]


def test_post_install_sha_mismatch_leaves_model_not_ready(tmp_path: Path) -> None:
    hook = _post_install(args=["{model_dir}", "bad"])
    outcome = verify([_app(tmp_path / "enlarge", hook)], devices=[_gpu()])
    assert outcome.code != 0
    assert "checksum" in hook_problems()["enlarge/upscaler"]
