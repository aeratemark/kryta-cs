"""The editor's look. CLASSIC is the default: the grey, bevelled Windows 98/2000
dialogs of the Morrowind Construction Set (Qt's own "Windows" style, Tahoma 8,
grey caption bars). DARK is the earlier dark Fusion look, kept as an option under
View > Theme.

Views read COLORS at paint time, so switching theme repaints them in place. The
render colours (marker, marker2, viewport) stay dark in both themes, as the CS's
render window did, because the terrain reads best on a dark ground."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

DARK = {
    "window": "#1e2127", "base": "#16181d", "alt": "#22262d", "panel": "#252a32",
    "text": "#d7dae0", "muted": "#8b93a1", "accent": "#d9a441", "accent2": "#4fa3d9",
    "border": "#333a45", "select": "#3c5a7a", "danger": "#e0605a", "ok": "#6cc070",
    "warn": "#d9a441", "marker": "#d9a441", "marker2": "#4fa3d9",
    "viewport": "#121418", "vtext": "#8b93a1", "sky_top": "#2a3340", "sky_bottom": "#14171c",
    "title1": "#22262d", "title2": "#3a4250", "title_text": "#e4e8ee", "title_sub": "#aab3c0",
}

CLASSIC = {
    "window": "#d4d0c8", "base": "#ffffff", "alt": "#ece9e2", "panel": "#d4d0c8",
    "text": "#000000", "muted": "#404040", "accent": "#000080", "accent2": "#0a246a",
    "border": "#808080", "select": "#0a246a", "danger": "#c00000", "ok": "#006000",
    "warn": "#806000", "marker": "#ffd200", "marker2": "#00e0ff",
    "light": "#ffffff", "shadow": "#808080", "dark": "#404040",
    "title1": "#d4d0c8", "title2": "#c8c4bc", "title_text": "#000000", "title_sub": "#404040",
    "viewport": "#000000", "vtext": "#c0c0c0", "sky_top": "#2b2620", "sky_bottom": "#0b0a08",
}

THEMES = {"classic": CLASSIC, "dark": DARK}

COLORS = dict(CLASSIC)
CURRENT = "classic"


def mono_font() -> QFont:
    if CURRENT == "classic":
        f = QFont("Courier New", 8)
    else:
        f = QFont("Consolas", 9)
    f.setStyleHint(QFont.Monospace)
    return f


def caption_font(compact: bool = False) -> QFont:
    f = QFont("Tahoma" if CURRENT == "classic" else "Segoe UI", 8)
    f.setBold(True)
    if compact:
        f.setPointSize(7)
    return f


def stylesheet_dark(c: dict) -> str:
    return f"""
QWidget {{ color: {c['text']}; }}
QMainWindow, QDialog {{ background: {c['window']}; }}
QMainWindow::separator {{ background: {c['border']}; width: 4px; height: 4px; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {c['panel']}; padding: 5px 8px; border-bottom: 2px solid {c['accent']};
    font-weight: 600; letter-spacing: 0.5px; }}
QToolBar {{ background: {c['panel']}; border: none; border-bottom: 1px solid {c['border']}; spacing: 3px;
    padding: 3px; }}
