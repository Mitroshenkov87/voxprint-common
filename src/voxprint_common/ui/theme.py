"""Dark suite palette: yellow primary actions, soft blue menus and accents.

Tokens live in ``tokens.json`` beside this module so a non-Python client can
read the same values. Text colours are opaque. Panels and controls are nearly
solid. Only the window background is translucent in the Acrylic stylesheet.

The splash bar is not one of these tokens. Each app passes its own identity
colour to :class:`voxprint_common.ui.splash.Splash`.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = [
    "ACCENT",
    "ACCENT_HOVER",
    "ACCENT_SOFT",
    "ACCENT_STRONG",
    "AMBER",
    "BADGE_AMBER_TEXT",
    "BADGE_GREEN_TEXT",
    "CARD_GLASS",
    "CARD_GLASS_MORE",
    "CARD_PLAIN",
    "CONTRAST_PAIRS",
    "CONTROL_BG",
    "CONTROL_BORDER",
    "CONTROL_HOVER",
    "GREEN",
    "MENU_BG",
    "MENU_SELECTION",
    "NOTE_BG",
    "NOTE_BORDER",
    "PRIMARY",
    "PRIMARY_BORDER",
    "PRIMARY_DISABLED_BG",
    "PRIMARY_DISABLED_TEXT",
    "PRIMARY_HOVER",
    "RED",
    "ROOT_GLASS",
    "ROOT_GLASS_MORE",
    "ROOT_PLAIN",
    "TEXT",
    "TEXT_DISABLED",
    "TEXT_FAINT",
    "TEXT_MUTED",
    "TEXT_ON_ACCENT",
    "TEXT_ON_PRIMARY",
    "TOKENS",
    "build_style",
    "contrast_ratio",
    "tokens_path",
]

_FONT = (
    '"Segoe UI Variable Text", "Segoe UI", "Ubuntu", "Noto Sans", '
    '"Cantarell", "DejaVu Sans", sans-serif'
)

_STYLE = """
* {{ font-family: {font}; font-size: 14px; color: {text}; }}
QWidget#root {{ background: {root_bg}; }}
QLabel#title {{ font-size: 26px; font-weight: 600; }}
QLabel#subtitle {{ color: {muted}; }}
QFrame#card {{
    background: {card_bg}; border: 1px solid {border}; border-radius: 12px;
}}
QFrame#card[primary="true"] {{ border: 2px solid {primary}; }}
QLabel#sectiontitle {{
    font-size: 16px; font-weight: 600; background: transparent;
}}
QLabel#fileLabel, QLabel#status {{ color: {muted}; background: transparent; }}
QLabel#hint {{ color: {faint}; font-size: 12px; background: transparent; }}
QLabel#warn {{ color: {amber}; font-size: 12px; background: transparent; }}
QLabel#ok {{ color: {green}; background: transparent; }}
QLabel#footer {{ color: {faint}; font-size: 12px; }}
QLabel#chip {{
    color: {faint}; padding: 3px 8px; border-radius: 10px;
    font-size: 12px; background: #2a2a35;
}}
QLabel#chip[state="ready"] {{
    color: {on_accent}; background: {soft}; font-weight: 600;
}}
QPushButton {{
    background: {control}; border: 1px solid {border};
    border-radius: 8px; padding: 8px 16px;
}}
QPushButton:hover {{ background: {hover}; }}
QPushButton:disabled {{
    color: {disabled}; background: #1f1f28; border-color: #34343f;
}}
QPushButton#primary {{
    background: {primary}; border: 1px solid {primary_border};
    font-weight: 600; color: {on_primary};
}}
QPushButton#primary:hover {{ background: {primary_hover}; }}
QPushButton#primary:disabled {{
    background: {primary_dis_bg}; color: {primary_dis_text}; border-color: #4a4430;
}}
QPushButton#step:checked {{
    background: {primary}; border-color: {primary_border}; color: {on_primary};
}}
QTableWidget {{
    background: #1b1b23; border: 1px solid {border}; border-radius: 8px;
    selection-background-color: {strong};
}}
QHeaderView::section {{
    background: {control}; color: {text}; border: none; padding: 6px;
}}
QSlider::groove:horizontal {{
    height: 6px; background: {control}; border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {accent}; width: 16px; margin: -6px 0; border-radius: 8px;
}}
QProgressBar {{
    background: {control}; border: 1px solid {border};
    border-radius: 8px; text-align: center;
}}
QProgressBar::chunk {{ background: {strong}; border-radius: 7px; }}
QComboBox, QLineEdit {{
    background: {control}; border: 1px solid {border};
    border-radius: 8px; padding: 6px 12px;
}}
QComboBox QAbstractItemView {{
    background: {menu_bg}; selection-background-color: {menu_sel};
    selection-color: #ffffff;
}}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {accent};
    border-radius: 4px; background: {control};
}}
QCheckBox::indicator:checked {{ background: {strong}; border-color: {soft}; }}
QMenu {{ background: {menu_bg}; border: 1px solid {border}; padding: 4px; }}
QMenu::item:selected {{ background: {menu_sel}; color: #ffffff; }}
QListWidget {{
    background: {menu_bg}; border: 1px solid {border}; border-radius: 8px;
}}
QListWidget::item:selected {{ background: {menu_sel}; color: #ffffff; }}
QPlainTextEdit {{
    background: #1b1b23; border: 1px solid {border}; border-radius: 8px;
}}
"""


def tokens_path() -> Path:
    """Return the path of the packaged ``tokens.json``."""
    return Path(__file__).with_name("tokens.json")


def _load_tokens() -> dict[str, str]:
    payload = json.loads(tokens_path().read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or any(
        not isinstance(value, str) for value in payload.values()
    ):
        raise ValueError("tokens.json must be an object of colour strings")
    return {str(key): value for key, value in payload.items()}


TOKENS: dict[str, str] = _load_tokens()

PRIMARY = TOKENS["PRIMARY"]
PRIMARY_HOVER = TOKENS["PRIMARY_HOVER"]
PRIMARY_BORDER = TOKENS["PRIMARY_BORDER"]
TEXT_ON_PRIMARY = TOKENS["TEXT_ON_PRIMARY"]
PRIMARY_DISABLED_BG = TOKENS["PRIMARY_DISABLED_BG"]
PRIMARY_DISABLED_TEXT = TOKENS["PRIMARY_DISABLED_TEXT"]
MENU_BG = TOKENS["MENU_BG"]
MENU_SELECTION = TOKENS["MENU_SELECTION"]
ACCENT = TOKENS["ACCENT"]
ACCENT_HOVER = TOKENS["ACCENT_HOVER"]
ACCENT_SOFT = TOKENS["ACCENT_SOFT"]
ACCENT_STRONG = TOKENS["ACCENT_STRONG"]
TEXT_ON_ACCENT = TOKENS["TEXT_ON_ACCENT"]
TEXT = TOKENS["TEXT"]
TEXT_MUTED = TOKENS["TEXT_MUTED"]
TEXT_FAINT = TOKENS["TEXT_FAINT"]
TEXT_DISABLED = TOKENS["TEXT_DISABLED"]
ROOT_PLAIN = TOKENS["ROOT_PLAIN"]
ROOT_GLASS = TOKENS["ROOT_GLASS"]
ROOT_GLASS_MORE = TOKENS["ROOT_GLASS_MORE"]
CARD_PLAIN = TOKENS["CARD_PLAIN"]
CARD_GLASS = TOKENS["CARD_GLASS"]
CARD_GLASS_MORE = TOKENS["CARD_GLASS_MORE"]
CONTROL_BG = TOKENS["CONTROL_BG"]
CONTROL_HOVER = TOKENS["CONTROL_HOVER"]
CONTROL_BORDER = TOKENS["CONTROL_BORDER"]
AMBER = TOKENS["AMBER"]
NOTE_BG = TOKENS["NOTE_BG"]
NOTE_BORDER = TOKENS["NOTE_BORDER"]
GREEN = TOKENS["GREEN"]
BADGE_GREEN_TEXT = TOKENS["BADGE_GREEN_TEXT"]
BADGE_AMBER_TEXT = TOKENS["BADGE_AMBER_TEXT"]
RED = TOKENS["RED"]

#: Opaque pairs that must stay at or above WCAG AA (4.5:1). Disabled text is exempt.
CONTRAST_PAIRS: list[tuple[str, str]] = [
    (TEXT, ROOT_PLAIN),
    (TEXT, CARD_PLAIN),
    (TEXT, CONTROL_BG),
    (TEXT, CONTROL_HOVER),
    (TEXT_MUTED, ROOT_PLAIN),
    (TEXT_MUTED, CARD_PLAIN),
    (TEXT_FAINT, ROOT_PLAIN),
    (TEXT_FAINT, CARD_PLAIN),
    (TEXT_ON_ACCENT, ACCENT),
    (TEXT_ON_ACCENT, ACCENT_HOVER),
    (TEXT_ON_ACCENT, ACCENT_SOFT),
    (TEXT, ACCENT_STRONG),
    ("#ffffff", ACCENT_STRONG),
    ("#ffffff", MENU_SELECTION),
    (TEXT, MENU_BG),
    (GREEN, CARD_PLAIN),
    (BADGE_GREEN_TEXT, GREEN),
    (BADGE_AMBER_TEXT, AMBER),
    (AMBER, CARD_PLAIN),
    (AMBER, NOTE_BG),
    (AMBER, ROOT_PLAIN),
    (RED, CARD_PLAIN),
    (TEXT_ON_PRIMARY, PRIMARY),
    (TEXT_ON_PRIMARY, PRIMARY_HOVER),
    (PRIMARY_DISABLED_TEXT, PRIMARY_DISABLED_BG),
]


def build_style(glass: bool, *, more: bool = False) -> str:
    """Return the suite Qt stylesheet.

    Args:
        glass: Use the translucent Acrylic backgrounds. False uses the solid
            fallback.
        more: Use the more-transparent Acrylic backgrounds. Ignored when
            ``glass`` is false.

    Returns:
        A stylesheet string. Qt is not imported.
    """
    if glass and more:
        root_bg, card_bg = ROOT_GLASS_MORE, CARD_GLASS_MORE
    elif glass:
        root_bg, card_bg = ROOT_GLASS, CARD_GLASS
    else:
        root_bg, card_bg = ROOT_PLAIN, CARD_PLAIN
    return _STYLE.format(
        font=_FONT,
        text=TEXT,
        muted=TEXT_MUTED,
        faint=TEXT_FAINT,
        disabled=TEXT_DISABLED,
        on_accent=TEXT_ON_ACCENT,
        accent=ACCENT,
        soft=ACCENT_SOFT,
        strong=ACCENT_STRONG,
        control=CONTROL_BG,
        hover=CONTROL_HOVER,
        border=CONTROL_BORDER,
        amber=AMBER,
        green=GREEN,
        primary=PRIMARY,
        primary_hover=PRIMARY_HOVER,
        primary_border=PRIMARY_BORDER,
        on_primary=TEXT_ON_PRIMARY,
        primary_dis_bg=PRIMARY_DISABLED_BG,
        primary_dis_text=PRIMARY_DISABLED_TEXT,
        menu_bg=MENU_BG,
        menu_sel=MENU_SELECTION,
        root_bg=root_bg,
        card_bg=card_bg,
    )


def contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG contrast ratio between two ``#RRGGBB`` colours.

    Args:
        foreground: Hex colour.
        background: Hex colour.

    Returns:
        The ratio, from 1 to 21.
    """
    lighter, darker = sorted((_luminance(foreground), _luminance(background)))
    return (darker + 0.05) / (lighter + 0.05)


def _luminance(colour: str) -> float:
    red, green, blue = _rgb(colour)

    def channel(value: int) -> float:
        scaled = value / 255
        if scaled <= 0.04045:
            return scaled / 12.92
        return ((scaled + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def _rgb(colour: str) -> tuple[int, int, int]:
    text = colour.strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) != 6:
        raise ValueError(f"colour must be #RRGGBB, got {colour!r}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
