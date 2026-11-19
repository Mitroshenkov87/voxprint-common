# Migration plan: suite apps onto the shared shell

This plan moves every VOXPrint suite app onto the one shared shell that lives
in `voxprint_common.ui`. There is no second API. The modules an app touches
are the ones already in the tree:

- `src/voxprint_common/ui/shell.py` — `ReferenceShell`, the reference main
  window (dock, keymap, palette, status), and `grab_shell` for renders.
- `src/voxprint_common/ui/variants.py` — `AppSurface`, one thin configuration
  per app (input kind, result kind, action, preset file, window minimum).
- `src/voxprint_common/ui/presets/` — the `voxprint-presets/1` preset files
  and `SettingState`, the one settings state both modes read and write.
- `src/voxprint_common/ui/simple_image.py` — `SimpleImageLayout` /
  `SimpleImageWidget`, the shared simple screen every surface paints.

Adoption order is `voxprint_common.ui.shell.ADOPTION`: Canvas first, then
Enlarge, Clarity, Audiobook Builder, Movie Dubber, Plotweaver, Lucent. Model
Forge follows on the tool surface.

## What changes for every app

Each app stops owning a private main window. It hosts `ReferenceShell` (or
the simple-surface helpers in `simple_image.py` when it has no dock), passes
its `app_id`, and keeps its engine behind the existing hooks: `on_generate`
receives the live settings, `on_pause` / `on_resume` serve the busy screen,
and the app's review gate answers the layout's two review calls. Window
chrome — menus, toolbar, command palette, shortcuts — comes from
`commands.py` and `keymap.py`, not from app code.

The old UI of an app stays frozen from the moment its shell release ships:
the first releases that depend on this shell ship only with it, and there is
no parallel restyle of the old window. Bug fixes and visual changes land in
the shared modules and reach every adopted app at once.

## Shared behaviour the apps inherit

- One settings state. Simple mode shows preset chips; Expert mode shows the
  same values live. Both views read and write `SettingState`, and switching
  modes never loses a value. When live values stop matching the stored chip,
  the chip reads "Custom"; `reset_group` restores only that group and leaves
  the other groups unchanged.
- Button radius is 4 px (`tokens.json`, `density.button_radius_px`). Apps do
  not override it.
- UI text is English-only catalog keys; no app hard-codes strings.
- `image.adult_content` is an image-only Expert bool. It ships on the image
  surfaces (Canvas, Enlarge, Lucent) and must not be added to the audio,
  video, text, tool, or viewer surfaces. Reserved keys stay outside the
  shared settings and the preset files; apps must not add or change them.

## Per app

### Canvas (first)

Surface: `AppSurface("canvas", VariantId.IMAGE, InputKind.PROMPT,
ResultKind.PICTURE)`, preset file `presets/canvas.json` via
`reference_presets`, state files `image-settings.json` /
`image-history.json`. Canvas hosts `ReferenceShell` with the full dock and
palette. It is the reference app: nothing here is special-cased.

### Enlarge

Surface: `AppSurface("enlarge", VariantId.IMAGE, InputKind.FILE,
ResultKind.PICTURE)`, preset file `presets/enlarge.json`. Settings are the
image reference list plus the expert `image.scale`; the 4x scale chip stays
unavailable until Enlarge is installed (`variant_presets(...,
enlarge_installed=True)`). Enlarge hosts `ReferenceShell` like Canvas.

### Clarity

Surface: `AppSurface("clarity", VariantId.VIDEO, InputKind.FILE,
ResultKind.COMPARE)`, preset file `presets/video.json`, state files
`video-settings.json` / `video-history.json`. The compare result and the
improve action come from the surface configuration; Clarity hosts
`ReferenceShell` and adds no window of its own.

### Audiobook Builder

Surface: `AppSurface("audiobook-builder", VariantId.AUDIO,
InputKind.MEDIA_VOICE, ResultKind.PLAYER)`, preset file `presets/audio.json`.
The section and wizard demo (`sections.py`, `SectionDeck`) is the app's
training flow inside the shared screen. Audiobook Builder hosts
`ReferenceShell(sections=True)` and its wizards behind the existing
`capture` / `restore` hooks.

### Movie Dubber

Surface: `AppSurface("movie-dubber", VariantId.AUDIO, InputKind.MEDIA_VOICE,
ResultKind.PLAYER)`, preset file `presets/audio.json`, with `video_result`
on and the voice picker off. The video preview and the dubbed-file result
come from the surface flags; the app hosts `ReferenceShell` and supplies the
media itself.

### Plotweaver

Surface: `AppSurface("plotweaver", VariantId.TEXT, InputKind.OUTLINE,
ResultKind.DOCUMENT)`, preset file `presets/text.json`, state files
`text-settings.json` / `text-history.json`. Plotweaver hosts
`ReferenceShell`; the outline input and document result are surface
configuration, not new widgets.

### Lucent

Surface: `AppSurface("lucent", VariantId.IMAGE, InputKind.PROMPT,
ResultKind.PICTURE)`, preset file `presets/canvas.json` (the Canvas file),
state files `image-settings.json` / `image-history.json`. Lucent runs as its
own process and does not import the dock; it hosts the simple surface
helpers from `simple_image.py`, and it may use the tokens, commands, and
palette scorer like any app. The picture-viewer surface
(`AppSurface("viewer", VariantId.VIEWER, ...)`, `presets/viewer.json`) is
the viewer shape for the same process.

### Model Forge (tool surface)

Surface: `AppSurface("model-forge", VariantId.TOOL, InputKind.MODEL,
ResultKind.REPORT)`, preset file `presets/tool.json`, state files
`tool-settings.json` / `tool-history.json`. Model Forge hosts
`ReferenceShell` on the tool surface; model management stays in the existing
manage window (`mdl_window.py`), which the shell opens by command.

## Verification

For each adopted app: the app's window is `ReferenceShell(app=<id>)`; the
window renders through `grab_shell` at 100% and 150%; the app's state file
round-trips (`tests/test_presets.py`, `tests/test_simple_image.py`,
`tests/test_variants.py`); and the offscreen screenshot suite
(`tests/test_shell.py` with `tests/shell_offscreen.py`) stays green. Sample
renders for the four primary surfaces ship from
`scripts/render_shell_variants.py`.
