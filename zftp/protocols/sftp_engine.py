"""SFTP/SSH backend with strict host-key checks and opt-in session trust."""
from __future__ import annotations

import base64
import hashlib
import posixpath
import socket
from pathlib import Path
from typing import Callable

import paramiko
from zftp.protocols.ftp_engine import RemoteEntry, clean_remote_name


class UntrustedHostKey(Exception):
    def __init__(self, host: str, key: paramiko.PKey):
        self.host = host
        self.key_type = key.get_name()
        self.key_b64 = key.get_base64()
        self.fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        super().__init__(f'Unrecognized SSH host key for {host}: {self.key_type} {self.fingerprint}')


class _SessionTrustPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, trusted: dict[str, str]):
        self.trusted = trusted

    def missing_host_key(self, client, hostname, key):
        if self.trusted.get(hostname) == key.get_base64():
            client.get_host_keys().add(hostname, key.get_name(), key)
            return
        raise UntrustedHostKey(hostname, key)


class SFTPBackend:
    def __init__(self):
        self.client: paramiko.SSHClient | None = None
        self.conn: paramiko.SFTPClient | None = None
        self._cwd = '/'
        self._trusted_once: dict[str, str] = {}

    def trust_host_once(self, hostname: str, key_b64: str):
        """Called only after the user accepts the exact key presented in the GUI."""
        self._trusted_once[hostname] = key_b64

    def new_session(self):
        """Create a separate connection with the tab's existing SSH trust."""
        backend = SFTPBackend()
        backend._trusted_once = self._trusted_once.copy()
        return backend

    def connect(self, host: str, port: int, username: str, password: str, *, tls: bool = True, timeout: int = 15, key_filename: str | None = None, passphrase: str | None = None):
        del tls  # SFTP uses SSH, not TLS.
        self.disconnect()
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        local_known_hosts = Path.home() / '.ssh' / 'known_hosts'
        if local_known_hosts.exists():
            client.load_host_keys(str(local_known_hosts))
        client.set_missing_host_key_policy(_SessionTrustPolicy(self._trusted_once))
        try:
            client.connect(hostname=host, port=port, username=username, password=password,
                           timeout=timeout, auth_timeout=timeout, banner_timeout=timeout,
                           allow_agent=False, look_for_keys=False,
                           key_filename=key_filename or None, passphrase=passphrase or None)
            sftp = client.open_sftp()
            self.client, self.conn = client, sftp
            self._cwd = sftp.normalize('.')
            return self._cwd
        except BaseException:
            client.close()
            raise

    def disconnect(self):
        conn, client = self.conn, self.client
        self.conn = None
        self.client = None
        if conn:
            conn.close()
        if client:
            client.close()

    def _sftp(self):
        if self.conn is None:
            raise RuntimeError('Not connected')
        return self.conn

    def pwd(self):
        return self._cwd

    def cwd(self, path: str):
        dest = path if path.startswith('/') else posixpath.join(self._cwd, path)
        dest = posixpath.normpath(dest)
        stat = self._sftp().stat(dest)
        import stat as statlib
        if not statlib.S_ISDIR(stat.st_mode):
            raise NotADirectoryError(dest)
        self._cwd = dest

    def list_dir(self) -> list[RemoteEntry]:
        import stat as statlib
        entries = []
        for item in self._sftp().listdir_attr(self._cwd):
            if item.filename in ('.', '..'):
                continue
            is_dir = statlib.S_ISDIR(item.st_mode)
            entries.append(RemoteEntry(item.filename, is_dir, None if is_dir else item.st_size, statlib.S_ISLNK(item.st_mode)))
        return sorted(entries, key=lambda item: (not item.is_dir, item.name.casefold()))

    def _path(self, name: str) -> str:
        return posixpath.join(self._cwd, clean_remote_name(name))

    def mkdir(self, name: str):
        self._sftp().mkdir(self._path(name))

    def delete_file(self, name: str):
        self._sftp().remove(self._path(name))

    def remove_empty_dir(self, name: str):
        self._sftp().rmdir(self._path(name))

    def download(self, name: str, destination: Path, progress: Callable[[int, int | None], None], cancelled: Callable[[], bool]):
        remote = self._path(name)
        sftp = self._sftp()
        size = sftp.stat(remote).st_size
        temp = destination.with_name(destination.name + '.zftp.part')
        try:
            with temp.open('xb') as output:
                with sftp.open(remote, 'rb') as source:
                    copied = 0
                    while True:
                        if cancelled():
                            raise InterruptedError('Download cancelled')
                        block = source.read(64 * 1024)
                        if not block:
                            break
                        output.write(block)
                        copied += len(block)
                        progress(copied, size)
            # Exclusive creation prevents overwriting a file created during the download.
            created_final = False
            try:
                with destination.open('xb') as final:
                    created_final = True
                    with temp.open('rb') as staged:
                        import shutil
                        shutil.copyfileobj(staged, final)
            except BaseException:
                if created_final:
                    destination.unlink(missing_ok=True)
                raise
        finally:
            temp.unlink(missing_ok=True)

    def upload(self, source: Path, remote_name: str, progress: Callable[[int, int | None], None], cancelled: Callable[[], bool]):
        sftp = self._sftp()
        remote = self._path(remote_name)
        # Avoid silently overwriting a remote file if the listing is stale.
        try:
            sftp.stat(remote)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(remote)
        size = source.stat().st_size
        copied = 0
        created = False
        try:
            with source.open('rb') as input_file, sftp.open(remote, 'x') as output:
                created = True
                while True:
                    if cancelled():
                        raise InterruptedError('Upload cancelled')
                    block = input_file.read(64 * 1024)
                    if not block:
                        break
                    output.write(block)
                    copied += len(block)
                    progress(copied, size)
        except BaseException:
            if created:
                try:
                    sftp.remove(remote)
                except (OSError, EOFError):
                    pass
            raise

    def rename(self, old_name: str, new_name: str):
        self._sftp().rename(self._path(old_name), self._path(new_name))

    def chmod(self, name: str, mode: int):
        if not 0 <= mode <= 0o777:
            raise ValueError('Invalid permission mode')
        self._sftp().chmod(self._path(name), mode)

    def read_bytes(self, name: str, max_bytes: int) -> bytes:
        with self._sftp().open(self._path(name), 'rb') as source:
            data = source.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError('Remote file is too large for the editor')
        return data

    def read_prefix(self, name: str, length: int) -> bytes:
        with self._sftp().open(self._path(name), 'rb') as source:
            return source.read(length)

    def write_new_bytes(self, name: str, data: bytes):
        with self._sftp().open(self._path(name), 'x') as target:
            target.write(data)
