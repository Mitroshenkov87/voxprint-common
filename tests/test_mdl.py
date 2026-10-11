"""VOXPrint MDL plan, resume, mirror failover, and the status file."""

from __future__ import annotations

import json
import math
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from voxprint_common.gpu_probe import GpuDevice, VramTier
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl import CLI_NAME, DISPLAY_NAME, discover, load_status, ready
from voxprint_common.mdl.catalog import (
    SUITE_APPS,
    ModelTier,
    gdrive_url,
    install_dir,
    kaggle_url,
    plan_files,
)
from voxprint_common.mdl.catalog import load_manifest as load_app_manifest
from voxprint_common.mdl.cli import main
from voxprint_common.mdl.fetch import Cancelled, FetchError, confirm_token, fetch_file
from voxprint_common.mdl.job import detect_tier, download
from voxprint_common.mdl.progress_ui import CANCEL_TEXT, RETRY_TEXT, format_eta, window_title
from voxprint_common.mdl.status import JobState, Status, save_status, status_path
from voxprint_common.paths import state_dir
from voxprint_common.runtime_manifest import load
from voxprint_common.suite_settings import set_keep_awake_during_jobs

PAYLOAD = b"voxprint-mdl-bytes-0123456789abcdef" * 4
DIGEST = sha256_bytes(PAYLOAD)


@pytest.fixture(autouse=True)
def _no_sleep_inhibit() -> None:
    set_keep_awake_during_jobs(False)


class _State:
    def __init__(self, payload: bytes, mode: str) -> None:
        self.payload = payload
        self.mode = mode
        self.dropped = False
        self.a_bodies = 0
        self.log: list[tuple[str, int, bool]] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return None

    def do_GET(self) -> None:
        state: _State = self.server.state  # type: ignore[attr-defined]
        start = _range_start(self.headers.get("Range"))
        probe = self.headers.get("X-Voxprint-Probe") == "1"
        path = self.path.split("?", 1)[0]
        state.log.append((path, start, probe))
        if path == "/b":
            time.sleep(0.25)
        body = _body(state, path, start, probe)
        code = 206 if start else 200
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
    if state.mode == "bad":
        return b"x" * len(state.payload)
    if state.mode == "drop" and path == "/drop" and not probe and start == 0 and not state.dropped:
        state.dropped = True
        return state.payload[:4]
    if state.mode == "switch" and path == "/a" and not probe:
        state.a_bodies += 1
        if state.a_bodies == 1:
            return state.payload[start : start + 1]
    return state.payload[start:]


def _serve(state: _State) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def _manifest(
    folder: Path,
    url: str,
    *,
    app: str = "enlarge",
    tier: str = "8gb",
    model: str = "upscaler",
    payload: bytes = PAYLOAD,
    name: str = "mdl.json",
) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": 1,
        "app": app,
        "models": [
            {
                "id": model,
                "version": "1",
                "vram_tier": tier,
                "files": [
                    {
                        "path": "weights.bin",
                        "size": len(payload),
                        "sha256": sha256_bytes(payload),
                        "mirrors": [{"source": "url", "url": url}],
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _gpu(mib: int) -> GpuDevice:
    return GpuDevice(0, "Test GPU", "610.00", (8, 9), mib)


def test_display_name_is_one_constant() -> None:
    root = Path("src")
    hits = [
        path for path in root.rglob("*.py") if "VOXPrint MDL" in path.read_text(encoding="utf-8")
    ]
    assert hits == [Path("src/voxprint_common/mdl/names.py")]
    assert DISPLAY_NAME == "VOXPrint MDL"
    assert CLI_NAME == "voxprint-mdl"
    assert window_title() == DISPLAY_NAME
    assert "PySide6" not in sys.modules
    assert RETRY_TEXT == "Повторить"
    assert CANCEL_TEXT == "Отмена"


def test_install_dirs_and_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    enlarge = next(app for app in SUITE_APPS if app.id == "enlarge")
    windows = install_dir(enlarge, Path(r"C:\Program Files\VOXPrint AI Suite"), platform="win32")
    assert windows.name == "Enlarge"
    linux = install_dir(enlarge, Path("/opt/voxprint-ai-suite"), platform="linux")
    assert linux.as_posix() == "/opt/voxprint-ai-suite/enlarge"
    monkeypatch.setenv("VOXPRINT_SUITE_ROOT", str(tmp_path))
    _manifest(tmp_path / "enlarge", "https://example.invalid/weights.bin")
    found = discover()
    assert [app.id for app in found] == ["enlarge"]
    registry = tmp_path / "registry"
    app_dir = registry / "clarity"
    _manifest(app_dir, "https://example.invalid/weights.bin", app="clarity")
    state_dir().mkdir(parents=True, exist_ok=True)
    (state_dir() / "installed-apps.json").write_text(
        json.dumps({"schema": 1, "apps": [{"id": "clarity", "path": str(app_dir)}]}),
        encoding="utf-8",
    )
    assert [app.id for app in discover()] == ["clarity"]


def test_plan_keeps_only_the_fitting_tier(tmp_path: Path) -> None:
    folder = tmp_path / "app"
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True)
    digest_small = "a" * 64
    digest_large = "b" * 64
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "app": "enlarge",
                "models": [
                    _model("upscaler", "8gb", digest_small, 10),
                    _model("upscaler", "16gb", "c" * 64, 20),
                    _model("upscaler", "24gb", digest_large, 30),
                    _model("tokenizer", "any", "d" * 64, 5),
                ],
            }
        ),
        encoding="utf-8",
    )
    parsed = load_app_manifest(path)
    small = plan_files([parsed], VramTier.GB8, models_root=tmp_path / "models")
    assert {item.tier for item in small} == {"8gb", "any"}
    card_24 = plan_files([parsed], detect_tier([_gpu(24064)]), models_root=tmp_path / "models")
    assert {item.sha256 for item in card_24} == {digest_large, "d" * 64}
    card_16 = plan_files([parsed], detect_tier([_gpu(24063)]), models_root=tmp_path / "models")
    assert "b" * 64 not in {item.sha256 for item in card_16}
    assert ModelTier.GB24.value == "24gb"


