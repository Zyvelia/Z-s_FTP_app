"""Read-only viewers for archive members, images, fonts, and binary metadata."""

import csv
from ftplib import Error as FTPError
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QFont, QFontDatabase, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QDialog, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from zftp.services.file_preview import MEDIA_LIMIT, OPAQUE_SUFFIXES, read_local_copy
from zftp.services.format_preview import (
    ARCHIVE_SUFFIXES, ArchiveReader, FONT_SUFFIXES, HEADER_LIMIT, HEADER_SUFFIXES, IMAGE_SUFFIXES,
    ZipMember, hex_preview, inspect_format, zip_listing, zip_member,
)
from zftp.services.remote_management import validate_name


def preview_dialog(parent, title, content, note=''):
    dialog = QDialog(parent)
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.setWindowTitle(title)
    dialog.resize(780, 520)
    layout = QVBoxLayout(dialog)
    if note:
        label = QLabel(note)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
    layout.addWidget(content, 1)
    close = QPushButton('Close')
    close.clicked.connect(dialog.close)
    layout.addWidget(close, alignment=Qt.AlignRight)
    dialog.show()
    return dialog


def table_view(headers, rows):
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(str(value)))
    table.horizontalHeader().setStretchLastSection(True)
    return table


def show_delimited(parent, name, text):
    rows = []
    try:
        reader = csv.reader(StringIO(text, newline=''), delimiter='\t' if name.lower().endswith('.tsv') else ',')
        for row in reader:
            if len(rows) == 200:
                break
            rows.append([value[:2048] for value in row[:100]])
    except csv.Error as error:
        QMessageBox.warning(parent, 'Could not preview table', str(error))
        return
    columns = max((len(row) for row in rows), default=0)
    table = table_view([str(i + 1) for i in range(columns)], rows)
    preview_dialog(parent, f'{name} · Table preview', table, 'First 200 rows and 100 columns')


def show_image(parent, name, data):
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    reader.setDecideFormatFromContent(True)
    size = reader.size()
    if (not size.isValid() or not 0 < size.width() <= 4096 or not 0 < size.height() <= 4096
            or bytes(reader.format()).lower() == b'svg'):
        raise ValueError('Expected a supported raster image up to 4096 × 4096 pixels.')
    image = reader.read()
    if image.isNull():
        raise ValueError(reader.errorString())
    label = QLabel()
    label.setPixmap(QPixmap.fromImage(image))
    label.setAlignment(Qt.AlignCenter)
    scroll = QScrollArea()
    scroll.setWidget(label)
    scroll.setWidgetResizable(True)
    preview_dialog(parent, name, scroll, f'{size.width()} × {size.height()} pixels')


def show_font(parent, name, data):
    identifier = QFontDatabase.addApplicationFontFromData(QByteArray(data))
    if identifier < 0:
        raise ValueError('Could not read the font.')
    families = QFontDatabase.applicationFontFamilies(identifier)
    if not families:
        QFontDatabase.removeApplicationFont(identifier)
        raise ValueError('The font does not contain a usable family.')
    sample = QPlainTextEdit('The quick brown fox jumps over the lazy dog.\n\n'
                            'ABCDEFGHIJKLMNOPQRSTUVWXYZ\nabcdefghijklmnopqrstuvwxyz\n0123456789')
    sample.setReadOnly(True)
    sample.setFont(QFont(families[0], 22))
    dialog = preview_dialog(parent, name, sample, ', '.join(families))
    dialog.destroyed.connect(lambda *_: QFontDatabase.removeApplicationFont(identifier))


