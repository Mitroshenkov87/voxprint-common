# Changelog

## 0.8.0 - 2026-11-19

Freeze of current main (0.7.11). No feature work in this release. Apps that still pin 0.7.4 (Model Forge, Canvas) should read the API notes below before switching.

### Public API changes since 0.7.4

Most additions are backward compatible: ``enqueue_download_all`` and ``--all`` / ``--all-tiers``, the GUI socket (``gui.sock`` / named pipe) and ``open-tab``, ``voxprint_common.mdl.icon.install_user_icons`` plus the packaged MDL mark, ``mdl.eta.EtaSmoother``, ``hashing.sha256_hasher``, optional ``SettingSpec.decimals`` and ``single_step``, and ``SimpleImageWidget(on_wizard_start=..., on_fill_wizard_step=...)`` with ``report_wizard_progress``.

Behaviour that may affect an app:

- ``plan``, ``download``, and ``enqueue --model <id>`` no longer reject a model above the card's VRAM tier; they warn and accept it. Compute-capability still rejects. An untargeted plan still defaults to the card's tier (0.7.6).
- A failed plan or download for one model no longer sets overall ``mdl-status.json`` ``state`` to ``failed`` when installed files stay verified (0.7.6). A finished job is no longer listed as ``running`` (0.7.8). A successful run also sets ``extra["phase"]`` to ``ready``.
- Same-id VRAM tiers (for example Qwen3-TTS 0.6B and 1.7B) show as Not installed, not Needs repair, when only the other tier is on disk. Views are per file-set digest and are not merged. ``inspect_view`` reports ``absent`` when the expected ``<id>-<digest12>`` folder is missing (0.7.9).
- ``ReferenceShell`` FLOAT spins now use 1 decimal and a 0.5 step (they used 2 decimals and 0.1). ``SimpleImageWidget`` already used 1 and 0.5; set ``SettingSpec.decimals`` / ``single_step`` to change either (0.7.11).
- The bundled model index is the signed serial 14 snapshot (0.7.5), not the unsigned serial 11 from 0.7.4.
- ``grab_view`` writes WebP unless the path ends in ``.png`` (0.7.5).
- ``voxprint-mdl-gui`` with no ``--app`` opens Manage models for the first installed app (0.7.7). ``mdl-instance.json`` is mode 0600; if that cannot be ensured on POSIX, the IPC token is omitted (0.7.6).

### 0.7.9–0.7.11

- 0.7.9: same-id MDL tiers keep separate views and the correct Not installed status; a mid-download fallback builds one combined view; Windows CI jobs no longer block a pull request.
- 0.7.10: verify and repair blob hashing goes through ``sha256_hasher``, so tests need not patch ``hashlib.sha256``. File-set stamps still use ``hashlib.sha256`` directly.
- 0.7.11: FLOAT settings can declare ``decimals`` and ``single_step`` (so ``1e-4`` no longer shows as 0.0). Public wizard Start, step-content, and progress hooks on ``SimpleImageWidget`` close issue #17.

## 0.7.11 - 2026-11-19

FLOAT settings can declare ``decimals`` and ``single_step``, so a value such as ``1e-4`` no longer shows as 0.0. The wizard Start button can call an app callback, and a public hook can fill a step's body so Model Forge no longer needs to subclass ``SimpleImageWidget``.

- ``SettingSpec.decimals`` and ``SettingSpec.single_step`` apply only to FLOAT fields. Omitted decimals stay 1; omitted single_step stays 0.5. Labels use the same decimal count.
- ``SimpleImageWidget(on_wizard_start=..., on_fill_wizard_step=...)`` and ``report_wizard_progress`` are the public Start/progress and step-content hooks. With no start callback the placeholder progress state is unchanged.

## 0.7.10 - 2026-11-19

Blob hashing on the verify and repair path goes through ``sha256_hasher``, so tests can observe it without patching ``hashlib.sha256``. File-set stamps again use ``hashlib.sha256`` directly.

- ``voxprint_common.hashing.sha256_hasher`` is the hasher factory for ``sha256_file`` and for ``job._hash_file``.
- Catalog and view file-set stamps no longer bind a private ``_sha256`` alias.

## 0.7.9 - 2026-11-18

