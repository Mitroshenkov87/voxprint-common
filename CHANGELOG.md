# Changelog

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
