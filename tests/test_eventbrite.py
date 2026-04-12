"""Tests for Eventbrite scraper — written FIRST (TDD red phase)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.models import Event
from src.scrapers.eventbrite import EventbriteScraper

FIXTURES = Path(__file__).parent / "fixtures"

SEARCH_QUERIES = [
    "hackathon cloud credits",
    "AI workshop AWS",
    "free API credits",
]


@pytest.fixture
def eb_html() -> str:
    return (FIXTURES / "eventbrite_search.html").read_text()


@pytest.fixture
def scraper() -> EventbriteScraper:
    return EventbriteScraper()


# ── Parsing ────────────────────────────────────────────────────


@respx.mock
async def test_parse_returns_correct_number_of_events(scraper: EventbriteScraper, eb_html: str):
    """Each query returns the same fixture (3 events); after dedup we should get 3 unique."""
    respx.get("https://www.eventbrite.com/d/online/hackathon-cloud-credits/").mock(
        return_value=httpx.Response(200, text=eb_html),
    )
    respx.get("https://www.eventbrite.com/d/online/ai-workshop-aws/").mock(
        return_value=httpx.Response(200, text=eb_html),
    )
    respx.get("https://www.eventbrite.com/d/online/free-api-credits/").mock(
        return_value=httpx.Response(200, text=eb_html),
    )
    # Page 2 returns empty for all queries
    respx.get(url__regex=r".*eventbrite\.com/d/online/.*/\?page=2").mock(
        return_value=httpx.Response(200, text="<html><body></body></html>"),
    )
    events = await scraper.scrape()
    unique = {e.id: e for e in events}
    assert len(unique) == 3


@respx.mock
async def test_source_and_id_format(scraper: EventbriteScraper, eb_html: str):
    """source='eventbrite', id='eventbrite:<event-id>'."""
    respx.get("https://www.eventbrite.com/d/online/hackathon-cloud-credits/").mock(
        return_value=httpx.Response(200, text=eb_html),
    )
    respx.get("https://www.eventbrite.com/d/online/ai-workshop-aws/").mock(
        return_value=httpx.Response(200, text=eb_html),
    )
    respx.get("https://www.eventbrite.com/d/online/free-api-credits/").mock(
        return_value=httpx.Response(200, text=eb_html),
    )
    respx.get(url__regex=r".*eventbrite\.com/d/online/.*/\?page=2").mock(
        return_value=httpx.Response(200, text="<html><body></body></html>"),
    )
    events = await scraper.scrape()
    for event in events:
        assert event.source == "eventbrite"
        assert event.id.startswith("eventbrite:")
        eid = event.id.split(":", 1)[1]
        assert eid.isdigit()


@respx.mock
async def test_first_event_fields(scraper: EventbriteScraper, eb_html: str):
    """Verify concrete values for the first fixture event."""
    respx.get("https://www.eventbrite.com/d/online/hackathon-cloud-credits/").mock(
        return_value=httpx.Response(200, text=eb_html),
    )
    respx.get("https://www.eventbrite.com/d/online/ai-workshop-aws/").mock(
        return_value=httpx.Response(200, text=eb_html),
    )
    respx.get("https://www.eventbrite.com/d/online/free-api-credits/").mock(
        return_value=httpx.Response(200, text=eb_html),
    )
    respx.get(url__regex=r".*eventbrite\.com/d/online/.*/\?page=2").mock(
        return_value=httpx.Response(200, text="<html><body></body></html>"),
    )
    events = await scraper.scrape()
    unique = {e.id: e for e in events}
    first = unique["eventbrite:1001001001"]
    assert first.title == "Cloud Credits Hackathon 2026"
    assert "eventbrite.com" in first.url
    assert first.location == "San Francisco, CA"
    assert first.organizer == "Tech Events Inc."


@respx.mock
async def test_search_queries_are_correct(scraper: EventbriteScraper):
    """Scraper must search for the 3 specified query terms."""
    assert scraper.search_queries == SEARCH_QUERIES


# ── Error handling ────────────────────────────────────────────


@respx.mock
async def test_network_error_returns_empty_list(scraper: EventbriteScraper):
    """Network failure for all queries should return empty list."""
    respx.get(url__regex=r".*eventbrite\.com.*").mock(
        side_effect=httpx.ConnectError("Connection refused"),
    )
    events = await scraper.scrape()
    assert events == []


@respx.mock
async def test_partial_failure_returns_available_events(scraper: EventbriteScraper, eb_html: str):
    """If one query fails, events from other queries should still be returned."""
    # First query works
    respx.get("https://www.eventbrite.com/d/online/hackathon-cloud-credits/").mock(
        return_value=httpx.Response(200, text=eb_html),
    )
    # Second query fails
    respx.get("https://www.eventbrite.com/d/online/ai-workshop-aws/").mock(
        side_effect=httpx.ConnectError("timeout"),
    )
    # Third query fails
    respx.get("https://www.eventbrite.com/d/online/free-api-credits/").mock(
        side_effect=httpx.ConnectError("timeout"),
    )
    # Page 2 returns empty
    respx.get(url__regex=r".*eventbrite\.com/d/online/.*/\?page=2").mock(
        return_value=httpx.Response(200, text="<html><body></body></html>"),
    )
    events = await scraper.scrape()
    assert len(events) >= 1  # at least events from the first query
