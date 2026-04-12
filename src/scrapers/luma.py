"""Luma event scraper — searches for events via the Luma public API."""

from __future__ import annotations

import logging
import os

from src.models import Event
from src.scrapers.base import BaseScraper, make_client

logger = logging.getLogger(__name__)

LUMA_API_URL = "https://api.lu.ma/public/v2/event/search"
SEARCH_KEYWORDS = ("hackathon", "AI workshop", "cloud credits")


def _parse_entry(entry: dict) -> Event | None:
    """Convert a single Luma API entry into an Event."""
    ev = entry.get("event", {})
    event_id = ev.get("api_id", "")
    if not event_id:
        return None

    title = ev.get("name", "")
    if not title:
        return None

    # Location
    geo = ev.get("geo_address_info")
    if geo and isinstance(geo, dict) and geo.get("city"):
        location = geo["city"]
    else:
        location = "Online"

    # Host / organizer
    hosts = entry.get("hosts", [])
    organizer = hosts[0]["name"] if hosts else ""

    return Event(
        id=f"luma:{event_id}",
        source="luma",
        title=title,
        url=ev.get("url", ""),
        organizer=organizer,
        description=ev.get("description", ""),
        location=location,
        start_date=ev.get("start_at"),
        end_date=ev.get("end_at"),
    )


def _parse_response(data: dict) -> list[Event]:
    """Parse Luma API response JSON into Event objects."""
    events: list[Event] = []
    for entry in data.get("entries", []):
        event = _parse_entry(entry)
        if event is not None:
            events.append(event)
    return events


class LumaScraper(BaseScraper):
    """Search Luma for hackathons and workshops offering free credits."""

    name = "luma"

    def __init__(self) -> None:
        self._api_key = os.environ.get("LUMA_API_KEY", "")

    async def scrape(self) -> list[Event]:
        if not self._api_key:
            logger.warning("LUMA_API_KEY not set — skipping Luma scraper")
            return []

        seen: dict[str, Event] = {}
        try:
            async with make_client() as client:
                for keyword in SEARCH_KEYWORDS:
                    try:
                        response = await self.fetch(
                            client,
                            LUMA_API_URL,
                            params={"query": keyword},
                            headers={"x-luma-api-key": self._api_key},
                        )
                        for event in _parse_response(response.json()):
                            if event.id not in seen:
                                seen[event.id] = event
                    except Exception:
                        logger.warning("Luma search failed for keyword %r", keyword)
                        continue
        except Exception:
            logger.exception("Luma scraper failed")
            return []

        return list(seen.values())
