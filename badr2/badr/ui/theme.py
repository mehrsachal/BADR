"""Dark operations-room theme: low glare, high-contrast map, one accent colour."""

BG = "#0e1318"
PANEL = "#151c23"
PANEL_2 = "#1b242d"
LINE = "#2a3540"
TEXT = "#dfe6ec"
MUTED = "#8496a5"
ACCENT = "#3fb6ff"
OK = "#39d98a"
WARN = "#ffb000"
BAD = "#ff5c5c"

CLASSIFICATION_COLORS = {
    "UNCLASSIFIED": ("#007a33", "#ffffff"),
    "RESTRICTED": ("#0033a0", "#ffffff"),
    "CONFIDENTIAL": ("#0033a0", "#ffffff"),
    "SECRET": ("#c8102e", "#ffffff"),
    "TOP SECRET": ("#ff8c00", "#000000"),
}


def classification_style(text):
    upper = (text or "").upper()
    for key in ("TOP SECRET", "SECRET", "CONFIDENTIAL", "RESTRICTED", "UNCLASSIFIED"):
        if key in upper:
            bg, fg = CLASSIFICATION_COLORS[key]
            return f"background:{bg};color:{fg};font-weight:bold;font-size:11px;padding:2px;letter-spacing:1px;"
    return f"background:{PANEL_2};color:{TEXT};font-weight:bold;font-size:11px;padding:2px;"


QSS = f"""
* {{ font-family: "Inter", "Segoe UI", "DejaVu Sans", sans-serif; font-size: 12px; }}
QMainWindow, QDialog, QWidget {{ background: {BG}; color: {TEXT}; }}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {LINE}; padding: 4px; }}
QFrame#header {{ background: {PANEL}; border-bottom: 1px solid {LINE}; }}
QFrame#rail {{ background: {PANEL}; border-right: 1px solid {LINE}; }}
QFrame#side, QFrame#assistant {{ background: {PANEL}; }}
QFrame#side {{ border-right: 1px solid {LINE}; }}
QFrame#assistant {{ border-left: 1px solid {LINE}; }}
QFrame#statusbar {{ background: {PANEL}; border-top: 1px solid {LINE}; }}
QFrame#statusbar QLabel {{ color: {MUTED}; font-family: "DejaVu Sans Mono", monospace; font-size: 11px; background: transparent; }}
QLabel {{ background: transparent; }}
QLabel#brand {{ font-size: 16px; font-weight: 800; letter-spacing: 3px; color: {TEXT}; }}
QLabel#opname {{ color: {MUTED}; }}
QLabel#clock {{ font-family: "DejaVu Sans Mono", monospace; color: {TEXT}; font-size: 12px; }}
QLabel#h1 {{ font-size: 13px; font-weight: 700; letter-spacing: 1px; color: {TEXT}; padding: 2px 0 6px 0; }}
QLabel#h2 {{ font-size: 10px; font-weight: 700; letter-spacing: 1.5px; color: {MUTED}; padding-top: 8px; }}
QLabel#hint {{ color: {MUTED}; font-size: 11px; }}
QLabel#pill {{ border-radius: 9px; padding: 2px 9px; font-size: 11px; font-weight: 600; }}
QLineEdit#command {{ background: {BG}; border: 1px solid {LINE}; border-radius: 6px; padding: 8px 10px;
    font-size: 13px; color: {TEXT}; selection-background-color: {ACCENT}; }}
QLineEdit#command:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QPlainTextEdit {{ background: {BG}; border: 1px solid {LINE};
    border-radius: 4px; padding: 4px 6px; color: {TEXT}; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {PANEL_2}; color: {TEXT}; selection-background-color: {ACCENT};
    selection-color: #000; border: 1px solid {LINE}; }}
QPushButton {{ background: {PANEL_2}; border: 1px solid {LINE}; border-radius: 4px; padding: 6px 10px; color: {TEXT}; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed, QPushButton:checked {{ background: {ACCENT}; color: #06131c; border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {LINE}; }}
QPushButton#primary {{ background: {ACCENT}; color: #06131c; font-weight: 700; border-color: {ACCENT}; }}
QPushButton#ghost {{ background: transparent; border: none; color: {MUTED}; padding: 2px 6px; }}
QPushButton#ghost:hover {{ color: {TEXT}; }}
QToolButton#mode {{ background: transparent; border: none; border-left: 3px solid transparent; color: {MUTED};
    padding: 10px 0; font-size: 9px; font-weight: 700; letter-spacing: 1px; min-width: 64px; }}
QToolButton#mode:hover {{ color: {TEXT}; }}
QToolButton#mode:checked {{ color: {ACCENT}; border-left: 3px solid {ACCENT}; background: {PANEL_2}; }}
QListWidget, QTreeWidget, QTreeView, QTableWidget, QTextBrowser {{ background: {BG}; border: 1px solid {LINE};
    border-radius: 4px; color: {TEXT}; outline: none; }}
QListWidget::item {{ padding: 3px; border-radius: 3px; }}
QListWidget::item:selected, QTreeWidget::item:selected, QTreeView::item:selected, QTableWidget::item:selected {{
    background: #1d4460; color: {TEXT}; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {PANEL_2}; }}
QHeaderView::section {{ background: {PANEL_2}; color: {MUTED}; border: none; border-bottom: 1px solid {LINE};
    padding: 4px; font-size: 10px; font-weight: 700; }}
QTableWidget {{ gridline-color: {LINE}; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; }}
QScrollBar::handle:horizontal {{ background: {LINE}; border-radius: 4px; min-width: 24px; }}
QCheckBox {{ spacing: 6px; }}
QMenu {{ background: {PANEL_2}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 5px 18px; border-radius: 3px; }}
QMenu::item:selected {{ background: {ACCENT}; color: #06131c; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 6px; }}
QSplitter::handle {{ background: {LINE}; }}
QProgressBar {{ background: {BG}; border: 1px solid {LINE}; border-radius: 3px; height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; }}
QGroupBox {{ border: 1px solid {LINE}; border-radius: 4px; margin-top: 10px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {MUTED}; font-size: 10px; font-weight: 700; }}
"""
