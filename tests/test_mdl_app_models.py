"""App-model descriptor resolution and per-tier file selection."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

from voxprint_common.gpu_probe import GpuDevice
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.descriptor import Descriptor, parse_descriptor
from voxprint_common.mdl.job import download, verify
from voxprint_common.mdl.readiness import disk_ready, gaps_for
from voxprint_common.mdl.resolve import resolve_descriptors
from voxprint_common.mdl.safety import UrlGuard
from voxprint_common.paths import models_dir, state_dir
from voxprint_common.runtime_manifest import load

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_DINOV2 = "5d34e67a046e3a8c70beaf98c198784ee7ff89409ceb415fb9bea16511b39947"
_MODEL = "controlnet-union"


class _Reply:
    def __init__(self, body: bytes) -> None:
        self.status = 200
        self._body = body

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._body
        return self._body[:size]

    def close(self) -> None:
        return None


def _gpu(mib: int) -> GpuDevice:
    return GpuDevice(0, "Test", "1", (8, 9), mib)


def _guard() -> UrlGuard:
    return UrlGuard(resolve=lambda _host: ["1.1.1.1"])


def test_fixture_index_resolves_dinov2() -> None:
    index = (_FIXTURES / "live-index.json").read_bytes()
    signature = (_FIXTURES / "live-index.json.sig").read_bytes()
    body = (_FIXTURES / "dinov2-base-voxprint-model.json").read_bytes()
    seen: list[str] = []

    def opener(request: object, timeout: float) -> _Reply:
        del timeout
        url = str(getattr(request, "full_url", ""))
        seen.append(url)
        path = urllib.parse.urlparse(url).path
        if path.endswith("/index.json"):
            return _Reply(index)
        if path.endswith("/index.json.sig"):
            return _Reply(signature)
        if path.endswith("/descriptors/dinov2-base/voxprint-model.json"):
            return _Reply(body)
        raise OSError(url)

    found = resolve_descriptors(["dinov2-base"], opener=opener, guard=_guard())
    assert found["dinov2-base"].id == "dinov2-base"
    cached = state_dir() / "mdl-descriptors" / f"{_DINOV2}.json"
    assert sha256_bytes(cached.read_bytes()) == _DINOV2
    assert any(
        "/datasets/Mitroshenkov87/voxprint-model-index/resolve/" in url
        and url.endswith("/descriptors/dinov2-base/voxprint-model.json")
        for url in seen
    )


def _weights() -> tuple[bytes, str, str]:
    lite = b"lite-weights"
    return lite, sha256_bytes(lite), "ab" * 32


def _descriptor(lite_sha: str, full_sha: str, lite_size: int) -> Descriptor:
    return parse_descriptor(
        {
            "schema": "voxprint-model/1",
            "id": _MODEL,
            "display_name": "ControlNet union",
            "app_ids": ["canvas"],
            "task": "controlnet",
            "license": "apache-2.0",
            "files": [
                {"path": "full.safetensors", "size": 16_000, "sha256": full_sha},
                {"path": "lite.safetensors", "size": lite_size, "sha256": lite_sha},
            ],
            "file_groups": [{"name": "lite", "paths": ["lite.safetensors"]}],
            "mirrors": [{"kind": "hf", "ref": "Mitroshenkov87/controlnet", "revision": "main"}],
            "tier": {"min_vram_gib": 8, "recommended_vram_gib": 16, "gpu_min_cc": 7.5},
            "runtime": {"engine": "diffusers", "version_req": ">=0.30"},
            "base_model": "",
            "card_url": "https://huggingface.co/Mitroshenkov87/controlnet",
            "flags": [],
            "on_demand": True,
            "build": {"forge_version": "0.1.0", "tag": "", "date": "2026-10-18"},
        }
    )


def _manifest(folder: Path) -> Path:
    path = folder / "voxprint-app-models.json"
    path.write_text(
        json.dumps(
            {
                "schema": "voxprint-app-models/1",
                "app_id": "canvas",
                "tiers": {
                    "16gb": [
                        {"id": _MODEL, "files": ["*lite*"], "file_groups": ["lite"]},
                    ]
                },
                "runtimes": [],
                "optional": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_tier_keeps_the_lite_file(monkeypatch, tmp_path: Path) -> None:
    lite, lite_sha, full_sha = _weights()
    descriptor = _descriptor(lite_sha, full_sha, len(lite))

    def resolve(model_ids, *, opener=None, guard=None, index=None):
        del opener, guard, index
        assert list(model_ids) == [_MODEL]
        return {_MODEL: descriptor}

    monkeypatch.setattr("voxprint_common.mdl.catalog.resolve_descriptors", resolve)
    path = _manifest(tmp_path)
    card = [_gpu(16384)]
    gaps = gaps_for("canvas", manifests=[path], devices=card)
    labels = [gap.label for gap in gaps]
    assert any("lite.safetensors" in label for label in labels)
    assert all("full.safetensors" not in label for label in labels)
    blob = models_dir() / "blobs" / lite_sha
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(lite)
    assert disk_ready("canvas", manifests=[path], devices=card)
    checked = verify([path], devices=card, opener=lambda *_args, **_kwargs: None)
    assert checked.code == 0
    assert "full.safetensors" not in checked.message
    fetched = download(
        [path],
        devices=card,
        disk_free=lambda _path: 0,
        priority=False,
        opener=lambda *_args, **_kwargs: None,
        guard=_guard(),
    )
    assert fetched.code == 0
    recorded = {item.sha256 for item in load().components.values()}
    assert lite_sha in recorded
    assert full_sha not in recorded


def test_missing_descriptor_exits_nonzero(tmp_path: Path) -> None:
    path = tmp_path / "voxprint-app-models.json"
    path.write_text(
        json.dumps(
            {
                "schema": "voxprint-app-models/1",
                "app_id": "canvas",
                "tiers": {"16gb": ["not-a-real-model"]},
                "runtimes": [],
                "optional": [],
            }
        ),
        encoding="utf-8",
    )

    def opener(request: object, timeout: float) -> _Reply:
        del request, timeout
        raise OSError("offline")

    outcome = download(
        [path],
        dry_run=True,
        devices=[_gpu(16384)],
        disk_free=lambda _folder: 10**15,
        opener=opener,
        priority=False,
        guard=_guard(),
    )
    assert outcome.code != 0
    assert "nothing to download" not in outcome.message
    assert "not-a-real-model" in outcome.message
