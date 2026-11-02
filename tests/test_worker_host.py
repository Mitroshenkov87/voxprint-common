"""Behaviour of the Qt-free worker host."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from voxprint_common.worker import (
    CheckHook,
    EventKind,
    Handlers,
    JobKind,
    JobSpec,
    JobState,
    WorkerHost,
)

_HANDLERS = Handlers(
    run="tests.worker_handlers:run",
    load_model="tests.worker_handlers:load_model",
    unload_model="tests.worker_handlers:unload_model",
    check="tests.worker_handlers:check",
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def make_host():
    """Yield a factory that shuts every host down."""
    hosts: list[WorkerHost] = []

    def factory(**kwargs: object) -> WorkerHost:
        host = WorkerHost(_HANDLERS, **kwargs)  # type: ignore[arg-type]
        hosts.append(host)
        return host

    yield factory
    for host in hosts:
        host.shutdown(timeout=2)


def _collect(host: WorkerHost, job_ids: set[str], *, timeout: float = 15) -> list[object]:
    seen: list[object] = []
    pending = set(job_ids)
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        for event in host.poll():
            seen.append(event)
            if event.kind in {EventKind.FINISHED, EventKind.FAILED, EventKind.CANCELLED}:
                pending.discard(event.job_id)
        time.sleep(0.01)
    return seen


def test_jobs_run_in_submit_order_in_the_child(make_host: Callable[..., WorkerHost]) -> None:
    host = make_host(backoff_initial=0)
    first = host.submit(
        JobSpec(JobKind.INFERENCE, {"op": "sleep", "seconds": 0.2, "value": "first"})
    )
    second = host.submit(
        JobSpec(JobKind.INFERENCE, {"op": "sleep", "seconds": 0.01, "value": "second"})
    )
    seen = _collect(host, {first, second})
    order = [
        event.value["value"]
        for event in seen
        if event.kind is EventKind.FINISHED  # type: ignore[attr-defined]
    ]
    assert order == ["first", "second"]
    assert host.outcome(first).value["pid"] != os.getpid()  # type: ignore[union-attr]


def test_progress_includes_step_epoch_eta_and_metrics(make_host: Callable[..., WorkerHost]) -> None:
    host = make_host()
    job = host.submit(JobSpec(JobKind.TRAINING, {"op": "progress"}, phase="train"))
    seen = _collect(host, {job})
    progress = next(event for event in seen if event.kind is EventKind.PROGRESS)  # type: ignore[attr-defined]
    assert progress.step == 2
    assert progress.epoch == 1
    assert progress.total_steps == 10
    assert progress.total_epochs == 3
    assert progress.eta_s == 12.5
    assert progress.metric_map["loss"] == 0.25
    assert progress.fraction == 0.2
    assert progress.phase == "train"
    assert progress.message == "step"


def test_worker_is_spawned_not_forked(make_host: Callable[..., WorkerHost]) -> None:
    import tests.worker_handlers as handlers

    handlers.MARKER = "parent"
    host = make_host()
    job = host.submit(JobSpec(JobKind.INFERENCE, {"op": "echo", "value": 1}))
    outcome = host.wait(job)
    assert outcome.state is JobState.FINISHED
    assert outcome.value["marker"] == "child-import"
    assert outcome.value["pid"] != os.getpid()


def test_poll_does_not_block(make_host: Callable[..., WorkerHost]) -> None:
    host = make_host(heartbeat_interval=0, heartbeat_timeout=30)
    host.submit(JobSpec(JobKind.INFERENCE, {"op": "block", "seconds": 30}))
    started = time.monotonic()
    host.poll()
    assert time.monotonic() - started < 0.2


def test_one_model_stays_loaded_across_a_batch_then_unloads(
    make_host: Callable[..., WorkerHost],
) -> None:
    host = make_host()
    jobs = [
        host.submit(
            JobSpec(
                JobKind.INFERENCE,
                {"op": "echo", "value": value},
                model=model,
                phase="batch",
            )
        )
        for model, value in (("alpha", 1), ("alpha", 2), ("beta", 3))
    ]
    seen = _collect(host, set(jobs))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not any(
        event.kind is EventKind.UNLOADED and event.model == "beta"  # type: ignore[attr-defined]
        for event in seen
    ):
        seen.extend(host.poll())
        time.sleep(0.01)
    loaded = [event.model for event in seen if event.kind is EventKind.LOADED]  # type: ignore[attr-defined]
    unloaded = [event.model for event in seen if event.kind is EventKind.UNLOADED]  # type: ignore[attr-defined]
    assert loaded == ["alpha", "beta"]
    assert unloaded == ["alpha", "beta"]
    finished = {
        event.job_id: event.value
        for event in seen
        if event.kind is EventKind.FINISHED  # type: ignore[attr-defined]
    }
    assert finished[jobs[0]]["loaded"] == "alpha"
    assert finished[jobs[1]]["loaded"] == "alpha"
    assert finished[jobs[2]]["loaded"] == "beta"


def test_cooperative_cancel_saves_a_checkpoint_and_keeps_the_process(
    make_host: Callable[..., WorkerHost], tmp_path: Path
) -> None:
    host = make_host()
    checkpoint = tmp_path / "stop.ckpt"
    job = host.submit(JobSpec(JobKind.TRAINING, {"op": "cancel-me", "checkpoint": str(checkpoint)}))
    deadline = time.monotonic() + 5
    while host.pid is None and time.monotonic() < deadline:
        host.poll()
        time.sleep(0.01)
    pid = host.pid
    assert pid is not None
    host.cancel(job)
    outcome = host.wait(job)
    assert outcome.state is JobState.CANCELLED
    assert "was cancelled" in outcome.message
    assert outcome.checkpoint == str(checkpoint)
    assert checkpoint.read_text(encoding="utf-8") == "saved"
    assert host.pid == pid
    resumed = host.resume(job)
    assert resumed == job
    outcome = host.wait(job)
    assert outcome.state is JobState.FINISHED
    assert outcome.value["checkpoint"] == str(checkpoint)


def test_cancel_kills_a_job_that_does_not_stop(make_host: Callable[..., WorkerHost]) -> None:
    host = make_host(cancel_timeout=0.2, backoff_initial=0, heartbeat_timeout=30)
    job = host.submit(JobSpec(JobKind.INFERENCE, {"op": "ignore-cancel", "seconds": 30}))
    deadline = time.monotonic() + 5
    while host.pid is None and time.monotonic() < deadline:
        host.poll()
        time.sleep(0.01)
    pid = host.pid
    host.cancel(job)
    outcome = host.wait(job, timeout=10)
    assert outcome.state is JobState.CANCELLED
    assert "did not stop after cancel" in outcome.message
    deadline = time.monotonic() + 5
    while host.pid == pid and time.monotonic() < deadline:
        host.poll()
        time.sleep(0.01)
    assert host.pid != pid


def test_queued_cancel_does_not_start_the_job(make_host: Callable[..., WorkerHost]) -> None:
    host = make_host()
    first = host.submit(
        JobSpec(JobKind.INFERENCE, {"op": "sleep", "seconds": 0.2, "value": "kept"})
    )
    second = host.submit(JobSpec(JobKind.INFERENCE, {"op": "echo", "value": "skip"}))
    host.cancel(second)
    assert host.outcome(second).state is JobState.CANCELLED  # type: ignore[union-attr]
    assert host.wait(first).value["value"] == "kept"
    assert host.outcome(second).value is None  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", [JobKind.INFERENCE, JobKind.CAPTION, JobKind.CHECK])
def test_crash_fails_fast_and_the_next_job_runs(
    make_host: Callable[..., WorkerHost], kind: JobKind
) -> None:
    host = make_host(backoff_initial=0, heartbeat_timeout=30)
    crashed = host.submit(JobSpec(kind, {"op": "crash", "code": 9}))
    outcome = host.wait(crashed, timeout=15)
    assert outcome.state is JobState.FAILED
    assert "exit 9" in outcome.message
    assert "not resumed" in outcome.message
    follow = host.submit(JobSpec(JobKind.INFERENCE, {"op": "echo", "value": "after"}))
    assert host.wait(follow).value["value"] == "after"


def test_training_resumes_from_the_checkpoint(
    make_host: Callable[..., WorkerHost], tmp_path: Path
) -> None:
    host = make_host(backoff_initial=0, heartbeat_timeout=30)
    checkpoint = tmp_path / "train.ckpt"
    job = host.submit(
        JobSpec(
            JobKind.TRAINING,
            {"op": "crash", "checkpoint": str(checkpoint)},
            model="forge",
        )
    )
    outcome = host.wait(job, timeout=15)
    assert outcome.state is JobState.FINISHED
    assert outcome.attempts == 2
    assert outcome.value["resumed"] == str(checkpoint)
    assert checkpoint.read_text(encoding="utf-8") == "weights"


def test_training_stops_resuming_after_its_limit(make_host: Callable[..., WorkerHost]) -> None:
    host = make_host(backoff_initial=0, heartbeat_timeout=30)
    job = host.submit(JobSpec(JobKind.TRAINING, {"op": "crash-always"}, max_resumes=1))
    outcome = host.wait(job, timeout=15)
    assert outcome.state is JobState.FAILED
    assert "Resume stopped after 2 attempts" in outcome.message


def test_restart_waits_out_the_backoff(make_host: Callable[..., WorkerHost]) -> None:
    clock = _Clock()
    host = make_host(clock=clock, backoff_initial=1, backoff_max=8, heartbeat_timeout=30)
    job = host.submit(JobSpec(JobKind.INFERENCE, {"op": "crash", "code": 9}))
    deadline = time.monotonic() + 10
    failed = False
    while time.monotonic() < deadline:
        if any(event.kind is EventKind.FAILED for event in host.poll()):
            failed = True
            break
        time.sleep(0.01)
    assert failed
    assert host.outcome(job).state is JobState.FAILED  # type: ignore[union-attr]
    assert host.pid is None
    clock.now = 0.9
    host.poll()
    assert host.pid is None
    clock.now = 1
    host.poll()
    assert host.pid is not None
    second = host.submit(JobSpec(JobKind.INFERENCE, {"op": "crash", "code": 9}))
    deadline = time.monotonic() + 10
    while host.outcome(second) is None and time.monotonic() < deadline:
        host.poll()
        time.sleep(0.01)
    assert host.pid is None
    clock.now = 2.9
    host.poll()
    assert host.pid is None
    clock.now = 3
    host.poll()
    assert host.pid is not None


def test_heartbeats_let_a_long_step_finish(make_host: Callable[..., WorkerHost]) -> None:
    host = make_host(heartbeat_interval=0.05, heartbeat_timeout=0.6, cancel_timeout=0.2)
    job = host.submit(JobSpec(JobKind.TRAINING, {"op": "sleep", "seconds": 1.0, "value": "long"}))
    outcome = host.wait(job, timeout=10)
    assert outcome.state is JobState.FINISHED
    assert outcome.value["value"] == "long"


def test_a_silent_job_is_closed(make_host: Callable[..., WorkerHost]) -> None:
    host = make_host(
        heartbeat_interval=0,
        heartbeat_timeout=0.25,
        cancel_timeout=0.2,
        backoff_initial=0,
    )
    job = host.submit(JobSpec(JobKind.INFERENCE, {"op": "block", "seconds": 30}))
    outcome = host.wait(job, timeout=10)
    assert outcome.state is JobState.FAILED
    assert "heartbeats" in outcome.message
    follow = host.submit(JobSpec(JobKind.INFERENCE, {"op": "echo", "value": "up"}))
    assert host.wait(follow, timeout=10).value["value"] == "up"


def test_check_gate_runs_in_the_worker(make_host: Callable[..., WorkerHost]) -> None:
    host = make_host()
    job = CheckHook(host).run({"gate": "face"}, model="face", phase="checks")
    outcome = host.wait(job)
    assert outcome.state is JobState.FINISHED
    assert outcome.value["checked"] == {"gate": "face"}
    assert outcome.value["model"] == "face"
    assert outcome.value["pid"] != os.getpid()


def test_shutdown_exits_the_worker_process(make_host: Callable[..., WorkerHost]) -> None:
    from voxprint_common.gpu_lock import pid_exists

    host = make_host()
    host.start()
    deadline = time.monotonic() + 5
    while host.pid is None and time.monotonic() < deadline:
        time.sleep(0.01)
    pid = host.pid
    assert pid is not None
    host.shutdown(timeout=2)
    assert host.pid is None
    assert not pid_exists(pid)


def test_importing_the_worker_does_not_load_qt() -> None:
    code = (
        "import sys\n"
        "import voxprint_common.worker\n"
        "import voxprint_common.worker.qt\n"
        "raise SystemExit(1 if 'PySide6' in sys.modules else 0)\n"
    )
    completed = subprocess.run([sys.executable, "-c", code], check=False)
    assert completed.returncode == 0


def test_submit_after_a_crash_settles_the_lost_job(make_host: Callable[..., WorkerHost]) -> None:
    host = make_host(backoff_initial=0)
    lost = host.submit(JobSpec(JobKind.INFERENCE, {"op": "block", "seconds": 30}))
    deadline = time.monotonic() + 15
    while host.pid is None and time.monotonic() < deadline:
        time.sleep(0.01)
    pid = host.pid
    host.kill_worker()
    while host.pid == pid and time.monotonic() < deadline:
        time.sleep(0.01)
    follow = host.submit(JobSpec(JobKind.INFERENCE, {"op": "echo", "value": "up"}))
    assert host.outcome(lost) is not None
    assert host.outcome(lost).state is JobState.FAILED  # type: ignore[union-attr]
    assert host.wait(follow, timeout=10).value["value"] == "up"  # type: ignore[index]
