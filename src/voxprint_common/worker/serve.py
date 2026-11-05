"""Child process entry point.

The process is started with the spawn context. Returning from :func:`serve`
ends the process, which is what releases GPU memory. ``os._exit`` from a
handler does the same without running ``unload_model``.
"""

from __future__ import annotations

import importlib
import logging
import os
import pickle
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from voxprint_common.gpu_lock import pid_exists
from voxprint_common.worker.context import JobCancelled, JobContext
from voxprint_common.worker.messages import Handlers, JobKind
from voxprint_common.worker.wire import (
    Beat,
    Cancel,
    DidCancel,
    DidFail,
    DidFinish,
    Loaded,
    Shutdown,
    Submit,
    Unload,
    Unloaded,
)

__all__ = ["serve"]

logger = logging.getLogger(__name__)


class _Out(Protocol):
    def send(self, obj: object) -> None:
        """Send one pickled object."""


class _In(Protocol):
    def poll(self, timeout: float = 0.0) -> bool:
        """Return whether a message is waiting."""

    def recv(self) -> object:
        """Return the next message."""


def serve(
    commands: _In,
    events: _Out,
    cancels: _In,
    handlers: Handlers,
    heartbeat_interval: float,
    parent_pid: int,
    state_dir: str,
) -> None:
    """Run jobs until the host sends shutdown or the parent exits.

    Args:
        commands: Receive end of the command pipe.
        events: Send end of the event pipe.
        cancels: Receive end of the cancel pipe.
        handlers: Import paths for run, load, unload, and check.
        heartbeat_interval: Seconds between automatic heartbeats. ``0`` sends
            none, so only :meth:`JobContext.beat` keeps a job alive.
        parent_pid: Process id of the UI. When it is gone this process exits.
        state_dir: Directory for checkpoint notes.
    """
    try:
        child = _Child(
            commands,
            events,
            cancels,
            handlers,
            heartbeat_interval,
            parent_pid,
            Path(state_dir),
        )
    except Exception as exc:
        try:
            events.send(DidFail("", f"{type(exc).__name__}: {exc}", None))
        except (OSError, EOFError, BrokenPipeError):
            return
        return
    child.run()


class _Child:
    """One worker process."""

    def __init__(
        self,
        commands: _In,
        events: _Out,
        cancels: _In,
        handlers: Handlers,
        heartbeat_interval: float,
        parent_pid: int,
        state_dir: Path,
    ) -> None:
        self._commands = commands
        self._events = events
        self._cancels = cancels
        self._run = _callable(handlers.run)
        self._check = _callable(handlers.check) if handlers.check else self._run
        self._load = _callable(handlers.load_model) if handlers.load_model else None
        self._unload = _callable(handlers.unload_model) if handlers.unload_model else None
        self._interval = heartbeat_interval
        self._parent = parent_pid
        self._state = state_dir
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._current: str | None = None
        self._model_id: str | None = None
        self._model: object = None
        self._cancelled: set[str] = set()

    def run(self) -> None:
        """Read commands until shutdown."""
        self._state.mkdir(parents=True, exist_ok=True)
        if self._interval > 0:
            threading.Thread(target=self._beats, name="voxprint-worker-beat", daemon=True).start()
        threading.Thread(
            target=self._watch_parent, name="voxprint-worker-parent", daemon=True
        ).start()
        try:
            while not self._stop.is_set():
                if not self._commands.poll(0.05):
                    continue
                command = self._commands.recv()
                if isinstance(command, Shutdown):
                    break
                if isinstance(command, Unload):
                    self._drop_model()
                    continue
                if isinstance(command, Submit):
                    self._run_job(command)
                    continue
                self._emit(DidFail("", f"Unknown command {type(command).__name__}", None))
        finally:
            self._stop.set()
            self._drop_model()

    def _emit(self, message: object) -> None:
        with self._lock:
            try:
                self._events.send(message)
            except (OSError, EOFError, BrokenPipeError):
                self._stop.set()

    def _flush_events(self) -> None:
        # ``Connection.send`` already wrote the bytes, so this only covers a
        # connection object that buffers of its own. It runs before a handler
        # calls ``os._exit`` from ``JobContext.reply_and_exit``.
        flush = getattr(self._events, "flush", None)
        if callable(flush):
            flush()

    def _beats(self) -> None:
        while not self._stop.wait(self._interval):
            job_id = self._current
            if job_id:
                self._emit(Beat(job_id))

    def _watch_parent(self) -> None:
        while not self._stop.wait(0.5):
            if not pid_exists(self._parent):
                os._exit(0)

    def _poll_cancel(self) -> set[str]:
        while self._cancels.poll(0):
            message = self._cancels.recv()
            if isinstance(message, Cancel):
                self._cancelled.add(message.job_id)
        return self._cancelled

    def _drop_model(self) -> None:
        if self._model_id is None:
            return
        model_id = self._model_id
        loaded = self._model
        self._model_id = None
        self._model = None
        if self._unload is not None:
            try:
                self._unload(model_id, loaded)
            except Exception:
                logger.exception("unload %s failed", model_id)
        self._emit(Unloaded(model_id))

    def _ensure_model(self, command: Submit) -> bool:
        if command.model == self._model_id:
            return True
        self._drop_model()
        if not command.model or self._load is None:
            return True
        try:
            self._model = self._load(command.model)
        except Exception as exc:
            self._emit(DidFail(command.job_id, f"{type(exc).__name__}: {exc}", None))
            return False
        self._model_id = command.model
        self._emit(Loaded(command.model))
        return True

    def _run_job(self, command: Submit) -> None:
        self._current = command.job_id
        self._cancelled.discard(command.job_id)
        self._emit(Beat(command.job_id))
        try:
            if not self._ensure_model(command):
                return
            kind = JobKind(command.kind)
            ctx = JobContext(
                job_id=command.job_id,
                kind=kind,
                model=command.model,
                phase=command.phase,
                payload=command.payload,
                checkpoint=command.checkpoint,
                model_object=self._model if command.model else None,
                state_dir=self._state,
                emit=self._emit,
                poll_cancel=self._poll_cancel,
                flush=self._flush_events,
            )
            handler = self._check if kind is JobKind.CHECK else self._run
            try:
                value = handler(ctx)
            except JobCancelled as exc:
                checkpoint = exc.checkpoint or ctx.checkpoint_out
                self._emit(
                    DidCancel(
                        command.job_id,
                        checkpoint,
                        f"Job {command.job_id} was cancelled.",
                    )
                )
                return
            except Exception as exc:
                self._emit(
                    DidFail(
                        command.job_id,
                        f"{type(exc).__name__}: {exc}",
                        ctx.checkpoint_out,
                    )
                )
                return
            if ctx.cancelled():
                self._emit(
                    DidCancel(
                        command.job_id,
                        ctx.checkpoint_out,
                        f"Job {command.job_id} was cancelled.",
                    )
                )
                return
            try:
                pickle.dumps(value)
            except Exception as exc:
                self._emit(
                    DidFail(
                        command.job_id,
                        f"TypeError: result cannot be sent to the UI: {exc}",
                        ctx.checkpoint_out,
                    )
                )
                return
            self._emit(DidFinish(command.job_id, value, ctx.checkpoint_out))
        finally:
            self._current = None


def _callable(path: str) -> Callable[..., object]:
    module_name, separator, qual = path.partition(":")
    if not separator or not module_name or not qual:
        raise ValueError(f"Handler {path!r} must look like module:callable")
    module = importlib.import_module(module_name)
    target: object = module
    for part in qual.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise TypeError(f"Handler {path} is not callable")
    return target
