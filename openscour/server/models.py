from typing import Annotated, Literal

from pydantic import BaseModel, Field, HttpUrl, StringConstraints

# A SearXNG engine name, e.g. "bing", "brave", "bing news".
EngineName = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")
]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=400)
    search_depth: Literal["basic", "advanced"] = "basic"
    topic: Literal["general", "news"] = "general"
    max_results: int = Field(default=5, ge=1, le=20)
    include_answer: bool = False
    include_raw_content: bool = False
    include_domains: list[str] = []
    exclude_domains: list[str] = []
    time_range: Literal["day", "week", "month", "year"] | None = None
    # Results in this language, optionally for a region: "de", "de-DE", "en-GB".
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}(-[A-Za-z]{2})?$")
    # Search only these SearXNG engines instead of the configured ones.
    engines: list[EngineName] = Field(default=[], max_length=10)


class SearchResult(BaseModel):
    title: str
    url: str
    content: str
    score: float
    raw_content: str | None = None
    published_date: str | None = None


class SearchResponse(BaseModel):
    query: str
    answer: str | None = None
    results: list[SearchResult]
    response_time: float


class ExtractRequest(BaseModel):
    urls: list[HttpUrl] = Field(min_length=1, max_length=20)
    # With a query, each result holds only the page's most relevant passages.
    query: str | None = Field(default=None, min_length=1, max_length=400)
    chunks_per_source: int = Field(default=3, ge=1, le=10)


class ExtractResult(BaseModel):
    url: str
    raw_content: str


class FailedResult(BaseModel):
    url: str
    error: str


class ExtractResponse(BaseModel):
    results: list[ExtractResult]
    failed_results: list[FailedResult]
    response_time: float
