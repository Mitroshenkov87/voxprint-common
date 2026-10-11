# voxprint-common

Shared Python library for the Voxprint AI Media Suite. One copy of the code every app was carrying itself.

Version 0.1.0. The import package is `voxprint_common`, and `API` is 1.

Each app bundles this library or installs it into the shared runtime. It is not an application.

```python
from voxprint_common.buildinfo import load
from voxprint_common.paths import home

print(home())
print(load("BUILD.json").label())
```

PySide6 is optional. Importing `voxprint_common.ui.theme` or `voxprint_common.ui.splash` does not load Qt. Constructing `Splash` does.

The page for each module is the public API. App maintainers should also read the [migration guide](migration.md).
