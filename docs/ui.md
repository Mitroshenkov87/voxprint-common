# Shared UI

Phase 1 is the headless dock, the command registry, the design tokens, and a reference window. Apps adopt it in this order: Canvas, Enlarge, Clarity, Audiobook Builder, Movie Dubber, Plotweaver, then the Lucent helper. The order is `voxprint_common.ui.shell.ADOPTION`. This library change does not edit those apps.

The Lucent viewer process does not import the dock. Its helper can use the tokens, the command registry, and the palette scorer.

## Simple by default

Every app opens in simple mode. The words are plain verbs. Simple mode has no jargon, a sensible default, and as few controls as the job allows. A control that needs a jargon word, a step count, or a sampler name is tagged `expert=True`. It is visible only when Expert mode is on.

Expert mode is one suite setting, `ui.expert_mode` in `state/suite.json`. It is off by default. `ExpertMode` stores that bool and notifies listeners when it changes. Commands, dock panels, and settings carry `expert: bool = False`. `Registry.visible`, `visible_layout`, and `Settings.visible` leave expert items out while the mode is off.

Hiding a panel does not remove it from the layout JSON. The stored tree is unchanged. Turning the mode on shows the same panels again.

Tooltips are for expert items. A simple control is labelled clearly enough that it does not need one.

`view.toggle_expert_mode` turns the mode on and off. It has no default shortcut. It is in the View menu and the command palette. The title is "Expert mode", "Режим эксперта", "Expertenmodus", "Режим експерта", and "Eksperta režīms".

A keymap binding may use the context `expert` (`WHEN_EXPERT`). That binding runs only while Expert mode is on. `active_contexts(..., expert=True)` adds the context.

### Adult content and the minors gate

Adult content (18+) is the expert setting `image.adult_content`, default off. On allows adult content and leaves the prompt neutral. Off adds a general-audience template and negatives. That is the whole effect of the setting.

The minors gate is outside the settings state and outside presets. It is always on. The app implements `SafetyGate`, and the layout calls it before a picture starts and after each result. A refusal, a raised error, or a missing gate blocks the picture. Keys under `safety`, and keys whose segments are minor, age, or guard, are rejected. The library has no function that reads or writes a gate switch.

### Adoption checklist

Canvas adopts this first. Then Enlarge, Clarity, Audiobook Builder, Movie Dubber, Plotweaver, and the Lucent helper. The order is `voxprint_common.ui.shell.ADOPTION`. This library change does not edit those apps.

- [ ] Open in simple mode. Leave `ui.expert_mode` off until the user turns it on.
- [ ] Write simple labels as plain verbs. Leave sampler names, step counts, and other jargon out of simple mode.
- [ ] Tag advanced commands, panels, and settings with `expert=True`.
- [ ] Read the flag through `ExpertMode` and subscribe to changes.
- [ ] Keep hidden expert panels in the app layout file. Filter with `visible_layout` only for what is on screen.
- [ ] Put `view.toggle_expert_mode` in the View menu and the command palette. Do not give it a default shortcut.
- [ ] Show a muted Expert chip while the mode is on.
- [ ] Put tooltips on expert items only.
- [ ] Keep the minors gate outside settings and presets. It is always on. Adult content (18+) only selects the prompt policy: a general-audience template and negatives when off, and a neutral prompt when on.
- [ ] Image apps (Canvas, Enlarge, and the Lucent helper) open on the image-app pattern in `SimpleImageLayout`.
- [ ] Drive the chips and the expert panel from one settings state. A preset file writes the values. Keep the chosen preset and any overrides in the app's State directory.
- [ ] Keep Size, Style, Number of pictures, More like this, Save, Open folder, and Use prompt again on that screen.
- [ ] Expert mode only adds the side panel. Leave the rest of the screen where it is. The dock stays an API, with no switch on the image screen.

## Import without Qt

```python
from voxprint_common.ui.dock import Layout, dump_layout, load_layout, visible_layout
from voxprint_common.ui.commands import Registry, reference_commands
from voxprint_common.ui.expert import ExpertMode, active_contexts
from voxprint_common.ui.presets import reference_presets
from voxprint_common.ui.safety import SafetyGate, adult_prompt_policy
from voxprint_common.ui.settings import reference_settings
from voxprint_common.ui.simple_image import SimpleImageLayout, image_presets
from voxprint_common.ui.keymap import WHEN_EXPERT, KeyMap, load_overrides, save_overrides
from voxprint_common.ui.palette import score_text
from voxprint_common.ui.tokens import load
```

Importing `voxprint_common`, or any of those modules, does not import PySide6. Install the `ui` extra before constructing `ReferenceShell`, `Splash`, or the download window.

