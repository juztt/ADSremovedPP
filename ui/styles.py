"""
ui.styles
=========

"Clean Pro" design system for ADSremovedPP.

The look takes inspiration from macOS / iOS: generous whitespace, hairline
borders instead of heavy shadows, one confident accent colour, rounded corners
everywhere and motion that is quick but never bouncy.

Two palettes ship here — ``light`` and ``dark`` — and :func:`build_qss` turns
either one into a Qt stylesheet. :func:`apply_theme` installs the requested one
on a live :class:`QApplication`, so switching themes at runtime only means
calling it again with a different name.

Design tokens
-------------
* Radius scale:  7 (control) / 10 (chip) / 14 (card) / 18 (window)
* Motion:        150–250 ms, ``cubic-bezier(.4, 0, .2, 1)``
* Borders:       1 px hairlines at ~8 % opacity instead of drop shadows
* Accent:        a single blue; risk colours are reserved for risk meaning
"""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

LIGHT = {
    "name": "light",

    # surfaces
    "window": "#F5F5F7",
    "surface": "#FFFFFF",
    "surface_alt": "#FBFBFD",
    "surface_sunken": "#EFEFF2",
    "surface_hover": "#F0F0F3",
    "overlay_scrim": "rgba(255, 255, 255, 0.72)",

    # lines
    "border": "#E3E3E7",
    "border_strong": "#D2D2D8",
    "hairline": "#EBEBEF",

    # type
    "text": "#1D1D1F",
    "text_muted": "#6E6E73",
    "text_faint": "#8E8E93",
    "text_inverse": "#FFFFFF",

    # accent
    "accent": "#0071E3",
    "accent_hover": "#0077ED",
    "accent_press": "#0062C4",
    "accent_soft": "#E8F1FE",
    "accent_ring": "rgba(0, 113, 227, 0.22)",

    # semantic
    "success": "#34C759",
    "success_soft": "#E6F8EB",
    "warning": "#FF9F0A",
    "warning_ink": "#A86700",
    "warning_soft": "#FFF3E0",
    "danger": "#FF3B30",
    "danger_hover": "#E5342B",
    "danger_soft": "#FFEBEA",
    "info": "#5AC8FA",
    "info_ink": "#0A72B4",
    "neutral": "#8E8E93",
    "neutral_soft": "#EFEFF2",

    # risk scale (used by pills and row accent bars)
    "risk_critical": "#FF3B30",
    "risk_high": "#FF9500",
    "risk_medium": "#FFCC00",
    "risk_low": "#5AC8FA",
    "risk_safe": "#34C759",

    # code / log
    "code_bg": "#F4F4F7",
    "code_border": "#E6E6EB",
    "code_text": "#3A3A3C",
}

DARK = {
    "name": "dark",

    "window": "#1C1C1E",
    "surface": "#2C2C2E",
    "surface_alt": "#262628",
    "surface_sunken": "#141416",
    "surface_hover": "#333336",
    "overlay_scrim": "rgba(20, 20, 22, 0.72)",

    "border": "#3A3A3D",
    "border_strong": "#48484A",
    "hairline": "#333336",

    "text": "#F5F5F7",
    "text_muted": "#A1A1A6",
    "text_faint": "#7C7C80",
    "text_inverse": "#1C1C1E",

    "accent": "#0A84FF",
    "accent_hover": "#3D9BFF",
    "accent_press": "#0071E3",
    "accent_soft": "#123253",
    "accent_ring": "rgba(10, 132, 255, 0.28)",

    "success": "#30D158",
    "success_soft": "#12351C",
    "warning": "#FF9F0A",
    "warning_ink": "#FFB340",
    "warning_soft": "#3A2A0C",
    "danger": "#FF453A",
    "danger_hover": "#FF6B61",
    "danger_soft": "#3D1512",
    "info": "#64D2FF",
    "info_ink": "#64D2FF",
    "neutral": "#8E8E93",
    "neutral_soft": "#2F2F31",

    "risk_critical": "#FF453A",
    "risk_high": "#FF9F0A",
    "risk_medium": "#FFD426",
    "risk_low": "#64D2FF",
    "risk_safe": "#30D158",

    "code_bg": "#232326",
    "code_border": "#353538",
    "code_text": "#C7C7CC",
}

PALETTES = {"light": LIGHT, "dark": DARK}

# ---------------------------------------------------------------------------
# Metrics — one place so spacing stays consistent
# ---------------------------------------------------------------------------

RADIUS_CONTROL = 7
RADIUS_CHIP = 10
RADIUS_CARD = 14
RADIUS_WINDOW = 18

