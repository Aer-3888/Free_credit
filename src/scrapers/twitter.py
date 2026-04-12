"""Twitter scraper via Nitter instances — no API key required.

Nitter is an open-source Twitter frontend that serves plain HTML.
We search multiple Nitter instances for tweets about hackathons,
workshops, and events offering free cloud/LLM credits.

Unlike other scrapers that use ``BaseScraper.fetch()`` (which applies
domain allow-listing, SSRF checks, and tenacity retries), this scraper
manages its own request lifecycle because Nitter instances are ephemeral
community mirrors — they go up and down frequently. Instance-level
fallback replaces per-request retry, and a simple ``asyncio.sleep``
provides rate-limiting between queries.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re

import httpx
from selectolax.parser import HTMLParser

from src.models import Event
from src.scrapers.base import BaseScraper, make_client

logger = logging.getLogger(__name__)

NITTER_INSTANCES: tuple[str, ...] = (
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
)

QUERIES: tuple[str, ...] = (
    "AWS credits hackathon",
    "free Bedrock workshop",
    "cloud credits hackathon",
    "Azure OpenAI credits",
)

MAX_TWEETS_PER_QUERY = 20

# Delay range (seconds) between successive Nitter requests.
_DELAY_RANGE = (2.0, 3.0)


def _extract_tweet_id(href: str) -> str | None:
    """Extract the numeric tweet ID from a Nitter-style path like /user/status/123."""
    match = re.search(r"/status/(\d+)", href)
    return match.group(1) if match else None


def _parse_tweets(html: str, instance: str) -> list[Event]:
    """Parse Nitter search results HTML into Event objects.

    Returns at most MAX_TWEETS_PER_QUERY events.
    """
    tree = HTMLParser(html)
    events: list[Event] = []

    for item in tree.css(".timeline-item"):
        if len(events) >= MAX_TWEETS_PER_QUERY:
            break

        # Extract tweet link and ID
        tweet_link = item.css_first(".tweet-link")
        if tweet_link is None:
            continue

        href = tweet_link.attributes.get("href", "")
        tweet_id = _extract_tweet_id(href)
        if not tweet_id:
            continue

        # Extract tweet content
        content_node = item.css_first(".tweet-content")
        description = content_node.text(strip=True) if content_node else ""
        if not description:
            continue

        # Extract username
        username_node = item.css_first(".tweet-header .username")
        organizer = username_node.text(strip=True) if username_node else ""

        # Build full tweet URL using the Nitter instance
        full_url = f"{instance}{href}"

        # Title is first 100 chars of tweet text
        title = description[:100].strip()

        event = Event(
            id=f"twitter:{tweet_id}",
            source="twitter",
            title=title,
            url=full_url,
            organizer=organizer,
            description=description,
            location="Online",
        )
        events.append(event)

    return events


def _build_search_url(instance: str, query: str) -> str:
    """Build a Nitter search URL for the given query."""
    encoded = query.replace(" ", "+")
    return f"{instance}/search?f=tweets&q={encoded}"


class TwitterScraper(BaseScraper):
    """Scrape tweets about cloud credit events via Nitter instances.

    Uses instance-level fallback instead of per-request retry because
    Nitter mirrors are community-run and frequently unavailable.
    """

    name = "twitter"

    async def scrape(self) -> list[Event]:
        seen_ids: set[str] = set()
        all_events: list[Event] = []

        try:
            async with make_client() as client:
                for query in QUERIES:
                    events = await self._search_query(client, query)
                    for event in events:
                        if event.id not in seen_ids:
                            seen_ids.add(event.id)
                            all_events.append(event)
        except Exception:
            logger.exception("Twitter scraper failed unexpectedly")

        return all_events

    async def _search_query(
        self,
        client: httpx.AsyncClient,
        query: str,
    ) -> list[Event]:
        """Try each Nitter instance in order until one succeeds.

        Instead of ``self.fetch()`` (which applies domain validation, SSRF
        checks, and tenacity retries), we issue requests directly so that
        a failing instance is abandoned quickly in favour of the next one.
        """
        for instance in NITTER_INSTANCES:
            try:
                url = _build_search_url(instance, query)
                await asyncio.sleep(random.uniform(*_DELAY_RANGE))
                response = await client.get(url)
                response.raise_for_status()
                return _parse_tweets(response.text, instance)
            except Exception:
                logger.warning(
                    "Nitter instance %s failed for query %r, trying next",
                    instance,
                    query,
                )
                continue

        logger.error("All Nitter instances failed for query %r", query)
        return []
