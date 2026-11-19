"""Block system sleep for the length of a long job.

:func:`keep_awake` is a context manager. :meth:`WakeLock.acquire` and
:meth:`WakeLock.release` are the same hold for code that cannot use ``with``.
The hold is released when the block exits, including when the block raises or
is cancelled. Nested holds share one inhibit and the last release drops it.

:func:`voxprint_common.suite_settings.keep_awake_during_jobs` defaults to true.
When it is false, the helper does not take an inhibit.

Windows calls ``SetThreadExecutionState``. That state belongs to the calling
thread, so the helper applies it on a dedicated keeper thread and resets it to
``ES_CONTINUOUS`` on release. ``display=True`` adds ``ES_DISPLAY_REQUIRED``.

Linux calls logind ``Inhibit("sleep:idle", "Voxprint", reason, "block")``
through jeepney and holds the returned file descriptor. The bus connection is
closed after the descriptor arrives; closing the descriptor releases the
inhibit. If logind is unavailable, the helper tries ``systemd-inhibit``, then
the session ``org.freedesktop.ScreenSaver`` ``Inhibit`` method, then logs a
warning and continues. A missing backend does not raise.
"""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import subprocess
import sys
import threading
from types import TracebackType

from voxprint_common.suite_settings import keep_awake_during_jobs

try:
    from jeepney import DBusAddress, MessageType, new_method_call
    from jeepney.io.blocking import open_dbus_connection
except ImportError:  # A stripped install still runs; Linux degrades below.
    DBusAddress = None  # type: ignore[misc, assignment]
    MessageType = None  # type: ignore[misc, assignment]
    new_method_call = None  # type: ignore[misc, assignment]
    open_dbus_connection = None  # type: ignore[misc, assignment]

__all__ = ["WakeLock", "keep_awake"]

logger = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
_WHO = "Voxprint"
_WHAT = "sleep:idle"
_MODE = "block"
_KEEPER_TIMEOUT = 5.0

if DBusAddress is not None:
    _LOGIN1 = DBusAddress(
        "/org/freedesktop/login1",
        bus_name="org.freedesktop.login1",
        interface="org.freedesktop.login1.Manager",
    )
    _SCREENSAVER = DBusAddress(
        "/org/freedesktop/ScreenSaver",
        bus_name="org.freedesktop.ScreenSaver",
        interface="org.freedesktop.ScreenSaver",
    )
else:
    _LOGIN1 = None
    _SCREENSAVER = None


