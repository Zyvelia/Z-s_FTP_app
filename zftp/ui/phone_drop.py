"""Phone Drop setup and desktop approval controls."""

from ipaddress import ip_address

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtNetwork import QNetworkInterface
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)
import qrcode

from zftp.servers.phone_drop import PhoneDrop, private_address


class PhoneDropPanel(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.window = parent
        self.receiver = None
        self.previous = ()
        layout = QVBoxLayout(self)
        settings = QFormLayout()
        self.address = QComboBox()
        self.folder = QLineEdit()
        self.folder.setReadOnly(True)
        settings.addRow('Local Wi-Fi address', self.address)
        folder_row = QHBoxLayout()
        self.browse = QPushButton('Choose folder…')
        self.browse.clicked.connect(self.choose_folder)
        folder_row.addWidget(self.folder)
        folder_row.addWidget(self.browse)
        settings.addRow('Save approved uploads to', folder_row)
        layout.addLayout(settings)
        self.address.addItem('Select a LAN address', None)
        addresses = {address.toString() for address in QNetworkInterface.allAddresses()}
        for address in sorted(value for value in addresses if private_address(value) and not ip_address(value).is_loopback):
            self.address.addItem(address, address)

        controls = QHBoxLayout()
        self.start_button = QPushButton('Start Phone Drop')
        self.start_button.setObjectName('primaryAction')
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton('Stop Phone Drop')
        self.stop_button.clicked.connect(self.stop)
        self.copy_button = QPushButton('Copy phone link')
        self.copy_button.clicked.connect(self.copy_link)
        for button in (self.start_button, self.stop_button, self.copy_button):
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignCenter)
        self.qr.setAccessibleName('QR code for opening Phone Drop')
        layout.addWidget(self.qr)
        self.status = QLabel('Scan the QR code from a phone on the same Wi-Fi. Approve each file here before it uploads.')
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.requests = QListWidget()
        self.requests.setAccessibleName('Phone Drop requests')
        self.requests.currentItemChanged.connect(self.update_controls)
        layout.addWidget(self.requests, 1)
        decisions = QHBoxLayout()
        self.approve = QPushButton('Approve selected upload')
        self.deny = QPushButton('Decline')
        self.approve.clicked.connect(lambda: self.decide(True))
        self.deny.clicked.connect(lambda: self.decide(False))
        decisions.addWidget(self.approve)
        decisions.addWidget(self.deny)
        decisions.addStretch(1)
        layout.addLayout(decisions)
        self.timer = QTimer(self, interval=350)
        self.timer.timeout.connect(self.refresh)
        self.address.currentIndexChanged.connect(self.update_controls)
        self.folder.textChanged.connect(self.update_controls)
        self.update_controls()

    @property
    def active_or_stopping(self):
        return self.receiver is not None

    def choose_folder(self):
        directory = QFileDialog.getExistingDirectory(self, 'Choose Phone Drop destination', self.folder.text())
        if directory:
            self.folder.setText(directory)

    def start(self):
        if self.receiver or not self.address.currentData() or not self.folder.text():
            return
        try:
            self.receiver = PhoneDrop(self.address.currentData(), self.folder.text())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Could not start Phone Drop', str(error))
            return
        code = qrcode.QRCode(box_size=5, border=4)
        code.add_data(self.receiver.url)
        code.make()
        matrix = code.get_matrix()
        side = len(matrix)
        image = QImage(side, side, QImage.Format_RGB32)
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                image.setPixel(x, y, 0xff000000 if dark else 0xffffffff)
        self.qr.setPixmap(QPixmap.fromImage(image).scaled(side * 5, side * 5))
        self.status.setText('Waiting for requests. Every upload needs your approval.')
        self.timer.start()
        self.update_controls()

    def stop(self):
        if self.receiver is None:
            return
        self.receiver.stop()
        self.receiver = None
        self.timer.stop()
        self.previous = ()
        self.requests.clear()
        self.qr.clear()
        self.status.setText('Phone Drop stopped. Its QR code and link have expired.')
        self.update_controls()

    def copy_link(self):
        if self.receiver:
            QApplication.clipboard().setText(self.receiver.url)

    def refresh(self, *, force=False):
        records = tuple(self.receiver.snapshot()) if self.receiver else ()
        if not force and records == self.previous:
            return
        selected = self.requests.currentItem()
        selected_id = selected.data(Qt.UserRole) if selected else None
        self.previous = records
        self.requests.clear()
        for identifier, ip, name, size, state in records:
            item = QListWidgetItem(f'{name} · {self.window.display_size(size)} · {ip} · {state.title()}')
            item.setData(Qt.UserRole, identifier)
            item.setData(Qt.UserRole + 1, state)
            self.requests.addItem(item)
            if identifier == selected_id:
                self.requests.setCurrentItem(item)
        if self.requests.currentItem() is None and self.requests.count():
            self.requests.setCurrentRow(0)
        self.update_controls()

    def update_controls(self, *_):
        active = self.receiver is not None
        self.start_button.setEnabled(not active and bool(self.address.currentData()) and bool(self.folder.text()))
        self.address.setEnabled(not active)
        self.browse.setEnabled(not active)
        self.stop_button.setEnabled(active)
        self.copy_button.setEnabled(active)
        self.qr.setVisible(active)
        item = self.requests.currentItem()
        pending = active and item is not None and item.data(Qt.UserRole + 1) == 'pending'
        self.approve.setEnabled(pending)
        self.deny.setEnabled(pending)

    def decide(self, accepted):
        item = self.requests.currentItem()
        if self.receiver and item:
            try:
                self.receiver.decide(item.data(Qt.UserRole), accepted)
            except OSError as error:
                QMessageBox.warning(self, 'Upload could not be approved', str(error))
            self.refresh()
