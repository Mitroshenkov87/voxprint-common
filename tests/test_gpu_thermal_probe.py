"""GPU lock contention, the thermal rule, and VRAM tiers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from voxprint_common.gpu_lock import (
    Acquired,
    GpuLockTimeout,
    gpu_job,
    is_stale,
    pid_exists,
    read_holder,
    try_acquire,
)
from voxprint_common.gpu_probe import (
    TIER_16_MIN_GIB,
    TIER_24_MIN_GIB,
    VramTier,
    assess,
    gib_from_mib,
    parse_gpu_table,
    tier_for_gib,
)
from voxprint_common.thermal import FULL_SPEED_S, Action, ThermalTimeout, decide, wait_until_ready

HOLD_GPU = Path(__file__).with_name("hold_gpu.py")


def _dead_pid() -> int:
    completed = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(completed.stdout.strip())


def _sleeper() -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_stale_holder_is_replaced(tmp_path: Path) -> None:
    lock = tmp_path / "gpu.lock"
    lock.write_text(
        json.dumps(
            {
                "owner": "dubber",
                "job": "mix",
                "pid": _dead_pid(),
                "started": "2020-01-01T00:00:00Z",
                "eta": "2099-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    assert read_holder(lock) is not None
    assert read_holder(lock).app == "dubber"  # type: ignore[union-attr]
    assert read_holder(lock).stage == "mix"  # type: ignore[union-attr]
    assert is_stale(json.loads(lock.read_text(encoding="utf-8")))
    outcome = try_acquire("clarity", "restore", path=lock)
    assert isinstance(outcome, Acquired)
    assert not outcome.borrowed
    holder = read_holder(lock)
    assert holder is not None
    assert holder.app == "clarity"
    assert holder.stage == "restore"


def test_live_holder_blocks_and_parent_is_borrowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "gpu.lock"
    child = _sleeper()
    try:
        future = "2099-01-01T00:00:00Z"
        lock.write_text(
            json.dumps({"app": "canvas", "stage": "generate", "pid": child.pid, "eta": future}),
            encoding="utf-8",
        )
        blocked = try_acquire("enlarge", "upscale", path=lock)
        assert not isinstance(blocked, Acquired)
        assert blocked.app == "canvas"
        assert blocked.stage == "generate"
        monkeypatch.setenv("VOXPRINT_GPU_PARENT_PID", str(child.pid))
        borrowed = try_acquire("enlarge", "upscale", path=lock)
        assert isinstance(borrowed, Acquired)
        assert borrowed.borrowed
        with gpu_job("enlarge", "upscale", path=lock, timeout=0.2):
            assert lock.is_file()
        assert lock.is_file()
    finally:
        child.kill()
        child.wait(5)


def test_gpu_lock_contention_across_processes(tmp_path: Path) -> None:
    lock = tmp_path / "gpu.lock"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    process = subprocess.Popen([sys.executable, str(HOLD_GPU), str(lock), str(ready), str(release)])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise AssertionError(process.stderr)
            time.sleep(0.02)
        with (
            pytest.raises(GpuLockTimeout, match="enlarge / upscale"),
            gpu_job("canvas", "generate", path=lock, timeout=0.3, poll=0.05),
        ):
            pass
        assert lock.is_file()
        release.write_text("go", encoding="utf-8")
        assert process.wait(10) == 0
        with gpu_job("canvas", "generate", path=lock, timeout=2):
            holder = read_holder(lock)
            assert holder is not None
            assert holder.app == "canvas"
            assert holder.stage == "generate"
        assert not lock.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(5)


def test_nested_gpu_job_releases_once(tmp_path: Path) -> None:
    lock = tmp_path / "gpu.lock"
    with gpu_job("enlarge", "outer", path=lock, eta=30):
        with gpu_job("enlarge", "inner", path=lock, eta=30):
            assert lock.is_file()
        assert lock.is_file()
    assert not lock.exists()


def test_pid_exists_for_self_and_dead() -> None:
    assert pid_exists(os.getpid())
    assert not pid_exists(_dead_pid())
    assert not pid_exists(0)


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows process check")
def test_windows_pid_query() -> None:
    from voxprint_common.gpu_lock import _windows_pid_exists

    assert _windows_pid_exists(os.getpid())
    assert not _windows_pid_exists(_dead_pid())


def test_thermal_windows() -> None:
    assert decide(10, 90, 0, False).action is Action.RUN
    hot = decide(FULL_SPEED_S + 1, 83, 0, False)
    assert hot.action is Action.PAUSE
    assert "83" in hot.reason
    throttled = decide(FULL_SPEED_S + 1, None, 61, False)
    assert throttled.action is Action.PAUSE
    cool = decide(FULL_SPEED_S + 1, 75, 0, True)
    assert cool.action is Action.RESUME
    waiting = decide(FULL_SPEED_S + 1, 80, 0, True)
    assert waiting.action is Action.PAUSE


def test_wait_until_ready_uses_the_sampler() -> None:
    clock = {"now": 0.0}
    samples = iter([(90.0, 0.0), (70.0, 0.0)])

    def sample() -> tuple[float | None, float]:
        return next(samples)

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    decision = wait_until_ready(
        sample,
        started=-(FULL_SPEED_S + 5),
        paused=False,
        sleep=sleep,
        now=lambda: clock["now"],
        interval=5,
    )
    assert decision.action is Action.RESUME
    assert clock["now"] == 5

    def stay_hot() -> tuple[float | None, float]:
        return (90.0, 0.0)

    with pytest.raises(ThermalTimeout):
        wait_until_ready(
            stay_hot,
            started=-(FULL_SPEED_S + 5),
            sleep=lambda _seconds: None,
            now=lambda: 0.0,
            interval=1,
            max_wait=0,
        )


def test_vram_tiers_use_gibibytes() -> None:
    assert TIER_16_MIN_GIB == 15.5
    assert TIER_24_MIN_GIB == 23.5
    assert tier_for_gib(15.49) is VramTier.GB8
    assert tier_for_gib(15.5) is VramTier.GB16
    assert tier_for_gib(23.49) is VramTier.GB16
    assert tier_for_gib(23.5) is VramTier.GB24
    assert tier_for_gib(None) is VramTier.UNKNOWN
    assert gib_from_mib(24564) >= 23.5
    assert tier_for_gib(gib_from_mib(24564)) is VramTier.GB24
    assert tier_for_gib(gib_from_mib(16384)) is VramTier.GB16


def test_probe_messages_use_the_app_name() -> None:
    table = "NVIDIA GeForce RTX 4090, 610.73.01, 8.9, 24564\n"
    devices = parse_gpu_table(table)
    assert devices[0].name == "NVIDIA GeForce RTX 4090"
    assert devices[0].tier is VramTier.GB24
    allowed = assess("Voxprint AI Enlarge", devices=devices)
    assert allowed.ok
    assert allowed.warning == ""
    refused = assess("Voxprint AI Clarity", devices=[])
    assert not refused.ok
    assert refused.message.startswith("Voxprint AI Clarity needs an NVIDIA GPU")
    old = parse_gpu_table("Old Card, 550.1, 8.9, 8192\n")
    gate = assess("Voxprint AI Canvas", devices=old)
    assert not gate.ok
    assert "550.1" in gate.message
    assert "Canvas" in gate.message
    below = parse_gpu_table("Older, 610.1, 7.5, 8192\n")
    warned = assess("Enlarge", devices=below)
    assert warned.ok
    assert "7.5" in warned.warning
    assert "Enlarge" in warned.warning
