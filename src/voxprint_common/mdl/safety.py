"""HTTPS allowlist for model downloads.

Production transfers stay on Hugging Face (including its CDN), Kaggle,
ModelScope, Google Drive, and GitHub release hosts. The scheme is HTTPS.
A redirect whose host is not on that list is refused. Private, loopback,
link-local, reserved, multicast, and unspecified addresses are refused.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = ["UnsafeUrl", "UrlGuard"]

Resolver = Callable[[str], Sequence[str]]

_SUFFIXES = (
    "huggingface.co",
    "hf.co",
    "kaggle.com",
    "modelscope.cn",
    "drive.google.com",
    "docs.google.com",
    "googleusercontent.com",
    "github.com",
    "githubusercontent.com",
)


class UnsafeUrl(ValueError):
    """A download URL is not HTTPS on an allowed public host."""


@dataclass(frozen=True)
class UrlGuard:
    """Rules applied before a connection and again on every redirect.

    ``allow_http`` and ``allow_private`` are for the test server. Production
    code uses the defaults.
    """

    allow_http: bool = False
    allow_private: bool = False
    extra_hosts: frozenset[str] = frozenset()
    resolve: Resolver | None = None

    def check(self, url: str) -> str:
        """Return ``url`` when it is safe to open.

        Args:
            url: Candidate URL, absolute.

        Raises:
            UnsafeUrl: The scheme, host, credentials, or an address is refused.
        """
        parsed = urllib.parse.urlsplit(url.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        schemes = {"https", "http"} if self.allow_http else {"https"}
        if parsed.scheme.lower() not in schemes or not host:
            raise UnsafeUrl("downloads must use https")
        if parsed.username or parsed.password:
            raise UnsafeUrl("downloads must not include credentials")
        if not _host_allowed(host, self.extra_hosts):
            raise UnsafeUrl(f"downloads must come from an allowed host, not {host}")
        for address in _addresses(host, self.resolve):
            if _blocked(address) and not self.allow_private:
                raise UnsafeUrl(f"downloads must not use a private address for {host}")
        return url.strip()

    @classmethod
    def loopback(cls) -> UrlGuard:
        """Allow the local test server on ``127.0.0.1`` and ``localhost``."""
        return cls(
            allow_http=True,
            allow_private=True,
            extra_hosts=frozenset({"127.0.0.1", "localhost"}),
        )


def _host_allowed(host: str, extra: frozenset[str]) -> bool:
    if host in extra:
        return True
    return any(host == suffix or host.endswith("." + suffix) for suffix in _SUFFIXES)


def _addresses(host: str, resolve: Resolver | None) -> list[str]:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return [host]
    if resolve is not None:
        found = [item.strip() for item in resolve(host) if item.strip()]
        if not found:
            raise UnsafeUrl(f"could not resolve {host}")
        return found
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrl(f"could not resolve {host}") from exc
    found: list[str] = []
    for info in infos:
        sockaddr = info[4]
        raw = sockaddr[0] if sockaddr else ""
        if isinstance(raw, str) and raw and raw not in found:
            found.append(raw)
    if not found:
        raise UnsafeUrl(f"could not resolve {host}")
    return found


def _blocked(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
