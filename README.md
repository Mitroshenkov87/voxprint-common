<p align="center">
  <a href="LICENSE"><img alt="Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
  <a href="https://www.python.org/"><img alt="Python 3.14" src="https://img.shields.io/badge/python-3.14-blue"></a>
  <a href="https://mitroshenkov87.github.io/voxprint-common/"><img alt="Docs" src="https://img.shields.io/badge/docs-mkdocs-lightgrey"></a>
</p>

<h1 align="center">voxprint-common</h1>

<p align="center">Shared Python library for the Voxprint AI Media Suite. One copy of the code every app was carrying itself.</p>

Part of the Voxprint AI Media Suite, together with [Enlarge](https://github.com/Mitroshenkov87/voxprint-enlarge), [Clarity](https://github.com/Mitroshenkov87/voxprint-clarity), [Canvas](https://github.com/Mitroshenkov87/voxprint-canvas), [Plotweaver](https://github.com/Mitroshenkov87/voxprint-plotweaver), [Audiobook Builder](https://github.com/Mitroshenkov87/voxprint-audiobook-builder), [Movie Dubber](https://github.com/Mitroshenkov87/voxprint-movie-dubber), and [Model Forge](https://github.com/Mitroshenkov87/voxprint-model-forge).

Each app stays self-sufficient. It bundles this library, or installs it into the shared runtime. voxprint-common is not a suite application and it never installs another app. The installer entry point is `voxprint-mdl`: it downloads models after the installer has copied files.

The docs site is [mitroshenkov87.github.io/voxprint-common](https://mitroshenkov87.github.io/voxprint-common/).

## Modules

| Module | What it does |
|---|---|
| `voxprint_common.paths` | Suite directories: `%LOCALAPPDATA%\VOXPrint AI Suite` and `~/.local/share/voxprint-ai-suite` |
| `voxprint_common.branding` | Installer names, multi-size `.ico` check, taskbar id. Rules: [docs/installer.md](docs/installer.md) |
| `voxprint_common.atomic` | Temporary file, fsync, rename, and an exclusive file lock |
| `voxprint_common.hashing` | Streaming SHA-256 |
| `voxprint_common.buildinfo` | One `BUILD.json`: app, version, rc, build, codename |
| `voxprint_common.suite_settings` | Shared `state/suite.json` |
| `voxprint_common.power` | Block system sleep for the length of a long job |
| `voxprint_common.gpu_lock` | Cross-process GPU lock with holder app and stage |
| `voxprint_common.thermal` | Shared pause rule: 2.5 h, 83 C, 75 C |
| `voxprint_common.gpu_probe` | nvidia-smi probe and VRAM tiers at 15.5 / 23.5 GiB |
| `voxprint_common.licence` | Allowed-licence rules for code and weights |
| `voxprint_common.runtime_manifest` | Shared runtime manifest, schema 1 |
| `voxprint_common.download` | Multi-source download, SHA-256, resume, disk precheck |
| `voxprint_common.cli_protocol` | `--json` progress lines and one result line |
| `voxprint_common.selftest` | `checks.json` helpers |
| `voxprint_common.ui.tokens` | Schema 1 colours, editor density, and splash identity colours |
| `voxprint_common.ui.theme` | Suite palette and stylesheets. Qt is not imported |
| `voxprint_common.ui.dock` | Headless tabs, splits, floats, undo, and schema 1 layout JSON |
| `voxprint_common.ui.commands` | Command ids, localised titles, and default chords |
| `voxprint_common.ui.keymap` | Context, conflicts, and chord overrides in `suite.json` |
| `voxprint_common.ui.expert` | Suite-wide Expert mode, off by default |
| `voxprint_common.ui.simple_image` | Shared simple screen. Image, audio, video, text, tool, and viewer are configurations |
| `voxprint_common.ui.variants` | Per-app input, action, result, and preset file for that screen |
| `voxprint_common.ui.safety` | Adult-content prompt policy and the reserved minors-gate keys |
| `voxprint_common.ui.settings` | Setting descriptors. Adult content (18+) selects the prompt policy |
| `voxprint_common.ui.presets` | Preset files, schema `voxprint-presets/1`, writing one shared settings state |
| `voxprint_common.ui.shell` | Reference window. PySide6 loads only when the window is built |
| `voxprint_common.ui.splash` | 960×540 splash. PySide6 loads only when a splash is shown |
| `voxprint_common.i18n` | JSON catalogs. English, German, Russian, Ukrainian, Latvian |
| `voxprint_common.mdl` | VOXPrint MDL. Shared model download, `voxprint-mdl` |
| `voxprint_common.provenance` | Documented stub. Embedding arrives in a later version |

## Install

Python 3.14. jeepney is the only required package. It is pure Python and speaks D-Bus so Linux can hold a sleep inhibit. PySide6 is an extra for the splash and for `voxprint-mdl download --gui`. `hf-xet` is optional; Hugging Face downloads fall back to HTTP Range when it is absent.

```bash
python -m pip install voxprint-common
python -m pip install "voxprint-common[ui]"
```

From a checkout:

```bash
python -m pip install -e ".[dev,ui]"
```

An app that ships an installer copies the wheel into its payload and installs that wheel into its own environment or the shared runtime. It does not ask the user to install a separate Voxprint Common application.

## Pin a release

Offline installers vendor the wheel from the GitHub release. They do not pin a git commit.

```text
voxprint-common @ https://github.com/Mitroshenkov87/voxprint-common/releases/download/v0.2.4/voxprint_common-0.2.4-py3-none-any.whl
```

The same directory has the sdist and a `.sha256` file beside each archive. Check the wheel against `voxprint_common-0.2.4-py3-none-any.whl.sha256` before installing it. A normal environment pin is `voxprint-common==0.2.4`.

## Usage

```python
from voxprint_common.buildinfo import load
from voxprint_common.paths import home, shared_dir
from voxprint_common.power import keep_awake
from voxprint_common.runtime_manifest import add_ref

print(home())
print(load("BUILD.json").label())

with keep_awake("render"):
    ...

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

## Installer integration

Apps do not download models. The installer copies the app, enqueues the models, and waits until the status file is ready. MDL does not open the app. The installer must not open it either until `ready()` is true.

```bash
voxprint-mdl enqueue --app enlarge --manifest "/opt/voxprint-ai-suite/enlarge/mdl.json"
voxprint-mdl download --app enlarge --manifest "/opt/voxprint-ai-suite/enlarge/mdl.json" --gui
voxprint-mdl status --json --wait
```

`download` enqueues and runs the single worker, or attaches when another process already holds the queue. `--gui` needs the `ui` extra and opens the shared download window. `voxprint-mdl-gui --app clarity` opens Manage models for that app without a console. `voxprint-mdl --gui` is the console script. Without `--gui` the job is headless. Exit 0 means the status file is ready when the plan had nothing left to fetch, or the download finished. Exit 2 means the job failed, including a disk check that refused to start and a post-install hook that failed. The app opens its own window only after `voxprint_common.mdl.ready()` is true. A missing status file is not ready.

The full contract, including `mdl.json` and the status file, is in the docs under Model downloader.

Maintainers moving an app off its private copy of this code should read [MIGRATION.md](MIGRATION.md).

## Requirements

* Windows 11 or Linux.
* Python 3.14.
* A display and the `ui` extra, only for the splash, the reference shell, and `voxprint-mdl download --gui`. The download itself is headless. Tests grab the reference shell offscreen.

**License:** Apache-2.0. See `LICENSE` and `NOTICE`.
