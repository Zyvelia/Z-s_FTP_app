"""Structured PySide6 sequential-copy workflow editor (no raw JSON required)."""
from __future__ import annotations
from copy import deepcopy
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QMessageBox, QListWidget, QFormLayout, QGroupBox)
from zftp.services.workflows import _validate_step


def format_endpoint(endpoint):
    protocol, host, port, user = endpoint
    return f'{protocol}  {user}@{host}:{port}'


def format_step(step):
    source = format_endpoint(step['source_endpoint'])
    target = format_endpoint(step['destination_endpoint'])
    source_path = f"{step['source_dir'].rstrip('/')}/{step['source_name']}"
    target_path = f"{step['destination_dir'].rstrip('/')}/{step['destination_name']}"
    return f"{source}  {source_path}\n→ {target}  {target_path}"


class StepDialog(QDialog):
    def __init__(self, endpoints, step=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Configure file-copy step')
        self.resize(760, 230)
        self.endpoints = [list(item) for item in endpoints]
        layout = QVBoxLayout(self)
        self.source = QComboBox()
        self.destination = QComboBox()
        for endpoint in self.endpoints:
            name = format_endpoint(endpoint)
            self.source.addItem(name, endpoint)
            self.destination.addItem(name, endpoint)
        self.source_dir = QLineEdit('/')
        self.source_name = QLineEdit()
        self.destination_dir = QLineEdit('/')
        self.destination_name = QLineEdit()
        locations = QHBoxLayout()
        for label, connection, directory, filename in (
            ('Copy from', self.source, self.source_dir, self.source_name),
            ('Copy to', self.destination, self.destination_dir, self.destination_name),
        ):
            group = QGroupBox(label)
            form = QFormLayout(group)
            form.addRow('Connection', connection)
            form.addRow('Directory', directory)
            form.addRow('Filename', filename)
            locations.addWidget(group, 1)
        layout.addLayout(locations)
        buttons = QHBoxLayout()
        cancel = QPushButton('Cancel')
        save = QPushButton('Save step')
        save.setObjectName('primaryAction')
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        save.clicked.connect(self._accept_checked)
        cancel.clicked.connect(self.reject)
        if step:
            for widget, key in ((self.source, 'source_endpoint'), (self.destination, 'destination_endpoint')):
                if list(step[key]) not in self.endpoints:
                    endpoint = list(step[key]); self.endpoints.append(endpoint)
                    for combo in (self.source, self.destination):
                        combo.addItem(format_endpoint(endpoint), endpoint)
                widget.setCurrentIndex(combo_index(widget, list(step[key])))
            for key in ('source_dir','source_name','destination_dir','destination_name'):
                getattr(self, key).setText(step[key])
        elif len(self.endpoints) >= 2:
            self.destination.setCurrentIndex(1)
        self.source_name.textChanged.connect(self.destination_name.setPlaceholderText)
        self.destination_name.setPlaceholderText(self.source_name.text())

    def step(self):
        return _validate_step(dict(action='copy_remote',
            source_endpoint=self.source.currentData(), destination_endpoint=self.destination.currentData(),
            source_dir=self.source_dir.text(), source_name=self.source_name.text(),
            destination_dir=self.destination_dir.text(), destination_name=self.destination_name.text() or self.source_name.text()))

    def _accept_checked(self):
        try:
            self.step()
        except (ValueError, TypeError, KeyError) as exc:
            QMessageBox.warning(self, 'Invalid step', str(exc))
            return
        self.accept()


def combo_index(widget, data):
    for i in range(widget.count()):
        if widget.itemData(i) == data:
            return i
    return -1


class WorkflowDialog(QDialog):
    def __init__(self, endpoints, name='', steps=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Workflow editor')
        self.resize(860, 560)
        self.endpoints = [list(x) for x in endpoints]
        self.steps = deepcopy(steps or [])
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel('Workflow name'))
        self.name = QLineEdit(name)
        self.name.setPlaceholderText('e.g. Back up project files')
        self.name.setMaxLength(140)
        outer.addWidget(self.name)
        self.steps_summary = QLabel()
        outer.addWidget(self.steps_summary)
        self.items = QListWidget()
        self.items.setSpacing(4)
        outer.addWidget(self.items, 1)
        tools = QHBoxLayout()
        self.add_btn = QPushButton('Add step'); self.edit_btn = QPushButton('Edit step')
        self.remove_btn = QPushButton('Remove'); self.up_btn = QPushButton('Move up'); self.down_btn = QPushButton('Move down')
        for button in (self.add_btn,self.edit_btn,self.remove_btn,self.up_btn,self.down_btn): tools.addWidget(button)
        outer.addLayout(tools)
        bottom = QHBoxLayout(); save = QPushButton('Save workflow'); cancel = QPushButton('Cancel')
        bottom.addStretch(1); bottom.addWidget(save); bottom.addWidget(cancel); outer.addLayout(bottom)
        self.add_btn.clicked.connect(self.add_step)
        self.edit_btn.clicked.connect(self.edit_step)
        self.remove_btn.clicked.connect(self.remove_step)
        self.up_btn.clicked.connect(lambda: self.move_step(-1))
        self.down_btn.clicked.connect(lambda: self.move_step(1))
        save.setObjectName('primaryAction')
        save.clicked.connect(self._accept_checked)
        cancel.clicked.connect(self.reject)
        self.items.currentRowChanged.connect(self.update_step_actions)
        self.items.itemDoubleClicked.connect(lambda _: self.edit_step())
        self.refresh()

    def refresh(self, focus=None):
        self.items.clear()
        for number, step in enumerate(self.steps, 1):
            self.items.addItem(f'{number}. {format_step(step)}')
        if self.steps:
            self.items.setCurrentRow(max(0, min(focus or 0, len(self.steps) - 1)))
        if self.steps:
            summary = f'{len(self.steps)} copy steps · Run in order'
        elif len(self.endpoints) < 2:
            summary = 'Connect to two distinct servers to add a copy step.'
        else:
            summary = 'Add a copy step to get started.'
        self.steps_summary.setText(summary)
        self.update_step_actions()

    def update_step_actions(self, *_):
        row = self.items.currentRow()
        selected = row >= 0
        self.add_btn.setEnabled(len(self.endpoints) >= 2 and len(self.steps) < 100)
        self.edit_btn.setEnabled(selected)
        self.remove_btn.setEnabled(selected)
        self.up_btn.setEnabled(row > 0)
        self.down_btn.setEnabled(selected and row < len(self.steps) - 1)

    def add_step(self):
        dialog = StepDialog(self.endpoints, parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.steps.append(dialog.step()); self.refresh(len(self.steps)-1)

    def edit_step(self):
        index = self.items.currentRow()
        if index < 0: return
        dialog = StepDialog(self.endpoints, self.steps[index], self)
        if dialog.exec() == QDialog.Accepted:
            self.steps[index] = dialog.step(); self.refresh(index)

    def remove_step(self):
        index = self.items.currentRow()
        if index >= 0:
            self.steps.pop(index); self.refresh(index)

    def move_step(self, offset):
        index = self.items.currentRow(); next_index = index + offset
        if index < 0 or not 0 <= next_index < len(self.steps): return
        self.steps[index], self.steps[next_index] = self.steps[next_index], self.steps[index]
        self.refresh(next_index)

    def _accept_checked(self):
        try:
            if not self.name.text().strip() or len(self.name.text()) > 140:
                raise ValueError('Enter a workflow name (1–140 characters)')
            if not 1 <= len(self.steps) <= 100:
                raise ValueError('Add at least one copy step (maximum 100)')
            self.steps = [_validate_step(step) for step in self.steps]
        except (ValueError, TypeError, KeyError) as exc:
            QMessageBox.warning(self, 'Invalid workflow', str(exc)); return
        self.accept()
