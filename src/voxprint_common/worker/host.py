"""Qt-free worker host.

The UI calls :meth:`WorkerHost.poll` from a timer. :meth:`poll` does not
block. :meth:`wait` blocks and is for tests and headless callers.
"""

from __future__ import annotations

import atexit
import contextlib
import multiprocessing
import os
import pickle
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from voxprint_common.paths import state_dir
from voxprint_common.worker.context import EXIT_GPU_UNRECOVERABLE, read_checkpoint_file
from voxprint_common.worker.messages import (
    EventKind,
    Handlers,
    JobKind,
    JobOutcome,
    JobSpec,
    JobState,
    RestartPolicy,
    WorkerEvent,
)
from voxprint_common.worker.serve import serve
from voxprint_common.worker.wire import (
    Beat,
    Cancel,
    DidCancel,
    DidFail,
    DidFinish,
    Loaded,
    Noted,
    Progress,
    Shutdown,
    Submit,
    Unload,
    Unloaded,
)

__all__ = ["CheckHook", "WorkerHost"]

_HOSTS: set[WorkerHost] = set()


def _shutdown_hosts() -> None:
    for host in list(_HOSTS):
        with contextlib.suppress(Exception):
            host.shutdown(timeout=0.5)


atexit.register(_shutdown_hosts)


@dataclass
class _Slot:
    """A job the parent has accepted."""

    spec: JobSpec
    job_id: str
    attempts: int
    checkpoint: str | None
    last_beat: float
    cancel_at: float | None = None
    hang: bool = False
    started: bool = False


