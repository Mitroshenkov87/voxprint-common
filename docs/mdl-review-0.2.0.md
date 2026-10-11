# MDL review, 0.2.0

Post-merge review of `voxprint-mdl` 0.2.0 (merge `9322ddb`) against the model-downloader spec. The fixes shipped in 0.2.1, codename Tiferet.

| # | Check | Result |
|---|---|---|
| 1 | Resume: HTTP Range on `.partial`, process restart, backoff | OK |
| 2 | Abandon a slow mirror (84 KiB/s) and switch back onto the same partial | FIXED |
| 3 | Refuse a missing sha256 or an empty Hugging Face revision; accept an explicit `main`; sha256 then atomic placement | FIXED |
| 4 | Installed-app discovery, 23.5 / 15.5 GiB tiers, plan only what is needed, dedupe, `model-` plus 12 hex, disk check `ceil(remaining * 1.10) + 2 GiB` | FIXED |
| 5 | Status file UTF-8, atomic, `ready()` only when state is `ready`; plan/download/status/verify `--json` and exit codes 0/1/2/3 | OK |
| 6 | MDL does not launch an app; installers wait for `ready()` | FIXED |
| 7 | HTTPS allowlist, redirects stay on the list, private and loopback blocked, no URL credentials | FIXED |
| 8 | Windows `nvidia-smi` is `%SystemRoot%\System32\nvidia-smi.exe` with `CREATE_NO_WINDOW` | FIXED |
| 9 | Keep-awake for the duration of the download | OK |

Later engine work in the same branch:

| Item | Result |
|---|---|
| Vendored Model Forge schema `cae1a1e`, checked in tests; runtime parser is not stricter except the empty Hugging Face revision | FIXED |
| Suite index pin; remote copy used only when the sha256 matches; no push from CI | FIXED |
| `voxprint-app-models/1`, `ensure_ready`, progress events, one system-wide queue | FIXED |
| Enqueue skips verified blobs, resumes `.partial`, adds the app ref | FIXED |
| Gated upstreams rejected; pyannote resolves to the public Hugging Face and Kaggle mirrors with no token | FIXED |
| Segmented Range for files over 256 MiB, 4–8 connections, 84 KiB/s floor | FIXED |
| `runtime-py<major><minor>-<stack>-cu<version>` documented beside `model-<12 hex>` | FIXED |
| `unavailable` models shown, excluded from totals, never enqueued | FIXED |
| Post-install hooks after placement, install-dir executable, idempotent marker, friendly error | FIXED |

## OPEN

* 0.2.1 is the pinnable wheel for the apps adopting MDL. Later engine notes belong in 0.2.2.
* The shared download window, Manage models, and `voxprint_common.i18n` shipped in 0.2.2.
* The index `sha256` is the bundled example descriptor. The weight repos do not publish `voxprint-model.json` yet.
