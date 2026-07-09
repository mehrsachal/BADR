import os
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout, QMessageBox
from qgis.PyQt.QtGui import QFont

class LessonsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Lessons Learnt")
        self.setMinimumSize(500, 400)
        self.lessons_path = os.path.join(os.path.dirname(__file__), "lessons.md")
        self._build_ui()
        self._load_lessons()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        
        self.text_edit = QTextEdit()
        self.text_edit.setAcceptRichText(False)
        self.text_edit.setFont(QFont("Consolas", 11))
        self.text_edit.setStyleSheet("""
            QTextEdit {
                border: 1px solid #E0E0E0;
                border-radius: 8px;
                padding: 10px;
                background: #FAFAFA;
                color: #222;
            }
            QTextEdit:focus {
                border: 1px solid #1565C0;
                background: white;
            }
        """)
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setMinimumWidth(100)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background: #F5F5F5;
                border: 1px solid #E0E0E0;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: bold;
                color: #444;
            }
            QPushButton:hover {
                background: #EEEEEE;
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)
        
        self.save_btn = QPushButton("Save")
        self.save_btn.setMinimumWidth(120)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background: #1565C0;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #0D47A1;
            }
        """)
        self.save_btn.clicked.connect(self._save_lessons)
        btn_layout.addWidget(self.save_btn)
        
        layout.addLayout(btn_layout)

    def _load_lessons(self):
        if os.path.exists(self.lessons_path):
            try:
                with open(self.lessons_path, "r", encoding="utf-8") as f:
                    self.text_edit.setPlainText(f.read())
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to load lessons: {e}")

    def _save_lessons(self):
        try:
            with open(self.lessons_path, "w", encoding="utf-8") as f:
                f.write(self.text_edit.toPlainText())
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save lessons: {e}")
