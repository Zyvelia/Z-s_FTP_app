"""Read-only viewers for archive members, images, fonts, and binary metadata."""

import csv
from ftplib import Error as FTPError
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QFont, QFontDatabase, QImage, QImageReader, QPixmap
from PIL import Image
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QDialog, QInputDialog, QLineEdit, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from zftp.services.archive_preview import archive_listing, archive_member
from zftp.services.file_preview import MEDIA_LIMIT, OPAQUE_SUFFIXES, read_local_copy
from zftp.services.format_preview import (
    ARCHIVE_READ_LIMIT, PLAYBACK_SUFFIXES, ArchiveBudgetExceeded, ArchivePasswordRequired, ArchiveReader, FONT_SUFFIXES, HEADER_LIMIT,
    HEADER_SUFFIXES, IMAGE_SUFFIXES, archive_kind, hex_preview, inspect_format,
)
from zftp.services.param_sfo import ParamSfo
from zftp.services.remote_management import validate_name
from zftp.ui.media_preview import REMOTE_PLAYBACK_LIMIT, check_media_header, show_media


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
    if Path(name).suffix.lower() == '.dds':
        try:
            with Image.open(BytesIO(data), formats=['DDS']) as source:
                width, height = source.size
                if not (0 < width <= 4096 and 0 < height <= 4096):
                    raise ValueError('DDS previews support images up to 4096 × 4096 pixels.')
                pixels = source.convert('RGBA').tobytes()
            image = QImage(pixels, width, height, width * 4, QImage.Format_RGBA8888).copy()
        except (OSError, NotImplementedError, Image.DecompressionBombError) as error:
            raise ValueError(f'Could not decode this DDS image. {error}') from error
    else:
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
    preview_dialog(parent, name, scroll, f'{image.width()} × {image.height()} pixels')


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
            elif suffix in PLAYBACK_SUFFIXES:
                show_media(self, name, data=data)
            elif suffix == '.sfo':
                document = ParamSfo(data)
                fields = table_view(['Field', 'Type', 'Value'],
                                    [(field.name, field.kind, field.value) for field in document.fields])
                preview_dialog(self, name, fields, 'PARAM.SFO · Read-only')
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
        table = table_view(
            ['Name', 'Size', 'Compressed size'],
            [(entry.name, self.display_size(entry.size), self.display_size(entry.compressed) if entry.compressed is not None else '—')
             for entry in entries],
        )
        table.horizontalHeader().resizeSection(0, 490)

        def activate(row, _column):
            entry = entries[row]
            if entry.is_file and not entry.is_directory:
                open_entry(entry)

        table.cellDoubleClicked.connect(activate)
        if total is None:
            note = f'Showing the first {len(entries):,} entries'
        elif len(entries) < total:
            note = f'{total:,} entries · Showing the first {len(entries):,}'
        else:
            note = f'{total:,} entries'
        note += ' · Double-click a file to preview it.'
        return preview_dialog(self, name, table, note)

    def show_archive_entry(self, member, data):
        title = PurePosixPath(member.name).name
        kind = archive_kind(title)
        if kind:
            self.preview_cached_archive(title, data, kind)
        else:
            self.open_file_preview(title, data, None)

    def open_archive_session(self, name, read, start):
        password = None

        def request(label, member, opened, owner):
            supplied_password = password

            def operation():
                try:
                    return read(member, supplied_password)
                except ArchivePasswordRequired as error:
                    return error

            def done(result):
                nonlocal password
                if not isValid(owner):
                    return
                if not isinstance(result, ArchivePasswordRequired):
                    opened(result)
                    return
                entered, accepted = QInputDialog.getText(owner, 'Archive password', str(result), QLineEdit.Password)
                if accepted and entered:
                    password = entered
                    request(label, member, opened, owner)

            start(label, operation, done)

        def listed(result):
            entries, total = result

            def open_entry(member):
                request(f'Opening {PurePosixPath(member.name).name}', member,
                        lambda data: self.show_archive_entry(member, data), dialog)

            def clear_password():
                nonlocal password
                password = None

            dialog = self.show_archive(name, entries, total, open_entry)
            dialog.finished.connect(clear_password)

        request(f'Opening {name}', None, listed, self)

    def start_local_archive_job(self, label, operation, done):
        return self.run_local_file_job(lambda _: operation(), done)

    def preview_cached_archive(self, name, data, kind='.zip'):
        def read(member, password):
            source = BytesIO(data)
            if member is None:
                return archive_listing(source, kind, password)
            return archive_member(source, kind, member, password)

        self.open_archive_session(name, read, self.start_local_archive_job)

    def preview_local_format(self, path):
        suffix = path.suffix.lower()
        kind = archive_kind(path.name)
        if kind:
            def read_archive(member, password):
                with path.open('rb') as file:
                    def read_at(offset, length):
                        file.seek(offset)
                        return file.read(length)

                    with ArchiveReader(file.seek(0, 2), read_at) as source:
                        if member is None:
                            return archive_listing(source, kind, password)
                        return archive_member(source, kind, member, password)

            self.open_archive_session(path.name, read_archive, self.start_local_archive_job)
            return

        if suffix in PLAYBACK_SUFFIXES:
            def check(_):
                with path.open('rb') as source:
                    check_media_header(path.name, source.read(16))

            self.run_local_file_job(check, lambda _: show_media(self, path.name, path=path))
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

        kind = archive_kind(name)
        if kind:
            if entry.size is not None and entry.size <= MEDIA_LIMIT:
                run(f'Opening {name}', lambda: read_whole(MEDIA_LIMIT),
                    lambda data: self.preview_cached_archive(name, data, kind))
                return

            def read_archive(member, password):
                size = backend.file_size(name)
                if size is None:
                    size = entry.size
                if size is None:
                    raise OSError('The server does not report this archive size. Download it to browse locally.')
                cache_start, cache = 0, b''
                fetched = 0

                def read_at(offset, length):
                    nonlocal cache_start, cache, fetched
                    if cache_start <= offset and offset + length <= cache_start + len(cache):
                        relative = offset - cache_start
                        return cache[relative:relative + length]
                    remaining = ARCHIVE_READ_LIMIT - fetched
                    if length > remaining:
                        raise ArchiveBudgetExceeded('Archive ranges exceed the 16 MiB preview budget.')
                    count = min(size - offset, remaining, max(128 * 1024, length))
                    cache = backend.read_prefix(name, count, offset=offset)
                    cache_start = offset
                    fetched += len(cache)
                    return cache[:length]

                try:
                    with ArchiveReader(size, read_at) as source:
                        if member is None:
                            return archive_listing(source, kind, password)
                        return archive_member(source, kind, member, password)
                except FTPError as error:
                    raise OSError(f'Could not read archive ranges from the server. Download it to browse locally. {error}') from error

            self.open_archive_session(name, read_archive, run)
            return

        if suffix in PLAYBACK_SUFFIXES:
            if entry.size is not None and entry.size > REMOTE_PLAYBACK_LIMIT:
                QMessageBox.information(self, 'Media too large',
                                        'Remote playback previews support up to 64 MiB. Download it to play locally.')
                return
            run(f'Opening {name}', lambda: read_whole(REMOTE_PLAYBACK_LIMIT),
                lambda data: self.show_format_preview(name, data, len(data)))
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
