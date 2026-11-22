"""Backend-neutral remote file operations and guarded UTF-8 editing.

Operations run exclusively on a tab's existing worker/connection.
All file names are validated single path segments, and saves never blindly
replace the current file: they compare snapshots then keep a recovery backup.
"""
from __future__ import annotations

from dataclasses import dataclass
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


def decode_text_preview(data: bytes) -> str:
    """Display common Windows and UTF-8 text files without modifying their bytes."""
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = data.decode('utf-16')
    else:
        if b'\x00' in data:
            raise ValueError('This file contains binary data')
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = data.decode('cp1252', errors='replace')
    if '\x00' in text:
        raise ValueError('This file contains binary data')
    return text


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


def safe_save_remote_bytes(backend, name: str, expected_sha256: str, payload: bytes) -> str:
    """Stage edited data, preserve old file as backup, then swap via renames.

    A failed publish attempts rollback, leaving a backup/stage for recovery when
    server failures prevent it. Existing backup/stage names are never reused.
    A second hash comparison directly before publish reduces accidental races;
    external simultaneous writers still require server-side locking for certainty.
    """
    name = validate_name(name)
    if len(payload) > MAX_EDITOR_BYTES:
        raise ValueError('Edited file exceeds the size limit')
    old = backend.read_bytes(name, MAX_EDITOR_BYTES)
    if hashlib.sha256(old).hexdigest() != expected_sha256:
        raise RemoteConflictError('Remote file changed since it was opened; save aborted')
    if old == payload:
        return ''
    nonce = uuid.uuid4().hex[:16]
    staged = validate_name(f'.zftp-edit-{nonce}.tmp')
    backup = validate_name(f'.zftp-backup-{nonce}.bak')
    backend.write_new_bytes(staged, payload)
    try:
        check = backend.read_bytes(name, MAX_EDITOR_BYTES)
        if hashlib.sha256(check).hexdigest() != expected_sha256:
            raise RemoteConflictError('Remote file changed during editing; save aborted')
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


def find_remote(backend, root: str, term: str, *, cancelled=lambda: False,
                max_results: int = MAX_SEARCH_RESULTS, max_entries: int = MAX_SEARCH_ENTRIES,
                max_depth: int = MAX_SEARCH_DEPTH):
    """Case-insensitive filename search; avoid symlink recursion and restore cwd."""
    term = term.strip().casefold()
    if not term:
        raise ValueError('Enter a search term')
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
                if term in name.casefold():
                    found.append((absolute, bool(entry.is_dir), entry.size))
                    if len(found) >= max_results: return found
                if entry.is_dir and depth < max_depth:
                    stack.append((absolute, depth + 1))
        return found
    finally:
        backend.cwd(initial)
