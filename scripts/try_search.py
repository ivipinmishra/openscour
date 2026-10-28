"""Quick manual check: uv run python scripts/try_search.py "your query" [basic|advanced] [general|news]"""

import sys

from openscour import OpenScour

query = sys.argv[1] if len(sys.argv) > 1 else "what is retrieval augmented generation"
depth = sys.argv[2] if len(sys.argv) > 2 else "basic"
topic = sys.argv[3] if len(sys.argv) > 3 else "general"

with OpenScour() as client:
    response = client.search(query, search_depth=depth, topic=topic, max_results=4)

print(f"{response.query!r}  ({depth}, {topic})  {response.response_time}s")
if response.answer:
    print("answer:", response.answer)
for r in response.results:
    print(f"\n{r.score:.2f}  {r.title}\n      {r.url}\n      {r.content[:300]}")
