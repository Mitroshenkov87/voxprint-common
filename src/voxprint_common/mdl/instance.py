"""Single running model-downloader process.

The first process holds ``state/mdl-instance.lock`` and listens on
``127.0.0.1``. A second launch connects, asks that process to focus its
window or to add an app to the queue, and then exits. The queue itself lives
in ``state/mdl-queue.json``.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path

from voxprint_common.atomic import LockTimeout, exclusive_lock, write_json
from voxprint_common.paths import state_dir

__all__ = ["Primary", "become_primary", "send"]

_LOCK = "mdl-instance.lock"
_INFO = "mdl-instance.json"


@dataclass
class Primary:
    """The process that owns the queue and, when asked, the window."""

    port: int
    _held: object
    _socket: socket.socket
    _messages: list[dict[str, object]] = field(default_factory=list)
    _guard: threading.Lock = field(default_factory=threading.Lock)
    _thread: threading.Thread | None = None
    _closed: bool = False

    def start(self) -> None:
        """Accept clients on a background thread."""
        self._thread = threading.Thread(target=self._serve, name="voxprint-mdl-ipc", daemon=True)
        self._thread.start()

    def poll(self) -> list[dict[str, object]]:
        """Return messages received since the previous call."""
        with self._guard:
            found = list(self._messages)
            self._messages.clear()
            return found

    def close(self) -> None:
        """Stop listening and release the single-instance lock."""
        self._closed = True
        with contextlib.suppress(OSError):
            self._socket.close()
        info = state_dir() / _INFO
        if info.is_file():
            with contextlib.suppress(OSError):
                info.unlink()
        closer = getattr(self._held, "__exit__", None)
        if callable(closer):
            closer(None, None, None)
        self._held = None

    def _serve(self) -> None:
        self._socket.settimeout(0.2)
        while not self._closed:
            try:
                client, _addr = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._read, args=(client,), daemon=True).start()

    def _read(self, client: socket.socket) -> None:
        try:
            chunks: list[bytes] = []
            while True:
                block = client.recv(65536)
                if not block:
                    break
                chunks.append(block)
                if b"\n" in block:
                    break
            raw = b"".join(chunks).split(b"\n", 1)[0]
            if not raw:
                return
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                return
            with self._guard:
                self._messages.append({str(key): value for key, value in payload.items()})
            client.sendall(b'{"ok": true}\n')
        except OSError, UnicodeError, ValueError:
            return
        finally:
            client.close()


def become_primary() -> Primary | None:
    """Become the single downloader process, or return None if one is running.

    Returns:
        The listening endpoint, or None when another live process holds the lock.
    """
    path = state_dir() / _LOCK
    try:
        held = exclusive_lock(path, timeout=0)
        held.__enter__()
    except LockTimeout:
        return None
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(8)
        port = int(sock.getsockname()[1])
        write_json(state_dir() / _INFO, {"pid": os.getpid(), "port": port})
    except OSError:
        held.__exit__(None, None, None)
        sock.close()
        return None
    primary = Primary(port, held, sock)
    primary.start()
    return primary


def send(message: dict[str, object], *, timeout: float = 1.0) -> bool:
    """Deliver ``message`` to the running downloader, if there is one.

    Args:
        message: A JSON object. ``cmd`` is ``show``, ``enqueue``, or ``manage``.
        timeout: Seconds to wait for the connection.

    Returns:
        True when the running process accepted the message.
    """
    info = _read_info()
    if info is None:
        return False
    port = info.get("port")
    if not isinstance(port, int):
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as client:
            client.sendall(json.dumps(message).encode("utf-8") + b"\n")
            client.shutdown(socket.SHUT_WR)
            client.recv(64)
    except OSError:
        return False
    return True


def _read_info() -> dict[str, object] | None:
    path = state_dir() / _INFO
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def instance_info_path() -> Path:
    """Return the path of the instance record. The file is not created."""
    return state_dir() / _INFO
