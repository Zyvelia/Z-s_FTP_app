"""Bounded notification model, deliberately independent of PySide6."""
from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass(frozen=True)
class Notice:
    level: str
    title: str
    message: str
    timestamp: str

class NotificationHistory:
    def __init__(self, capacity: int = 120):
        if not 1 <= capacity <= 10000:
            raise ValueError('capacity must be between 1 and 10000')
        self.capacity = capacity
        self.items: list[Notice] = []

    def add(self, level: str, title: str, message: str) -> Notice:
        if level not in {'info', 'success', 'warning', 'error'}:
            raise ValueError('unsupported notification level')
        notice = Notice(level, str(title)[:140], str(message)[:1000],
                        datetime.now(timezone.utc).isoformat(timespec='seconds'))
        self.items.append(notice)
        if len(self.items) > self.capacity:
            del self.items[:-self.capacity]
        return notice
