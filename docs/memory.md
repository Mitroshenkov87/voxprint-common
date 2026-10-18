# Memory

Host RAM is separate from the GPU tier in `gpu_probe`. Compute stays on the GPU. `voxprint_common.ram_probe` reads the machine and chooses a tier. `voxprint_common.model_park` keeps one process's weights on disk, in pageable RAM, or in pinned RAM.

Torch is not a core dependency. `pip install voxprint-common[torch]` provides it. The park imports torch when it first builds a staging buffer.

## Tier

`total_gib` and `available_gib` use `GlobalMemoryStatusEx` on Windows and `/proc/meminfo` on Linux.

| Installed RAM | Tier |
| --- | --- |
| 88 GiB and above | `ram96` |
| 44 GiB and above | `ram64` |
| Below 44 GiB | `ram32` |

Below 24 GiB the tier is still `ram32`, and a warning is logged. A 32 GB machine reports about 31.7 GiB. A 48 GB machine reports about 44.7 GiB and is `ram64`.

`ram_tier` checks `VOXPRINT_RAM_MODE`, then the suite setting `memory.ram_mode` (`auto`, `ram32`, `ram64`, or `ram96`, default `auto`), then the table above. `auto` means the table.

The pin budget is 0 on `ram32`. Otherwise it is the smaller of half of installed RAM on Windows, or three fifths elsewhere, and installed RAM minus 12 GiB. `ram64` is also capped at a quarter of installed RAM.

| Tier | Pin budget | Example |
| --- | --- | --- |
| `ram32` | 0 | 0 |
| `ram64` | min(0.5 x RAM on Windows or 0.6 x RAM elsewhere, RAM - 12 GiB, 0.25 x RAM) | 48 GiB: 12 GiB; 64 GiB: 16 GiB |
| `ram96` | min(0.5 x RAM on Windows or 0.6 x RAM elsewhere, RAM - 12 GiB) | 96 GiB: 48 GiB on Windows, 57.6 GiB elsewhere |

## Park

`ModelPark.for_process()` returns one park for the process. `get(key, loader, size_bytes=..., role=...)` returns a `Parked` value. `loader` returns a state dict. `role` is `main` (the generator), `aux` (an encoder, VAE, or ControlNet), or `optional` (a LoRA or an alternate). `Parked.to(device)` copies tensors onto that device. `Parked.release()` drops the in-use count. Weights that the policy keeps stay parked.

| Tier | main | aux | optional |
| --- | --- | --- | --- |
| `ram32` | disk, freed after release | disk | disk |
| `ram64` | pinned while the budget allows, otherwise pageable | pageable, least-recently used | disk |
| `ram96` | pinned | pinned | pinned, otherwise pageable |

Pins are one tensor at a time, and never past the budget. One tensor is pinned only up to 2 GiB, or 4 GiB on `ram96`. A larger tensor stays pageable. A 12 GiB `pin_memory` can fail with a CUDA out-of-memory error while RAM is still free, which is why the cap is there. `pin_memory` raising `RuntimeError` counts a fallback, leaves the tensor pageable, and sets this process's budget to the bytes it has already pinned.

Pageable copies, including on `ram32`, go through `StagingRing`: two 256 MiB pinned buffers, created on first use. The tensor is copied through a buffer and then to the device with `non_blocking=True`. When the ring cannot be pinned, the copy is blocking.

Unreferenced entries are evicted least-recently used. A pinned entry becomes pageable. A pageable entry is dropped. When available RAM is under 6 GiB, the park repeats that until available RAM is above 8 GiB, or nothing unreferenced is left.

`prefetch(keys)` loads keys on a background thread for `ram64` and `ram96`. `ram32` does not prefetch. `stats()` returns pinned, pageable, and disk bytes, plus how many pin attempts fell back and how many `get` calls reused a parked model.

`<home>/state/ram-park.json` lists `{pid, app, pinned_bytes}` for each live process, under the suite file lock. A pid that is no longer running is removed. This process may pin the global budget minus what the other rows hold.

## API

::: voxprint_common.ram_probe

::: voxprint_common.model_park
