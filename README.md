<p align="center">
  <a href="LICENSE"><img alt="Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
  <a href="https://www.python.org/"><img alt="Python 3.14" src="https://img.shields.io/badge/python-3.14-blue"></a>
  <a href="https://mitroshenkov87.github.io/voxprint-common/"><img alt="Docs" src="https://img.shields.io/badge/docs-mkdocs-lightgrey"></a>
</p>

<h1 align="center">voxprint-common</h1>

<p align="center">Shared Python library for the Voxprint AI Media Suite. One copy of the code every app was carrying itself.</p>

Part of the Voxprint AI Media Suite, together with [Enlarge](https://github.com/Mitroshenkov87/voxprint-enlarge), [Clarity](https://github.com/Mitroshenkov87/voxprint-clarity), [Canvas](https://github.com/Mitroshenkov87/voxprint-canvas), [Plotweaver](https://github.com/Mitroshenkov87/voxprint-plotweaver), [Audiobook Builder](https://github.com/Mitroshenkov87/voxprint-audiobook-builder), [Movie Dubber](https://github.com/Mitroshenkov87/voxprint-movie-dubber), and [Model Forge](https://github.com/Mitroshenkov87/voxprint-model-forge).

Each app stays self-sufficient. It bundles this library, or installs it into the shared runtime. voxprint-common is not an application. It has no installer entry point and it never installs another app.

The docs site is [mitroshenkov87.github.io/voxprint-common](https://mitroshenkov87.github.io/voxprint-common/).

## Modules

| Module | What it does |
|---|---|
| `voxprint_common.paths` | Suite directories: `%LOCALAPPDATA%\Voxprint` and `~/.local/share/voxprint` |
| `voxprint_common.atomic` | Temporary file, fsync, rename, and an exclusive file lock |
| `voxprint_common.hashing` | Streaming SHA-256 |
| `voxprint_common.buildinfo` | One `BUILD.json`: app, version, rc, build, codename |
| `voxprint_common.suite_settings` | Shared `state/suite.json` |
| `voxprint_common.gpu_lock` | Cross-process GPU lock with holder app and stage |
| `voxprint_common.thermal` | Shared pause rule: 2.5 h, 83 C, 75 C |
| `voxprint_common.gpu_probe` | nvidia-smi probe and VRAM tiers at 15.5 / 23.5 GiB |
| `voxprint_common.licence` | Allowed-licence rules for code and weights |
| `voxprint_common.runtime_manifest` | Shared runtime manifest, schema 1 |
| `voxprint_common.download` | Multi-source download, SHA-256, resume, disk precheck |
| `voxprint_common.cli_protocol` | `--json` progress lines and one result line |
| `voxprint_common.selftest` | `checks.json` helpers |
| `voxprint_common.ui.theme` | Suite palette. Qt is not imported |
| `voxprint_common.ui.splash` | 960×540 splash. PySide6 loads only when a splash is shown |
| `voxprint_common.provenance` | Documented stub. Embedding arrives in a later version |

## Install

Python 3.14. No required third-party packages. PySide6 is an extra for the splash.

```bash
python -m pip install voxprint-common
python -m pip install "voxprint-common[ui]"
```

From a checkout:

```bash
python -m pip install -e ".[dev]"
```

An app that ships an installer copies the wheel into its payload and installs that wheel into its own environment or the shared runtime. It does not ask the user to install a separate Voxprint Common application.

## Pin a release

Offline installers vendor the wheel from the GitHub release. They do not pin a git commit.

```text
voxprint-common @ https://github.com/Mitroshenkov87/voxprint-common/releases/download/v0.1.1/voxprint_common-0.1.1-py3-none-any.whl
```

The same directory has the sdist and a `.sha256` file beside each archive. Check the wheel against `voxprint_common-0.1.1-py3-none-any.whl.sha256` before installing it. A normal environment pin is `voxprint-common==0.1.1`.

## Usage

```python
from voxprint_common.buildinfo import load
from voxprint_common.paths import home, shared_dir
from voxprint_common.runtime_manifest import add_ref

print(home())
print(load("BUILD.json").label())

add_ref(
    "python-3.14",
    "enlarge",
    kind="runtime",
    version="3.14",
    path="runtime/py314",
    sha256="a" * 64,
)
```

`--json` writes progress lines, then one result line. Exit codes are 0 success, 1 usage error, 2 operation failed, 3 cancelled or timed out. See the docs for the line shapes.

Maintainers moving an app off its private copy of this code should read [MIGRATION.md](MIGRATION.md).

## Requirements

* Windows 11 or Linux.
* Python 3.14.
* A display and the `ui` extra, only for the splash. Everything else is headless.

**License:** Apache-2.0. See `LICENSE` and `NOTICE`.
