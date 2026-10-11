# Model downloader

VOXPrint MDL is the suite's model downloader. The display name lives in `voxprint_common.mdl.DISPLAY_NAME`. The command is `voxprint-mdl`, and `python -m voxprint_common.mdl` runs the same entry point.

Audiobook Builder, Plotweaver, Movie Dubber, Clarity, Enlarge, and Canvas do not download models. The installer copies the app, then runs this command. The app opens its own window only after the status file says ready.

## What the installer runs

After the files are in place:

```bash
voxprint-mdl download --manifest "/opt/voxprint-ai-suite/enlarge/mdl.json" --gui
```

On Windows the same flag points at `C:\Program Files\VOXPrint AI Suite\Enlarge\mdl.json`.

`--gui` opens the progress window. It needs `voxprint-common[ui]`. The labels are Russian: the step list columns are model, size, source, speed, and time remaining, with overall progress, Отмена, and Повторить after a failure. Without `--gui` the job is headless and suitable for a silent install.

Omit `--manifest` to discover every installed app. Discovery reads `<home>/state/installed-apps.json` when that file exists (`{"schema": 1, "apps": [{"id": "enlarge", "path": "..."}]}`). An empty registry means nothing is installed. When the registry is absent, the command looks for `mdl.json` under the suite root: `VOXPRINT_SUITE_ROOT`, otherwise `%PROGRAMFILES%\VOXPrint AI Suite\<App Name>` on Windows and `/opt/voxprint-ai-suite/<app>` on Linux.

`--json` prints progress lines and one result line on stdout. `--dry-run` plans and checks free disk space, and does not fetch. Both flags may sit before or after the subcommand.

```bash
voxprint-mdl --json download --dry-run --manifest ./mdl.json
voxprint-mdl plan
voxprint-mdl verify
voxprint-mdl status --json
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. `ready` on the result, and in the status file, is what the app checks. |
| 1 | Usage error. |
| 2 | The operation failed. A short disk, a digest mismatch, and a network failure use this code. A short disk sends no request. |
| 3 | Cancelled. |

## How an app checks ready

The status file is `<home>/state/mdl-status.json`. `<home>` is `VOXPRINT_HOME`, otherwise `%LOCALAPPDATA%\Voxprint` or `~/.local/share/voxprint`. The file is UTF-8 JSON, schema 1. `ready` is true only when `state` is `ready`. A missing file means idle, which is not ready. A damaged file is an error, not a success.

```python
from voxprint_common.mdl import ready

if ready():
    open_main_window()
```

`status --json` prints the same snapshot for an installer that would rather poll the process output. Do not treat a running download as ready: `state` stays `downloading` until every planned file has been verified.

## mdl.json

Schema 1. One file per app, named `mdl.json`.

```json
{
  "schema": 1,
  "app": "enlarge",
  "models": [
    {
      "id": "realesrgan",
      "version": "1",
      "vram_tier": "8gb",
      "files": [
        {
          "path": "realesrgan.onnx",
          "size": 1234,
          "sha256": "<64 hex characters>",
          "mirrors": [
            {"source": "huggingface", "repo": "org/name", "filename": "realesrgan.onnx"},
            {"source": "kaggle", "owner": "mitroshenkov87", "dataset": "suite-models", "filename": "realesrgan.onnx"},
            {"source": "modelscope", "repo": "org/name", "filename": "realesrgan.onnx"},
            {"source": "gdrive", "id": "0123456789abcdef"}
          ]
        }
      ]
    }
  ]
}
```

`vram_tier` is `any`, `8gb`, `16gb`, or `24gb`. Several objects may share an `id` and differ by tier. The plan keeps the highest tier that fits the card, so a 24 GB machine does not also pull the 8 GB and 16 GB copies of that id. The 24 GB check is about 23.5 GiB, because cards report a little under the label. An unknown GPU is planned as 8 GB. `any` is used when no larger variant fits.

A mirror may also be an `http` or `https` URL string, or `{"source": "url", "url": "https://..."}`. `..` in a file path is rejected.

Files that share a sha256 across apps are stored once, at `<models_dir>/blobs/<sha256>`. Each app is a ref on that runtime-manifest component. The component id is `model-` plus the digest prefix, and the component version is the full sha256, so a second app reuses the bytes instead of writing over them.

## Disk, resume, and mirrors

Before the first request, free space must cover the bytes still missing plus 15 percent. The error includes the product name and both byte counts (needed, and free). Bytes already in a finished file of the expected size are not counted again.

Transfers use HTTP Range and keep the `.partial` file across a dropped connection. Mirrors are timed and the faster one is used; a mirror that recovers and measures faster is selected again. After the digest matches, the file is renamed into place. A mismatch deletes the partial and tries another mirror.

Hugging Face URLs use `hf_xet` when that package is installed, `HF_HUB_DISABLE_XET` is unset, and the response offers an xet hash. Otherwise the transfer stays on HTTP Range. Install the package beside the library when you want it: `python -m pip install hf-xet`.

The job lowers CPU and IO priority on Windows and Linux, and holds a sleep inhibit through `voxprint_common.power.keep_awake` for as long as it runs. `--gui` also asks to keep the display awake.
