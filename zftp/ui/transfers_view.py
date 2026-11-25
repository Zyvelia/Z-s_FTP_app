"""TransfersViewMixin: isolated Z FTP UI behavior, attached to the same main window."""
from __future__ import annotations

import ftplib
import sys
import os
import time
import posixpath
from pathlib import Path
from threading import Event
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
from zftp.transfers.transfer_queue import DOWNLOADS, FolderProgress, TransferQueue, Transfer
from zftp.ui.transfer_tables import TransferTable
from zftp.transfers.reliable_transfer import TransferSettings, download as resilient_download, upload as resilient_upload, with_retries
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


class DownloadConnectionError(RuntimeError):
    """The extra connection failed before transferring data."""


class TransfersViewMixin:
    def selected_local_files(self):
        rows = sorted({item.row() for item in self.local_table.selectedItems()})
        paths = []
        for row in rows:
            item = self.local_table.item(row, 0)
            if item:
                path = self.local_path / item.text()
                if path.exists() and not path.is_symlink(): paths.append(path)
        return paths
    def selected_remote_files(self, tab):
        rows = sorted({item.row() for item in tab.table.selectedItems()})
        return [tab.state.entries[row] for row in rows
                if 0 <= row < len(tab.state.entries) and not getattr(tab.state.entries[row], 'is_link', False)]
    def download(self, tab):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.closing: return
        entries = self.selected_remote_files(tab)
        if not entries:
            QMessageBox.information(self, 'Select files', 'Select one or more remote files or folders.'); return
        folder = QFileDialog.getExistingDirectory(self, 'Choose download destination', str(self.local_path))
        if folder:
            self.enqueue_remote_items(tab, [e.name for e in entries], Path(folder))
    def enqueue_remote_items(self, tab, names, destination=None):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.closing: return
        root, local = state.remote_path, self.local_path if destination is None else destination
        valid = {e.name: e for e in state.entries if not getattr(e, 'is_link', False)}
        names = [name for name in names if name in valid]
        if not names: return
        if not any(valid[n].is_dir for n in names):
            self.enqueue_downloads(tab, names, root, local)
            return
        selected = set(names)
        def scan(_):
            remote = inventory_remote(state.backend, root, lambda: state.cancelled, selected=selected)
            local_inv = inventory_local(local, lambda: state.cancelled, selected=selected)
            return plan(local_inv, remote, 'download')
        def ready(changes):
            self.show_changes_and_enqueue(tab, changes, root, local, 'Download selected folders',
                                          folder_batch=names[0] if len(names) == 1 else 'Selected items')
        self.run_session_job(tab, 'Comparing remote folders', scan, ready)
    def upload(self):
        tab = self.current_tab()
        if not tab or not tab.state.connected or tab.state.closing: return
        paths = self.selected_local_files()
        if not paths:
            QMessageBox.information(self, 'Select files', 'Select one or more local files or folders.'); return
        self.enqueue_local_items(tab, paths)
    def enqueue_local_items(self, tab, paths):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.closing: return
        paths = [Path(p) for p in paths if Path(p).exists() and not Path(p).is_symlink()]
        if not paths: return
        if not any(p.is_dir() for p in paths):
            self.enqueue_uploads(tab, paths)
            return
        remote_root, local_root = state.remote_path, self.local_path
        selected = {p.name for p in paths}
        # Only scan paths selected in the currently displayed local folder.
        if any(p.parent != local_root for p in paths):
            QMessageBox.warning(self, 'Select local folders',
                'For recursive uploads, select folders inside the current local directory.'); return
        def scan(_):
            local_inv = inventory_local(local_root, lambda: state.cancelled)
            remote_inv = inventory_remote(state.backend, remote_root, lambda: state.cancelled)
            changes = plan(local_inv, remote_inv, 'upload')
            return [c for c in changes if any(c.path == n or c.path.startswith(n + '/') for n in selected)]
        def ready(changes):
            self.show_changes_and_enqueue(tab, changes, remote_root, local_root, 'Upload selected folders')
        self.run_session_job(tab, 'Comparing local folders', scan, ready)
    def preview_sync(self):
        tab = self.current_tab()
        if not tab or not tab.state.connected or tab.state.closing: return
        choices = {'Upload missing → remote': 'upload',
                   'Download missing → local': 'download',
                   'Two-way missing files': 'two-way'}
        choice, ok = QInputDialog.getItem(self, 'Directory synchronization',
            'Mode (no deletions or overwrites):', list(choices), 0, False)
        if not ok: return
        mode = choices[choice]
        local_root, remote_root = self.local_path, tab.state.remote_path
        def scan(_):
            local = inventory_local(local_root, lambda: tab.state.cancelled)
            remote = inventory_remote(tab.state.backend, remote_root, lambda: tab.state.cancelled)
            return plan(local, remote, mode)
        def ready(changes):
            self.show_changes_and_enqueue(tab, changes, remote_root, local_root, choice)
        self.run_session_job(tab, 'Comparing directory trees', scan, ready)
    def show_changes_and_enqueue(self, tab, changes, remote_root, local_root, title, *, folder_batch=None):
        """Explicit preview and confirmation before adding any recursive jobs."""
        if tab not in self.sessions or not tab.state.connected: return
        dialog = QDialog(self); dialog.setWindowTitle(title + ' — preview')
        dialog.resize(840, 550)
        layout = QVBoxLayout(dialog)
        if folder_batch is not None:
            location = QLabel(f'Download destination: {local_root}')
            location.setTextFormat(Qt.PlainText)
            location.setWordWrap(True)
            layout.addWidget(location)
        layout.addWidget(QLabel('No files are deleted or overwritten. Same-size matches are NOT checksum-verified.'))
        table = QTableWidget(len(changes), 4)
        table.setHorizontalHeaderLabels(['Action', 'Relative path', 'Local bytes', 'Remote bytes'])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        for row, change in enumerate(changes):
            for col, value in enumerate((change.kind, change.path,
                                         '—' if change.local_size is None else str(change.local_size),
                                         '—' if change.remote_size is None else str(change.remote_size))):
                table.setItem(row, col, QTableWidgetItem(value))
        layout.addWidget(table)
        conflict_count = sum(c.kind in ('conflict','same_size','type_conflict') for c in changes)
        layout.addWidget(QLabel(f'{len(changes)} entries; {conflict_count} existing paths need review.'))
        opts = QHBoxLayout()
        opts.addWidget(QLabel('Existing files:'))
        conflict_option = QComboBox(); conflict_option.addItems(['Skip existing', 'Keep both (download remote as renamed local copy)'])
        opts.addWidget(conflict_option)
        opts.addStretch(1)
        layout.addLayout(opts)
        row = QHBoxLayout(); cancel = QPushButton('Cancel'); confirm = QPushButton('Queue previewed changes')
        row.addStretch(1); row.addWidget(cancel); row.addWidget(confirm); layout.addLayout(row)
        cancel.clicked.connect(dialog.reject); confirm.clicked.connect(dialog.accept)
        if dialog.exec() != QDialog.Accepted: return
        policy = 'keep_both' if conflict_option.currentIndex() == 1 else 'skip'
        self.enqueue_changes(tab, changes, remote_root, local_root, policy, folder_batch=folder_batch)
    def enqueue_changes(self, tab, changes, remote_root, local_root, policy='skip', *, folder_batch=None):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.closing: return
        sid = str(id(tab)); local_root = Path(local_root)
        remote_root = posixpath.normpath(remote_root)
        accepted = 0; rejected = []
        # Do not attempt to operate below a file/folder type conflict.
        blocked = [c.path for c in changes if c.kind == 'type_conflict']
        tasks = []
        reserved_copies: dict[Path, set[str]] = {}
        for change in changes:
            try:
                if change.kind in ('same_size', 'conflict', 'type_conflict'):
                    if policy != 'keep_both' or change.kind == 'type_conflict': continue
                    # For conflicts, retain both copies by downloading remote into a distinctly named local file.
                    target = safe_local_target(local_root, change.path)
                    occupied = reserved_copies.setdefault(target.parent,
                        {p.name for p in target.parent.iterdir()} if target.parent.is_dir() else set())
                    for job in (part for queued in self.queue.jobs for part in (queued.children or (queued,))):
                        if job.direction == 'download' and Path(job.destination).parent == target.parent:
                            occupied.add(Path(job.destination).name)
                    renamed = unique_name(target.name, occupied)
                    tasks.append(Transfer(sid, 'download', posixpath.basename(change.path),
                                str(target.with_name(renamed)),
                                posixpath.join(remote_root, posixpath.dirname(change.path)), str(target.parent),
                                total=change.remote_size))
                    continue
                relative = relpath(change.path)
                if any(relative == b or relative.startswith(b + '/') for b in blocked): continue
                target = safe_local_target(local_root, relative)
                parent = posixpath.join(remote_root, posixpath.dirname(relative))
                name = relative.split('/')[-1]
                if change.kind == 'mkdir_remote':
                    tasks.append(Transfer(sid, 'mkdir_remote', name, name, parent, str(target.parent)))
                elif change.kind == 'mkdir_local':
                    tasks.append(Transfer(sid, 'mkdir_local', relative, str(target), remote_root, str(target.parent)))
                elif change.kind == 'upload':
                    if not target.is_file() or target.is_symlink(): raise FileNotFoundError(target)
                    tasks.append(Transfer(sid, 'upload', str(target), name, parent, str(target.parent)))
                elif change.kind == 'download':
                    if target.exists() or target.is_symlink(): raise FileExistsError(target)
                    tasks.append(Transfer(sid, 'download', name, str(target), parent,
                                          str(target.parent), total=change.remote_size))
            except (OSError, ValueError) as exc: rejected.append(f'{change.path}: {exc}')
        # Directory creation must happen before dependent transfers.
        rank = {'mkdir_local':0, 'mkdir_remote':0, 'upload':1, 'download':1}
        tasks.sort(key=lambda t: (rank[t.direction], t.destination.count('/') + t.remote_dir.count('/')))
        if folder_batch is not None and tasks:
            skipped = len(changes) - len(tasks)
            group = Transfer(sid, 'download_folder', folder_batch, str(local_root),
                             remote_root, str(local_root), children=tuple(tasks),
                             total=len(tasks), skipped=skipped)
            try:
                self.queue.add(group, keep_both=True)
                accepted = len(tasks)
            except (OSError, ValueError) as exc:
                rejected.append(str(exc))
        else:
            for item in tasks:
                try: self.queue.add(item, keep_both=item.direction == 'download'); accepted += 1
                except (OSError, ValueError) as exc: rejected.append(f'{item.source}: {exc}')
        if folder_batch is not None and not tasks and not rejected:
            QMessageBox.information(self, 'Nothing queued', 'No new files or folders to download under the selected policy.')
        self.render_queue()
        if accepted: self.log(f'Queued {accepted} recursive/sync operations for {state.host}')
        if rejected: QMessageBox.warning(self, 'Some entries were skipped', '\n'.join(rejected[:15]))
        self.pump_queue(tab)
        return accepted, rejected
    def enqueue_uploads(self, tab, paths):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.closing: return
        accepted, rejected = 0, []
        for path in paths:
            path = Path(path)
            try:
                if not path.is_file(): raise ValueError('Only files supported; folders arrive in v0.7')
                name = clean_remote_name(path.name)
                if not state.busy and any(e.name == name for e in state.entries):
                    raise FileExistsError('Remote item already exists')
                self.queue.add(Transfer(str(id(tab)), 'upload', str(path), name,
                                        state.remote_path, str(path.parent)))
                accepted += 1
            except (OSError, ValueError) as exc:
                rejected.append(f'{path.name}: {exc}')
        self.render_queue()
        if accepted: self.log(f'Queued {accepted} upload(s) for {state.host}')
        if rejected: QMessageBox.warning(self, 'Some files were not queued', '\n'.join(rejected[:12]))
        self.pump_queue(tab)
    def enqueue_downloads(self, tab, names, remote_dir, local_dir):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.closing: return
        accepted, rejected = 0, []
        local_dir = Path(local_dir)
        for raw in names:
            try:
                name = clean_remote_name(raw)
                dest = local_dir / name
                self.queue.add(Transfer(str(id(tab)), 'download', name, str(dest),
                                        remote_dir, str(local_dir)), keep_both=True)
                accepted += 1
            except (OSError, ValueError) as exc:
                rejected.append(f'{raw}: {exc}')
        self.render_queue()
        if accepted: self.log(f'Queued {accepted} download(s) from {state.host}')
        if rejected: QMessageBox.warning(self, 'Some files were not queued', '\n'.join(rejected[:12]))
        self.pump_queue(tab)
    def receive_remote_drop(self, data):
        # Drop onto local browser: preserve originating tab and only accept its currently known path.
        for tab in tuple(self.sessions):
            if str(id(tab)) == data.get('session'):
                names = data.get('files', [])
                remote_dir = data.get('path')
                if (remote_dir == tab.state.remote_path and isinstance(names, list)
                        and all(isinstance(n, str) for n in names)):
                    self.enqueue_remote_items(tab, names)
                return
    def checkpoint_queue(self, *, force=False):
        if self._quit_requested or self.queue_recovery.load_error:
            return
        pending = tuple((job.id, job.session_id, job.count // 32 if job.children else 0, job.verify_sha256)
                        for job in self.queue.jobs if job.status in ('Queued', 'Running'))
        key = pending, tuple(record['plan']['id'] for record in self.queue_recovery.waiting)
        if not force and key == self._queue_checkpoint:
            return
        try:
            self.queue_recovery.save(self.queue.jobs, {str(id(tab)): state for tab, state in self.sessions.items()})
        except OSError as error:
            if not self._queue_save_warning:
                self.log(f'Could not save the recovery queue: {error}')
            self._queue_save_warning = True
        else:
            self._queue_save_warning = False
            self._queue_checkpoint = key

    def restore_queue(self, tab):
        restored, blocked = self.queue_recovery.restore(self.queue, str(id(tab)), tab.state)
        if restored or blocked:
            self.log(f'Recovered {restored} queued transfer(s) · {blocked} waiting due to destination conflicts')
            self.render_queue()

    def set_queue_paused(self, paused):
        self.queue.paused = paused
        self.preferences.setValue('queue/paused', paused)
        self.queue_pause_button.setText('Resume queue' if paused else 'Pause queue')
        self.refresh_live_footer()
        self.refresh_tray_status()
        if not paused:
            for tab in tuple(self.sessions):
                self.pump_queue(tab)

    def render_queue(self):
        self.checkpoint_queue()
        current = self.queue.jobs
        running = next((job for job in current if job.status == 'Running'), None)
        featured = running
        if featured is None and current and current[-1].status == 'Completed':
            featured = current[-1]
        if featured is None:
            self.stage_ribbon.setText('No active transfers')
        else:
            if featured.direction == 'upload':
                stages = ['Uploading', 'Renaming']
            elif featured.direction == 'download':
                stages = ['Downloading', 'Finalizing']
            else:
                stages = ['Downloading' if featured.children else 'Processing']
            if featured.verify_sha256 and featured.direction in ('upload', 'download'):
                stages.insert(1, 'Verifying')
            stages.append('Completed')
            if featured.status == 'Completed':
                labels = ['✓ ' + stage for stage in stages]
            else:
                current_idx = stages.index(featured.phase) if featured.phase in stages else 0
                labels = [
                    ('✓ ' if i < current_idx else '● ' if i == current_idx else '○ ') + stage
                    for i, stage in enumerate(stages)
                ]
            self.stage_ribbon.setText('   ›   '.join(labels))
        self.queue_table.setRowCount(len(current))
        for row, item in enumerate(current):
            tab = next((t for t in self.sessions if str(id(t)) == item.session_id), None)
            host = self.private_text(tab.state.host) if tab else '(disconnected)'
            pct = (f'{min(100, item.count * 100 // item.total)}%' if item.total else
                   ('—' if item.status == 'Queued' else f'{item.count:,} bytes'))
            speed = getattr(item, 'speed_bps', 0.)
            eta = getattr(item, 'eta_seconds', None)
            attempts = getattr(item, 'attempts', 0)
            stage = getattr(item, 'phase', item.status)
            if item.status == 'Running' and item.total and item.count >= item.total:
                pct = '100% sent' if item.direction == 'upload' else '100% received'
            folder = item.folder_progress
            transferring = item.children or stage in ('Uploading', 'Downloading')
            speed_text = f'{speed / 1024:.1f} KiB/s' if item.status == 'Running' and speed > 0 and transferring else '—'
            eta_text = ('—' if item.status in ('Queued', 'Completed', 'Failed', 'Cancelled')
                        else ('Checking…' if stage == 'Verifying' else
                              'Finishing…' if stage in ('Renaming', 'Finalizing') else format_eta(eta)))
            if item.status == 'Running' and folder is not None:
                if folder.bytes_total is None:
                    eta_text = 'Size unknown'
                elif folder.bytes_done >= folder.bytes_total:
                    eta_text = 'Finishing…'
            for col, value in enumerate((host, ('Folder download' if item.children else item.direction.title()), item.name, pct,
                                         speed_text, eta_text, str(attempts),
                                         stage + (': ' + item.error if item.error else ''))):
                if col == 3:
                    continue  # Progress is a proper widget rather than text-only.
                cell = QTableWidgetItem(self.private_text(value))
                if col == 2 and item.direction == 'download':
                    cell.setToolTip(self.private_text(item.destination))
                if col == 7:
                    if item.status == 'Completed':
                        cell.setForeground(QColor('#7ce0ac'))
                    elif item.status in ('Failed', 'Cancelled'):
                        cell.setForeground(QColor('#ffa0ab'))
                    elif item.status == 'Running':
                        cell.setForeground(QColor('#d9b3ff'))
                    cell.setToolTip(self.private_text(f'{item.status}: {stage}' + (f' — {item.error}' if item.error else '')))
                self.queue_table.setItem(row, col, cell)
            widget = QWidget()
            bar_row = QHBoxLayout(widget)
            bar_row.setContentsMargins(6, 4, 6, 4)
            bar_row.setSpacing(5)
            bar = QProgressBar()
            bar.setObjectName('jobProgress')
            bar.setRange(0, 100)
            value = min(100, max(0, int(item.count * 100 / item.total))) if item.total else 0
            if item.status == 'Completed':
                value = 100
            bar.setValue(value)
            bar.setTextVisible(True)
            if item.children:
                if folder is not None and folder.bytes_total:
                    value = min(100, int(100 * folder.bytes_done / folder.bytes_total))
                    bar.setValue(100 if item.status == 'Completed' else value)
                    bar.setFormat(f'%p% · {item.count}/{item.total}')
                else:
                    bar.setFormat(f'{item.count} / {item.total} items')
            elif item.status == 'Running' and value == 100:
                # 100% of bytes sent is NOT the same as a completed job.
                bar.setFormat(f'100% sent · {stage}' if item.direction == 'upload'
                              else f'100% received · {stage}')
            elif item.total:
                bar.setFormat(f'{value}%')
            else:
                bar.setFormat('Queued' if item.status == 'Queued' else stage)
            bar.setProperty('complete', item.status == 'Completed')
            bar_row.addWidget(bar)
            self.queue_table.setCellWidget(row, 3, widget)
        self.queue_table.resizeColumnToContents(0)
        self.refresh_live_footer()
    def cancel_selected_job(self):
        row = self.queue_table.currentRow()
        if not 0 <= row < len(self.queue.jobs): return
        job = self.queue.cancel(self.queue.jobs[row].id)
        if not job: return
        if job.status == 'Cancelled':
            tab = next((t for t in self.sessions if str(id(t)) == job.session_id), None)
            if tab: self.record_transfer(job, tab.state)
        if job.status == 'Running':
            tab = next((t for t in self.sessions if str(id(t)) == job.session_id), None)
            if tab: tab.state.cancel_transfer(job.id)
        self.render_queue()
    def clear_finished_jobs(self):
        self.queue.clear_finished()
        self.transfer_metrics = {key: value for key, value in self.transfer_metrics.items()
                                 if any(j.id == key for j in self.queue.jobs)}
        self.render_queue()
    def set_parallel_downloads(self, limit):
        self.download_pool.setMaxThreadCount(limit)
        for tab in tuple(self.sessions):
            self.pump_queue(tab)

    def pump_queue(self, tab):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.busy or state.closing or self._quit_requested: return
        sid = str(id(tab))
        make_connection = getattr(tab, 'make_download_connection', None)
        parallel = state.parallel_downloads and make_connection is not None
        limit = 1
        if parallel:
            limit = self.parallel_download_limit.value()
            active = sum(len(session.downloads) for session in self.sessions.values())
            if active >= limit:
                limit = 0
        item = self.queue.next_for(sid, download_limit=limit)
        if item is None: return
        item.verify_sha256 = self.verify_checksums.isChecked() or (item.recovered and item.verify_sha256)
        background = parallel and item.direction in DOWNLOADS
        cancellation = Event() if background else None
        if background:
            backend, reconnect = make_connection()
            cancelled = cancellation.is_set
        else:
            backend = state.backend
            reconnect = lambda: tab.reconnect_fn()
            cancelled = lambda: state.cancelled
        self.render_queue()

        settings = TransferSettings(self.bandwidth_limit.value(),
                                    self.retry_limit.value(), self.backoff_delay.value(),
                                    self.chunk_select.currentData())
        item.attempts = 1
        item.speed_bps = 0.
        item.eta_seconds = None
        self.transfer_metrics.pop(item.id, None)

        verify_sha256 = item.verify_sha256

        def perform(item, progress):
            verify_enabled = verify_sha256 and item.direction in ('upload', 'download')
            if item.direction == 'mkdir_local':
                dest = Path(item.destination)
                if dest.is_symlink() or dest.is_file(): raise FileExistsError(dest)
                dest.mkdir(parents=True, exist_ok=True)
                return 0, None
            verified_digest = [None]
            def attempt():
                verified_digest[0] = None
                # Only this worker owns the connection. Never reuse the FTP
                # control channel concurrently with other jobs on this tab.
                old_dir = None if background else backend.pwd()
                try:
                    backend.cwd(item.remote_dir)
                    if item.direction == 'mkdir_remote':
                        # Idempotent directory creation, never replace existing files.
                        try:
                            backend.cwd(item.destination)
                            backend.cwd(item.remote_dir)
                        except Exception:
                            backend.cwd(item.remote_dir)
                            backend.mkdir(item.destination)
                        return 0
                    if item.direction == 'upload':
                        def staged_check(partial):
                            verified_digest[0] = verify_transfer(backend, 'upload', Path(item.source),
                                                                  partial, cancelled)
                        return resilient_upload(backend, Path(item.source), item.destination,
                                                progress, cancelled, settings,
                                                verify_staged=staged_check if verify_enabled else None,
                                                on_phase=lambda stage: progress('@stage:' + stage, None))
                    result = resilient_download(backend, item.source, Path(item.destination),
                                                progress, cancelled, settings)
                    return result
                finally:
                    if old_dir is not None:
                        try:
                            backend.cwd(old_dir)
                        except (OSError, EOFError, RuntimeError, ftplib.Error):
                            pass

            def on_retry(attempt_no, wait, why):
                item.attempts = attempt_no + 1
                # Qt signal transports state change to the GUI thread.
                progress(item.count, item.total)

            result = with_retries(attempt, reconnect, settings, cancelled,
                                  on_retry=on_retry)
            digest = verified_digest[0]
            if verify_enabled and item.direction == 'download':
                progress('@stage:Verifying', None)
                old_dir = None if background else backend.pwd()
                try:
                    backend.cwd(item.remote_dir)
                    digest = verify_transfer(backend, 'download', Path(item.destination), item.source,
                                             cancelled)
                finally:
                    if old_dir is not None:
                        try:
                            backend.cwd(old_dir)
                        except (OSError, EOFError, RuntimeError, ftplib.Error):
                            pass
            progress('@stage:Finalizing', None)
            return (result, digest)

        def operation(progress):
            if not item.children:
                return perform(item, progress)
            root = Path(item.local_dir)
            sizes = [child.total if child.direction == 'download' else 0 for child in item.children]
            known_bytes = sum(size or 0 for size in sizes)
            unknown_sizes = sizes.count(None)
            completed_bytes = 0
            next_update = 0.0

            def update_size(index, size):
                nonlocal known_bytes, unknown_sizes
                previous = sizes[index]
                if size is not None and size != previous:
                    sizes[index] = size
                    known_bytes += size - (previous or 0)
                    unknown_sizes -= int(previous is None)

            def report(items_done, bytes_done, status):
                total = known_bytes if not unknown_sizes else None
                progress(FolderProgress(items_done, bytes_done, total, status), None)

            for index, child in enumerate(item.children):
                if cancelled():
                    raise InterruptedError('Folder download cancelled')
                target = Path(child.destination)
                safe_local_target(root, target.relative_to(root).as_posix())
                label = f'{index + 1}/{len(item.children)} · {target.name}'
                report(index, completed_bytes, label)
                next_update = 0.0
                observed = 0

                def child_progress(count, total):
                    nonlocal next_update, observed
                    if isinstance(count, str):
                        report(index, completed_bytes + observed,
                               f"{label} · {count.removeprefix('@stage:')}")
                        return
                    observed = count
                    update_size(index, total)
                    now = time.monotonic()
                    if now >= next_update:
                        report(index, completed_bytes + observed, label)
                        next_update = now + 0.15

                try:
                    if item.recovered and child.direction == 'download' and (target.exists() or target.is_symlink()):
                        item.skipped += 1
                        size = 0
                    else:
                        size, _ = perform(child, child_progress)
                except InterruptedError:
                    raise
                except Exception as exc:
                    raise RuntimeError(f'{target.relative_to(root)}: {exc}') from exc
                update_size(index, size)
                completed_bytes += size
                report(index + 1, completed_bytes, label)

            return (completed_bytes, None)

        def after(result):
            self.queue.finish(item)
            self.record_transfer(item, state, result[1])
            if item.children:
                item.count = len(item.children)
                summary = f'{item.name} · {len(item.children)} items · {item.destination}'
                if item.skipped:
                    summary += f' · {item.skipped} skipped'
                self.notify('success', 'Folder download complete', summary, category='completion',
                            download_dir=Path(item.destination), download_name=item.name)
            else:
                self.notify('success', 'Transfer complete', f'{item.name} · {item.direction}', category='completion',
                            download_dir=Path(item.destination).parent if item.direction == 'download' else None,
                            download_name=item.name)
            self.render_queue()
            if item.direction in ('download', 'mkdir_local', 'download_folder'):
                if self.local_path == Path(item.local_dir): self.set_local_path(self.local_path)
            else:
                tab.directory_cache.invalidate(item.remote_dir)
                if not any(j.session_id == sid and j.status == 'Queued' for j in self.queue.jobs):
                    self.refresh(tab)

        def on_error(exc):
            self.queue.finish(item, exc)
            self.record_transfer(item, state)
            self.render_queue()
            if item.recovered and isinstance(exc, FileExistsError):
                # A completed destination may have been published right before a crash.
                return True
            # A failed data connection may leave its control session unusable.
            if not isinstance(exc, InterruptedError) and not self.queue_recovery.load_error:
                self.queue_recovery.defer_pending(self.queue, sid, state)
            else:
                self.queue.cancel_pending_for(sid)
                for cancelled_job in self.queue.jobs:
                    if cancelled_job.session_id == sid and cancelled_job.status == 'Cancelled':
                        self.record_transfer(cancelled_job, state)
            self.render_queue()
            return False

        label = f'{"Folder download" if item.children else item.direction.title()}: {item.name}'
        if background:
            self.start_background_download(tab, item, cancellation, backend, reconnect, operation, after)
        else:
            self.run_session_job(tab, label, operation, after, on_error, transfer=True,
                                 progress_callback=lambda count, total: self.on_transfer_progress(item, count, total))

    def start_background_download(self, tab, item, cancellation, backend, login, operation, after):
        state = tab.state
        state.downloads[item.id] = cancellation
        tab.set_controls()
        self.update_active()

        def run(progress):
            try:
                if cancellation.is_set():
                    raise InterruptedError('Download cancelled')
                try:
                    login()
                except Exception as exc:
                    raise DownloadConnectionError(str(exc)) from exc
                return operation(progress)
            finally:
                try:
                    backend.disconnect()
                except Exception:
                    # This connection is discarded even after an interrupted RETR.
                    pass

        job = Job(run)
        self.jobs.append(job)

        def done(result=None, error=None):
            state.downloads.pop(item.id)
            self.jobs.remove(job)
            if error is None:
                after(result)
            elif isinstance(error, DownloadConnectionError) and not cancellation.is_set() and not self._quit_requested:
                first_failure = state.parallel_downloads
                state.parallel_downloads = False
                item.status = item.phase = 'Queued'
                if first_failure:
                    self.log(f'{state.host} · Extra download connection unavailable. Retrying one at a time. {error}')
            else:
                if cancellation.is_set() and isinstance(error, DownloadConnectionError):
                    error = InterruptedError('Download cancelled')
                self.queue.finish(item, error)
                self.record_transfer(item, state)
                if item.status == 'Failed':
                    self.notify('error', 'Download failed', f'{state.host} · {item.name} · {error}')
            tab.set_controls()
            self.update_active()
            self.render_queue()
            for other in tuple(self.sessions):
                QTimer.singleShot(0, lambda other=other: self.pump_queue(other))

        job.events.progress.connect(lambda count, total: self.on_transfer_progress(item, count, total))
        job.events.done.connect(done)
        job.events.failed.connect(lambda error: done(error=error))
        self.download_pool.start(job)
        QTimer.singleShot(0, lambda: self.pump_queue(tab))

    def on_transfer_progress(self, item, count, total):
        if isinstance(count, FolderProgress):
            item.folder_progress = count
            item.phase = count.status
            previous = item.count
            self.queue.progress(item, count.items_done, len(item.children))
            if item.count // 32 != previous // 32:
                self.checkpoint_queue()
            count, total = count.bytes_done, count.bytes_total
        if isinstance(count, str) and count.startswith('@stage:'):
            item.phase = count[7:]
            item.speed_bps = 0.
            item.eta_seconds = None
            self.render_queue()
            return
        if not item.children:
            self.queue.progress(item, count, total)
        estimator = self.transfer_metrics.get(item.id)
        if estimator is None:
            estimator = ETAEstimator()
            self.transfer_metrics[item.id] = estimator
        item.speed_bps, item.eta_seconds = estimator.update(count, total, time.monotonic())
        if self._queue_redraw_throttle.due(finished=(total is not None and count >= total)):
            self.render_queue()
