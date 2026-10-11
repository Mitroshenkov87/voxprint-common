# Model downloader

VOXPrint MDL is the suite's model downloader. The display name lives in `voxprint_common.mdl.DISPLAY_NAME`. The command is `voxprint-mdl`, and `python -m voxprint_common.mdl` runs the same entry point.

Audiobook Builder, Plotweaver, Movie Dubber, Clarity, Enlarge, and Canvas do not download models. The installer copies the app, then runs this command. The app opens its own window only after the status file says ready.

## What the installer runs

MDL never starts an app window. The installer copies the app, enqueues the models, then waits until the status file is ready. It must not open the app before that.

```bash
voxprint-mdl enqueue --app enlarge --manifest "/opt/voxprint-ai-suite/enlarge/mdl.json"
voxprint-mdl download --app enlarge --manifest "/opt/voxprint-ai-suite/enlarge/mdl.json" --gui
```

`download` enqueues and then runs the worker, or attaches if another process already holds it. `enqueue` only appends the job. `status --wait` attaches until the queue is idle. On Windows the manifest path is `C:\Program Files\VOXPrint AI Suite\Enlarge\mdl.json` (or `voxprint-app-models.json`).

`--gui` opens the download window. It needs `voxprint-common[ui]`. Labels come from `voxprint_common.i18n` (English, German, Russian, Ukrainian, Latvian). Without `--gui` the job is headless and suitable for a silent install. A hook failure uses exit code 2. The window's Retry runs the job again.

Apps keep a Manage models button and drop their own download screens. The button runs:

```bash
voxprint-mdl --gui --app clarity
```

That focuses the single window on the manage page for that app. `voxprint-mdl worker` keeps the queue and the window process running. A second launch, from another installer or from Manage models, focuses the window that is already open. New files appear in the same queue, grouped by app. Closing the window does not cancel the transfer. On Windows the process stays in the notification area when a tray icon is available. Otherwise the window says that downloads continue in the background.

Omit `--manifest` to discover every installed app. Discovery reads `<home>/state/installed-apps.json` when that file exists (`{"schema": 1, "apps": [{"id": "enlarge", "path": "..."}]}`), then `mdl.json`, then `voxprint-app-models.json`. An empty registry means nothing is installed. When the registry is absent, the command looks under the suite root: `VOXPRINT_SUITE_ROOT`, otherwise `%PROGRAMFILES%\VOXPrint AI Suite\<App Name>` on Windows and `/opt/voxprint-ai-suite/<app>` on Linux.

`--json` prints progress lines and one result line on stdout. `--dry-run` plans and checks free disk space. It does not enqueue and does not fetch. Both flags may sit before or after the subcommand.

```bash
voxprint-mdl --json download --dry-run --manifest ./mdl.json
voxprint-mdl enqueue --app enlarge --json
voxprint-mdl plan
voxprint-mdl verify
voxprint-mdl status --json --wait
```

From Python, `enqueue(app)` returns a job id and `ensure_ready(app)` enqueues, runs or attaches, and returns `Ready` or `Failed`.

## Queue

There is one queue, `<home>/state/mdl-queue.json`, schema 1, UTF-8, written atomically under `mdl-queue.lock`. Installers and apps only enqueue. One worker holds `mdl-worker.lock`. Everyone else leaves the job in the queue or attaches through status and the progress events. A crash leaves jobs in `running` and `.partial` files in place; the next worker resumes them with HTTP Range. Files already in the shared models directory are not downloaded again: a matching size plus a `.verified` marker is enough, and the digest is read only when that marker is missing. The app is still added as a ref.

Two apps that need the same sha256 share one download. The default is two files at a time (`mdl_parallel_files` in the suite settings, 1 through 8). A request that arrives while the worker is running is appended.

## Progress events

`TransferSession` is the in-process API: `subscribe`, `publish`, `pause`, `resume`, and `cancel`. A `ProgressEvent` carries `ItemProgress` (id, display name, bytes done and total, speed, eta, source, state, apps, reason) and `JobProgress`, grouped by app with `jobs_by_app()`. Item states include `pending`, `downloading`, `paused`, `verified`, `failed`, `cancelled`, `error`, and `unavailable`. A cross-process window polls the status file. `reason` is the unavailable explanation; the existing table shows it in the source column.

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

Files that share a sha256 across apps are stored once, at `<models_dir>/blobs/<sha256>`. Each app is a ref on that runtime-manifest component. The component id is `model-` plus the first 12 hex characters of the file sha256. An older `model-` id that already records that digest is reused. The component version is the full sha256, so a second app reuses the bytes instead of writing over them.

A side virtualenv uses `runtime-py<major><minor>-<stack>-cu<version>`, from `runtime_component_id`. Examples: `runtime-py311-nunchaku-cu130` and `runtime-py314-torch2.11-cu130`.

A model may set `status` to `unavailable` with a `reason` such as `model_in_preparation`. It is shown in the progress list, left out of the byte total, and never downloaded. Asking for that id by name is an error. `voxprint-app-models/1` can list the same fact under `unavailable`.