QToolButton {{ border: 1px solid transparent; border-radius: 4px; padding: 4px 7px; }}
QToolButton:hover {{ background: {c['alt']}; border-color: {c['border']}; }}
QToolButton:checked {{ background: {c['select']}; border-color: {c['accent2']}; }}
QPushButton {{ background: {c['alt']}; border: 1px solid {c['border']}; border-radius: 4px; padding: 5px 12px; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['select']}; }}
QPushButton:disabled {{ color: {c['muted']}; }}
QPushButton[accent="true"] {{ background: {c['accent']}; color: #1b1b1b; font-weight: 600; border: none; }}
QPushButton[accent="true"]:hover {{ background: #e8b75a; }}
QPushButton[accent="true"]:disabled {{ background: #6b5a35; color: #2a2a2a; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{ background: {c['base']};
    border: 1px solid {c['border']}; border-radius: 3px; padding: 3px 5px; selection-background-color: {c['select']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {c['accent2']}; }}
QTreeWidget, QListWidget, QTableWidget, QTreeView, QListView, QTableView {{ background: {c['base']}; border: 1px solid {c['border']};
    alternate-background-color: {c['alt']}; selection-background-color: {c['select']}; }}
QHeaderView::section {{ background: {c['panel']}; border: none; border-right: 1px solid {c['border']};
    padding: 4px 6px; color: {c['muted']}; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: 5px; margin-top: 14px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {c['accent']}; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; }}
QTabBar::tab {{ background: {c['panel']}; padding: 5px 12px; border: 1px solid {c['border']}; border-bottom: none; }}
QTabBar::tab:selected {{ background: {c['window']}; color: {c['accent']}; }}
QStatusBar {{ background: {c['panel']}; border-top: 1px solid {c['border']}; }}
QStatusBar QLabel {{ color: {c['muted']}; padding: 0 8px; }}
QMenuBar {{ background: {c['panel']}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {c['select']}; }}
QMenu {{ background: {c['panel']}; border: 1px solid {c['border']}; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['border']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['accent']}; width: 12px; margin: -5px 0; border-radius: 6px; }}
QProgressBar {{ border: 1px solid {c['border']}; border-radius: 3px; text-align: center; background: {c['base']}; }}
QProgressBar::chunk {{ background: {c['accent']}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; }}
QToolTip {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['accent']}; }}
QLabel[muted="true"] {{ color: {c['muted']}; }}
QLabel[heading="true"] {{ font-size: 15px; font-weight: 600; color: {c['accent']}; }}
QLabel[panelHeader="true"] {{ background: {c['panel']}; border: 1px solid {c['border']};
    padding: 2px; color: {c['muted']}; }}
QSplitter::handle {{ background: {c['window']}; }}
"""


def stylesheet_classic(c: dict) -> str:
    """Only what Qt's Windows style does not already draw like Windows 2000: the
    caption bars of the docks, the status bar wells, the record tabs, and a few
    labels. The controls themselves are the Windows style's own bevelled ones."""
    return f"""
QDockWidget::title {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {c['light']},
    stop:0.20 {c['window']}, stop:1 {c['title2']}); color: {c['title_text']}; padding: 2px 4px;
    font-weight: bold; border-bottom: 1px solid {c['shadow']}; }}
QMainWindow::separator {{ background: {c['window']}; width: 4px; height: 4px; }}
QMainWindow::separator:hover {{ background: {c['shadow']}; }}
QToolBar {{ background: {c['window']}; border-top: 1px solid {c['light']};
    border-bottom: 1px solid {c['shadow']}; spacing: 1px; padding: 1px; }}
QToolBar::separator {{ background: {c['shadow']}; width: 1px; margin: 3px 4px; }}
QStatusBar::item {{ border: 1px solid; border-color: {c['shadow']} {c['light']} {c['light']} {c['shadow']}; }}
QStatusBar QLabel {{ padding: 0 4px; }}
QGroupBox {{ margin-top: 7px; padding-top: 4px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 7px; padding: 0 2px; color: #000000; }}
QTabWidget::pane {{ border: 1px solid {c['shadow']}; border-top: 1px solid {c['light']}; }}
QTabBar::tab {{ background: {c['window']}; border: 1px solid {c['shadow']}; border-bottom: none;
    padding: 2px 7px; margin-right: 1px; }}
QTabBar::tab:selected {{ background: {c['window']}; border-bottom: 1px solid {c['window']};
    margin-bottom: -1px; padding-bottom: 3px; }}
QHeaderView::section {{ background: {c['window']}; border: 1px solid;
    border-color: {c['light']} {c['shadow']} {c['shadow']} {c['light']}; padding: 2px 4px; }}
QTableWidget, QListWidget, QTreeWidget, QTableView, QListView, QTreeView {{ background: {c['base']};
    border: 1px solid {c['shadow']}; alternate-background-color: {c['alt']}; gridline-color: {c['window']}; }}
QPlainTextEdit, QTextEdit {{ font-family: "Courier New", monospace; font-size: 8pt; }}
QToolTip {{ background: #ffffe1; color: #000000; border: 1px solid #000000; }}
QLabel[muted="true"] {{ color: {c['muted']}; }}
QLabel[heading="true"] {{ font-weight: bold; color: #000000; }}
QLabel[panelHeader="true"] {{ background: {c['window']}; border: 1px solid;
    border-color: {c['light']} {c['shadow']} {c['shadow']} {c['light']}; padding: 1px; }}
QSplitter::handle:horizontal {{ width: 4px; background: {c['window']}; border-left: 1px solid {c['shadow']};
    border-right: 1px solid {c['light']}; }}
QSplitter::handle:vertical {{ height: 4px; background: {c['window']}; border-top: 1px solid {c['shadow']};
    border-bottom: 1px solid {c['light']}; }}
QPushButton[accent="true"] {{ font-weight: bold; }}
"""


def _palette(c: dict) -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c["window"]))
    pal.setColor(QPalette.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.Base, QColor(c["base"]))
    pal.setColor(QPalette.AlternateBase, QColor(c["alt"]))
    pal.setColor(QPalette.Text, QColor(c["text"]))
    pal.setColor(QPalette.Button, QColor(c["panel"] if "light" in c else c["alt"]))
    pal.setColor(QPalette.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.Highlight, QColor(c["select"]))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor("#ffffe1" if "light" in c else c["panel"]))
    pal.setColor(QPalette.ToolTipText, QColor(c["text"]))
    pal.setColor(QPalette.PlaceholderText, QColor(c["muted"]))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#808080" if "light" in c else c["muted"]))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#808080" if "light" in c else c["muted"]))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#808080" if "light" in c else c["muted"]))
    if "light" in c:
        pal.setColor(QPalette.Light, QColor(c["light"]))
        pal.setColor(QPalette.Midlight, QColor(c["window"]))
        pal.setColor(QPalette.Mid, QColor(c["shadow"]))
        pal.setColor(QPalette.Dark, QColor(c["dark"]))
        pal.setColor(QPalette.Shadow, QColor("#000000"))
    return pal


def apply(app: QApplication, name: str | None = None) -> None:
    """Apply a theme by name ("classic" or "dark"); unknown names fall back to classic."""
    global CURRENT
    name = name if name in THEMES else "classic"
    CURRENT = name
    COLORS.clear()
    COLORS.update(THEMES[name])
    c = COLORS
    if name == "classic":
        app.setStyle(QStyleFactory.create("Windows"))
        font = QFont("Tahoma", 8)
        font.setStyleHint(QFont.SansSerif)
        sheet = stylesheet_classic(c)
    else:
        app.setStyle(QStyleFactory.create("Fusion"))
        font = QFont("Segoe UI", 9)
        font.setStyleHint(QFont.SansSerif)
        sheet = stylesheet_dark(c)
    app.setPalette(_palette(c))
    app.setFont(font)
    app.setStyleSheet(sheet)
    for w in app.allWidgets():
        w.update()
