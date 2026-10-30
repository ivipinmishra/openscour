import httpx

from openscour.server import providers

SEARXNG_JSON = {
    "results": [
        {"title": "A", "url": "https://a.example/", "content": "a", "engines": ["brave", "bing"]},
        {"title": "B", "url": "https://b.example/", "content": "b", "engines": ["bing"]},
    ],
    "unresponsive_engines": [["google", "CAPTCHA"], ["duckduckgo", "Suspended: too many requests"]],
}


async def test_searxng_engine_outcomes_are_reported_and_remembered(monkeypatch):
    health = providers.EngineHealth()
    monkeypatch.setattr(providers, "engine_health", health)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=SEARXNG_JSON)))

    stats: dict = {}
    results = await providers.search(client, "query", 5, stats=stats)

    assert [r.url for r in results] == ["https://a.example/", "https://b.example/"]
    assert stats == {
        "engines": ["bing", "brave"],
        "engines_failed": {"google": "CAPTCHA", "duckduckgo": "too many requests"},
    }
    assert health.recent() == {"bing": "ok", "brave": "ok", "duckduckgo": "too many requests", "google": "CAPTCHA"}


def test_engine_health_forgets_old_outcomes(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(providers.time, "monotonic", lambda: now[0])
    health = providers.EngineHealth()
    health.record(["brave"], {"google": "CAPTCHA"})
    now[0] += 600
    health.record(["google"], {})  # recovered
    assert health.recent() == {"brave": "ok", "google": "ok"}
    now[0] += 600
    assert health.recent() == {"google": "ok"}
