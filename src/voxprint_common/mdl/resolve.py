"""Turn index rows into cached ``voxprint-model/1`` descriptors.

The signed index names each model's repository, revision, and the SHA-256 of
``voxprint-model.json``. The bytes are fetched with the same guarded opener
as ``index.json``, checked against that digest, and stored under
``<home>/state/mdl-descriptors``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from voxprint_common.atomic import write_bytes
from voxprint_common.download import DownloadError, huggingface_url
from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl.descriptor import Descriptor, DescriptorError, parse_descriptor_text
from voxprint_common.mdl.index import IndexEntry, SuiteIndex, fetch_indexed, load_index
from voxprint_common.mdl.safety import UnsafeUrl, UrlGuard
from voxprint_common.paths import state_dir

__all__ = ["ResolveError", "descriptor_url", "resolve_descriptors"]


class ResolveError(ValueError):
    """A model id could not be turned into a descriptor."""


def descriptor_url(entry: IndexEntry) -> str:
    """Return the HTTPS URL of ``voxprint-model.json`` for one index row.

    A row whose repo is ``datasets/<owner>/<name>`` is read from that dataset
    at ``descriptors/<id>/voxprint-model.json``. Any other repo is a model
    repository, and the file is ``voxprint-model.json`` at the row's revision.

    Args:
        entry: One signed index row.

    Raises:
        ResolveError: ``repo`` is not an owner/name or a dataset id.
    """
    repo = entry.repo.strip().strip("/")
    revision = entry.revision.strip()
    if repo.startswith("datasets/"):
        dataset = repo.removeprefix("datasets/")
        filename = f"descriptors/{entry.id}/voxprint-model.json"
        try:
            return huggingface_url(dataset, filename, revision).replace(
                "https://huggingface.co/",
                "https://huggingface.co/datasets/",
                1,
            )
        except DownloadError as exc:
            raise ResolveError(f"index repo {entry.repo!r} is not a dataset id") from exc
    try:
        return huggingface_url(repo, "voxprint-model.json", revision)
    except DownloadError as exc:
        raise ResolveError(f"index repo {entry.repo!r} is not owner/name") from exc


def resolve_descriptors(
    model_ids: Sequence[str],
    *,
    opener: object | None = None,
    guard: UrlGuard | None = None,
    index: SuiteIndex | None = None,
) -> dict[str, Descriptor]:
    """Load a descriptor for each id from the signed index.

    A cached file is used when its SHA-256 is the digest recorded on the
    index row. Otherwise the descriptor is fetched and the cache is replaced.

    Args:
        model_ids: Model ids named by an app manifest. Duplicates are fetched once.
        opener: Passed to :func:`voxprint_common.mdl.index.load_index` and to
            the descriptor fetch. Defaults to urllib.
        guard: URL rules. Defaults to the production allowlist.
        index: Index to read. When omitted, the trusted index is loaded.

    Returns:
        Descriptors keyed by model id.

    Raises:
        ResolveError: An id is missing, the fetch fails, the digest does not
            match, or the document is not ``voxprint-model/1``.
    """
    wanted = list(dict.fromkeys(model_ids))
    if not wanted:
        return {}
    loaded = index if index is not None else load_index(opener=opener, guard=guard)
    found: dict[str, Descriptor] = {}
    for model_id in wanted:
        entry = loaded.get(model_id)
        if entry is None:
            raise ResolveError(f"model {model_id} is not in the signed model index")
        found[model_id] = _one(entry, opener, guard)
    return found


def _one(entry: IndexEntry, opener: object | None, guard: UrlGuard | None) -> Descriptor:
    cached = _read_cache(entry.sha256)
    raw = cached if cached is not None else _fetch(entry, opener, guard)
    if sha256_bytes(raw) != entry.sha256:
        raise ResolveError(f"model {entry.id} descriptor sha256 does not match the index")
    if cached is None:
        write_bytes(_cache_path(entry.sha256), raw)
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ResolveError(f"model {entry.id} descriptor is not UTF-8") from exc
    try:
        parsed = parse_descriptor_text(text)
    except DescriptorError as exc:
        raise ResolveError(f"model {entry.id} descriptor is not voxprint-model/1: {exc}") from exc
    if parsed.id != entry.id:
        raise ResolveError(f"descriptor id {parsed.id} does not match {entry.id}")
    return parsed


def _fetch(entry: IndexEntry, opener: object | None, guard: UrlGuard | None) -> bytes:
    url = descriptor_url(entry)
    try:
        return fetch_indexed(url, opener=opener, guard=guard)
    except (OSError, UnsafeUrl, TimeoutError, ValueError) as exc:
        raise ResolveError(f"model {entry.id} descriptor could not be fetched: {exc}") from exc


def _cache_path(digest: str) -> Path:
    return state_dir() / "mdl-descriptors" / f"{digest}.json"


def _read_cache(digest: str) -> bytes | None:
    path = _cache_path(digest)
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if sha256_bytes(raw) != digest:
        return None
    return raw
