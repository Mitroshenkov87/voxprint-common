"""CLI-to-GUI channel for a running MDL window.

The first window for an app listens on ``<home>/state/<app>/gui.sock``
(JSON lines). A later ``enqueue``, ``open-tab``, or ``--gui`` command
sends one request and exits instead of starting a second window. On
Windows the same protocol uses a named pipe
``voxprint-mdl-<app>-gui``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from voxprint_common.paths import state_dir

__all__ = [
    "GuiServer",
    "listen",
    "pipe_name",
    "send",
    "socket_path",
]

_APP = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CMDS = frozenset({"ping", "show", "enqueue", "open-tab", "manage", "wakeup", "focus"})
Handler = Callable[[dict[str, Any]], dict[str, Any]]

# Windows named-pipe constants (kernel32). Unused on POSIX.
_PIPE_ACCESS_DUPLEX = 0x00000003
_FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
_FILE_FLAG_OVERLAPPED = 0x40000000
_PIPE_TYPE_BYTE = 0x00000000
_PIPE_WAIT = 0x00000000
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_INVALID_HANDLE = 2**64 - 1 if sys.maxsize > 2**32 else 2**32 - 1
_ERROR_PIPE_CONNECTED = 535
_ERROR_PIPE_BUSY = 231
_ERROR_IO_PENDING = 997
_WAIT_OBJECT_0 = 0


def _check_app(app: str) -> str:
    ident = (app or "").strip().lower()
    if not _APP.match(ident):
        raise ValueError(f"invalid suite app id: {app!r}")
    return ident


def socket_path(app: str) -> Path:
    """Return ``<home>/state/<app>/gui.sock``. The file is not created."""
    return state_dir() / _check_app(app) / "gui.sock"


def pipe_name(app: str) -> str:
    """Return the Windows named-pipe path for ``app``."""
    return rf"\\.\pipe\voxprint-mdl-{_check_app(app)}-gui"


class GuiServer:
    """Accept JSON-line requests for one app on a background thread."""

    def __init__(self, app: str, handler: Handler) -> None:
        self.app = _check_app(app)
        self._handler = handler
        self._closed = False
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._pipe_handles: list[int] = []
        self._security: object | None = None

    def start(self) -> bool:
        """Start listening. Return False if another live server already owns the path."""
        if sys.platform == "win32":
            return self._start_windows()
        return self._start_unix()

    def close(self) -> None:
        """Stop accepting and remove the socket file or close the pipe."""
        self._closed = True
        if self._socket is not None:
            with contextlib.suppress(OSError):
                self._socket.close()
        path = socket_path(self.app)
        if path.is_socket() or path.is_file():
            with contextlib.suppress(OSError):
                path.unlink()
        if sys.platform == "win32":
            self._disconnect_windows()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1)
        if sys.platform == "win32":
            self._release_windows()

    def _start_unix(self) -> bool:
        path = socket_path(self.app)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if send(self.app, {"cmd": "ping"}, timeout=0.2) is not None:
                return False
            with contextlib.suppress(OSError):
                path.unlink()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(path))
            sock.listen(8)
            # Only this user should reach the GUI.
            with contextlib.suppress(OSError):
                os.chmod(path, 0o600)
        except OSError:
            sock.close()
            return False
        self._socket = sock
        self._thread = threading.Thread(
            target=self._serve_unix, name=f"voxprint-mdl-gui-{self.app}", daemon=True
        )
        self._thread.start()
        return True

    def _serve_unix(self) -> None:
        assert self._socket is not None
        self._socket.settimeout(0.2)
        while not self._closed:
            try:
                client, _addr = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._read_socket, args=(client,), daemon=True).start()

    def _read_socket(self, client: socket.socket) -> None:
        try:
            client.settimeout(2.0)
            raw = _read_line(client.recv)
            reply = self._dispatch(raw)
            client.sendall(reply)
        except OSError:
            return
        finally:
            with contextlib.suppress(OSError):
                client.close()

    def _dispatch(self, raw: bytes) -> bytes:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except UnicodeError, ValueError:
            return _encode({"ok": False, "error": "invalid json"})
        if not isinstance(payload, dict):
            return _encode({"ok": False, "error": "request must be a JSON object"})
        message = {str(key): value for key, value in payload.items()}
        command = str(message.get("cmd") or "")
        if command not in _CMDS:
            return _encode({"ok": False, "error": f"unknown cmd: {command!r}"})
        try:
            reply = self._handler(message)
        except Exception as exc:
            return _encode({"ok": False, "error": str(exc)})
        if not isinstance(reply, dict):
            return _encode({"ok": True})
        reply.setdefault("ok", True)
        return _encode(reply)

    def _start_windows(self) -> bool:
        if send(self.app, {"cmd": "ping"}, timeout=0.2) is not None:
            return False
        attributes, descriptor = _pipe_security()
        if attributes is None:
            return False
        self._security = (attributes, descriptor)
        self._thread = threading.Thread(
            target=self._serve_windows, name=f"voxprint-mdl-gui-{self.app}", daemon=True
        )
        self._thread.start()
        return True

    def _create_win_pipe(self, kernel32: Any, name: str, *, first: bool) -> Any:
        """Create one duplex pipe instance, or None if it could not be opened."""
        if self._closed:
            return None
        attributes = self._security[0] if self._security else None
        if attributes is None:
            return None
        open_mode = _PIPE_ACCESS_DUPLEX | _FILE_FLAG_OVERLAPPED
        if first:
            open_mode |= _FILE_FLAG_FIRST_PIPE_INSTANCE
        handle = kernel32.CreateNamedPipeW(
            name,
            open_mode,
            _PIPE_TYPE_BYTE | _PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS,
            8,
            65536,
            65536,
            200,
            attributes,
        )
        if handle in (None, 0, -1) or int(handle) == _INVALID_HANDLE:
            return None
        self._pipe_handles.append(int(handle))
        return handle

    def _drop_win_pipe(self, kernel32: Any, handle: Any) -> None:
        kernel32.DisconnectNamedPipe(handle)
        kernel32.CloseHandle(handle)
        with contextlib.suppress(ValueError):
            self._pipe_handles.remove(int(handle))

    def _serve_windows(self) -> None:
        kernel32, name = _kernel32(), pipe_name(self.app)
        handle = self._create_win_pipe(kernel32, name, first=True)
        if handle is None:
            return
        while not self._closed:
            if not _connect_win(kernel32, handle, lambda: self._closed):
                self._drop_win_pipe(kernel32, handle)
                if self._closed:
                    return
                handle = self._create_win_pipe(kernel32, name, first=False)
                if handle is None:
                    kernel32.Sleep(50)
                    handle = self._create_win_pipe(kernel32, name, first=False)
                    if handle is None:
                        return
                continue
            # Keep a free instance so the next client is not racing CreateNamedPipe.
            nxt = self._create_win_pipe(kernel32, name, first=False)
            try:
                raw = _read_win_line(kernel32, handle, timeout=2.0)
                if raw:
                    _write_win(kernel32, handle, self._dispatch(raw), timeout=2.0)
            except OSError:
                # A timed-out or failed reply must not kill the serve thread
                # and abandon the pre-created spare instance.
                pass
            finally:
                self._drop_win_pipe(kernel32, handle)
            handle = nxt
            if handle is None and not self._closed:
                handle = self._create_win_pipe(kernel32, name, first=False)
                if handle is None:
                    kernel32.Sleep(50)
                    handle = self._create_win_pipe(kernel32, name, first=False)
                    if handle is None:
                        return

    def _disconnect_windows(self) -> None:
        """Wake the serve thread by closing open pipe handles. The DACL stays."""
        if sys.platform != "win32":
            return
        kernel32 = _kernel32()
        for handle in list(self._pipe_handles):
            with contextlib.suppress(OSError):
                kernel32.DisconnectNamedPipe(handle)
                kernel32.CloseHandle(handle)
        self._pipe_handles.clear()

    def _release_windows(self) -> None:
        """Free the pipe DACL after the serve thread has stopped."""
        held = self._security
        self._security = None
        if held is not None:
            _free_security(held)


def listen(app: str, handler: Handler) -> GuiServer | None:
    """Start a per-app GUI server. Return None when another live server is already there."""
    server = GuiServer(app, handler)
    if not server.start():
        return None
    return server


def send(app: str, message: dict[str, Any], *, timeout: float = 1.0) -> dict[str, Any] | None:
    """Deliver one JSON-line request to a running GUI, if one is listening.

    Args:
        app: Suite app id whose window should receive the message.
        message: A JSON object. ``cmd`` is one of ``ping``, ``show``,
            ``enqueue``, ``open-tab``, ``manage``, ``wakeup``, or ``focus``.
        timeout: Seconds to wait for the connection.

    Returns:
        The GUI's JSON reply, or ``None`` when nothing is listening.
    """
    if sys.platform == "win32":
        return _send_windows(_check_app(app), message, timeout=timeout)
    return _send_unix(_check_app(app), message, timeout=timeout)


def _send_unix(app: str, message: dict[str, Any], *, timeout: float) -> dict[str, Any] | None:
    path = socket_path(app)
    if not path.exists():
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(path))
            client.sendall(_encode(message))
            with contextlib.suppress(OSError):
                client.shutdown(socket.SHUT_WR)
            raw = _read_line(client.recv)
    except OSError:
        return None
    return _decode_reply(raw)


def _send_windows(app: str, message: dict[str, Any], *, timeout: float) -> dict[str, Any] | None:
    kernel32 = _kernel32()
    name = pipe_name(app)
    deadline = time.monotonic() + max(0.001, timeout)
    handle: Any = None
    # WaitNamedPipe returns immediately when no instance exists (the gap
    # between one client disconnecting and the next CreateNamedPipe).
    # Keep retrying until the caller's timeout elapses.
    while time.monotonic() < deadline:
        left_ms = max(1, int((deadline - time.monotonic()) * 1000))
        if not kernel32.WaitNamedPipeW(name, left_ms):
            kernel32.Sleep(20)
            continue
        handle = kernel32.CreateFileW(
            name,
            _GENERIC_READ | _GENERIC_WRITE,
            0,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_OVERLAPPED,
            None,
        )
        if handle not in (None, 0, -1) and int(handle) != _INVALID_HANDLE:
            break
        kernel32.Sleep(20)
    else:
        return None
    try:
        left = max(0.001, deadline - time.monotonic())
        _write_win(kernel32, handle, _encode(message), timeout=left)
        left = max(0.001, deadline - time.monotonic())
        raw = _read_win_line(kernel32, handle, timeout=left)
    except OSError:
        return None
    finally:
        kernel32.CloseHandle(handle)
    return _decode_reply(raw)


def _encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def _decode_reply(raw: bytes) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeError, ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _read_line(recv: Callable[[int], bytes], *, limit: int = 1_048_576) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        block = recv(65536)
        if not block:
            break
        chunks.append(block)
        total += len(block)
        if b"\n" in block:
            break
    return b"".join(chunks).split(b"\n", 1)[0]


def _pipe_security() -> tuple[Any, Any]:
    """Return a SECURITY_ATTRIBUTES that allows only the current user.

    The descriptor is owned by the caller and must be freed with
    :func:`_free_security` after the serve thread has stopped. On failure,
    ``(None, None)`` is returned and the server does not listen.
    """
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.windll.advapi32  # type: ignore[attr-defined]
    kernel32 = _kernel32()

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", SID_AND_ATTRIBUTES)]

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CreateNamedPipeW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SECURITY_ATTRIBUTES),
    ]
    kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.ConnectNamedPipe.restype = wintypes.BOOL
    kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.CancelIoEx.restype = wintypes.BOOL
    kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = wintypes.DWORD
    kernel32.Sleep.argtypes = [wintypes.DWORD]

    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        return None, None
    try:
        size = wintypes.DWORD(0)
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if size.value == 0:
            return None, None
        buf = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buf, size, ctypes.byref(size)):
            return None, None
        user = TOKEN_USER.from_buffer(buf)
        sid = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(user.User.Sid, ctypes.byref(sid)):
            return None, None
        try:
            sddl = f"D:P(A;;GA;;;{sid.value})"
        finally:
            kernel32.LocalFree(sid)
        descriptor = ctypes.c_void_p()
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None
        ):
            return None, None
        attributes = SECURITY_ATTRIBUTES()
        attributes.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
        attributes.lpSecurityDescriptor = descriptor
        attributes.bInheritHandle = False
        return ctypes.pointer(attributes), descriptor
    finally:
        kernel32.CloseHandle(token)


def _free_security(held: object) -> None:
    """Release a descriptor created by :func:`_pipe_security`."""
    if not held:
        return
    _attributes, descriptor = held  # type: ignore[misc]
    if descriptor:
        _kernel32().LocalFree(descriptor)


def _kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if getattr(kernel32, "_voxprint_bound", False):
        return kernel32
    kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.CancelIoEx.restype = wintypes.BOOL
    kernel32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    kernel32.GetOverlappedResult.restype = wintypes.BOOL
    kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.ConnectNamedPipe.restype = wintypes.BOOL
    kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    kernel32.DisconnectNamedPipe.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = wintypes.DWORD
    kernel32.Sleep.argtypes = [wintypes.DWORD]
    kernel32._voxprint_bound = True
    return kernel32


def _overlapped(kernel32: Any) -> Any:
    """Return an OVERLAPPED struct with a manual-reset event, or None."""
    import ctypes
    from ctypes import wintypes

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    event = kernel32.CreateEventW(None, True, False, None)
    if not event:
        return None
    overlapped = OVERLAPPED()
    overlapped.hEvent = event
    return overlapped


def _await_io(
    kernel32: Any,
    handle: Any,
    overlapped: Any,
    *,
    timeout: float,
    stopped: Any | None = None,
) -> int:
    """Wait for overlapped I/O. Return bytes transferred, or -1 on timeout/failure."""
    import ctypes
    from ctypes import wintypes

    event = overlapped.hEvent
    deadline = time.monotonic() + max(0.001, timeout)
    try:
        while True:
            if stopped is not None and stopped():
                kernel32.CancelIoEx(handle, ctypes.byref(overlapped))
                kernel32.WaitForSingleObject(event, 200)
                return -1
            left_ms = max(1, int((deadline - time.monotonic()) * 1000))
            wait = kernel32.WaitForSingleObject(event, min(200, left_ms))
            if wait == _WAIT_OBJECT_0:
                transferred = wintypes.DWORD(0)
                if not kernel32.GetOverlappedResult(
                    handle, ctypes.byref(overlapped), ctypes.byref(transferred), False
                ):
                    return -1
                return int(transferred.value)
            if time.monotonic() >= deadline:
                kernel32.CancelIoEx(handle, ctypes.byref(overlapped))
                kernel32.WaitForSingleObject(event, 200)
                return -1
    finally:
        kernel32.CloseHandle(event)


def _connect_win(kernel32: Any, handle: Any, stopped: Any) -> bool:
    """Wait for a client with a short poll so close() can stop the server."""
    import ctypes

    overlapped = _overlapped(kernel32)
    if overlapped is None:
        return False
    ok = bool(kernel32.ConnectNamedPipe(handle, ctypes.byref(overlapped)))
    if ok:
        kernel32.CloseHandle(overlapped.hEvent)
        return True
    error = kernel32.GetLastError()
    if error == _ERROR_PIPE_CONNECTED:
        kernel32.CloseHandle(overlapped.hEvent)
        return True
    if error != _ERROR_IO_PENDING:
        kernel32.CloseHandle(overlapped.hEvent)
        return False
    # No hard deadline: close() sets stopped() and cancels the wait.
    return _await_io(kernel32, handle, overlapped, timeout=24 * 3600, stopped=stopped) >= 0


def _xfer_win(
    kernel32: Any, handle: Any, write: bool, buf: Any, size: int, *, timeout: float
) -> int:
    """Overlapped ReadFile or WriteFile with a timeout. Returns bytes transferred, or -1."""
    import ctypes

    overlapped = _overlapped(kernel32)
    if overlapped is None:
        return -1
    op = kernel32.WriteFile if write else kernel32.ReadFile
    ok = bool(op(handle, buf, size, None, ctypes.byref(overlapped)))
    if not ok and kernel32.GetLastError() != _ERROR_IO_PENDING:
        kernel32.CloseHandle(overlapped.hEvent)
        return -1
    return _await_io(kernel32, handle, overlapped, timeout=timeout)


def _read_win_line(
    kernel32: Any, handle: Any, *, limit: int = 1_048_576, timeout: float = 2.0
) -> bytes:
    import ctypes

    chunks: list[bytes] = []
    total = 0
    deadline = time.monotonic() + max(0.001, timeout)
    while total < limit:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        buf = ctypes.create_string_buffer(65536)
        n = _xfer_win(kernel32, handle, False, buf, 65536, timeout=left)
        if n <= 0:
            break
        block = buf.raw[:n]
        chunks.append(block)
        total += len(block)
        if b"\n" in block:
            break
    return b"".join(chunks).split(b"\n", 1)[0]


def _write_win(kernel32: Any, handle: Any, payload: bytes, *, timeout: float = 2.0) -> None:
    import ctypes

    if not payload:
        return
    buf = ctypes.create_string_buffer(payload, len(payload))
    if _xfer_win(kernel32, handle, True, buf, len(payload), timeout=timeout) != len(payload):
        raise OSError("WriteFile failed")


def notify(app: str, message: dict[str, Any], *, timeout: float = 0.2) -> bool:
    """Best-effort notify. False when no GUI is listening."""
    try:
        reply = send(app, message, timeout=timeout)
    except ValueError:
        return False
    return bool(reply) and reply.get("ok") is not False
