"""Short-lived, bounded, per-session remote directory cache (no credentials)."""
from __future__ import annotations
from collections import OrderedDict
import time

class RemoteDirectoryCache:
    def __init__(self, ttl=10.0, limit=24, clock=None):
        self.ttl = ttl
        self.limit = limit
        self.clock = clock or time.monotonic
        self.items = OrderedDict()

    def get(self, path):
        record = self.items.get(path)
        if not record:
            return None
        when, entries = record
        if self.clock() - when > self.ttl:
            self.items.pop(path, None)
            return None
        self.items.move_to_end(path)
        return list(entries)

    def put(self, path, entries):
        self.items[path] = (self.clock(), tuple(entries))
        self.items.move_to_end(path)
        while len(self.items) > self.limit:
            self.items.popitem(last=False)

    def invalidate(self, path=None):
        if path is None:
            self.items.clear()
        else:
            self.items.pop(path, None)