class WakeLock:
    """One counted hold that blocks system sleep.

    Use it as a context manager, or call :meth:`acquire` and :meth:`release`.
    Acquiring the same lock twice keeps a single inhibit until both releases
    have run. A second :class:`WakeLock` acquired while this one is held shares
    that inhibit.
    """

    def __init__(self, reason: str, display: bool = False) -> None:
        """Store the reason and whether the display should stay on.

        Args:
            reason: Text passed to the Linux inhibit. Windows has no reason field.
            display: When true, Windows also sets ``ES_DISPLAY_REQUIRED``.

        Raises:
            TypeError: ``reason`` is not a str, or ``display`` is not a bool.
        """
        if not isinstance(reason, str):
            raise TypeError(f"reason must be a str, got {type(reason).__name__}")
        if not isinstance(display, bool):
            raise TypeError(f"display must be a bool, got {type(display).__name__}")
        self.reason = reason
        self.display = display
        self._depth = 0
        self._token: int | None = None
        self._guard = threading.Lock()

    def acquire(self) -> WakeLock:
        """Take the hold.

        When :func:`~voxprint_common.suite_settings.keep_awake_during_jobs` is
        false, this only counts the call. It does not ask the operating system
        to stay awake.

        Returns:
            This lock.
        """
        with self._guard:
            self._depth += 1
            take = self._depth == 1
        if not take or not keep_awake_during_jobs():
            return self
        token = _registry.hold(self.reason, self.display)
        with self._guard:
            if self._depth == 0:
                self._token = None
            else:
                self._token = token
                token = None
        if token is not None:
            _registry.drop(token)
        return self

    def release(self) -> None:
        """Drop one hold. A spare release does nothing."""
        with self._guard:
            if self._depth == 0:
                return
            self._depth -= 1
            if self._depth > 0:
                return
            token = self._token
            self._token = None
        if token is not None:
            _registry.drop(token)

    def __enter__(self) -> WakeLock:
        """Acquire the hold and return it."""
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the hold taken by :meth:`__enter__`."""
        self.release()


def keep_awake(reason: str, display: bool = False) -> WakeLock:
    """Return a hold that blocks system sleep while a long job runs.

    Entering the context manager, or calling :meth:`WakeLock.acquire`, takes
    the hold. Leaving the context manager releases it on a normal return, on
    an exception, and on cancellation. The last active hold is the one that
    releases the operating-system inhibit.

    Args:
        reason: Short description of the job. Linux puts this in the inhibit.
        display: When true, Windows keeps the display on as well.

    Returns:
        The lock. It is not held until ``with`` or :meth:`WakeLock.acquire`.
    """
    return WakeLock(reason, display)


class _Inhibit:
    """One operating-system inhibit, shared by every active hold."""

    def update(self, *, display: bool) -> None:
        """Apply a display-flag change. The default holds the current inhibit."""
        return

    def close(self) -> None:
        """Release the inhibit."""
        return


class _FdInhibit(_Inhibit):
    def __init__(self, descriptor: object) -> None:
        self._descriptor = descriptor

    def close(self) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is None:
            return
        if isinstance(descriptor, int):
            os.close(descriptor)
            return
        _close_quietly(descriptor)


class _ProcessInhibit(_Inhibit):
    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._proc = proc

    def close(self) -> None:
        if self._proc.poll() is not None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=_KEEPER_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=_KEEPER_TIMEOUT)


class _ScreenInhibit(_Inhibit):
    def __init__(self, connection: object, cookie: int) -> None:
        self._connection = connection
        self._cookie = cookie

    def close(self) -> None:
        try:
            if new_method_call is not None and _SCREENSAVER is not None:
                message = new_method_call(_SCREENSAVER, "UnInhibit", "u", (self._cookie,))
                send = getattr(self._connection, "send_and_get_reply", None)
                if callable(send):
                    send(message)
        except Exception:
            logger.debug("keep_awake could not release the screensaver inhibit", exc_info=True)
        finally:
            _close_quietly(self._connection)


class _WindowsInhibit(_Inhibit):
    """Keep Windows awake from a thread that outlives the caller."""

    def __init__(self, display: bool) -> None:
        self._display = display
        self._closed = False
        self._queue: queue.Queue[str] = queue.Queue()
        self._result: queue.Queue[BaseException | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="voxprint-keep-awake", daemon=True)
        self._thread.start()
        self._queue.put("apply")
        error = self._wait()
        if error is not None:
            self.close()
            raise error

    def update(self, *, display: bool) -> None:
        if self._closed:
            return
        self._display = display
        self._queue.put("apply")
        error = self._wait()
        if isinstance(error, Exception):
            logger.warning(
                "keep_awake could not update the Windows execution state", exc_info=error
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put("stop")
        self._wait()
        self._thread.join(timeout=_KEEPER_TIMEOUT)

    def _wait(self) -> BaseException | None:
        try:
            return self._result.get(timeout=_KEEPER_TIMEOUT)
        except queue.Empty:
            return TimeoutError("wake keeper did not answer")

    def _run(self) -> None:
        while True:
            command = self._queue.get()
            if command == "stop":
                try:
                    _set_thread_execution_state(ES_CONTINUOUS)
                except Exception:
                    logger.warning(
                        "keep_awake could not clear the Windows execution state",
                        exc_info=True,
                    )
                self._result.put(None)
                return
            try:
                _set_thread_execution_state(_execution_flags(self._display))
            except Exception as exc:
                self._result.put(exc)
            else:
                self._result.put(None)


class _Registry:
    """Process-wide set of holds and the one inhibit they share."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0
        self._holders: dict[int, tuple[str, bool]] = {}
        self._inhibit: _Inhibit | None = None

    def hold(self, reason: str, display: bool) -> int:
        with self._lock:
            self._next += 1
            token = self._next
            self._holders[token] = (reason, display)
            self._sync()
            return token

    def drop(self, token: int) -> None:
        with self._lock:
            self._holders.pop(token, None)
            self._sync()

    def reset(self) -> None:
        with self._lock:
            self._holders.clear()
            inhibit = self._inhibit
            self._inhibit = None
        if inhibit is not None:
            _close_inhibit(inhibit)

    def _sync(self) -> None:
        if not self._holders:
            inhibit = self._inhibit
            self._inhibit = None
            if inhibit is not None:
                _close_inhibit(inhibit)
            return
        display = any(flag for _, flag in self._holders.values())
        reason = next(iter(self._holders.values()))[0]
        if self._inhibit is None:
            self._inhibit = _start_inhibit(reason, display)
            return
        try:
            self._inhibit.update(display=display)
        except Exception:
            logger.warning("keep_awake could not update the inhibit", exc_info=True)


