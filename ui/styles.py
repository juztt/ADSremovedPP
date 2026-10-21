"""
ui.styles
=========

Modern dark theme expressed as Qt Style Sheets, plus the colour tokens and
font stacks used by the rest of the UI.

Everything here is intentionally centralised so a future light theme only
needs a second dictionary rather than a second stylesheet.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Colour tokens
# ---------------------------------------------------------------------------

C = {
    "bg":            "#12141a",
    "bg_alt":        "#171a21",
    "surface":       "#1c1f27",
    "surface_hi":    "#232733",
    "surface_hover": "#2a2f3d",
    "border":        "#2d3240",
    "border_hi":     "#3a4152",
    "text":          "#e6e9f0",
    "text_muted":    "#8b93a7",
    "text_dim":      "#6b7386",
    "accent":        "#4c8dff",
    "accent_hi":     "#6ba0ff",
    "accent_dark":   "#2f6fe0",
    "success":       "#3ecf8e",
    "warning":       "#f5c623",
    "danger":        "#ff5c5c",
    "danger_hi":     "#ff7a7a",
    "danger_dark":   "#d93f3f",
}

#: Font stacks. Leelawadee UI ships with Windows 10/11 and renders Thai well;
#: the fallbacks keep the layout sane on older systems.
FONT_UI = '"Leelawadee UI", "Tahoma", "Segoe UI", sans-serif'
FONT_MONO = '"Cascadia Mono", "Consolas", "JetBrains Mono", monospace'


def risk_color(risk: str) -> str:
    """Map a blacklist ``risk`` value to a display colour."""
    return {
        "Critical": C["danger"],
        "High": "#ff8a5c",
        "Medium": C["warning"],
        "Low": "#5cc8ff",
        "Safe": C["success"],
    }.get(risk.strip().title(), C["text_muted"])


# ---------------------------------------------------------------------------
# Main stylesheet
# ---------------------------------------------------------------------------

QSS = f"""
/* ---------- base ---------- */
QWidget {{
    background-color: {C['bg']};
    color: {C['text']};
    font-family: {FONT_UI};
    font-size: 14px;
}}

QMainWindow, QDialog {{
    background-color: {C['bg']};
}}

QToolTip {{
    background-color: {C['surface_hi']};
    color: {C['text']};
    border: 1px solid {C['border_hi']};
    padding: 6px 9px;
    border-radius: 6px;
}}

/* ---------- frames ---------- */
QFrame#HeaderBar {{
    background-color: {C['bg_alt']};
    border-bottom: 1px solid {C['border']};
}}

QFrame#Card {{
    background-color: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 12px;
}}

QFrame#CardAccent {{
    background-color: {C['surface']};
    border: 1px solid {C['border']};
    border-left: 3px solid {C['accent']};
    border-radius: 12px;
}}

QFrame#Divider {{
    background-color: {C['border']};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}

QFrame#LogFrame {{
    background-color: {C['bg_alt']};
    border-top: 1px solid {C['border']};
}}

/* ---------- labels ---------- */
QLabel#AppTitle {{
    font-size: 19px;
    font-weight: 600;
    color: {C['text']};
    background: transparent;
}}

QLabel#AppVersion {{
    font-size: 12px;
    color: {C['text_dim']};
    background: transparent;
}}

QLabel#CardTitle {{
    font-size: 15px;
    font-weight: 600;
    color: {C['text']};
    background: transparent;
}}

QLabel#FieldLabel {{
    font-size: 11px;
    color: {C['text_dim']};
    background: transparent;
    letter-spacing: 0.4px;
}}

QLabel#FieldValue {{
    font-size: 14px;
    font-weight: 600;
    color: {C['text']};
    background: transparent;
}}

QLabel#SectionHint {{
    font-size: 12.5px;
    color: {C['text_muted']};
    background: transparent;
}}

QLabel#StatValue {{
    font-size: 24px;
    font-weight: 700;
    color: {C['accent']};
    background: transparent;
}}

QLabel#StatusPill {{
    background-color: {C['surface_hi']};
    color: {C['text_muted']};
    border: 1px solid {C['border_hi']};
    border-radius: 11px;
    padding: 4px 14px;
    font-size: 12px;
    font-weight: 600;
}}

