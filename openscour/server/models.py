from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


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