def test_disk_short_fails_before_any_request(tmp_path: Path) -> None:
    server, root = _serve(_State(PAYLOAD, "ok"))
    try:
        manifest = _manifest(tmp_path / "app", f"{root}/file")
        outcome = download(
            [manifest],
            devices=[_gpu(8192)],
            disk_free=lambda _path: 10,
            priority=False,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert outcome.code == 2
    assert str(math.ceil(len(PAYLOAD) * 1.15)) in outcome.message
    assert "15%" in outcome.message
    assert "10 bytes free" in outcome.message
    assert server.state.log == []  # type: ignore[attr-defined]
    assert not ready()


def test_resume_after_mid_transfer_drop(tmp_path: Path) -> None:
    server, root = _serve(_State(PAYLOAD, "drop"))
    try:
        destination = tmp_path / "weights.bin"
        fetch_file(
            destination,
            [f"{root}/drop"],
            sha256=DIGEST,
            size=len(PAYLOAD),
            probe_bytes=8,
            batch_bytes=len(PAYLOAD),
            backoff_base=0.01,
            backoff_max=0.05,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert destination.read_bytes() == PAYLOAD
    bodies = [item for item in server.state.log if not item[2]]  # type: ignore[attr-defined]
    assert bodies[0][1] == 0
    assert any(item[1] > 0 for item in bodies[1:])


def test_mirror_failover_and_switch_back(tmp_path: Path) -> None:
    server, root = _serve(_State(PAYLOAD, "switch"))
    try:
        destination = tmp_path / "weights.bin"
        fetch_file(
            destination,
            [f"{root}/a", f"{root}/b"],
            sha256=DIGEST,
            size=len(PAYLOAD),
            probe_bytes=8,
            batch_bytes=10,
            backoff_base=0.05,
            backoff_max=0.05,
            switch_ratio=1.25,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert destination.read_bytes() == PAYLOAD
    bodies = [(path, start) for path, start, probe in server.state.log if not probe]  # type: ignore[attr-defined]
    b_at = [index for index, (path, _start) in enumerate(bodies) if path == "/b"]
    a_at = [index for index, (path, _start) in enumerate(bodies) if path == "/a"]
    assert b_at
    assert a_at
    assert max(a_at) > min(b_at)


def test_sha256_mismatch_removes_the_partial(tmp_path: Path) -> None:
    server, root = _serve(_State(PAYLOAD, "bad"))
    try:
        destination = tmp_path / "weights.bin"
        with pytest.raises(FetchError):
            fetch_file(
                destination,
                [f"{root}/bad"],
                sha256=DIGEST,
                size=len(PAYLOAD),
                probe_bytes=0,
                max_failures=1,
            )
    finally:
        server.shutdown()
        server.server_close()
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".partial").exists()


def test_shared_file_is_referenced_by_each_app(tmp_path: Path) -> None:
    server, root = _serve(_State(PAYLOAD, "ok"))
    try:
        enlarge = _manifest(tmp_path / "enlarge", f"{root}/file", app="enlarge")
        clarity = _manifest(tmp_path / "clarity", f"{root}/file", app="clarity")
        outcome = download(
            [enlarge, clarity],
            devices=[_gpu(8192)],
            priority=False,
            fetch_options={"probe_bytes": 0, "batch_bytes": len(PAYLOAD)},
        )
    finally:
        server.shutdown()
        server.server_close()
    assert outcome.code == 0
    assert outcome.status.ready is True
    assert ready()
    text = load_status().to_dict()
    assert text["tool"] == DISPLAY_NAME
    stored = json.loads(status_path().read_text(encoding="utf-8"))
    assert stored["ready"] is True
    document = load()
    component = next(iter(document.components.values()))
    assert component.kind == "model"
    assert sorted(component.refs) == ["clarity", "enlarge"]
    bodies = [item for item in server.state.log if not item[2] and item[0] == "/file"]  # type: ignore[attr-defined]
    assert len(bodies) == 1


def test_status_file_is_utf8() -> None:
    status = Status(JobState.FAILED, error="Ошибка: диск заполнен")
    save_status(status)
    raw = status_path().read_bytes()
    assert "Ошибка: диск заполнен".encode() in raw
    assert b"\\u" not in raw
    assert load_status().error == "Ошибка: диск заполнен"
    assert format_eta(90) == "1 мин 30 с"


def test_cli_json_and_usage(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 1
    server, root = _serve(_State(PAYLOAD, "ok"))
    try:
        manifest = _manifest(tmp_path / "app", f"{root}/file")
        code = main(
            ["download", "--manifest", str(manifest), "--json", "--dry-run"],
        )
    finally:
        server.shutdown()
        server.server_close()
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    assert code == 0
    assert rows[-1]["type"] == "result"
    assert rows[-1]["ok"] is True
    assert server.state.log == []  # type: ignore[attr-defined]


def test_cancel_before_download(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "app", "https://example.invalid/file")
    event = threading.Event()
    event.set()
    outcome = download([manifest], devices=[_gpu(8192)], cancel=event, priority=False)
    assert outcome.code == 3
    assert isinstance(Cancelled("cancelled"), Cancelled)


def test_xet_is_used_when_a_hugging_face_mirror_offers_it(tmp_path: Path) -> None:
    destination = tmp_path / "weights.bin"
    calls: list[str] = []

    class _Module:
        @staticmethod
        def PyXetDownloadInfo(path: str, digest: str, size: int) -> tuple[str, str, int]:
            return path, digest, size

        @staticmethod
        def download_files(
            files: list[tuple[str, str, int]],
            endpoint: str | None = None,
            progress_updater: list[object] | None = None,
        ) -> None:
            del endpoint, progress_updater
            calls.append("xet")
            Path(files[0][0]).write_bytes(PAYLOAD)

    class _Head:
        def __init__(self) -> None:
            self.status = 200
            self.headers = {"X-Xet-Hash": "abc123", "X-Xet-Endpoint": "https://cas.example"}

        def close(self) -> None:
            return None

    def opener(request: object, timeout: float) -> _Head:
        del timeout
        method = getattr(request, "get_method", lambda: "GET")()
        if method != "HEAD":
            raise AssertionError(method)
        return _Head()

    fetch_file(
        destination,
        ["https://huggingface.co/Mitroshenkov87/models/resolve/main/weights.bin"],
        sha256=DIGEST,
        size=len(PAYLOAD),
        opener=opener,
        xet_module=_Module(),
        probe_bytes=0,
    )
    assert destination.read_bytes() == PAYLOAD
    assert calls == ["xet"]


def test_mirror_url_builders() -> None:
    assert kaggle_url("mitroshenkov87", "voxprint-models", "weights.bin").startswith(
        "https://www.kaggle.com/api/v1/datasets/download/mitroshenkov87/"
    )
    assert gdrive_url("1AbCdEfGhIjKlMnOpQrStUvWxYz012345").startswith("https://drive.google.com/")
    assert confirm_token('<a href="/uc?confirm=t0ken&id=1">') == "t0ken"


def _model(model_id: str, tier: str, digest: str, size: int) -> dict[str, object]:
    return {
        "id": model_id,
        "version": "1",
        "vram_tier": tier,
        "files": [
            {
                "path": "weights.bin",
                "size": size,
                "sha256": digest,
                "mirrors": [{"source": "url", "url": "https://example.invalid/weights.bin"}],
            }
        ],
    }
