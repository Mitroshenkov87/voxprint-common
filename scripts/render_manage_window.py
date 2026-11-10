"""Paint the manage-models window at 1280 px, in English.

Run with ``QT_QPA_PLATFORM=offscreen``. The suite home is scripted: real
``mdl.json`` files, a runtime manifest with refcounts, and sparse blobs, so
``Controller.manage_page()`` builds the page through the production code
path. Nothing is hashed or downloaded. A child process paints the same
windows at 150% (``QT_SCALE_FACTOR``).

The 0.7.2 fixtures paint the tabbed manage page: Canvas's 14-row table from
the model poll (16 GB assumed, no GPU), Audiobook Builder, and Movie Dubber
with its three ``qwen3-tts`` rows. PNGs are written to
``/workspace/voxprint-components/renders/0.7.2/``; the Canvas 100% render
also refreshes ``docs/renders/mdl-manage-en.png``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if os.environ.get("MDL_RENDER_SCALE") == "150":
    os.environ["QT_SCALE_FACTOR"] = "1.5"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SUITE_HOME = Path(tempfile.mkdtemp(prefix="mdl-render-072-"))
os.environ["VOXPRINT_HOME"] = str(_SUITE_HOME)

from voxprint_common.mdl.control import Controller  # noqa: E402
from voxprint_common.mdl.present import Snapshot, view_for  # noqa: E402
from voxprint_common.paths import models_dir, state_dir  # noqa: E402
from voxprint_common.runtime_manifest import add_ref  # noqa: E402
from voxprint_common.ui.mdl_window import grab_view  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_DOCS = ROOT / "docs" / "renders"
ART = Path("/opt/cursor/artifacts/renders")
OUT_072 = Path("/workspace/voxprint-components/renders/0.7.2")

_GB = 1024**3
_MB = 1024**2

# Canvas full (rc.44), 14 rows. (id, group, tier, label, purpose, installed, size)
_CANVAS = (
    (
        "z-image-turbo-bf16-16gb",
        "generate",
        "16gb",
        "Z-Image Turbo for 16 GB cards",
        "The default model, sized to fit 16 GB graphics cards.",
        True,
        20_500_000_000,
    ),
    (
        "z-image-turbo-bf16-16gb-upstream",
        "generate",
        "16gb",
        "Z-Image Turbo for 16 GB cards (upstream full-precision)",
        "Full-precision upstream weights for 16 GB cards.",
        False,
        32_900_000_000,
    ),
    (
        "z-image-turbo-bf16",
        "generate",
        "24gb",
        "Z-Image Turbo (recommended)",
        "Fast, high-quality pictures from text; the default.",
        True,
        20_500_000_000,
    ),
    (
        "z-image-turbo-bf16-upstream",
        "generate",
        "24gb",
        "Z-Image Turbo (upstream full-precision)",
        "Full-precision upstream weights.",
        False,
        32_900_000_000,
    ),
    (
        "z-image-creative-bf16",
        "generate",
        "24gb",
        "Z-Image Creative",
        "Slower, more varied and artistic results.",
        False,
        20_500_000_000,
    ),
    (
        "z-image-turbo-fun-controlnet-union-bf16-lite",
        "edit",
        "16gb",
        "Pose & edge guidance, lite",
        "Follows a pose, edges or depth from a reference picture.",
        True,
        2_000_000_000,
    ),
    (
        "z-image-turbo-fun-controlnet-union-bf16",
        "edit",
        "24gb",
        "Pose & edge guidance",
        "Follows a pose, edges or depth from a reference picture.",
        False,
        6_700_000_000,
    ),
    (
        "qwen3-4b-writer",
        "writer",
        "any",
        "Photoshoot writer (Qwen3 4B)",
        "Writes the shot list for Photoshoot series.",
        True,
        8_100_000_000,
    ),
    (
        "qwen3guard-gen-0.6b",
        "safety",
        "any",
        "Prompt safety check",
        "Checks prompts before generating.",
        True,
        1_500_000_000,
    ),
    (
        "siglip2-so400m-patch14-384",
        "safety",
        "any",
        "Image safety check",
        "Checks input and output pictures.",
        True,
        4_600_000_000,
    ),
    (
        "yunet-face",
        "safety",
        "any",
        "Face finder",
        "Finds faces for the safety and likeness checks.",
        True,
        200_000,
    ),
    (
        "fairface-age",
        "safety",
        "any",
        "Age check",
        "Part of the adults-only check.",
        False,
        340_000_000,
    ),
    (
        "mivolo-v2-age",
        "safety",
        "any",
        "Fine age check",
        "Second age check for the adults-only gate.",
        True,
        120_000_000,
    ),
    (
        "nsfw-image-detection",
        "safety",
        "any",
        "Content check",
        "Classifies picture content for the 18+ setting.",
        True,
        340_000_000,
    ),
)

# Model Forge rows used only to share yunet with Canvas (poll: yunet-2023mar
# is the same upstream as Canvas yunet-face). Installed so "Used by" names it.
_FORGE = (
    (
        "yunet-2023mar",
        "safety",
        "any",
        "Face detector",
        "Finds faces for the checks.",
        True,
        200_000,
    ),
)

# Audiobook Builder. gemma-4-12b stays This app (Plotweaver share unconfirmed).
_AUDIOBOOK = (
    (
        "qwen3-tts-06b",
        "voice",
        "8gb",
        "Qwen3-TTS 0.6B",
        "Voice cloning for 16 GB cards.",
        True,
        2_500_000_000,
        "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "v1",
    ),
    (
        "qwen3-tts-17b",
        "voice",
        "16gb",
        "Qwen3-TTS 1.7B (default)",
        "Voice cloning, full size.",
        True,
        4_500_000_000,
        "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "v1",
    ),
    (
        "qwen3-tts-tokenizer",
        "internal",
        "any",
        "Qwen3-TTS tokenizer",
        "Tokenizer for the Qwen3-TTS rows.",
        True,
        700_000_000,
        "Qwen/Qwen3-TTS-12Hz-Tokenizer",
        "v1",
    ),
    (
        "mt-en-ru",
        "translate",
        "any",
        "Translator EN → RU",
        "Helsinki-NLP opus-mt.",
        True,
        300_000_000,
        "Helsinki-NLP/opus-mt-en-ru",
        "v1",
    ),
    (
        "mt-ru-en",
        "translate",
        "any",
        "Translator RU → EN",
        "Helsinki-NLP opus-mt.",
        True,
        300_000_000,
        "Helsinki-NLP/opus-mt-ru-en",
        "v1",
    ),
    (
        "mt-en-de",
        "translate",
        "any",
        "Translator EN → DE",
        "Helsinki-NLP opus-mt.",
        True,
        600_000_000,
        "Helsinki-NLP/opus-mt-en-de",
        "v1",
    ),
    (
        "mt-de-en",
        "translate",
        "any",
        "Translator DE → EN",
        "Helsinki-NLP opus-mt.",
        True,
        600_000_000,
        "Helsinki-NLP/opus-mt-de-en",
        "v1",
    ),
    (
        "mt-ru-de",
        "translate",
        "any",
        "Translator RU → DE",
        "Helsinki-NLP opus-mt.",
        True,
        900_000_000,
        "Helsinki-NLP/opus-mt-ru-de",
        "v1",
    ),
    (
        "mt-de-ru",
        "translate",
        "any",
        "Translator DE → RU",
        "Helsinki-NLP opus-mt.",
        True,
        900_000_000,
        "Helsinki-NLP/opus-mt-de-ru",
        "v1",
    ),
    (
        "ace-step",
        "generate",
        "any",
        "Ace-Step music",
        "Generates music from text.",
        True,
        10_100_000_000,
        "acewithwins/Ace-Step",
        "v1",
    ),
    (
        "gemma-4-12b",
        "writer",
        "any",
        "Gemma 4 12B writer (Q4_K_M)",
        "Long-form writing model.",
        True,
        7_100_000_000,
        "ggml-org/gemma-4-12b-gguf",
        "q4_k_m",
    ),
    (
        "voice-open-universal",
        "voice",
        "any",
        "Universal voice",
        "Default narration voice.",
        True,
        50_000_000,
        "suite/voice-open-universal",
        "v1",
    ),
    (
        "voice-noa",
        "voice",
        "any",
        "Noa voice",
        "Narration voice.",
        True,
        50_000_000,
        "suite/voice-noa",
        "v1",
    ),
    (
        "voice-levi",
        "voice",
        "any",
        "Levi voice",
        "Narration voice.",
        True,
        50_000_000,
        "suite/voice-levi",
        "v1",
    ),
    (
        "voice-natan",
        "voice",
        "any",
        "Natan voice",
        "Narration voice.",
        True,
        50_000_000,
        "suite/voice-natan",
        "v1",
    ),
    (
        "voice-shimon",
        "voice",
        "any",
        "Shimon voice",
        "Narration voice.",
        True,
        50_000_000,
        "suite/voice-shimon",
        "v1",
    ),
    (
        "voice-miriam",
        "voice",
        "any",
        "Miriam voice",
        "Narration voice.",
        True,
        50_000_000,
        "suite/voice-miriam",
        "v1",
    ),
    (
        "voice-rivka",
        "voice",
        "any",
        "Rivka voice",
        "Narration voice.",
        True,
        50_000_000,
        "suite/voice-rivka",
        "v1",
    ),
)

# Movie Dubber: three qwen3-tts rows share one id; the two 0.6B rows share a repo.
_DUBBER = (
    (
        "qwen3-tts",
        "voice",
        "24gb",
        "Voice cloning, full size",
        "Full-size voice cloning.",
        True,
        4_500_000_000,
        "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
        "v1",
    ),
    (
        "qwen3-tts",
        "voice",
        "16gb",
        "Voice cloning for 16 GB cards",
        "Voice cloning sized for 16 GB cards.",
        True,
        2_500_000_000,
        "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "v1",
    ),
    (
        "qwen3-tts",
        "voice",
        "any",
        "Voice cloning, compact",
        "Compact voice cloning.",
        True,
        2_500_000_000,
        "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "v1",
    ),
    (
        "qwen3-tts-tokenizer",
        "internal",
        "any",
        "Qwen3-TTS tokenizer",
        "Tokenizer for the Qwen3-TTS rows.",
        True,
        700_000_000,
        "Qwen/Qwen3-TTS-12Hz-Tokenizer",
        "v1",
    ),
    (
        "asr-whisper-turbo",
        "transcribe",
        "any",
        "Whisper large-v3 Turbo speech to text",
        "Speech to text for dubbing.",
        True,
        1_500_000_000,
        "deepdml/faster-whisper-large-v3-turbo-ct2",
        "main",
    ),
    (
        "sep-tiger-dnr",
        "internal",
        "any",
        "TIGER dialogue separation",
        "Separates dialogue from noise and music.",
        True,
        600_000_000,
        "JusperLee/TIGER-DnR",
        "main",
    ),
    (
        "diar-pyannote",
        "transcribe",
        "any",
        "Speaker diarization",
        "Who speaks when (optional).",
        True,
        1_100_000_000,
        "Mitroshenkov87/voxprint-pyannote-speaker-diarization-community-1",
        "main",
    ),
    (
        "embed-pyannote",
        "transcribe",
        "any",
        "Speaker voice fingerprints",
        "Voice fingerprints for casting (optional).",
        True,
        600_000_000,
        "Mitroshenkov87/voxprint-pyannote-embedding",
        "main",
    ),
    (
        "roformer-melband",
        "internal",
        "any",
        "Mel-Band RoFormer vocal cleanup",
        "Cleans up vocals before cloning.",
        True,
        200_000_000,
        "KimberleyJSN/melbandroformer",
        "main",
    ),
    (
        "mt-en-ru",
        "translate",
        "any",
        "Translator EN → RU",
        "Helsinki-NLP opus-mt.",
        True,
        300_000_000,
        "Helsinki-NLP/opus-mt-en-ru",
        "v1",
    ),
    (
        "mt-ru-en",
        "translate",
        "any",
        "Translator RU → EN",
        "Helsinki-NLP opus-mt.",
        True,
        300_000_000,
        "Helsinki-NLP/opus-mt-ru-en",
        "v1",
    ),
    (
        "mt-en-de",
        "translate",
        "any",
        "Translator EN → DE",
        "Helsinki-NLP opus-mt.",
        True,
        600_000_000,
        "Helsinki-NLP/opus-mt-en-de",
        "v1",
    ),
    (
        "mt-de-en",
        "translate",
        "any",
        "Translator DE → EN",
        "Helsinki-NLP opus-mt.",
        True,
        600_000_000,
        "Helsinki-NLP/opus-mt-de-en",
        "v1",
    ),
    (
        "mt-ru-de",
        "translate",
        "any",
        "Translator RU → DE",
        "Helsinki-NLP opus-mt.",
        True,
        900_000_000,
        "Helsinki-NLP/opus-mt-ru-de",
        "v1",
    ),
    (
        "mt-de-ru",
        "translate",
        "any",
        "Translator DE → RU",
        "Helsinki-NLP opus-mt.",
        True,
        900_000_000,
        "Helsinki-NLP/opus-mt-de-ru",
        "v1",
    ),
)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mirror(repo: str, revision: str) -> dict[str, str]:
    return {
        "source": "huggingface",
        "repo": repo,
        "revision": revision,
        "filename": "model.bin",
    }


def _model_row(
    model_id: str,
    group: str,
    tier: str,
    label: str,
    purpose: str,
    repo: str,
    revision: str,
    *,
    optional: bool = False,
) -> dict[str, object]:
    return {
        "id": model_id,
        "version": "1",
        "vram_tier": tier,
        "group": group,
        "label": label,
        "purpose": purpose,
        "optional": optional,
        "files": [
            {
                "path": "model.bin",
                "size": 0,  # filled by _write_app
                "sha256": "0" * 64,  # filled by _write_app
                "mirrors": [_mirror(repo, revision)],
            }
        ],
    }


def _write_app(app_id: str, rows: tuple[tuple[object, ...], ...]) -> Path:
    """Write one app's mdl.json. 7-tuples carry (repo, revision) at the end."""
    folder = _SUITE_HOME / "apps" / app_id
    folder.mkdir(parents=True, exist_ok=True)
    models = []
    for row in rows:
        model_id, group, tier, label, purpose, installed, size = row[:7]
        repo, revision = row[7:9] if len(row) > 7 else (f"suite/{model_id}", "v1")
        digest = _digest(f"{app_id}:{model_id}:{group}:{tier}:{size}:{repo}:{revision}")
        entry = _model_row(
            str(model_id),
            str(group),
            str(tier),
            str(label),
            str(purpose),
            str(repo),
            str(revision),
            optional=str(model_id) == "qwen3-4b-writer",
        )
        entry["files"][0]["size"] = int(size)  # type: ignore[index]
        entry["files"][0]["sha256"] = digest  # type: ignore[index]
        models.append(entry)
        if installed:
            blob = models_dir() / "blobs" / digest
            blob.parent.mkdir(parents=True, exist_ok=True)
            with blob.open("wb") as handle:
                handle.truncate(int(size))
    manifest = folder / "mdl.json"
    manifest.write_text(
        json.dumps({"schema": 1, "app": app_id, "models": models}), encoding="utf-8"
    )
    return manifest


