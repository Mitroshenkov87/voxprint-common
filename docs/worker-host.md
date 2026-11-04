# Worker host

Model work runs in a child process. The UI process only queues jobs and reads events. A hung or crashed model takes the child with it. The window stays up. When that process exits, the operating system releases its GPU memory.

Canvas adopts this first. Model Forge adopts it in the same round. Its jobs are training, captioning, and face or age checks. Those checks are ordinary jobs. This package does not filter prompts or pictures.

Importing `voxprint_common.worker` does not load Qt and does not start a process. The Qt adapter is `voxprint_common.worker.qt`.

## API

A handler path is `module:callable`. The child imports it. Pass picklable payloads. Do not pass Qt objects, lambdas, or closures.

```python
from voxprint_common.worker import (
    CheckHook,
    Handlers,
    JobKind,
    JobSpec,
    RestartPolicy,
    WorkerHost,
)

host = WorkerHost(
    Handlers(
        run="myapp.worker_jobs:run",
        load_model="myapp.worker_jobs:load_model",
        unload_model="myapp.worker_jobs:unload_model",
        check="myapp.worker_jobs:check",
    )
)
job_id = host.submit(
    JobSpec(JobKind.INFERENCE, payload={"prompt": prompt}, model="sdxl", phase="picture")
)
```

`start` spawns the child with the `spawn` start method. `submit` starts it when it is not running. Jobs run in submit order. Each job has an id.

`poll` reads events and does not block. Call it from the UI thread. `wait` blocks. Tests and headless tools may use it. The UI must not.

Events are progress, finished, failed, cancelled, crashed, loaded, and unloaded. Progress carries `step`, `epoch`, `total_steps`, `total_epochs`, `eta_s`, `metrics`, `fraction`, `phase`, and a short `message`. The app translates the message before the handler sends it.

`cancel` asks a running job to stop. The handler should notice `ctx.cancelled()`, write a checkpoint, call `ctx.note_checkpoint(path)`, and raise `JobCancelled` or return. If the job is still running after `cancel_timeout` (default 5 seconds), the host kills the process. A job that has not started is cancelled in the parent and never sent.

A dead child is started again after a backoff. The first delay is `backoff_initial` (default 0.25 seconds). Each further death doubles it, up to `backoff_max` (default 8 seconds). A finished job resets the delay.

Restart policy is per job:

| Kind | Default | After a crash |
| --- | --- | --- |
| `training` | `RESUME` | Queue the same job again with the last checkpoint path |
| `inference` | `FAIL` | Fail the job. Do not run it again |
| `caption` | `FAIL` | Fail the job. Do not run it again |
| `check` | `FAIL` | Fail the job. Do not run it again |

`JobSpec.restart` overrides the default. `max_resumes` is how many times a resume policy may start the job again (default 1). When the limit is used up, the job fails and the message says resume stopped. A crash with no checkpoint yet resumes from the start for a training job. The message says so.

A handler can also finish the job and leave the process at once. `ctx.reply_and_exit(result)` sends the same final reply a normal return sends, then exits the child with code 75 (`EXIT_GPU_UNRECOVERABLE`). Use it when the job already has its result but the GPU is in a state the process must not keep serving, such as after a CUDA error: the exit hands the VRAM back immediately. When the host has the clean final reply for the job, that exit is a planned restart. The job is delivered as finished, the worker starts again without a crash event and without backoff, and the GUI shows no crash InfoBar. Exit 75 without that reply is still a crash, with the usual fail-or-resume rules.

`resume(job_id)` queues a cancelled or failed job again from the checkpoint it noted. Training crashes do not need this call. Cancel does not start the job again by itself. The checkpoint path is on the outcome.

The child keeps one model loaded. A job names it with `model`. The next job with the same id reuses `load_model`'s return value. A different id unloads the previous model first. An empty queue unloads it too. `phase` is only a label on events, so a batch of caption jobs can share a model until the queue drains. Two heavy models at once are not supported. Load them as one object if a step needs both.

