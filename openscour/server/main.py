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
from openscour.server.fetcher import FetchError, fetch_many
from openscour.server.models import (
    ExtractRequest,
    ExtractResponse,
    ExtractResult,
    FailedResult,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from openscour.server.ranker import Doc, rank

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# depth -> (extra candidates fetched beyond max_results, chunks per result, fetch timeout)
DEPTH = {
    "basic": (3, 1, 5.0),
    "advanced": (None, 3, settings.fetch_timeout),  # None = 2x max_results
}

search_cache = TTLCache(settings.cache_ttl_seconds)
page_cache = TTLCache(settings.cache_ttl_seconds, max_items=4096)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(
        headers={"User-Agent": settings.user_agent, "Accept-Language": "en-US,en;q=0.9"},
        limits=httpx.Limits(max_connections=50),
    )
    yield
    await app.state.http.aclose()


app = FastAPI(title="OpenScour", version="0.1.0", lifespan=lifespan)
bearer = HTTPBearer(auto_error=False)


def require_api_key(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    keys = settings.api_key_set
    if keys and (creds is None or creds.credentials not in keys):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


async def fetch_cached(client: httpx.AsyncClient, urls: list[str], timeout: float) -> dict[str, str | FetchError]:
    out: dict[str, str | FetchError] = {}
    missing = []
    for u in urls:
        if (hit := page_cache.get(u)) is not None:
            out[u] = hit
        else:
            missing.append(u)
    for u, v in (await fetch_many(client, missing, timeout)).items():
        if isinstance(v, str):
            page_cache.set(u, v)
        out[u] = v
    return out


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/search", response_model=SearchResponse, dependencies=[Depends(require_api_key)])
async def search(req: SearchRequest, request: Request) -> SearchResponse:
    start = time.perf_counter()
    cache_key = hashlib.sha256(req.model_dump_json().encode()).hexdigest()
    if (hit := search_cache.get(cache_key)) is not None:
        return hit.model_copy(update={"response_time": round(time.perf_counter() - start, 3)})

    extra, chunks_per_doc, timeout = DEPTH[req.search_depth]
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

    pages = await fetch_cached(client, [r.url for r in raw], timeout)

    # Pages that could not be fetched still count, using the engine's snippet.
    docs = []
    meta = {}
    for i, r in enumerate(raw):
        page = pages.get(r.url)
        text = page if isinstance(page, str) else r.snippet
        if not text:
            continue
        docs.append(Doc(url=r.url, title=r.title, text=text, rank=i, published=r.published_date))
        meta[r.url] = (r, page if isinstance(page, str) else None)

    ranked = rank(req.query, docs, chunks_per_doc)[: req.max_results]

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
    pages = await fetch_cached(request.app.state.http, urls, settings.fetch_timeout)
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
