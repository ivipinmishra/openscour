# Changelog

All notable changes to OpenScour are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[0.1.0]: https://github.com/ivipinmishra/openscour/releases/tag/v0.1.0