class WorkerHost:
    """FIFO queue of model jobs, run in one spawned child process.

    ``start`` uses the ``spawn`` start method, so the child does not inherit
    the UI process memory. Cancel asks the handler to stop, then kills the
    process if it does not. A dead process is started again after a backoff.
    An exit with code ``EXIT_GPU_UNRECOVERABLE`` that follows the job's clean
    final reply is a planned restart instead: the result stands, no crash is
    counted, and the worker starts again without backoff. Training jobs are
    queued again from the last checkpoint. Other jobs fail immediately.
    Killing or shutting down the child exits that process, which is what
    gives the GPU memory back.

    ``poll`` is the only call the UI thread should make after a job is
    submitted. It uses non-blocking pipe reads.
    """

    def __init__(
        self,
        handlers: Handlers,
        *,
        heartbeat_interval: float = 1.0,
        heartbeat_timeout: float = 30.0,
        cancel_timeout: float = 5.0,
        backoff_initial: float = 0.25,
        backoff_max: float = 8.0,
        clock: Callable[[], float] | None = None,
        state_dir: Path | None = None,
    ) -> None:
        """Create a host. The process starts on :meth:`start` or :meth:`submit`.

        Args:
            handlers: Importable callables. See :class:`Handlers`.
            heartbeat_interval: Seconds between child heartbeats. ``0`` leaves
                heartbeats to :meth:`JobContext.beat`.
            heartbeat_timeout: Seconds without a heartbeat before a job is hung.
                A job can set its own.
            cancel_timeout: Seconds to wait for a cooperative cancel before
                the process is killed.
            backoff_initial: Delay before the first restart. Later delays double.
            backoff_max: Cap on the restart delay.
            clock: Monotonic clock. Tests pass a clock they can move.
            state_dir: Directory for checkpoint notes. None uses the suite
                state directory.

        Raises:
            ValueError: A handler path or a timeout is not usable.
        """
        _check_handlers(handlers)
        if heartbeat_interval < 0 or heartbeat_timeout <= 0 or cancel_timeout < 0:
            raise ValueError("heartbeat and cancel timings must be non-negative")
        if backoff_initial < 0 or backoff_max < 0:
            raise ValueError("backoff must be non-negative")
        self._handlers = handlers
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_timeout = heartbeat_timeout
        self._cancel_timeout = cancel_timeout
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._clock = clock or time.monotonic
        self._state = state_dir if state_dir is not None else _default_state()
        self._state.mkdir(parents=True, exist_ok=True)
        self._mp = multiprocessing.get_context("spawn")
        self._pending: deque[_Slot] = deque()
        self._inflight: _Slot | None = None
        self._outcomes: dict[str, JobOutcome] = {}
        self._archive: dict[str, tuple[JobSpec, str | None]] = {}
        self._process: multiprocessing.Process | None = None
        self._cmd: object | None = None
        self._events: object | None = None
        self._cancel: object | None = None
        self._resident: str | None = None
        self._unload_sent = False
        self._restart_at: float | None = None
        self._streak = 0
        self._expect_exit = False
        self._planned_gpu_exit = False
        self._stopping = False
        self._buffer: list[WorkerEvent] = []
        _HOSTS.add(self)

    def start(self) -> None:
        """Spawn the worker if it is not already running.

        Raises:
            RuntimeError: :meth:`shutdown` has already been called.
        """
        if self._stopping:
            raise RuntimeError("Worker is shut down")
        if self._alive():
            return
        if self._process is not None:
            # The child died since the last poll. Read what it sent and run
            # crash handling before the new process takes over the queue.
            self._read(self._buffer)
            self._check_process(self._buffer)
        self._restart_at = None
        self._expect_exit = False
        self._spawn()

    def submit(self, spec: JobSpec) -> str:
        """Queue ``spec`` and return a new job id.

        Jobs run in the order they are submitted. This starts the worker
        when it is not running.

        Args:
            spec: Work to run. ``payload`` must be picklable.

        Returns:
            The job id.

        Raises:
            TypeError: ``payload`` cannot be pickled.
            ValueError: The spec is inconsistent.
            RuntimeError: The host has been shut down.
        """
        _check_spec(spec)
        try:
            pickle.dumps(spec.payload)
        except Exception as exc:
            raise TypeError(f"Job payload cannot be sent to the worker: {exc}") from exc
        self.start()
        job_id = uuid.uuid4().hex
        self._pending.append(
            _Slot(
                spec=spec,
                job_id=job_id,
                attempts=1,
                checkpoint=spec.checkpoint,
                last_beat=self._clock(),
            )
        )
        self._pump()
        return job_id

    def check(
        self,
        payload: object,
        *,
        model: str | None = None,
        phase: str | None = None,
    ) -> str:
        """Queue a check for CheckGate and return the job id.

        The check runs in the worker. This host does not inspect ``payload``.

        Args:
            payload: Picklable value the check handler receives.
            model: Model to keep loaded across checks, or None.
            phase: Batch label, or None.

        Returns:
            The job id.
        """
        return self.submit(JobSpec(kind=JobKind.CHECK, payload=payload, model=model, phase=phase))

    def cancel(self, job_id: str) -> None:
        """Ask ``job_id`` to stop.

        A job that has not started is cancelled immediately. A running job is
        asked to stop and save a checkpoint. If it is still running after the
        cancel timeout, the process is killed.

        Args:
            job_id: Id returned by :meth:`submit`.

        Raises:
            LookupError: The id is unknown or already finished.
        """
        if self._inflight is not None and self._inflight.job_id == job_id:
            self._send_cancel(job_id)
            self._inflight.cancel_at = self._clock() + self._cancel_timeout
            self._inflight.hang = False
            return
        for slot in self._pending:
            if slot.job_id == job_id:
                self._pending.remove(slot)
                self._finish(
                    slot,
                    JobState.CANCELLED,
                    message=f"Job {job_id} was cancelled.",
                    value=None,
                    events=None,
                    kind=EventKind.CANCELLED,
                )
                return
        raise LookupError(f"Unknown job {job_id}")

    def resume(self, job_id: str) -> str:
        """Queue ``job_id`` again from its last checkpoint.

        Use this after a cancel. A training crash resumes on its own.

        Args:
            job_id: A cancelled or failed job that noted a checkpoint.

        Returns:
            The same job id.

        Raises:
            LookupError: There is no checkpoint for this job.
            RuntimeError: The job is still running or already queued.
        """
        if self._inflight is not None and self._inflight.job_id == job_id:
            raise RuntimeError(f"Job {job_id} is still running")
        if any(slot.job_id == job_id for slot in self._pending):
            raise RuntimeError(f"Job {job_id} is already queued")
        stored = self._archive.get(job_id)
        if stored is None:
            raise LookupError(f"Job {job_id} has no checkpoint to resume")
        spec, checkpoint = stored
        path = checkpoint or spec.checkpoint
        if not path:
            raise LookupError(f"Job {job_id} has no checkpoint to resume")
        self._outcomes.pop(job_id, None)
        self._archive.pop(job_id, None)
        self._pending.append(
            _Slot(
                spec=replace(spec, checkpoint=path),
                job_id=job_id,
                attempts=1,
                checkpoint=path,
                last_beat=self._clock(),
            )
        )
        self.start()
        self._pump()
        return job_id

    def poll(self) -> tuple[WorkerEvent, ...]:
        """Read events without blocking.

        Returns:
            Progress, results, and crash records since the previous call.
            Heartbeats are consumed here and are not returned.
        """
        if self._stopping:
            return ()
        events = self._buffer
        self._buffer = []
        self._read(events)
        self._check_limits(events)
        self._check_process(events)
        self._maybe_restart()
        self._pump()
        return tuple(events)

    def wait(self, job_id: str, *, timeout: float = 30.0) -> JobOutcome:
        """Block until ``job_id`` has an outcome.

        Do not call this on the UI thread. The UI should use :meth:`poll`.

        Args:
            job_id: Id returned by :meth:`submit`.
            timeout: Seconds of real time to wait.

        Returns:
            The outcome.

        Raises:
            TimeoutError: The job did not finish in time.
        """
        deadline = time.monotonic() + timeout
        while True:
            self.poll()
            outcome = self._outcomes.get(job_id)
            if outcome is not None:
                return outcome
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Job {job_id} did not finish within {timeout:g}s")
            time.sleep(0.01)

    def outcome(self, job_id: str) -> JobOutcome | None:
        """Return the outcome, or None when the job is still open.

        Args:
            job_id: Id returned by :meth:`submit`.
        """
        return self._outcomes.get(job_id)

    def kill_worker(self) -> None:
        """Kill the worker process.

        The operating system reclaims it, including GPU memory. The next
        :meth:`poll` reports a crash unless this host already decided the
        in-flight job, or :meth:`shutdown` is in progress.
        """
        process = self._process
        if process is not None and process.is_alive():
            process.kill()

    def shutdown(self, *, timeout: float = 2.0) -> None:
        """Stop the worker and exit its process.

        A short wait gives the child time to unload. Then the process is
        killed. This may block for up to ``timeout`` seconds.

        Args:
            timeout: Seconds to wait before killing the process.
        """
        self._stopping = True
        self._restart_at = None
        process = self._process
        if process is not None and process.is_alive():
            self._send(Shutdown())
            process.join(timeout)
            if process.is_alive():
                process.kill()
                process.join(timeout)
        self._close_pipes()
        self._process = None
        _HOSTS.discard(self)

    @property
    def pid(self) -> int | None:
        """Return the worker process id, or None when it is not running."""
        process = self._process
        if process is None or not process.is_alive():
            return None
        return process.pid

    def _alive(self) -> bool:
        return self.pid is not None

    def _spawn(self) -> None:
        self._close_pipes()
        child_cmd, parent_cmd = self._mp.Pipe(duplex=False)
        parent_evt, child_evt = self._mp.Pipe(duplex=False)
        child_cancel, parent_cancel = self._mp.Pipe(duplex=False)
        process = self._mp.Process(
            target=serve,
            name="voxprint-worker",
            args=(
                child_cmd,
                child_evt,
                child_cancel,
                self._handlers,
                self._heartbeat_interval,
                os.getpid(),
                str(self._state),
            ),
            daemon=False,
        )
        process.start()
        child_cmd.close()
        child_evt.close()
        child_cancel.close()
        self._process = process
        self._cmd = parent_cmd
        self._events = parent_evt
        self._cancel = parent_cancel
        self._resident = None
        self._unload_sent = False
        self._restart_at = None
        self._planned_gpu_exit = False

    def _close_pipes(self) -> None:
        for conn in (self._cmd, self._events, self._cancel):
            if conn is None:
                continue
            with contextlib.suppress(OSError):
                conn.close()
        self._cmd = None
        self._events = None
        self._cancel = None

    def _send(self, message: object) -> bool:
        conn = self._cmd
        if conn is None:
            return False
        try:
            conn.send(message)  # type: ignore[attr-defined]
        except OSError, EOFError, BrokenPipeError:
            return False
        return True

    def _send_cancel(self, job_id: str) -> None:
        conn = self._cancel
        if conn is None:
            return
        try:
            conn.send(Cancel(job_id))  # type: ignore[attr-defined]
        except OSError, EOFError, BrokenPipeError:
            return

    def _pump(self) -> None:
        if self._stopping or self._inflight is not None or self._expect_exit:
            return
        if not self._alive():
            return
        if not self._pending:
            return
        slot = self._pending.popleft()
        slot.last_beat = self._clock()
        self._inflight = slot
        self._planned_gpu_exit = False
        self._send(
            Submit(
                slot.job_id,
                slot.spec.kind.value,
                slot.spec.payload,
                slot.spec.model,
                slot.spec.phase,
                slot.checkpoint,
            )
        )

    def _read(self, events: list[WorkerEvent]) -> None:
        conn = self._events
        if conn is None:
            return
        try:
            while conn.poll(0):  # type: ignore[attr-defined]
                self._apply(conn.recv(), events)  # type: ignore[attr-defined]
        except EOFError, OSError:
            return

    def _apply(self, message: object, events: list[WorkerEvent]) -> None:
        if isinstance(message, Beat):
            self._on_beat(message.job_id)
            return
        if isinstance(message, Noted):
            self._on_note(message.job_id, message.checkpoint)
            return
        if isinstance(message, Progress):
            self._on_beat(message.job_id)
            events.append(_progress_event(message))
            return
        if isinstance(message, Loaded):
            self._resident = message.model
            self._unload_sent = False
            events.append(
                WorkerEvent(kind=EventKind.LOADED, model=message.model, message=message.model)
            )
            return
        if isinstance(message, Unloaded):
            if self._resident == message.model:
                self._resident = None
            self._unload_sent = False
            events.append(
                WorkerEvent(kind=EventKind.UNLOADED, model=message.model, message=message.model)
            )
            return
        if isinstance(message, DidFail) and not message.job_id and self._inflight is not None:
            message = DidFail(self._inflight.job_id, message.message, message.checkpoint)
        if isinstance(message, DidFinish | DidFail | DidCancel):
            self._on_terminal(message, events)

    def _on_beat(self, job_id: str) -> None:
        slot = self._inflight
        if slot is None or slot.job_id != job_id:
            return
        slot.last_beat = self._clock()
        slot.started = True
        if slot.hang:
            slot.hang = False
            slot.cancel_at = None

    def _on_note(self, job_id: str, checkpoint: str) -> None:
        slot = self._inflight
        if slot is not None and slot.job_id == job_id:
            slot.checkpoint = checkpoint

    def _on_terminal(
        self, message: DidFinish | DidFail | DidCancel, events: list[WorkerEvent]
    ) -> None:
        slot = self._inflight
        if slot is None or slot.job_id != message.job_id:
            return
        if message.checkpoint:
            slot.checkpoint = message.checkpoint
        # Only a clean finish lets the next exit 75 read as a planned restart.
        self._planned_gpu_exit = isinstance(message, DidFinish)
        if isinstance(message, DidFinish):
            self._finish(
                slot,
                JobState.FINISHED,
                message="",
                value=message.value,
                events=events,
                kind=EventKind.FINISHED,
            )
        elif isinstance(message, DidFail):
            self._finish(
                slot,
                JobState.FAILED,
                message=message.message,
                value=None,
                events=events,
                kind=EventKind.FAILED,
            )
        else:
            self._finish(
                slot,
                JobState.CANCELLED,
                message=message.message,
                value=None,
                events=events,
                kind=EventKind.CANCELLED,
            )
        if self._inflight is slot:
            self._inflight = None
        self._after_job()

    def _after_job(self) -> None:
        nxt = self._pending[0].spec.model if self._pending else None
        if (
            self._resident is not None
            and nxt != self._resident
            and not self._unload_sent
            and self._send(Unload())
        ):
            self._unload_sent = True

    def _check_limits(self, events: list[WorkerEvent]) -> None:
        slot = self._inflight
        if slot is None or slot.job_id in self._outcomes:
            return
        now = self._clock()
        timeout = slot.spec.heartbeat_timeout or self._heartbeat_timeout
        # The hang clock starts at the first beat from the child. Spawning a
        # worker and importing handlers can take longer than the timeout.
        if slot.cancel_at is None and slot.started and now - slot.last_beat > timeout:
            self._send_cancel(slot.job_id)
            slot.cancel_at = now + self._cancel_timeout
            slot.hang = True
            return
        if slot.cancel_at is None or now < slot.cancel_at:
            return
        if slot.hang:
            message = (
                f"Job {slot.job_id} stopped sending heartbeats for {timeout:g}s. "
                "The worker was closed so its memory could be released."
            )
            kind = EventKind.FAILED
            state = JobState.FAILED
        else:
            message = (
                f"Job {slot.job_id} did not stop after cancel ({self._cancel_timeout:g}s). "
                "The worker was closed so its memory could be released."
            )
            kind = EventKind.CANCELLED
            state = JobState.CANCELLED
        self._finish(slot, state, message=message, value=None, events=events, kind=kind)
        events.append(_crash_event(slot, None, message))
        self._inflight = None
        self._force_restart()

    def _check_process(self, events: list[WorkerEvent]) -> None:
        process = self._process
        if process is None or process.is_alive():
            return
        code = process.exitcode
        if code == EXIT_GPU_UNRECOVERABLE:
            # The child may have sent its final reply just before exiting.
            # Drain the pipe before the exit is classified.
            self._read(events)
        self._process = None
        self._close_pipes()
        self._resident = None
        self._unload_sent = False
        if self._expect_exit:
            self._expect_exit = False
            self._schedule_restart()
            return
        if code == EXIT_GPU_UNRECOVERABLE and self._planned_exit():
            # The handler used JobContext.reply_and_exit. The job result was
            # already delivered. Restart now, with no crash event and no
            # backoff.
            self._planned_gpu_exit = False
            if not self._stopping:
                self._restart_at = self._clock()
            return
        self._unexpected(code, events)

    def _planned_exit(self) -> bool:
        """Return whether exit 75 follows a clean final reply for the job."""
        slot = self._inflight
        if slot is None:
            return self._planned_gpu_exit
        outcome = self._outcomes.get(slot.job_id)
        return outcome is not None and outcome.state is JobState.FINISHED

    def _unexpected(self, code: int | None, events: list[WorkerEvent]) -> None:
        slot = self._inflight
        self._inflight = None
        if slot is None or slot.job_id in self._outcomes:
            events.append(
                WorkerEvent(
                    kind=EventKind.CRASHED,
                    message=f"Worker crashed (exit {_exit_text(code)}). It will be started again.",
                    exit_code=code,
                )
            )
            self._schedule_restart()
            return
        checkpoint = slot.checkpoint or read_checkpoint_file(self._state, slot.job_id)
        slot.checkpoint = checkpoint
        resumes_done = slot.attempts - 1
        can_resume = slot.spec.resolved_restart() is RestartPolicy.RESUME
        if can_resume and resumes_done < slot.spec.max_resumes:
            message = _crash_text(slot, code, resumed=True, checkpoint=checkpoint)
            events.append(_crash_event(slot, code, message))
            self._pending.appendleft(
                _Slot(
                    spec=replace(slot.spec, checkpoint=checkpoint),
                    job_id=slot.job_id,
                    attempts=slot.attempts + 1,
                    checkpoint=checkpoint,
                    last_beat=self._clock(),
                )
            )
            self._schedule_restart()
            return
        message = _crash_text(slot, code, resumed=False, checkpoint=checkpoint)
        self._finish(
            slot,
            JobState.FAILED,
            message=message,
            value=None,
            events=events,
            kind=EventKind.FAILED,
        )
        events.append(_crash_event(slot, code, message))
        self._schedule_restart()

    def _force_restart(self) -> None:
        """Kill the child and start it again after the backoff."""
        self._resident = None
        if self._alive():
            self._expect_exit = True
            self.kill_worker()
            return
        self._expect_exit = False
        self._schedule_restart()

    def _schedule_restart(self) -> None:
        if self._stopping or self._restart_at is not None:
            return
        delay = min(self._backoff_max, self._backoff_initial * (2**self._streak))
        self._streak += 1
        self._restart_at = self._clock() + delay

    def _maybe_restart(self) -> None:
        if self._stopping or self._restart_at is None:
            return
        if self._alive():
            self._restart_at = None
            return
        if self._clock() < self._restart_at:
            return
        self._restart_at = None
        self._spawn()

    def _finish(
        self,
        slot: _Slot,
        state: JobState,
        *,
        message: str,
        value: object,
        events: list[WorkerEvent] | None,
        kind: EventKind,
    ) -> None:
        if slot.job_id in self._outcomes:
            return
        checkpoint = slot.checkpoint
        outcome = JobOutcome(
            slot.job_id,
            state,
            value=value,
            message=message,
            checkpoint=checkpoint,
            attempts=slot.attempts,
        )
        self._outcomes[slot.job_id] = outcome
        self._archive[slot.job_id] = (slot.spec, checkpoint)
        if state is JobState.FINISHED:
            self._streak = 0
        event = WorkerEvent(
            kind=kind,
            job_id=slot.job_id,
            message=message,
            value=value,
            checkpoint=checkpoint,
            model=slot.spec.model,
            phase=slot.spec.phase,
            attempts=slot.attempts,
        )
        if events is None:
            self._buffer.append(event)
        else:
            events.append(event)


