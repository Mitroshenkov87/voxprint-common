"""Signed model index and mirror policy. Fakes only; no network."""

from __future__ import annotations

import json
import logging
import urllib.parse
from pathlib import Path

import jsonschema
import pytest

from voxprint_common.gpu_probe import VramTier
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.catalog import load_manifest, plan_files
from voxprint_common.mdl.cli import main
from voxprint_common.mdl.descriptor import parse_descriptor, schema_bytes
from voxprint_common.mdl.ed25519 import public_key, sign, verify
from voxprint_common.mdl.fetch import FetchError, fetch_file
from voxprint_common.mdl.index import (
    bundled_index_bytes,
    load_index,
    private_key_pem,
    public_key_pem,
    verify_index,
)
from voxprint_common.mdl.safety import UrlGuard

PAYLOAD = b"voxprint-mdl-bytes-0123456789abcdef"
DIGEST = sha256_bytes(PAYLOAD)
HEAD = "d2325ed8de2eec808b22a88381bfb35055f84203"
INDEX_SHA = "8a98babc1133d540229273d0a51df43c2e85dca33c50c7affd435ee78ede25b9"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_SEED = bytes(range(32))


class _Reply:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.headers: dict[str, str] = {}
        self._body = body
        self._off = 0

    def read(self, size: int = -1) -> bytes:
        if self._off >= len(self._body):
            return b""
        if size is None or size < 0:
            size = len(self._body) - self._off
        chunk = self._body[self._off : self._off + size]
        self._off += len(chunk)
        return chunk

    def close(self) -> None:
        return None


class _Site:
    """Path to a status and a body. ``slow`` advances the shared clock."""

    def __init__(self) -> None:
        self.routes: dict[str, list[tuple[int, bytes]]] = {}
        self.calls: list[str] = []
        self.slow: set[str] = set()
        self.clock = [0.0]
        self.slept: list[float] = []

    def add(self, path: str, status: int, body: bytes) -> None:
        self.routes.setdefault(path, []).append((status, body))

    def __call__(self, request: object, timeout: float) -> _Reply:
        del timeout
        url = str(getattr(request, "full_url", ""))
        path = urllib.parse.urlparse(url).path
        self.calls.append(path)
        planned = self.routes[path]
        status, body = planned[0] if len(planned) == 1 else planned.pop(0)
        header = getattr(request, "headers", None)
        ranged = header.get("Range") if header is not None else None
        if status == 200 and ranged:
            start_text, _, end_text = str(ranged).removeprefix("bytes=").partition("-")
            start = int(start_text) if start_text else 0
            end = int(end_text) if end_text else len(body) - 1
            body = body[start : end + 1]
            status = 206
        if path in self.slow:
            self.clock[0] += 30.0
        return _Reply(status, body)

    def now(self) -> float:
        return self.clock[0]

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock[0] += seconds


def _rfc_vector() -> None:
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    pub = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert public_key(seed) == pub
    assert sign(b"", seed) == signature
    assert verify(b"", signature, pub)


def _index_body(*, serial: int, generated_at: str = "2026-10-18T00:00:00Z") -> bytes:
    document = {
        "head": "f" * 40,
        "serial": serial,
        "generated_at": generated_at,
        "models": [
            {
                "id": "painter",
                "repo": "org/painter",
                "revision": "abc",
                "sha256": "ab" * 32,
            }
        ],
    }
    return json.dumps(document).encode()


def _signed_site(body: bytes, signature: bytes | None) -> _Site:
    site = _Site()
    site.add("/index.json", 200, body)
    if signature is None:
        site.add("/index.json.sig", 404, b"")
    else:
        site.add("/index.json.sig", 200, signature)
    return site


def _load(site: _Site):
    return load_index(
        opener=site,
        url="http://127.0.0.1/index.json",
        guard=UrlGuard.loopback(),
        public_keys=(public_key(_SEED),),
    )


def test_bundled_index_is_the_d2325ed_snapshot() -> None:
    raw = bundled_index_bytes()
    assert sha256_bytes(raw) == INDEX_SHA
    index = load_index(remote=False)
    assert index.source == "bundled"
    assert index.fallback == ""
    assert index.head == HEAD
    assert index.serial == 1
    assert index.generated_at == "2026-10-17T10:42:07Z"
    assert len(index.entries) == 17
    assert index.get("gemma4-31b") is not None
    assert index.get("huihui-qwen36-27b") is not None


def test_signed_index_is_accepted(caplog: pytest.LogCaptureFixture) -> None:
    _rfc_vector()
    body = _index_body(serial=2)
    site = _signed_site(body, sign(body, _SEED))
    with caplog.at_level(logging.WARNING):
        index = _load(site)
    assert index.source == "remote"
    assert index.fallback == ""
    assert [entry.id for entry in index.entries] == ["painter"]
    assert index.serial == 2
    assert caplog.text == ""


