"""Eventbrite scraper — searches Eventbrite HTML for hackathon/workshop events."""

from __future__ import annotations

import logging
import re

from selectolax.parser import HTMLParser

from src.models import Event
from src.scrapers.base import BaseScraper, make_client

logger = logging.getLogger(__name__)

EVENTBRITE_BASE = "https://www.eventbrite.com"
MAX_PAGES = 2


def _query_to_slug(query: str) -> str:
    """Convert search query to URL-slug form (lowercase, hyphens)."""
    return re.sub(r"\s+", "-", query.lower().strip())


def _parse_events_html(html: str) -> list[Event]:
    """Parse Eventbrite search results HTML into Event objects."""
    tree = HTMLParser(html)
    events: list[Event] = []

    for article in tree.css("article.eds-event-card-content"):
        event_id = article.attributes.get("data-event-id", "")
        if not event_id:
            continue

        link_node = article.css_first("a.eds-event-card-content__action-link")
        title_node = article.css_first(".eds-event-card-content__title")
        time_node = article.css_first("time")
        location_node = article.css_first(".eds-event-card-content__sub-content")
        organizer_node = article.css_first(".eds-event-card-content__organizer span")

        href = link_node.attributes.get("href", "") if link_node else ""
        title = title_node.text(strip=True) if title_node else ""
        raw_date = time_node.attributes.get("datetime", "") if time_node else ""
        location = location_node.text(strip=True) if location_node else ""
        organizer = organizer_node.text(strip=True) if organizer_node else ""

        # Strip leading "By " from organizer
        if organizer.startswith("By "):
            organizer = organizer[3:]

        if not title:
            continue

        events.append(Event(
            id=f"eventbrite:{event_id}",
            source="eventbrite",
            title=title,
            url=href,
            organizer=organizer,
            description="",
            location=location,
            start_date=raw_date or None,
        ))

    return events


class EventbriteScraper(BaseScraper):
    """Search Eventbrite for hackathons and workshops offering free credits."""

    name = "eventbrite"

    search_queries: list[str] = [
        "hackathon cloud credits",
        "AI workshop AWS",
        "free API credits",
    ]

    async def scrape(self) -> list[Event]:
        seen: dict[str, Event] = {}

        async with make_client() as client:
            for query in self.search_queries:
                slug = _query_to_slug(query)
                for page in range(1, MAX_PAGES + 1):
                    url = f"{EVENTBRITE_BASE}/d/online/{slug}/"
                    params: dict[str, int] | None = None
                    if page > 1:
                        url = f"{url}?page={page}"

                    try:
                        response = await self.fetch(client, url)
                        page_events = _parse_events_html(response.text)
                        if not page_events:
                            break  # No more results, skip remaining pages
                        for event in page_events:
                            if event.id not in seen:
                                seen[event.id] = event
                    except Exception:
                        logger.warning(
                            "Eventbrite search failed for query=%r page=%d",
                            query,
                            page,
                        )
                        break  # Move to next query on error

        return list(seen.values())
