# Dependency pin

An app pins voxprint-common by version and by the wheel sha256 published next to that tag. `docs/ci/pin-check.yml` is a reusable GitHub Actions workflow that fails the app build when `pyproject.toml`, or a lock file, disagrees with the wanted tag.

Copy `docs/ci/pin-check.yml` to `.github/workflows/pin-check.yml` in the app, then call it:

```yaml
jobs:
  pin:
    uses: ./.github/workflows/pin-check.yml
    with:
      tag: v0.5.2
```

`tag` is required (`v0.5.2` or `0.5.2`). `pyproject` defaults to `pyproject.toml`. `lock` defaults to empty, which reads `uv.lock`, `poetry.lock`, `pdm.lock`, or `requirements.txt` when that file is in the checkout.

The job checks out the app, checks out this repository at `tag`, and runs `docs/ci/check_pin.py`. The script reads the wheel digest from `voxprint_common-<version>-py3-none-any.whl.sha256` on the GitHub release. It fails when voxprint-common is not pinned, the pinned version is not that tag, the wheel sha256 is missing, or another sha256 is recorded on the pin. A version pin without the wheel sha256 fails.

`VOXPRINT_WHEEL_SHA256` supplies the digest for a local run so the script does not download the release. `WANTED_TAG` is required. This repository's own CI does not call the template: the tag has to exist before an app workflow can check it out.
