"""Dark suite palette: yellow primary actions, soft blue menus and accents.

Colours live in ``tokens.json`` (schema 1), loaded by
:mod:`voxprint_common.ui.tokens`. Text colours are opaque. Panels and controls
are nearly solid. Only the window background is translucent in the Acrylic
stylesheet.

:func:`build_style` is the wizard and download-window stylesheet. The editor
shell uses :func:`build_shell_style` and the density block in the same file
(13 px body, 28 px panel headers, 4 px control radius). Both read one colour
map.

The splash bar is not one of these tokens. Each app passes its own identity
colour to :class:`voxprint_common.ui.splash.Splash`. Identity colours for the
apps that have one are ``identity`` in ``tokens.json``.
"""

from __future__ import annotations

from pathlib import Path

from voxprint_common.ui.tokens import color_map, load

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
    "build_shell_style",
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
QPushButton#disclosure {{
    background: transparent; border: none; padding: 4px 2px;
    font-weight: 600; font-size: 15px;
}}
QPushButton#disclosure:hover {{ color: {soft}; background: transparent; }}
QPushButton#gear {{ padding: 6px 12px; font-size: 18px; }}
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


TOKENS: dict[str, str] = color_map()

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


def build_shell_style(glass: bool, *, more: bool = False) -> str:
    """Return the editor-shell stylesheet.

    Colours are the shared tokens. Sizes come from the density block: body
    text, panel headers, and the control radius. Qt is not imported.

    Args:
        glass: Use the translucent Acrylic backgrounds. False uses the solid
            fallback.
        more: Use the more-transparent Acrylic backgrounds. Ignored when
            ``glass`` is false.

    Returns:
        A stylesheet string.
    """
    if glass and more:
        root_bg, card_bg = ROOT_GLASS_MORE, CARD_GLASS_MORE
    elif glass:
        root_bg, card_bg = ROOT_GLASS, CARD_GLASS
    else:
        root_bg, card_bg = ROOT_PLAIN, CARD_PLAIN
    density = load().density
    radius = density.control_radius_px
    return f"""
* {{ font-family: {_FONT}; font-size: {density.body_px}px; color: {TEXT}; }}
QWidget#shell {{ background: {root_bg}; }}
QLabel#titlebar {{ font-size: 15px; font-weight: 600; background: transparent; }}
QLabel#status {{ color: {TEXT_MUTED}; background: transparent; }}
QLabel#expertChip {{
    color: {TEXT_MUTED}; background: {CONTROL_BG};
    border: {density.border_px}px solid {CONTROL_BORDER};
    border-radius: {radius}px; padding: 1px 8px; font-size: 11px;
}}
QLabel#panel {{ background: transparent; }}
QLabel#settingGroup {{
    color: {TEXT_MUTED}; font-weight: 600; background: transparent; padding-top: 6px;
}}
QCheckBox {{ background: transparent; spacing: 8px; }}
QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {CONTROL_BG}; border: {density.border_px}px solid {CONTROL_BORDER};
    border-radius: {radius}px; padding: 2px 8px; min-height: {density.panel_header_px}px;
}}
QFrame#dock {{
    background: {card_bg}; border: {density.border_px}px solid {CONTROL_BORDER};
    border-radius: {radius}px;
}}
QFrame#float {{
    background: {card_bg}; border: {density.border_px}px solid {ACCENT};
    border-radius: {radius}px;
}}
QTabWidget::pane {{
    border: {density.border_px}px solid {CONTROL_BORDER}; background: {card_bg};
    border-radius: {radius}px;
}}
QTabBar::tab {{
    background: transparent; color: {TEXT_MUTED}; padding: 0 12px;
    min-height: {density.panel_header_px}px; border: none;
    border-top: 2px solid transparent; margin-right: 2px;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{
    background: {CONTROL_BG}; color: {TEXT}; border-top: 2px solid {ACCENT};
}}
QPushButton {{
    background: {CONTROL_BG}; border: {density.border_px}px solid {CONTROL_BORDER};
    border-radius: {radius}px; padding: 4px 12px; min-height: {density.panel_header_px}px;
}}
QPushButton:hover {{ background: {CONTROL_HOVER}; }}
QPushButton#primary {{
    background: {PRIMARY}; color: {TEXT_ON_PRIMARY};
    border: {density.border_px}px solid {PRIMARY_BORDER};
    font-weight: 600;
}}
QLineEdit, QListWidget {{
    background: {CONTROL_BG}; border: {density.border_px}px solid {CONTROL_BORDER};
    border-radius: {radius}px; padding: 4px 8px;
}}
QListWidget::item:selected {{ background: {MENU_SELECTION}; color: #ffffff; }}
QListWidget#paletteHits {{ background: {card_bg}; border: none; padding: 4px; }}
QListWidget#paletteHits::item {{
    background: transparent; color: {TEXT}; border: none; padding: 0;
}}
QListWidget#paletteHits::item:selected {{ background: transparent; color: {TEXT}; }}
QWidget#paletteRow {{ background: transparent; border-radius: {radius}px; }}
QWidget#paletteRow[selected="true"] {{ background: {ACCENT}; }}
QWidget#paletteRow QLabel#paletteTitle {{ background: transparent; color: {TEXT}; }}
QWidget#paletteRow QLabel#paletteGroup {{
    background: transparent; color: {TEXT_MUTED}; font-size: 11px;
}}
QWidget#paletteRow[selected="true"] QLabel#paletteTitle,
QWidget#paletteRow[selected="true"] QLabel#paletteGroup {{ color: {TEXT_ON_ACCENT}; }}
QLabel#paletteChip {{
    background: {CONTROL_BG}; color: {TEXT_MUTED};
    border: {density.border_px}px solid {CONTROL_BORDER};
    border-radius: {radius}px; padding: 0 6px; font-size: 11px;
}}
QWidget#paletteRow[selected="true"] QLabel#paletteChip {{
    background: transparent; color: {TEXT_ON_ACCENT}; border-color: {TEXT_ON_ACCENT};
}}
QMenuBar {{ background: transparent; }}
QMenuBar::item:selected {{ background: {MENU_SELECTION}; color: #ffffff; }}
QMenu {{ background: {MENU_BG}; border: {density.border_px}px solid {CONTROL_BORDER}; }}
QMenu::item:selected {{ background: {MENU_SELECTION}; color: #ffffff; }}
QSplitter::handle {{ background: transparent; }}
QFrame#splitLine {{ background: {CONTROL_BORDER}; border: none; }}
QFrame#splitLine[hot="true"] {{ background: {ACCENT}; }}
QToolButton#tabClose {{
    background: transparent; border: none; padding: 0; min-width: 14px; max-width: 14px;
    min-height: 14px; max-height: 14px;
}}
QToolButton#tabClose:hover {{ background: {CONTROL_HOVER}; }}
QPlainTextEdit {{
    background: {CONTROL_BG}; color: {TEXT};
    border: {density.border_px}px solid {CONTROL_BORDER};
    border-radius: {radius}px; padding: 8px;
    selection-background-color: {ACCENT};
}}
QPlainTextEdit#promptBox {{ font-size: 16px; padding: 10px 12px; }}
QPushButton#generate {{
    background: {PRIMARY}; color: {TEXT_ON_PRIMARY};
    border: {density.border_px}px solid {PRIMARY_BORDER};
    border-radius: 8px; font-size: 16px; font-weight: 600; padding: 8px 18px;
}}
QPushButton#generate:disabled {{
    background: {CONTROL_BG}; color: {TEXT_MUTED}; border-color: {CONTROL_BORDER};
}}
QPushButton#overflow, QPushButton#expertToggle {{
    background: transparent; color: {TEXT_MUTED}; border: none; padding: 2px 10px;
    min-height: 28px;
}}
QPushButton#overflow::menu-indicator {{ image: none; width: 0; }}
QWidget#actionBar QPushButton {{ min-height: 34px; padding: 4px 14px; }}
QPushButton[chip="true"] {{
    min-height: 30px; padding: 2px 14px; border-radius: 15px;
}}
QPushButton[chip="true"]:checked {{
    background: {PRIMARY}; color: {TEXT_ON_PRIMARY};
    border-color: {PRIMARY_BORDER}; font-weight: 600;
}}
QPushButton[chip="true"]:disabled {{
    background: {CONTROL_BG}; color: {TEXT_MUTED}; border-color: {CONTROL_BORDER};
}}
QPushButton[thumb="true"] {{ padding: 2px; border-radius: 8px; }}
QPushButton[thumb="true"]:checked {{
    border: 2px solid {PRIMARY}; background: {CONTROL_BG};
}}
QLabel#presetLabel, QLabel[presetLabel="true"], QLabel#emptyHint, QLabel#simpleNote {{
    color: {TEXT_MUTED}; background: transparent;
}}
QLabel#emptyHint {{ font-size: 16px; }}
QLabel#progressText, QLabel#blockedText, QLabel#featureText {{
    color: {TEXT}; font-size: 15px; background: transparent;
}}
QLabel#result {{ background: transparent; }}
QFrame#stage, QFrame#expertPanel, QFrame#settingsSheet {{
    background: {card_bg}; border: {density.border_px}px solid {CONTROL_BORDER};
    border-radius: 8px;
}}
QProgressBar#progress {{
    border: none; background: {CONTROL_BG}; border-radius: 4px;
    min-height: 8px; max-height: 8px;
}}
QProgressBar#progress::chunk {{ background: {PRIMARY}; border-radius: 4px; }}
"""


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
