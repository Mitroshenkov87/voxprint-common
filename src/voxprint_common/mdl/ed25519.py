"""Ed25519 signatures (RFC 8032).

The index signer uses this so the package does not take a cryptography
dependency. Verification follows the RFC: a signature whose ``S`` is not
canonical, or a point that is not on the curve, is rejected.
"""

from __future__ import annotations

import hashlib

__all__ = ["public_key", "sign", "verify"]

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, -1, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)
_B = (
    15112221349535400772501151409588531511454012693041857206046113283949847762202,
    46316835694926478169428394003475163141307993866256225615783033603165251855960,
)


def _sha(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    y2 = y * y % _P
    u = (y2 - 1) % _P
    v = (_D * y2 + 1) % _P
    x = u * pow(v, 3, _P) * pow(u * pow(v, 7, _P), (_P - 5) // 8, _P) % _P
    vx2 = v * x * x % _P
    if vx2 == u:
        pass
    elif vx2 == (-u) % _P:
        x = x * _I % _P
    else:
        return None
    if x == 0 and sign:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _encode(point: tuple[int, int]) -> bytes:
    x, y = point
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode(raw: bytes) -> tuple[int, int] | None:
    if len(raw) != 32:
        return None
    value = int.from_bytes(raw, "little")
    x = _recover_x(value & ((1 << 255) - 1), value >> 255)
    if x is None:
        return None
    return x, value & ((1 << 255) - 1)


def _add(left: tuple[int, int], right: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = left
    x2, y2 = right
    product = _D * x1 * x2 * y1 * y2
    x3 = (x1 * y2 + x2 * y1) * pow((1 + product) % _P, -1, _P) % _P
    y3 = (y1 * y2 + x1 * x2) * pow((1 - product) % _P, -1, _P) % _P
    return x3, y3


def _scale(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    result = (0, 1)
    base = point
    while scalar:
        if scalar & 1:
            result = _add(result, base)
        base = _add(base, base)
        scalar >>= 1
    return result


def _expand(seed: bytes) -> tuple[int, bytes]:
    digest = _sha(seed)
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key(seed: bytes) -> bytes:
    """Return the 32-byte public key for a 32-byte seed.

    Args:
        seed: The Ed25519 private key seed.

    Raises:
        ValueError: ``seed`` is not 32 bytes.
    """
    if len(seed) != 32:
        raise ValueError("ed25519 seed must be 32 bytes")
    scalar, _prefix = _expand(seed)
    return _encode(_scale(_B, scalar))


def sign(message: bytes, seed: bytes) -> bytes:
    """Return the 64-byte signature of ``message``.

    Args:
        message: The bytes that were signed.
        seed: The Ed25519 private key seed.

    Raises:
        ValueError: ``seed`` is not 32 bytes.
    """
    scalar, prefix = _expand(seed)
    public = _encode(_scale(_B, scalar))
    nonce = int.from_bytes(_sha(prefix + message), "little") % _L
    commitment = _encode(_scale(_B, nonce))
    challenge = int.from_bytes(_sha(commitment + public + message), "little") % _L
    proof = (nonce + challenge * scalar) % _L
    return commitment + proof.to_bytes(32, "little")


def verify(message: bytes, signature: bytes, key: bytes) -> bool:
    """Return whether ``signature`` is valid for ``message`` under ``key``.

    Args:
        message: The bytes that were signed.
        signature: A 64-byte Ed25519 signature.
        key: A 32-byte Ed25519 public key.
    """
    if len(signature) != 64 or len(key) != 32:
        return False
    point = _decode(key)
    commitment = _decode(signature[:32])
    if point is None or commitment is None:
        return False
    proof = int.from_bytes(signature[32:], "little")
    if proof >= _L:
        return False
    challenge = int.from_bytes(_sha(signature[:32] + key + message), "little") % _L
    left = _scale(_B, proof)
    right = _add(commitment, _scale(point, challenge))
    return left == right
