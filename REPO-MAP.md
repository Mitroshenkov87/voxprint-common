# REPO-MAP

One-page map of the repository, from the source itself (module docstrings; public `def`/`class` names). `src/voxprint_common` is the shared library of the Voxprint AI Media Suite.

## `src/voxprint_common` — top-level modules

- `atomic.py` — atomic publishes and an exclusive file lock: `write_text`, `write_json`, `replace`, `exclusive_lock`, `LockTimeout`; `hashing.py` — streaming SHA-256: `sha256_file`, `verify`, `HashMismatch`.
- `branding.py` — Windows install identity and app icons: `install_identity`, `pack_ico`, `check_ico`, `identity_colour`, `set_app_user_model_id`, `InstallIdentity`.
- `buildinfo.py` — build identity for every app: `BuildInfo`, `load`, `parse`, `dump`; `selftest.py` — shared self-test result document: `check_call`, `document`, `write`.
- `cli_protocol.py` — line protocol for `--json`: `progress`, `result`, `emit`, `iter_lines`, `Session`.
- `download.py` — multi-source download with SHA-256, resume, disk check: `download`, `huggingface_url`, `modelscope_url`, `DownloadProgress`.
- `gpu_probe.py` — NVIDIA probe and VRAM tiers: `query_gpus`, `assess`, `VramTier`; `ram_probe.py` — RAM tier and pin budget: `ram_tier`, `pin_budget_gib`, `RamTier`; `gpu_lock.py` — cross-process GPU lock: `try_acquire`, `gpu_job`, `Holder`, `Acquired`.
- `image.py` — shared image saving and WebP metadata: `save_image`, `read_metadata`, `build_xmp`, `parse_xmp`; `provenance.py` — provenance embedding stub: `embed_image`, `finalize`, `detect`, `Report`.
- `licence.py` — licence gate for code and weights: `check`, `is_allowed`, `is_noncommercial`, `is_copyleft`, `canonical`.
- `model_park.py` — keep weights RAM-resident, move to GPU per stage: `ModelPark`, `StagingRing`, `for_process`; `model_users.py` — per-model `.users.json` registry: `load_users`, `add_user`, `remove_user`, `hold_users`.
- `paths.py` — suite directories shared by every app: `home`, `shared_dir`, `runtime_dir`, `cache_dir`, `state_dir`, `models_dir`; `home_migration.py` — move an existing suite home to its final path (`migrate`); `suite_settings.py` — shared `suite.json` (schema 1): `read`, `values`, `set_value`, theme/GPU/RAM accessors.
- `power.py` — block system sleep for a long job: `keep_awake`, `WakeLock`; `thermal.py` — suite thermal rule: `decide`, `wait_until_ready`, `Action`, `Decision`.

## `src/voxprint_common/i18n` — UI languages

- `__init__.py` — catalog loading and translation: `tr`, `tr_lang`, `set_language`, `detect_language`, `available_languages`; `check.py` — CI check that UI copy stays translated (`check_tree`).

## `src/voxprint_common/mdl` — shared model downloader

- `__init__.py` / `__main__.py` / `cli.py` — package entry and the `voxprint-mdl` command line (`main`, `ready`).
- `catalog.py` — per-app manifests, install discovery, download plan: `discover`, `plan_files`, `load_manifest`, `require_space`, `Model`, `AppManifest`.
- `descriptor.py` — `voxprint-model/1` and `voxprint-app-models/1` files: `parse_descriptor`, `parse_app_manifest`, `Descriptor`, `AppModels`.
- `index.py` — signed suite model index: `load_index`, `verify_index`, `fetch_indexed`, `SuiteIndex`; `resolve.py` — index rows to cached descriptors: `resolve_descriptors`.
- `job.py` — plan, download, verify a model set: `plan`, `download`, `verify`, `verify_repair`, `Outcome`.
- `fetch.py` — resumable downloads with backoff and mirrors: `fetch_file`, `FetchProgress`; `segments.py` — parallel HTTP Range connections: `download_segments`.
- `queue.py` — one system-wide download queue: `enqueue`, `load_queue`, `run_or_attach`, `ensure_ready`; `control.py` — window-side queue view: `Controller`, `shared_controller`; `instance.py` — single running downloader process: `become_primary`, `send`.
- `status.py` — status file apps poll for `ready`: `load_status`, `save_status`, `ready`; `readiness.py` — presence checks that distrust it: `disk_ready`, `gaps_for`, `Gap`.
- `presence.py` — installed-file status and hash cache: `classify_file`, `cached_digest`, `remember_digest`; `onnx_prebuilt.py` — prebuilt artifact vs installed onnxruntime: `version_matches`, `stale_rows`.
- `adopt.py` / `cleanup.py` — blob-store maintenance: adopt local files (`adopt`), reclaim legacy duplicates (`cleanup`).
- `views.py` — canvas model views over the blob store: `view_directory`, `materialize_views`; `variants.py` — fallback variant choice: `chosen_variants`, `choose_variant`.
- `hooks.py` — post-install commands from app manifests: `apply_hooks`, `files_awaiting_hooks`, `remove_outputs`.
- `ed25519.py` — Ed25519 signatures: `sign`, `verify`, `public_key`; `safety.py` — HTTPS allowlist: `UrlGuard`, `proxied`; `priority.py` — low CPU/IO priority: `background_priority`.
- `events.py` — progress events windows subscribe to: `TransferSession`, `ProgressEvent`; `present.py` — window text/errors/views: `view_for`, `explain`, `ManageView`, `WindowView`.
- `progress_ui.py` — headless progress and verify-repair windows: `run_window`, `run_verify_repair`, `build_verify_window`.
- `names.py` — the product name in one place; `data/` — pinned index snapshot; `schemas/` — vendored JSON Schema.

