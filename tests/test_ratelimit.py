from openscour.server import ratelimit
from openscour.server.ratelimit import RateLimiter


def test_bucket_allows_burst_then_refills(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])
    limiter = RateLimiter(per_minute=60)  # one request a second, bursts of 60
    assert all(limiter.acquire("a") is None for _ in range(60))
    assert limiter.acquire("a") == 1.0
    assert limiter.acquire("b") is None  # other clients have their own bucket
    now[0] += 1.0
    assert limiter.acquire("a") is None
    assert limiter.acquire("a") is not None


def test_least_recent_clients_are_forgotten():
    limiter = RateLimiter(per_minute=1, max_clients=2)
    for client in ("a", "b", "c"):
        assert limiter.acquire(client) is None
    assert limiter.acquire("c") is not None
    assert limiter.acquire("a") is None  # forgotten, so a fresh bucket


def test_daily_quota_counts_per_client_and_resets_at_utc_midnight():
    from datetime import UTC, datetime

    from openscour.server.ratelimit import DailyQuota

    quota = DailyQuota(per_day=2)
    morning = datetime(2026, 11, 24, 6, 0, tzinfo=UTC)
    assert quota.take("key:a", morning) == (True, 1, 18 * 3600)
    assert quota.take("key:a", morning)[:2] == (True, 0)
    assert quota.take("key:a", morning)[:2] == (False, 0)
    assert quota.take("key:b", morning)[:2] == (True, 1)  # each key has its own quota
    next_day = datetime(2026, 11, 25, 0, 0, 1, tzinfo=UTC)
    assert quota.take("key:a", next_day)[:2] == (True, 1)
