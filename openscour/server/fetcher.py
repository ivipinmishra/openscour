import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import httpx
import trafilatura

from openscour.server.config import settings

log = logging.getLogger(__name__)

_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")

# trafilatura/lxml is not safe to run in parallel threads: concurrent element
# copies corrupt libxml2's shared dictionary and abort the process. Extraction
# is CPU-bound under the GIL anyway, so one dedicated thread costs little.
_extract_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="extract")


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
    text = await asyncio.get_running_loop().run_in_executor(_extract_pool, _extract, html, ctype)
    if not text:
        raise FetchError("no extractable content")
    return text


async def fetch_many(
    client: httpx.AsyncClient, urls: list[str], timeout: float
) -> dict[str, str | FetchError]:
    sem = asyncio.Semaphore(settings.fetch_concurrency)

    async def one(url: str) -> tuple[str, str | FetchError]:
        async with sem:
            try:
                return url, await fetch_page(client, url, timeout)
            except FetchError as e:
                log.debug("fetch failed %s: %s", url, e)
                return url, e

    return dict(await asyncio.gather(*(one(u) for u in urls)))
