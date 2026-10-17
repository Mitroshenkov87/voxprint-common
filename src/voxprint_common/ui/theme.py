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

Qt fills a scroll area's viewport from ``QPalette.Base``, and a stylesheet
background does not paint that viewport. :func:`apply_shell_palette` sets the
application or shell palette from these tokens for the dark theme and the
light theme, so every viewport an app creates is covered.
"""

from __future__ import annotations

from pathlib import Path

from voxprint_common.ui.tokens import PALETTE_ROLES, color_map, load

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
    "apply_shell_palette",
    "build_shell_style",
    "build_style",
    "contrast_ratio",
    "palette_colors",
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


def palette_colors(*, light: bool = False) -> dict[str, str]:
    """Return ``QPalette`` role names and colours for one theme.

    ``Base`` is the colour Qt uses to fill a scroll-area viewport. The dark
    theme takes it from the suite tokens. The light theme takes every role
    from the ``light`` block in ``tokens.json``. Neither base is Qt's
    ``#efefef``.

    Args:
        light: True selects the light theme. False selects the dark tokens.

    Returns:
        Role name to ``#rrggbb``. ``DisabledText`` is included for the
        disabled colour group and is not itself a palette role.
    """
    if light:
        colors = dict(load().light)
        colors["DisabledText"] = colors["PlaceholderText"]
        return colors
    return {
        "Window": ROOT_PLAIN,
        "WindowText": TEXT,
        "Base": CARD_PLAIN,
        "AlternateBase": CONTROL_BG,
        "ToolTipBase": MENU_BG,
        "ToolTipText": TEXT,
        "Text": TEXT,
        "Button": CONTROL_BG,
        "ButtonText": TEXT,
        "BrightText": TEXT,
        "Link": ACCENT,
        "LinkVisited": ACCENT,
        "Highlight": MENU_SELECTION,
        "HighlightedText": TEXT,
        "PlaceholderText": TEXT_MUTED,
        "Light": CONTROL_HOVER,
        "Midlight": CONTROL_BG,
        "Mid": CONTROL_BORDER,
        "Dark": ROOT_PLAIN,
        "Shadow": ROOT_PLAIN,
        "Accent": ACCENT,
        "DisabledText": TEXT_DISABLED,
    }


def apply_shell_palette(target: object, *, light: bool = False) -> None:
    """Set ``target``'s palette from the suite tokens.

    ``target`` is the ``QApplication`` or the shell window. Call this when the
    shell applies its theme. A scroll area created afterwards, including one
    an app creates itself, fills its viewport from this palette.

    Args:
        target: ``QApplication`` or ``QWidget``.
        light: True selects the light theme. False selects the dark tokens.

    The Qt import stays inside this function so importing the theme module
    does not load PySide6.
    """
    gui = _theme_gui()
    palette = gui.QPalette()
    role = gui.QPalette.ColorRole
    group = gui.QPalette.ColorGroup
    colors = palette_colors(light=light)
    for name in PALETTE_ROLES:
        palette.setColor(getattr(role, name), gui.QColor(colors[name]))
    muted = gui.QColor(colors["DisabledText"])
    for name in ("WindowText", "Text", "ButtonText"):
        palette.setColor(group.Disabled, getattr(role, name), muted)
    target.setPalette(palette)  # type: ignore[attr-defined]


def _theme_gui() -> object:
    """Import ``PySide6.QtGui``.

    The import stays inside this function so a headless caller can import the
    theme module without the ``ui`` extra.
    """
    from PySide6 import QtGui

    return QtGui


def build_shell_style(glass: bool, *, more: bool = False, light: bool = False) -> str:
    """Return the editor-shell stylesheet.

    Colours are the shared tokens. Sizes come from the density block: body
    text, panel headers, the control radius, and the focus ring. Text views,
    lists, trees, tables, and scroll-area viewports use those tokens too, for
    the dark theme and the light theme. Qt is not imported.

    Args:
        glass: Use the translucent Acrylic backgrounds. False uses the solid
            fallback.
        more: Use the more-transparent Acrylic backgrounds. Ignored when
            ``glass`` is false.
        light: Use the light-theme palette for viewport backgrounds and text.
            False uses the dark tokens.

    Returns:
        A stylesheet string.
    """
    if glass and more:
        root_bg, card_bg = ROOT_GLASS_MORE, CARD_GLASS_MORE
    elif glass:
        root_bg, card_bg = ROOT_GLASS, CARD_GLASS
    else:
        root_bg, card_bg = ROOT_PLAIN, CARD_PLAIN
    if light:
        light_colors = load().light
        view_bg = light_colors["Base"]
        view_text = light_colors["Text"]
        view_alt = light_colors["AlternateBase"]
        scroll_bg = light_colors["Window"]
    else:
        view_bg, view_text, view_alt, scroll_bg = CONTROL_BG, TEXT, CARD_PLAIN, card_bg
    density = load().density
    radius = density.control_radius_px
    return f"""
