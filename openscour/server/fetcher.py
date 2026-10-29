import asyncio
import logging
import multiprocessing
import re
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import httpx
import trafilatura

from openscour.server.cache import TTLCache
from openscour.server.config import settings
from openscour.server.robots import RobotsChecker

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
            max_tasks_per_child=settings.extract_max_tasks_per_child,
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
        # Keep the reason for blocked addresses; for other errors the type is enough.
        reason = str(e) if str(e).startswith("blocked:") else type(e).__name__
        raise FetchError(reason) from e
    try:
        text = await asyncio.get_running_loop().run_in_executor(_extract_pool(), _extract, html, ctype)
    except BrokenProcessPool as e:
        # A worker died (e.g. a pathological page); start fresh workers next time.
        shutdown_extractors()
        raise FetchError("extraction worker crashed") from e
    if not text:
        raise FetchError("no extractable content")
    return text


def task_result(task: asyncio.Task[str]) -> str | FetchError:
    """The finished task's page text, or the reason it has none."""
    if task.cancelled():
        return FetchError("cancelled")
    exc = task.exception()
    if exc is None:
        return task.result()
    log.debug("fetch failed: %s", exc)
    return exc if isinstance(exc, FetchError) else FetchError(type(exc).__name__)


class PageFetcher:
    """Fetches pages for the whole server.

    - One download limit (fetch_concurrency) shared by all requests, rather than
      one per request, so concurrent searches can't multiply the load.
    - Requests for a URL that is already being fetched wait for that fetch
      instead of starting another (request coalescing).
    - Pages are cached; fetches that outlive a request's deadline finish in the
      background and fill the cache, up to max_inflight_fetches at a time.
    - robots.txt is checked before downloading, when enabled.
    """

    def __init__(self, client: httpx.AsyncClient, cache: TTLCache, robots: RobotsChecker | None = None) -> None:
        self._client = client
        self._cache = cache
        self._robots = robots
        self._downloads = asyncio.Semaphore(settings.fetch_concurrency)
        self._inflight: dict[str, asyncio.Task[str]] = {}

    @property
    def inflight(self) -> int:
        return len(self._inflight)

    def _start(self, url: str, timeout: float) -> asyncio.Task[str] | None:
        if (task := self._inflight.get(url)) is not None:
            return task
        if len(self._inflight) >= settings.max_inflight_fetches:
            return None
        task = asyncio.create_task(self._fetch(url, timeout))
        self._inflight[url] = task
        task.add_done_callback(lambda t, url=url: self._finished(url, t))
        return task

    def _finished(self, url: str, task: asyncio.Task[str]) -> None:
        if self._inflight.get(url) is task:
            del self._inflight[url]
        if isinstance(result := task_result(task), str):
            self._cache.set(url, result)

    async def _fetch(self, url: str, timeout: float) -> str:
        if self._robots is not None and not await self._robots.allowed(url):
            raise FetchError("disallowed by robots.txt")
        async with self._downloads:
            return await fetch_page(self._client, url, timeout)

    async def fetch(
        self,
        urls: list[str],
        timeout: float,
        deadline: float | None = None,
        enough: int | None = None,
    ) -> dict[str, str | FetchError]:
        """Fetch pages, using the cache.

        Without a deadline, wait for every page. With one, return once `enough`
        pages (counting cached ones) are ready, or when the deadline passes;
        pages not ready by then come back as FetchError and keep loading in
        the background, so a repeat request has them.
        """
        out: dict[str, str | FetchError] = {}
        tasks: dict[str, asyncio.Task[str]] = {}
        for url in urls:
            if (hit := self._cache.get(url)) is not None:
                out[url] = hit
            elif (task := self._start(url, timeout)) is not None:
                tasks[url] = task
            else:
                out[url] = FetchError("server busy")
        if tasks and deadline is None:
            await asyncio.wait(tasks.values())
        elif tasks:
            await _wait_for_enough(list(tasks.values()), (enough or len(urls)) - len(out), deadline)
        for url, task in tasks.items():
            out[url] = task_result(task) if task.done() else FetchError("not ready before the deadline")
        return out


async def _wait_for_enough(tasks: list[asyncio.Task[str]], enough: int, deadline: float) -> None:
    """Wait until `enough` tasks have produced page text, all tasks are done, or the deadline passes."""
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline
    pending = set(tasks)
    while pending:
        if sum(1 for t in tasks if t.done() and isinstance(task_result(t), str)) >= enough:
            return
        remaining = end - loop.time()
        if remaining <= 0:
            return
        _, pending = await asyncio.wait(pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
