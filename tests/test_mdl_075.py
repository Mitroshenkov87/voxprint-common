"""0.7.5: Retry the failed job, download-all CLI, GUI socket, signed serial 14."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

from voxprint_common.atomic import write_json
from voxprint_common.cli_protocol import EXIT_OK, EXIT_USAGE
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.catalog import load_manifest, models_for_download_all
from voxprint_common.mdl.cli import main
from voxprint_common.mdl.control import Controller
from voxprint_common.mdl.gui_ipc import listen, send, socket_path
from voxprint_common.mdl.queue import QueueJob, enqueue_download_all, load_queue, queue_path

PAYLOAD = b"voxprint-075-blob"
DIGEST = sha256_bytes(PAYLOAD)


def _entry(model_id: str, *, optional: bool = False, digest: str = DIGEST) -> dict[str, object]:
    return {
        "id": model_id,
        "version": "1",
        "vram_tier": "any",
        "optional": optional,
        "files": [
            {
                "path": f"{model_id}.bin",
                "size": len(PAYLOAD),
                "sha256": digest,
                "mirrors": [{"source": "url", "url": "https://huggingface.co/org/w.bin"}],
            }
        ],
    }


def _manifest(folder: Path, app: str, entries: list[dict[str, object]]) -> Path:
    path = folder / "mdl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "app": app, "models": entries}), encoding="utf-8")
    return path


def _write_job(job: QueueJob) -> None:
    path = queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = [item.to_dict() for item in load_queue()] if path.is_file() else []
    existing.append(job.to_dict())
    write_json(path, {"schema": 1, "jobs": existing})


def test_retry_requeues_failed_job_models_and_manifests(tmp_path: Path, monkeypatch) -> None:
    manifest = _manifest(
        tmp_path / "canvas",
        "canvas",
        [_entry("z-image-turbo"), _entry("qwen3-4b-writer", optional=True, digest="e" * 64)],
    )
    failed = QueueJob(
        "job-failed-writer",
        "canvas",
        ("qwen3-4b-writer",),
        (str(manifest),),
        "failed",
        "The read operation timed out",
        "2026-11-15T14:32:00Z",
    )
    empty = QueueJob(
        "job-empty-retry",
        "canvas",
        (),
        (),
        "failed",
        "nothing to do",
        "2026-11-15T14:33:00Z",
    )
    _write_job(failed)
    _write_job(empty)
    monkeypatch.setattr(Controller, "start", lambda self, display=True: None)
    controller = Controller()
    controller._focus = "canvas"
    controller._job_id = "job-empty-retry"
    snapshot = controller.retry()
    del snapshot
    jobs = load_queue()
    queued = [job for job in jobs if job.state == "queued"]
    assert queued, "Retry must enqueue the failed job, not an empty one"
    latest = queued[-1]
    assert latest.id != "job-failed-writer"
    assert latest.models == ("qwen3-4b-writer",)
    assert latest.manifests == (str(manifest),)
    assert latest.app == "canvas"


def test_retry_does_not_enqueue_when_there_is_no_failed_job(monkeypatch) -> None:
    before = load_queue()
    monkeypatch.setattr(Controller, "start", lambda self, display=True: None)
    controller = Controller()
    controller._focus = "canvas"
    controller.retry()
    assert load_queue() == before


def test_enqueue_download_all_includes_optional_and_drops_moss(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path / "canvas",
        "canvas",
        [
            _entry("z-image-turbo"),
            _entry("qwen3-4b-writer", optional=True, digest="e" * 64),
            _entry("moss-image-base", optional=True, digest="f" * 64),
        ],
    )
    ids = models_for_download_all(load_manifest(manifest))
    assert "z-image-turbo" in ids
    assert "qwen3-4b-writer" in ids
    assert "moss-image-base" not in ids
    job_id = enqueue_download_all("canvas", manifest)
    jobs = {job.id: job for job in load_queue()}
    assert job_id in jobs
    assert jobs[job_id].models == ids
    assert jobs[job_id].manifests == (str(manifest),)


def test_cli_all_cannot_combine_with_model(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "canvas", "canvas", [_entry("z-image-turbo")])
    assert main(["enqueue", "--all", "--model", "z-image-turbo", "--manifest", str(manifest)]) == (
        EXIT_USAGE
    )


def test_cli_all_queues_every_installable_model(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path / "canvas",
        "canvas",
        [
            _entry("z-image-turbo"),
            _entry("qwen3-4b-writer", optional=True, digest="e" * 64),
            _entry("moss-image-base", optional=True, digest="f" * 64),
        ],
    )
    assert main(["enqueue", "--all", "--manifest", str(manifest)]) == EXIT_OK
    jobs = load_queue()
    assert jobs
    assert "z-image-turbo" in (jobs[-1].models or ())
    assert "qwen3-4b-writer" in (jobs[-1].models or ())
    assert "moss-image-base" not in (jobs[-1].models or ())


def test_gui_socket_json_lines_round_trip() -> None:
    errors: list[Exception] = []

    def body() -> None:
        received: list[dict[str, object]] = []

        def handler(message: dict[str, object]) -> dict[str, object]:
            received.append(message)
            if message.get("cmd") == "enqueue":
                return {"ok": True, "job": "job-1"}
            return {"ok": True}

        server = listen("canvas", handler)
        assert server is not None
        try:
            reply = None
            for _ in range(50):
                reply = send("canvas", {"cmd": "ping"}, timeout=0.2)
                if reply is not None:
                    break
                time.sleep(0.02)
            assert reply == {"ok": True}
            assert send(
                "canvas",
                {"cmd": "enqueue", "app": "canvas", "models": ["qwen3-4b-writer"]},
                timeout=0.5,
            ) == {"ok": True, "job": "job-1"}
            opened = send(
                "canvas",
                {"cmd": "open-tab", "app": "canvas", "tab": "shared"},
                timeout=0.5,
            )
            assert opened == {"ok": True}
            unknown = send("canvas", {"cmd": "nope"}, timeout=0.5)
            assert unknown == {"ok": False, "error": "unknown cmd: 'nope'"}
            # Windows uses a named pipe, not a Unix socket file.
            if sys.platform != "win32":
                assert socket_path("canvas").is_socket()
        finally:
            server.close()
        assert send("canvas", {"cmd": "ping"}, timeout=0.2) is None
        again = listen("canvas", handler)
        assert again is not None
        again.close()

    def run() -> None:
        try:
            body()
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, name="gui-ipc-test", daemon=True)
    thread.start()
    thread.join(15)
    if thread.is_alive():
        raise AssertionError("GUI socket/pipe test timed out after 15s")
    if errors:
        raise errors[0]


def test_cli_download_all_dry_run_includes_optional(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path / "canvas",
        "canvas",
        [
            _entry("z-image-turbo"),
            _entry("qwen3-4b-writer", optional=True, digest="e" * 64),
            _entry("moss-image-base", optional=True, digest="f" * 64),
        ],
    )
    assert main(
        ["download", "--all", "--model", "z-image-turbo", "--manifest", str(manifest), "--dry-run"]
    ) == (EXIT_USAGE)
    assert main(["download", "--all", "--dry-run", "--manifest", str(manifest)]) == EXIT_OK
    # dry-run must not have queued a job
    jobs = load_queue()
    assert all(job.state != "queued" or "moss-image-base" not in (job.models or ()) for job in jobs)
