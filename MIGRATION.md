# Migrating an app to voxprint-common 0.2.0

voxprint-common is a library. Each app bundles the wheel in its installer, or installs that wheel into its own environment or the shared runtime. It is not a suite application, and adopting it is never a reason to install another app.

Pin `voxprint-common==0.2.4`. The import package is `voxprint_common`. `voxprint_common.API` is `1`.

## Changes in 0.2.4

Call `release_component` to drop a shared ref. It will not delete the models root, the runtime, or any directory another app still lists in `models/.users.json`. `ready(app)` checks file sizes and does not trust `mdl-status.json` alone. `voxprint-mdl verify --repair` clears a bad manifest path without deleting files.

## Changes in 0.2.3

The shared download window is the same entry point. Layout, copy, and number formatting are tighter. No installer change.

## Changes in 0.2.2

Apps drop their own model-download screens. A Manage models button runs `voxprint-mdl --gui --app <app_id>`. Installers that show progress call `voxprint-mdl download --gui`. Both open the same window. Strings go through `voxprint_common.i18n`, and an app can add its own catalog directory with `add_catalogs`.

## Changes in 0.2.0

Suite apps stop downloading models. Each app ships `mdl.json` beside its other files. The installer runs `voxprint-mdl download` after the copy step. The app calls `voxprint_common.mdl.ready()` and opens its window only when that returns true.

* The product name is `voxprint_common.mdl.DISPLAY_NAME` (`VOXPrint MDL`). The console script and `python -m voxprint_common.mdl` are `voxprint-mdl`.
* `plan`, `download`, and `verify` share `<models_dir>/blobs/<sha256>`. Each file is one `runtime_manifest` component of kind `model`, version equal to the sha256, with one ref per app. A component is removed only when its refs are empty, by the existing manifest rules.
* The plan keeps the highest VRAM variant of a model id that fits the card. A 24 GB card is about 23.5 GiB (`gpu_probe.TIER_24_MIN_GIB`). An unknown GPU is planned as 8 GB. Tier `any` is the fallback when no larger variant fits.
* Free space must cover the bytes still to fetch plus 15 percent. The error names both byte counts, and no request is sent.
* `voxprint_common.power.keep_awake` holds the machine awake. The downloader does not add a second helper.
* The status file is `<home>/state/mdl-status.json`, UTF-8, schema 1. `ready` is true only when `state` is `ready`.

## Changes in 0.1.2

These calls stay compatible.

* `voxprint_common.power.keep_awake(reason, display=False)` blocks system sleep for a long job. The context manager releases the hold on return, on an exception, and on cancellation. `acquire` and `release` are the same hold without `with`. Nested holds share one inhibit.
* On Windows the helper calls `SetThreadExecutionState` on a keeper thread, because that state is per-thread, and resets it to `ES_CONTINUOUS` on release. `display=True` adds `ES_DISPLAY_REQUIRED`.
* On Linux the helper calls logind `Inhibit("sleep:idle", "Voxprint", reason, "block")` through jeepney and holds the file descriptor. If that fails it tries `systemd-inhibit`, then the session screensaver `Inhibit`, then a logged no-op.
* `suite_settings.keep_awake_during_jobs` defaults to true. When it is false, `keep_awake` does not take an inhibit.

## Changes in 0.1.1

These calls stay compatible. A few results change.

* `buildinfo.load` searches the app package directory, the frozen executable directory, and the repository root. Pass a file or directory as `path` to skip the search. `build_info.json` beside `BUILD.json` is the frozen record. The build number is `GITHUB_RUN_NUMBER` plus `offset`. Without that variable, the recorded `build` is used. A missing recorded build is null, not 0.
* `Splash(..., on_click=...)` and `Splash.mouse_press` are the click hook. Apps do not use the private window.
* A `model` component path may be an absolute path under `paths.models_dir()`, `VOXPRINT_MODEL_CACHE`, or `model_roots`. Other absolute paths are still rejected.
* The stylesheet includes `QPushButton#gear` and `QPushButton#disclosure`.

