"""0.7.7: packaged MDL window icon and desktop entry."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

from voxprint_common import branding
from voxprint_common.mdl import icon as mdl_icon
from voxprint_common.ui import mdl_window

_PNG = b"\x89PNG\r\n\x1a\n"


def test_mdl_icon_resources_are_loaded_and_referenced(tmp_path: Path) -> None:
    for size in mdl_icon.FRAME_SIZES:
        data = mdl_icon.frame_bytes(size)
        assert data[:8] == _PNG
        width, height = struct.unpack(">II", data[16:24])
        assert width == height == size
    ico = mdl_icon.icon_root().joinpath("voxprint-mdl.ico").read_bytes()
    branding.check_ico(ico)
    desktop = mdl_icon.desktop_text()
    assert "Icon=voxprint-mdl" in desktop
    assert "Name=VOXPrint MDL" in desktop
    assert "StartupWMClass=voxprint-mdl" in desktop
    source = Path(mdl_window.__file__).read_text(encoding="utf-8")
    assert "mdl_window_icon" in source
    assert 'install_identity("MDL")' in source
    assert 'setDesktopFileName("voxprint-mdl")' in source
    assert 'setApplicationName("voxprint-mdl")' in source
    if sys.platform == "win32":
        assert mdl_icon.install_user_icons(home=tmp_path / "user") == []
        return
    written = mdl_icon.install_user_icons(home=tmp_path / "user")
    desktop_path = tmp_path / "user" / ".local" / "share" / "applications" / "voxprint-mdl.desktop"
    assert desktop_path in written
    assert "Icon=voxprint-mdl" in desktop_path.read_text(encoding="utf-8")
    frame = (
        tmp_path
        / "user"
        / ".local"
        / "share"
        / "icons"
        / "hicolor"
        / "256x256"
        / "apps"
        / "voxprint-mdl.png"
    )
    assert frame.is_file()
    assert frame.read_bytes()[:8] == _PNG


def test_gui_without_app_opens_manage_models(monkeypatch: pytest.MonkeyPatch) -> None:
    from voxprint_common.mdl.cli import main
    from voxprint_common.ui.mdl_window import WindowResult

    seen: dict[str, str] = {}

    class _Installed:
        id = "canvas"

    def fake_window(app: str, **_kwargs: object) -> WindowResult:
        seen["app"] = app
        return WindowResult.READY

    monkeypatch.setattr(sys, "argv", ["voxprint-mdl-gui"])
    monkeypatch.setattr("voxprint_common.mdl.cli.send_gui", lambda *_a, **_k: None)
    monkeypatch.setattr("voxprint_common.mdl.cli.run_manage_window", fake_window)
    monkeypatch.setattr("voxprint_common.mdl.cli.detach_console", lambda: None)
    monkeypatch.setattr("voxprint_common.mdl.cli.discover", lambda: [_Installed()])
    assert main() == 0
    assert seen["app"] == "canvas"

    seen.clear()
    monkeypatch.setattr("voxprint_common.mdl.cli.discover", lambda: [])
    assert main() == 0
    assert seen["app"] == "audiobook-builder"

    seen.clear()
    from voxprint_common.mdl.catalog import CatalogError

    def boom() -> list[object]:
        raise CatalogError("bad registry")

    monkeypatch.setattr("voxprint_common.mdl.cli.discover", boom)
    assert main() == 0
    assert seen["app"] == "audiobook-builder"

    seen.clear()
    monkeypatch.setattr(sys, "argv", ["voxprint-mdl-gui", "--app=canvas"])
    monkeypatch.setattr("voxprint_common.mdl.cli.discover", lambda: [])
    assert main() == 0
    assert seen["app"] == "canvas"