def _refcount(app: str, other: str, rows: tuple[tuple[object, ...], ...], *ids: str) -> None:
    """Add runtime-manifest components ref'd by both apps for ``ids``.

    No blob is written here: rows whose tuple says installed already have one,
    and a row like fairface-age must stay missing so the page shows the
    fail-closed "Required, missing" state.
    """
    for row in rows:
        model_id = str(row[0])
        if model_id not in ids:
            continue
        size = int(row[6])
        repo, revision = row[7:9] if len(row) > 7 else (f"suite/{model_id}", "v1")
        digest = _digest(f"{app}:{model_id}:{row[1]}:{row[2]}:{size}:{repo}:{revision}")
        blob = models_dir() / "blobs" / digest
        component = f"model-{digest[:12]}"
        add_ref(component, app, kind="model", version=digest, path=str(blob), sha256=digest)
        add_ref(component, other, kind="model", version=digest, path=str(blob), sha256=digest)


def _install_registry(*app_ids: str) -> None:
    state = state_dir()
    state.mkdir(parents=True, exist_ok=True)
    (state / "installed-apps.json").write_text(
        json.dumps(
            {
                "apps": [
                    {"id": app_id, "path": str(_SUITE_HOME / "apps" / app_id)} for app_id in app_ids
                ]
            }
        ),
        encoding="utf-8",
    )


