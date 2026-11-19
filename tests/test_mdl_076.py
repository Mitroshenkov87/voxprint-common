"""0.7.6: named above-tier models, scoped failures, window env, worker --gui, warm ETA, labels."""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from voxprint_common.atomic import write_json
from voxprint_common.cli_protocol import EXIT_FAILED, EXIT_OK
from voxprint_common.gpu_probe import GpuDevice, VramTier
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl import cli as mdl_cli
from voxprint_common.mdl.catalog import (
    AppManifest,
    CatalogError,
    Model,
    ModelFile,
    ModelTier,
    load_manifest,
    plan_files,
)
from voxprint_common.mdl.eta import EtaSmoother
from voxprint_common.mdl.instance import _write_info, instance_info_path, mark_authenticated
from voxprint_common.mdl.job import download, plan
from voxprint_common.mdl.queue import QueueJob, queue_path
from voxprint_common.mdl.safety import UrlGuard
from voxprint_common.mdl.status import JobState, Status, Step, load_status, save_status
from voxprint_common.ui import mdl_window
from voxprint_common.ui.mdl_window import WindowResult

PAYLOAD = b"voxprint-076-bytes"
DIGEST = sha256_bytes(PAYLOAD)


def _entry(
    model_id: str,
    *,
    tier: str = "any",
    min_vram_gb: float | None = None,
    digest: str = DIGEST,
    url: str = "https://huggingface.co/org/w.bin",
) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": model_id,
        "version": "1",
        "vram_tier": tier,
        "files": [
            {
                "path": f"{model_id}.bin",
                "size": len(PAYLOAD),
                "sha256": digest,
                "mirrors": [{"source": "url", "url": url}],
            }
        ],
    }
    if min_vram_gb is not None:
        entry["min_vram_gb"] = min_vram_gb
    return entry


def _manifest(folder: Path, app: str, entries: list[dict[str, object]]) -> Path:
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "app": app, "models": entries}), encoding="utf-8")
    return path


