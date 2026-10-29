"""Per-client rate limiting with a token bucket."""

from __future__ import annotations

import time
from collections import OrderedDict


class RateLimiter:
    """Allows each client ``per_minute`` requests a minute, with bursts up to the same number.

    Buckets live in memory, so each server instance limits on its own. The
    least recently seen clients are forgotten beyond ``max_clients``; a
    forgotten client starts again with a full bucket.
    """

    def __init__(self, per_minute: int, max_clients: int = 10_000) -> None:
        self.capacity = float(per_minute)
        self.refill_per_second = per_minute / 60.0
        self.max_clients = max_clients
        # client -> (tokens, last refill time)
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def acquire(self, client: str) -> float | None:
        """Take one request from the client's bucket.

        Returns None when the request may proceed, or the seconds to wait
        before the next request will be allowed.
        """
        now = time.monotonic()
        tokens, last = self._buckets.pop(client, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_second)
        if tokens >= 1:
            self._buckets[client] = (tokens - 1, now)
            wait = None
        else:
            self._buckets[client] = (tokens, now)
            wait = (1 - tokens) / self.refill_per_second
        while len(self._buckets) > self.max_clients:
            self._buckets.popitem(last=False)
        return wait
