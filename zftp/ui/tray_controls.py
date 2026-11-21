"""TrayControlsMixin: isolated Z FTP UI behavior, attached to the same main window."""
from __future__ import annotations

import sys
import subprocess
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



from zftp.ui.ui_workers import Job, Events
from zftp.ui.remote_tab import RemoteTab


class TrayControlsMixin:
    def setup_tray_controls(self):
        """Create an opt-in background mode; the default X still means Quit."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self)
        icon_path = Path(__file__).resolve().parents[2] / 'assets' / 'zftp_icon.png'
        self.tray.setIcon(QIcon(str(icon_path)) if icon_path.is_file() else
                          self.style().standardIcon(QStyle.SP_DriveNetIcon))
        menu = QMenu(self)
        self._tray_status_action = menu.addAction('Offline · 0 active transfers')
        self._tray_status_action.setEnabled(False)
        menu.addSeparator()
        self._tray_visibility_action = menu.addAction('Hide Z FTP')
        self._tray_visibility_action.triggered.connect(self.toggle_tray_visibility)
        transfers = menu.addAction('Open Transfers')
        transfers.triggered.connect(self.show_tray_transfers)
        activity = menu.addAction('Open Activity')
        activity.triggered.connect(self.show_tray_activity)
        menu.addSeparator()
        cancel_queued = menu.addAction('Cancel queued transfers…')
        cancel_queued.triggered.connect(self.tray_cancel_queued)
        cancel_active = menu.addAction('Cancel active transfers…')
        cancel_active.triggered.connect(self.tray_cancel_active)
        menu.addSeparator()
        self._tray_close_action = menu.addAction('Close button hides to tray')
        self._tray_close_action.setCheckable(True)
        self._tray_close_action.setChecked(False)
        self._tray_close_action.toggled.connect(self.set_close_to_tray)
        self._tray_notifications_action = menu.addAction('Desktop notifications')
        self._tray_notifications_action.setCheckable(True)
        self._tray_notifications_action.setChecked(self.notifications_enabled.isChecked())
        self._tray_notifications_action.toggled.connect(self.notifications_enabled.setChecked)
        self.notifications_enabled.toggled.connect(self._tray_notifications_action.setChecked)
        menu.addSeparator()
        update_action = menu.addAction('Check for updates')
        update_action.triggered.connect(self.update_from_tray)
        quit_action = menu.addAction('Quit Z FTP')
        quit_action.triggered.connect(self.quit_from_tray)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()
        # A hidden main window still retains its sockets, timers, jobs, and servers.
        self._tray_update_timer = QTimer(self)
        self._tray_update_timer.setInterval(1500)
        self._tray_update_timer.timeout.connect(self.refresh_tray_status)
        self._tray_update_timer.start()
        self.refresh_tray_status()
    def set_close_to_tray(self, enabled):
        self._close_to_tray = bool(enabled) and self.tray is not None and self.tray.isVisible()
    def refresh_tray_status(self):
        if self.tray is None:
            return
        connected = sum(bool(tab.state.connected) for tab in self.sessions)
        running = [job for job in self.queue.jobs if job.status == 'Running']
        queued = sum(job.status == 'Queued' for job in self.queue.jobs)
        speed = sum(max(0., job.speed_bps) for job in running)
        status = (f'{connected} connected · {len(running)} active · '
                  f'{queued} queued · {speed / 1024:.1f} KiB/s')
        if self._tray_status_action is not None:
            self._tray_status_action.setText(status)
        self.tray.setToolTip('Z FTP\n' + status)
        if self._tray_visibility_action is not None:
            self._tray_visibility_action.setText('Hide Z FTP' if self.isVisible() else 'Show Z FTP')
    def hide_to_tray(self):
        if self.tray is None or not self.tray.isVisible():
            return False
        self.hide()
        self.refresh_tray_status()
        return True
    def show_from_tray(self):
        self.showNormal() if self.isMinimized() else self.show()
        self.raise_()
        self.activateWindow()
        self.refresh_tray_status()
    def toggle_tray_visibility(self):
        if self.isVisible():
            self.hide_to_tray()
        else:
            self.show_from_tray()
    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.toggle_tray_visibility()
    def show_tray_transfers(self):
        self.show_from_tray()
        self.main_sections.setCurrentIndex(0)
        self.navigate_sidebar('transfers')
    def show_tray_activity(self):
        self.show_from_tray()
        self.main_sections.setCurrentIndex(0)
        self.activity_toggle.setChecked(True)
        self.bottom_tabs.setCurrentIndex(0)
    def tray_cancel_queued(self):
        queued = [job for job in self.queue.jobs if job.status == 'Queued']
        if not queued:
            return
        if QMessageBox.question(self, 'Cancel queued transfers',
                f'Cancel {len(queued)} queued transfer(s)? Running jobs will continue.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        for job in queued:
            self.queue.cancel(job.id)
            tab = next((tab for tab in self.sessions if str(id(tab)) == job.session_id), None)
            if tab:
                self.record_transfer(job, tab.state)
        self.render_queue()
        self.refresh_tray_status()
    def tray_cancel_active(self):
        running = [job for job in self.queue.jobs if job.status == 'Running']
        if not running:
            return
        if QMessageBox.question(self, 'Cancel active transfers',
                f'Request cancellation of {len(running)} active transfer(s)?\n\n'
                'Partial uploads may remain on the server for safe resumption.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        for job in running:
            tab = next((tab for tab in self.sessions if str(id(tab)) == job.session_id), None)
            if tab:
                tab.state.cancelled = True
        self.statusBar().showMessage('Cancellation requested; waiting for active operations to stop safely…')
    def update_from_tray(self):
        if self._quit_requested:
            return
        install_dir = Path(sys.executable).parent
        updater = install_dir / 'Update-ZFTP.ps1'
        if (sys.platform != 'win32' or not getattr(sys, 'frozen', False)
                or not updater.is_file() or not (install_dir / 'zftp-version.ini').is_file()):
            self.show_from_tray()
            QMessageBox.information(self, 'Updater unavailable',
                                    'Install Z FTP with the Windows installer to use tray updates.')
            return
        if (any(state.busy for state in self.sessions.values())
                or any(job.status in ('Queued', 'Running') for job in self.queue.jobs)
                or self.server_panel.active_or_stopping or self.sftp_server_panel.active_or_stopping):
            self.show_from_tray()
            QMessageBox.information(self, 'Z FTP is busy',
                                    'Finish queued and active transfers and stop any built-in servers before updating.')
            return
        self._pending_updater = updater
        self.quit_from_tray()

    def launch_pending_updater(self):
        updater, self._pending_updater = self._pending_updater, None
        if updater is None:
            return True
        system_root = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        powershell = system_root / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
        try:
            subprocess.Popen([str(powershell), '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                              '-File', str(updater), '-WaitForProcessId', str(os.getpid()), '-PauseOnExit'],
                             cwd=updater.parent, creationflags=subprocess.CREATE_NEW_CONSOLE)
        except OSError as exc:
            QMessageBox.warning(self, 'Could not start updater', str(exc))
            return False
        return True

    def quit_from_tray(self):
        # Explicit Quit always means the existing graceful disconnect procedure.
        self._tray_force_quit = True
        self.show_from_tray()
        self.close()