Call the functions below instead of the private copies. Leave app-specific behaviour where this document says it stays. Paths are relative to each app repository, at the commits reviewed on 11 October 2026.

## paths

`voxprint_common.paths` replaces the suite-directory helpers. `home()` is the old `voxprint_home()`. Nothing in this module creates directories.

| App | Old module | What moves |
|---|---|---|
| Enlarge | `voxprint_engine/paths.py` | `voxprint_home`, `shared_dir`, `models_dir`, TensorRT cache |
| Enlarge | `enlarge/paths.py` | The same suite helpers. `app_home` and other Enlarge-only paths stay |
| Canvas | `canvas/infra/paths.py` | Suite home, shared, and models |
| Plotweaver | `src/plotweaver/paths.py` | Suite home, shared, and models |
| Audiobook Builder | `infra/paths.py` | Suite home, models, and state. Voices, packages, and backup paths stay |
| Movie Dubber | `infra/shared_paths.py`, `infra/paths.py` | Suite home and the shared models folder. Dubber-only paths stay |

`models_dir()` checks `VOXPRINT_MODELS_DIR` (absolute only), then `models_dir` in `state/suite.json`, then the legacy `state/models_dir.txt`, then `<home>/models`. `trt_cache_dir()` follows `VOXPRINT_TRT_CACHE`, then the platform cache. It does not follow `VOXPRINT_HOME`.

## atomic

`voxprint_common.atomic` replaces ad-hoc temporary-file publishes and adds a directory fsync after the rename.

| App | Old module | What moves |
|---|---|---|
| Plotweaver | `src/plotweaver/atomic.py` | `write_text` / rename helper |
| Enlarge | manifest writes inside `voxprint_engine/suite_runtime.py` | Temporary file plus rename |
| Canvas | manifest writes inside `canvas/infra/manifest.py` | Temporary file plus rename |
| Plotweaver | manifest writes inside `src/plotweaver/manifest.py` | Temporary file plus rename |

Use `write_text`, `write_bytes`, `write_json`, or `replace`. Use `exclusive_lock` for one read-modify-write. The lock file is not deleted. `LockTimeout` means the wait expired.

## hashing

`voxprint_common.hashing.sha256_file` replaces the streaming SHA-256 copies. `verify` raises `HashMismatch` when the digest differs.

| App | Old location |
|---|---|
| Enlarge | `voxprint_engine/store.py` (`sha256_file`) |
| Canvas | `canvas/infra/runtime.py`, `canvas/models/pin.py` |
| Plotweaver | `src/plotweaver/manifest.py`, `src/plotweaver/registry.py` |
| Model Forge | `src/voxprint_forge/hashing.py` |
| Clarity | `clarity/pipeline/resume.py`, `clarity/project.py` |
| Movie Dubber | `dubber/infra/model_store.py`, `dubber/core/vxdub.py`, `dubber/core/voice_catalog.py` |
| Audiobook Builder | `infra/model_mirrors.py`, `infra/portable.py`, `infra/backup.py`, `infra/assets.py`, `infra/model_release.py`, `infra/text_models.py`, `infra/voice_repository.py`, and the copies under `tools/` |

Backup, voice catalogues, and project files stay in the app. Only the hash function moves.

## buildinfo

`voxprint_common.buildinfo` is the one reader and the one writer. Canonical `BUILD.json` is exactly these keys:

```json
{"app": "enlarge", "version": "0.2.0", "rc": 4, "build": 120, "codename": "Sample"}
```

`rc` and `build` are integers or JSON null. `load` and `parse` still accept the files each app already ships:

