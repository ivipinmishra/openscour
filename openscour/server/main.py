import asyncio
import hashlib
import logging
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from openscour.server import providers
from openscour.server.answer import generate_answer
from openscour.server.cache import TTLCache
from openscour.server.config import settings
from openscour.server.fetcher import PageFetcher, shutdown_extractors, warm_up_extractors
from openscour.server.models import (
    ExtractRequest,
    ExtractResponse,
    ExtractResult,
    FailedResult,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from openscour.server.netguard import guarded_transport
from openscour.server.ranker import Doc, rank, relevant_passages
from openscour.server.robots import RobotsChecker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# depth -> (extra candidates fetched beyond max_results, chunks per result, fetch timeout, deadline)
# A deadline returns early with whatever pages are ready; None waits for every page.
DEPTH = {
    "basic": (3, 1, 5.0, settings.basic_fetch_deadline),
    "advanced": (None, 3, settings.fetch_timeout, None),  # None = 2x max_results
}

# Pages that extract to fewer words than this also get the search snippet as a passage.
SHORT_PAGE_WORDS = 150

search_cache = TTLCache(settings.cache_ttl_seconds)
page_cache = TTLCache(settings.cache_ttl_seconds, max_items=4096)


@asynccontextmanager
async def lifespan(app: FastAPI):
    headers = {"User-Agent": settings.user_agent, "Accept-Language": "en-US,en;q=0.9"}
    # SearXNG runs on the private network next to OpenScour, so this client may
    # reach private addresses. It only ever talks to SEARXNG_URL.
    app.state.http = httpx.AsyncClient(headers=headers, limits=httpx.Limits(max_connections=20))
    # Pages come from untrusted URLs: this client can only connect to public
    # addresses, on every redirect hop. trust_env=False so proxy variables can't
    # route around that check.
    transport = None if settings.allow_private_network_fetches else guarded_transport(max_connections=50)
    app.state.page_http = httpx.AsyncClient(headers=headers, transport=transport, trust_env=False)
    robots = RobotsChecker(app.state.page_http) if settings.respect_robots_txt else None
    app.state.pages = PageFetcher(app.state.page_http, page_cache, robots)
    await asyncio.to_thread(warm_up_extractors)
    yield
    await app.state.page_http.aclose()
    await app.state.http.aclose()
    shutdown_extractors()


app = FastAPI(title="OpenScour", version="0.2.0", lifespan=lifespan)
bearer = HTTPBearer(auto_error=False)


def require_api_key(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    keys = settings.api_key_set
    if keys and (creds is None or creds.credentials not in keys):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/search", response_model=SearchResponse, dependencies=[Depends(require_api_key)])
async def search(req: SearchRequest, request: Request) -> SearchResponse:
    start = time.perf_counter()
    # Domain lists in a different order are the same search.
    normalized = req.model_copy(
        update={"include_domains": sorted(req.include_domains), "exclude_domains": sorted(req.exclude_domains)}
    )
    cache_key = hashlib.sha256(normalized.model_dump_json().encode()).hexdigest()
    if (hit := search_cache.get(cache_key)) is not None:
        return hit.model_copy(update={"response_time": round(time.perf_counter() - start, 3)})

    extra, chunks_per_doc, timeout, deadline = DEPTH[req.search_depth]
    n_candidates = req.max_results * 2 if extra is None else req.max_results + extra

    client: httpx.AsyncClient = request.app.state.http
    raw = await providers.search(
        client,
        req.query,
        n_candidates,
        topic=req.topic,
        time_range=req.time_range,
        include_domains=req.include_domains,
        exclude_domains=req.exclude_domains,
    )
    if not raw:
        raise HTTPException(status_code=502, detail="No results from search backends")

    pages = await request.app.state.pages.fetch([r.url for r in raw], timeout, deadline, enough=req.max_results)

    # Pages that could not be fetched still count, using the engine's snippet.
    docs = []
    meta = {}
    for i, r in enumerate(raw):
        page = pages.get(r.url)
        text = page if isinstance(page, str) else r.snippet
        # Video pages and JavaScript-heavy sites often extract to little more than
        # footer links; the search engine's snippet usually describes them better.
        if isinstance(page, str) and r.snippet and len(page.split()) < SHORT_PAGE_WORDS:
            text = f"{r.snippet}\n{page}"
        if not text:
            continue
        docs.append(Doc(url=r.url, title=r.title, text=text, rank=i, published=r.published_date))
        meta[r.url] = (r, page if isinstance(page, str) else None)

    # Ranking is CPU work; keep the event loop free for other requests.
    ranked = (await asyncio.to_thread(rank, req.query, docs, chunks_per_doc))[: req.max_results]

    results = [
        SearchResult(
            title=x.doc.title,
            url=x.doc.url,
            content=x.content,
            score=x.score,
            raw_content=meta[x.doc.url][1] if req.include_raw_content else None,
            published_date=meta[x.doc.url][0].published_date,
        )
        for x in ranked
    ]
    answer = await generate_answer(req.query, ranked) if req.include_answer else None

    resp = SearchResponse(
        query=req.query,
        answer=answer,
        results=results,
        response_time=round(time.perf_counter() - start, 3),
    )
    search_cache.set(cache_key, resp)
    return resp


@app.post("/extract", response_model=ExtractResponse, dependencies=[Depends(require_api_key)])
async def extract(req: ExtractRequest, request: Request) -> ExtractResponse:
    start = time.perf_counter()
    urls = list(dict.fromkeys(str(u) for u in req.urls))
    pages = await request.app.state.pages.fetch(urls, settings.fetch_timeout)
    if req.query:
        # Ranking is CPU work; keep it off the event loop.
        pages = {
            u: await asyncio.to_thread(relevant_passages, req.query, v, "", req.chunks_per_source)
            if isinstance(v, str)
            else v
            for u, v in pages.items()
        }
    return ExtractResponse(
        results=[ExtractResult(url=u, raw_content=v) for u, v in pages.items() if isinstance(v, str)],
        failed_results=[FailedResult(url=u, error=str(v)) for u, v in pages.items() if not isinstance(v, str)],
        response_time=round(time.perf_counter() - start, 3),
    )


def run() -> None:
    """Entry point for the ``openscour-server`` command."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Run the OpenScour search API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("openscour.server.main:app", host=args.host, port=args.port)
