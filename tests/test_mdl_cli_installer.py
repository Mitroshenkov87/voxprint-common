"""Installer integration: ``ensure``, ``--app-manifest``, and the JSON result shape."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.cli import main
from voxprint_common.runtime_manifest import load
from voxprint_common.suite_settings import set_keep_awake_during_jobs

PAYLOAD = b"voxprint-mdl-bytes-0123456789abcdef" * 4
DIGEST = sha256_bytes(PAYLOAD)


@pytest.fixture(autouse=True)
def _no_sleep_inhibit() -> None:
    set_keep_awake_during_jobs(False)


class _State:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.log: list[tuple[str, int, bool]] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return None

    def do_GET(self) -> None:
        state: _State = self.server.state  # type: ignore[attr-defined]
        start = _range_start(self.headers.get("Range"))
        state.log.append((self.path, start, self.headers.get("X-Voxprint-Probe") == "1"))
        body = state.payload[start:]
        code = 206 if start else 200
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _range_start(header: str | None) -> int:
    if not header or not header.startswith("bytes="):
        return 0
    start = header.split("=", 1)[1].split("-", 1)[0]
    return int(start) if start.isdigit() else 0


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
    model: str = "upscaler",
    group: str = "",
) -> Path:
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, object] = {
        "id": model,
        "version": "1",
        "vram_tier": "8gb",
        "files": [
            {
                "path": "weights.bin",
                "size": len(PAYLOAD),
                "sha256": DIGEST,
                "mirrors": [{"source": "url", "url": url}],
            }
        ],
    }
    if group:
        entry["group"] = group
    path.write_text(json.dumps({"schema": 1, "app": app, "models": [entry]}), encoding="utf-8")
    return path


def _result(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    out = capsys.readouterr().out
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert rows, out
    result = rows[-1]
    assert result["type"] == "result"
    return result


def _bodies(state: _State) -> list[tuple[str, int, bool]]:
    return [item for item in state.log if not item[2]]


def test_ensure_twice_second_run_downloads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _State(PAYLOAD)
    server, root = _serve(state)
    manifest = _manifest(tmp_path / "enlarge", f"{root}/file", app="enlarge")
    monkeypatch.setenv("VOXPRINT_MDL_TEST_GUARD", "loopback")
    try:
        code = main(["--json", "ensure", "--app-manifest", str(manifest)])
        first = _result(capsys)
        assert code == 0
        assert first["ok"] is True
        assert first["app"] == "enlarge"
        assert first["ready"] is True
        assert first["models"] == [
            {
                "id": "upscaler",
                "share_key": f"model-{DIGEST[:12]}",
                "state": "downloaded",
                "bytes": len(PAYLOAD),
            }
        ]
        assert first["safety_gaps"] == []
        assert len(_bodies(state)) == 1
        code = main(["--json", "ensure", "--app-manifest", str(manifest)])
        second = _result(capsys)
    finally:
        server.shutdown()
        server.server_close()
    assert code == 0
    assert second["ready"] is True
    assert second["models"][0]["state"] == "reused"
    assert len(_bodies(state)) == 1  # nothing was fetched again


def test_ensure_second_app_sharing_a_blob_reuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _State(PAYLOAD)
    server, root = _serve(state)
    enlarge = _manifest(tmp_path / "enlarge", f"{root}/file", app="enlarge")
    clarity = _manifest(tmp_path / "clarity", f"{root}/file", app="clarity")
    monkeypatch.setenv("VOXPRINT_MDL_TEST_GUARD", "loopback")
    try:
        assert main(["--json", "ensure", "--app-manifest", str(enlarge)]) == 0
        _result(capsys)
        assert len(_bodies(state)) == 1
        code = main(["--json", "ensure", "--app-manifest", str(clarity)])
        result = _result(capsys)
    finally:
        server.shutdown()
        server.server_close()
    assert code == 0
    assert result["app"] == "clarity"
    assert result["ready"] is True
    assert result["models"][0]["state"] == "reused"
    assert len(_bodies(state)) == 1  # the shared blob was not fetched again
    component = load().components[f"model-{DIGEST[:12]}"]
    assert sorted(component.refs) == ["clarity", "enlarge"]


def test_verify_json_result_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _State(PAYLOAD)
    server, root = _serve(state)
    manifest = _manifest(tmp_path / "enlarge", f"{root}/file", app="enlarge")
    monkeypatch.setenv("VOXPRINT_MDL_TEST_GUARD", "loopback")
    try:
        assert main(["--json", "ensure", "--app-manifest", str(manifest)]) == 0
        _result(capsys)
        code = main(["--json", "verify", "--app-manifest", str(manifest)])
        result = _result(capsys)
    finally:
        server.shutdown()
        server.server_close()
    assert code == 0
    assert result["ok"] is True
    assert result["app"] == "enlarge"
    assert result["ready"] is True
    assert result["models"] == [
        {
            "id": "upscaler",
            "share_key": f"model-{DIGEST[:12]}",
            "state": "ok",
            "bytes": len(PAYLOAD),
        }
    ]
    assert result["safety_gaps"] == []


def test_missing_safety_blob_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest(
        tmp_path / "canvas",
        "https://example.invalid/file",
        app="canvas",
        model="yunet-face",
        group="safety",
    )
    # A dry run never fetches; the fail-closed gate still blocks the app.
    code = main(["--json", "ensure", "--dry-run", "--app-manifest", str(manifest)])
    result = _result(capsys)
    # Documented choice: a non-empty safety_gaps list exits 2 with ready false.
    assert code == 2
    assert result["ok"] is False
    assert result["ready"] is False
    assert result["models"] == [
        {
            "id": "yunet-face",
            "share_key": f"model-{DIGEST[:12]}",
            "state": "missing",
            "bytes": len(PAYLOAD),
        }
    ]
    assert result["safety_gaps"] == [
        {"share_key": f"model-{DIGEST[:12]}", "model_id": "yunet-face", "state": "missing"}
    ]


def test_app_manifest_alias_matches_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest(tmp_path / "enlarge", "https://example.invalid/file", app="enlarge")
    code = main(["--json", "plan", "--app-manifest", str(manifest)])
    result = _result(capsys)
    assert code == 0
    assert result["ok"] is True
    code = main(["--json", "download", "--dry-run", "--app-manifest", str(manifest)])
    result = _result(capsys)
    assert code == 0
    assert result["ok"] is True


def test_app_mismatch_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = _manifest(tmp_path / "enlarge", "https://example.invalid/file", app="enlarge")
    code = main(["--json", "ensure", "--app-manifest", str(manifest), "--app", "clarity"])
    result = _result(capsys)
    assert code == 1
    assert result["ok"] is False
    assert result["code"] == 1