## Layout

A layout is a tree of tab groups, horizontal and vertical splits, and accordion stacks, plus floating groups that stay inside the same window. `Layout.apply` clones the tree, checks it, and commits only a valid result. `DockHistory` keeps those commits for undo and redo. `layout_json` writes schema 1. `layout_from_json` rejects any other schema.

Each app stores that document itself, for example `Settings/<app>.ui.json`. The shared `state/suite.json` file stores keymap overrides, not layouts.

The centre canvas uses `Permissions.protected()`. It cannot be moved, floated, closed, or merged into as a tab. Splits beside it stay allowed. `QDockWidget` is not used.

```python
from voxprint_common.ui.dock import DockHistory, Permissions, dump_layout, load_layout
from voxprint_common.ui.shell import reference_layout

history = DockHistory(reference_layout())
history.apply(action, lambda panel: Permissions.protected() if panel == "canvas" else Permissions())
saved = dump_layout(history.layout)
restored = load_layout(saved)
```

## Commands and keys

Shared command ids are `file.save`, `edit.undo`, `edit.redo`, and `view.palette`. An app command uses the app name as a prefix, for example `canvas.generate`. The title is an i18n key. The default chord uses `Mod`, which is Ctrl on Windows and Linux and Command on macOS. A `platforms` pair replaces that chord on one system. `when` names the context that must be active. A context binding beats a global binding. A text field keeps the keys: only a command whose `when` is `text` runs while a text field is focused.

`KeyMap.assign` stores a user override and reports conflicts. `save_overrides` writes the schema 1 `keymap` object into suite settings. Unknown saved entries are dropped. Other suite keys stay as they are.

## Tokens

`tokens.json` is schema 1: a colour map, editor density, and the splash colours for Audiobook Builder, Movie Dubber, Enlarge, and Clarity. `voxprint_common.ui.theme.TOKENS` is that colour map. `build_style` remains the wizard and download-window stylesheet. `build_shell_style` uses the density block (13 px body, 28 px panel headers, 4 px control radius, 2 px focus ring) with the same colours. A file whose schema is not 1 is refused. The language catalogs are the JSON files in `ui/locales`, beside `tokens.json`. English is the fallback.

## Image-app pattern

Canvas, Enlarge, and the Lucent helper open on one screen: `SimpleImageLayout`. The level of complexity stays the same from an empty window, through progress, to a finished picture. There is no settings dialog and no jargon. A problem is one plain sentence, such as "Write a few words about the picture."

The screen is a prompt and a yellow Generate button. Enter starts a picture. Shift+Enter adds a line. Improve prompt, when the app supplies a callback, replaces the words in that same box, and Revert puts the previous words back. There is no second prompt field.

Simple mode and Expert mode are two views of one settings state per app. The state file is `image-settings.json` in the app's State directory. It stores the live values, the chosen preset in each group, and the keys a hand edit overrode.

A preset file is schema `voxprint-presets/1`. The reference file is `src/voxprint_common/ui/presets/canvas.json`. Groups stay in file order: Quality (Fast, Balanced, Best), Style, Size, Output size, Detail (Light, Medium, Strong), and Number of pictures. Each option writes concrete values. Quality writes steps, guidance, and sampler. Style writes a negative template and leaves the prompt alone. Size writes a resolution. Output size writes Standard or 4K. Detail writes add-on strength. Number of pictures writes the count.

Merge order, applied when the state is first built:

1. Start from each setting's default.
2. Walk the groups in file order.
3. Write the chosen option's keys in the order they appear on that option.
4. When two groups write the same key, the later group in the file wins and owns that key.
5. Apply stored overrides last. An override stays until that group is reset or the live values match a preset exactly.

Choosing a chip writes that option's keys immediately and leaves every other group's keys alone. The expert panel shows the live values at once. A hand edit marks the group that owns the key. The chip reads "Balanced + changes" while the stored preset is still close: the number of owned keys that differ is greater than zero and no more than half of those keys, rounded down. Reset writes that option's values and leaves keys the option does not write as they are. When the stored preset is not close, and no available option matches exactly, the chip reads "Custom". The stored choice stays, so Reset can still restore it. Turning Expert mode on or off leaves the values, the chosen presets, and the overrides where they are.

Size is grouped, with no orientation switch: Landscape is 16:9, 21:9, 3:2, and 4:3; Square is 1:1; Portrait is 9:16, 2:3, and 3:4. A choice can stay on the row and be unavailable, with a plain reason and an action button. When Enlarge is missing, 4K is grey and the button says Install Enlarge. Style is Photo, Cinematic, Portrait, Illustration, Anime, 3D render, Painting, and Sketch. The layout reports the chosen id. The app owns any words added to the prompt and any negative prompt. On the picture are More like this, Save, Open folder, and Use prompt again.

