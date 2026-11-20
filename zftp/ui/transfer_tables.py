"""File transfer drag/drop tables using Qt MIME data, no credentials in payloads."""
from __future__ import annotations
import json
from pathlib import Path
from PySide6.QtCore import QMimeData, Qt, QUrl, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QAbstractItemView, QTableWidget

REMOTE_MIME = 'application/x-zftp-remote-entries'

class TransferTable(QTableWidget):
    filesDropped = Signal(object)
    remoteDropped = Signal(object)

    def __init__(self, side: str):
        super().__init__(0, 3)
        self.side = side
        self.local_directory = Path.home()
        self.remote_session_id = ''
        self.remote_directory = '/'
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)

    def _selected_file_names(self):
        rows = sorted({item.row() for item in self.selectedItems()})
        return [self.item(row, 0).text() for row in rows
                if self.item(row, 0) is not None and
                self.item(row, 1) is not None and self.item(row, 1).text() in ('File', 'Folder')]

    def startDrag(self, supported_actions):
        names = self._selected_file_names()
        if not names:
            return
        mime = QMimeData()
        if self.side == 'local':
            mime.setUrls([QUrl.fromLocalFile(str(self.local_directory / name)) for name in names])
        else:
            mime.setData(REMOTE_MIME, json.dumps({
                'session': self.remote_session_id, 'path': self.remote_directory,
                'files': names,
            }).encode('utf8'))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)

    def _allowed(self, mime):
        if self.side == 'remote' and mime.hasUrls():
            return bool(mime.urls()) and all(url.isLocalFile() for url in mime.urls())
        if self.side == 'local' and mime.hasFormat(REMOTE_MIME):
            return True
        return False

    def dragEnterEvent(self, event):
        if self._allowed(event.mimeData()): event.acceptProposedAction()
        else: event.ignore()

    def dragMoveEvent(self, event):
        if self._allowed(event.mimeData()): event.acceptProposedAction()
        else: event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        if not self._allowed(mime):
            event.ignore(); return
        if self.side == 'remote':
            paths = [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
            self.filesDropped.emit(paths)
        else:
            try:
                data = json.loads(bytes(mime.data(REMOTE_MIME)).decode('utf8'))
                if not isinstance(data, dict) or not isinstance(data.get('files'), list):
                    event.ignore(); return
                self.remoteDropped.emit(data)
            except (ValueError, UnicodeError):
                event.ignore(); return
        event.acceptProposedAction()
