"""Canvas view paths stay inside the view folder."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from voxprint_common.mdl.catalog import CatalogError, load_manifest
from voxprint_common.mdl.descriptor import DescriptorError, parse_descriptor
from voxprint_common.mdl.views import materialize_views
from voxprint_common.paths import models_dir

_DIGEST = "ab" * 32


def _descriptor(path: str) -> dict[str, object]:
    return {
        "schema": "voxprint-model/1",
        "id": "painter",
        "display_name": "Painter",
        "app_ids": ["canvas"],
        "task": "image",
        "license": "apache-2.0",
        "files": [{"path": path, "size": 1, "sha256": _DIGEST}],
        "mirrors": [{"kind": "hf", "ref": "Mitroshenkov87/painter", "revision": "main"}],
        "tier": {"min_vram_gib": 8, "recommended_vram_gib": 8, "gpu_min_cc": 8.0},
        "runtime": {"engine": "diffusers", "version_req": ">=0.30"},
        "base_model": "",
        "card_url": "",
        "flags": [],
        "on_demand": False,
        "build": {"forge_version": "0.1.0", "tag": "", "date": "2026-10-26"},
    }


def test_canvas_view_refuses_a_windows_drive_path(tmp_path: Path) -> None:
    blob = models_dir() / "blobs" / _DIGEST
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"x")
    names = ("C:Users/Public/foo.json", "nested/C:secret.json")
    for name in names:
        item = SimpleNamespace(
            apps=("canvas",),
            sha256=_DIGEST,
            model_id="painter",
            name=name,
            layouts=(("painter", name),),
        )
        with pytest.raises(OSError, match="leaves the model folder"):
            materialize_views([item])
        manifest = tmp_path / f"{name.replace('/', '-')}.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "app": "canvas",
                    "models": [
                        {
                            "id": "painter",
                            "version": "1",
                            "vram_tier": "8gb",
                            "files": [
                                {
                                    "path": name,
                                    "size": 1,
                                    "sha256": _DIGEST,
                                    "mirrors": ["https://example.invalid/foo.json"],
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(CatalogError, match="stay inside the model"):
            load_manifest(manifest)
        with pytest.raises(DescriptorError, match="not allowed"):
            parse_descriptor(_descriptor(name))
    root = models_dir()
    assert list(root.rglob("foo.json")) == []
    assert list(root.rglob("secret.json")) == []
    canvas = root / "views" / "canvas"
    if canvas.exists():
        assert list(canvas.glob("*.staging-*")) == []