/* ---------- buttons ---------- */
QPushButton {{
    background-color: {C['surface_hi']};
    color: {C['text']};
    border: 1px solid {C['border_hi']};
    border-radius: 8px;
    padding: 9px 16px;
    font-size: 13.5px;
    font-weight: 500;
    min-height: 20px;
}}
QPushButton:hover  {{ background-color: {C['surface_hover']}; border-color: {C['accent']}; }}
QPushButton:pressed{{ background-color: {C['border_hi']}; }}
QPushButton:disabled {{
    background-color: {C['surface']};
    color: {C['text_dim']};
    border-color: {C['border']};
}}

QPushButton#Primary {{
    background-color: {C['accent']};
    color: #ffffff;
    border: 1px solid {C['accent']};
    font-weight: 600;
}}
QPushButton#Primary:hover   {{ background-color: {C['accent_hi']}; border-color: {C['accent_hi']}; }}
QPushButton#Primary:pressed {{ background-color: {C['accent_dark']}; }}
QPushButton#Primary:disabled{{
    background-color: {C['surface']};
    color: {C['text_dim']};
    border-color: {C['border']};
}}

QPushButton#Danger {{
    background-color: {C['danger_dark']};
    color: #ffffff;
    border: 1px solid {C['danger_dark']};
    font-weight: 600;
}}
QPushButton#Danger:hover   {{ background-color: {C['danger']}; border-color: {C['danger']}; }}
QPushButton#Danger:pressed {{ background-color: #b93333; }}
QPushButton#Danger:disabled{{
    background-color: {C['surface']};
    color: {C['text_dim']};
    border-color: {C['border']};
}}

QPushButton#Ghost {{
    background-color: transparent;
    border: 1px solid {C['border']};
    color: {C['text_muted']};
    padding: 6px 12px;
    font-size: 12.5px;
}}
QPushButton#Ghost:hover {{
    color: {C['text']};
    border-color: {C['accent']};
    background-color: {C['surface']};
}}

QPushButton#UtilityTile {{
    background-color: {C['surface_hi']};
    border: 1px solid {C['border_hi']};
    border-radius: 10px;
    padding: 12px 14px;
    text-align: left;
    font-size: 13.5px;
    font-weight: 500;
}}
QPushButton#UtilityTile:hover {{
    background-color: {C['surface_hover']};
    border-color: {C['accent']};
}}
QPushButton#UtilityTile:disabled {{
    color: {C['text_dim']};
    background-color: {C['surface']};
}}

/* ---------- inputs ---------- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    background-color: {C['bg_alt']};
    border: 1px solid {C['border_hi']};
    border-radius: 8px;
    padding: 8px 12px;
    color: {C['text']};
    selection-background-color: {C['accent']};
    selection-color: #ffffff;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border-color: {C['accent']};
}}
QLineEdit::placeholder {{
    color: {C['text_dim']};
}}

QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {C['text_muted']};
    margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    background-color: {C['surface_hi']};
    border: 1px solid {C['border_hi']};
    border-radius: 8px;
    selection-background-color: {C['accent']};
    selection-color: #ffffff;
    padding: 4px;
}}

/* ---------- checkboxes ---------- */
QCheckBox {{
    background: transparent;
    spacing: 9px;
    color: {C['text']};
    font-size: 13.5px;
}}
QCheckBox::indicator, QTableWidget::indicator {{
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1px solid {C['border_hi']};
    background-color: {C['bg_alt']};
}}
QCheckBox::indicator:hover, QTableWidget::indicator:hover {{
    border-color: {C['accent']};
}}
QCheckBox::indicator:checked, QTableWidget::indicator:checked {{
    background-color: {C['accent']};
    border-color: {C['accent']};
    image: none;
}}
QCheckBox:disabled {{
    color: {C['text_dim']};
}}

/* ---------- tabs ---------- */
QTabWidget::pane {{
    background-color: transparent;
    border: none;
    top: -1px;
}}
QTabBar {{
    qproperty-drawBase: 0;
}}
QTabBar::tab {{
    background: transparent;
    color: {C['text_muted']};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 11px 20px;
    margin-right: 4px;
    font-size: 14px;
    font-weight: 500;
}}
QTabBar::tab:hover {{
    color: {C['text']};
}}
QTabBar::tab:selected {{
    color: {C['accent']};
    border-bottom: 2px solid {C['accent']};
    font-weight: 600;
}}

