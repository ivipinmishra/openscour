"""Per-client rate limits (a token bucket) and daily quotas."""

from __future__ import annotations

import time
from collections import OrderedDict
from datetime import UTC, date, datetime, timedelta


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


class DailyQuota:
    """Allows each client ``per_day`` requests per UTC day.

    Counts live in memory, so each server instance counts on its own, and a
    restart starts the day again. Beyond ``max_clients`` the least recently seen
    clients are forgotten.
    """

    def __init__(self, per_day: int, max_clients: int = 100_000) -> None:
        self.per_day = per_day
        self.max_clients = max_clients
        self._day: date | None = None
        self._used: OrderedDict[str, int] = OrderedDict()

    def take(self, client: str, now: datetime | None = None) -> tuple[bool, int, float]:
        """Use one request from today's quota.

        Returns (allowed, requests left today, seconds until the quota resets).
        """
        now = now or datetime.now(UTC)
        if now.date() != self._day:
            self._day = now.date()
            self._used.clear()
        tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        reset = (tomorrow - now).total_seconds()
        used = self._used.pop(client, 0)
        allowed = used < self.per_day
        self._used[client] = used + 1 if allowed else used
        while len(self._used) > self.max_clients:
            self._used.popitem(last=False)
        return allowed, max(0, self.per_day - self._used[client]), reset