_registry = _Registry()


def _reset_for_tests() -> None:
    _registry.reset()


def _execution_flags(display: bool) -> int:
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    if display:
        flags |= ES_DISPLAY_REQUIRED
    return flags


def _set_thread_execution_state(flags: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_state = kernel32.SetThreadExecutionState
    set_state.argtypes = (ctypes.c_uint,)
    set_state.restype = ctypes.c_uint
    if set_state(flags) == 0 and flags != ES_CONTINUOUS:
        raise OSError(ctypes.get_last_error(), "SetThreadExecutionState failed")


def _start_inhibit(reason: str, display: bool) -> _Inhibit:
    try:
        if sys.platform == "win32":
            return _WindowsInhibit(display)
        return _start_linux(reason)
    except Exception:
        logger.warning(
            "keep_awake could not block sleep; the job continues without an inhibit",
            exc_info=True,
        )
        return _Inhibit()


def _start_linux(reason: str) -> _Inhibit:
    errors = 0
    for start in (_inhibit_login1, _inhibit_systemd, _inhibit_screensaver):
        try:
            return start(reason)
        except Exception:
            errors += 1
            logger.debug("keep_awake backend %s failed", start.__name__, exc_info=True)
    if errors:
        logger.warning(
            "keep_awake has no sleep inhibit; the job continues without one (%s)",
            reason,
        )
    return _Inhibit()


def _inhibit_login1(reason: str) -> _Inhibit:
    if (
        open_dbus_connection is None
        or new_method_call is None
        or _LOGIN1 is None
        or MessageType is None
    ):
        raise RuntimeError("jeepney is not installed")
    connection = open_dbus_connection(bus="SYSTEM", enable_fds=True)
    try:
        message = new_method_call(_LOGIN1, "Inhibit", "ssss", (_WHAT, _WHO, reason, _MODE))
        reply = connection.send_and_get_reply(message)
        _expect_return(reply)
        descriptor = reply.body[0]
    except Exception:
        _close_quietly(connection)
        raise
    _close_quietly(connection)
    return _FdInhibit(descriptor)


def _inhibit_systemd(reason: str) -> _Inhibit:
    proc = subprocess.Popen(
        [
            "systemd-inhibit",
            f"--what={_WHAT}",
            f"--who={_WHO}",
            "--why",
            reason,
            f"--mode={_MODE}",
            "--",
            "sleep",
            "infinity",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        proc.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        return _ProcessInhibit(proc)
    raise RuntimeError(f"systemd-inhibit exited {proc.returncode}")


def _inhibit_screensaver(reason: str) -> _Inhibit:
    if open_dbus_connection is None or new_method_call is None or _SCREENSAVER is None:
        raise RuntimeError("jeepney is not installed")
    connection = open_dbus_connection(bus="SESSION")
    try:
        message = new_method_call(_SCREENSAVER, "Inhibit", "ss", (_WHO, reason))
        reply = connection.send_and_get_reply(message)
        _expect_return(reply)
        cookie = reply.body[0]
    except Exception:
        _close_quietly(connection)
        raise
    return _ScreenInhibit(connection, int(cookie))


def _expect_return(reply: object) -> None:
    header = getattr(reply, "header", None)
    message_type = getattr(header, "message_type", None)
    if MessageType is None or message_type is not MessageType.method_return:
        raise RuntimeError(f"D-Bus call failed: {getattr(reply, 'body', None)}")
    body = getattr(reply, "body", None)
    if not isinstance(body, tuple) or len(body) < 1:
        raise RuntimeError("D-Bus reply had an empty body")


def _close_quietly(resource: object) -> None:
    close = getattr(resource, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        logger.debug("keep_awake could not close %s", type(resource).__name__, exc_info=True)


def _close_inhibit(inhibit: _Inhibit) -> None:
    try:
        inhibit.close()
    except Exception:
        logger.warning("keep_awake could not release the inhibit", exc_info=True)
