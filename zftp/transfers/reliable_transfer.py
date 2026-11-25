"""Recoverable staged-file transfers for FTP/FTPS/SFTP.

A .zftp.part file belongs to the user; incomplete data is never silently trusted.
Pre-existing final targets are never overwritten. For resumed data, the entire
staged prefix is compared against the source before appending any bytes.
"""
from __future__ import annotations

import ftplib
import os
import posixpath
import socket
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

CHUNK = 64 * 1024

class UnsafeResumeError(ValueError):
    """An existing partial cannot be proven to match the source."""

class TransferCancelled(InterruptedError):
    pass

@dataclass(frozen=True)
class TransferSettings:
    bandwidth_kib: int = 0  # 0 = unlimited, per active transfer
    retries: int = 3
    backoff: float = 1.0
    chunk_kib: int = 64  # FTP/SFTP I/O chunk size: safe, per-job snapshot

    def __post_init__(self):
        if not 0 <= self.bandwidth_kib <= 1024 * 1024:
            raise ValueError('Bandwidth limit must be between 0 and 1,048,576 KiB/s')
        if not 0 <= self.retries <= 10:
            raise ValueError('Retries must be between 0 and 10')
        if not 0 <= self.backoff <= 60:
            raise ValueError('Backoff must be between 0 and 60 seconds')
        if self.chunk_kib not in (64, 128, 256, 512, 1024):
            raise ValueError('Chunk size must be 64, 128, 256, 512 or 1024 KiB')

    @property
    def chunk_bytes(self):
        return self.chunk_kib * 1024

class RateLimiter:
    def __init__(self, kib_per_second: int, *, clock=time.monotonic, sleep=time.sleep):
        self.rate = kib_per_second * 1024
        self.clock = clock
        self.sleep = sleep
        self.start = clock()
        self.bytes = 0

    def consume(self, count: int, cancelled: Callable[[], bool]):
        self.bytes += count
        if not self.rate:
            return
        deadline = self.start + self.bytes / self.rate
        while True:
            if cancelled():
                raise TransferCancelled('Transfer cancelled')
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            self.sleep(min(remaining, 0.1))

def _check(cancelled):
    if cancelled():
        raise TransferCancelled('Transfer cancelled')

def _local_copy_finish(part: Path, target: Path):
    # Same-directory exclusive hard-link protects final destination even if another
    # process creates it between the existence check and promotion.
    os.link(part, target)
    part.unlink()

def _check_local_partial(part: Path):
    """Reject unsafe pre-existing stage files, including linked files.

    This is a defensive preflight, not a full defense against a hostile
    concurrent local process. Only use directories trusted by the user.
    """
    try:
        info = part.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise UnsafeResumeError('Partial file must be a regular, unlinked file')
    return True


def _ftp_size(ftp, name):
    # FTP servers may reject SIZE while a fresh control channel is in ASCII mode.
    ftp.voidcmd('TYPE I')
    try:
        return ftp.size(name)
    except ftplib.error_perm as exc:
        if str(exc).startswith('550'):
            return None
        raise

def _ftp_prefix_matches(ftp, remote: str, staged: Path, count: int, cancelled, blocksize=CHUNK):
    """Compare the complete staged prefix; do not trust just sizes."""
    with staged.open('rb') as local:
        checked = 0
        def compare(block):
            nonlocal checked
            _check(cancelled)
            available = min(len(block), count - checked)
            if available and local.read(available) != block[:available]:
                raise UnsafeResumeError('Partial file differs from source')
            checked += available
            if checked >= count:
                # Avoid aborting RETR mid-stream: FTP control connection might desync.
                # Instead finish reading silently, without more comparisons.
                return
        ftp.retrbinary('RETR ' + remote, compare, blocksize=blocksize)
    if checked != count:
        raise UnsafeResumeError('Remote file is shorter than staged prefix')

