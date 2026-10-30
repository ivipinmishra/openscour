"""Endpoint tests: the real FastAPI app, with the search engine and page fetching faked."""

import httpx
import pytest
from fastapi.testclient import TestClient

from openscour.server import main, providers
from openscour.server.cache import TTLCache
from openscour.server.fetcher import FetchError
from openscour.server.providers import RawResult
from openscour.server.ratelimit import RateLimiter

VP_PAGE = (
    "Many vice presidents served in Congress before taking office.\n"
    "John Adams was the first vice president. "
    "JD Vance is the 50th and current vice president since January 20, 2025.\n"
    # A long page: extract returns 3 passages by default, which must be a small part of it.
    + "The office has changed a great deal over two centuries. " * 120
)


class FakePages:
    def __init__(self, pages: dict[str, str | FetchError]):
        self.pages = pages
        self.requested: list[list[str]] = []
        self.enough: list[int | None] = []

    async def fetch(self, urls, timeout, deadline=None, enough=None):
        self.requested.append(urls)
        self.enough.append(enough)
        return {u: self.pages.get(u, FetchError("not found")) for u in urls}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "search_cache", TTLCache(60))
    monkeypatch.setattr(main.settings, "api_keys", "")
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def fake_search(monkeypatch):
    calls = []
    results = [
        RawResult("Vice President of the United States", "https://wiki.example/vp", "The VP office.", None),
        RawResult("Slow site", "https://slow.example/vp", "Snippet: JD Vance is vice president.", "2026-10-01"),
    ]

    async def search(client, query, max_candidates, **kwargs):
        calls.append((query, kwargs))
        if (stats := kwargs.get("stats")) is not None:
            stats.update(engines=["bing", "brave"], engines_failed={"google": "CAPTCHA"})
        return results

    monkeypatch.setattr(providers, "search", search)
    return calls


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_api_key_required_when_configured(client, fake_search, monkeypatch):
    client.app.state.pages = FakePages({"https://wiki.example/vp": VP_PAGE})
    monkeypatch.setattr(main.settings, "api_keys", "key-one, key-two")
    assert client.post("/search", json={"query": "x"}).status_code == 401
    assert client.post("/search", json={"query": "x"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/extract", json={"urls": ["https://a.example/"]}).status_code == 401
    assert client.post("/search", json={"query": "x"}, headers={"Authorization": "Bearer key-two"}).status_code == 200


def test_search_ranks_answer_first_and_uses_snippets_for_missing_pages(client, fake_search):
    client.app.state.pages = FakePages({"https://wiki.example/vp": VP_PAGE})
    body = client.post("/search", json={"query": "Who is vice president in the USA?", "max_results": 2}).json()
    top = body["results"][0]
    assert top["url"] == "https://wiki.example/vp"
    assert top["content"].startswith("JD Vance is the 50th and current vice president")
    assert top["score"] == 1.0
    slow = next(r for r in body["results"] if r["url"] == "https://slow.example/vp")
    assert "Snippet" in slow["content"]
    assert slow["published_date"] == "2026-10-01"


@pytest.mark.parametrize(("max_results", "enough"), [(1, 3), (3, 3), (5, 5)])
def test_basic_search_reads_several_pages_before_answering(client, fake_search, max_results, enough):
    # Returning after the first page is ready would rank whichever page loads
    # fastest against bare snippets, instead of the page that answers.
    client.app.state.pages = pages = FakePages({})
    client.post("/search", json={"query": "who is vice president", "max_results": max_results})
    assert pages.enough == [enough]


def test_search_validation_and_no_results(client, monkeypatch):
    assert client.post("/search", json={"query": ""}).status_code == 422
    assert client.post("/search", json={"query": "x", "max_results": 50}).status_code == 422

    async def nothing(*args, **kwargs):
        return []

    monkeypatch.setattr(providers, "search", nothing)
    assert client.post("/search", json={"query": "zzz"}).status_code == 502


def test_search_cache_ignores_domain_order(client, fake_search):
    client.app.state.pages = FakePages({"https://wiki.example/vp": VP_PAGE})
    first = {"query": "vice president", "include_domains": ["a.example", "b.example"]}
    second = {"query": "vice president", "include_domains": ["b.example", "a.example"]}
    assert client.post("/search", json=first).status_code == 200
    assert client.post("/search", json=second).status_code == 200
    assert len(fake_search) == 1


def test_extract_with_query_returns_relevant_passages(client):
    client.app.state.pages = FakePages({"https://wiki.example/vp": VP_PAGE})
    body = client.post(
        "/extract",
        json={"urls": ["https://wiki.example/vp", "https://gone.example/"], "query": "Who is the current vice president?"},
    ).json()
    text = body["results"][0]["raw_content"]
    assert text.startswith("JD Vance is the 50th")
    assert len(text) < len(VP_PAGE) / 2
    assert body["failed_results"] == [{"url": "https://gone.example/", "error": "not found"}]


def test_extract_refuses_private_addresses(client):
    """Uses the real page fetcher: requests for internal addresses never leave the guard."""
    body = client.post(
        "/extract", json={"urls": ["http://127.0.0.1:9/", "http://169.254.169.254/latest/meta-data/"]}
    ).json()
    assert body["results"] == []
    assert all(f["error"].startswith("blocked:") for f in body["failed_results"])


def test_rate_limit_per_key_with_retry_after(client, fake_search, monkeypatch):
    client.app.state.pages = FakePages({"https://wiki.example/vp": VP_PAGE})
    client.app.state.limiter = RateLimiter(per_minute=2)
    monkeypatch.setattr(main.settings, "api_keys", "key-one, key-two")
    one = {"Authorization": "Bearer key-one"}
    assert client.post("/search", json={"query": "x"}, headers=one).status_code == 200
    assert client.post("/extract", json={"urls": ["https://wiki.example/vp"]}, headers=one).status_code == 200
    limited = client.post("/search", json={"query": "x"}, headers=one)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    # Each key has its own limit, and bad keys are rejected before counting.
    assert client.post("/search", json={"query": "x"}, headers={"Authorization": "Bearer key-two"}).status_code == 200
    assert client.post("/search", json={"query": "x"}, headers={"Authorization": "Bearer bad"}).status_code == 401


def test_rate_limit_by_forwarded_ip_only_when_trusted(client, fake_search, monkeypatch):
    client.app.state.pages = FakePages({})
    client.app.state.limiter = RateLimiter(per_minute=1)
    forged = [{"X-Forwarded-For": f"10.0.0.{i}"} for i in range(2)]
    # Untrusted: the header is ignored, so both requests come from the same client.
    assert client.post("/search", json={"query": "x"}, headers=forged[0]).status_code == 200
    assert client.post("/search", json={"query": "y"}, headers=forged[1]).status_code == 429

    monkeypatch.setattr(main.settings, "trust_forwarded_for", True)
    client.app.state.limiter = RateLimiter(per_minute=1)
    first = {"X-Forwarded-For": "1.1.1.1, 203.0.113.7"}
    assert client.post("/search", json={"query": "x"}, headers=first).status_code == 200
    assert client.post("/search", json={"query": "y"}, headers=first).status_code == 429
    # The last address is the one the proxy saw; earlier ones are the client's claim.
    other = {"X-Forwarded-For": "1.1.1.1, 203.0.113.8"}
    assert client.post("/search", json={"query": "z"}, headers=other).status_code == 200


def test_request_id_is_echoed_or_generated(client):
    assert client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"] == "abc-123"
    generated = client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"}).headers["X-Request-ID"]
    assert len(generated) == 32 and generated.isalnum()


def test_request_log_line(client, fake_search, caplog):
    client.app.state.pages = FakePages({"https://wiki.example/vp": VP_PAGE})
    with caplog.at_level("INFO", logger="openscour.access"):
        client.post("/search", json={"query": "vice president"}, headers={"X-Request-ID": "req-1"})
        client.post("/search", json={"query": "vice president"}, headers={"X-Request-ID": "req-2"})
    first, second = (r.getMessage() for r in caplog.records if r.name == "openscour.access")
    assert first.startswith("request_id=req-1 method=POST path=/search status=200 duration_ms=")
    assert "cache=miss engines=bing,brave engines_failed=google:CAPTCHA candidates=2 pages_fetched=1" in first
    assert "cache=hit" in second


@pytest.mark.parametrize(
    "searxng_status,fallback,status,code",
    [(200, True, "ready", 200), (None, True, "degraded", 200), (500, False, "not ready", 503)],
)
def test_readiness(client, monkeypatch, searxng_status, fallback, status, code):
    monkeypatch.setattr(providers, "engine_health", providers.EngineHealth())
    def handler(request):
        assert request.url.path == "/healthz"
        if searxng_status is None:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(searxng_status, text="OK")

    client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(main.settings, "duckduckgo_fallback", fallback)
    resp = client.get("/health/ready")
    assert resp.status_code == code
    assert resp.json()["status"] == status


def test_readiness_is_degraded_when_every_engine_is_blocked(client, monkeypatch):
    health = providers.EngineHealth()
    monkeypatch.setattr(providers, "engine_health", health)
    client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    health.record([], {"google": "CAPTCHA", "duckduckgo": "too many requests"})
    body = client.get("/health/ready").json()
    assert body["status"] == "degraded"
    assert body["checks"]["engines"] == {"duckduckgo": "too many requests", "google": "CAPTCHA"}

    health.record(["brave"], {})
    assert client.get("/health/ready").json()["status"] == "ready"
