"""Optional answer synthesis with Claude (Amazon Bedrock or the Claude API)."""

import logging
from functools import lru_cache

import anthropic

from openscour.server.config import settings
from openscour.server.ranker import Ranked

log = logging.getLogger(__name__)

SYSTEM = (
    "You answer a search query using only the numbered sources provided. "
    "Write a direct, factual answer in 1-4 sentences. Cite sources inline as [1], [2]. "
    "If the sources do not contain the answer, say so briefly."
)


@lru_cache(maxsize=1)
def _client():
    if settings.answer_provider == "bedrock":
        return anthropic.AsyncAnthropicBedrockMantle(aws_region=settings.aws_region)
    return anthropic.AsyncAnthropic()


def _model() -> str:
    m = settings.answer_model
    if settings.answer_provider == "bedrock" and not m.startswith("anthropic."):
        return f"anthropic.{m}"
    return m


async def generate_answer(query: str, results: list[Ranked]) -> str | None:
    if settings.answer_provider == "none" or not results:
        return None

    sources = "\n\n".join(
        f"[{i}] {r.doc.title}\n{r.doc.url}\n{r.content}" for i, r in enumerate(results, start=1)
    )
    messages = [{"role": "user", "content": f"Sources:\n\n{sources}\n\nQuery: {query}"}]
    try:
        response = await _client().messages.create(
            model=_model(),
            max_tokens=8000,
            system=SYSTEM,
            output_config={"effort": "low"},
            messages=messages,
        )
    except anthropic.APIError as e:
        log.warning("answer generation failed: %s", e)
        return None

    if response.stop_reason == "refusal":
        return None
    return "".join(b.text for b in response.content if b.type == "text").strip() or None
