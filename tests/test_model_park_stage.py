"""RAM hot tier: one disk read, VRAM swaps, dtype kept."""

from __future__ import annotations

import logging
import subprocess
import sys

from voxprint_common.model_park import Direction, ModelPark
from voxprint_common.ram_probe import GIB, RamTier


class _Tensor:
    def __init__(self, nbytes: int, dtype: object | None = None, *, fail: bool = False) -> None:
        self.nbytes = nbytes
        self.dtype = dtype
        self.fail = fail
        self.pinned = False
        self.pins = 0

    def pin_memory(self) -> _Tensor:
        self.pins += 1
        if self.fail:
            raise RuntimeError("pin failed")
        self.pinned = True
        return self

    def to(self, device: object, non_blocking: bool = False, **kwargs: object) -> _Tensor:
        if "dtype" in kwargs:
            raise AssertionError(kwargs["dtype"])
        out = _Tensor(self.nbytes, self.dtype)
        out.device = device
        out.non_blocking = non_blocking
        return out


def _park(tier: RamTier, **kwargs: object) -> ModelPark:
    return ModelPark(tier, app="canvas", total=96, windows=False, **kwargs)


def _loader(tensor: _Tensor, calls: list[str], key: str):
    def load() -> dict[str, _Tensor]:
        calls.append(key)
        return {"w": tensor}

    return load


def test_stage_swaps_vram_without_rereading_disk(caplog) -> None:
    assert ModelPark(RamTier.RAM32, total=32, windows=False).ram_budget() == 0
    assert ModelPark(RamTier.RAM64, total=64, windows=False).ram_budget() == 16 * GIB
    assert ModelPark(RamTier.RAM96, total=96, windows=False).ram_budget() == 84 * GIB
    assert ModelPark(RamTier.RAM96, total=96, windows=False).vram_limit() == 15 * GIB

    park = _park(RamTier.RAM96, vram_budget=1000)
    assert park.vram_limit() == 900
    calls: list[str] = []
    tensors = {key: _Tensor(400, "float8_e4m3fn") for key in ("a", "b", "c")}
    for key, tensor in tensors.items():
        park.get(key, _loader(tensor, calls, key), size_bytes=400, role="main")
    assert calls == ["a", "b", "c"]
    with caplog.at_level(logging.INFO, logger="voxprint_common.model_park"):
        with park.stage("cook", models=["a", "b", "c"], device="cuda") as placed:
            assert set(placed) == {"a", "b"}
            assert placed["a"]["w"].dtype == "float8_e4m3fn"
            assert park.stats().vram_bytes == 800
            assert park.stats().ram_bytes == 1200
        assert park.stats().vram_bytes == 0
        with park.stage("finish", models=["c"], device="cuda") as placed:
            assert set(placed) == {"c"}
            assert park.stats().vram_bytes == 400
    assert park.stats().vram_bytes == 0
    assert park.stats().ram_bytes == 1200
    assert park.stats().disk_reads == {"a": 1, "b": 1, "c": 1}
    park.get("a", _loader(tensors["a"], calls, "a"), size_bytes=400, role="main")
    assert calls == ["a", "b", "c"]
    assert park.stats().reloads == 1
    assert "already parked" in caplog.text
    directions = [item.direction for item in park.stats().timings]
    assert Direction.RAM_TO_VRAM in directions
    assert Direction.VRAM_TO_RAM in directions
    assert any(
        "direction=ram_to_vram" in record.message and "bytes=400" in record.message
        for record in caplog.records
    )

    small = ModelPark(RamTier.RAM32, app="canvas", total=32, windows=False, vram_budget=2000)
    seen: list[int] = []

    def load_small() -> dict[str, _Tensor]:
        seen.append(1)
        return {"w": _Tensor(100)}

    small.get("m", load_small, size_bytes=100, role="main").release()
    assert small.stats().ram_bytes == 0
    with small.stage("cook", models=["m"], device="cuda") as placed:
        assert "m" in placed
        assert small.stats().vram_bytes == 100
    assert seen == [1, 1]
    assert small.stats().disk_reads["m"] == 2
    assert small.stats().vram_bytes == 0
    assert small.stats().ram_bytes == 0


def test_transfers_keep_fp8_dtype_and_do_not_import_torch() -> None:
    park = _park(RamTier.RAM96)
    good = _Tensor(100, "float8_e4m3fn")
    bad = _Tensor(80, "float8_e5m2", fail=True)
    park.get("good", lambda: {"w": good}, size_bytes=100, role="main")
    park.get("bad", lambda: {"w": bad}, size_bytes=80, role="main")
    assert good.pinned is True
    assert bad.pinned is False
    assert park.stats().fallbacks >= 1
    assert park.stats().pinned == 100
    assert park.stats().pageable == 80
    with park.stage("cook", models=["good", "bad"], device="cuda") as placed:
        assert placed["good"]["w"].dtype == "float8_e4m3fn"
        assert placed["bad"]["w"].dtype == "float8_e5m2"
    script = (
        "import sys, voxprint_common.model_park; "
        "raise SystemExit(1 if 'torch' in sys.modules else 0)"
    )
    completed = subprocess.run([sys.executable, "-c", script], check=False)
    assert completed.returncode == 0
