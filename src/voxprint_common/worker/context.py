"""The object a handler receives inside the worker process."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from voxprint_common.atomic import write_json
from voxprint_common.worker.messages import JobKind
from voxprint_common.worker.wire import Beat, DidFinish, Noted, Progress

__all__ = ["EXIT_GPU_UNRECOVERABLE", "JobCancelled", "JobContext"]

EXIT_GPU_UNRECOVERABLE = 75


class JobCancelled(Exception):
    """The handler stopped because the job was cancelled.

    Args:
        checkpoint: Path the handler already wrote, if it saved one.
    """

    def __init__(self, checkpoint: str | None = None) -> None:
        """Record the checkpoint path, if the handler saved one.

        Args:
            checkpoint: Path already written, or None.
        """
        self.checkpoint = checkpoint
        super().__init__("cancelled")


class JobContext:
    """The running job, as the handler sees it.

    ``model_object`` is whatever ``load_model`` returned for this job's model,
    or None when the job names no model. ``checkpoint`` is the path the host
    was asked to resume from. It is None on the first attempt.

    Call :meth:`cancelled` often during a long job. Call
    :meth:`note_checkpoint` after the checkpoint file is on disk. Call
    :meth:`beat` during a long step that still runs Python. The child also
    sends heartbeats on its own while the interpreter can schedule a thread.
    Call :meth:`reply_and_exit` when the job already has its result but the
    GPU must be handed back at once.
    """

    def __init__(
        self,
        *,
        job_id: str,
        kind: JobKind,
        model: str | None,
        phase: str | None,
        payload: object,
        checkpoint: str | None,
        model_object: object,
        state_dir: Path,
        emit: Callable[[object], None],
        poll_cancel: Callable[[], set[str]],
        flush: Callable[[], None] | None = None,
    ) -> None:
        """Record the job the child is running.

        Args:
            job_id: Id the host assigned.
            kind: Inference, training, caption, or check.
            model: Model id to keep loaded, or None.
            phase: Batch label, or None.
            payload: Pickled job body.
            checkpoint: Resume path, or None on the first attempt.
            model_object: Value returned by the load handler.
            state_dir: Directory for the checkpoint note.
            emit: Sends one wire message to the host.
            poll_cancel: Returns the set of job ids the host has cancelled.
            flush: Pushes buffered pipe bytes out. Used by
                :meth:`reply_and_exit` before the process leaves.
        """
        self.job_id = job_id
        self.kind = kind
        self.model = model
        self.phase = phase
        self.payload = payload
        self.checkpoint = checkpoint
        self.model_object = model_object
        self._state_dir = state_dir
        self._emit = emit
        self._poll_cancel = poll_cancel
        self._flush = flush
        self._checkpoint_out: str | None = None
        self._saw_cancel = False

    def progress(
        self,
        *,
        message: str = "",
        step: int | None = None,
        epoch: int | None = None,
        total_steps: int | None = None,
        total_epochs: int | None = None,
        eta_s: float | None = None,
        metrics: Mapping[str, float] | None = None,
        fraction: float | None = None,
    ) -> None:
        """Report step, epoch, ETA, and metrics.

        Args:
            message: Short status. The app translates it before this call.
            step: Step inside the epoch, starting at 1 when the handler counts.
            epoch: Epoch, starting at 1 when the handler counts.
            total_steps: Steps in the epoch, when known.
            total_epochs: Epochs in the run, when known.
            eta_s: Seconds the handler expects the job to have left.
            metrics: Numbers such as loss. Booleans are rejected.
            fraction: Portion done, from 0 to 1, when the handler computes one.

        Raises:
            TypeError: A metric is not a number.
        """
        self._emit(
            Progress(
                self.job_id,
                message,
                step,
                epoch,
                total_steps,
                total_epochs,
                None if eta_s is None else float(eta_s),
                _metrics(metrics),
                None if fraction is None else float(fraction),
                self.phase,
            )
        )
        self.beat()

    def beat(self) -> None:
        """Tell the host this process is still inside the job."""
        self._emit(Beat(self.job_id))

    def cancelled(self) -> bool:
        """Return whether the host has asked this job to stop.

        Returns:
            True after :meth:`voxprint_common.worker.WorkerHost.cancel`.
        """
        if self.job_id in self._poll_cancel():
            self._saw_cancel = True
        return self._saw_cancel

    def note_checkpoint(self, path: str) -> None:
        """Remember a checkpoint that is already written.

        The path is stored beside the suite state so a killed process can
        still be resumed. Call this after the file is flushed.

        Args:
            path: Checkpoint file the handler just wrote.
        """
        self._checkpoint_out = str(path)
        destination = self._state_dir / f"{self.job_id}.checkpoint.json"
        write_json(destination, {"checkpoint": self._checkpoint_out})
        self._emit(Noted(self.job_id, self._checkpoint_out))

    def reply_and_exit(self, result: object, code: int = EXIT_GPU_UNRECOVERABLE) -> None:
        """Send the final reply, then leave the process without any cleanup.

        The reply is the same ``DidFinish`` message a normal return sends, so
        the host delivers ``result`` as a finished job. Use this when the GPU
        is in a state the process cannot recover from, but the job itself
        already produced its result: the host restarts the worker without
        counting a crash or backing off. The process then exits with ``code``
        immediately. ``unload_model`` does not run, which is the point: the
        operating system reclaims the CUDA context when the process ends.

        Args:
            result: Picklable value the host records as the job result.
            code: Process exit code. The default
                :data:`EXIT_GPU_UNRECOVERABLE` is what the host expects after
                a clean final reply. Any other code is read the same way.
        """
        self._emit(DidFinish(self.job_id, result, self._checkpoint_out))
        if self._flush is not None:
            self._flush()
        os._exit(code)

    @property
    def checkpoint_out(self) -> str | None:
        """Return the path passed to :meth:`note_checkpoint`, if any."""
        return self._checkpoint_out


def _metrics(values: Mapping[str, float] | None) -> tuple[tuple[str, float], ...]:
    if not values:
        return ()
    items: list[tuple[str, float]] = []
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError(f"metric {key} must be a number")
        number = float(value)
        if number != number or number in {float("inf"), float("-inf")}:
            raise TypeError(f"metric {key} must be finite")
        items.append((str(key), number))
    return tuple(items)


def read_checkpoint_file(state_dir: Path, job_id: str) -> str | None:
    """Return a checkpoint path the child wrote, or None when it did not.

    Args:
        state_dir: Directory the host handed to the child.
        job_id: Job whose note file to read.
    """
    path = state_dir / f"{job_id}.checkpoint.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    checkpoint = payload.get("checkpoint")
    if isinstance(checkpoint, str) and checkpoint:
        return checkpoint
    return None
