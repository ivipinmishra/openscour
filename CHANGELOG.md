# Changelog

All notable changes to OpenScour are listed here. Versions follow [Semantic Versioning](https://semver.org/).

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
