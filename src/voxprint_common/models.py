"""Shared base-model registry, quant selection, and LoRA base tags.

Three model ids back the suite's image generation and LoRA training:
Z-Image-Turbo (``z-image-turbo``, the default generation model, Apache-2.0),
full Z-Image (``z-image``, an optional download and the LoRA training base),
and MOSS-Image (``moss``, an optional download, Apache-2.0). Each model ships
in one or more weight quants (``bf16``, ``fp8``, ``int8``, ``nf4``);
:func:`select_quant` picks the best-quality quant whose estimated peak VRAM
fits the memory budget, for generation with a LoRA loaded or for training.

The peak figures in :data:`MODEL_PEAK_GB` are PLACEHOLDER estimates pending
calibration on the PG test VM. The VM calibration cap equals
:data:`MEMORY_BUDGET_GB`. Official Z-Image-Turbo transformer shards total
about 24.6 GB, so the copied Z-Image placeholders may understate Turbo's
bf16 peak.

The stored choice lives in the suite settings ``model.default``
(``z-image-turbo``) and ``model.quant`` (``auto`` calls :func:`select_quant`).
``z-image`` remains a distinct setting value for the full model; it is not
an alias of Turbo. :func:`normalize_base` still maps an untagged LoRA, and
the ``z-image-turbo`` tag, to the Z-Image family (``z-image``), because the
training base is full Z-Image (moss-final-spec item 4). This module does
not download anything, does not touch the network, and does not import torch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "MEMORY_BUDGET_GB",
    "MODELS",
    "MODEL_PEAK_GB",
    "QUANT_ORDER",
    "ModelSpec",
    "default_model",
    "list_models",
    "lora_base_matches",
    "normalize_base",
    "select_quant",
]

#: Shared peak-VRAM budget in GB. Generation with a LoRA loaded, and training,
#: must peak at most this, so a 16 GB card keeps headroom.
MEMORY_BUDGET_GB = 14.0

#: Quants from best quality to smallest. ``select_quant`` walks this order.
QUANT_ORDER = ("bf16", "fp8", "int8", "nf4")

#: Estimated peak VRAM in GB, per model, quant, and mode. PLACEHOLDERS pending
#: calibration on the PG test VM (``generate`` is generation with a LoRA
#: loaded; ``train`` is LoRA training).
MODEL_PEAK_GB: dict[str, dict[str, dict[str, float]]] = {
    "moss": {
        "generate": {"bf16": 30.0, "fp8": 13.5, "int8": 13.0, "nf4": 8.0},
        "train": {"bf16": 52.0, "fp8": 24.0, "int8": 22.0, "nf4": 13.0},
    },
    "z-image": {
        "generate": {"bf16": 28.0, "fp8": 13.0, "int8": 12.5, "nf4": 7.5},
        "train": {"bf16": 48.0, "fp8": 22.0, "int8": 20.0, "nf4": 12.0},
    },
    # Placeholders, copied from z-image pending VM calibration.
    "z-image-turbo": {
        "generate": {"bf16": 28.0, "fp8": 13.0, "int8": 12.5, "nf4": 7.5},
        "train": {"bf16": 48.0, "fp8": 22.0, "int8": 20.0, "nf4": 12.0},
    },
}


@dataclass(frozen=True)
class ModelSpec:
    """One base model the suite can generate and train on."""

    id: str
    title: str
    licence: str | None
    default: bool
    summary: str


MODELS = (
    ModelSpec(
        id="z-image-turbo",
        title="Z-Image-Turbo",
        licence="Apache-2.0",
        default=True,
        summary="The default model for generation (distilled Turbo).",
    ),
    ModelSpec(
        id="z-image",
        title="Z-Image",
        licence=None,
        default=False,
        summary="Optional download; the LoRA training base for the Z-Image family.",
    ),
    ModelSpec(
        id="moss",
        title="MOSS-Image",
        licence="Apache-2.0",
        default=False,
        summary="Optional download, not the default model.",
    ),
)

# Setting / registry ids that belong to the Z-Image LoRA family.
# Training base is full Z-Image; Turbo is a distilled sibling.
_FAMILY = {
    "z-image": "z-image",
    "z-image-turbo": "z-image",
    "moss": "moss",
}

_MODES = ("generate", "train")


def list_models() -> tuple[ModelSpec, ...]:
    """Return every known base model, the default first."""
    return MODELS


def default_model() -> str:
    """Return the shared generation model id: ``z-image-turbo``."""
    return "z-image-turbo"


def select_quant(
    model: str,
    budget_gb: float = MEMORY_BUDGET_GB,
    mode: str = "generate",
) -> str:
    """Pick the best-quality quant of ``model`` that peaks within the budget.

    Quants are tried in quality order (``bf16`` > ``fp8`` > ``int8`` > ``nf4``)
    against the estimated peak for ``mode``; the first fit wins.

    Args:
        model: A base model id from :func:`list_models`.
        budget_gb: Peak-VRAM budget in GB. The default is
            :data:`MEMORY_BUDGET_GB`.
        mode: ``generate`` (with a LoRA loaded) or ``train``.

    Returns:
        The chosen quant name.

    Raises:
        ValueError: The model or mode is unknown, or no quant fits the budget.
    """
    if model not in MODEL_PEAK_GB:
        raise ValueError(f"unknown model {model!r}; expected one of {sorted(MODEL_PEAK_GB)}")
    if mode not in _MODES:
        raise ValueError(f"mode must be 'generate' or 'train', got {mode!r}")
    peaks = MODEL_PEAK_GB[model][mode]
    for quant in QUANT_ORDER:
        if peaks[quant] <= budget_gb:
            return quant
    raise ValueError(f"no {model} quant fits a {budget_gb} GB peak budget in {mode} mode")


def normalize_base(tag: Any) -> str:
    """Normalize a LoRA base tag to a base model id.

    None, empty, or untagged tags are legacy LoRAs and read as ``z-image``.
    ``z-image-turbo`` also maps to ``z-image`` (same family; the training
    base is full Z-Image). ``moss`` and ``z-image`` pass through.

    Args:
        tag: Stored base tag, or None.

    Returns:
        ``moss`` or ``z-image``.

    Raises:
        ValueError: ``tag`` names no known base.
    """
    # By design (moss-final-spec item 4): untagged legacy LoRAs, including
    # Garnis, read as "z-image". That is the Z-Image family / training base,
    # not a rename of the suite default (which is now z-image-turbo).
    if tag is None:
        return "z-image"
    if not isinstance(tag, str):
        raise ValueError(f"base tag must be a string or null, got {tag!r}")
    text = tag.strip().lower()
    if text in ("", "z-image", "z-image-turbo"):
        return "z-image"
    if text == "moss":
        return "moss"
    raise ValueError(f"unknown base tag {tag!r}; expected 'moss', 'z-image', or 'z-image-turbo'")


def lora_base_matches(lora_base: Any, model: str) -> bool:
    """Return whether a LoRA tagged ``lora_base`` belongs on ``model``.

    Untagged (legacy) LoRAs are treated as Z-Image-family LoRAs, by design
    (moss-final-spec item 4). A LoRA tagged ``z-image`` or ``z-image-turbo``
    matches either member of that family.

    Args:
        lora_base: The LoRA's base tag, or None when untagged.
        model: The base model id to match against.

    Raises:
        ValueError: ``model`` is not a known base model id.
    """
    if model not in MODEL_PEAK_GB:
        raise ValueError(f"unknown model {model!r}; expected one of {sorted(MODEL_PEAK_GB)}")
    return normalize_base(lora_base) == _FAMILY[model]