def download(backend, name: str, target: Path, progress, cancelled, settings: TransferSettings):
    """Download to a resumable local partial then promote with no overwrite."""
    chunk = settings.chunk_bytes
    target = Path(target)
    part = target.with_name(target.name + '.zftp.part')
    if target.exists():
        raise FileExistsError(f'Local target already exists: {target}')
    sftp = getattr(backend, 'conn', None)
    is_sftp = hasattr(backend, '_sftp')
    remote = backend._path(name) if is_sftp else name
    size = sftp.stat(remote).st_size if is_sftp else _ftp_size(sftp, remote)
    offset = part.stat().st_size if _check_local_partial(part) else 0
    if size is None and offset:
        raise UnsafeResumeError('Cannot safely resume: server does not report file size')
    if size is not None and offset > size:
        raise UnsafeResumeError('Local partial is larger than remote file')
    if offset:
        if is_sftp:
            with sftp.open(remote, 'rb') as source, part.open('rb') as local:
                remaining = offset
                while remaining:
                    _check(cancelled)
                    block = source.read(min(chunk, remaining))
                    if not block or block != local.read(len(block)):
                        raise UnsafeResumeError('Local partial does not match remote source')
                    remaining -= len(block)
        else:
            _ftp_prefix_matches(sftp, remote, part, offset, cancelled, chunk)
    limiter = RateLimiter(settings.bandwidth_kib)
    written = offset
    progress(written, size)
    _check(cancelled)
    # O_NOFOLLOW prevents following a swapped-in symbolic link on platforms
    # supporting it. O_EXCL protects new stage creation from a racing creator.
    flags = os.O_WRONLY | os.O_APPEND
    if not offset and not part.exists():
        flags |= os.O_CREAT | os.O_EXCL
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(part, flags, 0o600)
    with os.fdopen(fd, 'ab') as output:
        info = os.fstat(output.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise UnsafeResumeError('Partial file changed or is multiply linked')
        if info.st_size != offset:
            raise UnsafeResumeError('Partial file changed during resume verification')
        if is_sftp:
            with sftp.open(remote, 'rb') as source:
                source.seek(offset)
                while True:
                    _check(cancelled)
                    block = source.read(chunk)
                    if not block: break
                    output.write(block)
                    written += len(block)
                    progress(written, size)
                    limiter.consume(len(block), cancelled)
        elif size is None or offset != size:
            def write(block):
                nonlocal written
                _check(cancelled)
                output.write(block)
                written += len(block)
                progress(written, size)
                limiter.consume(len(block), cancelled)
            sftp.retrbinary('RETR ' + remote, write, blocksize=chunk, rest=offset or None)
    if size is not None and written != size:
        raise OSError(f'Incomplete transfer: {written} of {size} bytes')
    _check(cancelled)
    _local_copy_finish(part, target)
    return written

def upload(backend, source: Path, name: str, progress, cancelled, settings: TransferSettings, *, verify_staged=None, on_phase=None):
    """Upload to .zftp.part then rename into place, without replacing a final file."""
    chunk = settings.chunk_bytes
    source = Path(source)
    size = source.stat().st_size
    if not source.is_file(): raise FileNotFoundError(source)
    is_sftp = hasattr(backend, '_sftp')
    conn = backend._sftp() if is_sftp else backend._ftp()
    final = backend._path(name) if is_sftp else name
    partial = final + '.zftp.part'
    if is_sftp:
        try: conn.stat(final)
        except FileNotFoundError: pass
        else: raise FileExistsError(f'Remote target exists: {final}')
        try: offset = conn.stat(partial).st_size
        except FileNotFoundError: offset = 0
    else:
        if _ftp_size(conn, final) is not None:
            raise FileExistsError(f'Remote target exists: {final}')
        offset = _ftp_size(conn, partial) or 0
    if offset > size:
        raise UnsafeResumeError('Remote partial is larger than local file')
    if offset:
        if is_sftp:
            with conn.open(partial, 'rb') as remote_file, source.open('rb') as local:
                remaining = offset
                while remaining:
                    _check(cancelled)
                    block = remote_file.read(min(chunk, remaining))
                    if not block or block != local.read(len(block)):
                        raise UnsafeResumeError('Remote partial does not match local source')
                    remaining -= len(block)
        else:
            _ftp_prefix_matches(conn, partial, source, offset, cancelled, chunk)
    limiter = RateLimiter(settings.bandwidth_kib)
    progress(offset, size)
    _check(cancelled)
    if offset != size or not offset:
        if is_sftp:
            # Start files with exclusive create. Existing partials are opened r+b.
            mode = 'r+b' if offset else 'x'
            with source.open('rb') as local, conn.open(partial, mode) as remote_file:
                local.seek(offset)
                if offset: remote_file.seek(offset)
                written = offset
                while True:
                    _check(cancelled)
                    block = local.read(chunk)
                    if not block: break
                    remote_file.write(block)
                    written += len(block)
                    progress(written, size)
                    limiter.consume(len(block), cancelled)
        else:
            with source.open('rb') as local:
                local.seek(offset)
                written = offset
                def report(block):
                    nonlocal written
                    _check(cancelled)
                    written += len(block)
                    progress(written, size)
                    limiter.consume(len(block), cancelled)
                conn.storbinary('STOR ' + partial, local, blocksize=chunk,
                                callback=report, rest=offset or None)
    # Read and verify the staged file BEFORE it is given the final filename.
    # A mismatch leaves the resumable .zftp.part file, never a published .pkg.
    _check(cancelled)
    if verify_staged is not None:
        if on_phase: on_phase('Verifying')
        verify_staged(partial)
    _check(cancelled)
    if on_phase: on_phase('Renaming')
    # Renames are only safe if the server supports the required no-clobber
    # guarantee. SFTP posix_rename overwrites; standard SFTP rename is server-
    # dependent. Reject promotion if a final appeared, but external race remains.
    if is_sftp:
        try: conn.stat(final)
        except FileNotFoundError: pass
        else: raise FileExistsError(f'Remote target appeared: {final}')
        if conn.stat(partial).st_size != size:
            raise OSError('Remote staged file has incorrect size')
        conn.rename(partial, final)
    else:
        if _ftp_size(conn, final) is not None:
            raise FileExistsError(f'Remote target appeared: {final}')
        if _ftp_size(conn, partial) != size:
            raise OSError('Remote staged file has incorrect size')
        conn.rename(partial, final)
    return size

def retryable(exc: Exception):
    if isinstance(exc, (TransferCancelled, UnsafeResumeError, FileExistsError, FileNotFoundError, IsADirectoryError, PermissionError, ValueError)):
        return False
    if isinstance(exc, ftplib.error_perm):
        return False
    return isinstance(exc, (OSError, EOFError, ftplib.error_temp, ftplib.error_proto, ftplib.error_reply))

def with_retries(action, reconnect, settings: TransferSettings, cancelled, on_retry=None, sleep=time.sleep):
    for attempt in range(settings.retries + 1):
        _check(cancelled)
        try:
            return action()
        except Exception as exc:
            if not retryable(exc) or attempt >= settings.retries:
                raise
            wait = min(60., settings.backoff * (2 ** attempt))
            if on_retry: on_retry(attempt + 1, wait, str(exc))
            elapsed = 0.
            while elapsed < wait:
                _check(cancelled)
                interval = min(.1, wait - elapsed)
                sleep(interval)
                elapsed += interval
            _check(cancelled)
            reconnect()
    raise AssertionError('unreachable')
