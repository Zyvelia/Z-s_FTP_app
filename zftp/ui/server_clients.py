"""Live hosted-client table shared by the FTP and SFTP panels."""

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QHeaderView, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout


def clients_button(panel):
    button = QPushButton('Connected clients…')
    button.clicked.connect(lambda: show_clients(panel))
    return button


def show_clients(panel):
    dialog = QDialog(panel)
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.setWindowTitle('Connected server clients')
    dialog.resize(690, 330)
    layout = QVBoxLayout(dialog)
    table = QTableWidget(0, 4)
    table.setHorizontalHeaderLabels(('Address', 'Account', 'Connected for', 'Current transfers'))
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
    layout.addWidget(table)
    close = QPushButton('Close')
    close.clicked.connect(dialog.close)
    layout.addWidget(close, alignment=Qt.AlignRight)

    def refresh():
        rows = panel.controller.clients.snapshot()
        table.setRowCount(len(rows))
        for row, (address, username, age, transfers) in enumerate(rows):
            hours, seconds = divmod(age, 3600)
            mins, seconds = divmod(seconds, 60)
            values = (address, username or 'Signing in', f'{hours:02}:{mins:02}:{seconds:02}',
                      ', '.join(transfers) if transfers else 'Idle')
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(row, column, item)

    timer = QTimer(dialog, interval=750)
    timer.timeout.connect(refresh)
    refresh()
    timer.start()
    dialog.show()
