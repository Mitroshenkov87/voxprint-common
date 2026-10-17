"""Queue, descriptor schema, URL guard, and the review fixes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files as pkg_files
from pathlib import Path

import jsonschema
import pytest

from voxprint_common.gpu_probe import GpuDevice, VramTier, nvidia_smi_executable, query_gpus
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.catalog import (
    CatalogError,
    is_gated_source,
    load_manifest,
    plan_files,
    public_mirror_urls,
)
from voxprint_common.mdl.cli import main
from voxprint_common.mdl.descriptor import (
    DescriptorError,
    parse_descriptor,
    parse_post_install,
    schema_bytes,
)
from voxprint_common.mdl.events import TransferSession
from voxprint_common.mdl.fetch import FetchError, _Mirror, _pick, fetch_file
from voxprint_common.mdl.hooks import _windows_flags
from voxprint_common.mdl.index import bundled_index_bytes, load_index, parse_index
from voxprint_common.mdl.job import download, progress_event, verify
from voxprint_common.mdl.queue import enqueue, ensure_ready, load_queue, queue_path, run_or_attach
from voxprint_common.mdl.safety import UnsafeUrl, UrlGuard
from voxprint_common.mdl.segments import SPEED_FLOOR_BPS, segment_count
from voxprint_common.mdl.status import load_status
from voxprint_common.paths import models_dir, state_dir
from voxprint_common.runtime_manifest import load, runtime_component_id
from voxprint_common.suite_settings import mdl_parallel_files, set_mdl_parallel_files

PAYLOAD = b"voxprint-mdl-bytes-0123456789abcdef" * 4
DIGEST = sha256_bytes(PAYLOAD)
_HELPER = Path(__file__).with_name("queue_child.py")


class _State:
    def __init__(self, payload: bytes, mode: str) -> None:
        self.payload = payload
        self.mode = mode
        self.dropped = False
        self.fast_bodies = 0
        self.slow_bodies = 0
        self.failures = 0
        self.release = threading.Event()
        self.log: list[tuple[str, int, bool]] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return None

    def do_GET(self) -> None:
        state: _State = self.server.state  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        probe = self.headers.get("X-Voxprint-Probe") == "1"
        start = _range_start(self.headers.get("Range"))
        state.log.append((path, start, probe))
        if path == "/go":
            self.send_response(302)
            self.send_header("Location", "https://evil.example/secret")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if state.mode == "flaky" and not probe and state.failures == 0:
            state.failures += 1
            body = b"no"
            self.send_response(500)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = _body(state, path, start, probe)
        ranged = self.headers.get("Range")
        if state.mode == "ranges" and ranged:
            code = 206
        elif state.mode == "ignore-range":
            code = 200
        else:
            code = 206 if start or probe else 200
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        self.wfile.write(body)


def _range_start(header: str | None) -> int:
    if not header or not header.startswith("bytes="):
        return 0
    start = header.split("=", 1)[1].split("-", 1)[0]
    return int(start) if start.isdigit() else 0


def _body(state: _State, path: str, start: int, probe: bool) -> bytes:
    payload = state.payload
    if state.mode == "crash" and not probe:
        if start == 0 and not state.dropped:
            state.dropped = True
            return payload[:16]
        if not state.release.is_set():
            state.release.wait(20)
        return payload[start:]
    if state.mode == "short" and not probe and not state.dropped:
        state.dropped = True
        return payload[start : start + 8]
    if state.mode == "slow":
        if probe and path == "/slow":
            time.sleep(1.0)
            return payload[start : start + 84 * 1024]
        if path == "/fast" and not probe:
            state.fast_bodies += 1
            if state.fast_bodies == 1:
                return payload[start : start + 1]
        if path == "/slow" and not probe:
            state.slow_bodies += 1
            if state.slow_bodies == 1:
                return payload[start : start + 8]
    if state.mode == "pause" and not probe and start == 0:
        time.sleep(0.4)
    return payload[start:]


def _serve(state: _State) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{server.server_address[0]}:{server.server_address[1]}"


def _manifest(folder: Path, url: str, *, app: str = "enlarge", model: str = "upscaler") -> Path:
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256_bytes(PAYLOAD)
    document = {
        "schema": 1,
        "app": app,
        "models": [
            {
                "id": model,
                "version": "1",
                "vram_tier": "8gb",
                "files": [
                    {
                        "path": "weights.bin",
                        "size": len(PAYLOAD),
                        "sha256": digest,
                        "mirrors": [{"source": "url", "url": url}],
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _gpu(mib: int, compute: tuple[int, int] = (8, 9)) -> GpuDevice:
    return GpuDevice(0, "Test GPU", "610.00", compute, mib)


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    root = Path(__file__).resolve().parents[1] / "src"
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    env["VOXPRINT_MDL_TEST_GUARD"] = "loopback"
    return env


def _descriptor_doc(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": "voxprint-model/1",
        "id": "painter",
        "display_name": "Painter",
        "app_ids": ["enlarge"],
        "task": "upscale",
        "license": "apache-2.0",
        "files": [
            {
                "path": "weights/model.bin",
                "size": 10,
                "sha256": "ab" * 32,
            }
        ],
        "mirrors": [{"kind": "hf", "ref": "Mitroshenkov87/painter", "revision": "main"}],
        "tier": {"min_vram_gib": 23.5, "recommended_vram_gib": 23.5, "gpu_min_cc": 8.0},
        "runtime": {"engine": "diffusers", "version_req": ">=0.30"},
        "base_model": "",
        "card_url": "https://huggingface.co/Mitroshenkov87/painter",
        "flags": ["least-censored"],
        "on_demand": False,
        "build": {"forge_version": "0.1.0", "tag": "", "date": "2026-10-11"},
    }
    document.update(overrides)
    return document


def test_vendored_schema_accepts_the_forge_shape() -> None:
    raw = schema_bytes()
    assert sha256_bytes(raw) == ("57a10f90d439e810629ddeb50902333aa1a593deb7974e5a8aee6390b7a3ee2b")
    schema = json.loads(raw)
    validator = jsonschema.Draft202012Validator(schema)
    document = _descriptor_doc()
    validator.validate(document)
    parsed = parse_descriptor(document)
    assert parsed.tier.min_vram_gib == 23.5
    assert parsed.mirrors[0].revision == "main"
    assert parsed.on_demand is False
    empty = _descriptor_doc()
    mirrors = [{"kind": "hf", "ref": "Mitroshenkov87/painter", "revision": ""}]
    empty["mirrors"] = mirrors
    validator.validate(empty)
    assert parse_descriptor(empty).mirrors[0].revision == ""
    small = _descriptor_doc()
    small["tier"] = {"min_vram_gib": 15.5, "recommended_vram_gib": 15.5, "gpu_min_cc": 6.0}
    validator.validate(small)
    bad = _descriptor_doc()
    bad["files"] = [{"path": "voxprint-model.json", "size": 1, "sha256": "ab" * 32}]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(bad)
    with pytest.raises(DescriptorError):
        parse_descriptor(bad)


def test_app_manifest_skips_optional_and_checks_the_card(tmp_path: Path) -> None:
    painter = parse_descriptor(_descriptor_doc())
    small = parse_descriptor(
        _descriptor_doc(
            id="painter-small",
            display_name="Painter small",
            on_demand=True,
            tier={"min_vram_gib": 15.5, "recommended_vram_gib": 15.5, "gpu_min_cc": 6.0},
        )
    )
    extra = parse_descriptor(_descriptor_doc(id="extra", display_name="Extra"))
    path = tmp_path / "voxprint-app-models.json"
    path.write_text(
        json.dumps(
            {
                "schema": "voxprint-app-models/1",
                "app_id": "enlarge",
                "tiers": {"24gb": ["painter"], "16gb": ["painter-small"], "8gb": []},
                "runtimes": [],
                "optional": [{"id": "extra", "min_vram_gib": 23.5}],
            }
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(
        path, descriptors={"painter": painter, "painter-small": small, "extra": extra}
    )
    card = plan_files([manifest], VramTier.GB24, memory_gib=23.5, models_root=tmp_path / "models")
    assert [item.model_id for item in card] == ["painter"]
    assert "weights/model.bin" in card[0].urls[0]
    assert "__" not in card[0].urls[0]
    assert card[0].urls[0].endswith("/resolve/main/weights/model.bin")
    skipped = plan_files(
        [manifest],
        VramTier.GB24,
        memory_gib=_gpu(24063).memory_gib,
        models_root=tmp_path / "models",
    )
    assert skipped == []
    with pytest.raises(CatalogError, match=r"23\.5"):
        plan_files([manifest], VramTier.GB8, only=["painter"], memory_gib=8, models_root=tmp_path)
    with pytest.raises(CatalogError, match="not available"):
        plan_files([manifest], VramTier.GB8, only=["missing"], memory_gib=8, models_root=tmp_path)
    demanded = plan_files(
        [manifest],
        VramTier.GB16,
        only=["painter-small"],
        memory_gib=15.5,
        models_root=tmp_path / "models",
    )
    assert demanded[0].model_id == "painter-small"
    missing = {
        "schema": 1,
        "app": "enlarge",
        "models": [
            {
                "id": "bare",
                "version": "1",
                "vram_tier": "8gb",
                "files": [
                    {
                        "path": "weights.bin",
                        "size": 1,
                        "sha256": "cd" * 32,
                        "mirrors": [
                            {"source": "huggingface", "repo": "org/name", "filename": "weights.bin"}
                        ],
                    }
                ],
            }
        ],
    }
    legacy = tmp_path / "mdl.json"
    legacy.write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(CatalogError, match="revision"):
        load_manifest(legacy)


def test_index_uses_the_remote_only_when_the_pin_matches() -> None:
    pinned = bundled_index_bytes()
    rows = json.loads(pinned)
    assert [row["id"] for row in rows] == [
        "pyannote-speaker-diarization-community-1",
        "pyannote-embedding",
    ]
    bundled = load_index(remote=False)
    assert bundled.source == "bundled"
    assert [entry.id for entry in bundled.entries] == [
        "pyannote-speaker-diarization-community-1",
        "pyannote-embedding",
    ]
    assert bundled.entries[0].revision == "9c851af7"
    assert bundled.entries[1].revision == "4655ca65"
    other = json.dumps(
        [{"id": "painter", "repo": "org/painter", "revision": "main", "sha256": "ab" * 32}]
    ).encode()
    server, root = _serve(_State(other, "index"))
    try:
        mismatched = load_index(url=f"{root}/index.json", guard=UrlGuard.loopback())
        assert mismatched.source == "bundled"
        matched_state = _State(pinned, "index")
        server.state = matched_state  # type: ignore[attr-defined]
        matched = load_index(url=f"{root}/index.json", guard=UrlGuard.loopback())
    finally:
        server.shutdown()
        server.server_close()
    assert matched.source == "remote"
    assert matched.sha256 == sha256_bytes(pinned)
    with pytest.raises(Exception, match="duplicate"):
        parse_index(
            [
                {"id": "painter", "repo": "org/painter", "revision": "main", "sha256": "ab" * 32},
                {"id": "painter", "repo": "org/painter", "revision": "abc", "sha256": "cd" * 32},
            ]
        )


def test_urls_stay_on_the_allowlist(tmp_path: Path) -> None:
    with pytest.raises(UnsafeUrl):
        UrlGuard().check("http://huggingface.co/org/model/resolve/main/file.bin")
    with pytest.raises(UnsafeUrl):
        UrlGuard(resolve=lambda _host: ["10.1.1.1"]).check("https://huggingface.co/file.bin")
    assert UrlGuard(resolve=lambda _host: ["1.1.1.1"]).check(
        "https://cdn-lfs.huggingface.co/file.bin"
    )
    server, root = _serve(_State(PAYLOAD, "redir"))
    try:
        with pytest.raises(FetchError):
            fetch_file(
                tmp_path / "weights.bin",
                [f"{root}/go"],
                sha256=DIGEST,
                size=len(PAYLOAD),
                probe_bytes=0,
                guard=UrlGuard.loopback(),
            )
    finally:
        server.shutdown()
        server.server_close()


def test_resume_survives_a_new_process(tmp_path: Path) -> None:
    state = _State(PAYLOAD, "short")
    server, root = _serve(state)
    destination = tmp_path / "weights.bin"
    try:
        with pytest.raises(FetchError):
            fetch_file(
                destination,
                [f"{root}/file"],
                sha256=DIGEST,
                size=len(PAYLOAD),
                probe_bytes=0,
                max_failures=1,
                guard=UrlGuard.loopback(),
            )
        partial = destination.with_name(destination.name + ".partial")
        assert partial.read_bytes() == PAYLOAD[:8]
        state.mode = "ok"
        fetch_file(
            destination,
            [f"{root}/file"],
            sha256=DIGEST,
            size=len(PAYLOAD),
            probe_bytes=0,
            guard=UrlGuard.loopback(),
        )
    finally:
        server.shutdown()
        server.server_close()
    assert destination.read_bytes() == PAYLOAD
    bodies = [item for item in state.log if not item[2]]
    assert bodies[-1][1] == 8


def test_backoff_uses_the_injected_clock(tmp_path: Path) -> None:
    server, root = _serve(_State(PAYLOAD, "flaky"))
    ticks = {"now": 0.0}
    slept: list[float] = []

    def clock() -> float:
        return ticks["now"]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        ticks["now"] += seconds

    try:
        fetch_file(
            tmp_path / "weights.bin",
            [f"{root}/file"],
            sha256=DIGEST,
            size=len(PAYLOAD),
            probe_bytes=0,
            backoff_base=0.5,
            backoff_max=0.5,
            clock=clock,
            sleep=sleep,
            guard=UrlGuard.loopback(),
        )
    finally:
        server.shutdown()
        server.server_close()
    assert sum(slept) >= 0.5


def test_slow_mirror_is_abandoned_and_the_partial_continues(tmp_path: Path) -> None:
    payload = b"q" * (90 * 1024)
    state = _State(payload, "slow")
    server, root = _serve(state)
    try:
        fetch_file(
            tmp_path / "weights.bin",
            [f"{root}/slow", f"{root}/fast"],
            sha256=sha256_bytes(payload),
            size=len(payload),
            probe_bytes=84 * 1024,
            batch_bytes=64 * 1024,
            backoff_base=0.2,
            backoff_max=0.2,
            guard=UrlGuard.loopback(),
        )
    finally:
        server.shutdown()
        server.server_close()
    bodies = [(path, start) for path, start, probe in state.log if not probe]
    fast = [index for index, (path, _start) in enumerate(bodies) if path == "/fast"]
    slow = [index for index, (path, _start) in enumerate(bodies) if path == "/slow"]
    assert bodies[0][0] == "/fast"
    assert slow
    assert max(fast) > min(slow)
    assert bodies[slow[0]][1] > 0
    assert bodies[max(fast)][1] > bodies[slow[0]][1]


def test_nvidia_smi_is_the_system32_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setattr("subprocess.CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert nvidia_smi_executable() == os.path.join("C:\\Windows", "System32", "nvidia-smi.exe")
    seen: dict[str, object] = {}

    def runner(command: list[str], **kwargs: object) -> object:
        seen["command"] = command
        seen["flags"] = kwargs.get("creationflags")

        class _Done:
            returncode = 0
            stdout = "Test GPU, 610.00, 8.9, 8192\n"

        return _Done()

    devices = query_gpus(runner=runner)  # type: ignore[arg-type]
    command = seen["command"]
    assert isinstance(command, list)
    assert str(command[0]).endswith("nvidia-smi.exe")
    assert seen["flags"] == 0x08000000
    assert devices[0].memory_mib == 8192


def test_parallel_files_default_and_bounds() -> None:
    assert mdl_parallel_files() == 2
    assert set_mdl_parallel_files(3) == 3
    assert mdl_parallel_files() == 3
    path = state_dir() / "suite.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["mdl_parallel_files"] = 9
    path.write_text(json.dumps(stored), encoding="utf-8")
    assert mdl_parallel_files() == 2


def test_two_processes_enqueue(tmp_path: Path) -> None:
    enlarge = _manifest(tmp_path / "enlarge", "https://example.invalid/file", app="enlarge")
    clarity = _manifest(tmp_path / "clarity", "https://example.invalid/file", app="clarity")
    first = subprocess.Popen(
        [sys.executable, str(_HELPER), "enqueue", "enlarge", str(enlarge)],
        env=_child_env(),
        stdout=subprocess.PIPE,
        text=True,
    )
    second = subprocess.Popen(
        [sys.executable, str(_HELPER), "enqueue", "clarity", str(clarity)],
        env=_child_env(),
        stdout=subprocess.PIPE,
        text=True,
    )
    assert first.wait(timeout=20) == 0
    assert second.wait(timeout=20) == 0
    jobs = load_queue()
    assert {job.app for job in jobs} == {"enlarge", "clarity"}
    raw = queue_path().read_bytes()
    assert raw.startswith(b"{")
    assert "\\u" not in raw.decode("utf-8")


def test_dedup_one_download_two_refs(tmp_path: Path) -> None:
    server, root = _serve(_State(PAYLOAD, "ok"))
    try:
        enlarge = _manifest(tmp_path / "enlarge", f"{root}/file", app="enlarge")
        clarity = _manifest(tmp_path / "clarity", f"{root}/file", app="clarity")
        enqueue("enlarge", manifests=[enlarge])
        enqueue("clarity", manifests=[clarity])
        outcome = run_or_attach(
            None,
            devices=[_gpu(8192)],
            guard=UrlGuard.loopback(),
            priority=False,
            fetch_options={"probe_bytes": 0, "batch_bytes": len(PAYLOAD)},
        )
    finally:
        server.shutdown()
        server.server_close()
    assert outcome.code == 0
    assert {job.state for job in load_queue()} == {"done"}
    document = load()
    component_id, component = next(iter(document.components.items()))
    assert component_id == f"model-{DIGEST[:12]}"
    assert sorted(component.refs) == ["clarity", "enlarge"]
    bodies = [item for item in server.state.log if not item[2]]  # type: ignore[attr-defined]
    assert len(bodies) == 1


def test_enqueue_while_the_worker_runs_appends(tmp_path: Path) -> None:
    state = _State(PAYLOAD, "pause")
    server, root = _serve(state)
    enlarge = _manifest(tmp_path / "enlarge", f"{root}/file", app="enlarge")
    clarity = _manifest(tmp_path / "clarity", f"{root}/file", app="clarity")
    enqueue("enlarge", manifests=[enlarge])
    box: dict[str, object] = {}

    def run() -> None:
        box["outcome"] = run_or_attach(
            None,
            devices=[_gpu(8192)],
            guard=UrlGuard.loopback(),
            priority=False,
            fetch_options={"probe_bytes": 0, "batch_bytes": len(PAYLOAD)},
        )

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not state.log:
        time.sleep(0.02)
    enqueue("clarity", manifests=[clarity])
    worker.join(timeout=20)
    server.shutdown()
    server.server_close()
    outcome = box["outcome"]
    assert getattr(outcome, "code", 1) == 0
    assert {job.app: job.state for job in load_queue()} == {"enlarge": "done", "clarity": "done"}
    document = load()
    component = next(iter(document.components.values()))
    assert sorted(component.refs) == ["clarity", "enlarge"]


def test_worker_crash_resumes_the_partial(tmp_path: Path) -> None:
    state = _State(PAYLOAD, "crash")
    server, root = _serve(state)
    manifest = _manifest(tmp_path / "enlarge", f"{root}/file")
    enqueue("enlarge", manifests=[manifest])
    child = subprocess.Popen(
        [sys.executable, str(_HELPER), "work"],
        env=_child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    partial = models_dir() / "blobs" / DIGEST
    partial = partial.with_name(partial.name + ".partial")
    deadline = time.monotonic() + 20
    try:
        while time.monotonic() < deadline:
            if partial.is_file() and partial.stat().st_size >= 16:
                break
            if child.poll() is not None:
                break
            time.sleep(0.05)
        ready = partial.is_file() and partial.stat().st_size >= 16
        assert ready, child.stderr.read() if child.poll() is not None else state.log
        child.kill()
        child.wait(timeout=10)
        state.release.set()
        outcome = run_or_attach(
            None,
            devices=[_gpu(8192)],
            guard=UrlGuard.loopback(),
            priority=False,
            fetch_options={"probe_bytes": 0, "batch_bytes": len(PAYLOAD)},
        )
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        server.shutdown()
        server.server_close()
    assert outcome.code == 0
    bodies = [item for item in state.log if not item[2]]
    assert any(start >= 16 for _path, start, _probe in bodies)


def test_second_process_attaches_until_the_lock_is_free(tmp_path: Path) -> None:
    server, root = _serve(_State(PAYLOAD, "ok"))
    manifest = _manifest(tmp_path / "enlarge", f"{root}/file")
    enqueue("enlarge", manifests=[manifest])
    ready = tmp_path / "held"
    release = tmp_path / "release"
    child = subprocess.Popen(
        [sys.executable, str(_HELPER), "hold", str(ready), str(release)],
        env=_child_env(),
    )
    box: dict[str, object] = {}

    def run() -> None:
        box["outcome"] = run_or_attach(
            None,
            devices=[_gpu(8192)],
            guard=UrlGuard.loopback(),
            priority=False,
            poll=0.05,
            fetch_options={"probe_bytes": 0},
        )

    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.02)
        assert ready.is_file()
        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.3)
        assert worker.is_alive()
        release.write_text("go", encoding="utf-8")
        worker.join(timeout=20)
    finally:
        release.write_text("go", encoding="utf-8")
        child.wait(timeout=10)
        server.shutdown()
        server.server_close()
    assert getattr(box["outcome"], "code", 1) == 0


def test_progress_groups_jobs_by_app_and_pause_resumes(tmp_path: Path) -> None:
    server, root = _serve(_State(PAYLOAD, "pause"))
    manifest = _manifest(tmp_path / "enlarge", f"{root}/file")
    enqueue("enlarge", manifests=[manifest])
    session = TransferSession()
    seen: list[object] = []
    paused = threading.Event()

    def listener(event: object) -> None:
        seen.append(event)
        state = getattr(event, "state", "")
        if state == "downloading" and not paused.is_set():
            session.pause()
            paused.set()

    session.subscribe(listener)

    def resume() -> None:
        assert paused.wait(5)
        time.sleep(0.1)
        session.resume()

    threading.Thread(target=resume, daemon=True).start()
    try:
        outcome = run_or_attach(
            None,
            devices=[_gpu(8192)],
            guard=UrlGuard.loopback(),
            priority=False,
            session=session,
            fetch_options={"probe_bytes": 0},
        )
    finally:
        server.shutdown()
        server.server_close()
    assert outcome.code == 0
    grouped = [event.jobs_by_app() for event in seen if hasattr(event, "jobs_by_app")]
    assert any("enlarge" in groups and groups["enlarge"] for groups in grouped)
    assert any(
        any(getattr(item, "state", "") == "paused" for item in getattr(event, "items", ()))
        for event in seen
    )


def test_ensure_ready_and_cli_enqueue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server, root = _serve(_State(PAYLOAD, "ok"))
    folder = tmp_path / "enlarge"
    manifest = _manifest(folder, f"{root}/file")
    state_dir().mkdir(parents=True, exist_ok=True)
    (state_dir() / "installed-apps.json").write_text(
        json.dumps({"schema": 1, "apps": [{"id": "enlarge", "path": str(folder)}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VOXPRINT_MDL_TEST_GUARD", "loopback")
    notes: list[str] = []
    try:
        code = main(["enqueue", "--app", "enlarge", "--manifest", str(manifest), "--json"])
        assert code == 0
        assert load_queue()[0].state == "queued"
        assert not queue_path().exists() or load_queue()
        dry = main(["download", "--manifest", str(manifest), "--dry-run"])
        assert dry == 0
        assert len(load_queue()) == 1
        ready = ensure_ready(
            "enlarge", progress_cb=lambda _fraction, message: notes.append(message)
        )
    finally:
        server.shutdown()
        server.server_close()
    assert ready.ok is True
    assert ready.kind == "ready"
    assert ready.app == "enlarge"
    assert notes
    with pytest.raises(CatalogError):
        enqueue("Missing App")


def test_download_command_enqueues_and_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server, root = _serve(_State(PAYLOAD, "ok"))
    manifest = _manifest(tmp_path / "enlarge", f"{root}/file")
    monkeypatch.setenv("VOXPRINT_MDL_TEST_GUARD", "loopback")
    try:
        code = main(["--json", "download", "--manifest", str(manifest)])
    finally:
        server.shutdown()
        server.server_close()
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    assert code == 0
    assert rows[-1]["ok"] is True
    assert load_queue()[0].state == "done"
    assert download is not None


_WORKER = """\
import pathlib
import sys
import time

