"""Backend-neutral remote file operations and guarded UTF-8 editing.

Operations run exclusively on a tab's existing worker/connection.
All file names are validated single path segments, and saves never blindly
replace the current file: they compare snapshots then keep a recovery backup.
"""
from __future__ import annotations

from codecs import BOM_UTF8, BOM_UTF16_BE, BOM_UTF16_LE
from dataclasses import dataclass
from datetime import date, datetime, time, timezone, timedelta
from fnmatch import fnmatchcase
import hashlib
import posixpath
import uuid
from zftp.protocols.ftp_engine import clean_remote_name

MAX_EDITOR_BYTES = 1024 * 1024
MAX_SEARCH_RESULTS = 2000
MAX_SEARCH_ENTRIES = 20000
MAX_SEARCH_DEPTH = 32


class RemoteConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class EditorSnapshot:
    name: str
    text: str
    sha256: str


def validate_name(name: str) -> str:
    clean_remote_name(name)
    if '\x00' in name or any(ord(ch) < 32 for ch in name):
        raise ValueError('Control characters are not permitted in names')
    return name


@dataclass(frozen=True)
class TextCopy:
    content: str
    encoding: str
    bom: bytes
    newline: str

    def encode(self, text):
        normalized = text.replace('\r\n', '\n').replace('\r', '\n')
        if '\x00' in normalized:
            raise ValueError('Text cannot contain NUL bytes')
        return self.bom + normalized.replace('\n', self.newline).encode(self.encoding)


def identify_text(data, *, strict_controls=False):
    for bom, encoding in ((BOM_UTF8, 'utf-8'), (BOM_UTF16_LE, 'utf-16-le'), (BOM_UTF16_BE, 'utf-16-be')):
        if data.startswith(bom):
            text = data[len(bom):].decode(encoding)
            break
    else:
        bom = b''
        for encoding in ('utf-8', 'cp1252', 'latin-1'):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    if '\x00' in text or (strict_controls and any(
            not character.isprintable() and character not in '\t\n\r\f' for character in text)):
        raise ValueError('This file contains binary data')
    newline = '\r\n' if '\r\n' in text else '\r' if '\r' in text else '\n'
    return TextCopy(text, encoding, bom, newline)


EDITOR_ENCODINGS = (
    ('Auto detect', None), ('UTF-8', 'utf-8'), ('UTF-8 with BOM', 'utf-8-sig'),
    ('UTF-16 LE', 'utf-16-le'), ('UTF-16 BE', 'utf-16-be'),
    ('Windows-1252', 'cp1252'), ('ISO-8859-1', 'latin-1'), ('DOS CP437', 'cp437'),
    ('Shift JIS', 'shift_jis'),
)


def decode_editor_copy(data, encoding, *, strict_controls=False):
    if encoding is None:
        return identify_text(data, strict_controls=strict_controls)
    bom = {'utf-8-sig': BOM_UTF8, 'utf-16-le': BOM_UTF16_LE, 'utf-16-be': BOM_UTF16_BE}.get(encoding, b'')
    if bom and data.startswith(bom):
        data = data[len(bom):]
    elif encoding != 'utf-8-sig':
        bom = b''
    encoding = 'utf-8' if encoding == 'utf-8-sig' else encoding
    content = data.decode(encoding)
    if '\x00' in content:
        raise ValueError('The file contains NUL characters in this encoding.')
    newline = '\r\n' if '\r\n' in content else '\r' if '\r' in content else '\n'
    return TextCopy(content, encoding, bom, newline)


def decode_text_preview(data: bytes) -> str:
    return identify_text(data).content


def read_editor_snapshot(backend, name: str, limit: int = MAX_EDITOR_BYTES) -> EditorSnapshot:
    name = validate_name(name)
    raw = backend.read_bytes(name, limit)
    if len(raw) > limit:
        raise ValueError('File exceeds the text editor size limit')
    if b'\x00' in raw:
        raise ValueError('Binary files cannot be edited as text')
    try:
        decoded = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('Only UTF-8 text files are supported') from exc
    if raw.startswith(b'\xef\xbb\xbf'):
        raise ValueError('UTF-8 BOM files are read-only in this editor to avoid changing encoding')
    return EditorSnapshot(name, decoded, hashlib.sha256(raw).hexdigest())


