# OpenScour

**Self-hosted web search for LLMs and AI agents. Answer-first passages, no paid search API.**

[![Tests](https://github.com/ivipinmishra/openscour/actions/workflows/tests.yml/badge.svg)](https://github.com/ivipinmishra/openscour/actions/workflows/tests.yml)
[![Release](https://img.shields.io/github/v/release/ivipinmishra/openscour?sort=semver)](https://github.com/ivipinmishra/openscour/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED.svg)](docker-compose.yml)
[![MCP](https://img.shields.io/badge/MCP-server-5c4ee5.svg)](#option-3-ai-agents-mcp)

OpenScour searches the web, reads the result pages and returns the passages that actually answer your query, ready to drop into an LLM prompt. You run it on your own machine or cloud account, and it needs no API key from any search provider.

```python
from openscour import OpenScour

client = OpenScour()
response = client.search("Who is vice president in the USA?")

print(response.results[0].content)
# JD Vance is the 50th and current vice president since January 20, 2025.
```

## Contents

- [Features](#features)
- [Step 1: Start the server](#step-1-start-the-server)
- [Step 2: Choose how to use it](#step-2-choose-how-to-use-it)
  - [Option 1: HTTP API (any language)](#option-1-http-api-any-language)
  - [Option 2: Python client](#option-2-python-client)
  - [Option 3: AI agents (MCP)](#option-3-ai-agents-mcp)
- [Example: answer questions with Claude](#example-answer-questions-with-claude)
- [Versions and updates](#versions-and-updates)
- [API reference](#api-reference)
- [Python client reference](#python-client-reference)
- [How it works](#how-it-works)
- [Configuration](#configuration)
- [Security](#security)
- [Deploying](#deploying)
- [Known limitations](#known-limitations)
- [Development](#development)

## Features

- **Answer-first passages.** Results are ranked passages from real pages, not just links. For questions like *when*, *who* and *how many*, the passage that contains the answer comes first and starts at the answering sentence.
- **Topic-aware and fresh.** Searches such as *"Latest AWS Bedrock updates"* require the named topic in each passage and favor recent content.
- **No paid search provider.** Results come from a self-hosted [SearXNG](https://github.com/searxng/searxng) meta-search engine, with a DuckDuckGo fallback.
- **Clean text extraction.** Navigation, cookie banners, reference lists and footnote markers are removed. Given a query, extraction returns only the passages that matter, often 95% shorter than the page.
- **Three ways in.** A plain HTTP API for any language, a typed Python client, and an MCP server for AI agents such as Claude Code, Kiro and Cursor.
- **Optional AI answers.** Claude can write a short cited answer from the top passages, through Amazon Bedrock or the Claude API.
- **Many languages.** Search and ranking work with accented, Cyrillic, Chinese, Japanese and Korean text, not only English.
- **Secure by default.** Protection against server-side request forgery, robots.txt respected, an unprivileged read-only container, and ports bound to this machine until you choose otherwise. See [Security](#security).
- **Runs anywhere Docker runs**, from a laptop to AWS.

## Step 1: Start the server

Every way of using OpenScour talks to an OpenScour server, so start one first. You need [Docker](https://docs.docker.com/get-docker/).

```bash
git clone https://github.com/ivipinmishra/openscour.git
```

```bash
cd openscour
```

```bash
docker compose up -d --build
```

This starts SearXNG and the OpenScour API on **http://localhost:8000**. Check that it's running:

```bash
curl http://localhost:8000/health
```

It should print `{"status":"ok"}`. Interactive API docs are at http://localhost:8000/docs.

Both ports are bound to this machine only (`127.0.0.1`). To stop it, run `docker compose down` in the same folder. To run it on a server for others, see [Deploying](#deploying).

## Step 2: Choose how to use it

| You want to… | Use | Needs |
|---|---|---|
| Call OpenScour from any language (JavaScript, Go, Java, …) or a tool like Postman | [Option 1: HTTP API](#option-1-http-api-any-language) | Nothing to install |
| Use OpenScour from Python code | [Option 2: Python client](#option-2-python-client) | `pip` or `uv` |
| Let an AI agent search the web on its own | [Option 3: AI agents (MCP)](#option-3-ai-agents-mcp) | [uv](https://docs.astral.sh/uv/) and an MCP client such as Claude Code, Kiro or Cursor |

All three use the same two operations:

- **search:** search the web and get the best passage from each result page.
- **extract:** read specific URLs and get their clean text, or only the parts that match a query.

### Option 1: HTTP API (any language)

Send JSON to the server with any HTTP client.

**Search:**

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Latest AWS Bedrock updates", "max_results": 5}'
```

**Read pages, keeping only what matches a query:**

```bash
curl -X POST http://localhost:8000/extract \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://en.wikipedia.org/wiki/Vice_President_of_the_United_States"], "query": "Who is the current vice president?"}'
```

**JavaScript or TypeScript** (Node.js 18+, Deno, Bun or the browser):

```javascript
const response = await fetch("http://localhost:8000/search", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ query: "Latest AWS Bedrock updates", max_results: 5 }),
});
const data = await response.json();

for (const result of data.results) {
  console.log(result.score, result.title, result.url);
  console.log(result.content);
}
```

If the server has `API_KEYS` set, add the header `Authorization: Bearer YOUR_API_KEY` to every request.

Every field and the response format are in the [API reference](#api-reference).

### Option 2: Python client

**Install** it in your project. OpenScour is not on PyPI yet, so install it from GitHub. The commands pin release `v0.2.0`; see [Versions and updates](#versions-and-updates) for newer releases.

With [uv](https://docs.astral.sh/uv/):

```bash
uv add git+https://github.com/ivipinmishra/openscour@v0.2.0
```

Or with pip, inside a virtual environment:

```bash
pip install git+https://github.com/ivipinmishra/openscour@v0.2.0
```

The client only needs `httpx`; none of the server's dependencies are installed.

**Use it:**

```python
from openscour import OpenScour

client = OpenScour()  # http://localhost:8000 by default

# Search
response = client.search("Latest AWS Bedrock updates", max_results=5)
for result in response.results:
    print(f"{result.score:.2f}  {result.title}")
    print(f"      {result.url}")
    print(f"      {result.content[:200]}")

# Read pages, keeping only what matches a query
pages = client.extract(
    ["https://en.wikipedia.org/wiki/Vice_President_of_the_United_States"],
    query="Who is the current vice president?",
)
print(pages.results[0].raw_content)
# JD Vance is the 50th and current vice president since January 20, 2025. …
```

For a server elsewhere, or one that requires a key, pass `base_url` and `api_key`, or set `OPENSCOUR_BASE_URL` and `OPENSCOUR_API_KEY`. Every option, the async client and the error types are in the [Python client reference](#python-client-reference).

### Option 3: AI agents (MCP)

OpenScour includes an [MCP](https://modelcontextprotocol.io) server that gives AI agents two tools:

| Tool | What it does |
|---|---|
| `search` | Searches the web and returns numbered results with title, URL and the best passage. Takes the same options as the API. |
| `extract` | Reads up to 20 URLs. With a `query`, returns only each page's most relevant passages, so long pages don't flood the agent's context. Without one, it returns the full text, capped at 20,000 characters per page. |

Both tools are read-only. The MCP server runs on your machine, started by your agent, and passes requests to your OpenScour server from [Step 1](#step-1-start-the-server). You need [uv](https://docs.astral.sh/uv/) installed; it downloads and runs the MCP server from GitHub automatically.

If your OpenScour server isn't at `http://localhost:8000`, or it requires a key, set `OPENSCOUR_BASE_URL` and `OPENSCOUR_API_KEY` in the configurations below.

#### Claude Code

```bash
claude mcp add openscour -- uvx --from "openscour[mcp] @ git+https://github.com/ivipinmishra/openscour@v0.2.0" openscour-mcp
```

With a different server address or a key:

```bash
claude mcp add openscour -e OPENSCOUR_BASE_URL=https://search.example.com -e OPENSCOUR_API_KEY=YOUR_API_KEY -- uvx --from "openscour[mcp] @ git+https://github.com/ivipinmishra/openscour@v0.2.0" openscour-mcp
```

Restart Claude Code, then ask something like *"What changed in Amazon Bedrock this month?"*.

#### Kiro

1. Open Kiro's MCP configuration: `~/.kiro/settings/mcp.json` for all projects, or `.kiro/settings/mcp.json` for one project. You can also use **Open MCP Config** in the Kiro panel.
2. Add OpenScour under `mcpServers`:

   ```json
   {
     "mcpServers": {
       "openscour": {
         "command": "uvx",
         "args": ["--from", "openscour[mcp] @ git+https://github.com/ivipinmishra/openscour@v0.2.0", "openscour-mcp"],
         "env": { "OPENSCOUR_BASE_URL": "http://localhost:8000" },
         "disabled": false,
         "autoApprove": ["search", "extract"]
       }
     }
   }
   ```

   `autoApprove` lets Kiro use the read-only tools without asking each time. Remove it if you'd rather approve each call.
3. Save. **MCP Servers** in the Kiro panel should show `openscour` as connected with 2 tools.
4. Kiro also has its own built-in web search. To make it use OpenScour instead, add a steering file such as `~/.kiro/steering/web-search.md`:

   ```markdown
   # Web search

   For any web search or reading a web page, use the `openscour` MCP server:
   `search` to search the web, and `extract` with a `query` to read pages.
   Do not use the built-in web search unless openscour returns an error.
   Cite the URLs of the results you use.
   ```

#### Cursor, Claude Desktop, VS Code and other MCP clients

Add the same command to the client's MCP configuration:

```json
{
  "mcpServers": {
    "openscour": {
      "command": "uvx",
      "args": ["--from", "openscour[mcp] @ git+https://github.com/ivipinmishra/openscour@v0.2.0", "openscour-mcp"],
      "env": { "OPENSCOUR_BASE_URL": "http://localhost:8000" }
    }
  }
}
```

The file's location depends on the client; see its documentation for "MCP servers".

#### Updating and troubleshooting

- **Update to a new release.** Change `v0.2.0` in the configuration to the new version and restart the server in your agent, or restart the agent. See [Versions and updates](#versions-and-updates).
- **"command not found: uvx".** Desktop apps launched from the Dock may not see your terminal's `PATH`. Use the full path from `which uvx` as `command`, for example `/opt/homebrew/bin/uvx` on Apple Silicon Macs with Homebrew.
- **"Could not reach OpenScour".** The OpenScour server isn't running or `OPENSCOUR_BASE_URL` is wrong. Start it as in [Step 1](#step-1-start-the-server).

## Example: answer questions with Claude

The most common use of OpenScour is giving an LLM current information. This example searches with OpenScour, then has Claude answer from the passages it found, citing them. The full script is in [`examples/answer_with_claude.py`](examples/answer_with_claude.py).

```bash
uv add git+https://github.com/ivipinmishra/openscour@v0.2.0 anthropic
```

```python
import anthropic
from openscour import OpenScour

question = "What changed in Amazon Bedrock this month?"

# 1. Search: the most relevant passage from each page.
search = OpenScour().search(question, max_results=5, search_depth="advanced")
sources = "\n\n".join(
    f"[{i}] {r.title}\nURL: {r.url}\n{r.content}" for i, r in enumerate(search.results, start=1)
)

# 2. Answer: Claude reads only those passages and cites them.
claude = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
response = claude.beta.messages.create(
    model="claude-opus-5-5",
    max_tokens=16000,
    output_config={"effort": "low"},  # enough for summarizing a few passages
    betas=["server-side-fallback-2026-07-01"],
    fallbacks="default",  # retry on a fallback model if a safety check declines
    system="Answer using only the numbered sources. Cite them like [1]. "
    "If the sources don't contain the answer, say so.",
    messages=[{"role": "user", "content": f"Sources:\n\n{sources}\n\nQuestion: {question}"}],
)
print("".join(block.text for block in response.content if block.type == "text"))
```

Because the passages are short and answer-first, the prompt stays small (about 7,000 characters for five sources) and Claude can cite exactly where each fact came from. To use Claude through Amazon Bedrock instead, create the client with `anthropic.AnthropicBedrockMantle(aws_region="us-east-1")`, use the model `anthropic.claude-opus-5-5`, and remove the `betas` and `fallbacks` lines, which are a Claude API feature.

## Versions and updates

OpenScour is released as tagged versions on [GitHub Releases](https://github.com/ivipinmishra/openscour/releases); what changed in each is in the [changelog](CHANGELOG.md). The install commands in this README pin a version (`@v0.2.0`), so your setup only changes when you choose.

To update:

- **Python client:** change the version in your install command, e.g. `uv add git+https://github.com/ivipinmishra/openscour@v0.2.0`.
- **MCP server:** change the version in your agent's configuration and restart the server in the agent.
- **Server:** in your clone, run `git fetch --tags`, `git checkout v0.2.0` and `docker compose up -d --build`.

To always follow the newest code instead, leave out `@v0.2.0`. The MCP server is then cached after its first download; get newer code by stopping it in your agent, running `uv cache clean openscour`, and starting it again.

## API reference

Send `Authorization: Bearer <key>` when the server has `API_KEYS` set.

### `POST /search`

| Field | Default | Description |
|---|---|---|
| `query` | required | The search query, up to 400 characters |
| `search_depth` | `basic` | `basic` answers within about 2 seconds and returns 1 passage per result. `advanced` reads `2 × max_results` pages, waits for all of them and returns 3 passages per result. |
| `topic` | `general` | `general` or `news` |
| `max_results` | `5` | 1 to 20 |
| `time_range` | `null` | `day`, `week`, `month` or `year` |
| `include_domains` | `[]` | Only return results from these domains |
| `exclude_domains` | `[]` | Never return results from these domains |
| `include_raw_content` | `false` | Also include the full extracted page text |
| `include_answer` | `false` | Generate an AI answer (needs `ANSWER_PROVIDER`, see [Configuration](#configuration)) |

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

| Field | Default | Description |
|---|---|---|
| `urls` | required | Up to 20 URLs |
| `query` | `null` | If set, each result holds only the page's most relevant passages, in page order, instead of the full text |
| `chunks_per_source` | `3` | With `query`: passages per page, 1 to 10 |

Response:

```json
{
  "results": [
    { "url": "https://en.wikipedia.org/wiki/Vice_President_of_the_United_States", "raw_content": "JD Vance is the 50th and current vice president since January 20, 2025. …" }
  ],
  "failed_results": [
    { "url": "https://example.invalid/page", "error": "ConnectError" }
  ],
  "response_time": 1.12
}
```

### `GET /health`

Returns `{"status": "ok"}`.

### Errors

| Status | Meaning |
|---|---|
| `401` | The API key is missing or wrong |
| `422` | The request is invalid, for example an empty query; the body says which field |
| `502` | No search backend returned results |

## Python client reference

```python
from openscour import OpenScour

client = OpenScour(
    api_key="YOUR_API_KEY",                 # or OPENSCOUR_API_KEY
    base_url="https://search.example.com",  # or OPENSCOUR_BASE_URL; default http://localhost:8000
    timeout=60,                             # seconds
)

response = client.search(
    "AWS re:Invent announcements",
    search_depth="advanced",        # "basic" (default) or "advanced"
    topic="news",                   # "general" (default) or "news"
    max_results=10,                 # 1 to 20
    time_range="week",              # "day", "week", "month" or "year"
    include_domains=["aws.amazon.com"],
    exclude_domains=[],
    include_raw_content=True,       # full page text in result.raw_content
    include_answer=False,           # AI answer in response.answer
)
for result in response.results:
    print(result.title, result.url, result.score, result.published_date)
    print(result.content)

pages = client.extract(
    ["https://en.wikipedia.org/wiki/Web_search_engine"],
    query="how search engines rank pages",  # optional
    chunks_per_source=3,                    # passages per page with a query, 1 to 10
)
for page in pages.results:
    print(page.url, page.raw_content)
for failed in pages.failed_results:
    print(failed.url, failed.error)
```

`extract` also accepts a single URL as a string. Use the client as a context manager (`with OpenScour() as client:`) to close its connections automatically.

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

## Configuration

Set environment variables on the server, or copy [`.env.example`](.env.example) to `.env`.

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
| `EXTRACT_MAX_TASKS_PER_CHILD` | `500` | Pages a worker extracts before it is replaced, to keep memory flat |
| `FETCH_CONCURRENCY` | `10` | Page downloads at once, across all requests |
| `MAX_INFLIGHT_FETCHES` | `200` | Fetches queued or running at once; beyond this, searches use snippets |
| `CACHE_TTL_SECONDS` | `900` | How long searches and pages stay cached |
| `USER_AGENT` | `OpenScour/0.2 (+repository URL)` | How OpenScour identifies itself to websites |
| `RESPECT_ROBOTS_TXT` | `true` | Skip pages a site's robots.txt disallows |
| `ALLOW_PRIVATE_NETWORK_FETCHES` | `false` | Allow fetching pages from private addresses. See [Security](#security) before enabling |

Clients (the Python client and the MCP server) read `OPENSCOUR_BASE_URL` and `OPENSCOUR_API_KEY`.

### Running the server without Docker

Keep only SearXNG in Docker and run the API with uv:

```bash
docker compose up -d searxng
```

```bash
uv sync
```

```bash
uv run openscour-server --port 8000
```

To install the server into another environment, use the `server` extra: `pip install "openscour[server] @ git+https://github.com/ivipinmishra/openscour@v0.2.0"`.

## Security

- **Server-side request forgery (SSRF) protection.** Pages are only fetched from public internet addresses. The check runs on the IP address actually being connected to, on every redirect, so URLs such as `http://169.254.169.254/` (cloud instance credentials), `http://localhost`, private network ranges, and public hostnames that resolve to private addresses are all refused with a `blocked:` error. Proxy environment variables are ignored for page fetches so they can't bypass the check.
  - `ALLOW_PRIVATE_NETWORK_FETCHES=true` turns this off, for example to search an intranet. Only do that on a deployment that untrusted callers, including AI agents reading untrusted pages, cannot reach.
- **Honest identity and robots.txt.** OpenScour identifies itself as `OpenScour/0.2` with a link to this repository, and skips pages a site's robots.txt disallows (the `OpenScour` user-agent token, or `*`).
- **Container hardening.** The API image runs as an unprivileged user with a read-only filesystem, no Linux capabilities and `no-new-privileges`, and has a health check. The SearXNG image is pinned to a specific release.
- **Local by default.** Docker Compose publishes both ports on `127.0.0.1` only, so nothing is reachable from other machines until you choose to expose it.

## Deploying

Before you expose OpenScour beyond your own machine:

- Change `server.secret_key` in [`searxng/settings.yml`](searxng/settings.yml). The checked-in value is a placeholder for local use.
- Set `API_KEYS` so only your applications can call the API, and give each client its key through `OPENSCOUR_API_KEY`.
- Publish only the API: in `docker-compose.yml`, change `"127.0.0.1:8000:8000"` to `"8000:8000"`, ideally behind HTTPS. Keep SearXNG's port local or remove it.
- Run more than one API instance only with a shared cache; the built-in cache lives in each process.

## Known limitations

- **Upstream engines rate-limit.** SearXNG gets results by querying public search engines, and some (Google, DuckDuckGo) answer heavy or automated traffic with CAPTCHAs or blocks. Cloud IP ranges such as AWS are blocked more often. SearXNG uses whichever engines respond; check the `unresponsive engines` lines in the logs and tune engines in `searxng/settings.yml`.
- **Ranking is rule-based.** It handles common question types well but can miss when the answer is phrased unusually, for example lists of past officeholders for *who is* questions. Turning on AI answers helps with those cases.
- **JavaScript-heavy pages and PDFs** are not extracted; the search engine's snippet is used instead.
- **No outbound proxy for page fetches.** To keep the SSRF check effective, page fetches connect directly and ignore `HTTP_PROXY`/`HTTPS_PROXY`. Networks that only allow internet access through a proxy aren't supported yet.

## Responsible use

SearXNG and the DuckDuckGo fallback query public search engines. Automated queries may conflict with those engines' terms of service, and heavy use gets rate-limited or blocked. When OpenScour reads pages itself, it identifies itself honestly and follows each site's robots.txt. You are responsible for how you deploy and use OpenScour: keep traffic reasonable, and consider a licensed search API for commercial-scale use.

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

### Contributing

Issues and pull requests are welcome. For ranking changes, include a query that improves and run both `uv run pytest` and `scripts/eval_answers.py`, so improvements for one kind of question don't break another.

## License

[MIT](LICENSE). SearXNG runs as a separate, unmodified container and is licensed under AGPL-3.0.
