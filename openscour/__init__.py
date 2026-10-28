"""OpenScour: self-hosted web search for LLMs and agents.

    from openscour import OpenScour

    client = OpenScour(api_key="YOUR_API_KEY")
    results = client.search("Latest AWS Bedrock updates", max_results=10)
"""

from openscour.client import AsyncOpenScour, OpenScour
from openscour.errors import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    InvalidRequestError,
    NoResultsError,
    OpenScourError,
)
from openscour.types import ExtractResponse, ExtractResult, FailedResult, SearchResponse, SearchResult

__version__ = "0.1.0"

__all__ = [
    "OpenScour",
    "AsyncOpenScour",
    "SearchResponse",
    "SearchResult",
    "ExtractResponse",
    "ExtractResult",
    "FailedResult",
    "OpenScourError",
    "APIConnectionError",
    "APIStatusError",
    "AuthenticationError",
    "InvalidRequestError",
    "NoResultsError",
]
