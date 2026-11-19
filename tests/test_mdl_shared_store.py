"""Shared-store core: blob resolution, adoption, and stub-view repair."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from voxprint_common.hashing import sha256_bytes
from voxprint_common.mdl import store, views
from voxprint_common.mdl.store import StoreFile, link_or_copy, resolve_file
from voxprint_common.paths import models_dir

KEY = "z-image-turbo-bf16-16gb-upstream"


@pytest.fixture(autouse=True)
def _models_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VOXPRINT_MODELS_DIR", str(tmp_path / "models"))
    return models_dir()


def _item(path: str, payload: bytes) -> StoreFile:
    return StoreFile(
        path=path,
        sha256=sha256_bytes(payload),
        size=len(payload),
        repo="upstream/z-image-turbo",
        revision="v1.0",
    )


def _write_blob(root: Path, item: StoreFile, payload: bytes) -> Path:
    blob = root / "blobs" / item.sha256
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(payload)
    blob.with_name(blob.name + ".verified").write_text(item.sha256 + "\n", encoding="utf-8")
    return blob


def test_resolve_file_returns_store_for_a_verified_blob() -> None:
    root = models_dir()
    payload = b"verified-weight-bytes"
    item = _item("transformer/w.safetensors", payload)
    blob = _write_blob(root, item, payload)
    state, path = resolve_file(root, item)
    assert state == "store"
    assert path == blob


def test_resolve_file_adopts_from_another_apps_folder() -> None:
    root = models_dir()
    payload = b"forge-weight-bytes"
    item = _item("transformer/w.safetensors", payload)
    source = root / "model-forge" / "z-image-turbo" / "transformer" / "w.safetensors"
    source.parent.mkdir(parents=True)
    source.write_bytes(payload)
    state, path = resolve_file(root, item)
    assert state == "adopted"
    blob = root / "blobs" / item.sha256
    assert path == blob
    assert blob.is_file()
    assert os.path.samefile(blob, source)
    marker = blob.with_name(blob.name + ".verified")
    assert marker.read_text(encoding="utf-8") == item.sha256 + "\n"
    refs = store.load_refs(root)
    assert refs["refs"]["upstream/z-image-turbo@v1.0"]["transformer/w.safetensors"] == item.sha256
    assert source.read_bytes() == payload
    assert source.exists()


def test_resolve_file_skips_a_copy_with_the_wrong_hash() -> None:
    root = models_dir()
    item = _item("transformer/w.safetensors", b"aaa")
    source = root / "model-forge" / "z-image-turbo" / "transformer" / "w.safetensors"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"bbb")
    state, path = resolve_file(root, item)
    assert (state, path) == ("missing", None)
    assert not (root / "blobs" / item.sha256).exists()
    assert source.read_bytes() == b"bbb"


def test_find_copy_matches_on_basename_when_the_folder_differs() -> None:
    root = models_dir()
    payload = b"loose-weight-bytes"
    item = _item("transformer/diffusion_pytorch_model-00001-of-00003.safetensors", payload)
    source = root / "model-forge" / "z-image-turbo" / "unpacked" / item.path.split("/")[-1]
    source.parent.mkdir(parents=True)
    source.write_bytes(payload)
    state, path = resolve_file(root, item, adopt=False)
    assert state == "found"
    assert path == source
    assert not (root / "blobs" / item.sha256).exists()


def _refused(_src: Path, _dst: Path) -> None:
    raise OSError("links are unavailable here")


def test_link_or_copy_falls_back_past_a_failed_hardlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "w.safetensors"
    source.write_bytes(b"bytes")
    dest = tmp_path / "blobs" / "w.safetensors"
    monkeypatch.setattr(store, "_hardlink", _refused)
    mode = link_or_copy(source, dest)
    assert mode in {"reflink", "symlink", "copy"}
    assert dest.read_bytes() == b"bytes"


def test_link_or_copy_copies_when_every_link_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "w.safetensors"
    source.write_bytes(b"bytes")
    dest = tmp_path / "blobs" / "w.safetensors"
    monkeypatch.setattr(store, "_hardlink", _refused)
    monkeypatch.setattr(store, "_reflink", _refused)
    monkeypatch.setattr(store, "_symlink", _refused)
    mode = link_or_copy(source, dest)
    assert mode == "copy"
    assert dest.read_bytes() == b"bytes"


def test_link_or_copy_without_copy_raises_and_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "w.safetensors"
    source.write_bytes(b"bytes")
    dest = tmp_path / "blobs" / "w.safetensors"
    monkeypatch.setattr(store, "_hardlink", _refused)
    monkeypatch.setattr(store, "_reflink", _refused)
    monkeypatch.setattr(store, "_symlink", _refused)
    with pytest.raises(OSError, match="links are unavailable here"):
        link_or_copy(source, dest, allow_copy=False)
    assert not dest.exists()
    assert list(dest.parent.glob("*.linking")) == []


def test_link_or_copy_on_windows_never_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "w.safetensors"
    source.write_bytes(b"bytes")
    dest = tmp_path / "blobs" / "w.safetensors"
    monkeypatch.setattr(sys, "platform", "win32")

    def forbidden_symlink(_src: Path, _dst: Path) -> None:
        raise AssertionError("symlinks must never be tried on Windows")

    monkeypatch.setattr(store, "_hardlink", _refused)
    monkeypatch.setattr(store, "_symlink", forbidden_symlink)
    mode = link_or_copy(source, dest)
    assert mode == "copy"
    assert dest.read_bytes() == b"bytes"


def test_link_or_copy_still_symlinks_when_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Views keep the default: with links refused, an entry may be a symlink."""
    source = tmp_path / "w.safetensors"
    source.write_bytes(b"bytes")
    dest = tmp_path / "views" / "w.safetensors"
    monkeypatch.setattr(store, "_hardlink", _refused)
    monkeypatch.setattr(store, "_reflink", _refused)
    mode = link_or_copy(source, dest)
    if sys.platform == "win32":
        assert mode == "copy"
        assert dest.is_file() and not dest.is_symlink()
    else:
        assert mode == "symlink"
        assert dest.is_symlink()
    assert dest.read_bytes() == b"bytes"


