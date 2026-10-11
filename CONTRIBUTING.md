# Contributing

voxprint-common is the shared library for the Voxprint AI Media Suite. Changes land through a pull request against `main`.

## What belongs here

Code that every app would otherwise copy: suite paths, the GPU lock, the runtime manifest, the palette, the splash, and the other modules listed in the README. An app-specific pipeline, model catalogue, or installer does not belong here.

The library must stay usable when no other Voxprint app is installed. It has no console script and it does not download or launch another app.

## Development

Python 3.14. From a checkout:

```bash
python -m pip install -e ".[dev]"
python -m ruff check src tests
python -m ruff format --check src tests
python -m interrogate -c pyproject.toml src/voxprint_common
python -m pytest
python -m mkdocs build --strict
python -m build
```

Tests that need Windows are marked `windows` and skip elsewhere. POSIX-only tests are marked `posix`. Qt is not installed in CI. `voxprint_common.ui.splash` must import without loading PySide6. Constructing `Splash` needs the `ui` extra.

Public functions, classes, and modules use Google-style docstrings. The interrogate gate is 65 percent of that public API. New public names should be documented anyway.

Imports stay at the top of the module. The exception is PySide6, which is imported inside the splash functions that open a window.

## Platform

Windows 11 and Linux are both supported. Prefer the standard library. Optional PySide6 is the only GUI dependency, and only the splash imports it.

## Docs

User-facing text is English. The MkDocs site is built from `docs/` with mkdocstrings. Update `MIGRATION.md` when a replacement for per-app code changes.

## Licence

Contributions are under the Apache License 2.0. See `LICENSE` and `NOTICE`.
