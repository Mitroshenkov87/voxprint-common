"""Pinned snapshot of the suite model index.

``model-index.json`` is the fallback when the Hugging Face dataset
``Mitroshenkov87/voxprint-model-index`` cannot be fetched or does not match
the pin. Each entry's ``sha256`` is the bundled example descriptor in
``examples/``, not a file from the weight repo.
"""
