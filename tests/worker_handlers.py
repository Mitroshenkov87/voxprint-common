"""Handlers the worker tests load inside the child process.

Spawn imports this module again. A value set on it in the parent is not
visible here unless the child was forked, which the host does not do.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from voxprint_common.worker import JobCancelled, JobContext

MARKER = "child-import"


def run(ctx: JobContext) -> object:
    """Dispatch a small payload used by the host tests.

    Args:
        ctx: The running job.

    Returns:
        A picklable result.
    """
    payload = ctx.payload if isinstance(ctx.payload, dict) else {"op": "echo", "value": ctx.payload}
    op = str(payload.get("op", "echo"))
    if op == "echo":
        loaded = ctx.model_object if isinstance(ctx.model_object, dict) else {}
        return {
            "value": payload.get("value"),
            "model": ctx.model,
            "loaded": loaded.get("id"),
            "checkpoint": ctx.checkpoint,
            "phase": ctx.phase,
            "kind": ctx.kind.value,
            "pid": os.getpid(),
            "marker": MARKER,
        }
    if op == "sleep":
        time.sleep(float(payload.get("seconds", 0)))
        return {"value": payload.get("value"), "pid": os.getpid()}
    if op == "progress":
        ctx.progress(
            message="step",
            step=2,
            epoch=1,
            total_steps=10,
            total_epochs=3,
            eta_s=12.5,
            metrics={"loss": 0.25},
            fraction=0.2,
        )
        return {"ok": True}
    if op == "crash":
        if ctx.checkpoint:
            return {"resumed": ctx.checkpoint}
        path = payload.get("checkpoint")
        if isinstance(path, str) and path:
            destination = Path(path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("weights", encoding="utf-8")
            ctx.note_checkpoint(path)
        os._exit(int(payload.get("code", 9)))
    if op == "cancel-me":
        if ctx.checkpoint:
            return {"checkpoint": ctx.checkpoint}
        while not ctx.cancelled():
            time.sleep(0.01)
        path = str(payload["checkpoint"])
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("saved", encoding="utf-8")
        ctx.note_checkpoint(path)
        raise JobCancelled(path)
    if op == "crash-always":
        os._exit(int(payload.get("code", 9)))
    if op == "ignore-cancel" or op == "block":
        time.sleep(float(payload.get("seconds", 30)))
        return {"done": True}
    raise ValueError(f"unknown op {op}")


def load_model(model_id: str) -> dict[str, str]:
    """Return a stand-in for a loaded model.

    Args:
        model_id: Model the job asked for.
    """
    return {"id": model_id}


def unload_model(model_id: str, loaded: object) -> None:
    """Drop a stand-in model.

    Args:
        model_id: Model id the host believes is resident.
        loaded: Value :func:`load_model` returned.

    Raises:
        RuntimeError: ``loaded`` is not that model.
    """
    if not isinstance(loaded, dict) or loaded.get("id") != model_id:
        raise RuntimeError(f"unload mismatch for {model_id}")


def check(ctx: JobContext) -> dict[str, object]:
    """Return the check payload and this process id.

    A payload with ``op`` ``crash`` uses :func:`run` so a check can fail fast
    in tests. The library does not inspect a real check payload.

    Args:
        ctx: Check job.
    """
    if isinstance(ctx.payload, dict) and ctx.payload.get("op") in {"crash", "crash-always"}:
        result = run(ctx)
        return result if isinstance(result, dict) else {"checked": result}
    return {"checked": ctx.payload, "pid": os.getpid(), "model": ctx.model}
