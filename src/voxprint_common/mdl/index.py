"""Suite model index from ``Mitroshenkov87/voxprint-model-index``.

The bundled snapshot is commit ``d2325ed``. A remote ``index.json`` is used
only when the detached ``index.json.sig`` verifies under a bundled Ed25519
public key and the document is not older than that pin. A missing signature,
a bad signature, a fetch error, or an older serial or ``generated_at`` is
logged and the bundled snapshot is used.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from voxprint_common.hashing import HASH_RE, sha256_bytes
from voxprint_common.mdl.ed25519 import public_key, sign, verify
from voxprint_common.mdl.safety import UnsafeUrl, UrlGuard, proxied

__all__ = [
    "INDEX_REPO",
    "IndexEntry",
    "SuiteIndex",
    "SuiteIndexError",
    "bundled_index_bytes",
    "fetch_indexed",
    "load_index",
    "parse_index",
    "private_key_pem",
    "public_key_pem",
    "verify_index",
    "verify_index_file",
    "write_index_signature",
]

INDEX_REPO = "Mitroshenkov87/voxprint-model-index"
_INDEX_URL = (
    "https://huggingface.co/datasets/Mitroshenkov87/voxprint-model-index/resolve/main/index.json"
)
_ENTRY_KEYS = frozenset({"id", "repo", "revision", "sha256"})
_OBJECT_KEYS = frozenset({"head", "serial", "generated_at", "models"})
_KEY_ENV = "VOXPRINT_MDL_INDEX_KEY"
_KEY_FILE_ENV = "VOXPRINT_MDL_INDEX_KEY_FILE"
_PRIVATE_PREFIX = bytes.fromhex("302e020100300506032b657004220420")
_PUBLIC_PREFIX = bytes.fromhex("302a300506032b6570032100")

logger = logging.getLogger(__name__)


class SuiteIndexError(ValueError):
    """The index document or signing key is not valid."""


@dataclass(frozen=True)
class IndexEntry:
    """One model in the suite index."""

    id: str
    repo: str
    revision: str
    sha256: str


@dataclass(frozen=True)
class SuiteIndex:
    """The index that was loaded, and whether it came from the bundle.

    ``fallback`` is empty when ``source`` is the document that was accepted.
    When the remote copy was refused, ``fallback`` says why and ``source``
    is ``bundled``.
    """

    entries: tuple[IndexEntry, ...]
    source: str
    sha256: str
    head: str = ""
    serial: int = 0
    generated_at: str = ""
    fallback: str = ""

    def get(self, model_id: str) -> IndexEntry | None:
        """Return the entry for ``model_id``, or None."""
        for entry in self.entries:
            if entry.id == model_id:
                return entry
        return None


@dataclass(frozen=True)
class _Pin:
    head: str
    serial: int
    generated_at: str
    sha256: str = ""


@dataclass(frozen=True)
class _Document:
    entries: tuple[IndexEntry, ...]
    head: str
    serial: int | None
    generated_at: str


def fetch_indexed(
    url: str,
    *,
    opener: object | None = None,
    guard: UrlGuard | None = None,
) -> bytes:
    """GET ``url`` through the same guarded opener that loads the index.

    Args:
        url: Absolute URL. The guard checks it before the opener runs.
        opener: ``opener(request, timeout) -> response``. Defaults to urllib.
        guard: URL rules. Defaults to the production allowlist.

    Raises:
        OSError: The response is an HTTP error or the body cannot be read.
        UnsafeUrl: ``url`` is not allowed.
    """
    return _fetch(url, opener, guard or UrlGuard())


def bundled_index_bytes() -> bytes:
    """Return the pinned ``index.json`` bytes shipped in this package."""
    return files("voxprint_common.mdl.data").joinpath("model-index.json").read_bytes()


def parse_index(payload: object) -> tuple[IndexEntry, ...]:
    """Parse an index document.

    A document is either a list of entries, or an object with ``models``
    plus an optional ``head``, ``serial``, and ``generated_at``.

    Args:
        payload: A decoded JSON value.

    Raises:
        SuiteIndexError: The document is not a list of complete entries.
    """
    return _document(payload).entries


def load_index(
    *,
    opener: object | None = None,
    url: str | None = None,
    guard: UrlGuard | None = None,
    remote: bool = True,
    public_keys: tuple[bytes, ...] | None = None,
) -> SuiteIndex:
    """Load the suite index, falling back to the bundled snapshot.

    Args:
        opener: ``opener(request, timeout) -> response``. Defaults to urllib.
        url: Override the dataset URL. The default is the Hugging Face dataset.
        guard: URL rules. Defaults to the production allowlist.
        remote: When false, skip the network and return the bundle.
        public_keys: Ed25519 public keys. Defaults to the keys bundled here.

    Returns:
        The index. ``source`` is ``remote`` only when the signature verifies
        and the document is not older than the bundled pin.
    """
    pinned = bundled_index_bytes()
    pin = _read_pin()
    if not remote:
        return _from_bundled(pinned, pin, "")
    keys = public_keys if public_keys is not None else _bundled_public_keys()
    checked = guard or UrlGuard()
    target = url or _INDEX_URL
    try:
        fetched = _fetch(target, opener, checked)
    except (OSError, UnsafeUrl, urllib.error.URLError, TimeoutError, ValueError) as exc:
        return _from_bundled(pinned, pin, f"remote index could not be fetched: {exc}")
    try:
        signature = _fetch(_signature_url(target), opener, checked)
    except (OSError, UnsafeUrl, urllib.error.URLError, TimeoutError, ValueError) as exc:
        if _missing(exc):
            return _from_bundled(pinned, pin, "remote index signature is missing")
        return _from_bundled(pinned, pin, f"remote index signature could not be fetched: {exc}")
    if not signature:
        return _from_bundled(pinned, pin, "remote index signature is missing")
    if not keys or not verify_index(fetched, signature, keys):
        return _from_bundled(pinned, pin, "remote index signature is invalid")
    try:
        document = _document(_decode(fetched))
        reason = _stale(document, pin)
    except SuiteIndexError as exc:
        return _from_bundled(pinned, pin, f"remote index rejected: {exc}")
    if reason:
        return _from_bundled(pinned, pin, reason)
    return SuiteIndex(
        document.entries,
        "remote",
        sha256_bytes(fetched),
        document.head,
        document.serial if document.serial is not None else pin.serial,
        document.generated_at or pin.generated_at,
        "",
    )


def verify_index(payload: bytes, signature: bytes, public_keys: tuple[bytes, ...]) -> bool:
    """Return whether ``signature`` matches ``payload`` under any bundled key.

    Args:
        payload: The exact ``index.json`` bytes.
        signature: Raw 64 bytes, or base64 of those bytes.
        public_keys: 32-byte Ed25519 public keys.
    """
    raw = _decode_signature(signature)
    if raw is None or not public_keys:
        return False
    return any(verify(payload, raw, key) for key in public_keys)


def write_index_signature(
    index_path: Path,
    *,
    key_file: Path | None = None,
    output: Path | None = None,
) -> Path:
    """Sign ``index_path`` and write a detached ``.sig`` file.

    The private key is read from ``key_file``, then
    ``VOXPRINT_MDL_INDEX_KEY_FILE``, then ``VOXPRINT_MDL_INDEX_KEY``.
    It is never read from the repository.

    Args:
        index_path: The ``index.json`` file to sign.
        key_file: PEM or raw seed file. Overrides the environment.
        output: Signature path. Defaults to ``<index_path>.sig``.

    Returns:
        The signature path.

    Raises:
        SuiteIndexError: No private key was provided, or the key is not Ed25519.
    """
    seed = _signing_seed(key_file)
    payload = Path(index_path).read_bytes()
    if output is not None:
        target = Path(output)
    else:
        target = Path(index_path).with_name(Path(index_path).name + ".sig")
    target.write_bytes(base64.b64encode(sign(payload, seed)) + b"\n")
    return target


def verify_index_file(
    index_path: Path,
    *,
    signature: Path | None = None,
    public_key_path: Path | None = None,
) -> bool:
    """Return whether the detached signature beside ``index_path`` verifies.

    Args:
        index_path: The ``index.json`` file.
        signature: Signature path. Defaults to ``<index_path>.sig``.
        public_key_path: PEM public key. Defaults to the bundled keys.

    Raises:
        SuiteIndexError: The public key file is not Ed25519.
    """
    payload = Path(index_path).read_bytes()
    sig_path = signature or Path(index_path).with_name(Path(index_path).name + ".sig")
    if not sig_path.is_file():
        return False
    if public_key_path is None:
        keys = _bundled_public_keys()
    else:
        keys = _public_keys(Path(public_key_path).read_bytes())
    return verify_index(payload, sig_path.read_bytes(), keys)


def private_key_pem(seed: bytes) -> str:
    """Return a PKCS8 PEM encoding of an Ed25519 seed.

    Args:
        seed: 32-byte private key seed.

    Raises:
        SuiteIndexError: ``seed`` is not 32 bytes.
    """
    if len(seed) != 32:
        raise SuiteIndexError("ed25519 seed must be 32 bytes")
    return _pem("PRIVATE KEY", _PRIVATE_PREFIX + seed)


def public_key_pem(key: bytes) -> str:
    """Return an SPKI PEM encoding of an Ed25519 public key.

    Args:
        key: 32-byte public key.

    Raises:
        SuiteIndexError: ``key`` is not 32 bytes.
    """
    if len(key) != 32:
        raise SuiteIndexError("ed25519 public key must be 32 bytes")
    return _pem("PUBLIC KEY", _PUBLIC_PREFIX + key)


def _from_bundled(raw: bytes, pin: _Pin, reason: str) -> SuiteIndex:
    if pin.sha256 and sha256_bytes(raw) != pin.sha256:
        raise SuiteIndexError("bundled index does not match its pin")
    if reason:
        logger.warning("using the bundled model index; %s", reason)
    document = _document(_decode(raw))
    return SuiteIndex(
        document.entries,
        "bundled",
        sha256_bytes(raw),
        pin.head,
        pin.serial,
        pin.generated_at,
        reason,
    )


def _document(payload: object) -> _Document:
    head = ""
    serial: int | None = None
    generated_at = ""
    if isinstance(payload, dict):
        extra = set(payload) - _OBJECT_KEYS
        if extra or "models" not in payload:
            raise SuiteIndexError("index object needs models, and only head, serial, generated_at")
        head = _head(payload.get("head"))
        serial = _serial(payload.get("serial")) if "serial" in payload else None
        if "generated_at" in payload:
            generated_at = _generated_at(payload.get("generated_at"))
        rows = payload.get("models")
    else:
        rows = payload
    return _Document(_entries(rows), head, serial, generated_at)


def _entries(rows: object) -> tuple[IndexEntry, ...]:
    if not isinstance(rows, list):
        raise SuiteIndexError("index.json must be a list")
    entries: list[IndexEntry] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _ENTRY_KEYS:
            raise SuiteIndexError("index entries need id, repo, revision, and sha256")
        model_id = row.get("id")
        repo = row.get("repo")
        revision = row.get("revision")
        digest = row.get("sha256")
        if not isinstance(model_id, str) or not model_id:
            raise SuiteIndexError("index id is required")
        if model_id in seen:
            raise SuiteIndexError(f"duplicate index id {model_id}")
        if not isinstance(repo, str) or not repo or ".." in repo.split("/"):
            raise SuiteIndexError("index repo is required")
        if not isinstance(revision, str) or not revision:
            raise SuiteIndexError("index revision is required")
        if not isinstance(digest, str) or HASH_RE.fullmatch(digest) is None:
            raise SuiteIndexError("index sha256 must be 64 hex characters")
        seen.add(model_id)
        entries.append(IndexEntry(model_id, repo, revision, digest.lower()))
    return tuple(entries)


def _head(value: object) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not value:
        raise SuiteIndexError("index head must be a commit sha")
    if any(ch not in "0123456789abcdef" for ch in value.lower()):
        raise SuiteIndexError("index head must be a commit sha")
    if not 7 <= len(value) <= 64:
        raise SuiteIndexError("index head must be a commit sha")
    return value.lower()


def _serial(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SuiteIndexError("index serial must be a non-negative integer")
    return value


def _generated_at(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SuiteIndexError("index generated_at must be an ISO-8601 timestamp")
    _stamp(value)
    return value.strip()


def _stamp(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SuiteIndexError("index generated_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SuiteIndexError("index generated_at must include a timezone")
    return parsed.astimezone(UTC)


def _stale(document: _Document, pin: _Pin) -> str:
    if document.serial is not None:
        if document.serial < pin.serial:
            return f"remote index serial {document.serial} is older than bundled pin {pin.serial}"
        return ""
    if document.generated_at:
        if _stamp(document.generated_at) < _stamp(pin.generated_at):
            return (
                f"remote index generated_at {document.generated_at} is older than "
                f"bundled pin {pin.generated_at}"
            )
        return ""
    return "remote index has no serial or generated_at"


def _read_pin() -> _Pin:
    raw = files("voxprint_common.mdl.data").joinpath("model-index.pin.json").read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise SuiteIndexError("bundled index pin is not JSON") from exc
    if not isinstance(payload, dict):
        raise SuiteIndexError("bundled index pin must be an object")
    head = _head(payload.get("head"))
    if len(head) < 7:
        raise SuiteIndexError("bundled index pin needs a head")
    if "serial" not in payload or "generated_at" not in payload:
        raise SuiteIndexError("bundled index pin needs serial and generated_at")
    digest = payload.get("sha256", "")
    if not isinstance(digest, str):
        raise SuiteIndexError("bundled index pin sha256 must be a string")
    return _Pin(
        head,
        _serial(payload.get("serial")),
        _generated_at(payload.get("generated_at")),
        digest,
    )


def _bundled_public_keys() -> tuple[bytes, ...]:
    raw = files("voxprint_common.mdl.data").joinpath("index-public.pem").read_bytes()
    return _public_keys(raw)


def _decode(raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise SuiteIndexError("index.json is not UTF-8 JSON") from exc


def _signature_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parts._replace(path=parts.path + ".sig"))


def _missing(exc: BaseException) -> bool:
    text = str(exc)
    return "HTTP 404" in text or "signature is missing" in text


def _fetch(url: str, opener: object | None, guard: UrlGuard) -> bytes:
    checked = guard.check(url, via_proxy=opener is None and proxied(url))
    request = urllib.request.Request(checked, method="GET", headers={"User-Agent": "voxprint-mdl"})
    open_url = opener or urllib.request.urlopen
    try:
        response = open_url(request, timeout=10)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise OSError("HTTP 404") from exc
        raise OSError(f"HTTP {exc.code}") from exc
    try:
        status = int(getattr(response, "status", 200) or 200)
        if status == 404:
            raise OSError("HTTP 404")
        if status >= 400:
            raise OSError(f"HTTP {status}")
        body = response.read()
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()
    return bytes(body or b"")


def _signing_seed(key_file: Path | None) -> bytes:
    if key_file is not None:
        return _private_seed(Path(key_file).read_bytes())
    file_env = os.environ.get(_KEY_FILE_ENV, "").strip()
    if file_env:
        return _private_seed(Path(file_env).read_bytes())
    value = os.environ.get(_KEY_ENV, "")
    if not value.strip():
        raise SuiteIndexError(
            "set VOXPRINT_MDL_INDEX_KEY or VOXPRINT_MDL_INDEX_KEY_FILE, or pass --key-file"
        )
    candidate = Path(value.strip())
    if "\n" not in value and candidate.is_file():
        return _private_seed(candidate.read_bytes())
    return _private_seed(value.encode())


def _private_seed(raw: bytes) -> bytes:
    der = _maybe_pem(raw, "PRIVATE KEY")
    if der is None:
        der = raw.strip()
        if len(der) != 32:
            try:
                der = base64.b64decode(der, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise SuiteIndexError("index private key is not Ed25519") from exc
    if der.startswith(_PRIVATE_PREFIX) and len(der) == len(_PRIVATE_PREFIX) + 32:
        der = der[len(_PRIVATE_PREFIX) :]
    if len(der) != 32:
        raise SuiteIndexError("index private key is not Ed25519")
    # Touch the expansion so a seed that cannot form a key fails here.
    public_key(der)
    return der


def _public_keys(raw: bytes) -> tuple[bytes, ...]:
    text = raw.decode("utf-8", errors="strict")
    if "BEGIN PUBLIC KEY" in text:
        found: list[bytes] = []
        for part in text.split("-----BEGIN PUBLIC KEY-----")[1:]:
            body, marker, _rest = part.partition("-----END PUBLIC KEY-----")
            if not marker:
                raise SuiteIndexError("public key PEM is truncated")
            der = base64.b64decode("".join(body.split()), validate=True)
            found.append(_one_public(der))
        if not found:
            raise SuiteIndexError("public key PEM is empty")
        return tuple(found)
    return (_one_public(raw.strip()),)


def _one_public(der: bytes) -> bytes:
    if der.startswith(_PUBLIC_PREFIX) and len(der) == len(_PUBLIC_PREFIX) + 32:
        der = der[len(_PUBLIC_PREFIX) :]
    elif len(der) != 32:
        try:
            decoded = base64.b64decode(der, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise SuiteIndexError("index public key is not Ed25519") from exc
        if decoded.startswith(_PUBLIC_PREFIX) and len(decoded) == len(_PUBLIC_PREFIX) + 32:
            der = decoded[len(_PUBLIC_PREFIX) :]
        else:
            der = decoded
    if len(der) != 32:
        raise SuiteIndexError("index public key is not Ed25519")
    return der


def _maybe_pem(raw: bytes, label: str) -> bytes | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        return None
    begin = f"-----BEGIN {label}-----"
    end = f"-----END {label}-----"
    if begin not in text:
        return None
    body = text.split(begin, 1)[1].split(end, 1)[0]
    try:
        return base64.b64decode("".join(body.split()), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SuiteIndexError("index private key is not Ed25519") from exc


def _decode_signature(raw: bytes) -> bytes | None:
    # A raw signature is 64 arbitrary bytes, so whitespace inside it must stay.
    if len(raw) == 64:
        return raw
    stripped = raw.strip()
    if len(stripped) == 64:
        return stripped
    try:
        decoded = base64.b64decode(stripped, validate=True)
    except ValueError, binascii.Error:
        return None
    if len(decoded) != 64:
        return None
    return decoded


def _pem(label: str, der: bytes) -> str:
    body = base64.b64encode(der).decode("ascii")
    lines = [body[index : index + 64] for index in range(0, len(body), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"
