"""Download guard for fake-IP DNS and proxies, and direct/system route fallback."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from voxprint_common.mdl import fetch
from voxprint_common.mdl.fetch import PROBE_HEADER, fetch_file
from voxprint_common.mdl.safety import UnsafeUrl, UrlGuard, proxied

_URL = "https://huggingface.co/org/model/resolve/main/model.bin"
_DATA = bytes(range(256)) * 16


def _guard(address: str) -> UrlGuard:
    return UrlGuard(resolve=lambda _host: [address])


def test_fake_ip_accepted_only_for_allowlisted_https() -> None:
    assert _guard("198.18.0.7").check(_URL) == _URL
    assert _guard("198.19.255.1").check(_URL) == _URL
    for address in ("10.1.2.3", "127.0.0.1", "169.254.1.1", "192.168.0.2"):
        with pytest.raises(UnsafeUrl):
            _guard(address).check(_URL)
    http = UrlGuard(allow_http=True, resolve=lambda _host: ["198.18.0.7"])
    with pytest.raises(UnsafeUrl):
        http.check(_URL.replace("https://", "http://"))
    with pytest.raises(UnsafeUrl):
        _guard("198.18.0.7").check("https://example.com/model.bin")


def test_proxied_request_ignores_local_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("no_proxy", "NO_PROXY", "http_proxy", "HTTP_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    assert proxied(_URL)

    def no_dns(_host: str) -> list[str]:
        raise AssertionError("a proxied request must not resolve the target")

    assert UrlGuard(resolve=no_dns).check(_URL, via_proxy=True) == _URL
    with pytest.raises(UnsafeUrl):
        _guard("10.1.2.3").check(_URL)
    with pytest.raises(UnsafeUrl):
        UrlGuard(resolve=no_dns).check("https://example.com/x", via_proxy=True)
    assert fetch._allowed_routes(_URL, ("direct", "system"), _guard("10.1.2.3"), True) == (
        "system",
    )
    monkeypatch.setenv("VOXPRINT_MDL_ROUTE", "direct")
    assert fetch._route_names() == ("direct",)
    with pytest.raises(fetch.FetchError):
        fetch._allowed_routes(_URL, fetch._route_names(), _guard("10.1.2.3"), True)


class _Response(io.BytesIO):
    def __init__(self, body: bytes, status: int) -> None:
        super().__init__(body)
        self.status = status
        self.headers: dict[str, str] = {}


def _server(calls: list[str], name: str, *, fail: str = "") -> fetch.Opener:
    def open_url(request: object, timeout: float) -> _Response:
        probe = request.get_header(PROBE_HEADER.capitalize()) is not None
        calls.append(f"{name}:{'probe' if probe else 'get'}")
        if fail in {"all", "probe" if probe else "get"}:
            raise OSError("connection reset")
        span = request.get_header("Range")
        if span:
            start, end = span.split("=")[1].split("-")
            stop = int(end) + 1 if end else len(_DATA)
            return _Response(_DATA[int(start) : stop], 206)
        return _Response(_DATA, 200)

    return open_url


def _get(tmp_path: Path, routes: dict[str, fetch.Opener]) -> Path:
    return fetch_file(
        tmp_path / "model.bin",
        [_URL],
        sha256=hashlib.sha256(_DATA).hexdigest(),
        size=len(_DATA),
        probe_bytes=512,
        routes=routes,
        guard=_guard("198.18.0.7"),
        use_xet=False,
        sleep=lambda _s: None,
    )


def test_direct_fails_system_works(tmp_path: Path) -> None:
    calls: list[str] = []
    routes = {"direct": _server(calls, "direct", fail="all"), "system": _server(calls, "system")}
    assert _get(tmp_path, routes).read_bytes() == _DATA
    assert calls[0] == "direct:probe"
    assert "system:get" in calls
    assert "direct:get" not in calls


def test_transfer_falls_back_after_route_stalls(tmp_path: Path) -> None:
    calls: list[str] = []
    routes = {
        "direct": _server(calls, "direct", fail="get"),
        "system": _server(calls, "system", fail="probe"),
    }
    assert _get(tmp_path, routes).read_bytes() == _DATA
    assert calls.index("direct:get") < calls.index("system:get")
