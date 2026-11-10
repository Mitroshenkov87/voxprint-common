"""Manage page tabs: share keys, shared rows, required models, fail-closed safety."""

from __future__ import annotations

import json
from pathlib import Path

from voxprint_common.gpu_probe import VramTier
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.catalog import load_manifest, model_share_key
from voxprint_common.mdl.control import Controller
from voxprint_common.mdl.present import ManageRow, badge_label, group_manage_rows
from voxprint_common.mdl.readiness import shared_safety_gaps
from voxprint_common.paths import models_dir, state_dir
from voxprint_common.runtime_manifest import add_ref, load

PAYLOAD = b"voxprint-manage-tabs"
DIGEST = sha256_bytes(PAYLOAD)
_URL = "https://huggingface.co/org/weights.bin"


def _hf(repo: str, revision: str = "main") -> dict[str, object]:
    return {"source": "huggingface", "repo": repo, "revision": revision, "filename": "w.bin"}


def _entry(
    model_id: str,
    *,
    tier: str = "any",
    group: str = "voice",
    label: str = "",
    digest: str = DIGEST,
    size: int = len(PAYLOAD),
    mirrors: list[dict[str, object]] | None = None,
    optional: bool = False,
    on_demand: bool = False,
) -> dict[str, object]:
    return {
        "id": model_id,
        "version": "1",
        "vram_tier": tier,
        "group": group,
        "label": label,
        "optional": optional,
        "on_demand": on_demand,
        "files": [
            {
                "path": f"{model_id}.bin",
                "size": size,
                "sha256": digest,
                "mirrors": mirrors if mirrors is not None else [{"source": "url", "url": _URL}],
            }
        ],
    }


def _manifest(folder: Path, app: str, entries: list[dict[str, object]]) -> Path:
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "app": app, "models": entries}), encoding="utf-8")
    return path


def _install(*apps: tuple[str, Path]) -> None:
    state = state_dir()
    state.mkdir(parents=True, exist_ok=True)
    (state / "installed-apps.json").write_text(
        json.dumps({"apps": [{"id": app_id, "path": str(path)} for app_id, path in apps]}),
        encoding="utf-8",
    )


def _blob(digest: str = DIGEST, payload: bytes = PAYLOAD) -> Path:
    blob = models_dir() / "blobs" / digest
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(payload)
    return blob


def _share(component_app: str, other_app: str, digest: str = DIGEST) -> None:
    blob = _blob(digest)
    component = f"model-{digest[:12]}"
    add_ref(component, component_app, kind="model", version=digest, path=str(blob), sha256=digest)
    add_ref(component, other_app, kind="model", version=digest, path=str(blob), sha256=digest)


def _rows_by_key(page: object) -> dict[str, ManageRow]:
    rows: dict[str, ManageRow] = {}
    for tab in page.tabs:
        for group in tab.groups:
            for row in group.rows:
                rows[row.key] = row
            for section in group.sections:
                for row in section.rows:
                    rows[row.key] = row
    return rows


def test_share_key_is_repo_plus_revision_not_id(tmp_path: Path) -> None:
    same_upstream_a = _manifest(
        tmp_path / "a",
        "canvas",
        [_entry("z-image-turbo", mirrors=[_hf("Org/Z-Image-Turbo", "rev1")])],
    )
    same_upstream_b = _manifest(
        tmp_path / "b",
        "model-forge",
        [_entry("z-image-turbo-bf16", mirrors=[_hf("org/z-image-turbo", "rev1")])],
    )
    parsed_a = load_manifest(same_upstream_a)
    parsed_b = load_manifest(same_upstream_b)
    # Different ids, same repo+revision: one share key, case-insensitive repo.
    assert model_share_key(parsed_a.models[0]) == model_share_key(parsed_b.models[0])
    # Same id, different repo+revision: not shared.
    other = _manifest(
        tmp_path / "c",
        "plotweaver",
        [_entry("z-image-turbo", mirrors=[_hf("Org/Z-Image-Turbo", "rev2")])],
    )
    assert model_share_key(load_manifest(other).models[0]) != model_share_key(parsed_a.models[0])
    # Same id, different repo entirely: not shared.
    fork = _manifest(
        tmp_path / "d",
        "plotweaver",
        [_entry("z-image-turbo", mirrors=[_hf("Someone/Else", "rev1")])],
    )
    assert model_share_key(load_manifest(fork).models[0]) != model_share_key(parsed_a.models[0])
    # No readable repo+revision: falls back to the blob component id.
    plain = load_manifest(_manifest(tmp_path / "e", "canvas", [_entry("voice-plain")]))
    assert model_share_key(plain.models[0]) == f"model-{DIGEST[:12]}"


