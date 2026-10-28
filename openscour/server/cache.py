import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    """Small in-process LRU cache with per-entry expiry.

    Good enough for a single container. Swap for Redis (ElastiCache) once
    you run more than one API task.
    """

    def __init__(self, ttl_seconds: int, max_items: int = 2048):
        self.ttl = ttl_seconds
        self.max_items = max_items
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.monotonic():
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (time.monotonic() + self.ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_items:
            self._data.popitem(last=False)