| App | Old readers | File shape that is still accepted |
|---|---|---|
| Enlarge | `enlarge/appinfo.py`, `tools/build_number.py` | `version` (`0.2.0-rc.4`), `offset`, `codename` |
| Clarity | `clarity/appinfo.py`, `clarity/codenames.py` | `version` (`1.0.0-rc`), `build`, `offset`, `codename` |
| Canvas | `canvas/app/identity.py`, `canvas/app/info.py` | `version`, `channel` `RC`, `build`, `offset`, `codename` |
| Lucent | `voxprint_lucent/cli.py` | `number` (the build), `offset`, `codename` |
| Plotweaver | `src/plotweaver/appinfo.py`, `packaging/assemble.py` | `version`, `prerelease`, `codename`, `offset`, `build: null` |
| Model Forge | `src/voxprint_forge/version.py` | `offset`, `codename` |
| Audiobook Builder | `core/appinfo.py`, `tools/build_number.py`, `codenames.py` | `offset`, `codename` |
| Movie Dubber | `dubber/appinfo.py` | `offset`, `codename` |

`label()` returns `X.Y.Z RC N build B "Codename"`. A null build renders as `dev`. Canvas's channel `RC` with no number sets `show_rc` so the label still contains the word `RC`. `dump()` writes only the five canonical keys, so an app that rewrites `BUILD.json` should set `rc` to the candidate number.

Build number: `VOXPRINT_BUILD` wins. Otherwise `GITHUB_RUN_NUMBER` plus `offset` is the number. Without that variable, the recorded `build` (or Lucent `number`, or the frozen `build_info.json`) is used. A missing recorded build stays null.

## suite_settings

`voxprint_common.suite_settings` is the reader and writer for `<home>/state/suite.json`.

| App | Old module | What moves |
|---|---|---|
| Audiobook Builder | `infra/suite_settings.py` | `ui_language`, `theme`, `models_dir`, `gpu` |
| Movie Dubber | `dubber/infra/suite.py` | The same four keys |
| Enlarge | `enlarge/suite.py` | `gpu` and `models_dir` |

Unknown keys are preserved. Unknown theme ids are stored and read back as `glass-dark` by `theme()`. `cpu` is rejected. `cuda` and `gpu` become `cuda:0`. `VOXPRINT_MODELS_DIR` is applied by `paths.models_dir()`, not by the settings getters. The file lock is `suite.json.lock`.

## gpu_lock

`voxprint_common.gpu_lock` is the cross-process GPU lock. The best previous implementation was Enlarge's `voxprint_engine/gpu_lock.py` (exclusive create, parent-pid hand-off). The default owner is no longer Enlarge: every caller passes `app` and `stage`.

| App | Old module |
|---|---|
| Enlarge | `voxprint_engine/gpu_lock.py` |
| Canvas | `canvas/infra/gpu_lock.py` |
| Plotweaver | `src/plotweaver/gpu_lock.py` |
| Audiobook Builder | `core/gpu_lock.py` |
| Movie Dubber | `dubber/infra/gpu_lock.py` |

Clarity uses the engine copy, so it follows Enlarge. Lucent and Model Forge have no GPU lock.

```python
from voxprint_common.gpu_lock import gpu_job

with gpu_job("enlarge", "upscale", eta=600):
    run()
```

The file is `%TEMP%/voxprint-gpu.lock`, or `VOXPRINT_GPU_LOCK`. Records use `app` and `stage`. Readers still accept `owner` and `job`. A holder is stale when its pid is gone, or when `eta` (else `started`) is more than two hours old. `VOXPRINT_GPU_PARENT_PID` borrows a live parent lock and does not delete it. `gpu_job` is re-entrant in one process; only the outermost acquire deletes the file.

## thermal

`voxprint_common.thermal` is the shared pause rule: full speed for 2.5 hours, pause at 83 C, resume at 75 C, throttle waits of 60 seconds.

| App | Old module | What moves |
|---|---|---|
| Enlarge | `voxprint_engine/thermal.py` | `decide` |
| Canvas | `canvas/infra/thermal.py` | The same thresholds |
| Audiobook Builder | `core/gpu_thermal.py` | The pause loop. Call `wait_until_ready` |
| Movie Dubber | `dubber/infra/thermal.py` | The pause loop. Call `wait_until_ready` |

