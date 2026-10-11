"""Suite licence gate for code and weights.

A release build accepts Apache-2.0, MIT, BSD-2-Clause, BSD-3-Clause, ISC,
CC-BY-2.0, CC-BY-3.0, CC-BY-4.0, and CC0-1.0. Non-commercial licences,
copyleft licences, and a missing licence are rejected in every build.
"""

from __future__ import annotations

import re

__all__ = [
    "ALLOWED",
    "LicenceError",
    "canonical",
    "check",
    "is_allowed",
    "is_copyleft",
    "is_noncommercial",
    "is_unlicensed",
    "normalize",
]

ALLOWED = frozenset(
    {
        "mit",
        "apache-2.0",
        "bsd-2-clause",
        "bsd-3-clause",
        "cc-by-2.0",
        "cc-by-3.0",
        "cc-by-4.0",
        "cc0-1.0",
        "isc",
    }
)

_UNLICENSED = frozenset(
    {
        "",
        "unlicensed",
        "none",
        "unknown",
        "proprietary",
        "all-rights-reserved",
        "no-license",
        "missing",
        "tbd",
    }
)

_ALIASES = {
    "apache-2": "apache-2.0",
    "apache2": "apache-2.0",
    "apache2.0": "apache-2.0",
    "bsd-2": "bsd-2-clause",
    "bsd-3": "bsd-3-clause",
    "bsd": "bsd-3-clause",
    "cc-by": "cc-by-4.0",
    "cc-by-0.4": "cc-by-4.0",
    "cc0": "cc0-1.0",
}

_PRETTY = {
    "mit": "MIT",
    "apache-2.0": "Apache-2.0",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "cc-by-2.0": "CC-BY-2.0",
    "cc-by-3.0": "CC-BY-3.0",
    "cc-by-4.0": "CC-BY-4.0",
    "cc0-1.0": "CC0-1.0",
    "isc": "ISC",
}

_NC = re.compile(r"(^|[^a-z0-9])nc([^a-z0-9]|$)")
_COPYLEFT = ("lgpl", "agpl", "gpl", "cc-by-sa", "sspl", "eupl", "cddl", "osl", "mpl")


class LicenceError(ValueError):
    """The licence string is missing or not accepted."""


def normalize(value: object) -> str:
    """Fold case, separators, and known aliases.

    Args:
        value: A licence string, or any other object (treated as empty).

    Returns:
        A lowercase SPDX-like id, or an empty string.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip().lower().replace("_", "-")
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return _ALIASES.get(text, text)


def canonical(value: object) -> str:
    """Return the display form of a licence id.

    Args:
        value: A raw or already normalized licence string.

    Returns:
        The conventional SPDX spelling for an allowed id, otherwise the
        normalized text.
    """
    text = normalize(value)
    return _PRETTY.get(text, text)


def is_unlicensed(value: object) -> bool:
    """Return whether the licence is empty or explicitly unlicensed.

    Args:
        value: A raw or normalized licence string.
    """
    return normalize(value) in _UNLICENSED


def is_noncommercial(value: object) -> bool:
    """Return whether the licence is non-commercial.

    Matches CC-BY-NC and other ids with an ``nc`` token, plus the words
    non-commercial and noncommercial.

    Args:
        value: A raw or normalized licence string.
    """
    text = normalize(value)
    if not text:
        return False
    if "non-commercial" in text or "noncommercial" in text:
        return True
    return _NC.search(text) is not None


def is_copyleft(value: object) -> bool:
    """Return whether the licence is a copyleft id the suite does not ship.

    Args:
        value: A raw or normalized licence string.
    """
    text = normalize(value)
    if not text:
        return False
    return any(marker in text for marker in _COPYLEFT)


def is_allowed(value: object, *, release: bool = True) -> bool:
    """Return whether ``value`` passes the suite gate.

    Args:
        value: A licence string from a model card or a package.
        release: When true, the id must be in :data:`ALLOWED`. When false,
            non-commercial, copyleft, and missing licences are still rejected.

    Returns:
        True when :func:`check` would return a canonical id.
    """
    try:
        check(value, release=release)
    except LicenceError:
        return False
    return True


def check(value: object, *, release: bool = True) -> str:
    """Return the canonical licence id, or raise :class:`LicenceError`.

    Args:
        value: A licence string from a model card or a package.
        release: When true, the id must be in :data:`ALLOWED`.

    Returns:
        The canonical SPDX spelling.

    Raises:
        LicenceError: The licence is missing, non-commercial, copyleft, or,
            in a release build, outside the allow-list.
    """
    folded = normalize(value)
    shown = value if isinstance(value, str) else folded
    if is_unlicensed(folded):
        raise LicenceError("licence is missing or unlicensed")
    if is_noncommercial(folded):
        raise LicenceError(f"non-commercial licence {shown!r} is not accepted")
    if is_copyleft(folded):
        raise LicenceError(f"copyleft licence {shown!r} is not accepted")
    if release and folded not in ALLOWED:
        raise LicenceError(f"licence {shown!r} is not allowed in a release build")
    return canonical(folded)
