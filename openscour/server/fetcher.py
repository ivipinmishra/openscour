import asyncio
import logging
import multiprocessing
import re
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import httpx
import trafilatura

from openscour.server.config import settings

log = logging.getLogger(__name__)

_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")

# Extraction runs in worker processes: trafilatura/lxml is not safe in parallel
# threads (concurrent element copies corrupt libxml2's shared dictionary and abort
# the process), and processes also extract several pages truly in parallel.
_pool: ProcessPoolExecutor | None = None


def _extract_pool() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(
            max_workers=settings.extract_workers,
            # "spawn" everywhere: forking a process that has running threads is unsafe.
            mp_context=multiprocessing.get_context("spawn"),
        )
    return _pool


def warm_up_extractors() -> None:
    """Start the worker processes now, so the first search doesn't pay for it."""
    pool = _extract_pool()
    list(pool.map(_extract, ["<p>warm up</p>"] * settings.extract_workers, ["text/html"] * settings.extract_workers))


def shutdown_extractors() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


class FetchError(Exception):
    pass


async def _download(client: httpx.AsyncClient, url: str, timeout: float) -> tuple[str, str]:
    async with client.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code}")
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype and not ctype.startswith(_TEXT_TYPES):
            raise FetchError(f"unsupported content type {ctype}")
        body = bytearray()
        async for chunk in resp.aiter_bytes():
            body.extend(chunk)
            if len(body) > settings.max_page_bytes:
                break
        encoding = resp.encoding or "utf-8"
    return body.decode(encoding, errors="replace"), ctype


# Back-matter headings; everything from the first one onward is citations and links.
_BACK_MATTER = re.compile(
    r"^#*\s*(references|notes|citations|footnotes|external links|further reading|bibliography|sources|see also)\s*$",
    re.I | re.M,
)


# Footnote markers such as "<sup>\[30\]</sup>" or "[30]".
_FOOTNOTE = re.compile(r"<sup>.*?</sup>|\\?\[\d+\\?\]")


def strip_back_matter(text: str) -> str:
    # Only cut in the second half so a page that opens with "Notes" keeps its body.
    for m in _BACK_MATTER.finditer(text):
        if m.start() > len(text) // 2:
            return text[: m.start()].rstrip()
    return text


def _extract(html: str, ctype: str) -> str:
    if ctype == "text/plain":
        return html.strip()
    # Markdown output keeps sentences and infobox rows that plain-text output
    # silently drops (e.g. "JD Vance is the 50th and current vice president").
    text = trafilatura.extract(
        html,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        favor_recall=True,
        deduplicate=True,
    )
    return strip_back_matter(_FOOTNOTE.sub("", text or "").strip())


async def fetch_page(client: httpx.AsyncClient, url: str, timeout: float) -> str:
    try:
        html, ctype = await asyncio.wait_for(_download(client, url, timeout), timeout + 1)
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        raise FetchError(type(e).__name__) from e
    try:
        text = await asyncio.get_running_loop().run_in_executor(_extract_pool(), _extract, html, ctype)
    except BrokenProcessPool as e:
        # A worker died (e.g. a pathological page); start fresh workers next time.
        shutdown_extractors()
        raise FetchError("extraction worker crashed") from e
    if not text:
        raise FetchError("no extractable content")
    return text


def start_fetches(client: httpx.AsyncClient, urls: list[str], timeout: float) -> dict[str, asyncio.Task[str]]:
    """Start fetching every URL; each task returns page text or raises FetchError."""
    sem = asyncio.Semaphore(settings.fetch_concurrency)

    async def one(url: str) -> str:
        async with sem:
            return await fetch_page(client, url, timeout)

    return {u: asyncio.create_task(one(u)) for u in urls}


def task_result(task: asyncio.Task[str]) -> str | FetchError:
    """The finished task's page text, or the reason it has none."""
    if task.cancelled():
        return FetchError("cancelled")
    exc = task.exception()
    if exc is None:
        return task.result()
    log.debug("fetch failed: %s", exc)
    return exc if isinstance(exc, FetchError) else FetchError(type(exc).__name__)


async def fetch_many(
    client: httpx.AsyncClient, urls: list[str], timeout: float
) -> dict[str, str | FetchError]:
    tasks = start_fetches(client, urls, timeout)
    if tasks:
        await asyncio.wait(tasks.values())
    return {u: task_result(t) for u, t in tasks.items()}
