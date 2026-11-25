"""Remote file tools backed by the existing per-tab worker and backup flow."""
from difflib import unified_diff
from itertools import islice
from hashlib import sha256
from pathlib import Path
import posixpath

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QInputDialog,
    QLabel, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from zftp.services.ps3_file_tools import CONFIG_LINE, missing_plugins, plugin_paths
from zftp.services.format_preview import HEADER_LIMIT, inspect_package
from zftp.services.remote_management import MAX_EDITOR_BYTES, RemoteConflictError, safe_save_remote_bytes, validate_name

HEX_LIMIT = 64 * 1024
IMAGE_LIMIT = MAX_EDITOR_BYTES


def table_view(parent, title, headers, rows, note=''):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(760, 480)
    layout = QVBoxLayout(dialog)
    if note:
        label = QLabel(note)
        label.setWordWrap(True)
        layout.addWidget(label)
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(value))
    table.horizontalHeader().setStretchLastSection(True)
    layout.addWidget(table)
    close = QDialogButtonBox(QDialogButtonBox.Close)
    close.rejected.connect(dialog.reject)
    layout.addWidget(close)
    dialog.exec()


def config_editor(parent, text):
    dialog = QDialog(parent)
    dialog.setWindowTitle('Config editor')
    dialog.resize(720, 460)
    layout = QVBoxLayout(dialog)
    lines = text.splitlines(keepends=True)
    entries = [(i, match) for i, line in enumerate(lines) if (match := CONFIG_LINE.fullmatch(line))]
    if not entries:
        raise ValueError('No supported key=value entries found. Use the text editor for this configuration.')
    table = QTableWidget(len(entries), 3)
    table.setHorizontalHeaderLabels(['Line', 'Key', 'Value'])
    table.horizontalHeader().setStretchLastSection(True)
    for row, (index, match) in enumerate(entries):
        prefix, value, _ = match.groups()
        for col, content in enumerate((str(index + 1), prefix.split('=', 1)[0].strip(), value)):
            item = QTableWidgetItem(content)
            if col != 2:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, col, item)
    layout.addWidget(table)
    buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.Accepted:
        return None
    for row, (index, match) in enumerate(entries):
        value = table.item(row, 2).text()
        if any(char in value for char in '\r\n\0'):
            raise ValueError('Config values must fit on one line.')
        if value != match[2]:
            lines[index] = match[1] + value + (match[3] or '')
    return ''.join(lines)


def text_editor(parent, title, text, note):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(730, 520)
    layout = QVBoxLayout(dialog)
    label = QLabel(note)
    label.setWordWrap(True)
    layout.addWidget(label)
    editor = QPlainTextEdit(text)
    layout.addWidget(editor)
    buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return editor.toPlainText() if dialog.exec() == QDialog.Accepted else None


def decode_image(raw, *, replacement=False):
    backing = QBuffer()
    backing.setData(QByteArray(raw))
    backing.open(QIODevice.ReadOnly)
    reader = QImageReader(backing)
    reader.setDecideFormatFromContent(True)
    size = reader.size()
    kind = bytes(reader.format()).lower()
    limit = (320, 176) if replacement else (4096, 4096)
    formats = (b'png',) if replacement else (b'png', b'jpg', b'jpeg')
    if len(raw) > IMAGE_LIMIT or kind not in formats or not size.isValid() or size.width() > limit[0] or size.height() > limit[1]:
        raise ValueError('Expected a PNG up to 320×176 and 1 MiB.' if replacement else
                         'Expected a PNG or JPEG up to 4096×4096 and 1 MiB.')
    image = reader.read()
    if image.isNull():
        raise ValueError('The image could not be decoded.')
    return image


