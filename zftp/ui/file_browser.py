"""FileBrowserMixin: isolated Z FTP UI behavior, attached to the same main window."""
from __future__ import annotations

import hashlib
import sqlite3
import sys
import os
import time
import posixpath
from pathlib import Path
from ftplib import Error as FTPError
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt, QTimer, QFile, QSize, QUrl
from PySide6.QtGui import QShortcut, QKeySequence, QCursor, QIcon, QColor, QDesktopServices
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
from zftp.transfers.transfer_queue import FolderProgress, TransferQueue, Transfer
from zftp.ui.transfer_tables import TransferTable
from zftp.transfers.reliable_transfer import TransferSettings, download as resilient_download, upload as resilient_upload, with_retries
from zftp.transfers.eta_estimator import ETAEstimator, format_eta
from zftp.services.remote_management import (
    RemoteConflictError, RemoteDirectoryRestoreError, RemoteSearch, delete_remote_tree, find_remote,
    validate_name,
)
from zftp.ui.server_ui import ServerPanel
from zftp.ui.sftp_server_ui import SFTPServerPanel
from zftp.transfers.integrity import verify_transfer
from zftp.services.transfer_history import TransferHistory
from zftp.services.workflows import WorkflowStore
from zftp.ui.workflow_dialog import WorkflowDialog
from zftp.ui.ui_theme import APP_STYLESHEET, status_label
from zftp.services.sync_engine import (Inventory, Change, inventory_local, inventory_remote, plan,
                         safe_local_target, relpath, unique_name)



from zftp.services.file_preview import (
    MEDIA_LIMIT, OPAQUE_SUFFIXES, SQLITE_HEADER, can_preview, preview_limit, read_local_copy, save_local_copy, sqlite_sidecars,
)
from zftp.services.format_preview import SPECIAL_SUFFIXES, ZIP_MAGIC, archive_kind
from zftp.ui.external_editor import ExternalEditorDialog, find_vscode
from zftp.ui.file_preview import FilePreviewDialog
from zftp.ui.format_preview import FormatPreviewMixin
from zftp.ui.ui_workers import Job, Events
from zftp.ui.remote_tab import RemoteTab
from zftp.services.param_sfo import ParamSfo
from zftp.services.remote_management import MAX_EDITOR_BYTES, safe_save_remote_bytes
from zftp.ui.sfo_editor import SfoEditor
from zftp.ui.ps3_file_tools import Ps3FileToolsMixin


