"""Per-tab, independently owned connection state, with no Qt dependency."""
from dataclasses import dataclass, field
from threading import Event
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
    downloads: dict[str, Event] = field(default_factory=dict)
    parallel_downloads: bool = True

    @property
    def active(self):
        return self.busy or bool(self.downloads)

    def cancel_transfer(self, transfer_id):
        if transfer_id in self.downloads:
            self.downloads[transfer_id].set()
        else:
            self.cancelled = True

    def cancel_downloads(self):
        for cancellation in self.downloads.values():
            cancellation.set()

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
        if self.active:
            return False
        self.closing = True
        return True

    def clear(self):
        self.connected = False
        self.entries.clear()
        self.remote_path = '/'
