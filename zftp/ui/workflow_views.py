"""WorkflowViewsMixin: isolated Z FTP UI behavior, attached to the same main window."""
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


class WorkflowViewsMixin:
    @staticmethod
    def endpoint_for(tab):
        state = tab.state
        return [state.protocol, state.host, str(state.port), state.username]
    def copy_to_server(self, source_tab):
        if source_tab not in self.sessions or not source_tab.state.connected:
            return
        entry = source_tab.selected()
        if not entry or entry.is_dir or getattr(entry, 'is_symlink', False):
            QMessageBox.information(self, 'Select a file', 'Select one regular remote file first. Folder copying comes in a later release.')
            return
        targets = [t for t in self.sessions if t is not source_tab and t.state.connected and not t.state.closing]
        if not targets:
            QMessageBox.information(self, 'Connect another server', 'Open another server tab first.')
            return
        labels = [f'{t.state.protocol} · {t.state.host}:{t.state.port} ({t.state.username}) — {t.state.remote_path}' for t in targets]
        label, ok = QInputDialog.getItem(self, 'Remote-to-remote copy', 'Destination server', labels, 0, False)
        if not ok: return
        dest_tab = targets[labels.index(label)]
        new_name, ok = QInputDialog.getText(self, 'Copy file', 'Destination file name', text=entry.name)
        if not ok: return
        from zftp.transfers.cross_server import safe_filename
        try: safe_filename(new_name)
        except ValueError as exc:
            QMessageBox.warning(self, 'Unsafe name', str(exc)); return
        if QMessageBox.question(self, 'Confirm cross-server copy',
             f'Copy {entry.name} from {source_tab.state.host} to {dest_tab.state.host} as {new_name}?\n'
             'Data passes through private local temporary storage. Existing destination files are never overwritten.',
             QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return
        step = dict(action='copy_remote', source_endpoint=self.endpoint_for(source_tab),
                    destination_endpoint=self.endpoint_for(dest_tab),
                    source_dir=source_tab.state.remote_path, source_name=entry.name,
                    destination_dir=dest_tab.state.remote_path, destination_name=new_name)
        self.start_cross_step(source_tab, dest_tab, step)
    def start_cross_step(self, source_tab, dest_tab, step, workflow_id=None):
        from zftp.services.workflows import _validate_step
        try: step = _validate_step(step)
        except ValueError as exc:
            QMessageBox.warning(self, 'Invalid cross-server transfer', str(exc)); return False
        if source_tab is dest_tab or source_tab not in self.sessions or dest_tab not in self.sessions:
            return False
        src, dst = source_tab.state, dest_tab.state
        if (not src.connected or not dst.connected or src.busy or dst.busy or
            src.closing or dst.closing or self.endpoint_for(source_tab) != step['source_endpoint'] or
            self.endpoint_for(dest_tab) != step['destination_endpoint'] or
            any(j.status in ('Queued', 'Running') and j.session_id in (str(id(source_tab)), str(id(dest_tab)))
                for j in self.queue.jobs)):
            return False
        try:
            source_token = src.begin()
            dest_token = dst.begin()
        except RuntimeError:
            src.complete(source_token)
            return False
        for tab in (source_tab, dest_tab):
            tab.cancel.setProperty('transfer_active', True)
            tab.progress.setRange(0, 100)
            tab.progress.setValue(0)
            tab.status.setText('Cross-server transfer running…')
            tab.set_controls()
        self.update_active()
        transfer = Transfer(str(id(source_tab)), 'remote_copy',
                            f"{src.host}:{step['source_dir']}/{step['source_name']}",
                            f"{dst.host}:{step['destination_dir']}/{step['destination_name']}",
                            step['source_dir'], '')
        transfer.status = 'Running'
        transfer.attempts = 1
        self.cross_operations.append(transfer)
        self.render_cross_operations()
        settings = TransferSettings(self.bandwidth_limit.value(), 0, 0)
        def run(progress):
            def emit(phase, done, total):
                # Preserve source/destination phase in progress signal.
                progress((phase, done), total)
            return copy_file(src.backend, dst.backend, source_dir=step['source_dir'],
                             source_name=step['source_name'], destination_dir=step['destination_dir'],
                             destination_name=step['destination_name'], settings=settings,
                             cancelled=lambda: src.cancelled or dst.cancelled, progress=emit,
                             verify=True)
        job = Job(run); self.jobs.append(job)
        def report(data, total):
            phase, count = data
            transfer.count, transfer.total = count, total
            for tab in (source_tab, dest_tab):
                tab.status.setText('Cross-server copy · ' + phase.replace('_', ' '))
                tab.progress.setRange(0, 100 if total else 0)
                if total: tab.progress.setValue(min(100, int(count * 100 / total)))
            self.render_cross_operations()
        job.events.progress.connect(report)
        def finish(result=None, error=None):
            src.complete(source_token); dst.complete(dest_token)
            for tab in (source_tab, dest_tab):
                tab.cancel.setProperty('transfer_active', False)
                tab.status.setText('Ready' if error is None else 'Copy failed — review connection')
                tab.set_controls()
            if job in self.jobs: self.jobs.remove(job)
            transfer.status = 'Completed' if error is None else ('Cancelled' if isinstance(error, InterruptedError) else 'Failed')
            transfer.error = str(error or '')
            if result: transfer.count = result['bytes']
            try:
                self.history.record(transfer, src.host + ' → ' + dst.host,
                                    src.protocol + ' → ' + dst.protocol,
                                    result.get('sha256') if result else None)
                self.refresh_history()
            except OSError as exc: self.log('History write failed: ' + str(exc))
            self.render_cross_operations()
            if workflow_id:
                if error is None:
                    self.workflows.advance(workflow_id)
                else:
                    self.workflows.pause(workflow_id, str(error))
                self.refresh_workflows()
            if error is not None:
                # Failed data-channel operation can desynchronize either session.
                # Require explicit reconnection instead of reusing it silently.
                for state in (src, dst): state.connected = False
                for tab in (source_tab, dest_tab): tab.set_controls()
                self.notify('error', 'Cross-server transfer failed', str(error))
            else:
                self.notify('success', 'Cross-server copy completed',
                            step['source_name'] + ' → ' + dst.host)
            self.update_active()
            if error is None:
                if workflow_id and workflow_id in self.workflow_running:
                    if self.workflows.get(workflow_id)['state'] == 'completed':
                        self.workflow_running.discard(workflow_id)
                        self.refresh(dest_tab)
                    else:
                        QTimer.singleShot(0, lambda: self.continue_workflow(workflow_id))
                else:
                    if workflow_id:
                        self.workflows.pause(workflow_id, 'Paused by user after completed step') if self.workflows.get(workflow_id)['state'] == 'ready' else None
                        self.refresh_workflows()
                    self.refresh(dest_tab)
                    QTimer.singleShot(0, lambda: self.pump_queue(source_tab))
                    QTimer.singleShot(0, lambda: self.pump_queue(dest_tab))
            if workflow_id and error is not None: self.workflow_running.discard(workflow_id)
        job.events.done.connect(lambda result: finish(result))
        job.events.failed.connect(lambda error: finish(error=error))
        self.pool.start(job)
        return True
    def render_cross_operations(self):
        # Separate from per-server TransferQueue: a copy reserves two sessions.
        self.cross_table.setRowCount(min(len(self.cross_operations), 200))
        for row, op in enumerate(self.cross_operations[-200:]):
            for col, value in enumerate((op.source, op.destination, op.status,
                                          str(op.count), op.error)):
                self.cross_table.setItem(row, col, QTableWidgetItem(str(value)))
    def refresh_workflows(self):
        self.workflow_rows = self.workflows.all()
        previous = self.selected_workflow()['id'] if hasattr(self, 'workflow_rows') and self.selected_workflow() else None
        self.workflow_table.setRowCount(len(self.workflow_rows))
        for row, item in enumerate(self.workflow_rows):
            for col, value in enumerate((item['name'], f"{item['cursor']}/{len(item['steps'])}",
                                         item['state'], item['last_error'])):
                self.workflow_table.setItem(row, col, QTableWidgetItem(str(value)))
            if item['id'] == previous: self.workflow_table.selectRow(row)
    def selected_workflow(self):
        row = self.workflow_table.currentRow()
        return self.workflow_rows[row] if 0 <= row < len(self.workflow_rows) else None
    def workflow_endpoints(self):
        # The editor is not tied to a particular tab. Only currently connected
        # identities are used for newly created steps; saved steps can be edited
        # without reconnecting because their identities are included in dialog.
        identities = []
        for tab in self.sessions:
            if tab.state.connected:
                endpoint = self.endpoint_for(tab)
                if endpoint not in identities:
                    identities.append(endpoint)
        return identities
    def create_workflow(self):
        dialog = WorkflowDialog(self.workflow_endpoints(), parent=self)
        if dialog.exec() != QDialog.Accepted: return
        try:
            self.workflows.add(dialog.name.text(), dialog.steps)
        except (ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, 'Invalid workflow', str(exc)); return
        self.refresh_workflows()
    def edit_selected_workflow(self):
        item = self.selected_workflow()
        if not item: return
        if item['state'] != 'ready' or item['cursor'] != 0:
            QMessageBox.information(self, 'Workflow already started',
                'Only workflows that have never run can have steps edited. '
                'Duplicate this workflow to create an editable copy.'); return
        dialog = WorkflowDialog(self.workflow_endpoints(), item['name'], item['steps'], self)
        if dialog.exec() != QDialog.Accepted: return
        try:
            if not self.workflows.replace_steps(item['id'], dialog.steps):
                raise ValueError('Workflow state changed; refresh and retry')
            self.workflows.rename(item['id'], dialog.name.text())
        except (ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, 'Cannot edit workflow', str(exc)); return
        self.refresh_workflows()
    def duplicate_selected_workflow(self):
        item = self.selected_workflow()
        if not item: return
        try:
            self.workflows.duplicate(item['id'])
        except ValueError as exc:
            QMessageBox.warning(self, 'Cannot duplicate workflow', str(exc)); return
        self.refresh_workflows()
    def run_selected_workflow(self):
        item = self.selected_workflow()
        if not item or item['state'] == 'completed': return
        if item['state'] == 'paused':
            if QMessageBox.question(self, 'Review interrupted workflow',
                 'Before resuming, verify the last target file was not created. Continue the pending step?',
                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return
            self.workflows.resume(item['id'])
        self.workflow_running.add(item['id'])
        self.continue_workflow(item['id'])
    def continue_workflow(self, ident):
        if ident not in self.workflow_running: return
        item = self.workflows.get(ident)
        if not item or item['state'] == 'completed':
            self.workflow_running.discard(ident)
            self.refresh_workflows()
            return
        if item['state'] != 'ready': return
        step = item['steps'][item['cursor']]
        src = [t for t in self.sessions if t.state.connected and self.endpoint_for(t) == step['source_endpoint']]
        dst = [t for t in self.sessions if t.state.connected and self.endpoint_for(t) == step['destination_endpoint']]
        if len(src) != 1 or len(dst) != 1:
            self.workflows.pause(ident, 'Expected exactly one connected tab for each server identity')
            self.workflow_running.discard(ident); self.refresh_workflows()
            return
        if not self.workflows.claim(ident): return
        if not self.start_cross_step(src[0], dst[0], step, workflow_id=ident):
            self.workflows.pause(ident, 'One server is busy; reconnect/review then resume manually')
            self.workflow_running.discard(ident)
            self.refresh_workflows()
    def pause_selected_workflow(self):
        item = self.selected_workflow()
        if item:
            self.workflow_running.discard(item['id'])
            # Active steps finish; pause is applied before the next step.
            if item['state'] == 'ready': self.workflows.pause(item['id'], 'Paused by user')
            self.refresh_workflows()
    def remove_selected_workflow(self):
        item = self.selected_workflow()
        if item and QMessageBox.question(self, 'Delete workflow',
               'Delete workflow definition? Files on servers are not deleted.',
               QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            if not self.workflows.delete(item['id']):
                QMessageBox.information(self, 'Workflow running', 'Finish the active step first.')
            self.workflow_running.discard(item['id'])
            self.refresh_workflows()
    def record_transfer(self, item, state, digest=None):
        try:
            self.history.record(item, state.host, state.protocol, digest)
            self.refresh_history()
        except OSError as exc:
            self.log('History could not be written: ' + str(exc))
    def refresh_history(self):
        rows = self.history.recent(200)
        self.history_table.setRowCount(len(rows))
        for n, record in enumerate(rows):
            for column, value in enumerate((record['timestamp'], record['server'],
                                           Path(record['source']).name, record['status'],
                                           record['sha256'] or 'Not verified')):
                self.history_table.setItem(n, column, QTableWidgetItem(str(value)))
    def export_history(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export transfer history',
                                              str(Path.home() / 'zftp-history.csv'), 'CSV (*.csv)')
        if not path: return
        try:
            self.history.export_csv(Path(path))
            self.notify('success', 'History exported', path)
        except OSError as exc:
            QMessageBox.warning(self, 'Export failed', str(exc))
    def refresh_schedules(self):
        self.schedule_rows = self.schedules.all()
        self.schedules_table.setRowCount(len(self.schedule_rows))
        for row, job in enumerate(self.schedule_rows):
            when = time.strftime('%Y-%m-%d %H:%M', time.localtime(job['next_run']))
            repeat = ('Once' if not job['repeat_seconds'] else f"Every {job['repeat_seconds'] // 60} min")
            for col, value in enumerate((job['label'], job['action'], when, repeat,
                                         job['state'] + (': ' + job['last_error'] if job['last_error'] else ''))):
                self.schedules_table.setItem(row, col, QTableWidgetItem(str(value)))
    def selected_schedule(self):
        row = self.schedules_table.currentRow()
        return self.schedule_rows[row] if 0 <= row < len(self.schedule_rows) else None
    def toggle_schedule(self):
        job = self.selected_schedule()
        if job:
            self.schedules.set_enabled(job['id'], job['state'] != 'enabled')
            self.refresh_schedules()
    def remove_schedule(self):
        job = self.selected_schedule()
        if job and QMessageBox.question(self, 'Remove schedule', f"Remove {job['label']}?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.schedules.delete(job['id'])
            self.refresh_schedules()
    def add_schedule(self):
        tab = self.current_tab()
        if not tab or not tab.state.connected:
            QMessageBox.information(self, 'Connect first', 'Open the target server tab before adding a schedule.')
            return
        labels = {'Upload one local file': 'upload', 'Download one remote file': 'download',
                  'Sync missing local → remote': 'sync_upload',
                  'Sync missing remote → local': 'sync_download',
                  'Two-way sync missing files': 'sync_two_way'}
        selected, ok = QInputDialog.getItem(self, 'Schedule an operation', 'Task:', list(labels), 0, False)
        if not ok: return
        action = labels[selected]
        local = str(self.local_path)
        remote = tab.state.remote_path
        if action == 'upload':
            local, _ = QFileDialog.getOpenFileName(self, 'Select file for scheduled upload', local)
            if not local: return
            remote = posixpath.join(remote, clean_remote_name(Path(local).name))
        elif action == 'download':
            name, ok = QInputDialog.getText(self, 'Remote filename', 'File in current remote directory:')
            if not ok: return
            try: remote = posixpath.join(remote, clean_remote_name(name))
            except ValueError as exc:
                QMessageBox.warning(self, 'Invalid remote name', str(exc)); return
            local = str(Path(local) / name)
        minutes, ok = QInputDialog.getInt(self, 'Start time', 'Minutes from now:', 5, 1, 525600)
        if not ok: return
        interval, ok = QInputDialog.getInt(self, 'Repeat interval',
            'Repeat every N minutes (0 = one time):', 0, 0, 525600)
        if not ok: return
        if action.startswith('sync_'):
            if QMessageBox.question(self, 'Allow scheduled synchronization',
                'This schedule can add missing files/folders automatically while Z FTP is running. '
                'Existing files will be skipped, never deleted or overwritten. Continue?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        state = tab.state
        endpoint = self.schedules.endpoint(state.protocol, state.host, state.port, state.username)
        self.schedules.add(selected, action, endpoint, local, remote, time.time() + minutes * 60,
                           interval * 60)
        self.refresh_schedules()
        self.notify('info', 'Schedule created', f'{selected} · {state.host}')
    def tick_schedules(self):
        # Schedules do not auto-connect or store credentials; a matching tab
        # must already be connected. Run each action at most once per tick.
        for spec in self.schedules.due():
            for tab, state in tuple(self.sessions.items()):
                endpoint = self.schedules.endpoint(state.protocol, state.host, state.port, state.username)
                if (endpoint != spec['endpoint'] or not state.connected or state.busy
                    or state.closing or any(j.status in ('Queued', 'Running') and
                                            j.session_id == str(id(tab)) for j in self.queue.jobs)):
                    continue
                if not self.schedules.claim(spec['id']): break
                try:
                    self.dispatch_schedule(tab, spec)
                except Exception as exc:
                    self.schedules.finish(spec['id'], False, str(exc))
                    self.notify('error', 'Schedule paused', str(exc))
                self.refresh_schedules()
                break
    def dispatch_schedule(self, tab, spec):
        action = spec['action']
        sid = str(id(tab))
        local, remote = spec['local_path'], spec['remote_path']
        if action in ('upload', 'download'):
            if action == 'upload':
                if not Path(local).is_file() or Path(local).is_symlink():
                    raise FileNotFoundError('Scheduled source is unavailable or unsafe')
                direction, source, destination, local_dir = 'upload', local, posixpath.basename(remote), str(Path(local).parent)
            else:
                if Path(local).exists() or Path(local).is_symlink():
                    raise FileExistsError('Scheduled destination already exists; schedule paused')
                if not Path(local).parent.is_dir() or Path(local).parent.is_symlink():
                    raise ValueError('Scheduled download directory is unavailable or unsafe')
                direction, source, destination, local_dir = 'download', posixpath.basename(remote), local, str(Path(local).parent)
            task = Transfer(sid, direction, source, destination, posixpath.dirname(remote), local_dir)
            self.queue.add(task)
            self.schedules.finish(spec['id'], True)
            self.render_queue()
            self.pump_queue(tab)
        else:
            mode = {'sync_upload':'upload','sync_download':'download','sync_two_way':'two-way'}[action]
            local_root, remote_root = Path(local), remote
            def scan(_):
                return plan(inventory_local(local_root, lambda: tab.state.cancelled),
                            inventory_remote(tab.state.backend, remote_root,
                                             lambda: tab.state.cancelled), mode)
            def done(changes):
                accepted, rejected = self.enqueue_changes(tab, changes, remote_root, local_root, 'skip', silent=True)
                self.schedules.finish(spec['id'], not bool(rejected), '; '.join(rejected[:6]))
                self.refresh_schedules()
            def failure(exc):
                self.schedules.finish(spec['id'], False, str(exc))
                self.refresh_schedules()
                return False
            if not self.run_session_job(tab, 'Scheduled directory comparison', scan, done, failure):
                raise RuntimeError('Server connection is unavailable')
