"""Atomic publishes, streaming hashes, and the licence gate."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from voxprint_common import hashing, licence
from voxprint_common.atomic import LockTimeout, exclusive_lock, replace, write_json, write_text


def test_write_text_is_exact_utf8(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    write_text(target, "café\n")
    assert target.read_bytes() == "café\n".encode()
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_json_and_replace(tmp_path: Path) -> None:
    target = tmp_path / "doc.json"
    write_json(target, {"name": "voxprint"})
    assert target.read_text(encoding="utf-8").endswith("\n")
    source = tmp_path / "next.json"
    source.write_bytes(b"{}\n")
    replace(source, target)
    assert target.read_bytes() == b"{}\n"
    assert not source.exists()


def test_lock_times_out_for_another_thread(tmp_path: Path) -> None:
    lock = tmp_path / "data.lock"
    started = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with exclusive_lock(lock, timeout=2):
            started.set()
            release.wait(2)

    thread = threading.Thread(target=hold)
    thread.start()
    assert started.wait(2)
    with pytest.raises(LockTimeout), exclusive_lock(lock, timeout=0.1, poll=0.02):
        pass
    release.set()
    thread.join(2)
    assert not thread.is_alive()


def test_sha256_streams_and_checks(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    target.write_bytes(b"abc")
    digest = hashing.sha256_file(target, chunk=2)
    assert digest == hashing.sha256_bytes(b"abc")
    assert hashing.verify(target, digest.upper()) == digest
    with pytest.raises(hashing.HashMismatch) as caught:
        hashing.verify(target, "0" * 64)
    assert caught.value.found == digest
    with pytest.raises(ValueError):
        hashing.verify(target, "zz")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("MIT", "MIT"),
        ("Apache 2.0", "Apache-2.0"),
        ("bsd", "BSD-3-Clause"),
        ("CC-BY-4.0", "CC-BY-4.0"),
        ("CC0-1.0", "CC0-1.0"),
        ("ISC", "ISC"),
    ],
)
def test_allowed_licences(value: str, expected: str) -> None:
    assert licence.check(value) == expected
    assert licence.is_allowed(value)


@pytest.mark.parametrize(
    "value", ["", "unknown", "CC-BY-NC-4.0", "non-commercial", "GPL-3.0", "LGPL-2.1"]
)
def test_rejected_licences(value: str) -> None:
    with pytest.raises(licence.LicenceError):
        licence.check(value, release=False)
    assert not licence.is_allowed(value, release=False)


def test_release_flag_rejects_unknown_permissive_id() -> None:
    with pytest.raises(licence.LicenceError):
        licence.check("WTFPL", release=True)
    assert licence.check("WTFPL", release=False) == "wtfpl"
