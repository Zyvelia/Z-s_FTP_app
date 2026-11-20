"""Separate opt-in server control panel; does not modify client session lifecycle."""
from __future__ import annotations

from threading import Thread

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QWidget)
from zftp.servers.server_core import ServerAccount, ServerConfig, ServerController


class ServerSignals(QObject):
    log = Signal(str)
    started = Signal(str)
    stopped = Signal(str)
    failure = Signal(str)


class ServerPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = ServerSignals()
        self.controller = ServerController(emit=self.signals.log.emit)
        self.accounts: list[ServerAccount] = []
        self._busy = False
        self._worker = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Built-in server — OFF by default. Accounts exist in memory for this run only.'))
        settings = QFormLayout()
        self.host = QComboBox(); self.host.addItems(['127.0.0.1', '0.0.0.0'])
        self.port = QSpinBox(); self.port.setRange(1024, 65535); self.port.setValue(2121)
        self.ftps = QCheckBox('Require TLS for login and file data (FTPS)')
        self.remote_consent = QCheckBox('Allow LAN/remote access (FTPS only; firewall/router managed separately)')
        self.cert = QLineEdit(); self.cert.setPlaceholderText('Full path to PEM certificate')
        self.key = QLineEdit(); self.key.setPlaceholderText('Full path to PEM private key')
        self.passive_from = QSpinBox(); self.passive_from.setRange(1024,65535); self.passive_from.setValue(30000)
        self.passive_to = QSpinBox(); self.passive_to.setRange(1024,65535); self.passive_to.setValue(30019)
        settings.addRow('Bind address', self.host)
        settings.addRow('Control port', self.port)
        settings.addRow('', self.ftps)
        settings.addRow('', self.remote_consent)
        settings.addRow('TLS certificate', self.cert)
        settings.addRow('TLS private key', self.key)
        settings.addRow('Passive ports start', self.passive_from)
        settings.addRow('Passive ports end', self.passive_to)
        layout.addLayout(settings)
        layout.addWidget(QLabel('Accounts — passwords never appear in the account list or activity logs'))
        self.listing = QListWidget(); layout.addWidget(self.listing)
        arow = QHBoxLayout()
        self.add_btn = QPushButton('Add account')
        self.remove_btn = QPushButton('Remove account')
        arow.addWidget(self.add_btn); arow.addWidget(self.remove_btn); arow.addStretch()
        layout.addLayout(arow)
        controls = QHBoxLayout()
        self.start_btn = QPushButton('Start server')
        self.stop_btn = QPushButton('Stop server')
        self.status = QLabel('Stopped')
        controls.addWidget(self.start_btn); controls.addWidget(self.stop_btn); controls.addWidget(self.status, 1)
        layout.addLayout(controls)
        layout.addWidget(QLabel('Server activity (current session only)'))
        self.activity = QTextEdit(); self.activity.setReadOnly(True); self.activity.setMaximumHeight(155)
        layout.addWidget(self.activity)
        self.export_btn = QPushButton('Export activity log…')
        layout.addWidget(self.export_btn)
        self.export_btn.clicked.connect(self.export_log)
        self.add_btn.clicked.connect(self.add_account)
        self.remove_btn.clicked.connect(self.remove_account)
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self.signals.log.connect(self.activity.append)
        self.signals.started.connect(self._started)
        self.signals.stopped.connect(self._stopped)
        self.signals.failure.connect(self._failed)
        self.ftps.toggled.connect(self.update_buttons)
        self.host.currentTextChanged.connect(self.update_buttons)
        self.update_buttons()

    @property
    def active_or_stopping(self):
        return self._busy or self.controller.running

    def update_buttons(self):
        active = self._busy or self.controller.running
        self.start_btn.setEnabled(not active)
        self.stop_btn.setEnabled(active and self.controller.running and not self._busy)
        for widget in (self.host, self.port, self.ftps, self.remote_consent, self.cert, self.key,
                       self.passive_from, self.passive_to, self.add_btn, self.remove_btn):
            widget.setEnabled(not active)
        self.remote_consent.setEnabled(not active and self.ftps.isChecked())
        self.cert.setEnabled(not active and self.ftps.isChecked())
        self.key.setEnabled(not active and self.ftps.isChecked())

    def export_log(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export server activity', 'zftp-server-activity.txt', 'Text files (*.txt)')
        if path:
            try:
                with open(path, 'x', encoding='utf-8') as stream:
                    stream.write(self.activity.toPlainText() + '\n')
            except FileExistsError:
                QMessageBox.warning(self, 'File exists', 'Choose a new filename to avoid overwriting a previous log.')
            except OSError as exc:
                QMessageBox.warning(self, 'Export failed', str(exc))

    def add_account(self):
        dialog = QDialog(self); dialog.setWindowTitle('Add server user')
        form = QFormLayout(dialog)
        username = QLineEdit()
        password = QLineEdit(); password.setEchoMode(QLineEdit.Password)
        password.setPlaceholderText('Minimum 12 characters')
        home = QLineEdit(); browse = QPushButton('Browse folder…')
        browse.clicked.connect(lambda: home.setText(QFileDialog.getExistingDirectory(dialog, 'Choose shared folder') or home.text()))
        writable = QCheckBox('Allow uploads, renames, deletes and folder changes')
        form.addRow('Username', username); form.addRow('Password', password)
        form.addRow('Shared folder', home); form.addRow('', browse); form.addRow('', writable)
        buttons = QHBoxLayout(); ok = QPushButton('Add'); cancel = QPushButton('Cancel')
        buttons.addWidget(ok); buttons.addWidget(cancel); form.addRow(buttons)
        cancel.clicked.connect(dialog.reject)
        def save():
            from zftp.servers.server_core import validate_setup
            proposed = ServerAccount(username.text().strip(), password.text(), home.text(), writable.isChecked())
            try:
                # Validation with a local-only config ensures the same account rules.
                validate_setup(ServerConfig(), self.accounts + [proposed])
            except Exception as exc:
                QMessageBox.warning(dialog, 'Invalid user', str(exc)); return
            self.accounts.append(proposed)
            self.listing.addItem(f'{proposed.username} — {proposed.home} — {"Read/write" if proposed.writable else "Read only"}')
            dialog.accept()
        ok.clicked.connect(save); dialog.exec()

    def remove_account(self):
        row = self.listing.currentRow()
        if row >= 0:
            self.accounts.pop(row); self.listing.takeItem(row)

    def config(self):
        return ServerConfig(host=self.host.currentText(), port=self.port.value(), tls=self.ftps.isChecked(),
                            certfile=self.cert.text().strip(), keyfile=self.key.text().strip(),
                            passive_start=self.passive_from.value(), passive_end=self.passive_to.value(),
                            allow_remote_tls=self.remote_consent.isChecked())

    def start(self):
        from zftp.servers.server_core import validate_setup
        cfg = self.config()
        try:
            validate_setup(cfg, self.accounts)
        except Exception as exc:
            QMessageBox.warning(self, 'Server configuration', str(exc)); return
        if QMessageBox.question(self, 'Start server',
                f'Start {"FTPS" if cfg.tls else "FTP"} on {cfg.host}:{cfg.port}?\n'
                f'{len(self.accounts)} account(s); {"remote access permitted" if cfg.allow_remote_tls and cfg.tls else "local-only access"}.\n'
                'FTP without TLS is restricted to loopback. Ensure the selected shares contain only intended files.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._busy = True; self.status.setText('Starting…'); self.update_buttons()
        # Capture snapshot; never log/account-persist credentials.
        def task():
            try: self.controller.start(cfg, list(self.accounts)); self.signals.started.emit('Running')
            except Exception as exc: self.signals.failure.emit(str(exc))
        self._worker = Thread(target=task, name='zftp-server-start', daemon=False); self._worker.start()

    def stop(self):
        self._busy = True; self.status.setText('Stopping…'); self.update_buttons()
        def task():
            try: self.controller.stop(); self.signals.stopped.emit('Stopped')
            except Exception as exc: self.signals.failure.emit(str(exc))
        self._worker = Thread(target=task, name='zftp-server-stop', daemon=False); self._worker.start()

    def _started(self, label):
        self._busy = False; self.status.setText(label); self.update_buttons()

    def _stopped(self, label):
        self._busy = False; self.status.setText(label); self.update_buttons()

    def _failed(self, reason):
        self._busy = False; self.status.setText('Error / stopped' if not self.controller.running else 'Still running')
        self.update_buttons(); QMessageBox.warning(self, 'Server operation failed', reason)
