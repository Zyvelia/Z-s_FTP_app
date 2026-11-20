"""Bounded directory scanning and GUI batching. No Qt required for testing."""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

BATCH_SIZE = 200

@dataclass(frozen=True)
class LocalEntry:
    name: str
    is_dir: bool
    size: int | None

def scan_directory(path: Path) -> list[LocalEntry]:
    """Use scandir's cached type information; avoid extra stats for folders."""
    found = []
    with os.scandir(path) as entries:
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                # Do not follow local symlinks while determining file size.
                size = None if is_dir else entry.stat(follow_symlinks=False).st_size
                found.append(LocalEntry(entry.name, is_dir, size))
            except OSError:
                continue  # A file vanished between enumeration and stat.
    found.sort(key=lambda entry: (not entry.is_dir, entry.name.casefold()))
    return found

def batches(items, size=BATCH_SIZE):
    if size < 1:
        raise ValueError('Batch size must be positive')
    for index in range(0, len(items), size):
        yield items[index:index + size]

class RenderGate:
    """Generation check prevents stale/old path contents appearing after navigation."""
    def __init__(self):
        self.generation = 0
    def new(self):
        self.generation += 1
        return self.generation
    def current(self, value):
        return self.generation == value

class UpdateThrottle:
    """Limit expensive redraws without suppressing terminal updates."""
    def __init__(self, interval=.12, clock=None):
        import time
        self.interval = interval
        self.clock = clock or time.monotonic
        self.last = None
    def due(self, *, finished=False):
        now = self.clock()
        if finished or self.last is None or now - self.last >= self.interval:
            self.last = now
            return True
        return False
