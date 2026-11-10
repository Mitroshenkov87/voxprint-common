"""Grouped manage page: display fields, task groups, tier split, protected groups."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxprint_common.gpu_probe import GpuDevice, VramTier
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.catalog import CatalogError, load_manifest
from voxprint_common.mdl.control import Controller
from voxprint_common.mdl.descriptor import DescriptorError, parse_app_manifest, parse_descriptor
from voxprint_common.mdl.present import (
    GROUP_ORDER,
    ManageRow,
    can_remove,
    group_manage_rows,
    normalize_group,
)
from voxprint_common.paths import models_dir
from voxprint_common.runtime_manifest import add_ref, load

PAYLOAD = b"voxprint-manage-groups"
DIGEST = sha256_bytes(PAYLOAD)
_URL = "https://huggingface.co/org/weights.bin"


def _gpu(mib: int) -> GpuDevice:
    return GpuDevice(0, "Test GPU", "610.00", (8, 9), mib)


def _entry(
    model_id: str,
    *,
    tier: str = "any",
    label: str | None = None,
    purpose: str | None = None,
    group: str | None = None,
    min_vram_gib: float | None = None,
    digest: str = DIGEST,
    size: int = len(PAYLOAD),
) -> dict[str, object]:
    row: dict[str, object] = {
        "id": model_id,
        "version": "1",
        "vram_tier": tier,
        "files": [
            {
                "path": f"{model_id}.bin",
                "size": size,
                "sha256": digest,
                "mirrors": [{"source": "url", "url": _URL}],
            }
        ],
    }
    if label is not None:
        row["label"] = label
    if purpose is not None:
        row["purpose"] = purpose
    if group is not None:
        row["group"] = group
    if min_vram_gib is not None:
        row["min_vram_gib"] = min_vram_gib
    return row


def _manifest(folder: Path, entries: list[dict[str, object]], app: str = "canvas") -> Path:
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "app": app, "models": entries}), encoding="utf-8")
    return path


def _descriptor(model_id: str) -> object:
    return parse_descriptor(
        {
            "schema": "voxprint-model/1",
            "id": model_id,
            "display_name": model_id,
            "app_ids": ["canvas"],
            "task": "test",
            "license": "apache-2.0",
            "files": [{"path": "w.bin", "size": len(PAYLOAD), "sha256": DIGEST}],
            "mirrors": [{"kind": "hf", "ref": "org/repo", "revision": "main"}],
            "tier": {"min_vram_gib": 0, "recommended_vram_gib": 0, "gpu_min_cc": 0},
            "runtime": {"engine": "diffusers", "version_req": ">=0.30"},
            "base_model": "",
            "card_url": "",
            "flags": [],
            "on_demand": False,
            "build": {"forge_version": "0.1.0", "tag": "", "date": "2026-11-10"},
        }
    )


def test_schema1_display_fields_parse_with_defaults(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        [
            _entry("painted", label="Painted", purpose="Paints things.", group="generate"),
            _entry("plain"),
        ],
    )
    models = load_manifest(path).models
    painted, plain = models
    assert (painted.label, painted.purpose, painted.group) == (
        "Painted",
        "Paints things.",
        "generate",
    )
    assert (plain.label, plain.purpose, plain.group) == ("", "", "")


def test_schema1_display_field_types_are_checked(tmp_path: Path) -> None:
    for field, value in (("label", 3), ("purpose", []), ("group", 5)):
        path = _manifest(tmp_path / field, [_entry("bad", **{field: value})])
        with pytest.raises(CatalogError, match=field):
            load_manifest(path)


def test_app_models_entries_carry_display_fields(tmp_path: Path) -> None:
    path = tmp_path / "voxprint-app-models.json"
    path.write_text(
        json.dumps(
            {
                "schema": "voxprint-app-models/1",
                "app_id": "canvas",
                "tiers": {
                    "8gb": [{"id": "shared-model", "group": "voice"}],
                    "16gb": [
                        {
                            "id": "shared-model",
                            "label": "Shared 16",
                            "purpose": "Second tier fills the label.",
                        },
                        {"id": "solo", "label": "Solo", "group": "writer"},
                    ],
                },
                "runtimes": [],
                "optional": [
                    {
                        "id": "plug-in",
                        "min_vram_gib": None,
                        "label": "Plug-in",
                        "group": "upscale",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    descriptors = {
        "shared-model": _descriptor("shared-model"),
        "solo": _descriptor("solo"),
        "plug-in": _descriptor("plug-in"),
    }
    models = {model.id: model for model in load_manifest(path, descriptors=descriptors).models}
    shared = models["shared-model"]
    assert shared.label == "Shared 16"
    assert shared.purpose == "Second tier fills the label."
    assert shared.group == "voice"
    assert (models["solo"].label, models["solo"].group) == ("Solo", "writer")
    assert (models["plug-in"].label, models["plug-in"].group) == ("Plug-in", "upscale")


def test_app_models_display_field_types_are_checked() -> None:
    base = {
        "schema": "voxprint-app-models/1",
        "app_id": "canvas",
        "tiers": {},
        "runtimes": [],
        "optional": [],
    }
    bad_tier = dict(base, tiers={"16gb": [{"id": "m", "label": 3}]})
    with pytest.raises(DescriptorError, match="label"):
        parse_app_manifest(bad_tier)
    bad_optional = dict(
        base,
        tiers={"8gb": ["keep"]},
        optional=[{"id": "m", "min_vram_gib": None, "group": ["x"]}],
    )
    with pytest.raises(DescriptorError, match="group"):
        parse_app_manifest(bad_optional)


def test_unknown_group_loads_and_lands_in_other(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        [_entry("odd", group="wat"), _entry("blank", group=""), _entry("gen", group="generate")],
    )
    models = load_manifest(path).models
    assert [normalize_group(model.group) for model in models] == ["other", "other", "generate"]
    controller = Controller()
    page = controller.manage_page("canvas", path, devices=[_gpu(16384)], lang="en")
    assert [group.key for group in page.groups] == ["generate", "other"]


def _row(key: str, group: str, badge: str = "") -> ManageRow:
    return ManageRow(key, key, "1.0 MB", "ready", "Installed", "", "", group=group, badge=badge)


def test_grouping_order_titles_and_hidden_groups() -> None:
    rows = [
        _row("w", "writer"),
        _row("v", "voice"),
        _row("i", "internal"),
        _row("s", "safety"),
        _row("o", "bogus-group"),
        _row("g", "generate"),
    ]
    groups = group_manage_rows(rows, tier=VramTier.UNKNOWN, lang="en")
    assert [group.key for group in groups] == [
        "generate",
        "voice",
        "writer",
        "other",
        "safety",
        "internal",
    ]
    assert groups[0].title == "Generate"
    assert groups[3].title == "Other"
    assert {group.key for group in groups}.isdisjoint({"edit", "upscale", "restore", "transcribe"})
    german = group_manage_rows(rows, tier=VramTier.UNKNOWN, lang="de")
    assert german[0].title == "Generieren"
    assert german[4].collapsed and german[5].collapsed
    assert not german[0].collapsed


def test_tier_split_opens_the_detected_half() -> None:
    rows = [
        _row("c16", "generate", "16gb"),
        _row("c8", "generate", "8gb"),
        _row("any", "generate", ""),
        _row("full", "generate", "24gb"),
    ]
    for tier, compact_open in (
        (VramTier.GB24, False),
        (VramTier.GB16, True),
        (VramTier.GB8, True),
        (VramTier.UNKNOWN, True),
    ):
        groups = group_manage_rows(rows, tier=tier, lang="en")
        (group,) = groups
        compact, full = group.sections
        assert compact.open is compact_open
        assert full.open is (not compact_open)
        open_rows = compact.rows if compact_open else full.rows
        closed_rows = full.rows if compact_open else compact.rows
        if compact_open:
            assert [row.key for row in open_rows] == ["c16", "c8", "any"]
            assert [row.key for row in closed_rows] == ["full"]
        else:
            assert [row.key for row in open_rows] == ["full", "any"]
            assert [row.key for row in closed_rows] == ["c16", "c8"]
        assert any(row.key == "any" and row.badge == "" for row in open_rows)


def test_single_tier_group_stays_flat_with_badges() -> None:
    rows = [_row("c16", "upscale", "16gb"), _row("c8", "upscale", "8gb")]
    (group,) = group_manage_rows(rows, tier=VramTier.GB24, lang="en")
    assert group.sections == ()
    assert [row.badge for row in group.rows] == ["16gb", "8gb"]


def test_manage_page_lists_other_tiers_while_listing_hides_them(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        [
            _entry("gen-16", tier="16gb", label="Gen 16", group="generate"),
            _entry("gen-24", tier="24gb", label="Gen 24", group="generate"),
        ],
    )
    controller = Controller()
    card16 = controller.listing("canvas", path, devices=[_gpu(16384)])
    assert [row.model_id for row in card16] == ["gen-16"]
    page = controller.manage_page("canvas", path, devices=[_gpu(16384)], lang="en")
    (group,) = page.groups
    assert group.key == "generate"
    compact, full = group.sections
    assert compact.open and [row.key for row in compact.rows] == ["gen-16"]
    assert not full.open and [row.key for row in full.rows] == ["gen-24"]
    assert not page.installed and len(page.available) == 2  # old tuples stay populated
    visible = controller.listing("canvas", path, devices=[_gpu(16384)], all_tiers=True)
    assert [row.model_id for row in visible] == ["gen-16", "gen-24"]
    no_gpu = controller.manage_page("canvas", path, devices=[], lang="en")
    compact, full = no_gpu.groups[0].sections
    assert compact.open and not full.open


def test_badges_follow_vram_tier_not_min_vram(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        [
            _entry("gen-16", tier="16gb", min_vram_gib=12, label="Gen 16", group="generate"),
            _entry("gen-24", tier="24gb", min_vram_gib=15.5, label="Gen 24", group="generate"),
            _entry("gen-any", tier="any", min_vram_gib=6, label="Gen any", group="generate"),
        ],
    )
    controller = Controller()
    page = controller.manage_page("canvas", path, devices=[_gpu(16384)], lang="en")
    (group,) = page.groups
    compact, full = group.sections
    assert compact.open and not full.open
    assert [row.key for row in compact.rows] == ["gen-16", "gen-any"]
    assert [row.key for row in full.rows] == ["gen-24"]
    rows = {row.key: row for row in (*compact.rows, *full.rows)}
    assert rows["gen-16"].badge == "16gb"
    assert rows["gen-24"].badge == "24gb"
    assert rows["gen-any"].badge == ""


def test_protected_groups_refuse_removal(tmp_path: Path) -> None:
    assert can_remove("voice") and can_remove("bogus")
    assert not can_remove("safety") and not can_remove("internal")
    controller = Controller()
    before = load().components
    assert controller.remove_model("canvas", "model-nope", group="safety") is False
    assert controller.remove_model("canvas", "model-nope", group="internal") is False
    assert load().components == before


def test_manage_page_protected_rows_have_no_remove(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        [
            _entry("guard", group="safety", label="Prompt guard"),
            _entry("helper", group="internal", label="Helper"),
            _entry("open-model", group="voice", label="Voice"),
        ],
    )
    blob = models_dir() / "blobs" / DIGEST
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(PAYLOAD)
    component = f"model-{DIGEST[:12]}"
    add_ref(component, "canvas", kind="model", version=DIGEST, path=str(blob), sha256=DIGEST)
    controller = Controller()
    page = controller.manage_page("canvas", path, devices=[_gpu(16384)], lang="en")
    groups = {group.key: group for group in page.groups}
    assert set(groups) == {"voice", "safety", "internal"}
    assert groups["safety"].collapsed and groups["internal"].collapsed
    assert not groups["voice"].collapsed
    rows = {row.key: row for row in (*page.installed, *page.available)}
    assert rows["guard"].removable is False and rows["guard"].action == ""
    assert rows["helper"].removable is False and rows["helper"].action == ""
    assert rows["open-model"].removable is True and rows["open-model"].action == "remove"
    assert rows["guard"].title == "Prompt guard"
    assert rows["guard"].key == "guard"  # the raw id, also painted as the tooltip
    assert GROUP_ORDER.index("other") == GROUP_ORDER.index("safety") - 1
