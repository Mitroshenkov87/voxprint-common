"""Which recorded fallback a model uses instead of its default files.

The choice is stored in ``<home>/state/mdl-variants.json`` as
``{"<model id>": "<fallback id>"}``. An app or the user sets it with
:func:`choose_variant`. The downloader sets it when the default source of a
replaced file fails, so later plans, ready checks, and verify use the same
files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from voxprint_common.paths import state_dir

__all__ = ["choose_variant", "chosen_variants"]


def _path() -> Path:
    return state_dir() / "mdl-variants.json"


def chosen_variants() -> dict[str, str]:
    """Return ``{model id: fallback id}`` for models that use a fallback."""
    try:
        payload = json.loads(_path().read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if isinstance(value, str)}


def choose_variant(model_id: str, variant_id: str | None) -> None:
    """Use fallback ``variant_id`` for ``model_id``, or the default when None.

    Args:
        model_id: Model id from the index.
        variant_id: A ``prebuilt`` entry id with role ``fallback``, or None.
    """
    rows = chosen_variants()
    if variant_id:
        rows[model_id] = variant_id
    else:
        rows.pop(model_id, None)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)
