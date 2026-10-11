"""Suite model index from ``Mitroshenkov87/voxprint-model-index``.

``index.json`` is a list of ``{id, repo, revision, sha256}``. ``sha256`` pins
the model descriptor. The Hugging Face weight repos do not ship
``voxprint-model.json`` yet, so the pin is the bundled example under
``data/examples`` until Forge publishes one. A remote copy is used only when
it hashes to the same digest; otherwise the bundled snapshot is the index.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib.resources import files

from voxprint_common.hashing import HASH_RE, sha256_bytes
from voxprint_common.mdl.safety import UnsafeUrl, UrlGuard

__all__ = [
    "INDEX_REPO",
    "IndexEntry",
    "SuiteIndex",
    "SuiteIndexError",
    "bundled_index_bytes",
    "load_index",
    "parse_index",
]

INDEX_REPO = "Mitroshenkov87/voxprint-model-index"
_INDEX_URL = (
    "https://huggingface.co/datasets/Mitroshenkov87/voxprint-model-index/resolve/main/index.json"
)
_KEYS = frozenset({"id", "repo", "revision", "sha256"})


class SuiteIndexError(ValueError):
    """The index document is not a list of pinned model records."""


@dataclass(frozen=True)
class IndexEntry:
    """One model in the suite index."""

    id: str
    repo: str
    revision: str
    sha256: str


@dataclass(frozen=True)
class SuiteIndex:
    """The index that was loaded, and whether it came from the bundle."""

    entries: tuple[IndexEntry, ...]
    source: str
    sha256: str

    def get(self, model_id: str) -> IndexEntry | None:
        """Return the entry for ``model_id``, or None."""
        for entry in self.entries:
            if entry.id == model_id:
                return entry
        return None


def bundled_index_bytes() -> bytes:
    """Return the pinned ``index.json`` bytes shipped in this package."""
    return files("voxprint_common.mdl.data").joinpath("model-index.json").read_bytes()


def parse_index(payload: object) -> tuple[IndexEntry, ...]:
    """Parse an index document.

    Args:
        payload: A decoded JSON value. The document is a list.

    Raises:
        SuiteIndexError: The document is not a list of complete entries.
    """
    if not isinstance(payload, list):
        raise SuiteIndexError("index.json must be a list")
    entries: list[IndexEntry] = []
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, dict) or set(row) != _KEYS:
            raise SuiteIndexError("index entries need id, repo, revision, and sha256")
        model_id = row.get("id")
        repo = row.get("repo")
        revision = row.get("revision")
        digest = row.get("sha256")
        if not isinstance(model_id, str) or not model_id:
            raise SuiteIndexError("index id is required")
        if model_id in seen:
            raise SuiteIndexError(f"duplicate index id {model_id}")
        if not isinstance(repo, str) or not repo or ".." in repo.split("/"):
            raise SuiteIndexError("index repo is required")
        if not isinstance(revision, str) or not revision:
            raise SuiteIndexError("index revision is required")
        if not isinstance(digest, str) or HASH_RE.fullmatch(digest) is None:
            raise SuiteIndexError("index sha256 must be 64 hex characters")
        seen.add(model_id)
        entries.append(IndexEntry(model_id, repo, revision, digest.lower()))
    return tuple(entries)


def load_index(
    *,
    opener: object | None = None,
    url: str | None = None,
    guard: UrlGuard | None = None,
    remote: bool = True,
) -> SuiteIndex:
    """Load the pinned index, falling back to the bundled snapshot.

    Args:
        opener: ``opener(request, timeout) -> response``. Defaults to urllib.
        url: Override the dataset URL. The default is the Hugging Face dataset.
        guard: URL rules. Defaults to the production allowlist.
        remote: When false, skip the network and return the bundle.

    Returns:
        The index. ``source`` is ``remote`` only when the fetched bytes match
        the bundled pin.
    """
    pinned = bundled_index_bytes()
    pin = sha256_bytes(pinned)
    if remote:
        try:
            fetched = _fetch(url or _INDEX_URL, opener, guard or UrlGuard())
        except OSError, UnsafeUrl, urllib.error.URLError, TimeoutError, ValueError:
            fetched = None
        if fetched is not None and sha256_bytes(fetched) == pin:
            return _finish(fetched, "remote", pin)
    return _finish(pinned, "bundled", pin)


def _finish(raw: bytes, source: str, pin: str) -> SuiteIndex:
    if sha256_bytes(raw) != pin and source == "bundled":
        raise SuiteIndexError("bundled model index does not match its pin")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise SuiteIndexError("index.json is not UTF-8 JSON") from exc
    return SuiteIndex(parse_index(payload), source, sha256_bytes(raw))


def _fetch(url: str, opener: object | None, guard: UrlGuard) -> bytes:
    checked = guard.check(url)
    request = urllib.request.Request(checked, method="GET", headers={"User-Agent": "voxprint-mdl"})
    open_url = opener or urllib.request.urlopen
    try:
        response = open_url(request, timeout=10)
    except urllib.error.HTTPError as exc:
        raise OSError(f"HTTP {exc.code}") from exc
    try:
        status = int(getattr(response, "status", 200) or 200)
        if status >= 400:
            raise OSError(f"HTTP {status}")
        body = response.read()
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()
    return bytes(body or b"")
