"""Google Search scraper — discovers events by parsing Google search result pages."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from urllib.parse import quote_plus

import httpx
from selectolax.parser import HTMLParser

from src.models import Event
from src.scrapers.base import BaseScraper, make_client, validate_url

logger = logging.getLogger(__name__)

QUERIES: tuple[str, ...] = (
    '"AWS credits" hackathon 2026',
    '"free Bedrock" workshop',
    '"Azure credits" hackathon',
    '"cloud credits" hackathon',
    '"free API credits" workshop',
)

# Maximum number of queries to execute per scrape() call.
# Google is aggressive about blocking automated requests,
# so we limit ourselves to a small batch each run.
MAX_QUERIES_PER_RUN = 3

# Delay range (seconds) between Google requests — longer than the
# default in base.py to reduce the chance of being blocked.
_GOOGLE_DELAY_RANGE = (3.0, 5.0)


def _build_search_url(query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus(query)}&num=10"


def _make_event_id(url: str) -> str:
    return "google:" + hashlib.md5(url.encode()).hexdigest()


def _parse_results(html: str) -> list[dict[str, str]]:
    """Parse Google search result HTML and return a list of raw result dicts.

    Each dict has keys: title, url, snippet.
    """
    tree = HTMLParser(html)
    results: list[dict[str, str]] = []

    for node in tree.css("div.g"):
        anchor = node.css_first("a")
        heading = node.css_first("h3")
        snippet_node = node.css_first("div.VwiC3b")

        if anchor is None or heading is None:
            continue

        href = anchor.attributes.get("href", "")
        title = heading.text(strip=True)
        snippet = snippet_node.text(strip=True) if snippet_node else ""

        if not href or not title:
            continue

        # Skip Google internal URLs (e.g. /search?q=related:...)
        if href.startswith("/"):
            continue

        results.append({"title": title, "url": href, "snippet": snippet})

    return results


class GoogleSearchScraper(BaseScraper):
    """Scrapes Google search results for hackathon/workshop events."""

    name: str = "google"

    def __init__(self) -> None:
        # Tracks which query index to start from on the next run.
        # Rotates through QUERIES so each scrape() uses different queries.
        self._query_index: int = 0

    def _next_queries(self) -> list[str]:
        """Return the next batch of queries to execute, rotating through the list."""
        selected: list[str] = []
        idx = self._query_index
        for _ in range(MAX_QUERIES_PER_RUN):
            selected.append(QUERIES[idx % len(QUERIES)])
            idx += 1
        self._query_index = idx % len(QUERIES)
        return selected

    @staticmethod
    async def _google_fetch(
        client: httpx.AsyncClient,
        url: str,
    ) -> httpx.Response:
        """Fetch a Google search URL directly (no retries).

        Uses URL validation from base.py for SSRF protection but avoids
        the retry decorator — retrying Google requests aggressively is
        counter-productive since it increases the chance of being blocked.
        """
        validate_url(url)
        response = await client.get(url)
        response.raise_for_status()
        return response

    async def scrape(self) -> list[Event]:
        """Execute up to MAX_QUERIES_PER_RUN Google searches and return deduplicated Events."""
        queries = self._next_queries()
        seen_urls: set[str] = set()
        events: list[Event] = []

        async with make_client() as client:
            for i, query in enumerate(queries):
                if i > 0:
                    await asyncio.sleep(random.uniform(*_GOOGLE_DELAY_RANGE))

                url = _build_search_url(query)
                try:
                    response = await self._google_fetch(client, url)
                except Exception:
                    logger.warning("Google search failed for query: %s", query, exc_info=True)
                    continue

                try:
                    raw_results = _parse_results(response.text)
                except Exception:
                    logger.warning("Failed to parse Google results for query: %s", query, exc_info=True)
                    continue

                for result in raw_results:
                    result_url = result["url"]
                    if result_url in seen_urls:
                        continue
                    seen_urls.add(result_url)

                    event = Event(
                        id=_make_event_id(result_url),
                        source="google",
                        title=result["title"],
                        url=result_url,
                        organizer="(via Google)",
                        description=result["snippet"],
                        location="",
                    )
                    events.append(event)

        return events