class CheckHook:
    """The object CheckGate holds when it runs checks through a host.

    :meth:`run` only queues a check job. It does not look at the payload.
    """

    def __init__(self, host: WorkerHost) -> None:
        """Keep ``host``.

        Args:
            host: The worker that will run the check.
        """
        self._host = host

    def run(
        self,
        payload: object,
        *,
        model: str | None = None,
        phase: str | None = None,
    ) -> str:
        """Queue ``payload`` as a check and return the job id.

        Args:
            payload: Picklable value for the check handler.
            model: Model to keep loaded, or None.
            phase: Batch label, or None.

        Returns:
            The job id.
        """
        return self._host.check(payload, model=model, phase=phase)


def _default_state() -> Path:
    return state_dir() / "worker"


def _check_handlers(handlers: Handlers) -> None:
    for label, path in (
        ("run", handlers.run),
        ("load_model", handlers.load_model),
        ("unload_model", handlers.unload_model),
        ("check", handlers.check),
    ):
        if path is None:
            if label == "run":
                raise ValueError("handlers.run is required")
            continue
        module_name, separator, qual = path.partition(":")
        if not separator or not module_name or not qual:
            raise ValueError(f"Handler {label} must look like module:callable")


def _check_spec(spec: JobSpec) -> None:
    if not isinstance(spec.kind, JobKind):
        raise TypeError("kind must be a JobKind")
    if spec.restart is not None and not isinstance(spec.restart, RestartPolicy):
        raise TypeError("restart must be a RestartPolicy")
    if spec.max_resumes < 0:
        raise ValueError("max_resumes must be >= 0")
    if spec.heartbeat_timeout is not None and spec.heartbeat_timeout <= 0:
        raise ValueError("heartbeat_timeout must be positive")


