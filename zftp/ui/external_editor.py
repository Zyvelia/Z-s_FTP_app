"""Edit a temporary text copy in VS Code and approve a diff before saving."""

from difflib import unified_diff
import os
from pathlib import Path
import re
from shutil import which
import subprocess
from tempfile import TemporaryDirectory

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout

from zftp.services.file_preview import read_local_copy
from zftp.services.remote_management import MAX_EDITOR_BYTES, identify_text


def find_vscode():
    if os.name != 'nt':
        return which('code')
    roots = [Path(base) / subdir for variable, subdir in (
        ('LOCALAPPDATA', 'Programs/Microsoft VS Code'),
        ('ProgramFiles', 'Microsoft VS Code'),
        ('ProgramFiles(x86)', 'Microsoft VS Code'),
    ) if (base := os.environ.get(variable))]
    for directory in os.get_exec_path():
        path = Path(directory.strip('"'))
        if path.is_absolute():
            roots.append(path.parent if path.name.casefold() == 'bin' else path)
    for root in roots:
        candidate = root / 'Code.exe'
        if candidate.is_file():
            return str(candidate)
    return None


def reviewed_text(name, original, text_copy, edited):
    text = edited.decode('utf-8-sig')
    if text == text_copy.content:
        return original, ''
    if '\0' in text:
        raise ValueError('Edited text contains a NUL character.')
    payload = text_copy.bom + text.encode(text_copy.encoding)
    if len(payload) > MAX_EDITOR_BYTES:
        raise ValueError('Edited file exceeds the 1 MiB upload limit.')
    changes = unified_diff(text_copy.content.splitlines(keepends=True), text.splitlines(keepends=True),
                           fromfile=f'Remote: {name}', tofile=f'Edited: {name}')
    return payload, '\n'.join(
        line.removesuffix('\n').replace('\r', '␍') +
        ('' if line.endswith(('\n', '\r')) else '\n\\ No newline at end of file')
        for line in changes
    )


class ExternalEditorDialog(QDialog):
    def __init__(self, parent, name, location, original, executable, save):
        text_copy = identify_text(original, strict_controls=True)
        initial = text_copy.content.encode('utf-8')
        if len(initial) > MAX_EDITOR_BYTES:
            raise ValueError('The UTF-8 editing copy would exceed 1 MiB.')
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f'Edit in VS Code · {name}')
        self.resize(800, 535)
        self.save = save
        self.saving = False
        self.reviewed = None
        self.local_snapshot = None

        layout = QVBoxLayout(self)
        location_label = QLabel(location)
        location_label.setTextFormat(Qt.PlainText)
        location_label.setWordWrap(True)
        layout.addWidget(location_label)
        self.status = QLabel('Save the temporary file in VS Code, then click Review changes.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.diff.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        layout.addWidget(self.diff, 1)
        actions = QHBoxLayout()
        review = QPushButton('Review changes')
        self.upload = QPushButton('Upload reviewed changes')
        self.upload.setEnabled(False)
        close = QPushButton('Close')
        actions.addWidget(review)
        actions.addStretch()
        actions.addWidget(close)
        actions.addWidget(self.upload)
        layout.addLayout(actions)
        close.clicked.connect(self.close)
        review.clicked.connect(self.review)
        self.upload.clicked.connect(self.publish)

        self.temporary = TemporaryDirectory(prefix='zftp-edit-', ignore_cleanup_errors=True)
        # Retain safe names for VS Code's language detection, including dotfiles.
        if not re.fullmatch(r'[.a-zA-Z0-9_-]{1,128}', name) or name.endswith('.') or Path(name).is_reserved():
            suffix = Path(name).suffix
            local_name = 'remote-copy' + (suffix if re.fullmatch(r'\.[a-zA-Z0-9_-]{1,16}', suffix) else '.txt')
        else:
            local_name = name
        self.path = Path(self.temporary.name) / local_name
        self.original = original
        self.initial = initial
        self.text_copy = text_copy
        self.name = name
        try:
            self.path.write_bytes(initial)
            subprocess.Popen([executable, '--new-window', '--disable-extensions', str(self.path)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, ValueError):
            self.temporary.cleanup()
            self.deleteLater()
            raise
        self.finished.connect(lambda _: self.temporary.cleanup())

    def read_copy(self):
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError('The temporary copy is missing or has been replaced.')
        return read_local_copy(self.path, MAX_EDITOR_BYTES)

    def review(self):
        self.upload.setEnabled(False)
        self.reviewed = None
        self.local_snapshot = None
        self.diff.clear()
        try:
            raw = self.read_copy()
            payload, changes = reviewed_text(self.name, self.original, self.text_copy, raw)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Could not review changes', str(error))
            return
        if payload == self.original:
            self.status.setText('No saved changes. Save the file in VS Code and review again.')
            return
        self.diff.setPlainText(changes)
        self.status.setText('Review the changes below. Uploading keeps the original as a remote backup.')
        self.reviewed, self.local_snapshot = payload, raw
        self.upload.setEnabled(True)

    def publish(self):
        if self.reviewed is None:
            return
        try:
            changed = self.read_copy() != self.local_snapshot
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Could not read edited copy', str(error))
            return
        if changed:
            self.upload.setEnabled(False)
            self.status.setText('The local copy changed. Review the latest changes before uploading.')
            return
        self.saving = True
        self.setEnabled(False)
        if not self.save(self, self.reviewed):
            self.save_failed()

    def save_failed(self):
        self.saving = False
        self.setEnabled(True)

    def saved(self):
        self.saving = False
        self.accept()

    def reject(self):
        if self.saving:
            return
        try:
            changed = self.read_copy() != self.initial
        except (OSError, ValueError):
            changed = self.path.exists()
        if changed and QMessageBox.question(
            self, 'Discard local edits?', 'Close and remove the temporary copy? The edits will be discarded.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        super().reject()
