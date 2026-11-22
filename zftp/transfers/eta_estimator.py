"""ETA estimation using a rolling window with stall-awareness. Qt-free."""
from collections import deque
from dataclasses import dataclass, field
import math

@dataclass
class ETAEstimator:
    window: float = 18.0
    minimum_window: float = 1.2
    samples: deque = field(default_factory=deque)
    last_count: int | None = None
    last_time: float | None = None

    def update(self, count: int, total: int | None, now: float):
        if count < 0 or (total is not None and (total <= 0 or count > total)):
            return 0.0, None
        if self.last_time is not None and (now < self.last_time or count < self.last_count):
            self.samples.clear()  # resume/retry: old throughput is no longer representative
        if not self.samples:
            self.samples.append((now, count))
        elif now > self.samples[-1][0] and count != self.samples[-1][1]:
            self.samples.append((now, count))
        self.last_time, self.last_count = now, count
        while len(self.samples) > 2 and self.samples[1][0] < now - self.window:
            self.samples.popleft()
        if total is not None and count >= total:
            return 0.0, 0.0
        # Keep a short warm-up before displaying an estimate.
        elapsed = now - self.samples[0][0]
        if elapsed < self.minimum_window or len(self.samples) < 2:
            return 0.0, None
        # The elapsed time includes network stalls, so the estimate slows down
        # naturally during congestion instead of showing an optimistic old speed.
        bytes_moved = count - self.samples[0][1]
        speed = max(0., bytes_moved / max(elapsed, .001))
        last_change = self.samples[-1][0]
        if now - last_change >= 8.0:
            return 0., None  # stalled; don't claim an accurate completion time
        if speed <= 0:
            return 0., None
        return speed, None if total is None else (total - count) / speed


def format_eta(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "Calculating…"
    seconds = math.ceil(seconds)
    days, remain = divmod(seconds, 86400)
    hours, remain = divmod(remain, 3600)
    minutes, secs = divmod(remain, 60)
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
