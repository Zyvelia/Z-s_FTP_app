"""Opt-in SFTP server (Paramiko). All paths are confined to each account share.

Host keys are user-managed and never auto-regenerated on each start.
Passwords/keys and accounts are not persisted by this component.
"""
from __future__ import annotations

import hmac
import ipaddress
import os
from pathlib import Path, PurePosixPath
import socket
import stat
from threading import Event, Lock, Thread, current_thread
import time
from dataclasses import dataclass
from typing import Callable

from zftp.servers.server_core import ServerAccount, sanitize_log, validate_setup, ServerConfig


@dataclass(frozen=True)
class SFTPServerConfig:
    host: str = '127.0.0.1'
    port: int = 2222
    host_key: str = ''
    allow_remote: bool = False
    max_clients: int = 5
    max_per_ip: int = 3


def validate_sftp_setup(config: SFTPServerConfig, accounts: list[ServerAccount]):
    address = ipaddress.ip_address(config.host)
    if not 1024 <= config.port <= 65535:
        raise ValueError('Choose an unprivileged port (1024–65535)')
    if not address.is_loopback and not config.allow_remote:
        raise ValueError('Non-loopback hosting requires explicit remote access consent')
    if not 1 <= config.max_clients <= 30 or not 1 <= config.max_per_ip <= config.max_clients:
        raise ValueError('Invalid SFTP client limits')
    if not config.host_key or not Path(config.host_key).is_file():
        raise ValueError('Choose an existing SSH host private key (RSA PEM)')
    if Path(config.host_key).is_symlink():
        raise ValueError('SSH host key must not be a symbolic link')
    # Use the existing account policy without a dependency on FTPS certs.
    validate_setup(ServerConfig(), accounts)
    return True


def generate_host_key(destination: str):
    """Explicit one-time creation; refuses to overwrite. Returns SHA256 fingerprint."""
    import paramiko
    import base64
    import hashlib
    target = Path(destination).expanduser()
    if target.parent.is_symlink() or not target.parent.is_dir():
        raise ValueError('Host-key parent directory must exist')
    key = paramiko.RSAKey.generate(3072)
    fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as out:
            key.write_private_key(out)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode('ascii').rstrip('=')


def host_key_fingerprint(path: str):
    import paramiko
    import base64
    import hashlib
    key = paramiko.RSAKey.from_private_key_file(path)
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode('ascii').rstrip('=')


def resolve_share(root: Path, path: str, *, allow_missing=False) -> Path:
    """Fail closed on traversal, symlinks (including intermediate links), and special paths."""
    if not isinstance(path, str) or '\x00' in path or '\\' in path:
        raise PermissionError('Invalid remote path')
    raw = PurePosixPath(path)
    if any(part == '..' for part in raw.parts):
        raise PermissionError('Parent traversal denied')
    if root.is_symlink() or (hasattr(root, 'is_junction') and root.is_junction()):
        raise PermissionError('Invalid shared folder')
    home = root.resolve(strict=True)
    if not home.is_dir():
        raise PermissionError('Invalid shared folder')
    candidate = home
    for part in raw.parts:
        if part in ('/', '.', ''):
            continue
        candidate = candidate / part
        if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
            raise PermissionError('Symbolic links and junctions are not accessible')
        if candidate.exists():
            if home not in candidate.resolve().parents and candidate.resolve() != home:
                raise PermissionError('Outside shared folder')
        elif not allow_missing:
            raise FileNotFoundError(str(candidate))
    if not allow_missing and not candidate.exists():
        raise FileNotFoundError(str(candidate))
    return candidate