class Ps3FileToolsMixin:
    def remote_ps3_tools(self, menu, tab, entry):
        if entry is None or entry.is_dir or entry.is_link:
            return
        tools = menu.addMenu('File tools')
        name = entry.name.lower()
        if name.endswith(('.ini', '.cfg', '.conf')):
            tools.addAction('Edit config', lambda: self.open_ps3_tool(tab, entry, 'config'))
        if name == 'boot_plugins.txt':
            tools.addAction('Edit boot plugins', lambda: self.open_ps3_tool(tab, entry, 'boot'))
        if name.endswith('.pkg'):
            tools.addAction('Inspect PKG header', lambda: self.open_ps3_tool(tab, entry, 'pkg'))
        if name.endswith(('.png', '.jpg', '.jpeg')):
            tools.addAction('Preview image', lambda: self.open_ps3_tool(tab, entry, 'image'))
        tools.addAction('View / edit hex', lambda: self.open_ps3_tool(tab, entry, 'hex'))

    def open_ps3_tool(self, tab, entry, tool):
        state = self.sessions.get(tab)
        if state is None:
            return
        name = validate_name(entry.name)
        limit = HEX_LIMIT if tool == 'hex' else MAX_EDITOR_BYTES
        def read(_):
            backend = state.backend
            if tool == 'pkg':
                return inspect_package(backend.read_prefix(name, HEADER_LIMIT), entry.size)
            return backend.pwd(), backend.read_bytes(name, limit)

        def opened(result):
            if self.sessions.get(tab) is not state:
                return
            if tool == 'pkg':
                table_view(self, name, ['Field', 'Value'], result,
                           'Header inspection shows metadata and size agreement.')
                return
            folder, raw = result
            try:
                revised, summary, plugins = self._edit_ps3_tool(name, tool, raw)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, 'Cannot edit file', str(exc))
                return
            if revised is None or revised == raw:
                return
            if len(revised) > limit:
                QMessageBox.warning(self, 'Too large', 'The edited file exceeds the size limit.')
                return
            if QMessageBox.question(self, 'Confirm remote edit',
                    f'{posixpath.join(folder, name)}\n\n{summary}\n\nSave with a remote backup?',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return

            def save(_):
                backend = state.backend
                if backend.pwd() != folder:
                    raise RemoteConflictError('Remote folder changed. Open the file again.')
                if plugins is not None:
                    missing = missing_plugins(backend, plugins)
                    if missing:
                        raise ValueError('Missing or unreadable plugins:\n' + '\n'.join(missing))
                return safe_save_remote_bytes(backend, name, sha256(raw).hexdigest(), revised)

            def saved(backup):
                QMessageBox.information(self, 'File saved', f'Original backup:\n{posixpath.join(folder, backup)}')
                self.refresh(tab)
            if not self.run_session_job(tab, 'Saving with backup', save, saved):
                QMessageBox.information(self, 'Connection busy', 'Finish the current operation and reopen this file.')

        self.run_session_job(tab, 'Opening remote file', read, opened)

    def _edit_ps3_tool(self, name, tool, raw):
        if tool == 'image':
            image = decode_image(raw)
            dialog = QDialog(self)
            dialog.setWindowTitle(name)
            layout = QVBoxLayout(dialog)
            label = QLabel()
            label.setPixmap(QPixmap.fromImage(image).scaled(720, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            layout.addWidget(label)
            close = QPushButton('Close')
            close.clicked.connect(dialog.reject)
            layout.addWidget(close)
            if name.upper() == 'ICON0.PNG':
                replace = QPushButton('Replace icon…')
                replace.clicked.connect(dialog.accept)
                layout.addWidget(replace)
            if dialog.exec() != QDialog.Accepted:
                return None, '', None
            filename, _ = QFileDialog.getOpenFileName(self, 'Choose a replacement icon', '', 'PNG images (*.png)')
            if not filename:
                return None, '', None
            with Path(filename).open('rb') as source:
                revised = source.read(IMAGE_LIMIT + 1)
            replacement = decode_image(revised, replacement=True)
            return revised, f'Replace {image.width()}×{image.height()} with {replacement.width()}×{replacement.height()} PNG.', None
        if tool == 'hex':
            hex_text = '\n'.join(raw[i:i + 16].hex(' ') for i in range(0, len(raw), 16))
            value = text_editor(self, 'Hex viewer / editor', hex_text, '16 bytes per row. Edits must keep the same byte count.')
            if value is None:
                return None, '', None
            revised = bytes.fromhex(value)
            if len(revised) != len(raw):
                raise ValueError('Hex edits must keep the original byte count.')
            edits = [(i, old, new) for i, (old, new) in enumerate(zip(raw, revised)) if old != new]
            summary = [f'{len(edits)} changed bytes'] + [f'{offset:08X}: {old:02X} → {new:02X}' for offset, old, new in edits[:24]]
            return revised, '\n'.join(summary), None
        old = raw.decode('utf-8')
        if '\0' in old or old.startswith('\ufeff'):
            raise ValueError('This editor expects UTF-8 text without a BOM or NUL bytes.')
        if tool == 'config':
            value = config_editor(self, old)
            plugins = None
        else:
            value = text_editor(self, 'Boot plugins', old, 'One absolute .sprx path per line. Reorder lines to change load order.')
            if value is not None and '\r\n' in old:
                value = value.replace('\n', '\r\n')
            plugins = plugin_paths(value) if value is not None else None
            if plugins is not None and len(plugins) > 64:
                raise ValueError('Too many plugin paths to validate.')
        if value is None:
            return None, '', None
        diff = list(islice(unified_diff(old.splitlines(), value.splitlines(), lineterm=''), 31))
        preview = '\n'.join(line[:240] for line in diff[:30])
        if len(diff) > 30:
            preview += '\n… More changes'
        return value.encode('utf-8'), preview, plugins
