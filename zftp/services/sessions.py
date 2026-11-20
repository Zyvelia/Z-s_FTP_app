"""Per-tab, independently owned connection state, with no Qt dependency."""
from dataclasses import dataclass, field
from typing import Any
from zftp.protocols.ftp_engine import RemoteEntry

@dataclass
class ConnectionSession:
    backend: Any
    protocol: str
    host: str
    port: int
    username: str
    remote_path: str = '/'
    entries: list[RemoteEntry] = field(default_factory=list)
    busy: bool = False
    cancelled: bool = False
    closing: bool = False
    connected: bool = False
    generation: int = 0

    def begin(self):
        if self.busy or self.closing:
            raise RuntimeError('Session is busy or closing')
        self.busy = True
        self.cancelled = False
        self.generation += 1
        return self.generation

    def complete(self, generation):
        if generation != self.generation:
            return False
        self.busy = False
        return True

    def request_close(self):
        if self.busy:
            return False
        self.closing = True
        return True

    def clear(self):
        self.connected = False
        self.entries.clear()
        self.remote_path = '/'
