from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit, QComboBox, QCheckBox, QPushButton, QHBoxLayout, QFileDialog, QDialogButtonBox
from zftp.services.profiles import Profile

class ProfileDialog(QDialog):
    def __init__(self, parent, profile=None):
        super().__init__(parent)
        self.setWindowTitle('Connection profile')
        self.profile = profile
        self.setMinimumWidth(440)
        form = QFormLayout(self)
        self.name = QLineEdit(profile.name if profile else '')
        self.host = QLineEdit(profile.host if profile else '')
        self.protocol = QComboBox(); self.protocol.addItems(['SFTP', 'FTPS', 'FTP'])
        self.protocol.setCurrentText(profile.protocol if profile else 'SFTP')
        self.port = QLineEdit(str(profile.port) if profile else '22')
        self.username = QLineEdit(profile.username if profile else '')
        self.key_path = QLineEdit(profile.key_path if profile else '')
        self.key_button = QPushButton('Browse key…')
        self.favorite = QCheckBox('Show at top of saved connections')
        self.favorite.setChecked(profile.favorite if profile else False)
        self.save_password = QCheckBox('Save password in operating system credential store')
        self.save_password.setChecked(profile.save_password if profile else False)
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password)
        if getattr(parent, 'streamer_enabled', False):
            for field in (self.name, self.host, self.port, self.username, self.key_path):
                field.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText('Leave blank to keep stored password' if profile and profile.save_password else 'Optional password')
        form.addRow('Profile name', self.name)
        form.addRow('Protocol', self.protocol)
        form.addRow('Server', self.host)
        form.addRow('Port', self.port)
        form.addRow('Username', self.username)
        key_row = QHBoxLayout(); key_row.addWidget(self.key_path); key_row.addWidget(self.key_button)
        form.addRow('SSH private key', key_row)
        form.addRow('', self.favorite)
        form.addRow('', self.save_password)
        form.addRow('Password', self.password)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        form.addRow(self.buttons)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.key_button.clicked.connect(self.browse_key)
        self.protocol.currentTextChanged.connect(self.update_protocol)
        self.update_protocol(self.protocol.currentText())

    def update_protocol(self, protocol):
        self.key_path.setEnabled(protocol == 'SFTP')
        self.key_button.setEnabled(protocol == 'SFTP')
        if not self.profile or self.protocol.currentText() != self.profile.protocol:
            self.port.setText('22' if protocol == 'SFTP' else '21')

    def browse_key(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Choose SSH private key')
        if filename: self.key_path.setText(filename)

    def build(self):
        port = int(self.port.text())
        result = Profile.create(self.name.text(), self.host.text(), port, self.protocol.currentText(),
                                self.username.text(), self.favorite.isChecked(),
                                self.key_path.text() if self.protocol.currentText() == 'SFTP' else '',
                                self.save_password.isChecked())
        if self.profile: result.id = self.profile.id
        return result