class SFTPServerController:
    """Bounded SSH transport accept loop, stoppable without touching FTP/FTPS server."""
    def __init__(self, emit: Callable[[str], None] | None = None):
        self.emit = emit or (lambda message: None)
        self._stop = Event()
        self._ready = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._listener = None
        self._clients: dict[object, tuple[str, Thread]] = {}
        self._error: Exception | None = None
        self._running = False

    @property
    def running(self):
        with self._lock:
            return self._running

    def _log(self, message):
        self.emit(time.strftime('%Y-%m-%d %H:%M:%S') + '  [SFTP] ' + sanitize_log(message))

    def start(self, config: SFTPServerConfig, accounts: list[ServerAccount], timeout=8):
        validate_sftp_setup(config, accounts)
        import paramiko
        key = paramiko.RSAKey.from_private_key_file(config.host_key)
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError('SFTP server already active')
            self._stop = Event()
            self._ready = Event()
            self._error = None
            self._thread = Thread(target=self._serve, args=(config, list(accounts), key), name='zftp-sftp-listener', daemon=False)
            self._thread.start()
        if not self._ready.wait(timeout):
            self._stop.set()
            raise TimeoutError('SFTP server startup timed out')
        if self._error:
            raise RuntimeError(f'SFTP startup failed: {self._error}') from self._error

    def _serve(self, config, accounts, host_key):
        try:
            listener = socket.socket(socket.AF_INET6 if ':' in config.host else socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((config.host, config.port))
            listener.listen(config.max_clients)
            listener.settimeout(0.25)
            self._listener = listener
            with self._lock:
                self._running = True
            self._ready.set()
            self._log(f'Listening on {config.host}:{config.port}; SSH host fingerprint {host_key_fingerprint(config.host_key)}')
            while not self._stop.is_set():
                try:
                    client, address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                ip = address[0]
                with self._lock:
                    # Remove completed transports; live client threads clean up on exit.
                    active_ips = [v[0] for v in self._clients.values()]
                    permit = len(self._clients) < config.max_clients and active_ips.count(ip) < config.max_per_ip
                    if permit:
                        thread = Thread(target=self._client, args=(client, ip, accounts, host_key), name='zftp-sftp-client', daemon=False)
                        self._clients[client] = (ip, thread)
                        thread.start()
                if not permit:
                    self._log('Connection limit reached for ' + ip)
                    client.close()
        except Exception as exc:
            self._error = exc
            self._log('Server error: ' + type(exc).__name__)
        finally:
            if self._listener:
                self._listener.close()
                self._listener = None
            self._ready.set()
            with self._lock:
                self._running = False
            self._log('Listener stopped')

    def _client(self, client, ip, accounts, host_key):
        import paramiko
        transport = None
        try:
            transport = paramiko.Transport(client)
            transport.add_server_key(host_key)
            auth = _Authentication(accounts, lambda m: self._log(m))
            transport.set_subsystem_handler('sftp', paramiko.SFTPServer, _ShareSFTP,
                                            accounts=auth, emit=self._log)
            transport.start_server(server=auth, event=None)
            while not self._stop.is_set() and transport.is_active():
                self._stop.wait(0.2)
        except (OSError, EOFError, paramiko.SSHException) as exc:
            self._log('Session ended: ' + type(exc).__name__)
        finally:
            if transport:
                transport.close()
            try: client.close()
            except OSError: pass
            with self._lock:
                self._clients.pop(client, None)
            self._log('Disconnected: ' + ip)

    def stop(self, timeout=6):
        self._stop.set()
        if self._listener:
            try: self._listener.close()
            except OSError: pass
        with self._lock:
            clients = list(self._clients.items())
        for sock, _ in clients:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            try: sock.close()
            except OSError: pass
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout)
        for _, (_, worker) in clients:
            if worker.is_alive():
                worker.join(max(0.1, timeout / max(1, len(clients))))
        if (thread and thread.is_alive()) or any(worker.is_alive() for _, (_, worker) in clients):
            raise TimeoutError('SFTP sessions did not stop safely')
        self._thread = None


# Paramiko types are resolved lazily so validation works without optional dependencies.
try:
    import paramiko
except ImportError:
    paramiko = None

