"""Progress events a download window can subscribe to.

One :class:`TransferSession` belongs to the worker. ``pause``, ``resume``, and
``cancel`` are the control hooks. Each :class:`ProgressEvent` lists the files
in flight and every queued job, so the window can group jobs by app.

Another process watches the same work by polling the status file. The queue
snapshot on that file matches :attr:`ProgressEvent.jobs`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = [
    "ItemProgress",
    "JobProgress",
    "ProgressEvent",
    "TransferCancelled",
    "TransferSession",
]

Listener = Callable[["ProgressEvent"], None]


class TransferCancelled(Exception):
    """The session's cancel hook was used."""


@dataclass(frozen=True)
class ItemProgress:
    """One file the worker is fetching or has finished."""

    id: str
    display_name: str
    bytes_done: int
    bytes_total: int
    speed_bps: float
    eta_s: float | None
    source: str
    state: str
    apps: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class JobProgress:
    """One queued request. ``models`` is None when the job asked for the tier."""

    id: str
    app: str
    models: tuple[str, ...] | None
    state: str


@dataclass(frozen=True)
class ProgressEvent:
    """A snapshot of files and the whole queue."""

    items: tuple[ItemProgress, ...]
    jobs: tuple[JobProgress, ...]
    bytes_done: int
    bytes_total: int
    state: str

    def jobs_by_app(self) -> dict[str, tuple[JobProgress, ...]]:
        """Group queued jobs by app id, preserving queue order inside each app."""
        grouped: dict[str, list[JobProgress]] = {}
        for job in self.jobs:
            grouped.setdefault(job.app, []).append(job)
        return {app: tuple(rows) for app, rows in grouped.items()}


class TransferSession:
    """Subscribe to progress, and pause, resume, or cancel the worker."""

    def __init__(self, cancel: threading.Event | None = None) -> None:
        """Bind an optional cancel event shared with an existing caller.

        Args:
            cancel: When omitted, the session owns a new event.
        """
        self._cancel = cancel if cancel is not None else threading.Event()
        self._pause = threading.Event()
        self._listeners: list[Listener] = []
        self._guard = threading.Lock()

    def subscribe(self, callback: Listener) -> Callable[[], None]:
        """Call ``callback`` on each published event.

        Args:
            callback: Receives the full snapshot, including every queued job.

        Returns:
            A function that removes this subscription.
        """
        with self._guard:
            self._listeners.append(callback)

        def unsubscribe() -> None:
            with self._guard:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return unsubscribe

    def publish(self, event: ProgressEvent) -> None:
        """Deliver ``event`` to the current subscribers."""
        with self._guard:
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener(event)

    def pause(self) -> None:
        """Stop reading further bytes until :meth:`resume`."""
        self._pause.set()

    def resume(self) -> None:
        """Continue a paused transfer."""
        self._pause.clear()

    def cancel(self) -> None:
        """Stop the worker. A paused transfer wakes and exits."""
        self._pause.clear()
        self._cancel.set()

    @property
    def paused(self) -> bool:
        """True after :meth:`pause` until :meth:`resume` or :meth:`cancel`."""
        return self._pause.is_set()

    @property
    def cancelled(self) -> bool:
        """True after :meth:`cancel`."""
        return self._cancel.is_set()

    def checkpoint(self) -> None:
        """Block while paused, or raise when cancelled.

        Raises:
            TransferCancelled: :meth:`cancel` was called.
        """
        while self._pause.is_set() and not self._cancel.is_set():
            self._pause.wait(0.1)
        if self._cancel.is_set():
            raise TransferCancelled("cancelled")

    def bind_cancel(self, extras: Sequence[threading.Event]) -> threading.Event:
        """Return the session cancel event, also set when any extra event is set.

        Fetch code polls a single ``is_set``. The returned object is the
        session event when ``extras`` is empty. Otherwise it is a view that
        is set when the session or any extra event is set.
        """
        if not extras:
            return self._cancel
        return _CombinedCancel(self._cancel, extras)


class _CombinedCancel:
    """``is_set`` is true when the session or any linked event is set."""

    def __init__(self, primary: threading.Event, extras: Sequence[threading.Event]) -> None:
        self._primary = primary
        self._extras = tuple(extras)

    def is_set(self) -> bool:
        """Return whether cancellation was requested."""
        if self._primary.is_set():
            return True
        return any(event.is_set() for event in self._extras)
