"""Checks whether the returned passages actually contain the answer.

Needs the API running on localhost:8000. Restart the API between runs so the
search cache doesn't hide ranking changes.

    uv run python scripts/eval_answers.py
"""

import json
import re
import sys
import urllib.request

# (query, regex the answer passage should match)
CASES = [
    ("When is the mid term election in USA?", r"Nov(ember|\.)? 3\b"),
    ("When was Python first released?", r"1991"),
    ("When did the Berlin Wall fall?", r"1989"),
    ("How tall is the Eiffel Tower?", r"3[0-3]\d(\.\d+)? ?(m|metres|meters)"),
    ("How many moons does Mars have?", r"\b(two|2)\b.{0,40}moons|moons.{0,40}\b(two|2)\b|Phobos"),
    ("Who founded SpaceX?", r"Musk"),
    ("What is the capital of Australia?", r"Canberra"),
    ("When was the Declaration of Independence signed?", r"1776"),
    ("How long is the Great Wall of China?", r"\d[\d,.]* ?(km|kilomet|mi)"),
    ("When did World War 2 end?", r"1945"),
]


def search(query: str, depth: str) -> list[dict]:
    body = json.dumps({"query": query, "search_depth": depth, "max_results": 5}).encode()
    req = urllib.request.Request(
        "http://localhost:8000/search", data=body, headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["results"]


def main() -> None:
    depth = sys.argv[1] if len(sys.argv) > 1 else "basic"
    top1 = top3 = 0
    for query, pattern in CASES:
        try:
            results = search(query, depth)
        except Exception as e:
            print(f"ERR   {query}  ({e})")
            continue
        hits = [bool(re.search(pattern, r["content"], re.I)) for r in results]
        top1 += hits[:1] == [True]
        top3 += any(hits[:3])
        mark = "TOP1" if hits[:1] == [True] else ("top3" if any(hits[:3]) else "miss")
        print(f"{mark}  {query}")
        if mark != "TOP1" and results:
            print(f"        #1: {results[0]['content'][:160]}")
    n = len(CASES)
    print(f"\n{depth}: answer in #1 passage {top1}/{n}, in top-3 passages {top3}/{n}")


if __name__ == "__main__":
    main()