def test_tabs_split_this_app_from_shared_by_manifest_upstream(tmp_path: Path) -> None:
    canvas = _manifest(
        tmp_path / "canvas",
        "canvas",
        [
            _entry("yunet-face", group="safety", mirrors=[_hf("Org/yunet-2023mar", "c1")]),
            _entry("solo-model", group="voice"),
        ],
    )
    forge = _manifest(
        tmp_path / "forge",
        "model-forge",
        [_entry("yunet-2023mar", group="safety", mirrors=[_hf("Org/yunet-2023mar", "c1")])],
    )
    _install(("canvas", canvas.parent), ("model-forge", forge.parent))
    page = Controller().manage_page("canvas", canvas, devices=[], lang="en")
    assert [tab.key for tab in page.tabs] == ["this_app", "shared"]
    this_app, shared = page.tabs
    assert this_app.title == "This app (1)" and this_app.count == 1
    assert shared.title == "Shared (1)" and shared.count == 1
    rows = _rows_by_key(page)
    yunet = rows["yunet-face"]
    assert "Used by:" in yunet.note
    assert "VOXPrint AI Canvas" in yunet.note and "VOXPrint AI Model Forge" in yunet.note


def test_tabs_split_this_app_from_shared_by_refcount(tmp_path: Path) -> None:
    canvas = _manifest(tmp_path / "canvas", "canvas", [_entry("shared-voice")])
    other = _manifest(tmp_path / "other", "clarity", [_entry("other-model")])
    _install(("canvas", canvas.parent), ("clarity", other.parent))
    _share("canvas", "clarity")
    page = Controller().manage_page("canvas", canvas, devices=[], lang="en")
    rows = _rows_by_key(page)
    row = rows["shared-voice"]
    assert row.state == "ready"
    assert "Used by:" in row.note
    assert "VOXPrint AI Clarity" in row.note
    # Only the Shared tab has rows: the refcounted row is shared even though
    # the other app's manifest never lists it.
    assert [tab.key for tab in page.tabs] == ["shared"]


def test_duplicate_ids_in_one_manifest_load_as_separate_rows(tmp_path: Path) -> None:
    dubber = _manifest(
        tmp_path / "dubber",
        "movie-dubber",
        [
            _entry("qwen3-tts", tier="24gb", label="Full size", mirrors=[_hf("Org/TTS-1.7B")]),
            _entry("qwen3-tts", tier="16gb", label="16 GB cards", mirrors=[_hf("Org/TTS-0.6B")]),
            _entry("qwen3-tts", tier="any", label="Compact", mirrors=[_hf("Org/TTS-0.6B")]),
        ],
    )
    parsed = load_manifest(dubber)
    assert [model.id for model in parsed.models] == ["qwen3-tts"] * 3
    page = Controller().manage_page("movie-dubber", dubber, devices=[], lang="en")
    keys = [row.key for row in (*page.installed, *page.available)]
    assert len(keys) == len(set(keys)) == 3
    assert all(key.startswith("qwen3-tts|") for key in keys)
    # The two 0.6B rows share a repo but differ by tier, so both keys are stable.
    assert any("|16gb|" in key for key in keys)
    assert any("|any|" in key for key in keys)
    for row in (*page.installed, *page.available):
        assert row.model_id == "qwen3-tts"


def test_required_by_another_installed_app_has_no_remove_in_shared(tmp_path: Path) -> None:
    canvas = _manifest(
        tmp_path / "canvas",
        "canvas",
        [
            _entry("asr-model", group="transcribe", mirrors=[_hf("Org/ASR", "r1")]),
            _entry(
                "optional-plug",
                group="transcribe",
                optional=True,
                mirrors=[_hf("Org/Plug", "r1")],
            ),
        ],
    )
    dubber = _manifest(
        tmp_path / "dubber",
        "movie-dubber",
        [
            _entry("asr-model", group="transcribe", mirrors=[_hf("Org/ASR", "r1")]),
            _entry(
                "optional-plug",
                group="transcribe",
                optional=True,
                mirrors=[_hf("Org/Plug", "r1")],
            ),
        ],
    )
    _install(("canvas", canvas.parent), ("movie-dubber", dubber.parent))
    _blob()
    page = Controller().manage_page("canvas", canvas, devices=[], lang="en")
    rows = _rows_by_key(page)
    required = rows["asr-model"]
    assert required.state == "ready"
    assert required.action == "" and required.removable is False
    assert "Required by VOXPrint AI Movie Dubber" in required.note
    optional = rows["optional-plug"]
    assert optional.removable is True
    assert "Remove drops only this app's reference" in optional.note


