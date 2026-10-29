import asyncio

import httpx

from openscour.server import fetcher
from openscour.server.cache import TTLCache
from openscour.server.fetcher import FetchError, PageFetcher
from openscour.server.robots import MAX_ROBOTS_BYTES, RobotsChecker


def robots_client(body: str | None, status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/robots.txt"
        return httpx.Response(status, text=body or "")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_robots_rules_are_followed():
    rules = "User-agent: *\nDisallow: /private/\n\nUser-agent: OpenScour\nDisallow: /no-bots/\n"
    checker = RobotsChecker(robots_client(rules))
    assert await checker.allowed("https://site.example/public/page")
    assert not await checker.allowed("https://site.example/no-bots/page")


async def test_missing_robots_txt_allows_everything_and_is_cached():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    checker = RobotsChecker(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await checker.allowed("https://site.example/anything")
    assert await checker.allowed("https://site.example/another")
    assert calls == 1


async def test_robots_txt_caps_bytes_and_evicts_oldest_origins():
    calls: list[str] = []
    # Disallow rule placed after MAX_ROBOTS_BYTES should never be parsed.
    padding = "# comment\n" * (MAX_ROBOTS_BYTES // 10 + 10)
    oversized = f"User-agent: *\nDisallow: /secret/\n{padding}Disallow: /after-limit/\n"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(200, text=oversized)

    checker = RobotsChecker(httpx.AsyncClient(transport=httpx.MockTransport(handler)), max_items=2)
    assert not await checker.allowed("https://one.example/secret/page")
    assert await checker.allowed("https://one.example/after-limit/page")
    await checker.allowed("https://two.example/page")
    await checker.allowed("https://three.example/page")  # evicts one.example
    await checker.allowed("https://two.example/page")  # still cached
    await checker.allowed("https://one.example/page")  # re-fetched after eviction
    assert calls == ["one.example", "two.example", "three.example", "one.example"]


async def test_robots_txt_is_fetched_once_per_site():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")

    checker = RobotsChecker(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await asyncio.gather(*(checker.allowed(f"https://site.example/p{i}") for i in range(10)))
    assert calls == 1


async def test_concurrent_requests_for_one_url_share_a_fetch(monkeypatch):
    calls = 0

    async def fake_fetch_page(client, url, timeout):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return f"text of {url}"

    monkeypatch.setattr(fetcher, "fetch_page", fake_fetch_page)
    pages = PageFetcher(httpx.AsyncClient(), TTLCache(60))
    results = await asyncio.gather(*(pages.fetch(["https://a.example/"], timeout=1) for _ in range(5)))
    assert calls == 1
    assert all(r["https://a.example/"] == "text of https://a.example/" for r in results)
    # Later requests are served from the cache.
    await pages.fetch(["https://a.example/"], timeout=1)
    assert calls == 1


async def test_download_limit_is_shared_across_requests(monkeypatch):
    monkeypatch.setattr(fetcher.settings, "fetch_concurrency", 3)
    active = peak = 0

    async def fake_fetch_page(client, url, timeout):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return "text"

    monkeypatch.setattr(fetcher, "fetch_page", fake_fetch_page)
    pages = PageFetcher(httpx.AsyncClient(), TTLCache(60))
    await asyncio.gather(*(pages.fetch([f"https://{r}-{i}.example/" for i in range(5)], timeout=1) for r in range(4)))
    assert peak == 3


async def test_deadline_returns_early_and_slow_pages_fill_the_cache(monkeypatch):
    async def fake_fetch_page(client, url, timeout):
        await asyncio.sleep(0.01 if "fast" in url else 0.3)
        return f"text of {url}"

    monkeypatch.setattr(fetcher, "fetch_page", fake_fetch_page)
    cache = TTLCache(60)
    pages = PageFetcher(httpx.AsyncClient(), cache)
    out = await pages.fetch(["https://fast.example/", "https://slow.example/"], timeout=1, deadline=0.1, enough=2)
    assert out["https://fast.example/"] == "text of https://fast.example/"
    assert isinstance(out["https://slow.example/"], FetchError)
    await asyncio.sleep(0.4)
    assert cache.get("https://slow.example/") == "text of https://slow.example/"


async def test_too_many_fetches_in_flight_are_refused(monkeypatch):
    monkeypatch.setattr(fetcher.settings, "max_inflight_fetches", 2)

    async def fake_fetch_page(client, url, timeout):
        await asyncio.sleep(0.05)
        return "text"

    monkeypatch.setattr(fetcher, "fetch_page", fake_fetch_page)
    pages = PageFetcher(httpx.AsyncClient(), TTLCache(60))
    out = await pages.fetch([f"https://{i}.example/" for i in range(4)], timeout=1)
    assert sum(isinstance(v, str) for v in out.values()) == 2
    assert [str(v) for v in out.values() if isinstance(v, FetchError)] == ["server busy", "server busy"]


async def test_robots_disallowed_pages_are_not_downloaded(monkeypatch):
    downloaded = []

    async def fake_fetch_page(client, url, timeout):
        downloaded.append(url)
        return "text"

    monkeypatch.setattr(fetcher, "fetch_page", fake_fetch_page)
    robots = RobotsChecker(robots_client("User-agent: *\nDisallow: /private/\n"))
    pages = PageFetcher(httpx.AsyncClient(), TTLCache(60), robots)
    out = await pages.fetch(["https://s.example/private/x", "https://s.example/public/y"], timeout=1)
    assert str(out["https://s.example/private/x"]) == "disallowed by robots.txt"
    assert downloaded == ["https://s.example/public/y"]
