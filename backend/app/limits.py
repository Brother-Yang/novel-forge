"""Thread-safe sliding windows shared by access control and model clients."""

import time
from collections import deque
from threading import Lock


class WindowBudget:
    """Keep recent allowed attempts without allocating unbounded identity keys."""

    def __init__(self) -> None:
        super().__init__()
        self._entries: dict[str, deque[float]] = {}
        self._lock = Lock()

    def consume(self, key: str, maximum: int, seconds: int) -> int | None:
        with self._lock:
            now = time.monotonic()
            if len(self._entries) >= 1024:
                self._entries = {
                    name: rows
                    for name, rows in self._entries.items()
                    if rows and rows[-1] > now - seconds
                }
                if key not in self._entries and len(self._entries) >= 1024:
                    return seconds
            rows = self._entries.setdefault(key, deque())
            while rows and rows[0] <= now - seconds:
                rows.popleft()
            if len(rows) >= maximum:
                return max(1, int(rows[0] + seconds - now) + 1)
            rows.append(now)
        return None
