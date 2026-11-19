"""HTTPS allowlist for model downloads.

Production transfers stay on Hugging Face (including its CDN), Kaggle,
ModelScope, Google Drive, and GitHub release hosts. The scheme is HTTPS.
A redirect whose host is not on that list is refused. Private, loopback,
link-local, reserved, multicast, and unspecified addresses are refused, with
two exceptions for an allowlisted HTTPS host: an address in ``198.18.0.0/15``
(DNS that answers with placeholder addresses for a local tunnel), and any
answer when the request goes through a configured proxy, because the proxy
opens the connection. TLS verification stays on in both cases, so the
certificate must still match the host name.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = ["FAKE_IP_NETWORK", "UnsafeUrl", "UrlGuard", "proxied"]

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


FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


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

    def check(self, url: str, *, via_proxy: bool = False) -> str:
        """Return ``url`` when it is safe to open.

        Args:
            url: Candidate URL, absolute.
            via_proxy: The request goes through a system or environment proxy,
                so the local DNS answer for the host is not used.

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
        https = parsed.scheme.lower() == "https"
        if https and via_proxy:
            return url.strip()
        for address in _addresses(host, self.resolve):
            if self.allow_private:
                continue
            if _blocked(address) and not (https and _fake_ip(address)):
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


def proxied(url: str) -> bool:
    """Return True when urllib's default proxy handler would proxy ``url``.

    Proxies come from the environment and, on Windows, the registry.
    """
    parsed = urllib.parse.urlsplit(url)
    proxies = urllib.request.getproxies()
    if not proxies.get(parsed.scheme.lower()):
        return False
    host = parsed.hostname or ""
    return not (host and urllib.request.proxy_bypass(host))


def _fake_ip(address: str) -> bool:
    try:
        return ipaddress.ip_address(address) in FAKE_IP_NETWORK
    except ValueError:
        return False


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
