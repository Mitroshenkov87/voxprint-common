# Changelog

## Unreleased

Expert mode is one suite setting, `ui.expert_mode`, off by default. Commands, dock panels, and settings tagged expert stay hidden until it is on, and a hidden panel stays in the layout JSON. `view.toggle_expert_mode` is in the View menu and the command palette, with no default shortcut. A minors-safety gate stays inside the app. The shared UI has no setting that turns that gate off. Adult content is an ordinary expert descriptor the app supplies.

Simple mode and Expert mode are two views of one settings state per app. A preset file, schema `voxprint-presets/1`, writes concrete values in file order. A later group owns a key both groups write. A hand edit marks that group. The chip reads "Balanced + changes" while the stored preset is still close, or "Custom" when it is not. Reset restores that group. The choice and the overrides persist in `image-settings.json`. The reference file is `src/voxprint_common/ui/presets/canvas.json`.

Image apps open on `SimpleImageLayout`. Canvas supplies Quality, Style, grouped size ratios, Standard or 4K output, Detail, and how many pictures. 4K can sit on the row greyed out, with a plain reason and Install Enlarge, until Enlarge is present. Improve prompt rewrites the same field. History keeps the last 50 pictures in the app's State directory, including the prompt and the seed. Expert mode adds a side panel for steps, guidance, seed, sampler, a negative prompt, add-ons, a memory limit, and adult content, and leaves the rest of the screen where it is. The dock stays an API. A muted Expert chip shows while the mode is on. The overflow menu calls the app for Manage models. Canvas runs `voxprint-mdl --gui --app canvas`.

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
