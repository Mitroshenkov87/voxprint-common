"""Adult-content prompt policy, and the reserved minors-gate keys.

Adult content is a prompt policy. On allows adult content and adds nothing.
Off adds a general-audience template and negatives.

The minors gate is not a setting. Keys under ``safety``, and keys whose
segments are minor, age, or guard, are rejected. :class:`SafetyGate` reviews
a prompt and a finished picture. It has no switch. The picture is a path or
bytes with a mime type. A missing picture is blocked before the gate runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

__all__ = [
    "AdultPromptPolicy",
    "ReviewedOutput",
    "SafetyGate",
    "adult_prompt_policy",
    "reject_reserved_key",
]

_TOKEN = re.compile(r"(?i)(?:^|[._-])(?:safety|minors?|ages?|guards?)(?:$|[._-])")
_CONTAINED = re.compile(r"(?i)minor|guard")
_SAFE_TEMPLATE = "Suitable for a general audience."
_SAFE_NEGATIVES = "adult content"


@dataclass(frozen=True)
class ReviewedOutput:
    """The picture :meth:`SafetyGate.review_result` checks.

    Pass ``path`` or both ``data`` and ``mime``. The layout blocks when
    neither is present. This type does not decide whether the picture is allowed.
    """

    path: Path | None = None
    data: bytes | None = None
    mime: str = ""

    def present(self) -> bool:
        """Return whether a path or a byte payload was supplied."""
        if self.path is not None and str(self.path).strip():
            return True
        return bool(self.data) and bool(self.mime.strip())


class SafetyGate(Protocol):
    """App review of a prompt and of a finished picture.

    The layout calls these two methods. A returned reason, or a raised
    exception, blocks the picture. There is no method that turns the review off.
    """

    def review_prompt(self, prompt: str) -> str | None:
        """Review ``prompt`` before a picture starts.

        Args:
            prompt: The words that would be used.

        Returns:
            A plain refusal, or None when the picture may start.
        """

    def review_result(self, prompt: str, item_id: str, output: ReviewedOutput) -> str | None:
        """Review a finished picture before it is kept.

        Args:
            prompt: The words that produced the picture.
            item_id: The id the app assigned to the picture.
            output: The picture, as a path or as bytes and a mime type.

        Returns:
            A plain refusal, or None when the picture may be kept.
        """


@dataclass(frozen=True)
class AdultPromptPolicy:
    """Extra words for a picture request.

    Empty strings are the neutral policy used when adult content is allowed.
    """

    template: str
    negatives: str


def adult_prompt_policy(*, allowed: bool) -> AdultPromptPolicy:
    """Return the prompt policy for the adult-content setting.

    Args:
        allowed: True when the adult-content setting is on.

    Returns:
        A neutral policy when ``allowed`` is true. Otherwise a general-audience
        template and negatives.
    """
    if allowed:
        return AdultPromptPolicy("", "")
    return AdultPromptPolicy(_SAFE_TEMPLATE, _SAFE_NEGATIVES)


def reject_reserved_key(key: str) -> None:
    """Reject a key that would configure the minors gate.

    A key is reserved when a ``.``, ``_``, or ``-`` segment is ``safety``,
    ``minor``, ``minors``, ``age``, ``ages``, ``guard``, or ``guards``, or when
    the key contains ``minor`` or ``guard``. ``image.adult_content`` is not reserved.

    Args:
        key: Setting id or preset value key.

    Raises:
        ValueError: ``key`` is reserved.
    """
    if _TOKEN.search(key) is not None or _CONTAINED.search(key) is not None:
        raise ValueError(f"reserved key {key!r} is outside settings and presets")
