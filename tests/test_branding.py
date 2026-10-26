import os
import struct
import zlib

import pytest

from voxprint_common import branding


def _png(size: int) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * size for _ in range(size))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_model_forge_identity_colour_comes_from_tokens():
    assert branding.identity_colour("model-forge") == "#f58a3c"
    if os.name != "nt":
        assert branding.detach_console() is False


def test_identity_and_ico_round_trip(tmp_path):
    ident = branding.install_identity("Model Forge")
    assert ident.display_name == "VOXPrint AI Model Forge"
    assert ident.shortcut == "VOXPrint AI Suite\\VOXPrint AI Model Forge.lnk"
    assert ident.install_dir == "VOXPrint AI Suite\\Model Forge"
    assert (ident.gui_exe, ident.cli_exe) == (
        "voxprint-model-forge.exe",
        "voxprint-model-forge-cli.exe",
    )
    assert ident.app_user_model_id == "VOXPrint.AISuite.ModelForge"
    with pytest.raises(ValueError):
        branding.install_identity("canvas")

    frames = [_png(size) for size in reversed(branding.ICO_SIZES)]
    data = branding.pack_ico(frames)
    assert branding.ico_sizes(data) == list(branding.ICO_SIZES)
    branding.check_ico(data)
    with pytest.raises(branding.IconError, match="256"):
        branding.pack_ico(frames[1:])
    out = tmp_path / "app.ico"
    paths = []
    for size, frame in zip(reversed(branding.ICO_SIZES), frames, strict=True):
        path = tmp_path / f"{size}.png"
        path.write_bytes(frame)
        paths.append(str(path))
    assert branding.main(["pack", str(out), *paths]) == 0
    assert branding.main(["check", str(out)]) == 0
