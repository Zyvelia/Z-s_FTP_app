"""ShutdownControlsMixin: isolated Z FTP UI behavior, attached to the same main window."""
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


class ShutdownControlsMixin:
    def close_tab_at(self, index):
        tab = self.tabs.widget(index)
        if tab in self.sessions: self.close_tab(tab)
    def close_tab(self, tab):
        state = self.sessions.get(tab)
        if not state: return
        if not state.request_close():
            QMessageBox.information(self, 'Session busy', 'Cancel or finish the operation on this tab before closing it.')
            return
        self.queue.cancel_pending_for(str(id(tab)))
        for cancelled_job in self.queue.jobs:
            if cancelled_job.session_id == str(id(tab)) and cancelled_job.status == 'Cancelled':
                self.record_transfer(cancelled_job, state)
        self.render_queue()
        tab.status.setText('Disconnecting…')
        tab.set_controls()
        # Close in a worker to avoid blocking the interface on network timeouts.
        job = Job(lambda _: state.backend.disconnect())
        self.jobs.append(job)
        def finish(error=None):
            if job in self.jobs: self.jobs.remove(job)
            state.clear()
            self.sessions.pop(tab, None)
            index = self.tabs.indexOf(tab)
            if index >= 0: self.tabs.removeTab(index)
            tab.deleteLater()
            self.update_active()
            if error: self.log(f'Failed to cleanly disconnect {state.host}: {error}')
            else:
                self.log(f'Disconnected {state.host}')
                self.notify('info', 'Disconnected', state.host)
        job.events.done.connect(lambda _: finish())
        job.events.failed.connect(finish)
        self.pool.start(job)
    def _advance_shutdown(self):
        """Close idle connections asynchronously; never destroy active workers."""
        if not self._quit_requested:
            return
        # Cancel queued work before starting disconnects. Running transfers
        # observe state.cancelled and reach their normal completion handlers.
        for tab, state in tuple(self.sessions.items()):
            self.queue.cancel_pending_for(str(id(tab)))
            if not state.busy and not state.closing:
                self.close_tab(tab)
        if not self.sessions and not self.jobs:
            self._quit_timer.stop()
            self.close()
    def _finish_close(self, event):
        if self.launch_pending_updater():
            event.accept()
        else:
            self._tray_force_quit = False
            self._quit_requested = False
            event.ignore()

    def closeEvent(self, event):
        if (not self._tray_force_quit and not self._quit_requested and self._close_to_tray
                and self.hide_to_tray()):
            event.ignore()
            return
        if self.server_panel.active_or_stopping or self.sftp_server_panel.active_or_stopping:
            QMessageBox.information(self, 'Server running',
                                    'Stop the built-in server before quitting Z FTP.')
            self._tray_force_quit = False
            self._pending_updater = None
            event.ignore()
            return
        if self._quit_requested:
            if not self.sessions and not self.jobs:
                self._quit_timer.stop()
                self._finish_close(event)
            else:
                event.ignore()
            return
        busy_sessions = [state for state in self.sessions.values() if state.busy]
        if busy_sessions:
            response = QMessageBox.question(
                self, 'Transfers still running',
                'An FTP/SFTP operation is still running.\n\n'
                'Cancel active operations and quit after they finish stopping safely?\n'
                'Incomplete uploads may remain as .zftp.part files.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if response != QMessageBox.Yes:
                self._tray_force_quit = False
                self._pending_updater = None
                event.ignore()
                return
            for state in busy_sessions:
                state.cancelled = True
        if not self.sessions and not self.jobs:
            self._finish_close(event)
            return
        # Idle tabs disconnect automatically; waiting for a worker stays
        # asynchronous, preserving safe ownership of sockets and Qt signals.
        self._quit_requested = True
        self.statusBar().showMessage('Closing Z FTP: disconnecting servers safely…')
        self._quit_timer.start()
        self._advance_shutdown()
        event.ignore()