def test_bad_or_missing_signature_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    body = _index_body(serial=2)
    missing = _signed_site(body, None)
    with caplog.at_level(logging.WARNING):
        unsigned = _load(missing)
    assert unsigned.source == "bundled"
    assert unsigned.fallback == "remote index signature is missing"
    assert "remote index signature is missing" in caplog.text
    assert len(unsigned.entries) == 17

    bad = bytearray(sign(body, _SEED))
    bad[-1] ^= 0x01
    site = _signed_site(body, bytes(bad))
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        forged = _load(site)
    assert forged.source == "bundled"
    assert forged.fallback == "remote index signature is invalid"
    assert "remote index signature is invalid" in caplog.text


def test_older_index_is_rejected(caplog: pytest.LogCaptureFixture) -> None:
    body = _index_body(serial=0)
    site = _signed_site(body, sign(body, _SEED))
    with caplog.at_level(logging.WARNING):
        index = _load(site)
    assert index.source == "bundled"
    assert "older than bundled pin" in index.fallback
    assert index.fallback in caplog.text

    stamped = _index_body(serial=2, generated_at="2020-01-01T00:00:00Z")
    # serial is present and newer, so the old timestamp does not win
    newer = _load(_signed_site(stamped, sign(stamped, _SEED)))
    assert newer.source == "remote"

    undated = {
        "generated_at": "2020-01-01T00:00:00Z",
        "models": [
            {"id": "painter", "repo": "org/painter", "revision": "abc", "sha256": "ab" * 32}
        ],
    }
    raw = json.dumps(undated).encode()
    old = _load(_signed_site(raw, sign(raw, _SEED)))
    assert old.source == "bundled"
    assert "generated_at" in old.fallback