Sampling the GPU stays in the app. Pass a `sample` callable that returns degrees Celsius.

## gpu_probe

`voxprint_common.gpu_probe` probes nvidia-smi and assigns a VRAM tier. Messages take the app name. They no longer say Enlarge.

| App | Old module | What moves |
|---|---|---|
| Enlarge | `voxprint_engine/hardware.py` | Probe, driver gate, capability warning |
| Plotweaver | `src/plotweaver/hardware.py` | The probe. Batch planning stays in the app |
| Audiobook Builder | `infra/env_probe.py`, `core/vram_policy.py` | The tier choice. The optimizer stays |
| Movie Dubber | `dubber/infra/vram_tier.py` | The tier choice |
| Canvas | `canvas/core/vram_policy.py`, `canvas/pipeline/vram_probe.py` | The 15.5 / 23.5 GiB thresholds |

Tiers, in GiB (`MiB / 1024`): below 15.5 is `8gb`, from 15.5 up to but not including 23.5 is `16gb`, 23.5 and above is `24gb`. Unknown, missing, NaN, or non-positive memory is `unknown`. A card that reports about 23.99 decimal GB is `24gb` because 24564 MiB is about 24.0 GiB. Dubber's old 22.0 GB cut is not used.

The driver gate is major version 600 or newer. Compute capability below 8.9 is a warning and still starts, unless the caller passes `refuse_below`.

## licence

`voxprint_common.licence` is the allow-list. Schema and role checks stay in `voxprint_engine/registry/validate.py`. Model Forge's excluded-repo list stays in Forge.

| App | Old location | What moves |
|---|---|---|
| Enlarge | `voxprint_engine/registry/validate.py` | `ALLOWED_RELEASE` and the allow-list checks |
| Clarity | `clarity/models.py` | `licence_allowed` |
| Model Forge | `src/voxprint_forge/licence.py` | The permissive-id decision. Excluded repos stay |

Allowed release ids: `mit`, `apache-2.0`, `bsd-2-clause`, `bsd-3-clause`, `cc-by-2.0`, `cc-by-3.0`, `cc-by-4.0`, `cc0-1.0`, `isc`. Aliases such as `apache-2`, `bsd`, `cc-by`, and `cc0` map onto those ids. Unlicensed, non-commercial, and copyleft ids are rejected even when `release` is false. Other ids are allowed only when `release` is false.

## runtime_manifest

`voxprint_common.runtime_manifest` reads and writes `shared/manifest.json` as schema 1: top-level `schema` and `components`. Each component has `kind`, `version`, `path`, `sha256`, `installed_at`, and `refs`. This is not the runtime installer.

| App | Old location | Old shape |
|---|---|---|
| Enlarge | `voxprint_engine/suite_runtime.py` (`read_manifest` and the holder writes) | `schema` + `components`, field `holders` |
| Canvas | `canvas/infra/manifest.py` | `version` + `resources` + `apps` |
| Plotweaver | `src/plotweaver/manifest.py` | `version` + `resources` + `apps` |

`load` migrates both old shapes. `holders` and `apps` become `refs`. A missing `kind` becomes `model` when the id is `model` or starts with `model-` or `model.`; otherwise it becomes `runtime`. Unknown keys are kept, so a migrated file can still contain `resources` or `version`. After migration, `components` and `refs` are authoritative.

`add_ref` reuses a component when id, version, and sha256 match. If version or sha256 differ and another app still references it, `add_ref` raises `ManifestConflict` and does not overwrite. If only the caller references it, the record is updated in place. `remove_ref` deletes the component when `refs` becomes empty.

The lock is `manifest.lock` beside the file, held for at most 30 seconds. A missing manifest is an empty document and is not created until a ref is added. `schema` other than 1 raises `ManifestError` and is not rewritten.

