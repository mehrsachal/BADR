"""Settings dialog for BADR plugin."""
import os

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QComboBox, QCheckBox, QSpinBox,
    QFileDialog, QToolButton, QPushButton, QGroupBox,
    QMessageBox, QScrollArea, QWidget, QTabWidget,
    QTextBrowser, QFrame, QSizePolicy
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

from .settings import Settings, LlmProvider, PROVIDER_INFO


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("BADR - Settings")
        self.setMinimumWidth(480)
        self.setMaximumWidth(580)
        self.resize(500, 560)
        self._current_provider = None
        self._cache = {}
        self._build_ui()
        self._load_settings()

    # ------------------------------------------------------------------
    # UI BUILD
    # ------------------------------------------------------------------
    def _build_ui(self):
        self.setStyleSheet("""
            QDialog { background-color: #F7F8FA; }
            QLabel { color: #333; font-size: 12px; font-family: 'Segoe UI', sans-serif; }
            QLineEdit, QComboBox, QSpinBox {
                border: 1px solid #D8DCE3; border-radius: 6px; padding: 5px 9px;
                background: #FFFFFF; font-size: 12px; color: #222;
                selection-background-color: #1565C0;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 1px solid #1565C0; background: white;
            }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox::down-arrow {
                image: none; border-left: 4px solid transparent;
                border-right: 4px solid transparent; border-top: 4px solid #666;
                margin-right: 8px;
            }
            QComboBox QAbstractItemView {
                background-color: white; color: #222;
                selection-background-color: #1565C0; selection-color: white;
                border: 1px solid #D8DCE3;
            }
            QPushButton {
                background: #EDEFF3; border: 1px solid #D8DCE3; border-radius: 6px;
                padding: 5px 12px; font-size: 12px; font-weight: 600; color: #444;
            }
            QPushButton:hover { background: #E1E4EA; }
            QToolButton {
                border: 1px solid #D8DCE3; border-radius: 6px; padding: 3px 7px;
                background: #EDEFF3; font-size: 12px;
            }
            QToolButton:hover { background: #E1E4EA; }
            QCheckBox { spacing: 6px; color: #444; font-size: 12px; }
            QCheckBox::indicator {
                width: 16px; height: 16px; border-radius: 4px;
                border: 1px solid #CCC; background: white;
            }
            QCheckBox::indicator:checked { background-color: #1565C0; border-color: #1565C0; }
            QGroupBox {
                font-size: 11px; font-weight: bold;
                border: 1px solid #E2E5EC; border-radius: 8px;
                margin-top: 14px; padding: 12px 10px 10px 10px; background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #1565C0;
            }
            QTabWidget::pane {
                border: 1px solid #D8DCE3; border-radius: 4px; background: #F7F8FA; top: -1px;
            }
            QTabBar::tab {
                background: #EDEFF3; color: #555; border: 1px solid #D8DCE3;
                padding: 6px 14px; border-top-left-radius: 4px; border-top-right-radius: 4px;
                margin-right: 2px; font-size: 12px;
            }
            QTabBar::tab:selected {
                background: #F7F8FA; border-bottom-color: #F7F8FA;
                font-weight: bold; color: #1565C0;
            }
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical {
                width: 7px; background: transparent; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #C8CDD8; border-radius: 3px; min-height: 20px;
            }
            QScrollBar::handle:vertical:hover { background: #B0B5C0; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        # --- Root layout ---
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # --- Header bar (title + Save button) ---
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        lbl_title = QLabel("⚙  BADR Settings")
        lbl_title.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #1565C0; background: transparent;"
        )
        header.addWidget(lbl_title)
        header.addStretch(1)

        self.save_btn = QPushButton("💾  Save")
        self.save_btn.setObjectName("save_header_btn")
        self.save_btn.setToolTip("Save all settings now")
        self.save_btn.setMinimumWidth(90)
        self.save_btn.setStyleSheet(
            "QPushButton#save_header_btn {"
            "  background: #1565C0; color: white; border: none; border-radius: 6px;"
            "  padding: 5px 14px; font-size: 12px; font-weight: bold;"
            "}"
            "QPushButton#save_header_btn:hover { background: #0D47A1; }"
        )
        self.save_btn.clicked.connect(self._save_settings)
        header.addWidget(self.save_btn)

        root.addLayout(header)

        # Thin separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #E0E3EA; background: #E0E3EA; border: none; max-height: 1px;")
        root.addWidget(sep)

        # --- Tabs ---
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self.tabs.addTab(self._build_general_tab(), "General")
        self.tabs.addTab(self._build_about_tab(), "About")

        # --- Bottom buttons (Cancel / OK) ---
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setMinimumWidth(80)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)

        ok = QPushButton("OK")
        ok.setObjectName("ok_btn")
        ok.setMinimumWidth(100)
        ok.setStyleSheet(
            "QPushButton#ok_btn { background: #1565C0; color: white; border: none; border-radius: 6px;"
            " padding: 5px 14px; font-weight: bold; }"
            "QPushButton#ok_btn:hover { background: #0D47A1; }"
        )
        ok.clicked.connect(self._save_and_accept)
        btns.addWidget(ok)
        root.addLayout(btns)

    def _build_general_tab(self):
        """Build the scrollable general settings tab."""
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(6, 6, 10, 6)
        layout.setSpacing(4)

        # ── 0. WORKSPACE ──────────────────────────────────────────────
        ws_group = QGroupBox("Workspace Folder")
        ws_form = QFormLayout(ws_group)
        ws_form.setSpacing(6)
        ws_form.setContentsMargins(8, 14, 8, 8)

        ws_info = QLabel(
            "All generated layers and files are saved permanently inside sub-folders "
            "of this directory. Leave blank to use system temp (not recommended)."
        )
        ws_info.setWordWrap(True)
        ws_info.setStyleSheet("color: #666; font-size: 10px; background: transparent;")
        ws_form.addRow(ws_info)

        self.ws_folder_edit = QLineEdit()
        self.ws_folder_edit.setPlaceholderText("e.g. C:/Projects/BADR_Workspace")
        # Auto-save immediately when text changes (covers paste / manual type)
        self.ws_folder_edit.editingFinished.connect(self._autosave_workspace)

        self.ws_folder_btn = QToolButton()
        self.ws_folder_btn.setText("...")
        self.ws_folder_btn.setToolTip("Browse for folder")
        self.ws_folder_btn.clicked.connect(self._browse_workspace_folder)

        self.ws_open_btn = QPushButton("Open")
        self.ws_open_btn.setToolTip("Open workspace folder in Explorer")
        self.ws_open_btn.clicked.connect(self._open_workspace_folder)
        self.ws_open_btn.setMaximumWidth(52)

        ws_row = QHBoxLayout()
        ws_row.setSpacing(4)
        ws_row.addWidget(self.ws_folder_edit, 1)
        ws_row.addWidget(self.ws_folder_btn)
        ws_row.addWidget(self.ws_open_btn)
        ws_form.addRow("Path:", ws_row)

        # Live saved indicator
        self.ws_saved_lbl = QLabel("")
        self.ws_saved_lbl.setStyleSheet("color: #2E7D32; font-size: 10px; background: transparent;")
        ws_form.addRow("", self.ws_saved_lbl)

        layout.addWidget(ws_group)

        # ── 1. CORE CONFIGURATION ─────────────────────────────────────
        core_group = QGroupBox("Core Configuration")
        core_layout = QFormLayout(core_group)
        core_layout.setSpacing(7)
        core_layout.setContentsMargins(8, 14, 8, 8)
        core_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Cloud Mode (Online)", "Local Mode (Offline)"])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        core_layout.addRow("Mode:", self.mode_combo)

        self.provider_combo = QComboBox()
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        core_layout.addRow("Provider:", self.provider_combo)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        core_layout.addRow("API Key:", self.api_key_edit)

        self.api_key_help = QLabel()
        self.api_key_help.setStyleSheet("color: #888; font-size: 10px; background: transparent;")
        self.api_key_help.setWordWrap(True)
        core_layout.addRow("", self.api_key_help)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        core_layout.addRow("Model:", self.model_combo)

        self.endpoint_edit = QLineEdit()
        self.endpoint_label = QLabel("Endpoint:")
        core_layout.addRow(self.endpoint_label, self.endpoint_edit)

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(256, 128000)
        self.max_tokens_spin.setSingleStep(1024)
        core_layout.addRow("Max Tokens:", self.max_tokens_spin)

        layout.addWidget(core_group)

        # ── 2. BEHAVIOR ───────────────────────────────────────────────
        beh_group = QGroupBox("Behavior")
        beh_layout = QVBoxLayout(beh_group)
        beh_layout.setSpacing(5)
        beh_layout.setContentsMargins(8, 14, 8, 8)
        self.confirm_check = QCheckBox("Confirm scripts before running")
        self.show_code_check = QCheckBox("Show generated Python code in chat")
        beh_layout.addWidget(self.confirm_check)
        beh_layout.addWidget(self.show_code_check)
        layout.addWidget(beh_group)

        # ── 3. ADVANCED ───────────────────────────────────────────────
        adv_group = QGroupBox("Advanced Context")
        adv_layout = QFormLayout(adv_group)
        adv_layout.setSpacing(6)
        adv_layout.setContentsMargins(8, 14, 8, 8)

        self.gee_project_edit = QLineEdit()
        adv_layout.addRow("GEE Project:", self.gee_project_edit)

        self.kb_dir_edit = QLineEdit()
        self.kb_manage_btn = QPushButton("Manage")
        self.kb_manage_btn.clicked.connect(self._manage_kb_docs)
        self.kb_manage_btn.setMaximumWidth(65)
        self.kb_dir_btn = QToolButton()
        self.kb_dir_btn.setText("...")
        self.kb_dir_btn.clicked.connect(self._browse_kb_dir)

        kb_row = QHBoxLayout()
        kb_row.setSpacing(4)
        kb_row.addWidget(self.kb_dir_edit, 1)
        kb_row.addWidget(self.kb_dir_btn)
        kb_row.addWidget(self.kb_manage_btn)
        adv_layout.addRow("Knowledge Base:", kb_row)

        vdb_row = QHBoxLayout()
        vdb_row.setSpacing(6)
        self.vdb_update_btn = QPushButton("Update Vector DB")
        self.vdb_update_btn.clicked.connect(self._update_vector_db)
        self.vdb_status_label = QLabel(f"Last: {Settings.vector_db_last_updated()}")
        self.vdb_status_label.setStyleSheet("color: #666; font-size: 10px; background: transparent;")
        vdb_row.addWidget(self.vdb_update_btn)
        vdb_row.addWidget(self.vdb_status_label, 1)
        adv_layout.addRow("Vector DB:", vdb_row)

        self.lessons_manage_btn = QPushButton("Edit Lessons")
        self.lessons_manage_btn.clicked.connect(self._manage_lessons)
        adv_layout.addRow("Lessons:", self.lessons_manage_btn)

        layout.addWidget(adv_group)
        layout.addStretch(1)

        scroll.setWidget(inner)
        outer_layout.addWidget(scroll)
        return outer

    def _build_about_tab(self):
        about_tab = QWidget()
        about_layout = QVBoxLayout(about_tab)
        about_text = QTextBrowser()
        about_text.setOpenExternalLinks(True)
        about_text.setStyleSheet(
            "border: none; background: white; font-family: 'Segoe UI', sans-serif;"
            " font-size: 12px; color: #333;"
        )
        about_text.setHtml("""
            <h2 style='color: #1565C0; margin-bottom: 0px;'>BADR</h2>
            <p style='margin-top: 0px;'><b>B</b>attlefield <b>A</b>ssessment and <b>D</b>ata <b>R</b>etrieval</p>
            <p><b>Author:</b> Mehr Sachal</p>
            <hr>
            <p>BADR is an AI-powered geospatial assistant embedded directly into QGIS.</p>
            <h3 style='color: #444;'>Google Earth Engine (GEE) Setup:</h3>
            <ol style='font-size: 11px; color: #555;'>
                <li><b>Install API:</b> <code>pip install earthengine-api</code></li>
                <li><b>Authenticate:</b> Run <code>earthengine authenticate</code> in OSGeo4W Shell.</li>
                <li><b>Cloud Project:</b> Enable Earth Engine API on your Google Cloud Project.</li>
                <li><b>Configuration:</b> Enter your Cloud Project ID in the General tab.</li>
            </ol>
        """)
        about_layout.addWidget(about_text)
        return about_tab

    # ------------------------------------------------------------------
    # PROVIDER LOGIC
    # ------------------------------------------------------------------
    def _on_mode_changed(self):
        is_online = self.mode_combo.currentIndex() == 0
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        online_list = [LlmProvider.Anthropic, LlmProvider.OpenAI, LlmProvider.GoogleGemini]
        offline_list = [LlmProvider.Ollama, LlmProvider.OpenAICompatible]
        targets = online_list if is_online else offline_list
        for p in targets:
            self.provider_combo.addItem(PROVIDER_INFO[p]["display"], p.value)
        if self.provider_combo.count() > 0:
            self.provider_combo.setCurrentIndex(0)
        self.provider_combo.blockSignals(False)
        self._on_provider_changed()

    def _on_provider_changed(self):
        idx = self.provider_combo.currentIndex()
        if idx < 0:
            return
        if self._current_provider is not None:
            self._cache[self._current_provider] = {
                "api_key": self.api_key_edit.text().strip(),
                "model": self.model_combo.currentText().strip(),
                "endpoint": self.endpoint_edit.text().strip(),
            }
        idx = self.provider_combo.currentIndex()
        provider_val = self.provider_combo.itemData(idx)
        self._current_provider = LlmProvider(provider_val)
        info = PROVIDER_INFO[self._current_provider]
        self.api_key_help.setText(info["key_help"])
        self.api_key_edit.setVisible(info["needs_key"])
        self.api_key_help.setVisible(info["needs_key"])
        show_endpoint = True
        self.endpoint_edit.setVisible(show_endpoint)
        self.endpoint_label.setVisible(show_endpoint)
        c = self._cache.get(self._current_provider, {})
        self.api_key_edit.setText(c.get("api_key", ""))
        self.model_combo.clear()
        for m in info["models"]:
            self.model_combo.addItem(m)
        model = c.get("model", "")
        if model:
            idx2 = self.model_combo.findText(model)
            if idx2 >= 0:
                self.model_combo.setCurrentIndex(idx2)
            else:
                self.model_combo.setEditText(model)
        elif info["models"]:
            self.model_combo.setCurrentIndex(0)
        self.endpoint_edit.setText(c.get("endpoint", ""))
        if show_endpoint and not self.endpoint_edit.text():
            self.endpoint_edit.setText(info["endpoint"])

    # ------------------------------------------------------------------
    # LOAD / SAVE
    # ------------------------------------------------------------------
    def _load_settings(self):
        self._cache = {}
        for p in LlmProvider:
            self._cache[p] = {
                "api_key": Settings.api_key(p),
                "model": Settings.model(p),
                "endpoint": Settings.endpoint(p),
            }
        provider = Settings.provider()
        online_list = [LlmProvider.Anthropic, LlmProvider.OpenAI, LlmProvider.GoogleGemini]
        is_online = provider in online_list
        self.mode_combo.blockSignals(True)
        self.mode_combo.setCurrentIndex(0 if is_online else 1)
        self.mode_combo.blockSignals(False)
        self._on_mode_changed()
        for i in range(self.provider_combo.count()):
            if self.provider_combo.itemData(i) == provider.value:
                self.provider_combo.setCurrentIndex(i)
                break

        # Load workspace — shown in field immediately
        ws = Settings.workspace_folder()
        self.ws_folder_edit.setText(ws)
        if ws:
            self.ws_saved_lbl.setText(f"✔ Saved: {ws}")

        self.max_tokens_spin.setValue(Settings.max_tokens())
        self.confirm_check.setChecked(Settings.confirm_before_execute())
        self.show_code_check.setChecked(Settings.show_generated_code())
        self.gee_project_edit.setText(Settings.gee_project())
        self.kb_dir_edit.setText(Settings.knowledge_base_dir())

    def _collect_and_persist(self):
        """Persist ALL settings to QgsSettings immediately."""
        # Provider cache flush
        if self._current_provider:
            self._cache[self._current_provider] = {
                "api_key": self.api_key_edit.text().strip(),
                "model": self.model_combo.currentText().strip(),
                "endpoint": self.endpoint_edit.text().strip(),
            }
        for provider, vals in self._cache.items():
            Settings.set_api_key(vals["api_key"], provider)
            Settings.set_model(vals["model"], provider)
            Settings.set_endpoint(vals["endpoint"], provider)
        if self._current_provider:
            Settings.set_provider(self._current_provider)

        Settings.set_max_tokens(self.max_tokens_spin.value())
        Settings.set_confirm_before_execute(self.confirm_check.isChecked())
        Settings.set_show_generated_code(self.show_code_check.isChecked())
        Settings.set_gee_project(self.gee_project_edit.text().strip())
        Settings.set_knowledge_base_dir(self.kb_dir_edit.text().strip())

        # Workspace folder
        ws = self.ws_folder_edit.text().strip()
        if ws:
            try:
                os.makedirs(ws, exist_ok=True)
            except Exception as e:
                QMessageBox.warning(self, "Workspace Warning",
                                    f"Could not create workspace folder:\n{e}")
        Settings.set_workspace_folder(ws)

    def _autosave_workspace(self):
        """Immediately persist the workspace path whenever it changes."""
        ws = self.ws_folder_edit.text().strip()
        if ws:
            try:
                os.makedirs(ws, exist_ok=True)
            except Exception:
                pass
        Settings.set_workspace_folder(ws)
        if ws:
            self.ws_saved_lbl.setText(f"✔ Saved: {ws}")
            self.ws_saved_lbl.setStyleSheet(
                "color: #2E7D32; font-size: 10px; background: transparent;"
            )
        else:
            self.ws_saved_lbl.setText("⚠ Cleared — using system temp")
            self.ws_saved_lbl.setStyleSheet(
                "color: #E65100; font-size: 10px; background: transparent;"
            )

    def _save_settings(self):
        """Top-right Save button — persist everything and show feedback."""
        self._collect_and_persist()
        self.save_btn.setText("✔  Saved!")
        self.save_btn.setStyleSheet(
            "QPushButton { background: #2E7D32; color: white; border: none;"
            " border-radius: 6px; padding: 5px 14px; font-size: 12px; font-weight: bold; }"
        )
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(1800, self._reset_save_btn)

    def _reset_save_btn(self):
        self.save_btn.setText("💾  Save")
        self.save_btn.setStyleSheet(
            "QPushButton#save_header_btn {"
            "  background: #1565C0; color: white; border: none; border-radius: 6px;"
            "  padding: 5px 14px; font-size: 12px; font-weight: bold;"
            "}"
            "QPushButton#save_header_btn:hover { background: #0D47A1; }"
        )

    def _save_and_accept(self):
        """OK button — persist and close."""
        self._collect_and_persist()
        self.accept()

    # ------------------------------------------------------------------
    # BROWSE HELPERS
    # ------------------------------------------------------------------
    def _browse_workspace_folder(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Workspace Folder", self.ws_folder_edit.text()
        )
        if dir_path:
            self.ws_folder_edit.setText(dir_path)
            # Auto-save immediately on browse selection
            self._autosave_workspace()

    def _open_workspace_folder(self):
        ws = self.ws_folder_edit.text().strip()
        if not ws:
            QMessageBox.information(self, "No Folder Set",
                                    "Please set a workspace folder first.")
            return
        if not os.path.exists(ws):
            try:
                os.makedirs(ws, exist_ok=True)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not create folder:\n{e}")
                return
        try:
            os.startfile(ws)
        except Exception:
            import subprocess
            subprocess.Popen(["explorer", ws])

    def _browse_kb_dir(self):
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Knowledge Base Directory", self.kb_dir_edit.text()
        )
        if dir_path:
            self.kb_dir_edit.setText(dir_path)

    # ------------------------------------------------------------------
    # KNOWLEDGE BASE / LESSONS
    # ------------------------------------------------------------------
    def _manage_kb_docs(self):
        from .knowledge_base_manager import KnowledgeBaseManager
        dlg = KnowledgeBaseManager(self)
        dlg.exec()

    def _manage_lessons(self):
        from .lessons_dialog import LessonsDialog
        dlg = LessonsDialog(self)
        dlg.exec()

    def _update_vector_db(self):
        from .vector_db import VectorDB
        import datetime

        kb_dir = self.kb_dir_edit.text().strip()
        if not kb_dir:
            QMessageBox.warning(self, "Warning", "Please select a Knowledge Base directory first.")
            return
        if not os.path.exists(kb_dir):
            try:
                os.makedirs(kb_dir, exist_ok=True)
            except Exception as e:
                QMessageBox.critical(self, "Error",
                                     f"Failed to create Knowledge Base directory:\n{e}")
                return
        try:
            db_path = os.path.join(kb_dir, "vector_db.json")
            db = VectorDB(db_path)
            num_docs = db.build_index(kb_dir)
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            Settings.set_vector_db_last_updated(now_str)
            self.vdb_status_label.setText(f"Last: {now_str}")
            QMessageBox.information(self, "Success",
                                    f"Vector database updated.\nIndexed {num_docs} chunks.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to update vector database:\n{e}")
