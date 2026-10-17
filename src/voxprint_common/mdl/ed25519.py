"""Ed25519 signatures (RFC 8032) through the ``cryptography`` package.

Signing and verification use ``cryptography``'s Ed25519 keys, which follow
RFC 8032 and reject non-canonical signatures and invalid points.
"""

from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

__all__ = ["public_key", "sign", "verify"]


def _private(seed: bytes) -> Ed25519PrivateKey:
    if len(seed) != 32:
        raise ValueError("ed25519 seed must be 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key(seed: bytes) -> bytes:
    """Return the 32-byte public key for a 32-byte private key seed.

    Args:
        seed: The Ed25519 private key seed.

    Raises:
        ValueError: ``seed`` is not 32 bytes.
    """
    return _private(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def sign(message: bytes, seed: bytes) -> bytes:
    """Return the 64-byte signature of ``message``.

    Args:
        message: The bytes that were signed.
        seed: The Ed25519 private key seed.

    Raises:
        ValueError: ``seed`` is not 32 bytes.
    """
    return _private(seed).sign(message)


def verify(message: bytes, signature: bytes, key: bytes) -> bool:
    """Return whether ``signature`` is valid for ``message`` under ``key``.

    Args:
        message: The bytes that were signed.
        signature: A 64-byte Ed25519 signature.
        key: A 32-byte Ed25519 public key.
    """
    if len(signature) != 64 or len(key) != 32:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(signature, message)
    except (InvalidSignature, ValueError):
        return False
    return True
