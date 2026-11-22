"""Dark theme and colour palette."""

BG = "#14161b"
SURFACE = "#1b1e25"
SURFACE_2 = "#232731"
SURFACE_3 = "#2c313d"
BORDER = "#323846"
TEXT = "#e6e9ef"
TEXT_DIM = "#9aa3b2"
TEXT_FAINT = "#6b7383"
ACCENT = "#5b8cff"
ACCENT_HOVER = "#7aa2ff"
RED = "#ff5c6c"
GREEN = "#3ddc97"
YELLOW = "#ffc857"
PURPLE = "#b48cff"
CYAN = "#4fd1e8"
ORANGE = "#ff9f5a"

TRACK_COLORS = {
    "move": ACCENT,
    "click": ORANGE,
    "scroll": CYAN,
    "key": PURPLE,
    "check": GREEN,
}

STYLESHEET = f"""
* {{
    font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif;
    font-size: 10pt;
    color: {TEXT};
}}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#Central {{ background: {BG}; }}
QToolTip {{
    background: {SURFACE_3}; color: {TEXT}; border: 1px solid {BORDER};
    padding: 4px 8px; border-radius: 6px;
}}
QFrame#Card {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px;
}}
QLabel#CardTitle {{ color: {TEXT_DIM}; font-size: 8.5pt; font-weight: 600; letter-spacing: 1px; }}
QLabel#Dim {{ color: {TEXT_DIM}; }}
QLabel#Faint {{ color: {TEXT_FAINT}; font-size: 9pt; }}
QLabel#BigTime {{ font-size: 20pt; font-weight: 600; font-family: "Cascadia Mono", "Consolas", monospace; }}
QLabel#Status {{ color: {TEXT_DIM}; }}

QPushButton, QToolButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 12px; color: {TEXT};
}}
QPushButton:hover, QToolButton:hover {{ background: {SURFACE_3}; border-color: #3d4456; }}
QPushButton:pressed, QToolButton:pressed {{ background: #20242d; }}
QPushButton:disabled, QToolButton:disabled {{ color: {TEXT_FAINT}; background: #1a1d23; border-color: #262a33; }}
QToolButton:checked {{ background: #263354; border-color: {ACCENT}; color: {ACCENT_HOVER}; }}
QToolButton::menu-indicator {{ image: none; }}

QPushButton#Transport, QToolButton#Transport {{
    min-width: 46px; min-height: 46px; max-height: 46px; border-radius: 23px; padding: 0;
    background: {SURFACE_2};
}}
QToolButton#Transport:hover {{ background: {SURFACE_3}; }}
QToolButton#Transport[role="rec"]:checked {{ background: #4a1f27; border-color: {RED}; }}
QToolButton#Transport[role="play"]:checked {{ background: #173d31; border-color: {GREEN}; }}

QPushButton#Primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}

QToolButton#EditBtn {{ padding: 4px 9px; border-radius: 7px; }}

QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 7px;
    padding: 4px 8px; min-height: 22px; selection-background-color: {ACCENT};
}}
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {TEXT_FAINT}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 16px; border: none; background: transparent;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("@up@"); width: 12px; height: 12px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("@down@"); width: 12px; height: 12px; }}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{ image: url("@up_off@"); }}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{ image: url("@down_off@"); }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {SURFACE_3}; border-radius: 4px; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: url("@down@"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT};
    outline: none;
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 5px; border: 1px solid #4a5163; background: {SURFACE_2};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("@check@"); }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}

QSlider::groove:horizontal {{ height: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}

QProgressBar {{
    background: {SURFACE_2}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QTableView {{
    background: {SURFACE}; alternate-background-color: #1e222a; border: none;
    gridline-color: transparent; selection-background-color: #2a3b66; selection-color: {TEXT};
}}
QHeaderView::section {{
    background: {SURFACE}; color: {TEXT_DIM}; border: none; border-bottom: 1px solid {BORDER};
    padding: 5px 8px; font-weight: 600; font-size: 9pt;
}}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

QScrollBar:horizontal {{ height: 10px; background: transparent; margin: 0; }}
QScrollBar:vertical {{ width: 10px; background: transparent; margin: 0; }}
QScrollBar::handle {{ background: #3a4152; border-radius: 5px; min-width: 30px; min-height: 30px; }}
QScrollBar::handle:hover {{ background: #4a5266; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:vertical {{ height: 8px; }}

QMenu {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px;
}}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::item:disabled {{ color: {TEXT_FAINT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; background: {SURFACE}; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM}; padding: 8px 16px; border: none;
    border-bottom: 2px solid transparent; margin-right: 2px;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QStatusBar QLabel {{ color: {TEXT_DIM}; padding: 0 6px; }}
"""


def build_stylesheet(assets: dict) -> str:
    css = STYLESHEET
    for k, v in assets.items():
        css = css.replace(f"@{k}@", v)
    return css