if "--fail" in sys.argv:
    sys.stderr.write("secret-token-xyz\\n")
    raise SystemExit(1)
if "--sleep" in sys.argv:
    time.sleep(30)
model_dir = pathlib.Path(sys.argv[1])
model_dir.mkdir(parents=True, exist_ok=True)
counter = model_dir / "runs.txt"
previous = int(counter.read_text()) if counter.is_file() else 0
counter.write_text(str(previous + 1))
(model_dir / "model.onnx").write_bytes(b"fp16")
(model_dir / "id.txt").write_text(sys.argv[2])
(model_dir / "root.txt").write_text(sys.argv[3])
"""


def _launcher(folder: Path) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    worker = folder / "worker.py"
    worker.write_text(_WORKER, encoding="utf-8")
    python = sys.executable
    if os.name == "nt":
        name = "prepare.cmd"
        (folder / name).write_text(f'@"{python}" "{worker}" %*\r\n', encoding="utf-8")
        return name
    name = "prepare"
    path = folder / name
    path.write_text(f'#!/bin/sh\nexec "{python}" "{worker}" "$@"\n', encoding="utf-8")
    path.chmod(0o755)
    return name


def _hook_manifest(
    folder: Path,
    url: str,
    argv0: str,
    *,
    extra: list[str] | None = None,
    timeout: float = 30,
) -> Path:
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    argv = [argv0, "{model_dir}", "{id}", "{models_root}", *(extra or [])]
    document = {
        "schema": 1,
        "app": "enlarge",
        "models": [
            {
                "id": "upscaler",
                "version": "1",
                "vram_tier": "8gb",
                "post_install": {
                    "argv": argv,
                    "timeout_s": timeout,
                    "produces": ["model.onnx"],
                },
                "files": [
                    {
                        "path": "weights.bin",
                        "size": len(PAYLOAD),
                        "sha256": DIGEST,
                        "mirrors": [{"source": "url", "url": url}],
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _blob() -> Path:
    path = models_dir() / "blobs" / DIGEST
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_enqueue_skips_a_verified_blob_and_adds_the_ref(tmp_path: Path) -> None:
    blob = _blob()
    blob.write_bytes(PAYLOAD)
    blob.with_name(blob.name + ".verified").write_text(DIGEST + "\n", encoding="utf-8")
    enqueue("enlarge", manifests=[_manifest(tmp_path / "enlarge", "https://example.invalid/file")])
    job = load_queue()[0]
    assert job.state == "done"
    assert job.models == ()
    document = load()
    assert document.components[f"model-{DIGEST[:12]}"].refs == ["enlarge"]
    assert load_status().ready


def test_size_match_without_a_marker_hashes_once(tmp_path: Path) -> None:
    blob = _blob()
    blob.write_bytes(PAYLOAD)
    enqueue("enlarge", manifests=[_manifest(tmp_path / "enlarge", "https://example.invalid/file")])
    marker = blob.with_name(blob.name + ".verified")
    assert marker.read_text(encoding="utf-8").strip() == DIGEST
    assert load_queue()[0].state == "done"


def test_broken_bytes_stay_queued_and_the_partial_is_kept(tmp_path: Path) -> None:
    blob = _blob()
    blob.write_bytes(b"x" * len(PAYLOAD))
    partial = blob.with_name(blob.name + ".partial")
    partial.write_bytes(b"keep-me")
    enqueue("enlarge", manifests=[_manifest(tmp_path / "enlarge", "https://example.invalid/file")])
    assert blob.read_bytes() == b"x" * len(PAYLOAD)
    assert partial.read_bytes() == b"keep-me"
    assert load_queue()[0].state == "queued"


def test_enqueue_resumes_a_partial(tmp_path: Path) -> None:
    blob = _blob()
    partial = blob.with_name(blob.name + ".partial")
    partial.write_bytes(PAYLOAD[:8])
    state = _State(PAYLOAD, "ok")
    server, root = _serve(state)
    try:
        enqueue("enlarge", manifests=[_manifest(tmp_path / "enlarge", f"{root}/file")])
        assert partial.read_bytes() == PAYLOAD[:8]
        outcome = run_or_attach(
            None,
            devices=[_gpu(8192)],
            guard=UrlGuard.loopback(),
            priority=False,
            fetch_options={"probe_bytes": 0, "batch_bytes": len(PAYLOAD)},
        )
    finally:
        server.shutdown()
        server.server_close()
    assert outcome.code == 0
    starts = [start for _path, start, probe in state.log if not probe]
    assert starts[0] >= 8
    assert blob.read_bytes() == PAYLOAD


def test_gated_upstream_is_rejected_without_a_token(tmp_path: Path) -> None:
    assert os.environ.get("HF_TOKEN") is None
    assert os.environ.get("HUGGING_FACE_HUB_TOKEN") is None
    path = tmp_path / "mdl.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "app": "audiobook-builder",
                "models": [
                    {
                        "id": "pyannote-embedding",
                        "version": "1",
                        "vram_tier": "8gb",
                        "files": [
                            {
                                "path": "pytorch_model.bin",
                                "size": 10,
                                "sha256": "ab" * 32,
                                "mirrors": [
                                    {
                                        "source": "huggingface",
                                        "repo": "pyannote/embedding",
                                        "filename": "pytorch_model.bin",
                                        "revision": "4db48997",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="gated"):
        load_manifest(path)
    assert is_gated_source(
        "https://huggingface.co/pyannote/embedding/resolve/4db48997/pytorch_model.bin"
    )
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "app": "audiobook-builder",
                "models": [
                    {
                        "id": "pyannote-embedding",
                        "version": "1",
                        "vram_tier": "8gb",
                        "files": [
                            {
                                "path": "pytorch_model.bin",
                                "size": 10,
                                "sha256": "cd" * 32,
                                "mirrors": [
                                    {
                                        "source": "huggingface",
                                        "repo": "Mitroshenkov87/voxprint-pyannote-embedding",
                                        "filename": "pytorch_model.bin",
                                        "revision": "4655ca65",
                                    },
                                    {
                                        "source": "kaggle",
                                        "owner": "mitroshenkov87",
                                        "dataset": "voxprint-pyannote-embedding",
                                        "filename": "pytorch_model.bin",
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    parsed = load_manifest(path)
    urls = parsed.models[0].files[0].urls
    assert urls[0].startswith(
        "https://huggingface.co/Mitroshenkov87/voxprint-pyannote-embedding/resolve/4655ca65/"
    )
    assert "mitroshenkov87/voxprint-pyannote-embedding" in urls[1]
    assert "token" not in urls[0].lower()


def test_pyannote_index_points_at_the_public_mirrors() -> None:
    index = load_index(remote=False)
    for name in ("pyannote-embedding.json", "pyannote-speaker-diarization-community-1.json"):
        raw = pkg_files("voxprint_common.mdl.data").joinpath(f"examples/{name}").read_bytes()
        document = json.loads(raw)
        jsonschema.Draft202012Validator(json.loads(schema_bytes())).validate(document)
        parsed = parse_descriptor(document)
        entry = index.get(parsed.id)
        assert entry is not None
        assert entry.sha256 == sha256_bytes(raw)
        hf, kaggle = public_mirror_urls(entry.repo, entry.revision, parsed.files[0].path)
        assert hf.startswith(f"https://huggingface.co/{entry.repo}/resolve/{entry.revision}/")
        assert f"mitroshenkov87/{entry.repo.split('/')[-1]}" in kaggle
        assert "token" not in hf.lower()


def test_segments_resume_a_finished_part_and_ignore_range_falls_back(tmp_path: Path) -> None:
    payload = b"abcdefghij" * 100
    digest = sha256_bytes(payload)
    state = _State(payload, "ranges")
    server, root = _serve(state)
    destination = tmp_path / "weights.bin"
    partial = destination.with_name(destination.name + ".partial")
    parts = partial.with_name(partial.name + ".parts")
    parts.mkdir()
    (parts / "0000").write_bytes(payload[:250])
    try:
        fetch_file(
            destination,
            [f"{root}/file"],
            sha256=digest,
            size=len(payload),
            segments=4,
            segment_min=1,
            probe_bytes=0,
            guard=UrlGuard.loopback(),
        )
    finally:
        server.shutdown()
        server.server_close()
    starts = [start for _path, start, probe in state.log if not probe]
    assert 0 not in starts
    assert len(set(starts)) >= 3
    assert destination.read_bytes() == payload
    assert segment_count(256 * 1024 * 1024, 1, 2) == 1
    assert segment_count(256 * 1024 * 1024 + 1, 1, 2) == 8
    assert segment_count(256 * 1024 * 1024 + 1, 2, 2) == 4

    ignored = _State(payload, "ignore-range")
    server, root = _serve(ignored)
    other = tmp_path / "again.bin"
    try:
        fetch_file(
            other,
            [f"{root}/file"],
            sha256=digest,
            size=len(payload),
            segments=4,
            segment_min=1,
            probe_bytes=0,
            batch_bytes=len(payload),
            guard=UrlGuard.loopback(),
        )
    finally:
        server.shutdown()
        server.server_close()
    assert other.read_bytes() == payload
    assert not other.with_name(other.name + ".partial.parts").exists()


def test_speed_floor_switches_when_the_ratio_would_not() -> None:
    slow = _Mirror("https://example.invalid/slow", bps=80 * 1024)
    fast = _Mirror("https://example.invalid/fast", bps=100 * 1024)
    assert _pick([slow, fast], slow, 0.0, 100, SPEED_FLOOR_BPS) is fast
    assert _pick([slow, fast], slow, 0.0, 100, 0) is slow


def test_runtime_component_ids() -> None:
    assert runtime_component_id(3, 11, "nunchaku", "130") == "runtime-py311-nunchaku-cu130"
    assert runtime_component_id(3, 14, "torch2.11", "cu130") == "runtime-py314-torch2.11-cu130"


def test_unavailable_models_are_shown_and_not_downloaded(tmp_path: Path) -> None:
    path = tmp_path / "mdl.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "app": "enlarge",
                "models": [
                    {
                        "id": "soon",
                        "version": "1",
                        "vram_tier": "8gb",
                        "status": "unavailable",
                        "reason": "model_in_preparation",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="unavailable"):
        plan_files([load_manifest(path)], VramTier.GB8, only=["soon"], memory_gib=8)
    assert plan_files([load_manifest(path)], VramTier.GB8, memory_gib=8) == []
    enqueue("enlarge", manifests=[path], devices=[_gpu(8192)])
    assert load_queue()[0].state == "done"
    status = load_status()
    assert status.ready
    assert status.bytes_total == 0
    assert status.steps[0].state == "unavailable"
    assert status.steps[0].source == "model_in_preparation"
    assert status.steps[0].size == 0
    event = progress_event(status)
    assert event.items[0].reason == "model_in_preparation"
    assert event.items[0].state == "unavailable"


def test_post_install_hook_runs_once_and_a_failure_stays_friendly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "enlarge"
    name = _launcher(folder)
    server, root = _serve(_State(PAYLOAD, "ok"))
    manifest = _hook_manifest(folder, f"{root}/file", name)
    try:
        enqueue("enlarge", manifests=[manifest], devices=[_gpu(8192)])
        outcome = run_or_attach(
            None,
            devices=[_gpu(8192)],
            guard=UrlGuard.loopback(),
            priority=False,
            fetch_options={"probe_bytes": 0, "batch_bytes": len(PAYLOAD)},
        )
        assert outcome.code == 0
        produced = models_dir() / "enlarge" / "upscaler"
        assert (produced / "model.onnx").read_bytes() == b"fp16"
        assert (produced / "id.txt").read_text(encoding="utf-8") == "upscaler"
        assert (produced / "root.txt").read_text(encoding="utf-8") == str(models_dir())
        assert (produced / "runs.txt").read_text(encoding="utf-8") == "1"
        assert load_status().ready
        enqueue("enlarge", manifests=[manifest], devices=[_gpu(8192)])
        again = verify([manifest], devices=[_gpu(8192)])
    finally:
        server.shutdown()
        server.server_close()
    assert again.code == 0
    assert (produced / "runs.txt").read_text(encoding="utf-8") == "1"
    marker = state_dir() / "mdl-hooks" / "enlarge" / "upscaler" / DIGEST
    marker.unlink()
    repaired = verify([manifest], devices=[_gpu(8192)])
    assert repaired.code == 0
    assert (produced / "runs.txt").read_text(encoding="utf-8") == "2"
    marker.unlink()
    (produced / "model.onnx").unlink()

    failed_dir = tmp_path / "enlarge-fail"
    failed_name = _launcher(failed_dir)
    server, root = _serve(_State(PAYLOAD, "ok"))
    failed_manifest = _hook_manifest(failed_dir, f"{root}/file", failed_name, extra=["--fail"])
    try:
        enqueue("enlarge", manifests=[failed_manifest], devices=[_gpu(8192)])
        failed = run_or_attach(
            None,
            devices=[_gpu(8192)],
            guard=UrlGuard.loopback(),
            priority=False,
            fetch_options={"probe_bytes": 0, "batch_bytes": len(PAYLOAD)},
        )
    finally:
        server.shutdown()
        server.server_close()
    assert failed.code == 2
    assert "secret-token-xyz" not in failed.message
    assert "Could not finish preparing" in failed.message
    assert load_status().ready is False
    assert any(step.state == "error" for step in load_status().steps)
    assert "secret-token-xyz" in (state_dir() / "mdl-hook.log").read_text(encoding="utf-8")

    missing = tmp_path / "enlarge-missing"
    missing.mkdir()
    planted = tmp_path / "on-path"
    planted.mkdir()
    trap = planted / "voxprint-enlarge-cli"
    flag = tmp_path / "path-ran"
    trap.write_text(f"#!/bin/sh\ntouch {flag}\n", encoding="utf-8")
    trap.chmod(0o755)
    monkeypatch.setenv("PATH", f"{planted}{os.pathsep}{os.environ.get('PATH', '')}")
    bare = _hook_manifest(missing, "https://example.invalid/file", "voxprint-enlarge-cli")
    blob = _blob()
    blob.write_bytes(PAYLOAD)
    refused = verify([bare], devices=[_gpu(8192)])
    assert refused.code == 2
    assert "Could not find voxprint-enlarge-cli" in refused.message
    assert not flag.exists()
    with pytest.raises(DescriptorError, match="inside"):
        parse_post_install(
            {"argv": ["prepare"], "timeout_s": 1, "produces": ["../model.onnx"]},
            "post_install",
        )


def test_hook_uses_create_no_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert _windows_flags("nt") == {"creationflags": 0x08000000}
    monkeypatch.setattr(
        "voxprint_common.mdl.hooks._windows_flags",
        lambda platform=None: {"creationflags": 0x08000000},
    )
    folder = tmp_path / "enlarge"
    folder.mkdir()
    (folder / "prepare.cmd").write_bytes(b"")
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen["flags"] = kwargs.get("creationflags")
        model_dir = Path(argv[1])
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "model.onnx").write_bytes(b"fp16")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    manifest = _hook_manifest(folder, "https://example.invalid/file", "prepare.cmd")
    blob = _blob()
    blob.write_bytes(PAYLOAD)
    outcome = verify([manifest], devices=[_gpu(8192)])
    assert outcome.code == 0
    assert seen["flags"] == 0x08000000
