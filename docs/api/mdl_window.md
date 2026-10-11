# Download window

The model-downloader window. PySide6 loads only when a window opens.

`run_download_window` is what an installer calls. It focuses the single running window, or starts one, and returns when that app's files are checked. It does not launch the app. `run_manage_window` opens the manage-models page for one app. The command apps should ship on a button is `voxprint-mdl --gui --app <app_id>`.

::: voxprint_common.ui.mdl_window