class FileBrowserMixin(Ps3FileToolsMixin, FormatPreviewMixin):
    def set_local_path(self, path, *, create=False):
        path = Path(path)
        generation = self._local_render_gate.new()
        self.update_local_caption('Loading local: ' + str(path))
        def scan(_):
            if create:
                path.mkdir(parents=True, exist_ok=True)
            return scan_directory(path)

        job = Job(scan)
        self.jobs.append(job)
        def finish():
            if job in self.jobs:
                self.jobs.remove(job)
        def failed(error):
            finish()
            if self._local_render_gate.current(generation):
                self.update_local_caption('Local: ' + str(self.local_path))
                QMessageBox.warning(self, 'Local folder', str(error))
        def ready(entries):
            finish()
            if not self._local_render_gate.current(generation):
                return
            self.local_path = path
            self.local_table.local_directory = path
            self.update_local_caption('Local: ' + str(path))
            table = self.local_table
            table.setUpdatesEnabled(False)
            table.setSortingEnabled(False)
            table.setRowCount(len(entries))
            batches_iter = iter(enumerate(batches(entries)))
            def render_chunk():
                if not self._local_render_gate.current(generation):
                    table.setUpdatesEnabled(True)
                    return
                try:
                    batch_number, chunk = next(batches_iter)
                except StopIteration:
                    table.setUpdatesEnabled(True)
                    return
                for offset, item in enumerate(chunk):
                    row = batch_number * 200 + offset
                    for column, value in enumerate((item.name, 'Folder' if item.is_dir else 'File',
                                                    '' if item.is_dir else self.display_size(item.size))):
                        table.setItem(row, column, QTableWidgetItem(str(value)))
                table.setUpdatesEnabled(True)
                QTimer.singleShot(0, render_chunk)
            QTimer.singleShot(0, render_chunk)
        job.events.done.connect(ready)
        job.events.failed.connect(failed)
        self.pool.start(job)
    def focus_local_path(self):
        value, ok = QInputDialog.getText(self, 'Local directory', 'Local path:', text=str(self.local_path))
        if ok and value.strip():
            self.set_local_path(Path(value.strip()).expanduser())
    def refresh_current_remote(self):
        tab = self.current_tab()
        if tab and tab.state.connected and not tab.state.busy and not tab.state.closing:
            self.refresh(tab)
    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Choose local folder', str(self.local_path))
        if folder: self.set_local_path(Path(folder))
    def open_local(self, row, _column):
        item = self.local_table.item(row, 0)
        if item is None:
            return
        path = self.local_path / item.text()
        if path.is_dir():
            self.set_local_path(path)
        elif path.suffix.lower() == '.txt' and path.is_file():
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                QMessageBox.warning(self, 'Could not open text file', str(path))
        elif can_preview(path.name) and path.is_file() and not path.is_symlink():
            suffix = path.suffix.lower()
            if suffix == '.sfo':
                self.edit_local_sfo(path)
            elif (archive_kind(path.name) or suffix in SPECIAL_SUFFIXES
                  or (suffix in OPAQUE_SUFFIXES and path.stat().st_size > MEDIA_LIMIT)):
                self.preview_local_format(path)
            else:
                self.preview_local_file(path)

    def revised_sfo(self, document, path):
        dialog = SfoEditor(document, str(path), self)
        accepted = dialog.exec() == QDialog.Accepted
        changes = dialog.changes
        dialog.deleteLater()
        return document.edited(changes) if accepted else document.raw

    def edit_local_sfo(self, path):
        def opened(document):
            revised = self.revised_sfo(document, path)
            if revised == document.raw:
                return
            if self.queue.protects_local_path(path):
                QMessageBox.information(self, 'File in transfer queue', 'Finish or cancel the transfer before saving.')
                return
            self.run_local_file_job(
                lambda _: save_local_copy(path, document.raw, revised, limit=MAX_EDITOR_BYTES),
                lambda backup: self.notify('success', 'PARAM.SFO saved', f'Original kept as {backup}'),
            )

        self.run_local_file_job(lambda _: ParamSfo(read_local_copy(path, MAX_EDITOR_BYTES)), opened)

    def preview_local_file(self, path):
        limit = preview_limit(path.name)

        def read(_):
            return read_local_copy(path, limit)

        def opened(data):
            is_sqlite = data.startswith(SQLITE_HEADER)
            in_use = is_sqlite and any(path.with_name(name).exists() for name in sqlite_sidecars(path.name))

            def save(dialog, revised):
                if self.queue.protects_local_path(path):
                    QMessageBox.information(self, 'File in transfer queue', 'Finish or cancel the transfer before saving.')
                    return False
                return self.run_local_file_job(
                    lambda _: save_local_copy(path, data, revised, limit=limit, sqlite=is_sqlite),
                    lambda backup: self.finish_file_save(dialog, backup),
                    dialog.save_failed,
                )

            self.open_file_preview(path.name, data, save, sqlite_in_use=in_use)

        self.run_local_file_job(read, opened)

    def run_local_file_job(self, operation, after, on_error=None):
        if self._quit_requested:
            return False
        job = Job(operation)
        self.jobs.append(job)

        def done(result=None, error=None):
            self.jobs.remove(job)
            if error is not None:
                if on_error:
                    on_error()
                if not self._quit_requested:
                    QMessageBox.warning(self, 'File operation failed', str(error))
            elif not self._quit_requested:
                after(result)

        job.events.done.connect(done)
        job.events.failed.connect(lambda error: done(error=error))
        self.pool.start(job)
        return True

    def finish_file_save(self, dialog, backup):
        dialog.saved()
        self.notify('success', 'File saved', f'Original kept as {backup}')

    def open_file_preview(self, name, data, save, *, sqlite_in_use=False):
        if data.startswith(ZIP_MAGIC):
            self.preview_cached_archive(name, data)
            return
        if self.show_format_preview(name, data, len(data)):
            return
        try:
            dialog = FilePreviewDialog(name, data, save, sqlite_in_use=sqlite_in_use, parent=self)
        except (ValueError, AttributeError, sqlite3.Error) as error:
            QMessageBox.warning(self, 'Could not open file', str(error))
            return
        dialog.show()

    def selected_local(self):
        row = self.local_table.currentRow()
        item = self.local_table.item(row, 0) if row >= 0 else None
        return self.local_path / item.text() if item else None
    @staticmethod
    def _context_row(table, point):
        """Right-click operates on the row under the cursor, not an old selection."""
        row = table.rowAt(point.y())
        if row < 0:
            return -1
        if not table.selectionModel().isRowSelected(row, table.rootIndex()):
            table.clearSelection()
        table.selectRow(row)
        table.setCurrentCell(row, 0)
        return row
    def local_context_menu(self, point):
        row = self._context_row(self.local_table, point)
        menu = QMenu(self)
        if row >= 0:
            path = self.selected_local()
            if path is None:
                return
            open_action = menu.addAction('Open folder' if path.is_dir() else 'Open / view with default app')
            upload_action = menu.addAction('Upload selected to server')
            menu.addSeparator()
            rename_action = menu.addAction('Rename…')
            delete_action = menu.addAction('Move to Recycle Bin…')
            menu.addSeparator()
            copy_action = menu.addAction('Copy full path')
        menu.addAction('New folder…', self.create_local_folder)
        menu.addAction('Refresh', lambda: self.set_local_path(self.local_path))
        picked = menu.exec(self.local_table.viewport().mapToGlobal(point))
        if row < 0 or picked is None:
            return
        if picked == open_action:
            if path.is_dir(): self.set_local_path(path)
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        elif picked == upload_action: self.upload()
        elif picked == rename_action: self.rename_local(path)
        elif picked == delete_action: self.delete_local(path)
        elif picked == copy_action: QApplication.clipboard().setText(str(path))
    def create_local_folder(self):
        name, ok = QInputDialog.getText(self, 'New local folder', 'Folder name:')
        if not ok: return
        try:
            from zftp.services.remote_management import validate_name
            target = self.local_path / validate_name(name)
            target.mkdir(exist_ok=False)
            self.set_local_path(self.local_path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Create folder failed', str(exc))
    def rename_local(self, source):
        if source.is_symlink():
            QMessageBox.warning(self, 'Rename blocked', 'Symbolic links are not supported here.'); return
        name, ok = QInputDialog.getText(self, 'Rename local item', 'New name:', text=source.name)
        if not ok or name == source.name: return
        try:
            from zftp.services.remote_management import validate_name
            target = source.parent / validate_name(name)
            if target.exists() or target.is_symlink(): raise FileExistsError(target)
            source.rename(target)
            self.set_local_path(self.local_path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Rename failed', str(exc))
    def delete_local(self, source):
        if source.is_symlink():
            QMessageBox.warning(self, 'Delete blocked', 'Symbolic links are not supported here.'); return
        if self.queue.protects_local_path(source):
            QMessageBox.warning(self, 'File in transfer queue',
                                'Finish or cancel its queued transfer before deleting this item.')
            return
        if QMessageBox.question(self, 'Move to Recycle Bin',
                f'Move this local item to the Recycle Bin?\n\n{source}\n\n'
                'This may include an entire folder and its contents.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if self.queue.protects_local_path(source):
            QMessageBox.warning(self, 'File in transfer queue',
                                'Finish or cancel its queued transfer before deleting this item.')
            return
        if not QFile.moveToTrash(str(source)):
            QMessageBox.warning(self, 'Recycle Bin unavailable',
                'Windows could not move the item to the Recycle Bin. Nothing was permanently deleted.')
            return
        self.set_local_path(self.local_path)
    def remote_context_menu(self, tab, point):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.closing:
            return
        row = self._context_row(tab.table, point)
        entry = tab.selected() if row >= 0 else None
        menu = QMenu(self)
        if state.busy:
            if entry and not entry.is_link:
                download_only = menu.addAction('Queue download…')
                if menu.exec(tab.table.viewport().mapToGlobal(point)) == download_only:
                    self.download(tab)
            return
        if entry is not None:
            open_action = menu.addAction('Open folder') if entry.is_dir else None
            download_action = menu.addAction('Download') if not entry.is_dir else menu.addAction('Download folder')
            menu.addSeparator()
            rename_action = menu.addAction('Rename…')
            edit_label = 'View / edit PARAM.SFO…' if entry.name.lower().endswith('.sfo') else 'Edit text…'
            edit_action = menu.addAction(edit_label) if not entry.is_dir else None
            external_action = None
            suffix = Path(entry.name).suffix.lower()
            if (not entry.is_dir and not entry.is_link and not archive_kind(entry.name)
                    and suffix not in SPECIAL_SUFFIXES and suffix not in ('.db', '.sfo')):
                external_action = menu.addAction('Edit in VS Code…')
                if entry.size is not None and entry.size > MAX_EDITOR_BYTES:
                    external_action.setEnabled(False)
                    external_action.setToolTip('External text editing supports files up to 1 MiB.')
            permissions_action = menu.addAction('Permissions…')
            delete_action = menu.addAction('Delete folder and contents…' if entry.is_dir else 'Delete permanently…')
            if state.downloads:
                for action in (rename_action, edit_action, permissions_action):
                    if action:
                        action.setEnabled(False)
            if entry.is_link:
                for action in (download_action, rename_action, edit_action, permissions_action, delete_action):
                    if action: action.setEnabled(False)
            menu.addSeparator()
        if not state.downloads:
            self.remote_ps3_tools(menu, tab, entry)
        new_action = menu.addAction('New folder…')
        refresh_action = menu.addAction('Refresh')
        picked = menu.exec(tab.table.viewport().mapToGlobal(point))
        if picked is None: return
        if picked == new_action: self.mkdir(tab)
        elif picked == refresh_action: self.refresh(tab)
        elif entry is not None:
            if picked == open_action: self.open_remote(tab, row)
            elif picked == download_action: self.download(tab)
            elif picked == rename_action: self.rename_remote(tab)
            elif picked == edit_action: self.edit_remote(tab)
            elif picked == external_action: self.edit_remote_in_vscode(tab, entry)
            elif picked == permissions_action: self.permissions_remote(tab)
            elif picked == delete_action: self.delete_remote_entry(tab)
    def connect_new(self):
        host = self.host.text().strip()
        if not host:
            QMessageBox.warning(self, 'Host required', 'Enter a hostname or IP address.'); return
        try:
            port = int(self.port.text())
            if not 1 <= port <= 65535: raise ValueError()
        except ValueError:
            QMessageBox.warning(self, 'Port', 'Use a port from 1 to 65535.'); return
        protocol = self.protocol_select.currentText()
        username, password = self.user.text().strip(), self.password.text()
        key_filename = self.ssh_key.text().strip() if protocol == 'SFTP' else ''
        passphrase = self.passphrase.text() if protocol == 'SFTP' else ''
        if key_filename and not Path(key_filename).is_file():
            QMessageBox.warning(self, 'SSH private key', 'Key file does not exist.'); return
        backend = SFTPBackend() if protocol == 'SFTP' else FTPBackend()
        state = ConnectionSession(backend, protocol, host, port, username,
                                  display_name=self.connection_name.text().strip(),
                                  bandwidth_kib=self.connection_limit.value())
        profile = self.loaded_profile
        if profile and (profile.host, profile.port, profile.protocol, profile.username) != (host, port, protocol, username):
            profile = None
        tab = RemoteTab(state)
        tab.table.remote_session_id = str(id(tab))
        tab.table.filesDropped.connect(lambda paths, t=tab: self.enqueue_local_items(t, paths))
        self.sessions[tab] = state
        self.update_transfer_limits()
        self._connection_values.update((host, username))
        tab.display_text = self.private_text
        tab.display_size = self.display_size
        index = self.tabs.addTab(tab, f'{protocol} · Server')
        self.update_private_tab(tab)
        self.tabs.setCurrentIndex(index)
        tab.table.remote_directory = state.remote_path
        tab.up.clicked.connect(lambda: self.navigate(tab, parent_remote(state.remote_path)))
        tab.back.clicked.connect(lambda: self.navigate_history(tab, -1))
        tab.forward.clicked.connect(lambda: self.navigate_history(tab, 1))
        tab.refresh.clicked.connect(lambda: self.refresh(tab))
        tab.mkdir.clicked.connect(lambda: self.mkdir(tab))
        tab.delete.clicked.connect(lambda: self.delete(tab))
        tab.rename_btn.clicked.connect(lambda: self.rename_remote(tab))
        tab.edit_btn.clicked.connect(lambda: self.edit_remote(tab))
        tab.search_btn.clicked.connect(lambda: self.search_remote(tab))
        tab.permissions_btn.clicked.connect(lambda: self.permissions_remote(tab))
        tab.download.clicked.connect(lambda: self.download(tab))
        tab.disconnect.clicked.connect(lambda: self.close_tab(tab))
        def cancel_tab_transfer():
            if state.busy and tab.cancel.property('transfer_active'):
                state.cancelled = True
            else:
                state.cancel_downloads()
        tab.cancel.clicked.connect(cancel_tab_transfer)
        tab.table.cellDoubleClicked.connect(lambda row, col: self.open_remote(tab, row))
        tab.table.setContextMenuPolicy(Qt.CustomContextMenu)
        tab.table.customContextMenuRequested.connect(lambda point, t=tab: self.remote_context_menu(t, point))
        self.password.clear(); self.passphrase.clear()
        # Login secrets remain only in this tab's in-memory reconnect closure.
        # Never serialized to the queue, logs, or saved-profile JSON.
        def login(target):
            if protocol == 'SFTP':
                return target.connect(host, port, username, password or None,
                                      key_filename=key_filename or None, passphrase=passphrase or None)
            return target.connect(host, port, username, password, tls=(protocol == 'FTPS'))

        def make_download_connection():
            worker = backend.new_session() if protocol == 'SFTP' else FTPBackend()
            return worker, lambda: login(worker)

        def connection(_):
            cwd = login(backend)
            if profile and profile.remote_path:
                try:
                    backend.cwd(profile.remote_path)
                except (OSError, RuntimeError, FTPError):
                    pass
                else:
                    cwd = backend.pwd()
            return cwd, backend.list_dir()
        def after(result):
            state.connected = True
            state.remote_path, state.entries = result
            tab.directory_cache.put(state.remote_path, state.entries)
            tab.nav_history = [state.remote_path]
            tab.nav_index = 0
            tab.show_entries()
            if self.connection_setup.isVisible():
                self.toggle_connection_setup()
            if profile and profile.local_path and Path(profile.local_path).is_dir():
                self.set_local_path(Path(profile.local_path))
            self.restore_queue(tab)
            self.notify('success', 'Connected', f'{protocol} · {host}:{port}', category='connection')
        def on_error(exc):
            if isinstance(exc, UntrustedHostKey):
                reply = QMessageBox.question(self, 'Verify SSH server key',
                    f'Server: {self.private_text(f"{host}:{port}")}\nKey: {exc.key_type}\nFingerprint: {exc.fingerprint}\n\n'
                    'Verify this fingerprint through a trusted channel. Trust for this session only?',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if reply == QMessageBox.Yes:
                    backend.trust_host_once(exc.host, exc.key_b64)
                    self.run_session_job(tab, 'Connecting with verified SSH key', connection, after, on_error)
                else:
                    self.close_tab(tab)
                return True
            return False
        tab.reconnect_fn = lambda: connection(None)[0]
        tab.make_download_connection = make_download_connection
        self.run_session_job(tab, f'Connecting to {host}', connection, after, on_error)
    def run_session_job(self, tab, label, fn, after=None, on_error=None, *, transfer=False, progress_callback=None):
        state = self.sessions.get(tab)
        if not state or state.busy or state.closing: return False
        token = state.begin()
        tab.status.setText(self.private_text(label))
        tab.cancel.setProperty('transfer_active', transfer)
        if transfer: tab.progress.setRange(0, 100); tab.progress.setValue(0)
        tab.set_controls(); self.update_active()
        job = Job(fn); self.jobs.append(job)
        if transfer:
            def report(count, total):
                if tab not in self.sessions or state.generation != token: return
                if isinstance(count, FolderProgress):
                    if progress_callback:
                        progress_callback(count, total)
                    tab.status.setText(count.status)
                    if count.bytes_total:
                        tab.progress.setRange(0, 100)
                        tab.progress.setValue(min(100, (100 * count.bytes_done) // count.bytes_total))
                    return
                if isinstance(count, str) and count.startswith('@stage:'):
                    if progress_callback: progress_callback(count, total)
                    tab.status.setText(count[7:] + '…')
                    tab.progress.setRange(0, 100)
                    return
                if progress_callback: progress_callback(count, total)
                if not total: tab.progress.setRange(0, 0)
                else:
                    tab.progress.setRange(0, 100)
                    tab.progress.setValue(min(100, int(count * 100 / total)))
            job.events.progress.connect(report)
        def finish():
            state.complete(token)
            tab.cancel.setProperty('transfer_active', False)
            tab.set_controls()
            self.update_active()
            if job in self.jobs: self.jobs.remove(job)
            QTimer.singleShot(0, lambda: self.pump_queue(tab))
        def succeeded(result):
            finish()
            tab.status.setText('Ready')
            if after: after(result)
            tab.set_controls(); self.update_active()
        def failed(exc):
            finish()
            summary = ('The server could not be reached or sign-in failed.'
                       if self.streamer_enabled and label.startswith('Connecting') else str(exc))
            tab.status.setText(self.private_text('Failed: ' + summary))
            self.notify('error', 'Operation failed', f'{state.host} · {label}: {summary}')
            if on_error and on_error(exc): return
            QMessageBox.warning(self, 'Z FTP operation failed',
                                    'See Activity for the masked error.' if self.streamer_enabled else str(exc))
            # Interrupted data connections can invalidate the protocol session.
            if transfer:
                self.close_tab(tab)
            elif not state.connected:
                self.close_tab(tab)
        job.events.done.connect(succeeded)
        job.events.failed.connect(failed)
        self.pool.start(job)
        return True
    def open_remote(self, tab, row):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.busy or state.closing:
            return
        if not 0 <= row < len(state.entries):
            return
        entry = state.entries[row]
        if entry.is_dir:
            self.navigate(tab, entry.name)
        elif not entry.is_link and can_preview(entry.name):
            suffix = Path(entry.name).suffix.lower()
            if suffix == '.sfo':
                try:
                    name = validate_name(entry.name)
                except ValueError as error:
                    QMessageBox.warning(self, 'Invalid filename', str(error))
                    return
                if entry.size is not None and entry.size > MAX_EDITOR_BYTES:
                    QMessageBox.information(self, 'File too large', 'PARAM.SFO previews support up to 1 MiB.')
                    return
                self.edit_remote_sfo(tab, state, name)
            elif (archive_kind(entry.name) or suffix in SPECIAL_SUFFIXES
                  or (suffix in OPAQUE_SUFFIXES and entry.size is not None and entry.size > MEDIA_LIMIT)):
                self.preview_remote_format(tab, entry)
            else:
                self.preview_remote_file(tab, entry)

    def edit_remote_in_vscode(self, tab, entry):
        state = self.sessions.get(tab)
        if (not state or not state.connected or state.busy or state.closing
                or entry.is_dir or entry.is_link):
            return
        executable = find_vscode()
        if not executable:
            QMessageBox.information(self, 'VS Code unavailable',
                                    'Install Visual Studio Code or make its command available on PATH.')
            return
        self.preview_remote_file(tab, entry, external_editor=executable)

    def preview_remote_file(self, tab, entry, *, external_editor=None):
        state = self.sessions[tab]
        limit = MAX_EDITOR_BYTES if external_editor else preview_limit(entry.name)
        if entry.size is not None and entry.size > limit:
            QMessageBox.information(self, 'File too large',
                                    f'The editor limit for this file is {limit // (1024 * 1024)} MiB.')
            return
        try:
            name = validate_name(entry.name)
        except ValueError as error:
            QMessageBox.warning(self, 'Invalid filename', str(error))
            return
        folder = state.remote_path

        def has_sidecars():
            reserved = {sidecar.casefold() for sidecar in sqlite_sidecars(name)}
            return any(item.name.casefold() in reserved for item in state.backend.list_dir())

        def read(_):
            state.backend.cwd(folder)
            try:
                data = state.backend.read_bytes(name, limit)
            except ValueError:
                # An FTP read aborted at the size limit can leave pending control replies.
                if state.protocol in ('FTP', 'FTPS'):
                    state.connected = False
                raise
            return data, not external_editor and data.startswith(SQLITE_HEADER) and has_sidecars()

        def opened(result):
            data, in_use = result

            def save(dialog, revised):
                if not state.connected or state.busy or state.closing or tab not in self.sessions:
                    QMessageBox.information(self, 'Server unavailable', 'Wait for the server tab to be connected and idle.')
                    return False
                session_ids = {str(id(other)) for other in self.sessions if self.endpoint_for(other) == self.endpoint_for(tab)}
                if self.queue.protects_remote_path(session_ids, folder, name):
                    QMessageBox.information(self, 'File in transfer queue', 'Finish or cancel the transfer before saving.')
                    return False
                current = state.remote_path

                def check_database():
                    if data.startswith(SQLITE_HEADER) and has_sidecars():
                        raise RemoteConflictError('Close the application using this database before editing it')

                def publish(_):
                    try:
                        state.backend.cwd(folder)
                        check_database()
                        digest = hashlib.sha256(data).hexdigest()
                        return safe_save_remote_bytes(state.backend, name, digest, revised,
                                                      limit=limit, before_publish=check_database)
                    finally:
                        if current != folder:
                            try:
                                state.backend.cwd(current)
                            except Exception:
                                state.connected = False
                                raise

                def complete(backup):
                    tab.directory_cache.invalidate(folder)
                    self.finish_file_save(dialog, posixpath.join(folder, backup))
                    self.refresh(tab)

                def failed(_error):
                    dialog.save_failed()
                    return False

                return self.run_session_job(tab, f'Saving {name}', publish, complete, failed)

            if external_editor:
                if data.startswith((SQLITE_HEADER, b'bplist00', *ZIP_MAGIC)):
                    QMessageBox.warning(self, 'Could not open text in VS Code', 'This file contains binary data.')
                    return
                location = self.private_text(f'{state.host} · {posixpath.join(folder, name)}')
                try:
                    dialog = ExternalEditorDialog(self, name, location, data, external_editor, save)
                except (OSError, ValueError) as error:
                    QMessageBox.warning(self, 'Could not open text in VS Code', str(error))
                    return
                dialog.show()
            else:
                self.open_file_preview(name, data, save, sqlite_in_use=in_use)

        self.run_session_job(tab, f'Opening {name}', read, opened)

    def navigate_history(self, tab, delta):
        index = tab.nav_index + delta
        if not (0 <= index < len(tab.nav_history)):
            return
        self.navigate(tab, tab.nav_history[index], history_index=index)
    def navigate(self, tab, path, *, history_index=None):
        state = self.sessions.get(tab)
        if not state or not state.connected: return
        def operation(_):
            state.backend.cwd(path)
            remote_path = state.backend.pwd()
            cached = tab.directory_cache.get(remote_path)
            if cached is not None:
                return remote_path, cached, True
            entries = state.backend.list_dir()
            return remote_path, entries, False
        def after(result):
            state.remote_path, state.entries, from_cache = result
            if not from_cache: tab.directory_cache.put(state.remote_path, state.entries)
            if history_index is not None:
                tab.nav_index = history_index
            elif tab.nav_index < 0 or state.remote_path != tab.nav_history[tab.nav_index]:
                tab.nav_history = tab.nav_history[:tab.nav_index + 1] + [state.remote_path]
                tab.nav_index = len(tab.nav_history) - 1
            tab.show_entries()
            tab.status.setText('Ready · cached listing' if from_cache else 'Ready')
        self.run_session_job(tab, 'Opening remote folder', operation, after)
    def refresh(self, tab):
        state = self.sessions.get(tab)
        if not state or not state.connected: return
        def after(entries):
            state.entries = entries
            tab.directory_cache.put(state.remote_path, entries)
            tab.show_entries()
        tab.directory_cache.invalidate(state.remote_path)
        self.run_session_job(tab, 'Refreshing folder', lambda _: state.backend.list_dir(), after)
    def mkdir(self, tab):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.busy: return
        name, ok = QInputDialog.getText(self, 'New remote folder', 'Folder name')
        if not ok: return
        try: name = clean_remote_name(name.strip())
        except ValueError as exc:
            QMessageBox.warning(self, 'Invalid name', str(exc)); return
        def action(_):
            state.backend.mkdir(name)
            return state.backend.list_dir()
        self.run_session_job(tab, 'Creating folder', action, lambda entries: self._replace_entries(tab, entries))
    def _replace_entries(self, tab, entries):
        tab.directory_cache.invalidate()
        tab.state.entries = entries
        tab.show_entries()
    def delete(self, tab):
        self.delete_remote_entry(tab)

    def delete_remote_entry(self, tab):
        """Confirm permanent removal of the selected remote item."""
        state = self.sessions.get(tab)
        if not state or not state.connected or state.busy or state.closing:
            return
        entry = tab.selected()
        if entry is None:
            QMessageBox.information(self, 'Select an item', 'Select a remote file or folder.')
            return
        if entry.is_link:
            return
        directory = state.remote_path
        try:
            name = clean_remote_name(entry.name)
        except ValueError as exc:
            QMessageBox.warning(self, 'Invalid name', str(exc))
            return
        def blocked():
            session_ids = {
                str(id(other)) for other in self.sessions
                if self.endpoint_for(other) == self.endpoint_for(tab)
            }
            if not self.queue.protects_remote_path(session_ids, directory, name):
                return False
            QMessageBox.warning(self, 'Item in transfer queue',
                                'Finish or cancel the transfer before deleting this item.')
            return True

        if blocked():
            return
        path = posixpath.join(directory, name)
        prompt = (f'Permanently delete this remote folder and everything inside it?\n\n{path}'
                  if entry.is_dir else
                  f'Permanently delete this remote file?\n\n{path}')
        if QMessageBox.question(self, 'Confirm remote deletion', prompt,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if state.remote_path != directory or blocked():
            return

        tab.directory_cache.invalidate()

        def action(_):
            state.backend.cwd(directory)
            if entry.is_dir:
                delete_remote_tree(state.backend, name, cancelled=lambda: state.cancelled)
            else:
                state.backend.delete_file(name)
            return state.backend.list_dir()

        def failed(error):
            if isinstance(error, RemoteDirectoryRestoreError):
                state.connected = False
                tab.set_controls()
            elif not self._quit_requested:
                QTimer.singleShot(0, lambda: self.refresh(tab))
            return self._quit_requested and isinstance(error, InterruptedError)

        label = 'Deleting remote folder' if entry.is_dir else 'Deleting remote file'
        self.run_session_job(tab, label, action,
                             lambda entries: self._replace_entries(tab, entries), failed)

    def rename_remote(self, tab):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.active: return
        entry = tab.selected()
        if not entry or entry.is_link: return
        name, ok = QInputDialog.getText(self, 'Rename remote item', 'New name', text=entry.name)
        if not ok or name == entry.name: return
        try: name = validate_name(name)
        except ValueError as exc:
            QMessageBox.warning(self, 'Invalid name', str(exc)); return
        def action(_):
            # Never use a stale listing alone to decide whether destination exists.
            if any(e.name == name for e in state.backend.list_dir()):
                raise FileExistsError('Destination already exists')
            state.backend.rename(validate_name(entry.name), name)
            return state.backend.list_dir()
        self.run_session_job(tab, 'Renaming remote item', action, lambda entries: self._replace_entries(tab, entries))
    def edit_remote(self, tab):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.active: return
        entry = tab.selected()
        if not entry or entry.is_dir or entry.is_link:
            QMessageBox.information(self, 'Select file', 'Select a regular remote file.'); return
        name = validate_name(entry.name)
        if entry.size is not None and entry.size > 1024 * 1024:
            QMessageBox.information(self, 'Too large', 'Editor supports files up to 1 MiB.'); return
        if name.lower().endswith('.sfo'):
            self.edit_remote_sfo(tab, state, name)
            return
        self.preview_remote_file(tab, entry)

    def edit_remote_sfo(self, tab, state, name):
        def read(_):
            folder = state.backend.pwd()
            try:
                raw = state.backend.read_bytes(name, MAX_EDITOR_BYTES)
            except ValueError:
                if state.protocol in ('FTP', 'FTPS'):
                    state.connected = False
                raise
            return folder, ParamSfo(raw)

        def opened(result):
            if self.sessions.get(tab) is not state:
                return
            folder, document = result
            path = posixpath.join(folder, name)
            revised = self.revised_sfo(document, path)
            if revised == document.raw or self.sessions.get(tab) is not state or not state.connected:
                return
            if QMessageBox.question(self, 'Confirm PARAM.SFO edit',
                    f'Save changes to {self.private_text(state.host + path)}?\n\n'
                    'The original will be kept as a backup in the same remote folder. '
                    'Altered metadata can affect how the console recognizes this item.',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            session_ids = {str(id(other)) for other in self.sessions if self.endpoint_for(other) == self.endpoint_for(tab)}
            if self.queue.protects_remote_path(session_ids, folder, name):
                QMessageBox.information(self, 'File in transfer queue', 'Finish or cancel the transfer before saving.')
                return

            def save(_):
                if state.backend.pwd() != folder:
                    raise RemoteConflictError('Remote folder changed. Open the PARAM.SFO again.')
                digest = hashlib.sha256(document.raw).hexdigest()
                return safe_save_remote_bytes(state.backend, name, digest, revised)

            def saved(backup):
                if backup:
                    tab.directory_cache.invalidate(folder)
                    self.log(f'{state.host}: updated {path}; recovery backup {backup}')
                    QMessageBox.information(self, 'PARAM.SFO saved', f'Original preserved as:\n{posixpath.join(folder, backup)}')
                    self.refresh(tab)

            if not self.run_session_job(tab, 'Saving PARAM.SFO with backup', save, saved):
                QMessageBox.information(self, 'Connection busy', 'Wait for the current operation, then reopen the PARAM.SFO.')

        self.run_session_job(tab, 'Opening PARAM.SFO', read, opened)

    def search_remote(self, tab):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.busy: return
        options = QDialog(self)
        options.setWindowTitle('Advanced remote search')
        form = QFormLayout(options)
        pattern = QLineEdit()
        pattern.setPlaceholderText('Filename or pattern')
        wildcard = QCheckBox('Match wildcards (*, ?, [abc])')
        extensions = QLineEdit()
        extensions.setPlaceholderText('For example pkg, tar.gz')
        minimum, maximum, since, until = [QLineEdit() for _ in range(4)]
        since.setPlaceholderText('YYYY-MM-DD')
        until.setPlaceholderText('YYYY-MM-DD')
        for label, field in (
            ('Filename', pattern), ('', wildcard), ('Extensions', extensions),
            ('Minimum bytes', minimum), ('Maximum bytes', maximum),
            ('Modified on or after (UTC)', since), ('Modified on or before (UTC)', until),
        ):
            form.addRow(label, field)
        note = QLabel('Size and date filters skip entries when the server has no matching metadata.')
        note.setWordWrap(True)
        form.addRow(note)
        run = QPushButton('Search')
        form.addRow(run)
        run.clicked.connect(options.accept)
        run.setDefault(True)
        while options.exec() == QDialog.Accepted:
            try:
                query = RemoteSearch.from_fields(pattern.text(), wildcard.isChecked(), extensions.text(),
                                                 minimum.text(), maximum.text(), since.text(), until.text())
                break
            except (ValueError, OverflowError, OSError) as error:
                QMessageBox.warning(options, 'Invalid search filters', str(error))
        else:
            options.deleteLater()
            return
        options.deleteLater()
        root = state.remote_path
        def after(results):
            dialog = QDialog(self); dialog.setWindowTitle(f'Search results — {query}')
            dialog.resize(850, 550)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel(f'{len(results)} result(s) under {root} (limit 2000)'))
            listing = QListWidget()
            for path, is_dir, size in results:
                detail = 'Folder' if is_dir else self.display_size(size) if size is not None else 'File'
                listing.addItem(self.private_text(f'{detail} · {path}'))
            layout.addWidget(listing)
            close = QPushButton('Close'); close.clicked.connect(dialog.accept)
            layout.addWidget(close); dialog.exec()
        self.run_session_job(tab, 'Searching remote folders',
            lambda _: find_remote(state.backend, root, query), after)
    def permissions_remote(self, tab):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.active: return
        entry = tab.selected()
        if not entry or entry.is_link: return
        text, ok = QInputDialog.getText(self, 'Remote permissions',
                   'Unix octal permissions (e.g. 644 for file, 755 for folder):')
        if not ok: return
        if len(text) != 3 or any(c not in '01234567' for c in text):
            QMessageBox.warning(self, 'Permissions', 'Enter three octal digits (000–777).'); return
        mode = int(text, 8)
        if QMessageBox.question(self, 'Confirm permission change',
                                f'Apply {text} to {entry.name}? The server must support chmod.') != QMessageBox.Yes:
            return
        self.run_session_job(tab, 'Changing permissions',
            lambda _: state.backend.chmod(validate_name(entry.name), mode),
            lambda _: self.log(f'{state.host}: permissions {text} applied to {entry.name}'))