def test_adopt_into_store_copies_past_failed_links_and_blob_stays_regular(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cross-device source must never become a symlinked blob."""
    root = models_dir()
    payload = b"cross-device-weight-bytes"
    item = _item("transformer/w.safetensors", payload)
    source = tmp_path / "external" / "w.safetensors"
    source.parent.mkdir(parents=True)
    source.write_bytes(payload)
    monkeypatch.setattr(store, "_hardlink", _refused)
    monkeypatch.setattr(store, "_reflink", _refused)

    mode = store.adopt_into_store(root, item, source)

    assert mode == "copy"
    blob = root / "blobs" / item.sha256
    assert stat.S_ISREG(blob.lstat().st_mode)
    assert not blob.is_symlink()
    source.unlink()
    assert blob.read_bytes() == payload
    assert store.blob_ready(root, item)


def test_adopt_into_store_replaces_a_leftover_symlink_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adoption re-publishes over a symlinked blob instead of "reusing" it."""
    root = models_dir()
    payload = b"weight-bytes"
    item = _item("transformer/w.safetensors", payload)
    source = tmp_path / "external" / "w.safetensors"
    source.parent.mkdir(parents=True)
    source.write_bytes(payload)
    blob = root / "blobs" / item.sha256
    blob.parent.mkdir(parents=True)
    try:
        blob.symlink_to(source)
    except OSError:
        pytest.skip("this platform cannot create symlinks")

    mode = store.adopt_into_store(root, item, source)

    assert mode == "hardlink"
    assert stat.S_ISREG(blob.lstat().st_mode)
    assert not blob.is_symlink()
    assert os.path.samefile(blob, source)
    source.unlink()
    assert blob.read_bytes() == payload
    assert store.blob_ready(root, item)


def test_adopt_into_store_copies_over_a_leftover_symlink_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = models_dir()
    payload = b"weight-bytes"
    item = _item("transformer/w.safetensors", payload)
    source = tmp_path / "external" / "w.safetensors"
    source.parent.mkdir(parents=True)
    source.write_bytes(payload)
    blob = root / "blobs" / item.sha256
    blob.parent.mkdir(parents=True)
    try:
        blob.symlink_to(source)
    except OSError:
        pytest.skip("this platform cannot create symlinks")
    monkeypatch.setattr(store, "_hardlink", _refused)
    monkeypatch.setattr(store, "_reflink", _refused)

    mode = store.adopt_into_store(root, item, source)

    assert mode == "copy"
    assert stat.S_ISREG(blob.lstat().st_mode)
    assert not blob.is_symlink()
    source.unlink()
    assert blob.read_bytes() == payload
    assert store.blob_ready(root, item)


def test_blob_ready_rejects_a_symlink_blob_even_when_marked_and_sized(
    tmp_path: Path,
) -> None:
    root = models_dir()
    payload = b"symlinked-weight-bytes"
    item = _item("transformer/w.safetensors", payload)
    source = tmp_path / "external.safetensors"
    source.write_bytes(payload)
    blob = root / "blobs" / item.sha256
    blob.parent.mkdir(parents=True)
    try:
        blob.symlink_to(source)
    except OSError:
        pytest.skip("this platform cannot create symlinks")
    blob.with_name(blob.name + ".verified").write_text(item.sha256 + "\n", encoding="utf-8")

    assert blob.is_symlink()
    assert store.blob_ready(root, item) is False
    state, path = resolve_file(root, item)
    assert (state, path) == ("missing", None)


def _seed_blobs(root: Path, payloads: dict[str, bytes]) -> dict[str, str]:
    files = {name: sha256_bytes(data) for name, data in payloads.items()}
    for name, data in payloads.items():
        _write_blob(
            root,
            StoreFile(path=name, sha256=files[name], size=len(data)),
            data,
        )
    return files


def test_stub_view_is_detected_and_repaired() -> None:
    root = models_dir()
    payloads = {"transformer/a.safetensors": b"aaa", "transformer/b.safetensors": b"bbbb"}
    files = _seed_blobs(root, payloads)
    folder = views.view_directory(root, KEY, files)
    folder.mkdir(parents=True)
    assert views.inspect_view(root, KEY, files) == "stub"
    state, repaired = views.repair_view(root, KEY, files)
    assert state == "repaired"
    assert repaired == folder
    for name, digest in files.items():
        assert os.path.samefile(folder / name, root / "blobs" / digest)
    marker = json.loads((folder / views.MARKER).read_text(encoding="utf-8"))
    assert marker["schema"] == 1
    assert marker["files"] == files
    assert views.inspect_view(root, KEY, files) == "ok"
    assert views.repair_view(root, KEY, files) == ("ok", folder)


def test_stub_view_with_missing_blobs_is_dropped_without_touching_blobs() -> None:
    root = models_dir()
    payload = b"some-weight-bytes"
    files = {"transformer/a.safetensors": sha256_bytes(payload)}
    folder = views.view_directory(root, KEY, files)
    folder.mkdir(parents=True)
    (folder / "stale.txt").write_text("leftover", encoding="utf-8")
    unrelated = root / "blobs" / sha256_bytes(b"unrelated")
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"unrelated")
    state, path = views.repair_view(root, KEY, files)
    assert (state, path) == ("missing", None)
    assert not folder.exists()
    assert unrelated.read_bytes() == b"unrelated"