if paramiko is not None:
    class _Authentication(paramiko.ServerInterface):
        def __init__(self, accounts, log):
            self.accounts = {account.username: account for account in accounts}
            self.account = None
            self.log = log
            self.failures = 0

        def get_allowed_auths(self, username):
            return 'password'

        def check_auth_password(self, username, password):
            self.failures += 1
            # Use dummy comparison to avoid a trivial timing oracle for nonexistent users.
            account = self.accounts.get(username)
            candidate = account.password if account else 'not-a-real-user-password-value'
            good = hmac.compare_digest(candidate.encode(), password.encode())
            if good and account and self.failures <= 3:
                self.account = account
                self.log('Login: ' + username)
                return paramiko.AUTH_SUCCESSFUL
            self.log('Rejected login: ' + sanitize_log(username))
            return paramiko.AUTH_FAILED

        def check_channel_request(self, kind, chanid):
            if kind == 'session' and self.account is not None:
                return paramiko.OPEN_SUCCEEDED
            return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        def check_channel_subsystem_request(self, channel, name):
            return super().check_channel_subsystem_request(channel, name) if name == 'sftp' else False


    class _ShareSFTP(paramiko.SFTPServerInterface):
        def __init__(self, server, *args, accounts, emit, **kwargs):
            super().__init__(server, *args, **kwargs)
            if not server.account:
                raise PermissionError('Authentication required')
            self.account = server.account
            self.home = Path(self.account.home)
            self.emit = emit

        def _path(self, path, missing=False):
            return resolve_share(self.home, path, allow_missing=missing)

        @staticmethod
        def _error(exc):
            if isinstance(exc, PermissionError):
                return paramiko.SFTP_PERMISSION_DENIED
            return paramiko.SFTPServer.convert_errno(getattr(exc, 'errno', None) or 2)

        def list_folder(self, path):
            try:
                directory = self._path(path)
                if not directory.is_dir():
                    return paramiko.SFTP_NO_SUCH_FILE
                result = []
                for child in directory.iterdir():
                    if child.is_symlink():
                        continue
                    attrs = paramiko.SFTPAttributes.from_stat(child.stat())
                    attrs.filename = child.name
                    result.append(attrs)
                return result
            except (OSError, ValueError) as exc:
                return self._error(exc)

        def stat(self, path):
            try:
                return paramiko.SFTPAttributes.from_stat(self._path(path).stat())
            except (OSError, ValueError) as exc:
                return self._error(exc)

        lstat = stat

        def canonicalize(self, path):
            # Purely logical path; no real filesystem path is returned to the client.
            parts = []
            for part in PurePosixPath(path).parts:
                if part == '..':
                    if parts: parts.pop()
                elif part not in ('.', '/', ''):
                    parts.append(part)
            return '/' + '/'.join(parts)

        def open(self, path, flags, attr):
            try:
                write = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
                if write and not self.account.writable:
                    return paramiko.SFTP_PERMISSION_DENIED
                target = self._path(path, missing=bool(flags & os.O_CREAT))
                if target.is_dir():
                    return paramiko.SFTP_PERMISSION_DENIED
                flags |= getattr(os, 'O_NOFOLLOW', 0)
                fd = os.open(target, flags, 0o600)
                mode = ('ab' if flags & os.O_APPEND and flags & os.O_WRONLY
                        else 'a+b' if flags & os.O_APPEND
                        else 'r+b' if flags & os.O_RDWR
                        else 'wb' if flags & os.O_WRONLY else 'rb')
                stream = os.fdopen(fd, mode)
                handle = paramiko.SFTPHandle(flags)
                if write:
                    handle.writefile = stream
                handle.readfile = stream
                self.emit(('Write' if write else 'Read') + ': ' + sanitize_log(path))
                return handle
            except (OSError, ValueError) as exc:
                return self._error(exc)

        def remove(self, path):
            return self._change(path, lambda p: p.unlink(), 'Removed')

        def mkdir(self, path, attr):
            return self._change(path, lambda p: p.mkdir(mode=0o700), 'Directory created', missing=True)

        def rmdir(self, path):
            return self._change(path, lambda p: p.rmdir(), 'Directory removed')

        def _change(self, path, operation, label, missing=False):
            if not self.account.writable:
                return paramiko.SFTP_PERMISSION_DENIED
            try:
                target = self._path(path, missing=missing)
                if target == self.home.resolve():
                    return paramiko.SFTP_PERMISSION_DENIED
                operation(target)
                self.emit(label + ': ' + sanitize_log(path))
                return paramiko.SFTP_OK
            except (OSError, ValueError) as exc:
                return self._error(exc)

        def rename(self, oldpath, newpath):
            if not self.account.writable:
                return paramiko.SFTP_PERMISSION_DENIED
            try:
                src = self._path(oldpath)
                dst = self._path(newpath, missing=True)
                if src == self.home.resolve() or dst.exists():
                    return paramiko.SFTP_PERMISSION_DENIED
                src.rename(dst)
                self.emit('Renamed: ' + sanitize_log(oldpath))
                return paramiko.SFTP_OK
            except (OSError, ValueError) as exc:
                return self._error(exc)

        def setstat(self, path, attr):
            # Refuse remote chmod/utime to avoid unwanted metadata modifications.
            return paramiko.SFTP_OP_UNSUPPORTED

        def symlink(self, target_path, path):
            return paramiko.SFTP_OP_UNSUPPORTED

        def readlink(self, path):
            return paramiko.SFTP_OP_UNSUPPORTED
else:
    class _Authentication:
        pass
    class _ShareSFTP:
        pass