def encode_editor_text(text: str, limit: int = MAX_EDITOR_BYTES) -> bytes:
    raw = text.encode('utf-8')
    if len(raw) > limit:
        raise ValueError('Edited file exceeds the size limit')
    if b'\x00' in raw:
        raise ValueError('NUL bytes are not allowed in text files')
    return raw


def safe_save_editor(backend, snapshot: EditorSnapshot, updated: str) -> str:
    return safe_save_remote_bytes(backend, snapshot.name, snapshot.sha256, encode_editor_text(updated))


def safe_save_remote_bytes(backend, name: str, expected_sha256: str, payload: bytes, *,
                           limit=MAX_EDITOR_BYTES, before_publish=None) -> str:
    """Stage edited data, preserve old file as backup, then swap via renames.

    A failed publish attempts rollback, leaving a backup/stage for recovery when
    server failures prevent it. Existing backup/stage names are never reused.
    A second hash comparison directly before publish reduces accidental races;
    external simultaneous writers still require server-side locking for certainty.
    """
    name = validate_name(name)
    if len(payload) > limit:
        raise ValueError('Edited file exceeds the size limit')
    old = backend.read_bytes(name, limit)
    if hashlib.sha256(old).hexdigest() != expected_sha256:
        raise RemoteConflictError('Remote file changed since it was opened; save aborted')
    if old == payload:
        return ''
    nonce = uuid.uuid4().hex[:16]
    staged = validate_name(f'.zftp-edit-{nonce}.tmp')
    backup = validate_name(f'.zftp-backup-{nonce}.bak')
    backend.write_new_bytes(staged, payload)
    try:
        check = backend.read_bytes(name, limit)
        if hashlib.sha256(check).hexdigest() != expected_sha256:
            raise RemoteConflictError('Remote file changed during editing; save aborted')
        if before_publish is not None:
            before_publish()
        backend.rename(name, backup)
    except BaseException:
        try: backend.delete_file(staged)
        except Exception: pass
        raise
    try:
        backend.rename(staged, name)
    except BaseException as exc:
        try: backend.rename(backup, name)
        except Exception:
            raise RuntimeError(f'Could not publish edit or restore original. Recovery file: {backup}; staged file: {staged}') from exc
        raise
    # Backup deliberately kept until user manually removes it.
    return backup


class RemoteDirectoryRestoreError(RuntimeError):
    """The browser connection can no longer be used from its previous path."""


def delete_remote_tree(backend, name, *, cancelled=lambda: False):
    """Remove a selected tree in post-order, treating links as leaf entries."""
    name = validate_name(name)
    initial = backend.pwd()
    parent = posixpath.normpath(backend.canonical_directory())
    if not parent.startswith('/'):
        raise ValueError('Server did not report an absolute remote directory')
    root = posixpath.join(parent, name)
    current = parent
    planned = []
    stack = [(root, False)]
    visited_paths = set()
    removed = 0
    target = root

    def enter(folder):
        nonlocal current
        if current != folder:
            backend.cwd(folder)
            if posixpath.normpath(backend.canonical_directory()) != folder:
                raise ValueError(f'Server resolved {folder} to another location')
            current = folder

    def check_cancelled():
        if cancelled():
            raise InterruptedError(f'Folder deletion cancelled after removing {removed} items')

    try:
        # Recheck the selected directory with a listing that distinguishes links.
        listing = backend.list_dir_for_deletion
        selected = [entry for entry in listing() if entry.name == name]
        if len(selected) != 1 or not selected[0].is_dir or selected[0].is_link:
            raise ValueError('Selected item is no longer a regular directory')
        # Validate the entire listing before sending the first delete command.
        while stack:
            check_cancelled()
            target, scanned = stack.pop()
            if scanned:
                planned.append((target, True))
                continue
            if target in visited_paths:
                raise ValueError(f'Server listed the same directory more than once: {target}')
            visited_paths.add(target)
            enter(target)
            stack.append((target, True))
            for entry in listing():
                child = posixpath.join(target, validate_name(entry.name))
                if entry.is_dir and not entry.is_link:
                    stack.append((child, False))
                else:
                    planned.append((child, False))

        for target, is_dir in planned:
            check_cancelled()
            folder, filename = posixpath.split(target)
            enter(folder)
            if is_dir:
                backend.remove_empty_dir(filename)
            else:
                backend.delete_file(filename)
            removed += 1
    except InterruptedError:
        raise
    except Exception as exc:
        raise RuntimeError(f'Could not delete {root}. Stopped at {target} after removing {removed} items. {exc}') from exc
    finally:
        try:
            backend.cwd(initial)
            if posixpath.normpath(backend.canonical_directory()) != parent:
                raise ValueError('Server returned a different directory')
        except Exception as exc:
            raise RemoteDirectoryRestoreError(
                f'Could not return to {initial} after removing {removed} items. Reconnect and check {root}.'
            ) from exc
    return removed