class FormatPreviewMixin:
    def show_format_preview(self, name, data, size):
        suffix = Path(name).suffix.lower()
        try:
            if suffix in IMAGE_SUFFIXES:
                show_image(self, name, data)
            elif suffix in FONT_SUFFIXES:
                show_font(self, name, data)
            else:
                report = inspect_format(name, data, size)
                if report is None:
                    return False
                text = QPlainTextEdit(report.text)
                text.setReadOnly(True)
                text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
                preview_dialog(self, name, text, report.title)
        except ValueError as error:
            QMessageBox.warning(self, 'Could not preview file', str(error))
        return True

    def show_archive(self, name, entries, total, open_entry):
        table = table_view(['Name', 'Bytes', 'Compressed bytes'],
                           [(entry.name, entry.size, entry.compressed) for entry in entries])
        table.horizontalHeader().resizeSection(0, 490)
        table.cellDoubleClicked.connect(lambda row, _: open_entry(entries[row])
                                        if not entries[row].is_directory else None)
        note = f'{total:,} entries'
        if len(entries) < total:
            note += f' · Showing the first {len(entries):,}'
        note += ' · Double-click a file to preview it.'
        preview_dialog(self, name, table, note)

    def show_zip_entry(self, member, data):
        title = PurePosixPath(member.name).name
        if Path(title).suffix.lower() in ARCHIVE_SUFFIXES:
            self.preview_cached_zip(title, data)
        else:
            self.open_file_preview(title, data, None)

    def preview_cached_zip(self, name, data):
        def opened(result):
            entries, total = result

            def open_entry(member):
                self.run_local_file_job(lambda _: zip_member(BytesIO(data), member),
                                        lambda content: self.show_zip_entry(member, content))

            self.show_archive(name, entries, total, open_entry)

        self.run_local_file_job(lambda _: zip_listing(BytesIO(data)), opened)

    def preview_local_format(self, path):
        suffix = path.suffix.lower()
        if suffix in ARCHIVE_SUFFIXES:
            def read_archive(member: ZipMember | None = None):
                with path.open('rb') as file:
                    def read_at(offset, length):
                        file.seek(offset)
                        return file.read(length)

                    with ArchiveReader(path.stat().st_size, read_at) as source:
                        return zip_listing(source) if member is None else zip_member(source, member)

            def opened(result):
                entries, total = result

                def open_entry(member):
                    self.run_local_file_job(lambda _: read_archive(member),
                                            lambda data: self.show_zip_entry(member, data))

                self.show_archive(path.name, entries, total, open_entry)

            self.run_local_file_job(lambda _: read_archive(), opened)
            return

        def read(_):
            size = path.stat().st_size
            if suffix in HEADER_SUFFIXES or suffix in OPAQUE_SUFFIXES:
                with path.open('rb') as file:
                    limit = MEDIA_LIMIT if suffix == '.a' else HEADER_LIMIT
                    return file.read(limit), size
            return read_local_copy(path, MEDIA_LIMIT), size

        self.run_local_file_job(read, lambda result: self.show_special_or_prefix(path.name, *result))

    def show_special_or_prefix(self, name, data, size):
        if not self.show_format_preview(name, data, size):
            text = QPlainTextEdit(hex_preview(data))
            text.setReadOnly(True)
            text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
            preview_dialog(self, name, text, f'Hex preview · First {len(data):,} bytes')

    def preview_remote_format(self, tab, entry):
        state = self.sessions[tab]
        try:
            name = validate_name(entry.name)
        except ValueError as error:
            QMessageBox.warning(self, 'Invalid filename', str(error))
            return
        folder = state.remote_path
        suffix = Path(name).suffix.lower()
        backend = state.backend

        def run(label, operation, opened):
            current_folder = state.remote_path

            def read(_):
                try:
                    backend.cwd(folder)
                    return operation()
                finally:
                    if current_folder != folder and state.connected:
                        try:
                            backend.cwd(current_folder)
                        except Exception:
                            state.connected = False
                            raise

            if not state.connected or not self.run_session_job(tab, label, read, opened):
                QMessageBox.information(self, 'Server unavailable', 'Wait until the original server tab is connected and idle.')

        def read_whole(limit):
            try:
                return backend.read_bytes(name, limit)
            except ValueError:
                # A bounded FTP read that stopped mid-file needs a reconnect.
                if state.protocol in ('FTP', 'FTPS'):
                    state.connected = False
                raise

        if suffix in ARCHIVE_SUFFIXES:
            if entry.size is not None and entry.size <= MEDIA_LIMIT:
                run(f'Opening {name}', lambda: read_whole(MEDIA_LIMIT),
                    lambda data: self.preview_cached_zip(name, data))
                return

            def read_archive(member=None):
                size = backend.file_size(name)
                if size is None:
                    raise OSError('The FTP server does not report this ZIP size. Download it to browse on your PC.')
                try:
                    with ArchiveReader(size, lambda offset, length: backend.read_prefix(name, length, offset=offset)) as source:
                        return zip_listing(source) if member is None else zip_member(source, member)
                except FTPError as error:
                    raise OSError(f'Could not read ZIP ranges from the FTP server. Download the ZIP to browse locally. {error}') from error

            def opened(result):
                entries, total = result

                def open_entry(member):
                    run(f'Opening {PurePosixPath(member.name).name}', lambda: read_archive(member),
                        lambda data: self.show_zip_entry(member, data))

                self.show_archive(name, entries, total, open_entry)

            run(f'Opening {name}', read_archive, opened)
            return

        prefix_only = suffix in HEADER_SUFFIXES or suffix in OPAQUE_SUFFIXES
        if not prefix_only and entry.size is not None and entry.size > MEDIA_LIMIT:
            QMessageBox.information(self, 'File too large', 'Font and image previews support files up to 8 MiB.')
            return

        def read():
            limit = MEDIA_LIMIT if not prefix_only or suffix == '.a' else HEADER_LIMIT
            if prefix_only and (entry.size is None or entry.size > limit):
                data = backend.read_prefix(name, limit)
            else:
                data = read_whole(limit)
            return data, entry.size

        run(f'Opening {name}', read, lambda result: self.show_special_or_prefix(name, *result))