`cpu_onnxruntime_conflicts()` is true when the distribution `onnxruntime` is installed. The shared runtime must not contain that CPU wheel. Installing the runtime is a later version.

## download

`voxprint_common.download` is the multi-source downloader. The behaviour comes from Audiobook Builder: mirror order is the list the caller passes, SHA-256 is required, a partial file is resumed, and the disk check fails before any byte is written when the size is known.

| App | Old module | What moves |
|---|---|---|
| Audiobook Builder | `infra/model_downloader.py`, `infra/parallel_download.py`, `infra/model_mirrors.py`, `infra/modelscope_mirror.py` | Failover, resume, hash check, disk precheck |
| Enlarge | download inside `voxprint_engine/store.py` | Single-URL HTTPS download |
| Canvas | `canvas/models/downloader.py` | Single-URL download |
| Plotweaver | `src/plotweaver/fetch.py` | Single-URL download |
| Model Forge | `src/voxprint_forge/fetch.py` | Single-URL download |
| Movie Dubber | download inside `dubber/infra/model_store.py` | Single-URL download |

```python
from voxprint_common.download import download, huggingface_url, modelscope_url

download(
    dest,
    [huggingface_url(repo, name), modelscope_url(repo, name)],
    sha256=digest,
    size=nbytes,
)
```

The free-space check uses `ceil(size * 1.2)`. `DiskSpaceError` names both the bytes required and the bytes free. Catalogues, ModelScope SDK calls, and NIC binding (`infra/netroute.py`, `infra/download_watch.py`) stay in the app. Only `http` and `https` URLs are fetched.

## cli_protocol

`voxprint_common.cli_protocol` is the `--json` line protocol. Each line is one JSON object. Progress lines come first. Exactly one result line comes last.

| Code | Name | Meaning |
|---|---|---|
| 0 | `EXIT_OK` | Success. `ok` is true |
| 1 | `EXIT_USAGE` | The arguments are missing or unknown |
| 2 | `EXIT_FAILED` | The operation failed |
| 3 | `EXIT_CANCELLED` | Cancelled, or a wait such as the GPU lock expired |

| App | Old location | What moves |
|---|---|---|
| Enlarge | `enlarge/ipc/protocol.py` | The line envelope only. Enlarge job messages and the old exit codes 1–8 and 130 stay in Enlarge |
| Canvas | `canvas/infra/enlarge.py` | The re-declared exit table for the shared envelope |
| Lucent | `voxprint_lucent/enlarge_protocol.py` | Nothing. That file is Enlarge's job protocol |
| Clarity, Plotweaver, Model Forge, Audiobook Builder, Movie Dubber | each app's `cli.py` `--json` writer | The progress and result lines |

## selftest

`voxprint_common.selftest` writes `checks.json`: `{"ok": true, "checks": [{"name", "ok", "detail"}]}`. `ok` is true only when the list is non-empty and every check passed.

| App | Old location | What moves |
|---|---|---|
| Canvas | `canvas/app/selftest.py` | The result object. Canvas-specific checks stay |
| Plotweaver | `checks.json` written from `src/plotweaver/cli.py` | The file shape |
| Other apps | ad-hoc prints in `main.py` or `cli.py` | Optional. App checks stay in the app |

Lucent's C++ self-test stays in Lucent.

## ui.theme

`voxprint_common.ui.theme` loads schema 1 `tokens.json` through `voxprint_common.ui.tokens`. The colour map is the `colors` object. `theme.TOKENS` is that map, so existing `TOKENS["RED"]` reads stay valid. `build_style(glass, more=False)` returns the wizard stylesheet. `build_shell_style` is the editor shell and reads the `density` block. Pass nothing for an identity colour: splash colours live under `identity`, not in the theme colour map. A reader that expected the old flat file should read `colors`.

