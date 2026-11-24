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


def test_duckduckgo_fallback_uses_a_search_proxy(monkeypatch):
    import ddgs

    used = []

    class FakeDDGS:
        def __init__(self, proxy=None):
            used.append(proxy)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def text(self, query, max_results, timelimit, region="wt-wt"):
            return [{"title": "A", "href": "https://a.example/", "body": "a"}]

    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    monkeypatch.setattr(providers.settings, "search_proxies", "http://p1:8080, socks5h://p2:1080")

    results = providers._ddg_search_sync("query", 5, "general", None)

    assert [r.url for r in results] == ["https://a.example/"]
    assert used[0] in ("http://p1:8080", "socks5h://p2:1080")

    monkeypatch.setattr(providers.settings, "search_proxies", "")
    providers._ddg_search_sync("query", 5, "general", None)
    assert used[1] is None


def test_results_without_a_host_are_dropped():
    # Brave's redirect links come through SearXNG as "http:///go/v1/...".
    results = [
        providers.RawResult("Broken", "http:///go/v1/AYsk3nEr8x4Aaw", "Read the whitepaper."),
        providers.RawResult(
            "Whitepaper", "https://bitcoin.org/bitcoin.pdf", "Bitcoin: A Peer-to-Peer Electronic Cash System"
        ),
        providers.RawResult("Relative", "/go/v1/abc", "x"),
        providers.RawResult("Not web", "ftp://files.example/a.pdf", "x"),
    ]
    assert [r.url for r in providers.filter_results(results, [], [])] == ["https://bitcoin.org/bitcoin.pdf"]


def test_one_arxiv_paper_takes_one_result():
    results = [
        providers.RawResult("Attention Is All You Need", "https://arxiv.org/pdf/1706.03762", "PDF"),
        providers.RawResult("Attention Is All You Need", "https://arxiv.org/html/1706.03762v3", "HTML"),
        providers.RawResult("[1706.03762] Attention", "https://arxiv.org/abs/1706.03762v7", "abstract"),
        providers.RawResult("Attention", "https://www.arxiv.org/pdf/1706.03762v1.pdf", "old PDF"),
        providers.RawResult("Another paper", "https://arxiv.org/abs/2501.09166", "other"),
        providers.RawResult("Old-style ID", "https://arxiv.org/abs/hep-th/9711200v2", "old id"),
    ]
    kept = [r.url for r in providers.filter_results(results, [], [])]
    assert kept == [
        "https://arxiv.org/pdf/1706.03762",
        "https://arxiv.org/abs/2501.09166",
        "https://arxiv.org/abs/hep-th/9711200v2",
    ]


async def test_language_and_engines_are_sent_to_searxng():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=SEARXNG_JSON)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await providers.search(client, "Wetter Berlin", 5, language="de-DE")
    await providers.search(client, "query", 5, topic="news", engines=["bing news", "brave.news"])
    assert seen[0]["language"] == "de-DE" and seen[0]["categories"] == "general"
    # With engines, no category: SearXNG would add that category's engines too.
    assert seen[1]["engines"] == "bing news,brave.news" and "categories" not in seen[1]


async def test_chosen_engines_get_no_duckduckgo_fallback(monkeypatch):
    called = []
    monkeypatch.setattr(providers, "_ddg_search_sync", lambda *a: called.append(a) or [])
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []})))
    assert await providers.search(client, "query", 5, engines=["bing"]) == []
    assert called == []
    await providers.search(client, "query", 5)
    assert len(called) == 1


def test_duckduckgo_region_from_language():
    assert providers._ddg_region(None) == "wt-wt"
    assert providers._ddg_region("de") == "wt-wt"
    assert providers._ddg_region("de-DE") == "de-de"
    assert providers._ddg_region("en-GB") == "uk-en"


async def test_known_engines_come_from_searxng_config(monkeypatch):
    monkeypatch.setattr(providers, "_known_engines", None)
    config = {"engines": [{"name": "bing", "enabled": True}, {"name": "google", "enabled": False}]}
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=config)))
    assert await providers.known_engines(client) == {"bing", "google"}
    broken = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    monkeypatch.setattr(providers, "_known_engines", None)
    assert await providers.known_engines(broken) is None
