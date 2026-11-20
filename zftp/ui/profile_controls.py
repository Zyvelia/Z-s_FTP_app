"""ProfileControlsMixin: isolated Z FTP UI behavior, attached to the same main window."""
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
from zftp.ui.notifications import NotificationHistory
from zftp.transfers.integrity import verify_transfer
from zftp.services.transfer_history import TransferHistory
from zftp.services.scheduler import ScheduleStore
from zftp.services.workflows import WorkflowStore
from zftp.ui.workflow_dialog import WorkflowDialog
from zftp.transfers.cross_server import copy_file
from zftp.ui.ui_theme import APP_STYLESHEET, status_label
from zftp.services.sync_engine import (Inventory, Change, inventory_local, inventory_remote, plan,
                         safe_local_target, relpath, unique_name)



from zftp.ui.ui_workers import Job, Events
from zftp.ui.remote_tab import RemoteTab


class ProfileControlsMixin:
    def refresh_profiles(self):
        self.profile_select.blockSignals(True)
        self.profile_select.clear()
        self.profile_select.addItem('Choose a saved connection…', None)
        try:
            for p in self.store.list():
                self.profile_select.addItem(('★ ' if p.favorite else '') + p.name, p.id)
        except Exception as exc: QMessageBox.warning(self, 'Saved connections', str(exc))
        self.profile_select.blockSignals(False)
    def load_selected_profile(self):
        profile_id = self.profile_select.currentData()
        if not profile_id: return
        try:
            profile = next(p for p in self.store.list() if p.id == profile_id)
            password = self.store.get_password(profile)
        except Exception as exc:
            QMessageBox.warning(self, 'Credential error', str(exc)); return
        self.protocol_select.setCurrentText(profile.protocol)
        self.host.setText(profile.host)
        self.port.setText(str(profile.port))
        self.user.setText(profile.username)
        self.password.setText(password)
        self.ssh_key.setText(profile.key_path)
        self.passphrase.clear()
    def browse_ssh_key(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Choose SSH private key')
        if filename: self.ssh_key.setText(filename)
    def save_current_profile(self):
        dialog = ProfileDialog(self)
        dialog.name.setText(self.host.text().strip())
        dialog.host.setText(self.host.text())
        dialog.protocol.setCurrentText(self.protocol_select.currentText())
        dialog.port.setText(self.port.text())
        dialog.username.setText(self.user.text())
        dialog.key_path.setText(self.ssh_key.text())
        if dialog.exec() == QDialog.Accepted: self.store_dialog_profile(dialog)
    def store_dialog_profile(self, dialog):
        try:
            profile = dialog.build()
            password = dialog.password.text()
            if profile.save_password and not password and (not dialog.profile or not dialog.profile.save_password):
                raise ValueError('Enter a password to enable secure storage')
            self.store.put(profile, password if password else None)
        except Exception as exc:
            QMessageBox.warning(self, 'Could not save connection', str(exc)); return False
        self.refresh_profiles()
        index = self.profile_select.findData(profile.id)
        if index >= 0: self.profile_select.setCurrentIndex(index)
        return True
    def manage_profiles(self):
        dialog = QDialog(self); dialog.setWindowTitle('Saved connections'); dialog.resize(600, 400)
        layout = QVBoxLayout(dialog)
        listing = QListWidget(); layout.addWidget(listing)
        controls = QHBoxLayout()
        edit = QPushButton('Edit'); delete = QPushButton('Delete')
        choose = QPushButton('Load'); close = QPushButton('Close')
        for w in (edit, delete, choose, close): controls.addWidget(w)
        layout.addLayout(controls)
        def refresh():
            listing.clear()
            try:
                for p in self.store.list():
                    item = QListWidgetItem(('★ ' if p.favorite else '') + f'{p.name} — {p.protocol}://{p.host}:{p.port}')
                    item.setData(Qt.UserRole, p.id); listing.addItem(item)
            except Exception as exc: QMessageBox.warning(dialog, 'Profile error', str(exc))
        def selected():
            item = listing.currentItem()
            return next((p for p in self.store.list() if p.id == item.data(Qt.UserRole)), None) if item else None
        def do_edit():
            p = selected()
            if p:
                editor = ProfileDialog(dialog, p)
                if editor.exec() == QDialog.Accepted and self.store_dialog_profile(editor): refresh()
        def do_delete():
            p = selected()
            if p and QMessageBox.question(dialog, 'Delete saved connection', f'Delete {p.name}?') == QMessageBox.Yes:
                try: self.store.delete(p.id)
                except Exception as exc: QMessageBox.warning(dialog, 'Delete failed', str(exc)); return
                self.refresh_profiles(); refresh()
        def do_choose():
            p = selected()
            if not p: return
            self.refresh_profiles()
            index = self.profile_select.findData(p.id)
            if index >= 0: self.profile_select.setCurrentIndex(index)
            dialog.accept()
        edit.clicked.connect(do_edit); delete.clicked.connect(do_delete)
        choose.clicked.connect(do_choose); close.clicked.connect(dialog.reject)
        listing.itemDoubleClicked.connect(lambda _: do_choose())
        refresh(); dialog.exec()
