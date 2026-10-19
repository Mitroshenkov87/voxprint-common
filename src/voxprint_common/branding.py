"""Windows install identity and app icons for suite installers.

The rules are in ``docs/installer.md``. This module gives each app the same
Start menu group, shortcut name, executable names, icon file name, and
AppUserModelID, packs pre-rendered PNG frames into a multi-size ``.ico``, and
checks an existing ``.ico``. It uses only the standard library.

    python -m voxprint_common.branding check assets/canvas.ico
    python -m voxprint_common.branding pack assets/canvas.ico icon-16.png icon-24.png ...
"""

from __future__ import annotations

import ctypes
import os
import re
import struct
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

SUITE_NAME = "VOXPrint AI Suite"
"""Start menu group and the folder under Program Files."""

PUBLISHER = "Aleksandr Mitroshenkov"
"""Publisher shown in Apps & features."""

ICO_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
"""Frames every app ``.ico`` must contain."""

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_APP_NAME = re.compile(r"^[A-Z][A-Za-z0-9]*( [A-Z][A-Za-z0-9]*)*$")


class IconError(ValueError):
    """An icon file or PNG frame does not meet the suite rule."""


@dataclass(frozen=True)
class InstallIdentity:
    """Names one app uses in its Windows installer.

    Attributes:
        app_name: Short app name, such as ``Canvas`` or ``Model Forge``.
        display_name: ``VOXPrint AI <App>``; the shortcut, window title, and
            Apps & features entry.
        start_menu_group: Always ``VOXPrint AI Suite``.
        shortcut: Shortcut path relative to the Programs folder.
        install_dir: Folder under Program Files.
        gui_exe: GUI launcher file name.
        cli_exe: Console launcher file name.
        icon_file: Icon file name, installed under ``assets``.
        app_user_model_id: Taskbar identity shared by the shortcut and the process.
    """

    app_name: str
    display_name: str
    start_menu_group: str
    shortcut: str
    install_dir: str
    gui_exe: str
    cli_exe: str
    icon_file: str
    app_user_model_id: str


def install_identity(app_name: str) -> InstallIdentity:
    """Return the installer names for one app.

    Args:
        app_name: Short app name in title case, such as ``Canvas``.

    Returns:
        The names that installer uses.

    Raises:
        ValueError: The name is not title-case words of letters and digits.
    """
    if not _APP_NAME.match(app_name):
        raise ValueError(f"app name must be title-case words, got {app_name!r}")
    kebab = "voxprint-" + app_name.lower().replace(" ", "-")
    display = f"VOXPrint AI {app_name}"
    return InstallIdentity(
        app_name=app_name,
        display_name=display,
        start_menu_group=SUITE_NAME,
        shortcut=f"{SUITE_NAME}\\{display}.lnk",
        install_dir=f"{SUITE_NAME}\\{app_name}",
        gui_exe=f"{kebab}.exe",
        cli_exe=f"{kebab}-cli.exe",
        icon_file=f"{kebab}.ico",
        app_user_model_id="VOXPrint.AISuite." + app_name.replace(" ", ""),
    )


def _png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != _PNG_MAGIC or data[12:16] != b"IHDR":
        raise IconError("frame is not a PNG")
    return struct.unpack(">II", data[16:24])


def pack_ico(frames: Sequence[bytes]) -> bytes:
    """Pack square PNG frames into one ``.ico``.

    Args:
        frames: PNG bytes, one per size. Each size in ``ICO_SIZES`` must appear once.

    Returns:
        The ``.ico`` bytes, smallest frame first.

    Raises:
        IconError: A frame is not a square PNG, is over 256px, or a size is missing or repeated.
    """
    by_size: dict[int, bytes] = {}
    for data in frames:
        width, height = _png_size(data)
        if width != height:
            raise IconError(f"frame is {width}x{height}, not square")
        if width > 256:
            raise IconError(f"frame of {width}px is larger than 256px")
        if width in by_size:
            raise IconError(f"two frames of {width}px")
        by_size[width] = data
    _require_sizes(by_size)
    sizes = sorted(by_size)
    out = bytearray(struct.pack("<HHH", 0, 1, len(sizes)))
    offset = 6 + 16 * len(sizes)
    for size in sizes:
        data = by_size[size]
        dim = size % 256
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for size in sizes:
        out += by_size[size]
    return bytes(out)


def ico_sizes(data: bytes) -> list[int]:
    """Return the square frame sizes listed in an ``.ico``.

    Args:
        data: ``.ico`` bytes.

    Returns:
        Frame sizes in file order.

    Raises:
        IconError: The bytes are not an icon file.
    """
    if len(data) < 6:
        raise IconError("not an icon file")
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    if reserved != 0 or kind != 1 or len(data) < 6 + 16 * count:
        raise IconError("not an icon file")
    sizes = []
    for index in range(count):
        width, height = data[6 + 16 * index], data[7 + 16 * index]
        if width != height:
            raise IconError(f"frame {index} is not square")
        sizes.append(width or 256)
    return sizes


def check_ico(data: bytes) -> None:
    """Fail unless an ``.ico`` holds every size in ``ICO_SIZES``.

    Args:
        data: ``.ico`` bytes.

    Raises:
        IconError: The file is not an icon or a size is missing.
    """
    _require_sizes(dict.fromkeys(ico_sizes(data), b""))


def _require_sizes(found: Mapping[int, bytes]) -> None:
    missing = [size for size in ICO_SIZES if size not in found]
    if missing:
        raise IconError("missing icon sizes: " + ", ".join(map(str, missing)))


def set_app_user_model_id(app_id: str) -> bool:
    """Give this process the taskbar identity of its Start menu shortcut.

    Call it before the first window opens, then set the window icon from the
    installed ``.ico``. Without it the taskbar groups the app under
    ``pythonw.exe`` and shows the Python icon.

    Args:
        app_id: ``InstallIdentity.app_user_model_id``.

    Returns:
        True when Windows accepted the id. False elsewhere or on failure.
    """
    if os.name != "nt":
        return False
    try:
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        return shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id)) == 0
    except AttributeError, OSError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``check <ico>`` or ``pack <out.ico> <png>...``.

    Args:
        argv: Arguments without the program name.

    Returns:
        0 on success, 1 when the icon fails the rule, 2 on bad usage.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) == 2 and args[0] == "check":
            check_ico(Path(args[1]).read_bytes())
            print(f"{args[1]}: ok")
            return 0
        if len(args) >= 3 and args[0] == "pack":
            data = pack_ico([Path(name).read_bytes() for name in args[2:]])
            Path(args[1]).write_bytes(data)
            print(f"{args[1]}: {len(data)} bytes")
            return 0
    except (OSError, IconError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        "usage: python -m voxprint_common.branding check <ico> | pack <out.ico> <png>...",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
