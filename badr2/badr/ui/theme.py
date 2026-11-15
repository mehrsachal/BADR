"""Visual language: neutral graphite, one accent, system type, tight spacing.

Colour is reserved for meaning (affiliation on the map, state in the UI).
Chrome stays grey so the map is the brightest thing on screen.
"""

BG = "#17181a"          # window / wells
PANEL = "#1f2023"       # side panels, header
RAISED = "#292a2e"      # buttons, inputs on panels
PANEL_2 = RAISED
LINE = "#34363b"
TEXT = "#e4e5e8"
MUTED = "#8f9199"
ACCENT = "#4a90e2"
ACCENT_DIM = "#24476f"
OK = "#4cc38a"
WARN = "#e5a23a"
BAD = "#e5604d"

CLASSIFICATION_COLORS = {
    "UNCLASSIFIED": ("#0a7a3b", "#ffffff"),
    "RESTRICTED": ("#1d4ea8", "#ffffff"),
    "CONFIDENTIAL": ("#1d4ea8", "#ffffff"),
    "SECRET": ("#b3202e", "#ffffff"),
    "TOP SECRET": ("#e08a00", "#000000"),
}


def classification_style(text):
    upper = (text or "").upper()
    for key in ("TOP SECRET", "SECRET", "CONFIDENTIAL", "RESTRICTED", "UNCLASSIFIED"):
        if key in upper:
            bg, fg = CLASSIFICATION_COLORS[key]
            return f"background:{bg};color:{fg};font-weight:600;font-size:10px;padding:1px;"
    return f"background:{RAISED};color:{TEXT};font-weight:600;font-size:10px;padding:1px;"


QSS = f"""
* {{ font-family: "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans", sans-serif; font-size: 9pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QDialog QWidget, QMenu, QToolTip {{ background-color: {PANEL}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE}; padding: 3px 6px; }}
QLabel {{ background: transparent; }}

QFrame#header {{ background: {PANEL}; border-bottom: 1px solid {LINE}; }}
QFrame#rail {{ background: {PANEL}; border-right: 1px solid {LINE}; }}
QFrame#side {{ background: {PANEL}; border-right: 1px solid {LINE}; }}
QFrame#assistant {{ background: {PANEL}; border-left: 1px solid {LINE}; }}
QFrame#statusbar {{ background: {PANEL}; border-top: 1px solid {LINE}; }}
QFrame#statusbar QLabel {{ color: {MUTED}; font-family: "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace;
    font-size: 8pt; }}
QWidget#bare {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QFrame#card {{ background: {BG}; border: 1px solid {LINE}; border-radius: 4px; }}
QFrame#maptools {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 4px; }}

QLabel#brand {{ font-size: 10pt; font-weight: 700; color: {TEXT}; }}
QLabel#opname {{ color: {MUTED}; }}
QLabel#clock {{ font-family: "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace; color: {MUTED}; font-size: 8pt; }}
QLabel#h1 {{ font-size: 10pt; font-weight: 600; color: {TEXT}; padding: 0 0 4px 0; }}
QLabel#h2 {{ font-size: 8pt; font-weight: 600; color: {MUTED}; padding-top: 8px; }}
QLabel#hint {{ color: {MUTED}; font-size: 8pt; }}
QLabel#state {{ color: {MUTED}; font-size: 8pt; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QPlainTextEdit {{ background: {BG}; border: 1px solid {LINE};
    border-radius: 3px; padding: 3px 6px; min-height: 18px; color: {TEXT}; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit#command {{ padding: 6px 8px; font-size: 9.5pt; }}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{ background: {RAISED}; color: {TEXT}; selection-background-color: {ACCENT_DIM};
    selection-color: {TEXT}; border: 1px solid {LINE}; outline: none; }}

QPushButton {{ background: {RAISED}; border: 1px solid {LINE}; border-radius: 3px; padding: 4px 10px; min-height: 16px;
    color: {TEXT}; }}
QPushButton:hover {{ background: #323338; border-color: #4a4c52; }}
QPushButton:pressed {{ background: {BG}; }}
QPushButton:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #6a6c73; }}
QPushButton#primary {{ background: #2f6fbd; border-color: #2f6fbd; color: #ffffff; font-weight: 600; }}
QPushButton#primary:hover {{ background: #3a7dcf; }}
QPushButton#flat {{ background: transparent; border: none; color: {MUTED}; padding: 2px 6px; }}
QPushButton#flat:hover {{ color: {TEXT}; }}

QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 3px; padding: 3px; }}
QToolButton:hover {{ background: {RAISED}; }}
QToolButton:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT}; }}
QToolButton#mode {{ border: none; border-left: 2px solid transparent; border-radius: 0; padding: 9px 0; min-width: 42px; }}
QToolButton#mode:hover {{ background: {RAISED}; }}
QToolButton#mode:checked {{ background: {BG}; border-left: 2px solid {ACCENT}; }}

QListWidget, QTreeWidget, QTreeView, QTableWidget, QTableView, QTextBrowser {{ background: {BG}; border: 1px solid {LINE};
    border-radius: 3px; color: {TEXT}; outline: none; alternate-background-color: #1b1c1f; }}
QListWidget::item {{ padding: 2px 3px; }}
QListWidget::item:selected, QTreeWidget::item:selected, QTreeView::item:selected, QTableWidget::item:selected,
QTableView::item:selected {{ background: {ACCENT_DIM}; color: {TEXT}; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {RAISED}; }}
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none; border-bottom: 1px solid {LINE};
    border-right: 1px solid {LINE}; padding: 3px 6px; font-size: 8pt; }}
QTableWidget, QTableView {{ gridline-color: {LINE}; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #3d3f45; border-radius: 3px; min-height: 24px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #3d3f45; border-radius: 3px; min-width: 24px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QCheckBox {{ spacing: 6px; }}
QMenu {{ background: {RAISED}; border: 1px solid {LINE}; padding: 3px; }}
QMenu::item {{ padding: 4px 22px 4px 12px; }}
QMenu::item:selected {{ background: {ACCENT_DIM}; }}
QMenu::item:disabled {{ color: #6a6c73; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 3px 4px; }}
QSplitter::handle {{ background: {LINE}; }}
QProgressBar {{ background: {BG}; border: none; border-radius: 1px; max-height: 3px; min-height: 3px; }}
QProgressBar::chunk {{ background: {ACCENT}; }}
QProgressBar#splashbar {{ max-height: 4px; min-height: 4px; background: #2a2b2f; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 4px 10px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QDialog QLabel {{ background: transparent; }}
"""