def test_partial_view_is_detected_and_repaired() -> None:
    root = models_dir()
    payloads = {"transformer/a.safetensors": b"aaa", "transformer/b.safetensors": b"bbbb"}
    files = _seed_blobs(root, payloads)
    state, folder = views.repair_view(root, KEY, files)
    assert state == "repaired"

    def entries_linked() -> None:
        for name, digest in files.items():
            assert os.path.samefile(folder / name, root / "blobs" / digest)

    (folder / "transformer/a.safetensors").unlink()
    assert views.inspect_view(root, KEY, files) == "stub"
    state, folder = views.repair_view(root, KEY, files)
    assert state == "repaired"
    entries_linked()

    zeroed = folder / "transformer/b.safetensors"
    zeroed.unlink()
    zeroed.write_bytes(b"")
    assert views.inspect_view(root, KEY, files) == "stub"
    state, folder = views.repair_view(root, KEY, files)
    assert state == "repaired"
    entries_linked()
    assert views.inspect_view(root, KEY, files) == "ok"


def _complete_looking_view(root: Path, files: dict[str, str]) -> Path:
    """A valid-looking view whose entries symlink the blobs."""
    folder = views.view_directory(root, KEY, files)
    folder.mkdir(parents=True)
    marker = {"schema": views.MARKER_SCHEMA, "key": KEY, "files": files}
    (folder / views.MARKER).write_text(json.dumps(marker) + "\n", encoding="utf-8")
    for name, digest in files.items():
        entry = folder / name
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.symlink_to(root / "blobs" / digest)
    return folder


