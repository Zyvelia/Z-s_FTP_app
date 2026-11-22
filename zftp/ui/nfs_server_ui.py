"""Manage a single writable NFSv3 export through WinNFSd on Windows."""

from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
import sys

from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtNetwork import QNetworkInterface
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)


EXPORT = '/ps4'
PRIVATE_NETWORKS = tuple(IPv4Network(network) for network in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
))


class NFSServerPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._supported = sys.platform == 'win32'
        self._stopping = False
        self._bind_address = None
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.started.connect(self._started)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.stop_timer = QTimer(self)
        self.stop_timer.setSingleShot(True)
        self.stop_timer.setInterval(5000)
        self.stop_timer.timeout.connect(self.process.kill)

        layout = QVBoxLayout(self)
        title = QLabel('NFS Server')
        title.setObjectName('sectionHeading')
        layout.addWidget(title)
        requirements = QLabel(
            'Writable NFSv3 hosting on Windows. Select an official '
            '<a href="https://github.com/winnfsd/winnfsd/releases">WinNFSd.exe</a> '
            'and a dedicated folder on your PC.')
        requirements.setOpenExternalLinks(True)
        requirements.setWordWrap(True)
        layout.addWidget(requirements)

        self.settings = QWidget()
        form = QFormLayout(self.settings)
        self.executable = QLineEdit()
        self.executable.setReadOnly(True)
        self.executable.setPlaceholderText('Select WinNFSd.exe')
        executable_row = QHBoxLayout()
        executable_row.addWidget(self.executable, 1)
        browse_executable = QPushButton('Browse…')
        browse_executable.clicked.connect(self._browse_executable)
        executable_row.addWidget(browse_executable)
        form.addRow('WinNFSd', executable_row)

        self.folder = QLineEdit()
        self.folder.setReadOnly(True)
        self.folder.setPlaceholderText('Choose a folder for your dumps')
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        browse_folder = QPushButton('Browse…')
        browse_folder.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_folder)
        form.addRow('Shared folder', folder_row)

        self.address = QComboBox()
        address_row = QHBoxLayout()
        address_row.addWidget(self.address, 1)
        refresh = QPushButton('Refresh')
        refresh.clicked.connect(self.refresh_addresses)
        address_row.addWidget(refresh)
        form.addRow('PC LAN address', address_row)
        self.start_button = QPushButton('Start writable share')
        self.start_button.setObjectName('primaryAction')
        self.start_button.clicked.connect(self.start)
        form.addRow('', self.start_button)
        layout.addWidget(self.settings)

        access = QLabel('NFS has no password. Use a trusted private network and stop sharing when done.')
        access.setWordWrap(True)
        layout.addWidget(access)

        url_row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setReadOnly(True)
        self.url.setPlaceholderText('NFS URL for Itemzflow')
        self.url.setAccessibleName('NFS share URL')
        url_row.addWidget(self.url, 1)
        self.copy_url = QPushButton('Copy URL')
        self.copy_url.clicked.connect(lambda: QApplication.clipboard().setText(self.url.text()))
        url_row.addWidget(self.copy_url)
        self.stop_button = QPushButton('Stop sharing')
        self.stop_button.clicked.connect(self.stop)
        url_row.addWidget(self.stop_button)
        layout.addLayout(url_row)

        self.status = QLabel('Stopped' if self._supported else 'NFS hosting is available on Windows.')
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(200)
        self.output.setPlaceholderText('WinNFSd output appears here.')
        layout.addWidget(self.output, 1)

        self.address.currentIndexChanged.connect(self._update_controls)
        self.folder.textChanged.connect(self._update_controls)
        self.executable.textChanged.connect(self._update_controls)
        self.refresh_addresses()
        self._update_controls()

    @property
    def active_or_stopping(self):
        return self.process.state() != QProcess.NotRunning

    @property
    def running(self):
        return self.process.state() == QProcess.Running

    def refresh_addresses(self):
        previous = self.address.currentData()
        self.address.clear()
        self.address.addItem('Select a private IPv4 address', None)
        if not self._supported:
            return
        for interface in QNetworkInterface.allInterfaces():
            if not interface.flags() & QNetworkInterface.IsUp:
                continue
            for entry in interface.addressEntries():
                try:
                    address = IPv4Address(entry.ip().toString())
                except ValueError:
                    continue
                if any(address in network for network in PRIVATE_NETWORKS):
                    self.address.addItem(f'{address} · {interface.humanReadableName()}', str(address))
        index = self.address.findData(previous) if previous else -1
        if index >= 0:
            self.address.setCurrentIndex(index)
        elif self.address.count() == 2:
            self.address.setCurrentIndex(1)

    def _browse_executable(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, 'Select WinNFSd.exe', self.executable.text(), 'Windows executable (*.exe)')
        if filename:
            self.executable.setText(filename)

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select NFS share folder', self.folder.text())
        if folder:
            self.folder.setText(folder)

    def _update_controls(self, *_):
        active = self.active_or_stopping
        self.settings.setEnabled(self._supported and not active)
        self.start_button.setEnabled(bool(self.address.currentData() and self.folder.text() and self.executable.text()))
        self.stop_button.setEnabled(active and not self._stopping)
        self.copy_url.setEnabled(self.running and not self._stopping)

    def start(self):
        if not self._supported or self.active_or_stopping:
            return

        self.refresh_addresses()
        address = self.address.currentData()
        executable = Path(self.executable.text())
        folder = Path(self.folder.text())
        if not address:
            self.status.setText('Choose the private IPv4 address on the same network as your PS4.')
            return
        if not executable.is_absolute() or executable.name.lower() != 'winnfsd.exe' or not executable.is_file():
            self.status.setText('Select the WinNFSd.exe file from the official release.')
            return
        if not folder.is_absolute() or not folder.is_dir():
            self.status.setText('Choose an existing folder for the share.')
            return
        try:
            folder = folder.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            self.status.setText(f'Could not open the share folder. {error}')
            return
        if folder == Path(folder.anchor) or str(folder).startswith('\\\\'):
            self.status.setText('Choose a folder on a local drive below the drive root.')
            return

        self._bind_address = address
        self.output.clear()
        self.url.clear()
        self.status.setText('Starting WinNFSd…')
        self.process.setProgram(str(executable))
        self.process.setArguments(['-addr', address, str(folder), EXPORT])
        self.process.start()
        self._update_controls()

    def _started(self):
        if not self._stopping:
            # Use the console command without hiding a console inherited by WinNFSd.
            self.process.write(b'log off\n')
            self.url.setText(f'nfs://{self._bind_address}{EXPORT}')
            self.status.setText('WinNFSd running. Connect using the NFS URL.')
        self._update_controls()

    def _read_output(self):
        data = self.process.readAllStandardOutput().data()
        if data:
            self.output.moveCursor(QTextCursor.End)
            self.output.insertPlainText(data.decode(errors='replace'))

    def stop(self):
        if not self.active_or_stopping or self._stopping:
            return
        self._stopping = True
        self.status.setText('Stopping WinNFSd…')
        # WinNFSd asks for confirmation when a client is mounted.
        self.process.write(b'quit\ny\n')
        self.stop_timer.start()
        self._update_controls()

    def _finished(self, exit_code, _exit_status):
        self.stop_timer.stop()
        self._read_output()
        if self._stopping:
            self.status.setText('Stopped')
        else:
            self.status.setText(
                f'WinNFSd exited (code {exit_code}). Check the output and whether another NFS server is running.')
        self._stopping = False
        self.url.clear()
        self._update_controls()

    def _process_error(self, error):
        if error == QProcess.FailedToStart:
            self.stop_timer.stop()
            self._stopping = False
            self.url.clear()
            self.status.setText(f'Could not start WinNFSd. {self.process.errorString()}')
            self._update_controls()
