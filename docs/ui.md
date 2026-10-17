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

### Content filter

Image apps share one expert setting, `image.content_filter` (`IMAGE_CONTENT_FILTER`). It is a bool, `expert=True`, group `image`, and the default is on, so adult content stays hidden. Canvas, Enlarge, Clarity, and Lucent use this descriptor. This library stores the setting. It does not classify images and it does not add another flow.

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
- [ ] Image apps include `IMAGE_CONTENT_FILTER` and do not add a classifier here.

## Import without Qt

```python
from voxprint_common.ui.dock import Layout, dump_layout, load_layout, visible_layout
from voxprint_common.ui.commands import Registry, reference_commands
from voxprint_common.ui.expert import ExpertMode, active_contexts
from voxprint_common.ui.settings import IMAGE_CONTENT_FILTER, reference_settings
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

`tokens.json` is schema 1: a colour map, editor density, and the splash colours for Audiobook Builder, Movie Dubber, Enlarge, and Clarity. `voxprint_common.ui.theme.TOKENS` is that colour map. `build_style` remains the wizard and download-window stylesheet. `build_shell_style` uses the density block (13 px body, 28 px panel headers, 4 px control radius) with the same colours. A file whose schema is not 1 is refused.

## Reference window

`ReferenceShell` paints two side tab groups around a protected centre, a command palette, and the shell theme. Screenshots below are grabbed with `QT_QPA_PLATFORM=offscreen`.

![English reference shell](img/shell/en-shell.png)

![Russian reference shell](img/shell/ru-shell.png)

![German reference shell](img/shell/de-shell.png)

![English command palette](img/shell/en-palette.png)

![Russian command palette](img/shell/ru-palette.png)

![Splitter while dragging](img/shell/en-split.png)

Settings is a plain panel: how many pictures, and where to save them. Expert mode adds steps, the sampler, and the image content filter, and a muted Expert chip in the status bar. The Advanced panel comes back with them. These pictures are offscreen grabs.

![English settings, simple mode](img/shell/en-simple.png)

![English settings, Expert mode](img/shell/en-expert.png)

![Russian settings, Expert mode](img/shell/ru-expert.png)

The German picture shows the close control on the active side tab. Inactive tabs keep that control hidden until the pointer is over them. The last picture is the root divider while it is dragged: the resting line is one pixel in the border colour, and the drag stroke is a wider accent-blue line. The hit area stays wider than the stroke and stays invisible. The translucent Acrylic backgrounds are a stylesheet flag (`glass=True`). An offscreen grab cannot show operating-system blur, so these pictures use the solid background.
