# ui.simple_image

Shared simple screen. `SimpleLayout` and `SimpleWidget` are the headless description and the Qt view. Image, audio, video, text, tool, and viewer are configurations in `voxprint_common.ui.variants`. The viewer has no input and no primary action. Importing this module does not import PySide6. Expert mode adds a side panel. The dock is an API, not a control on this screen. The history strip reads `HISTORY_CELL_SIZE` / `HISTORY_CELL_GAP`, is resized through the widget's `cell_size` / `cell_gap` properties, and snaps its scroll offset with `snap_history_offset`.

::: voxprint_common.ui.simple_image
