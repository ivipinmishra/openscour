import asyncio
import hashlib
import logging
import math
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
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
from openscour.server.ratelimit import RateLimiter
from openscour.server.robots import RobotsChecker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
access_log = logging.getLogger("openscour.access")

# depth -> (extra candidates fetched beyond max_results, chunks per result, fetch timeout, deadline)
# A deadline returns early with whatever pages are ready; None waits for every page.
DEPTH = {
    "basic": (3, 1, 5.0, settings.basic_fetch_deadline),
    "advanced": (None, 3, settings.fetch_timeout, None),  # None = 2x max_results
}

# A basic search returns early once max_results pages are read, but never with fewer
# than this many: with max_results=1 it would otherwise rank only the fastest page
# against bare search snippets, however poor that page is.
MIN_PAGES_READ = 3

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
    app.state.limiter = RateLimiter(settings.rate_limit_per_minute) if settings.rate_limit_per_minute > 0 else None
    await asyncio.to_thread(warm_up_extractors)
    yield
    await app.state.page_http.aclose()
    await app.state.http.aclose()
    shutdown_extractors()


app = FastAPI(title="OpenScour", version="0.3.1", lifespan=lifespan)
bearer = HTTPBearer(auto_error=False)

# Request IDs from callers are echoed back only if they are short and plain.
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_HEALTH_PATHS = ("/health", "/health/ready")


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Tags each request with an ID (X-Request-ID) and logs one line when it finishes."""
    request_id = request.headers.get("x-request-id", "")
    if not _REQUEST_ID.fullmatch(request_id):
        request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    finally:
        fields = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "duration_ms": round((time.perf_counter() - start) * 1000),
            **getattr(request.state, "log_fields", {}),
        }
        # Load balancers poll the health checks every few seconds; keep them out of the log.
        level = logging.DEBUG if request.url.path in _HEALTH_PATHS else logging.INFO
        access_log.log(level, " ".join(f"{k}={v}" for k, v in fields.items()))
    response.headers["X-Request-ID"] = request_id
    return response


def require_api_key(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str | None:
    """Checks the bearer token; returns a fingerprint of the matched key, or None when auth is off."""
    keys = settings.api_key_set
    if not keys:
        return None
    token = creds.credentials if creds is not None else ""
    token_digest = hashlib.sha256(token.encode()).digest()
    matched = False
    for key in keys:
        matched |= secrets.compare_digest(token_digest, hashlib.sha256(key.encode()).digest())
    if creds is None or not matched:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return token_digest.hex()[:16]


def client_ip(request: Request) -> str:
    if settings.trust_forwarded_for and (forwarded := request.headers.get("x-forwarded-for")):
        # The proxy appends the address it saw last; earlier entries come from the client.
        return forwarded.rsplit(",", 1)[-1].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request, key: str | None = Depends(require_api_key)) -> None:
    """Limits each API key, or each client IP when auth is off (after auth, so bad keys get 401)."""
    limiter: RateLimiter | None = request.app.state.limiter
    if limiter is None:
        return
    client = f"key:{key}" if key else f"ip:{client_ip(request)}"
    if (wait := limiter.acquire(client)) is not None:
        raise HTTPException(
            status_code=429, detail="Rate limit exceeded", headers={"Retry-After": str(math.ceil(wait))}
        )


def _engine_log_fields(stats: dict) -> dict[str, str]:
    """Which search engines answered, for the request log: engines=brave,bing engines_failed=google:CAPTCHA."""
    fields = {"engines": ",".join(stats.get("engines", [])) or "none"}
    if failed := stats.get("engines_failed"):
        fields["engines_failed"] = ",".join(f"{name}:{reason}".replace(" ", "_") for name, reason in failed.items())
    if fallback := stats.get("fallback"):
        fields["fallback"] = fallback
    return fields


@app.get("/health")
async def health():
    """Liveness: the process is up. Checks nothing else, so it stays fast."""
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(request: Request):
    """Readiness: whether this instance can serve searches, for load balancers.

    When SearXNG is down but the DuckDuckGo fallback is on, searches still work,
    so the instance reports "degraded" and stays in service: failing every
    instance at once for a shared dependency would turn degraded into down.
    The same goes for search engines blocking requests: "engines" shows what
    each engine did in the last 15 minutes of searches, and the instance is
    "degraded" when none of them answered.
    """
    try:
        resp = await request.app.state.http.get(f"{settings.searxng_url.rstrip('/')}/healthz", timeout=2.0)
        searxng = "ok" if resp.status_code == 200 else "error"
    except httpx.HTTPError:
        searxng = "error"
    engines = providers.engine_health.recent()
    engines_blocked = bool(engines) and "ok" not in engines.values()
    if searxng == "ok" and not engines_blocked:
        status, code = "ready", 200
    elif searxng == "ok":
        status, code = "degraded", 200
    elif settings.duckduckgo_fallback:
        status, code = "degraded", 200
    else:
        status, code = "not ready", 503
    return JSONResponse({"status": status, "checks": {"searxng": searxng, "engines": engines}}, status_code=code)


@app.post("/search", response_model=SearchResponse, dependencies=[Depends(rate_limit)])
async def search(req: SearchRequest, request: Request) -> SearchResponse:
    start = time.perf_counter()
    # Domain lists in a different order are the same search.
    normalized = req.model_copy(
        update={"include_domains": sorted(req.include_domains), "exclude_domains": sorted(req.exclude_domains)}
    )
    cache_key = hashlib.sha256(normalized.model_dump_json().encode()).hexdigest()
    if (hit := search_cache.get(cache_key)) is not None:
        request.state.log_fields = {"cache": "hit", "results": len(hit.results)}
        return hit.model_copy(update={"response_time": round(time.perf_counter() - start, 3)})

    extra, chunks_per_doc, timeout, deadline = DEPTH[req.search_depth]
    n_candidates = req.max_results * 2 if extra is None else req.max_results + extra

    client: httpx.AsyncClient = request.app.state.http
    engine_stats: dict = {}
    raw = await providers.search(
        client,
        req.query,
        n_candidates,
        topic=req.topic,
        time_range=req.time_range,
        include_domains=req.include_domains,
        exclude_domains=req.exclude_domains,
        stats=engine_stats,
    )
    request.state.log_fields = _engine_log_fields(engine_stats)
    if not raw:
        raise HTTPException(status_code=502, detail="No results from search backends")

    enough = max(req.max_results, MIN_PAGES_READ)
    pages = await request.app.state.pages.fetch([r.url for r in raw], timeout, deadline, enough=enough)

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
    request.state.log_fields = {
        "cache": "miss",
        **_engine_log_fields(engine_stats),
        "candidates": len(raw),
        "pages_fetched": sum(isinstance(p, str) for p in pages.values()),
        "results": len(results),
    }
    return resp


@app.post("/extract", response_model=ExtractResponse, dependencies=[Depends(rate_limit)])
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
    request.state.log_fields = {"urls": len(urls), "extracted": sum(isinstance(v, str) for v in pages.values())}
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
