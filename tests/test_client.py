import json

import httpx
import pytest

from openscour import (
    APIConnectionError,
    AsyncOpenScour,
    AuthenticationError,
    NoResultsError,
    OpenScour,
)

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


def test_unreachable_server_raises_connection_error():
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