def test_view_is_never_ok_from_a_truncated_blob() -> None:
    root = models_dir()
    payload = b"complete-weight-bytes"
    name = "transformer/a.safetensors"
    files = {name: sha256_bytes(payload)}
    blob = root / "blobs" / files[name]
    blob.parent.mkdir(parents=True)
    truncated = payload[:5]
    blob.write_bytes(truncated)
    blob.with_name(blob.name + ".verified").write_text(files[name] + "\n", encoding="utf-8")
    folder = _complete_looking_view(root, files)
    sizes = {name: len(payload)}

    assert views.inspect_view(root, KEY, files, sizes=sizes) == "stub"
    assert views.repair_view(root, KEY, files, sizes=sizes) == ("missing", None)
    assert not folder.exists()
    assert views.inspect_view(root, KEY, files, sizes=sizes) == "absent"
    assert blob.read_bytes() == truncated


def test_view_is_never_ok_from_an_unverified_blob() -> None:
    root = models_dir()
    payload = b"complete-weight-bytes"
    name = "transformer/a.safetensors"
    files = {name: sha256_bytes(payload)}
    blob = root / "blobs" / files[name]
    blob.parent.mkdir(parents=True)
    blob.write_bytes(payload)
    folder = _complete_looking_view(root, files)
    sizes = {name: len(payload)}

    assert views.inspect_view(root, KEY, files, sizes=sizes) == "stub"
    assert views.repair_view(root, KEY, files, sizes=sizes) == ("missing", None)
    assert not folder.exists()
    assert views.inspect_view(root, KEY, files, sizes=sizes) == "absent"
    assert blob.read_bytes() == payload


def test_view_is_never_ok_from_a_symlink_blob(tmp_path: Path) -> None:
    root = models_dir()
    payload = b"complete-weight-bytes"
    name = "transformer/a.safetensors"
    files = {name: sha256_bytes(payload)}
    source = tmp_path / "external.safetensors"
    source.write_bytes(payload)
    blob = root / "blobs" / files[name]
    blob.parent.mkdir(parents=True)
    try:
        blob.symlink_to(source)
    except OSError:
        pytest.skip("this platform cannot create symlinks")
    blob.with_name(blob.name + ".verified").write_text(files[name] + "\n", encoding="utf-8")
    folder = _complete_looking_view(root, files)
    sizes = {name: len(payload)}

    assert views.inspect_view(root, KEY, files, sizes=sizes) == "stub"
    assert views.repair_view(root, KEY, files, sizes=sizes) == ("missing", None)
    assert not folder.exists()
    assert views.inspect_view(root, KEY, files, sizes=sizes) == "absent"


def test_materialize_views_builds_other_apps_only_when_their_folder_exists() -> None:
    root = models_dir()
    payloads = {"transformer/a.safetensors": b"aaa"}
    files = _seed_blobs(root, payloads)
    items = [
        SimpleNamespace(
            apps=("canvas", "forge"),
            sha256=digest,
            model_id=KEY,
            name=name,
            layouts=((KEY, name),),
        )
        for name, digest in files.items()
    ]
    built = views.materialize_views(items)
    assert built == [views.view_directory(root, KEY, files, "canvas")]
    assert not (root / "views" / "forge").exists()
    (root / "views" / "forge").mkdir(parents=True)
    built = views.materialize_views(items)
    assert set(built) == {
        views.view_directory(root, KEY, files, "canvas"),
        views.view_directory(root, KEY, files, "forge"),
    }
    assert views.inspect_view(root, KEY, files, "forge") == "ok"


def test_remove_never_deletes_outside_the_apps_views() -> None:
    root = models_dir()
    payload = b"weight-bytes-here"
    digest = sha256_bytes(payload)
    blob = root / "blobs" / digest
    blob.parent.mkdir(parents=True)
    blob.write_bytes(payload)
    base = root / "views" / "canvas"
    base.mkdir(parents=True)
    link = base / "z-image-turbo-bf16-16gb-upstream-deadbeef0000"
    try:
        link.symlink_to(blob.parent, target_is_directory=True)
    except OSError:
        pytest.skip("this platform cannot create symlinks")
    views._remove(root, link, "canvas")
    assert link.is_symlink()
    assert blob.read_bytes() == payload
    views._remove(root, blob, "canvas")
    assert blob.read_bytes() == payload
    views._remove(root, blob.parent, "canvas")
    assert blob.read_bytes() == payload
