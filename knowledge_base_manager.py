"""Dialog to manage Markdown files in the knowledge base directory."""
import os
import shutil
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
    QPushButton, QMessageBox, QFileDialog, QLabel
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from .settings import Settings


class KnowledgeBaseManager(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Knowledge Base Documents")
        self.setMinimumSize(500, 400)
        self.kb_dir = Settings.knowledge_base_dir()
        
        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.label = QLabel(f"Directory: {self.kb_dir}")
        self.label.setStyleSheet("color: #666; font-size: 11px;")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        
        self.add_btn = QPushButton("Add Document")
        self.add_btn.clicked.connect(self._add_document)
        btn_layout.addWidget(self.add_btn)

        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.clicked.connect(self._delete_document)
        btn_layout.addWidget(self.delete_btn)

        self.open_folder_btn = QPushButton("Open Folder")
        self.open_folder_btn.clicked.connect(self._open_folder)
        btn_layout.addWidget(self.open_folder_btn)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_list)
        btn_layout.addWidget(self.refresh_btn)

        layout.addLayout(btn_layout)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

    def _refresh_list(self):
        self.list_widget.clear()
        if not os.path.isdir(self.kb_dir):
            return

        files = [f for f in os.listdir(self.kb_dir) if f.lower().endswith(".md")]
        for f in sorted(files):
            self.list_widget.addItem(f)

    def _add_document(self):
        if not os.path.isdir(self.kb_dir):
            os.makedirs(self.kb_dir, exist_ok=True)

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Markdown Document", "", "Markdown Files (*.md)"
        )
        if not file_path:
            return

        filename = os.path.basename(file_path)
        dest_path = os.path.join(self.kb_dir, filename)

        if os.path.exists(dest_path):
            reply = QMessageBox.question(
                self, "Overwrite File?",
                f"A file named '{filename}' already exists. Overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                return

        try:
            shutil.copy2(file_path, dest_path)
            self._refresh_list()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to copy file: {str(e)}")

    def _delete_document(self):
        selected_items = self.list_widget.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "No Selection", "Please select a document to delete.")
            return

        filename = selected_items[0].text()
        file_path = os.path.join(self.kb_dir, filename)

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Are you sure you want to delete '{filename}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                os.remove(file_path)
                self._refresh_list()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete file: {str(e)}")

    def _open_folder(self):
        if os.path.isdir(self.kb_dir):
            import subprocess
            if os.name == 'nt':
                os.startfile(self.kb_dir)
            else:
                subprocess.Popen(['xdg-open', self.kb_dir])
        else:
            QMessageBox.warning(self, "Warning", "Directory does not exist yet.")
