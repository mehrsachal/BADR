"""Modern Chat dock widget for BADR — featuring message bubbles, history, and integrated progress."""
import threading
import os
import uuid

from qgis.PyQt.QtWidgets import ( # type: ignore
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLabel, QMessageBox,
    QScrollArea, QFrame, QProgressBar, QSizePolicy,
    QSpacerItem, QDialog, QListWidget, QListWidgetItem,
    QMenu, QInputDialog
)
from qgis.PyQt.QtCore import Qt, pyqtSignal, QObject, QSize, QCoreApplication, QPointF, QRectF, QBuffer, QIODevice, QTimer, QByteArray # type: ignore
from qgis.PyQt.QtGui import QColor, QFont, QIcon, QPixmap, QMovie, QPainter, QPolygonF, QTextDocument # type: ignore
from qgis.utils import iface # type: ignore

from .settings import Settings, PROVIDER_INFO
from .llm_service import LlmService
from .map_context import get_map_context, get_knowledge_context
from .code_executor import run_pyqgis
from .history_manager import HistoryManager


def is_alive(qobj):
    if qobj is None:
        return False
    try:
        qobj.parent()
        return True
    except RuntimeError:
        return False


def get_paper_plane_icon(size=40):
    """Draw a clean paper plane icon using QPainter."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#1565C0"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.setBrush(Qt.GlobalColor.white)
    painter.setPen(Qt.PenStyle.NoPen)
    pts = [QPointF(0.3, 0.5), QPointF(0.75, 0.25), QPointF(0.5, 0.7), QPointF(0.45, 0.55)]
    poly = QPolygonF([QPointF(p.x() * size, p.y() * size) for p in pts])
    painter.drawPolygon(poly)
    painter.end()
    return QIcon(pixmap)


def get_stop_icon(size=40):
    """Draw a red square stop icon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#FFEBEE"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.setBrush(QColor("#E53935"))
    margin = int(size * 0.3)
    painter.drawRoundedRect(margin, margin, int(size - 2*margin), int(size - 2*margin), 3, 3)
    painter.end()
    return QIcon(pixmap)