The child sends a heartbeat from a side thread every `heartbeat_interval` seconds (default 1). A long step that still lets Python schedule that thread does not fail the job. `ctx.beat()` and `ctx.progress(...)` count as heartbeats too. If none arrive for `heartbeat_timeout` seconds (default 30, or `JobSpec.heartbeat_timeout`), the host cancels cooperatively and then kills the process. Set the timeout above the longest stretch that holds the interpreter. Pass `heartbeat_interval=0` only when the handler calls `beat` itself.

`shutdown` asks the child to unload and exit, then kills it if it is still alive. Killing the process, or the process exiting, is what gives the memory back. `unload_model` is not a substitute for that. If the UI process disappears, the child exits as well.

CheckGate holds a `CheckHook` and calls `run(payload, model=..., phase=...)`. That queues a `check` job. The hook does not read the payload. The check callable is the worker's `check` handler, or `run` when `check` is omitted.

### Qt

```python
from voxprint_common.worker.qt import QtWorkerHost, install_crash_bar

adapter = QtWorkerHost(host)
adapter.progress.connect(on_progress)
adapter.finished.connect(on_finished)
adapter.failed.connect(on_failed)
adapter.crashed.connect(on_crashed)
install_crash_bar(window, adapter, title=title, body=body)
```

`QtWorkerHost` is a `QTimer` over `poll`. The signals are `progress`, `finished`, `failed`, and `crashed`. Each emits the same event object `poll` returns. A cancelled job is delivered on `failed`, with kind `cancelled`. The timer does not call `wait`. Construct the adapter on the UI thread.

`install_crash_bar` shows an `InfoBar` and does not quit. Pass title and body that are already translated. On `aboutToQuit`, call `adapter.shutdown()`.

### Handler

```python
def load_model(model_id: str) -> object:
    ...

def unload_model(model_id: str, loaded: object) -> None:
    ...

def run(ctx) -> object:
    if ctx.cancelled():
        path = save(ctx)
        ctx.note_checkpoint(path)
        raise JobCancelled(path)
    ctx.progress(step=step, epoch=epoch, total_steps=steps, eta_s=eta, metrics={"loss": loss})
    return result
```

`ctx.model_object` is the loaded model, or None. `ctx.checkpoint` is the resume path, or None on the first attempt. `ctx.kind` is the job kind. Return a picklable value. `ctx.reply_and_exit(result)` sends the final reply and exits the process, for a GPU the job can no longer use. The host restarts the worker without a crash.

## Why this IPC

The child is a separate process started with `spawn`.

A `QThread` runs in the UI process. A hard crash in native code, or a CUDA call that never returns, freezes or kills the window. Killing a thread does not unload a CUDA context cleanly. The memory stays until the process exits.

`fork` copies the parent address space. CUDA does not support that. The child would see a context it must not use.

A fresh interpreter avoids both. `multiprocessing` pipes carry the commands and the events. `send` writes the bytes before it returns, so a checkpoint note is not lost if the handler then calls `os._exit`. The parent reads with `poll(0)`, so the UI thread never blocks on the pipe. Cancel uses a second pipe, because the child cannot read the command pipe while it is inside a job.

The parent keeps the queue. The child runs one job at a time. A cancel of a job that has not started never reaches the child. Spawn can import a `module:callable` string on Windows, where a local function cannot be pickled.

The heartbeat is a small message on a timer thread inside the child, plus any `beat` or progress the handler sends. The hang rule is "no heartbeat", not "no new step". A long training step can keep the job alive. A process that is no longer scheduling Python cannot send either kind, and the host closes it.

## Risks

