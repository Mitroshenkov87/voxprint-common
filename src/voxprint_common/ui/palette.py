"""Fuzzy scoring for the command palette.

The scorer is translated from PhotoCraft ``crates/ui-egui/src/palette.rs``
``fuzzy_score`` and the exact-match bonus, revision
``5f65579ecb44047c2a619036c18ff273202e1389``
(https://github.com/storytold/photocraft), MIT OR Apache-2.0.
Copyright (c) 2026 ArtCraft Team and the PhotoCraft contributors.
VOXPrint distributes this translation under the Apache License, Version 2.0.

The popup itself is Qt and lives in :mod:`voxprint_common.ui.shell`.
This module does not import Qt.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["exact_bonus", "fuzzy_score", "score_text"]


def fuzzy_score(query: str, text: str) -> int | None:
    """Score a case-insensitive subsequence match.

    Consecutive letters and letters at the start of a word score higher.
    None means ``query`` is not a subsequence of ``text``. An empty query
    scores 0, so every row stays listed.

    Args:
        query: Text typed in the palette.
        text: Title, id, or other line being searched.
    """
    if query == "":
        return 0
    letters = list(text.lower())
    score = 0
    index = 0
    last: int | None = None
    for char in query.lower():
        found = False
        while index < len(letters):
            if letters[index] == char:
                score += 8 if last == index - 1 else 1
                if index == 0 or letters[index - 1] == " ":
                    score += 5
                last = index
                index += 1
                found = True
                break
            index += 1
        if not found:
            return None
    return score - (len(letters) // 8)


def exact_bonus(query: str, names: Sequence[str]) -> int:
    """Return 1000 when ``query`` equals one of ``names``, ignoring case.

    A trailing ellipsis on a name is ignored. Any other query returns 0.

    Args:
        query: Text typed in the palette.
        names: Titles and ids for one command.
    """
    wanted = query.strip()
    if not wanted:
        return 0
    for name in names:
        if name.rstrip("…").strip().lower() == wanted.lower():
            return 1000
    return 0


def score_text(query: str, *names: str) -> int | None:
    """Score ``query`` against ``names`` joined for the fuzzy pass.

    The exact-match bonus is added when the query equals one name.

    Args:
        query: Text typed in the palette.
        names: Title, command id, and any extra search text.
    """
    base = fuzzy_score(query, " ".join(names))
    if base is None:
        return None
    return base + exact_bonus(query, names)
