"""Dedicated SSH/SFTP hosting page with in-memory accounts and worker-backed controls."""
from __future__ import annotations

from threading import Thread
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QWidget)

from zftp.servers.server_core import ServerAccount
from zftp.servers.sftp_server_core import (SFTPServerConfig, SFTPServerController,
    generate_host_key, host_key_fingerprint, validate_sftp_setup)

from zftp.ui.server_clients import clients_button


class SFTPEvents(QObject):
    log = Signal(str)
    started = Signal()
    stopped = Signal()
    failed = Signal(str)


class SFTPServerPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.events = SFTPEvents()
        self.controller = SFTPServerController(self.events.log.emit)
        self.accounts: list[ServerAccount] = []
        self._busy = False
        self._worker = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('SSH/SFTP hosting — OFF by default. Key remains on disk; user passwords remain in memory.'))
        fields = QFormLayout()
        self.host = QComboBox(); self.host.addItems(['127.0.0.1', '0.0.0.0'])
        self.port = QSpinBox(); self.port.setRange(1024, 65535); self.port.setValue(2222)
        self.remote = QCheckBox('Explicitly allow LAN/remote SSH connections (configure firewall yourself)')
        self.key = QLineEdit(); self.key.setPlaceholderText('Path to existing SSH RSA host key')
        self.browse_key = QPushButton('Select host key…')
        self.generate_key = QPushButton('Generate new host key…')
        self.fingerprint = QLabel('Host key fingerprint: select or generate a key')
        self.fingerprint.setWordWrap(True)
        self.max_clients = QSpinBox(); self.max_clients.setRange(1, 30); self.max_clients.setValue(5)
        self.per_ip = QSpinBox(); self.per_ip.setRange(1, 30); self.per_ip.setValue(3)
        fields.addRow('Bind address', self.host)
        fields.addRow('SSH port', self.port)
        fields.addRow('', self.remote)
        fields.addRow('Host private key', self.key)
        key_row = QHBoxLayout(); key_row.addWidget(self.browse_key); key_row.addWidget(self.generate_key)
        fields.addRow('', key_row)
        fields.addRow('', self.fingerprint)
        fields.addRow('Maximum connections', self.max_clients)
        fields.addRow('Per-IP connections', self.per_ip)
        layout.addLayout(fields)
        layout.addWidget(QLabel('SFTP accounts (not saved to disk)'))
        self.account_list = QListWidget(); layout.addWidget(self.account_list, 1)
        account_actions = QHBoxLayout()
        self.add_btn = QPushButton('Add user')
        self.remove_btn = QPushButton('Remove selected user')
        account_actions.addWidget(self.add_btn); account_actions.addWidget(self.remove_btn); account_actions.addStretch()
        layout.addLayout(account_actions)
        control_row = QHBoxLayout()
        self.start_btn = QPushButton('Start SFTP server')
        self.stop_btn = QPushButton('Stop SFTP server')
        self.status = QLabel('Stopped')
        control_row.addWidget(self.start_btn); control_row.addWidget(self.stop_btn); control_row.addWidget(self.status, 1)
        control_row.addWidget(clients_button(self))
        layout.addLayout(control_row)
        layout.addWidget(QLabel('SSH/SFTP activity (current session only)'))
        self.activity = QTextEdit(); self.activity.setReadOnly(True); self.activity.setMaximumHeight(140)
        layout.addWidget(self.activity)
        self.export_btn = QPushButton('Export activity log…'); layout.addWidget(self.export_btn)
        self.browse_key.clicked.connect(self.select_key)
        self.generate_key.clicked.connect(self.create_key)
        self.key.editingFinished.connect(self.update_fingerprint)
        self.add_btn.clicked.connect(self.add_user)
        self.remove_btn.clicked.connect(self.remove_user)
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self.export_btn.clicked.connect(self.export_log)
        self.events.log.connect(self.activity.append)
        self.events.started.connect(self.started)
        self.events.stopped.connect(self.stopped)
        self.events.failed.connect(self.failed)
        self.update_buttons()

    @property
    def active_or_stopping(self):
        return self._busy or self.controller.running

    def update_buttons(self):
        busy = self.active_or_stopping
        self.start_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(self.controller.running and not self._busy)
        for widget in (self.host, self.port, self.remote, self.key, self.browse_key, self.generate_key,
                       self.max_clients, self.per_ip, self.add_btn, self.remove_btn):
            widget.setEnabled(not busy)

    def select_key(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select SSH host private key', str(Path.home()))
        if path:
            self.key.setText(path)
            self.update_fingerprint()

    def create_key(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Create new SSH host key (never overwrite)',
                                               str(Path.home() / '.zftp_ssh_host_rsa'))
        if not path:
            return
        try:
            fingerprint = generate_host_key(path)
        except Exception as exc:
            QMessageBox.warning(self, 'Host key creation failed', str(exc)); return
        self.key.setText(path)
        self.fingerprint.setText('Host key fingerprint: ' + fingerprint)
        QMessageBox.information(self, 'Host key generated',
            'Keep this key private and back it up securely. Clients will remember its fingerprint; changing it later causes warnings.')

    def update_fingerprint(self):
        if not self.key.text().strip():
            self.fingerprint.setText('Host key fingerprint: select or generate a key'); return
        try:
            self.fingerprint.setText('Host key fingerprint: ' + host_key_fingerprint(self.key.text().strip()))
        except Exception:
            self.fingerprint.setText('Host key fingerprint unavailable (invalid key or missing dependency)')

    def add_user(self):
        dialog = QDialog(self); dialog.setWindowTitle('Add SFTP user')
        fields = QFormLayout(dialog)
        username = QLineEdit()
        password = QLineEdit(); password.setEchoMode(QLineEdit.Password)
        password.setPlaceholderText('At least 12 characters')
        home = QLineEdit()
        browse = QPushButton('Choose shared folder…')
        browse.clicked.connect(lambda: home.setText(QFileDialog.getExistingDirectory(dialog, 'Share a folder') or home.text()))
        writable = QCheckBox('Read/write (uploads, removes, renames, new directories)')
        for label, widget in [('Username', username), ('Password', password), ('Shared folder', home), ('', browse), ('', writable)]:
            fields.addRow(label, widget)
        buttons = QHBoxLayout(); accept = QPushButton('Add'); cancel = QPushButton('Cancel')
        buttons.addWidget(accept); buttons.addWidget(cancel); fields.addRow(buttons)
        cancel.clicked.connect(dialog.reject)
        def add():
            from zftp.servers.server_core import validate_setup, ServerConfig
            account = ServerAccount(username.text().strip(), password.text(), home.text().strip(), writable.isChecked())
            try:
                validate_setup(ServerConfig(), self.accounts + [account])
            except ValueError as exc:
                QMessageBox.warning(dialog, 'Invalid account', str(exc)); return
            self.accounts.append(account)
            self.account_list.addItem(f'{account.username} — {account.home} — {"Read/write" if account.writable else "Read-only"}')
            dialog.accept()
        accept.clicked.connect(add)
        dialog.exec()

    def remove_user(self):
        row = self.account_list.currentRow()
        if row >= 0:
            self.accounts.pop(row); self.account_list.takeItem(row)

    def config(self):
        return SFTPServerConfig(host=self.host.currentText(), port=self.port.value(),
            host_key=self.key.text().strip(), allow_remote=self.remote.isChecked(),
            max_clients=self.max_clients.value(), max_per_ip=self.per_ip.value())

    def start(self):
        cfg = self.config()
        try:
            validate_sftp_setup(cfg, self.accounts)
            fingerprint = host_key_fingerprint(cfg.host_key)
        except Exception as exc:
            QMessageBox.warning(self, 'SFTP configuration', str(exc)); return
        if QMessageBox.question(self, 'Start SSH/SFTP hosting',
            f'Listen on {cfg.host}:{cfg.port}?\nHost key: {fingerprint}\n'
            f'{len(self.accounts)} user(s); remote access {"ALLOWED" if cfg.allow_remote else "restricted to loopback"}.\n'
            'Only share folders that contain intended files.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._busy = True; self.status.setText('Starting…'); self.update_buttons()
        def run():
            try: self.controller.start(cfg, list(self.accounts)); self.events.started.emit()
            except Exception as exc: self.events.failed.emit(str(exc))
        self._worker = Thread(target=run, name='sftp-start-action', daemon=False); self._worker.start()

    def stop(self):
        self._busy = True; self.status.setText('Stopping…'); self.update_buttons()
        def run():
            try: self.controller.stop(); self.events.stopped.emit()
            except Exception as exc: self.events.failed.emit(str(exc))
        self._worker = Thread(target=run, name='sftp-stop-action', daemon=False); self._worker.start()

    def started(self):
        self._busy = False; self.status.setText('Running'); self.update_buttons()

    def stopped(self):
        self._busy = False; self.status.setText('Stopped'); self.update_buttons()

    def failed(self, error):
        self._busy = False
        self.status.setText('Still running' if self.controller.running else 'Stopped / error')
        self.update_buttons()
        QMessageBox.warning(self, 'SFTP server error', error)

    def export_log(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export SFTP activity', 'zftp-sftp-activity.txt', 'Text files (*.txt)')
        if not path:
            return
        try:
            with open(path, 'x', encoding='utf-8') as out:
                out.write(self.activity.toPlainText() + '\n')
        except FileExistsError:
            QMessageBox.warning(self, 'Already exists', 'Choose a new filename; existing logs are never overwritten.')
        except OSError as exc:
            QMessageBox.warning(self, 'Export failed', str(exc))