/* ---------- tables ---------- */
QTableWidget {{
    background-color: {C['surface']};
    alternate-background-color: {C['bg_alt']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    gridline-color: {C['border']};
    font-size: 13px;
    selection-background-color: {C['accent_dark']};
    selection-color: #ffffff;
}}
QTableWidget::item {{
    padding: 7px 8px;
    border: none;
}}
QTableWidget::item:selected {{
    background-color: {C['accent_dark']};
    color: #ffffff;
}}

QHeaderView {{
    background-color: {C['bg_alt']};
    border: none;
}}
QHeaderView::section {{
    background-color: {C['bg_alt']};
    color: {C['text_dim']};
    border: none;
    border-bottom: 1px solid {C['border']};
    padding: 9px 8px;
    font-size: 11.5px;
    font-weight: 600;
    letter-spacing: 0.4px;
}}
QHeaderView::section:hover {{
    color: {C['text']};
}}
QTableCornerButton::section {{
    background-color: {C['bg_alt']};
    border: none;
}}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {C['border_hi']};
    border-radius: 5px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: {C['accent']};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {C['border_hi']};
    border-radius: 5px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {C['accent']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0px;
    width: 0px;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ---------- log ---------- */
QPlainTextEdit#LogView {{
    background-color: {C['bg']};
    border: none;
    font-family: {FONT_MONO};
    font-size: 12px;
    color: {C['text_muted']};
    padding: 8px 12px;
}}

/* ---------- progress ---------- */
QProgressBar {{
    background-color: {C['bg_alt']};
    border: 1px solid {C['border']};
    border-radius: 6px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {C['accent']};
    border-radius: 5px;
}}

/* ---------- message box ---------- */
QMessageBox {{
    background-color: {C['surface']};
    border: 1px solid {C['border_hi']};
    border-radius: 12px;
}}
QMessageBox QLabel {{
    color: {C['text']};
    background: transparent;
    font-size: 14px;
}}
QMessageBox QPushButton {{
    min-width: 84px;
    padding: 8px 14px;
}}

/* ---------- menu ---------- */
QMenuBar {{
    background-color: {C['bg_alt']};
    border-bottom: 1px solid {C['border']};
}}
QMenuBar::item {{
    background: transparent;
    padding: 7px 12px;
    color: {C['text_muted']};
}}
QMenuBar::item:selected {{
    background-color: {C['surface_hi']};
    color: {C['text']};
}}
QMenu {{
    background-color: {C['surface_hi']};
    border: 1px solid {C['border_hi']};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{
    padding: 7px 26px 7px 14px;
    border-radius: 6px;
    color: {C['text']};
}}
QMenu::item:selected {{
    background-color: {C['accent']};
    color: #ffffff;
}}
QMenu::separator {{
    height: 1px;
    background: {C['border']};
    margin: 5px 8px;
}}
"""


def apply_theme(app) -> None:
    """Install the palette and stylesheet on a ``QApplication``."""
    from PySide6.QtGui import QColor, QPalette

    app.setStyle("Fusion")

    palette = QPalette()
    window = QColor(C["bg"])
    base = QColor(C["surface"])
    text = QColor(C["text"])
    disabled_text = QColor(C["text_dim"])
    highlight = QColor(C["accent"])

    palette.setColor(QPalette.Window, window)
    palette.setColor(QPalette.WindowText, text)
    palette.setColor(QPalette.Base, base)
    palette.setColor(QPalette.AlternateBase, QColor(C["bg_alt"]))
    palette.setColor(QPalette.Text, text)
    palette.setColor(QPalette.Button, QColor(C["surface_hi"]))
    palette.setColor(QPalette.ButtonText, text)
    palette.setColor(QPalette.Highlight, highlight)
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ToolTipBase, QColor(C["surface_hi"]))
    palette.setColor(QPalette.ToolTipText, text)
    palette.setColor(QPalette.PlaceholderText, disabled_text)
    palette.setColor(QPalette.Link, highlight)
    palette.setColor(QPalette.Disabled, QPalette.Text, disabled_text)
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, disabled_text)
    palette.setColor(QPalette.Disabled, QPalette.WindowText, disabled_text)
    app.setPalette(palette)

    app.setStyleSheet(QSS)