# Canvas share keys: the Z-Image rows use per-variant repos so Model Forge's
# yunet is the only upstream match; everything shared otherwise is refcounted.
_CANVAS_REPOS = tuple(
    row
    + (
        (
            "Mitroshenkov87/voxprint-yunet-2023mar",
            "c7a6ffadaa0",
        )
        if row[0] == "yunet-face"
        else (f"Mitroshenkov87/voxprint-{row[0]}", "main")
    )
    for row in _CANVAS
)
_FORGE_REPOS = tuple(
    (*row, "Mitroshenkov87/voxprint-yunet-2023mar", "c7a6ffadaa0") for row in _FORGE
)


def _build_suite() -> dict[str, Path]:
    """Write the scripted suite home and return app -> manifest paths."""
    canvas = _write_app("canvas", _CANVAS_REPOS)
    _write_app("model-forge", _FORGE_REPOS)
    audiobook = _write_app("audiobook-builder", _AUDIOBOOK)
    dubber = _write_app("movie-dubber", _DUBBER)
    _install_registry("canvas", "model-forge", "audiobook-builder", "movie-dubber")
    shared_ids = (
        "qwen3-4b-writer",
        "qwen3guard-gen-0.6b",
        "siglip2-so400m-patch14-384",
        "fairface-age",
        "mivolo-v2-age",
        "nsfw-image-detection",
    )
    _refcount("canvas", "model-forge", _CANVAS_REPOS, *shared_ids)
    return {
        "canvas": canvas,
        "audiobook-builder": audiobook,
        "movie-dubber": dubber,
    }


