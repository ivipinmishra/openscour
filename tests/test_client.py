import json

import httpx
import pytest

from openscour import (
    APIConnectionError,
    AsyncOpenScour,
    AuthenticationError,
    APIStatusError,
    NoResultsError,
    OpenScour,
    RateLimitError,
)
from openscour import client as client_module

SEARCH_JSON = {
    "query": "Latest AWS Bedrock updates",
    "answer": None,
    "results": [
        {
            "title": "What's new in Bedrock",
            "url": "https://aws.amazon.com/bedrock/",
            "content": "Bedrock now supports...",
            "score": 1.0,
            "raw_content": None,
            "published_date": "2026-10-20",
        }
    ],
    "response_time": 1.23,
}


def make_transport(seen: list[httpx.Request], status: int = 200, body: object = SEARCH_JSON):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def test_search_sends_request_and_parses_results():
    seen: list[httpx.Request] = []
    client = OpenScour(
        api_key="sk-test",
        base_url="http://search.internal:8000/",
        http_client=httpx.Client(transport=make_transport(seen)),
    )
    response = client.search("Latest AWS Bedrock updates", max_results=10, include_domains=["aws.amazon.com"])

    request = seen[0]
    assert str(request.url) == "http://search.internal:8000/search"
    assert request.headers["authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body["query"] == "Latest AWS Bedrock updates"
    assert body["max_results"] == 10
    assert body["include_domains"] == ["aws.amazon.com"]

    assert response.results[0].url == "https://aws.amazon.com/bedrock/"
    assert response.results[0].published_date == "2026-10-20"
    assert response.response_time == 1.23


def test_env_vars_configure_client(monkeypatch):
    monkeypatch.setenv("OPENSCOUR_API_KEY", "from-env")
    monkeypatch.setenv("OPENSCOUR_BASE_URL", "http://env-host:9000")
    client = OpenScour()
    assert client.api_key == "from-env"
    assert client.base_url == "http://env-host:9000"


def test_no_api_key_sends_no_auth_header(monkeypatch):
    monkeypatch.delenv("OPENSCOUR_API_KEY", raising=False)
    seen: list[httpx.Request] = []
    OpenScour(http_client=httpx.Client(transport=make_transport(seen))).search("x")
    assert "authorization" not in seen[0].headers


@pytest.mark.parametrize(
    "status,error",
    [(401, AuthenticationError), (502, NoResultsError)],
)
def test_error_statuses_raise_typed_errors(status, error):
    client = OpenScour(http_client=httpx.Client(transport=make_transport([], status, {"detail": "nope"})))
    with pytest.raises(error) as exc:
        client.search("x")
    assert exc.value.status_code == status
    assert "nope" in str(exc.value)


def test_unreachable_server_raises_connection_error(waits):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    client = OpenScour(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(APIConnectionError):
        client.search("x")


def test_extract_accepts_single_url():
    seen: list[httpx.Request] = []
    body = {"results": [{"url": "https://a.com", "raw_content": "text"}], "failed_results": [], "response_time": 0.5}
    client = OpenScour(http_client=httpx.Client(transport=make_transport(seen, body=body)))
    response = client.extract("https://a.com")
    assert json.loads(seen[0].content) == {"urls": ["https://a.com"]}
    assert response.results[0].raw_content == "text"


async def test_async_client():
    seen: list[httpx.Request] = []
    async with AsyncOpenScour(http_client=httpx.AsyncClient(transport=make_transport(seen))) as client:
        response = await client.search("Latest AWS Bedrock updates")
    assert response.results[0].title == "What's new in Bedrock"


def test_extract_with_query_sends_query_and_passage_count():
    seen: list[httpx.Request] = []
    body = {"results": [{"url": "https://a.com", "raw_content": "passage"}], "failed_results": [], "response_time": 0.1}
    client = OpenScour(http_client=httpx.Client(transport=make_transport(seen, body=body)))
    client.extract(["https://a.com"], query="bedrock models", chunks_per_source=2)
    assert json.loads(seen[0].content) == {
        "urls": ["https://a.com"],
        "query": "bedrock models",
        "chunks_per_source": 2,
    }



@pytest.fixture
def waits(monkeypatch):
    """Records retry waits instead of sleeping."""
    waited: list[float] = []

    async def async_sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(client_module, "_sleep", waited.append)
    monkeypatch.setattr(client_module, "_async_sleep", async_sleep)
    return waited


def sequence_transport(seen: list[httpx.Request], responses: list):
    """Answers each request with the next response, or raises it if it's an exception class."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = responses.pop(0)
        if isinstance(item, type):
            raise item("failed", request=request)
        return item

    return httpx.MockTransport(handler)


def test_retries_transient_errors_then_succeeds(waits):
    seen: list[httpx.Request] = []
    transport = sequence_transport(
        seen, [httpx.ConnectError, httpx.Response(503, json={"detail": "busy"}), httpx.Response(200, json=SEARCH_JSON)]
    )
    response = OpenScour(http_client=httpx.Client(transport=transport)).search("x")
    assert response.results[0].url == "https://aws.amazon.com/bedrock/"
    assert len(seen) == 3
    assert len(waits) == 2 and all(0 < w <= 1.0 for w in waits)


def test_rate_limit_honors_retry_after_then_raises(waits):
    seen: list[httpx.Request] = []
    limited = [httpx.Response(429, json={"detail": "Rate limit exceeded"}, headers={"Retry-After": "7"})] * 3
    client = OpenScour(http_client=httpx.Client(transport=sequence_transport(seen, list(limited))))
    with pytest.raises(RateLimitError) as exc:
        client.search("x")
    assert exc.value.retry_after == 7.0
    assert waits == [7.0, 7.0]
    assert len(seen) == 3


def test_long_retry_after_is_not_waited(waits):
    seen: list[httpx.Request] = []
    long_wait = httpx.Response(429, json={"detail": "Rate limit exceeded"}, headers={"Retry-After": "600"})
    with pytest.raises(RateLimitError):
        OpenScour(http_client=httpx.Client(transport=sequence_transport(seen, [long_wait]))).search("x")
    assert waits == [] and len(seen) == 1


@pytest.mark.parametrize(
    "response,error",
    [
        (httpx.Response(401, json={"detail": "bad key"}), AuthenticationError),
        (httpx.Response(502, json={"detail": "No results from search backends"}), NoResultsError),
    ],
)
def test_permanent_errors_are_not_retried(waits, response, error):
    seen: list[httpx.Request] = []
    with pytest.raises(error):
        OpenScour(http_client=httpx.Client(transport=sequence_transport(seen, [response]))).search("x")
    assert len(seen) == 1 and waits == []


def test_proxy_502_is_retried_and_not_mistaken_for_no_results(waits):
    seen: list[httpx.Request] = []
    gateway = httpx.Response(502, text="<html>Bad Gateway</html>", headers={"content-type": "text/html"})
    with pytest.raises(APIStatusError) as exc:
        OpenScour(http_client=httpx.Client(transport=sequence_transport(seen, [gateway] * 3))).search("x")
    assert not isinstance(exc.value, NoResultsError)
    assert len(seen) == 3


def test_max_retries_zero_disables_retries(waits):
    seen: list[httpx.Request] = []
    transport = sequence_transport(seen, [httpx.ConnectError])
    with pytest.raises(APIConnectionError):
        OpenScour(http_client=httpx.Client(transport=transport), max_retries=0).search("x")
    assert len(seen) == 1


async def test_async_client_retries(waits):
    seen: list[httpx.Request] = []
    transport = sequence_transport(seen, [httpx.Response(504, json={}), httpx.Response(200, json=SEARCH_JSON)])
    async with AsyncOpenScour(http_client=httpx.AsyncClient(transport=transport)) as client:
        response = await client.search("x")
    assert response.query == "Latest AWS Bedrock updates"
    assert len(seen) == 2 and len(waits) == 1
