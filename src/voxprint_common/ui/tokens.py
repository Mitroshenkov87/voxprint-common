"""Versioned suite tokens.

The file beside this module is the language-neutral copy of the suite
palette: a schema number, a colour map, the light-theme palette roles,
editor density, and the per-app splash colours.
:mod:`voxprint_common.ui.theme` reads the same colour map for the wizard
stylesheet and builds the Qt palette from these roles. This module does not
import Qt.

``schema`` is 1. A file with another schema is refused so an older build
does not paint with colours it does not know.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "PALETTE_ROLES",
    "SCHEMA",
    "TOKEN_NAMES",
    "Density",
    "TokenSet",
    "color_map",
    "load",
    "tokens_path",
]

SCHEMA = 1

TOKEN_NAMES = (
    "PRIMARY",
    "PRIMARY_HOVER",
    "PRIMARY_BORDER",
    "TEXT_ON_PRIMARY",
    "PRIMARY_DISABLED_BG",
    "PRIMARY_DISABLED_TEXT",
    "MENU_BG",
    "MENU_SELECTION",
    "ACCENT",
    "ACCENT_HOVER",
    "ACCENT_SOFT",
    "ACCENT_STRONG",
    "TEXT_ON_ACCENT",
    "TEXT",
    "TEXT_MUTED",
    "TEXT_FAINT",
    "TEXT_DISABLED",
    "ROOT_PLAIN",
    "ROOT_GLASS",
    "ROOT_GLASS_MORE",
    "CARD_PLAIN",
    "CARD_GLASS",
    "CARD_GLASS_MORE",
    "CONTROL_BG",
    "CONTROL_HOVER",
    "CONTROL_BORDER",
    "AMBER",
    "NOTE_BG",
    "NOTE_BORDER",
    "GREEN",
    "BADGE_GREEN_TEXT",
    "BADGE_AMBER_TEXT",
    "RED",
)

_DENSITY = (
    "body_px",
    "icon_px",
    "panel_header_px",
    "control_radius_px",
    "border_px",
    "focus_ring_px",
    "chip_pad_px",
)
_IDENTITY = ("audiobook-builder", "movie-dubber", "enlarge", "clarity")
PALETTE_ROLES = (
    "Window",
    "WindowText",
    "Base",
    "AlternateBase",
    "ToolTipBase",
    "ToolTipText",
    "Text",
    "Button",
    "ButtonText",
    "BrightText",
    "Link",
    "LinkVisited",
    "Highlight",
    "HighlightedText",
    "PlaceholderText",
    "Light",
    "Midlight",
    "Mid",
    "Dark",
    "Shadow",
    "Accent",
)


@dataclass(frozen=True)
class Density:
    """Editor spacing. Wizard cards keep the sizes in :func:`theme.build_style`."""

    body_px: int
    icon_px: int
    panel_header_px: int
    control_radius_px: int
    border_px: int
    focus_ring_px: int
    chip_pad_px: int


@dataclass(frozen=True)
class TokenSet:
    """One version of the suite palette."""

    schema: int
    name: str
    colors: Mapping[str, str]
    density: Density
    identity: Mapping[str, str]
    light: Mapping[str, str]


def tokens_path() -> Path:
    """Return the packaged ``tokens.json`` path."""
    return Path(__file__).with_name("tokens.json")


def load() -> TokenSet:
    """Load and check ``tokens.json``.

    Returns:
        The versioned token set.

    Raises:
        ValueError: The file is not schema 1 or a required token is missing.
    """
    payload = json.loads(tokens_path().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("tokens.json must be an object")
    schema = payload.get("schema")
    if schema != SCHEMA:
        raise ValueError(f"tokens.json schema must be {SCHEMA}, got {schema!r}")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("tokens.json name must be text")
    colors = _strings(payload.get("colors"), TOKEN_NAMES, "colors")
    density_raw = payload.get("density")
    if not isinstance(density_raw, dict):
        raise ValueError("tokens.json density must be an object")
    density_values = [_positive(density_raw.get(key), key) for key in _DENSITY]
    identity = _strings(payload.get("identity"), _IDENTITY, "identity")
    light = _strings(payload.get("light"), PALETTE_ROLES, "light")
    return TokenSet(
        schema=SCHEMA,
        name=name,
        colors=colors,
        density=Density(*density_values),
        identity=identity,
        light=light,
    )


def color_map() -> dict[str, str]:
    """Return the colour tokens as a new dict.

    This is the map :mod:`voxprint_common.ui.theme` publishes as ``TOKENS``.
    """
    return dict(load().colors)


def _strings(value: object, names: tuple[str, ...], label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"tokens.json {label} must be an object")
    missing = [name for name in names if name not in value]
    if missing:
        raise ValueError(f"tokens.json {label} missing {missing[0]}")
    parsed: dict[str, str] = {}
    for name in names:
        item = value[name]
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"tokens.json {label}.{name} must be text")
        parsed[name] = item
    return parsed


def _positive(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"tokens.json density.{name} must be a positive integer")
    return value