`cancel()` returns the screen to a clean idle state. `block(reason)` and `unavailable(reason)` show a localized plain sentence in the picture area.

The history strip keeps the last 50 pictures in the app's State directory (`image-history.json`). The menu offers Open, Show in folder, Copy, Use prompt again (the words and the seed), Use as input picture, and Delete. Delete asks in that same menu before it removes the picture. The overflow menu is Open folder, Manage models, Settings, and Expert mode. Manage models calls the app. Canvas runs `voxprint-mdl --gui --app canvas`. The reference shell records that choice and does not start the program.

A long job uses one progress block: a stage label, a percent, an estimate of the time left, and Cancel. `progress(step, total, stage_label, eta_seconds=...)` fills it. `stage_label` is already translated. Omit `eta_seconds` to leave the estimate off.

Expert mode adds a side panel to the right of the picture and does not move or restyle the rest. The panel holds the live values. The reference list is Steps, Guidance (CFG), Seed, Sampler, Picture size, Output size, Negative prompt, Add-ons, Add-on strength, VRAM limit, and Adult content (18+). A path field is expert because Open folder is the everyday action. The adult-content value selects the prompt policy. The minors gate still reviews the prompt and the picture.

The dock (`Layout`, and `ReferenceShell(full_layout=True)`) stays available as an API for tools that arrange panels. The image screen has no control that opens it. Pass the app's `SafetyGate` as `gate`. Without that object, every picture is blocked.

```python
from pathlib import Path

from voxprint_common.ui.presets import reference_presets
from voxprint_common.ui.settings import reference_settings
from voxprint_common.ui.simple_image import SimpleImageLayout

settings = reference_settings()
layout = SimpleImageLayout(
    presets=reference_presets(settings),
    settings=settings,
    state_dir=Path("state") / "canvas",
    gate=app_gate,
)
```

## Variants

`SimpleLayout` is the same screen for every app. A variant is a short configuration: the input, the primary action, the result, and a preset file. It does not add a second layout. Expert mode still only adds the side panel. That panel is 280 px and can be resized from 220 to 420. The overflow menu stays Open folder, Manage models, Settings, and Expert mode. A job shows time left on the status row only when the app supplies a duration.

The code name is `SimpleLayout` / `SimpleWidget` in `voxprint_common.ui.simple_image`. `SimpleImageLayout` and `SimpleImageWidget` are the same classes. `surface_for(app_id)` in `voxprint_common.ui.variants` picks the configuration. `ReferenceShell(app="clarity")` draws that app.

### Image

Canvas, Enlarge, and the Lucent helper. Canvas and Lucent ask for a prompt and the action is Generate. Enlarge asks for a dropped picture and the action is Enlarge. The result is the picture, and it is the largest region. Size is one row: Square, Landscape, or Portrait, then the ratios for that orientation. Quality, Style, Size, and the number of pictures stay visible. Detail shares a row with output size, and Install Enlarge stays on that row. The expert panel is titled Expert settings and shows the live preset: steps, guidance, seed, sampler, and Adult content (18+) off. The pictures below are Canvas. Every grab is 1200 by 900.

![English image screen](img/shell/image-simple.png)

![English image screen, Expert mode](img/shell/image-expert.png)

![English image screen, preset with changes](img/shell/image-modified.png)

![Russian image screen](img/shell/ru-image-simple.png)

![Russian image screen, Expert mode](img/shell/ru-image-expert.png)

![German image screen](img/shell/de-image-simple.png)

![German image screen, Expert mode](img/shell/de-image-expert.png)

### Audio

Audiobook Builder and Movie Dubber. The input is a Choose a book drop area that shows the file name once a book is chosen, plus a voice. The action is Create audiobook or Dub video. The result is a player with sound bars and chapters. History lists past jobs. The preset file is `presets/audio.json` (Pace). The expert panel shows sample rate, format, normalisation, and loudness. The pictures below are Audiobook Builder.

![English audio screen](img/shell/audio-simple.png)

![English audio screen, Expert mode](img/shell/audio-expert.png)

![Russian audio screen](img/shell/ru-audio-simple.png)

![Russian audio screen, Expert mode](img/shell/ru-audio-expert.png)

![English audio screen, preset with changes](img/shell/audio-modified.png)

