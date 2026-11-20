"""Cross-server copy through private local staging, without direct server-to-server trust.

The caller owns exclusive access to both backend connections for the entire call.
Any destination partial is deliberately left for manual recovery if upload failed;
the private local staging directory is always removed on exit.
"""
from __future__ import annotations
import os
import posixpath
import tempfile
from pathlib import Path
from typing import Callable

from zftp.protocols.ftp_engine import clean_remote_name
from zftp.transfers.reliable_transfer import TransferSettings, download, upload, TransferCancelled
from zftp.transfers.integrity import verify_transfer


def normalized_directory(path: str) -> str:
    if not isinstance(path, str) or not path.startswith('/') or '\\' in path or '\x00' in path:
        raise ValueError('Remote directory must be an absolute POSIX path')
    pieces = path.split('/')
    if any(p in ('.', '..') for p in pieces):
        raise ValueError('Remote directory contains traversal components')
    return posixpath.normpath(path)


def safe_filename(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError('Invalid filename type')
    clean_remote_name(name)
    if not isinstance(name, str) or name in ('', '.', '..') or '/' in name or '\\' in name:
        raise ValueError('Cross-server copies support one file name, not a path')
    if '\x00' in name or name.endswith('.zftp.part'):
        raise ValueError('Unsafe file name')
    return name


def copy_file(source_backend, destination_backend, *, source_dir: str, source_name: str,
              destination_dir: str, destination_name: str | None = None,
              settings: TransferSettings | None = None,
              cancelled: Callable[[], bool] = lambda: False,
              progress: Callable[[str, int, int | None], None] = lambda phase, count, total: None,
              verify: bool = True) -> dict:
    """Copy one file. Never overwrite an existing destination. Non-atomic remote rename
    semantics depend on server implementation. Call only from a worker with both
    sessions reserved; do not call simultaneously with any backend navigation.
    """
    source_dir = normalized_directory(source_dir)
    destination_dir = normalized_directory(destination_dir)
    source_name = safe_filename(source_name)
    destination_name = safe_filename(destination_name or source_name)
    if source_backend is destination_backend:
        raise ValueError('Use two different server connections')
    settings = settings or TransferSettings()
    if cancelled(): raise TransferCancelled('Copy cancelled')
    previous_src, previous_dst = source_backend.pwd(), destination_backend.pwd()
    try:
        # The source file is read from the requested directory, regardless of
        # the tab's later UI selection (the tab is locked while this runs).
        source_backend.cwd(source_dir)
        destination_backend.cwd(destination_dir)
        # Reject known remote symlinks; they can escape the selected share.
        for entry in source_backend.list_dir():
            if entry.name == source_name:
                if entry.is_dir or getattr(entry, 'is_symlink', False):
                    raise ValueError('Source must be a regular file, not a folder or symlink')
                break
        else:
            raise FileNotFoundError(source_name)
        if any(entry.name == destination_name for entry in destination_backend.list_dir()):
            raise FileExistsError('Destination name already exists')
        with tempfile.TemporaryDirectory(prefix='zftp-cross-') as temporary:
            root = Path(temporary)
            try:
                os.chmod(root, 0o700)
            except OSError:
                pass
            staged = root / source_name
            downloaded = download(source_backend, source_name, staged,
                                  lambda count, total: progress('download', count, total),
                                  cancelled, settings)
            if verify:
                progress('verify_source', downloaded, downloaded)
                verify_transfer(source_backend, 'download', staged, source_name, cancelled)
            if cancelled(): raise TransferCancelled('Copy cancelled')
            uploaded = upload(destination_backend, staged, destination_name,
                              lambda count, total: progress('upload', count, total),
                              cancelled, settings)
            if verify:
                progress('verify_destination', uploaded, uploaded)
                digest = verify_transfer(destination_backend, 'upload', staged, destination_name, cancelled)
            else:
                digest = None
            return {'bytes': uploaded, 'sha256': digest, 'source': source_name,
                    'destination': destination_name}
    finally:
        # Restore both server directories even when either transfer fails.
        # A protocol/data-channel error may require reconnecting the session.
        for backend, original in ((source_backend, previous_src), (destination_backend, previous_dst)):
            try: backend.cwd(original)
            except (OSError, EOFError, RuntimeError): pass
