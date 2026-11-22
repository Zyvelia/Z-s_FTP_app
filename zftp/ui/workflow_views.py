"""WorkflowViewsMixin: isolated Z FTP UI behavior, attached to the same main window."""
from __future__ import annotations

import sqlite3
from html import escape
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, QTimer
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QTableWidgetItem

from zftp.services.workflows import _validate_step
from zftp.transfers.cross_server import copy_file
from zftp.transfers.reliable_transfer import TransferSettings
from zftp.transfers.transfer_queue import Transfer
from zftp.ui.ui_workers import Job
from zftp.ui.workflow_dialog import WorkflowDialog


class WorkflowViewsMixin:
    @staticmethod
    def endpoint_for(tab):
        state = tab.state
        return [state.protocol, state.host, str(state.port), state.username]
    def start_workflow_step(self, source_tab, dest_tab, step, workflow_id):
        try: step = _validate_step(step)
        except ValueError as exc:
            QMessageBox.warning(self, 'Invalid workflow step', str(exc)); return False
        if source_tab is dest_tab or source_tab not in self.sessions or dest_tab not in self.sessions:
            return False
        src, dst = source_tab.state, dest_tab.state
        if (not src.connected or not dst.connected or src.busy or dst.busy or
            src.closing or dst.closing or self.endpoint_for(source_tab) != step['source_endpoint'] or
            self.endpoint_for(dest_tab) != step['destination_endpoint'] or
            any(j.status in ('Queued', 'Running') and j.session_id in (str(id(source_tab)), str(id(dest_tab)))
                for j in self.queue.jobs)):
            return False
        source_token = src.begin()
        try:
            dest_token = dst.begin()
        except RuntimeError:
            src.complete(source_token)
            return False
        for tab in (source_tab, dest_tab):
            tab.cancel.setProperty('transfer_active', True)
            tab.progress.setRange(0, 100)
            tab.progress.setValue(0)
            tab.status.setText('Workflow step running…')
            tab.set_controls()
        self.update_active()
        transfer = Transfer(str(id(source_tab)), 'remote_copy',
                            f"{src.host}:{step['source_dir']}/{step['source_name']}",
                            f"{dst.host}:{step['destination_dir']}/{step['destination_name']}",
                            step['source_dir'], '')
        transfer.status = 'Running'
        transfer.attempts = 1
        settings = TransferSettings(self.bandwidth_limit.value(), 0, 0)
        verify_sha256 = self.verify_checksums.isChecked()
        def run(progress):
            def emit(phase, done, total):
                # Preserve source/destination phase in progress signal.
                progress((phase, done), total)
            return copy_file(src.backend, dst.backend, source_dir=step['source_dir'],
                             source_name=step['source_name'], destination_dir=step['destination_dir'],
                             destination_name=step['destination_name'], settings=settings,
                             cancelled=lambda: src.cancelled or dst.cancelled, progress=emit,
                             verify=verify_sha256)
        job = Job(run); self.jobs.append(job)
        def report(data, total):
            phase, count = data
            transfer.count, transfer.total = count, total
            for tab in (source_tab, dest_tab):
                tab.status.setText('Workflow copy · ' + phase.replace('_', ' '))
                tab.progress.setRange(0, 100 if total else 0)
                if total: tab.progress.setValue(min(100, int(count * 100 / total)))
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
            except (OSError, sqlite3.Error) as exc:
                self.log('History could not be written: ' + str(exc))
            if error is not None:
                self.workflows.pause(workflow_id, str(error))
                self.workflow_running.discard(workflow_id)
                # A failed data-channel operation can desynchronize either session.
                for state in (src, dst):
                    state.connected = False
                for tab in (source_tab, dest_tab):
                    tab.set_controls()
                self.notify('error', 'Workflow step failed', f"{step['source_name']} · {error}")
                self.update_active()
                self.refresh_workflows()
                return

            self.workflows.advance(workflow_id)
            workflow = self.workflows.get(workflow_id)
            if workflow['state'] == 'completed':
                self.workflow_running.discard(workflow_id)
                self.notify('success', 'Workflow complete', workflow['name'])
            elif workflow_id in self.workflow_running:
                self.log(f"Workflow step completed · {step['source_name']} → {dst.host}")
                QTimer.singleShot(0, lambda: self.continue_workflow(workflow_id))
            else:
                self.workflows.pause(workflow_id, 'Paused by user after completed step')
                self.notify('info', 'Workflow paused', 'The active step completed.')
            self.update_active()
            self.refresh_workflows()
            if workflow_id not in self.workflow_running:
                self.refresh(dest_tab)
                QTimer.singleShot(0, lambda: self.pump_queue(source_tab))
                QTimer.singleShot(0, lambda: self.pump_queue(dest_tab))
        job.events.done.connect(lambda result: finish(result))
        job.events.failed.connect(lambda error: finish(error=error))
        self.pool.start(job)
        return True
    def refresh_workflows(self, selected_id=None):
        selection = self.selected_workflow()
        if selected_id is None and selection:
            selected_id = selection['id']
        self.workflow_rows = self.workflows.all()
        with QSignalBlocker(self.workflow_table):
            self.workflow_table.clearSelection()
            self.workflow_table.setRowCount(len(self.workflow_rows))
            for row, workflow in enumerate(self.workflow_rows):
                status = workflow['state'].capitalize()
                if workflow['state'] == 'running' and workflow['id'] not in self.workflow_running:
                    status = 'Pausing'
                values = (workflow['name'], f"{workflow['cursor']} / {len(workflow['steps'])}", status)
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(value)
                    cell.setToolTip(escape(workflow['last_error'] or value))
                    self.workflow_table.setItem(row, column, cell)
                if workflow['id'] == selected_id:
                    self.workflow_table.selectRow(row)
        self.update_workflow_actions()

    def selected_workflow(self):
        selected = self.workflow_table.selectionModel().selectedRows()
        return self.workflow_rows[selected[0].row()] if selected else None

    def update_workflow_actions(self):
        workflow = self.selected_workflow()
        state = workflow['state'] if workflow else None
        active = workflow is not None and workflow['id'] in self.workflow_running
        self.workflow_run.setText('Resume' if state == 'paused' else 'Run')
        self.workflow_run.setEnabled(state in ('ready', 'paused') and not active)
        self.workflow_pause.setEnabled(active)
        self.workflow_edit.setEnabled(state == 'ready' and not active and workflow['cursor'] == 0)
        self.workflow_more.setEnabled(workflow is not None)
        self.workflow_duplicate.setEnabled(workflow is not None)
        self.workflow_remove.setEnabled(workflow is not None and state != 'running' and not active)
        if not self.workflow_rows:
            detail = 'Create a workflow to save a sequence of file copies.'
        elif workflow is None:
            detail = 'Select a workflow to see its status and available actions.'
        elif workflow['last_error']:
            detail = workflow['last_error']
        elif state == 'running' and not active:
            detail = 'Pausing when the current step finishes.'
        elif state == 'completed':
            detail = 'All steps complete.'
        else:
            step = workflow['steps'][workflow['cursor']]
            detail = f"{step['source_name']} · {step['source_endpoint'][1]} → {step['destination_endpoint'][1]}"
        self.workflow_summary.setText(detail)
        self.workflow_summary.setToolTip(escape(detail))

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
            ident = self.workflows.add(dialog.name.text(), dialog.steps)
        except (ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, 'Invalid workflow', str(exc)); return
        self.refresh_workflows(ident)
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
            ident = self.workflows.duplicate(item['id'])
        except ValueError as exc:
            QMessageBox.warning(self, 'Cannot duplicate workflow', str(exc)); return
        self.refresh_workflows(ident)
    def run_selected_workflow(self):
        item = self.selected_workflow()
        if not item or not self.workflow_run.isEnabled(): return
        if item['state'] == 'paused':
            if QMessageBox.question(self, 'Review interrupted workflow',
                 'Before resuming, verify the last target file was not created. Continue the pending step?',
                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return
            self.workflows.resume(item['id'])
        self.workflow_running.add(item['id'])
        self.log(f"Started workflow · {item['name']}")
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
        if not self.start_workflow_step(src[0], dst[0], step, workflow_id=ident):
            self.workflows.pause(ident, 'One server is busy; reconnect/review then resume manually')
            self.workflow_running.discard(ident)
        self.refresh_workflows()
    def pause_selected_workflow(self):
        item = self.selected_workflow()
        if item and self.workflow_pause.isEnabled():
            self.workflow_running.discard(item['id'])
            # Active steps finish; pause is applied before the next step.
            if item['state'] == 'ready': self.workflows.pause(item['id'], 'Paused by user')
            self.refresh_workflows()
    def remove_selected_workflow(self):
        item = self.selected_workflow()
        if item and self.workflow_remove.isEnabled() and QMessageBox.question(self, 'Delete workflow',
               'Delete workflow definition? Files on servers are not deleted.',
               QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            if not self.workflows.delete(item['id']):
                QMessageBox.information(self, 'Workflow running', 'Finish the active step first.')
            self.refresh_workflows()
    def record_transfer(self, item, state, digest=None):
        try:
            self.history.record(item, state.host, state.protocol, digest)
            self.refresh_history()
        except (OSError, sqlite3.Error) as exc:
            self.log('History could not be written: ' + str(exc))
    def set_transfer_details_visible(self, visible):
        self.transfer_details.setVisible(visible)
        if visible:
            self.transfer_details.raise_()
            self.transfer_details.activateWindow()
            self.refresh_history_if_visible()

    def show_history(self):
        self.bottom_tabs.setCurrentWidget(self.history_panel)
        if self.activity_toggle.isChecked():
            self.transfer_details.raise_()
            self.transfer_details.activateWindow()
        else:
            self.activity_toggle.setChecked(True)
        self.history_table.setFocus()

    def refresh_history_if_visible(self, *_):
        if self.bottom_tabs.isVisible() and self.bottom_tabs.currentWidget() is self.history_panel:
            self.refresh_history()

    def refresh_history(self):
        try:
            rows = self.history.recent(200)
        except (OSError, sqlite3.Error) as exc:
            self.history_summary.setText('Could not load transfer history')
            self.history_summary.setToolTip(escape(str(exc)))
            return

        self.history_table.setRowCount(len(rows))
        if rows:
            noun = 'transfer' if len(rows) == 1 else 'transfers'
            self.history_summary.setText(f'{len(rows)} recent {noun}')
        else:
            self.history_summary.setText('No transfers recorded yet')
        self.history_summary.setToolTip('Shows up to 200 recent transfer outcomes')
        self.history_export.setEnabled(bool(rows))
        for row, record in enumerate(rows):
            timestamp = record['timestamp'][:19].replace('T', ' ')
            source = record['source']
            name = Path(source).name or source
            values = (timestamp, record['server'], name, record['status'],
                      record['sha256'] or 'Not verified')
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column == 2:
                    cell.setToolTip(escape(source))
                elif column == 3:
                    cell.setToolTip(escape(record['error'] or value))
                else:
                    cell.setToolTip(escape(str(value)))
                self.history_table.setItem(row, column, cell)

    def export_history(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export transfer history',
                                              str(Path.home() / 'zftp-history.csv'), 'CSV (*.csv)')
        if not path: return
        try:
            self.history.export_csv(Path(path))
            self.notify('success', 'History exported', path)
        except (OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, 'Export failed', str(exc))