* Spawn pays the import cost of the handler module, including torch, on first start. Keep the process up across jobs. Do not start one child per picture.
* The UI must keep polling. A stuck UI thread will not kill a healthy child. It also will not read events. The pipes are not bounded by the host. A handler that emits progress in a tight loop can grow the parent's memory if nobody calls `poll`.
* Heartbeats mean the interpreter is scheduling, not that the GPU is making progress. A CUDA deadlock that still runs the heartbeat thread looks alive. Set `heartbeat_timeout` from the longest acceptable step, and call `beat` at step boundaries, if that is the failure you need to catch. A step that holds the GIL longer than the timeout looks hung even though it will finish. Raise the timeout for that job.
* `Process.kill` does not run `unload_model` or finish a checkpoint write. Call `note_checkpoint` only after the file is flushed. A kill can leave a partial file. Training resume has to tolerate that. The host only remembers the last path that was noted.
* Cancel waits, then kills. A kill ends the process. The next job pays for a new load. That is the point of the timeout.
* One resident model. A second id unloads the first. VRAM for the first is released only if `unload_model` drops it, or if the process exits.
* Backoff delays the next spawn. A burst of crashes does not tight-loop. The in-flight inference job is already failed. A training job stays open until resume or until `max_resumes` is used up.
* Payloads and results must pickle. A failure to pickle a result fails the job with a short error. It does not stay running.
* The child exits if the UI process disappears. Do not detach it.
* This host is not a second GPU lock. Apps still take `voxprint_common.gpu_lock` around the work that uses the device, from the child.

## Adoption

### Canvas

Canvas is first. Move sampling and the other model calls out of the UI process and out of any `QThread`.

* One handler module, imported by string. The UI process does not import torch.
* One job per stage, or one job per picture. Stages of one picture that share a checkpoint use the same `model` id and a `phase` such as the picture id. The model stays loaded until the queue is empty or the next job names another model.
* Kind `inference`. Leave the restart policy as fail. A crashed picture shows the failure. It does not start again on its own.
* Build `QtWorkerHost` next to the main window. Drive the progress label from `progress`. A finished event stores the picture. A failed event shows the message.
* Connect `crashed` to an InfoBar. Do not call `QApplication.quit`. The bar in `install_crash_bar` is the small version. A Qt-Fluent `InfoBar` is fine if its object name or class name is `InfoBar`.
* `aboutToQuit` calls `adapter.shutdown()` so the child exits and VRAM comes back.
* If Canvas runs a CheckGate check, give the gate a `CheckHook(host)`. Do not run that check on the UI thread.

Suggested timeouts for a picture: heartbeat interval 1 second, heartbeat timeout 30 seconds, cancel timeout 5 seconds. Raise the heartbeat timeout only for a stage that blocks the interpreter longer than that.

### Model Forge

Model Forge adopts the same host in this round. The jobs are training, captioning, and face or age checks.

* Training uses kind `training`. The default policy resumes. Pass `checkpoint` on the spec when the user continues a run. The handler reads `ctx.checkpoint`, trains, and calls `note_checkpoint` after each flush of the file. A crash or a kill after that note queues the same job id again with that path. `max_resumes` should match how many unattended restarts are acceptable on a laptop. After cancel, the outcome's checkpoint is the file to offer in the UI. Call `resume(job_id)` when the user continues. Cancel itself does not resume.
* Cooperative cancel saves the checkpoint and returns. Do not wait for the host to kill the process. A kill can cut the write.
* Progress for a multi-hour run includes `step`, `epoch`, `total_steps` or `total_epochs`, `eta_s`, and metrics such as loss. The heartbeat thread covers a long step that still runs Python. Set `heartbeat_timeout` above the longest step that does not return to Python. Call `ctx.beat()` at least once per step so a stuck step is still visible if you disable the automatic heartbeat.
* Captioning uses kind `caption` and fails fast. A folder of images is a series of jobs with the same `model` and one `phase`. The caption model stays loaded across that batch and unloads when the queue drains.
* Face and age checks use kind `check`, or `CheckHook.run`. They fail fast. They can name the detector as `model` so a batch reuses it. The payload is whatever Forge already uses. The host does not interpret it.
* The window uses the same `QtWorkerHost` signals. A crash shows an InfoBar and does not exit, including while a training job is about to resume.
* Shut the adapter down when the window closes. A training cancel should have noted its checkpoint before that, or the latest noted path is what resume will see.

