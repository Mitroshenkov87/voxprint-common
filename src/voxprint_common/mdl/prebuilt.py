"""Decide whether a pre-built artifact matches the installed onnxruntime.

The app supplies a probe with :func:`set_onnxruntime_probe`. The probe returns
the installed onnxruntime version and the highest ONNX opset it runs, or None
when onnxruntime is not available. MDL never imports onnxruntime itself.

A stale artifact is not downloaded. The source files and ``post_install``
are used instead, and the reason is logged and shown by ``--check``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

__all__ = [
    "OnnxProbe",
    "clear_stale",
    "note_stale",
    "set_onnxruntime_probe",
    "stale_reason",
    "stale_rows",
    "version_matches",
]

logger = logging.getLogger(__name__)

OnnxProbe = Callable[[], "tuple[str, int] | None"]

_probe: OnnxProbe | None = None
_stale: dict[tuple[str, str, str], str] = {}
_CLAUSE = re.compile(r"^(~=|==|!=|>=|<=|>|<)?\s*([0-9]+(?:\.[0-9]+)*)(\.\*)?$")


def set_onnxruntime_probe(probe: OnnxProbe | None) -> None:
    """Install the app hook that reports onnxruntime's version and opset.

    Args:
        probe: Returns ``(version, max_opset)``, or None when onnxruntime is
            missing. None removes the hook, and every artifact is then stale.
    """
    global _probe
    _probe = probe


def _parts(text: str) -> tuple[int, ...]:
    found = re.match(r"^[0-9]+(?:\.[0-9]+)*", text.strip())
    if found is None:
        raise ValueError(f"not a version: {text!r}")
    return tuple(int(item) for item in found.group(0).split("."))


def _pad(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)), right + (0,) * (width - len(right))


def version_matches(version: str, spec: str) -> bool:
    """Return whether ``version`` satisfies a comma-separated spec.

    Supports ``==``, ``!=``, ``>=``, ``<=``, ``>``, ``<``, ``~=``, and a
    ``.*`` suffix on ``==`` and ``!=``. A bare version means ``==``.

    Args:
        version: Installed version, such as ``1.20.1``.
        spec: Requirement, such as ``>=1.20,<2``.

    Raises:
        ValueError: The spec or the version cannot be read.
    """
    have = _parts(version)
    for raw in spec.split(","):
        clause = _CLAUSE.fullmatch(raw.strip())
        if clause is None:
            raise ValueError(f"not a version spec: {raw!r}")
        op = clause.group(1) or "=="
        want = tuple(int(item) for item in clause.group(2).split("."))
        if clause.group(3):
            prefix = have[: len(want)] == want
            if (op == "==") != prefix or op not in {"==", "!="}:
                return False
            continue
        left, right = _pad(have, want)
        if op == "~=":
            ok = left >= right and have[: len(want) - 1] == want[:-1]
        else:
            ok = {
                "==": left == right,
                "!=": left != right,
                ">=": left >= right,
                "<=": left <= right,
                ">": left > right,
                "<": left < right,
            }[op]
        if not ok:
            return False
    return True


def stale_reason(spec: str, opset: int) -> str:
    """Return why an artifact built for ``spec`` and ``opset`` is stale, or ``""``.

    Args:
        spec: ``built_for.onnxruntime``.
        opset: ``built_for.opset``.
    """
    if _probe is None:
        return "the app did not report an onnxruntime version"
    try:
        found = _probe()
    except Exception as exc:
        return f"the onnxruntime probe failed: {exc}"
    if found is None:
        return "onnxruntime is not installed"
    version, max_opset = found
    try:
        if not version_matches(version, spec):
            return f"built for onnxruntime {spec}, installed {version}"
    except ValueError as exc:
        return str(exc)
    if opset > max_opset:
        return f"built for opset {opset}, onnxruntime {version} runs up to {max_opset}"
    return ""


def note_stale(app: str, model_id: str, path: str, reason: str) -> None:
    """Remember and log a stale artifact for ``--check``.

    Args:
        app: Suite app id.
        model_id: Model id.
        path: Artifact path.
        reason: Why it is stale.
    """
    key = (app, model_id, path)
    if _stale.get(key) != reason:
        logger.warning("prebuilt %s for %s/%s is stale: %s", path, app, model_id, reason)
    _stale[key] = reason


def stale_rows() -> list[str]:
    """Return ``app/model path: reason`` for each stale artifact seen by planning."""
    return [f"{app}/{model} {path}: {why}" for (app, model, path), why in sorted(_stale.items())]


def clear_stale() -> None:
    """Forget stale artifacts recorded so far."""
    _stale.clear()
