"""Structured PySide6 sequential-copy workflow editor (no raw JSON required)."""
from __future__ import annotations
from copy import deepcopy
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QMessageBox, QListWidget, QFormLayout, QWidget)
from zftp.services.workflows import _validate_step


def format_endpoint(endpoint):
    protocol, host, port, user = endpoint
    return f'{protocol}  {user}@{host}:{port}'


def format_step(step):
    source = format_endpoint(step['source_endpoint'])
    target = format_endpoint(step['destination_endpoint'])
    return f"{step['source_name']}  ·  {source}  →  {target} / {step['destination_name']}"


class StepDialog(QDialog):
    def __init__(self, endpoints, step=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Configure file-copy step')
        self.resize(640, 280)
        self.endpoints = [list(item) for item in endpoints]
        layout = QFormLayout(self)
        self.source = QComboBox(); self.destination = QComboBox()
        for endpoint in self.endpoints:
            name = format_endpoint(endpoint)
            self.source.addItem(name, endpoint)
            self.destination.addItem(name, endpoint)
        self.source_dir = QLineEdit('/'); self.source_name = QLineEdit()
        self.destination_dir = QLineEdit('/'); self.destination_name = QLineEdit()
        layout.addRow('Source connection', self.source)
        layout.addRow('Source directory', self.source_dir)
        layout.addRow('Source filename', self.source_name)
        layout.addRow('Destination connection', self.destination)
        layout.addRow('Destination directory', self.destination_dir)
        layout.addRow('Destination filename', self.destination_name)
        buttons = QHBoxLayout(); ok = QPushButton('Save step'); cancel = QPushButton('Cancel')
        buttons.addWidget(ok); buttons.addWidget(cancel); layout.addRow(buttons)
        ok.clicked.connect(self._accept_checked); cancel.clicked.connect(self.reject)
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

    def step(self):
        return _validate_step(dict(action='copy_remote',
            source_endpoint=self.source.currentData(), destination_endpoint=self.destination.currentData(),
            source_dir=self.source_dir.text(), source_name=self.source_name.text(),
            destination_dir=self.destination_dir.text(), destination_name=self.destination_name.text()))

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
        self.name = QLineEdit(name); self.name.setPlaceholderText('e.g. Back up project files')
        outer.addWidget(self.name)
        outer.addWidget(QLabel('Operations execute in order. Existing destination files are protected.'))
        self.items = QListWidget(); outer.addWidget(self.items, 1)
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
        save.clicked.connect(self._accept_checked); cancel.clicked.connect(self.reject)
        self.refresh()

    def refresh(self, focus=None):
        self.items.clear()
        for number, step in enumerate(self.steps, 1):
            self.items.addItem(f'{number}. {format_step(step)}')
        if focus is not None and self.steps:
            self.items.setCurrentRow(max(0, min(focus,len(self.steps)-1)))

    def add_step(self):
        if len(self.steps) >= 100:
            QMessageBox.warning(self, 'Step limit', 'Workflows support at most 100 steps.'); return
        if len(self.endpoints) < 2:
            QMessageBox.warning(self, 'Connections required', 'Connect to two distinct servers to add a step.'); return
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
