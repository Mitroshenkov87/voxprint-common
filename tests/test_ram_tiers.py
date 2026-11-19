"""RAM tiers, pin budget, and model parking."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from voxprint_common.atomic import write_json
from voxprint_common.model_park import (
    ModelPark,
    _budget_path,
    _others_pinned,
    _publish_pin,
    reset_process_park,
)
from voxprint_common.ram_probe import (
    GIB,
    RamTier,
    _linux_memory,
    _windows_memory,
    pin_budget_gib,
    ram_tier,
    tier_for_gib,
    total_gib,
)
from voxprint_common.ram_probe import (
    ctypes as ram_ctypes,
)
from voxprint_common.suite_settings import set_ram_mode

TWO_GIB = 2 * GIB


class _Tensor:
    def __init__(self, nbytes: int, *, fail: bool = False) -> None:
        self.nbytes = nbytes
        self.fail = fail
        self.pinned = False
        self.pins = 0
        self.moved: list[tuple[object, bool]] = []

    def pin_memory(self) -> _Tensor:
        self.pins += 1
        if self.fail:
            raise RuntimeError("CUDA out of memory")
        self.pinned = True
        return self

    def to(self, device: object, non_blocking: bool = False) -> _Tensor:
        self.moved.append((device, non_blocking))
        if device == "cpu":
            self.pinned = False
        return self


@pytest.fixture(autouse=True)
def _roomy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("voxprint_common.model_park.available_gib", lambda: 32.0)
    reset_process_park()
    yield
    reset_process_park()


def test_tier_thresholds_and_overrides(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    assert tier_for_gib(88) is RamTier.RAM96
    assert tier_for_gib(87.9) is RamTier.RAM64
    assert tier_for_gib(44) is RamTier.RAM64
    assert tier_for_gib(43.9) is RamTier.RAM32
    assert tier_for_gib(31.7) is RamTier.RAM32
    import voxprint_common.ram_probe as probe

    probe._warned = False
    with caplog.at_level("WARNING"):
        assert tier_for_gib(16) is RamTier.RAM32
    assert "ram32" in caplog.text
    monkeypatch.setenv("VOXPRINT_RAM_MODE", "ram96")
    set_ram_mode("ram32")
    assert ram_tier() is RamTier.RAM96
    monkeypatch.setenv("VOXPRINT_RAM_MODE", "auto")
    assert ram_tier() is RamTier.RAM32
    monkeypatch.delenv("VOXPRINT_RAM_MODE")
    set_ram_mode("ram64")
    assert ram_tier() is RamTier.RAM64
    monkeypatch.setattr(probe, "total_gib", lambda: 90.0)
    set_ram_mode("auto")
    assert ram_tier() is RamTier.RAM96
    park = ModelPark.for_process(RamTier.RAM64, app="canvas")
    assert ModelPark.for_process() is park
    sample = tmp_path / "meminfo"
    sample.write_text(
        "MemTotal:        16777216 kB\nMemAvailable:     8388608 kB\n",
        encoding="utf-8",
    )
    assert _linux_memory(sample) == (16 * GIB, 8 * GIB)
    if sys.platform != "win32":
        monkeypatch.setattr(probe, "_MEMINFO", sample)
        assert total_gib() == pytest.approx(16.0)


def test_pin_budget_windows_and_linux() -> None:
    assert pin_budget_gib(128, tier=RamTier.RAM32, windows=False) == 0
    assert pin_budget_gib(48, tier=RamTier.RAM64, windows=True) == pytest.approx(12)
    assert pin_budget_gib(48, tier=RamTier.RAM64, windows=False) == pytest.approx(12)
    assert pin_budget_gib(64, tier=RamTier.RAM64, windows=True) == pytest.approx(16)
    assert pin_budget_gib(64, tier=RamTier.RAM64, windows=False) == pytest.approx(16)
    assert pin_budget_gib(44.7, windows=True) == pytest.approx(11.175)
    assert pin_budget_gib(96, tier=RamTier.RAM96, windows=True) == pytest.approx(48)
    assert pin_budget_gib(96, tier=RamTier.RAM96, windows=False) == pytest.approx(57.6)
    assert pin_budget_gib(20, windows=False) == 0


def test_windows_memory_status(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Kernel:
        def GlobalMemoryStatusEx(self, ref) -> int:
            ref._obj.ullTotalPhys = 96 * GIB
            ref._obj.ullAvailPhys = 40 * GIB
            return 1

    monkeypatch.setattr(
        ram_ctypes, "windll", types.SimpleNamespace(kernel32=_Kernel()), raising=False
    )
    assert _windows_memory() == (96 * GIB, 40 * GIB)


def _park(tier: RamTier, **kwargs: object) -> ModelPark:
    return ModelPark(tier, app="canvas", total=96, windows=False, **kwargs)


def _load(weights: _Tensor):
    def loader() -> dict[str, _Tensor]:
        return {"w": weights}

    return loader


def test_policy_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    disk = _park(RamTier.RAM32)
    held = disk.get("m", _load(_Tensor(10)), size_bytes=10, role="main")
    assert disk.stats().disk == 10
    assert disk.stats().pinned == 0
    held.release()
    assert disk.stats().disk == 0
    wide = _park(RamTier.RAM64)
    main = wide.get("main", _load(_Tensor(100)), size_bytes=100, role="main")
    assert wide.stats().pinned == 100
    aux = wide.get("aux", _load(_Tensor(40)), size_bytes=40, role="aux")
    assert wide.stats().pageable == 40
    optional = wide.get("opt", _load(_Tensor(7)), size_bytes=7, role="optional")
    assert wide.stats().disk == 7
    optional.release()
    assert wide.stats().disk == 0
    wide.get("main", _load(_Tensor(1)), size_bytes=100, role="main")
    assert wide.stats().hits == 1
    huge = _Tensor(3 * GIB)
    over = wide.get("big", _load(huge), size_bytes=3 * GIB, role="main")
    assert huge.pins == 0
    assert wide.stats().fallbacks >= 1
    torch = _torch_module()
    monkeypatch.setitem(sys.modules, "torch", torch)
    moved = over.to("cuda")
    assert moved["w"].moved[-1] == ("cuda", True)
    assert torch.calls[:2] == [(256 * 1024 * 1024, True), (256 * 1024 * 1024, True)]
    high = _park(RamTier.RAM96)
    fit = _Tensor(3 * GIB)
    high.get("fit", _load(fit), size_bytes=3 * GIB, role="optional")
    assert fit.pins == 1
    assert high.stats().pinned == 3 * GIB
    tight = ModelPark(RamTier.RAM96, app="canvas", total=96, windows=False)
    monkeypatch.setattr("voxprint_common.model_park.pin_budget_bytes", lambda *args, **kwargs: 0)
    skipped = _Tensor(10)
    tight.get("no", _load(skipped), size_bytes=10, role="optional")
    assert skipped.pins == 0
    assert tight.stats().pageable == 10
    aux.release()
    main.release()


def test_lru_and_demotion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("voxprint_common.model_park.pin_budget_bytes", lambda *args, **kwargs: 3000)
    park = _park(RamTier.RAM64)
    held = []
    for index in range(3):
        held.append(park.get(f"m{index}", _load(_Tensor(1000)), size_bytes=1000, role="main"))
    for item in held:
        item.release()
    park.get("m3", _load(_Tensor(1000)), size_bytes=1000, role="main")
    assert park.stats().pinned == 3000
    assert park.stats().pageable == 1000
    monkeypatch.setattr(
        "voxprint_common.model_park.pin_budget_bytes", lambda *args, **kwargs: 10 * GIB
    )
    crowded = _park(RamTier.RAM64)
    for index in range(3):
        parked = crowded.get(f"a{index}", _load(_Tensor(TWO_GIB)), size_bytes=TWO_GIB, role="aux")
        parked.release()

    def available() -> float:
        held_bytes = sum(entry.size_bytes for entry in crowded._entries.values())
        return 5 + (3 * TWO_GIB - held_bytes) / GIB

    monkeypatch.setattr("voxprint_common.model_park.available_gib", available)
    crowded.get("more", _load(_Tensor(1)), size_bytes=1, role="aux")
    assert crowded.stats().pageable == TWO_GIB + 1


def test_pin_failure_falls_back() -> None:
    park = _park(RamTier.RAM96)
    broken = _Tensor(100, fail=True)
    park.get("bad", _load(broken), size_bytes=100, role="main")
    assert park.stats().pinned == 0
    assert park.stats().pageable == 100
    assert park.stats().fallbacks == 1
    later = _Tensor(100)
    park.get("later", _load(later), size_bytes=100, role="main")
    assert later.pins == 0
    assert park.stats().pageable == 200


def test_two_processes_share_the_budget(tmp_path: Path) -> None:
    dead = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        check=True,
        capture_output=True,
        text=True,
    )
    path = _budget_path()
    write_json(
        path, {"holders": [{"pid": int(dead.stdout.strip()), "app": "ghost", "pinned_bytes": 50}]}
    )
    ready = tmp_path / "ready"
    stop = tmp_path / "stop"
    child = Path(__file__).resolve().parent / "ram_park_child.py"
    proc = subprocess.Popen([sys.executable, str(child), "600", str(ready), str(stop)])
    try:
        for _ in range(200):
            if ready.is_file():
                break
            assert proc.poll() is None
            time.sleep(0.05)
        else:
            raise AssertionError("child did not publish")
        assert _others_pinned() == 600
        assert "ghost" not in path.read_text(encoding="utf-8")
    finally:
        stop.write_text("stop", encoding="utf-8")
        proc.wait(timeout=10)
        _release_process(proc)
    _publish_pin("parent", 100)
    assert _others_pinned() == 0
    holders = json.loads(path.read_text(encoding="utf-8"))["holders"]
    assert [row["pid"] for row in holders] == [os.getpid()]


def _release_process(proc: subprocess.Popen) -> None:
    """Close the process handle so a dead pid is reported as gone.

    On Windows, OpenProcess still succeeds while this handle is open.
    """
    if sys.platform != "win32":
        return
    handle = getattr(proc, "_handle", None)
    if handle is None:
        return
    handle.Close()
    proc._handle = None


def _torch_module():
    if getattr(_torch_module, "mod", None) is None:
        module = types.ModuleType("torch")
        module.calls = []

        def empty(n, dtype=None, pin_memory=False):
            module.calls.append((n, pin_memory))
            return object()

        module.empty = empty
        module.uint8 = "uint8"
        _torch_module.mod = module
    return _torch_module.mod
