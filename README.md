# OpenScour

**Self-hosted web search for LLMs and AI agents. Answer-first passages, no paid search API.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED.svg)](docker-compose.yml)

OpenScour searches the web, reads the result pages and returns the passages that actually answer your query, ready to drop into an LLM prompt. You run it on your own machine or cloud account, and it needs no API key from any search provider.

```python
from openscour import OpenScour

client = OpenScour()
response = client.search("Who is vice president in the USA?")

print(response.results[0].content)
# JD Vance is the 50th and current vice president since January 20, 2025.
```

## Features

- **Answer-first passages.** Results are ranked passages from real pages, not just links. For questions like *when*, *who* and *how many*, the passage that contains the answer comes first and starts at the answering sentence.
- **Topic-aware and fresh.** Searches such as *"Latest AWS Bedrock updates"* require the named topic in each passage and favor recent content.
- **No paid search provider.** Results come from a self-hosted [SearXNG](https://github.com/searxng/searxng) meta-search engine, with a DuckDuckGo fallback.
- **Clean text extraction.** Navigation, cookie banners, reference lists and footnote markers are removed. The `/extract` endpoint returns the clean text of any URL.
- **MCP server for AI agents.** Register OpenScour once in Claude Code, Cursor or any MCP client, and the agent can search the web and read pages on its own.
- **Typed Python client.** `OpenScour` and `AsyncOpenScour` return typed results and raise typed errors. The client only depends on `httpx`.
- **Plain HTTP API.** Any language can call `POST /search` and `POST /extract`.
- **Optional AI answers.** Claude can write a short cited answer from the top passages, through Amazon Bedrock or the Claude API.
- **Runs anywhere Docker runs**, from a laptop to AWS.

## Quickstart

Requirements: [Docker](https://docs.docker.com/get-docker/) and Python 3.11 or newer.

**1. Start the server**

```bash
git clone https://github.com/ivipinmishra/openscour.git
```

```bash
cd openscour
```

```bash
docker compose up -d --build
```

This starts SearXNG and the OpenScour API on http://localhost:8000. Check it with `curl http://localhost:8000/health`, or open the interactive API docs at http://localhost:8000/docs.

**2. Install the client in your project**

OpenScour is not on PyPI yet, so install it from GitHub. With [uv](https://docs.astral.sh/uv/):

```bash
uv add git+https://github.com/ivipinmishra/openscour
```

Or with pip, inside a virtual environment:

```bash
pip install git+https://github.com/ivipinmishra/openscour
```

**3. Search**

```python
from openscour import OpenScour

client = OpenScour()  # http://localhost:8000 by default

response = client.search("Latest AWS Bedrock updates", max_results=5)
for result in response.results:
    print(f"{result.score:.2f}  {result.title}")
    print(f"      {result.url}")
    print(f"      {result.content[:200]}")
```

## Use with AI agents (MCP)

OpenScour includes an [MCP](https://modelcontextprotocol.io) server, so AI agents can use it as a built-in tool. It gives the agent two tools:

| Tool | What it does |
|---|---|
| `search` | Searches the web and returns numbered results with title, URL and the best passage. Takes the same options as the Python client. |
| `extract` | Returns the clean main text of up to 20 URLs. |

Both tools are read-only. The MCP server talks to your OpenScour server, so start that first (see Quickstart).

**Claude Code**, with [uv](https://docs.astral.sh/uv/) installed:

```bash
claude mcp add openscour -- uvx --from "openscour[mcp] @ git+https://github.com/ivipinmishra/openscour" openscour-mcp
```

If your OpenScour server isn't on `http://localhost:8000`, or it requires a key, pass them as environment variables:

```bash
claude mcp add openscour -e OPENSCOUR_BASE_URL=https://search.example.com -e OPENSCOUR_API_KEY=YOUR_API_KEY -- uvx --from "openscour[mcp] @ git+https://github.com/ivipinmishra/openscour" openscour-mcp
```

Then ask Claude something like *"What changed in Amazon Bedrock this month?"* and it will search with OpenScour.

**Other MCP clients** (Cursor, Claude Desktop, VS Code and others) take the same command in their JSON configuration:

```json
{
  "mcpServers": {
    "openscour": {
      "command": "uvx",
      "args": ["--from", "openscour[mcp] @ git+https://github.com/ivipinmishra/openscour", "openscour-mcp"],
      "env": { "OPENSCOUR_BASE_URL": "http://localhost:8000" }
    }
  }
}
```

If the OpenScour server is unreachable, the tools return an error that tells the agent so, instead of failing silently.

## How it works

```
                ┌────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
query ────────► │  SearXNG   │──►│  Fetch pages │──►│ Extract text │──►│  Rank passages   │──► results
                │ meta-search│   │  in parallel │   │ (trafilatura)│   │ BM25 + signals   │
                └─────┬──────┘   └──────────────┘   └──────────────┘   └────────┬─────────┘
                      │ no results                                              │ optional
                      ▼                                                         ▼
                 DuckDuckGo                                              Claude answer
```

1. **Search.** SearXNG queries several public search engines and merges their results. If it returns nothing, OpenScour falls back to DuckDuckGo.
2. **Fetch.** The top pages are downloaded in parallel. A `basic` search answers as soon as enough pages are ready, or after 2 seconds at most; slow pages are represented by their search snippet and keep loading in the background, so a repeat search has them.
3. **Extract.** [trafilatura](https://github.com/adbar/trafilatura) pulls out the main text, in parallel worker processes. OpenScour then removes reference sections, footnote markers and other page clutter. Pages that yield very little text, such as video pages, also get their search snippet as a passage.
4. **Rank.** Each page is split into passages and scored with BM25, adjusted by:
   - **answer shape:** dates for *when*, numbers for *how many* or *how tall*, names for *who*, version numbers for *latest release* questions, with a boost for *current* holders on *who is* questions
   - **topic coverage:** passages must mention the query's named terms, and lists of headings (tables of contents, menus) rank lower than passages that explain something
   - **freshness:** *latest*, *new* and *recent* queries prefer this year's content
   - **page quality:** passages near the top of a page rank higher, and boilerplate ranks lower
5. **Answer (optional).** Claude reads the top passages and writes a short answer with citations.

Searches and pages are cached in memory for 15 minutes.

## Python client

```python
from openscour import OpenScour

client = OpenScour(
    api_key="YOUR_API_KEY",              # or OPENSCOUR_API_KEY
    base_url="https://search.example.com",  # or OPENSCOUR_BASE_URL; default http://localhost:8000
)

response = client.search(
    "AWS re:Invent announcements",
    search_depth="advanced",        # "basic" (default) or "advanced"
    topic="news",                   # "general" (default) or "news"
    max_results=10,
    time_range="week",              # "day", "week", "month" or "year"
    include_domains=["aws.amazon.com"],
    include_raw_content=True,       # full page text in result.raw_content
)

pages = client.extract(["https://en.wikipedia.org/wiki/Web_search_engine"])
print(pages.results[0].raw_content)
print(pages.failed_results)         # URLs that could not be fetched, with the reason
```

For async code, `AsyncOpenScour` has the same methods:

```python
from openscour import AsyncOpenScour

async with AsyncOpenScour() as client:
    response = await client.search("When is the midterm election in the USA?")
```

Errors are typed and all inherit from `OpenScourError`:

| Error | When |
|---|---|
| `AuthenticationError` | The API key is missing or wrong |
| `InvalidRequestError` | The request was rejected, for example an empty query |
| `NoResultsError` | No search backend returned results |
| `APIConnectionError` | The server could not be reached |

## HTTP API

Send `Authorization: Bearer <key>` when the server has `API_KEYS` set.

### `POST /search`

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Who is vice president in the USA?", "max_results": 3}'
```

| Field | Default | Description |
|---|---|---|
| `query` | required | The search query, up to 400 characters |
| `search_depth` | `basic` | `basic` fetches `max_results + 3` pages and returns 1 passage per result. `advanced` fetches `2 × max_results` pages and returns 3 passages per result. |
| `topic` | `general` | `general` or `news` |
| `max_results` | `5` | 1 to 20 |
| `time_range` | `null` | `day`, `week`, `month` or `year` |
| `include_domains` | `[]` | Only return results from these domains |
| `exclude_domains` | `[]` | Never return results from these domains |
| `include_raw_content` | `false` | Include the full extracted page text |
| `include_answer` | `false` | Generate an AI answer (needs `ANSWER_PROVIDER`, see Configuration) |

Response:

```json
{
  "query": "Who is vice president in the USA?",
  "answer": null,
  "results": [
    {
      "title": "Vice President of the United States - Wikipedia",
      "url": "https://en.wikipedia.org/wiki/Vice_President_of_the_United_States",
      "content": "JD Vance is the 50th and current vice president since January 20, 2025.",
      "score": 1.0,
      "raw_content": null,
      "published_date": null
    }
  ],
  "response_time": 2.94
}
```

`score` is relative: the best result in each response scores 1.0.

### `POST /extract`

```bash
curl -X POST http://localhost:8000/extract \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://en.wikipedia.org/wiki/Web_search_engine"]}'
```

Takes up to 20 URLs. Returns the clean text of each page in `results` and per-URL errors in `failed_results`.

### `GET /health`

Returns `{"status": "ok"}`.

## Configuration

Set environment variables, or copy [`.env.example`](.env.example) to `.env`.

| Variable | Default | Description |
|---|---|---|
| `API_KEYS` | empty (auth off) | Comma-separated keys that clients must send |
| `SEARXNG_URL` | `http://localhost:8888` | Where the SearXNG instance runs |
| `DUCKDUCKGO_FALLBACK` | `true` | Use DuckDuckGo when SearXNG returns nothing |
| `ANSWER_PROVIDER` | `none` | `bedrock` (uses your AWS credentials) or `anthropic` (uses `ANTHROPIC_API_KEY`) |
| `ANSWER_MODEL` | `claude-opus-5-5` | Model used for AI answers |
| `AWS_REGION` | `us-east-1` | Region for Amazon Bedrock |
| `FETCH_TIMEOUT` | `8` | Page fetch timeout in seconds for `advanced` searches |
| `BASIC_FETCH_DEADLINE` | `2.0` | Longest a `basic` search waits for pages, in seconds |
| `EXTRACT_WORKERS` | `4` | Worker processes for text extraction |
| `CACHE_TTL_SECONDS` | `900` | How long searches and pages stay cached |

## Running without Docker

You can run the API directly and keep only SearXNG in Docker:

```bash
docker compose up -d searxng
```

```bash
uv sync
```

```bash
uv run openscour-server --port 8000
```

To install the server into another environment, use the `server` extra: `pip install "openscour[server] @ git+https://github.com/ivipinmishra/openscour"`.

## Deploying

Before you expose OpenScour beyond your own machine:

- Change `server.secret_key` in [`searxng/settings.yml`](searxng/settings.yml). The checked-in value is a placeholder for local use.
- Set `API_KEYS` so only your applications can call the API.
- Keep SearXNG on a private network. Only the API needs to be reachable.
- Run more than one API instance only with a shared cache; the built-in cache lives in each process.

## Known limitations

- **Upstream engines rate-limit.** SearXNG gets results by querying public search engines, and some (Google, DuckDuckGo) answer heavy or automated traffic with CAPTCHAs or blocks. Cloud IP ranges such as AWS are blocked more often. SearXNG uses whichever engines respond; check the `unresponsive engines` lines in the logs and tune engines in `searxng/settings.yml`.
- **Ranking is rule-based.** It handles common question types well but can miss when the answer is phrased unusually. Turning on AI answers helps with those cases.
- **JavaScript-heavy pages and PDFs** are not extracted; the search engine's snippet is used instead.

## Responsible use

SearXNG and the DuckDuckGo fallback query public search engines. Automated queries may conflict with those engines' terms of service, and heavy use gets rate-limited or blocked. You are responsible for how you deploy and use OpenScour. Keep traffic reasonable, respect site terms when fetching pages, and consider a licensed search API for commercial-scale use.

OpenScour is not affiliated with SearXNG, DuckDuckGo or any search engine.

## Development

```bash
uv sync
```

```bash
uv run pytest
```

Check answer quality against a running server (sends 10 real searches):

```bash
uv run python scripts/eval_answers.py
```

Try a single query from the command line:

```bash
uv run python scripts/try_search.py "how does BM25 ranking work"
```

Project layout:

```
openscour/
  client.py          Python client: OpenScour, AsyncOpenScour
  types.py           Typed results
  errors.py          Typed errors
  mcp_server.py      MCP server: search and extract tools for AI agents
  server/
    main.py          FastAPI app and the openscour-server command
    providers.py     SearXNG search with DuckDuckGo fallback
    fetcher.py       Page fetching and text extraction
    ranker.py        Passage chunking and ranking
    answer.py        Optional Claude answers
searxng/settings.yml SearXNG configuration
tests/               Unit tests for the client, MCP server and ranker
scripts/             Manual search and answer-quality check
```

## Contributing

Issues and pull requests are welcome. For ranking changes, include a query that improves and run both `uv run pytest` and `scripts/eval_answers.py`, so improvements for one kind of question don't break another.

## License

[MIT](LICENSE). SearXNG runs as a separate, unmodified container and is licensed under AGPL-3.0.
