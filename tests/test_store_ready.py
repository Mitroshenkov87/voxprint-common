"""A stale status file does not make a half-wiped store ready."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from voxprint_common.gpu_probe import GpuDevice
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl import ready
from voxprint_common.mdl.cli import main
from voxprint_common.mdl.job import download, plan
from voxprint_common.mdl.safety import UrlGuard
from voxprint_common.mdl.status import JobState, Status, save_status
from voxprint_common.paths import models_dir, shared_dir, state_dir
from voxprint_common.runtime_manifest import add_ref

GONE = b"g" * 32
SHORT = b"s" * 40
OK = b"o" * 24
BODIES = {"/gone.bin": GONE, "/short.bin": SHORT, "/ok.bin": OK}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return None

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        body = BODIES[path]
        self.server.paths.append(path)  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _gpu() -> GpuDevice:
    return GpuDevice(0, "Test GPU", "610.00", (8, 9), 8192)


def _serve() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.paths = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def _manifest(folder: Path, root: str) -> Path:
    files = []
    for name, body in (("gone.bin", GONE), ("short.bin", SHORT), ("ok.bin", OK)):
        files.append(
            {
                "path": name,
                "size": len(body),
                "sha256": sha256_bytes(body),
                "mirrors": [{"source": "url", "url": f"{root}/{name}"}],
            }
        )
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "app": "enlarge",
                "runtimes": ["runtime-py314-torch2.11-cu130"],
                "models": [
                    {"id": "pack", "version": "1", "vram_tier": "any", "files": files},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _install(folder: Path) -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    (state_dir() / "installed-apps.json").write_text(
        json.dumps({"schema": 1, "apps": [{"id": "enlarge", "path": str(folder)}]}),
        encoding="utf-8",
    )


def _place(name: str, body: bytes) -> Path:
    destination = models_dir() / "blobs" / sha256_bytes(body)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    return destination


def test_missing_and_truncated_files_are_not_ready(tmp_path: Path) -> None:
    server, root = _serve()
    folder = tmp_path / "enlarge"
    manifest = _manifest(folder, root)
    _install(folder)
    gone = _place("gone.bin", GONE)
    short = _place("short.bin", SHORT)
    _place("ok.bin", OK)
    runtime = shared_dir() / "runtime-py314-torch2.11-cu130"
    runtime.mkdir(parents=True)
    (runtime / "libtorch.so").write_bytes(b"t" * 100)
    add_ref(
        "runtime-py314-torch2.11-cu130",
        "enlarge",
        kind="runtime",
        version="1",
        path="runtime-py314-torch2.11-cu130",
        sha256="d" * 64,
        files=({"path": "libtorch.so", "size": 100},),
    )
    save_status(Status(JobState.READY, tier="8gb"))
    gone.unlink()
    short.write_bytes(b"s")
    (runtime / "libtorch.so").write_bytes(b"t")
    try:
        assert ready() is True
        assert ready("enlarge") is False
        outcome = plan([manifest], devices=[_gpu()], disk_free=lambda _path: 10**15)
        gaps = [step for step in outcome.status.steps if step.state != "verified"]
        assert [(step.state, step.model) for step in gaps] == [
            ("needs download", "pack/gone.bin"),
            ("needs repair", "pack/short.bin"),
        ]
        assert "ok.bin" not in outcome.message
        code = main(["status", "--app", "enlarge"])
        assert code == 0
        assert ready("enlarge") is False
        filled = download(
            [manifest],
            devices=[_gpu()],
            guard=UrlGuard.loopback(),
            priority=False,
            disk_free=lambda _path: 10**15,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert filled.code == 0
    assert gone.read_bytes() == GONE
    assert short.read_bytes() == SHORT
    requested = server.paths  # type: ignore[attr-defined]
    assert requested.count("/gone.bin") >= 1
    assert requested.count("/short.bin") >= 1
    assert "/ok.bin" not in requested
    assert ready("enlarge") is False
    (runtime / "libtorch.so").write_bytes(b"t" * 100)
    assert ready("enlarge") is True
