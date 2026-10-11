"""Line protocol for ``--json``.

Stdout is UTF-8. Each line is one JSON object, flushed immediately. Progress
lines come first. Exactly one result line comes last. Human text belongs on
stderr.

Exit codes:

* ``0``: success. The result line has ``ok`` true.
* ``1``: usage error. The arguments are missing or unknown.
* ``2``: the operation failed.
* ``3``: cancelled, or the wait for a shared resource such as the GPU lock expired.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator, Mapping
from typing import Any, TextIO

__all__ = [
    "EXIT_CANCELLED",
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_USAGE",
    "ProtocolError",
    "Session",
    "emit",
    "iter_lines",
    "progress",
    "result",
]

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_FAILED = 2
EXIT_CANCELLED = 3

_CODES = frozenset({EXIT_OK, EXIT_USAGE, EXIT_FAILED, EXIT_CANCELLED})
_RESERVED = frozenset({"type", "ok", "code", "message"})


class ProtocolError(ValueError):
    """A ``--json`` line is missing, duplicated, or not an object this protocol knows."""


def progress(stage: str, fraction: float | None, message: str = "") -> dict[str, Any]:
    """Build one progress line.

    Args:
        stage: Short stage id, for example ``download``.
        fraction: From 0 to 1, or None while the stage has no total yet.
        message: Short status text. May be empty.

    Returns:
        A JSON-ready object with ``type`` ``progress``.

    Raises:
        ValueError: ``fraction`` is outside 0..1.
    """
    if fraction is not None and not 0.0 <= float(fraction) <= 1.0:
        raise ValueError("fraction must be between 0 and 1, or null")
    return {
        "type": "progress",
        "stage": stage,
        "fraction": None if fraction is None else float(fraction),
        "message": message,
    }


def result(ok: bool, *, code: int, message: str = "", **extra: Any) -> dict[str, Any]:
    """Build the final result line.

    Args:
        ok: True only for exit code 0.
        code: One of 0, 1, 2, 3.
        message: Short summary. May be empty.
        **extra: More JSON fields. ``type``, ``ok``, ``code``, and ``message``
            are reserved.

    Returns:
        A JSON-ready object with ``type`` ``result``.

    Raises:
        ValueError: The code disagrees with ``ok``, or a reserved field was passed.
    """
    if code not in _CODES:
        raise ValueError("code must be 0, 1, 2, or 3")
    if ok and code != EXIT_OK:
        raise ValueError("a successful result uses exit code 0")
    if not ok and code == EXIT_OK:
        raise ValueError("a failed result cannot use exit code 0")
    overlap = _RESERVED.intersection(extra)
    if overlap:
        raise ValueError(f"reserved result fields: {sorted(overlap)}")
    body: dict[str, Any] = {"type": "result", "ok": ok, "code": code, "message": message}
    body.update(extra)
    return body


def emit(payload: Mapping[str, Any], stream: TextIO | None = None) -> None:
    """Write one JSON object as a single line and flush it.

    Args:
        payload: Progress or result object.
        stream: Destination. Defaults to stdout.
    """
    out = sys.stdout if stream is None else stream
    out.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    out.flush()


def iter_lines(stream: TextIO) -> Iterator[dict[str, Any]]:
    """Yield JSON objects from a ``--json`` stream.

    Blank lines are skipped. The caller checks that the last object is the
    result.

    Args:
        stream: Text such as an open stdout pipe.

    Yields:
        One object per non-blank line.

    Raises:
        ProtocolError: A line is not a progress or result object.
        json.JSONDecodeError: A line is not JSON.
    """
    for line in stream:
        text = line.strip()
        if not text:
            continue
        payload = json.loads(text)
        if not isinstance(payload, dict) or payload.get("type") not in ("progress", "result"):
            raise ProtocolError("each line must be a progress or result object")
        yield payload


class Session:
    """Writer for one ``--json`` invocation: progress lines, then one result."""

    def __init__(self, stream: TextIO | None = None) -> None:
        """Bind the stream that receives the lines.

        Args:
            stream: Destination. Defaults to stdout for each write.
        """
        self.stream = stream
        self.finished = False

    def progress(self, stage: str, fraction: float | None, message: str = "") -> None:
        """Emit one progress line.

        Args:
            stage: Short stage id.
            fraction: From 0 to 1, or None.
            message: Short status text.

        Raises:
            ProtocolError: The result line was already written.
        """
        if self.finished:
            raise ProtocolError("progress after the result line")
        emit(progress(stage, fraction, message), self.stream)

    def finish(
        self,
        ok: bool,
        *,
        code: int | None = None,
        message: str = "",
        **extra: Any,
    ) -> int:
        """Emit the result line and return the exit code the process should use.

        Args:
            ok: True only for a successful run.
            code: Exit code. Defaults to 0 when ``ok`` is true and 2 otherwise.
            message: Short summary.
            **extra: More JSON fields on the result line.

        Returns:
            The exit code written into the result line.

        Raises:
            ProtocolError: A result line was already written.
        """
        if self.finished:
            raise ProtocolError("result line already written")
        chosen = (EXIT_OK if ok else EXIT_FAILED) if code is None else code
        emit(result(ok, code=chosen, message=message, **extra), self.stream)
        self.finished = True
        return chosen
