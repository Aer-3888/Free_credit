"""MLH event scraper — fetches hackathon listings from mlh.com."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from selectolax.parser import HTMLParser

from src.models import Event
from src.scrapers.base import BaseScraper, make_client

logger = logging.getLogger(__name__)

_CURRENT_YEAR = datetime.now(timezone.utc).year
MLH_URL = f"https://www.mlh.com/seasons/{_CURRENT_YEAR}/events"


def _slugify(text: str) -> str:
    """Convert event name to a URL-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _parse_events(html: str) -> list[Event]:
    """Parse MLH season page HTML into Event objects."""
    tree = HTMLParser(html)
    events: list[Event] = []

    for wrapper in tree.css(".event-wrapper"):
        link_node = wrapper.css_first(".event-link")
        if link_node is None:
            continue

        href = link_node.attributes.get("href", "")
        name_node = wrapper.css_first(".event-name")
        date_node = wrapper.css_first(".event-date")
        city_node = wrapper.css_first(".event-city")

        title = name_node.text(strip=True) if name_node else ""
        raw_date = date_node.text(strip=True) if date_node else ""
        location = city_node.text(strip=True) if city_node else ""

        if not title:
            continue

        slug = _slugify(title)
        event = Event(
            id=f"mlh:{slug}",
            source="mlh",
            title=title,
            url=href,
            organizer="MLH",
            description="",
            location=location,
            start_date=raw_date,
        )
        events.append(event)

    return events


class MLHScraper(BaseScraper):
    """Scrape hackathon listings from Major League Hacking."""

    name = "mlh"

    async def scrape(self) -> list[Event]:
        try:
            async with make_client() as client:
                response = await self.fetch(client, MLH_URL)
                return _parse_events(response.text)
        except Exception:
            logger.exception("MLH scraper failed")
            return []