FONT_UI = '"Segoe UI Variable Text", "Segoe UI", "Leelawadee UI", Tahoma, sans-serif'
FONT_DISPLAY = '"Segoe UI Variable Display", "Segoe UI", "Leelawadee UI", Tahoma, sans-serif'
FONT_MONO = '"Cascadia Mono", "JetBrains Mono", Consolas, "Courier New", monospace'

#: Current palette name; :func:`apply_theme` keeps this in sync.
_active = "dark"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def palette(name: str | None = None) -> dict:
    """Return a palette dictionary by name, defaulting to the active one."""
    return PALETTES.get(name or _active, DARK)


def current() -> dict:
    """Return the palette currently applied to the application."""
    return PALETTES[_active]


def rgba(hex_color: str, alpha: float) -> str:
    """Turn ``#RRGGBB`` into an ``rgba(...)`` string usable inside Qt stylesheets."""
    color = QColor(hex_color)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha:.3f})"


def risk_color(risk: str) -> str:
    """Map a blacklist ``risk`` value to its palette colour."""
    tokens = current()
    key = str(risk or "").strip().title()
    return {
        "Critical": tokens["risk_critical"],
        "High": tokens["risk_high"],
        "Medium": tokens["risk_medium"],
        "Low": tokens["risk_low"],
        "Safe": tokens["risk_safe"],
    }.get(key, tokens["text_muted"])


def risk_soft(risk: str) -> str:
    """Return the translucent background used behind a risk pill."""
    tokens = current()
    key = str(risk or "").strip().title()
    return {
        "Critical": tokens["danger_soft"],
        "High": tokens["warning_soft"],
        "Medium": tokens["warning_soft"],
        "Low": tokens["accent_soft"],
        "Safe": tokens["success_soft"],
    }.get(key, tokens["neutral_soft"])


def risk_ink(risk: str) -> str:
    """Foreground colour that stays readable on top of :func:`risk_soft`."""
    tokens = current()
    key = str(risk or "").strip().title()
    return {
        "Critical": tokens["danger"],
        "High": tokens["warning_ink"],
        "Medium": tokens["warning_ink"],
        "Low": tokens["info_ink"],
        "Safe": tokens["success"],
    }.get(key, tokens["text_muted"])


def risk_label(risk: str) -> str:
    """Thai label shown inside a risk pill."""
    return {
        "Critical": "วิกฤต",
        "High": "สูงมาก",
        "Medium": "ปานกลาง",
        "Low": "ต่ำ",
        "Safe": "ปลอดภัย",
    }.get(str(risk or "").strip().title(), "ไม่ระบุ")


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------