def _paint_fixture(name: str, app_id: str, manifests: dict[str, Path]) -> None:
    controller = Controller()
    page = controller.manage_page(app_id, manifests[app_id], devices=[], lang="en")
    view = view_for(Snapshot(), mode="manage", focus_app=app_id, manage=page, lang="en")
    suffix = "-150" if os.environ.get("MDL_RENDER_SCALE") == "150" else "-100"
    path = OUT_072 / f"mdl-manage-{name}{suffix}.png"
    print(f"painting {path}", flush=True)
    grab_view(view, path, (1280, 1120))
    print(f"  {path.name} bytes={path.stat().st_size}", flush=True)
    if name == "canvas" and suffix == "-100":
        docs_path = OUT_DOCS / "mdl-manage-en.png"
        shutil.copyfile(path, docs_path)
        print(f"  refreshed {docs_path}", flush=True)


def main() -> None:
    """Write the 100% renders, then the 150% renders in a child process."""
    OUT_072.mkdir(parents=True, exist_ok=True)
    OUT_DOCS.mkdir(parents=True, exist_ok=True)
    manifests = _build_suite()
    for name, app_id in (
        ("canvas", "canvas"),
        ("audiobook", "audiobook-builder"),
        ("dubber", "movie-dubber"),
    ):
        _paint_fixture(name, app_id, manifests)
    if os.environ.get("MDL_RENDER_SCALE") != "150":
        env = os.environ.copy()
        env["MDL_RENDER_SCALE"] = "150"
        env["QT_SCALE_FACTOR"] = "1.5"
        env["QT_QPA_PLATFORM"] = "offscreen"
        subprocess.check_call([sys.executable, str(Path(__file__).resolve())], env=env)
    try:
        ART.mkdir(parents=True, exist_ok=True)
        for png in OUT_072.glob("mdl-manage-*.png"):
            shutil.copyfile(png, ART / png.name)
    except OSError as exc:
        print(f"artifacts not copied: {exc}", flush=True)


if __name__ == "__main__":
    main()
