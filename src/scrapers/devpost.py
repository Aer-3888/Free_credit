"""Devpost hackathon scraper.

Paginates through the public Devpost API to discover open hackathons,
maps each to the shared Event model, and optionally fetches detail pages
to extract full sponsor lists when cloud-provider keywords are detected.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from selectolax.parser import HTMLParser

from src.models import Event
from src.scrapers.base import BaseScraper, make_client

logger = logging.getLogger(__name__)

_API_BASE = "https://devpost.com/api/hackathons"
_MAX_PAGES = 50

# Keywords that hint a hackathon is sponsored by a cloud/LLM provider.
# When any of these appear in the title or organizer name, we fetch the
# detail page to get the full sponsor list.
_PROVIDER_KEYWORDS: tuple[str, ...] = (
    "aws",
    "amazon web services",
    "azure",
    "microsoft",
    "google cloud",
    "gcp",
    "firebase",
    "ibm",
    "oracle",
    "digitalocean",
    "cloudflare",
    "openai",
    "anthropic",
    "hugging face",
    "nvidia",
    "intel",
    "snowflake",
    "databricks",
    "vercel",
    "supabase",
)


class DevpostScraper(BaseScraper):
    """Scrapes open hackathons from Devpost's public API."""

    name: str = "devpost"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def scrape(self) -> list[Event]:
        """Paginate through the Devpost API and return discovered Events.

        Never raises — returns whatever was successfully collected.
        """
        events: list[Event] = []

        try:
            async with make_client() as client:
                for page in range(1, _MAX_PAGES + 1):
                    url = f"{_API_BASE}?page={page}&status=open"
                    try:
                        response = await self.fetch(client, url)
                    except Exception:
                        logger.warning("Devpost API request failed for page %d", page, exc_info=True)
                        break

                    try:
                        data = response.json()
                    except Exception:
                        logger.warning("Devpost returned non-JSON on page %d", page)
                        break

                    hackathons = data.get("hackathons", [])
                    if not hackathons:
                        break

                    for h in hackathons:
                        try:
                            event = self._parse_hackathon(h)
                        except Exception:
                            logger.warning("Failed to parse hackathon: %s", h.get("title", "?"), exc_info=True)
                            continue

                        # Fetch detail page for provider-related hackathons
                        if self._matches_provider(event):
                            try:
                                detail_resp = await self.fetch(client, event.url)
                                sponsors = self._extract_sponsors(detail_resp.text)
                                if sponsors:
                                    event = Event(
                                        id=event.id,
                                        source=event.source,
                                        title=event.title,
                                        url=event.url,
                                        organizer=event.organizer,
                                        description=event.description,
                                        location=event.location,
                                        start_date=event.start_date,
                                        end_date=event.end_date,
                                        registration_deadline=event.registration_deadline,
                                        sponsors=sponsors,
                                        prizes=event.prizes,
                                        credit_score=event.credit_score,
                                        credit_signals=event.credit_signals,
                                        providers_detected=event.providers_detected,
                                        scraped_at=event.scraped_at,
                                    )
                            except Exception:
                                logger.debug("Could not fetch detail page for %s", event.url, exc_info=True)

                        events.append(event)

        except Exception:
            logger.exception("Devpost scraper encountered a top-level error")

        return events

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_hackathon(self, data: dict) -> Event:
        """Map a single hackathon JSON object to an Event dataclass."""
        slug = data.get("analytics_identifier") or _slug_from_url(data.get("url", ""))
        location_info = data.get("displayed_location") or {}
        location = location_info.get("location", "Unknown")
        dates = _parse_submission_dates(data.get("submission_period_dates", ""))

        return Event(
            id=f"devpost:{slug}",
            source="devpost",
            title=data.get("title", ""),
            url=data.get("url", ""),
            organizer=data.get("organization_name", ""),
            description="",  # The list API does not return descriptions
            location=location,
            start_date=dates[0],
            end_date=dates[1],
            prizes=data.get("prize_amount") or None,
        )

    @staticmethod
    def _extract_sponsors(html: str) -> tuple[str, ...]:
        """Extract sponsor names from a Devpost hackathon detail page."""
        tree = HTMLParser(html)
        sponsors: list[str] = []

        # Sponsors are typically in a #challenge-sponsors div, each inside
        # a .sponsor block with an <img alt="SponsorName"> tag.
        sponsor_nodes = tree.css("#challenge-sponsors .sponsor img")
        for node in sponsor_nodes:
            alt = (node.attributes.get("alt") or "").strip()
            if alt:
                sponsors.append(alt)

        return tuple(dict.fromkeys(sponsors))  # deduplicate, preserve order

    @staticmethod
    def _matches_provider(event: Event) -> bool:
        """Return True if the event title or organizer hints at a cloud provider."""
        text = f"{event.title} {event.organizer}".lower()
        return any(kw in text for kw in _PROVIDER_KEYWORDS)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _slug_from_url(url: str) -> str:
    """Derive a slug from a Devpost URL like https://foo-bar.devpost.com."""
    try:
        host = url.split("//", 1)[1].split(".")[0]
        return host
    except (IndexError, AttributeError):
        return "unknown"


def _parse_submission_dates(raw: str) -> tuple[str | None, str | None]:
    """Best-effort parse of Devpost's 'Apr 15 - May 01, 2026' format.

    Returns (start_iso, end_iso) or (None, None) on failure.
    """
    if not raw:
        return (None, None)
    try:
        parts = raw.split(" - ")
        if len(parts) != 2:
            return (None, None)
        start_raw = parts[0].strip()
        end_raw = parts[1].strip()

        # The end part always has the year; the start part may lack it.
        # e.g. "Apr 15 - May 01, 2026"
        # Parse end first to extract the year.
        end_dt = datetime.strptime(end_raw, "%b %d, %Y")
        # Try parsing start with year, fall back to without
        try:
            start_dt = datetime.strptime(start_raw, "%b %d, %Y")
        except ValueError:
            start_dt = datetime.strptime(f"{start_raw}, {end_dt.year}", "%b %d, %Y")

        return (
            start_dt.replace(tzinfo=timezone.utc).isoformat(),
            end_dt.replace(tzinfo=timezone.utc).isoformat(),
        )
    except Exception:
        return (None, None)