@dataclass(frozen=True)
class RemoteSearch:
    name: str = ''
    wildcard: bool = False
    extensions: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    modified_from: float | None = None
    modified_until: float | None = None

    @classmethod
    def from_fields(cls, name, wildcard, extensions, minimum, maximum, since, until):
        suffixes = tuple('.' + value.casefold().lstrip('.') for value in extensions.replace(',', ' ').split())
        lower = int(minimum) if minimum.strip() else None
        upper = int(maximum) if maximum.strip() else None
        start = date.fromisoformat(since.strip()) if since.strip() else None
        end = date.fromisoformat(until.strip()) if until.strip() else None
        if any(value is not None and value < 0 for value in (lower, upper)):
            raise ValueError('Sizes must be non-negative bytes.')
        if lower is not None and upper is not None and lower > upper:
            raise ValueError('Minimum size must be at most the maximum size.')
        if start and end and start > end:
            raise ValueError('The start date must be at most the end date.')
        if not name.strip() and not suffixes and all(value is None for value in (lower, upper, start, end)):
            raise ValueError('Enter at least one search criterion.')
        return cls(name.strip().casefold(), wildcard, suffixes, lower, upper,
                   datetime.combine(start, time.min, timezone.utc).timestamp() if start else None,
                   (datetime.combine(end, time.min, timezone.utc) + timedelta(days=1)).timestamp() if end else None)

    def matches(self, entry, name):
        folded = name.casefold()
        if self.name and not (fnmatchcase(folded, self.name) if self.wildcard else self.name in folded):
            return False
        if self.extensions and (entry.is_dir or not folded.endswith(self.extensions)):
            return False
        size = None if entry.is_dir else entry.size
        modified = getattr(entry, 'modified', None)
        return ((self.minimum is None or size is not None and size >= self.minimum)
                and (self.maximum is None or size is not None and size <= self.maximum)
                and (self.modified_from is None or modified is not None and modified >= self.modified_from)
                and (self.modified_until is None or modified is not None and modified < self.modified_until))


def find_remote(backend, root: str, term: str, *, cancelled=lambda: False,
                max_results: int = MAX_SEARCH_RESULTS, max_entries: int = MAX_SEARCH_ENTRIES,
                max_depth: int = MAX_SEARCH_DEPTH):
    """Case-insensitive filename search; avoid symlink recursion and restore cwd."""
    query = term if isinstance(term, RemoteSearch) else RemoteSearch.from_fields(term, False, '', '', '', '', '')
    if not root.startswith('/'):
        raise ValueError('Search root must be absolute')
    initial = backend.pwd()
    found = []
    inspected = 0
    stack = [(posixpath.normpath(root), 0)]
    try:
        while stack:
            if cancelled(): raise InterruptedError('Search cancelled')
            folder, depth = stack.pop()
            backend.cwd(folder)
            for entry in backend.list_dir():
                if cancelled(): raise InterruptedError('Search cancelled')
                if getattr(entry, 'is_link', False): continue
                name = validate_name(entry.name)
                inspected += 1
                if inspected > max_entries:
                    raise ValueError('Search entry limit reached; narrow the search folder')
                absolute = posixpath.join(folder, name)
                if query.matches(entry, name):
                    found.append((absolute, bool(entry.is_dir), entry.size))
                    if len(found) >= max_results: return found
                if entry.is_dir and depth < max_depth:
                    stack.append((absolute, depth + 1))
        return found
    finally:
        backend.cwd(initial)
