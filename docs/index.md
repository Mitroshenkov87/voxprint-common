# voxprint-common

Shared Python library for the Voxprint AI Media Suite. One copy of the code every app was carrying itself.

Version 0.2.4. The import package is `voxprint_common`, and `API` is 1.

Each app bundles this library or installs it into the shared runtime. It is not an application.

```python
from voxprint_common.buildinfo import load
from voxprint_common.paths import home

print(home())
print(load("BUILD.json").label())
```

PySide6 is optional. Importing `voxprint_common.ui.theme`, `voxprint_common.ui.tokens`, `voxprint_common.ui.dock`, `voxprint_common.ui.commands`, `voxprint_common.ui.keymap`, `voxprint_common.ui.expert`, `voxprint_common.ui.settings`, `voxprint_common.ui.simple_image`, `voxprint_common.ui.palette`, `voxprint_common.ui.splash`, or `voxprint_common.mdl` does not load Qt. Constructing `Splash`, `ReferenceShell`, `SimpleImageWidget`, or running `voxprint-mdl download --gui`, does. The [shared UI](ui.md) adoption order starts with Canvas.

VOXPrint MDL is the shared model downloader. Installers run `voxprint-mdl` after copying files. An app opens its window when [the status file reports ready](mdl.md). The download window and Manage models are the same process. UI strings live in [i18n](api/i18n.md).

The page for each module is the public API. App maintainers should also read the [migration guide](migration.md).
