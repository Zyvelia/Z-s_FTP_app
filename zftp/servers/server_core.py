"""Opt-in, local-first FTP/FTPS server isolated from the Z FTP client.

No server account/password or private key is persisted. The caller owns UI thread
marshalling; event callbacks are invoked on the server worker thread.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Callable
import ipaddress
import logging
import time


@dataclass(frozen=True)
class ServerAccount:
    username: str
    password: str
    home: str
    writable: bool = False


@dataclass(frozen=True)
class ServerConfig:
    host: str = '127.0.0.1'
    port: int = 2121
    tls: bool = False
    certfile: str = ''
    keyfile: str = ''
    passive_start: int = 30000
    passive_end: int = 30019
    allow_remote_tls: bool = False


def validate_setup(config: ServerConfig, accounts: list[ServerAccount]):
    """Perform checks without importing optional pyftpdlib or opening a socket."""
    try:
        address = ipaddress.ip_address(config.host)
    except ValueError as exc:
        raise ValueError('Bind address must be a numeric IPv4 or IPv6 address') from exc
    if not 1 <= config.port <= 65535:
        raise ValueError('Invalid listening port')
    if config.port < 1024:
        raise ValueError('Use an unprivileged server port (1024–65535)')
    if not 1024 <= config.passive_start <= config.passive_end <= 65535:
        raise ValueError('Invalid passive port range')
    if config.passive_start <= config.port <= config.passive_end:
        raise ValueError('Control port cannot overlap passive data ports')
    if not address.is_loopback and (not config.tls or not config.allow_remote_tls):
        raise ValueError('Non-loopback access requires FTPS and explicit remote access consent')
    if config.tls:
        if not config.certfile or not config.keyfile:
            raise ValueError('FTPS requires a server certificate and private key in PEM format')
        for file in (config.certfile, config.keyfile):
            if not Path(file).is_file():
                raise ValueError('FTPS certificate or private key file does not exist')
    if not accounts:
        raise ValueError('Create at least one server account')
    seen = set()
    for account in accounts:
        name = account.username
        if not name or len(name) > 64 or any(c in name for c in '\r\n\x00'):
            raise ValueError('Invalid account username')
        if name.lower() in ('anonymous', 'ftp'):
            raise ValueError('Anonymous-style accounts are disabled')
        if name.casefold() in seen:
            raise ValueError('Duplicate username')
        seen.add(name.casefold())
        if len(account.password) < 12:
            raise ValueError('Each password must be at least 12 characters')
        root = Path(account.home)
        if not root.is_dir():
            raise ValueError('Each shared folder must already exist')
        if root.is_symlink() or (hasattr(root, 'is_junction') and root.is_junction()):
            raise ValueError('Shared folder cannot be a symbolic link')
    return True


def sanitize_log(value: str) -> str:
    """Prevent control characters or passwords entering the activity UI."""
    return ''.join(c if c.isprintable() and c not in '\r\n' else '?' for c in str(value))[:360]


class ServerController:
    """Runs pyftpdlib with its own async loop in a dedicated thread."""
    def __init__(self, emit: Callable[[str], None] | None = None):
        self.emit = emit or (lambda message: None)
        self._thread: Thread | None = None
        self._stop = Event()
        self._ready = Event()
        self._lock = Lock()
        self._error: Exception | None = None
        self._running = False

    @property
    def running(self):
        with self._lock:
            return self._running

    def start(self, config: ServerConfig, accounts: list[ServerAccount], timeout=5):
        validate_setup(config, accounts)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError('Server already started')
            self._stop = Event()
            self._ready = Event()
            self._error = None
            self._thread = Thread(target=self._serve, args=(config, list(accounts)), name='zftp-server', daemon=False)
            self._thread.start()
        if not self._ready.wait(timeout):
            self._stop.set()
            raise TimeoutError('Server initialization timed out')
        if self._error:
            raise RuntimeError(f'Could not start server: {self._error}') from self._error

    def _serve(self, config, accounts):
        server = None
        try:
            from pyftpdlib.authorizers import DummyAuthorizer
            from pyftpdlib.filesystems import AbstractedFS
            from pyftpdlib.handlers import FTPHandler, TLS_FTPHandler
            from pyftpdlib.servers import FTPServer

            def send(message):
                self.emit(time.strftime("%Y-%m-%d %H:%M:%S") + "  " + sanitize_log(message))
            class ConfinedFS(AbstractedFS):
                def validpath(self, path):
                    # Real paths for symlinks must remain below the assigned home.
                    candidate = Path(path).resolve()
                    root = Path(self.root).resolve()
                    return candidate == root or root in candidate.parents

            authorizer = DummyAuthorizer()
            for account in accounts:
                authorizer.add_user(account.username, account.password, str(Path(account.home).resolve()),
                                    perm='elradfmwMT' if account.writable else 'elr')

            class Handler(TLS_FTPHandler if config.tls else FTPHandler):
                def on_connect(self):
                    send('Connected: ' + sanitize_log(self.remote_ip))
                def on_disconnect(self):
                    send('Disconnected: ' + sanitize_log(self.remote_ip))
                def on_login(self, username):
                    send('Login: ' + sanitize_log(username))
                def on_login_failed(self, username, password):
                    send('Login failed: ' + sanitize_log(username))
                def on_file_received(self, file):
                    send('Uploaded: ' + sanitize_log(Path(file).name))
                def on_file_sent(self, file):
                    send('Downloaded: ' + sanitize_log(Path(file).name))
                def on_incomplete_file_received(self, file):
                    send('Incomplete upload: ' + sanitize_log(Path(file).name))
                def on_incomplete_file_sent(self, file):
                    send('Incomplete download: ' + sanitize_log(Path(file).name))

            Handler.authorizer = authorizer
            Handler.abstracted_fs = ConfinedFS
            Handler.passive_ports = range(config.passive_start, config.passive_end + 1)
            Handler.timeout = 120
            Handler.max_login_attempts = 3
            Handler.banner = 'Z FTP server'
            if config.tls:
                Handler.certfile = config.certfile
                Handler.keyfile = config.keyfile
                Handler.tls_control_required = True
                Handler.tls_data_required = True
            server = FTPServer((config.host, config.port), Handler)
            server.max_cons = 30
            server.max_cons_per_ip = 5
            logging.getLogger('pyftpdlib').setLevel(logging.WARNING)
            with self._lock:
                self._running = True
            self._ready.set()
            send('Listening on ' + sanitize_log(config.host) + ':' + str(config.port) + (' (FTPS)' if config.tls else ' (FTP, loopback only)'))
            while not self._stop.is_set():
                server.serve_forever(timeout=0.2, blocking=False, handle_exit=False)
                self._stop.wait(0.05)
        except Exception as exc:
            self._error = exc
            self.emit('Server error: ' + sanitize_log(type(exc).__name__ + ': ' + str(exc)))
        finally:
            self._ready.set()
            if server is not None:
                try: server.close_all()
                except Exception: pass
            with self._lock:
                self._running = False
            self.emit(time.strftime('%Y-%m-%d %H:%M:%S') + '  Server stopped')

    def stop(self, timeout=5):
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout)
        if thread and thread.is_alive():
            raise TimeoutError('Server did not stop within the shutdown timeout')
        self._thread = None