* {{ font-family: {_FONT}; font-size: {density.body_px}px; color: {TEXT}; }}
QWidget#shell {{ background: {root_bg}; }}
QLabel#titlebar {{ font-size: 15px; font-weight: 600; background: transparent; }}
QLabel#status {{ color: {TEXT_MUTED}; background: transparent; }}
QLabel#expertChip {{
    color: {TEXT}; background: {CONTROL_BG};
    border: {density.border_px}px solid {ACCENT};
    border-radius: {radius}px; padding: 1px 8px; font-size: 11px;
}}
QLabel#expertTitle {{
    color: {TEXT}; font-size: 15px; font-weight: 600; background: transparent;
}}
QLabel#settingGroup {{
    color: {TEXT_MUTED}; font-size: 11px; font-weight: 600; background: transparent;
    padding: 10px 0 2px 0;
}}
QLabel#panel {{ color: {TEXT}; background: transparent; }}
QCheckBox {{ background: transparent; spacing: 8px; color: {TEXT}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 3px;
    border: {density.border_px}px solid {CONTROL_BORDER}; background: {CONTROL_BG};
}}
QCheckBox::indicator:checked {{
    background: {PRIMARY}; border-color: {PRIMARY_BORDER};
}}
QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {CONTROL_BG}; border: {density.border_px}px solid {CONTROL_BORDER};
    border-radius: {radius}px; padding: 2px 6px; min-height: {density.panel_header_px}px;
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
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QListWidget:focus, QPushButton:focus {{
    border: {density.focus_ring_px}px solid {ACCENT};
}}
QPushButton#primary:focus, QPushButton#generate:focus {{
    border: {density.focus_ring_px}px solid {TEXT_ON_PRIMARY};
}}
QPushButton#overflow {{
    background: {CONTROL_BG}; color: {TEXT};
    border: {density.border_px}px solid {CONTROL_BORDER};
    border-radius: {radius}px; padding: 2px 12px; min-height: 28px;
}}
QPushButton#overflow::menu-indicator {{ image: none; width: 0; }}
QPushButton#expertToggle {{
    background: {CONTROL_BG}; color: {TEXT_MUTED};
    border: {density.border_px}px solid {CONTROL_BORDER};
    border-radius: 14px; padding: 2px {density.chip_pad_px}px; min-height: 28px;
}}
QPushButton#expertToggle:checked {{
    background: {PRIMARY}; color: {TEXT_ON_PRIMARY}; border-color: {PRIMARY_BORDER};
    font-weight: 600;
}}
QWidget#actionBar QPushButton {{ min-height: 34px; padding: 4px 14px; }}
QPushButton[chip="true"] {{
    min-height: 30px; padding: 2px {density.chip_pad_px}px; border-radius: 15px;
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
QLabel#progressText, QLabel#blockedText, QLabel#featureText,
QLabel#progressPercent, QLabel#progressEta, QLabel#chapters, QLabel#reportView {{
    color: {TEXT}; font-size: 15px; background: transparent;
}}
QLabel#fileDrop, QLabel#sourceDrop {{
    color: {TEXT_MUTED}; background: {CONTROL_BG};
    border: {density.border_px}px dashed {CONTROL_BORDER};
    border-radius: {radius}px; padding: 16px; font-size: 15px;
}}
QLabel#player, QLabel#compareBefore, QLabel#compareAfter {{ background: transparent; }}
QPlainTextEdit#documentView {{ font-size: 16px; padding: 16px 18px; }}
QLabel#result {{ background: transparent; }}
QFrame#stage, QFrame#expertPanel, QFrame#settingsSheet {{
    background: {card_bg}; border: {density.border_px}px solid {CONTROL_BORDER};
    border-radius: 8px;
}}
QAbstractScrollArea > QWidget {{
    background: {scroll_bg}; color: {view_text};
}}
QScrollArea {{
    background: {scroll_bg}; border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: {scroll_bg}; color: {view_text};
}}
QTextEdit, QPlainTextEdit, QTextBrowser,
QListView, QListWidget, QTreeView, QTreeWidget,
QTableView, QTableWidget, QHeaderView {{
    background: {view_bg}; color: {view_text};
    alternate-background-color: {view_alt};
}}
QTextEdit > QWidget, QPlainTextEdit > QWidget, QTextBrowser > QWidget,
QListView > QWidget, QListWidget > QWidget,
QTreeView > QWidget, QTreeWidget > QWidget,
QTableView > QWidget, QTableWidget > QWidget {{
    background: {view_bg}; color: {view_text};
}}
QListWidget#paletteHits, QListWidget#paletteHits > QWidget {{
    background: {card_bg}; color: {TEXT};
}}
QScrollArea#historyStrip, QScrollArea#expertScroll {{
    background: {card_bg}; border: none;
}}
QScrollArea#historyStrip > QWidget > QWidget,
QScrollArea#expertScroll > QWidget > QWidget,
QWidget#historyRow, QWidget#expertForm {{
    background: {card_bg}; color: {TEXT};
}}
QAbstractScrollArea QScrollBar:vertical {{
    background: {card_bg}; width: 8px; margin: 0; border: none;
}}
QAbstractScrollArea QScrollBar::groove:vertical {{
    background: {card_bg}; border: none; width: 8px;
}}
QAbstractScrollArea QScrollBar::handle:vertical {{
    background: {CONTROL_BORDER}; min-height: 24px; border: none; border-radius: 4px;
}}
QAbstractScrollArea QScrollBar::handle:vertical:hover {{
    background: {TEXT_FAINT};
}}
QAbstractScrollArea QScrollBar::add-line:vertical,
QAbstractScrollArea QScrollBar::sub-line:vertical {{
    height: 0; width: 0; background: {card_bg}; border: none;
}}
QAbstractScrollArea QScrollBar::up-arrow:vertical,
QAbstractScrollArea QScrollBar::down-arrow:vertical {{
    width: 0; height: 0; background: {card_bg}; border: none;
}}
QAbstractScrollArea QScrollBar::add-page:vertical,
QAbstractScrollArea QScrollBar::sub-page:vertical {{
    background: {card_bg}; border: none;
}}
QAbstractScrollArea QScrollBar:horizontal {{
    background: {card_bg}; height: 8px; margin: 0; border: none;
}}
QAbstractScrollArea QScrollBar::groove:horizontal {{
    background: {card_bg}; border: none; height: 8px;
}}
QAbstractScrollArea QScrollBar::handle:horizontal {{
    background: {CONTROL_BORDER}; min-width: 24px; border: none; border-radius: 4px;
}}
QAbstractScrollArea QScrollBar::handle:horizontal:hover {{
    background: {TEXT_FAINT};
}}
QAbstractScrollArea QScrollBar::add-line:horizontal,
QAbstractScrollArea QScrollBar::sub-line:horizontal {{
    height: 0; width: 0; background: {card_bg}; border: none;
}}
QAbstractScrollArea QScrollBar::left-arrow:horizontal,
QAbstractScrollArea QScrollBar::right-arrow:horizontal {{
    width: 0; height: 0; background: {card_bg}; border: none;
}}
QAbstractScrollArea QScrollBar::add-page:horizontal,
QAbstractScrollArea QScrollBar::sub-page:horizontal {{
    background: {card_bg}; border: none;
}}
QScrollArea#expertScroll QScrollBar:horizontal {{
    height: 0; background: {card_bg}; border: none;
}}
QWidget#sectionBar {{ background: transparent; }}
QPushButton#sectionTab {{
    background: transparent; color: {TEXT_MUTED}; border: none; border-radius: 0;
    border-bottom: 2px solid transparent; padding: 4px 12px; min-height: 28px;
}}
QPushButton#sectionTab:checked {{
    color: {TEXT}; font-weight: 600; background: transparent;
    border-bottom: 2px solid {ACCENT};
}}
QPushButton#sectionMore, QPushButton#sectionAction, QPushButton#sectionPreview {{
    background: {CONTROL_BG}; color: {TEXT};
    border: {density.border_px}px solid {CONTROL_BORDER};
    border-radius: {radius}px; padding: 2px 12px; min-height: 28px;
}}
QPushButton#sectionMore::menu-indicator {{ image: none; width: 0; }}
QPushButton#expertReset {{
    background: transparent; color: {TEXT}; text-align: left;
    border: {density.border_px}px solid {CONTROL_BORDER}; padding: 4px 10px;
}}
QSlider#compareSlider {{ background: transparent; }}
QSlider#compareSlider::groove:horizontal {{
    height: 0px; background: transparent; border: none;
}}
QSlider#compareSlider::add-page:horizontal, QSlider#compareSlider::sub-page:horizontal {{
    background: transparent; border: none;
}}
QSlider#compareSlider::handle:horizontal {{
    width: 4px; margin: -220px 0; background: {TEXT}; border: none; border-radius: 1px;
}}
QWidget#videoResult {{ background: transparent; }}
QLabel#videoPoster {{
    background: {CONTROL_BG}; color: {TEXT};
    border: {density.border_px}px solid {CONTROL_BORDER}; border-radius: 8px;
}}
QLabel#videoPath {{ color: {TEXT}; background: transparent; }}
QPushButton#videoPlay, QPushButton#videoOpen {{ padding: 4px 14px; min-height: 34px; }}
QSlider#videoSeek::groove:horizontal {{
    height: 6px; background: {CONTROL_BG}; border-radius: 3px;
}}
QSlider#videoSeek::handle:horizontal {{
    background: {ACCENT}; width: 16px; margin: -6px 0; border-radius: 8px;
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
