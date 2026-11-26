"""Thread-safe snapshots of hosted client sessions and their open transfers."""

from dataclasses import dataclass, field
from threading import Lock
from time import monotonic


@dataclass
class Client:
    address: str
    connected: float
    username: str = ''
    transfers: dict = field(default_factory=dict)


class ServerClients:
    def __init__(self):
        self.lock = Lock()
        self.clients = {}

    def connect(self, key, address):
        with self.lock:
            self.clients[key] = Client(address, monotonic())

    def login(self, key, username):
        with self.lock:
            client = self.clients.get(key)
            if client:
                client.username = username

    def transfer(self, key, handle, description=None):
        with self.lock:
            client = self.clients.get(key)
            if client is None:
                return
            if description is None:
                client.transfers.pop(handle, None)
            else:
                client.transfers[handle] = description

    def disconnect(self, key):
        with self.lock:
            self.clients.pop(key, None)

    def snapshot(self):
        now = monotonic()
        with self.lock:
            return [(c.address, c.username, int(now - c.connected), tuple(c.transfers.values()))
                    for c in self.clients.values()]
