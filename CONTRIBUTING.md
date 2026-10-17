# Contributing

voxprint-common is the shared library for the Voxprint AI Media Suite. Changes land through a pull request against `main`.

## What belongs here

Code that every app would otherwise copy: suite paths, the GPU lock, the runtime manifest, the palette, the splash, and the other modules listed in the README. An app-specific pipeline, model catalogue, or installer does not belong here.

The library must stay usable when no other Voxprint app is installed. The console script is `voxprint-mdl`. It downloads models declared by installed apps. It does not install or launch another app.

## Development

Python 3.14. From a checkout:

```bash
python -m pip install -e ".[dev,ui]"
python -m ruff check src tests
python -m ruff format --check src tests
python -m interrogate -c pyproject.toml src/voxprint_common
python -m pytest
python -m voxprint_common.i18n.check --src src/voxprint_common --locales src/voxprint_common/ui/locales --langs en,ru,de,uk,lv
python -m mkdocs build --strict
python -m build
```

Tests that need Windows are marked `windows` and skip elsewhere. POSIX-only tests are marked `posix`. CI installs the `ui` extra and grabs the reference shell with `QT_QPA_PLATFORM=offscreen` on Ubuntu and Windows. Importing the dock, keymap, commands, palette scorer, tokens, theme, splash, or shell module must not load PySide6. Constructing `Splash`, `ReferenceShell`, or the download window needs the `ui` extra.

Public functions, classes, and modules use Google-style docstrings. The interrogate gate is 65 percent of that public API. New public names should be documented anyway.

Imports stay at the top of the module. The exception is PySide6, which is imported inside the functions that open a window: the splash, `voxprint_common.ui.shell`, and `voxprint_common.mdl.progress_ui.run_window`. `hf_xet` is imported inside the Hugging Face transfer path so a missing package falls back to HTTP Range.

## Platform

Windows 11 and Linux are both supported. Prefer the standard library. Optional PySide6 is the only GUI dependency. The splash and the model-downloader progress window import it, and only when a window is opened.

## Docs

User-facing text goes through `voxprint_common.i18n`. English is the source catalog. German, Russian, Ukrainian, and Latvian ship beside it. The checker above fails when a Qt call hard-codes a sentence. The MkDocs site is built from `docs/` with mkdocstrings. Update `MIGRATION.md` when a replacement for per-app code changes.

## Licence

Contributions are under the Apache License 2.0. See `LICENSE` and `NOTICE`.
