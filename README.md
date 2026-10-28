# OpenScour

A self-hosted web search API for your apps and AI agents, in the style of Tavily. It needs no paid search provider.

It returns ranked passages from real pages, not just links. For factual questions ("When is…", "Who is…", "How tall…") the ranker favors passages that actually contain the answer and starts each passage at the answering sentence.

```
query ─► SearXNG (meta-search) ─► fetch pages ─► extract clean text ─► BM25 rank chunks ─► results
                 └─ DuckDuckGo fallback                                   └─ optional Claude answer
```

## Python client

```python
from openscour import OpenScour

client = OpenScour(api_key="YOUR_API_KEY", base_url="http://localhost:8000")

response = client.search(
    query="Latest AWS Bedrock updates",
    max_results=10,
)
for result in response.results:
    print(result.score, result.title, result.url)
    print(result.content)

pages = client.extract(["https://en.wikipedia.org/wiki/Web_search_engine"])
print(pages.results[0].raw_content)
```

`api_key` and `base_url` fall back to the `OPENSCOUR_API_KEY` and `OPENSCOUR_BASE_URL` environment variables (default URL `http://localhost:8000`). `search()` takes every `/search` option below as a keyword argument.

For async code use `AsyncOpenScour`, which has the same methods:

```python
from openscour import AsyncOpenScour

async with AsyncOpenScour() as client:
    response = await client.search("Who is vice president in the USA?")
```

Errors are typed: `AuthenticationError` (bad key), `NoResultsError` (no search backend answered), `APIConnectionError` (server unreachable), all subclasses of `OpenScourError`.

## Install

| You want | Install |
|---|---|
| The client, to call a running OpenScour server | `pip install openscour` (only needs `httpx`) |
| To run the server | `pip install "openscour[server]"`, or use Docker |

## Run the server locally

Requirements: Docker and [uv](https://docs.astral.sh/uv/).

```bash
docker compose up -d searxng
```

```bash
uv sync
```

```bash
uv run openscour-server --port 8000
```

Try it:

```bash
uv run python scripts/try_search.py "how does BM25 ranking work"
```

```bash
uv run python scripts/try_search.py "AWS re:Invent announcements" advanced news
```

Interactive API docs: http://localhost:8000/docs

To run the API in Docker as well: `docker compose up -d --build`.

## HTTP API

Any language can call the server directly. Send `Authorization: Bearer <key>` when `API_KEYS` is set.

### `POST /search`

```json
{
  "query": "what is retrieval augmented generation",
  "search_depth": "basic",
  "topic": "general",
  "max_results": 5,
  "include_answer": false,
  "include_raw_content": false,
  "include_domains": [],
  "exclude_domains": [],
  "time_range": null
}
```

| Field | Notes |
|---|---|
| `search_depth` | `basic`: fetches `max_results + 3` pages, 1 best passage each, 5s timeout. `advanced`: fetches `2 × max_results` pages, 3 passages each, longer timeout. |
| `topic` | `general` or `news` |
| `time_range` | `day`, `week`, `month`, `year` |
| `include_answer` | Needs `ANSWER_PROVIDER` set (see below), otherwise `answer` is `null` |

Response: `{ query, answer, results: [{ title, url, content, score, raw_content, published_date }], response_time }`

### `POST /extract`

```json
{ "urls": ["https://en.wikipedia.org/wiki/Web_search_engine"] }
```

Returns clean page text in `results` and per-URL errors in `failed_results`.

## Configuration (env vars or `.env`)

Copy `.env.example` to `.env` to start.

| Variable | Default | |
|---|---|---|
| `API_KEYS` | empty (auth off) | Comma-separated; clients send `Authorization: Bearer <key>` |
| `SEARXNG_URL` | `http://localhost:8888` | |
| `DUCKDUCKGO_FALLBACK` | `true` | Used only when SearXNG returns nothing |
| `ANSWER_PROVIDER` | `none` | `bedrock` (uses your AWS credentials) or `anthropic` (`ANTHROPIC_API_KEY`) |
| `ANSWER_MODEL` | `claude-opus-5-5` | |
| `AWS_REGION` | `us-east-1` | For Bedrock |
| `FETCH_TIMEOUT` | `8` | Seconds, advanced depth |
| `CACHE_TTL_SECONDS` | `900` | In-memory cache for searches and pages |

## Known limitations

- **SearXNG scrapes upstream engines.** Some engines (Google, DuckDuckGo) return CAPTCHAs or "access denied", more often from cloud IPs such as AWS. SearXNG uses whichever engines respond. Check the `unresponsive engines` log lines and tune engines in `searxng/settings.yml`.
- The cache is per process. Move it to Redis (ElastiCache) when you run more than one API task.
- JavaScript-heavy pages and PDFs are not extracted; the search engine's snippet is used for them instead.

## Before exposing it

- Change `server.secret_key` in `searxng/settings.yml`. The checked-in value is a placeholder for local use.
- Set `API_KEYS` so only your apps can call the API.
- Keep SearXNG on a private network; only the API needs to be reachable.

## Responsible use

SearXNG and the DuckDuckGo fallback get their results by querying public search engines. Automated queries may conflict with those engines' terms of service, and heavy use gets rate-limited or blocked. You are responsible for how you deploy and use this tool. Keep traffic reasonable, respect `robots.txt` and site terms when fetching pages, and consider a licensed search API for commercial-scale use.

This project is not affiliated with Tavily, SearXNG, DuckDuckGo or any search engine.

## Tests

```bash
uv run pytest
```

Answer-quality check against the running API (sends 10 real searches):

```bash
uv run python scripts/eval_answers.py
```

## License

[MIT](LICENSE). SearXNG, which runs as a separate container, is licensed under AGPL-3.0.