def _progress_event(message: Progress) -> WorkerEvent:
    return WorkerEvent(
        kind=EventKind.PROGRESS,
        job_id=message.job_id,
        message=message.message,
        step=message.step,
        epoch=message.epoch,
        total_steps=message.total_steps,
        total_epochs=message.total_epochs,
        eta_s=message.eta_s,
        metrics=message.metrics,
        fraction=message.fraction,
        phase=message.phase,
    )


def _crash_event(slot: _Slot, code: int | None, message: str) -> WorkerEvent:
    return WorkerEvent(
        kind=EventKind.CRASHED,
        job_id=slot.job_id,
        message=message,
        checkpoint=slot.checkpoint,
        model=slot.spec.model,
        phase=slot.spec.phase,
        exit_code=code,
        attempts=slot.attempts,
    )


def _crash_text(slot: _Slot, code: int | None, *, resumed: bool, checkpoint: str | None) -> str:
    model = f" (model {slot.spec.model})" if slot.spec.model else ""
    base = (
        f"Worker crashed (exit {_exit_text(code)}) during {slot.spec.kind.value} "
        f"job {slot.job_id}{model}."
    )
    if resumed:
        where = checkpoint or "the start"
        return f"{base} Resuming from {where}."
    if slot.spec.resolved_restart() is RestartPolicy.RESUME and slot.attempts > 1:
        return f"{base} Resume stopped after {slot.attempts} attempts."
    return f"{base} The job was not resumed."


def _exit_text(code: int | None) -> str:
    if code is None:
        return "unknown"
    return str(code)