def _gpu(mib: int) -> GpuDevice:
    return GpuDevice(0, "Test GPU", "610.00", (8, 9), mib)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return None

    def do_GET(self) -> None:
        payload: bytes = self.server.payload  # type: ignore[attr-defined]
        start = _range_start(self.headers.get("Range"))
        body = payload[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        self.wfile.write(body)


def _range_start(header: str | None) -> int:
    if not header or not header.startswith("bytes="):
        return 0
    start = header.split("=", 1)[1].split("-", 1)[0]
    return int(start) if start.isdigit() else 0


def _serve() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.payload = PAYLOAD  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakePrimary:
    """Stands in for the instance lock holder: no messages, no-op close."""

    def __init__(self) -> None:
        self._messages: list[dict[str, object]] = []

    def poll(self) -> list[dict[str, object]]:
        messages, self._messages = self._messages, []
        return messages

    def close(self) -> None:
        return None


class _FakeSnapshot:
    items: tuple[object, ...] = ()
    paused = False


class _FakeController:
    def snapshot(self) -> _FakeSnapshot:
        return _FakeSnapshot()

    def start(self, display: bool = True) -> None:
        return None

    def stop(self) -> None:
        return None


def test_named_model_above_the_card_tier_warns_and_plans(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manifest = load_manifest(
        _manifest(tmp_path / "canvas", "canvas", [_entry("z-image", tier="24gb", min_vram_gb=24)])
    )
    skipped = plan_files(
        [manifest], VramTier.GB16, memory_gib=15.5, models_root=tmp_path / "models"
    )
    assert skipped == []
    with caplog.at_level(logging.WARNING, logger="voxprint_common.mdl.catalog"):
        named = plan_files(
            [manifest],
            VramTier.GB16,
            only=["z-image"],
            memory_gib=15.5,
            models_root=tmp_path / "models",
        )
        every = plan_files(
            [manifest],
            VramTier.GB16,
            memory_gib=15.5,
            models_root=tmp_path / "models",
            all_tiers=True,
        )
    assert [item.model_id for item in named] == ["z-image"]
    assert [item.model_id for item in every] == ["z-image"]
    warnings = [record.getMessage() for record in caplog.records]
    assert any("z-image" in text and "GiB" in text for text in warnings)
    with pytest.raises(CatalogError, match="not available for this card"):
        plan_files(
            [manifest],
            VramTier.GB16,
            only=["ghost"],
            memory_gib=15.5,
            models_root=tmp_path / "models",
        )


def test_failed_plan_keeps_a_ready_status_file(tmp_path: Path) -> None:
    server, root = _serve()
    try:
        manifest = _manifest(tmp_path / "app", "enlarge", [_entry("upscaler", url=f"{root}/file")])
        outcome = download(
            [manifest], devices=[_gpu(8192)], priority=False, guard=UrlGuard.loopback()
        )
        assert outcome.code == EXIT_OK
        assert load_status().ready
        failed = plan([manifest], devices=[_gpu(8192)], only=["ghost"])
    finally:
        server.shutdown()
        server.server_close()
    assert failed.code == EXIT_FAILED
    assert "not available" in failed.message
    stored = load_status()
    assert stored.ready
    assert stored.error == ""
    assert stored.extra.get("phase") == "ready"
    assert [step.state for step in stored.steps] == ["verified"]


def test_failed_download_after_preparing_keeps_a_ready_status_file(tmp_path: Path) -> None:
    server, root = _serve()
    try:
        manifest = _manifest(tmp_path / "app", "enlarge", [_entry("upscaler", url=f"{root}/file")])
        outcome = download(
            [manifest], devices=[_gpu(8192)], priority=False, guard=UrlGuard.loopback()
        )
        assert outcome.code == EXIT_OK
        failed = download(
            [manifest],
            devices=[_gpu(8192)],
            only=["ghost"],
            priority=False,
            guard=UrlGuard.loopback(),
        )
    finally:
        server.shutdown()
        server.server_close()
    assert failed.code == EXIT_FAILED
    stored = load_status()
    assert stored.ready
    assert stored.error == ""
    assert stored.extra.get("phase") == "ready"
    assert [step.state for step in stored.steps] == ["verified"]


def test_planning_error_keeps_mixed_verified_status(tmp_path: Path) -> None:
    save_status(
        Status(
            state=JobState.READY,
            error="",
            extra={"phase": "ready"},
            steps=[
                Step(
                    "upscaler",
                    len(PAYLOAD),
                    "",
                    0.0,
                    None,
                    "verified",
                    len(PAYLOAD),
                    DIGEST,
                    ["enlarge"],
                ),
                Step("other", len(PAYLOAD), "", 0.0, None, "failed", 0, "cd" * 32, ["enlarge"]),
            ],
        )
    )
    manifest = _manifest(tmp_path / "app", "enlarge", [_entry("upscaler")])
    failed = plan([manifest], devices=[_gpu(8192)], only=["ghost"])
    assert failed.code == EXIT_FAILED
    stored = load_status()
    assert stored.ready
    assert stored.error == ""
    assert stored.extra.get("phase") == "ready"
    assert [step.state for step in stored.steps] == ["verified", "failed"]
    assert [step.model for step in stored.steps] == ["upscaler", "other"]


def test_compute_mismatch_still_rejects_or_skips(tmp_path: Path) -> None:
    heavy_file = ModelFile(
        "heavy.bin", len(PAYLOAD), "ab" * 32, ("https://huggingface.co/org/h.bin",)
    )
    ok_file = ModelFile("ok.bin", len(PAYLOAD), "cd" * 32, ("https://huggingface.co/org/o.bin",))
    heavy = Model(
        "heavy",
        "1",
        ModelTier.GB24,
        (heavy_file,),
        min_vram_gib=24,
        gpu_min_cc=9.0,
    )
    ok = Model("ok", "1", ModelTier.ANY, (ok_file,))
    manifest = AppManifest("enlarge", (heavy, ok), tmp_path / "mdl.json")
    untargeted = plan_files(
        [manifest],
        VramTier.GB16,
        memory_gib=15.5,
        compute=(8, 9),
        models_root=tmp_path / "models",
    )
    assert [item.model_id for item in untargeted] == ["ok"]
    with pytest.raises(CatalogError, match="compute capability"):
        plan_files(
            [manifest],
            VramTier.GB16,
            only=["heavy"],
            memory_gib=15.5,
            compute=(8, 9),
            models_root=tmp_path / "models",
        )


def test_hidden_worker_and_show_carry_the_session_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-9")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("DISPLAY", raising=False)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    spawned = {"value": False}

    def _popen(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))
        spawned["value"] = True

    def _send(message: dict[str, object], timeout: float = 1.0) -> bool:
        del message, timeout
        return spawned["value"]

    monkeypatch.setattr(mdl_window.subprocess, "Popen", _popen)
    monkeypatch.setattr(mdl_window, "send", _send)
    assert mdl_window.ensure_worker() is True
    (command,), kwargs = calls[0]
    assert list(command) == [os.sys.executable, "-m", "voxprint_common.mdl", "worker"]
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["WAYLAND_DISPLAY"] == "wayland-9"
    assert env["XDG_SESSION_TYPE"] == "wayland"
    assert "DISPLAY" not in env
    assert env.get("PATH")  # the rest of the environment is inherited
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    primary = _FakePrimary()
    primary._messages.append(
        mark_authenticated({"cmd": "show", "app": "", "env": {"WAYLAND_DISPLAY": "wayland-9"}})
    )
    opened, _saw = mdl_window._consume(primary, _FakeController())
    assert os.environ.get("WAYLAND_DISPLAY") == "wayland-9"
    assert opened is not None and opened[2] == "queue"


def test_session_env_ignores_ping_and_unsafe_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    mdl_window._apply_session_env({"cmd": "ping", "env": {"WAYLAND_DISPLAY": "wayland-evil"}})
    assert "WAYLAND_DISPLAY" not in os.environ
    mdl_window._apply_session_env(
        {"cmd": "show", "env": {"WAYLAND_DISPLAY": "wayland-0; rm -rf /", "DISPLAY": ":9"}}
    )
    assert "WAYLAND_DISPLAY" not in os.environ
    assert os.environ["DISPLAY"] == ":0"  # already set, not overwritten
    mdl_window._apply_session_env(
        mark_authenticated({"cmd": "show", "env": {"WAYLAND_DISPLAY": "wayland-0"}})
    )
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"


def test_session_env_ignored_without_authenticated_ipc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    mdl_window._apply_session_env({"cmd": "show", "env": {"WAYLAND_DISPLAY": "wayland-9"}})
    assert "WAYLAND_DISPLAY" not in os.environ
    mdl_window._apply_session_env(
        mark_authenticated({"cmd": "show", "env": {"WAYLAND_DISPLAY": "wayland-9"}})
    )
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-9"


def test_gui_ignores_stale_failed_job_on_successful_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = QueueJob(
        "old-failed",
        "enlarge",
        ("ghost",),
        (),
        "failed",
        "not available for this card",
        "2026-11-16T12:00:00+03:00",
    )
    write_json(queue_path(), {"schema": 1, "jobs": [stale.to_dict()]})
    save_status(
        Status(
            state=JobState.READY,
            error="",
            extra={"phase": "ready"},
            jobs=[stale.to_dict()],
        )
    )
    monkeypatch.setattr(mdl_cli, "detach_console", lambda: None)
    monkeypatch.setattr(mdl_cli, "run_download_window", lambda *args, **kwargs: WindowResult.READY)
    monkeypatch.setattr(
        mdl_cli, "_request", lambda args: ("enlarge", None, [tmp_path / "mdl.json"])
    )
    args = argparse.Namespace(app="enlarge", model=[], manifest=[])
    code, message, payload = mdl_cli._gui(args, None)
    assert code == EXIT_OK
    assert payload["ready"] is True
    assert message == "ready"


def test_worker_gui_sends_show_to_a_running_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-3")
    gui: list[dict[str, object]] = []
    instance: list[dict[str, object]] = []
    monkeypatch.setattr(mdl_window, "become_primary", lambda: None)
    monkeypatch.setattr(
        mdl_window,
        "send_gui",
        lambda app, message, timeout=1.0: gui.append(message) or {"ok": True},
    )
    monkeypatch.setattr(
        mdl_window, "send", lambda message, timeout=1.0: instance.append(message) or True
    )
    assert mdl_window.serve(gui=True, app="canvas") == 0
    assert [message["cmd"] for message in gui] == ["show"]
    assert gui[0]["app"] == "canvas"
    assert gui[0]["env"]["WAYLAND_DISPLAY"] == "wayland-3"
    assert instance == []


def test_worker_gui_falls_back_to_instance_ipc(monkeypatch: pytest.MonkeyPatch) -> None:
    instance: list[dict[str, object]] = []
    monkeypatch.setattr(mdl_window, "become_primary", lambda: None)
    monkeypatch.setattr(mdl_window, "send_gui", lambda app, message, timeout=1.0: None)
    monkeypatch.setattr(
        mdl_window, "send", lambda message, timeout=1.0: instance.append(message) or True
    )
    assert mdl_window.serve(gui=True, app="canvas") == 0
    assert [message["cmd"] for message in instance] == ["show"]
    assert instance[0]["app"] == "canvas"
    assert "env" in instance[0]


def test_worker_gui_opens_the_window_when_it_is_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[tuple[str, str]] = []
    monkeypatch.setattr(mdl_window, "become_primary", _FakePrimary)
    monkeypatch.setattr(mdl_window, "shared_controller", _FakeController)

    def _run(primary, controller, app, open_cmd, mode, manifest):
        del primary, controller, open_cmd, manifest
        ran.append((app, mode))
        return mdl_window.WindowResult.READY

    def _wake(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(mdl_window, "_run", _run)
    monkeypatch.setattr(mdl_window.time, "sleep", _wake)
    with pytest.raises(KeyboardInterrupt):
        mdl_window.serve(gui=True, app="canvas")
    assert ("canvas", "queue") in ran


def test_eta_smoother_hides_until_warm_up_then_uses_the_ema() -> None:
    clock = _FakeClock()
    total = 1_000_000
    smoother = EtaSmoother(clock)
    assert smoother.eta(5_000, total, 500.0) is None  # 0.5% and under 3 s: hidden
    clock.advance(1.0)
    assert smoother.eta(10_000, total, 500.0) is None  # 1% and 1 s: still hidden
    eta = smoother.eta(20_000, total, 500.0)  # 2% of bytes ends the warm-up first
    assert eta == pytest.approx((total - 20_000) / 500.0)
    clock.advance(5.0)
    smoothed_rate = 0.3 * 100_000.0 + 0.7 * 500.0
    eta = smoother.eta(20_000, total, 100_000.0)  # a spike is smoothed, not shown
    assert eta == pytest.approx((total - 20_000) / smoothed_rate)
    assert eta > (total - 20_000) / 100_000.0
    other = EtaSmoother(clock)
    clock.advance(10.0)  # 10 s elapsed ends the warm-up on time, at 0.5%
    assert other.eta(5_000, total, 250.0) == pytest.approx((total - 5_000) / 250.0)


def test_successful_download_publishes_a_terminal_ready_phase(tmp_path: Path) -> None:
    server, root = _serve()
    try:
        manifest = _manifest(tmp_path / "app", "enlarge", [_entry("upscaler", url=f"{root}/file")])
        outcome = download(
            [manifest], devices=[_gpu(8192)], priority=False, guard=UrlGuard.loopback()
        )
    finally:
        server.shutdown()
        server.server_close()
    assert outcome.code == EXIT_OK
    stored = load_status()
    assert stored.state is JobState.READY
    assert stored.extra["phase"] == "ready"


def test_status_display_name_prefers_the_manifest_label(tmp_path: Path) -> None:
    entry = _entry("z-image-turbo-fun-controlnet-union-bf16")
    entry["display_name"] = "Z Image Turbo"
    entry["label"] = "Pose & edge guidance"
    manifest = _manifest(tmp_path / "canvas", "canvas", [entry])
    outcome = plan([manifest], devices=[_gpu(8192)])
    assert outcome.code == EXIT_OK
    step = load_status().steps[0]
    assert step.item_id == "z-image-turbo-fun-controlnet-union-bf16"
    assert step.display_name == "Pose & edge guidance"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode 0600 on mdl-instance.json")
def test_instance_info_is_mode_0600_right_after_write() -> None:
    published = _write_info(os.getpid(), 9, "secret-token")
    path = instance_info_path()
    assert (path.stat().st_mode & 0o777) == 0o600
    assert published == "secret-token"
    assert path.read_text(encoding="utf-8").count("secret-token") == 1
