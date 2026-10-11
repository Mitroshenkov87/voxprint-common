# Changelog

## 0.2.2

The download window is the shared progress UI. One window shows the queue grouped by app, and a second launch focuses that window. Manage models lists what is installed and what this card can add. Closing the window leaves the worker running.

`voxprint_common.i18n` reads JSON catalogs. English is the source. German, Russian, Ukrainian, and Latvian ship with the package. `python -m voxprint_common.i18n.check` fails when a Qt call contains a hard-coded user-facing string, a key is missing, or placeholders differ.

Codename: Netzach.

## 0.2.1

The model downloader keeps a single queue, checks the shared models directory before enqueue, refuses a gated upstream that has no suite mirror, and resumes large files in several HTTP ranges. App manifests can mark a model unavailable and can run a post-install command after the digest check.

Codename: Tiferet.

## 0.2.0

VOXPrint MDL (`voxprint-mdl`) is the suite model downloader. Installers run it after copying files. Apps open their own window only when the status file reports ready. Models land in one shared directory, referenced per app, and a short disk refuses the job before the first request.

Codename: Gevurah.

## 0.1.2

`voxprint_common.power.keep_awake` blocks system sleep for a long job.

Codename: Chesed.

## 0.1.1

Build-info lookup, the splash click hook, absolute model paths under the models directory, and the release workflow that publishes the wheel.

Codename: Menuchah.

## 0.1.0

Initial shared library.

Codename: Yesod.
