"""Packaged MDL window icon and the Linux desktop entry."""

from __future__ import annotations

import sys
from importlib.resources import files
from pathlib import Path

FRAME_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256, 1024)
"""Raster frames shipped in the wheel, in pixels."""

ICON_ID = "voxprint-mdl"
"""Freedesktop icon name and the stem of the packaged ``.ico``."""

__all__ = [
    "FRAME_SIZES",
    "ICON_ID",
    "desktop_text",
    "frame_bytes",
    "icon_root",
    "install_user_icons",
    "window_icon",
]


def icon_root() -> object:
    """Return the packaged ``mdl/data/icon`` directory."""
    return files("voxprint_common.mdl.data").joinpath("icon")


def frame_bytes(size: int) -> bytes:
    """Return one packaged PNG frame.

    Args:
        size: A size in :data:`FRAME_SIZES`.

    Returns:
        The PNG bytes.

    Raises:
        ValueError: ``size`` is not a shipped frame.
        FileNotFoundError: The frame is missing from the package.
    """
    if size not in FRAME_SIZES:
        raise ValueError(f"no MDL icon frame of {size}px")
    path = icon_root().joinpath("frames", f"{size}.png")
    return path.read_bytes()


def desktop_text() -> str:
    """Return the packaged ``voxprint-mdl.desktop`` entry."""
    return icon_root().joinpath(f"{ICON_ID}.desktop").read_text(encoding="utf-8")


def window_icon() -> object:
    """Return a ``QIcon`` that holds every packaged frame.

    PySide6 is imported here, so a silent install can import this module
    without Qt.
    """
    from PySide6.QtGui import QIcon, QPixmap

    icon = QIcon()
    for size in FRAME_SIZES:
        pix = QPixmap()
        pix.loadFromData(frame_bytes(size))
        if not pix.isNull():
            icon.addPixmap(pix)
    return icon


def install_user_icons(*, home: Path | None = None) -> list[Path]:
    """Install the frames into the hicolor theme and write the desktop entry.

    This is the Linux layout Canvas and Model Forge use: frames go under
    ``~/.local/share/icons/hicolor/<size>x<size>/apps/voxprint-mdl.png``
    and the desktop file under ``~/.local/share/applications/``. On Windows
    the window uses the packaged ``.ico`` and this function writes nothing.

    Args:
        home: User home used as the root of ``.local/share``. ``Path.home()``
            when omitted.

    Returns:
        Paths that were written. Empty on Windows.
    """
    if sys.platform == "win32":
        return []
    root = (Path.home() if home is None else home) / ".local" / "share"
    written: list[Path] = []
    for size in FRAME_SIZES:
        dest = root / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{ICON_ID}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(frame_bytes(size))
        written.append(dest)
    desktop = root / "applications" / f"{ICON_ID}.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(desktop_text(), encoding="utf-8")
    written.append(desktop)
    return written
