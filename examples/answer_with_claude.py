"""Answer a question with current web information: search with OpenScour, answer with Claude.

    uv add git+https://github.com/ivipinmishra/openscour anthropic
    export ANTHROPIC_API_KEY=...
    uv run python answer_with_claude.py "What changed in Amazon Bedrock this month?"
"""

import sys

import anthropic

from openscour import OpenScour

question = sys.argv[1] if len(sys.argv) > 1 else "What changed in Amazon Bedrock this month?"

# 1. Search: OpenScour returns the most relevant passage from each page.
with OpenScour() as search_client:
    search = search_client.search(question, max_results=5, search_depth="advanced")

sources = "\n\n".join(
    f"[{i}] {r.title}\nURL: {r.url}\n{r.content}" for i, r in enumerate(search.results, start=1)
)

# 2. Answer: Claude reads only those passages and cites them.
claude = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
response = claude.beta.messages.create(
    model="claude-opus-5-5",
    max_tokens=16000,
    # Low effort is enough for summarizing a few passages, and answers faster.
    output_config={"effort": "low"},
    # If a safety check declines the request, retry it on a fallback model.
    betas=["server-side-fallback-2026-07-01"],
    fallbacks="default",
    system=(
        "Answer the question using only the numbered sources. Cite sources inline like [1]. "
        "If the sources don't contain the answer, say so."
    ),
    messages=[{"role": "user", "content": f"Sources:\n\n{sources}\n\nQuestion: {question}"}],
)

if response.stop_reason == "refusal":
    print("Claude declined to answer this question.")
else:
    print("".join(block.text for block in response.content if block.type == "text"))
    print("\nSources:")
    for i, r in enumerate(search.results, start=1):
        print(f"[{i}] {r.url}")