Audiobook Builder can also open as sections. A single-section screen does not gain a tab row. Six sections fit the 960 px minimum, and any tab that does not fit goes to More instead of a scrollbar. Narration keeps the shared screen and adds Prepare text. Roles is a casting table: each row picks a voice, and that value is stored in the section's settings. Voices lists the voices and opens Train a voice, a three-step wizard whose last step shows progress and Cancel. A restart restores that step from the shared settings and a checkpoint the app provides. Expert fields for another section, or for a wizard step that is not open, stay out of the panel.

![Russian sections](img/shell/ru-sections.png)

![Russian casting table](img/shell/ru-casting.png)

![Russian wizard](img/shell/ru-steps.png)

![Russian wizard, running step](img/shell/ru-steps-running.png)

### Video

Clarity. The input is a dropped video. The action is Improve video. The result is one frame with a before/after wipe. The divider is the slider. The preset file is `presets/video.json` (Strength). The expert panel shows the model, tile size, denoise strength, and encoder/CRF.

![English video screen](img/shell/video-simple.png)

![English video screen, Expert mode](img/shell/video-expert.png)

![English video screen, preset with changes](img/shell/video-modified.png)

![Russian video screen](img/shell/ru-video-simple.png)

![Russian video screen, Expert mode](img/shell/ru-video-expert.png)

### Text

Plotweaver. The input is an idea or an outline. The action is Write. The result is a chapter document. The preset file is `presets/text.json` (Length). The expert panel shows the model, temperature, length target, and style.

![English text screen](img/shell/text-simple.png)

![English text screen, Expert mode](img/shell/text-expert.png)

![English text screen, preset with changes](img/shell/text-modified.png)

![Russian text screen](img/shell/ru-text-simple.png)

![Russian text screen, Expert mode](img/shell/ru-text-expert.png)

### Tool

Model Forge. The input is a model. The action is Prepare model. The result is a short status report. The preset file is `presets/tool.json` (Precision). The expert panel shows the model, quantisation, precision, and output.

![English tool screen](img/shell/tool-simple.png)

![English tool screen, Expert mode](img/shell/tool-expert.png)

![English tool screen, preset with changes](img/shell/tool-modified.png)

![Russian tool screen](img/shell/ru-tool-simple.png)

![Russian tool screen, Expert mode](img/shell/ru-tool-expert.png)

### Viewer

The viewer has no input and no primary action. A thin toolbar offers Open, Save, previous, and next. Presets are Fit, 100%, and Enhance. Enhance stays grey until Enlarge is installed. History is a strip of 88 px thumbnails and can be hidden with `viewer.show_history`. The window minimum for this variant is 640 by 400. Other variants stay at 960 by 640. The first launch is 1200 by 900. Later launches restore the saved rectangle from `suite.json`.

![English viewer](img/shell/viewer-simple.png)

![English viewer, Expert mode](img/shell/viewer-expert.png)

![English viewer, preset with changes](img/shell/viewer-modified.png)

![Russian viewer](img/shell/ru-viewer-simple.png)

![Russian viewer, Expert mode](img/shell/ru-viewer-expert.png)

## Reference window

`ReferenceShell` opens on the image-app screen. `full_layout=True` is the dock API: two side tab groups around a protected centre, a command palette, and the shell theme. Those pictures are separate from the image screen. Screenshots below are grabbed with `QT_QPA_PLATFORM=offscreen`.

![English reference shell](img/shell/en-shell.png)

![Russian reference shell](img/shell/ru-shell.png)

![German reference shell](img/shell/de-shell.png)

![English command palette](img/shell/en-palette.png)

![Russian command palette](img/shell/ru-palette.png)

![Splitter while dragging](img/shell/en-split.png)

The German picture shows the close control on the active side tab. Inactive tabs keep that control hidden until the pointer is over them. The splitter picture is the root divider while it is dragged: the resting line is one pixel in the border colour, and the drag stroke is a wider accent-blue line. The hit area stays wider than the stroke and stays invisible. The translucent Acrylic backgrounds are a stylesheet flag (`glass=True`). An offscreen grab cannot show operating-system blur, so these pictures use the solid background.

Simple mode is the picture screen. Expert mode keeps that screen and adds the side panel. It does not change the window size. The result pictures are offscreen grabs of a painted landscape, not a photograph. The English result strip holds four of those placeholders. History uses the dark surface token.

![English simple mode, before a picture](img/shell/en-simple-empty.png)

![English simple mode, with a picture](img/shell/en-simple-result.png)

![Russian simple mode, with a picture](img/shell/ru-simple-result.png)

![English Expert mode](img/shell/en-expert.png)

The result picture and the Expert picture share one state. The modified picture is that state after Steps is edited: Quality reads "Balanced + changes", and Reset to Balanced is on that row.

![English Expert mode, with a change](img/shell/en-expert-modified.png)