## `src/voxprint_common/ui` — Qt helpers

- `settings.py` — setting descriptors: `SettingSpec`, `Settings`, `reference_settings`; `tokens.py` — versioned design tokens: `TokenSet`, `load`; `theme.py` — palette and stylesheets: `build_style`, `build_shell_style`, `palette_colors`, `contrast_ratio`.
- `commands.py` — command registry for menus/palette/keymap: `Registry`, `CommandSpec`, `reference_commands`; `palette.py` — fuzzy scoring for the palette: `score_text`, `fuzzy_score`.
- `keymap.py` — chords and user overrides: `KeyMap`, `normalize`, `effective`, `load_overrides`, `save_overrides`; `expert.py` — Expert mode contexts; `safety.py` — adult-content policy and reserved keys.
- `variants.py` — thin per-app configurations: `AppSurface`, `surfaces`, `settings_for`, `variant_presets`; `presets/` — preset files: `parse_presets`, `load_presets`, `reference_presets`.
- `sections.py` — sections/wizards model: `Section`, `Wizard`, `SectionDeck`, `audiobook_sections`; `section_view.py` — Qt pieces (no Qt import at module import).
- `simple_image.py` — shared simple screen: `SimpleImageWidget`, `SimpleImageLayout`, `image_presets`.
- `shell.py` — reference main window wiring dock/keymap/palette: `reference_layout`, `reference_panels`, `grab_shell`; `splash.py` — splash window: `Splash`, `layout`, `identity`.
- `mdl_window.py` — model-downloader window: `run_download_window`, `run_manage_window`, `serve`, `ensure_worker`.
- `dock/` — headless dock model: `model.py` (`Layout`, `DockHistory`, `dump_layout`), `geometry.py` (`Rect`, `DropProposal`, `resolve_drop`), `panels.py` (`PanelSpec`, `visible_layout`).

## `src/voxprint_common/worker` — model jobs in a spawned child process

- `host.py` — Qt-free worker host: `WorkerHost`; `qt.py` — Qt adapter: `QtWorkerHost`, `install_crash_bar`.
- `messages.py` — job specs and events: `JobSpec`, `JobOutcome`, `WorkerEvent`, `Handlers`; `wire.py` — pipe messages: `Submit`, `Progress`, `DidFinish`.
- `context.py` — handler-side object: `JobContext`, `read_checkpoint_file`; `serve.py` — child entry point.

## `src/voxprint_common/testing` — helpers apps import in their own CI

- `gui_smoke.py` — headless main-window walk: `configure_headless`, `exercise`, `assert_survives_worker_crash`.

## `scripts/` — offscreen PNG renders of the windows

- `render_mdl_window.py` (download window), `render_manage_window.py` (manage-models, EN/RU plus 150 % scale), `render_verify_window.py` (check-and-repair); all scripted, nothing downloaded.

## `docs/` — MkDocs site

- MkDocs sources: CLI/installer/memory/pin/migration/UI/worker-host guides, `mdl.md` and the 0.2.0 review, `api/` and `ci/` references, `img/` and `renders/` assets.
