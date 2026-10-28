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
    max_page_bytes: int = 3_000_000
    cache_ttl_seconds: int = 900
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    )

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


settings = Settings()
