"""Endpoint tests: the real FastAPI app, with the search engine and page fetching faked."""

import pytest
from fastapi.testclient import TestClient

from openscour.server import main, providers
from openscour.server.cache import TTLCache
from openscour.server.fetcher import FetchError
from openscour.server.providers import RawResult

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

    async def fetch(self, urls, timeout, deadline=None, enough=None):
        self.requested.append(urls)
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
