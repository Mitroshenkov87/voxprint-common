"""Fail when a checkout's voxprint-common pin disagrees with a release wheel.

The wheel identity is the ``.sha256`` file published beside
``voxprint_common-<version>-py3-none-any.whl``. ``VOXPRINT_WHEEL_SHA256``
supplies that digest in tests so the checker does not download a release.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.request
from pathlib import Path

_VERSION = re.compile(r"(\d+\.\d+\.\d+)")
_SHA = re.compile(r"\b([a-f0-9]{64})\b")
_PACKAGE = re.compile(r"voxprint[-_]common", re.IGNORECASE)
_LOCKS = ("uv.lock", "poetry.lock", "pdm.lock", "requirements.txt")


def main() -> int:
    """Compare the checkout pin with the wanted tag and return a process code."""
    tag = os.environ.get("WANTED_TAG", "").strip()
    if not tag:
        print("WANTED_TAG is required", file=sys.stderr)
        return 1
    version = tag[1:] if tag.startswith("v") else tag
    if _VERSION.fullmatch(version) is None:
        print(f"wanted tag {tag} is not a version", file=sys.stderr)
        return 1
    try:
        wheel = os.environ.get("VOXPRINT_WHEEL_SHA256", "").strip().lower() or _download(version)
    except OSError as exc:
        print(f"could not read the wheel sha256: {exc}", file=sys.stderr)
        return 1
    root = Path.cwd()
    pyproject = root / os.environ.get("PYPROJECT", "pyproject.toml")
    errors = check(pyproject, version, wheel, _lock_paths(root))
    for line in errors:
        print(line, file=sys.stderr)
    return 1 if errors else 0


def check(pyproject: Path, version: str, wheel_sha: str, locks: list[Path]) -> list[str]:
    """Return the reasons ``pyproject`` and ``locks`` do not pin ``wheel_sha``.

    Args:
        pyproject: Project file that should name voxprint-common.
        version: Version inside the wanted tag, without a leading ``v``.
        wheel_sha: Lowercase SHA-256 of the published wheel.
        locks: Lock files to read in addition to ``pyproject``.
    """
    wanted = wheel_sha.strip().lower()
    if _SHA.fullmatch(wanted) is None:
        return [f"wheel sha256 is not 64 hex characters: {wheel_sha!r}"]
    texts: list[tuple[str, str]] = []
    if not pyproject.is_file():
        return [f"missing {pyproject}"]
    texts.append((str(pyproject), pyproject.read_text(encoding="utf-8")))
    for path in locks:
        if path.is_file():
            texts.append((str(path), path.read_text(encoding="utf-8")))
    versions: set[str] = set()
    digests: set[str] = set()
    for _name, text in texts:
        found_versions, found_digests = pins(text)
        versions.update(found_versions)
        digests.update(found_digests)
    if not versions and not digests:
        return ["voxprint-common is not pinned"]
    errors: list[str] = []
    if versions and versions != {version}:
        errors.append(f"pinned version {sorted(versions)} is not {version}")
    if wanted not in digests:
        errors.append(f"pin does not record wheel sha256 {wanted}")
    extra = sorted(digest for digest in digests if digest != wanted)
    if extra:
        errors.append(f"pin sha256 {extra[0]} is not the {version} wheel")
    return errors


def pins(text: str) -> tuple[set[str], set[str]]:
    """Return versions and sha256 digests from lines that name voxprint-common.

    Args:
        text: A pyproject or lock file.
    """
    versions: set[str] = set()
    digests: set[str] = set()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _PACKAGE.search(line) is None:
            continue
        window = "\n".join(lines[index : index + 30])
        cut = re.split(r"\n\[\[", window, maxsplit=1)[0]
        for match in re.finditer(
            r"voxprint[-_]common(?:==|[=@\s].*?)(\d+\.\d+\.\d+)",
            cut,
            re.IGNORECASE,
        ):
            versions.add(match.group(1))
        for match in re.finditer(r'version\s*=\s*["\'](\d+\.\d+\.\d+)["\']', cut):
            versions.add(match.group(1))
        digests.update(_SHA.findall(cut))
    return versions, digests


def _download(version: str) -> str:
    url = (
        "https://github.com/Mitroshenkov87/voxprint-common/releases/download/"
        f"v{version}/voxprint_common-{version}-py3-none-any.whl.sha256"
    )
    with urllib.request.urlopen(url, timeout=30) as response:
        body = response.read().decode("utf-8", errors="replace")
    match = _SHA.search(body.lower())
    if match is None:
        raise OSError(f"{url} has no sha256")
    return match.group(1)


def _lock_paths(root: Path) -> list[Path]:
    named = os.environ.get("LOCK", "").strip()
    if named:
        return [root / named]
    return [root / name for name in _LOCKS if (root / name).is_file()]


if __name__ == "__main__":
    raise SystemExit(main())
