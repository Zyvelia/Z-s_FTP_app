"""Streamed, post-transfer SHA-256 verification without trusting equal file sizes.

The caller exclusively owns the backend connection while verifying. A server
without read permission cannot provide end-to-end verification and will fail
closed when verification is enabled.
"""
from __future__ import annotations
import hashlib
from pathlib import Path

BLOCK = 128 * 1024

class IntegrityMismatch(IOError):
    pass


def local_sha256(path: Path, cancelled=lambda: False) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while True:
            if cancelled(): raise InterruptedError('Verification cancelled')
            block = stream.read(BLOCK)
            if not block: break
            digest.update(block)
    return digest.hexdigest()


def remote_sha256(backend, remote_name: str, cancelled=lambda: False) -> str:
    digest = hashlib.sha256()
    if hasattr(backend, '_sftp'):
        with backend._sftp().open(backend._path(remote_name), 'rb') as stream:
            while True:
                if cancelled(): raise InterruptedError('Verification cancelled')
                block = stream.read(BLOCK)
                if not block: break
                digest.update(block)
    else:
        def receive(block):
            if cancelled(): raise InterruptedError('Verification cancelled')
            digest.update(block)
        # Reading the full file is necessary for protocols without a standard
        # remotely-computed SHA-256 command (FTP, FTPS, and baseline SFTP).
        backend._ftp().retrbinary('RETR ' + remote_name, receive, blocksize=BLOCK)
    return digest.hexdigest()


def verify_transfer(backend, direction: str, local_file: Path, remote_name: str,
                    cancelled=lambda: False) -> str:
    if direction not in ('upload', 'download'):
        raise ValueError('Only files can be verified')
    local_hash = local_sha256(Path(local_file), cancelled)
    remote_hash = remote_sha256(backend, remote_name, cancelled)
    if local_hash != remote_hash:
        raise IntegrityMismatch('SHA-256 mismatch: local and remote files differ; inspect both copies')
    return local_hash
