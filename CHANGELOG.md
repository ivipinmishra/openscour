# Changelog

All notable changes to OpenScour are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Search engines
- SearXNG uses Brave, Bing and Yahoo for web results, and Brave News, Bing News and Wikinews for news. In measurements, Google, DuckDuckGo, Qwant and Google CSE blocked almost every automated query, so they are off, along with translation and currency engines that don't return web pages.
- Searches and pages are cached for an hour instead of 15 minutes (`CACHE_TTL_SECONDS`), so repeated searches send fewer queries to the engines.

### Monitoring
- Each search's log line shows which engines answered and which failed, and why: `engines=bing,brave engines_failed=google:CAPTCHA`, plus `fallback=duckduckgo` when the fallback was used.
- `/health/ready` reports each engine's status from the last 15 minutes of searches, and is "degraded" when every engine is blocking.

## [0.3.1] - 2026-10-30

### Packaging
- Published on [PyPI](https://pypi.org/project/openscour/): `pip install openscour`, and `uvx --from "openscour[mcp]==0.3.1" openscour-mcp` for the MCP server. New releases are published automatically from GitHub releases through PyPI trusted publishing.
- Package metadata (project links, classifiers, keywords) and a `py.typed` marker, so type checkers use the client's type hints.
- The package contains only an explicit list of files, so local files such as `.env` can never be published.

## [0.3.0] - 2026-10-29

Operations and client resilience (phase 2 of the enterprise roadmap).

### Server
- Rate limits: `RATE_LIMIT_PER_MINUTE` (default 60) requests a minute per API key, or per client IP when auth is off. Excess requests get `429` with a `Retry-After` header. `TRUST_FORWARDED_FOR` takes the client IP from a load balancer's `X-Forwarded-For`.
- `GET /health/ready` for load balancers: checks SearXNG, and reports "degraded" rather than failing while the DuckDuckGo fallback can serve searches.
- Request IDs: every response has an `X-Request-ID` header (the caller's own, or a generated one), and each request is logged on one line with its ID, status, duration, cache hit or miss, and pages read.

### Python client
- Retries transient failures (connection failures, `429`, `503`, `504`, and `502` from a proxy) with exponential backoff, honoring `Retry-After`. Set `max_retries` (default 2); `0` turns retries off.
- New `RateLimitError` with `retry_after`. A `502` that doesn't come from OpenScour is now an `APIStatusError` instead of `NoResultsError`.

### MCP server
- Reuses one client, and its connections, for all tool calls.

### Security
- The user agent follows the package version (`OpenScour/0.3.0`) instead of a fixed `OpenScour/0.2`.
- API keys are compared in constant time.
- robots.txt downloads stop at 512 KB instead of reading the whole response first.

### Reliability and performance
- The robots.txt cache holds at most 4,096 sites, dropping the least recently used, and also caches sites that have no robots.txt.
- robots.txt lookups time out after 1 second instead of 2, so a slow site can't use up a basic search's 2-second budget.

## [0.2.0] - 2026-10-29

Security and reliability hardening (phase 1 of the enterprise roadmap).

### Security
- Server-side request forgery (SSRF) protection: pages are only fetched from public addresses, checked on the address actually connected to, on every redirect. Requests for cloud metadata, localhost, private ranges or hostnames resolving to them return a `blocked:` error. `ALLOW_PRIVATE_NETWORK_FETCHES` opts out for trusted internal deployments.
- OpenScour identifies itself honestly (`OpenScour/0.2` user agent) instead of imitating a browser, and follows robots.txt (`RESPECT_ROBOTS_TXT`).
- The API container runs as an unprivileged user with a read-only filesystem, no capabilities, `no-new-privileges` and a health check. SearXNG is pinned to a release. Docker Compose publishes ports on `127.0.0.1` only.

### Reliability and performance
- One page-download limit shared by all requests (`FETCH_CONCURRENCY`), instead of one per request.
- Concurrent requests for the same page share one download (request coalescing).
- Background fetches are capped (`MAX_INFLIGHT_FETCHES`); beyond the cap, searches use search snippets.
- Ranking runs off the event loop, so it no longer delays other requests.
- Extraction workers are replaced after `EXTRACT_MAX_TASKS_PER_CHILD` pages to keep memory flat.
- The search cache treats domain lists in any order as the same search.

### Ranking
- Tokenization handles any script: accents, Cyrillic, and Chinese, Japanese and Korean (as character pairs).
- The current year is computed per request, so freshness stays correct across New Year.

### Testing
- 35 new tests: SSRF guard (including redirects), robots.txt, the shared page fetcher, and the server endpoints.

## [0.1.0] - 2026-10-29

First release.

### Server
- `POST /search`: searches the web through a self-hosted SearXNG instance, with a DuckDuckGo fallback, and returns the most relevant passage from each result page.
- `POST /extract`: returns the clean text of up to 20 URLs, or only the passages that match a `query`.
- Answer-aware ranking: dates for *when*, numbers for *how many*, names for *who*, version numbers for *latest release* questions, and the current holder for *who is* questions.
- Topic searches require the query's named terms in each passage, favor recent content for *latest/new/recent* queries, and rank tables of contents below explanatory passages.
- Basic searches answer as soon as enough pages are ready, capped at 2 seconds; text extraction runs in parallel worker processes.
- Optional AI answers with Claude, through Amazon Bedrock or the Claude API.
- API keys, in-memory caching, and a Docker Compose setup.

### Python client
- `OpenScour` and `AsyncOpenScour` with `search()` and `extract()`, typed results and typed errors. Only depends on `httpx`.

### MCP server
- `openscour-mcp` gives AI agents read-only `search` and `extract` tools, with setup instructions for Claude Code, Kiro, Cursor, Claude Desktop and VS Code.

[0.3.1]: https://github.com/ivipinmishra/openscour/releases/tag/v0.3.1
[0.3.0]: https://github.com/ivipinmishra/openscour/releases/tag/v0.3.0
[0.2.0]: https://github.com/ivipinmishra/openscour/releases/tag/v0.2.0
[0.1.0]: https://github.com/ivipinmishra/openscour/releases/tag/v0.1.0
