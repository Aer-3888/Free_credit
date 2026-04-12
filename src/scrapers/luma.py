"""Luma event scraper — HTML scraper of Luma's public discovery page.

No API key required. Parses server-rendered HTML from https://lu.ma/discover.
"""

from __future__ import annotations

import logging

from selectolax.parser import HTMLParser

from src.models import Event
from src.scrapers.base import BaseScraper, make_client

logger = logging.getLogger(__name__)

DISCOVER_URL = "https://lu.ma/discover"


def _parse_events(html: str) -> list[Event]:
    """Parse Luma discovery page HTML into Event objects."""
    tree = HTMLParser(html)
    events: list[Event] = []

    for card in tree.css("[data-testid='event-card']"):
        try:
            event = _parse_card(card)
            if event is not None:
                events.append(event)
        except Exception:
            logger.debug("Failed to parse an event card, skipping", exc_info=True)
            continue

    return events


def _parse_card(card) -> Event | None:
    """Extract a single Event from an event card node."""
    # Link and slug
    link_node = card.css_first("a.event-link")
    if link_node is None:
        return None
    href = link_node.attributes.get("href", "")
    if not href:
        return None
    slug = href.lstrip("/")
    if not slug:
        return None

    # Title
    title_node = card.css_first(".event-card-title")
    title = title_node.text(strip=True) if title_node else ""
    if not title:
        return None

    # URL
    url = f"https://lu.ma/{slug}"

    # Dates — look for <time> elements with datetime attributes
    time_nodes = card.css(".event-card-date time")
    start_date: str | None = None
    end_date: str | None = None
    if len(time_nodes) >= 1:
        start_date = time_nodes[0].attributes.get("datetime")
    if len(time_nodes) >= 2:
        end_date = time_nodes[1].attributes.get("datetime")

    # Location
    location_node = card.css_first(".event-card-location")
    location = location_node.text(strip=True) if location_node else "Online"

    # Organizer / host
    host_node = card.css_first(".host-name")
    organizer = ""
    if host_node:
        raw_host = host_node.text(strip=True)
        # Strip leading "By " prefix
        organizer = raw_host.removeprefix("By ").strip()

    return Event(
        id=f"luma:{slug}",
        source="luma",
        title=title,
        url=url,
        organizer=organizer,
        description="",
        location=location,
        start_date=start_date,
        end_date=end_date,
    )


class LumaScraper(BaseScraper):
    """Scrape Luma's public discovery page for events. No API key needed."""

    name = "luma"

    async def scrape(self) -> list[Event]:
        try:
            async with make_client() as client:
                response = await self.fetch(client, DISCOVER_URL)
                return _parse_events(response.text)
        except Exception:
            logger.exception("Luma scraper failed")
            return []
