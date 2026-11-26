"""A modeless file viewer with opt-in editing and an explicit save."""

from pathlib import Path
import sqlite3

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from zftp.services.file_preview import HEX_LIMIT, SQLITE_HEADER, SQLiteCopy, is_text_name
from zftp.services.remote_management import EDITOR_ENCODINGS, decode_editor_copy
from zftp.ui.format_preview import show_delimited
from zftp.ui.text_editor import TextEditor, TextHighlighter
from zftp.ui.find_replace import FindReplace


class FilePreviewDialog(QDialog):
    def __init__(self, name, data, save, *, sqlite_in_use=False, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(name)
        self.resize(780, 530)
        self.name = name
        self.data = data
        self.save = save
        self.writable = save is not None
        self.editing = False
        self.saving = False
        self.sqlite = None
        self.table = None
        self.text_copy = None
        self.contents = None

        layout = QVBoxLayout(self)
        self.hint = QLabel()
        self.hint.setTextFormat(Qt.PlainText)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.edit = QPushButton('Edit')
        self.edit.clicked.connect(self._enable_editing)
        self.edit.setVisible(self.writable)
        self.save_button = QPushButton('Save with backup')
        self.save_button.setObjectName('primaryAction')
        self.save_button.setEnabled(False)
        self.save_button.setVisible(self.writable)
        self.save_button.clicked.connect(self._save)
        close = QPushButton('Close')
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.edit)
        buttons.addStretch()
        buttons.addWidget(close)
        buttons.addWidget(self.save_button)

        if data.startswith(SQLITE_HEADER):
            self.sqlite = SQLiteCopy(data)
            self.table_select = QComboBox()
            self.table_select.addItems(self.sqlite.tables())
            layout.addWidget(self.table_select)
            self.grid = QTableWidget()
            self.grid.setAlternatingRowColors(True)
            layout.addWidget(self.grid, 1)
            self.sqlite_in_use = sqlite_in_use
            self.table_select.currentTextChanged.connect(self._show_table)
            self._show_table(self.table_select.currentText())
        else:
            self.viewer = TextEditor()
            self.viewer.setReadOnly(True)
            layout.addWidget(self.viewer, 1)
            self.find_panel = FindReplace(self.viewer)
            layout.addWidget(self.find_panel)
            self.find_button = QPushButton('Find…')
            self.find_button.clicked.connect(self.find_panel.open_find)
            buttons.insertWidget(1, self.find_button)
            self.encoding = QComboBox()
            self.encoding.setAccessibleName('File encoding')
            for label, codec in EDITOR_ENCODINGS:
                self.encoding.addItem(label, codec)
            self.goto = QPushButton('Go to line…')
            self.goto.clicked.connect(self.viewer.go_to_line)
            self.table_button = QPushButton('Preview table')
            self.table_button.clicked.connect(lambda: show_delimited(self, name, self.viewer.toPlainText()))
            buttons.insertWidget(1, self.encoding)
            buttons.insertWidget(2, self.goto)
            buttons.insertWidget(3, self.table_button)
            self.highlighter = TextHighlighter(self.viewer.document(), name)
            self._encoding_index = 0
            try:
                self._load_copy(None)
            except ValueError:
                shown = self.data[:HEX_LIMIT]
                self._display_text('\n'.join(shown[i:i + 16].hex(' ') for i in range(0, len(shown), 16)))
            self.encoding.currentIndexChanged.connect(self._change_encoding)
        layout.addLayout(buttons)

    def _load_copy(self, encoding):
        self.text_copy = decode_editor_copy(self.data, encoding, strict_controls=not is_text_name(self.name))
        self._display_text(self.text_copy.content)

    def _display_text(self, content):
        is_text = self.text_copy is not None
        self.highlighter.setDocument(self.viewer.document() if is_text else None)
        self.viewer.gutter.setVisible(is_text)
        self.viewer.setPlainText(content)
        self.contents = self.viewer.toPlainText()
        self.goto.setVisible(is_text)
        self.table_button.setVisible(is_text and Path(self.name).suffix.lower() in ('.csv', '.tsv'))
        self.edit.setEnabled(not self.editing and (is_text or len(self.data) <= HEX_LIMIT))
        self.encoding.setVisible(is_text or is_text_name(self.name))
        if is_text:
            self.hint.setText(f'Text file · {self.text_copy.encoding.upper()}')
        elif len(self.data) > HEX_LIMIT:
            self.hint.setText('Hex preview of the first 64 KiB. This file is too large for hex editing.')
        else:
            self.hint.setText('Hex bytes. Edits must keep the original file size.')

    def _change_encoding(self, index):
        previous = self._encoding_index
        if self.editing and self.viewer.toPlainText() != self.contents:
            if QMessageBox.question(self, 'Change encoding',
                                    'Reload the original bytes using this encoding and discard unsaved edits?',
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                with QSignalBlocker(self.encoding):
                    self.encoding.setCurrentIndex(previous)
                return
        try:
            self._load_copy(self.encoding.itemData(index))
        except ValueError as error:
            QMessageBox.warning(self, 'Could not decode file', str(error))
            with QSignalBlocker(self.encoding):
                self.encoding.setCurrentIndex(previous)
        else:
            self._encoding_index = index

    def _enable_editing(self):
        self.editing = True
        self.edit.setEnabled(False)
        self.save_button.setEnabled(True)
        if self.sqlite:
            self._fill_table()
        else:
            self.viewer.setReadOnly(False)
            for widget in (self.find_panel.replacement, self.find_panel.replace_button, self.find_panel.replace_all_button):
                widget.setEnabled(True)

    def _show_table(self, name):
        previous = self.table.name if self.table else ''
        try:
            if self.editing and self.table is not None:
                self._apply_table()
            self.table = self.sqlite.read_table(name) if name else None
        except (ValueError, sqlite3.Error, OverflowError) as error:
            QMessageBox.warning(self, 'Could not open table', str(error))
            with QSignalBlocker(self.table_select):
                self.table_select.setCurrentText(previous)
            return
        self._fill_table()

    def _fill_table(self):
        table = self.table
        self.grid.clear()
        self.grid.setRowCount(len(table.rows) if table else 0)
        self.grid.setColumnCount(len(table.columns) if table else 0)
        if table:
            self.grid.setHorizontalHeaderLabels(table.columns)
            for row, values in enumerate(table.rows):
                for column, value in enumerate(values):
                    if isinstance(value, bytes):
                        text = f'BLOB ({len(value)} bytes)'
                    elif value is None:
                        text = 'NULL'
                    else:
                        text = str(value)
                    cell = QTableWidgetItem(text[:2048])
                    if (not self.editing or not table.rowid_alias or self.sqlite_in_use
                            or column in table.locked_columns or isinstance(value, bytes) or len(text) > 2048):
                        cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                    self.grid.setItem(row, column, cell)
        self.edit.setEnabled(bool(table and table.rowid_alias and not self.sqlite_in_use and not self.editing))
        if self.sqlite_in_use:
            self.hint.setText('Read-only snapshot. Close the application using this database before editing.')
        elif table and table.rowid_alias:
            self.hint.setText('Showing up to 100 rows. Key, generated, and binary fields are read-only.')
        elif table:
            self.hint.setText('Showing up to 100 rows. This table has no editable row identifier.')
        else:
            self.hint.setText('No ordinary SQLite tables to display.')

    def _apply_table(self):
        changes = []
        for row, values in enumerate(self.table.rows):
            for column, old in enumerate(values):
                cell = self.grid.item(row, column)
                if not cell.flags() & Qt.ItemIsEditable:
                    continue
                text = cell.text()
                if text == ('NULL' if old is None else str(old)):
                    continue
                if isinstance(old, int):
                    text = int(text)
                elif isinstance(old, float):
                    text = float(text)
                changes.append((row, column, text))
        if changes:
            data = self.sqlite.update(self.table, changes)
            self.sqlite = SQLiteCopy(data)
            self.table = self.sqlite.read_table(self.table.name)
            self._fill_table()

    def _save(self):
        try:
            if self.sqlite:
                if self.table:
                    self._apply_table()
                revised = self.sqlite.data
            else:
                text = self.viewer.toPlainText()
                if text == self.contents:
                    return
                revised = self.text_copy.encode(text) if self.text_copy else bytes.fromhex(text)
                if self.text_copy is None and len(revised) != len(self.data):
                    raise ValueError('Hex edits must keep the original byte count')
        except (ValueError, sqlite3.Error, OverflowError) as error:
            QMessageBox.warning(self, 'Could not apply edits', str(error))
            return
        if revised == self.data:
            return
        if QMessageBox.question(self, 'Save edited file',
                                f'Save changes to {self.windowTitle()}? The original will be kept as a backup.',
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.setEnabled(False)
        self.saving = True
        if not self.save(self, revised):
            self.save_failed()

    def save_failed(self):
        self.setEnabled(True)
        self.saving = False
        self.save_button.setEnabled(True)

    def saved(self):
        self.saving = False
        self.close()

    def closeEvent(self, event):
        if self.saving:
            event.ignore()
        else:
            super().closeEvent(event)
