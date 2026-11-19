# ui.theme

Suite palette. Colours come from schema 1 `tokens.json` through `voxprint_common.ui.tokens`. `TOKENS` is the colour map. `build_style` is the wizard stylesheet. `build_shell_style` is the editor shell and uses the density block in the same file, including `focus_ring_px`. A push button opts into the tighter `QPushButton[compact="true"]` rule with `mark_compact`; the button radius token stays 4 px. Importing this module does not import Qt.

::: voxprint_common.ui.theme
