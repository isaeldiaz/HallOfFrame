"""Visual system (REDESIGN-PLAN §7).

One app-wide Qt stylesheet, applied on the QApplication in main.py (not
per-widget setStyleSheet calls). Dark, high-contrast console: IBM Plex Sans for
labels/prose, IBM Plex Mono for every number — always tabular figures.

Ship both fonts with the app: a ``fonts/`` directory next to the package.
``load_fonts()`` registers them with QFontDatabase and returns True on success;
it is never fatal (falls back to system fonts).
"""
from __future__ import annotations

from pathlib import Path

# --- tokens ---------------------------------------------------------------
BG = "#0d1114"
PANEL = "#11161a"
PANEL_BORDER = "#1c2429"
DIVIDER = "#232b31"
TEXT_PRIMARY = "#f2f6f8"
TEXT_SECONDARY = "#c6d3da"
TEXT_DIM = "#8fa0ab"
TEXT_FAINT = "#5f6f79"
LETTERBOX = "#1b262c"
FINISH_LINE = "rgba(255,90,66,.9)"

GREEN = "#38b26a"
GREEN_TEXT = "#4ecb80"
AMBER = "#f2a01e"
AMBER_TEXT = "#ffb43a"
RED = "#e8402a"
RED_TEXT = "#ff5a42"
BLUE = "#4a90d9"
BLUE_TEXT = "#6fb2e8"

COLORS = {
    "bg": BG, "panel": PANEL, "panel_border": PANEL_BORDER,
    "divider": DIVIDER, "text_primary": TEXT_PRIMARY,
    "text_secondary": TEXT_SECONDARY, "text_dim": TEXT_DIM,
    "text_faint": TEXT_FAINT, "letterbox": LETTERBOX,
    "finish_line": FINISH_LINE,
    "green": GREEN, "green_text": GREEN_TEXT, "amber": AMBER,
    "amber_text": AMBER_TEXT, "red": RED, "red_text": RED_TEXT,
    "blue": BLUE, "blue_text": BLUE_TEXT,
}

# Per-state band theme: accent dot, state text, band background (§1).
STATE_THEME = {
    "stream_down": {"accent": RED, "text": RED_TEXT, "bg": "#1b0f0d"},
    "recalibrate": {"accent": AMBER, "text": AMBER_TEXT, "bg": "#1e1608"},
    "ready": {"accent": GREEN, "text": GREEN_TEXT, "bg": "#0f1a14"},
    "armed": {"accent": AMBER, "text": AMBER_TEXT, "bg": "#1e1608"},
    "recording": {"accent": RED, "text": RED_TEXT, "bg": "#1b0f0d"},
    "race_over": {"accent": BLUE, "text": BLUE_TEXT, "bg": "#0d1620"},
    "review": {"accent": BLUE, "text": BLUE_TEXT, "bg": "#0d1620"},
}

FONT_SANS = "IBM Plex Sans"
FONT_MONO = "IBM Plex Mono"


def load_fonts(fonts_dir: Path | None = None) -> bool:
    """Register bundled IBM Plex fonts with QFontDatabase.

    *fonts_dir* defaults to ``<package>/fonts``. Returns True if at least one
    font was loaded. Never raises.
    """
    try:
        from PySide6.QtGui import QFontDatabase
    except Exception:
        return False
    if fonts_dir is None:
        fonts_dir = Path(__file__).resolve().parent / "fonts"
    if not fonts_dir.is_dir():
        return False
    db = QFontDatabase()
    loaded = 0
    for path in sorted(fonts_dir.iterdir()):
        if path.suffix.lower() not in (".ttf", ".otf"):
            continue
        try:
            if db.addApplicationFont(str(path)) != -1:
                loaded += 1
        except Exception:
            continue
    return loaded > 0


# --- stylesheet -----------------------------------------------------------
STYLESHEET = f"""
* {{
    font-family: '{FONT_SANS}';
    color: {TEXT_PRIMARY};
}}
QMainWindow, QWidget#Root {{
    background-color: {BG};
}}
QLabel {{
    background: transparent;
}}
QLabel#StateName, QLabel[mono="true"] {{
    font-family: '{FONT_MONO}';
}}

/* --- buttons: click-only, never keyboard-focusable (§4) --- */
QPushButton {{
    background-color: {PANEL};
    border: 1px solid {PANEL_BORDER};
    border-radius: 4px;
    padding: 10px 18px;
    font-size: 18px;
    color: {TEXT_SECONDARY};
    min-height: 28px;
}}
QPushButton:hover {{
    border-color: {TEXT_DIM};
    color: {TEXT_PRIMARY};
}}
QPushButton:disabled {{
    color: {TEXT_FAINT};
    border-color: {DIVIDER};
}}
QPushButton:focus {{
    outline: none;
    border-color: {BLUE};
}}

/* --- text inputs --- */
QLineEdit {{
    background-color: #0b0f12;
    border: 1px solid {PANEL_BORDER};
    border-radius: 2px;
    color: {TEXT_PRIMARY};
    font-family: '{FONT_MONO}';
    font-size: 20px;
    padding: 6px 10px;
    selection-background-color: {BLUE};
}}
QLineEdit:focus {{
    border-color: {BLUE};
}}

/* --- lists --- */
QListWidget {{
    background-color: {BG};
    border: none;
    outline: none;
}}
QListWidget::item {{
    border-bottom: 1px solid #171e23;
}}
QListWidget::item:selected {{
    background: {PANEL};
    border-left: 3px solid {BLUE};
}}

/* --- combo / dropdown --- */
QComboBox {{
    background-color: {PANEL};
    border: 1px solid {PANEL_BORDER};
    border-radius: 4px;
    padding: 8px 14px;
    font-size: 18px;
    color: {TEXT_PRIMARY};
}}
QComboBox:focus {{
    border-color: {BLUE};
}}
QComboBox QAbstractItemView {{
    background-color: {PANEL};
    border: 1px solid {PANEL_BORDER};
    selection-background-color: {BLUE};
    color: {TEXT_PRIMARY};
}}

QScrollArea {{
    background: {PANEL};
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: {PANEL};
}}

QScrollBar:vertical {{
    background: {BG};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {DIVIDER};
    border-radius: 6px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def key_cap_style(hot: bool = False) -> str:
    """Key-cap chip in the key bar; ``hot`` highlights the active action green."""
    border = "#2f6b45" if hot else "#2c3942"
    keycolor = GREEN_TEXT if hot else TEXT_SECONDARY
    labelcolor = TEXT_PRIMARY if hot else TEXT_DIM
    bg = "#151d16" if hot else "#151a1e"
    return (
        f"QWidget {{ background:{bg}; border:1px solid {border};"
        f" border-radius:4px; }}"
        f" QLabel[role='key'] {{ font-family:'{FONT_MONO}'; font-weight:600;"
        f" color:{keycolor}; padding:3px 9px; border:1px solid {border};"
        f" border-radius:3px; }}"
        f" QLabel[role='label'] {{ color:{labelcolor}; font-size:18px; }}"
    )
