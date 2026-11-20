"""Read and edit structured PS3 metadata."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QInputDialog,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


class SfoEditor(QDialog):
    def __init__(self, document, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle('PARAM.SFO Editor')
        self.resize(720, 490)
        self.document = document
        self.changes = {}
        layout = QVBoxLayout(self)
        location = QLabel(path)
        location.setTextFormat(Qt.PlainText)
        location.setWordWrap(True)
        layout.addWidget(location)
        self.table = QTableWidget(len(document.fields), 3)
        self.table.setHorizontalHeaderLabels(['Field', 'Type', 'Value'])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)
        for row, field in enumerate(document.fields):
            for column, value in enumerate((field.name, field.kind, field.value)):
                self.table.setItem(row, column, QTableWidgetItem(value))
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.edit = QPushButton('Edit value…')
        buttons.addButton(self.edit, QDialogButtonBox.ActionRole)
        self.save = buttons.addButton('Save with backup', QDialogButtonBox.AcceptRole)
        self.save.setEnabled(False)
        layout.addWidget(buttons)
        self.table.itemSelectionChanged.connect(self.update_controls)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_value())
        self.edit.clicked.connect(self.edit_value)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        if document.fields:
            self.table.selectRow(0)
        self.update_controls()

    def update_controls(self):
        row = self.table.currentRow()
        self.edit.setEnabled(row >= 0 and self.document.fields[row].editable)

    def edit_value(self):
        row = self.table.currentRow()
        if row < 0:
            return
        field = self.document.fields[row]
        if not field.editable:
            return
        current = self.changes.get(field.name, field.value)
        if field.kind == 'Text':
            limit = f'{field.capacity - 1} UTF-8 bytes maximum'
            value, accepted = QInputDialog.getMultiLineText(self, field.name, limit, current)
        else:
            value, accepted = QInputDialog.getText(self, field.name, 'Unsigned integer (decimal or 0x hex)', text=current)
        if not accepted:
            return
        try:
            field.encode(value)
        except ValueError as exc:
            QMessageBox.warning(self, 'Invalid SFO value', str(exc))
            return
        if value == field.value:
            self.changes.pop(field.name, None)
        else:
            self.changes[field.name] = value
        self.table.item(row, 2).setText(value)
        self.save.setEnabled(bool(self.changes))
