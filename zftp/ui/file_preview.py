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
from zftp.services.remote_management import identify_text
from zftp.ui.format_preview import show_delimited


class FilePreviewDialog(QDialog):
    def __init__(self, name, data, save, *, sqlite_in_use=False, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(name)
        self.resize(780, 530)
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
            self.viewer = QPlainTextEdit()
            self.viewer.setReadOnly(True)
            try:
                self.text_copy = identify_text(data, strict_controls=not is_text_name(name))
                content = self.text_copy.content
                self.hint.setText('Text file')
            except ValueError:
                shown = data[:HEX_LIMIT]
                content = '\n'.join(shown[i:i + 16].hex(' ') for i in range(0, len(shown), 16))
                self.viewer.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
                if len(data) > HEX_LIMIT:
                    self.hint.setText('Hex preview of the first 64 KiB. This file is too large for hex editing.')
                    self.edit.setEnabled(False)
                else:
                    self.hint.setText('Hex bytes. Edits must keep the original file size.')
            self.viewer.setPlainText(content)
            self.contents = self.viewer.toPlainText()
            layout.addWidget(self.viewer, 1)
            if self.text_copy and Path(name).suffix.lower() in ('.csv', '.tsv'):
                view_table = QPushButton('Preview table')
                view_table.clicked.connect(lambda: show_delimited(self, name, self.viewer.toPlainText()))
                buttons.insertWidget(0, view_table)
        layout.addLayout(buttons)

    def _enable_editing(self):
        self.editing = True
        self.edit.setEnabled(False)
        self.save_button.setEnabled(True)
        if self.sqlite:
            self._fill_table()
        else:
            self.viewer.setReadOnly(False)

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