## Laptop proof

Baseline: a 16 GB card. `voxprint_common.gpu_probe` calls that tier `16gb` when nvidia-smi reports at least 15.5 GiB and below 23.5 GiB. The proof is that killing the worker returns the free memory. It does not download a checkpoint. A CUDA tensor of about 1 GiB is enough to see the drop on a 16 GB card.

Save this as `voxprint_vram_proof.py` on `PYTHONPATH` and run `python -m voxprint_vram_proof` on the laptop. Torch is imported only inside the child.

```python
"""Show that killing the worker returns VRAM. Run on a machine with a GPU."""

from __future__ import annotations

import os
import subprocess
import time

from voxprint_common.worker import Handlers, JobKind, JobSpec, WorkerHost

GIB = 1024**3


def load_model(model_id: str) -> dict[str, object]:
    import torch

    tensor = torch.empty(GIB // 4, dtype=torch.float32, device="cuda")
    torch.cuda.synchronize()
    return {"id": model_id, "tensor": tensor}


def unload_model(model_id: str, loaded: object) -> None:
    del model_id, loaded
    import torch

    torch.cuda.synchronize()


def run(ctx: object) -> dict[str, int]:
    import torch

    torch.cuda.synchronize()
    free, total = torch.cuda.mem_get_info()
    return {"free": int(free), "total": int(total)}


def _smi_free() -> int | None:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    line = completed.stdout.strip().splitlines()[0]
    return int(line.split()[0]) * 1024**2


def main() -> None:
    import torch

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is not available")
    free_before, total = torch.cuda.mem_get_info()
    print(f"card {total / GIB:.1f} GiB, free before {free_before / GIB:.2f} GiB")
    host = WorkerHost(
        Handlers(
            run="voxprint_vram_proof:run",
            load_model="voxprint_vram_proof:load_model",
            unload_model="voxprint_vram_proof:unload_model",
        )
    )
    job = host.submit(JobSpec(JobKind.INFERENCE, model="proof"))
    outcome = host.wait(job, timeout=120)
    print(f"free inside worker {outcome.value['free'] / GIB:.2f} GiB")
    if free_before - outcome.value["free"] < 0.5 * GIB:
        raise SystemExit("the worker did not allocate about 1 GiB")
    pid = host.pid
    host.kill_worker()
    deadline = time.monotonic() + 10
    while host.pid == pid and time.monotonic() < deadline:
        host.poll()
        time.sleep(0.05)
    torch.cuda.synchronize()
    free_after, _total = torch.cuda.mem_get_info()
    smi = _smi_free()
    print(f"free after kill {free_after / GIB:.2f} GiB, nvidia-smi {smi}")
    if free_before - free_after > 256 * 1024**2:
        raise SystemExit("torch.cuda.mem_get_info did not return to the baseline")
    if smi is not None:
        print("nvidia-smi free MiB", smi // (1024**2))
    host.shutdown(timeout=2)
    print("VRAM returned")


if __name__ == "__main__":
    main()
```

`spawn` re-imports `voxprint_vram_proof`, so the handlers stay at module level and the process guard stays. `kill_worker` is `Process.kill`. On Windows that is `TerminateProcess`. Free memory within about 256 MiB of the starting `mem_get_info` value is the pass. `nvidia-smi` is the second reading, for the same moment. The UI process in this script imported torch so it can read `mem_get_info`. An app does not. The app can shell out to `nvidia-smi` for the before and after numbers and keep torch in the child only.

## Reference

::: voxprint_common.worker
