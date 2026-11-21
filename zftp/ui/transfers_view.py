"""TransfersViewMixin: isolated Z FTP UI behavior, attached to the same main window."""
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



from zftp.ui.ui_workers import Job, Events
from zftp.ui.remote_tab import RemoteTab


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
                                posixpath.join(remote_root, posixpath.dirname(change.path)), str(target.parent)))
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
                    tasks.append(Transfer(sid, 'download', name, str(target), parent, str(target.parent)))
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
                self.queue.add(group)
                accepted = len(tasks)
            except (OSError, ValueError) as exc:
                rejected.append(str(exc))
        else:
            for item in tasks:
                try: self.queue.add(item); accepted += 1
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
                if dest.exists():
                    raise FileExistsError('Local destination already exists')
                self.queue.add(Transfer(str(id(tab)), 'download', name, str(dest),
                                        remote_dir, str(local_dir)))
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
    def render_queue(self):
        current = self.queue.jobs
        running = next((job for job in current if job.status == 'Running'), None)
        if running:
            phase = getattr(running, 'phase', 'Uploading')
            stages = ('Uploading', 'Verifying', 'Renaming', 'Completed') if running.direction == 'upload' else ('Downloading', 'Verifying', 'Finalizing', 'Completed')
            current_idx = stages.index(phase) if phase in stages else 0
            self.stage_ribbon.setText('   ›   '.join(
                ('✓ ' if i < current_idx else ('● ' if i == current_idx else '○ ')) + stage
                for i, stage in enumerate(stages)))
        elif current and current[-1].status == 'Completed':
            self.stage_ribbon.setText('✓ Uploading   ›   ✓ Verifying   ›   ✓ Renaming   ›   ✓ Completed')
        else:
            self.stage_ribbon.setText('○ Uploading   ›   ○ Verifying   ›   ○ Renaming   ›   ○ Completed')
        self.queue_table.setRowCount(len(current))
        for row, item in enumerate(current):
            tab = next((t for t in self.sessions if str(id(t)) == item.session_id), None)
            host = tab.state.host if tab else '(disconnected)'
            pct = (f'{min(100, item.count * 100 // item.total)}%' if item.total else
                   ('—' if item.status == 'Queued' else f'{item.count:,} bytes'))
            speed = getattr(item, 'speed_bps', 0.)
            eta = getattr(item, 'eta_seconds', None)
            attempts = getattr(item, 'attempts', 0)
            stage = getattr(item, 'phase', item.status)
            if item.status == 'Running' and item.total and item.count >= item.total:
                pct = '100% sent' if item.direction == 'upload' else '100% received'
            speed_text = f'{speed / 1024:.1f} KiB/s' if speed > 0 and stage in ('Uploading', 'Downloading') else '—'
            eta_text = ('—' if item.status in ('Queued', 'Completed', 'Failed', 'Cancelled')
                        else ('Checking…' if stage == 'Verifying' else
                              'Finishing…' if stage in ('Renaming', 'Finalizing') else format_eta(eta)))
            for col, value in enumerate((host, ('Folder download' if item.children else item.direction.title()), item.name, pct,
                                         speed_text, eta_text, str(attempts),
                                         stage + (': ' + item.error if item.error else ''))):
                if col == 3:
                    continue  # Progress is a proper widget rather than text-only.
                cell = QTableWidgetItem(value)
                if col == 7:
                    if item.status == 'Completed':
                        cell.setForeground(QColor('#7ce0ac'))
                    elif item.status in ('Failed', 'Cancelled'):
                        cell.setForeground(QColor('#ffa0ab'))
                    elif item.status == 'Running':
                        cell.setForeground(QColor('#d9b3ff'))
                    cell.setToolTip(f'{item.status}: {stage}' + (f' — {item.error}' if item.error else ''))
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
            if tab: tab.state.cancelled = True
        self.render_queue()
    def clear_finished_jobs(self):
        self.queue.clear_finished()
        self.transfer_metrics = {key: value for key, value in self.transfer_metrics.items()
                                 if any(j.id == key for j in self.queue.jobs)}
        self.render_queue()
    def apply_ps3_preset(self):
        """Apply conservative throughput settings without lowering integrity checks."""
        self.protocol_select.setCurrentText('FTP')
        self.port.setText('21')
        self.bandwidth_limit.setValue(0)
        self.chunk_select.setCurrentIndex(self.chunk_select.findData(PRESETS['PS3 Wi-Fi (balanced)']))
        self.retry_limit.setValue(3)
        self.backoff_delay.setValue(1)
        # Deliberately preserve SHA-256 and the user's username/password.
        self.transfer_options_toggle.setChecked(True)
        self.log('PS3 Wi-Fi preset: FTP, 256 KiB chunks, unlimited bandwidth; SHA-256 setting unchanged')
        QMessageBox.information(self, 'PS3 Wi-Fi preset',
            'Applied FTP port 21, 256 KiB transfer blocks and unlimited bandwidth.\n'
            'Your username, password and SHA-256 verification setting were not changed.\n'
            'If a PS3 tab is already connected, it keeps its existing protocol; '
            'the chunk setting applies to newly started jobs.')
    def benchmark_ps3(self):
        """Actual FTP upload benchmark; run only in a session-owned worker."""
        tab = self.tabs.currentWidget()
        state = self.sessions.get(tab)
        if not state or not state.connected or state.protocol != 'FTP':
            QMessageBox.information(self, 'FTP benchmark',
                'Connect to your PS3 using plain FTP and select its server tab first.')
            return
        sid = str(id(tab))
        if state.busy or state.closing or any(
                j.status in ('Queued', 'Running') and j.session_id == sid for j in self.queue.jobs):
            QMessageBox.information(self, 'FTP benchmark',
                'Finish or clear pending transfers for this server before benchmarking.')
            return
        answer = QMessageBox.question(self, 'Confirm PS3 upload benchmark',
            'Upload 8 MiB of random data into the current remote folder, then delete '
            'the test file? This uses your PS3 Wi-Fi connection and writes to its disk.\n\n'
            'Cancellation or connection loss may leave a uniquely named '
            'zftp_benchmark_*.bin or .zftp.part file requiring manual cleanup. '
            'Do not run this while another PS3 FTP client is changing files.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        settings = TransferSettings(0, 0, 0, self.chunk_select.currentData())
        origin = state.remote_path
        def perform(progress):
            backend = state.backend
            original = backend.pwd()
            try:
                backend.cwd(origin)
                return benchmark_ftp_upload(backend, settings=settings,
                    cancelled=lambda: state.cancelled, progress=progress)
            finally:
                try: backend.cwd(original)
                except (OSError, EOFError, RuntimeError): pass
        def done(result):
            message = (f'{result.mib_per_second:.2f} MiB/s over {result.elapsed_seconds:.1f} seconds '
                       f'({result.byte_count // (1024 * 1024)} MiB uploaded).\n'
                       f'Remote test-file cleanup: {"confirmed" if result.remote_cleanup else "UNCONFIRMED"}.')
            self.log('PS3 Wi-Fi benchmark: ' + message.replace('\n', ' '))
            QMessageBox.information(self, 'PS3 upload benchmark', message)
            self.refresh(tab)
        self.run_session_job(tab, 'PS3 benchmark (8 MiB test upload)', perform,
            done, transfer=True)
    def pump_queue(self, tab):
        state = self.sessions.get(tab)
        if not state or not state.connected or state.busy or state.closing: return
        sid = str(id(tab))
        item = self.queue.next_for(sid)
        if item is None: return
        self.render_queue()

        settings = TransferSettings(self.bandwidth_limit.value(),
                                    self.retry_limit.value(), self.backoff_delay.value(),
                                    self.chunk_select.currentData())
        item.attempts = 1
        verify_checksums = self.verify_checksums.isChecked()
        item.speed_bps = 0.
        item.eta_seconds = None
        self.transfer_metrics.pop(item.id, None)

        def perform(item, progress):
            backend = state.backend
            verify_enabled = verify_checksums and item.direction in ('upload', 'download')
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
                old_dir = backend.pwd()
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
                                                                  partial, lambda: state.cancelled)
                        return resilient_upload(backend, Path(item.source), item.destination,
                                                progress, lambda: state.cancelled, settings,
                                                verify_staged=staged_check if verify_enabled else None,
                                                on_phase=lambda stage: progress('@stage:' + stage, None))
                    result = resilient_download(backend, item.source, Path(item.destination),
                                                progress, lambda: state.cancelled, settings)
                    return result
                finally:
                    try: backend.cwd(old_dir)
                    except (OSError, EOFError, RuntimeError): pass

            def reconnect():
                # An interrupted FTP data connection may desynchronize its
                # control channel. Re-establish instead of issuing commands
                # against an untrustworthy session.
                tab.reconnect_fn()

            def on_retry(attempt_no, wait, why):
                item.attempts = attempt_no + 1
                # Qt signal transports state change to the GUI thread.
                progress(item.count, item.total)

            result = with_retries(attempt, reconnect, settings, lambda: state.cancelled,
                                  on_retry=on_retry)
            digest = verified_digest[0]
            if verify_enabled and item.direction == 'download':
                progress('@stage:Verifying', None)
                old_dir = backend.pwd()
                try:
                    backend.cwd(item.remote_dir)
                    digest = verify_transfer(backend, 'download', Path(item.destination), item.source,
                                             lambda: state.cancelled)
                finally:
                    try: backend.cwd(old_dir)
                    except (OSError, EOFError, RuntimeError): pass
            progress('@stage:Finalizing', None)
            return (result, digest)

        def operation(progress):
            if not item.children:
                return perform(item, progress)
            root = Path(item.local_dir)
            next_update = 0.0
            for index, child in enumerate(item.children):
                if state.cancelled:
                    raise InterruptedError('Folder download cancelled')
                target = Path(child.destination)
                safe_local_target(root, target.relative_to(root).as_posix())
                now = time.monotonic()
                if now >= next_update:
                    progress(index, len(item.children))
                    progress(f'@stage:{index + 1}/{len(item.children)} · {target.name}', None)
                    next_update = now + 0.15
                def child_progress(count, total):
                    nonlocal next_update
                    now = time.monotonic()
                    if now < next_update:
                        return
                    if isinstance(count, str):
                        detail = count.removeprefix('@stage:')
                    elif total:
                        detail = f'{min(100, count * 100 // total)}%'
                    else:
                        detail = f'{count:,} bytes'
                    progress(f'@stage:{index + 1}/{len(item.children)} · {target.name} · {detail}', None)
                    next_update = now + 0.15

                try:
                    perform(child, child_progress)
                except InterruptedError:
                    progress(index, len(item.children))
                    raise
                except Exception as exc:
                    progress(index, len(item.children))
                    raise RuntimeError(f'{target.relative_to(root)}: {exc}') from exc
            progress(len(item.children), len(item.children))
            return (None, None)

        def after(result):
            self.queue.finish(item)
            self.record_transfer(item, state, result[1])
            if item.children:
                item.count = len(item.children)
                summary = f'{item.name} · {len(item.children)} items · {item.destination}'
                if item.skipped:
                    summary += f' · {item.skipped} skipped'
                self.notify('success', 'Folder download complete', summary)
            else:
                self.notify('success', 'Transfer complete', f'{item.name} · {item.direction}')
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
            # Interrupted transfer can leave FTP/SFTP channel state uncertain.
            # Stop only this session's pending jobs and close it after the worker ends.
            self.queue.cancel_pending_for(sid)
            for cancelled_job in self.queue.jobs:
                if cancelled_job.session_id == sid and cancelled_job.status == 'Cancelled':
                    self.record_transfer(cancelled_job, state)
            self.render_queue()
            return False

        # run_session_job calls the error handler before its own transfer cleanup.
        self.run_session_job(tab, f'{"Folder download" if item.children else item.direction.title()}: {item.name}', operation,
                             after, on_error, transfer=True,
                             progress_callback=lambda count, total: self.on_transfer_progress(item, count, total))
    def on_transfer_progress(self, item, count, total):
        if isinstance(count, str) and count.startswith('@stage:'):
            item.phase = count[7:]
            item.speed_bps = 0.
            item.eta_seconds = None
            self.render_queue()
            return
        self.queue.progress(item, count, total)
        if item.children:
            self.render_queue()
            return
        estimator = self.transfer_metrics.get(item.id)
        if estimator is None:
            estimator = ETAEstimator()
            self.transfer_metrics[item.id] = estimator
        item.speed_bps, item.eta_seconds = estimator.update(count, total, time.monotonic())
        if self._queue_redraw_throttle.due(finished=(total is not None and count >= total)):
            self.render_queue()
