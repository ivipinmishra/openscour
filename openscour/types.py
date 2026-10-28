from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    score: float
    raw_content: str | None = None
    published_date: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SearchResult:
        return cls(
            title=d["title"],
            url=d["url"],
            content=d["content"],
            score=d["score"],
            raw_content=d.get("raw_content"),
            published_date=d.get("published_date"),
        )


@dataclass
class SearchResponse:
    query: str
    results: list[SearchResult]
    response_time: float
    answer: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SearchResponse:
        return cls(
            query=d["query"],
            results=[SearchResult.from_dict(r) for r in d.get("results", [])],
            response_time=d.get("response_time", 0.0),
            answer=d.get("answer"),
        )


@dataclass
class ExtractResult:
    url: str
    raw_content: str


@dataclass
class FailedResult:
    url: str
    error: str


@dataclass
class ExtractResponse:
    results: list[ExtractResult]
    failed_results: list[FailedResult] = field(default_factory=list)
    response_time: float = 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExtractResponse:
        return cls(
            results=[ExtractResult(r["url"], r["raw_content"]) for r in d.get("results", [])],
            failed_results=[FailedResult(r["url"], r["error"]) for r in d.get("failed_results", [])],
            response_time=d.get("response_time", 0.0),
        )