| App | Old module |
|---|---|
| Enlarge | `enlarge/ui/theme.py` |
| Clarity | `clarity/ui/theme.py` |
| Canvas | `canvas/ui/theme.py` |
| Plotweaver | `src/plotweaver/ui/theme.py` |
| Model Forge | `src/voxprint_forge/gui/theme.py` |
| Movie Dubber | `dubber/ui/theme.py` |
| Audiobook Builder | theme tokens in `ui/` |
| Lucent | `voxprint_lucent/theme_tokens.py` should read this `tokens.json`. `cpp/src/theme.cpp` stays |

## ui.splash

`voxprint_common.ui.splash.Splash` is the 960×540 splash. PySide6 is imported only when a splash is constructed. Install the `ui` extra for that.

```python
from voxprint_common.ui.splash import Splash

splash = Splash(app_art, identity_colour, build_info)
splash.set_stage("Loading models")
splash.set_progress(0.4)
splash.finish()
```

| App | Old module | Identity colour the app passes |
|---|---|---|
| Enlarge | `enlarge/ui/splash.py` | `#ff6b8a` |
| Movie Dubber | `dubber/ui/splash.py` | `#b98cff` |
| Audiobook Builder | `ui/splash.py` | `#f5d76e` |
| Canvas | `canvas/ui/splash.py` | the app's own colour |
| Plotweaver | `src/plotweaver/ui/splash.py` | the app's own colour |

The old 480-pixel square layout is retired. Lucent's C++ splash stays in Lucent. The app passes its own art. A missing file paints `#0b1020`. `finish()` keeps the window at least 0.6 seconds, then fades for 200 ms.

## provenance

`voxprint_common.provenance` is a documented stub. `embed_image`, `finalize`, and `detect` raise `ProvenanceUnavailable`. There was no per-app module to replace. Do not add watermark keys to `suite.json` in this version. `Report.NO_MARK_NOTE` is `no mark found does not prove the file is human-made`.

## Expert mode

Apps open in simple mode. `state/suite.json` key `ui.expert_mode` is false until the user turns it on. `voxprint_common.ui.expert.ExpertMode` reads and writes that bool and notifies listeners. Commands, panels, and settings take `expert: bool = False`. `Registry.visible`, `visible_layout`, and `Settings.visible` omit expert items while the mode is off. A hidden expert panel stays in the app's layout JSON.

`view.toggle_expert_mode` has no default chord. Put it in the View menu and the command palette. A keymap `when` of `expert` runs only while the mode is on.

Image apps (Canvas first, then Enlarge, Clarity, and Lucent) use `IMAGE_CONTENT_FILTER` for the adult-content bool. The descriptor is the whole feature: no classifier and no extra flow live in this library.

Canvas adopts the shell, including this rule, before the other apps.

## Not in 0.1

These stay in the apps. A later version may take them.

| Concern | Where it still lives |
|---|---|
| Discovery, socket names, registration | Canvas `canvas/infra/discovery.py`; Lucent `voxprint_lucent/discovery.py`; Enlarge `enlarge/ipc/register.py`, `naming.py`, `discovery.py` |
| Shared runtime installer | Enlarge `voxprint_engine/suite_runtime.py`; Canvas `canvas/infra/runtime.py`; Plotweaver `src/plotweaver/suite.py`, `install.py`; Audiobook Builder `infra/runtime_reuse.py`; Movie Dubber `dubber/infra/runtime.py`; Clarity `clarity/ffmpeg_setup.py`; Plotweaver `src/plotweaver/llama_release.py` |
| Model refcount (`.users.json`) | Enlarge `voxprint_engine/store.py`; Movie Dubber `dubber/infra/model_store.py`; Audiobook Builder `infra/models_users.py` |
| Shared log rotation | Enlarge `enlarge/main.py`; Movie Dubber `dubber/main.py`; Audiobook Builder `infra/diagnostics.py` |
| Audit and VM-test tools | `tools/audit.py` in Enlarge, Canvas, Audiobook Builder, and Movie Dubber |