Hugging Face mirrors need a revision. `main` is accepted when the manifest says it. An empty or missing revision is refused. Downloads are HTTPS and stay on the host allowlist (`huggingface.co`, `hf.co`, `kaggle.com`, `modelscope.cn`, `drive.google.com`, `docs.google.com`, `googleusercontent.com`, `github.com`, `githubusercontent.com`), including redirects. Private, loopback, and link-local addresses are refused. Tests opt in with `UrlGuard.loopback()` or `VOXPRINT_MDL_TEST_GUARD=loopback`.

An entry whose only URL is a gated upstream (`pyannote/speaker-diarization-community-1`, `pyannote/embedding`) is rejected. The suite copies live at `Mitroshenkov87/voxprint-pyannote-speaker-diarization-community-1` (revision `9c851af7`) and `Mitroshenkov87/voxprint-pyannote-embedding` (revision `4655ca65`), with the same dataset names under the Kaggle owner `mitroshenkov87`. `public_mirror_urls` builds those URLs and does not read `HF_TOKEN`. The bundled index pins the example descriptors in this package until Forge publishes `voxprint-model.json` in the weight repos. A remote index is used only when its sha256 matches that pin.

A model, or the app, may declare `post_install`:

```json
{"argv": ["voxprint-enlarge-cli", "--prepare-model", "{id}"], "timeout_s": 1800, "produces": ["model.onnx"]}
```

The worker runs it after the digest check and the atomic rename. `{id}`, `{model_dir}`, and `{models_root}` are filled in. The executable is resolved from the app install directory (and `bin/`), never from `PATH`. On Windows the process is created with `CREATE_NO_WINDOW`. The hook is skipped when every `produces` path exists and the marker under `<home>/state/mdl-hooks/` records the source sha256. `verify` runs it again when the marker or the digest differs. Failure sets the item to `error` with a short message and exit code 2. Stderr is appended to `mdl-hook.log` and is not shown in the window. The item is ready only after the hook succeeds. Enlarge uses this to turn a downloaded `.pth` into an fp16 ONNX file.

## Disk, resume, and mirrors

Before the first request, free space must cover the bytes still missing times 1.10, plus 2 GiB. Nothing is fetched when that check fails. The error names VOXPrint MDL and both byte counts. Bytes already in a finished file of the expected size are not counted again.

Transfers use HTTP Range and keep the `.partial` file across a dropped connection. A file larger than 256 MiB is split into 4 to 8 ranges when the server answers 206, with a per-host cap and a shared connection budget. Each part resumes from `.partial.parts`. A server that ignores Range falls back to one stream. Mirrors are timed. A mirror at or under 84 KiB/s is dropped when another mirror is faster, and a mirror that recovers and measures faster is selected again. After the digest matches, the file is renamed into place. A mismatch deletes the partial and tries another mirror.

Hugging Face URLs use `hf_xet` when that package is installed, `HF_HUB_DISABLE_XET` is unset, and the response offers an xet hash. Otherwise the transfer stays on HTTP Range. Install the package beside the library when you want it: `python -m pip install hf-xet`.

The job lowers CPU and IO priority on Windows and Linux, and holds a sleep inhibit through `voxprint_common.power.keep_awake` for as long as it runs. `--gui` also asks to keep the display awake.

## Download window

There is one window for the whole queue. Each app is a group with that app's splash motif. Overall progress is the bytes of every queued file, eased so the bar does not jump backwards. An app can show "Open" while another app is still downloading. The Open button is the only thing that launches an app.

Pause and Resume talk to the worker's `TransferSession`. Cancel asks for confirmation and says the download can resume later. Errors are a short "What happened" and "What to do", with Retry. The traceback stays behind Details and in the log file.

Manage models lists installed files with size and status, and optional models this GPU tier can run. A model whose minimum VRAM is above the card is hidden. Add enqueues that model. Remove drops this app's ref and deletes the file only when no other app still uses it. Verify re-hashes the plan.

The window reads the queue in `voxprint_common.mdl.queue`. It does not write a second queue file.

![Download queue in English](img/mdl-window/en-queue.png)

![Two apps, Clarity ready while Enlarge is still downloading](img/mdl-window/en-multi.png)

![A disk error with Retry](img/mdl-window/en-error.png)

![Paused](img/mdl-window/en-paused.png)

![All set](img/mdl-window/en-all-set.png)

![Manage models](img/mdl-window/en-manage.png)

![The same queue in Russian](img/mdl-window/ru-queue.png)

![A long German error on a 720-wide window](img/mdl-window/de-error.png)

![A long Latvian queue on a 720-wide window](img/mdl-window/lv-queue.png)

## Languages

`voxprint_common.i18n` loads one JSON file per language from `locales/`. English is the source and the fallback. The package ships `en`, `de`, `ru`, `uk`, and `lv`. The active language is the `ui_language` key when that key is present in `suite.json`, then `VOXPRINT_LANG`, then the system UI locale, then English. `add_catalogs` lets an app point at its own `locales` directory. Later folders override the same key.

Apps check their own UI in CI:

```yaml
- name: UI strings
  run: python -m voxprint_common.i18n.check --src src/<pkg> --locales src/<pkg>/locales --langs en,ru,de,uk,lv
```

The checker fails on a hard-coded user-facing string in a Qt call, a key missing from a shipped locale, or a `{placeholder}` sequence that differs between languages. An unused key is a warning. `# i18n: ignore` skips one call. Splash titles use `splash.app.<id>` and keep the same brand spelling in every language.