def get_camera_icon(size=40):
    """Draw a camera icon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#F5F5F5"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.setBrush(QColor("#555555"))
    # Camera body
    body = QRectF(size*0.25, size*0.35, size*0.5, size*0.35)
    painter.drawRoundedRect(body, 2, 2)
    # Lens
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawEllipse(QPointF(size*0.5, size*0.52), size*0.12, size*0.12)
    # Flash/Button
    painter.setBrush(QColor("#555555"))
    painter.drawRect(QRectF(size*0.35, size*0.3, size*0.1, size*0.05))
    painter.end()
    return QIcon(pixmap)


class HistoryDialog(QDialog):
    """A styled dialog to browse past chat sessions."""
    session_selected = pyqtSignal(str)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Chat History")
        self.setMinimumSize(320, 400)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget { border: 1px solid #EEE; border-radius: 8px; background: white; color: #333; outline: none; }
            QListWidget::item { padding: 12px; border-bottom: 1px solid #F5F5F5; color: #333; }
            QListWidget::item:selected { background: #E3F2FD; color: #1565C0; border-radius: 4px; }
            QListWidget::item:hover { background: #F5F5F5; color: #333; }
        """)
        
        sessions = self.manager.list_sessions()
        for s in sessions:
            item = QListWidgetItem()
            # Title + Date
            date_str = s["timestamp"][:10] if s["timestamp"] else ""
            item.setText(f"{s['title']}\n{date_str}")
            item.setData(Qt.ItemDataRole.UserRole, s["id"])
            item.setFont(QFont("Segoe UI", 10))
            self.list_widget.addItem(item)
            
        if not sessions:
            self.list_widget.addItem("No past chats found.")
            self.list_widget.setEnabled(False)

        self.list_widget.itemDoubleClicked.connect(self._on_select)
        layout.addWidget(self.list_widget)

        btns = QHBoxLayout()
        delete_btn = QPushButton("Delete")
        delete_btn.setStyleSheet("background: #FFEBEE; color: #C62828; border-radius: 6px; padding: 8px; border: 1px solid #FFCDD2;")
        delete_btn.clicked.connect(self._on_delete)
        btns.addWidget(delete_btn)
        
        btns.addStretch(1)
        
        load_btn = QPushButton("Load Chat")
        load_btn.setStyleSheet("background: #1565C0; color: white; border-radius: 6px; padding: 8px;")
        load_btn.clicked.connect(self._on_select)
        btns.addWidget(load_btn)
        layout.addLayout(btns)

    def _on_delete(self):
        item = self.list_widget.currentItem()
        if item and item.data(Qt.ItemDataRole.UserRole):
            res = QMessageBox.question(self, "Delete Chat", "Are you sure you want to delete this chat?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if res == QMessageBox.StandardButton.Yes:
                self.manager.delete_session(item.data(Qt.ItemDataRole.UserRole))
                self.list_widget.takeItem(self.list_widget.row(item))

    def _on_select(self):
        item = self.list_widget.currentItem()
        if item and item.data(Qt.ItemDataRole.UserRole):
            self.session_selected.emit(item.data(Qt.ItemDataRole.UserRole))
            self.accept()


class _WorkerSignals(QObject):
    response_ready = pyqtSignal(object)
    progress = pyqtSignal(str, int)
    error = pyqtSignal(str)
    tool_executed = pyqtSignal(list)
    status_update = pyqtSignal(str)
    append_msg_signal = pyqtSignal(str, str)
    append_code_signal = pyqtSignal(str)
    execute_main = pyqtSignal(object)
    console_output = pyqtSignal(str)
    canvas_refresh = pyqtSignal()


class SmartTextEdit(QTextEdit):
    send_requested = pyqtSignal()
    image_pasted = pyqtSignal(QPixmap)
    
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier: super().keyPressEvent(event)
            else: self.send_requested.emit()
        else:
            super().keyPressEvent(event)
            
    def insertFromMimeData(self, source):
        if source.hasImage():
            pixmap = QPixmap(source.imageData())
            if not pixmap.isNull():
                self.image_pasted.emit(pixmap)
        else:
            super().insertFromMimeData(source)


class ImagePreviewWidget(QFrame):
    closed = pyqtSignal()
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: #F5F5F5; border-radius: 8px; border: 1px solid #DDD;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        self.img_label = QLabel()
        self.img_label.setPixmap(pixmap.scaled(80, 80, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(self.img_label)
        self.close_btn = QPushButton("\u00d7")
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setStyleSheet("QPushButton { background: #DDD; border: none; border-radius: 10px; font-weight: bold; color: #555; } QPushButton:hover { background: #CCC; }")
        self.close_btn.clicked.connect(self.closed.emit)
        layout.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class ChatBubble(QFrame):
    lesson_requested = pyqtSignal(str)
    
    def __init__(self, role, text, parent=None, pixmap=None):
        super().__init__(parent)
        self.role = role
        self.raw_text = text
        self._setup_ui(text, pixmap)
        
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _show_context_menu(self, pos):
        menu = QMenu()
        save_act = menu.addAction("💡 Save as Lesson Learnt")
        copy_act = menu.addAction("📋 Copy Text")
        
        action = menu.exec(self.mapToGlobal(pos))
        if action == save_act:
            self.lesson_requested.emit(self.raw_text)
        elif action == copy_act:
            from qgis.PyQt.QtWidgets import QApplication # type: ignore
            QApplication.clipboard().setText(self.raw_text)

    def _setup_ui(self, text, pixmap=None):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(10)
        # Ensure the bubble row never forces a horizontal scroll
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.avatar = QLabel()
        self.avatar.setFixedSize(32, 32)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setStyleSheet("background: #F5F5F5; border-radius: 16px; font-size: 18px;")
        avatar_map = {"user": "👤", "assistant": "🤖", "system": "⚙️", "thought": "💭"}
        self.avatar.setText(avatar_map.get(self.role, "?"))
        self.bubble = QFrame()
        self.bubble.setObjectName("bubble")
        self.bubble.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bubble_layout = QVBoxLayout(self.bubble)
        bubble_layout.setContentsMargins(16, 12, 16, 12)
        self.name_label = QLabel()
        if self.role == "assistant":
            self.name_label.setText("BADR")
            self.name_label.setStyleSheet("font-weight: bold; font-size: 11px; color: #2E7D32; background: transparent;")
            bubble_layout.addWidget(self.name_label)
        elif self.role == "thought":
            self.name_label.setText("Thinking Process")
            self.name_label.setStyleSheet("font-weight: bold; font-size: 11px; color: #757575; background: transparent;")
            bubble_layout.addWidget(self.name_label)
        else: self.name_label.hide()
        
        if pixmap:
            img_label = QLabel()
            img_label.setPixmap(pixmap.scaled(320, 240, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            img_label.setStyleSheet("border-radius: 8px; border: 1px solid #DDD; background: #FAFAFA;")
            bubble_layout.addWidget(img_label)

        self.content = QLabel()
        self.content.setWordWrap(True)
        self.content.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.content.setOpenExternalLinks(True)
        self.content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.content.setStyleSheet("background: transparent; border: none; padding: 0;")
        self._set_text(text)
        bubble_layout.addWidget(self.content)
        if self.role == "user":
            layout.addStretch(1); layout.addWidget(self.bubble); layout.addWidget(self.avatar)
            layout.setAlignment(self.avatar, Qt.AlignmentFlag.AlignTop)
            self.bubble.setStyleSheet("QFrame#bubble { background-color: #1565C0; border-radius: 18px; border-bottom-right-radius: 4px; border: none; }")
            self.content.setStyleSheet("color: white; font-size: 14px; background: transparent; border: none;")
        elif self.role == "assistant":
            layout.addWidget(self.avatar); layout.addWidget(self.bubble); layout.addStretch(1)
            layout.setAlignment(self.avatar, Qt.AlignmentFlag.AlignTop)
            self.bubble.setStyleSheet("QFrame#bubble { background-color: #F0F0F0; border-radius: 18px; border-bottom-left-radius: 4px; border: none; }")
            self.content.setStyleSheet("color: #222; font-size: 14px; background: transparent; border: none;")
        elif self.role == "thought":
            layout.addWidget(self.avatar); layout.addWidget(self.bubble); layout.addStretch(1)
            layout.setAlignment(self.avatar, Qt.AlignmentFlag.AlignTop)
            self.bubble.setStyleSheet("QFrame#bubble { background-color: #F8F9FA; border: 1px dashed #B0BEC5; border-radius: 16px; border-bottom-left-radius: 4px; }")
            self.content.setStyleSheet("color: #546E7A; font-style: italic; font-size: 13px; background: transparent; border: none;")
        else:
            layout.addStretch(1); layout.addWidget(self.bubble); layout.addStretch(1)
            self.avatar.hide()
            self.bubble.setStyleSheet("QFrame#bubble { background-color: transparent; border: none; }")
            self.content.setStyleSheet("color: #777; font-style: italic; font-size: 12px; background: transparent; border: none;")

    def _set_text(self, text):
        if self.role in ("user", "assistant", "thought"):
            try:
                import markdown
                html = markdown.markdown(text, extensions=['tables', 'fenced_code', 'nl2br'])
                if "<table>" in html:
                    html = html.replace("<table>", "<table border='1' cellspacing='0' cellpadding='4' style='border-collapse: collapse; border: 1px solid #ddd; width: 100%;'>")
                    html = html.replace("<th>", "<th style='background-color: #e0e0e0; font-weight: bold; text-align: left;'>")
                self.content.setText(html)
            except ImportError:
                doc = QTextDocument()
                doc.setMarkdown(text)
                self.content.setText(doc.toHtml())
        else:
            self.content.setText(text)

    def update_text(self, text):
        self.raw_text = text
        self._set_text(text)


class ThinkingBubble(ChatBubble):
    def __init__(self, parent=None):
        super().__init__("assistant", "", parent)
        if self.name_label: self.name_label.hide()
        pill = QFrame()
        pill.setStyleSheet("background: #FFF3E0; border-radius: 12px; border: none;")
        pill_layout = QHBoxLayout(pill); pill_layout.setContentsMargins(10, 5, 10, 5)
        dot = QLabel("\u25cf"); dot.setStyleSheet("color: #FFA000; font-size: 14px; border: none; background: transparent;")
        self.status_label = QLabel("Thinking...")
        self.status_label.setStyleSheet("color: #E65100; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        pill_layout.addWidget(dot); pill_layout.addWidget(self.status_label)
        self.layout().itemAt(1).widget().layout().addWidget(pill); self.content.hide()
    def set_status(self, text): self.status_label.setText(text)


class ConsoleBubble(ChatBubble):
    """A bubble that displays live console output."""
    def __init__(self, parent=None):
        super().__init__("system", "Console Output...", parent)
        self.content.setStyleSheet("color: #2E7D32; font-family: 'Consolas', monospace; font-size: 11px; background: #F1F8E9; padding: 5px; border-radius: 4px;")
        self.full_text = ""
    def append_text(self, text):
        self.full_text += text
        self.content.setText(self.full_text)


class ProgressBubble(ChatBubble):
    def __init__(self, label, parent=None):
        super().__init__("system", label, parent)
        self.pbar = QProgressBar(); self.pbar.setRange(0, 100); self.pbar.setFixedHeight(10)
        self.layout().itemAt(1).widget().layout().addWidget(self.pbar)
        self.update_style(label)

    def update_style(self, text):
        color = "#2E7D32" if "Google Earth Engine" in text or "GEE" in text else "#FFA726"
        self.pbar.setStyleSheet(f"QProgressBar {{ border: 1px solid #DDD; border-radius: 5px; text-align: center; font-size: 9px; }} QProgressBar::chunk {{ background-color: {color}; border-radius: 4px; }}")

    def update_text(self, text):
        super().update_text(text)
        self.update_style(text)

    def set_value(self, value):
        if value < 0: self.pbar.setRange(0, 0)
        else: self.pbar.setRange(0, 100); self.pbar.setValue(value)


class CodeBubble(ChatBubble):
    def __init__(self, code, parent=None):
        super().__init__("assistant", "Executing Code...", parent)
        self.name_label.setText("PyQGIS Script")
        self.code_edit = QTextEdit(); self.code_edit.setPlainText(code); self.code_edit.setReadOnly(True); self.code_edit.setFixedHeight(140)
        self.code_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.code_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.code_edit.setStyleSheet("QTextEdit { background: #263238; color: #ECEFF1; font-family: 'Consolas', 'Courier New'; font-size: 12px; border-radius: 8px; padding: 10px; border: none; }")
        self.layout().itemAt(1).widget().layout().addWidget(self.code_edit)


class ChatDockWidget(QDockWidget):
    def __init__(self, parent=None):
        super().__init__("BADR", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.setStyleSheet("* { font-family: 'Segoe UI', 'Inter', 'Roboto', sans-serif; } QScrollBar:vertical { width: 8px; background: transparent; } QScrollBar::handle:vertical { background: #DDD; border-radius: 4px; } QScrollBar::handle:vertical:hover { background: #CCC; } QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }")

        self._llm = LlmService()
        self._history_mgr = HistoryManager()
        self._signals = _WorkerSignals()
        self._signals.response_ready.connect(self._on_response)
        self._signals.progress.connect(self._on_progress)
        self._signals.error.connect(self._on_error)
        self._signals.tool_executed.connect(self._on_tool_executed)
        self._signals.status_update.connect(self._on_status_update)
        self._signals.append_msg_signal.connect(self._on_append_msg_signal)
        self._signals.append_code_signal.connect(self._on_append_code)
        self._signals.execute_main.connect(self._on_execute_main)
        self._signals.console_output.connect(self._on_console_output)
        self._signals.canvas_refresh.connect(self._on_canvas_refresh)
        
        self._session_id = uuid.uuid4().hex[:12]
        self._session_title = ""
        self._pending_map_context = ""
        self._is_processing = False
        self._is_aborted = False
        self._tool_depth = 0
        self._active_progress_bubble = None
        self._thinking_bubble = None
        self._active_console_bubble = None
        self._attached_pixmap = None
        self._preview_widget = None
        self._last_user_query = ""

        self._build_ui()
        self._append_system(f"Ready using {PROVIDER_INFO[Settings.provider()]['display']}.")

    def _build_ui(self):
        container = QWidget(); container.setStyleSheet("background: white;")
        layout = QVBoxLayout(container); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        header = QFrame(); header.setFixedHeight(48); header.setStyleSheet("background: white; border-bottom: 1px solid #F0F0F0;")
        header_layout = QHBoxLayout(header); header_layout.setContentsMargins(15, 0, 10, 0)
        
        logo_label = QLabel()
        logo_path = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(logo_path):
            logo_pix = QPixmap(logo_path).scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            logo_label.setPixmap(logo_pix)
        header_layout.addWidget(logo_label)
        
        title = QLabel("BADR"); title.setStyleSheet("font-weight: bold; color: #333; font-size: 16px; letter-spacing: 0.5px;")
        header_layout.addWidget(title); header_layout.addStretch(1)

        btn_style = "QPushButton { border: none; background: transparent; border-radius: 14px; padding: 4px; } QPushButton:hover { background: #F5F5F5; } QPushButton#clear_btn:hover { background: #FFEBEE; }"
        
        # History Button
        self.history_btn = QPushButton()
        self.history_btn.setIcon(QIcon(":/images/themes/default/mActionHistory.svg"))
        self.history_btn.setToolTip("Past Chats")
        self.history_btn.setFixedSize(30, 30); self.history_btn.setStyleSheet(btn_style)
        self.history_btn.clicked.connect(self._open_history)
        header_layout.addWidget(self.history_btn)

        self.clear_btn = QPushButton()
        self.clear_btn.setObjectName("clear_btn"); self.clear_btn.setIcon(QIcon(":/images/themes/default/mActionDeleteSelected.svg")); self.clear_btn.setToolTip("Clear History")
        self.clear_btn.setFixedSize(30, 30); self.clear_btn.setStyleSheet(btn_style); self.clear_btn.clicked.connect(self._clear_chat)
        header_layout.addWidget(self.clear_btn)

        self.settings_btn = QPushButton()
        self.settings_btn.setIcon(QIcon(":/images/themes/default/mActionOptions.svg")); self.settings_btn.setToolTip("Settings")
        self.settings_btn.setFixedSize(30, 30); self.settings_btn.setStyleSheet(btn_style); self.settings_btn.clicked.connect(self._open_settings)
        header_layout.addWidget(self.settings_btn)

        self.lesson_btn = QPushButton()
        self.lesson_btn.setText("💡"); self.lesson_btn.setToolTip("Add Lesson Learnt")
        self.lesson_btn.setFixedSize(30, 30); self.lesson_btn.setStyleSheet(btn_style); self.lesson_btn.clicked.connect(self._add_manual_lesson)
        header_layout.addWidget(self.lesson_btn)

        layout.addWidget(header)
        self.scroll_area = QScrollArea(); self.scroll_area.setWidgetResizable(True); self.scroll_area.setFrameShape(QFrame.Shape.NoFrame); self.scroll_area.setStyleSheet("background: white; border: none;")
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_content = QWidget(); self.scroll_content.setStyleSheet("background: white;")
        self.scroll_content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.chat_layout = QVBoxLayout(self.scroll_content); self.chat_layout.setContentsMargins(12, 12, 12, 12); self.chat_layout.setSpacing(16); self.chat_layout.addStretch(1)
        self.scroll_area.setWidget(self.scroll_content); layout.addWidget(self.scroll_area, 1)

        input_container = QFrame(); input_container.setStyleSheet("background: white; border-top: 1px solid #F0F0F0;")
        self.input_layout = QVBoxLayout(input_container); self.input_layout.setContentsMargins(15, 12, 15, 15)
        prompt_layout = QHBoxLayout(); prompt_layout.setSpacing(10)
        
        self.camera_btn = QPushButton(); self.camera_btn.setFixedSize(42, 42); self.camera_btn.setCursor(Qt.CursorShape.PointingHandCursor); self.camera_btn.setStyleSheet("QPushButton { border: none; background: transparent; padding: 0; }"); self.camera_btn.setIcon(get_camera_icon(42)); self.camera_btn.setIconSize(QSize(42, 42)); self.camera_btn.setToolTip("Snapshot of Map Canvas"); self.camera_btn.clicked.connect(self._capture_screen)
        prompt_layout.addWidget(self.camera_btn)

        self.input_edit = SmartTextEdit(); self.input_edit.setPlaceholderText("Type a message..."); self.input_edit.setMaximumHeight(100); self.input_edit.setMinimumHeight(40)
        self.input_edit.setStyleSheet("QTextEdit { border: 1px solid #E0E0E0; border-radius: 20px; padding: 10px 16px; font-size: 14px; background: #FAFAFA; color: #222; } QTextEdit:focus { border: 1px solid #1565C0; background: #FFF; }")
        self.input_edit.send_requested.connect(self._send_message); self.input_edit.image_pasted.connect(self._on_image_attached); prompt_layout.addWidget(self.input_edit, 1)
        
        self.send_btn = QPushButton(); self.send_btn.setFixedSize(42, 42); self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor); self.send_btn.setStyleSheet("QPushButton { border: none; background: transparent; padding: 0; }"); self.send_btn.setIcon(get_paper_plane_icon(42)); self.send_btn.setIconSize(QSize(42, 42)); self.send_btn.clicked.connect(self._send_message); prompt_layout.addWidget(self.send_btn)
        
        self.stop_btn = QPushButton(); self.stop_btn.setFixedSize(42, 42); self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor); self.stop_btn.setStyleSheet("QPushButton { border: none; background: transparent; padding: 0; }"); self.stop_btn.setIcon(get_stop_icon(42)); self.stop_btn.setIconSize(QSize(42, 42)); self.stop_btn.clicked.connect(self._stop_processing); self.stop_btn.hide(); prompt_layout.addWidget(self.stop_btn)

        self.input_layout.addLayout(prompt_layout); layout.addWidget(input_container); self.setWidget(container)

    def _append_bubble(self, widget):
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, widget)
        if is_alive(self._thinking_bubble) and widget != self._thinking_bubble:
            self.chat_layout.removeWidget(self._thinking_bubble); self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._thinking_bubble)
        if isinstance(widget, ChatBubble) and not isinstance(widget, (ThinkingBubble, ConsoleBubble, ProgressBubble, CodeBubble)):
            widget.lesson_requested.connect(self._save_lesson)
        QTimer.singleShot(50, lambda: self._scroll_bottom(force=True)); return widget

    def _append_msg(self, role: str, text: str, pixmap=None):
        bubble = ChatBubble(role, text, pixmap=pixmap); return self._append_bubble(bubble)

    def _append_system(self, text: str): return self._append_msg("system", text)

    def _scroll_bottom(self, force=False):
        vbar = self.scroll_area.verticalScrollBar()
        # Only scroll if we're already near the bottom (within 50px) or forced
        if force or vbar.value() >= vbar.maximum() - 50:
            QCoreApplication.processEvents()
            vbar.setValue(vbar.maximum())

    def _clear_chat(self):
        if self._llm._history:
            self._history_mgr.save_session(self._session_id, self._session_title, self._llm._history)
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._llm.clear_history(); self._session_id = uuid.uuid4().hex[:12]; self._session_title = ""
        self._active_console_bubble = None
        self._thinking_bubble = None
        self._active_progress_bubble = None
        self._append_system("New session started.")

    def _open_settings(self):
        from .settings_dialog import SettingsDialog
        from .settings import Settings, PROVIDER_INFO
        dlg = SettingsDialog(self.parentWidget())
        if dlg.exec():
            provider = Settings.provider()
            self._append_system(f"Settings updated. Ready using {PROVIDER_INFO[provider]['display']}.")

    def _open_history(self):
        dlg = HistoryDialog(self._history_mgr, self)
        dlg.session_selected.connect(self._load_session)
        dlg.exec()

    def _add_manual_lesson(self):
        text, ok = QInputDialog.getMultiLineText(self, "New Lesson Learnt", "Describe the lesson to avoid future mistakes\n(e.g. Always check CRS before slope calculation):")
        if ok and text.strip():
            self._save_lesson(text.strip())

    def _save_lesson(self, text):
        path = os.path.join(os.path.dirname(__file__), "lessons.md")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n- {text}")
            self._append_system("Lesson saved to long-term memory.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save lesson: {e}")

    def _load_session(self, session_id):
        import base64 as _b64
        data = self._history_mgr.load_session(session_id)
        if not data: return
        self._clear_chat()
        self._session_id = data["id"]; self._session_title = data.get("title", "")

        def _pix_from_b64(b64_str: str):
            """Decode a base64 string to QPixmap. Returns None if decoding fails."""
            try:
                raw = _b64.b64decode(b64_str)   # plain Python bytes — works with loadFromData
                pix = QPixmap()
                if pix.loadFromData(raw) and not pix.isNull():
                    return pix
            except Exception:
                pass
            return None

        for msg in data.get("messages", []):
            role = msg.get("role")
            if role == "user":
                content = msg.get("content") or msg.get("parts", [])
                text = ""
                pixmap = None

                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict):
                            # Anthropic: {"type": "text"} / {"type": "image", "source": {"data": <b64>}}
                            if b.get("type") == "text":
                                text += b.get("text", "")
                            elif b.get("type") == "image":
                                b64 = (b.get("source") or {}).get("data", "")
                                if b64:
                                    pixmap = _pix_from_b64(b64)
                            # OpenAI: {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
                            elif b.get("type") == "image_url":
                                url = (b.get("image_url") or {}).get("url", "")
                                if url.startswith("data:image/"):
                                    b64 = url.split(",", 1)[-1]
                                    pixmap = _pix_from_b64(b64)
                            # Gemini: {"text": ...} / {"inline_data": {"data": <b64>}}
                            elif "text" in b:
                                text += b["text"]
                            elif "inline_data" in b:
                                b64 = (b.get("inline_data") or {}).get("data", "")
                                if b64:
                                    pixmap = _pix_from_b64(b64)
                else:
                    text = str(content)

                self._append_msg("user", text.strip(), pixmap=pixmap)
            elif role in ("assistant", "model"):
                content = msg.get("content") or msg.get("parts", [])
                text = ""
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "text":
                            text += b.get("text", "")
                        elif isinstance(b, dict) and "text" in b:
                            text += b["text"]
                else:
                    text = str(content)
                if text:
                    self._append_msg("assistant", text)
        self._llm._history = data.get("messages", [])

    def _set_processing(self, active: bool, status="Thinking..."):
        self._is_processing = active
        self.send_btn.setVisible(not active)
        self.stop_btn.setVisible(active)
        self.input_edit.setEnabled(not active)
        if active:
            if not is_alive(self._thinking_bubble): self._thinking_bubble = ThinkingBubble(); self._append_bubble(self._thinking_bubble)
            self._thinking_bubble.set_status(status)
        else:
            if is_alive(self._thinking_bubble): self._thinking_bubble.deleteLater()
            self._thinking_bubble = None
            self._force_canvas_refresh()

    def _force_canvas_refresh(self):
        """Aggressively restore map canvas rendering. Runs on main thread."""
        try:
            canvas = iface.mapCanvas()
            if not canvas: return
            # Re-enable the render flag (the 'Render' checkbox in status bar)
            canvas.setRenderFlag(True)
            # Ensure we fully unfreeze if frozen multiple times
            if hasattr(canvas, "isFrozen"):
                while canvas.isFrozen():
                    canvas.freeze(False)
            else:
                canvas.freeze(False)
            # Trigger a full redraw
            canvas.refresh()
            canvas.repaint()
        except Exception:
            pass

    def _on_canvas_refresh(self):
        """Slot connected to canvas_refresh signal — runs on main thread."""
        self._force_canvas_refresh()

    def _send_message(self):
        text = self.input_edit.toPlainText().strip()
        if not text and not self._attached_pixmap: return
        if self._is_processing: return
        
        self._is_aborted = False
        if not self._session_title: self._session_title = text[:30] + "..." if text else "Visual Inquiry"
        self._last_user_query = text
        
        pixmap = self._attached_pixmap
        img_b64 = None
        if pixmap:
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            pixmap.save(buffer, "PNG")
            img_b64 = bytes(buffer.data().toBase64()).decode()
        
        self._append_msg("user", text, pixmap=pixmap)
        self.input_edit.clear()
        self._remove_image()
        self._set_processing(True)
        
        self._tool_depth = 0
        def retry_cb(msg): self._signals.status_update.emit(msg)
        full_ctx = "### CURRENT QGIS STATE:\n" + get_map_context() + "\n" + get_knowledge_context(self._last_user_query)
        threading.Thread(target=self._llm_worker, args=(lambda: self._llm.send(text, full_ctx, image_base64=img_b64, retry_callback=retry_cb),), daemon=True).start()

    def _on_image_attached(self, pixmap):
        self._remove_image()
        self._attached_pixmap = pixmap
        self._preview_widget = ImagePreviewWidget(pixmap)
        self._preview_widget.closed.connect(self._remove_image)
        # Insert preview ABOVE the prompt row (index 0 in input_layout)
        self.input_layout.insertWidget(0, self._preview_widget, 0, Qt.AlignmentFlag.AlignLeft)

    def _remove_image(self):
        if self._preview_widget:
            self.input_layout.removeWidget(self._preview_widget)
            self._preview_widget.deleteLater()
            self._preview_widget = None
        self._attached_pixmap = None

    def _capture_screen(self):
        if iface:
            canvas = iface.mapCanvas()
            pixmap = canvas.grab()
            self._on_image_attached(pixmap)

    def _llm_worker(self, fn):
        try: 
            res = fn()
            if not self._is_aborted: self._signals.response_ready.emit(res)
        except Exception as e: 
            if not self._is_aborted: self._signals.error.emit(str(e))

    def _stop_processing(self):
        self._is_aborted = True
        self._set_processing(False)
        self._append_system("Processing cancelled by user.")

    def _on_response(self, resp):
        if self._is_aborted: return
        if not resp.has_tool_call:
            if resp.text:
                import re
                suggestions = []
                match = re.search(r"<suggested_prompts>(.*?)</suggested_prompts>", resp.text, re.DOTALL)
                if match:
                    content = match.group(1)
                    suggestions = [s.strip() for s in content.split("|") if s.strip()]
                    resp.text = resp.text.replace(match.group(0), "").strip()
                
                if resp.text:
                    self._append_msg("assistant", resp.text)
                
                if suggestions:
                    self._add_suggestions_ui(suggestions[:2])
                    
            self._set_processing(False)
            if self._llm._history:
                self._history_mgr.save_session(self._session_id, self._session_title, self._llm._history)
            return
        if self._tool_depth >= 15: self._append_system("Too many tool calls."); self._set_processing(False); return
        threading.Thread(target=self._tool_worker, args=(resp,), daemon=True).start()

    def _add_suggestions_ui(self, suggestions):
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        outer = QVBoxLayout(container)
        outer.setContentsMargins(50, 0, 10, 10)  # Indent to align with assistant bubble
        outer.setSpacing(6)

        btn_style = """
            QPushButton {
                background: #E3F2FD;
                color: #1565C0;
                border-radius: 12px;
                padding: 8px 12px;
                border: 1px solid #BBDEFB;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #BBDEFB;
            }
        """

        for text in suggestions:
            btn = QPushButton()
            btn.setStyleSheet(btn_style)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            
            btn_layout = QHBoxLayout(btn)
            btn_layout.setContentsMargins(12, 8, 12, 8)
            btn_layout.setSpacing(0)
            
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lbl.setStyleSheet("color: #1565C0; font-size: 12px; background: transparent;")
            
            btn_layout.addWidget(lbl)
            btn.clicked.connect(lambda checked, t=text: self._execute_suggestion(t))
            outer.addWidget(btn)

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, container)
        QTimer.singleShot(50, lambda: self._scroll_bottom(force=True))

    def _execute_suggestion(self, text):
        self.input_edit.setText(text)
        self._send_message()

    def _tool_worker(self, resp):
        results = []
        for i, tc in enumerate(resp.tool_calls):
            if tc["name"] == "record_lesson":
                lesson = tc["arguments"].get("lesson", "")
                self._save_lesson(lesson)
                msg = "Lesson recorded successfully."
                self._signals.append_msg_signal.emit("system", msg)
                results.append((tc["id"], msg))
                continue

            if tc["name"] != "run_pyqgis": 
                msg = f"Unknown tool: {tc['name']}"
                self._signals.append_msg_signal.emit("system", msg)
                results.append((tc["id"], msg))
                continue
                
            code = tc["arguments"].get("code", ""); explanation = tc["arguments"].get("explanation", "")
            if i == 0 and resp.text: self._signals.append_msg_signal.emit("thought", resp.text)
            self._signals.append_msg_signal.emit("thought", explanation or "Executing script...")
            
            if Settings.show_generated_code():
                self._signals.append_code_signal.emit(code)
                
            user_approved = [True]
            if Settings.confirm_before_execute():
                confirm_event = threading.Event()
                def ask_user():
                    from qgis.PyQt.QtWidgets import QMessageBox
                    reply = QMessageBox.question(
                        self, "Confirm Execution", 
                        "The AI wants to run the following PyQGIS script:\n\n" + 
                        (code[:500] + "...\n(truncated)" if len(code) > 500 else code) + 
                        "\n\nDo you want to allow this?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.Yes
                    )
                    user_approved[0] = (reply == QMessageBox.StandardButton.Yes)
                    confirm_event.set()
                self._signals.execute_main.emit(ask_user)
                confirm_event.wait()

            if not user_approved[0]:
                msg = "Execution cancelled by user."
                self._signals.append_msg_signal.emit("system", msg)
                results.append((tc["id"], msg))
                continue

            self._signals.status_update.emit("Running Python...")

            res_container = []
            done_event = threading.Event()

            def do_run(code=code, res_container=res_container, done_event=done_event):
                """Runs entirely on the main thread via execute_main signal."""
                try:
                    is_gee = "import ee" in code or "ee.Initialize" in code
                    label = "Requesting Google Earth Engine..." if is_gee else "Executing PyQGIS script..."
                    self._signals.progress.emit(label, -1)
                    res_container.append(run_pyqgis(
                        code,
                        progress_callback=lambda l, v: self._signals.progress.emit(l, v),
                        stream_callback=lambda t: self._signals.console_output.emit(t),
                        is_aborted_callback=lambda: self._is_aborted
                    ))
                    self._signals.progress.emit("Execution complete.", 100)
                except Exception as e:
                    import traceback
                    print(f"Error in do_run: {e}\n{traceback.format_exc()}")
                finally:
                    done_event.set()

            # Run the code on the main thread via execute_main signal
            self._signals.execute_main.emit(do_run)

            # Wait for completion on this worker thread. Responsive UI (during downloads)
            # is maintained by QCoreApplication.processEvents() called inside the download helpers on the main thread.
            while not done_event.wait(timeout=0.05):
                if self._is_aborted:
                    break

            if self._is_aborted:
                results.append((tc["id"], "Execution aborted by user."))
                break

            if not res_container:
                results.append((tc["id"], "Execution produced no result."))
                continue

            res = res_container[0]
            
            # Signal main thread to refresh the canvas after each script
            self._signals.canvas_refresh.emit()
            
            if not res.success:
                self._signals.append_msg_signal.emit("system", str(res))
                
            results.append((tc["id"], str(res)))
        self._signals.tool_executed.emit(results)

    def _on_console_output(self, text):
        if not is_alive(self._active_console_bubble): self._active_console_bubble = self._append_bubble(ConsoleBubble())
        self._active_console_bubble.append_text(text)
        QTimer.singleShot(10, lambda: self._scroll_bottom())

    def _on_append_msg_signal(self, role, text):
        self._append_msg(role, text)

    def _on_append_code(self, code):
        self._append_bubble(CodeBubble(code))

    def _on_execute_main(self, func):
        try:
            func()
        except Exception as e:
            print(f"Error in execute_main: {e}")

    def _on_tool_executed(self, results):
        if self._is_aborted: return
        self._active_console_bubble = None # Reset for next tool call
        self._active_progress_bubble = None
        self._tool_depth += 1; self._set_processing(True, status="Thinking...")
        def retry_cb(msg): self._signals.status_update.emit(msg)
        full_ctx = "### CURRENT QGIS STATE:\n" + get_map_context() + "\n" + get_knowledge_context(self._last_user_query)
        threading.Thread(target=self._llm_worker, args=(lambda: self._llm.send_tool_results(results, full_ctx, retry_callback=retry_cb),), daemon=True).start()

    def _on_progress(self, l, v):
        if not is_alive(self._active_progress_bubble): self._active_progress_bubble = self._append_bubble(ProgressBubble(l))
        self._active_progress_bubble.update_text(l); self._active_progress_bubble.set_value(v)
    def _on_status_update(self, t):
        if is_alive(self._thinking_bubble): self._thinking_bubble.set_status(t)
    def _on_error(self, e): self._append_system(f"Error: {e}"); self._set_processing(False)