def build_qss(name: str | None = None) -> str:
    """Render a full Qt stylesheet for the requested palette."""
    t = palette(name)

    return f"""
/* ============================================================
   ADSremovedPP — Clean Pro · {t['name']}
   ============================================================ */

QWidget {{
    background-color: transparent;
    color: {t['text']};
    font-family: {FONT_UI};
    font-size: 13px;
    outline: none;
}}

QMainWindow, QDialog {{
    background-color: {t['window']};
}}

QToolTip {{
    background-color: {t['surface']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 6px;
    padding: 6px 9px;
    font-size: 12px;
}}


/* ---------- cards ---------- */

QFrame#Card {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: {RADIUS_CARD}px;
}}

QFrame#CardAlt {{
    background-color: {t['surface_alt']};
    border: 1px solid {t['hairline']};
    border-radius: {RADIUS_CARD}px;
}}

QFrame#Sunken {{
    background-color: {t['surface_sunken']};
    border: 1px solid {t['hairline']};
    border-radius: {RADIUS_CHIP}px;
}}

QFrame#Hairline {{
    background-color: {t['hairline']};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}

QFrame#VLine {{
    background-color: {t['hairline']};
    border: none;
    max-width: 1px;
    min-width: 1px;
}}


/* ---------- sidebar ---------- */

QFrame#Sidebar {{
    background-color: {t['surface_alt']};
    border: none;
    border-right: 1px solid {t['hairline']};
}}

QLabel#SidebarTitle {{
    color: {t['text']};
    font-family: {FONT_DISPLAY};
    font-size: 15px;
    font-weight: 700;
}}

QLabel#SidebarVersion {{
    color: {t['text_faint']};
    font-size: 11px;
}}

QPushButton#NavItem {{
    background-color: transparent;
    border: none;
    border-radius: 8px;
    color: {t['text_muted']};
    font-family: {FONT_UI};
    font-size: 13.5px;
    font-weight: 500;
    padding: 8px 10px;
    text-align: left;
}}

QPushButton#NavItem:hover {{
    background-color: {t['surface_hover']};
    color: {t['text']};
}}

QPushButton#NavItem:checked {{
    background-color: {t['accent']};
    color: {t['text_inverse']};
    font-weight: 600;
}}

QFrame#SidebarFooter {{
    background-color: transparent;
    border: none;
    border-top: 1px solid {t['hairline']};
}}


/* ---------- titles ---------- */

QLabel#PageTitle {{
    color: {t['text']};
    font-family: {FONT_DISPLAY};
    font-size: 22px;
    font-weight: 700;
}}

QLabel#PageSubtitle {{
    color: {t['text_muted']};
    font-size: 13px;
}}

QLabel#SectionTitle {{
    color: {t['text']};
    font-family: {FONT_DISPLAY};
    font-size: 14px;
    font-weight: 700;
}}

QLabel#SectionCaption {{
    color: {t['text_faint']};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
}}

QLabel#FieldLabel {{
    color: {t['text_faint']};
    font-size: 11px;
    font-weight: 500;
}}

QLabel#FieldValue {{
    color: {t['text']};
    font-size: 13.5px;
    font-weight: 600;
}}

QLabel#FieldValueMono {{
    color: {t['text']};
    font-family: {FONT_MONO};
    font-size: 11.5px;
    font-weight: 600;
}}

QLabel#Muted {{
    color: {t['text_muted']};
    font-size: 12px;
}}

QLabel#Faint {{
    color: {t['text_faint']};
    font-size: 11.5px;
}}

QLabel#Mono {{
    color: {t['text_muted']};
    font-family: {FONT_MONO};
    font-size: 11px;
}}

QLabel#Danger {{
    color: {t['danger']};
    font-size: 12.5px;
    font-weight: 600;
}}

QLabel#Success {{
    color: {t['success']};
    font-size: 12.5px;
    font-weight: 600;
}}

QLabel#Warning {{
    color: {t['warning_ink']};
    font-size: 12.5px;
    font-weight: 600;
}}


/* ---------- buttons ---------- */

QPushButton {{
    background-color: {t['surface_alt']};
    border: 1px solid {t['border']};
    border-radius: {RADIUS_CONTROL}px;
    color: {t['text']};
    font-family: {FONT_UI};
    font-size: 13px;
    font-weight: 600;
    padding: 8px 15px;
    min-height: 18px;
}}

/* Kit-painted controls draw every pixel themselves. Clearing the Qt rules
   here is what lets the QPainter radius, gradient and shadow actually show —
   without this, a stylesheet border overrides the painted shape and the
   button renders as a hard square.

   No min-height / max-height here on purpose: Qt gives stylesheet sizing rules
   priority over setFixedSize, so declaring them collapses painted buttons to
   zero height. */
QAbstractButton {{
    background: transparent;
    border: none;
    border-radius: 0px;
    padding: 0px;
    margin: 0px;
}}

QPushButton:hover {{
    background-color: {t['surface']};
    border-color: {t['border_strong']};
}}

QPushButton:pressed {{
    background-color: {t['surface_sunken']};
    padding-top: 9px;
    padding-bottom: 7px;
}}

QPushButton:disabled {{
    color: {t['text_faint']};
    background-color: {t['surface_alt']};
    border-color: {t['hairline']};
}}

QPushButton#Primary {{
    background-color: {t['accent']};
    border: 1px solid {t['accent']};
    color: {t['text_inverse']};
    font-weight: 600;
}}

QPushButton#Primary:hover {{
    background-color: {t['accent_hover']};
    border-color: {t['accent_hover']};
}}

QPushButton#Primary:pressed {{
    background-color: {t['accent_press']};
}}

QPushButton#Primary:disabled {{
    background-color: {t['border']};
    border-color: {t['border']};
    color: {t['text_faint']};
}}

QPushButton#Danger {{
    background-color: {t['danger']};
    border: 1px solid {t['danger']};
    color: #FFFFFF;
}}

QPushButton#Danger:hover {{
    background-color: {t['danger_hover']};
    border-color: {t['danger_hover']};
}}

QPushButton#Danger:disabled {{
    background-color: {t['border']};
    border-color: {t['border']};
    color: {t['text_faint']};
}}

QPushButton#Ghost {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {t['text_muted']};
    padding: 6px 10px;
}}

QPushButton#Ghost:hover {{
    background-color: {t['surface_hover']};
    color: {t['text']};
}}

QPushButton#Ghost:checked {{
    background-color: {t['accent_soft']};
    color: {t['accent']};
}}

QPushButton#IconButton {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: {RADIUS_CONTROL}px;
    padding: 4px;
    min-width: 28px;
    max-width: 28px;
    min-height: 28px;
    max-height: 28px;
}}

QPushButton#IconButton:hover {{
    background-color: {t['danger_soft']};
    border-color: {t['danger']};
    color: {t['danger']};
}}

QPushButton#IconButtonWarn:hover {{
    background-color: {t['warning_soft']};
    border-color: {t['warning']};
    color: {t['warning_ink']};
}}

QPushButton#Small {{
    font-size: 12px;
    padding: 5px 11px;
}}

QPushButton#Tile {{
    background-color: {t['surface_alt']};
    border: 1px solid {t['border']};
    border-radius: {RADIUS_CARD}px;
    font-size: 13px;
    font-weight: 600;
    padding: 14px 12px;
    text-align: left;
}}

QPushButton#Tile:hover {{
    background-color: {t['surface']};
    border-color: {t['accent']};
    color: {t['accent']};
}}

QPushButton#Tile:pressed {{
    background-color: {t['accent_soft']};
}}


/* ---------- segmented control ---------- */

QWidget#Segment {{
    background-color: transparent;
    border: none;
}}

QPushButton#SegmentButton {{
    background-color: transparent;
    border: none;
    color: transparent;
    padding: 0px;
}}


/* ---------- inputs ---------- */

QLineEdit {{
    background-color: {t['surface_alt']};
    border: 1px solid {t['border']};
    border-radius: {RADIUS_CONTROL}px;
    color: {t['text']};
    font-family: {FONT_UI};
    font-size: 13px;
    padding: 7px 11px;
    selection-background-color: {t['accent']};
    selection-color: {t['text_inverse']};
}}

QLineEdit:hover {{
    border-color: {t['border_strong']};
}}

QLineEdit:focus {{
    background-color: {t['surface']};
    border-color: {t['accent']};
}}

QLineEdit:disabled {{
    color: {t['text_faint']};
    background-color: {t['surface_sunken']};
}}

QTextEdit, QPlainTextEdit {{
    background-color: {t['surface_alt']};
    border: 1px solid {t['border']};
    border-radius: {RADIUS_CONTROL}px;
    color: {t['text']};
    font-family: {FONT_UI};
    font-size: 13px;
    padding: 8px 11px;
    selection-background-color: {t['accent']};
    selection-color: {t['text_inverse']};
}}

QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {t['accent']};
    background-color: {t['surface']};
}}

QComboBox {{
    background-color: {t['surface_alt']};
    border: 1px solid {t['border']};
    border-radius: {RADIUS_CONTROL}px;
    color: {t['text']};
    font-family: {FONT_UI};
    font-size: 13px;
    padding: 7px 11px;
    min-height: 18px;
}}

QComboBox:hover {{
    border-color: {t['border_strong']};
}}

QComboBox:focus {{
    border-color: {t['accent']};
    background-color: {t['surface']};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: {RADIUS_CONTROL}px;
    color: {t['text']};
    selection-background-color: {t['accent']};
    selection-color: {t['text_inverse']};
    padding: 4px;
    outline: none;
}}

QSpinBox, QDoubleSpinBox {{
    background-color: {t['surface_alt']};
    border: 1px solid {t['border']};
    border-radius: {RADIUS_CONTROL}px;
    color: {t['text']};
    font-family: {FONT_UI};
    font-size: 13px;
    padding: 7px 9px;
}}

QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {t['accent']};
}}

QCheckBox, QRadioButton {{
    color: {t['text']};
    font-family: {FONT_UI};
    font-size: 13px;
    spacing: 8px;
    background: transparent;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1.5px solid {t['border_strong']};
    background-color: {t['surface']};
}}

QCheckBox::indicator {{
    border-radius: 4.5px;
}}

QRadioButton::indicator {{
    border-radius: 8px;
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {t['accent']};
}}

QCheckBox::indicator:checked {{
    background-color: {t['accent']};
    border-color: {t['accent']};
    image: none;
}}

QRadioButton::indicator:checked {{
    background-color: {t['accent']};
    border: 4px solid {t['surface']};
    outline: 1.5px solid {t['accent']};
}}


/* ---------- tables ---------- */

QTableWidget, QTableView {{
    background-color: {t['surface']};
    alternate-background-color: {t['surface']};
    border: none;
    color: {t['text']};
    font-family: {FONT_UI};
    font-size: 13px;
    gridline-color: transparent;
    outline: none;
    selection-background-color: {t['accent_soft']};
    selection-color: {t['text']};
}}

QTableWidget::item, QTableView::item {{
    border: none;
    border-bottom: 1px solid {t['hairline']};
    padding: 0px;
}}

QTableWidget::item:selected, QTableView::item:selected {{
    background-color: {t['accent_soft']};
    color: {t['text']};
}}

QTableWidget::item:hover, QTableView::item:hover {{
    background-color: {t['surface_alt']};
}}

QHeaderView {{
    background-color: transparent;
}}

QHeaderView::section {{
    background-color: {t['surface_alt']};
    border: none;
    border-bottom: 1px solid {t['hairline']};
    color: {t['text_faint']};
    font-family: {FONT_UI};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
    padding: 8px 10px;
    text-align: left;
}}

QHeaderView::section:hover {{
    color: {t['text_muted']};
}}

QTableCornerButton::section {{
    background-color: {t['surface_alt']};
    border: none;
}}


/* ---------- scrollbars ---------- */

QScrollBar:vertical {{
    background-color: transparent;
    width: 11px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background-color: {t['border_strong']};
    border-radius: 5px;
    min-height: 32px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {t['text_faint']};
}}

QScrollBar:horizontal {{
    background-color: transparent;
    height: 11px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background-color: {t['border_strong']};
    border-radius: 5px;
    min-width: 32px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {t['text_faint']};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0px;
    width: 0px;
    border: none;
    background: none;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}


/* ---------- progress ---------- */

QProgressBar {{
    background-color: {t['surface_sunken']};
    border: none;
    border-radius: 3px;
    color: transparent;
    height: 5px;
    max-height: 5px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {t['accent']};
    border-radius: 3px;
}}

QProgressBar#Success::chunk {{
    background-color: {t['success']};
}}

QProgressBar#Warning::chunk {{
    background-color: {t['warning']};
}}

QProgressBar#Danger::chunk {{
    background-color: {t['danger']};
}}


/* ---------- scroll area ---------- */

QScrollArea {{
    background-color: transparent;
    border: none;
}}

QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}

QWidget#ScrollBody {{
    background-color: transparent;
}}


/* ---------- misc ---------- */

QCheckBox::indicator:disabled {{
    border-color: {t['hairline']};
    background-color: {t['surface_sunken']};
}}

QMenu {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 8px;
    padding: 5px;
}}

QMenu::item {{
    border-radius: 6px;
    color: {t['text']};
    font-size: 13px;
    padding: 6px 22px 6px 12px;
}}

QMenu::item:selected {{
    background-color: {t['accent']};
    color: {t['text_inverse']};
}}

QMenu::separator {{
    background-color: {t['hairline']};
    height: 1px;
    margin: 5px 8px;
}}

QSplitter::handle {{
    background-color: {t['hairline']};
}}

QSplitter::handle:horizontal {{
    width: 1px;
}}

QSplitter::handle:vertical {{
    height: 1px;
}}

QDialogButtonBox QPushButton {{
    min-width: 84px;
}}

QTabWidget::pane {{
    border: none;
    background: transparent;
}}

QTabBar::tab {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: {t['text_muted']};
    font-size: 13px;
    font-weight: 600;
    padding: 8px 14px;
}}

QTabBar::tab:selected {{
    border-bottom: 2px solid {t['accent']};
    color: {t['text']};
}}

QTabBar::tab:hover {{
    color: {t['text']};
}}
"""


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def apply_theme(app: QApplication | None = None, name: str | None = None) -> str:
    """Install the stylesheet for ``name`` and return the palette that was used.

    Passing ``None`` re-applies whichever palette is currently active, which is
    exactly what a toggle button wants after flipping :data:`_active`.
    """
    global _active

    target = QApplication.instance() if app is None else app
    if target is None:
        raise RuntimeError("apply_theme() ต้องเรียกหลังสร้าง QApplication แล้ว")

    resolved = name if name in PALETTES else (_active if name is None else _active)
    _active = resolved

    font = target.font()
    font.setFamily("Segoe UI")
    font.setPointSizeF(9.5)
    target.setFont(font)

    target.setStyleSheet(build_qss(resolved))
    return resolved


def toggle_theme(app: QApplication | None = None) -> str:
    """Flip between light and dark, returning the newly active palette name."""
    return apply_theme(app, "light" if _active == "dark" else "dark")


def active_palette_name() -> str:
    """Name of the palette currently installed (``"light"`` or ``"dark"``)."""
    return _active