def test_safety_marked_by_any_app_says_required_for_safety(tmp_path: Path) -> None:
    canvas = _manifest(
        tmp_path / "canvas",
        "canvas",
        [_entry("qwen3guard", group="safety", optional=True, mirrors=[_hf("Org/Guard", "g1")])],
    )
    forge = _manifest(
        tmp_path / "forge",
        "model-forge",
        [_entry("forge-guard", group="safety", mirrors=[_hf("Org/Guard", "g1")])],
    )
    _install(("canvas", canvas.parent), ("model-forge", forge.parent))
    _blob()
    page = Controller().manage_page("canvas", canvas, devices=[], lang="en")
    rows = _rows_by_key(page)
    row = rows["qwen3guard"]
    # optional in Canvas, safety in Forge: safety wins, no Remove anywhere.
    assert row.state == "ready"
    assert row.action == "" and row.removable is False
    assert "Required for safety" in row.note


def test_missing_shared_safety_fails_closed_with_repair_row(tmp_path: Path) -> None:
    canvas = _manifest(
        tmp_path / "canvas",
        "canvas",
        [_entry("siglip2", group="safety", mirrors=[_hf("Org/SigLIP2", "s1")])],
    )
    forge = _manifest(
        tmp_path / "forge",
        "model-forge",
        [_entry("forge-siglip", group="safety", mirrors=[_hf("Org/SigLIP2", "s1")])],
    )
    _install(("canvas", canvas.parent), ("model-forge", forge.parent))
    page = Controller().manage_page("canvas", canvas, devices=[], lang="en")
    row = _rows_by_key(page)["siglip2"]
    assert row.state == "pending"
    assert row.state_label == "Required, missing"
    assert row.action == "repair" and row.action_label == "Repair"
    assert row.removable is False
    gaps = shared_safety_gaps("canvas")
    assert len(gaps) == 1
    assert gaps[0].state == "missing"
    assert gaps[0].share_key == "org/siglip2@s1"
    assert gaps[0].model_id == "siglip2"


def test_shared_safety_gaps_report_corrupt_and_hash_mismatch(tmp_path: Path) -> None:
    entry = _entry("fairface-age", group="safety")
    canvas = _manifest(tmp_path / "canvas", "canvas", [entry])
    _install(("canvas", canvas.parent))
    # Truncated copy: size mismatch.
    blob = _blob()
    blob.write_bytes(PAYLOAD[:4])
    (gaps,) = shared_safety_gaps("canvas")
    assert gaps.state == "corrupt"
    # Right size, wrong content: hash mismatch.
    blob.write_bytes(b"other-bytes-of-same-length!!"[: len(PAYLOAD)])
    assert len(blob.read_bytes()) == len(PAYLOAD)
    (gap,) = shared_safety_gaps("canvas")
    assert gap.state == "corrupt"
    assert gap.share_key == f"model-{DIGEST[:12]}"
    # Healthy copy: no gap.
    blob.write_bytes(PAYLOAD)
    assert shared_safety_gaps("canvas") == ()


def test_remove_on_shared_row_drops_only_this_apps_ref(tmp_path: Path) -> None:
    canvas = _manifest(tmp_path / "canvas", "canvas", [_entry("shared-voice")])
    clarity = _manifest(tmp_path / "clarity", "clarity", [_entry("other-model")])
    _install(("canvas", canvas.parent), ("clarity", clarity.parent))
    _share("canvas", "clarity")
    blob = models_dir() / "blobs" / DIGEST
    controller = Controller()
    component = f"model-{DIGEST[:12]}"
    # Remove refuses protected groups, drops the ref otherwise, and only
    # deletes the files when the refcount reaches zero.
    assert controller.remove_model("canvas", component, group="safety") is False
    assert controller.remove_model("canvas", component, group="voice") is False
    assert blob.is_file()
    assert load().components[component].refs == ["clarity"]
    assert controller.remove_model("clarity", component, group="voice") is True
    assert not blob.exists()
    assert component not in load().components


def test_badge_display_maps_tier_but_grouping_keeps_raw_values() -> None:
    assert badge_label("8gb", lang="en") == "8 GB"
    assert badge_label("16gb", lang="en") == "16 GB"
    assert badge_label("24gb", lang="en") == "24 GB"
    assert badge_label("", lang="en") == ""
    assert badge_label("any", lang="en") == ""
    assert badge_label("16gb", lang="de") == "16 GB"
    rows = [
        ManageRow(
            "c16", "C16", "1.0 MB", "ready", "Installed", "", "", group="generate", badge="16gb"
        ),
        ManageRow(
            "c24", "C24", "1.0 MB", "ready", "Installed", "", "", group="generate", badge="24gb"
        ),
    ]
    (group,) = group_manage_rows(rows, tier=VramTier.UNKNOWN, lang="en")
    assert [row.badge for row in group.sections[0].rows] == ["16gb"]
    assert group.sections[1].rows[0].badge == "24gb"
