"""Shared self-test result document.

Apps keep their own checks. This module turns those checks into one
``checks.json`` shape: an ``ok`` flag and a list of ``{name, ok, detail}``.
An empty list is not a pass.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from voxprint_common.atomic import write_json

__all__ = ["Check", "check_call", "document", "write"]


@dataclass(frozen=True)
class Check:
    """One named check."""

    name: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        """Return the JSON object for this check."""
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def check_call(name: str, fn: Callable[[], None]) -> Check:
    """Run ``fn`` and turn a raised exception into a failed check.

    Args:
        name: Check name stored in the document.
        fn: Zero-argument check. Return normally to pass.

    Returns:
        A passing check, or a failing check whose detail is the exception text.
    """
    try:
        fn()
    except Exception as exc:
        return Check(name, False, str(exc))
    return Check(name, True, "")


def document(checks: Sequence[Check]) -> dict[str, object]:
    """Build the ``checks.json`` object.

    Args:
        checks: Checks in display order.

    Returns:
        ``{"ok": bool, "checks": [...]}``. ``ok`` is true only when every
        check passed and the list is not empty.
    """
    return {
        "ok": bool(checks) and all(item.ok for item in checks),
        "checks": [item.as_dict() for item in checks],
    }


def write(path: Path, checks: Sequence[Check]) -> None:
    """Atomically write ``checks.json``.

    Args:
        path: Destination, usually ``checks.json``.
        checks: Checks to record.
    """
    write_json(path, document(checks))
