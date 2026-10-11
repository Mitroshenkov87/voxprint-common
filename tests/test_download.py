"""Mirror failover, resume, and the disk-space precheck."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

import pytest

from voxprint_common.download import DiskSpaceError, DownloadError, download, huggingface_url
from voxprint_common.hashing import sha256_bytes

PAYLOAD = b"voxprint-bytes"
DIGEST = sha256_bytes(PAYLOAD)


class _Body:
    def __init__(self, status: int, data: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}
        self._data = data

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size >= len(self._data):
            out, self._data = self._data, b""
            return out
        out, self._data = self._data[:size], self._data[size:]
        return out

    def close(self) -> None:
        return None


class _Short:
    def __init__(self, data: bytes) -> None:
        self.status = 200
        self.headers: dict[str, str] = {}
        self._data = data
        self._sent = False

    def read(self, size: int = -1) -> bytes:
        if not self._sent:
            self._sent = True
            return self._data
        raise TimeoutError("connection reset")

    def close(self) -> None:
        return None


def test_disk_precheck_fails_before_any_request(tmp_path: Path) -> None:
    calls = {"n": 0}

    def opener(request: urllib.request.Request, timeout: float) -> _Body:
        del request, timeout
        calls["n"] += 1
        return _Body(200, PAYLOAD)

    destination = tmp_path / "model.bin"
    with pytest.raises(DiskSpaceError) as caught:
        download(
            destination,
            ["https://example.invalid/model.bin"],
            sha256=DIGEST,
            size=1000,
            disk_free=lambda _path: 100,
            opener=opener,
        )
    assert caught.value.need == 1200
    assert caught.value.free == 100
    assert "1200" in str(caught.value)
    assert "100" in str(caught.value)
    assert calls["n"] == 0
    assert not destination.exists()
    assert not Path(str(destination) + ".partial").exists()


def test_failover_after_network_and_hash_mismatch(tmp_path: Path) -> None:
    def opener(request: urllib.request.Request, timeout: float) -> _Body:
        del timeout
        url = request.full_url
        if url.endswith("/down"):
            raise urllib.error.URLError("unreachable")
        if url.endswith("/wrong"):
            return _Body(200, b"not-the-file")
        return _Body(200, PAYLOAD)

    destination = tmp_path / "model.bin"
    download(
        destination,
        [
            "https://mirror.example/down",
            "https://mirror.example/wrong",
            "https://mirror.example/good",
        ],
        sha256=DIGEST,
        disk_free=lambda _path: 10**12,
        opener=opener,
    )
    assert destination.read_bytes() == PAYLOAD
    assert not destination.with_name(destination.name + ".partial").exists()


def test_resume_sends_range(tmp_path: Path) -> None:
    destination = tmp_path / "model.bin"
    url = "https://mirror.example/file"

    def fail_early(request: urllib.request.Request, timeout: float) -> _Short:
        del request, timeout
        return _Short(PAYLOAD[:4])

    with pytest.raises(DownloadError):
        download(
            destination,
            [url],
            sha256=DIGEST,
            size=len(PAYLOAD),
            disk_free=lambda _path: 10**12,
            opener=fail_early,
        )
    partial = destination.with_name(destination.name + ".partial")
    assert partial.read_bytes() == PAYLOAD[:4]

    def resume(request: urllib.request.Request, timeout: float) -> _Body:
        del timeout
        assert request.get_header("Range") == "bytes=4-"
        return _Body(206, PAYLOAD[4:])

    download(
        destination,
        [url],
        sha256=DIGEST,
        size=len(PAYLOAD),
        disk_free=lambda _path: 10**12,
        opener=resume,
    )
    assert destination.read_bytes() == PAYLOAD


def test_file_url_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(DownloadError, match="http"):
        download(tmp_path / "x.bin", ["file:///tmp/x"], sha256=DIGEST, size=1)


def test_huggingface_url() -> None:
    assert huggingface_url("owner/name", "weights.safetensors", "abc") == (
        "https://huggingface.co/owner/name/resolve/abc/weights.safetensors"
    )
    with pytest.raises(DownloadError):
        huggingface_url("owner/name", "../secret", "main")
