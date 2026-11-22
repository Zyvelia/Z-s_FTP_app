"""Z FTP v2.1.5 — tray controls, background running and compact transfer controls and graceful automatic disconnect on app exit."""
from __future__ import annotations

import sys
import os
import time
import posixpath
from pathlib import Path
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt, QTimer, QFile, QSize
from PySide6.QtGui import QShortcut, QKeySequence, QCursor, QIcon, QColor
from zftp.services.directory_performance import scan_directory, batches, RenderGate, UpdateThrottle
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget, QSizePolicy, QListWidget, QListWidgetItem, QSpinBox, QCheckBox, QPlainTextEdit,
    QFrame, QGridLayout, QSystemTrayIcon, QStyle, QToolButton, QMenu, QFormLayout,
)
from zftp.protocols.ftp_engine import FTPBackend, clean_remote_name, parent_remote
from zftp.services.remote_cache import RemoteDirectoryCache
from zftp.protocols.sftp_engine import SFTPBackend, UntrustedHostKey
from zftp.services.profiles import ProfileStore
from zftp.ui.profile_dialog import ProfileDialog
from zftp.services.sessions import ConnectionSession
from zftp.transfers.transfer_queue import TransferQueue, Transfer
from zftp.ui.transfer_tables import TransferTable
from zftp.transfers.reliable_transfer import TransferSettings, download as resilient_download, upload as resilient_upload, with_retries
from zftp.transfers.ps3_performance import benchmark_ftp_upload, PRESETS
from zftp.transfers.eta_estimator import ETAEstimator, format_eta
from zftp.services.remote_management import (validate_name, read_editor_snapshot, safe_save_editor, find_remote, RemoteConflictError)
from zftp.ui.server_ui import ServerPanel
from zftp.ui.sftp_server_ui import SFTPServerPanel
from zftp.transfers.integrity import verify_transfer
from zftp.services.transfer_history import TransferHistory
from zftp.services.workflows import WorkflowStore
from zftp.ui.workflow_dialog import WorkflowDialog
from zftp.ui.ui_theme import APP_STYLESHEET, status_label
from zftp.services.sync_engine import (Inventory, Change, inventory_local, inventory_remote, plan,
                         safe_local_target, relpath, unique_name)



def create_file_table(side="local"):
    table = TransferTable(side)
    table.setHorizontalHeaderLabels(['Name', 'Type', 'Size'])
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSelectionMode(QTableWidget.ExtendedSelection)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    return table

class RemoteTab(QWidget):
    """One remote browser and its connection lifecycle."""
    def __init__(self, state: ConnectionSession, parent=None):
        super().__init__(parent)
        self.state = state
        self._render_gate = RenderGate()
        self.directory_cache = RemoteDirectoryCache()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 6, 7, 6)
        layout.setSpacing(5)
        self.path_label = QLabel('Connecting…'); self.path_label.setObjectName('sectionHeading'); self.path_label.setWordWrap(True)
        actions = QHBoxLayout()
        actions.setSpacing(5)
        self.back = QPushButton('←')
        self.back.setToolTip('Back to previous remote folder')
        self.forward = QPushButton('→')
        self.forward.setToolTip('Forward to next remote folder')
        self.nav_history = []
        self.nav_index = -1
        self.up = QPushButton('↑ Up')
        self.refresh = QPushButton('Refresh')
        self.mkdir = QPushButton('New folder')
        self.delete = QPushButton('Delete…')
        self.download = QPushButton('↓ Download')
        self.download.setObjectName('primaryAction')
        self.rename_btn = QPushButton('Rename')
        self.edit_btn = QPushButton('Edit text / SFO')
        self.search_btn = QPushButton('Search')
        self.permissions_btn = QPushButton('Permissions')
        self.disconnect = QPushButton('Disconnect')
        self.manage_menu = QMenu(self)
        for button in (self.mkdir, self.rename_btn, self.edit_btn, self.search_btn,
                       self.permissions_btn, self.delete):
            action = self.manage_menu.addAction(button.text())
            action.triggered.connect(button.click)
            button.changed_state_action = action
        self.more_actions = QToolButton()
        self.more_actions.setText('File actions  ▾')
        self.more_actions.setPopupMode(QToolButton.InstantPopup)
        self.more_actions.setMenu(self.manage_menu)
        for button in (self.back, self.forward, self.up, self.refresh, self.download):
            actions.addWidget(button)
        actions.addWidget(self.more_actions)
        actions.addStretch(1)
        actions.addWidget(self.disconnect)
        self.table = create_file_table("remote")
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(29)
        self.table.setMinimumHeight(220)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.progress = QProgressBar(); self.progress.setRange(0, 100)
        self.progress.setMaximumHeight(18)
        self.cancel = QPushButton('Cancel transfer'); self.cancel.setEnabled(False)
        progress_row = QHBoxLayout()
        self.status = QLabel('Connecting…')
        self.status.setMinimumWidth(75)
        self.status.setMaximumWidth(175)
        progress_row.setSpacing(6)
        progress_row.addWidget(self.status)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.cancel)
        layout.addWidget(self.path_label)
        layout.addLayout(actions)
        layout.addWidget(self.table, 1)
        layout.addLayout(progress_row)
        self.set_controls()

    def set_controls(self):
        active = self.state.connected and not self.state.busy and not self.state.closing
        for button in (self.up, self.refresh, self.mkdir, self.delete, self.download, self.rename_btn, self.edit_btn, self.search_btn, self.permissions_btn):
            button.setEnabled(active)
        self.back.setEnabled(active and self.nav_index > 0)
        self.forward.setEnabled(active and self.nav_index + 1 < len(self.nav_history))
        for button in (self.rename_btn, self.edit_btn, self.permissions_btn):
            button.setEnabled(active and not self.state.downloads)
        self.disconnect.setEnabled(not self.state.active and not self.state.closing)
        self.more_actions.setEnabled(active)
        for button in (self.mkdir, self.rename_btn, self.edit_btn, self.search_btn, self.permissions_btn, self.delete):
            button.changed_state_action.setEnabled(active)
        self.cancel.setText('Cancel downloads' if self.state.downloads else 'Cancel transfer')
        self.cancel.setEnabled(bool(self.state.downloads) or
                               (self.state.busy and self.cancel.property('transfer_active') is True))

    def show_entries(self):
        self.path_label.setText('Remote: ' + self.state.remote_path)
        generation = self._render_gate.new()
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        self.table.remote_directory = self.state.remote_path
        entries = list(self.state.entries)
        self.table.setRowCount(len(entries))
        iterator = iter(enumerate(batches(entries)))
        def append_batch():
            if not self._render_gate.current(generation):
                self.table.setUpdatesEnabled(True)
                return
            try:
                batch_num, batch = next(iterator)
            except StopIteration:
                self.table.setUpdatesEnabled(True)
                return
            for offset, entry in enumerate(batch):
                row = batch_num * 200 + offset
                for col, value in enumerate((entry.name, 'Folder' if entry.is_dir else 'File',
                                             '' if entry.size is None or entry.is_dir else str(entry.size))):
                    self.table.setItem(row, col, QTableWidgetItem(str(value)))
            self.table.setUpdatesEnabled(True)
            QTimer.singleShot(0, append_batch)
        QTimer.singleShot(0, append_batch)

    def selected(self):
        row = self.table.currentRow()
        return self.state.entries[row] if 0 <= row < len(self.state.entries) else None
