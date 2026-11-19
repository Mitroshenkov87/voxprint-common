"""Single running model-downloader process.

The first process holds ``state/mdl-instance.lock`` and listens on
``127.0.0.1``. A second launch connects, asks that process to focus its
window or to add an app to the queue, and then exits. The queue itself lives
in ``state/mdl-queue.json``.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import os
import secrets
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path

from voxprint_common.atomic import LockTimeout, exclusive_lock, write_json
from voxprint_common.paths import state_dir

logger = logging.getLogger(__name__)

__all__ = [
    "AUTHENTICATED_FLAG",
    "Primary",
    "become_primary",
    "is_authenticated",
    "mark_authenticated",
    "send",
]

_LOCK = "mdl-instance.lock"
_INFO = "mdl-instance.json"
# Set only by this process after the instance-IPC gate (shared token in
# the 0600 info file, plus a peer-credential check on a Unix socket).
# Clients cannot set it: incoming JSON has the key stripped first.
AUTHENTICATED_FLAG = "_authenticated"
_AUTH_FIELD = "auth"


@dataclass
class Primary:
    """The process that owns the queue and, when asked, the window."""

    port: int
    _held: object
    _socket: socket.socket
    _token: str = ""
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
            message = _accepted_message(payload, self._token, client)
            with self._guard:
                self._messages.append(message)
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
        token = _write_info(os.getpid(), port, secrets.token_urlsafe(32))
    except OSError:
        held.__exit__(None, None, None)
        sock.close()
        return None
    primary = Primary(port, held, sock, _token=token)
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
    outgoing = dict(message)
    outgoing.pop(AUTHENTICATED_FLAG, None)
    token = info.get("token")
    if isinstance(token, str) and token:
        outgoing[_AUTH_FIELD] = token
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as client:
            client.sendall(json.dumps(outgoing).encode("utf-8") + b"\n")
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


def is_authenticated(message: dict[str, object]) -> bool:
    """Return whether ``message`` passed the instance or GUI IPC gate."""
    return message.get(AUTHENTICATED_FLAG) is True


def mark_authenticated(message: dict[str, object]) -> dict[str, object]:
    """Return a copy marked as coming from an already-authenticated channel.

    The GUI socket (mode 0600) and the Windows GUI pipe (owner-only DACL)
    authenticate the peer at the transport, the same gate a ``ping`` uses.
    """
    out = {str(key): value for key, value in message.items() if key != _AUTH_FIELD}
    out[AUTHENTICATED_FLAG] = True
    return out


def _write_info(pid: int, port: int, token: str) -> str:
    """Publish the instance record. Returns the token that was stored.

    On POSIX the file is created mode 0600 so the token is never readable
    by others. If that mode cannot be ensured, the token is omitted and
    token-auth IPC is disabled. On Windows the state directory is already
    under the per-user profile, so the token is written as before.
    """
    path = state_dir() / _INFO
    payload: dict[str, object] = {"pid": pid, "port": port, "token": token}
    if os.name == "nt":
        write_json(path, payload)
        return token
    write_json(path, payload, mode=0o600)
    if _is_owner_only(path):
        return token
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.warning("could not set mode 0600 on %s (%s); omitting the IPC token", path, exc)
        write_json(path, {"pid": pid, "port": port}, mode=0o600)
        return ""
    if _is_owner_only(path):
        return token
    logger.warning("%s is not mode 0600; omitting the IPC token", path)
    write_json(path, {"pid": pid, "port": port}, mode=0o600)
    return ""


def _is_owner_only(path: Path) -> bool:
    """Return whether ``path`` is mode 0600 on POSIX."""
    try:
        return (path.stat().st_mode & 0o777) == 0o600
    except OSError:
        return False


def _accepted_message(
    payload: dict[object, object], token: str, client: socket.socket
) -> dict[str, object]:
    """Strip client auth fields and mark the message if the gate passes."""
    message = {str(key): value for key, value in payload.items()}
    message.pop(AUTHENTICATED_FLAG, None)
    provided = message.pop(_AUTH_FIELD, None)
    if _token_matches(token, provided) and _peer_is_same_user(client):
        message[AUTHENTICATED_FLAG] = True
    return message


def _token_matches(expected: str, provided: object) -> bool:
    return bool(expected) and isinstance(provided, str) and hmac.compare_digest(provided, expected)


def _peer_is_same_user(client: socket.socket) -> bool:
    """Return whether the peer is this user, when the transport can say.

    A Unix socket uses ``SO_PEERCRED`` (or ``getpeereid``). TCP has no
    peer credentials, so the 0600 token is the gate. On Windows the GUI
    pipe already has an owner-only DACL; instance TCP still uses the token.
    """
    if getattr(client, "family", None) != getattr(socket, "AF_UNIX", -1):
        return True
    try:
        if hasattr(client, "getsockopt") and hasattr(socket, "SO_PEERCRED"):
            import struct

            creds = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", creds)
            return uid == os.getuid()
        getpeereid = getattr(socket, "getpeereid", None)
        if callable(getpeereid):
            uid, _gid = getpeereid(client)
            return uid == os.getuid()
    except OSError:
        return False
    return True


def instance_info_path() -> Path:
    """Return the path of the instance record. The file is not created."""
    return state_dir() / _INFO
