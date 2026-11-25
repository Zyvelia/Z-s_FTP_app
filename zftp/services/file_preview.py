"""Read and edit bounded copies of text, binary files, and SQLite databases."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import os
import sqlite3
from tempfile import NamedTemporaryFile
from uuid import uuid4

from zftp.services.remote_management import MAX_EDITOR_BYTES, RemoteConflictError
from zftp.services.format_preview import SPECIAL_SUFFIXES, archive_kind


TEXT_SUFFIXES = frozenset({
    '.txt', '.ini', '.log', '.logs', '.json', '.jsonl', '.ndjson', '.yaml', '.yml',
    '.toml', '.xml', '.csv', '.tsv', '.md', '.rst', '.cfg', '.conf', '.properties',
    '.env', '.html', '.htm', '.css', '.svg', '.sql', '.sh', '.bash', '.bat', '.cmd',
    '.ps1', '.py', '.lua', '.js', '.ts', '.c', '.h', '.cpp', '.hpp', '.cs', '.go',
    '.rs', '.java', '.patch', '.diff', '.service', '.desktop',
    '.nfo', '.plist', '.reg', '.srt', '.vtt',
})
TEXT_NAMES = frozenset({'.env', '.gitignore', '.gitattributes', '.editorconfig', '.gitmodules',
                        'dockerfile', 'makefile', 'readme', 'license'})
OPAQUE_SUFFIXES = frozenset({'.dat', '.backup', '.idx', '.nvs', '.rec'})
PREVIEW_SUFFIXES = TEXT_SUFFIXES | SPECIAL_SUFFIXES | OPAQUE_SUFFIXES | {'.db', '.sfo'}
MEDIA_LIMIT = 8 * 1024 * 1024
HEX_LIMIT = 64 * 1024
SQLITE_HEADER = b'SQLite format 3\x00'
TABLE_LIMIT = 100


def is_text_name(name):
    path = Path(name)
    return path.suffix.lower() in TEXT_SUFFIXES or path.name.lower() in TEXT_NAMES


def can_preview(name):
    return Path(name).suffix.lower() in PREVIEW_SUFFIXES or is_text_name(name) or archive_kind(name) is not None


def preview_limit(name):
    return MAX_EDITOR_BYTES if is_text_name(name) else MEDIA_LIMIT


def sqlite_sidecars(name):
    return {name + suffix for suffix in ('-wal', '-shm', '-journal')}


def read_local_copy(path, limit):
    with path.open('rb') as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f'File exceeds the {limit // (1024 * 1024)} MiB editor limit')
    return data


def quote_identifier(name):
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class SQLiteTable:
    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple, ...]
    rowid_alias: str | None
    rowids: tuple[int, ...]
    locked_columns: frozenset[int]


class SQLiteCopy:
    """Operate on an in-memory snapshot, with bounded table and row previews."""

    def __init__(self, data):
        self.data = data
        self.wal = data[18:20] == b'\x02\x02'

    def _connection(self):
        db = sqlite3.connect(':memory:')
        try:
            # A standalone snapshot has no sidecar WAL to open.
            data = self.data[:18] + b'\x01\x01' + self.data[20:] if self.wal else self.data
            db.deserialize(data)
            db.execute('PRAGMA trusted_schema = OFF')
            return db
        except (sqlite3.Error, AttributeError):
            db.close()
            raise

    def tables(self):
        with closing(self._connection()) as db:
            names = [
                name for schema, name, kind, *_ in db.execute('PRAGMA table_list')
                if schema == 'main' and kind == 'table' and not name.startswith('sqlite_')
            ]
        return sorted(names, key=str.casefold)

    def read_table(self, name):
        if name not in self.tables():
            raise ValueError('Select an ordinary SQLite table')
        table = quote_identifier(name)
        with closing(self._connection()) as db:
            columns = list(db.execute('SELECT name, pk, hidden FROM pragma_table_xinfo(?)', (name,)))
            if len(columns) > TABLE_LIMIT:
                raise ValueError(f'This table exceeds the {TABLE_LIMIT}-column editor limit')
            column_names = {column[0].casefold() for column in columns}
            rowid = next((alias for alias in ('_rowid_', 'rowid', 'oid') if alias not in column_names), None)
            # WITHOUT ROWID tables can be read, but the editor needs a stable row key.
            if rowid:
                try:
                    db.execute(f'SELECT {rowid} FROM {table} LIMIT 0')
                except sqlite3.OperationalError:
                    rowid = None
            if rowid:
                result = db.execute(f'SELECT {rowid}, * FROM {table} LIMIT ?', (TABLE_LIMIT,))
                values = result.fetchall()
                rowids = tuple(row[0] for row in values)
                rows = tuple(row[1:] for row in values)
            else:
                rows = tuple(db.execute(f'SELECT * FROM {table} LIMIT ?', (TABLE_LIMIT,)))
                rowids = ()
        return SQLiteTable(name, tuple(column[0] for column in columns), rows, rowid, rowids,
                           frozenset(i for i, (_, pk, hidden) in enumerate(columns) if pk or hidden))

    def update(self, table, changes):
        if not table.rowid_alias:
            raise ValueError('This table has no editable row identifier')
        with closing(self._connection()) as db:
            def authorize(action, target, column, schema, source):
                if source is not None or action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE,
                                                     sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
                    return sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_UPDATE and (target != table.name or schema != 'main'):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            db.set_authorizer(authorize)
            updates = {}
            for row, column, value in changes:
                if column in table.locked_columns:
                    raise ValueError('Key and generated columns are read-only')
                updates.setdefault(row, {})[table.columns[column]] = value
            quoted_table = quote_identifier(table.name)
            with db:
                for row, values in updates.items():
                    assignments = ', '.join(f'{quote_identifier(column)} = ?' for column in values)
                    if db.execute(f'UPDATE {quoted_table} SET {assignments} WHERE {table.rowid_alias} = ?',
                                  (*values.values(), table.rowids[row])).rowcount != 1:
                        raise RemoteConflictError('SQLite row is no longer available')
            data = db.serialize()
            return data[:18] + b'\x02\x02' + data[20:] if self.wal else data


def save_local_copy(path, original, revised, *, limit, sqlite=False):
    if path.is_symlink():
        raise ValueError('Symbolic links cannot be edited')
    if len(revised) > limit:
        raise ValueError('Edited file exceeds the editor size limit')

    def check_current():
        if sqlite and any(path.with_name(name).exists() for name in sqlite_sidecars(path.name)):
            raise RemoteConflictError('Close the application using this database before editing it')
        if path.is_symlink() or read_local_copy(path, limit) != original:
            raise RemoteConflictError('Local file changed since it was opened')

    check_current()
    backup = path.with_name(f'{path.name}.zftp-backup-{uuid4().hex[:12]}')
    with NamedTemporaryFile(dir=path.parent, delete=False) as target:
        staged = Path(target.name)
        try:
            target.write(revised)
        except OSError:
            target.close()
            staged.unlink(missing_ok=True)
            raise
    try:
        os.chmod(staged, path.stat().st_mode)
        with backup.open('xb') as original_file:
            original_file.write(original)
        check_current()
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
    return backup
