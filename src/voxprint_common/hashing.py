"""Streaming SHA-256 for files the suite downloads or pins."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

__all__ = ["HASH_RE", "HashMismatch", "sha256_bytes", "sha256_file", "verify"]

HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_CHUNK = 1024 * 1024


class HashMismatch(ValueError):
    """A file's SHA-256 does not match the expected digest."""

    def __init__(self, path: Path, expected: str, found: str) -> None:
        """Record the path and both digests.

        Args:
            path: File that was hashed.
            expected: Digest the caller required, lowercase hex.
            found: Digest of the bytes on disk, lowercase hex.
        """
        self.path = path
        self.expected = expected
        self.found = found
        super().__init__(f"{path} sha256 is {found}, expected {expected}")


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of ``data``.

    Args:
        data: Bytes to hash.

    Returns:
        64 lowercase hexadecimal characters.
    """
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk: int = _CHUNK) -> str:
    """Return the lowercase SHA-256 hex digest of a file, read in chunks.

    Args:
        path: File to hash.
        chunk: Read size in bytes. Must be positive.

    Returns:
        64 lowercase hexadecimal characters.
    """
    if chunk <= 0:
        raise ValueError("chunk must be positive")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verify(path: Path, expected: str) -> str:
    """Hash ``path`` and return the digest when it matches ``expected``.

    Args:
        path: File to hash.
        expected: 64 hex characters. Case is ignored.

    Returns:
        The lowercase digest.

    Raises:
        ValueError: ``expected`` is not 64 hex characters.
        HashMismatch: The file hashed to a different digest.
    """
    wanted = expected.strip().lower()
    if HASH_RE.fullmatch(wanted) is None:
        raise ValueError("sha256 must be 64 hexadecimal characters")
    found = sha256_file(path)
    if found != wanted:
        raise HashMismatch(path, wanted, found)
    return found