Same-id MDL rows (two VRAM tiers that share an id, such as Audiobook's Qwen3-TTS 0.6B and 1.7B) stay Not installed when only the other tier is on disk, and their views are no longer dropped or merged. Windows CI jobs stay in the matrix but no longer block a pull request.

- `_model_kind` treats a file as shared when any other manifest row names the same sha256, even if that row reuses the same model id.
- `inspect_view` reports `absent` when the expected `<id>-<digest12>` folder is missing; another digest of the same id is not a stub.
- `repair_view` / `_drop` leave a valid view of another file set in place. `materialize_views` and `repair-store` group by file-set digest so two tiers are not merged into one folder.
- Windows legs of the CI `test` job use `continue-on-error` so a Windows failure does not block the PR (R18). The jobs themselves stay.
- A mid-download fallback restamps kept default files and their replacements to one file-set digest, so `materialize_views` still builds a single combined view.

## 0.7.8 - 2026-11-17

One-pass MDL wiring check on the laptop (real breakages only) and a faceless README.

- The download and check windows set the MDL window icon, application name and desktop file name (`voxprint-mdl`), so the taskbar shows the MDL mark for them as it already did for Manage models. The standalone tray icon uses the MDL mark instead of a blank square.
- After a download finishes, `mdl-status.json` and the `download` result no longer list the finished job as `running`; the worker refreshes the job rows from the queue.
- README.md is now minimal (work in progress, not ready for use). Developer notes moved to docs/development.md. No tag and no GitHub release for this version.

## 0.7.7 - 2026-11-17

MDL window and desktop icon: a golden-amber neural web with a down arrow on the suite's dark rounded tile.

- The download and manage window, the Windows taskbar (AppUserModelID ``VOXPrint.AISuite.MDL``), and the notification-area icon use a packaged multi-size MDL mark. 16, 24, and 32 px frames use a six-node glyph (thick links and a down arrow, no ring) so the mark stays readable.
- The wheel ships the SVG masters, frames 16–1024, a Windows ``.ico``, and ``voxprint-mdl.desktop`` (with ``Icon=voxprint-mdl`` and ``StartupWMClass=voxprint-mdl``). The window sets the same desktop file name and application name. ``voxprint_common.mdl.icon.install_user_icons()`` copies the frames into the hicolor theme and writes the desktop entry the way Canvas and Model Forge do on Linux.
- ``voxprint-mdl-gui`` with no ``--app`` opens the Manage models window for the first installed app (or the first suite app if none is installed).

## 0.7.6 - 2026-11-17

Seven live-laptop fixes: above-tier models plan with a warning, one model's failure no longer poisons the status file, `--gui` windows actually appear, and the ETA stops lying.

- `plan --model <id>`, `download --model <id>`, and a named enqueue accept a model above the card's VRAM tier the way the manage page and the row selector do: the plan warns about VRAM instead of rejecting the model, and a genuinely unknown id still raises "not available for this card". `plan`, `download`, and `enqueue` gain `--all-tiers`, which lists models above the card's tier (untargeted plans still default to the card's tier). Compute-capability requirements still reject.
- A failed plan/download for one model no longer flips the whole `mdl-status.json` to `failed` when installed files stay verified. The error is scoped to the queue job (`status.jobs` / `QueueJob.error`); overall `state` stays `ready`, `status` stops reprinting the job error as the only message, and the failing command still exits 2.
- `download --gui` spawns the hidden worker with the Wayland/X session variables (`WAYLAND_DISPLAY`, `DISPLAY`, `XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS`, `XDG_SESSION_TYPE`) and also sends them on the `show` message, which a headless worker applies before Qt starts — so the progress window appears instead of the false "not available" result. When the window cannot be shown at all, `--gui` says so.
- `voxprint-mdl worker --gui [--app <id>]` is honoured: the primary worker opens the progress window; when another worker is already running, `show` is sent over the GUI socket, falling back to the instance IPC with the session env, and the command exits 0.
- The ETA hides until a warm-up (~3 s or ~2% of bytes, whichever comes first) and then uses an EMA-smoothed rate, in the overall line, the per-step `eta_s`, and the check window. A new `mdl.eta.EtaSmoother` (clock injected) backs all three.
- A successful run now sets `extra["phase"]` to `ready` together with `state: ready`, on download, already-verified plans, and settled jobs.
- Status `display_name` prefers the manifest/index `label` (falling back to `display_name`, then the id), so a step reads "Pose & edge guidance" instead of the raw id.
- `mdl-instance.json` is created mode 0600 (temp file `O_CREAT|O_EXCL`) so the instance-IPC token is never world-readable; if that mode cannot be ensured on POSIX, the token is omitted and a warning is logged. Windows still writes under the per-user profile.

## 0.7.5 - 2026-11-15

Retry the exact failed job, a common "Download all models" option, a CLI-to-GUI socket, and the signed serial 14 index.

- The window Retry button re-queues the failed job's exact models and manifests. An empty job is never queued; leftover empty failed jobs from 0.7.4 are skipped.
- Installers get `enqueue_download_all(app, manifest)` and `voxprint-mdl enqueue|download|ensure --all`, which queue every model in the app descriptor, including optional ones. Dropped ids such as MOSS are omitted. `--all` cannot be combined with `--model`.
- A running MDL window listens on `<home>/state/<app>/gui.sock` (named pipe `voxprint-mdl-<app>-gui` on Windows). Requests and replies are JSON lines (`ping`, `show`, `enqueue`, `open-tab`, `manage`, `wakeup`). `voxprint-mdl open-tab --app … --tab this_app|shared` uses the socket when a window is already open. See [docs/mdl.md](docs/mdl.md).
- Windows named-pipe connect, read, and write use short overlapped timeouts so a stuck client cannot hang the GUI or CI.
- The bundled model index is the signed serial 14 snapshot (30 models, sha256 `0d1e5ef747ca70b6960b444751fcd1ed5001aaa7bf4b7afd720f4b3df36d58be`, Hugging Face commit `915999691f3ede30669e05f75d81b4d9863c9c98`, generated `2026-11-15T12:22:48Z`), with its `.sig`. Head stays `15edeab0…`.
- The manage window is restyled to match the download window. Offscreen renders at 100% and 150% DPI are saved as WebP.


## 0.7.4 - 2026-11-15

Manage-window status and layout, live progress polish, and safer downloads.

- The manage page compact title sits above a list that fills the window; the scroll area ends at the last row (no empty stretch under the title). Every model from this app's manifest stays on the This app tab, including shared ones such as Z-Image Turbo. A never-installed optional model (or a row whose only on-disk files are shared with another model) reads Not installed, not Needs repair. When every blob is already in the shared store, MDL links the view and shows Installed, with no download. A 16 GB Turbo row resolves to the on-disk Turbo of the same share key. Missing derived output is "Installed, needs preparing", not Needs repair. A cancelled job no longer leaves a Retry banner.
- `register_app` / `unregister_app` write `<home>/state/installed-apps.json` (id, path, display name, version, descriptor). The first write also records other already-installed apps, and `discover()` still scans default install folders, so one `ensure` cannot hide an unregistered install. `voxprint-mdl register` / `unregister` and `ensure` call them. `paths.derived_dir(app, model_id, source_sha)` is `<store>/derived/<app>/<model-id>/<source-sha12>/`. See [docs/mdl.md](docs/mdl.md).
- `download --gui --model` forwards optional and on-demand ids, so the job is no longer empty. A running worker is woken and picks up a new job. `voxprint-mdl clean-partials` lists leftover `.partial` / `.partial.parts` files and deletes only orphans, and only with `--apply`. Partials named by a queued, running, failed, or cancelled job are kept even when `mdl-status.json` has no steps yet.
- The download window shows a labelled smoothed `Speed: 12.3 MB/s` next to time left. A Preparing… stage (planning, checking hashes n/N, resuming parts) with a busy indicator runs before Downloading. Closing the download window while a job is running hides it to the tray (Open / Quit); Quit asks first if a transfer is active. X closes MDL when nothing is downloading.
- Read timeouts, connection resets, and incomplete reads auto-retry from the partial, up to five times, with exponential backoff (2–60 s plus jitter). Disk errors such as no space or permission denied are not retried. A planning failure is written as a failed status, not left on Preparing…. Segmented downloads record the part count in a `layout` file so a resume does not treat unstarted trailing parts as a shorter split. The window shows `Retrying (n/5)…` before the job needs attention. A download card is shown only for an app with a real queued or active job, not for an app that merely shares a file (the stray "Model Forge — waiting" card).
- MOSS is removed from the selectable model list, `MODEL_IDS`, and the bundled index. "Download all models" (`models_for_download_all`) never includes it. The unsigned bundled index is serial 11 (27 models, sha256 `1137150b1646611c9b04e1fd196e9ccb53ad06025af12a3d22468be143139e5b`, generated `2026-11-15T12:00:00Z`). Head stays `15edeab0…`. Model Forge must re-sign; this repo does not sign the index.

## 0.7.4.dev1 - 2026-11-15

Live download progress and resume. The MDL download window shows bytes done/total, percent, speed and time left, per file and overall. The worker writes `mdl-status.json` atomically as Range parts grow (at least every 1–2 s or 8 MB), and the window reloads that file on a timer. Segmented downloads resume from existing `.partial` / `.partial.parts` using HTTP Range from each part's current size, even when the planned segment count would have changed, and verify sha256 when the file is complete. A running `voxprint-mdl worker` stays up and starts a newly queued job without being restarted.


## 0.7.3 - 2026-11-15

The shared model store gains a repair pass, `voxprint-mdl repair-store`, that folds complete copies already on disk into the blob store and rebuilds stub views — nothing is downloaded, nothing is deleted, app folders such as `model-forge/` are never written. It takes the common manifest flags plus repeatable `--from DIR`; exit 0 means ready, 2 means a file or view is still missing so an installer can fall back to `ensure`, 1 is a usage error. The new `voxprint_common.mdl.repair_store` module hosts `repair_store()` (returning a `RepairReport` with per-file and per-view rows) and the manifest discovery / machine planning helpers the CLI shares; `find_copy` / `resolve_file` accept an optional walk cache so a repair pass walks each app folder at most once, and directory walks skip `blobs`, `.git`, `__pycache__`, and `*.staging-*` folders.

`ensure` runs that adoption before enqueueing, so bytes that are already on disk are adopted instead of fetched and count as `reused`, and it relinks views after the job (blobs are trusted by their `.verified` markers, no re-hash). Plain `verify` now also inspects app views: a model whose view is a stub or absent while its blobs are present reports state `stub` (blocking, like `corrupt`, exit 2), and its JSON result carries a `views` list; `verify --repair` runs the local adoption and view repair before the existing download repair. `ensure`/`verify` results and the dry run report the same `views` rows. Refcount rules are unchanged — shared roots are never refcounted or deleted, blobs never deleted by repair — and safety models stay fail-closed through `readiness.shared_safety_gaps()`. See [docs/mdl.md](docs/mdl.md) "Shared store" for the layout, resolution and link order, migration, undo, and how apps adopt the store.

Three review fixes harden the same surface. Store adoption never publishes a blob as a symlink — the order is hard link, then reflink, then a byte copy — so a blob is always a durable regular file even when the adopted `--from` or app-folder source is later removed or unplugged, and `blob_ready` refuses any leftover symlinked blob outright, marker or not. The reuse checks now match: `ensure`/queue and job-worker planning treat a symlinked blob as not installed (never writing a `.verified` marker onto it), and the worker unlinks a symlink destination before fetching or adopting so a download or adoption re-publishes the blob as a regular file. `plan`, `download --dry-run`, and `require_space` now treat a leftover symlinked blob as missing too, so it is neither reported as present nor counted when disk space is reserved. A view is rebuilt only from blobs `blob_ready` accepts (complete, verified, regular files): a leftover, truncated, unverified, or symlinked blob makes `inspect_view` report `stub` and `repair_view` drop the folder instead of materializing a complete-looking tree of bad weights. View entries may still symlink to a blob.

## 0.7.2 - 2026-11-10

The MDL Manage models window splits into two tabs, "This app" and "Shared". A model lands in Shared when it lives in the shared suite model store and is refcounted by more than one app in `<home>/shared/manifest.json`, or is used by another installed app discovered through the existing `catalog.discover()` / `installed-apps.json` / suite-home scan, or shares the same upstream as another installed app's entry. The share key is repo+revision of the resolved descriptor (or the Hugging Face / ModelScope mirror `repo`+`revision` on a schema-1 entry), never the model id: ids are not unique across apps and may be duplicated inside one `mdl.json`. When repo+revision cannot be read, the fallback key is the file sha256 / `model-<sha12>` component id. Duplicate ids inside one manifest still load; the manage page lists each entry as its own row and keys rows by repo+revision plus tier and label so row keys and actions do not collide.

Each Shared row shows "Used by: <app display names>" in stable `SUITE_APPS` order, including the current app. Remove on a non-protected shared row drops only this app's reference (`release_component`); the files are deleted only when the refcount reaches 0, and the row says so. A model that any installed app marks required (not `optional` and not `on_demand`, or normalising to `group=safety`) paints no Remove in the Shared tab and the remove path refuses it; the row reads "Required for safety" when any installed app marks it safety, otherwise "Required by <app display name>". `safety` and `internal` keep their 0.7.1 rules everywhere. A missing, corrupt, or hash-mismatched shared safety copy fails closed: `voxprint_common.mdl.readiness.shared_safety_gaps()` returns the safety models (keyed by share key) whose shared copy is missing, corrupt, or hash-mismatched, so an app can refuse the gated action; the manage row shows Repair and "Required, missing" instead of Download or Remove. `gaps_for` / `disk_ready` are unchanged.

The tier badge now paints "16 GB" / "24 GB" / "8 GB" through the `badge_label()` i18n helper; the internal `ManageRow.badge` values stay `8gb` / `16gb` / `24gb` / `""` so `group_manage_rows` split logic is unchanged, and `any` still has no badge. The task groups and VRAM tier sections from 0.7.1 apply inside each tab independently, safety and internal groups still start collapsed, empty groups stay hidden, and a tab with no rows is not shown. The disk line stays above the tabs and "Check and repair" below. `ManageView` keeps `installed` / `available` / `groups` and gains `tabs` (`ManageTab` with key, title, groups, and count); when nothing is shared the page shows only "This app" and old callers keep working.

The shipped Russian UI catalog is removed (English-only source UI; Suite Lead approved): `ui/locales/ru.json` is gone, `i18n.LANGS` ships `en`, `de`, `uk`, `lv`, and the scripted renders are English-only. The i18n machinery for Russian stays — `normalize_code("ru-RU")`, `plural_category` for ru, and `add_catalogs` for an app that ships its own `ru.json` — as does the input-side Cyrillic detection in the safety checks. `mdl/progress_ui.py` now reads its labels from the catalogs through `tr_lang()` (English source) instead of hardcoded Russian constants; `format_size` / `format_speed` / `format_eta` there take a `lang` argument.

`voxprint-mdl` gains a stable, non-interactive installer surface (see [docs/mdl.md](docs/mdl.md) "Installer integration"): `--app-manifest PATH` is an alias of `--manifest` on every manifest-taking subcommand, and a new `ensure` subcommand reads the app id from the manifest, enqueues that app's planned models through the single shared queue, and runs or attaches to the one worker — headless, never a window, never a prompt. Files already in the shared store are reused, never downloaded twice; `ensure` only adds the app's ref. `--model` covers on-demand models and `--dry-run` plans plus checks the disk without fetching. With `--json` the `ensure` and `verify` result lines add `app`, a per-model `models` list (`id`, `share_key`, `state`, `bytes`), and `safety_gaps` from `readiness.shared_safety_gaps()`; `ready` is false when any model is missing or corrupt or any safety gap exists, and both commands exit 2 in that case (fail closed). An `--app` that does not match the manifest is a usage error (exit 1). In the Manage models window, Remove resolves the row's own `component_id` (new `ManageRow` field), so duplicate model ids such as Movie Dubber's three `qwen3-tts` rows release the component the row names instead of the last model-id match.

## 0.7.1 - 2026-11-10

The MDL Manage models window groups rows by task. App model entries may carry three optional display strings — `label` (falls back to `display_name`, then the id), `purpose` (one line under the title), and `group` — on schema 1 `mdl.json` model objects and on `voxprint-app-models/1` tier entries and `optional[]` entries. `group` is one of `generate`, `edit`, `upscale`, `restore`, `voice`, `transcribe`, `translate`, `writer`, `safety`, `internal`. A missing, empty, or unknown group does not fail the load: it sorts into the Other task group. A non-string `label`, `purpose`, or `group` is refused with `CatalogError` / `DescriptorError`. Old manifests without the fields load and render unchanged, and `Model(...)` constructions keep working through the new defaults. The vendored `voxprint-app-models.schema.json` accepts the new keys; its pinned sha256 moves to `ff18ac6c…`. See [docs/mdl-fields.md](docs/mdl-fields.md) for the field spec.

The manage page shows groups in the order Generate, Edit & structure, Upscale, Restore, Voice & speech, Speech to text, Translate, Writer, Other (just before safety), Required for safety, Built-in helpers; empty groups are hidden. The raw model id sits in the row tooltip. Inside generate, edit, upscale, and restore, a group holding both a compact (8 GB or 16 GB) and a full-size (24 GB) variant splits into "For 16 GB cards (compact)" and "Full size (24 GB+)"; the half matching the detected card starts open and the other folds under "Show models for other cards". Each row carries its tier badge (`8gb`, `16gb`, `24gb`; `any` has none), taken from `vram_tier` — a numeric `min_vram_gib` only feeds the "N GB VRAM" meta line. A card whose tier cannot be detected reads as 16 GB for that choice, while download planning still treats an unknown GPU as 8 GB. `Controller.listing()` keeps hiding models above the card's tier; pass `all_tiers=True` to include them, which is how the window resolves Remove or Repair for a row in the folded section. `manage_page()` lists other-tier models so they stay addable from that section.

`safety` and `internal` start collapsed and paint no Remove button; `can_remove(group)` refuses them and `Controller.remove_model(..., group=...)` returns False, as does the window action even when a button is forced. Without `group`, `remove_model` keeps today's behaviour. `ManageView` keeps its `installed` and `available` tuples and gains a `groups` field; `ManageRow` gains `purpose`, `group`, `badge`, and `removable` with defaults, so existing call sites keep working. New UI strings live in all five catalogs (`mdl.manage.group.*`, `mdl.manage.section.*`, `mdl.manage.other_cards_*`).

## 0.7.0 - 2026-11-09

`save_image` now distinguishes an unset WebP quality (`None`, purpose default: lossless for masters, series, and hand-offs; lossy quality 90 for pictures) from explicit lossless (`QUALITY_LOSSLESS`, the string `"lossless"`). `build_xmp` omits non-finite float strengths. `parse_xmp` returns empty metadata and logs a warning on a malformed packet instead of raising. `normalize_base` still reads an untagged LoRA as `z-image` (moss-final-spec item 4), the Z-Image family / LoRA training base; `z-image-turbo` maps to that family too. That behaviour is pinned in a comment and a test.

Z-Image-Turbo is the suite default generation model. `DEFAULT_MODEL` / `default_model` / `model.default` read as `z-image-turbo`; unknown values fall back to `z-image-turbo`. The setting value `z-image` stays the full Z-Image model (optional download, LoRA training base) and is not remapped to Turbo. MOSS-Image (`moss`) is an optional download. The bundled index drops `moss-image-compressed` (no compact build yet) and retires the old BF16 mirror id `mirror-z-image-turbo` (descriptor history remains on the HF repo at `b74ab2e…`; use `z-image-turbo` instead). `mirror-z-image-turbo-controlnet-union-2.1` stays. The `z-image-turbo` row is `default: true` with descriptor `size` 11047 and revision `a853c26eb946b9010b4bd142a2ad1439d83e7896` (`voxprint-model.json` on `Mitroshenkov87/voxprint-mirror-z-image-turbo`); `z-image` and every `moss-image*` row are `optional: true`. Index entries may carry optional `note`, `size`, `license`, `default`, and `optional` fields. The pin is serial 10, sha256 `dab444f7d820b2611cb2aa1ec00ad8241a1ae41a4c68823aff793bfa0a72ce0e`, generated `2026-11-09T23:46:34Z`. Head stays `15edeab0…` until Model Forge publishes a signed index. Serial 10 is newer than the live signed serial-9 remote, so the bundled Turbo default is used until that signed index arrives. Pillow is already the newest release (`>=13.0`, 13.0.0). filelock is not a dependency (locking stays in `atomic.py`).

Shared image Quality presets use Z-Image Turbo values: fast 4 / balanced 8 / best 16 steps, guidance 0.0, Euler. `shell.guidance` allows 0.0.

The Lucent identity token is `#5ce1ff`. `MIGRATION-PLAN.md` describes how each app hosts the shared simple/Expert/Custom shell; the old UI stays frozen. `scripts/render_shell_variants.py` paints one sample window per app type (image, audio, video, text) at 100% and 150%.

`ui.theme` adds `mark_compact`, which flags a `QPushButton` for the tighter `QPushButton[compact="true"]` rule (same 4 px button radius). `SimpleImageWidget` sizes and spaces its history strip through the `cell_size` / `cell_gap` constructor arguments and properties (defaults `HISTORY_CELL_SIZE` 88 and `HISTORY_CELL_GAP` 8 leave the strip unchanged), and `snap_history_offset` snaps the strip's scroll offset to whole-cell boundaries.

## 0.6.4 - 2026-11-05

The library supports Python 3.14 and newer. `requires-python` is `>=3.14`. The Trove classifiers list `Programming Language :: Python :: 3.14` and `Programming Language :: Python :: 3.15`. Ruff's `target-version` is `py314`, the minimum. CI tests 3.14 and 3.15 on Ubuntu and Windows. The docs job, `release.yml`, `pages.yml`, and `docs/ci/pin-check.yml` install 3.15. The newest stable target is 3.15. The floor stays 3.14 so Clarity and Enlarge can keep `onnxruntime-gpu` until that package ships a 3.15 wheel. This repository does not ship an installer or a bundled Python runtime.

`remove_outputs` skips a hook-marker line whose JSON is not a list. A damaged marker (`TypeError` or `KeyError`) no longer aborts cleanup of the remaining lines.

The module docstrings in `mdl/index.py` and `mdl/data/__init__.py` name the bundled index as commit `15edeab0`, serial 9, matching `model-index.pin.json`.

## 0.6.3 - 2026-11-04

`save_image` still defaults to lossless WebP. `purpose="picture"` with no explicit quality saves lossy WebP at quality 90 (`LOSSY_DEFAULT_QUALITY`, from `default_quality`); `purpose="master"` (the default) stays lossless for masters, series, and hand-offs. PNG, AVIF, and JPEG remain options. The written file is still published atomically and keeps the format's extension.

Metadata is off unless `write_metadata=True` or the `metadata` key of `image.save_format` is true. A mapping passed while that switch is off is not written. The document is `prompt`, `seed`, `steps`, `base` (`base_model` is accepted on input and both are read back), `lora_strength`, and `loras` (a weight name, or `name` / `strength` / `sha256` / `base`). It is XMP on WebP, AVIF, and JPEG, with the same document as JSON in EXIF ImageDescription and UserComment, and a PNG iTXt chunk under `XML:com.adobe.xmp`. `read_metadata` reads the XMP from all four. LoRA entries come back as `{name, strength, sha256, base}`, and an untagged LoRA base is None. Multi-line prompts, including LF and CRLF, round-trip.

`voxprint_common.models` is the shared model and quant selector. `moss` (MOSS-Image, Apache-2.0) is the default; `z-image` is an optional separate download. `select_quant` picks `bf16`, then `fp8`, `int8`, or `nf4`, the best whose placeholder peak fits `MEMORY_BUDGET_GB` (14 GB) for generation with a LoRA loaded or for training. Those peaks are placeholders pending VM calibration. `normalize_base` treats a missing or empty LoRA tag as `z-image`. Suite settings `model.default` (`moss`) and `model.quant` (`auto`) store the choice. Nothing is downloaded and torch is not imported.

## 0.6.2 - 2026-11-04

`voxprint_common.image` saves images with the shared `image.save_format` suite setting: lossless WebP by default, lossy WebP with a quality of 1-100, or PNG, AVIF, or JPEG. `save_image` encodes first and publishes the bytes atomically, and the written file carries the extension of the format. WebP files carry `prompt`, `seed`, and the LoRA list as XMP; `read_metadata` reads them back. Pillow is an optional extra, `voxprint-common[image]`, and the module never imports it at load.

## 0.6.0 - 2026-11-02

`voxprint_common.worker` runs model jobs in a spawned child process. The UI polls a pipe from a timer. A crash restarts the worker with backoff. Training can resume from a checkpoint. Inference fails fast. `voxprint_common.testing.gui_smoke` walks a window's buttons and menu actions under `QT_QPA_PLATFORM=offscreen`.

## 0.5.2 - 2026-10-26

`adopt` reports a source file whose hash matches a blob and whose inode does not. The text report and `--json` include each `duplicate` row (`path`, `bytes`, `blob`) and `reclaimable_bytes`. The adopted, reused, skipped, and would-adopt counts stay as they were. A dry run still does not hash. A source that is already the blob inode is not read again.

`voxprint-mdl cleanup` reclaims those copies. The default is a dry run. `--apply --mode link` replaces a hash-matched legacy file with a hardlink to `blobs\<sha256>`: link to a temporary name in the same directory, then rename over the original. A failed link is skipped and nothing is copied. `--mode delete` removes a file only after a fresh SHA-256 of the file and of the blob match, ignoring the hash cache. Empty directories are removed only when every file under them was verified. Files with no matching blob are `unique` and are left in place. `--gc` removes blobs that no view, app ref, or manifest names, under the same dry-run and `--apply` rules. Before a file is replaced or removed, model entries in `shared\manifest.json` that point at the legacy path are retargeted at the blob. The previous path is kept as `retargeted_from`. Schema stays 1. An interrupted link leaves the original file or the hardlink. A second run changes nothing.

`docs/ci/pin-check.yml` is a reusable workflow apps can call to fail when a pyproject or lock pin for voxprint-common disagrees with the wanted tag's wheel sha256.

## 0.5.1 - 2026-10-26

The manage-models window opens on the suite surface colours, at 1280 px wide, sized to its rows. The header shows the installed `.ico` when that file is present. Otherwise it paints the app glyph in the identity colour from `tokens.json` — Model Forge is the isometric cube in `#f58a3c` — on the dark rounded tile, and an unknown app falls back to the VOXPrint mark. The disk line prints a Windows drive path with backslashes. A model with several files is installed when each file matches its own size. Check and repair opened from Manage stays on the window's event loop and blocks that window until the check closes. A manage scan that fails is tried once more, then shown with Retry. The pink rule, the edge fade, and the empty band under the buttons are gone in manage mode. The app name sits in the header. Opening the window does not hash: a row is installed when the stored size and mtime match the file. The scan runs off the UI thread, and the window is on screen before that scan finishes.

Check and repair (`Проверить и починить`) has three phases. Verify hashes one model at a time. Review then marks every row OK, damaged, or missing and shows that summary, with an N/M counter. Repair starts only after review, one marked model at a time. Cancel waits until the current file has finished. Closing the check window, with Cancel or the title-bar control, re-enables Manage in that same close. A second check does not start while the first job is still writing blobs. A digest is cached by path, size, and mtime, so an unchanged file is not hashed again.

`voxprint-mdl adopt --app <id> --from <dir>` takes model files that are already on disk. It plans the same files as `download`, matches each one by relative path or filename and size, and hashes one file at a time (in the progress window when `--gui` is set). A matching sha256 reuses `blobs\<sha256>` when that blob is already the right bytes, or hardlinks the file there, copying only when a hardlink is impossible. A leftover file at that path is replaced. The source is never deleted or moved. The blob is marked verified and the app ref and post-install hooks are built the same way as a download. A finished download writes that `.verified` marker, so the next run does not hash the file again. A Canvas model whose files are all in the blob store also gets `views\canvas\<model-id>-<12 hex>\`, the folder Canvas rc.28 opens. A mismatch is reported and left for `download`. The command is idempotent, and it accepts `--json` and `--dry-run`. `download` reuses an existing `blobs\<sha256>` of the right size instead of fetching it again, and it builds that same Canvas view.

`voxprint-mdl-gui --app <id>` is the windowed entry point. Apps should call that instead of `voxprint-mdl --gui`. On Windows the queue process is started with `CREATE_NO_WINDOW`, and a GUI launch drops an inherited console.

## 0.5.0 - 2026-10-25

`ModelPark` is the shared RAM hot tier. A model is read from SSD once and kept in RAM while the tier budget allows. `ram32` loads on demand and does not keep the weights. `ram64` parks main models. `ram96` keeps every model. `stage(name, models=[...])` moves the requested models to the GPU and the others back to RAM. The VRAM budget defaults to 16 GiB and leaves 1 GiB free. Least-recently-used GPU residents return to RAM, not to disk, while the RAM budget still holds them. Host copies are pinned when that succeeds and stay pageable when it does not. Transfers keep the source dtype, so FP8 stays FP8. A second `get` of a parked model does not read the disk; the attempt is counted and logged. `stats()` reports resident RAM and VRAM bytes, disk reads per model, and the copy timings.

A reader of `suite.json` treats a new inode as a new file. Writers also remember the object they just published, so a second update in the same timestamp tick is visible in this process.

`run_verify_repair(model_ids, repair=True)` and `run_window(start, mode="verify")` open the check-and-repair window. Phase 1 hashes each model. The rows then show OK, missing, or corrupt. Phase 2 downloads only the missing or corrupt models. A summary follows. `voxprint-mdl verify --gui` opens that window. `voxprint-mdl verify --repair` still clears a dangerous manifest path and does not delete files, and it now also downloads only the models that are missing or corrupt. Strings are in the catalogs, including English and Russian, and the summary uses the plural forms so a zero count reads as a word that agrees with the number. Buttons use `button_radius_px`. Close stays a secondary button until the summary, then it becomes the primary button, at the same size as Cancel.

## 0.4.2 - 2026-10-25

Buttons are rectangles with small corners: `ui/tokens.json` density `button_radius_px` (4) sets the radius of every push button, tool button, chip, segment, and the Expert toggle in `build_style` and `build_shell_style`; no button is pill-shaped.

## 0.4.1 - 2026-10-19

`model-forge` (`Model Forge`) is a suite app, so `voxprint-mdl --app model-forge` and `voxprint-mdl --gui --app model-forge` find its `mdl.json` in the install folder. A `voxprint-app-models/1` plan builds models only from the descriptors it resolved for the active tier or the requested ids, instead of failing on ids listed for other tiers or unrequested optional ids. `--model` ids are planned only against the manifests that list them; an id that no manifest lists still fails. `download --dry-run --app <id>` plans only that app's installed manifest. Bare `--gui --app <id> --manifest <path>` opens the manage page for that manifest.

`docs/installer.md` icon style allows a small flat scene inside the identity-colour frame. Identity colours in `ui/tokens.json`: Canvas `#4da3ff`, Model Forge `#f58a3c`.

Windows installer and branding standard in `docs/installer.md`: one `VOXPrint AI Suite` Start menu folder with only `VOXPrint AI <App>` launchers and no Uninstall shortcut, a 16 to 256 px `.ico` on the launcher, the shortcut, the uninstall entry, and the window, and a matching AppUserModelID. `voxprint_common.branding` returns each app's installer names, packs and checks `.ico` files (`python -m voxprint_common.branding check|pack`), and sets the process AppUserModelID.

## 0.4.0 - 2026-10-18

RAM tiers and a process-local model park. `memory.ram_mode` is `auto`, `ram32`, `ram64`, or `ram96` (default `auto`). `VOXPRINT_RAM_MODE` overrides it. Installed RAM is read with `GlobalMemoryStatusEx` on Windows and `/proc/meminfo` on Linux. 88 GiB and above is `ram96`, 44 GiB and above is `ram64`, and anything smaller is `ram32`. Below 24 GiB logs a warning and stays `ram32`.

`ModelPark` keeps weights on disk, in pageable RAM, or in pinned RAM. `ram32` does not pin. Elsewhere the pin budget is the smaller of half of RAM on Windows (three fifths elsewhere) and RAM minus 12 GiB, and `ram64` is also capped at a quarter of RAM (48 GiB pins up to 12 GiB, 64 GiB up to 16 GiB). One pin is at most 2 GiB, or 4 GiB on `ram96`. A larger tensor stays pageable. `StagingRing` is two 256 MiB pinned buffers, created on first use, including `ram32`. `pin_memory` raising `RuntimeError` leaves the tensor pageable and lowers this process's budget to the bytes already pinned. Unreferenced weights are evicted least-recently-used: pinned becomes pageable, then pageable is dropped. Below 6 GiB available, the park frees weights until available RAM is above 8 GiB. `prefetch` warms keys on `ram64` and `ram96`.

Pinned bytes are shared through `<home>/state/ram-park.json` under the suite file lock. A row names the pid, the app, and the pinned bytes. Rows whose pid is no longer running are removed. Torch is an optional extra, `voxprint-common[torch]`, and is imported only when a staging buffer is created.

Note for installers: from 0.4.0, `paths.read_home()` no longer reads the legacy home folder (the read-through ended with the 0.3 line, `_LEGACY_READ_THROUGH = (0, 3)`). Installers must migrate first: `paths.home()` (or the home migration it calls) moves the legacy folder, while `read_home()` alone no longer sees it, so settings and models left in the old folder are not found.

## 0.3.2 - 2026-10-18

`voxprint-mdl download` resolves each model id in a `voxprint-app-models/1` manifest to a `voxprint-model/1` descriptor from the signed index. The index row supplies the repository, revision, and sha256 of `voxprint-model.json`. The file is fetched with the same guarded opener as the index and cached under `<home>/state/mdl-descriptors`. The cached bytes must match that digest.

A tier entry may be a model id, which keeps every file, or an object with `id` and `files` (globs) and/or `file_groups` (names on the descriptor). Readiness, verify, and refcounts use that subset. A model listed for the active tier is fetched even when its descriptor sets `on_demand`. A descriptor that does not resolve, or a selection that matches no files, exits nonzero instead of reporting `nothing to download`.

The vendored `voxprint-app-models.schema.json` sha256 is `ed451b995e39ebba061f4604fcfc05d13a4000909769e931e700bb3f35c1c420`. A `file_groups` entry may set `name`. The vendored `voxprint-model.schema.json` sha256 is `e46ae56a06516ff127d6d7c45669873ad268e60a8b2c3a96c1b3e1f3919f5138`.

## 0.3.1 - 2026-10-18

Downloads work behind DNS that answers with placeholder addresses and behind proxies. The guard still requires an allowlisted host and HTTPS with certificate and host name checks. On top of that it accepts a resolution in `198.18.0.0/15`, and it skips the local DNS answer when the request goes through a proxy from the environment or the Windows settings (the proxy itself may be on loopback). Loopback, link-local, RFC 1918, and other private answers stay refused for direct connections, plain HTTP to `198.18.0.0/15` is refused, and redirects must stay on the allowlist. `100.64.0.0/10` was already allowed and is unchanged.

Each mirror now has a `direct` and a `system` route. The probe measures both when a proxy applies and keeps the faster working one per host; the transfer switches to the other route after a connection error or stall before the source counts as failed. `VOXPRINT_MDL_ROUTE=direct|system|auto` (default `auto`) forces a route. The signed index fetch uses the same proxy rule. No source-address binding on Windows. BUILD.json is 0.3.1.

## 0.3.0 - 2026-10-18

Model integration for ONNX apps. `runtime.engine` may be `onnxruntime` (no TensorRT engine). A model or a file may declare an optional `onnx_prebuilt` artifact (`path`, `sha256`, `size`, `url` or `sources`, `mirror_policy`, and `built_for: {onnxruntime, opset}`); it is separate from the 0.2.6 `prebuilt` list of fallback sets, which is unchanged. The app reports the installed onnxruntime through `voxprint_common.mdl.onnx_prebuilt.set_onnxruntime_probe`. A stale artifact (version or opset mismatch, no probe, or no size) is not downloaded; it is logged, shown by `voxprint-mdl --check` as `stale onnx_prebuilt`, and replaced by the source files plus `post_install`. A fresh one is downloaded and the model's own `post_install` is skipped.

`post_install` in `voxprint-model/1` and `mdl.json` takes `cli`, `args`, `timeout_s`, and `outputs`. `cli` resolves only to the owning app's own executable in its install folder (never `PATH`, never a path from the document, and not through a symlink out of the folder). `args` accept only `{id}`, `{model_dir}`, `{source_path}`, `{prebuilt_path}` (the downloaded `onnx_prebuilt` file), and `{cache_dir}`. The command runs with `shell=False`, `VOXPRINT_OFFLINE=1`, and every proxy variable pointed at `127.0.0.1:9`. `timeout_s` defaults to 600 and is capped at 3600, and a timeout kills the process tree. The last 64 KB of stdout and stderr go to `<home>/state/mdl-hook.log`. Outputs are relative to `{model_dir}`, may carry a sha256, and count for ready, verify, and removal. A failed, timed-out, or mismatched command leaves the model not ready, with the reason in `--check`, and runs again on the next install. The older app-manifest form (`argv`, `timeout_s`, `produces`) still works. BUILD.json is 0.3.0. The vendored `voxprint-model.schema.json` sha256 is `08dc67f30991c9558abedd9adbcd9ae77d8e6cd429520f2a9a548bfc2beeec08`.

Fixed: on Windows, `exclusive_lock` no longer fails with `PermissionError` when two processes create the same lock file at once.

## 0.2.6 - 2026-10-18

Hotfix. The bundled index is the live signed `Mitroshenkov87/voxprint-model-index` document at serial 9, head `15edeab0f7a874c27d2b38ef6ecb69106ff94df8`, generated `2026-10-18T04:09:36Z` (29 models, sha256 `104742f9d8cc7c8f693f963dbd871471a3e57fda3d585d52b105b3855d49f45f`), and `model-index.pin.json` records that serial, head, `generated_at`, and sha256. The bundled public key is Model Forge's `keys/index-public.pem` (voxprint-model-forge `c7883f3e`). The index loader accepts the object form `{head, serial, generated_at, models}` and the older list form. Model documents may mark a mirror `ours: true` and may list `prebuilt` alternatives (`id`, `role`, `replaces`, `files`, `note`). An entry with `role` `fallback` replaces the default files under `replaces` when a default file fails (404, hash mismatch, or still unavailable after retries) or when an app or the user picks it with `voxprint_common.mdl.variants.choose_variant`; the choice is saved in `state/mdl-variants.json`, and the fallback files keep the model's `mirror_policy` and their own `sources`. `trt_cache_dir()` is `<home>/trt-cache` and no longer recreates `%LOCALAPPDATA%\Voxprint`; on Windows the old cache moves with the home, elsewhere engines are rebuilt.

The signed model index. A remote index is used when its detached Ed25519 signature verifies and its `serial`, or `generated_at` when serial is absent, is at least the bundled pin. Otherwise the log and `voxprint-mdl --check` name the fallback. `voxprint-mdl index sign` and `index verify` write and check `index.json.sig`. Signing and verification use the `cryptography` package, now a dependency. Model documents may set `mirror_policy` to `race` or `strict-priority`, and a file or `file_groups` entry may set `sources`. `strict-priority` keeps the first source until it returns 404, stays unavailable after its own retries, or the hash does not match. A 404 skips that source immediately in both policies.

## 0.2.5 (Yitro) - 2026-10-17

The shared simple and Expert shell for every VOXPrint app (UI codename Yitro).

The expert panel uses the dark tokens, a title, and the live values of the current preset, with Reset while that preset has changes. History uses the same dark surface. Expert mode does not resize the window. The image screen keeps one size row and a large result. The viewer is a picture with a thin toolbar. Clarity wipes one frame along a vertical divider. Audiobook Builder asks you to choose a book. Selected chips and the Expert toggle keep `chip_pad_px` of padding on each side when the label is bold. Expert fields share one label column, and the values line up in the column beside it. A combo keeps its longest value inside the panel, or ends it with an ellipsis and a tooltip. A model uses one name in the simple picker and in Expert mode. The shell sets the application palette from the tokens for the dark theme and the light theme, so a scroll area's viewport is not left on Qt's grey. Scrollbars in every scroll area use the same thin dark track, muted handle, and no arrow buttons. The expert panel scrollbar uses the same dark tokens: a thin track, a muted handle, and no arrow buttons. A group name is shown only when that panel has more than one group. A screen can declare sections. Tabs that do not fit go to a More menu, and a section can add an action that opens a wizard. A table section stores a per-row picker in that section's settings. Expert fields follow the open section, or only the open wizard step. A long-running step shows progress and Cancel, and a restart restores the step from the shared settings and a checkpoint the app supplies. Text, list, tree, and table viewports use the same token colours as the scroll areas, in the dark theme and the light theme. The minors gate reviews the finished picture, and a missing picture is blocked. An image screen requires a gate. An image screen can store a source picture and a mask. Generate calls the app with the live settings, including the expert values, and then becomes busy; a second Generate while busy is ignored. A media-and-voice screen can omit the built-in voice picker. A setting can carry a localisable unit, shown on the expert field and on a simple value label. Movie Dubber previews the chosen video and, after the dub, plays the result with the new audio. Audiobook Builder can replace the voice list, keep a dropped book's full path, pause or resume a job, and play the finished audio with chapters. `suite.json` stores `ui.theme` (`dark`, `light`, or `system`) and `ui.geometry`, including maximized and fullscreen, and an older `ui.window` rectangle is migrated. Preset pills wrap onto another row instead of clipping.

Language catalogs are JSON files in `ui/locales`, beside `tokens.json`. A missing translation falls back to English. `density.focus_ring_px` is 2. `suite.json` is written under the data home with a lock, and a torn file does not replace the last good copy. The viewer screen has no input and no primary action. History can be hidden. The first window is 1200 by 900, with a minimum of 960 by 640, or 640 by 400 for the viewer. Later launches restore the saved rectangle. The expert panel is 280 px and resizes from 220 to 420. Time left appears only when a job supplies a duration.

The suite data directory is `%LOCALAPPDATA%\VOXPrint AI Suite` on Windows and `~/.local/share/voxprint-ai-suite` on Linux, or `$XDG_DATA_HOME/voxprint-ai-suite` when that variable is an absolute path. The first call to `home()` moves an existing `%LOCALAPPDATA%\Voxprint` or `~/.local/share/voxprint` directory. On the same volume the move is a rename. Otherwise the files are copied, the file count and sizes are checked, manifests and state are hashed, and the old directory is renamed to `.migrated-<date>` and kept. When both directories already exist, the new one is used and the old one is left in place. `VOXPRINT_HOME` still wins and is not moved. `read_home()` reads the new directory, or the old one through version 0.3, and does not move it. Absolute paths in `shared/manifest.json` that pointed at the old directory are rewritten. The TensorRT cache is `<home>/trt-cache` (from 0.2.6).

Expert mode is one suite setting, `ui.expert_mode`, off by default. Commands, dock panels, and settings tagged expert stay hidden until it is on, and a hidden panel stays in the layout JSON. `view.toggle_expert_mode` is in the View menu and the command palette, with no default shortcut. Adult content (18+) selects a prompt policy: on allows adult content, and off adds a general-audience template and negatives. The minors gate is outside settings and presets. `SafetyGate` reviews the prompt and each result, and a refusal or a failure blocks the picture. Keys under `safety`, and keys for minor, age, or guard, are rejected.

Simple mode and Expert mode are two views of one settings state per app. A preset file, schema `voxprint-presets/1`, writes concrete values in file order. A later group owns a key both groups write. A hand edit marks that group. The chip reads "Balanced + changes" while the stored preset is still close, or "Custom" when it is not. Reset restores that group. The choice and the overrides persist in `image-settings.json`. The reference file is `src/voxprint_common/ui/presets/canvas.json`.

`SimpleLayout` is the shared screen for every app: an input, one primary action, a result, and a history strip. Image, audio, video, text, and tool are configurations of that screen, each with a `voxprint-presets/1` file. Canvas, Enlarge, and the Lucent helper are the image configuration. Enlarge takes a dropped picture. Audiobook Builder takes a book and a voice. Movie Dubber takes a video, leaves out that voice picker, and shows the video result. Clarity takes a video and shows a before/after player. Plotweaver takes an outline and shows a chapter document. Model Forge takes a model and shows a status report. A long job shows a stage, a percent, an estimate, and Cancel. The overflow menu is Open folder, Manage models, Settings, and Expert mode.

Image apps open on that screen. Canvas supplies Quality, Style, grouped size ratios, Standard or 4K output, Detail, and how many pictures. 4K can sit on the row greyed out, with a plain reason and Install Enlarge, until Enlarge is present. Improve prompt rewrites the same field. History keeps the last 50 pictures in the app's State directory, including the prompt and the seed. Expert mode adds a side panel for steps, guidance, seed, sampler, a negative prompt, add-ons, a memory limit, and adult content, and leaves the rest of the screen where it is. The dock stays an API. A muted Expert chip shows while the mode is on. Manage models calls the app. Canvas runs `voxprint-mdl --gui --app canvas`.

The download window keeps a one-row card as tight as the others, fades the bottom of a list that still has more to scroll, and names the file that is downloading instead of a check happening beside it.

The shared editor UI is in `voxprint_common.ui`: a headless dock with undo and schema 1 layout JSON, a command registry and keymap, schema 1 design tokens, and a reference shell. Qt stays in the `ui` extra. Canvas adopts this first. The reference shell draws palette shortcuts as key chips, a one-pixel splitter that turns blue while dragged, and an accent mark on the active tab. A splitter drag is one undo step, recorded when the pointer is released. A click that does not move the divider is not an undo step. Fixed panes keep their pixel size and their minimum, every overflowed command is listed in More, and closing a panel follows the tab that remains.

## 0.2.4

Safety. A component whose path is, or contains, the suite home, the models root, the shared runtime, voices, cache, projects, or state is refused, and so is a path that overlaps another component. Release deletes a directory only when it is one model's folder directly inside the models root and both the shared manifest and `models/.users.json` have no remaining users. Otherwise the ref is dropped and the files stay. `ready(app)`, `ensure_ready`, and `status` check that every required model file and runtime file is present at the expected size, so a stale status file cannot hide a wiped store. `voxprint-mdl verify --repair` clears a dangerous manifest path and does not delete files.

Codename: Malkhut.

## 0.2.3

The download window is tighter and calmer. The header motif sits inside the window, item cards use the full width, and a short queue no longer leaves an empty pane. Speed and source appear only when they mean something. Sizes follow the language: a decimal comma and a narrow space in German, Russian, Ukrainian, and Latvian. A disk error names the models folder, keeps the path under Details, and does not say the download is still waiting.

Codename: Hod.

## 0.2.2

The download window is the shared progress UI. One window shows the queue grouped by app, and a second launch focuses that window. Manage models lists what is installed and what this card can add. Closing the window leaves the worker running.

`voxprint_common.i18n` reads JSON catalogs. English is the source. German, Russian, Ukrainian, and Latvian ship with the package. `python -m voxprint_common.i18n.check` fails when a Qt call contains a hard-coded user-facing string, a key is missing, or placeholders differ.

Codename: Netzach.

## 0.2.1

The model downloader keeps a single queue, checks the shared models directory before enqueue, refuses a gated upstream that has no suite mirror, and resumes large files in several HTTP ranges. App manifests can mark a model unavailable and can run a post-install command after the digest check.

Codename: Tiferet.

## 0.2.0

VOXPrint MDL (`voxprint-mdl`) is the suite model downloader. Installers run it after copying files. Apps open their own window only when the status file reports ready. Models land in one shared directory, referenced per app, and a short disk refuses the job before the first request.

Codename: Gevurah.

## 0.1.2

`voxprint_common.power.keep_awake` blocks system sleep for a long job.

Codename: Chesed.

## 0.1.1

Build-info lookup, the splash click hook, absolute model paths under the models directory, and the release workflow that publishes the wheel.

Codename: Menuchah.

## 0.1.0

Initial shared library.

Codename: Yesod.
