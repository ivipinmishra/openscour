from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Comma-separated list of keys clients must send as "Authorization: Bearer <key>".
    # Leave empty to disable auth (local development).
    api_keys: str = ""

    searxng_url: str = "http://localhost:8888"
    # Used only when SearXNG errors out or returns nothing.
    duckduckgo_fallback: bool = True

    # include_answer backend. "none" makes include_answer return null.
    answer_provider: Literal["none", "bedrock", "anthropic"] = "none"
    answer_model: str = "claude-opus-5-5"
    aws_region: str = "us-east-1"

    fetch_timeout: float = 8.0
    fetch_concurrency: int = 10
    extract_workers: int = 4
    # Basic searches answer after this many seconds and use search snippets for
    # pages that aren't ready; those pages keep loading in the background for the cache.
    basic_fetch_deadline: float = 2.0
    max_page_bytes: int = 3_000_000
    cache_ttl_seconds: int = 900
    # Pages being fetched at once across all requests; more wait their turn.
    # Requests beyond max_inflight_fetches use search snippets instead.
    max_inflight_fetches: int = 200
    # Extraction workers are replaced after this many pages, so memory
    # fragmentation in lxml can't build up in a long-running server.
    extract_max_tasks_per_child: int = 500

    # Identify honestly to the sites OpenScour reads, and follow their robots.txt.
    user_agent: str = "OpenScour/0.2 (+https://github.com/ivipinmishra/openscour)"
    respect_robots_txt: bool = True
    # Pages are only fetched from public internet addresses (SSRF protection).
    # Set to true only for a trusted, internal-only deployment that must read
    # intranet pages; never on a server untrusted callers can reach.
    allow_private_network_fetches: bool = False

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


settings = Settings()