def test_check_prints_the_fallback(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    index = load_index(remote=False)
    fallen = type(index)(
        entries=index.entries,
        source="bundled",
        sha256=index.sha256,
        head=index.head,
        serial=index.serial,
        generated_at=index.generated_at,
        fallback="remote index signature is missing",
    )
    monkeypatch.setattr("voxprint_common.mdl.cli.load_index", lambda: fallen)
    assert main(["--check"]) == 0
    err = capsys.readouterr().err
    assert "source: bundled" in err
    assert "fallback: remote index signature is missing" in err
    assert HEAD in err


def test_index_sign_and_verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = _index_body(serial=2)
    target = tmp_path / "index.json"
    target.write_bytes(body)
    private = tmp_path / "index-private.pem"
    private.write_text(private_key_pem(_SEED), encoding="utf-8")
    monkeypatch.setenv("VOXPRINT_MDL_INDEX_KEY_FILE", str(private))
    assert main(["index", "sign", str(target)]) == 0
    signature = target.with_name("index.json.sig")
    assert signature.is_file()
    public = tmp_path / "index-public.pem"
    public.write_text(public_key_pem(public_key(_SEED)), encoding="utf-8")
    assert main(["index", "verify", str(target), "--public-key", str(public)]) == 0
    target.write_bytes(body + b" ")
    assert main(["index", "verify", str(target), "--public-key", str(public)]) == 2
    assert verify_index(body, signature.read_bytes(), (public_key(_SEED),))


def test_strict_priority_falls_back_only_on_404_hash_or_outage(tmp_path: Path) -> None:
    guard = UrlGuard.loopback()

    slow = _Site()
    slow.add("/author", 200, PAYLOAD)
    slow.add("/backup", 200, PAYLOAD)
    slow.slow.add("/author")
    fetch_file(
        tmp_path / "slow.bin",
        ["http://127.0.0.1/author", "http://127.0.0.1/backup"],
        sha256=DIGEST,
        size=len(PAYLOAD),
        probe_bytes=4,
        batch_bytes=8,
        mirror_policy="strict-priority",
        opener=slow,
        clock=slow.now,
        sleep=slow.sleep,
        guard=guard,
        speed_floor=1,
    )
    assert "/backup" not in slow.calls
    assert (tmp_path / "slow.bin").read_bytes() == PAYLOAD

    missing = _Site()
    missing.add("/author", 404, b"")
    missing.add("/backup", 200, PAYLOAD)
    fetch_file(
        tmp_path / "missing.bin",
        ["http://127.0.0.1/author", "http://127.0.0.1/backup"],
        sha256=DIGEST,
        size=len(PAYLOAD),
        probe_bytes=0,
        batch_bytes=len(PAYLOAD),
        mirror_policy="strict-priority",
        opener=missing,
        clock=missing.now,
        sleep=missing.sleep,
        guard=guard,
        max_failures=5,
    )
    assert missing.calls.count("/author") == 1
    assert missing.slept == []
    assert (tmp_path / "missing.bin").read_bytes() == PAYLOAD

    mismatch = _Site()
    mismatch.add("/author", 200, b"x" * len(PAYLOAD))
    mismatch.add("/backup", 200, PAYLOAD)
    fetch_file(
        tmp_path / "mismatch.bin",
        ["http://127.0.0.1/author", "http://127.0.0.1/backup"],
        sha256=DIGEST,
        size=len(PAYLOAD),
        probe_bytes=0,
        batch_bytes=len(PAYLOAD),
        mirror_policy="strict-priority",
        opener=mismatch,
        clock=mismatch.now,
        sleep=mismatch.sleep,
        guard=guard,
    )
    assert mismatch.calls.count("/author") == 1
    assert (tmp_path / "mismatch.bin").read_bytes() == PAYLOAD

    outage = _Site()
    outage.add("/author", 500, b"")
    outage.add("/author", 500, b"")
    outage.add("/backup", 200, PAYLOAD)
    fetch_file(
        tmp_path / "outage.bin",
        ["http://127.0.0.1/author", "http://127.0.0.1/backup"],
        sha256=DIGEST,
        size=len(PAYLOAD),
        probe_bytes=0,
        batch_bytes=len(PAYLOAD),
        mirror_policy="strict-priority",
        opener=outage,
        clock=outage.now,
        sleep=outage.sleep,
        guard=guard,
        max_failures=2,
        backoff_base=0.01,
        backoff_max=0.01,
    )
    assert outage.calls.count("/author") == 2
    assert outage.slept
    assert (tmp_path / "outage.bin").read_bytes() == PAYLOAD

    denied = _Site()
    denied.add("/author", 403, b"")
    denied.add("/backup", 200, PAYLOAD)
    with pytest.raises(FetchError):
        fetch_file(
            tmp_path / "denied.bin",
            ["http://127.0.0.1/author", "http://127.0.0.1/backup"],
            sha256=DIGEST,
            size=len(PAYLOAD),
            probe_bytes=0,
            batch_bytes=len(PAYLOAD),
            mirror_policy="strict-priority",
            opener=denied,
            clock=denied.now,
            sleep=denied.sleep,
            guard=guard,
            max_failures=4,
        )
    assert "/backup" not in denied.calls


def test_race_skips_a_404_immediately(tmp_path: Path) -> None:
    site = _Site()
    site.add("/author", 404, b"")
    site.add("/backup", 200, PAYLOAD)
    fetch_file(
        tmp_path / "race.bin",
        ["http://127.0.0.1/author", "http://127.0.0.1/backup"],
        sha256=DIGEST,
        size=len(PAYLOAD),
        probe_bytes=0,
        batch_bytes=len(PAYLOAD),
        mirror_policy="race",
        opener=site,
        clock=site.now,
        sleep=site.sleep,
        guard=UrlGuard.loopback(),
        max_failures=5,
        backoff_base=2,
    )
    assert site.calls.count("/author") == 1
    assert site.slept == []
    assert (tmp_path / "race.bin").read_bytes() == PAYLOAD


def test_per_file_sources_are_honoured(tmp_path: Path) -> None:
    gemma = json.loads((_FIXTURES / "voxprint-gemma4-31b.json").read_text(encoding="utf-8"))
    qwen = json.loads((_FIXTURES / "voxprint-huihui-qwen36-27b.json").read_text(encoding="utf-8"))
    schema = json.loads(schema_bytes())
    jsonschema.Draft202012Validator(schema).validate(gemma)
    jsonschema.Draft202012Validator(schema).validate(qwen)
    author_first = parse_descriptor(qwen)
    assert author_first.mirrors[0].ref.startswith("huihui-ai/")
    assert author_first.mirrors[1].ref.startswith("Mitroshenkov87/")
    assert author_first.policy_for(author_first.files[0].path) == "race"

    ours = gemma["mirrors"][1]
    gemma["mirror_policy"] = "strict-priority"
    for row in gemma["files"]:
        if row["path"] == "IQ3_XXS.gguf":
            row["sources"] = [ours]
    gemma["file_groups"] = [{"paths": ["imatrix.gguf", "imatrix.gguf.sha256"], "sources": [ours]}]
    parsed = parse_descriptor(gemma)
    assert parsed.mirrors[0].ref.startswith("paperscarecrow/")
    assert parsed.policy_for("gemma-4-31b-abliterated-Q4_K_M.gguf") == "strict-priority"
    manifest_path = tmp_path / "voxprint-app-models.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "voxprint-app-models/1",
                "app_id": "plotweaver",
                "tiers": {"24gb": ["gemma4-31b"], "16gb": [], "8gb": []},
                "runtimes": [],
                "optional": [],
            }
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_path, descriptors={"gemma4-31b": parsed})
    planned = plan_files(
        [manifest],
        VramTier.GB24,
        only=["gemma4-31b"],
        memory_gib=23.5,
        models_root=tmp_path / "models",
    )
    by_name = {item.name: item for item in planned}
    shared = by_name["gemma-4-31b-abliterated-Q4_K_M.gguf"]
    assert "paperscarecrow" in shared.urls[0]
    assert "Mitroshenkov87" in shared.urls[1]
    assert shared.mirror_policy == "strict-priority"
    own_quant = by_name["IQ3_XXS.gguf"]
    assert len(own_quant.urls) == 1
    assert "Mitroshenkov87" in own_quant.urls[0]
    assert "paperscarecrow" not in own_quant.urls[0]
    for name in ("imatrix.gguf", "imatrix.gguf.sha256"):
        urls = by_name[name].urls
        assert len(urls) == 1
        assert "paperscarecrow" not in urls[0]
